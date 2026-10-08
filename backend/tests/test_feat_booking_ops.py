"""Feature build 2026-10-07 — manager new-booking alert recipients (spec
1.7), the pre-slot customer reminder sweep, timestamps with offsets."""
import asyncio
import logging
from datetime import datetime, timedelta, timezone

import pytest
from bson import ObjectId

from app.services.booking_service import BookingService
from app.utils.serializers import serialize_doc
from app.utils.timezone import now_ist
from tests import test_feat_booking_helpers as fb
from tests import test_fix_core_helpers as h

pytestmark = pytest.mark.asyncio


async def _admins(db) -> set[str]:
    return {str(u["_id"]) for u in await db.users.find({"role": "admin", "is_deleted": {"$ne": True}}).to_list(10)}


async def test_deleted_or_invalid_manager_id_falls_back_to_the_admins(db):
    cid, _pin = await h.center(db)
    svc = BookingService(db)
    admins = await _admins(db)
    # A garbage id, a deleted manager, a user who isn't a manager: none of
    # them may swallow the alert.
    assert set(await svc._manager_recipients(cid, "not-an-id")) <= admins and await svc._manager_recipients(cid, "not-an-id")
    gone = await h.manager(db, None)
    await db.users.update_one({"_id": ObjectId(gone["id"])}, {"$set": {"is_deleted": True}})
    got = await svc._manager_recipients(cid, gone["id"])
    assert gone["id"] not in got and got and set(got) <= admins
    cust = await h.customer(db, _pin)
    got = await svc._manager_recipients(cid, cust["id"])
    assert cust["id"] not in got and set(got) <= admins
    missing = str(ObjectId())
    assert missing not in await svc._manager_recipients(cid, missing)
    # A real working manager still gets it (and the admins don't).
    mgr = await h.manager(db, cid)
    assert await svc._manager_recipients(cid, mgr["id"]) == [mgr["id"]]


async def test_new_booking_reaches_admins_when_the_named_manager_is_gone(db):
    cid, pin = await h.center(db)
    gone = await h.manager(db, None)
    await db.users.update_one({"_id": ObjectId(gone["id"])}, {"$set": {"is_deleted": True}})
    await db.service_centers.update_one({"_id": ObjectId(cid)}, {"$set": {"manager_id": gone["id"]}})
    when, keys = await h.slot_keys(db, cid)
    cu = await h.customer(db, pin)
    b = await fb.book(db, cu, when, keys[0])
    admins = await _admins(db)
    rows = await db.notifications.find({"reference_id": b["id"], "title": {"$regex": "^New booking"}}).to_list(10)
    assert rows and {r["user_id"] for r in rows} <= admins
    assert not [r for r in rows if r["user_id"] == gone["id"]]


async def test_per_manager_send_failure_is_logged(db, caplog, monkeypatch):
    s = await fb.rig(db)
    svc = BookingService(db)
    real = svc.notifications.notify

    async def failing(user_id, *a, **k):
        if user_id == s["mgr"]["id"]:
            raise RuntimeError("meta down")
        return await real(user_id, *a, **k)

    monkeypatch.setattr(svc.notifications, "notify", failing)
    with caplog.at_level(logging.ERROR):
        from app.schemas.booking_schema import BookingCreateRequest

        await svc.create_booking(s["cu"]["id"], BookingCreateRequest(
            vehicle_type=await fb.get_hatchback_type_id(db), address_id=s["cu"]["address_id"],
            service_ids=[await fb.get_star_wash_service_id(db)], scheduled_date=s["when"], scheduled_slot=s["keys"][0],
        ), _allow_pinless=True, notify_background=False)
    assert any(s["mgr"]["id"] in r.getMessage() and "meta down" in r.getMessage() for r in caplog.records)


# ---------------------------------------------------------------- pre-slot customer reminder


