"""MONEY-2 (founder 2026-10-07): a tip records its OWN method
(`tip_method`: cash | online, default cash), independent of how the booking
was paid. Money received per method puts the tip in its own bucket and the
collections report counts tips_cash / tips_online. A tip saved before the
field existed is cash. Regression: a tip on an online-paid manager-done job
used to be counted as paid_online."""
import itertools

import pytest

from app.schemas.booking_schema import ManagerLogBookingRequest, QuickBookingLine
from app.services import booking_money as bm
from app.services.booking_service import BookingService
from app.services.money_service import backfill_booking_money
from app.services.payment_service import PaymentService
from tests import test_feat_booking_helpers as fb
from tests import test_fix_core_helpers as h
from tests.factories import get_hatchback_type_id, get_star_wash_service_id

pytestmark = pytest.mark.asyncio

_PHONE = itertools.count(9777700001)


@pytest.fixture(autouse=True)
def _marked_done_on_the_bookings_day(monkeypatch):
    monkeypatch.setattr("app.services.booking_service._ist_today", lambda: "2999-12-31")


async def _log(db, s: dict, **kw) -> dict:
    payload = dict(
        customer_name="Tip Tester", customer_phone=str(next(_PHONE)),
        lines=[QuickBookingLine(vehicle_type=await get_hatchback_type_id(db), quantity=1, service_ids=[await get_star_wash_service_id(db)])],
        scheduled_date=h.day(-1), service_time="10:30", address_line="Tip Lane 4, Indore", send_whatsapp=False,
    )
    payload.update(kw)
    out = await BookingService(db).create_manager_logged_visit(
        ManagerLogBookingRequest(**payload), manager_id=s["mgr"]["id"], manager_center_id=s["center_id"],
    )
    return await fb.doc(db, out["bookings"][0]["id"])


async def test_logged_online_job_with_a_cash_tip(db):
    s = await fb.rig(db)
    plain = await _log(db, s, payment_method="online")
    bill = bm.total_of(plain)
    doc = await _log(db, s, payment_method="online", tip_amount=50)
    assert doc["tip_method"] == "cash" and doc["tip_amount"] == 50
    assert doc["paid_online"] == bill and doc["paid_cash"] == 50
    assert bm.amount_paid_of(doc) == bill + 50 and bm.amount_due(doc) == 0
    online = await _log(db, s, payment_method="cash", tip_amount=30, tip_method="online")
    assert online["tip_method"] == "online" and online["paid_online"] == 30 and online["paid_cash"] == bill


async def test_tip_on_an_online_paid_manager_done_job_is_cash(db):
    """The case found earlier: it landed in paid_online."""
    s = await fb.rig(db)
    b = await fb.book(db, s["cu"], s["when"], s["keys"][0])
    await fb.pay_online(db, b["id"])
    bs = BookingService(db)
    await bs.manager_mark_done(b["id"], s["mgr"]["id"], "manager", s["center_id"], send_whatsapp=False)
    before = await fb.doc(db, b["id"])
    paid_online = before["paid_online"]
    assert paid_online > 0 and not before.get("paid_cash")
    await bs.set_tip(b["id"], 60, s["mgr"]["id"], "manager", s["center_id"])
    doc = await fb.doc(db, b["id"])
    assert doc["tip_method"] == "cash" and doc["paid_cash"] == 60 and doc["paid_online"] == paid_online
    # Corrected to online: the 60 moves bucket, nothing is counted twice.
    await bs.set_tip(b["id"], 60, s["mgr"]["id"], "manager", s["center_id"], tip_method="online")
    doc = await fb.doc(db, b["id"])
    assert doc["tip_method"] == "online" and doc["paid_cash"] == 0 and doc["paid_online"] == paid_online + 60
    assert doc["amount_paid"] == bm.r2(paid_online + 60)
    # Lowered and back to cash.
    await bs.set_tip(b["id"], 25, s["mgr"]["id"], "manager", s["center_id"], tip_method="cash")
    doc = await fb.doc(db, b["id"])
    assert doc["paid_cash"] == 25 and doc["paid_online"] == paid_online and doc["total_amount"] == bm.total_of(before) + 25
    # Removed.
    await bs.set_tip(b["id"], 0, s["mgr"]["id"], "manager", s["center_id"])
    doc = await fb.doc(db, b["id"])
    assert doc["paid_cash"] == 0 and doc["paid_online"] == paid_online and doc["total_amount"] == bm.total_of(before)


