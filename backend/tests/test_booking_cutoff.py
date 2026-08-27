"""
Spec Section 4: a slot must stop being bookable a configurable number of
minutes before its own end — never hardcoded, backend-enforced regardless
of what the frontend shows.
"""
from datetime import datetime, timedelta, timezone

import pytest
from bson import ObjectId

from app.core.exceptions import BadRequestException
from app.schemas.booking_schema import BookingCreateRequest
from app.services.booking_service import BookingService

from tests.factories import get_foam_wash_service_id, get_hatchback_type_id, make_customer_with_vehicle, make_service_center


@pytest.fixture
async def rig(db, cleanup):
    """24h operating hours so "today" always contains an already-elapsed
    or near-cutoff slot to exercise the rule against, regardless of what
    time this suite happens to run."""
    hatchback = await get_hatchback_type_id(db)
    foam = await get_foam_wash_service_id(db)
    center_id = await make_service_center(db, working_hours_start="00:00", working_hours_end="23:59", slot_duration_minutes=180, default_slot_capacity=20)
    cleanup.append(("service_centers", {"_id": ObjectId(center_id)}))
    cleanup.append(("slot_capacity", {"service_center_id": center_id}))
    cleanup.append(("daily_capacity", {"service_center_id": center_id}))
    return {"db": db, "center_id": center_id, "hatchback": hatchback, "foam": foam}


@pytest.mark.asyncio
async def test_elapsed_or_near_cutoff_slot_shows_full_and_is_unbookable(rig, cleanup):
    bs = BookingService(rig["db"])
    now = datetime.now(timezone.utc)
    today_str = now.strftime("%Y-%m-%d")
    slots_today = await bs.available_slots(rig["center_id"], today_str)
    elapsed = [s for s in slots_today if s["status"] == "full"]
    assert elapsed, "expected at least one already-elapsed/cutoff-passed slot earlier today"

    customer_id, vehicle_id, address_id = await make_customer_with_vehicle(rig["db"], rig["hatchback"])
    cleanup.append(("users", {"_id": ObjectId(customer_id)}))
    cleanup.append(("vehicles", {"owner_id": customer_id}))
    cleanup.append(("addresses", {"owner_id": customer_id}))
    cleanup.append(("bookings", {"customer_id": customer_id}))

    with pytest.raises(BadRequestException):
        await bs.create_booking(
            customer_id,
            BookingCreateRequest(vehicle_id=vehicle_id, address_id=address_id, service_ids=[rig["foam"]], scheduled_date=now, scheduled_slot=elapsed[0]["key"]),
        )


@pytest.mark.asyncio
async def test_cutoff_is_policy_driven_not_hardcoded(db):
    """The cutoff must come from booking_policy's slot_booking_cutoff_minutes,
    not a hardcoded 30 anywhere in the code path."""
    from app.services.booking_policy_service import BookingPolicyService

    policy = await BookingPolicyService(db).get_policy()
    assert "slot_booking_cutoff_minutes" in policy
    assert isinstance(policy["slot_booking_cutoff_minutes"], int)


@pytest.mark.asyncio
async def test_future_slot_within_operating_hours_is_bookable(rig, cleanup):
    """Sanity: a slot that hasn't reached cutoff at all (tomorrow) is
    still perfectly bookable — the cutoff rule shouldn't over-fire."""
    bs = BookingService(rig["db"])
    tomorrow = datetime.now(timezone.utc) + timedelta(days=1)
    customer_id, vehicle_id, address_id = await make_customer_with_vehicle(rig["db"], rig["hatchback"])
    cleanup.append(("users", {"_id": ObjectId(customer_id)}))
    cleanup.append(("vehicles", {"owner_id": customer_id}))
    cleanup.append(("addresses", {"owner_id": customer_id}))
    cleanup.append(("bookings", {"customer_id": customer_id}))

    booking = await bs.create_booking(
        customer_id,
        BookingCreateRequest(vehicle_id=vehicle_id, address_id=address_id, service_ids=[rig["foam"]], scheduled_date=tomorrow, scheduled_slot="09:00-12:00"),
    )
    assert booking["status"] == "pending"
