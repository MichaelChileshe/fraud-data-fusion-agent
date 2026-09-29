"""Check the stores against the ground truth: did every record end up exactly where it should?

This is how I prove "0 lost, 0 duplicated" after a crash drill, instead of
eyeballing it. Expected numbers come from data/ground_truth.json.

Run:  python -m fusion.verify                 (PostgreSQL + dead-letter topic)
      python -m fusion.verify --clickhouse    (also check ClickHouse)
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

import psycopg

from fusion.config import DLQ_TOPIC, settings


def expected_counts(truth: dict) -> dict[str, int]:
    pub, bad = truth["published_counts"], truth["bad_records"]
    exp = {s: n - len(bad.get(s, [])) for s, n in pub.items()}
    exp["transactions"] -= len(truth["duplicate_txn_ids"])
    return exp


def dlq_counts(bootstrap: str) -> Counter:
    """Read the whole dead-letter topic with a throwaway consumer group.

    Counts DISTINCT records per kind, so a replay drill (which dead-letters the
    same damaged records a second time) doesn't inflate the numbers."""
    import uuid

    from fusion.bus import KafkaConsumer

    c = KafkaConsumer(bootstrap, f"verify-{uuid.uuid4().hex[:8]}", [DLQ_TOPIC])
    seen: set[tuple[str, str, str]] = set()
    empty_polls = 0
    while empty_polls < 5:
        msg = c.poll(1.0)
        if msg is None:
            empty_polls += 1
            continue
        empty_polls = 0
        seen.add((msg.value["kind"], msg.value["source"], msg.key or ""))
    c.close()
    return Counter(kind for kind, _, _ in seen)


def check_postgres(conn, truth: dict) -> list[tuple[str, object, object]]:
    exp = expected_counts(truth)
    got = dict(conn.execute("SELECT source, count(*) FROM evidence GROUP BY source").fetchall())
    rows = [(f"evidence rows: {s}", exp[s], got.get(s, 0)) for s in exp]
    one = lambda sql, *a: conn.execute(sql, a).fetchone()[0]  # noqa: E731
    rows += [
        ("idempotency ledger = evidence", one("SELECT count(*) FROM evidence"),
         one("SELECT count(*) FROM processed_events")),
        ("transactions stored", exp["transactions"], one("SELECT count(*) FROM transactions")),
        ("accounts", truth["expected_accounts"], one("SELECT count(*) FROM accounts")),
        ("persons", truth["expected_persons"], one("SELECT count(*) FROM persons")),
        ("controller's 4 accounts resolve to 1 person", 1,
         one("SELECT count(DISTINCT person_id) FROM accounts WHERE account_id = ANY(%s)",
             truth["controller_accounts"])),
        ("sanctions match on the controller", True,
         one("""SELECT count(*) > 0 FROM links l JOIN accounts a ON l.src = 'person:' || a.person_id
                WHERE l.kind = 'SANCTIONS_MATCH' AND l.dst = %s AND a.account_id = %s""",
             f"sanction:{truth['sanctions_hit']['entry_id']}", truth["controller_accounts"][0])),
    ]
    return rows


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--clickhouse", action="store_true")
    ap.add_argument("--no-dlq", action="store_true", help="skip reading the dead-letter topic")
    args = ap.parse_args()
    truth = json.loads((Path(settings.data_dir) / "ground_truth.json").read_text())
    with psycopg.connect(settings.pg_dsn) as conn:
        rows = check_postgres(conn, truth)
    if not args.no_dlq:
        dlq = dlq_counts(settings.kafka_bootstrap)
        rows.append(("dead-lettered as rejected", sum(len(v) for v in truth["bad_records"].values()), dlq["rejected"]))
        rows.append(("dead-lettered as poison (drill only)", "-", dlq["poison"]))
    if args.clickhouse:
        from fusion.warehouse import client
        ch = client()
        rows.append(("ClickHouse transactions (FINAL)", expected_counts(truth)["transactions"],
                     ch.query("SELECT count() FROM transactions FINAL").result_rows[0][0]))
    failed = 0
    print(f"{'check':<48}{'expected':>12}{'actual':>12}")
    for name, exp, got in rows:
        ok = exp == "-" or exp == got
        failed += not ok
        print(f"{name:<48}{exp!s:>12}{got!s:>12}  {'PASS' if ok else 'FAIL'}")
    print(f"\n{'ALL CHECKS PASSED' if not failed else f'{failed} CHECK(S) FAILED'}")
    raise SystemExit(1 if failed else 0)


if __name__ == "__main__":
    main()
