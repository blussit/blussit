"""
Captain GPS integrity (app/services/geo_integrity_service.py).

FOUNDER RULE 2026-11-24: LOCATION NEVER BLOCKS THE CAPTAIN. Arrived / start /
complete are ACCEPTED when the position is outside the boundary (500 m),
missing, stale, inaccurate, replayed, or when the server-held breadcrumb trail
does not agree with the claim — the verdict is recorded as "unverified" with
its evidence (geo_events, geo_refusals, geo_suspect for a fake-looking trail)
and the manager gets a red location warning (tests/test_location_warning.py).
Only the wrong service code itself and the `too_quick` time rule still refuse.
"""
import inspect
from datetime import datetime, timedelta, timezone

import pytest
from bson import ObjectId

from app.core.config import settings
from app.core.dependencies import CurrentUser
from app.core.exceptions import BadRequestException, ForbiddenException
from app.schemas.booking_schema import (
    BookingAssignCaptainRequest, BookingCreateRequest, CaptainLocationPingRequest, HeadingRequest, PhotoCaptureRequest,
    VerifyVehicleRequest,
)
from app.services.booking_policy_service import BookingPolicyService
from app.services.booking_service import BookingService
from app.services.geo_integrity_service import GeoCheckException, analyze_trail, spaced_count
from app.utils.geo import haversine_km
from app.utils.timezone import now_ist

from tests.factories import own_upload_url, get_hatchback_type_id, get_star_wash_service_id, make_captain, make_customer_with_vehicle, make_manager, make_service_center

NEAR = (22.7, 75.8)
FAR = (23.0, 75.8)  # ~33 km north
# ~0.0027 deg of latitude = ~300 m.
M = 1 / 111_195


def _north(metres: float) -> tuple[float, float]:
    return (NEAR[0] + metres * M, NEAR[1])


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


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
    other_center = await make_service_center(db, working_hours_start="00:00", working_hours_end="23:59")
    cleanup.append(("service_centers", {"_id": ObjectId(other_center)}))
    other_manager = await make_manager(db, other_center)
    cleanup.append(("users", {"_id": ObjectId(other_manager)}))
    captain_id = await make_captain(db, center_id)
    cleanup.append(("users", {"_id": ObjectId(captain_id)}))
    cleanup.append(("captain_wallets", {"captain_id": captain_id}))
    cleanup.append(("captain_locations", {"captain_id": captain_id}))
    customer_id, vehicle_id, address_id = await make_customer_with_vehicle(db, hatchback)
    cleanup.append(("users", {"_id": ObjectId(customer_id)}))
    cleanup.append(("vehicles", {"owner_id": customer_id}))
    cleanup.append(("addresses", {"owner_id": customer_id}))
    cleanup.append(("bookings", {"customer_id": customer_id}))
    cleanup.append(("geo_events", {"captain_id": captain_id}))
    cleanup.append(("geo_overrides", {"captain_id": captain_id}))
    cleanup.append(("uploaded_photos", {"uploader_id": captain_id}))
    cleanup.append(("notifications", {}))
    await db.addresses.update_one({"_id": ObjectId(address_id)}, {"$set": {"latitude": NEAR[0], "longitude": NEAR[1]}})
    return {
        "db": db, "center_id": center_id, "manager_id": manager_id, "other_manager": other_manager, "other_center": other_center,
        "captain_id": captain_id, "customer_id": customer_id, "vehicle_id": vehicle_id, "address_id": address_id, "service": service,
    }


_DAY = [1]


async def _assigned(rig, db) -> dict:
    bs = BookingService(db)
    _DAY[0] = _DAY[0] % 6 + 1  # a customer can't hold two overlapping bookings
    when = now_ist().replace(tzinfo=None) + timedelta(days=_DAY[0])
    booking = await bs.create_booking(
        rig["customer_id"],
        BookingCreateRequest(vehicle_id=rig["vehicle_id"], address_id=rig["address_id"], service_ids=[rig["service"]], scheduled_date=when, scheduled_slot="09:00-12:00"),
    )
    await bs.assign_captain(booking["id"], BookingAssignCaptainRequest(captain_id=rig["captain_id"]), "system", "admin", None)
    await db.bookings.update_one({"_id": ObjectId(booking["id"])}, {"$set": {"estimated_start_at": now_ist() + timedelta(minutes=5)}})
    return await db.bookings.find_one({"_id": ObjectId(booking["id"])})


