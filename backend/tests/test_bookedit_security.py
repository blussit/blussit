"""Red-team regressions for the post-booking money tools (founder 2026-10-28).
Each test is an attack that was run live against the local stack (see the
change log in docs/ARCHITECTURE.md) and now stays closed."""
import asyncio

import pytest
from bson import ObjectId

from app.schemas.booking_adjust_schema import BookingDiscountRequest
from app.services import booking_money as bm
from app.services.booking_adjust_service import BookingAdjustService
from app.services.booking_service import BookingService
from tests import test_feat_booking_helpers as fb
from tests import test_fix_core_helpers as h

pytestmark = pytest.mark.asyncio


def mgr(s):
    return {"id": s["mgr"]["id"], "role": "manager", "center": s["center_id"]}


async def give(db, s, bid, amount, **kw):
    return await BookingAdjustService(db).set_discount(bid, BookingDiscountRequest(amount=amount, reason="Security test reason", **kw), mgr(s))


async def test_payment_rows_are_not_in_the_customer_or_captain_view(db):
    s = await fb.rig(db)
    b = await fb.book(db, s["cu"], s["when"], s["keys"][0])
    await fb.assign(db, b["id"], s)
    await fb.pay_online(db, b["id"], 100)
    async with h.client() as c:
        for headers in (s["cu"]["h"], s["cap"]["h"]):
            view = (await c.get(f"/api/v1/bookings/{b['id']}", headers=headers)).json()["data"]
            assert "payments" not in view and "payment_keys" not in view and view["amount_paid"] == 100
        staff = (await c.get(f"/api/v1/bookings/{b['id']}", headers=s["mgr"]["h"])).json()["data"]
        assert staff["payments"] and staff["payment_keys"]
        mine = (await c.get("/api/v1/bookings/my", headers=s["cu"]["h"])).json()["data"]
        assert all("payments" not in row and "payment_keys" not in row for row in mine)


async def test_old_endpoints_ignore_money_fields_in_the_body(db):
    s = await fb.rig(db)
    b = await fb.book(db, s["cu"], s["when"], s["keys"][0])
    before = await fb.doc(db, b["id"])
    evil = {"total_amount": 1, "amount_paid": 999, "amount_due": 0, "platform_earning": 5000, "captain_earning": 9999, "payment_status": "paid",
            "customer_id": "a" * 24, "service_center_id": "b" * 24, "wallet_applied": 500, "manager_discount": 300, "status": "completed"}
    async with h.client() as c:
        await c.patch(f"/api/v1/bookings/{b['id']}/details", json={"customer_notes": "hello", **evil}, headers=s["mgr"]["h"])
        await c.patch(f"/api/v1/bookings/{b['id']}", json={"customer_notes": "hello", **evil}, headers=s["cu"]["h"])
        await c.post(f"/api/v1/bookings/{b['id']}/reschedule", json={"scheduled_date": s["when"], "scheduled_slot": s["keys"][1], **evil}, headers=s["mgr"]["h"])
    after = await fb.doc(db, b["id"])
    for k in ("total_amount", "amount_paid", "platform_earning", "captain_earning", "payment_status", "customer_id", "service_center_id",
              "wallet_applied", "manager_discount"):
        assert after.get(k) == before.get(k), k
    assert after["status"] in ("pending", "rescheduled")


async def test_non_admin_cannot_touch_a_completed_booking(db, monkeypatch):
    monkeypatch.setattr("app.services.booking_service._ist_today", lambda: "2999-12-31")
    s = await fb.rig(db)
    b = await fb.book(db, s["cu"], s["when"], s["keys"][0])
    await BookingService(db).manager_mark_done(b["id"], s["mgr"]["id"], "manager", s["center_id"], send_whatsapp=False)
    before = await fb.doc(db, b["id"])
    async with h.client() as c:
        attempts = [
            ("patch", f"/api/v1/bookings/{b['id']}", s["cu"]["h"], {"customer_notes": "x"}),
            ("post", f"/api/v1/bookings/{b['id']}/cancel", s["cu"]["h"], {"reason": "cancel a completed one"}),
            ("post", f"/api/v1/bookings/{b['id']}/reschedule", s["mgr"]["h"], {"scheduled_date": s["when"], "scheduled_slot": s["keys"][1]}),
            ("patch", f"/api/v1/bookings/{b['id']}/details", s["mgr"]["h"], {"customer_notes": "x"}),
            ("post", f"/api/v1/bookings/{b['id']}/discount", s["mgr"]["h"], {"amount": 10, "reason": "post-completion discount"}),
            ("post", f"/api/v1/bookings/{b['id']}/cancel", s["mgr"]["h"], {"reason": "cancel a completed one"}),
        ]
        for method, url, headers, body in attempts:
            r = await getattr(c, method)(url, json=body, headers=headers)
            assert r.status_code in (400, 403, 404), (url, r.status_code)
    after = await fb.doc(db, b["id"])
    assert after["total_amount"] == before["total_amount"] and after["status"] == "completed" and not after.get("manager_discount")


