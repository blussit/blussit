"""Commission engine (§7.13 D4–D8, H3, H4, D2): percent / per-booking /
override / cap / snapshot, the base, and that every path a booking can change
by posts exactly once and reverses correctly — with real service calls."""
import asyncio

import pytest
from bson import ObjectId

from app.core.exceptions import BadRequestException
from app.services import booking_money as bm
from app.services.payroll_service import PayrollService
from tests import test_feat_booking_helpers as fb
from tests import test_fix_core_helpers as h
from tests.payroll_helpers import API, complete_job, dues, ledger, make_payroll, rig, set_pay, wallet

pytestmark = pytest.mark.asyncio


def pct(base: int, percent: float) -> int:
    return int(base * percent / 100 + 0.5)


async def cm_rows(db, captain_id: str) -> list[dict]:
    return await ledger(db, captain_id, kind="commission")


async def test_percent_commission_posts_once_on_completion_and_snapshots_the_rate(db):
    s = await rig(db)
    async with h.client() as c:
        await make_payroll(c, s, "commission", commission={"mode": "percent", "percent": 40})
        before = await wallet(db, s["cap"]["id"])
        b = await complete_job(db, s)
        base = bm.commission_base_paise(b)
        assert base > 0
        rows = await cm_rows(db, s["cap"]["id"])
        assert len(rows) == 1 and rows[0]["amount_paise"] == pct(base, 40) and rows[0]["booking_id"] == str(b["_id"])
        assert rows[0]["key"] == f"cm:{b['_id']}:1"
        assert b["commission_rate_snapshot"] == 40 and b["commission_mode_snapshot"] == "percent" and b["commission_state"] == "accrued"
        assert b["commission_posted_paise"] == pct(base, 40) and b["commission_rev"] == 1
        assert b["captain_pay_mode"] == "payroll"
        # D2: the per-job wallet earning is NOT credited
        assert await wallet(db, s["cap"]["id"]) == pytest.approx(before)
        assert b.get("captain_wallet_posted") in (None, 0)
        # the same completion a second time posts nothing
        again = await PayrollService(db).sync_commission(str(b["_id"]))
        assert again is None and len(await cm_rows(db, s["cap"]["id"])) == 1
        # a rate change afterwards touches future bookings only
        await set_pay(c, s["mgr"]["h"], s["cap"]["id"], commission={"mode": "percent", "percent": 10})
        assert await PayrollService(db).sync_commission(str(b["_id"])) is None
        b2 = await complete_job(db, s, 1)
        assert b2["commission_rate_snapshot"] == 10 and b2["commission_posted_paise"] == pct(bm.commission_base_paise(b2), 10)
        assert await dues(db, s["cap"]["id"]) == pct(base, 40) + pct(bm.commission_base_paise(b2), 10)


async def test_cash_held_is_still_debited_but_no_earning_credited(db):
    s = await rig(db)
    async with h.client() as c:
        await make_payroll(c, s, "commission", commission={"mode": "percent", "percent": 40})
    from app.services.payment_service import PaymentService

    b = await complete_job(db, s)
    start = await wallet(db, s["cap"]["id"])
    out = await PaymentService(db).captain_collect_cash(str(b["_id"]), s["cap"]["id"])
    assert await wallet(db, s["cap"]["id"]) == pytest.approx(start - out["amount"])
    d = await fb.doc(db, str(b["_id"]))
    assert d["captain_wallet_posted"] == pytest.approx(-out["amount"])
    assert d["commission_posted_paise"] == pct(bm.commission_base_paise(d), 40)  # unchanged by collection


async def test_legacy_captain_keeps_per_job_earning_and_no_commission(db):
    s = await rig(db)
    before = await wallet(db, s["cap"]["id"])
    b = await complete_job(db, s)
    assert b["captain_pay_mode"] == "legacy" and b["captain_earning"] > 0
    assert await wallet(db, s["cap"]["id"]) == pytest.approx(before + b["captain_earning"])
    assert await cm_rows(db, s["cap"]["id"]) == [] and "commission_rev" not in b


