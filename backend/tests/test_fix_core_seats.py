"""Core fix round 1 — explicit seat ownership (audit 2026-10-07: BOOK-02,
BOOK-03, BOOK-04, BOOK-06, STATE-02) and the counter reconciliation job.

Invariant checked everywhere: a slot's booked_count equals the number of
bookings that RECORD holding that seat (holds_seat + seat_key), and never
exceeds capacity."""
import asyncio
import random
from datetime import datetime, timedelta, timezone

import pytest
from bson import ObjectId

from app.schemas.booking_schema import BookingCancelRequest
from app.services.booking_service import BookingService, backfill_seat_ownership
from app.utils.timezone import now_ist
from tests import test_fix_core_helpers as h

pytestmark = pytest.mark.asyncio


async def _rig(db, n: int, cap: int, offset: int = 2, slot_index: int = 0):
    cid, pin = await h.center(db)
    when, keys = await h.slot_keys(db, cid, offset)
    slot = keys[slot_index]
    await h.set_cap(db, cid, when, slot, cap)
    custs = [await h.customer(db, pin) for _ in range(n)]
    return cid, when, slot, keys, custs


def _consistent(st: dict) -> bool:
    return st["booked"] == st["owners"] == st["live"] and st["booked"] <= st["cap"]


# ---------------------------------------------------------------- BOOK-02


async def test_book02_delete_racing_cancel_releases_once(db):
    adm = await h.admin(db)
    bad = []
    for i in range(15):
        cid, when, slot, keys, (keep, x) = await _rig(db, 2, 3)
        async with h.client() as c:
            assert (await h.book(c, db, keep, when, slot)).status_code == 200
            x_id = (await h.book(c, db, x, when, slot)).json()["data"]["id"]

            async def cancel():
                await asyncio.sleep(random.uniform(0, 0.01))
                return await c.post(f"/api/v1/bookings/{x_id}/cancel", json={"reason": "customer cancel"}, headers=x["h"])

            async def delete():
                await asyncio.sleep(random.uniform(0, 0.01))
                return await c.post(f"/api/v1/bookings/{x_id}/delete", headers=adm["h"])

            rs = await asyncio.gather(cancel(), delete())
        assert all(r.status_code < 500 for r in rs), [r.text for r in rs]
        st = await h.state(db, cid, when, slot)
        if not (st["booked"] == st["owners"] == 1):
            bad.append((i, st, [r.status_code for r in rs]))
    assert not bad, bad


# ---------------------------------------------------------------- BOOK-03


async def test_book03_restore_into_full_slot_is_refused(db):
    cid, when, slot, keys, (a, b, c3) = await _rig(db, 3, 1)
    adm = await h.admin(db)
    async with h.client() as c:
        a_id = (await h.book(c, db, a, when, slot)).json()["data"]["id"]
        assert (await c.post(f"/api/v1/bookings/{a_id}/delete", headers=adm["h"])).status_code == 200
        assert (await h.book(c, db, b, when, slot)).status_code == 200
        r = await c.post(f"/api/v1/bookings/{a_id}/restore", headers=adm["h"])
        assert r.status_code == 400, r.text
        assert "full" in r.json()["message"].lower()
        doc = await db.bookings.find_one({"_id": ObjectId(a_id)})
        assert doc["is_deleted"] is True and doc.get("holds_seat") is False
        # The later cancel/book cycle can no longer oversell.
        rc = await h.book(c, db, c3, when, slot)
        assert rc.status_code == 400
    st = await h.state(db, cid, when, slot)
    assert _consistent(st) and st["booked"] == 1, st


async def test_book03_restore_with_free_seat_takes_it(db):
    cid, when, slot, keys, (a,) = await _rig(db, 1, 2)
    adm = await h.admin(db)
    async with h.client() as c:
        a_id = (await h.book(c, db, a, when, slot)).json()["data"]["id"]
        assert (await c.post(f"/api/v1/bookings/{a_id}/delete", headers=adm["h"])).status_code == 200
        assert (await h.state(db, cid, when, slot))["booked"] == 0
        r = await c.post(f"/api/v1/bookings/{a_id}/restore", headers=adm["h"])
        assert r.status_code == 200, r.text
    st = await h.state(db, cid, when, slot)
    assert _consistent(st) and st["booked"] == 1, st


