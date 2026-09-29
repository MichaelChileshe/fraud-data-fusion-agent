"""ClickHouse: the analytical copy of every transaction.

Two jobs live here:
  * `sink`    - a second consumer group on raw.transactions that batches valid
                transactions into ClickHouse (at-least-once; FINAL de-duplicates);
  * `summary` - the per-account aggregate the API serves, in ClickHouse SQL.

Run the sink:  python -m fusion.warehouse sink --idle-exit 20
"""

from __future__ import annotations

import argparse
import time
from datetime import datetime, timezone
from decimal import Decimal

from fusion import telemetry
from fusion.config import TOPICS, settings
from fusion.gate import check

COLUMNS = ["txn_id", "ts", "from_account", "to_account", "amount_zar", "channel", "reference"]


def client():
    import clickhouse_connect

    return clickhouse_connect.get_client(
        host=settings.ch_host, port=settings.ch_port, username=settings.ch_user,
        password=settings.ch_password, database=settings.ch_database,
    )


def to_row(model) -> list:
    ts = model.ts if model.ts.tzinfo else model.ts.replace(tzinfo=timezone.utc)
    return [model.txn_id, ts, model.from_account, model.to_account,
            Decimal(str(model.amount_zar)), model.channel, model.reference]


def run_sink(consumer, ch, batch_size: int = 5000, flush_seconds: float = 2.0,
             idle_exit: float | None = None) -> dict:
    """Consume raw.transactions and insert valid rows into ClickHouse in batches."""
    rows, last_msg = [], None
    stats = {"inserted": 0, "skipped_invalid": 0, "batches": 0}
    last_flush = idle_since = time.monotonic()

    def flush():
        nonlocal rows, last_flush
        if rows:
            with telemetry.span("clickhouse.insert", rows=len(rows)):
                ch.insert("transactions", rows, column_names=COLUMNS)
            stats["inserted"] += len(rows)
            stats["batches"] += 1
            rows = []
        if last_msg is not None:
            consumer.commit(last_msg)  # only after ClickHouse has the rows
        last_flush = time.monotonic()

    try:
        while True:
            msg = consumer.poll(1.0)
            if msg is None:
                flush()
                if not consumer.assigned():  # waiting for partitions is not "idle"
                    idle_since = time.monotonic()
                    continue
                if idle_exit is not None and time.monotonic() - idle_since >= idle_exit:
                    break
                continue
            idle_since = time.monotonic()
            result = check("transactions", msg.value)
            if result.ok:
                rows.append(to_row(result.model))
            else:
                stats["skipped_invalid"] += 1  # the fusion consumer dead-letters these
            last_msg = msg
            if len(rows) >= batch_size or time.monotonic() - last_flush >= flush_seconds:
                flush()
    finally:
        flush()
    return stats


def _f(x) -> float:
    return float(x) if x is not None else 0.0


class ClickHouseTxnStore:
    """Per-account transaction summary, computed by ClickHouse."""

    name = "clickhouse"

    def __init__(self, ch=None):
        self.ch = ch or client()

    def summary(self, account_id: str, days: int, table: str = "transactions") -> dict:
        p = {"acct": account_id, "days": days}
        where = (f"FROM {table} FINAL WHERE (to_account = {{acct:String}} OR from_account = {{acct:String}}) "
                 "AND ts >= now64(3) - toIntervalDay({days:UInt32})")
        (row,) = self.ch.query(f"""
            SELECT countIf(to_account = {{acct:String}}),
                   sumIf(amount_zar, to_account = {{acct:String}}),
                   countIf(from_account = {{acct:String}}),
                   sumIf(amount_zar, from_account = {{acct:String}}),
                   uniqExactIf(from_account, to_account = {{acct:String}}),
                   uniqExactIf(to_account, from_account = {{acct:String}}),
                   min(ts), max(ts)
            {where}""", parameters=p).result_rows
        top = self.ch.query(f"""
            SELECT if(to_account = {{acct:String}}, from_account, to_account) AS counterparty,
                   if(to_account = {{acct:String}}, 'in', 'out') AS direction,
                   count() AS n, sum(amount_zar) AS total
            {where}
            GROUP BY counterparty, direction ORDER BY total DESC LIMIT 5""", parameters=p).result_rows
        evidence = self.ch.query(f"SELECT txn_id {where} ORDER BY amount_zar DESC LIMIT 5",
                                 parameters=p).result_rows
        return _shape(account_id, days, row, top, [e[0] for e in evidence], self.name)


def _shape(account_id, days, row, top, evidence, store) -> dict:
    in_n, in_total, out_n, out_total, senders, receivers, first, last = row
    in_total, out_total = _f(in_total), _f(out_total)
    if in_n + out_n == 0:
        first = last = None
    return {
        "account_id": account_id, "days": days, "store": store,
        "inbound_count": int(in_n), "inbound_total_zar": round(in_total, 2),
        "outbound_count": int(out_n), "outbound_total_zar": round(out_total, 2),
        "pass_through_ratio": round(out_total / in_total, 3) if in_total else None,
        "distinct_senders": int(senders), "distinct_receivers": int(receivers),
        "first_seen": first, "last_seen": last,
        "top_counterparties": [
            {"account_id": c, "direction": d, "count": int(n), "total_zar": round(_f(t), 2)}
            for c, d, n, t in top
        ],
        "evidence_ids": evidence,
    }


def main() -> None:
    from fusion.bus import KafkaConsumer

    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=["sink", "count"])
    ap.add_argument("--idle-exit", type=float, default=None)
    args = ap.parse_args()
    ch = client()
    if args.command == "count":
        raw = ch.query("SELECT count() FROM transactions").result_rows[0][0]
        final = ch.query("SELECT count() FROM transactions FINAL").result_rows[0][0]
        print(f"rows stored: {raw:,}   distinct transactions (FINAL): {final:,}")
        return
    telemetry.setup("clickhouse-sink")
    consumer = KafkaConsumer(settings.kafka_bootstrap, "clickhouse-sink", [TOPICS["transactions"]])
    print(f"Sinking {TOPICS['transactions']} into ClickHouse ({datetime.now():%H:%M:%S})")
    stats = run_sink(consumer, ch, idle_exit=args.idle_exit)
    consumer.close()
    telemetry.flush()
    print(stats)


if __name__ == "__main__":
    main()
