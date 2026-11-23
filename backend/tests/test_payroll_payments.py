"""Payments, adjustments, cash offset, void, Finance integration (§7.13 D3,
D12–D14): one transaction, idempotent, locked Finance rows, franchise-managed
centers write nothing, alerts, notifications, CSV, isolation."""
import asyncio

import pytest
from bson import ObjectId

from app.services.finance_service import FinanceService, clear_cache
from tests import test_fix_core_helpers as h
from tests.payroll_helpers import API, dues, key, ledger, make_payroll, rig, second_center, wallet

pytestmark = pytest.mark.asyncio

FIN = "/api/v1/admin/finance"


def pay_body(amount=100000, **kw) -> dict:
    return {"amount_paise": amount, "kind": "salary", "method": "upi", "note": "October pay", "idempotency_key": kw.pop("idempotency_key", key()), **kw}


async def pay(c, s, **kw):
    return await c.post(f"{API}/captains/{s['cap']['id']}/payments", json=pay_body(**kw), headers=s["mgr"]["h"])


async def entries(db, s) -> list[dict]:
    return await db.finance_entries.find({"service_center_id": s["center_id"]}).sort("_id", 1).to_list(None)


async def test_payment_writes_ledger_and_a_locked_finance_debit_together(db):
    s = await rig(db)
    async with h.client() as c:
        await make_payroll(c, s, "salary", salary_monthly_paise=3000000)
        r = await pay(c, s, amount=250000, kind="salary", method="bank")
        assert r.status_code == 200, r.text
        data = r.json()["data"]
        assert data["amount_paise"] == -250000 and data["kind"] == "payout" and data["payment_kind"] == "salary"
        assert data["finance_impact"] is True and data["finance_entry_id"] and data["dues_paise"] == -250000
        rows = await entries(db, s)
        assert len(rows) == 1
        f = rows[0]
        assert f["type"] == "debit" and f["amount_paise"] == 250000 and f["category"] == "Salary" and f["locked"] is True
        assert f["source"] == "payroll" and f["source_id"] == data["id"] and f["service_center_id"] == s["center_id"]
        assert str(f["_id"]) == data["finance_entry_id"] and not f["is_deleted"]
        # the admin's Finance page: locked row can't be edited or voided there; filters and CSV carry center + source
        a = s["admin"]["h"]
        r = await c.patch(f"{FIN}/entries/{f['_id']}", json={"title": "Changed"}, headers=a)
        assert r.status_code == 400 and "Created by Salary" in r.json()["message"]
        r = await c.post(f"{FIN}/entries/{f['_id']}/void", json={"reason": "just remove it"}, headers=a)
        assert r.status_code == 400 and "Created by Salary" in r.json()["message"]
        listing = (await c.get(f"{FIN}/entries", params={"service_center_id": s["center_id"], "source": "payroll"}, headers=a)).json()["data"]
        assert [x["id"] for x in listing] == [str(f["_id"])] and listing[0]["locked"] is True and listing[0]["source"] == "payroll"
        assert (await c.get(f"{FIN}/entries", params={"service_center_id": "0" * 24}, headers=a)).json()["data"] == []
        csv = (await c.get(f"{FIN}/entries.csv", params={"source": "payroll", "service_center_id": s["center_id"]}, headers=a)).text
        assert "payroll" in csv and "Source" in csv.splitlines()[0]
        cats = (await c.get(f"{FIN}/categories", headers=a)).json()["data"]["suggested"]
        assert {"Salary", "Commission", "Petrol & Expenses", "Advance", "Captain Deposit"} <= set(cats)


async def test_the_same_idempotency_key_pays_once(db):
    s = await rig(db)
    k = key()
    async with h.client() as c:
        first = await pay(c, s, idempotency_key=k)
        again = await pay(c, s, idempotency_key=k)
        assert first.status_code == 200 and again.status_code == 200
        assert first.json()["data"]["id"] == again.json()["data"]["id"] and again.json()["data"]["duplicate"] is True
        # a key can't be reused by another captain (keys are per captain)
        racers = await asyncio.gather(*[pay(c, s, idempotency_key=key()) for _ in range(1)] + [pay(c, s, idempotency_key=k) for _ in range(4)])
        assert all(r.status_code == 200 for r in racers)
    assert len(await ledger(db, s["cap"]["id"], kind="payout")) == 2
    assert len(await entries(db, s)) == 2
    assert await dues(db, s["cap"]["id"]) == -200000