async def test_book03_restore_racing_new_booking_for_last_seat(db):
    adm = await h.admin(db)
    tally = []
    for i in range(8):
        cid, when, slot, keys, (a, b) = await _rig(db, 2, 1)
        async with h.client() as c:
            a_id = (await h.book(c, db, a, when, slot)).json()["data"]["id"]
            assert (await c.post(f"/api/v1/bookings/{a_id}/delete", headers=adm["h"])).status_code == 200

            async def restore():
                await asyncio.sleep(random.uniform(0, 0.01))
                return await c.post(f"/api/v1/bookings/{a_id}/restore", headers=adm["h"])

            async def newcomer():
                await asyncio.sleep(random.uniform(0, 0.01))
                return await h.book(c, db, b, when, slot)

            rs = await asyncio.gather(restore(), newcomer())
        codes = sorted(r.status_code for r in rs)
        tally.append(codes)
        assert codes == [200, 400], [r.text for r in rs]
        st = await h.state(db, cid, when, slot)
        assert _consistent(st) and st["booked"] == 1, st
    print("\n[restore vs book]", tally)


# ---------------------------------------------------------------- BOOK-04


async def test_book04_visit_reschedule_racing_single_car_cancel(db):
    bad = []
    for i in range(8):
        cid, pin = await h.center(db)
        when, keys = await h.slot_keys(db, cid, 2)
        a, b = keys[0], keys[1]
        await h.set_cap(db, cid, when, a, 5)
        await h.set_cap(db, cid, when, b, 5)
        cu = await h.customer(db, pin)
        other = await h.customer(db, pin)
        mgr = await h.manager(db, cid)
        async with h.client() as c:
            assert (await h.book(c, db, other, when, a)).status_code == 200
            gid, ids = await h.group(c, db, cu, when, a, cars=2)
            victim = ids[i % 2]

            async def move():
                await asyncio.sleep(random.uniform(0, 0.01))
                return await c.post(f"/api/v1/bookings/{ids[0]}/reschedule", json={"scheduled_date": when, "scheduled_slot": b}, headers=mgr["h"])

            async def cancel():
                await asyncio.sleep(random.uniform(0, 0.01))
                return await c.post(f"/api/v1/bookings/{victim}/cancel", json={"reason": "drop a car"}, headers=cu["h"])

            rs = await asyncio.gather(move(), cancel())
        assert all(r.status_code < 500 for r in rs), [r.text for r in rs]
        sa, sb = await h.state(db, cid, when, a), await h.state(db, cid, when, b)
        cars = [await db.bookings.find_one({"_id": ObjectId(x)}) for x in ids]
        live_slots = {x["scheduled_slot"] for x in cars if x["status"] != "cancelled"}
        if not (_consistent(sa) and _consistent(sb)) or len(live_slots) > 1:
            bad.append((i, sa, sb, [(x["status"], x["scheduled_slot"]) for x in cars], [r.status_code for r in rs]))
    assert not bad, bad


async def test_book04_visit_reschedule_moves_one_seat(db):
    cid, pin = await h.center(db)
    when, keys = await h.slot_keys(db, cid, 2)
    await h.set_cap(db, cid, when, keys[0], 5)
    await h.set_cap(db, cid, when, keys[1], 5)
    cu = await h.customer(db, pin)
    async with h.client() as c:
        gid, ids = await h.group(c, db, cu, when, keys[0], cars=3)
        r = await c.post(f"/api/v1/bookings/{ids[1]}/reschedule", json={"scheduled_date": when, "scheduled_slot": keys[1]}, headers=cu["h"])
        assert r.status_code == 200, r.text
    sa, sb = await h.state(db, cid, when, keys[0]), await h.state(db, cid, when, keys[1])
    assert (sa["booked"], sa["owners"]) == (0, 0) and (sb["booked"], sb["owners"]) == (1, 1), (sa, sb)
    cars = [await db.bookings.find_one({"_id": ObjectId(x)}) for x in ids]
    assert {x["scheduled_slot"] for x in cars} == {keys[1]} and {x["status"] for x in cars} == {"rescheduled"}


# ---------------------------------------------------------------- BOOK-06


@pytest.fixture
def on_the_bookings_day(monkeypatch):
    """MGR-06: a booking is marked done on (or after) its own day, never
    before — these tests book a day or two ahead, so the manager's
    mark-done happens "on the day"."""
    monkeypatch.setattr("app.services.booking_service._ist_today", lambda: "2999-12-31")


async def test_book06_mark_done_then_delete_frees_the_seat(db, on_the_bookings_day):
    cid, when, slot, keys, (cu,) = await _rig(db, 1, 3)
    mgr = await h.manager(db, cid)
    adm = await h.admin(db)
    async with h.client() as c:
        bid = (await h.book(c, db, cu, when, slot)).json()["data"]["id"]
        assert (await c.post(f"/api/v1/bookings/{bid}/mark-done", json={}, headers=mgr["h"])).status_code == 200
        assert (await h.state(db, cid, when, slot))["booked"] == 1
        assert (await c.post(f"/api/v1/bookings/{bid}/delete", headers=adm["h"])).status_code == 200
    st = await h.state(db, cid, when, slot)
    assert st["booked"] == st["owners"] == 0, st