async def test_a_job_settled_as_legacy_never_earns_commission_after_a_switch(db):
    s = await rig(db)
    b = await complete_job(db, s)
    async with h.client() as c:
        await make_payroll(c, s, "commission", commission={"mode": "percent", "percent": 50})
        # admin edit of the old job (a discount) must not start a commission for it
        r = await c.post(f"/api/v1/bookings/{b['_id']}/discount", json={"amount": 10, "reason": "Goodwill for the delay"}, headers=s["admin"]["h"])
        assert r.status_code == 200, r.text
    assert await cm_rows(db, s["cap"]["id"]) == []
    d = await fb.doc(db, str(b["_id"]))
    assert d["captain_pay_mode"] == "legacy" and d["captain_wallet_posted"] == pytest.approx(d["captain_earning"])


async def test_per_booking_mode_waits_for_an_amount_then_edits_post_deltas(db):
    s = await rig(db)
    async with h.client() as c:
        await make_payroll(c, s, "commission", commission={"mode": "per_booking", "percent": 0})
        b = await complete_job(db, s)
        bid = str(b["_id"])
        base = bm.commission_base_paise(b)
        assert b["commission_state"] == "pending" and await cm_rows(db, s["cap"]["id"]) == []
        listing = (await c.get(f"{API}/captains/{s['cap']['id']}/commissions", headers=s["mgr"]["h"])).json()["data"]
        assert [r["booking_id"] for r in listing] == [bid] and listing[0]["not_set"] is True and listing[0]["amount_paise"] == 0
        cap_row = (await c.get(f"{API}/captains", headers=s["mgr"]["h"])).json()["data"]["rows"][0]
        assert cap_row["pending_commissions"] == 1
        # a reason is required
        r = await c.put(f"{API}/bookings/{bid}/commission", json={"amount_paise": 5000, "reason": ""}, headers=s["mgr"]["h"])
        assert r.status_code == 422
        # set an amount → one accrual
        r = await c.put(f"{API}/bookings/{bid}/commission", json={"amount_paise": 5000, "reason": "Agreed on site"}, headers=s["mgr"]["h"])
        assert r.status_code == 200, r.text
        assert r.json()["data"]["amount_paise"] == 5000 and r.json()["data"]["not_set"] is False and r.json()["data"]["edited"] is True
        # a repeat of the same call posts nothing more; a changed amount posts the difference
        await c.put(f"{API}/bookings/{bid}/commission", json={"amount_paise": 5000, "reason": "Agreed on site"}, headers=s["mgr"]["h"])
        await c.put(f"{API}/bookings/{bid}/commission", json={"amount_paise": 7000, "reason": "Extra dirty car"}, headers=s["mgr"]["h"])
        rows = await cm_rows(db, s["cap"]["id"])
        assert [r["amount_paise"] for r in rows] == [5000, 2000]
        assert await dues(db, s["cap"]["id"]) == 7000
        d = await fb.doc(db, bid)
        assert d["commission_override_paise"] == 7000 and d["commission_override_reason"] == "Extra dirty car"
        audit = await db.audit_logs.find_one({"action": "PAYROLL_COMMISSION_SET", "target_id": bid, "details.after.override_paise": 7000})
        assert audit and audit["details"]["after"]["override_paise"] == 7000
        # the cap: 70 % of the base, hard
        cap = int(base * 70 / 100)
        r = await c.put(f"{API}/bookings/{bid}/commission", json={"amount_paise": cap + 1, "reason": "Too generous"}, headers=s["mgr"]["h"])
        assert r.status_code == 400 and "70%" in r.json()["message"]
        r = await c.put(f"{API}/bookings/{bid}/commission", json={"amount_paise": cap, "reason": "Maximum allowed"}, headers=s["mgr"]["h"])
        assert r.status_code == 200
        # percent override
        r = await c.put(f"{API}/bookings/{bid}/commission", json={"percent": 25, "reason": "Back to normal"}, headers=s["mgr"]["h"])
        assert r.status_code == 200 and r.json()["data"]["amount_paise"] == pct(base, 25)
        assert await dues(db, s["cap"]["id"]) == pct(base, 25)


