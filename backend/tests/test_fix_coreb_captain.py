"""Core fix round 2 — captain job steps (audit 2026-10-07: CAP-01 center
re-check, CAP-02 arrival-code lockout / GPS / photo binding / quick
completion, CAP-03 inventory scope, CAP-04 GPS ranges, NTF-03 "captain
arrived")."""
import asyncio
from datetime import datetime, timedelta, timezone

import pytest
from bson import ObjectId

from app.core.exceptions import BadRequestException, ForbiddenException
from app.schemas.booking_schema import HeadingRequest, PhotoCaptureRequest, VerifyVehicleRequest
from app.services.booking_service import BookingService
from app.services.notification_service import NotificationService
from tests import test_fix_core_helpers as h
from tests import test_fix_coreb_helpers as hb
from tests.factories import own_upload_url

pytestmark = pytest.mark.asyncio
GPS = {"latitude": 22.7, "longitude": 75.8}


@pytest.fixture
def sent(monkeypatch):
    calls: list[dict] = []
    real = NotificationService.notify

    async def spy(self, user_id, title, message, *args, **kwargs):
        calls.append({"user_id": user_id, "title": title, "message": message, **kwargs})
        return await real(self, user_id, title, message, *args, **kwargs)

    monkeypatch.setattr(NotificationService, "notify", spy)
    return calls


async def _assigned(db, s: dict, slot_index: int = 0) -> dict:
    b = await hb.new_booking(db, s["cu"], s["when"], s["keys"][slot_index])
    await db.bookings.update_one({"_id": ObjectId(b["id"])}, {"$set": {
        "captain_id": s["cap"]["id"], "status": "assigned", "assigned_at": datetime.now(timezone.utc),
        "estimated_start_at": datetime.now(timezone.utc),
    }})
    return await hb.booking(db, b["id"])


# ---------------------------------------------------------------- CAP-01


async def test_cap01_captain_moved_to_another_center_cannot_work_old_jobs(db):
    s = await hb.staffed_center(db)
    job = await _assigned(db, s)
    jid = str(job["_id"])
    other_center, _pin = await h.center(db)
    await db.users.update_one({"_id": ObjectId(s["cap"]["id"])}, {"$set": {"service_center_id": other_center}})
    moved = h.auth(s["cap"]["id"], "captain", other_center)
    async with h.client() as c:
        for path, body in (
            ("heading", GPS), ("report-risk", {"note": "running late"}), ("captain-cancel", {"reason": "can't make it"}),
        ):
            r = await c.post(f"/api/v1/bookings/{jid}/{path}", json=body, headers=moved)
            assert r.status_code == 403, (path, r.text)
        await hb.on_the_way(db, jid, s["cap"]["id"])
        r = await c.post(f"/api/v1/bookings/{jid}/verify-vehicle", json={"service_code": job["service_code"], **GPS}, headers=moved)
        assert r.status_code == 403, r.text
    svc = BookingService(db)
    await db.bookings.update_one({"_id": job["_id"]}, {"$set": {"vehicle_verified": True}})
    with pytest.raises(ForbiddenException):
        await svc.capture_before_photo(jid, await hb.snap(db, s["cap"]["id"]), s["cap"]["id"])
    await db.bookings.update_one({"_id": job["_id"]}, {"$set": {"status": "service_started", "service_started_at": datetime.now(timezone.utc) - timedelta(minutes=30)}})
    with pytest.raises(ForbiddenException):
        await svc.capture_after_photo_and_complete(jid, await hb.snap(db, s["cap"]["id"]), s["cap"]["id"])
    doc = await hb.booking(db, jid)
    assert doc["status"] == "service_started" and not doc.get("wallet_settled")
    # Back at his own center, the same captain works it normally.
    await db.users.update_one({"_id": ObjectId(s["cap"]["id"])}, {"$set": {"service_center_id": s["center_id"]}})
    done = await svc.capture_after_photo_and_complete(jid, await hb.snap(db, s["cap"]["id"]), s["cap"]["id"])
    assert done["status"] == "completed"


async def test_cap01_switched_off_captain_cannot_work_a_job(db):
    s = await hb.staffed_center(db)
    job = await _assigned(db, s)
    await db.users.update_one({"_id": ObjectId(s["cap"]["id"])}, {"$set": {"status": "inactive"}})
    with pytest.raises(ForbiddenException):
        await BookingService(db).start_heading(str(job["_id"]), HeadingRequest(**GPS), s["cap"]["id"])
    assert (await hb.booking(db, str(job["_id"])))["status"] == "assigned"


# ---------------------------------------------------------------- CAP-02 arrival code


