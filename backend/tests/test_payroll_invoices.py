"""Invoices and settlements (§7.14): an invoice on every payment, salary months
paid once, commission periods that settle each booking exactly once, voids,
the plan-covered wash rule, weekly off, and the reconcile that proves it."""
import asyncio
import re

import pytest
from bson import ObjectId

from app.services import booking_money as bm
from tests import test_feat_booking_helpers as fb
from tests import test_fix_core_helpers as h
from tests.payroll_helpers import API, complete_job, dues, key, ledger, make_payroll, rig, second_center, set_pay

pytestmark = pytest.mark.asyncio

NUMBER = re.compile(r"^BLS-PAY-\d{4}-\d{6}$")


def pct(base: int, percent: float) -> int:
    return int(base * percent / 100 + 0.5)


def last_month() -> str:
    from app.services.payroll_salary import previous_month
    from app.utils.timezone import now_ist

    return previous_month(now_ist().date().isoformat()[:7])


def today() -> str:
    from app.utils.timezone import now_ist

    return now_ist().date().isoformat()


async def jobs(db, s, n: int) -> list[dict]:
    return [await complete_job(db, s, i) for i in range(n)]


def pay_body(**kw) -> dict:
    return {"method": "upi", "idempotency_key": kw.pop("idempotency_key", key()), **kw}


async def pay_commission(c, s, **kw):
    body = pay_body(kind="commission", from_date=kw.pop("from_date", today()), to_date=kw.pop("to_date", today()), **kw)
    return await c.post(f"{API}/captains/{s['cap']['id']}/pay-invoice", json=body, headers=kw.pop("headers", None) or s["mgr"]["h"])


async def preview(c, s, **params):
    return await c.get(f"{API}/captains/{s['cap']['id']}/pay-preview", params=params, headers=s["mgr"]["h"])


async def reconcile(c, s):
    r = await c.get(f"{API}/reconcile", params={"center_id": s["center_id"]}, headers=s["admin"]["h"])
    assert r.status_code == 200, r.text
    return r.json()["data"]


async def finance_total(db, s) -> int:
    rows = await db.finance_entries.find({"service_center_id": s["center_id"], "source": "payroll", "is_deleted": {"$ne": True}}).to_list(None)
    return sum(r["amount_paise"] for r in rows)


# ------------------------------------------------------------------ an invoice on every payment


async def test_every_plain_payment_gets_a_numbered_invoice_with_one_idempotent_row(db):
    s = await rig(db)
    k = key()
    async with h.client() as c:
        body = {"kind": "advance", "method": "cash", "amount_paise": 150000, "note": "Festival advance", "idempotency_key": k}
        r1 = await c.post(f"{API}/captains/{s['cap']['id']}/payments", json=body, headers=s["mgr"]["h"])
        r2 = await c.post(f"{API}/captains/{s['cap']['id']}/payments", json=body, headers=s["mgr"]["h"])
        assert r1.status_code == r2.status_code == 200
        d1, d2 = r1.json()["data"], r2.json()["data"]
        assert NUMBER.match(d1["invoice_number"]) and d1["invoice_id"] == d2["invoice_id"] and d2["duplicate"] is True
        inv = (await c.get(f"{API}/invoices/{d1['invoice_id']}", headers=s["mgr"]["h"])).json()["data"]
        assert inv["kind"] == "advance" and inv["status"] == "paid" and inv["paid_paise"] == 150000 and inv["gross_paise"] == 150000
        assert inv["lines"][0]["type"] == "payment" and inv["finance_entry_id"] and inv["captain_name"] and inv["service_center_name"]
        r3 = await c.post(f"{API}/captains/{s['cap']['id']}/payments", json={**body, "idempotency_key": key()}, headers=s["mgr"]["h"])
        assert r3.json()["data"]["invoice_number"] != d1["invoice_number"]
        # racing double submits of one new key land once (one invoice, one ledger row, one Finance row)
        k2 = key()
        racers = await asyncio.gather(*[c.post(f"{API}/captains/{s['cap']['id']}/payments", json={**body, "idempotency_key": k2}, headers=s["mgr"]["h"]) for _ in range(4)])
        assert all(r.status_code == 200 for r in racers)
    assert await db.payroll_invoices.count_documents({"captain_id": s["cap"]["id"]}) == 3
    assert len({r.json()["data"]["invoice_number"] for r in racers}) == 1
    assert (await ledger(db, s["cap"]["id"], kind="advance"))[0]["invoice_number"] == d1["invoice_number"]


async def test_invoice_numbers_are_sequential_and_a_failed_payment_gives_its_number_back(db, monkeypatch):
    s = await rig(db)
    async with h.client() as c:
        first = (await c.post(f"{API}/captains/{s['cap']['id']}/payments", json={"kind": "other", "method": "cash", "amount_paise": 1000, "idempotency_key": key()}, headers=s["mgr"]["h"])).json()["data"]
        from app.services.finance_service import FinanceService

        async def boom(*a, **kw):
            raise RuntimeError("finance down")

        real = FinanceService.create_system_entry
        monkeypatch.setattr(FinanceService, "create_system_entry", boom)
        bad = await c.post(f"{API}/captains/{s['cap']['id']}/payments", json={"kind": "other", "method": "cash", "amount_paise": 1000, "idempotency_key": key()}, headers=s["mgr"]["h"])
        assert bad.status_code >= 500 or bad.status_code == 400
        monkeypatch.setattr(FinanceService, "create_system_entry", real)
        second = (await c.post(f"{API}/captains/{s['cap']['id']}/payments", json={"kind": "other", "method": "cash", "amount_paise": 1000, "idempotency_key": key()}, headers=s["mgr"]["h"])).json()["data"]
    a, b = int(first["invoice_number"][-6:]), int(second["invoice_number"][-6:])
    assert b == a + 1  # no gap


