"""The ClickHouse sink's delivery rules, tested with fakes (no ClickHouse, no broker)."""

from decimal import Decimal

from fusion.bus import InMemoryBus
from fusion.config import TOPICS
from fusion.gate import check
from fusion.warehouse import _shape, run_sink, to_row

GOOD = {"txn_id": "T0000001", "ts": "2026-08-01T10:00:00", "from_account": "A000001",
        "to_account": "A000002", "amount_zar": 477.97, "channel": "app", "reference": ""}


class FakeClickHouse:
    def __init__(self, bus, fail=False):
        self.bus, self.fail, self.rows = bus, fail, []
        self.committed_before_insert = False

    def insert(self, table, rows, column_names):
        if self.bus.committed[("clickhouse-sink", TOPICS["transactions"])]:
            self.committed_before_insert = True
        if self.fail:
            raise ConnectionError("clickhouse down")
        self.rows += rows


def test_to_row_keeps_money_exact_and_time_zone_aware():
    row = to_row(check("transactions", GOOD).model)
    assert row[4] == Decimal("477.97")
    assert row[1].tzinfo is not None


def test_valid_rows_are_inserted_and_offsets_committed_only_after_the_insert():
    bus = InMemoryBus()
    p = bus.producer()
    p.send(TOPICS["transactions"], "T0000001", GOOD)
    p.send(TOPICS["transactions"], "T0000002", GOOD | {"txn_id": "T0000002", "amount_zar": -5})  # invalid
    ch = FakeClickHouse(bus)
    stats = run_sink(bus.consumer("clickhouse-sink", [TOPICS["transactions"]]), ch, idle_exit=0)
    assert stats == {"inserted": 1, "skipped_invalid": 1, "batches": 1}
    assert not ch.committed_before_insert
    assert bus.committed[("clickhouse-sink", TOPICS["transactions"])] == 2


def test_a_failed_insert_commits_nothing():
    bus = InMemoryBus()
    bus.producer().send(TOPICS["transactions"], "T0000001", GOOD)
    try:
        run_sink(bus.consumer("clickhouse-sink", [TOPICS["transactions"]]), FakeClickHouse(bus, fail=True),
                 idle_exit=0)
    except ConnectionError:
        pass
    assert bus.committed[("clickhouse-sink", TOPICS["transactions"])] == 0  # will be re-read


def test_shape_computes_pass_through_and_handles_no_money_in():
    row = (2, Decimal("1000.00"), 1, Decimal("900.00"), 2, 1, None, None)
    s = _shape("A000001", 30, row, [("A000009", "in", 1, Decimal("600"))], ["T1"], "postgres")
    assert s["pass_through_ratio"] == 0.9 and s["top_counterparties"][0]["total_zar"] == 600.0
    empty = _shape("A000001", 30, (0, None, 0, None, 0, 0, None, None), [], [], "postgres")
    assert empty["pass_through_ratio"] is None and empty["inbound_total_zar"] == 0.0