async def test_override_over_alert_percent_notifies_admins_and_other_center_is_refused(db):
    s = await rig(db)
    from tests.payroll_helpers import second_center

    other = await second_center(db)
    async with h.client() as c:
        await make_payroll(c, s, "commission", commission={"mode": "percent", "percent": 20})
        b = await complete_job(db, s)
        bid = str(b["_id"])
        base = bm.commission_base_paise(b)
        r = await c.put(f"{API}/bookings/{bid}/commission", json={"percent": 41, "reason": "Great job"}, headers=other["mgr"]["h"])
        assert r.status_code == 403
        r = await c.put(f"{API}/bookings/{bid}/commission", json={"percent": 41, "reason": "Great job"}, headers=s["mgr"]["h"])
        assert r.status_code == 200
        admins = await db.users.find({"role": "admin"}).to_list(None)
        note = await db.notifications.find_one({"user_id": str(admins[0]["_id"]), "title": "High commission set", "reference_id": bid})
        assert note and "41%" in note["message"]
        r = await c.put(f"{API}/bookings/{bid}/commission", json={"percent": 30, "reason": "Fine"}, headers=s["mgr"]["h"])
        assert r.status_code == 200
        assert await db.notifications.count_documents({"title": "High commission set", "reference_id": bid}) == len(admins)  # no second alert at 30 %


async def test_discount_after_completion_posts_only_the_difference(db):
    s = await rig(db)
    async with h.client() as c:
        await make_payroll(c, s, "commission", commission={"mode": "percent", "percent": 40})
        b = await complete_job(db, s)
        bid = str(b["_id"])
        base0 = bm.commission_base_paise(b)
        r = await c.post(f"/api/v1/bookings/{bid}/discount", json={"amount": 20, "reason": "Late arrival goodwill"}, headers=s["admin"]["h"])
        assert r.status_code == 200, r.text
        d = await fb.doc(db, bid)
        base1 = bm.commission_base_paise(d)
        assert base1 == base0 - 2000
        rows = await cm_rows(db, s["cap"]["id"])
        assert [r["amount_paise"] for r in rows] == [pct(base0, 40), pct(base1, 40) - pct(base0, 40)]
        assert d["commission_posted_paise"] == pct(base1, 40) and d["commission_rate_snapshot"] == 40
        # removing the discount restores it, still exactly once per change
        r = await c.post(f"/api/v1/bookings/{bid}/discount", json={"amount": 0, "reason": "Discount removed again"}, headers=s["admin"]["h"])
        assert r.status_code == 200
        assert await dues(db, s["cap"]["id"]) == pct(base0, 40)


async def test_admin_status_change_away_from_completed_reverses_and_back_restores(db):
    s = await rig(db)
    async with h.client() as c:
        await make_payroll(c, s, "commission", commission={"mode": "percent", "percent": 40})
        b = await complete_job(db, s)
        bid = str(b["_id"])
        amount = pct(bm.commission_base_paise(b), 40)
        r = await c.patch(f"/api/v1/admin/bookings/{bid}", json={"reason": "Marked done by mistake", "status": "assigned", "force_status": True}, headers=s["admin"]["h"])
        assert r.status_code == 200, r.text
        assert await dues(db, s["cap"]["id"]) == 0
        d = await fb.doc(db, bid)
        assert d["commission_state"] == "none" and d["commission_posted_paise"] == 0 and d["commission_rate_snapshot"] == 40
        r = await c.patch(f"/api/v1/admin/bookings/{bid}", json={"reason": "It was done after all", "status": "completed", "force_status": True}, headers=s["admin"]["h"])
        assert r.status_code == 200, r.text
        assert await dues(db, s["cap"]["id"]) == amount
        rows = await cm_rows(db, s["cap"]["id"])
        assert [r["amount_paise"] for r in rows] == [amount, -amount, amount]


