"""
LOCATION NEVER BLOCKS THE CAPTAIN (founder rule 2026-11-24) — the new half.

  * a captain 2 km from the pin enters the right code / starts / finishes: every
    step is accepted, the manager gets a RED `location_warning` + an in-app
    note, the captain's flow is untouched;
  * the manager's Ignore clears the warning (idempotent, own center / admin);
  * the manager can act for a stuck captain: Mark Arrived, Complete For Captain
    (the captain stays the earner);
  * every captain / manager step announces itself over the websocket (the
    "captain on the way" that never changed on the manager's screen);
  * the 500 m boundary, its default, and the boot migration 300 -> 500.

The older evidence matrix lives in tests/test_gps_integrity.py.
"""
import inspect
from datetime import timedelta

import pytest
from bson import ObjectId

from app.core.exceptions import BadRequestException, ForbiddenException
from app.schemas.booking_schema import PhotoCaptureRequest, VerifyVehicleRequest
from app.services.booking_policy_service import (
    DEFAULT_BOOKING_POLICY, BookingPolicyService, migrate_geofence_radius_to_500,
)
from app.services.booking_service import BookingService, redact_for_captain
from app.services.geo_integrity_service import Fix, GeoIntegrityService
from app.utils.geo import haversine_km
from app.utils.timezone import now_ist

# Fixtures and helpers shared with the evidence matrix (importing a fixture makes it available here).
from tests.test_gps_integrity import (  # noqa: F401
    FAR, NEAR, _arrive, _code, _on_the_way, _north, _upload, _utc_now, rig,
)

TWO_KM = _north(2000)
CENTER_ONLY = "center"


def _photo(url: str, at=NEAR, **meta) -> PhotoCaptureRequest:
    return PhotoCaptureRequest(image_url=url, latitude=at[0], longitude=at[1], fix_at=_utc_now(), **meta)


async def _started(rig, db, at=NEAR) -> dict:
    """On the way -> arrived -> before photo, all from `at`."""
    booking = await _on_the_way(rig, db, at=at)
    await _arrive(rig, db, booking, at=at)
    await BookingService(db).capture_before_photo(str(booking["_id"]), _photo(await _upload(db, rig, "b"), at), rig["captain_id"])
    await db.bookings.update_one({"_id": booking["_id"]}, {"$set": {"service_started_at": now_ist() - timedelta(minutes=30)}})
    return await db.bookings.find_one({"_id": booking["_id"]})


# ------------------------------------------------------------- the incident


@pytest.mark.asyncio
async def test_captain_two_km_away_enters_the_right_code_and_finishes_the_job(rig, db):
    bs = BookingService(db)
    booking = await _on_the_way(rig, db, at=TWO_KM)
    bid = str(booking["_id"])

    arrived = await _arrive(rig, db, booking, at=TWO_KM)
    assert arrived["vehicle_verified"] is True
    doc = await db.bookings.find_one({"_id": booking["_id"]})
    warning = doc["location_warning"]
    assert warning["resolved"] is False and warning["step"] == "arrival"
    assert warning["reasons"] == ["outside_geofence"]
    assert 1990 < warning["distance_m"] < 2010 and warning["allowed_m"] == 500
    assert warning["captain_id"] == rig["captain_id"]
    assert warning["message"].endswith("entered the service code 2.0 km away from the customer's address")
    assert warning["message"].startswith("Captain ")
    # The manager heard about it, in the app.
    note = await db.notifications.find_one({"user_id": rig["manager_id"], "title": {"$regex": "Location Flagged"}})
    assert note and "2.0 km away from the customer's address" in note["message"]
    # It is NOT an issue flag: nothing about it can block the captain.
    assert not doc.get("issue_flag")

    # He carries on exactly as normal — also from 2 km away.
    await bs.capture_before_photo(bid, _photo(await _upload(db, rig, "b"), TWO_KM), rig["captain_id"])
    assert (await db.bookings.find_one({"_id": booking["_id"]}))["status"] == "service_started"
    await db.bookings.update_one({"_id": booking["_id"]}, {"$set": {"service_started_at": now_ist() - timedelta(minutes=30)}})
    done = await bs.capture_after_photo_and_complete(bid, _photo(await _upload(db, rig, "a"), TWO_KM), rig["captain_id"])
    assert done["status"] == "completed"
    final = await db.bookings.find_one({"_id": booking["_id"]})
    assert final["location_warning"]["step"] == "complete" and final["location_warning"]["resolved"] is False
    assert [final["geo_checks"][s]["status"] for s in ("arrival", "start", "complete")] == ["unverified"] * 3
    assert final["captain_id"] == rig["captain_id"]


