"""
Spec Section 7: captain start-time window adapted to slots — the existing
30-minute pre-start rule is preserved, anchored to the booking's own
estimated_start_at, and enforced on the backend (not just disabled in the
UI). Also exercises Section 10's automatic delay flagging.
"""
from datetime import datetime, timedelta, timezone

import pytest
from bson import ObjectId

from app.core.exceptions import BadRequestException
from app.schemas.booking_schema import BookingAssignCaptainRequest, BookingCreateRequest, HeadingRequest
from app.services.booking_service import BookingService

from tests.factories import get_foam_wash_service_id, get_hatchback_type_id, make_captain, make_customer_with_vehicle, make_service_center


@pytest.fixture
async def rig(db, cleanup):
    hatchback = await get_hatchback_type_id(db)
    foam = await get_foam_wash_service_id(db)
    center_id = await make_service_center(db, working_hours_start="00:00", working_hours_end="23:59", slot_duration_minutes=180, default_slot_capacity=20)
    cleanup.append(("service_centers", {"_id": ObjectId(center_id)}))
    cleanup.append(("slot_capacity", {"service_center_id": center_id}))
    cleanup.append(("daily_capacity", {"service_center_id": center_id}))
    captain_id = await make_captain(db, center_id)
    cleanup.append(("users", {"_id": ObjectId(captain_id)}))
    cleanup.append(("captain_wallets", {"captain_id": captain_id}))
    customer_id, vehicle_id, address_id = await make_customer_with_vehicle(db, hatchback)
    cleanup.append(("users", {"_id": ObjectId(customer_id)}))
    cleanup.append(("vehicles", {"owner_id": customer_id}))
    cleanup.append(("addresses", {"owner_id": customer_id}))
    cleanup.append(("bookings", {"customer_id": customer_id}))
    return {"db": db, "center_id": center_id, "foam": foam, "captain_id": captain_id, "customer_id": customer_id, "vehicle_id": vehicle_id, "address_id": address_id}


def _find_slot_far_in_future(now: datetime) -> tuple[datetime, str]:
    """A slot key whose start is comfortably more than 30 minutes from
    now, on a date far enough out that scheduling math never crosses
    midnight awkwardly for this 24h-hours test center."""
    target_date = now + timedelta(days=1)
    return target_date, "09:00-12:00"


@pytest.mark.asyncio
async def test_heading_before_window_is_rejected(rig):
    bs = BookingService(rig["db"])
    now = datetime.now(timezone.utc)
    target_date, slot_key = _find_slot_far_in_future(now)
    booking = await bs.create_booking(rig["customer_id"], BookingCreateRequest(vehicle_id=rig["vehicle_id"], address_id=rig["address_id"], service_ids=[rig["foam"]], scheduled_date=target_date, scheduled_slot=slot_key))
    await bs.assign_captain(booking["id"], BookingAssignCaptainRequest(captain_id=rig["captain_id"]), "system", "admin", None)

    # The slot starts far in the future (tomorrow 09:00) — heading out now
    # is well outside the 30-minute pre-start window.
    with pytest.raises(BadRequestException, match="Too early"):
        await bs.start_heading(booking["id"], HeadingRequest(latitude=22.7, longitude=75.8, equipment_used=[]), rig["captain_id"])


@pytest.mark.asyncio
async def test_heading_within_30_minutes_of_start_succeeds(rig):
    """A booking whose estimated_start_at is only 20 minutes from now must
    be headable (inside the 30-minute pre-start window). The document's
    estimated_start_at is set directly rather than via assign_captain's
    payload, the same "backdate the timestamp directly" pattern used for
    the late-start case below — assign_captain's own validation legitimately
    restricts a manager-chosen estimated_start_at to fall inside the
    booking's slot window, which isn't what this test is exercising."""
    bs = BookingService(rig["db"])
    now = datetime.now(timezone.utc)
    target_date, slot_key = _find_slot_far_in_future(now)
    booking = await bs.create_booking(rig["customer_id"], BookingCreateRequest(vehicle_id=rig["vehicle_id"], address_id=rig["address_id"], service_ids=[rig["foam"]], scheduled_date=target_date, scheduled_slot=slot_key))
    await bs.assign_captain(booking["id"], BookingAssignCaptainRequest(captain_id=rig["captain_id"]), "system", "admin", None)

    from app.utils.timezone import now_ist

    soon = now_ist() + timedelta(minutes=20)
    await rig["db"].bookings.update_one({"_id": ObjectId(booking["id"])}, {"$set": {"estimated_start_at": soon}})

    result = await bs.start_heading(booking["id"], HeadingRequest(latitude=22.7, longitude=75.8, equipment_used=[]), rig["captain_id"])
    assert result["status"] == "captain_on_the_way"
    assert result["captain_start_stage"] == "early"
    assert result["heading_at"] is not None


