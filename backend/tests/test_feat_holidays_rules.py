"""Holiday system (2026-10-19) — admin rules, IST handling, labels, the
resume date across adjacent holidays, the public/customer endpoints, admin
access + audit, and the affected-bookings report."""
from datetime import datetime, timedelta, timezone

import pytest
from bson import ObjectId

from app.services.holiday_service import (
    HolidayService,
    dates_label,
    invalidate_holiday_cache,
    resume_at,
    resume_label,
)
from app.utils.timezone import IST
from tests import test_feat_booking_helpers as fb
from tests import test_feat_holidays_helpers as hh
from tests import test_fix_core_helpers as h
from tests.test_feat_holidays_helpers import clean  # noqa: F401 — fixture

pytestmark = pytest.mark.asyncio


def _ist(iso: str, hh_: int = 0, mm: int = 0) -> datetime:
    y, m, d = [int(x) for x in iso.split("-")]
    return datetime(y, m, d, hh_, mm, tzinfo=IST)


async def test_admin_input_rules_are_enforced(db, clean):
    cid, *_ = await hh.center(db, clean)
    off, _pin = await h.center(db, latitude=24.9, longitude=77.9)
    clean.append(off)
    await db.service_centers.update_one({"_id": ObjectId(off)}, {"$set": {"is_active": False}})
    admin = await hh.admin_headers(db)
    cases = [
        (hh.holiday_body(message="   "), 422),
        (hh.holiday_body(message="x" * 301), 422),
        (hh.holiday_body(start_date=hh.day(3), end_date=hh.day(2)), 400),
        (hh.holiday_body(start_date=hh.day(2), start_time="15:00", end_date=hh.day(2), end_time="14:00"), 400),
        (hh.holiday_body(start_date=hh.day(1), end_date=hh.day(61)), 400),
        (hh.holiday_body(start_date=hh.day(370), end_date=hh.day(371)), 400),
        (hh.holiday_body(start_date=hh.day(-3), end_date=hh.day(-1)), 400),
        (hh.holiday_body(scope="centers", service_center_ids=[]), 400),
        (hh.holiday_body(scope="centers", service_center_ids=[str(ObjectId())]), 400),
        (hh.holiday_body(scope="centers", service_center_ids=["not-an-id"]), 400),
        (hh.holiday_body(scope="centers", service_center_ids=[cid, off]), 400),
        (hh.holiday_body(start_time="25:00"), 422),
        (hh.holiday_body(notice_days_before=31), 422),
    ]
    async with h.client() as c:
        for body, code in cases:
            r = await c.post("/api/v1/admin/holidays", json=body, headers=admin)
            assert r.status_code == code, (body, r.text)
        # The edges that ARE allowed: 60 days long, a single day, today.
        for body in (
            hh.holiday_body(start_date=hh.day(1), end_date=hh.day(60)),
            hh.holiday_body(start_date=hh.day(0), end_date=hh.day(0), scope="centers", service_center_ids=[cid]),
        ):
            r = await c.post("/api/v1/admin/holidays", json=body, headers=admin)
            assert r.status_code == 200, r.text
    assert await db.holidays.count_documents({}) == 2


async def test_ist_dates_become_utc_instants_and_labels_read_in_ist(db, clean):
    admin = await hh.admin_headers(db)
    d1, d2, d3 = hh.day(1), hh.day(2), hh.day(3)
    async with h.client() as c:
        full = await hh.make(c, admin, start_date=d1, end_date=d2)
        one = await hh.make(c, admin, start_date=hh.day(5), end_date=hh.day(5), message="One day")
        part = await hh.make(c, admin, start_date=hh.day(7), start_time="13:00", end_date=hh.day(7), end_time="16:00")
        span = await hh.make(c, admin, start_date=hh.day(9), start_time="14:00", end_date=hh.day(10))
    doc = await db.holidays.find_one({"_id": ObjectId(full["holiday"]["id"])})
    # 00:00 IST on day 1 is 18:30 UTC the evening before.
    assert doc["start_at"] == _ist(d1).astimezone(timezone.utc).replace(tzinfo=None)
    end_ist = doc["end_at"].replace(tzinfo=timezone.utc).astimezone(IST)
    assert (end_ist.date().isoformat(), end_ist.hour, end_ist.minute, end_ist.second) == (d2, 23, 59, 59)
    assert doc["notice_from"] == (_ist(d1) - timedelta(days=2)).astimezone(timezone.utc).replace(tzinfo=None)
    assert doc["start_time"] is None and doc["end_time"] is None and doc["active"] is True

    f = full["holiday"]
    assert f["dates_label"] == f"{hh.short(d1)} – {hh.short(d2)}"
    assert f["resume_label"] == hh.long(d3) and f["resume_date"] == d3
    assert f["popup"]["resume_label"] == hh.long(d3) and f["status"] == "upcoming"
    assert one["holiday"]["dates_label"] == hh.short(hh.day(5)) and one["holiday"]["resume_label"] == hh.long(hh.day(6))
    assert part["holiday"]["dates_label"] == f"{hh.short(hh.day(7))}, 1:00 PM – 4:00 PM"
    assert part["holiday"]["resume_label"] == f"{hh.long(hh.day(7))}, 4:00 PM" and part["holiday"]["resume_date"] == hh.day(7)
    assert span["holiday"]["dates_label"] == f"{hh.short(hh.day(9))}, 2:00 PM – {hh.short(hh.day(10))}"
    assert span["holiday"]["start_time"] == "14:00" and span["holiday"]["end_time"] is None