# ------------------------------------------------------------------ commission invoices


async def test_commission_invoice_settles_each_booking_once_and_a_second_pay_finds_nothing(db):
    s = await rig(db)
    async with h.client() as c:
        await make_payroll(c, s, "commission", commission={"mode": "percent", "percent": 40})
        bookings = await jobs(db, s, 3)
        amounts = [pct(bm.commission_base_paise(b), 40) for b in bookings]
        pv = (await preview(c, s, kind="commission", **{"from": today(), "to": today()})).json()["data"]
        assert pv["gross_paise"] == sum(amounts) and len(pv["lines"]) == 3 and pv["can_pay"] is True
        assert {l["kind"] for l in pv["lines"]} == {"new"} and all(l["booking_number"] for l in pv["lines"])
        # untick one booking
        pick = [str(b["_id"]) for b in bookings[:2]]
        r = await pay_commission(c, s, booking_ids=pick, expected_gross_paise=amounts[0] + amounts[1])
        assert r.status_code == 200, r.text
        inv = r.json()["data"]
        assert NUMBER.match(inv["number"]) and inv["kind"] == "commission" and inv["paid_paise"] == amounts[0] + amounts[1]
        assert inv["adjustment_paise"] == 0 and [l["booking_id"] for l in inv["lines"]] == pick
        assert inv["period_from"] == inv["period_to"] == today()
        b0 = await fb.doc(db, pick[0])
        assert b0["commission_settled_paise"] == amounts[0] and b0["commission_settled_by"][s["cap"]["id"]] == amounts[0]
        assert await dues(db, s["cap"]["id"]) == amounts[2]
        # the same period again: only the unticked booking remains
        pv2 = (await preview(c, s, kind="commission", **{"from": today(), "to": today()})).json()["data"]
        assert [l["booking_id"] for l in pv2["lines"]] == [str(bookings[2]["_id"])]
        # a wash can't be paid twice: two racing invoices (different keys) for the rest → exactly one wins
        racers = await asyncio.gather(*[pay_commission(c, s) for _ in range(4)])
        ok = [r for r in racers if r.status_code == 200]
        assert len(ok) == 1, [(r.status_code, r.text[:120]) for r in racers]
        assert all(r.status_code in (400, 409) for r in racers if r.status_code != 200)
        again = await pay_commission(c, s)
        assert again.status_code == 400 and "no unpaid commission" in again.json()["message"].lower()
        assert await dues(db, s["cap"]["id"]) == 0
        assert await db.payroll_settlements.count_documents({"kind": "settle"}) == 3
        # the finance debit equals what was handed over
        assert await finance_total(db, s) == sum(amounts)
        rec = await reconcile(c, s)
        assert rec["ok"] is True and rec["mismatches"] == [], rec


async def test_commission_invoice_adjustment_needs_a_reason_and_posts_a_ledger_row(db):
    s = await rig(db)
    async with h.client() as c:
        await make_payroll(c, s, "commission", commission={"mode": "percent", "percent": 40})
        b = (await jobs(db, s, 1))[0]
        amount = pct(bm.commission_base_paise(b), 40)
        r = await pay_commission(c, s, amount_paise=amount - 5000)
        assert r.status_code == 400 and "say why" in r.json()["message"].lower()
        r = await pay_commission(c, s, amount_paise=amount - 5000, reason="Damaged a mirror")
        assert r.status_code == 200, r.text
        inv = r.json()["data"]
        assert inv["adjustment_paise"] == -5000 and inv["adjustment_reason"] == "Damaged a mirror" and inv["paid_paise"] == amount - 5000
        adj = await db.payroll_ledger.find_one({"source": "invoice_adjustment", "captain_id": s["cap"]["id"]})
        assert adj["kind"] == "deduction" and adj["amount_paise"] == -5000
        assert await dues(db, s["cap"]["id"]) == 0
        # void reverses payout, settlement, finance and the adjustment row
        v = await c.post(f"{API}/invoices/{inv['id']}/void", json={"reason": "Paid to the wrong person"}, headers=s["mgr"]["h"])
        assert v.status_code == 200 and v.json()["data"]["status"] == "void"
        assert await dues(db, s["cap"]["id"]) == amount
        d = await fb.doc(db, str(b["_id"]))
        assert d["commission_settled_by"][s["cap"]["id"]] == 0 and d["commission_settled_paise"] == 0
        assert await finance_total(db, s) == 0
        pv = (await preview(c, s, kind="commission", **{"from": today(), "to": today()})).json()["data"]
        assert pv["gross_paise"] == amount
        # a void twice changes nothing; the old void route reaches the same place
        v2 = await c.post(f"{API}/invoices/{inv['id']}/void", json={"reason": "Paid to the wrong person"}, headers=s["mgr"]["h"])
        assert v2.status_code == 200 and v2.json()["data"]["duplicate"] is True
        assert (await reconcile(c, s))["ok"] is True
        # and it can be invoiced again
        again = await pay_commission(c, s)
        assert again.status_code == 200 and again.json()["data"]["number"] != inv["number"]
        assert (await reconcile(c, s))["ok"] is True