async def test_simultaneous_double_submit_of_a_new_key_lands_once(db):
    s = await rig(db)
    k = key()
    async with h.client() as c:
        results = await asyncio.gather(*[pay(c, s, idempotency_key=k) for _ in range(5)])
        assert all(r.status_code == 200 for r in results), [r.text for r in results if r.status_code != 200]
    assert len(await ledger(db, s["cap"]["id"], kind="payout")) == 1 and len(await entries(db, s)) == 1


async def test_kinds_map_to_finance_categories_and_advance_goes_negative(db):
    s = await rig(db)
    async with h.client() as c:
        for kind, cat in (("commission", "Commission"), ("petrol", "Petrol & Expenses"), ("advance", "Advance"), ("other", "Other")):
            r = await pay(c, s, kind=kind, amount=1000)
            assert r.status_code == 200
        rows = await entries(db, s)
        assert [x["category"] for x in rows] == ["Commission", "Petrol & Expenses", "Advance", "Other"]
        kinds = [x["kind"] for x in await ledger(db, s["cap"]["id"])]
        assert kinds == ["payout", "payout", "advance", "payout"]
        assert await dues(db, s["cap"]["id"]) == -4000  # an advance / recovery: dues can go negative
        st = (await c.get(f"{API}/captains/{s['cap']['id']}", headers=s["mgr"]["h"])).json()["data"]
        assert st["dues_paise"] == -4000


async def test_payment_validation(db):
    s = await rig(db)
    async with h.client() as c:
        for bad in (
            pay_body(amount=0), pay_body(amount=-5), pay_body(kind="bogus"), pay_body(method="cheque"),
            pay_body(idempotency_key="short"), pay_body(paid_on="2999-01-01"), pay_body(paid_on="2001-01-01"),
            pay_body(amount=1000, cash_offset_paise=2000), pay_body(amount=10 ** 12),
        ):
            r = await c.post(f"{API}/captains/{s['cap']['id']}/payments", json=bad, headers=s["mgr"]["h"])
            assert r.status_code in (400, 422), (bad, r.text)
    assert await ledger(db, s["cap"]["id"]) == []


async def test_void_reverses_the_ledger_and_the_finance_entry_once(db):
    s = await rig(db)
    async with h.client() as c:
        p = (await pay(c, s, amount=500000)).json()["data"]
        r = await c.post(f"{API}/payments/{p['id']}/void", json={"reason": "Wrong captain paid"}, headers=s["mgr"]["h"])
        assert r.status_code == 200, r.text
        assert r.json()["data"]["kind"] == "payout_void" and r.json()["data"]["amount_paise"] == 500000 and r.json()["data"]["voids"] == p["id"]
        assert await dues(db, s["cap"]["id"]) == 0
        f = (await entries(db, s))[0]
        assert f["is_deleted"] is True and "Wrong captain paid" in f["delete_reason"]
        # twice is a no-op
        r2 = await c.post(f"{API}/payments/{p['id']}/void", json={"reason": "Wrong captain paid"}, headers=s["mgr"]["h"])
        assert r2.status_code == 200 and r2.json()["data"]["duplicate"] is True and await dues(db, s["cap"]["id"]) == 0
        led = (await c.get(f"{API}/captains/{s['cap']['id']}/ledger", headers=s["mgr"]["h"])).json()["data"]
        by_id = {e["id"]: e for e in led}
        assert by_id[p["id"]]["voided"] is True
        # only payments can be voided, and another center's manager may not
        other = await second_center(db)
        p2 = (await pay(c, s, amount=1000)).json()["data"]
        assert (await c.post(f"{API}/payments/{p2['id']}/void", json={"reason": "not yours"}, headers=other["mgr"]["h"])).status_code == 403
        assert (await c.post(f"{API}/payments/{p2['id']}/void", json={"reason": "no"}, headers=s["mgr"]["h"])).status_code == 422
        adj = (await c.post(f"{API}/captains/{s['cap']['id']}/adjustments", json={"kind": "bonus", "amount_paise": 100, "note": "Diwali", "idempotency_key": key()}, headers=s["mgr"]["h"])).json()["data"]
        assert (await c.post(f"{API}/payments/{adj['id']}/void", json={"reason": "not a payment"}, headers=s["mgr"]["h"])).status_code == 404
    audit = await db.audit_logs.find_one({"action": "PAYROLL_PAYMENT_VOID", "target_id": p["id"]})
    assert audit and audit["service_center_id"] == s["center_id"]


