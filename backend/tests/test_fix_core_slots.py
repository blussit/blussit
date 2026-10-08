"""Core fix round 1 — slot configuration and availability (audit 2026-10-07:
SLOT-01, SLOT-02, SLOT-04/MGR-05, SLOT-05, STATE-01, VAL-3, E2E-03)."""
from datetime import datetime, timedelta, timezone

import pytest
from bson import ObjectId
from httpx import ASGITransport, AsyncClient

from app.schemas.booking_schema import BookingRescheduleRequest
from app.services.booking_service import BookingService
from app.services.capacity_policy_service import CapacityPolicyService
from app.utils.timezone import now_ist
from tests import test_fix_core_helpers as h

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------- SLOT-01


async def test_slot01_center_slot_length_change_refused_while_bookings_exist(db):
    cid, pin = await h.center(db)
    when, keys = await h.slot_keys(db, cid, 2)
    cu = await h.customer(db, pin)
    adm = await h.admin(db)
    async with h.client() as c:
        assert (await h.book(c, db, cu, when, keys[0])).status_code == 200
        r = await c.put(f"/api/v1/service-centers/{cid}", json={"slot_duration_minutes": 120}, headers=adm["h"])
        assert r.status_code == 400, r.text
        assert "1 upcoming booking" in r.json()["message"]
        r = await c.put(f"/api/v1/service-centers/{cid}", json={"working_hours_start": "07:00"}, headers=adm["h"])
        assert r.status_code == 400, r.text
        # Unaffected edits still go through: same slots, other fields.
        r = await c.put(f"/api/v1/service-centers/{cid}", json={"name": "Renamed Center", "slot_duration_minutes": 180}, headers=adm["h"])
        assert r.status_code == 200, r.text
    center = await db.service_centers.find_one({"_id": ObjectId(cid)})
    assert center["slot_duration_minutes"] == 180 and center["working_hours_start"] == "09:00"


async def test_slot01_center_change_allowed_when_nothing_is_booked_and_rekeys_policy(db):
    cid, pin = await h.center(db)
    adm = await h.admin(db)
    when, keys = await h.slot_keys(db, cid, 2)
    today = now_ist().strftime("%Y-%m-%d")
    await CapacityPolicyService(db).schedule_change(cid, today, 2 * len(keys), {k: 2 for k in keys}, adm["id"], "admin", None)
    async with h.client() as c:
        r = await c.put(f"/api/v1/service-centers/{cid}", json={"slot_duration_minutes": 120}, headers=adm["h"])
        assert r.status_code == 200, r.text
    _, new_keys = await h.slot_keys(db, cid, 2)
    assert new_keys != keys
    policy = await CapacityPolicyService(db).get_effective_policy(cid, when)
    assert set(policy["slot_distribution"]) == set(new_keys)
    assert sum(policy["slot_distribution"].values()) == 2 * len(keys)


async def test_slot01_global_slot_length_change_refused_while_bookings_exist(db):
    cid, pin = await h.center(db)
    await db.service_centers.update_one({"_id": ObjectId(cid)}, {"$unset": {"slot_duration_minutes": ""}})
    when, keys = await h.slot_keys(db, cid, 2)
    cu = await h.customer(db, pin)
    adm = await h.admin(db)
    async with h.client() as c:
        assert (await h.book(c, db, cu, when, keys[0])).status_code == 200
        current = (await c.get("/api/v1/booking-policy")).json()["data"]["slot_duration_minutes"]
        r = await c.put("/api/v1/booking-policy", json={"slot_duration_minutes": current + 60}, headers=adm["h"])
        assert r.status_code == 400, r.text
        assert "upcoming booking" in r.json()["message"]
        # Re-saving the same length is not a change.
        r = await c.put("/api/v1/booking-policy", json={"slot_duration_minutes": current}, headers=adm["h"])
        assert r.status_code == 200, r.text


async def test_slot01_unknown_slot_key_under_a_policy_never_gets_999(db):
    """A slot key the day's policy doesn't name (config drifted under it)
    takes the policy's own daily figure spread over today's slots."""
    cid, pin = await h.center(db)
    await db.service_centers.update_one({"_id": ObjectId(cid)}, {"$unset": {"default_slot_capacity": ""}})
    adm = await h.admin(db)
    when, keys = await h.slot_keys(db, cid, 2)
    today = now_ist().strftime("%Y-%m-%d")
    await CapacityPolicyService(db).schedule_change(cid, today, 2 * len(keys), {k: 2 for k in keys}, adm["id"], "admin", None)
    # Simulate a config change that bypassed the guard (legacy data).
    await db.service_centers.update_one({"_id": ObjectId(cid)}, {"$set": {"slot_duration_minutes": 120}})
    svc = BookingService(db)
    center = await db.service_centers.find_one({"_id": ObjectId(cid)})
    _, new_keys = await h.slot_keys(db, cid, 2)
    caps = [await svc._default_slot_capacity(center, when, k) for k in new_keys]
    assert all(cap is not None and cap < 999 for cap in caps), caps
    assert sum(caps) == 2 * len(keys)