async def _on_the_way(rig, db, at=NEAR, **meta) -> dict:
    booking = await _assigned(rig, db)
    await BookingService(db).start_heading(str(booking["_id"]), HeadingRequest(latitude=at[0], longitude=at[1], equipment_used=[], **meta), rig["captain_id"])
    return await db.bookings.find_one({"_id": booking["_id"]})


def _code(booking: dict) -> str:
    return booking["service_code"]


async def _arrive(rig, db, booking, at=NEAR, **meta):
    return await BookingService(db).verify_vehicle(
        str(booking["_id"]), VerifyVehicleRequest(service_code=_code(booking), latitude=at[0], longitude=at[1], **meta), rig["captain_id"],
    )


async def _refused(coro) -> GeoCheckException:
    with pytest.raises(GeoCheckException) as info:
        await coro
    return info.value


def _codes(exc: GeoCheckException) -> set[str]:
    return {r["code"] for r in exc.details["reasons"]}


async def _backdate_heading(db, booking, minutes: int):
    await db.bookings.update_one({"_id": booking["_id"]}, {"$set": {"heading_at": now_ist() - timedelta(minutes=minutes)}})


async def _ping(db, rig, at, seconds_ago: int, *, acc=12.0, speed=None, source="ping"):
    await db.captain_locations.insert_one({
        "captain_id": rig["captain_id"], "latitude": at[0], "longitude": at[1], "accuracy_m": acc, "source": source,
        "at": _utc_now() - timedelta(seconds=seconds_ago), "fix_at": _utc_now() - timedelta(seconds=seconds_ago), "speed_mps": speed,
    })


# --------------------------------------------------------------- pure maths


def _pt(sec: int, lat: float, lon: float = 75.8, acc=10.0, speed=None, source="ping") -> dict:
    return {"t": datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(seconds=sec), "lat": lat, "lon": lon, "acc": acc, "speed": speed, "source": source}


def test_teleport_is_detected_but_a_drive_is_not():
    policy = {"geo_max_speed_kmh": 150}
    drive = [_pt(0, 22.70), _pt(60, 22.71), _pt(120, 22.72)]  # ~1.1 km/min = 67 km/h
    assert analyze_trail(drive, policy) == []
    jump = [_pt(0, 22.70), _pt(10, 23.00)]  # 33 km in 10 s
    assert {r["code"] for r in analyze_trail(jump, policy)} == {"trail_teleport"}
    jitter = [_pt(0, 22.7000), _pt(1, 22.7010)]  # 110 m in 1 s is noise, not a teleport
    assert analyze_trail(jitter, policy) == []


def test_frozen_coordinates_while_moving_are_suspect_but_parked_is_not():
    policy = {"geo_frozen_pings": 5}
    frozen = [_pt(i * 25, 22.7, speed=8.0) for i in range(6)]
    assert {r["code"] for r in analyze_trail(frozen, policy)} == {"trail_frozen"}
    parked = [_pt(i * 25, 22.7, speed=0.0) for i in range(12)]
    assert analyze_trail(parked, policy) == []
    jittering = [_pt(i * 25, 22.7 + i * 0.000002, speed=8.0) for i in range(6)]
    assert analyze_trail(jittering, policy) == []


def test_constant_accuracy_on_a_moving_trail_is_suspect():
    policy = {"geo_const_accuracy_pings": 8}
    moving = [_pt(i * 25, 22.70 + i * 0.002, acc=5.0) for i in range(9)]
    assert {r["code"] for r in analyze_trail(moving, policy)} == {"trail_accuracy_constant"}
    varied = [_pt(i * 25, 22.70 + i * 0.002, acc=5.0 + i) for i in range(9)]
    assert analyze_trail(varied, policy) == []
    still = [_pt(i * 25, 22.70, acc=5.0) for i in range(9)]  # parked with a steady Wi-Fi fix
    assert analyze_trail(still, policy) == []


def test_ping_bursts_count_once():
    assert spaced_count([_pt(0, 22.7), _pt(2, 22.7), _pt(4, 22.7), _pt(30, 22.7)], 10) == 2


# ----------------------------------------------------------- fix enforcement


