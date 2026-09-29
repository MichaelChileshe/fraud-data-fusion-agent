"""The fusion consumer: read every raw topic, gate each record, resolve it into PostgreSQL.

For each message:
  1. gate.check()          - bad record?  -> dead-letter topic, with the reason
  2. Resolver.apply()      - good record? -> PostgreSQL, exactly once (idempotency ledger)
  3. if applying fails 3 times (a "poison" record) -> dead-letter topic, keep going
  4. commit the offset     - only after steps 1-3 are safely done

Offsets are committed in batches (every 500 messages or 2 seconds). If the
process is killed between commits, the uncommitted messages are read again
on restart and the idempotency ledger skips the ones already applied.

Run:  python -m fusion.consumer --idle-exit 20
"""

from __future__ import annotations

import argparse
import json
import signal
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import psycopg

from fusion import telemetry
from fusion.bus import Consumer, Message, Producer
from fusion.config import DLQ_TOPIC, TOPICS, settings
from fusion.gate import check
from fusion.models import ID_FIELD
from fusion.resolve import Resolver

SOURCE_OF_TOPIC = {topic: source for source, topic in TOPICS.items()}
MAX_ATTEMPTS = 3
COMMIT_EVERY = 500
COMMIT_SECONDS = 2.0


class FusionConsumer:
    def __init__(self, consumer: Consumer, dlq: Producer, conn: psycopg.Connection,
                 resolver: Resolver | None = None):
        self.consumer, self.dlq, self.conn = consumer, dlq, conn
        self.resolver = resolver or Resolver()
        self.stats: Counter = Counter()
        self._pending: dict[tuple[str, int], Message] = {}
        self._since_commit = 0
        self._last_commit = time.monotonic()
        self._stop = False
        self._metric = telemetry.counter("fusion_events_total", "events by source and outcome")

    # ------------------------------------------------------------------ one message
    def handle(self, msg: Message) -> str:
        source = SOURCE_OF_TOPIC[msg.topic]
        with telemetry.span("fusion.consume", source=source, offset=msg.offset) as s:
            result = check(source, msg.value)
            if not result.ok:
                self._dead_letter(msg, source, result.reasons, "rejected")
                outcome = "rejected"
            else:
                outcome = self._apply_with_retries(msg, source, result.model)
            s.set_attribute("outcome", outcome)
        self.stats[f"{source}.{outcome}"] += 1
        self._metric.add(1, {"source": source, "outcome": outcome})
        return outcome

    def _apply_with_retries(self, msg: Message, source: str, model) -> str:
        record_id = msg.value[ID_FIELD[source]]
        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                applied = self.resolver.apply(self.conn, source, record_id, model, msg.value)
                return "applied" if applied else "duplicate"
            except psycopg.OperationalError:
                raise  # the database is down: stop, don't send good data to the DLQ
            except Exception as exc:  # noqa: BLE001 - any other failure is per-record
                if attempt == MAX_ATTEMPTS:
                    self._dead_letter(msg, source, [f"processing error after {attempt} attempts: {exc}"], "poison")
                    return "poison"
                time.sleep(0.2 * attempt)
        return "poison"  # unreachable, keeps type checkers happy

    def _dead_letter(self, msg: Message, source: str, reasons: list[str], kind: str) -> None:
        self.dlq.send(DLQ_TOPIC, msg.key, {
            "kind": kind, "source": source, "reasons": reasons, "record": msg.value,
            "topic": msg.topic, "offset": msg.offset,
            "dead_lettered_at": datetime.now(timezone.utc).isoformat(),
        })

    # ------------------------------------------------------------------ offsets
    def _mark(self, msg: Message) -> None:
        self._pending[(msg.topic, msg.partition)] = msg
        self._since_commit += 1
        if self._since_commit >= COMMIT_EVERY or time.monotonic() - self._last_commit >= COMMIT_SECONDS:
            self.commit()

    def commit(self) -> None:
        if not self._pending:
            return
        self.dlq.flush()  # dead letters must be durable before we move past their source
        for m in self._pending.values():
            self.consumer.commit(m)
        self._pending.clear()
        self._since_commit = 0
        self._last_commit = time.monotonic()

    # ------------------------------------------------------------------ loop
    def run(self, idle_exit: float | None = None, max_messages: int | None = None,
            progress_every: int = 5000) -> Counter:
        idle_since = time.monotonic()
        seen = 0
        started = time.monotonic()
        try:
            while not self._stop:
                msg = self.consumer.poll(1.0)
                if msg is None:
                    self.commit()
                    if not self.consumer.assigned():
                        # Still waiting for partitions (e.g. a crashed member hasn't
                        # timed out yet): that is not "the stream is empty".
                        idle_since = time.monotonic()
                        continue
                    if idle_exit is not None and time.monotonic() - idle_since >= idle_exit:
                        break
                    continue
                idle_since = time.monotonic()
                self.handle(msg)
                self._mark(msg)
                seen += 1
                if progress_every and seen % progress_every == 0:
                    rate = seen / (time.monotonic() - started)
                    print(f"  {seen:>7,} messages  ({rate:,.0f}/s)", flush=True)
                if max_messages is not None and seen >= max_messages:
                    break
        finally:
            self.commit()
        return self.stats

    def stop(self, *_):
        self._stop = True


def summarise(stats: Counter) -> str:
    lines = [f"{'source':<14}{'applied':>9}{'duplicate':>11}{'rejected':>10}{'poison':>8}"]
    for source in TOPICS:
        row = [stats.get(f"{source}.{o}", 0) for o in ("applied", "duplicate", "rejected", "poison")]
        lines.append(f"{source:<14}" + "".join(f"{v:>{w},}" for v, w in zip(row, (9, 11, 10, 8))))
    return "\n".join(lines)


def main() -> None:
    from fusion.bus import KafkaConsumer, KafkaProducer

    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--group", default="fusion-resolver")
    ap.add_argument("--idle-exit", type=float, default=None,
                    help="exit after this many seconds with no new messages")
    args = ap.parse_args()

    telemetry.setup("fusion-consumer")
    conn = psycopg.connect(settings.pg_dsn, autocommit=True)
    worker = FusionConsumer(KafkaConsumer(settings.kafka_bootstrap, args.group, list(TOPICS.values())),
                            KafkaProducer(settings.kafka_bootstrap), conn)
    signal.signal(signal.SIGTERM, worker.stop)
    signal.signal(signal.SIGINT, worker.stop)
    print(f"Consuming {', '.join(TOPICS.values())} as group '{args.group}' (Ctrl+C to stop)")
    started = time.monotonic()
    stats = worker.run(idle_exit=args.idle_exit)
    worker.consumer.close()
    telemetry.flush()
    print(summarise(stats))
    print(f"Finished in {time.monotonic() - started:,.1f} s")
    out = Path(settings.results_dir)
    out.mkdir(exist_ok=True)
    (out / "consumer-last-run.json").write_text(json.dumps(dict(stats), indent=2))


if __name__ == "__main__":
    main()
