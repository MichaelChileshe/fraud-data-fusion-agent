"""The in-memory bus must behave like a Kafka consumer group: the pipeline tests depend on it."""

from fusion.bus import InMemoryBus


def test_messages_arrive_in_order_with_offsets():
    bus = InMemoryBus()
    p = bus.producer()
    for i in range(3):
        p.send("raw.kyc", f"KYC-{i}", {"n": i})
    c = bus.consumer("g", ["raw.kyc"])
    msgs = [c.poll(), c.poll(), c.poll()]
    assert [m.offset for m in msgs] == [0, 1, 2]
    assert [m.value["n"] for m in msgs] == [0, 1, 2] and msgs[0].key == "KYC-0"
    assert c.poll() is None  # nothing left


def test_a_new_consumer_resumes_from_the_last_commit_not_the_last_read():
    bus = InMemoryBus()
    p = bus.producer()
    for i in range(5):
        p.send("raw.kyc", str(i), {"n": i})
    first = bus.consumer("g", ["raw.kyc"])
    _m0, m1, _m2 = first.poll(), first.poll(), first.poll()
    first.commit(m1)  # offsets 0 and 1 are done; 2 was read but never committed ("crash")
    second = bus.consumer("g", ["raw.kyc"])
    assert second.poll().offset == 2  # re-read: at-least-once


def test_consumer_groups_are_independent():
    bus = InMemoryBus()
    bus.producer().send("raw.transactions", "T1", {"x": 1})
    a, b = bus.consumer("resolver", ["raw.transactions"]), bus.consumer("sink", ["raw.transactions"])
    a.commit(a.poll())
    assert b.poll().offset == 0  # the sink still sees the message the resolver finished


def test_values_are_copied_so_later_changes_cannot_leak_in():
    bus = InMemoryBus()
    record = {"n": 1}
    bus.producer().send("raw.kyc", "k", record)
    record["n"] = 99
    assert bus.consumer("g", ["raw.kyc"]).poll().value == {"n": 1}
