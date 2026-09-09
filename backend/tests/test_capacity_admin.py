"""
Spec Sections 2, 22: admin/manager can increase/decrease per-slot capacity,
close/reopen a slot, and see real booked/remaining counts — changes take
effect immediately (no caching layer to invalidate), and the backend stays
the source of truth even after capacity is reduced below the current
booked count.
"""
from datetime import datetime, timedelta, timezone

import pytest
from bson import ObjectId

from app.core.exceptions import BadRequestException
from app.schemas.booking_schema import BookingCreateRequest
from app.services.booking_service import BookingService

from app.utils.timezone import now_ist

from tests.factories import get_star_wash_service_id, get_hatchback_type_id, make_customer_with_vehicle, make_service_center


@pytest.fixture
async def rig(db, cleanup):
    hatchback = await get_hatchback_type_id(db)
    foam = await get_star_wash_service_id(db)
    center_id = await make_service_center(db, default_slot_capacity=5)
    cleanup.append(("service_centers", {"_id": ObjectId(center_id)}))
    cleanup.append(("slot_capacity", {"service_center_id": center_id}))
    cleanup.append(("daily_capacity", {"service_center_id": center_id}))
    return {"db": db, "center_id": center_id, "hatchback": hatchback, "foam": foam}


@pytest.mark.asyncio
async def test_admin_view_shows_real_numbers_not_the_customer_wording(rig):
    bs = BookingService(rig["db"])
    tomorrow = now_ist().replace(tzinfo=None) + timedelta(days=1)
    admin_view = await bs.admin_slot_capacity(rig["center_id"], tomorrow.strftime("%Y-%m-%d"))
    slot = next(s for s in admin_view["slots"] if s["key"] == "09:00-12:00")
    assert slot["capacity"] == 5
    assert slot["booked_count"] == 0
    assert slot["remaining"] == 5  # real numbers, not the "Available"/"Only N left" customer wording


@pytest.mark.asyncio
async def test_admin_can_increase_capacity_and_it_applies_immediately(rig, cleanup):
    bs = BookingService(rig["db"])
    tomorrow = now_ist().replace(tzinfo=None) + timedelta(days=1)
    date_str = tomorrow.strftime("%Y-%m-%d")

    # Fill the default capacity of 5.
    for _ in range(5):
        customer_id, vehicle_id, address_id = await make_customer_with_vehicle(rig["db"], rig["hatchback"])
        cleanup.append(("users", {"_id": ObjectId(customer_id)}))
        cleanup.append(("vehicles", {"owner_id": customer_id}))
        cleanup.append(("addresses", {"owner_id": customer_id}))
        cleanup.append(("bookings", {"customer_id": customer_id}))
        await bs.create_booking(customer_id, BookingCreateRequest(vehicle_id=vehicle_id, address_id=address_id, service_ids=[rig["foam"]], scheduled_date=tomorrow, scheduled_slot="09:00-12:00"))

    slots = await bs.available_slots(rig["center_id"], date_str)
    assert next(s for s in slots if s["key"] == "09:00-12:00")["status"] == "full"

    # Admin raises capacity to 10 — must free up the slot immediately, no
    # separate cache/flag to flip.
    await bs.set_slot_capacity(rig["center_id"], date_str, "09:00-12:00", capacity=10, is_closed=None)
    slots = await bs.available_slots(rig["center_id"], date_str)
    slot = next(s for s in slots if s["key"] == "09:00-12:00")
    assert slot["status"] == "low" and slot["remaining"] == 5


@pytest.mark.asyncio
async def test_admin_can_close_a_slot_blocking_new_bookings(rig, cleanup):
    bs = BookingService(rig["db"])
    tomorrow = now_ist().replace(tzinfo=None) + timedelta(days=1)
    date_str = tomorrow.strftime("%Y-%m-%d")

    await bs.set_slot_capacity(rig["center_id"], date_str, "09:00-12:00", capacity=None, is_closed=True)
    slots = await bs.available_slots(rig["center_id"], date_str)
    slot = next(s for s in slots if s["key"] == "09:00-12:00")
    assert slot["status"] == "full"  # closed reads as unavailable, same customer wording as exhausted

    customer_id, vehicle_id, address_id = await make_customer_with_vehicle(rig["db"], rig["hatchback"])
    cleanup.append(("users", {"_id": ObjectId(customer_id)}))
    cleanup.append(("vehicles", {"owner_id": customer_id}))
    cleanup.append(("addresses", {"owner_id": customer_id}))
    cleanup.append(("bookings", {"customer_id": customer_id}))
    with pytest.raises(BadRequestException):
        await bs.create_booking(customer_id, BookingCreateRequest(vehicle_id=vehicle_id, address_id=address_id, service_ids=[rig["foam"]], scheduled_date=tomorrow, scheduled_slot="09:00-12:00"))

    # Reopen — bookings flow again.
    await bs.set_slot_capacity(rig["center_id"], date_str, "09:00-12:00", capacity=None, is_closed=False)
    booking = await bs.create_booking(customer_id, BookingCreateRequest(vehicle_id=vehicle_id, address_id=address_id, service_ids=[rig["foam"]], scheduled_date=tomorrow, scheduled_slot="09:00-12:00"))
    assert booking["status"] == "pending"


@pytest.mark.asyncio
async def test_reducing_capacity_below_booked_count_does_not_break_existing_bookings(rig, cleanup):
    """Section 24: capacity reduced after bookings already exist — those
    existing bookings must remain valid; only NEW bookings are blocked."""
    bs = BookingService(rig["db"])
    tomorrow = now_ist().replace(tzinfo=None) + timedelta(days=1)
    date_str = tomorrow.strftime("%Y-%m-%d")

    booked_ids = []
    for _ in range(5):
        customer_id, vehicle_id, address_id = await make_customer_with_vehicle(rig["db"], rig["hatchback"])
        cleanup.append(("users", {"_id": ObjectId(customer_id)}))
        cleanup.append(("vehicles", {"owner_id": customer_id}))
        cleanup.append(("addresses", {"owner_id": customer_id}))
        cleanup.append(("bookings", {"customer_id": customer_id}))
        b = await bs.create_booking(customer_id, BookingCreateRequest(vehicle_id=vehicle_id, address_id=address_id, service_ids=[rig["foam"]], scheduled_date=tomorrow, scheduled_slot="09:00-12:00"))
        booked_ids.append(b["id"])

    await bs.set_slot_capacity(rig["center_id"], date_str, "09:00-12:00", capacity=2, is_closed=None)

    for bid in booked_ids:
        booking = await rig["db"].bookings.find_one({"_id": ObjectId(bid)})
        assert booking["status"] == "pending"  # untouched, still valid

    doc = await rig["db"].slot_capacity.find_one({"service_center_id": rig["center_id"], "slot_key": "09:00-12:00"})
    assert doc["capacity"] == 2 and doc["booked_count"] == 5  # over capacity, but not corrupted