async def test_reversal_after_a_payout_makes_dues_negative(db):
    s = await rig(db)
    async with h.client() as c:
        await make_payroll(c, s, "commission", commission={"mode": "percent", "percent": 40})
        b = await complete_job(db, s)
        bid = str(b["_id"])
        amount = pct(bm.commission_base_paise(b), 40)
        from tests.payroll_helpers import key

        r = await c.post(f"{API}/captains/{s['cap']['id']}/payments", json={"amount_paise": amount, "kind": "commission", "method": "cash", "idempotency_key": key()}, headers=s["mgr"]["h"])
        assert r.status_code == 200
        assert await dues(db, s["cap"]["id"]) == 0
        await c.patch(f"/api/v1/admin/bookings/{bid}", json={"reason": "Done by mistake", "status": "assigned", "force_status": True}, headers=s["admin"]["h"])
        assert await dues(db, s["cap"]["id"]) == -amount
        detail = (await c.get(f"{API}/captains/{s['cap']['id']}", headers=s["mgr"]["h"])).json()["data"]
        assert detail["dues_paise"] == -amount


async def test_delete_reverses_and_restore_reaccrues_with_the_same_rate(db):
    s = await rig(db)
    async with h.client() as c:
        await make_payroll(c, s, "commission", commission={"mode": "percent", "percent": 40})
        b = await complete_job(db, s)
        bid = str(b["_id"])
        amount = pct(bm.commission_base_paise(b), 40)
        await set_pay(c, s["mgr"]["h"], s["cap"]["id"], commission={"mode": "percent", "percent": 5})
        from app.services.booking_service import BookingService

        await BookingService(db).soft_delete_booking(bid, s["admin"]["id"])
        assert await dues(db, s["cap"]["id"]) == 0
        await BookingService(db).restore_booking(bid)
        assert await dues(db, s["cap"]["id"]) == amount  # the frozen 40 %, not today's 5 %


async def test_reassigning_a_completed_job_moves_the_commission(db):
    s = await rig(db)
    cap2 = await h.captain(db, s["center_id"], wallet=100.0)
    async with h.client() as c:
        await make_payroll(c, s, "commission", commission={"mode": "percent", "percent": 40})
        b = await complete_job(db, s)
        bid = str(b["_id"])
        amount = pct(bm.commission_base_paise(b), 40)
        await db.users.update_one({"_id": ObjectId(cap2["id"])}, {"$set": {"pay": {
            "basis": "commission", "commission": {"mode": "percent", "percent": 20}, "salary_monthly_paise": 0, "weekly_off": 6,
            "paid_leaves_per_month": 1, "petrol_per_day_paise": 0, "extra_day_on_off": False, "deposit_expected_paise": 0,
            "kit": {"provided": False, "items": []}, "pay_history": [], "start_date": "2026-01-01",
        }}})
        r = await c.patch(f"/api/v1/admin/bookings/{bid}", json={"reason": "Wrong captain recorded", "captain_id": cap2["id"]}, headers=s["admin"]["h"])
        assert r.status_code == 200, r.text
        assert await dues(db, s["cap"]["id"]) == 0
        assert await dues(db, cap2["id"]) == pct(bm.commission_base_paise(await fb.doc(db, bid)), 20)
        d = await fb.doc(db, bid)
        assert d["commission_captain_id"] == cap2["id"] and d["commission_rate_snapshot"] == 20


async def _thousand_rupee_job(db, s, slot: int) -> str:
    """A booking priced exactly ₹1,000 (no travel / tip / carried balance) so the 70 % cap is ₹700."""
    b = await fb.book(db, s["cu"], s["when"], s["keys"][slot])
    await db.bookings.update_one({"_id": ObjectId(b["id"])}, {"$set": {
        "subtotal": 1000.0, "discount_amount": 0.0, "total_amount": 1000.0, "travel_charge": 0.0, "wallet_due_carried": 0.0,
        "wallet_applied": 0.0, "amount_due": 1000.0, "tip_amount": 0.0, "platform_earning": 1000.0,
    }})
    return b["id"]