async def test_resume_skips_past_touching_and_overlapping_holidays(db, clean):
    cid, *_ = await hh.center(db, clean)
    admin = await hh.admin_headers(db)
    async with h.client() as c:
        a = await hh.make(c, admin, start_date=hh.day(1), end_date=hh.day(2))
        # Touches A (starts the moment A's break would end) …
        await hh.make(c, admin, start_date=hh.day(3), end_date=hh.day(3), message="B")
        # … and C overlaps B — booking reopens only after C.
        await hh.make(c, admin, start_date=hh.day(3), start_time="10:00", end_date=hh.day(4), end_time="15:00", message="C")
        # A centers-only holiday right after C extends the break for THAT
        # center, but not for the website at large.
        await hh.make(c, admin, start_date=hh.day(4), start_time="15:00", end_date=hh.day(5), scope="centers", service_center_ids=[cid], message="D")
        listed = (await c.get("/api/v1/admin/holidays", params={"status": "upcoming"}, headers=admin)).json()["data"]
        public = (await c.get("/api/v1/public/holidays")).json()["data"]
        at_center = (await c.get("/api/v1/public/holidays", params={"center_id": cid})).json()["data"]
    first = next(x for x in listed if x["id"] == a["holiday"]["id"])
    assert first["resume_label"] == f"{hh.long(hh.day(4))}, 3:00 PM"
    a_public = next(x for x in public if x["id"] == a["holiday"]["id"])
    assert a_public["resume_label"] == f"{hh.long(hh.day(4))}, 3:00 PM"
    a_center = next(x for x in at_center if x["id"] == a["holiday"]["id"])
    assert a_center["resume_label"] == hh.long(hh.day(6)) and a_center["resume_date"] == hh.day(6)

    # The pure helpers agree, and a gap of one minute breaks the chain.
    pool = await HolidayService(db).live_holidays()
    gap = {"_id": "x", "scope": "all", "start_at": _ist(hh.day(20)).astimezone(timezone.utc),
           "end_at": _ist(hh.day(20), 12, 0).astimezone(timezone.utc), "active": True}
    later = {"_id": "y", "scope": "all", "start_at": _ist(hh.day(20), 12, 1).astimezone(timezone.utc),
             "end_at": _ist(hh.day(21)).astimezone(timezone.utc) + timedelta(hours=23, minutes=59, seconds=59), "active": True}
    assert resume_label(resume_at(gap, pool + [gap, later])) == f"{hh.long(hh.day(20))}, 12:00 PM"
    later["start_at"] = gap["end_at"]
    assert resume_label(resume_at(gap, pool + [gap, later])) == hh.long(hh.day(22))
    assert dates_label(later) == f"{hh.short(hh.day(20))}, 12:00 PM – {hh.short(hh.day(21))}"