async def test_slot01_unconfigured_center_fails_closed(db):
    cid, pin = await h.center(db)
    await db.service_centers.update_one({"_id": ObjectId(cid)}, {"$unset": {"default_slot_capacity": ""}})
    cu = await h.customer(db, pin)
    when, keys = await h.slot_keys(db, cid, 2)
    slots = await BookingService(db).available_slots(cid, when)
    assert {s["status"] for s in slots} == {"full"}
    async with h.client() as c:
        r = await h.book(c, db, cu, when, keys[0])
    assert r.status_code == 400
    assert "capacity" in r.json()["message"].lower()


# ---------------------------------------------------------------- SLOT-05


async def test_slot05_availability_reflects_the_daily_cap(db):
    cid, pin = await h.center(db, max_bookings_per_day=1)
    when, keys = await h.slot_keys(db, cid, 2)
    cu = await h.customer(db, pin)
    before = await BookingService(db).available_slots(cid, when)
    assert any(s["status"] != "full" for s in before)
    async with h.client() as c:
        assert (await h.book(c, db, cu, when, keys[0])).status_code == 200
    after = await BookingService(db).available_slots(cid, when)
    assert {s["status"] for s in after} == {"full"}, after


# ---------------------------------------------------------------- SLOT-04 / MGR-05


async def test_slot04_override_validates_date_and_slot(db):
    cid, pin = await h.center(db)
    mgr = await h.manager(db, cid)
    when, keys = await h.slot_keys(db, cid, 2)
    yesterday = (now_ist().date() - timedelta(days=1)).isoformat()
    async with h.client() as c:
        for payload in (
            {"date": "not-a-date", "slot_key": keys[0], "capacity": 5},
            {"date": "9999-01-01", "slot_key": keys[0], "capacity": 5},
            {"date": yesterday, "slot_key": keys[0], "capacity": 5},
            {"date": when, "slot_key": "zz", "capacity": 5},
            {"date": when, "slot_key": "07:00-10:00", "capacity": 5},
        ):
            r = await c.put(f"/api/v1/service-centers/{cid}/slot-capacity", json=payload, headers=mgr["h"])
            assert r.status_code in (400, 422), (payload, r.text)
        r = await c.put(f"/api/v1/service-centers/{cid}/slot-capacity", json={"date": when, "slot_key": keys[0], "capacity": 5}, headers=mgr["h"])
        assert r.status_code == 200, r.text
    assert await db.slot_capacity.count_documents({"service_center_id": cid, "date": {"$ne": when}}) == 0
    assert await db.slot_capacity.count_documents({"service_center_id": cid, "slot_key": {"$nin": keys}}) == 0


async def test_mgr05_capacity_get_writes_nothing(db):
    cid, pin = await h.center(db)
    mgr = await h.manager(db, cid)
    when, keys = await h.slot_keys(db, cid, 3)
    async with h.client() as c:
        r = await c.get(f"/api/v1/service-centers/{cid}/slot-capacity", params={"date": "junk"}, headers=mgr["h"])
        assert r.status_code in (400, 422)
        r = await c.get(f"/api/v1/service-centers/{cid}/slot-capacity", params={"date": when}, headers=mgr["h"])
        assert r.status_code == 200, r.text
        assert [s["key"] for s in r.json()["data"]["slots"]] == keys
        assert r.json()["data"]["slots"][0]["capacity"] == 20
    assert await db.slot_capacity.count_documents({"service_center_id": cid}) == 0
    assert await db.daily_capacity.count_documents({"service_center_id": cid}) == 0


# ---------------------------------------------------------------- SLOT-02


async def test_slot02_hold_cap_counts_an_ipv6_slash64_as_one_client(db):
    from app.main import app

    cid, pin = await h.center(db)
    when, keys = await h.slot_keys(db, cid, 2)
    await h.set_cap(db, cid, when, keys[0], 40)
    codes = []
    for i in range(BookingService.MAX_HOLDS_PER_IP + 1):
        transport = ASGITransport(app=app, client=(f"2001:db8:1:2::{i + 1:x}", 4000 + i))
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            r = await c.post("/api/v1/bookings/hold", json={"holder_key": f"fc-holder-{cid[-6:]}-{i:02d}", "service_center_id": cid, "date": when, "slot_key": keys[0]})
            codes.append(r.status_code)
    assert codes[:-1] == [200] * BookingService.MAX_HOLDS_PER_IP, codes
    assert codes[-1] == 429, codes