async def test_manager_done_commission_is_a_rupee_amount_capped_at_70_percent(db, monkeypatch):
    s = await rig(db)
    monkeypatch.setattr("app.services.booking_service._ist_today", lambda: "2999-12-31")
    async with h.client() as c:
        await make_payroll(c, s, "commission", commission={"mode": "percent", "percent": 30})
        # ₹300 on a ₹1,000 job; the captain it was assigned to is the default
        b1 = await _thousand_rupee_job(db, s, 0)
        await db.bookings.update_one({"_id": ObjectId(b1)}, {"$set": {"captain_id": s["cap"]["id"], "status": "assigned"}})
        r = await c.post(f"/api/v1/bookings/{b1}/mark-done", json={"commission_amount_paise": 30000, "send_whatsapp": False}, headers=s["mgr"]["h"])
        assert r.status_code == 200, r.text
        d = await fb.doc(db, b1)
        assert d["status"] == "completed" and d["completed_by_role"] == "manager" and d["captain_id"] is None
        assert d["commission_captain_id"] == s["cap"]["id"] and d["commission_override_paise"] == 30000
        assert await dues(db, s["cap"]["id"]) == 30000
        assert d.get("captain_wallet_posted") in (None, 0) and await wallet(db, s["cap"]["id"]) == pytest.approx(500.0)
        # ₹701 is refused with the cap in the message, ₹700 is the maximum accepted
        b2 = await _thousand_rupee_job(db, s, 1)
        r = await c.post(f"/api/v1/bookings/{b2}/mark-done", json={"captain_id": s["cap"]["id"], "commission_amount_paise": 70100}, headers=s["mgr"]["h"])
        assert r.status_code == 400 and "Commission can't be more than 70% (₹700) of this ₹1,000 job." in r.json()["message"]
        assert (await fb.doc(db, b2))["status"] != "completed"
        r = await c.post(f"/api/v1/bookings/{b2}/mark-done", json={"captain_id": s["cap"]["id"], "commission_amount_paise": 70000, "send_whatsapp": False}, headers=s["mgr"]["h"])
        assert r.status_code == 200, r.text
        assert await dues(db, s["cap"]["id"]) == 100000
        # the same cap applies to a later edit of that booking's commission
        r = await c.put(f"{API}/bookings/{b2}/commission", json={"amount_paise": 70100, "reason": "More please"}, headers=s["mgr"]["h"])
        assert r.status_code == 400 and "70% (₹700)" in r.json()["message"]
        r = await c.put(f"{API}/bookings/{b2}/commission", json={"amount_paise": 40000, "reason": "Reduced after review"}, headers=s["mgr"]["h"])
        assert r.status_code == 200 and await dues(db, s["cap"]["id"]) == 70000


async def test_manager_done_without_captain_or_commission_earns_nothing_and_validates(db, monkeypatch):
    s = await rig(db)
    other = await h.captain(db, (await h.center(db))[0])
    monkeypatch.setattr("app.services.booking_service._ist_today", lambda: "2999-12-31")
    async with h.client() as c:
        await make_payroll(c, s, "commission", commission={"mode": "percent", "percent": 30})
        b1 = await fb.book(db, s["cu"], s["when"], s["keys"][0])
        r = await c.post(f"/api/v1/bookings/{b1['id']}/mark-done", json={"send_whatsapp": False}, headers=s["mgr"]["h"])
        assert r.status_code == 200
        assert await dues(db, s["cap"]["id"]) == 0 and "commission_captain_id" not in await fb.doc(db, b1["id"])
        # a commission with no captain at all → pick one
        b2 = await fb.book(db, s["cu"], s["when"], s["keys"][1])
        r = await c.post(f"/api/v1/bookings/{b2['id']}/mark-done", json={"commission_amount_paise": 1000}, headers=s["mgr"]["h"])
        assert r.status_code == 400 and "captain" in r.json()["message"]
        # a captain of another center
        r = await c.post(f"/api/v1/bookings/{b2['id']}/mark-done", json={"captain_id": other["id"], "commission_amount_paise": 1000}, headers=s["mgr"]["h"])
        assert r.status_code == 400
        # a captain named without an amount earns the captain's own percent; an old `commission_percent` field is ignored
        r = await c.post(f"/api/v1/bookings/{b2['id']}/mark-done", json={"captain_id": s["cap"]["id"], "commission_percent": 10, "send_whatsapp": False}, headers=s["mgr"]["h"])
        assert r.status_code == 200
        d2 = await fb.doc(db, b2["id"])
        assert "commission_override_percent" not in d2 and await dues(db, s["cap"]["id"]) == pct(bm.commission_base_paise(d2), 30)
        # a salary captain can't be given a commission
        b3 = await fb.book(db, s["cu"], s["when"], s["keys"][2])
        await set_pay(c, s["mgr"]["h"], s["cap"]["id"], basis="salary", salary_monthly_paise=1000000)
        r = await c.post(f"/api/v1/bookings/{b3['id']}/mark-done", json={"captain_id": s["cap"]["id"], "commission_amount_paise": 1000}, headers=s["mgr"]["h"])
        assert r.status_code == 400 and "isn't paid by commission" in r.json()["message"]
        assert (await fb.doc(db, b3["id"]))["status"] != "completed"


