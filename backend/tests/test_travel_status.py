"""
Travel-status surface: store->customer persisted (and lazily backfilled),
captain->customer computed from the captain's last ping while assigned /
en route, and the same authz walls as get_booking. Google keys are blank
in tests, so every leg deterministically uses the haversine source.
"""
from datetime import timedelta

import pytest
from bson import ObjectId

from app.core.exceptions import NotFoundException
from app.schemas.booking_schema import BookingAssignCaptainRequest, BookingCreateRequest
from app.services.booking_service import BookingService
from app.utils.timezone import now_ist

from tests.factories import get_star_wash_service_id, get_hatchback_type_id, make_captain, make_customer_with_vehicle, make_service_center


@pytest.fixture
async def rig(db, cleanup):
    hatchback = await get_hatchback_type_id(db)
    foam = await get_star_wash_service_id(db)
    center_id = await make_service_center(db, working_hours_start="09:00", working_hours_end="21:00", slot_duration_minutes=180, pincode="452777")
    cleanup.append(("service_centers", {"_id": ObjectId(center_id)}))
    # Center gets coordinates so the store leg can compute.
    await db.service_centers.update_one({"_id": ObjectId(center_id)}, {"$set": {"latitude": 22.7000, "longitude": 75.8000}})
    captain_id = await make_captain(db, center_id)
    cleanup.append(("users", {"_id": ObjectId(captain_id)}))
    customer_id, vehicle_id, address_id = await make_customer_with_vehicle(db, hatchback, pincode="452777")
    cleanup.append(("users", {"_id": ObjectId(customer_id)}))
    cleanup.append(("vehicles", {"owner_id": customer_id}))
    cleanup.append(("addresses", {"owner_id": customer_id}))
    cleanup.append(("bookings", {"customer_id": customer_id}))
    cleanup.append(("notifications", {"user_id": {"$in": [customer_id, captain_id]}}))
    cleanup.append(("purchase_confirmations", {"customer_id": customer_id}))
    cleanup.append(("slot_capacity", {"service_center_id": center_id}))
    cleanup.append(("daily_capacity", {"service_center_id": center_id}))
    # Address pinned right next to the factory center (dispatch is
    # nearest-by-coords, so this keeps the booking on OUR center).
    await db.addresses.update_one({"_id": ObjectId(address_id)}, {"$set": {"latitude": 22.7010, "longitude": 75.8010}})

    svc = BookingService(db)
    date_str = (now_ist().date() + timedelta(days=2)).isoformat()
    slots = await svc.available_slots(center_id, date_str)
    booking = await svc.create_booking(customer_id, BookingCreateRequest(
        vehicle_id=vehicle_id, address_id=address_id, service_ids=[foam],
        scheduled_date=now_ist().replace(tzinfo=None) + timedelta(days=2), scheduled_slot=slots[0]["key"],
    ))
    return {"db": db, "svc": svc, "booking": booking, "customer_id": customer_id, "captain_id": captain_id, "center_id": center_id}


@pytest.mark.asyncio
async def test_store_leg_persisted_at_creation(rig):
    out = await rig["svc"].travel_status(rig["booking"]["id"], rig["customer_id"], "customer")
    assert out["store_to_customer"] is not None
    assert out["store_to_customer"]["km"] > 0
    assert out["store_to_customer"]["source"] == "haversine"  # keys blank in tests
    assert out["captain_to_customer"] is None  # nobody assigned yet


@pytest.mark.asyncio
async def test_captain_leg_appears_once_assigned_with_a_ping(rig, db):
    await rig["svc"].assign_captain(rig["booking"]["id"], BookingAssignCaptainRequest(captain_id=rig["captain_id"]), assigned_by="admin-test", actor_role="admin", actor_center_id=None)
    # No ping yet -> no captain leg.
    out = await rig["svc"].travel_status(rig["booking"]["id"], rig["customer_id"], "customer")
    assert out["captain_to_customer"] is None

    await db.users.update_one(
        {"_id": ObjectId(rig["captain_id"])},
        {"$set": {"last_known_location": {"latitude": 22.7100, "longitude": 75.8100}, "last_location_at": now_ist().replace(tzinfo=None)}},
    )
    out = await rig["svc"].travel_status(rig["booking"]["id"], rig["customer_id"], "customer")
    leg = out["captain_to_customer"]
    assert leg is not None and leg["km"] > 0
    assert leg["captain_name"]


@pytest.mark.asyncio
async def test_travel_status_authz(rig, db, cleanup):
    hatchback = await get_hatchback_type_id(db)
    stranger_id, _, _ = await make_customer_with_vehicle(db, hatchback)
    cleanup.append(("users", {"_id": ObjectId(stranger_id)}))
    cleanup.append(("vehicles", {"owner_id": stranger_id}))
    cleanup.append(("addresses", {"owner_id": stranger_id}))
    with pytest.raises(NotFoundException):
        await rig["svc"].travel_status(rig["booking"]["id"], stranger_id, "customer")