async def test_public_list_follows_the_notice_window_and_center(db, clean):
    cid, *_ = await hh.center(db, clean)
    other, *_ = await hh.center(db, clean)
    admin = await hh.admin_headers(db)
    async with h.client() as c:
        soon = await hh.make(c, admin, start_date=hh.day(1), end_date=hh.day(2))
        far = await hh.make(c, admin, start_date=hh.day(6), end_date=hh.day(7), message="Far")  # popup from day 4
        early = await hh.make(c, admin, start_date=hh.day(6), end_date=hh.day(6), message="Early notice", notice_days_before=10)
        mine = await hh.make(c, admin, start_date=hh.day(1), end_date=hh.day(1), scope="centers", service_center_ids=[cid], message="Center only")
        r = await c.get("/api/v1/public/holidays")
        assert r.status_code == 200 and r.headers["cache-control"] == "public, max-age=60"
        ids = {x["id"] for x in r.json()["data"]}
        at_cid = {x["id"] for x in (await c.get("/api/v1/public/holidays", params={"center_id": cid})).json()["data"]}
        at_other = {x["id"] for x in (await c.get("/api/v1/public/holidays", params={"center_id": other})).json()["data"]}
        junk = {x["id"] for x in (await c.get("/api/v1/public/holidays", params={"center_id": "nope"})).json()["data"]}
    sid, fid, eid, mid = (x["holiday"]["id"] for x in (soon, far, early, mine))
    assert ids == {sid, eid} and junk == ids
    assert at_cid == {sid, eid, mid} and at_other == {sid, eid}
    item = next(x for x in r.json()["data"] if x["id"] == sid)
    assert set(item) >= {"id", "scope", "message", "title", "starts_at", "ends_at", "is_active_now", "dates_label", "resume_at", "resume_label"}
    assert item["is_active_now"] is False and item["message"].startswith("Diwali")

    # A running holiday reads is_active_now; an ended one is gone.
    now = datetime.now(timezone.utc)
    run = await hh.insert_raw(db, start=now - timedelta(hours=1), end=now + timedelta(hours=3), message="Running")
    gone = await hh.insert_raw(db, start=now - timedelta(hours=5), end=now - timedelta(minutes=1), message="Gone")
    data = await HolidayService(db).public_list()
    assert next(x for x in data if x["id"] == run)["is_active_now"] is True
    assert gone not in {x["id"] for x in data}


async def test_customer_sees_their_centers_holidays(db, clean):
    cid, pin, *_ = await hh.center(db, clean)
    cu = await h.customer(db, pin)
    admin = await hh.admin_headers(db)
    async with h.client() as c:
        mine = await hh.make(c, admin, scope="centers", service_center_ids=[cid], message="Our team is at a festival")
        r = await c.get("/api/v1/holidays/mine", headers=cu["h"])
        assert r.status_code == 200, r.text
        assert [x["id"] for x in r.json()["data"]] == [mine["holiday"]["id"]]
        assert (await c.get("/api/v1/holidays/mine")).status_code == 401
        assert (await c.get("/api/v1/holidays/mine", headers=admin)).status_code == 403


async def test_admin_only_and_every_write_audited(db, clean):
    cid, pin, *_ = await hh.center(db, clean)
    cu = await h.customer(db, pin)
    mgr = await h.manager(db, cid)
    admin = await hh.admin_headers(db)
    async with h.client() as c:
        for headers, code in ((None, 401), (cu["h"], 403), (mgr["h"], 403)):
            kw = {"headers": headers} if headers else {}
            assert (await c.get("/api/v1/admin/holidays", **kw)).status_code == code
            assert (await c.post("/api/v1/admin/holidays", json=hh.holiday_body(), **kw)).status_code == code
            assert (await c.post("/api/v1/admin/holidays/preview", json=hh.holiday_body(), **kw)).status_code == code
        made = await hh.make(c, admin)
        hid = made["holiday"]["id"]
        for headers in (cu["h"], mgr["h"]):
            assert (await c.patch(f"/api/v1/admin/holidays/{hid}", json={"message": "x"}, headers=headers)).status_code == 403
            assert (await c.post(f"/api/v1/admin/holidays/{hid}/end", headers=headers)).status_code == 403
        r = await c.patch(f"/api/v1/admin/holidays/{hid}", json={"message": "Back on the 4th!", "end_date": hh.day(3)}, headers=admin)
        assert r.status_code == 200, r.text
        assert r.json()["data"]["holiday"]["resume_label"] == hh.long(hh.day(4))
        assert r.json()["data"]["holiday"]["message"] == "Back on the 4th!"
        r = await c.post(f"/api/v1/admin/holidays/{hid}/end", headers=admin)
        assert r.status_code == 200 and r.json()["data"]["holiday"]["status"] == "past"
        assert r.json()["data"]["holiday"]["ended"] == "cancelled"
        # Ended: no second end, no edits.
        assert (await c.post(f"/api/v1/admin/holidays/{hid}/end", headers=admin)).status_code == 400
        assert (await c.patch(f"/api/v1/admin/holidays/{hid}", json={"message": "again"}, headers=admin)).status_code == 400
        assert (await c.post(f"/api/v1/admin/holidays/{ObjectId()}/end", headers=admin)).status_code == 404
    actions = [a["action"] for a in await db.audit_logs.find({"module": "holidays", "target_id": hid}).sort("_id", 1).to_list(None)]
    assert actions == ["HOLIDAY_CREATE", "HOLIDAY_UPDATE", "HOLIDAY_END"]
    upd = await db.audit_logs.find_one({"action": "HOLIDAY_UPDATE", "target_id": hid})
    assert "message" in upd["details"]["changes"] and "end_at" in upd["details"]["changes"]