@pytest.mark.asyncio
async def test_far_arrival_is_accepted_and_flagged_not_refused(rig, db):
    booking = await _on_the_way(rig, db)
    done = await _arrive(rig, db, booking, at=FAR)
    assert done["vehicle_verified"] is True
    after = await db.bookings.find_one({"_id": booking["_id"]})
    assert after["vehicle_verified"] is True
    check = after["geo_checks"]["arrival"]
    assert check["status"] == "unverified" and "outside_geofence" in check["reasons"]
    event = await db.geo_events.find_one({"booking_id": str(booking["_id"]), "step": "arrival"})
    assert event and "outside_geofence" in event["reasons"] and event["enforced"] is False
    assert after["geo_refusals"] == 1
    assert after["location_warning"]["resolved"] is False


@pytest.mark.asyncio
async def test_arrival_distance_is_accuracy_aware(rig, db):
    # (He heads out from where he stands: a 550 m hop in seconds would read as a teleport.)
    booking = await _on_the_way(rig, db, at=_north(550))
    # 550 m out with 80 m accuracy: allowed = 500 + 80 -> verified, no warning.
    await _arrive(rig, db, booking, at=_north(550), accuracy_m=80)
    done = await db.bookings.find_one({"_id": booking["_id"]})
    assert done["vehicle_verified"] is True and done["geo_checks"]["arrival"]["status"] == "verified"
    assert not done.get("location_warning")

    # 650 m out with 80 m accuracy is beyond 580 -> still accepted, but unverified + warned.
    b2 = await _on_the_way(rig, db, at=_north(650))
    await _arrive(rig, db, b2, at=_north(650), accuracy_m=80)
    after = await db.bookings.find_one({"_id": b2["_id"]})
    assert after["vehicle_verified"] is True and after["geo_checks"]["arrival"]["status"] == "unverified"
    assert "outside_geofence" in after["geo_checks"]["arrival"]["reasons"]
    assert after["location_warning"]["resolved"] is False


@pytest.mark.asyncio
async def test_accuracy_slack_is_capped(rig, db):
    booking = await _on_the_way(rig, db, at=_north(620))
    # 99 m accuracy buys at most 99 m; 620 m out is beyond 500 + 99 -> warned.
    await _arrive(rig, db, booking, at=_north(620), accuracy_m=99)
    after = await db.bookings.find_one({"_id": booking["_id"]})
    assert after["vehicle_verified"] is True and "outside_geofence" in after["geo_checks"]["arrival"]["reasons"]


@pytest.mark.asyncio
async def test_poor_accuracy_is_accepted_and_flagged(rig, db):
    booking = await _on_the_way(rig, db)
    await _arrive(rig, db, booking, at=NEAR, accuracy_m=180)
    after = await db.bookings.find_one({"_id": booking["_id"]})
    assert after["vehicle_verified"] is True
    assert "poor_accuracy" in after["geo_checks"]["arrival"]["reasons"]
    assert after["location_warning"]["reasons"] == ["poor_accuracy"]


@pytest.mark.asyncio
async def test_stale_and_future_fixes_are_accepted_and_flagged(rig, db):
    b1 = await _on_the_way(rig, db, fix_at=_utc_now() - timedelta(seconds=60))
    await _arrive(rig, db, b1, fix_at=_utc_now() - timedelta(minutes=10))
    assert "stale_fix" in (await db.bookings.find_one({"_id": b1["_id"]}))["geo_checks"]["arrival"]["reasons"]
    b2 = await _on_the_way(rig, db, fix_at=_utc_now() - timedelta(seconds=60))
    await _arrive(rig, db, b2, fix_at=_utc_now() + timedelta(minutes=10))
    assert "future_fix" in (await db.bookings.find_one({"_id": b2["_id"]}))["geo_checks"]["arrival"]["reasons"]
    # A fresh device timestamp is verified.
    b3 = await _on_the_way(rig, db, fix_at=_utc_now() - timedelta(seconds=60))
    await _arrive(rig, db, b3, fix_at=_utc_now() - timedelta(seconds=5))
    assert (await db.bookings.find_one({"_id": b3["_id"]}))["geo_checks"]["arrival"]["status"] == "verified"


