"""
Anti-moonlighting plumbing:
 - "I've reached" (verify_vehicle) now carries GPS, geofence-checked
   against the customer's address — flags + notifies the manager, never
   blocks;
 - every capture appends a captain_locations breadcrumb;
 - step-gap sweeps: reached-but-not-started (idle_after_arrival) and
   walked-off-mid-service (left_site_during_service, fresh pings only).
"""
from datetime import timedelta

import pytest
from bson import ObjectId

from app.schemas.booking_schema import BookingAssignCaptainRequest, BookingCreateRequest, HeadingRequest, VerifyVehicleRequest
from app.services.booking_service import BookingService
from app.utils.timezone import now_ist

from tests.factories import get_hatchback_type_id, get_star_wash_service_id, make_captain, make_customer_with_vehicle, make_manager, make_service_center

NEAR = (22.7, 75.8)
FAR = (23.0, 75.8)  # ~33 km north — way past any sane geofence radius


@pytest.fixture
async def rig(db, cleanup):
    hatchback = await get_hatchback_type_id(db)
    service = await get_star_wash_service_id(db)
    center_id = await make_service_center(db, working_hours_start="00:00", working_hours_end="23:59", slot_duration_minutes=180, default_slot_capacity=20)
    cleanup.append(("service_centers", {"_id": ObjectId(center_id)}))
    cleanup.append(("slot_capacity", {"service_center_id": center_id}))
    cleanup.append(("daily_capacity", {"service_center_id": center_id}))
    manager_id = await make_manager(db, center_id)
    cleanup.append(("users", {"_id": ObjectId(manager_id)}))
    await db.service_centers.update_one({"_id": ObjectId(center_id)}, {"$set": {"manager_id": manager_id}})
    captain_id = await make_captain(db, center_id)
    cleanup.append(("users", {"_id": ObjectId(captain_id)}))
    cleanup.append(("captain_wallets", {"captain_id": captain_id}))
    cleanup.append(("captain_locations", {"captain_id": captain_id}))
    customer_id, vehicle_id, address_id = await make_customer_with_vehicle(db, hatchback)
    cleanup.append(("users", {"_id": ObjectId(customer_id)}))
    cleanup.append(("vehicles", {"owner_id": customer_id}))
    cleanup.append(("addresses", {"owner_id": customer_id}))
    cleanup.append(("bookings", {"customer_id": customer_id}))
    cleanup.append(("notifications", {}))
    # Give the address a real pin — the geofence silently no-ops without one.
    await db.addresses.update_one({"_id": ObjectId(address_id)}, {"$set": {"latitude": NEAR[0], "longitude": NEAR[1]}})
    return {
        "db": db, "center_id": center_id, "manager_id": manager_id, "captain_id": captain_id,
        "customer_id": customer_id, "vehicle_id": vehicle_id, "address_id": address_id, "service": service,
    }


async def _booking_on_the_way(rig, db) -> dict:
    """Create → assign → head out, ending in CAPTAIN_ON_THE_WAY right now."""
    bs = BookingService(db)
    target_date = now_ist().replace(tzinfo=None) + timedelta(days=1)
    booking = await bs.create_booking(
        rig["customer_id"],
        BookingCreateRequest(vehicle_id=rig["vehicle_id"], address_id=rig["address_id"], service_ids=[rig["service"]], scheduled_date=target_date, scheduled_slot="09:00-12:00"),
    )
    await bs.assign_captain(booking["id"], BookingAssignCaptainRequest(captain_id=rig["captain_id"]), "system", "admin", None)
    # Pull the start window to "now" so heading is legal (backdate pattern).
    await db.bookings.update_one({"_id": ObjectId(booking["id"])}, {"$set": {"estimated_start_at": now_ist() + timedelta(minutes=5)}})
    await bs.start_heading(booking["id"], HeadingRequest(latitude=NEAR[0], longitude=NEAR[1], equipment_used=[]), rig["captain_id"])
    return await db.bookings.find_one({"_id": ObjectId(booking["id"])})


def _plate(booking: dict) -> str:
    return booking["vehicle_registration_number"]


@pytest.mark.asyncio
async def test_far_arrival_is_flagged_and_manager_notified(rig, db):
    bs = BookingService(db)
    booking = await _booking_on_the_way(rig, db)
    await bs.verify_vehicle(
        str(booking["_id"]),
        VerifyVehicleRequest(registration_number=_plate(booking), latitude=FAR[0], longitude=FAR[1]),
        rig["captain_id"],
    )
    updated = await db.bookings.find_one({"_id": booking["_id"]})
    assert updated["vehicle_verified"] is True  # flag, never block
    assert updated["arrival_flagged"] is True
    assert updated["arrival_distance_m"] > 10000
    note = await db.notifications.find_one({"user_id": rig["manager_id"], "title": {"$regex": "Location flagged"}})
    assert note is not None and "reached" in note["message"]