async def test_customer_reminder_once_two_hours_before_inside_hours(db):
    s = await fb.rig(db)
    svc = BookingService(db)
    b = await fb.book(db, s["cu"], s["when"], s["keys"][0])
    # Booked long ago, slot in 100 minutes.
    await db.bookings.update_one({"_id": ObjectId(b["id"])}, {"$set": {"created_at": datetime.now(timezone.utc) - timedelta(days=1)}})
    today = now_ist().replace(hour=0, minute=0, second=0, microsecond=0, tzinfo=None)
    noon = now_ist().replace(hour=12, minute=0)
    start = noon + timedelta(minutes=100)
    await db.bookings.update_one({"_id": ObjectId(b["id"])}, {"$set": {
        "scheduled_date": today, "slot_start": start, "slot_end": start + timedelta(hours=3),
    }})
    due = [x for x in await svc.find_bookings_needing_customer_reminder(now=noon) if str(x["_id"]) == b["id"]]
    assert due
    # Run the real sweep twice (and three times at once): one message.
    row = await fb.doc(db, b["id"])
    sent = await asyncio.gather(*(BookingService(db).send_customer_reminder(row) for _ in range(3)))
    assert sent.count(True) == 1
    await svc.send_customer_reminder(await fb.doc(db, b["id"]))
    rows = await fb.queued(db, s["cu"]["id"], "booking_reminder")
    assert len(rows) == 1 and len(rows[0]["wa_params"]) == 5 and rows[0]["reference_id"] == b["id"]
    assert (await fb.doc(db, b["id"]))["customer_reminder_sent_at"]


async def test_customer_reminder_window_rules(db):
    s = await fb.rig(db)
    svc = BookingService(db)
    today = now_ist().replace(hour=0, minute=0, second=0, microsecond=0, tzinfo=None)
    old = datetime.now(timezone.utc) - timedelta(days=1)

    async def make(slot_index: int, minutes: float, created=old) -> str:
        b = await fb.book(db, s["cu"], s["when"], s["keys"][slot_index])
        start = now_ist() + timedelta(minutes=minutes)
        await db.bookings.update_one({"_id": ObjectId(b["id"])}, {"$set": {
            "created_at": created, "scheduled_date": today, "slot_start": start, "slot_end": start + timedelta(hours=3),
        }})
        return b["id"]

    due = await make(0, 90)
    too_early = await make(1, 4 * 60)
    fresh = await make(2, 60, created=datetime.now(timezone.utc))  # booked inside the lead: the confirmation was its reminder
    at = now_ist().replace(hour=12)
    # Re-time the rows relative to `at` so the check is clock-independent.
    for bid, minutes in ((due, 90), (too_early, 240), (fresh, 60)):
        start = at + timedelta(minutes=minutes)
        await db.bookings.update_one({"_id": ObjectId(bid)}, {"$set": {"slot_start": start, "slot_end": start + timedelta(hours=3)}})
    await db.bookings.update_one({"_id": ObjectId(fresh)}, {"$set": {"created_at": at - timedelta(minutes=10)}})
    ids = {str(x["_id"]) for x in await svc.find_bookings_needing_customer_reminder(now=at)}
    assert due in ids and too_early not in ids and fresh not in ids
    # Outside 7 AM–7 PM: nothing at all.
    assert await svc.find_bookings_needing_customer_reminder(now=at.replace(hour=6, minute=30)) == []
    assert await svc.find_bookings_needing_customer_reminder(now=at.replace(hour=19, minute=5)) == []
    # A cancelled booking is never reminded, even if the finder saw it.
    row = await fb.doc(db, due)
    await BookingService(db).cancel_booking(due, fb.cancel(), s["cu"]["id"], "customer")
    assert await svc.send_customer_reminder(row) is False


# ---------------------------------------------------------------- timestamps


def test_money_and_edit_timestamps_carry_an_offset():
    naive = datetime(2026, 10, 7, 4, 30)
    doc = serialize_doc({
        "_id": ObjectId(), "manager_discount_at": naive, "tip_updated_at": naive, "logged_at": naive, "refunded_at": naive,
        "customer_edited_at": naive, "history": [{"at": naive, "action": "created"}],
        "added_services": [{"at": naive, "name": "Polish"}], "scheduled_date": naive,
    })
    for key in ("manager_discount_at", "tip_updated_at", "logged_at", "refunded_at", "customer_edited_at"):
        assert doc[key].endswith("+05:30"), (key, doc[key])
    assert doc["history"][0]["at"].endswith("+05:30") and doc["added_services"][0]["at"].endswith("+05:30")
    assert doc["scheduled_date"] == "2026-10-07T04:30:00"  # wall-clock fields untouched