async def test_cash_offset_credits_the_wallet_and_only_the_net_reaches_finance(db):
    s = await rig(db)
    await db.captain_wallets.update_one({"captain_id": s["cap"]["id"]}, {"$set": {"balance": -150.0}})  # owes ₹150 cash
    async with h.client() as c:
        # more than the cash he holds is refused
        r = await pay(c, s, amount=300000, cash_offset_paise=15001)
        assert r.status_code == 400 and "holds ₹150" in r.json()["message"]
        assert await wallet(db, s["cap"]["id"]) == -150.0 and await entries(db, s) == []
        r = await pay(c, s, amount=30000, cash_offset_paise=15000)
        assert r.status_code == 200, r.text
        assert await wallet(db, s["cap"]["id"]) == pytest.approx(0.0)
        f = (await entries(db, s))[0]
        assert f["amount_paise"] == 15000  # ₹300 owed − ₹150 cash he held = ₹150 handed over
        assert (await ledger(db, s["cap"]["id"], kind="payout"))[0]["amount_paise"] == -30000  # the dues are fully cleared
        # voiding takes the offset back out of the wallet
        pid = r.json()["data"]["id"]
        assert (await c.post(f"{API}/payments/{pid}/void", json={"reason": "Entered twice"}, headers=s["mgr"]["h"])).status_code == 200
        assert await wallet(db, s["cap"]["id"]) == pytest.approx(-150.0)
        detail = (await c.get(f"{API}/captains/{s['cap']['id']}", headers=s["mgr"]["h"])).json()["data"]
        assert detail["cash_held_paise"] == 15000


async def test_offset_equal_to_the_whole_payment_writes_no_finance_entry(db):
    s = await rig(db)
    await db.captain_wallets.update_one({"captain_id": s["cap"]["id"]}, {"$set": {"balance": -500.0}})
    async with h.client() as c:
        r = await pay(c, s, amount=20000, cash_offset_paise=20000)
        assert r.status_code == 200 and await entries(db, s) == [] and r.json()["data"]["finance_entry_id"] is None
        assert await wallet(db, s["cap"]["id"]) == pytest.approx(-300.0)


async def test_franchise_managed_center_pays_without_touching_finance(db):
    s = await rig(db)
    await db.service_centers.update_one(
        {"_id": ObjectId(s["center_id"])}, {"$set": {"operation": {"type": "franchise", "captain_pay_managed_by": "franchise"}}},
    )
    async with h.client() as c:
        r = await pay(c, s, amount=400000)
        assert r.status_code == 200 and r.json()["data"]["finance_impact"] is False and r.json()["data"]["finance_entry_id"] is None
        assert await entries(db, s) == []
        listing = (await c.get(f"{API}/captains", headers=s["mgr"]["h"])).json()["data"]
        assert listing["finance_impact"] is False and listing["rows"][0]["finance_impact"] is False
        # a void of it also leaves Finance alone
        assert (await c.post(f"{API}/payments/{r.json()['data']['id']}/void", json={"reason": "Wrong amount"}, headers=s["mgr"]["h"])).status_code == 200
        assert await entries(db, s) == []
        # Blussit-paid again after a switch: each payment keeps the flag it had
        await db.service_centers.update_one({"_id": ObjectId(s["center_id"])}, {"$set": {"operation.captain_pay_managed_by": "blussit"}})
        r2 = await pay(c, s, amount=1000)
        assert r2.json()["data"]["finance_impact"] is True and len(await entries(db, s)) == 1


async def test_finance_entry_failure_rolls_the_ledger_row_back(db, monkeypatch):
    s = await rig(db)

    async def boom(self, **kw):
        raise RuntimeError("finance is down")

    monkeypatch.setattr(FinanceService, "create_system_entry", boom)
    async with h.client() as c:
        r = await c.post(f"{API}/captains/{s['cap']['id']}/payments", json=pay_body(), headers=s["mgr"]["h"])
        assert r.status_code >= 500
    assert await ledger(db, s["cap"]["id"]) == [] and await entries(db, s) == []