@pytest.mark.asyncio
async def test_late_start_is_flagged_with_penalty(rig):
    """Starting well after the slot window but still well inside the
    captain_start_lockout_hours threshold (simulated via a backdated
    estimated_start_at, the codebase's own established pattern for testing
    time-based sweep logic — see the plan's test_captain_start_window.py
    entry) incurs the late-start stage/penalty and does not crash — a late
    start alone must still be ALLOWED, just penalized."""
    bs = BookingService(rig["db"])
    now = datetime.now(timezone.utc)
    target_date, slot_key = _find_slot_far_in_future(now)
    booking = await bs.create_booking(rig["customer_id"], BookingCreateRequest(vehicle_id=rig["vehicle_id"], address_id=rig["address_id"], service_ids=[rig["foam"]], scheduled_date=target_date, scheduled_slot=slot_key))
    assigned = await bs.assign_captain(booking["id"], BookingAssignCaptainRequest(captain_id=rig["captain_id"]), "system", "admin", None)

    # Backdate estimated_start_at directly on the document to simulate
    # "now" being well past the slot's end + grace period (foam-wash is
    # 40 minutes, +30 min grace = 70 min window_end — 2 hours past that is
    # solidly "severely_late" but nowhere near the default 4h lockout).
    from app.utils.timezone import now_ist

    far_past = now_ist() - timedelta(hours=2)
    await rig["db"].bookings.update_one({"_id": ObjectId(booking["id"])}, {"$set": {"estimated_start_at": far_past}})

    result = await bs.start_heading(booking["id"], HeadingRequest(latitude=22.7, longitude=75.8, equipment_used=[]), rig["captain_id"])
    assert result["captain_start_stage"] == "severely_late"
    assert result["late_penalty_pct"] > 0


@pytest.mark.asyncio
async def test_start_heading_blocked_once_window_missed_by_days(rig):
    """The gap this fixes: a captain must NOT be able to just "start
    heading" on a booking that's been sitting unstarted for days — past
    captain_start_lockout_hours, start_heading is blocked outright and
    needs a manager reschedule/reassign, not just a bigger penalty."""
    bs = BookingService(rig["db"])
    now = datetime.now(timezone.utc)
    target_date, slot_key = _find_slot_far_in_future(now)
    booking = await bs.create_booking(rig["customer_id"], BookingCreateRequest(vehicle_id=rig["vehicle_id"], address_id=rig["address_id"], service_ids=[rig["foam"]], scheduled_date=target_date, scheduled_slot=slot_key))
    await bs.assign_captain(booking["id"], BookingAssignCaptainRequest(captain_id=rig["captain_id"]), "system", "admin", None)

    from app.utils.timezone import now_ist

    days_past = now_ist() - timedelta(days=2)
    await rig["db"].bookings.update_one({"_id": ObjectId(booking["id"])}, {"$set": {"estimated_start_at": days_past}})

    with pytest.raises(BadRequestException, match="expired too long ago"):
        await bs.start_heading(booking["id"], HeadingRequest(latitude=22.7, longitude=75.8, equipment_used=[]), rig["captain_id"])

    # Confirmed still blocked, not silently downgraded to another stage.
    unchanged = await rig["db"].bookings.find_one({"_id": ObjectId(booking["id"])})
    assert unchanged["status"] == "assigned"


@pytest.mark.asyncio
async def test_sweep_flags_missed_window_distinctly_once_locked_out(rig):
    """flag_late_to_start (the automated sweep behind main.py's reminder
    loop) must switch from the "nudge the captain" flag to a distinct
    "needs a manager decision" flag once the same lockout threshold is
    crossed — this is what the manager queue's Flagged Issues section
    reads to explain WHY a booking needs reschedule/reassign rather than
    just "hasn't started yet"."""
    bs = BookingService(rig["db"])
    now = datetime.now(timezone.utc)
    target_date, slot_key = _find_slot_far_in_future(now)
    booking = await bs.create_booking(rig["customer_id"], BookingCreateRequest(vehicle_id=rig["vehicle_id"], address_id=rig["address_id"], service_ids=[rig["foam"]], scheduled_date=target_date, scheduled_slot=slot_key))
    assigned = await bs.assign_captain(booking["id"], BookingAssignCaptainRequest(captain_id=rig["captain_id"]), "system", "admin", None)

    from app.utils.timezone import now_ist

    days_past = now_ist() - timedelta(days=2)
    await rig["db"].bookings.update_one({"_id": ObjectId(booking["id"])}, {"$set": {"estimated_start_at": days_past}})

    fresh = await rig["db"].bookings.find_one({"_id": ObjectId(booking["id"])})
    await bs.flag_late_to_start(fresh)

    flagged = await rig["db"].bookings.find_one({"_id": ObjectId(booking["id"])})
    assert flagged["issue_flag"] == "captain_missed_window"
    assert flagged["issue_resolved"] is False
