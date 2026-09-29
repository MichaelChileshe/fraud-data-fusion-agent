"""A restarted consumer must not mistake "waiting for partitions" for "no more messages"."""

from fusion.bus import InMemoryBus, Message
from fusion.config import TOPICS
from fusion.consumer import FusionConsumer


class SlowToAssign:
    """Behaves like a consumer that rejoins a group whose crashed member hasn't timed out yet."""

    def __init__(self, waits: int, message: Message):
        self.waits, self.message, self._assigned = waits, message, False

    def assigned(self) -> bool:
        return self._assigned

    def poll(self, timeout: float = 1.0):
        if self.waits:  # the group hasn't handed over the partitions yet
            self.waits -= 1
            return None
        self._assigned = True
        msg, self.message = self.message, None
        return msg

    def commit(self, message: Message) -> None:
        pass

    def close(self) -> None:
        pass


class AlwaysApplies:
    def apply(self, conn, source, record_id, model, raw) -> bool:
        return True


def test_idle_exit_waits_until_partitions_are_assigned():
    entry = {"entry_id": "S-001", "full_name": "Test Person", "list_name": "SYNTH-CONSOLIDATED"}
    consumer = SlowToAssign(waits=3, message=Message(TOPICS["sanctions"], "S-001", entry, 0))
    worker = FusionConsumer(consumer, InMemoryBus().producer(), conn=None, resolver=AlwaysApplies())
    stats = worker.run(idle_exit=0, progress_every=0)
    assert stats["sanctions.applied"] == 1
