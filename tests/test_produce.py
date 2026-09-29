import json

from fusion.bus import InMemoryBus
from fusion.config import TOPICS
from fusion.models import ID_FIELD
from fusion.produce import ORDER, publish


def test_every_line_is_published_to_its_topic_keyed_by_its_id(small_world):
    data, truth = small_world
    bus = InMemoryBus()
    counts = publish(bus.producer(), data)
    assert counts == truth["published_counts"]
    for source in ORDER:
        log = bus.logs[TOPICS[source]]
        assert len(log) == counts[source]
        first = json.loads((data / f"{source}.jsonl").read_text().splitlines()[0])
        assert log[0] == (first[ID_FIELD[source]], first)


def test_only_publishes_the_sources_asked_for(small_world):
    data, _ = small_world
    bus = InMemoryBus()
    counts = publish(bus.producer(), data, sources=["sanctions"])
    assert set(counts) == {"sanctions"} and not bus.logs[TOPICS["kyc"]]


def test_sanctions_are_published_before_account_openings():
    assert ORDER.index("sanctions") < ORDER.index("kyc") < ORDER.index("transactions")
