import json
from datetime import date

from fusion.gate import check
from fusion.generate import Generator


def build(seed=7):
    return Generator(customers=300, transactions=2000, seed=seed, bad_rate=0.03,
                     dup_rate=0.01, as_of=date(2026, 9, 1)).build()


def test_same_seed_gives_the_same_world():
    a, b = build(), build()
    assert a.kyc == b.kyc and a.transactions == b.transactions and a.truth == b.truth


def test_the_ring_is_planted_as_described(small_world):
    _, truth = small_world
    assert len(truth["ring_accounts"]) == 12
    assert len(truth["controller_accounts"]) == 4 and len(truth["mule_accounts"]) == 8
    assert truth["cash_out_account"] in truth["controller_accounts"]
    assert set(truth["shared_phone_mules"]) <= set(truth["mule_accounts"])


def test_exactly_the_damaged_records_fail_the_gate(small_world):
    out, truth = small_world
    id_field = {"kyc": "record_id", "transactions": "txn_id", "logins": "event_id",
                "case_notes": "note_id", "sanctions": "entry_id"}
    for source, idf in id_field.items():
        bad = {b["id"] for b in truth["bad_records"].get(source, [])}
        failed = {json.loads(line)[idf] for line in (out / f"{source}.jsonl").read_text().splitlines()
                  if not check(source, json.loads(line)).ok}
        assert failed == bad, source


def test_ring_accounts_are_never_damaged(small_world):
    out, truth = small_world
    ring = set(truth["ring_accounts"])
    for source, idf in (("transactions", "txn_id"), ("kyc", "record_id"), ("logins", "event_id")):
        bad = {b["id"] for b in truth["bad_records"][source]}
        for line in (out / f"{source}.jsonl").read_text().splitlines():
            r = json.loads(line)
            if r[idf] in bad:
                touched = {r.get("from_account"), r.get("to_account"), r.get("account_id")}
                assert not touched & ring, (source, r[idf])


def test_duplicates_are_exact_copies_sent_later(small_world):
    out, truth = small_world
    lines = [json.loads(x) for x in (out / "transactions.jsonl").read_text().splitlines()]
    for dup_id in truth["duplicate_txn_ids"]:
        copies = [r for r in lines if r["txn_id"] == dup_id]
        assert len(copies) == 2 and copies[0] == copies[1]
