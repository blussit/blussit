"""
Spec Sections 1-3, 22, 24: admin-generated slots, per-slot capacity, the
customer-facing "Available / Only N spot(s) left / Fully booked" wording
(never the raw number), and the concurrent-last-slot race.
"""
import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from app.core.exceptions import BadRequestException
from app.schemas.booking_schema import BookingCancelRequest, BookingCreateRequest
from app.services.booking_service import BookingService

from app.utils.timezone import now_ist

from tests.factories import (
    get_star_wash_service_id,
    get_hatchback_type_id,
    make_customer_with_vehicle,
    make_service_center,
)


@pytest.fixture
async def rig(db, cleanup):
    """A dedicated 09:00-13:00, 3h-slot, 20-capacity center — isolated
    from any other test's bookings so capacity assertions are exact."""
    hatchback = await get_hatchback_type_id(db)
    foam = await get_star_wash_service_id(db)
    center_id = await make_service_center(db, working_hours_start="09:00", working_hours_end="13:00", slot_duration_minutes=180, default_slot_capacity=20)
    cleanup.append(("service_centers", {"_id": __import__("bson").ObjectId(center_id)}))
    cleanup.append(("slot_capacity", {"service_center_id": center_id}))
    cleanup.append(("daily_capacity", {"service_center_id": center_id}))
    return {"db": db, "center_id": center_id, "hatchback": hatchback, "foam": foam}


async def _book(db, cleanup, rig, tomorrow, slot_key="09:00-12:00"):
    customer_id, vehicle_id, address_id = await make_customer_with_vehicle(db, rig["hatchback"])
    cleanup.append(("users", {"_id": __import__("bson").ObjectId(customer_id)}))
    cleanup.append(("vehicles", {"owner_id": customer_id}))
    cleanup.append(("addresses", {"owner_id": customer_id}))
    cleanup.append(("bookings", {"customer_id": customer_id}))
    bs = BookingService(db)
    return await bs.create_booking(
        customer_id,
        BookingCreateRequest(vehicle_id=vehicle_id, address_id=address_id, service_ids=[rig["foam"]], scheduled_date=tomorrow, scheduled_slot=slot_key),
    ), customer_id, vehicle_id, address_id


@pytest.mark.asyncio
async def test_slots_are_generated_from_center_hours_with_remainder(rig):
    bs = BookingService(rig["db"])
    tomorrow = now_ist().replace(tzinfo=None) + timedelta(days=1)
    slots = await bs.available_slots(rig["center_id"], tomorrow.strftime("%Y-%m-%d"))
    # 09:00-13:00 at 180min -> one full 09:00-12:00 slot, remainder 12:00-13:00
    assert [s["key"] for s in slots] == ["09:00-12:00", "12:00-13:00"]


@pytest.mark.asyncio
async def test_availability_never_exposes_raw_capacity(rig, cleanup):
    """Section 3: total capacity must never appear in the response, at any
    fill level — not just hidden by the frontend."""
    bs = BookingService(rig["db"])
    tomorrow = now_ist().replace(tzinfo=None) + timedelta(days=1)
    slots = await bs.available_slots(rig["center_id"], tomorrow.strftime("%Y-%m-%d"))
    slot = slots[0]
    assert slot["status"] == "available" and slot["remaining"] is None
    for key in slot:
        assert key in {"key", "start", "end", "status", "remaining"}
    assert "capacity" not in slot and "booked_count" not in slot


@pytest.mark.asyncio
async def test_low_and_full_wording_thresholds(rig, cleanup):
    """Section 3: status flips to "low" (remaining shown) once remaining <=
    5, and to "full" (remaining=0) once the slot is exhausted — exact
    thresholds, not approximate."""
    bs = BookingService(rig["db"])
    tomorrow = now_ist().replace(tzinfo=None) + timedelta(days=1)
    for _ in range(15):  # 20 capacity - 15 = 5 remaining -> "low"
        await _book(rig["db"], cleanup, rig, tomorrow)
    slots = await bs.available_slots(rig["center_id"], tomorrow.strftime("%Y-%m-%d"))
    slot = next(s for s in slots if s["key"] == "09:00-12:00")
    assert slot["status"] == "low" and slot["remaining"] == 5

    for _ in range(5):  # fill the remaining 5 -> "full"
        await _book(rig["db"], cleanup, rig, tomorrow)
    slots = await bs.available_slots(rig["center_id"], tomorrow.strftime("%Y-%m-%d"))
    slot = next(s for s in slots if s["key"] == "09:00-12:00")
    assert slot["status"] == "full" and slot["remaining"] == 0


