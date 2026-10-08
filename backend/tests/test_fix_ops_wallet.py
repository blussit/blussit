"""
OPS remediation (audit 2026-10-07) — captain wallet + personal data.

ADM-09  wallet adjust accepted a customer id or a garbage id (and minted a
        wallet for it): an existing captain is required, else 404.
ADM-10  a withdrawal could never reach "paid" after "approved": the review
        is now a real state machine — pending → approved (debits once) →
        paid, or pending → rejected, or approved → rejected (payout
        cancelled: the amount goes back). Every transition is a guarded
        claim on the current state.
DATA-1  managers saw full Aadhaar / PAN numbers: masked to the last 4 for
        managers (documents stay viewable for review); admins and the
        captain himself keep the full values.
"""
import asyncio

import pytest
from bson import ObjectId
from httpx import ASGITransport, AsyncClient

from tests.factories import make_captain, make_customer, make_manager, make_service_center, own_upload_url

pytestmark = pytest.mark.asyncio


def _auth(user_id: str, role: str, center_id: str | None = None) -> dict:
    from app.core.security import create_access_token

    return {"Authorization": f"Bearer {create_access_token(user_id, role, {'service_center_id': center_id, 'tv': 0})}"}


def _client():
    from app.main import app

    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


@pytest.fixture
async def rig(db, cleanup):
    ca = await make_service_center(db)
    cb = await make_service_center(db)
    mgr = await make_manager(db, ca)
    mgr_b = await make_manager(db, cb)
    cap = await make_captain(db, ca, wallet_balance=1000)
    cust = await make_customer(db)
    admin = await db.users.find_one({"role": "admin"})
    ids = [mgr, mgr_b, cap, cust]
    for coll in ("captain_wallets", "wallet_transactions", "withdrawal_requests"):
        cleanup.append((coll, {"captain_id": {"$in": [cap, cust, "not-an-id"]}}))
    cleanup.append(("audit_logs", {"target_id": {"$in": ids}}))
    cleanup.append(("users", {"_id": {"$in": [ObjectId(i) for i in ids]}}))
    cleanup.append(("service_centers", {"_id": {"$in": [ObjectId(ca), ObjectId(cb)]}}))
    return {
        "ca": ca, "cb": cb, "cap": cap, "cust": cust, "mgr": mgr,
        "ADM": _auth(str(admin["_id"]), "admin"), "MA": _auth(mgr, "manager", ca), "MB": _auth(mgr_b, "manager", cb),
        "CP": _auth(cap, "captain", ca),
    }


async def _balance(db, captain_id):
    return (await db.captain_wallets.find_one({"captain_id": captain_id}))["balance"]


# ---------------------------------------------------------------- ADM-09


async def test_wallet_adjust_requires_an_existing_captain(db, rig):
    async with _client() as c:
        on_customer = await c.post(f"/api/v1/wallet/captain/{rig['cust']}/adjust", headers=rig["ADM"], json={"amount": 5000, "description": "oops"})
        on_garbage = await c.post("/api/v1/wallet/captain/not-an-id/adjust", headers=rig["ADM"], json={"amount": 1, "description": "oops"})
        on_missing = await c.post(f"/api/v1/wallet/captain/{ObjectId()}/adjust", headers=rig["ADM"], json={"amount": 1, "description": "oops"})
        ok = await c.post(f"/api/v1/wallet/captain/{rig['cap']}/adjust", headers=rig["ADM"], json={"amount": -100, "description": "cash handed in"})
        by_manager = await c.post(f"/api/v1/wallet/captain/{rig['cap']}/adjust", headers=rig["MA"], json={"amount": 10, "description": "x"})
    assert on_customer.status_code == 404, on_customer.text
    assert on_garbage.status_code == 404, on_garbage.text
    assert on_missing.status_code == 404, on_missing.text
    assert await db.captain_wallets.find_one({"captain_id": rig["cust"]}) is None
    assert await db.captain_wallets.find_one({"captain_id": "not-an-id"}) is None
    assert ok.status_code == 200, ok.text
    assert await _balance(db, rig["cap"]) == 900
    assert by_manager.status_code == 403
    row = await db.audit_logs.find_one({"target_id": rig["cap"], "action": "ADJUST_WALLET"})
    assert row["details"]["balance_before"] == 1000 and row["details"]["balance_after"] == 900


# ---------------------------------------------------------------- ADM-10


async def _request(c, rig, amount=100):
    res = await c.post("/api/v1/wallet/withdrawals", headers=rig["CP"], json={"amount": amount})
    assert res.status_code == 200, res.text
    return res.json()["data"]["id"]


async def _review(c, rig, wid, status):
    return await c.put(f"/api/v1/wallet/withdrawals/{wid}/review", headers=rig["ADM"], json={"status": status})