@pytest.mark.asyncio
async def test_near_arrival_is_clean_and_breadcrumbed(rig, db):
    bs = BookingService(db)
    booking = await _booking_on_the_way(rig, db)
    await bs.verify_vehicle(
        str(booking["_id"]),
        VerifyVehicleRequest(registration_number=_plate(booking), latitude=NEAR[0] + 0.0005, longitude=NEAR[1]),
        rig["captain_id"],
    )
    updated = await db.bookings.find_one({"_id": booking["_id"]})
    assert updated["arrival_flagged"] is False
    assert updated["arrival_location"]["latitude"] == pytest.approx(NEAR[0] + 0.0005)
    # Breadcrumb trail: one row for heading, one for arrival.
    sources = {r["source"] async for r in db.captain_locations.find({"captain_id": rig["captain_id"]})}
    assert {"heading", "arrival"} <= sources


@pytest.mark.asyncio
async def test_verify_without_gps_still_works(rig, db):
    """Old app versions in the field send only the plate — must not break."""
    bs = BookingService(db)
    booking = await _booking_on_the_way(rig, db)
    await bs.verify_vehicle(str(booking["_id"]), VerifyVehicleRequest(registration_number=_plate(booking)), rig["captain_id"])
    updated = await db.bookings.find_one({"_id": booking["_id"]})
    assert updated["vehicle_verified"] is True
    assert updated.get("arrival_location") is None


# ------------------------------------------------------------- sweeps


@pytest.mark.asyncio
async def test_idle_after_arrival_sweep(rig, db):
    bs = BookingService(db)
    booking = await _booking_on_the_way(rig, db)
    await bs.verify_vehicle(str(booking["_id"]), VerifyVehicleRequest(registration_number=_plate(booking)), rig["captain_id"])

    # Fresh arrival — not idle yet.
    assert not any(str(b["_id"]) == str(booking["_id"]) for b in await bs.find_bookings_idle_after_arrival())

    # Backdate the arrival well past the tolerance (default 15 min).
    await db.bookings.update_one({"_id": booking["_id"]}, {"$set": {"vehicle_verified_at": now_ist() - timedelta(minutes=40)}})
    idle = await bs.find_bookings_idle_after_arrival()
    assert any(str(b["_id"]) == str(booking["_id"]) for b in idle)

    # Once flagged, the finder (issue_flag None filter) stops re-firing.
    await bs.flag_issue(str(booking["_id"]), "idle_after_arrival", "test")
    assert not any(str(b["_id"]) == str(booking["_id"]) for b in await bs.find_bookings_idle_after_arrival())


@pytest.mark.asyncio
async def test_left_site_sweep_needs_fresh_far_ping(rig, db):
    bs = BookingService(db)
    booking = await _booking_on_the_way(rig, db)
    await db.bookings.update_one({"_id": booking["_id"]}, {"$set": {"status": "service_started", "vehicle_verified": True, "service_started_at": now_ist()}})

    # Fresh ping far from the site → flagged.
    await db.users.update_one(
        {"_id": ObjectId(rig["captain_id"])},
        {"$set": {"last_known_location": {"latitude": FAR[0], "longitude": FAR[1]}, "last_location_at": now_ist()}},
    )
    away = await bs.find_bookings_captain_left_site()
    match = [b for b in away if str(b["_id"]) == str(booking["_id"])]
    assert match and match[0]["_distance_from_site_m"] > 10000

    # Same far position but STALE (10 min old) → no verdict, no flag.
    await db.users.update_one({"_id": ObjectId(rig["captain_id"])}, {"$set": {"last_location_at": now_ist() - timedelta(minutes=10)}})
    assert not any(str(b["_id"]) == str(booking["_id"]) for b in await bs.find_bookings_captain_left_site())

    # Fresh but ON site → clean.
    await db.users.update_one(
        {"_id": ObjectId(rig["captain_id"])},
        {"$set": {"last_known_location": {"latitude": NEAR[0], "longitude": NEAR[1]}, "last_location_at": now_ist()}},
    )
    assert not any(str(b["_id"]) == str(booking["_id"]) for b in await bs.find_bookings_captain_left_site())