async def test_list_by_status_and_end_now_of_a_running_holiday(db, clean):
    admin = await hh.admin_headers(db)
    now = datetime.now(timezone.utc)
    running = await hh.insert_raw(db, start=now - timedelta(hours=2), end=now + timedelta(days=1), message="Running")
    async with h.client() as c:
        upcoming = (await hh.make(c, admin, start_date=hh.day(3), end_date=hh.day(3)))["holiday"]["id"]

        async def ids(status):
            r = await c.get("/api/v1/admin/holidays", params={"status": status}, headers=admin)
            assert r.status_code == 200, r.text
            assert r.json()["meta"]["total"] == len(r.json()["data"])
            return [x["id"] for x in r.json()["data"]]

        assert await ids("upcoming") == [upcoming]
        assert await ids("active") == [running]
        assert await ids("past") == []
        assert set(await ids("all")) == {running, upcoming}
        assert (await c.get("/api/v1/admin/holidays", params={"status": "bogus"}, headers=admin)).status_code == 422
        # A running holiday can't be pushed to start later.
        r = await c.patch(f"/api/v1/admin/holidays/{running}", json={"start_date": hh.day(1)}, headers=admin)
        assert r.status_code == 400 and "already started" in r.json()["message"]
        r = await c.post(f"/api/v1/admin/holidays/{running}/end", headers=admin)
        assert r.status_code == 200, r.text
        ended = r.json()["data"]["holiday"]
        assert ended["ended"] == "ended_early" and ended["status"] == "past" and ended["original_end_at"]
        assert await ids("active") == [] and await ids("past") == [running]
    doc = await db.holidays.find_one({"_id": ObjectId(running)})
    assert doc["active"] is False and doc["end_at"] <= datetime.utcnow() and doc["cancelled_by"]


async def test_preview_and_create_report_affected_bookings_without_cancelling(db, clean):
    s = await fb.rig(db)
    clean.append(s["center_id"])
    when, keys = await h.slot_keys(db, s["center_id"], offset=2)
    b1 = await fb.book(db, s["cu"], when, keys[0])
    other = await h.customer(db, s["pin"])
    b2 = await fb.book(db, other, when, keys[1])
    b3 = await fb.book(db, other, h.day(3), keys[0])  # outside the holiday
    cancelled = await fb.book(db, s["cu"], when, keys[-1])
    from app.services.booking_service import BookingService

    await BookingService(db).cancel_booking(cancelled["id"], fb.cancel(), s["cu"]["id"], "customer")
    admin = await hh.admin_headers(db)
    body = hh.holiday_body(start_date=when, end_date=when, scope="centers", service_center_ids=[s["center_id"]])
    async with h.client() as c:
        r = await c.post("/api/v1/admin/holidays/preview", json=body, headers=admin)
        assert r.status_code == 200, r.text
        prev = r.json()["data"]
        assert await db.holidays.count_documents({}) == 0  # nothing written
        assert prev["affected_bookings"]["count"] == 2
        sample = prev["affected_bookings"]["sample"]
        assert [x["booking_number"] for x in sample] == [b1["booking_number"], b2["booking_number"]]
        assert sample[0]["date"] == hh.long(when) and "AM" in sample[0]["slot"] and sample[0]["customer_name"] and sample[0]["center_name"]
        assert prev["holiday"]["popup"]["dates_label"] == hh.short(when)
        # The same holiday on a day with no bookings counts nothing.
        r = await c.post("/api/v1/admin/holidays/preview", json={**body, "start_date": h.day(5), "end_date": h.day(5)}, headers=admin)
        assert r.json()["data"]["affected_bookings"]["count"] == 0
        made = (await c.post("/api/v1/admin/holidays", json=body, headers=admin)).json()["data"]
        assert made["affected_bookings"]["count"] == 2
        # Previewing an edit of the saved holiday (one more day).
        r = await c.post("/api/v1/admin/holidays/preview", params={"holiday_id": made["holiday"]["id"]},
                         json={**body, "end_date": h.day(3)}, headers=admin)
        assert r.status_code == 200 and r.json()["data"]["affected_bookings"]["count"] == 3
    for b in (b1, b2, b3):
        assert (await fb.doc(db, b["id"]))["status"] == "pending"  # never auto-cancelled
    invalidate_holiday_cache()