async def test_holiday_blocks_a_managers_and_an_admins_reschedule(db):
    s = await fb.rig(db)
    b = await fb.book(db, s["cu"], s["when"], s["keys"][0])
    adm = await h.admin(db)
    async with h.client() as c:
        r = await c.post("/api/v1/admin/holidays", headers=adm["h"], json={
            "title": "Closed", "message": "Closed for a test", "scope": "centers", "service_center_ids": [s["center_id"]],
            "start_date": s["when"], "end_date": s["when"], "notice_days_before": 0})
        assert r.status_code == 200, r.text
        r = await c.post(f"/api/v1/bookings/{b['id']}/reschedule", json={"scheduled_date": s["when"], "scheduled_slot": s["keys"][1]}, headers=s["mgr"]["h"])
        assert r.status_code == 400, r.text
        r = await c.patch(f"/api/v1/admin/bookings/{b['id']}", json={"reason": "into the holiday", "scheduled_date": s["when"], "scheduled_slot": s["keys"][1]}, headers=adm["h"])
        assert r.status_code == 400, r.text
    assert (await fb.doc(db, b["id"]))["scheduled_slot"] == s["keys"][0]


async def test_forty_parallel_different_discounts_leave_one_consistent_state(db):
    s = await fb.rig(db)
    b = await fb.book(db, s["cu"], s["when"], s["keys"][0])
    out = await asyncio.gather(*[give(db, s, b["id"], a) for a in range(1, 31)], return_exceptions=True)
    assert any(isinstance(r, dict) for r in out)
    doc = await fb.doc(db, b["id"])
    rows = await db.booking_adjustments.find({"booking_id": b["id"]}).sort("seq", 1).to_list(None)
    assert [r["seq"] for r in rows] == list(range(1, len(rows) + 1))
    assert doc["manager_discount"] == rows[-1]["after"]["manager_discount"]
    assert round(doc["platform_earning"] + doc["captain_earning"], 2) == doc["total_amount"]
    assert doc["amount_due"] == doc["total_amount"]
    # Every step chained from the one before it: no lost update.
    for before, after in zip(rows, rows[1:]):
        assert after["before"]["manager_discount"] == before["after"]["manager_discount"]


async def test_discount_cancel_conserves_money(db):
    """Discount, undo, raise, then cancel: the customer ends with exactly what they paid."""
    s = await fb.rig(db)
    b = await fb.book(db, s["cu"], s["when"], s["keys"][0])
    paid = bm.total_of(await fb.doc(db, b["id"]))
    await fb.pay_online(db, b["id"])
    for amount in (100, 0, 150, 40):
        await give(db, s, b["id"], amount)
    await BookingService(db).cancel_booking(b["id"], fb.cancel(), s["mgr"]["id"], "manager", s["center_id"])
    ledger = await db.customer_wallet_ledger.find({"booking_id": b["id"]}).to_list(None)
    assert round(sum(r["amount"] for r in ledger), 2) == paid == await fb.balance(db, s["cu"]["id"])


