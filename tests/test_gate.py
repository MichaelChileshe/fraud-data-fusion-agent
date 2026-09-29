from datetime import datetime, timedelta, timezone

import pytest

from fusion.gate import check

NOW = datetime(2026, 9, 1, tzinfo=timezone.utc)


def txn(**overrides):
    base = {"txn_id": "T0000001", "ts": "2026-08-01T10:00:00+00:00", "from_account": "A000001",
            "to_account": "A000002", "amount_zar": 250.0, "channel": "app", "reference": "rent"}
    return base | overrides


def kyc(**overrides):
    base = {"record_id": "KYC-000001", "account_id": "A000001", "full_name": "Thandi Nkosi",
            "date_of_birth": "1990-05-01", "phone": "082 123 4567", "email": "t@example.com",
            "city": "Soweto", "opened_at": "2024-01-01T00:00:00+00:00"}
    return base | overrides


def test_clean_records_pass():
    assert check("transactions", txn(), NOW).ok
    assert check("kyc", kyc(), NOW).ok


@pytest.mark.parametrize("record, reason", [
    (txn(amount_zar=-5), "schema: amount_zar"),
    (txn(to_account="A12"), "schema: to_account"),
    (txn(channel="fax"), "schema: channel"),
    (txn(extra="x"), "schema: extra"),
    (txn(to_account="A000001"), "sends money to itself"),
    (txn(ts=(NOW + timedelta(days=5)).isoformat()), "in the future"),
    (txn(amount_zar=5_000_000), "exceeds the R1m"),
])
def test_bad_transactions_are_rejected_with_a_reason(record, reason):
    result = check("transactions", record, NOW)
    assert not result.ok
    assert any(reason in r for r in result.reasons), result.reasons


def test_missing_field_is_a_schema_rejection():
    record = txn()
    del record["channel"]
    assert check("transactions", record, NOW).reasons[0].startswith("schema: channel")


@pytest.mark.parametrize("record, reason", [
    (kyc(phone="011 123 4567"), "not a valid South African mobile"),
    (kyc(phone="12345"), "schema: phone"),
    (kyc(date_of_birth="2017-01-01"), "age"),
    (kyc(opened_at="2010-01-01T00:00:00+00:00"), "before 2015"),
])
def test_implausible_kyc_is_rejected(record, reason):
    result = check("kyc", record, NOW)
    assert not result.ok and any(reason in r for r in result.reasons)


def test_timestamps_without_a_timezone_are_treated_as_utc():
    assert check("transactions", txn(ts="2026-08-01T10:00:00"), NOW).ok