async def test_franchise_center_refuses_manager_done_commission(db, monkeypatch):
    s = await rig(db)
    monkeypatch.setattr("app.services.booking_service._ist_today", lambda: "2999-12-31")
    await db.service_centers.update_one(
        {"_id": ObjectId(s["center_id"])}, {"$set": {"operation": {"type": "franchise", "captain_pay_managed_by": "franchise"}}},
    )
    async with h.client() as c:
        await make_payroll(c, s, "commission", commission={"mode": "percent", "percent": 30})
        b = await fb.book(db, s["cu"], s["when"], s["keys"][0])
        r = await c.post(f"/api/v1/bookings/{b['id']}/mark-done", json={"captain_id": s["cap"]["id"], "commission_amount_paise": 1000}, headers=s["mgr"]["h"])
        assert r.status_code == 400 and "managed by the franchise partner" in r.json()["message"]
        # a captain with no amount: it closes, nothing accrues from it
        r = await c.post(f"/api/v1/bookings/{b['id']}/mark-done", json={"captain_id": s["cap"]["id"], "send_whatsapp": False}, headers=s["mgr"]["h"])
        assert r.status_code == 200 and await dues(db, s["cap"]["id"]) == 0
        assert "commission_captain_id" not in await fb.doc(db, b["id"])


async def test_logged_job_with_commission_amount_split_over_cars(db):
    s = await rig(db)
    from app.schemas.booking_schema import ManagerLogBookingRequest, QuickBookingLine
    from app.services.booking_service import BookingService
    from tests.factories import get_hatchback_type_id, get_star_wash_service_id

    await make_payroll_direct(db, s, 30)
    payload = ManagerLogBookingRequest(
        customer_name="Walk In Wanda", customer_phone="9666601234",
        lines=[QuickBookingLine(vehicle_type=await get_hatchback_type_id(db), quantity=2, service_ids=[await get_star_wash_service_id(db)])],
        scheduled_date=h.day(-1), service_time="10:30", address_line="Logged Lane 7, Vijay Nagar", send_whatsapp=False,
        captain_id=s["cap"]["id"], commission_amount_paise=20000,
    )
    result = await BookingService(db).create_manager_logged_visit(payload, manager_id=s["mgr"]["id"], manager_center_id=s["center_id"])
    ids = [b["id"] for b in result["bookings"]]
    assert len(ids) == 2
    rows = await cm_rows(db, s["cap"]["id"])
    assert sorted(r["booking_id"] for r in rows) == sorted(ids) and sum(r["amount_paise"] for r in rows) == 20000
    # a repeat of the same sync posts nothing
    for i in ids:
        assert await PayrollService(db).sync_commission(i) is None
    # an amount above the cap unwinds the whole job
    big = payload.model_copy(update={"commission_amount_paise": 10_000_000, "customer_phone": "9666601235", "service_time": "11:15"})
    with pytest.raises(BadRequestException):
        await BookingService(db).create_manager_logged_visit(big, manager_id=s["mgr"]["id"], manager_center_id=s["center_id"])
    assert await db.bookings.count_documents({"customer_phone": "9666601235"}) == 0