async def test_reports_count_tips_by_method(db):
    s = await fb.rig(db)
    await _log(db, s, payment_method="online", tip_amount=50)                       # cash tip
    await _log(db, s, payment_method="cash", tip_amount=20, tip_method="online")    # online tip
    rep = await PaymentService(db).center_collections(s["center_id"], "manager", s["center_id"], h.day(-3), h.day(3))
    row = next(r for r in rep["rows"] if r["captain_id"] == "manager")
    assert row["tips_cash"] == 50 and row["tips_online"] == 20 and row["tip_amount"] == 70
    assert rep["totals"]["tips_cash"] == 50 and rep["totals"]["tips_online"] == 20
    admin = await PaymentService(db).admin_collections(h.day(-3), h.day(3))
    arow = next(r for r in admin["rows"] if r["service_center_id"] == s["center_id"])
    assert arow["tips_cash"] == 50 and arow["tips_online"] == 20


async def test_legacy_tip_without_a_method_is_cash(db):
    s = await fb.rig(db)
    doc = await _log(db, s, payment_method="online")
    bill = bm.total_of(doc)
    # As the old code left it: the tip inside paid_online, no tip_method.
    await db.bookings.update_one({"_id": doc["_id"]}, {
        "$set": {"tip_amount": 40.0, "paid_online": bill + 40, "amount_paid": bill + 40, "total_amount": bill + 40,
                 "platform_earning": bill + 40},
        "$unset": {"tip_method": "", "paid_cash": ""},
    })
    rep = await PaymentService(db).center_collections(s["center_id"], "manager", s["center_id"], h.day(-3), h.day(3))
    row = next(r for r in rep["rows"] if r["captain_id"] == "manager")
    assert row["tips_cash"] == 40 and row["tips_online"] == 0
    await backfill_booking_money(db)
    after = await fb.doc(db, str(doc["_id"]))
    assert after["tip_method"] == "cash" and after["paid_cash"] == 40 and after["paid_online"] == bill
    assert after["amount_paid"] == bill + 40
    await backfill_booking_money(db)   # idempotent
    again = await fb.doc(db, str(doc["_id"]))
    assert again["paid_cash"] == 40 and again["paid_online"] == bill


async def test_tip_endpoint_accepts_the_method(db):
    s = await fb.rig(db)
    doc = await _log(db, s, payment_method="cash")
    bill = bm.total_of(doc)
    url = f"/api/v1/bookings/{doc['_id']}/tip"
    async with h.client() as c:
        r = await c.patch(url, json={"tip_amount": 30, "tip_method": "card"}, headers=s["mgr"]["h"])
        assert r.status_code == 422, r.text
        r = await c.patch(url, json={"tip_amount": 30, "tip_method": "online"}, headers=s["mgr"]["h"])
        assert r.status_code == 200 and r.json()["data"]["tip_method"] == "online", r.text
        r = await c.patch(url, json={"tip_amount": 30}, headers=s["mgr"]["h"])   # default: cash
        assert r.status_code == 200 and r.json()["data"]["tip_method"] == "cash", r.text
    after = await fb.doc(db, str(doc["_id"]))
    assert after["paid_cash"] == bill + 30 and (after.get("paid_online") or 0) == 0
    audit = await db.audit_logs.find_one({"action": "SET_BOOKING_TIP", "target_id": str(doc["_id"])}, sort=[("created_at", -1)])
    assert audit and audit["details"].get("tip_method") in ("cash", "online")
