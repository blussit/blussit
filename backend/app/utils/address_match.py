"""
Is the address a customer just gave for a booking the same place as one
they already have? Used before a booking creates an address record, so a
returning customer (guest "book without login", the WhatsApp bot, a staff
booking) lands on the address they already have instead of piling up a
fresh copy per booking.

Same place:
  - both pinned  -> within SAME_PLACE_RADIUS_M of each other (the pin is
    the truth; the geocoded label can drift between two drops);
  - otherwise    -> the same line1 once normalised (lowercase, punctuation
    and spaces collapsed, the country / pincode / city / state parts a
    geocoded label repeats dropped) AND no conflicting pincode.

Pure functions, no I/O — AddressService.find_same_place does the lookup.
"""
import re
from collections.abc import Iterable

from app.utils.geo import haversine_km

SAME_PLACE_RADIUS_M = 60.0

_PINCODE_RE = re.compile(r"\b\d{6}\b")
_NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")
_PART_SPLIT_RE = re.compile(r"[,;\n|]+")
_COUNTRY = frozenset({"india", "bharat"})
# Stand-ins the booking paths write when a real value is missing.
_PLACEHOLDERS = frozenset({"", "-", "—", "000000"})


def _words(text: str | None) -> str:
    return " ".join(_NON_ALNUM_RE.sub(" ", (text or "").lower()).split())


def normalize_address_text(line1: str | None, *, drop: Iterable[str | None] = ()) -> str:
    """"12, Test Lane,  Vijay Nagar, Indore, Madhya Pradesh 452001, India"
    -> "12 test lane vijay nagar" (with drop=["Indore", "Madhya Pradesh"]).
    Comma parts that are only a pincode, the country, or one of `drop`
    (the records' own city/state) go; everything else is kept in order."""
    text = _PINCODE_RE.sub(" ", (line1 or "").lower())
    drops = {_words(d) for d in drop if d} | _COUNTRY
    drops.discard("")
    kept = [w for w in (_words(part) for part in _PART_SPLIT_RE.split(text)) if w and w not in drops]
    return " ".join(kept)


def _pincode(addr: dict) -> str:
    digits = re.sub(r"\D", "", str(addr.get("pincode") or ""))
    return "" if digits in _PLACEHOLDERS or len(digits) < 4 else digits


def _coords(addr: dict) -> tuple[float, float] | None:
    lat, lng = addr.get("latitude"), addr.get("longitude")
    if lat is None or lng is None:
        return None
    try:
        return float(lat), float(lng)
    except (TypeError, ValueError):
        return None


def same_place(existing: dict, incoming: dict, radius_m: float = SAME_PLACE_RADIUS_M) -> float | None:
    """How far apart (metres) `incoming` is from `existing` when they are
    the same place, else None. A text match (no pin on one side) scores
    `radius_m`, so a real pin match always ranks ahead of it."""
    a, b = _coords(existing), _coords(incoming)
    if a and b:
        metres = haversine_km(a[0], a[1], b[0], b[1]) * 1000
        return metres if metres <= radius_m else None
    pin_a, pin_b = _pincode(existing), _pincode(incoming)
    if pin_a and pin_b and pin_a != pin_b:
        return None
    drop = [existing.get("city"), existing.get("state"), incoming.get("city"), incoming.get("state")]
    text_a = normalize_address_text(existing.get("line1"), drop=drop)
    text_b = normalize_address_text(incoming.get("line1"), drop=drop)
    return float(radius_m) if text_a and text_a == text_b else None


def fill_empty_fields(existing: dict, incoming: dict) -> dict:
    """What `incoming` may add to the reused record: only fields the record
    is MISSING (a landmark / flat line, a pin, a real city/state/pincode in
    place of a stand-in). A filled field is never overwritten."""
    updates: dict = {}
    for field in ("landmark", "line2"):
        value = (incoming.get(field) or "").strip()
        if value and not (existing.get(field) or "").strip():
            updates[field] = value
    if _coords(incoming) and not _coords(existing):
        updates["latitude"], updates["longitude"] = _coords(incoming)
    for field in ("city", "state"):
        value = (incoming.get(field) or "").strip()
        if value not in _PLACEHOLDERS and (existing.get(field) or "").strip() in _PLACEHOLDERS:
            updates[field] = value
    pin = _pincode(incoming)
    if pin and not _pincode(existing):
        updates["pincode"] = pin
    return updates