async def test_system_entry_is_idempotent_on_source_and_voidable(db):
    s = await rig(db)
    svc = FinanceService(db)
    kw = dict(title="Test", type="credit", amount_paise=5000, category="Captain Deposit", source="test_src", source_id="abc",
              service_center_id=s["center_id"], done_by="Tester", actor_id="x", actor_role="admin")
    a = await svc.create_system_entry(**kw)
    b = await svc.create_system_entry(**kw)
    assert a["id"] == b["id"] and a["locked"] is True
    assert await db.finance_entries.count_documents({"source": "test_src", "source_id": "abc"}) == 1
    v = await svc.void_system_entry("test_src", "abc", "because", "x", "admin")
    assert v["is_deleted"] is True
    assert (await svc.void_system_entry("test_src", "abc", "again", "x", "admin"))["is_deleted"] is True
    assert await svc.void_system_entry("test_src", "missing", "n/a", "x", "admin") is None
    clear_cache()


async def test_large_payment_alerts_admins_without_whatsapp(db):
    s = await rig(db)
    async with h.client() as c:
        assert (await pay(c, s, amount=5_000_000)).status_code == 200  # exactly the threshold: no alert
        admins = [str(a["_id"]) for a in await db.users.find({"role": "admin"}).to_list(None)]
        assert await db.notifications.count_documents({"user_id": {"$in": admins}, "title": "Large captain payment"}) == 0
        assert (await pay(c, s, amount=5_000_001)).status_code == 200
        notes = await db.notifications.find({"user_id": admins[0], "title": "Large captain payment"}).to_list(None)
        assert len(notes) == 1 and "₹50,000.01" in notes[0]["message"]
    assert await db.whatsapp_queue.count_documents({"user_id": {"$in": admins}, "title": "Large captain payment"}) == 0


async def test_captain_is_told_about_a_payment_and_a_void(db):
    s = await rig(db)
    async with h.client() as c:
        p = (await pay(c, s, amount=123400, method="upi")).json()["data"]
        note = await db.notifications.find_one({"user_id": s["cap"]["id"], "title": "₹1,234 paid to you"})
        assert note and "UPI" in note["message"]
        await c.post(f"{API}/payments/{p['id']}/void", json={"reason": "Typed the wrong amount"}, headers=s["mgr"]["h"])
        assert await db.notifications.count_documents({"user_id": s["cap"]["id"], "title": "A payment was cancelled"}) == 1
    q = await db.whatsapp_queue.find_one({"user_id": s["cap"]["id"], "title": "₹1,234 paid to you"})
    assert q is not None  # the generic update goes out for a captain; no new template


async def test_bonus_deduction_allowance_adjustments_and_idempotency(db):
    s = await rig(db)
    async with h.client() as c:
        def adj(kind, amount, k=None, note="Reason given"):
            return c.post(f"{API}/captains/{s['cap']['id']}/adjustments", json={"kind": kind, "amount_paise": amount, "note": note, "idempotency_key": k or key()}, headers=s["mgr"]["h"])

        assert (await adj("bonus", 50000)).status_code == 200
        assert (await adj("allowance", 20000)).status_code == 200
        k = key()
        assert (await adj("deduction", 10000, k)).json()["data"]["amount_paise"] == -10000
        assert (await adj("deduction", 10000, k)).json()["data"]["duplicate"] is True
        assert await dues(db, s["cap"]["id"]) == 60000
        assert (await adj("bonus", 100, note="")).status_code == 422
        assert (await adj("bonus", 0)).status_code == 422
        assert (await adj("commission_cut", 100)).status_code == 400  # needs a booking
        assert (await c.post(f"{API}/captains/{s['cap']['id']}/adjustments", json={"kind": "bonus", "amount_paise": 5, "note": "abc", "idempotency_key": key()}, headers=s["cap"]["h"])).status_code == 403
    # adjustments never touch Finance (they are accruals, not money out)
    assert await entries(db, s) == []


