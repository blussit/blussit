"""§7.15: mark commission paid / unpaid (no money moves) and cash received from a
captain (wallet + per-booking trail + receipts + undo), and the per-booking view."""
import pytest
from bson import ObjectId

from app.services import booking_money as bm
from tests import test_fix_core_helpers as h
from tests.payroll_helpers import API, complete_job, dues, key, ledger, make_payroll, rig, second_center, wallet
from tests.test_payroll_invoices import jobs, pay_body, pay_commission, preview, reconcile, today

pytestmark = pytest.mark.asyncio


async def commissions(c, s):
    r = await c.get(f"{API}/captains/{s['cap']['id']}/commissions", headers=s["mgr"]["h"])
    assert r.status_code == 200, r.text
    return {row["booking_number"]: row for row in r.json()["data"]}


async def hold_cash(db, s, bookings, amounts):
    """The captain took `amounts` (rupees) in cash for these bookings; his wallet shows the debt."""
    for b, amt in zip(bookings, amounts):
        await db.bookings.update_one({"_id": b["_id"]}, {"$set": {"captain_cash_collected": float(amt), "captain_wallet_posted": 0.0, "captain_wallet_captain_id": s["cap"]["id"]}})
    await db.captain_wallets.update_one({"captain_id": s["cap"]["id"]}, {"$set": {"balance": -float(sum(amounts))}})


# ------------------------------------------------------------------ mark paid / unpaid


async def test_a_booking_paid_by_a_plain_payment_can_be_marked_paid_without_paying_again(db):
    s = await rig(db)
    async with h.client() as c:
        await make_payroll(c, s, "commission", commission={"mode": "percent", "percent": 40})
        b1, b2 = await jobs(db, s, 2)
        rows = await commissions(c, s)
        assert {r["payment_state"] for r in rows.values()} == {"unpaid"}
        total = sum(r["amount_paise"] for r in rows.values())
        # The manager paid it as a plain advance — dues drop, the bookings stay "Unpaid".
        r = await c.post(f"{API}/captains/{s['cap']['id']}/payments", json={"kind": "advance", "method": "cash", "amount_paise": total, "idempotency_key": key()}, headers=s["mgr"]["h"])
        assert r.status_code == 200, r.text
        assert await dues(db, s["cap"]["id"]) == 0
        assert {r["payment_state"] for r in (await commissions(c, s)).values()} == {"unpaid"}
        # The pay screen warns instead of quietly offering to pay twice.
        pv = (await preview(c, s, kind="commission", from_date=today(), to_date=today())).json()["data"]
        assert any("nothing due" in w for w in pv["warnings"])

        ledger_before = len(await ledger(db, s["cap"]["id"]))
        r = await c.post(f"{API}/captains/{s['cap']['id']}/commissions/mark-paid", json={"booking_ids": [str(b1["_id"]), str(b2["_id"])], "reason": "Paid by advance"}, headers=s["mgr"]["h"])
        assert r.status_code == 200, r.text
        assert r.json()["data"]["count"] == 2 and r.json()["data"]["amount_paise"] == total
        rows = await commissions(c, s)
        assert {r["payment_state"] for r in rows.values()} == {"paid"} and all(r["marked_paise"] == r["amount_paise"] for r in rows.values())
        assert len(await ledger(db, s["cap"]["id"])) == ledger_before and await dues(db, s["cap"]["id"]) == 0  # no money moved
        assert (await c.post(f"{API}/captains/{s['cap']['id']}/pay-invoice", json=pay_body(kind="commission", from_date=today(), to_date=today()), headers=s["mgr"]["h"])).status_code == 400
        assert (await reconcile(c, s))["ok"] is True
        # Twice is harmless.
        again = (await c.post(f"{API}/captains/{s['cap']['id']}/commissions/mark-paid", json={"month": today()[:7]}, headers=s["mgr"]["h"])).json()["data"]
        assert again["count"] == 0
        # Undo — only the by-hand part.
        r = await c.post(f"{API}/captains/{s['cap']['id']}/commissions/mark-unpaid", json={"booking_ids": [str(b1["_id"])], "reason": "Marked by mistake"}, headers=s["mgr"]["h"])
        assert r.status_code == 200 and r.json()["data"]["count"] == 1
        rows = await commissions(c, s)
        assert rows[b1["booking_number"]]["payment_state"] == "unpaid" and rows[b2["booking_number"]]["payment_state"] == "paid"
        assert (await reconcile(c, s))["ok"] is True


async def test_mark_unpaid_leaves_money_paid_by_an_invoice_alone(db):
    s = await rig(db)
    async with h.client() as c:
        await make_payroll(c, s, "commission", commission={"mode": "percent", "percent": 40})
        b = (await jobs(db, s, 1))[0]
        assert (await pay_commission(c, s)).status_code == 200
        r = await c.post(f"{API}/captains/{s['cap']['id']}/commissions/mark-unpaid", json={"booking_ids": [str(b["_id"])], "reason": "Trying"}, headers=s["mgr"]["h"])
        assert r.status_code == 200 and r.json()["data"]["count"] == 0
        assert (await commissions(c, s))[b["booking_number"]]["payment_state"] == "paid"
        assert (await reconcile(c, s))["ok"] is True