async def test_a_later_edit_becomes_a_revised_line_and_a_refund_a_recovery(db):
    s = await rig(db)
    async with h.client() as c:
        await make_payroll(c, s, "commission", commission={"mode": "per_booking", "percent": 0})
        b1, b2 = await jobs(db, s, 2)
        ids = [str(b1["_id"]), str(b2["_id"])]
        for bid, amount in zip(ids, (10000, 20000)):
            assert (await c.put(f"{API}/bookings/{bid}/commission", json={"amount_paise": amount, "reason": "Agreed"}, headers=s["mgr"]["h"])).status_code == 200
        first = (await pay_commission(c, s)).json()["data"]
        assert first["gross_paise"] == 30000
        # edited up after payment: a +5,000 revised line, found even in another period
        await c.put(f"{API}/bookings/{ids[0]}/commission", json={"amount_paise": 15000, "reason": "More dirt"}, headers=s["mgr"]["h"])
        pv = (await preview(c, s, kind="commission", **{"from": "2026-01-01", "to": "2026-01-02"})).json()["data"]
        assert [(l["booking_id"], l["amount_paise"], l["kind"]) for l in pv["lines"]] == [(ids[0], 5000, "revised")]
        # the second booking refunded after it was paid → a negative line (recovery); netted against the +5,000 it is not payable alone
        await db.bookings.update_one({"_id": ObjectId(ids[1])}, {"$set": {"payment_status": "refunded"}})
        from app.services.payroll_service import PayrollService

        await PayrollService(db).sync_commission(ids[1])
        pv = (await preview(c, s, kind="commission", **{"from": today(), "to": today()})).json()["data"]
        amounts = {l["booking_id"]: l["amount_paise"] for l in pv["lines"]}
        assert amounts == {ids[0]: 5000, ids[1]: -20000} and pv["gross_paise"] == -15000 and pv["can_pay"] is False
        r = await pay_commission(c, s)
        assert r.status_code == 400 and "recover" in r.json()["message"].lower()
        # a new booking's commission nets the recovery off the invoice
        b3 = await complete_job(db, s, 2)
        assert (await c.put(f"{API}/bookings/{b3['_id']}/commission", json={"amount_paise": 20000, "reason": "Agreed"}, headers=s["mgr"]["h"])).status_code == 200
        r = await pay_commission(c, s)
        assert r.status_code == 200, r.text
        inv = r.json()["data"]
        assert inv["gross_paise"] == 5000 and {l["kind"] for l in inv["lines"]} == {"new", "revised"}
        assert await dues(db, s["cap"]["id"]) == 0
        assert (await reconcile(c, s))["ok"] is True


async def test_reassigned_booking_gives_the_old_captain_a_recovery_line(db):
    s = await rig(db)
    cap2 = await h.captain(db, s["center_id"], wallet=100.0)
    async with h.client() as c:
        await make_payroll(c, s, "commission", commission={"mode": "percent", "percent": 40})
        b = await complete_job(db, s)
        bid = str(b["_id"])
        amount = pct(bm.commission_base_paise(b), 40)
        assert (await pay_commission(c, s)).status_code == 200
        await db.users.update_one({"_id": ObjectId(cap2["id"])}, {"$set": {"pay": {
            "basis": "commission", "commission": {"mode": "percent", "percent": 20}, "salary_monthly_paise": 0, "weekly_off_days": [5, 6],
            "paid_leaves_per_month": 0, "petrol_per_day_paise": 0, "extra_day_on_off": False, "deposit_expected_paise": 0,
            "kit": {"provided": False, "items": []}, "pay_history": [], "start_date": "2026-01-01",
        }}})
        r = await c.patch(f"/api/v1/admin/bookings/{bid}", json={"reason": "Wrong captain recorded", "captain_id": cap2["id"]}, headers=s["admin"]["h"])
        assert r.status_code == 200, r.text
        assert await dues(db, s["cap"]["id"]) == -amount
        pv = (await preview(c, s, kind="commission", **{"from": today(), "to": today()})).json()["data"]
        assert [(l["booking_id"], l["amount_paise"]) for l in pv["lines"]] == [(bid, -amount)]
        # the new captain starts from 0 on that booking
        d = await fb.doc(db, bid)
        assert d["commission_captain_id"] == cap2["id"] and d["commission_settled_paise"] == 0
        pv2 = (await c.get(f"{API}/captains/{cap2['id']}/pay-preview", params={"kind": "commission", "from": today(), "to": today()}, headers=s["mgr"]["h"])).json()["data"]
        assert len(pv2["lines"]) == 1 and pv2["lines"][0]["amount_paise"] > 0
        assert (await reconcile(c, s))["ok"] is True


# ------------------------------------------------------------------ plan-covered wash