async def make_payroll_direct(db, s, percent: float) -> None:
    await db.users.update_one({"_id": ObjectId(s["cap"]["id"])}, {"$set": {"pay": {
        "basis": "commission", "commission": {"mode": "percent", "percent": percent}, "salary_monthly_paise": 0, "weekly_off": 6,
        "paid_leaves_per_month": 1, "petrol_per_day_paise": 0, "extra_day_on_off": False, "deposit_expected_paise": 0,
        "kit": {"provided": False, "items": []}, "pay_history": [], "start_date": "2026-01-01",
    }}})


async def test_commission_adjustments_extra_cut_cap_and_reversal(db):
    s = await rig(db)
    async with h.client() as c:
        await make_payroll(c, s, "commission", commission={"mode": "percent", "percent": 40})
        b = await complete_job(db, s)
        bid = str(b["_id"])
        base = bm.commission_base_paise(b)
        auto = pct(base, 40)
        from tests.payroll_helpers import key

        def adj(kind, amount, **kw):
            return c.post(f"{API}/captains/{s['cap']['id']}/adjustments", json={"kind": kind, "amount_paise": amount, "note": "Washed twice", "booking_id": bid, "idempotency_key": kw.pop("k", key())}, headers=s["mgr"]["h"])

        k = key()
        r = await adj("commission_extra", 1000, k=k)
        assert r.status_code == 200 and r.json()["data"]["amount_paise"] == 1000
        # the same key lands once
        r = await adj("commission_extra", 1000, k=k)
        assert r.status_code == 200 and r.json()["data"]["duplicate"] is True
        assert await dues(db, s["cap"]["id"]) == auto + 1000
        cap = int(base * 70 / 100)
        r = await adj("commission_extra", cap, k=key())
        assert r.status_code == 400
        r = await adj("commission_cut", auto + 2000)
        assert r.status_code == 400 and "below" in r.json()["message"]
        r = await adj("commission_cut", 500)
        assert r.status_code == 200 and r.json()["data"]["amount_paise"] == -500
        assert await dues(db, s["cap"]["id"]) == auto + 500
        # a commission adjustment needs its booking
        r = await c.post(f"{API}/captains/{s['cap']['id']}/adjustments", json={"kind": "commission_extra", "amount_paise": 100, "note": "no booking", "idempotency_key": key()}, headers=s["mgr"]["h"])
        assert r.status_code == 400
        # cancelling the booking's earning takes the extra / cut with it
        await c.patch(f"/api/v1/admin/bookings/{bid}", json={"reason": "Done by mistake", "status": "assigned", "force_status": True}, headers=s["admin"]["h"])
        assert await dues(db, s["cap"]["id"]) == 0


async def test_salary_captain_earns_no_commission_and_withdrawal_is_refused(db):
    s = await rig(db)
    async with h.client() as c:
        await make_payroll(c, s, "salary", salary_monthly_paise=2000000)
        b = await complete_job(db, s)
        assert b["captain_pay_mode"] == "payroll" and "commission_rev" not in b
        assert await cm_rows(db, s["cap"]["id"]) == []
        r = await c.post("/api/v1/wallet/withdrawals", json={"amount": 10}, headers=s["cap"]["h"])
        assert r.status_code == 400 and "Salary page" in r.json()["message"]


async def test_concurrent_completion_and_sync_post_one_row(db):
    s = await rig(db)
    async with h.client() as c:
        await make_payroll(c, s, "commission", commission={"mode": "percent", "percent": 40})
    b = await complete_job(db, s)
    results = await asyncio.gather(*[PayrollService(db).sync_commission(str(b["_id"])) for _ in range(4)], return_exceptions=True)
    assert not [r for r in results if isinstance(r, Exception) and not isinstance(r, BadRequestException)]
    assert len(await cm_rows(db, s["cap"]["id"])) == 1
