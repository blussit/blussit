"""
Spec Section 13, 24: the same customer+vehicle+slot must never produce two
active bookings, even under a double-click/multi-tab/retry race — enforced
atomically at the database level (a partial unique index), not just a
pre-check in application code (which a race could slip through).
"""
import asyncio
from datetime import datetime, timedelta, timezone

import pytest
from bson import ObjectId

from app.core.exceptions import BadRequestException
from app.schemas.booking_schema import BookingCreateRequest
from app.services.booking_service import BookingService

from tests.factories import get_star_wash_service_id, get_hatchback_type_id, make_customer_with_vehicle, make_service_center


@pytest.fixture
async def rig(db, cleanup):
    hatchback = await get_hatchback_type_id(db)
    foam = await get_star_wash_service_id(db)
    center_id = await make_service_center(db)
    cleanup.append(("service_centers", {"_id": ObjectId(center_id)}))
    cleanup.append(("slot_capacity", {"service_center_id": center_id}))
    cleanup.append(("daily_capacity", {"service_center_id": center_id}))
    customer_id, vehicle_id, address_id = await make_customer_with_vehicle(db, hatchback)
    cleanup.append(("users", {"_id": ObjectId(customer_id)}))
    cleanup.append(("vehicles", {"owner_id": customer_id}))
    cleanup.append(("addresses", {"owner_id": customer_id}))
    cleanup.append(("bookings", {"customer_id": customer_id}))
    return {"db": db, "center_id": center_id, "foam": foam, "customer_id": customer_id, "vehicle_id": vehicle_id, "address_id": address_id}


def _payload(rig, tomorrow, slot="09:00-12:00"):
    return BookingCreateRequest(vehicle_id=rig["vehicle_id"], address_id=rig["address_id"], service_ids=[rig["foam"]], scheduled_date=tomorrow, scheduled_slot=slot)


@pytest.mark.asyncio
async def test_sequential_duplicate_is_rejected(rig):
    bs = BookingService(rig["db"])
    tomorrow = datetime.now(timezone.utc) + timedelta(days=1)
    await bs.create_booking(rig["customer_id"], _payload(rig, tomorrow))
    with pytest.raises(BadRequestException):
        await bs.create_booking(rig["customer_id"], _payload(rig, tomorrow))


@pytest.mark.asyncio
async def test_concurrent_double_click_only_one_succeeds(rig):
    """The literal double-click/multi-tab scenario: two near-simultaneous
    create_booking calls for the identical customer+vehicle+slot."""
    tomorrow = datetime.now(timezone.utc) + timedelta(days=1)

    async def attempt():
        try:
            await BookingService(rig["db"]).create_booking(rig["customer_id"], _payload(rig, tomorrow))
            return "ok"
        except BadRequestException:
            return "rejected"

    results = await asyncio.gather(*[attempt() for _ in range(5)])
    assert results.count("ok") == 1
    assert results.count("rejected") == 4

    count = await rig["db"].bookings.count_documents({"customer_id": rig["customer_id"], "status": {"$ne": "cancelled"}})
    assert count == 1


@pytest.mark.asyncio
async def test_different_slot_same_day_is_allowed(rig):
    """Section 14: multiple bookings the same day ARE allowed as long as
    they don't collide on the same vehicle+slot."""
    bs = BookingService(rig["db"])
    tomorrow = datetime.now(timezone.utc) + timedelta(days=1)
    b1 = await bs.create_booking(rig["customer_id"], _payload(rig, tomorrow, slot="09:00-12:00"))
    b2 = await bs.create_booking(rig["customer_id"], _payload(rig, tomorrow, slot="12:00-15:00"))
    assert b1["id"] != b2["id"]