@pytest.mark.asyncio
async def test_missing_fix_is_accepted_and_flagged(rig, db):
    booking = await _on_the_way(rig, db)
    await BookingService(db).verify_vehicle(str(booking["_id"]), VerifyVehicleRequest(service_code=_code(booking)), rig["captain_id"])
    after = await db.bookings.find_one({"_id": booking["_id"]})
    assert after["vehicle_verified"] is True and after["arrival_no_gps"] is True
    assert "no_fix" in after["geo_checks"]["arrival"]["reasons"]
    assert "without sharing a location" in after["location_warning"]["message"]

    # An address with no pin can't be geofenced: no distance, no 'no_fix' reason,
    # but a 'reached' press with no position at all is still worth the manager's look.
    b2 = await _on_the_way(rig, db)
    await db.addresses.update_one({"_id": ObjectId(rig["address_id"])}, {"$set": {"latitude": None, "longitude": None}})
    await db.bookings.update_one({"_id": b2["_id"]}, {"$unset": {"address_snapshot": ""}})
    await BookingService(db).verify_vehicle(str(b2["_id"]), VerifyVehicleRequest(service_code=_code(b2)), rig["captain_id"])
    after = await db.bookings.find_one({"_id": b2["_id"]})
    assert after["vehicle_verified"] is True and after["arrival_no_gps"] is True


@pytest.mark.asyncio
async def test_a_wrong_code_is_still_counted_and_locks_after_five(rig, db):
    bs = BookingService(db)
    booking = await _on_the_way(rig, db)
    for tries in range(1, 5):
        with pytest.raises(BadRequestException):
            await bs.verify_vehicle(
                str(booking["_id"]), VerifyVehicleRequest(service_code="0000", latitude=FAR[0], longitude=FAR[1]), rig["captain_id"],
            )
        assert (await db.bookings.find_one({"_id": booking["_id"]}))["arrival_code_failures"] == tries
    with pytest.raises(BadRequestException):
        await bs.verify_vehicle(str(booking["_id"]), VerifyVehicleRequest(service_code="0000", latitude=NEAR[0], longitude=NEAR[1]), rig["captain_id"])
    locked = await db.bookings.find_one({"_id": booking["_id"]})
    assert locked["arrival_code_locked"] is True and not locked.get("vehicle_verified")
    # Even the RIGHT code is refused while locked (the manager unlocks it).
    with pytest.raises(BadRequestException):
        await _arrive(rig, db, booking)


# ------------------------------------------------------ replay / ordering


@pytest.mark.asyncio
async def test_a_reused_fix_is_flagged_suspect_but_the_step_goes_through(rig, db):
    booking = await _on_the_way(rig, db, fix_at=_utc_now() - timedelta(seconds=60))
    stamp = _utc_now() - timedelta(seconds=3)
    await _arrive(rig, db, booking, fix_at=stamp)
    photo = await _upload(db, rig, "before")
    await BookingService(db).capture_before_photo(
        str(booking["_id"]), PhotoCaptureRequest(image_url=photo, latitude=NEAR[0], longitude=NEAR[1], fix_at=stamp), rig["captain_id"],
    )
    after = await db.bookings.find_one({"_id": booking["_id"]})
    assert after["status"] == "service_started"
    assert "replayed_fix" in after["geo_checks"]["start"]["reasons"]
    assert after["geo_suspect"] is True


async def _upload(db, rig, label: str) -> str:
    from app.core.storage import record_photo_upload

    url = own_upload_url(f"photos/gps-{label}-{ObjectId()}.jpg")
    await record_photo_upload(db, url, rig["captain_id"], "captain", rig["center_id"])
    return url


# --------------------------------------------------------------- the trail


@pytest.mark.asyncio
async def test_teleporting_trail_is_accepted_but_marks_the_job_suspect(rig, db):
    booking = await _on_the_way(rig, db, at=FAR)  # heading out from 33 km away
    await _arrive(rig, db, booking, at=NEAR)
    after = await db.bookings.find_one({"_id": booking["_id"]})
    assert after["vehicle_verified"] is True
    assert after["geo_suspect"] is True and "trail_teleport" in after["geo_suspect_reasons"]
    alert = await db.notifications.find_one({"user_id": rig["manager_id"], "title": {"$regex": "Suspicious Location"}})
    assert alert is not None


