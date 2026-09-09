"""
Polygon service zones — the coverage authority when they exist:
  - point-in-polygon via MongoDB $geoIntersects;
  - THE scenario the system exists for: a WRONG pincode with an in-zone
    pin books fine, and a "right" pincode with an out-of-zone pin is
    refused;
  - addresses without a pin (and installs with no zones drawn) keep the
    legacy pincode behavior — nothing breaks at rollout.
"""
from datetime import timedelta

import pytest
from bson import ObjectId

from app.core.exceptions import BadRequestException
from app.schemas.booking_schema import BookingCreateRequest
from app.services.booking_service import BookingService
from app.services.zone_service import ZoneService
from app.utils.timezone import now_ist

from tests.factories import get_star_wash_service_id, get_hatchback_type_id, make_customer_with_vehicle, make_service_center

# A tight square around "our" test neighbourhood (lng, lat order).
SQUARE = [[75.79, 22.69], [75.81, 22.69], [75.81, 22.71], [75.79, 22.71]]
INSIDE = (22.700, 75.800)
OUTSIDE = (22.7532, 75.8937)


@pytest.fixture
async def rig(db, cleanup):
    hatchback = await get_hatchback_type_id(db)
    foam = await get_star_wash_service_id(db)
    center_id = await make_service_center(db, working_hours_start="09:00", working_hours_end="21:00", slot_duration_minutes=180, pincode="452888")
    cleanup.append(("service_centers", {"_id": ObjectId(center_id)}))
    cleanup.append(("slot_capacity", {"service_center_id": center_id}))
    cleanup.append(("daily_capacity", {"service_center_id": center_id}))
    zone = await ZoneService(db).create_zone("Test Square", center_id, SQUARE)
    cleanup.append(("service_zones", {"_id": ObjectId(zone["id"])}))

    customer_id, vehicle_id, address_id = await make_customer_with_vehicle(db, hatchback, pincode="452888")
    cleanup.append(("users", {"_id": ObjectId(customer_id)}))
    cleanup.append(("vehicles", {"owner_id": customer_id}))
    cleanup.append(("addresses", {"owner_id": customer_id}))
    cleanup.append(("bookings", {"customer_id": customer_id}))
    cleanup.append(("notifications", {"user_id": customer_id}))
    cleanup.append(("purchase_confirmations", {"customer_id": customer_id}))
    return {"db": db, "center_id": center_id, "zone_id": zone["id"], "customer_id": customer_id,
            "vehicle_id": vehicle_id, "address_id": address_id, "foam": foam}


@pytest.mark.asyncio
async def test_point_in_polygon_lookup(rig, db):
    zones = ZoneService(db)
    assert any(z["service_center_id"] == rig["center_id"] for z in await zones.zones_for_point(*INSIDE))
    assert all(z["service_center_id"] != rig["center_id"] for z in await zones.zones_for_point(*OUTSIDE))


@pytest.mark.asyncio
async def test_wrong_pincode_right_pin_books_and_vice_versa(rig, db):
    svc = BookingService(db)
    date = now_ist().replace(tzinfo=None) + timedelta(days=2)
    slots = await svc.available_slots(rig["center_id"], (now_ist().date() + timedelta(days=2)).isoformat())

    def payload(slot_key):
        return BookingCreateRequest(vehicle_id=rig["vehicle_id"], address_id=rig["address_id"],
                                    service_ids=[rig["foam"]], scheduled_date=date, scheduled_slot=slot_key)

    # WRONG pincode, pin INSIDE the zone -> the pin wins, booking succeeds.
    await db.addresses.update_one({"_id": ObjectId(rig["address_id"])},
                                  {"$set": {"latitude": INSIDE[0], "longitude": INSIDE[1], "pincode": "999999"}})
    booking = await svc.create_booking(rig["customer_id"], payload(slots[0]["key"]))
    assert booking["service_center_id"] == rig["center_id"]

    # "Right" pincode, pin OUTSIDE every zone -> refused. (Only OUR zone
    # exists for this square; the dev DB has no other zones.)
    await db.addresses.update_one({"_id": ObjectId(rig["address_id"])},
                                  {"$set": {"latitude": OUTSIDE[0], "longitude": OUTSIDE[1], "pincode": "452888"}})
    with pytest.raises(BadRequestException, match="not yet available"):
        await svc.create_booking(rig["customer_id"], payload(slots[1]["key"]))


@pytest.mark.asyncio
async def test_pinless_customer_rejected_but_staff_bypass_keeps_legacy(rig, db):
    """Once zones exist, a typed-only address can't book (the manual-typing
    loophole the zones exist to close) — but a manager booking on behalf
    of a phone-in customer still can (allow_pinless)."""
    svc = BookingService(db)
    await db.addresses.update_one({"_id": ObjectId(rig["address_id"])},
                                  {"$set": {"latitude": None, "longitude": None, "pincode": "452888"}})
    address = await db.addresses.find_one({"_id": ObjectId(rig["address_id"])})
    with pytest.raises(BadRequestException, match="pin your location"):
        await svc._resolve_service_center(address)
    center, _ = await svc._resolve_service_center(address, allow_pinless=True)
    assert str(center["_id"]) == rig["center_id"]


@pytest.mark.asyncio
async def test_self_intersecting_ring_rejected_with_clear_message(rig, db):
    """A hand-drawn outline whose edges cross (bowtie) used to hit MongoDB's
    'Loop is not valid' as a raw 500 — must be a clean 400 instead."""
    bowtie = [[75.79, 22.69], [75.81, 22.71], [75.81, 22.69], [75.79, 22.71]]
    with pytest.raises(BadRequestException, match="crosses itself"):
        await ZoneService(db).create_zone("Bowtie", rig["center_id"], bowtie)


@pytest.mark.asyncio
async def test_double_clicked_corner_deduped(rig, db, cleanup):
    ring = [[75.82, 22.69], [75.82, 22.69], [75.84, 22.69], [75.84, 22.71], [75.82, 22.71]]
    zone = await ZoneService(db).create_zone("Dedup", rig["center_id"], ring)
    cleanup.append(("service_zones", {"_id": ObjectId(zone["id"])}))
    assert len(zone["polygon"]["coordinates"][0]) == 5  # 4 distinct + closing point


@pytest.mark.asyncio
async def test_deactivated_zone_stops_counting(rig, db):
    zones = ZoneService(db)
    await zones.update_zone(rig["zone_id"], is_active=False)
    assert all(z["service_center_id"] != rig["center_id"] for z in await zones.zones_for_point(*INSIDE))
