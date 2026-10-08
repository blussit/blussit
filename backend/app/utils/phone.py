"""
Indian mobile number validation + normalization.

The app stores phones as bare 10-digit strings everywhere (users.phone,
WhatsApp linking, SMS providers) — this is the one place that turns
whatever a person typed ("+91 98765-43210", "09876543210", "9876543210")
into that canonical form, or rejects it.
"""


_ASCII_DIGITS = frozenset("0123456789")
# What people type around a number: "+91 (98765) 432-10", "98765.43210".
_SEPARATORS = frozenset("+-().")


def validate_indian_mobile(raw: str) -> str | None:
    """Returns the canonical 10-digit number, or None if invalid.
    Accepts optional +91 / 91 / 0 prefixes and any spacing/dashes.
    Indian mobiles always start with 6-9.

    ASCII 0-9 only: str.isdigit() also accepts Arabic-Indic, Devanagari and
    fullwidth digits, so "9٨76543210" used to pass as a look-alike of
    9876543210 — a second account for "the same" number. Anything that is
    neither an ASCII digit nor a separator (letters, other symbols) makes
    the whole thing invalid instead of being skipped over."""
    text = raw or ""
    if any(ch not in _ASCII_DIGITS and ch not in _SEPARATORS and not ch.isspace() for ch in text):
        return None
    digits = "".join(ch for ch in text if ch in _ASCII_DIGITS)
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


def to_whatsapp_e164(raw: str | None) -> str:
    """The recipient as Meta's Cloud API wants it: country code + number,
    digits only. Every spelling of an Indian mobile — "9876543210",
    "09876543210" (trunk 0), "+91 98765 43210", "919876543210",
    "0091 98765 43210" — becomes "919876543210". Anything that isn't an
    Indian mobile is passed through as its digits (an international
    number already carrying its country code, a test number), minus an
    international "00" prefix."""
    text = str(raw or "")
    digits = "".join(ch for ch in text if ch in _ASCII_DIGITS)
    if digits.startswith("00"):
        digits = digits[2:]
    local = validate_indian_mobile(digits)
    if local:
        return f"91{local}"
    if len(digits) == 10:
        return f"91{digits}"  # a bare 10-digit number is always local
    return digits
