"""Turn the many ways people write names and phone numbers into one form.

Matching only works on normalised values: "082 123 4567", "+27821234567"
and "0821234567" are the same phone, and "  MBALI  n. zulu" is
"mbali n zulu". Every other module calls these two functions, so there is
exactly one definition of "the same".
"""

import re
import unicodedata

_HONORIFICS = {"mr", "mrs", "ms", "miss", "dr", "prof", "mama", "baba", "rev"}


def normalise_phone(raw: str) -> str | None:
    """Return a South African mobile number in E.164 (+27XXXXXXXXX), or None.

    Accepts spaces, dashes, brackets, a leading 0, 27 or +27. South African
    mobile numbers start with 6, 7 or 8 after the country code.
    """
    digits = re.sub(r"\D", "", raw or "")
    if digits.startswith("27") and len(digits) == 11:
        national = digits[2:]
    elif digits.startswith("0") and len(digits) == 10:
        national = digits[1:]
    else:
        return None
    if national[0] not in "678":
        return None
    return "+27" + national


def normalise_name(raw: str) -> str:
    """Lowercase, strip accents and punctuation, drop titles, collapse spaces."""
    text = unicodedata.normalize("NFKD", raw or "")
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = re.sub(r"[^a-zA-Z\s-]", " ", text).lower().replace("-", " ")
    parts = [p for p in text.split() if p not in _HONORIFICS]
    return " ".join(parts)