@pytest.mark.asyncio
async def test_booking_a_full_slot_is_rejected(rig, cleanup):
    bs = BookingService(rig["db"])
    tomorrow = now_ist().replace(tzinfo=None) + timedelta(days=1)
    for _ in range(20):
        await _book(rig["db"], cleanup, rig, tomorrow)
    customer_id, vehicle_id, address_id = await make_customer_with_vehicle(rig["db"], rig["hatchback"])
    cleanup.append(("users", {"_id": __import__("bson").ObjectId(customer_id)}))
    cleanup.append(("vehicles", {"owner_id": customer_id}))
    cleanup.append(("addresses", {"owner_id": customer_id}))
    cleanup.append(("bookings", {"customer_id": customer_id}))
    with pytest.raises(BadRequestException):
        await bs.create_booking(
            customer_id,
            BookingCreateRequest(vehicle_id=vehicle_id, address_id=address_id, service_ids=[rig["foam"]], scheduled_date=tomorrow, scheduled_slot="09:00-12:00"),
        )


@pytest.mark.asyncio
async def test_concurrent_last_slot_race_never_overbooks(rig, cleanup):
    """Section 24: two customers racing for the last spot(s) — the
    atomic increment_if guard must let exactly `capacity` through, never
    more, regardless of request ordering."""
    bs_db = rig["db"]
    tomorrow = now_ist().replace(tzinfo=None) + timedelta(days=1)
    for _ in range(15):  # fill to 15/20, leaving exactly 5 spots for the race
        await _book(bs_db, cleanup, rig, tomorrow)

    racers = []
    for _ in range(8):  # 8 racers chasing 5 remaining spots
        customer_id, vehicle_id, address_id = await make_customer_with_vehicle(bs_db, rig["hatchback"])
        cleanup.append(("users", {"_id": __import__("bson").ObjectId(customer_id)}))
        cleanup.append(("vehicles", {"owner_id": customer_id}))
        cleanup.append(("addresses", {"owner_id": customer_id}))
        cleanup.append(("bookings", {"customer_id": customer_id}))
        racers.append((customer_id, vehicle_id, address_id))

    async def attempt(cid, vid, aid):
        try:
            await BookingService(bs_db).create_booking(
                cid, BookingCreateRequest(vehicle_id=vid, address_id=aid, service_ids=[rig["foam"]], scheduled_date=tomorrow, scheduled_slot="09:00-12:00")
            )
            return "ok"
        except BadRequestException:
            return "rejected"

    results = await asyncio.gather(*[attempt(c, v, a) for c, v, a in racers])
    assert results.count("ok") == 5
    assert results.count("rejected") == 3

    doc = await bs_db.slot_capacity.find_one({"service_center_id": rig["center_id"], "slot_key": "09:00-12:00"})
    assert doc["booked_count"] == 20  # never over capacity


@pytest.mark.asyncio
async def test_cancel_releases_capacity(rig, cleanup):
    bs = BookingService(rig["db"])
    tomorrow = now_ist().replace(tzinfo=None) + timedelta(days=1)
    booking, customer_id, _, _ = await _book(rig["db"], cleanup, rig, tomorrow)

    doc = await rig["db"].slot_capacity.find_one({"service_center_id": rig["center_id"], "slot_key": "09:00-12:00"})
    assert doc["booked_count"] == 1

    await bs.cancel_booking(booking["id"], BookingCancelRequest(reason="test cleanup"), customer_id, "customer", None)
    doc = await rig["db"].slot_capacity.find_one({"service_center_id": rig["center_id"], "slot_key": "09:00-12:00"})
    assert doc["booked_count"] == 0
