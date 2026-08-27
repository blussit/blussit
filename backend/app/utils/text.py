import re


def slugify(value: str) -> str:
    value = value.strip().lower()
    value = re.sub(r"[^a-z0-9]+", "-", value)
    return value.strip("-")


def normalize_plate(value: str) -> str:
    """Uppercased, every non-alphanumeric character stripped — so
    "MP09 XX-1234", "mp09xx1234", and "MP-09-XX-1234" all normalize to the
    same key. Used both for the first-time-offer fraud check (same plate
    under a new phone number) and the semi-unique-registration rule (same
    plate on up to 2 accounts) — shared here (not defined in either
    service) specifically to avoid a circular import between them."""
    return "".join(ch for ch in value.upper() if ch.isalnum())