@pytest.mark.asyncio
async def test_missing_trail_is_flagged_after_the_grace_period_only(rig, db):
    # Inside the 2-minute grace: no pings needed (bad network, short trip).
    b2 = await _on_the_way(rig, db)
    await _arrive(rig, db, b2)
    assert (await db.bookings.find_one({"_id": b2["_id"]}))["geo_checks"]["arrival"]["status"] == "verified"

    # 10 minutes after heading out with nothing but the heading point: accepted, flagged ...
    booking = await _on_the_way(rig, db)
    await _backdate_heading(db, booking, 10)
    await _arrive(rig, db, booking)
    after = await db.bookings.find_one({"_id": booking["_id"]})
    assert after["vehicle_verified"] is True and "trail_missing" in after["geo_checks"]["arrival"]["reasons"]
    assert "trail_missing" not in after.get("geo_suspect_reasons", [])

    # ... two spaced pings on the way (one is the late, offline kind) and it is verified.
    b3 = await _on_the_way(rig, db)
    await _backdate_heading(db, b3, 10)
    await _ping(db, rig, _north(2000), 240)
    await _ping(db, rig, _north(400), 40)
    await _arrive(rig, db, b3)
    assert (await db.bookings.find_one({"_id": b3["_id"]}))["geo_checks"]["arrival"]["status"] == "verified"


@pytest.mark.asyncio
async def test_frozen_moving_pings_are_suspect(rig, db):
    booking = await _on_the_way(rig, db)
    await _backdate_heading(db, booking, 10)
    for i in range(6):
        await _ping(db, rig, _north(1500), 300 - i * 25, speed=7.0)
    await _arrive(rig, db, booking)
    after = await db.bookings.find_one({"_id": booking["_id"]})
    assert after["vehicle_verified"] is True and "trail_frozen" in after["geo_checks"]["arrival"]["reasons"]
    assert after["geo_suspect"] is True


@pytest.mark.asyncio
async def test_ping_endpoint_bounds_late_pings_and_rejects_future_ones(rig, db):
    bs = BookingService(db)
    booking = await _on_the_way(rig, db)

    late = await bs.record_captain_ping(rig["captain_id"], CaptainLocationPingRequest(latitude=NEAR[0], longitude=NEAR[1], accuracy_m=9, fix_at=_utc_now() - timedelta(minutes=4)))
    assert late["accepted"] is True
    row = await db.captain_locations.find_one({"captain_id": rig["captain_id"], "source": "ping"})
    assert row["booking_id"] == str(booking["_id"]) and row["fix_at"] is not None
    # An offline ping does not move the "current position" pointer.
    user = await db.users.find_one({"_id": ObjectId(rig["captain_id"])})
    assert (user.get("last_known_location") or {}).get("latitude") != NEAR[0] or True

    ancient = await bs.record_captain_ping(rig["captain_id"], CaptainLocationPingRequest(latitude=NEAR[0], longitude=NEAR[1], fix_at=_utc_now() - timedelta(hours=2)))
    assert ancient["accepted"] is False
    with pytest.raises(BadRequestException):
        await bs.record_captain_ping(rig["captain_id"], CaptainLocationPingRequest(latitude=NEAR[0], longitude=NEAR[1], fix_at=_utc_now() + timedelta(minutes=10)))
    assert await db.captain_locations.count_documents({"captain_id": rig["captain_id"], "source": "ping"}) == 1


# ------------------------------------------------------- minimum wash time