@pytest.mark.asyncio
async def test_no_gps_at_all_is_accepted_and_warned_at_every_step(rig, db):
    bs = BookingService(db)
    booking = await _on_the_way(rig, db)
    bid = str(booking["_id"])
    await bs.verify_vehicle(bid, VerifyVehicleRequest(service_code=_code(booking)), rig["captain_id"])
    doc = await db.bookings.find_one({"_id": booking["_id"]})
    assert doc["vehicle_verified"] is True and doc["location_warning"]["reasons"] == ["no_fix"]
    assert doc["location_warning"]["message"].endswith("entered the service code without sharing a location")
    # Photos without any coordinates (denied / timed-out browser) are accepted too.
    await bs.capture_before_photo(bid, PhotoCaptureRequest(image_url=await _upload(db, rig, "b")), rig["captain_id"])
    await db.bookings.update_one({"_id": booking["_id"]}, {"$set": {"service_started_at": now_ist() - timedelta(minutes=30)}})
    await bs.capture_after_photo_and_complete(bid, PhotoCaptureRequest(image_url=await _upload(db, rig, "a")), rig["captain_id"])
    final = await db.bookings.find_one({"_id": booking["_id"]})
    assert final["status"] == "completed" and final["location_warning"]["step"] == "complete"
    assert "no_fix" in final["geo_checks"]["complete"]["reasons"]
    assert final["after_photo"]["latitude"] is None


@pytest.mark.asyncio
async def test_heading_out_without_a_position_is_accepted(rig, db):
    from app.schemas.booking_schema import HeadingRequest
    from tests.test_gps_integrity import _assigned

    booking = await _assigned(rig, db)
    await BookingService(db).start_heading(str(booking["_id"]), HeadingRequest(equipment_used=[]), rig["captain_id"])
    after = await db.bookings.find_one({"_id": booking["_id"]})
    assert after["status"] == "captain_on_the_way" and after["heading_location"] is None


@pytest.mark.asyncio
async def test_a_good_arrival_has_no_warning(rig, db):
    booking = await _on_the_way(rig, db)
    await _arrive(rig, db, booking, at=_north(100))
    doc = await db.bookings.find_one({"_id": booking["_id"]})
    assert not doc.get("location_warning") and doc["geo_checks"]["arrival"]["status"] == "verified"
    assert await db.notifications.count_documents({"user_id": rig["manager_id"], "title": {"$regex": "Location Flagged"}}) == 0


@pytest.mark.asyncio
async def test_the_captain_and_the_customer_never_see_the_warning(rig, db):
    booking = await _on_the_way(rig, db, at=TWO_KM)
    arrived = await _arrive(rig, db, booking, at=TWO_KM)
    assert "location_warning" in arrived  # staff see it
    assert "location_warning" not in redact_for_captain(arrived)
    from app.services.booking_service import _CUSTOMER_HIDDEN_FIELDS

    assert "location_warning" in _CUSTOMER_HIDDEN_FIELDS


@pytest.mark.asyncio
async def test_a_multi_car_visit_warns_every_car_and_ignore_clears_the_visit(rig, db):
    booking = await _on_the_way(rig, db, at=TWO_KM)
    sibling_id = ObjectId()
    sib = dict(booking)
    sib.update({"_id": sibling_id, "booking_number": booking["booking_number"] + "B", "booking_group_id": "grp-warn", "service_code": booking["service_code"], "visit_line_key": str(ObjectId())})
    sib.pop("seat_key", None)
    await db.bookings.update_one({"_id": booking["_id"]}, {"$set": {"booking_group_id": "grp-warn"}})
    await db.bookings.insert_one(sib)
    await _arrive(rig, db, await db.bookings.find_one({"_id": booking["_id"]}), at=TWO_KM)
    for oid in (booking["_id"], sibling_id):
        assert (await db.bookings.find_one({"_id": oid}))["location_warning"]["resolved"] is False
    await BookingService(db).ignore_location_warning(str(booking["_id"]), None, rig["manager_id"], "manager", rig["center_id"])
    for oid in (booking["_id"], sibling_id):
        assert (await db.bookings.find_one({"_id": oid}))["location_warning"]["resolved"] is True
    await db.bookings.delete_one({"_id": sibling_id})


# --------------------------------------------------------------- Ignore


@pytest.mark.asyncio
async def test_ignore_clears_the_warning_once_and_is_idempotent(rig, db):
    bs = BookingService(db)
    booking = await _on_the_way(rig, db, at=TWO_KM)
    await _arrive(rig, db, booking, at=TWO_KM)
    bid = str(booking["_id"])

    result = await bs.ignore_location_warning(bid, "Customer is at his office", rig["manager_id"], "manager", rig["center_id"])
    warning = result["location_warning"]
    assert warning["resolved"] is True and warning["ignored_by"] == rig["manager_id"] and warning["ignore_note"] == "Customer is at his office"
    assert warning["ignored_at"] is not None and warning["distance_m"]  # the evidence stays
    history = await db.booking_status_history.count_documents({"booking_id": bid, "note": {"$regex": "Location warning ignored"}})
    audit = await db.audit_logs.count_documents({"action": "IGNORE_LOCATION_WARNING", "target_id": bid}) if "audit_logs" in await db.list_collection_names() else 1
    assert history == 1 and audit == 1

    again = await bs.ignore_location_warning(bid, None, rig["manager_id"], "manager", rig["center_id"])
    assert again["location_warning"]["ignored_at"] == warning["ignored_at"]  # nothing rewritten
    assert await db.booking_status_history.count_documents({"booking_id": bid, "note": {"$regex": "Location warning ignored"}}) == 1


