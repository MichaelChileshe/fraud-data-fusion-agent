"""The shape every incoming record must have, one Pydantic model per source.

A model answers "is this record well formed?" (types, required fields, ranges).
Whether it is *believable* is a separate question, answered in gate.py.
"""

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

# Ids follow fixed patterns so a typo'd or truncated id fails fast.
ACCOUNT_ID = r"^A\d{6}$"


class _Strict(BaseModel):
    # Reject unknown fields: a new field appearing upstream should be a
    # conscious schema change, not something silently ignored.
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class KycRecord(_Strict):
    """One account opening, as captured by onboarding (KYC)."""

    record_id: str = Field(pattern=r"^KYC-\d{6}$")
    account_id: str = Field(pattern=ACCOUNT_ID)
    full_name: str = Field(min_length=2, max_length=120)
    date_of_birth: date
    phone: str = Field(min_length=9, max_length=20)
    email: str | None = Field(default=None, max_length=120)
    city: str = Field(min_length=2, max_length=60)
    opened_at: datetime


class Transaction(_Strict):
    """One transfer between two accounts, in rand."""

    txn_id: str = Field(pattern=r"^T\d{7}$")
    ts: datetime
    from_account: str = Field(pattern=ACCOUNT_ID)
    to_account: str = Field(pattern=ACCOUNT_ID)
    amount_zar: float = Field(gt=0)
    channel: Literal["app", "web", "ussd", "card"]
    reference: str = Field(default="", max_length=140)


class Login(_Strict):
    """One sign-in attempt to an account from a device."""

    event_id: str = Field(pattern=r"^L\d{7}$")
    ts: datetime
    account_id: str = Field(pattern=ACCOUNT_ID)
    device_id: str = Field(pattern=r"^D-[0-9a-f]{8}$")
    ip: str = Field(min_length=7, max_length=45)
    success: bool


class SanctionsEntry(_Strict):
    """One entry on a (synthetic) sanctions list."""

    entry_id: str = Field(pattern=r"^S-\d{3}$")
    full_name: str = Field(min_length=2, max_length=120)
    date_of_birth: date | None = None
    list_name: str
    aliases: list[str] = Field(default_factory=list)


class CaseNote(_Strict):
    """Free-text note written by a fraud analyst about an account."""

    note_id: str = Field(pattern=r"^N-\d{5}$")
    account_id: str = Field(pattern=ACCOUNT_ID)
    author: str
    created_at: datetime
    text: str = Field(min_length=10, max_length=2000)


# Which model validates which source, and which field is the record's own id.
SOURCE_MODELS: dict[str, type[_Strict]] = {
    "kyc": KycRecord,
    "transactions": Transaction,
    "logins": Login,
    "sanctions": SanctionsEntry,
    "case_notes": CaseNote,
}
ID_FIELD = {
    "kyc": "record_id",
    "transactions": "txn_id",
    "logins": "event_id",
    "sanctions": "entry_id",
    "case_notes": "note_id",
}
