"""The data gate: nothing reaches the stores unless it is well formed AND believable.

Two checks run in order:

1. Schema: does the record match its Pydantic model? (types, required fields)
2. Plausibility: could this record physically exist? (no transfers dated in
   the future, no account sending money to itself, no customer aged 7)

A record that fails either check is not dropped. It is wrapped with the
reason and sent to the dead-letter topic, so every rejection is counted,
visible and replayable.
"""

from datetime import date, datetime, timedelta, timezone

from pydantic import BaseModel, ValidationError

from fusion.models import SOURCE_MODELS
from fusion.normalise import normalise_phone

EARLIEST = datetime(2015, 1, 1, tzinfo=timezone.utc)
MAX_TRANSFER_ZAR = 1_000_000.0


class GateResult(BaseModel):
    ok: bool
    source: str
    record: dict
    model: BaseModel | None = None
    reasons: list[str] = []

    model_config = {"arbitrary_types_allowed": True}


def _aware(ts: datetime) -> datetime:
    """Treat a timestamp without a timezone as UTC so comparisons are safe."""
    return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)


def _age_on(dob: date, day: date) -> int:
    return day.year - dob.year - ((day.month, day.day) < (dob.month, dob.day))


def _plausibility(source: str, m: BaseModel, now: datetime) -> list[str]:
    reasons: list[str] = []
    latest = now + timedelta(days=1)

    def check_time(label: str, ts: datetime) -> None:
        ts = _aware(ts)
        if ts > latest:
            reasons.append(f"{label} is in the future")
        if ts < EARLIEST:
            reasons.append(f"{label} is before 2015")

    if source == "kyc":
        check_time("opened_at", m.opened_at)
        if normalise_phone(m.phone) is None:
            reasons.append("phone is not a valid South African mobile number")
        age = _age_on(m.date_of_birth, _aware(m.opened_at).date())
        if not 16 <= age <= 110:
            reasons.append(f"customer age {age} at opening is implausible")
    elif source == "transactions":
        check_time("ts", m.ts)
        if m.from_account == m.to_account:
            reasons.append("account sends money to itself")
        if m.amount_zar > MAX_TRANSFER_ZAR:
            reasons.append(f"amount {m.amount_zar:.2f} exceeds the R1m single-transfer limit")
    elif source == "logins":
        check_time("ts", m.ts)
    elif source == "case_notes":
        check_time("created_at", m.created_at)
    return reasons


def check(source: str, record: dict, now: datetime | None = None) -> GateResult:
    """Validate one raw record from `source`. Never raises on bad data."""
    now = now or datetime.now(timezone.utc)
    model_cls = SOURCE_MODELS[source]
    try:
        model = model_cls.model_validate(record)
    except ValidationError as exc:
        first = exc.errors()[0]
        field = ".".join(str(p) for p in first["loc"]) or "record"
        return GateResult(
            ok=False, source=source, record=record,
            reasons=[f"schema: {field}: {first['msg']}"],
        )
    reasons = _plausibility(source, model, now)
    return GateResult(ok=not reasons, source=source, record=record, model=model, reasons=reasons)