@pytest.mark.asyncio
async def test_ignore_is_for_the_own_center_manager_or_an_admin(rig, db):
    bs = BookingService(db)
    booking = await _on_the_way(rig, db, at=TWO_KM)
    await _arrive(rig, db, booking, at=TWO_KM)
    bid = str(booking["_id"])
    with pytest.raises(ForbiddenException):
        await bs.ignore_location_warning(bid, None, rig["other_manager"], "manager", rig["other_center"])
    assert (await db.bookings.find_one({"_id": booking["_id"]}))["location_warning"]["resolved"] is False
    admin = await bs.ignore_location_warning(bid, None, "admin-id-1", "admin", None)
    assert admin["location_warning"]["resolved"] is True and admin["location_warning"]["ignored_by_role"] == "admin"


@pytest.mark.asyncio
async def test_ignore_without_a_warning_is_a_clear_error(rig, db):
    booking = await _on_the_way(rig, db)
    with pytest.raises(BadRequestException):
        await BookingService(db).ignore_location_warning(str(booking["_id"]), None, rig["manager_id"], "manager", rig["center_id"])


def test_ignore_route_is_manager_or_admin_only():
    import asyncio

    from app.core.dependencies import CurrentUser
    from app.routes.v1.booking_routes import router

    wanted = {"/bookings/{booking_id}/location-warning/ignore", "/bookings/{booking_id}/mark-arrived", "/bookings/{booking_id}/complete-for-captain"}
    seen = set()
    for route in router.routes:
        if getattr(route, "path", "") in wanted:
            seen.add(route.path)
            for dep in route.dependencies:
                for role in ("captain", "customer"):
                    with pytest.raises(ForbiddenException):
                        asyncio.run(dep.dependency(current_user=CurrentUser(id="x", role=role, service_center_id="c")))
    assert seen == wanted


# ------------------------------------------- the boundary: 500 m exactly


def test_the_default_boundary_is_500_metres():
    assert DEFAULT_BOOKING_POLICY["photo_geofence_radius_m"] == 500
    assert "300" not in inspect.getsource(GeoIntegrityService.evaluate)


def _offset_for(metres: float) -> tuple[float, float]:
    return (NEAR[0] + metres / (6371000 * 3.141592653589793 / 180), NEAR[1])


@pytest.mark.asyncio
async def test_exactly_500_m_is_inside_and_500_1_m_is_outside(rig, db):
    geo = GeoIntegrityService(db)
    policy = await BookingPolicyService(db).get_policy()
    assert policy["photo_geofence_radius_m"] == 500
    booking = {"_id": ObjectId(), "heading_at": None}

    async def verdict(metres: float):
        at = _offset_for(metres)
        fix = Fix(latitude=at[0], longitude=at[1], received_at=_utc_now(), accuracy_m=None)
        return await geo.evaluate("arrival", booking, rig["captain_id"], fix, NEAR, policy)

    on_the_line = await verdict(500.0)
    assert on_the_line.distance_m == 500.0 and on_the_line.ok
    just_out = await verdict(500.1)
    assert just_out.distance_m == 500.1 and {r["code"] for r in just_out.reasons} == {"outside_geofence"}
    # The phone's own accuracy widens it (capped at 100 m).
    fix = Fix(latitude=_offset_for(560)[0], longitude=NEAR[1], received_at=_utc_now(), accuracy_m=80)
    assert (await geo.evaluate("arrival", booking, rig["captain_id"], fix, NEAR, policy)).ok


@pytest.mark.asyncio
async def test_the_legacy_photo_flag_uses_the_same_boundary(rig, db):
    bs = BookingService(db)
    booking = await _on_the_way(rig, db)
    assert await bs._check_geofence(booking, *_offset_for(499.0)) == (False, 499.0)
    flagged, distance = await bs._check_geofence(booking, *_offset_for(500.4))
    assert flagged is True and distance == 500.4
    assert await bs._check_geofence(booking, None, None) == (False, None)  # no fix, no distance


# ---------------------------------------------------------- unpinned


@pytest.mark.asyncio
async def test_a_missing_or_zero_pin_never_produces_a_distance(rig, db):
    bs = BookingService(db)
    booking = await _on_the_way(rig, db, at=FAR)
    for lat, lng in ((None, None), (0, 0), (0.0, 0.0)):
        await db.bookings.update_one({"_id": booking["_id"]}, {"$set": {"address_snapshot": {"latitude": lat, "longitude": lng}}})
        fresh = await db.bookings.find_one({"_id": booking["_id"]})
        assert await bs._site_of(fresh) is None
        assert await bs._check_geofence(fresh, FAR[0], FAR[1]) == (False, None)
    # A captain arriving anywhere is not warned about a distance to nowhere.
    await _arrive(rig, db, booking, at=FAR)
    doc = await db.bookings.find_one({"_id": booking["_id"]})
    assert doc["arrival_distance_m"] is None and not doc.get("location_warning")
    assert doc["geo_checks"]["arrival"]["status"] == "unpinned"


# ------------------------------------------------- manager: Mark Arrived


