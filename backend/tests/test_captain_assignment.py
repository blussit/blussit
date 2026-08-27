"""
Spec Sections 2 (capacity buckets shared across bookings), 6 (priority is
independent of assignment), 13/24 (concurrency): assigning/reassigning
captains, the corrected per-captain-per-slot design (several bookings in
one shared admin slot can go to the same captain at different
estimated_start_at values without a false conflict), and the atomic
concurrent-assignment race.
"""
import asyncio
from datetime import datetime, timedelta, timezone

import pytest
from bson import ObjectId

from app.core.exceptions import BadRequestException
from app.schemas.booking_schema import BookingAssignCaptainRequest, BookingCreateRequest, ReassignCaptainRequest
from app.services.booking_service import BookingService

from tests.factories import get_foam_wash_service_id, get_hatchback_type_id, make_captain, make_customer_with_vehicle, make_service_center


@pytest.fixture
async def rig(db, cleanup):
    hatchback = await get_hatchback_type_id(db)
    foam = await get_foam_wash_service_id(db)
    center_id = await make_service_center(db, working_hours_start="09:00", working_hours_end="19:00", slot_duration_minutes=180, default_slot_capacity=20)
    cleanup.append(("service_centers", {"_id": ObjectId(center_id)}))
    cleanup.append(("slot_capacity", {"service_center_id": center_id}))
    cleanup.append(("daily_capacity", {"service_center_id": center_id}))
    captain_id = await make_captain(db, center_id)
    cleanup.append(("users", {"_id": ObjectId(captain_id)}))
    cleanup.append(("captain_wallets", {"captain_id": captain_id}))
    return {"db": db, "center_id": center_id, "hatchback": hatchback, "foam": foam, "captain_id": captain_id}


async def _new_booking(rig, cleanup, tomorrow, slot="09:00-12:00"):
    customer_id, vehicle_id, address_id = await make_customer_with_vehicle(rig["db"], rig["hatchback"])
    cleanup.append(("users", {"_id": ObjectId(customer_id)}))
    cleanup.append(("vehicles", {"owner_id": customer_id}))
    cleanup.append(("addresses", {"owner_id": customer_id}))
    cleanup.append(("bookings", {"customer_id": customer_id}))
    bs = BookingService(rig["db"])
    return await bs.create_booking(customer_id, BookingCreateRequest(vehicle_id=vehicle_id, address_id=address_id, service_ids=[rig["foam"]], scheduled_date=tomorrow, scheduled_slot=slot))


@pytest.mark.asyncio
async def test_assign_captain_sets_estimated_start_at(rig, cleanup):
    bs = BookingService(rig["db"])
    tomorrow = datetime.now(timezone.utc) + timedelta(days=1)
    booking = await _new_booking(rig, cleanup, tomorrow)
    assigned = await bs.assign_captain(booking["id"], BookingAssignCaptainRequest(captain_id=rig["captain_id"]), "system", "admin", None)
    assert assigned["status"] == "assigned"
    assert assigned["captain_id"] == rig["captain_id"]
    assert assigned["estimated_start_at"] is not None


@pytest.mark.asyncio
async def test_same_captain_two_bookings_same_shared_slot_different_estimated_start(rig, cleanup):
    """The core design correction this project made: a shared admin slot
    bucket must not force every booking inside it to look like the same
    instant for conflict purposes."""
    bs = BookingService(rig["db"])
    tomorrow = datetime.now(timezone.utc) + timedelta(days=1)
    booking_a = await _new_booking(rig, cleanup, tomorrow)
    booking_b = await _new_booking(rig, cleanup, tomorrow)

    assigned_a = await bs.assign_captain(booking_a["id"], BookingAssignCaptainRequest(captain_id=rig["captain_id"]), "system", "admin", None)
    slot_start = datetime.fromisoformat(assigned_a["slot_start"]) if isinstance(assigned_a["slot_start"], str) else assigned_a["slot_start"]
    est_b = slot_start + timedelta(minutes=90)

    assigned_b = await bs.assign_captain(booking_b["id"], BookingAssignCaptainRequest(captain_id=rig["captain_id"], estimated_start_at=est_b), "system", "admin", None)
    assert assigned_b["status"] == "assigned"
    assert assigned_a["captain_id"] == assigned_b["captain_id"] == rig["captain_id"]


