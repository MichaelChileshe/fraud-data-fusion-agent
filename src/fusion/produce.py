"""Publish the generated files onto the event stream, one topic per source.

Sources are sent in a realistic order: the sanctions list first (it exists
before any customer), then account openings, then activity.

Run:  python -m fusion.produce                 (everything, as fast as possible)
      python -m fusion.produce --rate 1500     (throttled, for the crash drill)
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from fusion.bus import Producer
from fusion.config import TOPICS, settings
from fusion.models import ID_FIELD

ORDER = ["sanctions", "kyc", "logins", "transactions", "case_notes"]


def publish(producer: Producer, data_dir: Path, sources: list[str] = ORDER,
            rate: float | None = None) -> dict[str, int]:
    counts: dict[str, int] = {}
    for source in sources:
        path = data_dir / f"{source}.jsonl"
        n = 0
        started = time.monotonic()
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                record = json.loads(line)
                # Key = the record's own id. Damaged records may lack fields, never the id.
                producer.send(TOPICS[source], record.get(ID_FIELD[source]), record)
                n += 1
                if rate:
                    delay = n / rate - (time.monotonic() - started)
                    if delay > 0:
                        time.sleep(delay)
        producer.flush()
        counts[source] = n
        print(f"  {source:<13} {n:>7,} -> {TOPICS[source]}", flush=True)
    return counts


def main() -> None:
    from fusion.bus import KafkaProducer, create_topics
    from fusion.config import DLQ_TOPIC

    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data", default=settings.data_dir)
    ap.add_argument("--rate", type=float, default=None, help="messages per second (default: unthrottled)")
    ap.add_argument("--only", nargs="*", choices=ORDER, help="publish only these sources")
    args = ap.parse_args()

    created = create_topics(settings.kafka_bootstrap, [*TOPICS.values(), DLQ_TOPIC])
    if created:
        print(f"Created topics: {', '.join(created)}")
    print(f"Publishing to {settings.kafka_bootstrap}")
    counts = publish(KafkaProducer(settings.kafka_bootstrap), Path(args.data), args.only or ORDER, args.rate)
    print(f"Published {sum(counts.values()):,} messages.")


if __name__ == "__main__":
    main()