@pytest.mark.asyncio
async def test_manager_mark_arrived_unsticks_a_captain_and_he_goes_on_with_photos(rig, db, monkeypatch):
    bs = BookingService(db)
    booking = await _on_the_way(rig, db)
    bid = str(booking["_id"])
    # The arrival check is locked (5 wrong codes) and flagged.
    for _ in range(5):
        with pytest.raises(BadRequestException):
            await bs.verify_vehicle(bid, VerifyVehicleRequest(service_code="0000", latitude=NEAR[0], longitude=NEAR[1]), rig["captain_id"])
    locked = await db.bookings.find_one({"_id": booking["_id"]})
    assert locked["arrival_code_locked"] is True and locked["issue_flag"] == "arrival_code_locked"

    sent = _record_broadcasts(monkeypatch)
    with pytest.raises(BadRequestException):
        await bs.manager_mark_arrived(bid, "no", rig["manager_id"], "manager", rig["center_id"])  # reason too short
    with pytest.raises(ForbiddenException):
        await bs.manager_mark_arrived(bid, "Captain is at the door", rig["other_manager"], "manager", rig["other_center"])

    result = await bs.manager_mark_arrived(bid, "Captain is at the door, customer confirmed", rig["manager_id"], "manager", rig["center_id"])
    assert result["vehicle_verified"] is True and result["status"] == "captain_on_the_way"
    doc = await db.bookings.find_one({"_id": booking["_id"]})
    assert doc["arrival_code_locked"] is False and doc["arrival_code_failures"] == 0
    assert doc["issue_flag"] is None and doc["issue_resolved"] is True
    assert doc["arrival_confirmed_by"] == rig["manager_id"] and doc["geo_checks"]["arrival"]["status"] == "confirmed"
    history = await db.booking_status_history.find_one({"booking_id": bid, "note": {"$regex": "^Arrival confirmed by .* — Captain is at the door"}})
    assert history is not None
    assert await db.audit_logs.count_documents({"action": "MANAGER_MARK_ARRIVED", "target_id": bid}) == 1
    assert await db.notifications.count_documents({"user_id": rig["captain_id"], "title": "Arrival Confirmed"}) == 1
    assert f"user:{rig['captain_id']}" in sent  # his app moves to the next step now

    # He continues with the photos from his app; and a second Mark Arrived is refused.
    await bs.capture_before_photo(bid, _photo(await _upload(db, rig, "b")), rig["captain_id"])
    assert (await db.bookings.find_one({"_id": booking["_id"]}))["status"] == "service_started"
    with pytest.raises(BadRequestException):
        await bs.manager_mark_arrived(bid, "Captain is at the door", rig["manager_id"], "manager", rig["center_id"])


@pytest.mark.asyncio
async def test_mark_arrived_only_while_on_the_way(rig, db):
    from tests.test_gps_integrity import _assigned

    booking = await _assigned(rig, db)
    with pytest.raises(BadRequestException):
        await BookingService(db).manager_mark_arrived(str(booking["_id"]), "Captain is at the door", rig["manager_id"], "manager", rig["center_id"])


# ------------------------------------------- manager: Complete For Captain


async def _wallet_view(db, booking_id: str) -> dict:
    doc = await db.bookings.find_one({"_id": ObjectId(booking_id)})
    keys = ("status", "captain_id", "captain_earning", "captain_wallet_posted", "wallet_settled", "completed_by_role", "amount_paid", "payment_status")
    return {k: doc.get(k) for k in keys}


@pytest.mark.asyncio
async def test_complete_for_captain_credits_the_captain_exactly_like_his_own_completion(rig, db):
    bs = BookingService(db)
    # Baseline: the captain completes one himself.
    mine = await _started(rig, db)
    await bs.capture_after_photo_and_complete(str(mine["_id"]), _photo(await _upload(db, rig, "a")), rig["captain_id"])
    baseline = await _wallet_view(db, str(mine["_id"]))

    other = await _started(rig, db)
    oid = str(other["_id"])
    # Money is due: the manager must say how it is collected.
    with pytest.raises(BadRequestException):
        await bs.complete_for_captain(oid, "Captain's phone is dead", None, rig["manager_id"], "manager", rig["center_id"])
    with pytest.raises(ForbiddenException):
        await bs.complete_for_captain(oid, "Captain's phone is dead", "captain", rig["other_manager"], "manager", rig["other_center"])
    with pytest.raises(BadRequestException):
        await bs.complete_for_captain(oid, "ok", "captain", rig["manager_id"], "manager", rig["center_id"])  # reason too short

    done = await bs.complete_for_captain(oid, "Captain's phone is dead", "captain", rig["manager_id"], "manager", rig["center_id"])
    assert done["status"] == "completed"
    view = await _wallet_view(db, oid)
    assert view["captain_id"] == rig["captain_id"] and view["completed_by_role"] is None
    assert view["captain_earning"] == baseline["captain_earning"] and view["captain_earning"] > 0
    assert view["captain_wallet_posted"] == baseline["captain_wallet_posted"]
    assert view["wallet_settled"] == baseline["wallet_settled"]
    # Nothing was collected: it is still due for the captain to take from his app.
    assert view["amount_paid"] == baseline["amount_paid"] == 0 or view["amount_paid"] == baseline["amount_paid"]
    doc = await db.bookings.find_one({"_id": other["_id"]})
    assert doc["completed_on_behalf_by"] == rig["manager_id"] and doc["completed_on_behalf_reason"] == "Captain's phone is dead"
    assert await db.booking_status_history.count_documents({"booking_id": oid, "note": {"$regex": "^Completed for the captain by"}}) == 1
    assert await db.audit_logs.count_documents({"action": "MANAGER_COMPLETE_FOR_CAPTAIN", "target_id": oid}) == 1
    assert await db.notifications.count_documents({"user_id": rig["captain_id"], "title": "Job Completed By Your Manager"}) == 1
    # A second tap changes nothing.
    with pytest.raises(BadRequestException):
        await bs.complete_for_captain(oid, "Captain's phone is dead", "captain", rig["manager_id"], "manager", rig["center_id"])

    # The captain then collects the cash from his own app, exactly once.
    from app.services.payment_service import PaymentService

    due = float((await db.bookings.find_one({"_id": other["_id"]}))["total_amount"])
    took = await PaymentService(db).captain_collect_cash(oid, rig["captain_id"], expected_amount=due)
    assert took["amount"] == due
    with pytest.raises(BadRequestException):
        await PaymentService(db).captain_collect_cash(oid, rig["captain_id"], expected_amount=due)