@pytest.mark.asyncio
async def test_same_captain_overlapping_estimated_start_is_rejected(rig, cleanup):
    bs = BookingService(rig["db"])
    tomorrow = datetime.now(timezone.utc) + timedelta(days=1)
    booking_a = await _new_booking(rig, cleanup, tomorrow)
    booking_b = await _new_booking(rig, cleanup, tomorrow)

    await bs.assign_captain(booking_a["id"], BookingAssignCaptainRequest(captain_id=rig["captain_id"]), "system", "admin", None)
    with pytest.raises(BadRequestException):
        await bs.assign_captain(booking_b["id"], BookingAssignCaptainRequest(captain_id=rig["captain_id"]), "system", "admin", None)


@pytest.mark.asyncio
async def test_concurrent_assignment_of_same_captain_never_double_books(rig, cleanup):
    """Regression: two managers assigning the SAME captain to two
    DIFFERENT (overlapping-time) bookings at nearly the same instant —
    only one may win."""
    bs_db = rig["db"]
    tomorrow = datetime.now(timezone.utc) + timedelta(days=1)
    bookings = [await _new_booking(rig, cleanup, tomorrow) for _ in range(4)]

    async def try_assign(booking_id):
        try:
            await BookingService(bs_db).assign_captain(booking_id, BookingAssignCaptainRequest(captain_id=rig["captain_id"]), "system", "admin", None)
            return "ok"
        except BadRequestException:
            return "rejected"

    results = await asyncio.gather(*[try_assign(b["id"]) for b in bookings])
    assert results.count("ok") == 1
    assert results.count("rejected") == 3


@pytest.mark.asyncio
async def test_reassign_captain_swaps_captain(rig, cleanup):
    bs = BookingService(rig["db"])
    tomorrow = datetime.now(timezone.utc) + timedelta(days=1)
    booking = await _new_booking(rig, cleanup, tomorrow)
    await bs.assign_captain(booking["id"], BookingAssignCaptainRequest(captain_id=rig["captain_id"]), "system", "admin", None)

    captain2_id = await make_captain(rig["db"], rig["center_id"])
    cleanup.append(("users", {"_id": ObjectId(captain2_id)}))
    cleanup.append(("captain_wallets", {"captain_id": captain2_id}))

    reassigned = await bs.reassign_captain(booking["id"], ReassignCaptainRequest(captain_id=captain2_id), "system", "admin", None)
    assert reassigned["captain_id"] == captain2_id
    assert reassigned["status"] == "assigned"


@pytest.mark.asyncio
async def test_captain_wallet_below_minimum_blocks_assignment(rig, cleanup):
    """wallet_gating_enabled is a GLOBAL policy flag, off by default (no
    real top-up path exists yet — see WalletService.is_eligible_for_assignment).
    Toggled on for just this test, then always restored, since leaving it on
    would silently break assignment in every other test in this session."""
    from app.services.booking_policy_service import BookingPolicyService

    policy_service = BookingPolicyService(rig["db"])
    await policy_service.set_policy({"wallet_gating_enabled": True})
    try:
        bs = BookingService(rig["db"])
        tomorrow = datetime.now(timezone.utc) + timedelta(days=1)
        booking = await _new_booking(rig, cleanup, tomorrow)

        poor_captain_id = await make_captain(rig["db"], rig["center_id"], wallet_balance=0.0)
        cleanup.append(("users", {"_id": ObjectId(poor_captain_id)}))
        cleanup.append(("captain_wallets", {"captain_id": poor_captain_id}))

        with pytest.raises(BadRequestException):
            await bs.assign_captain(booking["id"], BookingAssignCaptainRequest(captain_id=poor_captain_id), "system", "admin", None)
    finally:
        await policy_service.set_policy({"wallet_gating_enabled": False})