# ---------------------------------------------------------------- STATE-01


async def test_state01_self_assign_racing_reschedule_never_anchors_to_old_slot(db, monkeypatch):
    cid, pin = await h.center(db)
    when, keys = await h.slot_keys(db, cid, 2)
    cu = await h.customer(db, pin)
    mgr = await h.manager(db, cid)
    async with h.client() as c:
        bid = (await h.book(c, db, cu, when, keys[0])).json()["data"]["id"]
        r = await c.post(f"/api/v1/bookings/{bid}/reschedule", json={"scheduled_date": when, "scheduled_slot": keys[1]}, headers=mgr["h"])
        assert r.status_code == 200
    svc = BookingService(db)
    real = svc._visit_cars

    async def cars_then_moved(b, session=None):
        cars = await real(b, session=session)
        if session is None and not getattr(svc, "_fc_moved", False):
            svc._fc_moved = True
            await BookingService(db).reschedule_booking(
                bid, BookingRescheduleRequest(scheduled_date=when, scheduled_slot=keys[2]), mgr["id"], "manager", cid
            )
        return cars

    monkeypatch.setattr(svc, "_visit_cars", cars_then_moved)
    try:
        await svc.self_assign(bid, mgr["id"], "manager", cid)
    except Exception as exc:  # noqa: BLE001 — a clean refusal is a correct outcome
        assert "self-assigned" in str(exc) or "just changed" in str(exc), exc
    doc = await db.bookings.find_one({"_id": ObjectId(bid)})
    assert doc["scheduled_slot"] == keys[2]
    if doc.get("estimated_start_at"):
        start = keys[2].split("-")[0]
        assert doc["estimated_start_at"].replace(tzinfo=timezone.utc).astimezone(now_ist().tzinfo).strftime("%H:%M") == start


# ---------------------------------------------------------------- VAL-3 / E2E-03


async def _assigned(db):
    cid, pin = await h.center(db)
    when, keys = await h.slot_keys(db, cid, 2)
    cu = await h.customer(db, pin)
    mgr = await h.manager(db, cid)
    cap = await h.captain(db, cid)
    async with h.client() as c:
        bid = (await h.book(c, db, cu, when, keys[0])).json()["data"]["id"]
        r = await c.post(f"/api/v1/bookings/{bid}/assign-captain", json={"captain_id": cap["id"]}, headers=mgr["h"])
        assert r.status_code == 200, r.text
    await db.bookings.update_one({"_id": ObjectId(bid)}, {"$set": {"estimated_start_at": datetime.now(timezone.utc) + timedelta(minutes=20)}})
    return cid, bid, cap


async def test_val3_heading_with_bad_inventory_id_is_refused_before_any_write(db):
    cid, bid, cap = await _assigned(db)
    async with h.client() as c:
        for item in ({"inventory_item_id": "not-an-id", "item_name": "Shampoo", "quantity": 1},
                     {"inventory_item_id": str(ObjectId()), "item_name": "Ghost", "quantity": 1},
                     {"inventory_item_id": str(ObjectId()), "item_name": "Negative", "quantity": -3}):
            r = await c.post(f"/api/v1/bookings/{bid}/heading", json={"latitude": 22.7, "longitude": 75.8, "equipment_used": [item]}, headers=cap["h"])
            assert r.status_code in (400, 422), r.text
    doc = await db.bookings.find_one({"_id": ObjectId(bid)})
    assert doc["status"] == "assigned" and not doc.get("heading_at")


async def test_e2e03_history_logs_on_the_way_once(db):
    cid, bid, cap = await _assigned(db)
    async with h.client() as c:
        r = await c.post(f"/api/v1/bookings/{bid}/heading", json={"latitude": 22.7, "longitude": 75.8}, headers=cap["h"])
        assert r.status_code == 200, r.text
        code = (await db.bookings.find_one({"_id": ObjectId(bid)}))["service_code"]
        r = await c.post(f"/api/v1/bookings/{bid}/verify-vehicle", json={"service_code": code, "latitude": 22.7, "longitude": 75.8}, headers=cap["h"])
        assert r.status_code == 200, r.text
    rows = await db.booking_status_history.find({"booking_id": bid}).to_list(None)
    assert [r["status"] for r in rows].count("captain_on_the_way") == 1
    assert any(r["status"] == "captain_arrived" for r in rows)