@pytest.mark.asyncio
async def test_complete_for_captain_when_the_manager_took_the_cash_never_touches_the_captains_cash(rig, db):
    bs = BookingService(db)
    other = await _started(rig, db)
    oid = str(other["_id"])
    total = float(other["total_amount"])
    await bs.complete_for_captain(oid, "Customer paid me at the center", "cash", rig["manager_id"], "manager", rig["center_id"])
    doc = await db.bookings.find_one({"_id": other["_id"]})
    assert doc["status"] == "completed" and doc["payment_status"] == "paid"
    assert float(doc["amount_paid"]) == total
    assert not doc.get("captain_cash_collected")  # the captain holds no cash for it
    assert doc["captain_id"] == rig["captain_id"] and doc["captain_earning"] > 0
    # Nothing left for the captain to collect, so he cannot take it a second time.
    from app.services.payment_service import PaymentService

    with pytest.raises(BadRequestException):
        await PaymentService(db).captain_collect_cash(oid, rig["captain_id"], expected_amount=total)


@pytest.mark.asyncio
async def test_complete_for_captain_works_from_on_the_way_and_only_then(rig, db):
    bs = BookingService(db)
    booking = await _on_the_way(rig, db)
    bid = str(booking["_id"])
    done = await bs.complete_for_captain(bid, "Job done, app crashed", "captain", rig["manager_id"], "manager", rig["center_id"])
    assert done["status"] == "completed"
    doc = await db.bookings.find_one({"_id": booking["_id"]})
    assert doc["vehicle_verified"] is True and doc["captain_id"] == rig["captain_id"]

    from tests.test_gps_integrity import _assigned

    assigned = await _assigned(rig, db)  # not started yet: the existing Mark Done is for that
    with pytest.raises(BadRequestException):
        await bs.complete_for_captain(str(assigned["_id"]), "Job done, app crashed", "captain", rig["manager_id"], "manager", rig["center_id"])


def test_mark_done_refusal_points_at_the_new_actions():
    assert "Complete For Captain" in BookingService._mark_done_refusal("service_started")
    assert "Mark Arrived" in BookingService._mark_done_refusal("captain_on_the_way")


# ------------------------------------------------- the stale "on the way"


def _record_broadcasts(monkeypatch) -> list[str]:
    from app.services import booking_service as module

    channels: list[str] = []

    async def fake(channel, message):
        channels.append(channel)

    monkeypatch.setattr(module.ws_manager, "broadcast", fake)
    return channels


async def _step_verify(rig, db, bs):
    booking = await _on_the_way(rig, db)
    return str(booking["_id"]), bs.verify_vehicle(str(booking["_id"]), VerifyVehicleRequest(service_code=_code(booking), latitude=NEAR[0], longitude=NEAR[1]), rig["captain_id"])


async def _step_heading(rig, db, bs):
    from app.schemas.booking_schema import HeadingRequest
    from tests.test_gps_integrity import _assigned

    booking = await _assigned(rig, db)
    return str(booking["_id"]), bs.start_heading(str(booking["_id"]), HeadingRequest(latitude=NEAR[0], longitude=NEAR[1], equipment_used=[]), rig["captain_id"])


async def _step_before(rig, db, bs):
    booking = await _on_the_way(rig, db)
    await _arrive(rig, db, booking)
    return str(booking["_id"]), bs.capture_before_photo(str(booking["_id"]), _photo(await _upload(db, rig, "b")), rig["captain_id"])


async def _step_after(rig, db, bs):
    booking = await _started(rig, db)
    return str(booking["_id"]), bs.capture_after_photo_and_complete(str(booking["_id"]), _photo(await _upload(db, rig, "a")), rig["captain_id"])


async def _step_mark_arrived(rig, db, bs):
    booking = await _on_the_way(rig, db)
    return str(booking["_id"]), bs.manager_mark_arrived(str(booking["_id"]), "Captain is at the door", rig["manager_id"], "manager", rig["center_id"])


async def _step_complete_for_captain(rig, db, bs):
    booking = await _started(rig, db)
    return str(booking["_id"]), bs.complete_for_captain(str(booking["_id"]), "Captain's phone is dead", "captain", rig["manager_id"], "manager", rig["center_id"])


async def _step_ignore(rig, db, bs):
    booking = await _on_the_way(rig, db, at=TWO_KM)
    await _arrive(rig, db, booking, at=TWO_KM)
    return str(booking["_id"]), bs.ignore_location_warning(str(booking["_id"]), None, rig["manager_id"], "manager", rig["center_id"])


