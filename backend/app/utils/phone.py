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


BUSINESS_NUMBER_MESSAGE = (
    "This is Blussit's own WhatsApp business number — WhatsApp can't send messages to itself. "
    "Use the person's own mobile number."
)


def is_business_whatsapp_number(raw: str | None) -> bool:
    """True when `raw` is the number our WhatsApp Cloud API sends FROM
    (settings.WHATSAPP_BUSINESS_NUMBER). Meta rejects every message
    addressed to it, so it must never be anyone's contact number."""
    from app.core.config import settings

    business = validate_indian_mobile(settings.WHATSAPP_BUSINESS_NUMBER or "")
    return bool(business) and validate_indian_mobile(raw or "") == business
