"""
Converts raw MongoDB documents (with ObjectId/datetime values) into
JSON-safe dicts. Used by services that return plain dicts instead of
building full Pydantic response schemas for every collection.
"""
from datetime import datetime
from typing import Any

from bson import ObjectId

from app.utils.timezone import from_stored

# Keys holding *computed* timestamps — aware (IST or UTC) at write time, so
# Mongo converted them to a real UTC instant on insert. Motor hands them back
# NAIVE on read (no tz_aware on the client), so a value under one of these
# keys is a naive datetime whose digits ARE a UTC instant, and must go
# through from_stored() before .isoformat() or the API silently returns a
# UTC digit-string with no offset marker (which the frontend then misreads
# as its own local time). Checked by key name at every nesting depth, so
# e.g. before_photo.captured_at and after_photo.captured_at both match
# "captured_at".
#
# This is the ONE place to extend when a new computed timestamp is added
# anywhere in the codebase — do NOT solve this by calling from_stored() at
# write sites instead, that's what caused the original 5.5-hour bug (see
# KNOWN_ISSUES.md, "self-inflicted" entry) and it's an easy mistake to
# reintroduce.
#
# Do NOT add booking.scheduled_date / scheduled_slot / any other genuinely
# naive user-entered wall-clock field here — those must stay untouched,
# that's to_ist() territory, not from_stored()'s.
COMPUTED_INSTANT_KEYS = frozenset({
    "created_at", "updated_at", "deleted_at",
    "heading_at", "issue_flagged_at", "vehicle_verified_at",
    "service_started_at", "completed_at", "captured_at",
    "start_date", "end_date",
    "valid_from", "valid_until",
    "last_login_at",
    "awaiting_assignment_since", "unassigned_reminder_sent_at", "late_start_reminder_sent_at",
    # Written via a raw update_one in inventory_repository.py::adjust_quantity
    # (bypasses BaseRepository.update_by_id's usual path) — same "computed
    # aware-UTC instant, read back naive" category as heading_at etc., so it
    # needs the same from_stored() treatment or it silently reproduces the
    # 5.5-hour display bug for restock timestamps.
    "last_restocked_at",
})


def serialize_value(value: Any, key: str | None = None) -> Any:
    if isinstance(value, ObjectId):
        return str(value)
    if isinstance(value, datetime):
        if key in COMPUTED_INSTANT_KEYS and value.tzinfo is None:
            value = from_stored(value)
        return value.isoformat()
    if isinstance(value, dict):
        return serialize_doc(value)
    if isinstance(value, list):
        return [serialize_value(v) for v in value]
    return value


def serialize_doc(doc: dict | None) -> dict | None:
    if doc is None:
        return None
    result = {}
    for key, value in doc.items():
        if key == "_id":
            result["id"] = str(value)
            continue
        result[key] = serialize_value(value, key)
    return result


def serialize_list(docs: list[dict]) -> list[dict]:
    return [serialize_doc(d) for d in docs]
