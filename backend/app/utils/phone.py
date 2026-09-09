"""
Indian mobile number validation + normalization.

The app stores phones as bare 10-digit strings everywhere (users.phone,
WhatsApp linking, SMS providers) — this is the one place that turns
whatever a person typed ("+91 98765-43210", "09876543210", "9876543210")
into that canonical form, or rejects it.
"""


def validate_indian_mobile(raw: str) -> str | None:
    """Returns the canonical 10-digit number, or None if invalid.
    Accepts optional +91 / 91 / 0 prefixes and any spacing/dashes.
    Indian mobiles always start with 6-9."""
    digits = "".join(ch for ch in (raw or "") if ch.isdigit())
    if len(digits) == 12 and digits.startswith("91"):
        digits = digits[2:]
    elif len(digits) == 11 and digits.startswith("0"):
        digits = digits[1:]
    if len(digits) == 10 and digits[0] in "6789":
        return digits
    return None