async def test_deposit_and_kit_flow(db):
    s = await rig(db)
    async with h.client() as c:
        await make_payroll(c, s, "salary", salary_monthly_paise=1000000, deposit_expected_paise=500000,
                           kit={"provided": True, "items": [{"name": "Jet washer", "qty": 1, "serial": "JW-1", "value_paise": 600000}]})
        d = f"{API}/captains/{s['cap']['id']}/deposit"
        k = key()
        r = await c.post(d, json={"action": "received", "amount_paise": 500000, "method": "cash", "key": k}, headers=s["mgr"]["h"])
        assert r.status_code == 200 and r.json()["data"]["deposit_held_paise"] == 500000
        r = await c.post(d, json={"action": "received", "amount_paise": 500000, "method": "cash", "key": k}, headers=s["mgr"]["h"])
        assert r.json()["data"]["deposit_held_paise"] == 500000  # same key: once
        f = (await entries(db, s))[0]
        assert f["type"] == "credit" and f["category"] == "Captain Deposit" and f["amount_paise"] == 500000 and f["locked"] is True
        assert await dues(db, s["cap"]["id"]) == 0  # a deposit is not earnings or pay
        # refund more than held is refused; part refund debits Finance; forfeit releases the rest without a Finance entry
        r = await c.post(d, json={"action": "refund", "amount_paise": 500001, "key": key()}, headers=s["mgr"]["h"])
        assert r.status_code == 400
        r = await c.post(d, json={"action": "refund", "amount_paise": 200000, "key": key()}, headers=s["mgr"]["h"])
        assert r.json()["data"]["deposit_held_paise"] == 300000
        r = await c.post(d, json={"action": "forfeit", "amount_paise": 300000, "note": "Kit not returned", "key": key()}, headers=s["mgr"]["h"])
        assert r.json()["data"]["deposit_held_paise"] == 0
        rows = await entries(db, s)
        assert [(x["type"], x["amount_paise"]) for x in rows] == [("credit", 500000), ("debit", 200000)]
        # the kit goes back
        r = await c.post(f"{API}/captains/{s['cap']['id']}/kit/return", json={"note": "All items"}, headers=s["mgr"]["h"])
        assert r.status_code == 200 and r.json()["data"]["kit_returned"] is True and r.json()["data"]["pay"]["kit"]["returned_on"]
        again = await c.post(f"{API}/captains/{s['cap']['id']}/kit/return", json={}, headers=s["mgr"]["h"])
        assert again.status_code == 200
        # deposit expected can be changed
        r = await c.post(d, json={"action": "expect", "amount_paise": 700000, "key": key()}, headers=s["mgr"]["h"])
        assert r.json()["data"]["pay"]["deposit_expected_paise"] == 700000
        # a kit that was never issued can't be returned
        other = await h.captain(db, s["center_id"])
        assert (await c.post(f"{API}/captains/{other['id']}/kit/return", json={}, headers=s["mgr"]["h"])).status_code == 400
        # kit edit via PUT
        r = await c.put(f"{API}/captains/{other['id']}/kit", json={"provided": True, "items": [{"name": "Polisher", "qty": 2}]}, headers=s["mgr"]["h"])
        assert r.status_code == 200 and r.json()["data"]["kit_provided"] is True and r.json()["data"]["kit_returned"] is False


async def test_deposit_at_a_franchise_managed_center_writes_no_finance_entry(db):
    s = await rig(db)
    await db.service_centers.update_one({"_id": ObjectId(s["center_id"])}, {"$set": {"operation": {"type": "franchise", "captain_pay_managed_by": "franchise"}}})
    async with h.client() as c:
        r = await c.post(f"{API}/captains/{s['cap']['id']}/deposit", json={"action": "received", "amount_paise": 10000, "key": key()}, headers=s["mgr"]["h"])
        assert r.status_code == 200 and r.json()["data"]["deposit_held_paise"] == 10000
    assert await entries(db, s) == []


async def test_opening_deposit_at_creation_posts_the_ledger_and_finance_rows(db):
    s = await rig(db)
    from tests.payroll_helpers import phone

    async with h.client() as c:
        body = {"full_name": "Deposit Dev", "email": f"dep.{ObjectId()}@example.com", "phone": phone(), "password": "Passw0rd!x", "role": "captain",
                "pay": {"basis": "salary", "salary_monthly_paise": 1500000, "deposit_expected_paise": 300000, "deposit_received": True}}
        r = await c.post("/api/v1/auth/staff", json=body, headers=s["mgr"]["h"])
        assert r.status_code == 200, r.text
        cid = r.json()["data"]["id"]
        rows = await ledger(db, cid, kind="deposit_received")
        assert len(rows) == 1 and rows[0]["amount_paise"] == 300000
        assert len(await entries(db, s)) == 1