async def test_a_plan_covered_wash_is_never_auto_computed_but_paid_extras_are(db):
    from datetime import datetime, timedelta, timezone

    from app.services.booking_service import BookingService

    s = await rig(db)
    async with h.client() as c:
        await make_payroll(c, s, "commission", commission={"mode": "percent", "percent": 40})
    # a booking the plan fully covers: list value 1000 waived, an extra of 200 paid
    b = await fb.book(db, s["cu"], s["when"], s["keys"][0])
    await db.bookings.update_one({"_id": ObjectId(b["id"])}, {"$set": {
        "subscription_id": "sub1", "subtotal": 1200.0, "discount_amount": 1000.0, "manager_discount": 0.0, "total_amount": 200.0,
        "travel_charge": 0.0, "wallet_due_carried": 0.0, "tip_amount": 0.0, "amount_due": 200.0, "captain_id": s["cap"]["id"],
        "status": "service_started", "vehicle_verified": True, "service_started_at": datetime.now(timezone.utc) - timedelta(minutes=30),
    }})
    await BookingService(db).capture_after_photo_and_complete(b["id"], await fb.hb.snap(db, s["cap"]["id"], "after"), s["cap"]["id"])
    d = await fb.doc(db, b["id"])
    assert bm.plan_covered_base_paise(d) == 100000 and bm.commission_base_paise(d) == 120000
    # percent captain, yet the wash is "Commission Not Set"; only the paid extra (₹200 × 40 %) accrued
    assert d["commission_state"] == "pending" and d["commission_posted_paise"] == pct(20000, 40) == 8000
    async with h.client() as c:
        listing = (await c.get(f"{API}/captains/{s['cap']['id']}/commissions", headers=s["mgr"]["h"])).json()["data"]
        row = listing[0]
        assert row["not_set"] is True and row["plan_covered"] is True and row["plan_base_paise"] == 100000 and row["payment_state"] == "not_set"
        # it can't go on an invoice while Not Set (it is listed, not selectable) ...
        pv = (await preview(c, s, kind="commission", **{"from": today(), "to": today()})).json()["data"]
        # the paid extras already accrued, so only those are payable; the booking itself is flagged Not Set in the Commissions tab
        assert pv["gross_paise"] == 8000 and pv["not_set"] == []
        # ... the manager sets the wash amount (cap: 70 % of 1,200 = 840)
        too_much = await c.put(f"{API}/bookings/{b['id']}/commission", json={"amount_paise": 83000, "reason": "Too much"}, headers=s["mgr"]["h"])
        assert too_much.status_code == 400 and "70%" in too_much.json()["message"]
        ok = await c.put(f"{API}/bookings/{b['id']}/commission", json={"amount_paise": 30000, "reason": "Plan wash, agreed"}, headers=s["mgr"]["h"])
        assert ok.status_code == 200, ok.text
        assert ok.json()["data"]["amount_paise"] == 38000 and ok.json()["data"]["not_set"] is False and ok.json()["data"]["edited"] is True
        d = await fb.doc(db, b["id"])
        assert d["commission_plan_wash_paise"] == 30000 and d["commission_posted_paise"] == 38000 and d["commission_state"] == "accrued"
        assert await dues(db, s["cap"]["id"]) == 38000
        # a customer / captain never sees the new fields
        for who in ("cu", "cap"):
            seen = (await c.get(f"/api/v1/bookings/{b['id']}", headers=s[who]["h"])).json()["data"]
            assert not [k for k in seen if k.startswith("commission_")], who


async def test_a_plan_wash_stays_not_set_for_a_per_booking_captain_and_for_zero_extras(db):
    s = await rig(db)
    async with h.client() as c:
        await make_payroll(c, s, "commission", commission={"mode": "percent", "percent": 30})
    b = await complete_job(db, s)
    # turn it into a fully plan-covered wash with no paid extra
    await db.bookings.update_one({"_id": b["_id"]}, {"$set": {
        "subscription_id": "sub9", "discount_amount": float(b["subtotal"]), "manager_discount": 0.0, "total_amount": 0.0, "tip_amount": 0.0,
        "travel_charge": 0.0, "wallet_due_carried": 0.0,
    }})
    from app.services.payroll_service import PayrollService

    await PayrollService(db).sync_commission(b["_id"])
    d = await fb.doc(db, str(b["_id"]))
    assert d["commission_state"] == "pending" and d["commission_posted_paise"] == 0
    assert await dues(db, s["cap"]["id"]) == 0
    async with h.client() as c:
        pv = (await preview(c, s, kind="commission", **{"from": today(), "to": today()})).json()["data"]
        assert [n["booking_id"] for n in pv["not_set"]] == [str(b["_id"])] and pv["not_set"][0]["plan_covered"] is True and pv["lines"] == []
        r = await pay_commission(c, s)
        assert r.status_code == 400
        assert (await c.put(f"{API}/bookings/{b['_id']}/commission", json={"amount_paise": 0, "reason": "Plan wash — no commission"}, headers=s["mgr"]["h"])).status_code == 200
    d = await fb.doc(db, str(b["_id"]))
    assert d["commission_state"] == "accrued" and d["commission_plan_wash_paise"] == 0


# ------------------------------------------------------------------ salary invoices


async def _salary_captain(c, s, **terms):
    await make_payroll(c, s, "salary", salary_monthly_paise=3_000_000, weekly_off_days=[6], effective_from=last_month() + "-01", **terms)


