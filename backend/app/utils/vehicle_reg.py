"""
Indian vehicle registration number validation.

Two live formats are accepted:
  1. Standard state series:  SS RR L(0-3) NNNN
       SS  = real state/UT code (whitelisted below),
       RR  = 1-2 digit RTO district code,
       L   = 0-3 series letters (older plates can have none;
             Delhi-style DL1CXY9876 has three),
       NNNN = 4-digit number.
     Examples: MP09AB1234, DL1CXY9876, KA05MN4321, MH121234 (old series).
  2. Bharat (BH) series (2021+): YY BH NNNN L(1-2)
     Example: 22BH1234AB.

Separators/spacing/case are ignored: "mp 09-ab 1234" == "MP09AB1234".
"""
import re

# Current states + UTs, plus still-on-the-road legacy codes (OR→OD rename,
# UA→UK rename, DN/DD merger, TS/TG both seen on Telangana plates).
STATE_CODES = {
    "AN", "AP", "AR", "AS", "BR", "CG", "CH", "DD", "DL", "DN", "GA", "GJ",
    "HP", "HR", "JH", "JK", "KA", "KL", "LA", "LD", "MH", "ML", "MN", "MP",
    "MZ", "NL", "OD", "OR", "PB", "PY", "RJ", "SK", "TN", "TR", "TS", "TG",
    "UK", "UA", "UP", "WB",
}

_STANDARD = re.compile(r"^([A-Z]{2})(\d{1,2})([A-Z]{0,3})(\d{4})$")
_BHARAT = re.compile(r"^(\d{2})(BH)(\d{4})([A-Z]{1,2})$")


def normalize_registration(raw: str) -> str:
    return "".join(ch for ch in raw.upper() if ch.isalnum())


def validate_indian_registration(raw: str) -> str | None:
    """Returns the canonical (separator-free, uppercase) plate if valid,
    None otherwise."""
    plate = normalize_registration(raw)
    if not (6 <= len(plate) <= 11):
        return None
    m = _STANDARD.match(plate)
    if m and m.group(1) in STATE_CODES:
        return plate
    if _BHARAT.match(plate):
        return plate
    return None