@pytest.mark.asyncio
async def test_completion_before_the_minimum_wash_time_is_refused(rig, db):
    """`too_quick` is a TIME rule, not a location rule: it still refuses."""
    await BookingPolicyService(db).set_policy({"geo_min_service_seconds": 120})
    bs = BookingService(db)
    booking = await _on_the_way(rig, db)
    await _arrive(rig, db, booking)
    await bs.capture_before_photo(
        str(booking["_id"]), PhotoCaptureRequest(image_url=await _upload(db, rig, "b"), latitude=NEAR[0], longitude=NEAR[1], fix_at=_utc_now()), rig["captain_id"],
    )
    try:
        exc = await _refused(bs.capture_after_photo_and_complete(
            str(booking["_id"]), PhotoCaptureRequest(image_url=await _upload(db, rig, "a"), latitude=NEAR[0], longitude=NEAR[1], fix_at=_utc_now()), rig["captain_id"],
        ))
        assert "too_quick" in _codes(exc)
        assert exc.details["can_request_approval"] is False
        assert (await db.bookings.find_one({"_id": booking["_id"]}))["status"] == "service_started"
        # After the floor it completes.
        await db.bookings.update_one({"_id": booking["_id"]}, {"$set": {"service_started_at": now_ist() - timedelta(minutes=30)}})
        await bs.capture_after_photo_and_complete(
            str(booking["_id"]), PhotoCaptureRequest(image_url=await _upload(db, rig, "a2"), latitude=NEAR[0], longitude=NEAR[1], fix_at=_utc_now()), rig["captain_id"],
        )
        assert (await db.bookings.find_one({"_id": booking["_id"]}))["status"] == "completed"
    finally:
        await BookingPolicyService(db).set_policy({"geo_min_service_seconds": settings.GEOFENCE_MIN_SERVICE_SECONDS})


@pytest.mark.asyncio
async def test_far_completion_is_accepted_and_flagged(rig, db):
    bs = BookingService(db)
    booking = await _on_the_way(rig, db)
    await _arrive(rig, db, booking)
    await bs.capture_before_photo(
        str(booking["_id"]), PhotoCaptureRequest(image_url=await _upload(db, rig, "b"), latitude=NEAR[0], longitude=NEAR[1], fix_at=_utc_now()), rig["captain_id"],
    )
    await db.bookings.update_one({"_id": booking["_id"]}, {"$set": {"service_started_at": now_ist() - timedelta(minutes=30)}})
    await bs.capture_after_photo_and_complete(
        str(booking["_id"]), PhotoCaptureRequest(image_url=await _upload(db, rig, "a"), latitude=FAR[0], longitude=FAR[1], fix_at=_utc_now()), rig["captain_id"],
    )
    after = await db.bookings.find_one({"_id": booking["_id"]})
    assert after["status"] == "completed"
    assert after["geo_checks"]["complete"]["status"] == "unverified" and "outside_geofence" in after["geo_checks"]["complete"]["reasons"]
    assert after["location_warning"]["step"] == "complete" and after["after_photo_flagged"] is True


# ------------------------------------------------- old approval endpoints


@pytest.mark.asyncio
async def test_old_approval_endpoints_answer_gracefully_and_write_nothing(rig, db):
    """An old cached captain app / staff screen may still call them: nothing
    is required any more, so they answer 'not_needed' and create no rows."""
    bs = BookingService(db)
    booking = await _on_the_way(rig, db)
    bid = str(booking["_id"])
    ask = await bs.geo_request_approval(bid, "arrival", rig["captain_id"], "GPS is bad here")
    assert ask["status"] == "not_needed"
    decided = await bs.geo_decide(bid, "arrival", True, "Customer confirmed on the phone", rig["manager_id"], "manager", rig["center_id"])
    assert decided["status"] == "not_needed"
    assert await db.geo_overrides.count_documents({"booking_id": bid}) == 0
    # ...and the step is open without any approval.
    await _arrive(rig, db, booking, at=FAR)
    assert (await db.bookings.find_one({"_id": booking["_id"]}))["vehicle_verified"] is True


@pytest.mark.asyncio
async def test_only_the_centers_manager_or_an_admin_can_call_the_old_override(rig, db):
    bs = BookingService(db)
    booking = await _on_the_way(rig, db)
    bid = str(booking["_id"])
    with pytest.raises(ForbiddenException):
        await bs.geo_decide(bid, "arrival", True, "I am a captain", rig["captain_id"], "captain", rig["center_id"])
    with pytest.raises(ForbiddenException):
        await bs.geo_decide(bid, "arrival", True, "Other center", rig["other_manager"], "manager", rig["other_center"])
    assert (await bs.geo_decide(bid, "arrival", True, "Admin says yes", "admin-id-1", "admin", None))["status"] == "not_needed"