async def test_salary_invoice_is_per_month_part_paid_then_balance_then_refused(db):
    s = await rig(db)
    month = last_month()
    async with h.client() as c:
        await _salary_captain(c, s, petrol_per_day_paise=1000)
        days = [d for d in __import__("app.services.payroll_salary", fromlist=["month_days"]).month_days(month) if d.weekday() != 6]
        for d in days[:10]:
            assert (await c.post(f"{API}/captains/{s['cap']['id']}/attendance", json={"date": d.isoformat(), "status": "present", "note": "Marked"}, headers=s["mgr"]["h"])).status_code == 200
        months = (await c.get(f"{API}/captains/{s['cap']['id']}/salary-months", headers=s["mgr"]["h"])).json()["data"]
        assert months["applicable"] is True and months["default_month"] == month
        pv = (await preview(c, s, kind="salary", month=month)).json()["data"]
        payable = pv["gross_paise"]
        assert payable > 0 and pv["status"] == "unpaid" and pv["finalized"] is False and pv["due_paise"] == payable
        assert [l["type"] for l in pv["lines"]] == ["salary", "petrol"] and pv["lines"][0]["present_days"] == 10
        # part payment: needs a reason (it differs from the balance)
        half = payable // 2
        assert (await c.post(f"{API}/captains/{s['cap']['id']}/pay-invoice", json=pay_body(kind="salary", month=month, amount_paise=half), headers=s["mgr"]["h"])).status_code == 400
        r1 = await c.post(f"{API}/captains/{s['cap']['id']}/pay-invoice", json=pay_body(kind="salary", month=month, amount_paise=half, reason="Part payment now"), headers=s["mgr"]["h"])
        assert r1.status_code == 200, r1.text
        i1 = r1.json()["data"]
        assert i1["month"] == month and i1["gross_paise"] == payable and i1["paid_paise"] == half and i1["adjustment_paise"] == half - payable
        # month got finalized by the invoice, so dues = payable − paid
        assert await dues(db, s["cap"]["id"]) == payable - half
        pv2 = (await preview(c, s, kind="salary", month=month)).json()["data"]
        assert pv2["status"] == "part_paid" and pv2["previously_paid_paise"] == half and pv2["due_paise"] == payable - half and pv2["finalized"] is True
        # the balance, in full → paid
        r2 = await c.post(f"{API}/captains/{s['cap']['id']}/pay-invoice", json=pay_body(kind="salary", month=month, expected_gross_paise=payable - half), headers=s["mgr"]["h"])
        assert r2.status_code == 200 and r2.json()["data"]["paid_paise"] == payable - half and r2.json()["data"]["adjustment_paise"] == 0
        # never invoiced twice in full
        r3 = await c.post(f"{API}/captains/{s['cap']['id']}/pay-invoice", json=pay_body(kind="salary", month=month), headers=s["mgr"]["h"])
        assert r3.status_code == 400 and "already paid in full" in r3.json()["message"]
        assert (await preview(c, s, kind="salary", month=month)).json()["data"]["status"] == "paid"
        assert await dues(db, s["cap"]["id"]) == 0
        # voiding the second invoice makes the month part-paid again
        assert (await c.post(f"{API}/invoices/{r2.json()['data']['id']}/void", json={"reason": "Bounced transfer"}, headers=s["mgr"]["h"])).status_code == 200
        assert (await preview(c, s, kind="salary", month=month)).json()["data"]["status"] == "part_paid"
        # overpaying posts the excess as a bonus (reason needed) so dues stay 0
        r4 = await c.post(f"{API}/captains/{s['cap']['id']}/pay-invoice", json=pay_body(kind="salary", month=month, amount_paise=payable - half + 10000, reason="Festival bonus"), headers=s["mgr"]["h"])
        assert r4.status_code == 200 and r4.json()["data"]["adjustment_paise"] == 10000
        assert await dues(db, s["cap"]["id"]) == 0
        assert (await reconcile(c, s))["ok"] is True
        # attendance edited after the month was paid → the invoice keeps its numbers, the month shows the difference
        assert (await c.post(f"{API}/captains/{s['cap']['id']}/attendance", json={"date": days[11].isoformat(), "status": "present", "note": "Forgot to mark"}, headers=s["mgr"]["h"])).status_code == 200
        pv3 = (await preview(c, s, kind="salary", month=month)).json()["data"]
        assert pv3["stale"] is True and pv3["gross_paise"] == payable
        assert (await c.post(f"{API}/captains/{s['cap']['id']}/months/{month}/finalize", json={}, headers=s["mgr"]["h"])).status_code == 200
        pv4 = (await preview(c, s, kind="salary", month=month)).json()["data"]
        assert pv4["gross_paise"] > payable and pv4["stale"] is False


async def test_salary_pay_validation_and_scope(db):
    s = await rig(db)
    other = await second_center(db)
    async with h.client() as c:
        await _salary_captain(c, s)
        url = f"{API}/captains/{s['cap']['id']}/pay-invoice"
        for bad in (
            pay_body(kind="salary"), pay_body(kind="salary", month="2099-01"), pay_body(kind="commission"), pay_body(kind="advance"),
            pay_body(kind="salary", month=last_month(), cash_offset_paise=5), pay_body(kind="bogus"),
        ):
            r = await c.post(url, json=bad, headers=s["mgr"]["h"])
            assert r.status_code in (400, 422), (bad, r.text)
        # nothing to pay in an empty month (no attendance, no jobs)
        r = await c.post(url, json=pay_body(kind="salary", month=last_month()), headers=s["mgr"]["h"])
        assert r.status_code == 400
        # other center's manager, the captain themselves, a customer
        for who, code in ((other["mgr"], 403), (s["cap"], 403), (s["cu"], 403)):
            assert (await c.post(url, json=pay_body(kind="advance", amount_paise=1000), headers=who["h"])).status_code == code
        assert (await c.get(f"{API}/captains/{s['cap']['id']}/pay-preview", params={"kind": "salary", "month": last_month()}, headers=other["mgr"]["h"])).status_code == 403
    assert await db.payroll_invoices.count_documents({"captain_id": s["cap"]["id"]}) == 0


# ------------------------------------------------------------------ reads, scope, captain view


