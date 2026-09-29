"""A thin layer over the event stream, so the pipeline never imports Kafka directly.

`KafkaProducer` and `KafkaConsumer` talk to Redpanda through confluent-kafka
(Redpanda speaks the Kafka protocol, so this is standard Kafka client code). `InMemoryBus`
behaves the same way inside one Python process, which lets the tests run
the whole pipeline, including a consumer crash and restart, with no broker.

Delivery contract (both implementations):
  * a consumer reads from its group's last *committed* offset;
  * it commits only after its work is safely stored;
  * so after a crash, anything read but not committed is read again
    (at-least-once). The database makes re-processing harmless (idempotency).
"""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass
from typing import Protocol


@dataclass
class Message:
    topic: str
    key: str | None
    value: dict
    offset: int
    partition: int = 0
    raw: object = None  # the underlying client message, needed to commit


class Producer(Protocol):
    def send(self, topic: str, key: str | None, value: dict) -> None:
        ...

    def flush(self) -> None:
        ...


class Consumer(Protocol):
    def poll(self, timeout: float = 1.0) -> Message | None:
        ...

    def commit(self, message: Message) -> None:
        ...

    def assigned(self) -> bool:
        ...

    def close(self) -> None:
        ...


# ---------------------------------------------------------------- Kafka / Redpanda
class KafkaProducer:
    def __init__(self, bootstrap: str):
        from confluent_kafka import Producer as _P

        # Idempotent producer + acks=all: a network retry can't write a message twice.
        self._p = _P({"bootstrap.servers": bootstrap, "enable.idempotence": True,
                      "acks": "all", "linger.ms": 20})
        self.errors: list[str] = []

    def _on_delivery(self, err, msg):
        if err is not None:
            self.errors.append(str(err))

    def send(self, topic: str, key: str | None, value: dict) -> None:
        payload = json.dumps(value, ensure_ascii=False).encode()
        while True:
            try:
                self._p.produce(topic, key=key.encode() if key else None, value=payload,
                                on_delivery=self._on_delivery)
                break
            except BufferError:  # local queue full: let it drain, then retry
                self._p.poll(0.5)
        self._p.poll(0)

    def flush(self) -> None:
        self._p.flush(30)
        if self.errors:
            raise RuntimeError(f"{len(self.errors)} messages failed delivery: {self.errors[:3]}")


class KafkaConsumer:
    def __init__(self, bootstrap: str, group_id: str, topics: list[str]):
        from confluent_kafka import Consumer as _C

        self._c = _C({
            "bootstrap.servers": bootstrap,
            "group.id": group_id,
            "auto.offset.reset": "earliest",  # a new group starts from the beginning
            "enable.auto.commit": False,      # we commit, and only after storing
            "session.timeout.ms": 10000,      # declare a crashed member dead after 10 s, not 45 s
        })
        self._c.subscribe(topics)

    def poll(self, timeout: float = 1.0) -> Message | None:
        msg = self._c.poll(timeout)
        if msg is None:
            return None
        if msg.error():
            from confluent_kafka import KafkaError

            # "End of partition" is informational; anything else is a real problem.
            if msg.error().code() == KafkaError._PARTITION_EOF:
                return None
            raise RuntimeError(f"consumer error: {msg.error()}")
        key = msg.key().decode() if msg.key() else None
        return Message(msg.topic(), key, json.loads(msg.value()), msg.offset(), msg.partition(), raw=msg)

    def commit(self, message: Message) -> None:
        self._c.commit(message=message.raw, asynchronous=False)

    def assigned(self) -> bool:
        """True once the group has given this consumer its partitions."""
        return bool(self._c.assignment())

    def close(self) -> None:
        self._c.close()


def create_topics(bootstrap: str, topics: list[str]) -> list[str]:
    """Create any missing topics with ONE partition each (see ADR-0003)."""
    from confluent_kafka.admin import AdminClient, NewTopic

    admin = AdminClient({"bootstrap.servers": bootstrap})
    existing = set(admin.list_topics(timeout=10).topics)
    new = [t for t in topics if t not in existing]
    if new:
        futures = admin.create_topics([NewTopic(t, num_partitions=1, replication_factor=1) for t in new])
        for f in futures.values():
            f.result()
    return new


# ---------------------------------------------------------------- in-memory (tests)
class InMemoryBus:
    """A single-partition log per topic, with committed offsets per consumer group."""

    def __init__(self):
        self.logs: dict[str, list[tuple[str | None, dict]]] = defaultdict(list)
        self.committed: dict[tuple[str, str], int] = defaultdict(int)

    def producer(self) -> "InMemoryProducer":
        return InMemoryProducer(self)

    def consumer(self, group_id: str, topics: list[str]) -> "InMemoryConsumer":
        return InMemoryConsumer(self, group_id, topics)


class InMemoryProducer:
    def __init__(self, bus: InMemoryBus):
        self.bus = bus

    def send(self, topic: str, key: str | None, value: dict) -> None:
        self.bus.logs[topic].append((key, json.loads(json.dumps(value))))

    def flush(self) -> None:
        pass


class InMemoryConsumer:
    def __init__(self, bus: InMemoryBus, group_id: str, topics: list[str]):
        self.bus, self.group, self.topics = bus, group_id, topics
        # Resume from the committed offset, like a real consumer group.
        self.position = {t: bus.committed[(group_id, t)] for t in topics}

    def poll(self, timeout: float = 1.0) -> Message | None:
        for t in self.topics:
            pos = self.position[t]
            if pos < len(self.bus.logs[t]):
                key, value = self.bus.logs[t][pos]
                self.position[t] = pos + 1
                return Message(t, key, value, pos)
        return None

    def commit(self, message: Message) -> None:
        self.bus.committed[(self.group, message.topic)] = message.offset + 1

    def assigned(self) -> bool:
        return True  # one process, no group rebalancing

    def close(self) -> None:
        pass