async def _step_unlock(rig, db, bs):
    booking = await _on_the_way(rig, db)
    await db.bookings.update_one({"_id": booking["_id"]}, {"$set": {"arrival_code_locked": True, "arrival_code_failures": 5}})
    return str(booking["_id"]), bs.unlock_arrival_code(str(booking["_id"]), rig["manager_id"], "manager", rig["center_id"])


async def _step_release(rig, db, bs):
    from app.schemas.booking_schema import CaptainCancelRequest

    booking = await _on_the_way(rig, db)
    return str(booking["_id"]), bs.captain_cancel(str(booking["_id"]), CaptainCancelRequest(reason="Bike broke down"), rig["captain_id"])


async def _step_resolve(rig, db, bs):
    booking = await _on_the_way(rig, db)
    await bs.flag_issue(str(booking["_id"]), "captain_delay", "late", send_whatsapp=False)
    return str(booking["_id"]), bs.resolve_issue(str(booking["_id"]), rig["manager_id"], "Called him, fine", "manager", rig["center_id"])


async def _step_wrong_code(rig, db, bs):
    booking = await _on_the_way(rig, db)

    async def go():
        with pytest.raises(BadRequestException):
            await bs.verify_vehicle(str(booking["_id"]), VerifyVehicleRequest(service_code="0000", latitude=NEAR[0], longitude=NEAR[1]), rig["captain_id"])

    return str(booking["_id"]), go()


STEPS = {
    "captain_heading": _step_heading,
    "captain_verify_vehicle": _step_verify,
    "captain_wrong_code": _step_wrong_code,
    "captain_before_photo": _step_before,
    "captain_after_photo_complete": _step_after,
    "captain_release": _step_release,
    "manager_mark_arrived": _step_mark_arrived,
    "manager_complete_for_captain": _step_complete_for_captain,
    "manager_ignore_location_warning": _step_ignore,
    "manager_unlock_arrival_code": _step_unlock,
    "manager_resolve_issue": _step_resolve,
}


@pytest.mark.asyncio
@pytest.mark.parametrize("name", sorted(STEPS))
async def test_every_booking_step_pings_the_booking_center_customer_and_captain(rig, db, monkeypatch, name):
    """The 'captain on the way' that never turned into 'completed' on the
    manager's screen: verify_vehicle never announced itself. EVERY step that
    changes a booking must ping booking:{id}, center-bookings:{center},
    user:{customer}, user:{captain} and admin:live."""
    bs = BookingService(db)
    booking_id, call = await STEPS[name](rig, db, bs)
    sent = _record_broadcasts(monkeypatch)
    await call
    center = (await db.bookings.find_one({"_id": ObjectId(booking_id)}))["service_center_id"]
    customer = (await db.bookings.find_one({"_id": ObjectId(booking_id)}))["customer_id"]
    expected = {f"booking:{booking_id}", f"center-bookings:{center}", f"user:{customer}", "admin:live"}
    assert expected <= set(sent), f"{name} did not announce itself: missing {expected - set(sent)}"
    if name not in ("captain_release",):
        assert f"user:{rig['captain_id']}" in sent, f"{name}: the captain's own app was not pinged"


def test_every_state_changing_booking_route_is_accounted_for():
    """A new POST/PATCH under /bookings must be listed here — either covered by
    the broadcast test above or consciously exempt — so a new step cannot ship
    silently without announcing itself."""
    from app.routes.v1.booking_routes import router

    covered_by_step_test = {
        "/bookings/{booking_id}/heading", "/bookings/{booking_id}/verify-vehicle", "/bookings/{booking_id}/before-photo",
        "/bookings/{booking_id}/after-photo", "/bookings/{booking_id}/captain-cancel", "/bookings/{booking_id}/mark-arrived",
        "/bookings/{booking_id}/complete-for-captain", "/bookings/{booking_id}/location-warning/ignore",
        "/bookings/{booking_id}/unlock-arrival-code", "/bookings/{booking_id}/resolve-issue",
    }
    covered_elsewhere = {  # each is exercised by its own flow's tests and already calls _broadcast_booking_changed
        "/bookings", "/bookings/quick", "/bookings/manager-quick", "/bookings/manager-log-completed", "/bookings/manager-create",
        "/bookings/group", "/bookings/group/{booking_group_id}/switch-to-cash", "/bookings/group/{booking_group_id}/cancel",
        "/bookings/group/{booking_group_id}/assign-captain", "/bookings/group/{booking_group_id}", "/bookings/{booking_id}",
        "/bookings/{booking_id}/switch-to-cash", "/bookings/{booking_id}/cancel", "/bookings/{booking_id}/reschedule",
        "/bookings/{booking_id}/assign-captain", "/bookings/{booking_id}/reassign-captain", "/bookings/{booking_id}/self-assign",
        "/bookings/{booking_id}/mark-done", "/bookings/{booking_id}/report-risk", "/bookings/{booking_id}/priority",
        "/bookings/{booking_id}/add-services", "/bookings/{booking_id}/extras", "/bookings/{booking_id}/tip",
        "/bookings/{booking_id}/discount", "/bookings/{booking_id}/details", "/bookings/{booking_id}/delete",
        "/bookings/{booking_id}/restore", "/bookings/{booking_id}/permanent",
    }
    exempt = {  # no booking row changes status / assignment, or they only move slot holds / counters
        "/bookings/verify-phone/request", "/bookings/quote", "/bookings/admin/reconcile-slot-counters", "/bookings/hold",
        "/bookings/hold/release", "/bookings/{booking_id}/geo-approval-request", "/bookings/{booking_id}/geo-override",
    }
    writes = set()
    for route in router.routes:
        if getattr(route, "methods", None) and route.methods & {"POST", "PATCH", "DELETE", "PUT"}:
            writes.add(route.path)
    unknown = writes - covered_by_step_test - covered_elsewhere - exempt
    assert not unknown, f"New booking write route(s) with no broadcast decision: {sorted(unknown)}"
    missing = (covered_by_step_test | covered_elsewhere | exempt) - writes
    assert not missing, f"Listed but no longer routes (update this list): {sorted(missing)}"