async def test_invoice_lists_and_detail_are_scoped_and_the_captain_sees_only_their_own(db):
    s = await rig(db)
    other = await second_center(db)
    async with h.client() as c:
        mine = (await c.post(f"{API}/captains/{s['cap']['id']}/pay-invoice", json=pay_body(kind="advance", amount_paise=5000), headers=s["mgr"]["h"])).json()["data"]
        theirs = (await c.post(f"{API}/captains/{other['cap']['id']}/pay-invoice", json=pay_body(kind="other", amount_paise=7000), headers=other["mgr"]["h"])).json()["data"]
        lst = (await c.get(f"{API}/invoices", headers=s["mgr"]["h"])).json()
        assert [i["id"] for i in lst["data"]] == [mine["id"]] and lst["totals"]["paid_paise"] == 5000 and "lines" not in lst["data"][0]
        both = (await c.get(f"{API}/invoices", params={"page_size": 100}, headers=s["admin"]["h"])).json()
        assert {mine["id"], theirs["id"]} <= {i["id"] for i in both["data"]}
        f = (await c.get(f"{API}/invoices", params={"center_id": other["center_id"], "kind": "other"}, headers=s["admin"]["h"])).json()
        assert [i["id"] for i in f["data"]] == [theirs["id"]]
        assert (await c.get(f"{API}/invoices", params={"captain_id": s["cap"]["id"], "from": "2000-01-01", "to": "2000-01-02"}, headers=s["admin"]["h"])).json()["data"] == []
        # manager A can't open or void manager B's invoice
        assert (await c.get(f"{API}/invoices/{theirs['id']}", headers=s["mgr"]["h"])).status_code == 403
        assert (await c.post(f"{API}/invoices/{theirs['id']}/void", json={"reason": "Not mine"}, headers=s["mgr"]["h"])).status_code == 403
        # the captain: own list + own detail, never another's, never void; no finance id
        mi = (await c.get(f"{API}/me/invoices", headers=s["cap"]["h"])).json()
        assert [i["number"] for i in mi["data"]] == [mine["number"]] and "finance_entry_id" not in mi["data"][0]
        assert (await c.get(f"{API}/invoices/{mine['id']}", headers=s["cap"]["h"])).status_code == 200
        assert "finance_entry_id" not in (await c.get(f"{API}/invoices/{mine['id']}", headers=s["cap"]["h"])).json()["data"]
        assert (await c.get(f"{API}/invoices/{theirs['id']}", headers=s["cap"]["h"])).status_code == 404
        assert (await c.post(f"{API}/invoices/{mine['id']}/void", json={"reason": "I want it back"}, headers=s["cap"]["h"])).status_code == 403
        assert (await c.get(f"{API}/invoices", headers=s["cap"]["h"])).status_code == 403
        assert (await c.get(f"{API}/invoices/{mine['id']}", headers=s["cu"]["h"])).status_code == 403
        assert (await c.get(f"{API}/reconcile", headers=s["mgr"]["h"])).status_code == 403
        assert (await c.get(f"{API}/invoices/not-an-id", headers=s["admin"]["h"])).status_code == 404


async def test_my_pay_shows_paid_invoices_and_each_commission_row_paid_or_unpaid(db):
    s = await rig(db)
    async with h.client() as c:
        await make_payroll(c, s, "commission", commission={"mode": "percent", "percent": 40})
        b1, b2 = await jobs(db, s, 2)
        r = await pay_commission(c, s, booking_ids=[str(b1["_id"])])
        assert r.status_code == 200
        me = (await c.get(f"{API}/me", headers=s["cap"]["h"])).json()["data"]
        states = {row["booking_id"]: row for row in me["commissions"]}
        assert states[str(b1["_id"])]["payment_state"] == "paid" and states[str(b1["_id"])]["invoice_numbers"] == [r.json()["data"]["number"]]
        assert states[str(b2["_id"])]["payment_state"] == "unpaid" and states[str(b2["_id"])]["invoice_numbers"] == []
        assert [i["number"] for i in me["invoices"]] == [r.json()["data"]["number"]]
        assert me["dues_paise"] == states[str(b2["_id"])]["amount_paise"]
        entry = next(e for e in me["entries"] if e["kind"] == "payout")
        assert entry["invoice_number"] == r.json()["data"]["number"]
    note = await db.notifications.find_one({"user_id": s["cap"]["id"], "message": {"$regex": "Invoice BLS-PAY-"}})
    assert note is not None


async def test_franchise_managed_center_still_gets_an_invoice_but_no_finance_row(db):
    s = await rig(db)
    await db.service_centers.update_one({"_id": ObjectId(s["center_id"])}, {"$set": {"operation": {"type": "franchise", "captain_pay_managed_by": "franchise"}}})
    async with h.client() as c:
        await make_payroll(c, s, "commission", commission={"mode": "percent", "percent": 40})
        await jobs(db, s, 1)
        r = await pay_commission(c, s)
        assert r.status_code == 200 and r.json()["data"]["finance_impact"] is False
        assert await finance_total(db, s) == 0
        assert (await reconcile(c, s))["ok"] is True


async def test_cash_offset_on_an_invoice_nets_finance_and_reverses_on_void(db):
    s = await rig(db)
    async with h.client() as c:
        await make_payroll(c, s, "commission", commission={"mode": "percent", "percent": 40})
        b = (await jobs(db, s, 1))[0]
        amount = pct(bm.commission_base_paise(b), 40)
        owed = await db.captain_wallets.find_one({"captain_id": s["cap"]["id"]})
        await db.captain_wallets.update_one({"captain_id": s["cap"]["id"]}, {"$set": {"balance": -(amount / 100 / 2)}})
        offset = amount // 2
        r = await pay_commission(c, s, cash_offset_paise=offset)
        assert r.status_code == 200, r.text
        inv = r.json()["data"]
        assert inv["net_paise"] == amount - offset and inv["cash_offset_paise"] == offset
        assert await finance_total(db, s) == amount - offset
        assert (await reconcile(c, s))["ok"] is True
        assert (await c.post(f"{API}/invoices/{inv['id']}/void", json={"reason": "Undo"}, headers=s["mgr"]["h"])).status_code == 200
        assert await finance_total(db, s) == 0
        assert (await reconcile(c, s))["ok"] is True
    assert owed is not None