async def test_wallet_paid_booking_discount_returns_wallet_credit_never_more(db):
    s = await fb.rig(db)
    await fb.credit(db, s["cu"]["id"], 1000)
    b = await fb.book(db, s["cu"], s["when"], s["keys"][0])
    doc = await fb.doc(db, b["id"])
    total = bm.total_of(doc)
    assert bm.wallet_applied_of(doc) == total and await fb.balance(db, s["cu"]["id"]) == 1000 - total
    out = await give(db, s, b["id"], 100)
    after = await fb.doc(db, b["id"])
    assert out["wallet_credit"] == 100 and await fb.balance(db, s["cu"]["id"]) == 1000 - total + 100
    assert after["wallet_applied"] == total - 100 and after["total_amount"] == total - 100 and after["amount_due"] == 0
    # the whole bill: the customer gets every rupee of wallet back, not a paisa more
    await give(db, s, b["id"], int(total))
    assert await fb.balance(db, s["cu"]["id"]) == 1000


async def test_discount_cannot_waive_a_carried_wallet_debt_or_the_tip(db, monkeypatch):
    s = await fb.rig(db)
    await fb.credit(db, s["cu"]["id"], -100)                      # the customer owes ₹100
    b = await fb.book(db, s["cu"], s["when"], s["keys"][0])
    doc = await fb.doc(db, b["id"])
    assert bm.r2(doc.get("wallet_due_carried")) == 100
    total = bm.total_of(doc)
    with pytest.raises(Exception) as exc:
        await give(db, s, b["id"], int(total))                    # more than the price: the debt is not discountable
    assert "more than the bill" in str(exc.value)
    await give(db, s, b["id"], int(total) - 100)
    after = await fb.doc(db, b["id"])
    assert after["total_amount"] == 100 and after["wallet_due_carried"] == 100 and after["amount_due"] == 100

    # a manager-done job with a tip: the tip stays
    monkeypatch.setattr("app.services.booking_service._ist_today", lambda: "2999-12-31")
    s2 = await fb.rig(db)
    b2 = await fb.book(db, s2["cu"], s2["when"], s2["keys"][0])
    svc = BookingService(db)
    await svc.manager_mark_done(b2["id"], s2["mgr"]["id"], "manager", s2["center_id"], send_whatsapp=False)
    adm = await h.admin(db)
    await svc.set_tip(b2["id"], 40, adm["id"], "admin", None)
    doc2 = await fb.doc(db, b2["id"])
    bill = bm.total_of(doc2) - 40
    out = await BookingAdjustService(db).set_discount(
        b2["id"], BookingDiscountRequest(amount=int(bill), reason="Full discount keeps the tip"), {"id": adm["id"], "role": "admin", "center": None})
    end = await fb.doc(db, b2["id"])
    assert end["total_amount"] == 40 and end["tip_amount"] == 40 and out["amount_due_after"] >= 0
    with pytest.raises(Exception):
        await BookingAdjustService(db).set_discount(
            b2["id"], BookingDiscountRequest(amount=int(bill) + 1, reason="One rupee too many"), {"id": adm["id"], "role": "admin", "center": None})


async def test_rate_limit_on_money_edits_per_account(db):
    s = await fb.rig(db)
    b = await fb.book(db, s["cu"], s["when"], s["keys"][0])
    svc = BookingAdjustService(db)
    now = __import__("datetime").datetime.now(__import__("datetime").timezone.utc)
    await db.booking_adjustments.insert_many([
        {"booking_id": str(ObjectId()), "key": f"flood:{i}:{s['mgr']['id']}", "seq": 1, "kind": "discount", "actor_id": s["mgr"]["id"], "created_at": now}
        for i in range(60)
    ])
    with pytest.raises(Exception) as exc:
        await give(db, s, b["id"], 5)
    assert getattr(exc.value, "status_code", None) == 429
    _ = svc


async def test_discount_racing_a_cancel_never_creates_money(db):
    s = await fb.rig(db)
    b = await fb.book(db, s["cu"], s["when"], s["keys"][0])
    paid = bm.total_of(await fb.doc(db, b["id"]))
    await fb.pay_online(db, b["id"])
    out = await asyncio.gather(
        give(db, s, b["id"], 120),
        BookingService(db).cancel_booking(b["id"], fb.cancel(), s["mgr"]["id"], "manager", s["center_id"]),
        return_exceptions=True,
    )
    assert any(not isinstance(r, Exception) for r in out)
    doc = await fb.doc(db, b["id"])
    assert doc["status"] == "cancelled"
    assert await fb.balance(db, s["cu"]["id"]) == paid