async def test_delete_and_suspend_are_blocked_until_settled_unless_admin_overrides(db):
    s = await rig(db)
    async with h.client() as c:
        await make_payroll(c, s, "salary", salary_monthly_paise=1000000)
        a = s["admin"]["h"]
        # a clean captain deletes fine (other captain)
        await db.captain_wallets.update_one({"captain_id": s["cap"]["id"]}, {"$set": {"balance": 0.0}})
        clean = await h.captain(db, s["center_id"], wallet=0.0)
        assert (await c.delete(f"/api/v1/users/{clean['id']}", headers=a)).status_code == 200
        # dues owed
        await c.post(f"{API}/captains/{s['cap']['id']}/adjustments", json={"kind": "bonus", "amount_paise": 40000, "note": "Owed pay", "idempotency_key": key()}, headers=s["mgr"]["h"])
        r = await c.delete(f"/api/v1/users/{s['cap']['id']}", headers=a)
        assert r.status_code == 400 and "₹400" in r.json()["message"] and "owed to them" in r.json()["message"]
        assert (await c.post(f"/api/v1/users/{s['cap']['id']}/suspend", headers=a)).status_code == 400
        # settle the dues; a deposit held and an unreturned kit still block
        await pay(c, s, amount=40000)
        await c.post(f"{API}/captains/{s['cap']['id']}/deposit", json={"action": "received", "amount_paise": 100000, "key": key()}, headers=s["mgr"]["h"])
        await c.put(f"{API}/captains/{s['cap']['id']}/kit", json={"provided": True, "items": [{"name": "Vac", "qty": 1}]}, headers=s["mgr"]["h"])
        r = await c.delete(f"/api/v1/users/{s['cap']['id']}", headers=a)
        assert r.status_code == 400 and "deposit of ₹1,000" in r.json()["message"] and "kit" in r.json()["message"]
        # recovery owed the other way also blocks
        await c.post(f"{API}/captains/{s['cap']['id']}/deposit", json={"action": "refund", "amount_paise": 100000, "key": key()}, headers=s["mgr"]["h"])
        await c.post(f"{API}/captains/{s['cap']['id']}/kit/return", json={}, headers=s["mgr"]["h"])
        assert (await c.post(f"/api/v1/users/{s['cap']['id']}/suspend", headers=a)).status_code == 200  # all clear
        await pay(c, s, amount=1000, kind="advance")
        r = await c.delete(f"/api/v1/users/{s['cap']['id']}", headers=a)
        assert r.status_code == 400 and "to recover from them" in r.json()["message"]
        # an admin overrides with a reason; a manager can't delete anyone at all
        assert (await c.delete(f"/api/v1/users/{s['cap']['id']}", headers=s["mgr"]["h"])).status_code == 403
        r = await c.delete(f"/api/v1/users/{s['cap']['id']}", params={"override_reason": "Left; settled outside the app"}, headers=a)
        assert r.status_code == 200
    audit = await db.audit_logs.find_one({"action": "DELETE_USER", "target_id": s["cap"]["id"]})
    assert audit["details"]["pay_override_reason"] == "Left; settled outside the app"