async def test_logged_job_never_holds_a_seat(db):
    from app.schemas.booking_schema import ManagerLogBookingRequest, QuickBookingLine
    from tests.factories import get_hatchback_type_id, get_star_wash_service_id, make_manager

    cid, pin = await h.center(db, working_hours_start="00:00", working_hours_end="23:59")
    manager_id = await make_manager(db, cid)
    at = now_ist() - timedelta(hours=1)
    payload = ManagerLogBookingRequest(
        customer_name="Logged Person", customer_phone=f"9{random.randint(100000000, 999999999)}",
        lines=[QuickBookingLine(vehicle_type=await get_hatchback_type_id(db), quantity=2, service_ids=[await get_star_wash_service_id(db)])],
        scheduled_date=at.strftime("%Y-%m-%d"), service_time=at.strftime("%H:%M"), address_line="12 Logged Lane",
        send_whatsapp=False,
    )
    result = await BookingService(db).create_manager_logged_visit(payload, manager_id=manager_id, manager_center_id=cid)
    for b in result["bookings"]:
        doc = await db.bookings.find_one({"_id": ObjectId(b["id"])})
        assert doc["holds_seat"] is False


# ---------------------------------------------------------------- double clicks


async def test_cancel_double_click_releases_once(db):
    cid, when, slot, keys, (keep, x) = await _rig(db, 2, 3)
    async with h.client() as c:
        assert (await h.book(c, db, keep, when, slot)).status_code == 200
        x_id = (await h.book(c, db, x, when, slot)).json()["data"]["id"]
        rs = await asyncio.gather(*(c.post(f"/api/v1/bookings/{x_id}/cancel", json={"reason": "double click"}, headers=x["h"]) for _ in range(4)))
    assert sorted(r.status_code for r in rs).count(200) == 1
    st = await h.state(db, cid, when, slot)
    assert st["booked"] == st["owners"] == st["live"] == 1, st


async def test_visit_seat_follows_the_cars(db):
    cid, when, slot, keys, (cu,) = await _rig(db, 1, 3)
    async with h.client() as c:
        gid, ids = await h.group(c, db, cu, when, slot, cars=3)
        assert (await h.state(db, cid, when, slot))["booked"] == 1
        r = await c.post(f"/api/v1/bookings/{ids[0]}/cancel", json={"reason": "first car"}, headers=cu["h"])
        assert r.status_code == 200
        st = await h.state(db, cid, when, slot)
        assert st["booked"] == st["owners"] == 1, st  # the seat moved to a remaining car
        r = await c.post(f"/api/v1/bookings/group/{gid}/cancel", json={"reason": "rest"}, headers=cu["h"])
        assert r.status_code == 200
    st = await h.state(db, cid, when, slot)
    assert st["booked"] == st["owners"] == 0, st


async def test_two_cars_of_a_visit_cancelled_at_once_release_once(db):
    for _ in range(6):
        cid, when, slot, keys, (cu,) = await _rig(db, 1, 3)
        async with h.client() as c:
            gid, ids = await h.group(c, db, cu, when, slot, cars=2)
            rs = await asyncio.gather(*(c.post(f"/api/v1/bookings/{x}/cancel", json={"reason": "both"}, headers=cu["h"]) for x in ids))
        assert all(r.status_code == 200 for r in rs), [r.text for r in rs]
        st = await h.state(db, cid, when, slot)
        assert st["booked"] == st["owners"] == 0, st


# ---------------------------------------------------------------- backfill (legacy rows)