# ------------------------------------------------------------------ reconcile after a messy scenario


async def test_reconcile_stays_clean_after_a_messy_scenario_and_flags_real_drift(db):
    s = await rig(db)
    cap2 = await h.captain(db, s["center_id"], wallet=100.0)
    month = last_month()
    async with h.client() as c:
        await make_payroll(c, s, "commission", commission={"mode": "percent", "percent": 40})
        b1, b2, b3 = await jobs(db, s, 3)
        ids = [str(b["_id"]) for b in (b1, b2, b3)]
        a = (await pay_commission(c, s, booking_ids=ids[:2])).json()["data"]
        # edit one after paying, discount another, pay again (revised + new), void the first, advance, retry with the same keys
        await c.put(f"{API}/bookings/{ids[0]}/commission", json={"amount_paise": 9000, "reason": "Adjusted after the fact"}, headers=s["mgr"]["h"])
        k = key()
        b_ = await c.post(f"{API}/captains/{s['cap']['id']}/pay-invoice", json=pay_body(kind="commission", from_date=today(), to_date=today(), idempotency_key=k), headers=s["mgr"]["h"])
        assert b_.status_code == 200, b_.text
        again = await c.post(f"{API}/captains/{s['cap']['id']}/pay-invoice", json=pay_body(kind="commission", from_date=today(), to_date=today(), idempotency_key=k), headers=s["mgr"]["h"])
        assert again.json()["data"]["duplicate"] is True and again.json()["data"]["number"] == b_.json()["data"]["number"]
        assert (await c.post(f"{API}/invoices/{a['id']}/void", json={"reason": "Cheque bounced"}, headers=s["mgr"]["h"])).status_code == 200
        assert (await c.post(f"{API}/captains/{s['cap']['id']}/pay-invoice", json=pay_body(kind="advance", amount_paise=20000, cash_offset_paise=0), headers=s["mgr"]["h"])).status_code == 200
        assert (await c.post(f"{API}/captains/{s['cap']['id']}/adjustments", json={"kind": "bonus", "amount_paise": 3000, "note": "Good month", "idempotency_key": key()}, headers=s["mgr"]["h"])).status_code == 200
        # a second captain on salary, a month part-paid then voided
        await db.users.update_one({"_id": ObjectId(cap2["id"])}, {"$set": {"pay": {
            "basis": "salary", "salary_monthly_paise": 2_400_000, "weekly_off_days": [6], "paid_leaves_per_month": 0, "petrol_per_day_paise": 0,
            "extra_day_on_off": False, "commission": {"mode": "percent", "percent": 0}, "deposit_expected_paise": 0, "kit": {"provided": False, "items": []},
            "pay_history": [], "start_date": month + "-01",
        }}})
        from app.services.payroll_salary import month_days

        for d in [d for d in month_days(month) if d.weekday() != 6][:6]:
            await c.post(f"{API}/captains/{cap2['id']}/attendance", json={"date": d.isoformat(), "status": "present", "note": "Marked"}, headers=s["mgr"]["h"])
        sal = await c.post(f"{API}/captains/{cap2['id']}/pay-invoice", json=pay_body(kind="salary", month=month, amount_paise=10000, reason="Part payment"), headers=s["mgr"]["h"])
        assert sal.status_code == 200, sal.text
        sal2 = await c.post(f"{API}/captains/{cap2['id']}/pay-invoice", json=pay_body(kind="salary", month=month), headers=s["mgr"]["h"])
        assert sal2.status_code == 200
        assert (await c.post(f"{API}/invoices/{sal.json()['data']['id']}/void", json={"reason": "Wrong captain"}, headers=s["mgr"]["h"])).status_code == 200
        rec = await reconcile(c, s)
        assert rec["ok"] is True and rec["mismatches"] == [] and rec["captains_checked"] == 2 and rec["invoices_checked"] >= 5, rec
        # real drift is caught: a Finance row edited behind the invoices' back, and a stray settled amount
        await db.finance_entries.update_one({"source": "payroll", "service_center_id": s["center_id"], "is_deleted": False}, {"$inc": {"amount_paise": 1}})
        await db.bookings.update_one({"_id": ObjectId(ids[2])}, {"$set": {f"commission_settled_by.{s['cap']['id']}": 77}})
        bad = await reconcile(c, s)
        checks = {m["check"] for m in bad["mismatches"]}
        assert bad["ok"] is False and "Finance debits = paid invoices (net of cash offset)" in checks and "settled commission" in checks, bad


# ------------------------------------------------------------------ weekly off and leave