async def test_cap02_five_wrong_codes_lock_alert_and_manager_unlocks(db, sent):
    s = await hb.staffed_center(db)
    second = await h.manager(db, s["center_id"])
    job = await _assigned(db, s)
    jid = str(job["_id"])
    await hb.on_the_way(db, jid, s["cap"]["id"])
    svc = BookingService(db)
    wrong = "0000" if job["service_code"] != "0000" else "1111"
    for left in (4, 3, 2, 1):
        with pytest.raises(BadRequestException, match=f"{left} tr"):
            await svc.verify_vehicle(jid, VerifyVehicleRequest(service_code=wrong, **GPS), s["cap"]["id"])
    with pytest.raises(BadRequestException, match="locked"):
        await svc.verify_vehicle(jid, VerifyVehicleRequest(service_code=wrong, **GPS), s["cap"]["id"])
    # Now even the RIGHT code is refused until a manager unlocks it.
    with pytest.raises(BadRequestException, match="locked"):
        await svc.verify_vehicle(jid, VerifyVehicleRequest(service_code=job["service_code"], **GPS), s["cap"]["id"])
    doc = await hb.booking(db, jid)
    assert doc["arrival_code_locked"] is True and doc["arrival_code_failures"] == 5 and not doc.get("vehicle_verified")
    assert doc["issue_flag"] == "arrival_code_locked" and doc["issue_resolved"] is False
    to_named = [x for x in sent if x["user_id"] == s["mgr"]["id"] and "Urgent" in x["title"] and "Arrival check locked" in x["message"]]
    to_second = [x for x in sent if x["user_id"] == second["id"] and "Arrival check locked" in x["title"]]
    assert len(to_named) == 1 and len(to_second) == 1  # every manager of the center, once
    assert all(x.get("send_whatsapp") is False for x in to_named + to_second)  # in-app
    other_center, _pin = await h.center(db)
    stranger = await h.manager(db, other_center)
    async with h.client() as c:
        assert (await c.post(f"/api/v1/bookings/{jid}/unlock-arrival-code", headers=stranger["h"])).status_code == 403
        assert (await c.post(f"/api/v1/bookings/{jid}/unlock-arrival-code", headers=s["cap"]["h"])).status_code == 403
        r = await c.post(f"/api/v1/bookings/{jid}/unlock-arrival-code", headers=s["mgr"]["h"])
        assert r.status_code == 200, r.text
    doc = await hb.booking(db, jid)
    assert doc["arrival_code_locked"] is False and doc["arrival_code_failures"] == 0 and doc["issue_flag"] is None
    assert await db.audit_logs.find_one({"action": "UNLOCK_ARRIVAL_CODE", "target_id": jid})
    ok = await svc.verify_vehicle(jid, VerifyVehicleRequest(service_code=job["service_code"], **GPS), s["cap"]["id"])
    assert ok["vehicle_verified"] is True


async def test_cap02_parallel_wrong_codes_are_all_counted_and_lock_once(db, sent):
    s = await hb.staffed_center(db)
    job = await _assigned(db, s)
    jid = str(job["_id"])
    await hb.on_the_way(db, jid, s["cap"]["id"])
    wrong = "0000" if job["service_code"] != "0000" else "1111"
    svc = BookingService(db)
    results = await asyncio.gather(*(
        svc.verify_vehicle(jid, VerifyVehicleRequest(service_code=wrong, **GPS), s["cap"]["id"]) for _ in range(12)
    ), return_exceptions=True)
    assert all(isinstance(r, BadRequestException) for r in results)
    doc = await hb.booking(db, jid)
    assert doc["arrival_code_locked"] is True and doc["arrival_code_failures"] >= 5
    history = await db.booking_status_history.count_documents({"booking_id": jid, "note": {"$regex": "^Arrival check locked"}})
    assert history == 1


async def test_cap02_code_count_is_per_visit(db):
    s = await hb.staffed_center(db)
    async with h.client() as c:
        gid, ids = await h.group(c, db, s["cu"], s["when"], s["keys"][0], cars=2)
    for i in ids:
        await hb.on_the_way(db, i, s["cap"]["id"])
    code = (await hb.booking(db, ids[0]))["service_code"]
    wrong = "0000" if code != "0000" else "1111"
    svc = BookingService(db)
    for n in range(5):
        with pytest.raises(BadRequestException):
            await svc.verify_vehicle(ids[n % 2], VerifyVehicleRequest(service_code=wrong, **GPS), s["cap"]["id"])
    for i in ids:  # switching cars doesn't buy more tries
        with pytest.raises(BadRequestException, match="locked"):
            await svc.verify_vehicle(i, VerifyVehicleRequest(service_code=code, **GPS), s["cap"]["id"])


