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
    "start_date", "end_date", "extended_until",
    "valid_from", "valid_until",
    "last_login_at",
    "awaiting_assignment_since", "unassigned_reminder_sent_at", "late_start_reminder_sent_at",
    # Written via a raw update_one in inventory_repository.py::adjust_quantity
    # (bypasses BaseRepository.update_by_id's usual path) — same "computed
    # aware-UTC instant, read back naive" category as heading_at etc., so it
    # needs the same from_stored() treatment or it silently reproduces the
    # 5.5-hour display bug for restock timestamps.
    "last_restocked_at",
    # Coverage leads (uncovered-area demand capture) — written with
    # datetime.now(timezone.utc) in CoverageLeadService.capture, read back
    # naive like every other computed instant.
    "last_requested_at",
    # BLUSSIT slot/capacity/timeline update — slot_start/slot_end are built
    # via to_ist(...) at booking creation (see _resolve_slot_window),
    # estimated_start_at via _resolve_estimated_start (also to_ist()-based),
    # and assigned_at/manager_notified_at/closed_at/last_location_at are all
    # now_ist() at write time — every one of them is aware-at-write-time and
    # read back naive, exactly like heading_at/completed_at above.
    "slot_start", "slot_end", "estimated_start_at",
    "assigned_at", "manager_notified_at", "closed_at",
    "last_location_at",
    # Pass reminder bookkeeping (UserSubscriptionModel) — now_ist() /
    # datetime.now(utc) at write time, read back naive.
    "last_used_at", "wash_reminder_sent_at", "used_up_notice_sent_at", "last_renewed_at",
    # Society plans (society_service.py) — now_ist() at write time.
    "cycle_start", "prev_cycle_start", "arrived_at", "activated_at", "cancelled_at", "washed_updated_at",
    # Booking money/edit bookkeeping (feature build 2026-10-07) — every one
    # is now_ist() / datetime.now(utc) at write time: a manager's discount
    # and tip on a done job, when a logged job was entered, a refund, cash
    # handed over, a customer's own edit, the pre-slot reminder, and the
    # `at` of history rows (customer_charges.history, added_services,
    # booking edit history) — "at" is only ever a computed instant here.
    "manager_discount_at", "tip_updated_at", "logged_at", "refunded_at", "cash_collected_at",
    "customer_edited_at", "customer_reminder_sent_at", "consumption_forfeited_at", "at",
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
