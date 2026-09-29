"""Failure drills. Each one breaks something on purpose so I can measure what happens.

  poison   publish one transaction that passes the gate but cannot be stored
           (a NUL byte in the reference; PostgreSQL text can't hold 0x00).
           Expected: 3 attempts, then parked in the DLQ as "poison",
           and the consumer keeps going.

  replay   rewind: publish the whole data set a second time.
           Expected: every event is recognised as a duplicate, nothing changes.

The crash drill (kill -9 the consumer mid-stream) is done by hand; the steps
are in docs/runbook.md.

Run:  python -m fusion.drills poison
      python -m fusion.drills replay
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path

from fusion.config import TOPICS, settings


def poison_record() -> dict:
    return {"txn_id": "T9000001", "ts": datetime.now(timezone.utc).isoformat(),
            "from_account": "A000001", "to_account": "A000002", "amount_zar": 100.0,
            "channel": "app", "reference": "invoice\x00 42"}


def main() -> None:
    from fusion.bus import KafkaProducer
    from fusion.produce import publish

    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("drill", choices=["poison", "replay"])
    args = ap.parse_args()
    producer = KafkaProducer(settings.kafka_bootstrap)
    if args.drill == "poison":
        rec = poison_record()
        producer.send(TOPICS["transactions"], rec["txn_id"], rec)
        producer.flush()
        print(f"Published poison record {rec['txn_id']} to {TOPICS['transactions']}. "
              "Watch the consumer: 3 attempts, then the DLQ.")
    else:
        counts = publish(producer, Path(settings.data_dir))
        print(f"Re-published {sum(counts.values()):,} events. Expect all of them as 'duplicate' "
              "(or 'rejected' for the damaged ones), and no change in any table.")


if __name__ == "__main__":
    main()