async def test_cap02_code_is_never_shown_to_the_captain(db):
    s = await hb.staffed_center(db)
    job = await _assigned(db, s)
    async with h.client() as c:
        jobs = (await c.get("/api/v1/bookings/my-jobs", headers=s["cap"]["h"])).json()["data"]
        one = (await c.get(f"/api/v1/bookings/{job['_id']}", headers=s["cap"]["h"])).json()["data"]
    mine = next(j for j in jobs if j["id"] == str(job["_id"]))
    assert "service_code" not in mine and "service_code" not in one and mine["requires_service_code"] is True


async def test_cap02_arrival_without_gps_is_flagged_never_blocked(db, sent):
    s = await hb.staffed_center(db)
    job = await _assigned(db, s)
    jid = str(job["_id"])
    await hb.on_the_way(db, jid, s["cap"]["id"])
    svc = BookingService(db)
    out = await svc.verify_vehicle(jid, VerifyVehicleRequest(service_code=job["service_code"]), s["cap"]["id"])
    assert out["vehicle_verified"] is True and out["arrival_flagged"] is True and out["arrival_no_gps"] is True
    assert any(x["user_id"] == s["mgr"]["id"] and "Location flagged" in x["title"] and "without sharing" in x["message"] for x in sent)
    started = await svc.capture_before_photo(jid, await hb.snap(db, s["cap"]["id"], "before"), s["cap"]["id"])
    assert started["status"] == "service_started"


# ---------------------------------------------------------------- CAP-02 photo binding


async def _verified(db, s: dict, slot_index: int = 0) -> str:
    job = await _assigned(db, s, slot_index)
    jid = str(job["_id"])
    await hb.on_the_way(db, jid, s["cap"]["id"], vehicle_verified=True)
    return jid


async def test_cap02_photo_must_be_this_captains_fresh_unused_upload(db):
    s = await hb.staffed_center(db)
    jid = await _verified(db, s)
    svc = BookingService(db)
    # Never uploaded through our upload endpoint.
    with pytest.raises(BadRequestException, match="wasn't uploaded from your app"):
        await svc.capture_before_photo(jid, PhotoCaptureRequest(image_url=own_upload_url("photos/never-uploaded.jpg"), **GPS), s["cap"]["id"])
    # Another captain's upload.
    other = await h.captain(db, s["center_id"])
    with pytest.raises(BadRequestException, match="wasn't uploaded from your app"):
        await svc.capture_before_photo(jid, await hb.snap(db, other["id"]), s["cap"]["id"])
    # Too old.
    stale = await hb.snap(db, s["cap"]["id"], "stale")
    from app.core.storage import photo_key_for_url

    await db.uploaded_photos.update_one({"_id": photo_key_for_url(stale.image_url)}, {"$set": {"created_at": datetime.now(timezone.utc) - timedelta(hours=13)}})
    with pytest.raises(BadRequestException, match="too old"):
        await svc.capture_before_photo(jid, stale, s["cap"]["id"])
    assert (await hb.booking(db, jid))["status"] == "captain_on_the_way"
    # A fresh one of his own works — and a double-submit of it is the same start.
    before = await hb.snap(db, s["cap"]["id"], "before")
    first = await svc.capture_before_photo(jid, before, s["cap"]["id"])
    again = await svc.capture_before_photo(jid, before, s["cap"]["id"])
    assert first["status"] == again["status"] == "service_started"
    # The before photo can't be the after photo.
    await db.bookings.update_one({"_id": ObjectId(jid)}, {"$set": {"service_started_at": datetime.now(timezone.utc) - timedelta(minutes=30)}})
    with pytest.raises(BadRequestException, match="new photo"):
        await svc.capture_after_photo_and_complete(jid, before, s["cap"]["id"])
    # Nor can one job's photo serve another job.
    jid2 = await _verified(db, s, 1)
    with pytest.raises(BadRequestException, match="already used"):
        await svc.capture_before_photo(jid2, before, s["cap"]["id"])
    done = await svc.capture_after_photo_and_complete(jid, await hb.snap(db, s["cap"]["id"], "after"), s["cap"]["id"])
    assert done["status"] == "completed" and not done.get("quick_completion_flagged")