async def test_lists_summary_csv_and_isolation(db):
    s = await rig(db)
    other = await second_center(db)
    async with h.client() as c:
        await make_payroll(c, s, "salary", salary_monthly_paise=1000000)
        await pay(c, s, amount=2500)
        await c.post(f"{API}/captains/{s['cap']['id']}/adjustments", json={"kind": "bonus", "amount_paise": 10000, "note": "Good work", "idempotency_key": key()}, headers=s["mgr"]["h"])
        data = (await c.get(f"{API}/captains", headers=s["mgr"]["h"])).json()["data"]
        row = data["rows"][0]
        assert row["id"] == s["cap"]["id"] and row["earned_paise"] == 10000 and row["paid_paise"] == 2500 and row["dues_paise"] == 7500
        assert row["basis"] == "salary" and row["pay"]["salary_monthly_paise"] == 1000000 and row["legacy"] is False
        assert data["summary"]["earned_paise"] == 10000 and data["summary"]["dues_paise"] == 7500 and data["summary"]["active_captains"] == 1
        summary = (await c.get(f"{API}/summary", headers=s["mgr"]["h"])).json()["data"]
        assert summary["dues_paise"] == 7500
        legacy = (await c.get(f"{API}/captains", params={"basis": "legacy"}, headers=s["mgr"]["h"])).json()["data"]["rows"]
        assert legacy == []
        # CSV: a manager sees only theirs; the admin can scope to a center
        csv = (await c.get(f"{API}/export.csv", headers=s["mgr"]["h"]))
        assert csv.headers["content-type"].startswith("text/csv") and s["cap"]["id"] not in csv.text and "Test Captain" in csv.text
        assert other["cap"]["id"] not in csv.text
        admin_csv = (await c.get(f"{API}/export.csv", params={"center_id": other["center_id"]}, headers=s["admin"]["h"])).text
        assert "Test Captain" in admin_csv and "75.00" not in admin_csv
        # the ledger is paginated, newest first, and only for the right center
        led = (await c.get(f"{API}/captains/{s['cap']['id']}/ledger", params={"page_size": 1}, headers=s["mgr"]["h"])).json()
        assert led["meta"]["total"] == 2 and len(led["data"]) == 1 and led["data"][0]["kind"] == "bonus"
        for who, expect in (("cu", 403), ("cap", 403)):
            assert (await c.get(f"{API}/captains", headers=s[who]["h"])).status_code == expect
            assert (await c.get(f"{API}/export.csv", headers=s[who]["h"])).status_code == expect
        assert (await c.get(f"{API}/captains")).status_code == 401


async def test_captain_me_is_own_data_only(db):
    s = await rig(db)
    other = await second_center(db)
    async with h.client() as c:
        await make_payroll(c, s, "salary", salary_monthly_paise=1000000)
        await pay(c, s, amount=5000)
        r = await c.post(f"{API}/captains/{other['cap']['id']}/adjustments", json={"kind": "bonus", "amount_paise": 99999, "note": "Theirs", "idempotency_key": key()}, headers=other["mgr"]["h"])
        assert r.status_code == 200
        me = (await c.get(f"{API}/me", headers=s["cap"]["h"])).json()["data"]
        assert me["basis"] == "salary" and me["paid_paise"] == 5000 and me["dues_paise"] == -5000 and me["pay"]["salary_monthly_paise"] == 1000000
        assert all(e["captain_id"] == s["cap"]["id"] for e in me["entries"])
        other_me = (await c.get(f"{API}/me", headers=other["cap"]["h"])).json()["data"]
        assert other_me["basis"] == "legacy" and other_me["dues_paise"] == 99999 and other_me["paid_paise"] == 0
        st = (await c.get(f"{API}/me/statement", params={"month": me["month"]}, headers=s["cap"]["h"])).json()["data"]
        assert st["month"] == me["month"] and st["paid_paise"] == 5000
        # a captain can't read anyone else's through the staff routes, and managers/customers have no /me
        assert (await c.get(f"{API}/captains/{other['cap']['id']}", headers=s["cap"]["h"])).status_code == 403
        assert (await c.get(f"{API}/captains/{s['cap']['id']}/ledger", headers=s["cap"]["h"])).status_code == 403
        assert (await c.get(f"{API}/me", headers=s["mgr"]["h"])).status_code == 403
        assert (await c.get(f"{API}/me", headers=s["cu"]["h"])).status_code == 403
        assert (await c.get(f"{API}/me")).status_code == 401
        # the captain's profile shows their own pay terms only
        prof = (await c.get("/api/v1/auth/me", headers=s["cap"]["h"])).json()
        pay_block = (prof.get("data") or prof).get("pay")
        assert pay_block and pay_block["basis"] == "salary" and "pay_history" not in pay_block


async def test_booking_reads_hide_commission_from_customer_and_captain_but_show_staff(db):
    from tests.payroll_helpers import complete_job

    s = await rig(db)
    async with h.client() as c:
        await make_payroll(c, s, "commission", commission={"mode": "percent", "percent": 40})
        b = await complete_job(db, s)
        bid = str(b["_id"])
        staff = (await c.get(f"/api/v1/bookings/{bid}", headers=s["mgr"]["h"])).json()["data"]
        assert staff["commission_posted_paise"] > 0 and staff["commission_captain_id"] == s["cap"]["id"]
        for who in ("cu", "cap"):
            seen = (await c.get(f"/api/v1/bookings/{bid}", headers=s[who]["h"])).json()["data"]
            assert not [k for k in seen if k.startswith("commission_") or k.startswith("captain_pay_mode")], who