def test_every_captain_and_manager_step_function_announces_itself():
    """Static twin of the runtime test: the BookingService methods behind the
    captain / manager step routes must reach _broadcast_booking_changed."""
    reaches = {
        "start_heading": "start_heading", "verify_vehicle": "verify_vehicle", "capture_before_photo": "capture_before_photo",
        "capture_after_photo_and_complete": "_after_captain_completion", "captain_cancel": "captain_cancel",
        "manager_mark_arrived": "manager_mark_arrived", "complete_for_captain": "_after_captain_completion",
        "ignore_location_warning": "ignore_location_warning", "unlock_arrival_code": "unlock_arrival_code",
        "resolve_issue": "resolve_issue", "manager_mark_done": "manager_mark_done",
    }
    for name, via in reaches.items():
        source = inspect.getsource(getattr(BookingService, name)) + inspect.getsource(getattr(BookingService, via))
        assert "_broadcast_booking_changed" in source, name


# ----------------------------------------------- boot migration 300 -> 500


async def _store_policy(db, value: dict, history: list[dict] | None = None):
    await db.settings.delete_many({"key": "booking_policy"})
    await db.settings_history.delete_many({"key": "booking_policy"})
    await db.settings.insert_one({"key": "booking_policy", "value": value, "description": "t"})
    for row in history or []:
        await db.settings_history.insert_one({"key": "booking_policy", "changed_by": "u1", "changed_at": _utc_now(), **row})
    from app.services import booking_policy_service as module

    module._POLICY_CACHE["value"] = None


@pytest.mark.asyncio
async def test_migration_moves_an_untouched_300_to_500_once(db):
    try:
        # An admin saved some OTHER field once: the save materialised the old default 300.
        await _store_policy(db, {"photo_geofence_radius_m": 300, "max_advance_days": 9}, [
            {"changes": {"photo_geofence_radius_m": {"from": None, "to": 300}, "max_advance_days": {"from": None, "to": 9}}},
        ])
        first = await migrate_geofence_radius_to_500(db)
        assert first == {"migrated": True}
        stored = (await db.settings.find_one({"key": "booking_policy"}))["value"]
        assert stored["photo_geofence_radius_m"] == 500 and stored["max_advance_days"] == 9
        assert (await BookingPolicyService(db).get_policy())["photo_geofence_radius_m"] == 500
        assert await db.settings_history.count_documents({"key": "booking_policy", "changed_by": "system:geofence-500"}) == 1
        again = await migrate_geofence_radius_to_500(db)  # idempotent
        assert again["migrated"] is False
        assert await db.settings_history.count_documents({"key": "booking_policy", "changed_by": "system:geofence-500"}) == 1
    finally:
        await db.settings.delete_many({"key": "booking_policy"})
        await db.settings_history.delete_many({"key": "booking_policy"})


@pytest.mark.asyncio
async def test_migration_without_any_history_also_moves_300(db):
    try:
        await _store_policy(db, {"photo_geofence_radius_m": 300})
        assert (await migrate_geofence_radius_to_500(db))["migrated"] is True
    finally:
        await db.settings.delete_many({"key": "booking_policy"})
        await db.settings_history.delete_many({"key": "booking_policy"})


@pytest.mark.asyncio
async def test_migration_leaves_an_admins_choice_alone(db):
    try:
        # Admin set 400 and later typed 300 back: an edit of the radius -> hands off.
        await _store_policy(db, {"photo_geofence_radius_m": 300}, [
            {"changes": {"photo_geofence_radius_m": {"from": 300, "to": 400}}},
            {"changes": {"photo_geofence_radius_m": {"from": 400, "to": 300}}},
        ])
        assert (await migrate_geofence_radius_to_500(db))["migrated"] is False
        assert (await db.settings.find_one({"key": "booking_policy"}))["value"]["photo_geofence_radius_m"] == 300
        # Any other stored value is never touched either.
        await _store_policy(db, {"photo_geofence_radius_m": 250})
        assert (await migrate_geofence_radius_to_500(db))["migrated"] is False
        assert (await db.settings.find_one({"key": "booking_policy"}))["value"]["photo_geofence_radius_m"] == 250
        # No stored policy: nothing to do, the code default (500) applies.
        await db.settings.delete_many({"key": "booking_policy"})
        assert (await migrate_geofence_radius_to_500(db))["migrated"] is False
    finally:
        await db.settings.delete_many({"key": "booking_policy"})
        await db.settings_history.delete_many({"key": "booking_policy"})