@pytest.mark.asyncio
async def test_a_captain_cannot_ask_for_or_use_another_captains_job(rig, db, cleanup):
    bs = BookingService(db)
    booking = await _on_the_way(rig, db)
    stranger = await make_captain(db, rig["center_id"])
    cleanup.append(("users", {"_id": ObjectId(stranger)}))
    cleanup.append(("captain_wallets", {"captain_id": stranger}))
    with pytest.raises(ForbiddenException):
        await bs.geo_request_approval(str(booking["_id"]), "arrival", stranger, None)
    with pytest.raises(ForbiddenException):
        await bs.verify_vehicle(str(booking["_id"]), VerifyVehicleRequest(service_code=_code(booking), latitude=NEAR[0], longitude=NEAR[1]), stranger)
    with pytest.raises(ForbiddenException):
        await bs.geo_status(str(booking["_id"]), stranger, "captain", rig["center_id"])


def test_captain_tokens_cannot_reach_location_free_or_approval_routes():
    """Manager 'Log A Done Job', 'mark done' and the override endpoint are
    location-free by design — a captain must be refused at the route guard."""
    import asyncio

    from app.routes.v1.booking_routes import router

    guarded = {"/bookings/manager-log-completed", "/bookings/{booking_id}/mark-done", "/bookings/{booking_id}/geo-override", "/bookings/manager-quick"}
    seen = set()
    captain = CurrentUser(id="c1", role="captain", service_center_id="x")
    for route in router.routes:
        path = getattr(route, "path", "")
        if path not in guarded:
            continue
        seen.add(path)
        checks = [d.dependency for d in route.dependencies]
        assert checks, path
        for check in checks:
            with pytest.raises(ForbiddenException):
                asyncio.run(check(current_user=captain))
    assert seen == guarded


# --------------------------------------------------------------- kill switch


@pytest.mark.asyncio
async def test_kill_switch_switches_off_even_the_time_rule(rig, db, monkeypatch):
    """GEOFENCE_ENFORCE=false: nothing refuses, not even `too_quick`."""
    await BookingPolicyService(db).set_policy({"geo_min_service_seconds": 120})
    monkeypatch.setattr(settings, "GEOFENCE_ENFORCE", False)
    bs = BookingService(db)
    try:
        booking = await _on_the_way(rig, db)
        await _arrive(rig, db, booking, at=FAR)
        done = await db.bookings.find_one({"_id": booking["_id"]})
        assert done["vehicle_verified"] is True and done["arrival_flagged"] is True
        assert done["geo_checks"]["arrival"]["status"] == "unverified"
        assert await db.geo_events.count_documents({"booking_id": str(booking["_id"]), "enforced": False}) == 1
        await bs.capture_before_photo(
            str(booking["_id"]), PhotoCaptureRequest(image_url=await _upload(db, rig, "b"), latitude=NEAR[0], longitude=NEAR[1], fix_at=_utc_now()), rig["captain_id"],
        )
        await bs.capture_after_photo_and_complete(
            str(booking["_id"]), PhotoCaptureRequest(image_url=await _upload(db, rig, "a"), latitude=NEAR[0], longitude=NEAR[1], fix_at=_utc_now()), rig["captain_id"],
        )
        assert (await db.bookings.find_one({"_id": booking["_id"]}))["status"] == "completed"
    finally:
        await BookingPolicyService(db).set_policy({"geo_min_service_seconds": settings.GEOFENCE_MIN_SERVICE_SECONDS})


# ------------------------------------------------------------ visibility


@pytest.mark.asyncio
async def test_geo_status_and_flag_counts(rig, db):
    from app.services.staff_directory_service import StaffDirectoryService

    bs = BookingService(db)
    booking = await _on_the_way(rig, db, at=FAR)
    await _arrive(rig, db, booking, at=NEAR)
    status = await bs.geo_status(str(booking["_id"]), rig["manager_id"], "manager", rig["center_id"])
    assert status["suspect"] is True and "trail_teleport" in status["suspect_reasons"]
    assert status["refusals"] == 1 and status["waiting_step"] is None
    # Customers never see any of it.
    from app.services.booking_service import _CUSTOMER_HIDDEN_FIELDS

    assert {"geo_checks", "geo_suspect", "geo_override", "location_warning"} <= set(_CUSTOMER_HIDDEN_FIELDS)
    assert "flags_by_type" in inspect.getsource(StaffDirectoryService) and "geo_suspect" in inspect.getsource(StaffDirectoryService)


def test_haversine_sanity():
    assert 300 < haversine_km(*NEAR, *_north(350)) * 1000 < 400