async def test_marking_is_center_scoped_and_validated(db):
    s = await rig(db)
    other = await second_center(db)
    async with h.client() as c:
        await make_payroll(c, s, "commission", commission={"mode": "percent", "percent": 40})
        b = (await jobs(db, s, 1))[0]
        url = f"{API}/captains/{s['cap']['id']}/commissions/mark-paid"
        assert (await c.post(url, json={"booking_ids": [str(b["_id"])]}, headers=other["mgr"]["h"])).status_code == 403
        assert (await c.post(url, json={}, headers=s["mgr"]["h"])).status_code == 422
        assert (await c.post(url, json={"booking_ids": [str(b["_id"])]}, headers=s["cu"]["h"])).status_code == 403
        assert (await c.post(url, json={"booking_ids": [str(b["_id"])]}, headers=s["admin"]["h"])).status_code == 200
        assert (await c.post(f"{API}/captains/{s['cap']['id']}/commissions/mark-unpaid", json={"booking_ids": [str(b["_id"])], "reason": "x"}, headers=s["mgr"]["h"])).status_code == 422


# ------------------------------------------------------------------ cash received from a captain


async def test_cash_received_goes_oldest_first_lifts_the_wallet_and_can_be_undone(db):
    s = await rig(db)
    async with h.client() as c:
        await make_payroll(c, s, "commission", commission={"mode": "percent", "percent": 40})
        b1, b2 = await jobs(db, s, 2)
        await hold_cash(db, s, [b1, b2], [300, 200])
        url = f"{API}/captains/{s['cap']['id']}"
        pos = (await c.get(f"{url}/cash", headers=s["mgr"]["h"])).json()["data"]
        assert pos["held_paise"] == 50000 and len(pos["bookings"]) == 2

        k = key()
        r = await c.post(f"{url}/cash-received", json={"amount_paise": 40000, "note": "Evening handover", "idempotency_key": k}, headers=s["mgr"]["h"])
        assert r.status_code == 200, r.text
        rec = r.json()["data"]
        assert [a["amount_paise"] for a in rec["allocations"]] == [30000, 10000]
        assert await wallet(db, s["cap"]["id"]) == -100.0
        d1, d2 = await db.bookings.find_one({"_id": b1["_id"]}), await db.bookings.find_one({"_id": b2["_id"]})
        assert d1["captain_cash_returned"] == 300.0 and d2["captain_cash_returned"] == 100.0
        assert (await c.get(f"{url}/cash", headers=s["mgr"]["h"])).json()["data"]["held_paise"] == 10000
        # The same tap again lands once.
        dup = await c.post(f"{url}/cash-received", json={"amount_paise": 40000, "idempotency_key": k}, headers=s["mgr"]["h"])
        assert dup.json()["data"]["duplicate"] is True and await wallet(db, s["cap"]["id"]) == -100.0
        # More than he holds is refused and changes nothing.
        too = await c.post(f"{url}/cash-received", json={"amount_paise": 10001, "idempotency_key": key()}, headers=s["mgr"]["h"])
        assert too.status_code == 400 and await wallet(db, s["cap"]["id"]) == -100.0
        # Undo puts it all back.
        assert (await c.post(f"/api/v1/payroll/cash-receipts/{rec['id']}/void", json={"reason": "Counted wrong"}, headers=s["mgr"]["h"])).status_code == 200
        assert await wallet(db, s["cap"]["id"]) == -500.0
        assert (await c.get(f"{url}/cash", headers=s["mgr"]["h"])).json()["data"]["held_paise"] == 50000
        again = await c.post(f"/api/v1/payroll/cash-receipts/{rec['id']}/void", json={"reason": "Again"}, headers=s["mgr"]["h"])
        assert again.json()["data"]["duplicate"] is True and await wallet(db, s["cap"]["id"]) == -500.0


async def test_cash_received_for_one_booking_only_and_scoping(db):
    s = await rig(db)
    other = await second_center(db)
    async with h.client() as c:
        await make_payroll(c, s, "commission", commission={"mode": "percent", "percent": 40})
        b1, b2 = await jobs(db, s, 2)
        await hold_cash(db, s, [b1, b2], [300, 200])
        url = f"{API}/captains/{s['cap']['id']}/cash-received"
        assert (await c.post(url, json={"amount_paise": 25000, "booking_id": str(b2["_id"]), "idempotency_key": key()}, headers=s["mgr"]["h"])).status_code == 400
        assert (await c.post(url, json={"amount_paise": 20000, "booking_id": str(b2["_id"]), "idempotency_key": key()}, headers=s["mgr"]["h"])).status_code == 200
        assert (await db.bookings.find_one({"_id": b1["_id"]})).get("captain_cash_returned") in (None, 0)
        assert (await c.post(url, json={"amount_paise": 100, "idempotency_key": key()}, headers=other["mgr"]["h"])).status_code == 403
        assert (await c.post(url, json={"amount_paise": 100, "idempotency_key": key()}, headers=s["cu"]["h"])).status_code == 403
        assert (await c.post(url, json={"amount_paise": 0, "idempotency_key": key()}, headers=s["mgr"]["h"])).status_code == 422
        assert (await c.post(url, json={"amount_paise": 100, "booking_id": str(ObjectId()), "idempotency_key": key()}, headers=s["mgr"]["h"])).status_code == 404