async def test_withdrawal_goes_requested_approved_paid_and_debits_once(db, rig):
    async with _client() as c:
        wid = await _request(c, rig)
        straight_to_paid = await _review(c, rig, wid, "paid")
        assert straight_to_paid.status_code == 400 and "approve" in straight_to_paid.json()["message"].lower()
        assert await _balance(db, rig["cap"]) == 1000
        approved = await _review(c, rig, wid, "approved")
        assert approved.status_code == 200, approved.text
        assert await _balance(db, rig["cap"]) == 900
        again = await _review(c, rig, wid, "approved")
        assert again.status_code == 400 and "already been reviewed" in again.json()["message"]
        # The admin's "awaiting payout" queue lists it until it's paid.
        queue = await c.get("/api/v1/wallet/withdrawals/pending?status=approved", headers=rig["ADM"])
        assert wid in [w["id"] for w in queue.json()["data"]]
        pending_q = await c.get("/api/v1/wallet/withdrawals/pending", headers=rig["ADM"])
        assert wid not in [w["id"] for w in pending_q.json()["data"]]
        paid = await _review(c, rig, wid, "paid")
        assert paid.status_code == 200, paid.text
        assert paid.json()["data"]["status"] == "paid" and paid.json()["data"].get("paid_at")
        assert await _balance(db, rig["cap"]) == 900  # paying out moves no wallet money
        after_paid = await _review(c, rig, wid, "rejected")
        assert after_paid.status_code == 400
    debits = await db.wallet_transactions.count_documents({"captain_id": rig["cap"], "type": "debit"})
    assert debits == 1


async def test_rejecting_an_approved_withdrawal_returns_the_money(db, rig):
    async with _client() as c:
        wid = await _request(c, rig, 250)
        assert (await _review(c, rig, wid, "approved")).status_code == 200
        assert await _balance(db, rig["cap"]) == 750
        rej = await _review(c, rig, wid, "rejected")
        assert rej.status_code == 200, rej.text
        assert await _balance(db, rig["cap"]) == 1000
        assert (await _review(c, rig, wid, "paid")).status_code == 400
        # A plain pending → rejected moves nothing.
        wid2 = await _request(c, rig, 50)
        assert (await _review(c, rig, wid2, "rejected")).status_code == 200
    assert await _balance(db, rig["cap"]) == 1000


async def test_concurrent_reviews_take_exactly_one_transition(db, rig):
    async with _client() as c:
        wid = await _request(c, rig, 100)
        res = await asyncio.gather(*[_review(c, rig, wid, "approved") for _ in range(4)])
        assert sorted(r.status_code for r in res) == [200, 400, 400, 400]
        assert await _balance(db, rig["cap"]) == 900
        res = await asyncio.gather(_review(c, rig, wid, "paid"), _review(c, rig, wid, "paid"), _review(c, rig, wid, "rejected"))
        assert sorted(r.status_code for r in res) == [200, 400, 400]
    final = await db.withdrawal_requests.find_one({"_id": ObjectId(wid)})
    assert final["status"] in ("paid", "rejected")
    assert await _balance(db, rig["cap"]) == (900 if final["status"] == "paid" else 1000)


# ---------------------------------------------------------------- DATA-1


async def test_manager_sees_masked_kyc_numbers_admin_and_captain_full(db, rig):
    doc_url = own_upload_url("documents/ops-kyc.jpg")
    await db.users.update_one({"_id": ObjectId(rig["cap"])}, {"$set": {"captain_kyc": {
        "status": "submitted", "aadhaar_number": "123412345678", "pan_number": "ABCDE1234F",
        "aadhaar_doc_url": doc_url, "pan_doc_url": doc_url, "photo_url": own_upload_url("photos/ops.jpg"),
        "local_address": "x", "permanent_address": "y",
    }}})
    await db.captain_wallets.update_one({"captain_id": rig["cap"]}, {"$set": {"bank_account_number": "123456789012"}})
    async with _client() as c:
        mgr = (await c.get(f"/api/v1/staff/captains/{rig['cap']}/kyc", headers=rig["MA"])).json()["data"]
        adm = (await c.get(f"/api/v1/staff/captains/{rig['cap']}/kyc", headers=rig["ADM"])).json()["data"]
        own = (await c.get("/api/v1/staff/my-kyc", headers=rig["CP"])).json()["data"]
        reviewed = await c.post(f"/api/v1/staff/captains/{rig['cap']}/kyc/review", headers=rig["MA"], json={"status": "rejected", "note": "blurry"})
        foreign = await c.get(f"/api/v1/staff/captains/{rig['cap']}/kyc", headers=rig["MB"])
        wallet_mgr = (await c.get(f"/api/v1/wallet/captain/{rig['cap']}", headers=rig["MA"])).json()["data"]
        wallet_adm = (await c.get(f"/api/v1/wallet/captain/{rig['cap']}", headers=rig["ADM"])).json()["data"]
    assert mgr["aadhaar_number"].endswith("5678") and "1234123" not in mgr["aadhaar_number"]
    assert mgr["pan_number"].endswith("234F") and "ABCDE" not in mgr["pan_number"]
    assert mgr["aadhaar_doc_url"] == doc_url  # the document itself stays reviewable
    assert adm["aadhaar_number"] == "123412345678" and adm["pan_number"] == "ABCDE1234F"
    assert own["aadhaar_number"] == "123412345678"
    assert reviewed.status_code == 200, reviewed.text
    assert "1234123" not in reviewed.json()["data"]["aadhaar_number"]
    assert foreign.status_code == 403
    assert wallet_mgr["bank_account_number"].endswith("9012") and "12345678" not in wallet_mgr["bank_account_number"]
    assert wallet_adm["bank_account_number"] == "123456789012"
    # The stored packet is untouched by masking.
    stored = (await db.users.find_one({"_id": ObjectId(rig["cap"])}))["captain_kyc"]
    assert stored["aadhaar_number"] == "123412345678"
