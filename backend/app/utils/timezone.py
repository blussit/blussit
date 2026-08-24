"""
The platform runs exclusively in India, so every business-facing time
(operating hours, booking slots, "now" for scheduling checks) is computed in
IST — never left ambiguous as naive UTC. Storage in MongoDB is unaffected
(BSON always normalizes to UTC on the wire); what matters is that we
interpret and compare wall-clock times correctly in IST terms.
"""
from datetime import datetime, timedelta, timezone

IST = timezone(timedelta(hours=5, minutes=30), name="IST")


def now_ist() -> datetime:
    return datetime.now(IST)


def to_ist(dt: datetime) -> datetime:
    """For *user-entered* wall-clock values only (a booking's scheduled_date)
    that are naive from the moment they're parsed — MongoDB round-trips
    naive datetimes byte-for-byte (this driver isn't tz_aware), so a naive
    value here still holds its original IST digits and should be tagged as
    IST, not converted.

    NOT for coupon valid_from/valid_until — the frontend sends those as real
    aware UTC timestamps ("Z"-suffixed ISO strings), so they round-trip
    through Mongo as a true UTC instant and belong to from_stored()'s
    category instead. Using to_ist() on them was an earlier bug (see
    KNOWN_ISSUES.md) — check which category a field actually falls into
    before picking a helper, this is an easy mistake to reintroduce."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=IST)
    return dt.astimezone(IST)


def from_stored(dt: datetime) -> datetime:
    """For *computed* timestamps that were timezone-aware at write time
    (now_ist() results like heading_at, captured_at, issue_flagged_at,
    created_at/updated_at) — MongoDB converts aware datetimes to a real UTC
    instant before storing, so a naive value read back here represents UTC,
    not IST. Converts it to IST for display/duration math. Mixing this up
    with to_ist() silently reintroduces the same 5.5-hour bug this module
    exists to prevent — check which category a field falls into before use."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc).astimezone(IST)
    return dt.astimezone(IST)