async def test_a_cash_offset_against_pay_hands_the_cash_in_and_a_void_gives_it_back(db):
    s = await rig(db)
    async with h.client() as c:
        await make_payroll(c, s, "commission", commission={"mode": "percent", "percent": 40})
        b = (await jobs(db, s, 1))[0]
        await hold_cash(db, s, [b], [200])
        amount = bm.paise(bm.commission_base_paise(b) / 100 * 0.4)
        r = await pay_commission(c, s, cash_offset_paise=10000)
        assert r.status_code == 200, r.text
        inv = r.json()["data"]
        assert (await db.bookings.find_one({"_id": b["_id"]}))["captain_cash_returned"] == 100.0
        assert await wallet(db, s["cap"]["id"]) == -100.0
        assert (await c.get(f"{API}/captains/{s['cap']['id']}/cash", headers=s["mgr"]["h"])).json()["data"]["held_paise"] == 10000
        assert (await c.post(f"{API}/invoices/{inv['id']}/void", json={"reason": "Undo"}, headers=s["mgr"]["h"])).status_code == 200
        assert (await db.bookings.find_one({"_id": b["_id"]}))["captain_cash_returned"] == 0.0
        assert await wallet(db, s["cap"]["id"]) == -200.0
    assert amount > 0


# ------------------------------------------------------------------ the booking's own view


async def test_a_bookings_captain_money_view_shows_commission_cash_and_adjustments(db):
    s = await rig(db)
    other = await second_center(db)
    async with h.client() as c:
        await make_payroll(c, s, "commission", commission={"mode": "percent", "percent": 40})
        b = (await jobs(db, s, 1))[0]
        await hold_cash(db, s, [b], [150])
        url = f"{API}/bookings/{b['_id']}/captain-money"
        v = (await c.get(url, headers=s["mgr"]["h"])).json()["data"]
        assert v["captain_name"] and v["basis"] == "commission"
        assert v["commission"]["payment_state"] == "unpaid" and v["cash"]["held_paise"] == 15000
        r = await c.post(f"{API}/captains/{s['cap']['id']}/adjustments", json={"kind": "bonus", "amount_paise": 5000, "note": "Extra heavy job", "booking_id": str(b["_id"]), "idempotency_key": key()}, headers=s["mgr"]["h"])
        assert r.status_code == 200, r.text
        v = (await c.get(url, headers=s["mgr"]["h"])).json()["data"]
        assert [a["amount_paise"] for a in v["adjustments"]] == [5000]
        assert (await c.get(url, headers=other["mgr"]["h"])).status_code == 403
        assert (await c.get(url, headers=s["cu"]["h"])).status_code == 403
        assert (await c.get(f"{API}/bookings/{ObjectId()}/captain-money", headers=s["mgr"]["h"])).status_code == 404


# ------------------------------------------------------------------ found in the browser pass


async def test_a_commission_edited_down_after_it_was_marked_paid_reads_paid_not_part_paid(db):
    s = await rig(db)
    async with h.client() as c:
        await make_payroll(c, s, "commission", commission={"mode": "percent", "percent": 40})
        b = (await jobs(db, s, 1))[0]
        bid = str(b["_id"])
        assert (await c.put(f"{API}/bookings/{bid}/commission", json={"amount_paise": 6000, "reason": "Heavy job"}, headers=s["mgr"]["h"])).status_code == 200
        assert (await c.post(f"{API}/captains/{s['cap']['id']}/commissions/mark-paid", json={"booking_ids": [bid]}, headers=s["mgr"]["h"])).status_code == 200
        assert (await c.put(f"{API}/bookings/{bid}/commission", json={"amount_paise": 2000, "reason": "Corrected"}, headers=s["mgr"]["h"])).status_code == 200
        row = (await commissions(c, s))[b["booking_number"]]
        assert row["payment_state"] == "paid" and row["amount_paise"] == 2000


async def test_the_same_cash_receipt_sent_twice_at_once_lands_once_and_neither_call_errors(db):
    import asyncio

    s = await rig(db)
    async with h.client() as c:
        await make_payroll(c, s, "commission", commission={"mode": "percent", "percent": 40})
        b = (await jobs(db, s, 1))[0]
        await hold_cash(db, s, [b], [100])
        body = {"amount_paise": 10000, "idempotency_key": key()}
        url = f"{API}/captains/{s['cap']['id']}/cash-received"
        a, d = await asyncio.gather(c.post(url, json=body, headers=s["mgr"]["h"]), c.post(url, json=body, headers=s["mgr"]["h"]))
        assert sorted([a.status_code, d.status_code]) == [200, 200], (a.text, d.text)
        assert await db.captain_cash_receipts.count_documents({"captain_id": s["cap"]["id"]}) == 1
        assert await wallet(db, s["cap"]["id"]) == 0.0