async def test_backfill_derives_ownership_and_repairs_shifted_dates(db):
    cid, when, slot, keys, (single, grp, gone) = await _rig(db, 3, 5)
    async with h.client() as c:
        s_id = (await h.book(c, db, single, when, slot)).json()["data"]["id"]
        gid, g_ids = await h.group(c, db, grp, when, slot, cars=2)
        x_id = (await h.book(c, db, gone, when, slot)).json()["data"]["id"]
        assert (await c.post(f"/api/v1/bookings/{x_id}/cancel", json={"reason": "gone"}, headers=gone["h"])).status_code == 200
    ids = [s_id, *g_ids, x_id]
    # Make them look like rows written before ownership existed — and give
    # the single one the BOOK-01 shape (an aware date stored as UTC: the
    # previous calendar day at 18:30).
    await db.bookings.update_many({"_id": {"$in": [ObjectId(i) for i in ids]}}, {"$unset": {"holds_seat": "", "seat_key": ""}})
    shifted = datetime.strptime(when, "%Y-%m-%d") - timedelta(hours=5, minutes=30)
    await db.bookings.update_one({"_id": ObjectId(s_id)}, {"$set": {"scheduled_date": shifted}})
    await db.migrations.delete_many({"_id": {"$regex": "^seat_ownership"}})
    out = await backfill_seat_ownership(db)
    assert out["bookings"] >= 4
    docs = {str(d["_id"]): d for d in await db.bookings.find({"_id": {"$in": [ObjectId(i) for i in ids]}}).to_list(None)}
    assert docs[s_id]["holds_seat"] is True and docs[s_id]["seat_key"]["date"] == when
    assert docs[s_id]["scheduled_date"] == datetime.strptime(when, "%Y-%m-%d")
    assert [docs[i]["holds_seat"] for i in g_ids].count(True) == 1
    assert docs[x_id]["holds_seat"] is False
    st = await h.state(db, cid, when, slot)
    assert st["booked"] == st["owners"] == 2, st


# ---------------------------------------------------------------- reconciliation (item 8)


async def test_reconcile_reports_and_repairs_only_drifted_rows(db):
    cid, when, slot, keys, (a, b) = await _rig(db, 2, 4)
    other = keys[1]
    await h.set_cap(db, cid, when, other, 4)
    async with h.client() as c:
        assert (await h.book(c, db, a, when, slot)).status_code == 200
        assert (await h.book(c, db, b, when, other)).status_code == 200
    # Drift on `slot` only: a leaked seat (counter 3, one real owner).
    await db.slot_capacity.update_one({"service_center_id": cid, "date": when, "slot_key": slot}, {"$set": {"booked_count": 3}})
    untouched = await h.seat(db, cid, when, other)
    svc = BookingService(db)
    report = await svc.reconcile_slot_counters()
    mine = [d for d in report["drift"] if d["service_center_id"] == cid]
    assert [(d["kind"], d["slot_key"], d["stored"], d["expected"]) for d in mine] == [("slot", slot, 3, 1)]
    assert (await h.seat(db, cid, when, slot))["booked_count"] == 3  # report mode never writes
    fixed = await svc.reconcile_slot_counters(repair=True)
    assert any(d["service_center_id"] == cid and d["repaired"] for d in fixed["drift"])
    assert (await h.seat(db, cid, when, slot))["booked_count"] == 1
    after_other = await h.seat(db, cid, when, other)
    assert after_other["booked_count"] == untouched["booked_count"] == 1
    assert after_other["updated_at"] == untouched["updated_at"]  # a correct row is never written
    assert await db.audit_logs.count_documents({"action": "RECONCILE_SLOT_COUNTERS"}) >= 1
    again = await svc.reconcile_slot_counters()
    assert not [d for d in again["drift"] if d["service_center_id"] == cid]


async def test_reconcile_endpoint_is_admin_only(db):
    cid, pin = await h.center(db)
    mgr = await h.manager(db, cid)
    adm = await h.admin(db)
    async with h.client() as c:
        r = await c.post("/api/v1/bookings/admin/reconcile-slot-counters", headers=mgr["h"])
        assert r.status_code == 403
        r = await c.post("/api/v1/bookings/admin/reconcile-slot-counters", headers=adm["h"])
        assert r.status_code == 200, r.text
        assert r.json()["data"]["repair"] is False


async def test_same_day_move_under_a_full_daily_cap_keeps_its_place_in_the_day(db):
    cid, pin = await h.center(db, max_bookings_per_day=1)
    when, keys = await h.slot_keys(db, cid, 2)
    cu = await h.customer(db, pin)
    async with h.client() as c:
        bid = (await h.book(c, db, cu, when, keys[0])).json()["data"]["id"]
        r = await c.post(f"/api/v1/bookings/{bid}/reschedule", json={"scheduled_date": when, "scheduled_slot": keys[1]}, headers=cu["h"])
        assert r.status_code == 200, r.text
    day = await db.daily_capacity.find_one({"service_center_id": cid, "date": when})
    assert day["booked_count"] == 1
    assert (await h.state(db, cid, when, keys[0]))["booked"] == 0
    st = await h.state(db, cid, when, keys[1])
    assert st["booked"] == st["owners"] == 1
    report = await BookingService(db).reconcile_slot_counters()
    assert not [d for d in report["drift"] if d["service_center_id"] == cid]