def test_the_migration_is_registered_as_a_boot_task():
    from app import main

    names = [candidates[0][1] for _label, candidates in main._OPTIONAL_BOOT_TASKS]
    assert "migrate_geofence_radius_to_500" in names


# ------------------------------------------------------------ over HTTP


def _auth(user: dict, role: str, center_id: str | None) -> dict:
    from app.core.security import create_access_token

    token = create_access_token(str(user["_id"]), role, {"service_center_id": center_id, "tv": user.get("token_version", 0)})
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.asyncio
async def test_the_whole_incident_over_http(rig, db):
    """Captain 2 km out enters the right code through the real route -> 200;
    the manager's queue lists it red (scope=location_warnings); Ignore clears
    it; the captain's own answer never carries the warning."""
    from httpx import ASGITransport, AsyncClient

    from app.main import app

    captain = await db.users.find_one({"_id": ObjectId(rig["captain_id"])})
    manager = await db.users.find_one({"_id": ObjectId(rig["manager_id"])})
    other = await db.users.find_one({"_id": ObjectId(rig["other_manager"])})
    booking = await _on_the_way(rig, db, at=TWO_KM)
    bid = str(booking["_id"])
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test", timeout=60) as http:
        cap_h, mgr_h = _auth(captain, "captain", rig["center_id"]), _auth(manager, "manager", rig["center_id"])
        verify = await http.post(f"/api/v1/bookings/{bid}/verify-vehicle", headers=cap_h, json={"service_code": _code(booking), "latitude": TWO_KM[0], "longitude": TWO_KM[1], "accuracy_m": 20})
        assert verify.status_code == 200, verify.text
        assert verify.json()["data"]["vehicle_verified"] is True and "location_warning" not in verify.json()["data"]

        listing = await http.get(f"/api/v1/bookings/center/{rig['center_id']}", headers=mgr_h, params={"scope": "location_warnings"})
        assert listing.status_code == 200 and [b["id"] for b in listing.json()["data"]] == [bid]
        assert listing.json()["data"][0]["location_warning"]["message"].endswith("2.0 km away from the customer's address")
        attention = await http.get(f"/api/v1/bookings/center/{rig['center_id']}", headers=mgr_h, params={"scope": "attention"})
        assert bid in [b["id"] for b in attention.json()["data"]]

        # Another center's manager is refused; an unauthenticated / captain call too.
        denied = await http.post(f"/api/v1/bookings/{bid}/location-warning/ignore", headers=_auth(other, "manager", rig["other_center"]), json={})
        assert denied.status_code in (403, 404)
        assert (await http.post(f"/api/v1/bookings/{bid}/location-warning/ignore", headers=cap_h, json={})).status_code == 403

        ok = await http.post(f"/api/v1/bookings/{bid}/location-warning/ignore", headers=mgr_h, json={"note": "Customer waited at the gate"})
        assert ok.status_code == 200 and ok.json()["data"]["location_warning"]["resolved"] is True
        again = await http.post(f"/api/v1/bookings/{bid}/location-warning/ignore", headers=mgr_h, json={})
        assert again.status_code == 200
        gone = await http.get(f"/api/v1/bookings/center/{rig['center_id']}", headers=mgr_h, params={"scope": "location_warnings"})
        assert gone.json()["data"] == []

        # Old approval endpoints answer gracefully.
        assert (await http.post(f"/api/v1/bookings/{bid}/geo-approval-request", headers=cap_h, json={"step": "arrival"})).json()["data"]["status"] == "not_needed"
        assert (await http.post(f"/api/v1/bookings/{bid}/geo-override", headers=mgr_h, json={"step": "arrival", "approve": True, "reason": "ok fine"})).json()["data"]["status"] == "not_needed"


@pytest.mark.asyncio
async def test_manager_actions_over_http(rig, db):
    from httpx import ASGITransport, AsyncClient

    from app.main import app

    manager = await db.users.find_one({"_id": ObjectId(rig["manager_id"])})
    booking = await _on_the_way(rig, db)
    bid = str(booking["_id"])
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test", timeout=60) as http:
        mgr_h = _auth(manager, "manager", rig["center_id"])
        short = await http.post(f"/api/v1/bookings/{bid}/mark-arrived", headers=mgr_h, json={"reason": "no"})
        assert short.status_code == 422
        arrived = await http.post(f"/api/v1/bookings/{bid}/mark-arrived", headers=mgr_h, json={"reason": "At the gate, spoke to him"})
        assert arrived.status_code == 200 and arrived.json()["data"]["vehicle_verified"] is True
        await BookingService(db).capture_before_photo(bid, _photo(await _upload(db, rig, "b")), rig["captain_id"])
        due = await http.post(f"/api/v1/bookings/{bid}/complete-for-captain", headers=mgr_h, json={"reason": "His phone died"})
        assert due.status_code == 400 and "how" in due.json()["message"].lower()
        done = await http.post(f"/api/v1/bookings/{bid}/complete-for-captain", headers=mgr_h, json={"reason": "His phone died", "payment": "captain"})
        assert done.status_code == 200 and done.json()["data"]["status"] == "completed"