async def test_default_weekly_off_is_saturday_and_sunday_with_no_paid_leave_and_old_terms_still_read(db):
    from app.services import payroll_terms as pt

    assert pt.off_days_of({}) == [5, 6] and pt.blank_pay()["weekly_off_days"] == [5, 6] and pt.blank_pay()["paid_leaves_per_month"] == 0
    assert pt.off_days_of({"weekly_off": 6}) == [6] and pt.off_days_of({"weekly_off": 6, "weekly_off_days": [0]}) == [0]
    s = await rig(db)
    async with h.client() as c:
        data = await make_payroll(c, s, "salary", salary_monthly_paise=3_000_000)
        assert data["pay"]["weekly_off_days"] == [5, 6] and data["pay"]["paid_leaves_per_month"] == 0
        # a captain stored the old way keeps Sunday-only until edited, and the edit migrates the field
        await db.users.update_one({"_id": ObjectId(s["cap"]["id"])}, {"$set": {"pay.weekly_off": 6, "pay.pay_history": []}, "$unset": {"pay.weekly_off_days": ""}})
        det = (await c.get(f"{API}/captains/{s['cap']['id']}", headers=s["mgr"]["h"])).json()["data"]
        assert det["pay"]["weekly_off_days"] == [6]
        r = await set_pay(c, s["mgr"]["h"], s["cap"]["id"], weekly_off_days=[1, 5, 5], reason="Tuesday + Saturday off")
        assert r.status_code == 200, r.text
        assert r.json()["data"]["pay"]["weekly_off_days"] == [1, 5]
        stored = await db.users.find_one({"_id": ObjectId(s["cap"]["id"])})
        assert "weekly_off" not in stored["pay"] and stored["pay"]["weekly_off_days"] == [1, 5]
        # the old single-day field is still accepted, and bad weekdays are not
        assert (await set_pay(c, s["mgr"]["h"], s["cap"]["id"], weekly_off=2)).json()["data"]["pay"]["weekly_off_days"] == [2]
        for bad in ([7], [-1], list(range(7)), ["x"]):
            assert (await set_pay(c, s["mgr"]["h"], s["cap"]["id"], weekly_off_days=bad)).status_code == 422


async def test_leave_is_refused_on_a_weekly_off_day_for_attendance_and_leave_requests(db):
    from datetime import date, timedelta

    s = await rig(db)
    async with h.client() as c:
        await make_payroll(c, s, "salary", salary_monthly_paise=3_000_000, effective_from=(date.fromisoformat(today()) - timedelta(days=40)).isoformat())
        day = date.fromisoformat(today()) - timedelta(days=1)
        while day.weekday() != 5:
            day -= timedelta(days=1)
        r = await c.post(f"{API}/captains/{s['cap']['id']}/attendance", json={"date": day.isoformat(), "status": "leave", "note": "Sick"}, headers=s["mgr"]["h"])
        assert r.status_code == 400 and "Saturday is a weekly off" in r.json()["message"]
        monday = day - timedelta(days=4)  # a working day: leave is fine
        assert (await c.post(f"{API}/captains/{s['cap']['id']}/attendance", json={"date": monday.isoformat(), "status": "leave", "note": "Sick"}, headers=s["mgr"]["h"])).status_code == 200
        # present on a weekly off isn't a leave: fine
        assert (await c.post(f"{API}/captains/{s['cap']['id']}/attendance", json={"date": day.isoformat(), "status": "present", "note": "Came in"}, headers=s["mgr"]["h"])).status_code == 200
        # leave requests: can't start or end on a weekly off, may span one
        future_sat = date.fromisoformat(today()) + timedelta(days=1)
        while future_sat.weekday() != 5:
            future_sat += timedelta(days=1)
        lr = "/api/v1/leave-requests"
        probe = await c.post(lr, json={"start_date": future_sat.isoformat(), "end_date": future_sat.isoformat(), "reason": "Family"}, headers=s["cap"]["h"])
        assert probe.status_code == 400 and "weekly off" in probe.json()["message"]
        friday, monday2 = future_sat - timedelta(days=1), future_sat + timedelta(days=2)
        ok = await c.post(lr, json={"start_date": friday.isoformat(), "end_date": monday2.isoformat(), "reason": "Wedding"}, headers=s["cap"]["h"])
        assert ok.status_code == 200, ok.text
        # a commission captain has no weekly off to protect
        back = (date.fromisoformat(today()) - timedelta(days=40)).isoformat()
        await set_pay(c, s["mgr"]["h"], s["cap"]["id"], basis="commission", commission={"mode": "percent", "percent": 10}, effective_from=back)
        assert (await c.post(f"{API}/captains/{s['cap']['id']}/attendance", json={"date": day.isoformat(), "status": "leave", "note": "Sick"}, headers=s["mgr"]["h"])).status_code == 200


async def test_salary_maths_use_the_weekly_off_set(db):
    from app.services.payroll_salary import compute_month, month_days, working_days_in_month

    days = month_days("2026-10")  # 31 days: 5 Sat, 4 Sun... (Oct 2026: Sat 3,10,17,24,31 / Sun 4,11,18,25)
    assert working_days_in_month(days, [5, 6]) == 22 and working_days_in_month(days, 6) == 27 and working_days_in_month(days, (5, 6)) == 22
    user = {"_id": ObjectId(), "pay": {"basis": "salary", "salary_monthly_paise": 2_200_000, "pay_history": [], "start_date": "2026-01-01"}}
    att = {d.isoformat(): {"attendance_date": d.isoformat(), "status": "present"} for d in days if d.weekday() < 5}
    r = compute_month(user, "2026-10", "2026-11-23", att, set(), set(), set())
    assert r["expected_working_days"] == 22 and r["computed_paise"] == 2_200_000 and r["absent_days"] == 0
    assert {d["status"] for d in r["days"] if date_of(d).weekday() >= 5} == {"weekly_off"}


def date_of(row):
    from datetime import date

    return date.fromisoformat(row["date"])