async def test_cap02_completion_minutes_after_start_is_flagged_not_blocked(db, sent):
    s = await hb.staffed_center(db)
    jid = await _verified(db, s)
    svc = BookingService(db)
    await svc.capture_before_photo(jid, await hb.snap(db, s["cap"]["id"], "b"), s["cap"]["id"])
    done = await svc.capture_after_photo_and_complete(jid, await hb.snap(db, s["cap"]["id"], "a"), s["cap"]["id"])
    assert done["status"] == "completed"
    doc = await hb.booking(db, jid)
    assert doc["quick_completion_flagged"] is True and doc["issue_flag"] == "completed_too_fast" and doc["issue_resolved"] is False
    assert any(x["user_id"] == s["mgr"]["id"] and "Very quick completion" in x["title"] for x in sent)


# ---------------------------------------------------------------- CAP-03 / CAP-04


async def _stock(db, center_id: str, qty: float = 10.0) -> str:
    res = await db.inventory.insert_one({
        "service_center_id": center_id, "item_name": "Shampoo", "unit": "litre", "quantity_available": qty,
        "reorder_level": 1, "is_deleted": False,
    })
    return str(res.inserted_id)


async def test_cap03_heading_takes_stock_only_from_the_jobs_center(db):
    s = await hb.staffed_center(db)
    job = await _assigned(db, s)
    other_center, _pin = await h.center(db)
    foreign = await _stock(db, other_center)
    own = await _stock(db, s["center_id"])
    svc = BookingService(db)
    with pytest.raises(BadRequestException, match="isn't in your center's store"):
        await svc.start_heading(str(job["_id"]), HeadingRequest(**GPS, equipment_used=[
            {"inventory_item_id": foreign, "item_name": "Shampoo", "quantity": 2}]), s["cap"]["id"])
    assert (await db.inventory.find_one({"_id": ObjectId(foreign)}))["quantity_available"] == 10
    await svc.start_heading(str(job["_id"]), HeadingRequest(**GPS, equipment_used=[
        {"inventory_item_id": own, "item_name": "Shampoo", "quantity": 2}]), s["cap"]["id"])
    assert (await db.inventory.find_one({"_id": ObjectId(own)}))["quantity_available"] == 8
    assert (await db.inventory.find_one({"_id": ObjectId(foreign)}))["quantity_available"] == 10
    async with h.client() as c:
        job2 = await _assigned(db, s, 1)
        r = await c.post(f"/api/v1/bookings/{job2['_id']}/heading", json={**GPS, "equipment_used": [
            {"inventory_item_id": own, "item_name": "Shampoo", "quantity": -5}]}, headers=s["cap"]["h"])
    assert r.status_code == 422
    assert (await db.inventory.find_one({"_id": ObjectId(own)}))["quantity_available"] == 8


async def test_cap04_gps_out_of_range_is_refused(db):
    s = await hb.staffed_center(db)
    job = await _assigned(db, s)
    jid = str(job["_id"])
    async with h.client() as c:
        for body in ({"latitude": 91, "longitude": 75.8}, {"latitude": 22.7, "longitude": -181}):
            assert (await c.post(f"/api/v1/bookings/{jid}/heading", json=body, headers=s["cap"]["h"])).status_code == 422
            assert (await c.post(f"/api/v1/bookings/{jid}/verify-vehicle", json={"service_code": "1234", **body}, headers=s["cap"]["h"])).status_code == 422
            photo = {"image_url": own_upload_url("photos/x.jpg"), **body}
            assert (await c.post(f"/api/v1/bookings/{jid}/before-photo", json=photo, headers=s["cap"]["h"])).status_code == 422
            assert (await c.post(f"/api/v1/bookings/{jid}/after-photo", json=photo, headers=s["cap"]["h"])).status_code == 422
    assert (await hb.booking(db, jid))["status"] == "assigned"


# ---------------------------------------------------------------- NTF-03


async def test_ntf03_customer_hears_captain_arrived_once_per_visit(db, sent):
    s = await hb.staffed_center(db)
    async with h.client() as c:
        gid, ids = await h.group(c, db, s["cu"], s["when"], s["keys"][0], cars=2)
    for i in ids:
        await hb.on_the_way(db, i, s["cap"]["id"])
    code = (await hb.booking(db, ids[0]))["service_code"]
    await BookingService(db).verify_vehicle(ids[1], VerifyVehicleRequest(service_code=code, **GPS), s["cap"]["id"])
    arrived = [x for x in sent if x.get("wa_event") == "captain_arrived"]
    assert len(arrived) == 1 and arrived[0]["user_id"] == s["cu"]["id"]
    params = arrived[0]["wa_params"]
    assert len(params) == 5 and " + " in params[1] and params[4] == "2 vehicles"
    for i in ids:
        assert (await hb.booking(db, i))["vehicle_verified"] is True
