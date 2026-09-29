"""The whole pipeline against real PostgreSQL, with an in-memory event stream.

Run with:  PG_TEST_DSN=postgresql://kgotla:kgotla@localhost:5432/fusion_test pytest -m integration
"""

import pytest

from fusion import consumer as consumer_mod
from fusion.bus import InMemoryBus
from fusion.config import DLQ_TOPIC, TOPICS
from fusion.consumer import FusionConsumer
from fusion.drills import poison_record
from fusion.produce import publish
from fusion.verify import check_postgres

pytestmark = pytest.mark.integration


def run_all(bus, pg, group="resolver"):
    worker = FusionConsumer(bus.consumer(group, list(TOPICS.values())), bus.producer(), pg)
    return worker.run(idle_exit=0, progress_every=0)


def assert_all_checks_pass(pg, truth):
    for name, expected, actual in check_postgres(pg, truth):
        assert expected == actual, name


def test_every_record_lands_exactly_once(pg, small_world):
    data, truth = small_world
    bus = InMemoryBus()
    publish(bus.producer(), data)
    stats = run_all(bus, pg)
    assert_all_checks_pass(pg, truth)
    assert stats["transactions.duplicate"] == len(truth["duplicate_txn_ids"])
    bad_total = sum(len(v) for v in truth["bad_records"].values())
    assert len(bus.logs[DLQ_TOPIC]) == bad_total
    assert all(m["kind"] == "rejected" and m["reasons"] for _, m in bus.logs[DLQ_TOPIC])


def test_a_crash_before_commit_loses_nothing_and_duplicates_nothing(pg, small_world, monkeypatch):
    data, truth = small_world
    bus = InMemoryBus()
    publish(bus.producer(), data)
    # First run: process 2,500 messages but "crash" before committing any offsets.
    monkeypatch.setattr(consumer_mod, "COMMIT_EVERY", 10**9)
    monkeypatch.setattr(consumer_mod, "COMMIT_SECONDS", 10**9)
    first = FusionConsumer(bus.consumer("resolver", list(TOPICS.values())), bus.producer(), pg)
    for _ in range(2500):
        msg = first.consumer.poll()
        first.handle(msg)  # stored in PostgreSQL, offset never committed
    # Restart: the new consumer starts again from offset 0 and re-reads those 2,500.
    monkeypatch.setattr(consumer_mod, "COMMIT_EVERY", 500)
    stats = run_all(bus, pg)
    assert stats["kyc.duplicate"] + stats["logins.duplicate"] + stats["transactions.duplicate"] >= 2500 - 200
    assert_all_checks_pass(pg, truth)


def test_a_poison_record_is_parked_and_the_stream_keeps_moving(pg, small_world, monkeypatch):
    monkeypatch.setattr("time.sleep", lambda s: None)
    data, truth = small_world
    bus = InMemoryBus()
    producer = bus.producer()
    publish(producer, data, sources=["sanctions", "kyc"])
    rec = poison_record()
    producer.send(TOPICS["transactions"], rec["txn_id"], rec)
    publish(producer, data, sources=["logins", "transactions", "case_notes"])
    stats = run_all(bus, pg)
    poison = [m for _, m in bus.logs[DLQ_TOPIC] if m["kind"] == "poison"]
    assert stats["transactions.poison"] == 1 and len(poison) == 1
    assert "after 3 attempts" in poison[0]["reasons"][0]
    assert_all_checks_pass(pg, truth)  # everything after the poison record still landed


def test_replaying_the_whole_stream_changes_nothing(pg, small_world):
    data, truth = small_world
    bus = InMemoryBus()
    publish(bus.producer(), data)
    run_all(bus, pg)
    before = pg.execute("SELECT count(*), sum(weight) FROM links").fetchone()
    publish(bus.producer(), data)  # the same events again
    stats = run_all(bus, pg)
    assert stats["kyc.applied"] == stats["transactions.applied"] == 0
    assert pg.execute("SELECT count(*), sum(weight) FROM links").fetchone() == before
    assert_all_checks_pass(pg, truth)
