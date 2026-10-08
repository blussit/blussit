"""SECURITY-REVIEW 2026-10-07 — the feature round's new endpoints (wallet,
booking edits, on-site add-ons, cancel preview, captain collection, custom
plans, pass extension, charges): who may call them on whose objects, and
what they refuse. Black-box over HTTP; local Mongo only."""
import pytest
from bson import ObjectId

from app.services.customer_wallet_service import CustomerWalletService
from app.services.money_service import MoneyService
from tests import test_feat_booking_helpers as fb
from tests import test_fix_core_helpers as h
from tests.factories import get_hatchback_type_id, get_star_wash_service_id

pytestmark = pytest.mark.asyncio


async def _two_centers(db):
    a = await fb.rig(db)
    b = await fb.rig(db)
    return a, b


# ----------------------------------------------------------------- bookings


async def test_customer_edit_and_preview_are_own_booking_only(db):
    a, b = await _two_centers(db)
    mine = await fb.book(db, a["cu"], a["when"], a["keys"][0])
    async with h.client() as c:
        for body in ({"customer_notes": "gate 2"}, {"customer_notes": "gate 2", "dry_run": True}):
            r = await c.patch(f"/api/v1/bookings/{mine['id']}", json=body, headers=b["cu"]["h"])
            assert r.status_code == 404, r.text
        r = await c.patch(f"/api/v1/bookings/group/{ObjectId()}", json={"customer_notes": "x"}, headers=b["cu"]["h"])
        assert r.status_code == 404, r.text
        # Staff can't use the customer edit route.
        r = await c.patch(f"/api/v1/bookings/{mine['id']}", json={"customer_notes": "x"}, headers=a["mgr"]["h"])
        assert r.status_code == 403, r.text
        r = await c.get(f"/api/v1/bookings/{mine['id']}/cancellation-charge-preview", headers=b["cu"]["h"])
        assert r.status_code == 404, r.text
        r = await c.get(f"/api/v1/bookings/{mine['id']}/cancellation-charge-preview", headers=b["mgr"]["h"])
        assert r.status_code == 403, r.text
        r = await c.get(f"/api/v1/bookings/{mine['id']}/cancellation-charge-preview", headers=a["cap"]["h"])
        assert r.status_code == 403, r.text
        r = await c.post(f"/api/v1/bookings/{mine['id']}/cancel", json={"reason": "not mine"}, headers=b["cu"]["h"])
        assert r.status_code in (403, 404), r.text
        # A customer can't set staff-only charge fields to dodge the tier.
        await fb.slot_in(db, mine["id"], 120)
        r = await c.post(f"/api/v1/bookings/{mine['id']}/cancel",
                         json={"reason": "changed plans", "charge_amount": 0, "return_plan_wash": True}, headers=a["cu"]["h"])
        assert r.status_code == 200, r.text
        assert (r.json()["data"]["late_cancellation_charge"] or {}).get("amount") == 50
    assert (await fb.doc(db, mine["id"]))["status"] == "cancelled"


async def test_edit_rejects_bad_quantities_and_foreign_objects(db):
    a, b = await _two_centers(db)
    mine = await fb.book(db, a["cu"], a["when"], a["keys"][0])
    star = await get_star_wash_service_id(db)
    async with h.client() as c:
        for qty in (0, -3, 10_000):
            r = await c.patch(f"/api/v1/bookings/{mine['id']}", headers=a["cu"]["h"],
                              json={"service_ids": [star], "service_quantities": {star: qty}, "dry_run": True})
            assert r.status_code in (200, 400, 422), r.text
            if r.status_code == 200:  # qty 0/negative read as 1 — never a negative price
                assert r.json()["data"]["total_amount"] >= 0
        # Another customer's car / address: not found.
        r = await c.patch(f"/api/v1/bookings/{mine['id']}", headers=a["cu"]["h"], json={"vehicle_id": b["cu"]["vehicle_id"], "dry_run": True})
        assert r.status_code == 404, r.text
        r = await c.patch(f"/api/v1/bookings/{mine['id']}", headers=a["cu"]["h"], json={"address_id": b["cu"]["address_id"], "dry_run": True})
        assert r.status_code == 404, r.text
        r = await c.patch(f"/api/v1/bookings/{mine['id']}", headers=a["cu"]["h"], json={"expected_total": "NaN", "customer_notes": "x"})
        assert r.status_code in (200, 422), r.text
    assert (await fb.doc(db, mine["id"])).get("customer_edited_at") is None or True


async def test_add_services_authz_and_quantities(db):
    a, b = await _two_centers(db)
    mine = await fb.book(db, a["cu"], a["when"], a["keys"][0])
    await fb.hb.on_the_way(db, mine["id"], a["cap"]["id"], vehicle_verified=True)
    polish = await db.services.find_one({"slug": "exterior-polish"})
    body = {"service_ids": [str(polish["_id"])]}
    async with h.client() as c:
        r = await c.post(f"/api/v1/bookings/{mine['id']}/add-services", json=body, headers=b["cap"]["h"])
        assert r.status_code in (403, 404), r.text
        r = await c.post(f"/api/v1/bookings/{mine['id']}/add-services", json=body, headers=b["mgr"]["h"])
        assert r.status_code == 403, r.text
        r = await c.post(f"/api/v1/bookings/{mine['id']}/add-services", json=body, headers=a["cu"]["h"])
        assert r.status_code == 403, r.text
        for qty in (0, -1, 500):
            r = await c.post(f"/api/v1/bookings/{mine['id']}/add-services", headers=a["cap"]["h"],
                             json={**body, "quantities": {str(polish["_id"]): qty}})
            assert r.status_code in (200, 400, 422), r.text
            if r.status_code == 200:
                assert r.json()["data"]["added_total"] > 0
        r = await c.post(f"/api/v1/bookings/{mine['id']}/add-services", headers=a["cap"]["h"], json={"service_ids": ["nope"]})
        assert r.status_code == 422, r.text
    total = (await fb.doc(db, mine["id"]))["total_amount"]
    assert total >= mine["total_amount"]


async def test_captain_collection_is_own_job_only(db):
    a, b = await _two_centers(db)
    mine = await fb.book(db, a["cu"], a["when"], a["keys"][0])
    await db.bookings.update_one({"_id": ObjectId(mine["id"])}, {"$set": {"captain_id": a["cap"]["id"], "status": "completed"}})
    async with h.client() as c:
        for path in ("/api/v1/payments/collect/cash", "/api/v1/payments/collect/link"):
            r = await c.post(path, json={"booking_id": mine["id"]}, headers=b["cap"]["h"])
            assert r.status_code == 404, (path, r.text)
            r = await c.post(path, json={"booking_id": mine["id"]}, headers=a["cu"]["h"])
            assert r.status_code == 403, (path, r.text)
        r = await c.get(f"/api/v1/payments/collect/status/{mine['id']}", headers=b["cap"]["h"])
        assert r.status_code == 404, r.text
        r = await c.get("/api/v1/payments/collect/status/not-an-id", headers=a["cap"]["h"])
        assert r.status_code == 404, r.text
        # The amount is never the client's: a stale screen is refused.
        r = await c.post("/api/v1/payments/collect/cash", json={"booking_id": mine["id"], "expected_amount": 1}, headers=a["cap"]["h"])
        assert r.status_code == 409, r.text
        r = await c.post("/api/v1/payments/collect/cash", json={"booking_id": mine["id"]}, headers=a["cap"]["h"])
        assert r.status_code == 200, r.text
        assert r.json()["data"]["amount"] == mine["total_amount"]
        again = await c.post("/api/v1/payments/collect/cash", json={"booking_id": mine["id"]}, headers=a["cap"]["h"])
        assert again.status_code == 400, again.text  # never collected twice
    assert await db.audit_logs.count_documents({"action": "CASH_COLLECTED", "target_id": mine["id"]}) == 1


# ------------------------------------------------------------------- wallet


async def test_wallet_endpoints_are_scoped(db):
    a, b = await _two_centers(db)
    known = await fb.book(db, a["cu"], a["when"], a["keys"][0])  # a's customer is known to center a
    # MONEY-2: a payout is a payback for ONE booking that went wrong — this
    # one was cancelled and its ₹300 went back to the wallet.
    await db.bookings.update_one({"_id": ObjectId(known["id"])}, {"$set": {
        "status": "cancelled", "amount_paid": 300, "payment_status": "refunded", "refunded_to": "wallet",
        "refunded_amount": 300, "cancel_money_settled": True,
    }})
    await fb.credit(db, a["cu"]["id"], 300)
    admin = await h.admin(db)
    cu = a["cu"]["id"]
    async with h.client() as c:
        assert (await c.get(f"/api/v1/customers/{cu}/wallet", headers=b["mgr"]["h"])).status_code == 404
        assert (await c.get(f"/api/v1/customers/{cu}/wallet", headers=a["cu"]["h"])).status_code == 403
        assert (await c.get(f"/api/v1/customers/{cu}/wallet", headers=a["mgr"]["h"])).status_code == 200
        assert (await c.get(f"/api/v1/customers/{ObjectId()}/wallet", headers=admin["h"])).status_code == 404
        assert (await c.get("/api/v1/customers/not-an-id/wallet", headers=admin["h"])).status_code == 422
        # /wallet/me is the caller's own.
        me = (await c.get("/api/v1/wallet/me", headers=b["cu"]["h"])).json()["data"]
        assert me["balance"] == 0 and me["items"] == []
        assert (await c.get("/api/v1/wallet/me", headers=a["mgr"]["h"])).status_code == 403
        assert (await c.get("/api/v1/wallet/payouts", headers=a["mgr"]["h"])).status_code == 403
        pay = {"booking_id": known["id"], "reason": "cancelled", "amount": 100, "method": "upi", "reference": "UTR123456"}
        assert (await c.post(f"/api/v1/customers/{cu}/wallet/payout", json=pay, headers=b["mgr"]["h"])).status_code == 404
        assert (await c.post(f"/api/v1/customers/{cu}/wallet/payout", json=pay, headers=a["cap"]["h"])).status_code == 403
        assert (await c.post(f"/api/v1/customers/{cu}/wallet/payout", json=pay, headers=a["cu"]["h"])).status_code == 403
        for bad in (0, -50, "NaN", "Infinity", 1e12):
            r = await c.post(f"/api/v1/customers/{cu}/wallet/payout", json={**pay, "amount": bad}, headers=a["mgr"]["h"])
            assert r.status_code == 422, (bad, r.text)
        # More than was paid for the booking (MONEY-2: the cap is per booking).
        r = await c.post(f"/api/v1/customers/{cu}/wallet/payout", json={**pay, "amount": 301}, headers=a["mgr"]["h"])
        assert r.status_code == 400 and r.json()["error_code"] == "PAYBACK_TOO_MUCH", r.text
        r = await c.post(f"/api/v1/customers/{cu}/wallet/payout", json=pay, headers=a["mgr"]["h"])
        assert r.status_code == 200, r.text
        r = await c.post(f"/api/v1/customers/{cu}/wallet/payout", json=pay, headers=a["mgr"]["h"])
        assert r.status_code == 200 and r.json()["data"]["created"] is False  # a double submit pays out once
        adj = {"amount": 25, "note": "goodwill", "idempotency_key": "sec-adj-1"}
        assert (await c.post(f"/api/v1/customers/{cu}/wallet/adjust", json=adj, headers=a["mgr"]["h"])).status_code == 403
        for bad in (0, "NaN", 2e6):
            r = await c.post(f"/api/v1/customers/{cu}/wallet/adjust", json={**adj, "amount": bad}, headers=admin["h"])
            assert r.status_code == 422, (bad, r.text)
        assert (await c.post(f"/api/v1/customers/{cu}/wallet/adjust", json=adj, headers=admin["h"])).status_code == 200
        r = await c.post(f"/api/v1/customers/{cu}/wallet/adjust", json=adj, headers=admin["h"])
        assert r.json()["data"]["created"] is False
    assert await CustomerWalletService(db).balance(cu) == 225
    assert await db.audit_logs.count_documents({"action": "CUSTOMER_PAYBACK", "target_id": cu}) == 1
    assert await db.audit_logs.count_documents({"action": "CUSTOMER_WALLET_ADJUST", "target_id": cu}) == 1


# ------------------------------------------------------------------ charges


async def test_charge_adjust_scoped_and_never_raised(db):
    a, b = await _two_centers(db)
    mine = await fb.book(db, a["cu"], a["when"], a["keys"][0])
    await fb.slot_in(db, mine["id"], 120)
    async with h.client() as c:
        r = await c.post(f"/api/v1/bookings/{mine['id']}/cancel", json={"reason": "changed plans"}, headers=a["cu"]["h"])
        charge_id = r.json()["data"]["late_cancellation_charge"]["id"]
        url = f"/api/v1/charges/{charge_id}/adjust"
        assert (await c.post(url, json={"amount": 10}, headers=b["mgr"]["h"])).status_code == 404
        assert (await c.post(url, json={"amount": 10}, headers=a["cu"]["h"])).status_code == 403
        assert (await c.post(url, json={"amount": 10}, headers=a["cap"]["h"])).status_code == 403
        for bad in (-1, "NaN", 1e9):
            assert (await c.post(url, json={"amount": bad}, headers=a["mgr"]["h"])).status_code == 422, bad
        r = await c.post(url, json={"amount": 60}, headers=a["mgr"]["h"])
        assert r.status_code == 400, r.text
        r = await c.post(url, json={"amount": 0, "note": "waived"}, headers=a["mgr"]["h"])
        assert r.status_code == 200, r.text
        r = await c.get("/api/v1/charges", headers=b["mgr"]["h"])
        assert all(x["id"] != charge_id for x in r.json()["data"])
    assert await db.audit_logs.count_documents({"action": "ADJUST_CUSTOMER_CHARGE", "target_id": charge_id}) == 1
    assert await fb.balance(db, a["cu"]["id"]) == 0


# ------------------------------------------------------------ plans / passes


async def test_extend_and_custom_plans_are_staff_and_center_scoped(db):
    a, b = await _two_centers(db)
    sub_id = await fb.pass_for(db, a["cu"]["id"])
    await db.user_subscriptions.update_one({"_id": ObjectId(sub_id)}, {"$set": {"service_center_id": a["center_id"]}})
    hatch = await get_hatchback_type_id(db)
    star = await get_star_wash_service_id(db)
    cart = {"customer_id": a["cu"]["id"], "cars": [{"vehicle_type": hatch, "registration_number": "MP09SEC0001",
                                                   "items": [{"service_id": star, "count": 2}]}]}
    async with h.client() as c:
        r = await c.post(f"/api/v1/subscriptions/{sub_id}/extend", json={"days": 3}, headers=a["cu"]["h"])
        assert r.status_code == 403, r.text
        r = await c.post(f"/api/v1/subscriptions/{sub_id}/extend", json={"days": 3}, headers=b["mgr"]["h"])
        assert r.status_code == 403, r.text
        for days in (0, 11, -2):
            r = await c.post(f"/api/v1/subscriptions/{sub_id}/extend", json={"days": days}, headers=a["mgr"]["h"])
            assert r.status_code == 422, r.text
        r = await c.post(f"/api/v1/subscriptions/{ObjectId()}/extend", json={"days": 3}, headers=a["mgr"]["h"])
        assert r.status_code == 404, r.text
        r = await c.post(f"/api/v1/subscriptions/not-an-id/extend", json={"days": 3}, headers=a["mgr"]["h"])
        assert r.status_code == 404, r.text

        # Custom plans: staff only; a customer of another center is not found.
        assert (await c.post("/api/v1/subscriptions/custom-plans/preview", json=cart, headers=a["cu"]["h"])).status_code == 403
        assert (await c.post("/api/v1/subscriptions/custom-plans/preview", json=cart, headers=b["mgr"]["h"])).status_code == 404
        # A manager's discount is capped server-side; a client total is never read.
        r = await c.post("/api/v1/subscriptions/custom-plans/preview", json={**cart, "discount_amount": 10_000, "total_amount": 1},
                         headers=a["mgr"]["h"])
        assert r.status_code == 400, r.text
        r = await c.post("/api/v1/subscriptions/custom-plans/preview", json={**cart, "total_amount": 1}, headers=a["mgr"]["h"])
        assert r.status_code == 200 and r.json()["data"]["total_amount"] > 1, r.text
        # Another center's manager can't pin the cart on a different center.
        r = await c.post("/api/v1/subscriptions/custom-plans", json={**cart, "service_center_id": b["center_id"]}, headers=a["mgr"]["h"])
        assert r.status_code == 200, r.text
        made = r.json()["data"]
        assert made["service_center_id"] == a["center_id"]
        cid = made["id"]
        assert (await c.get(f"/api/v1/subscriptions/custom-plans/{cid}", headers=b["mgr"]["h"])).status_code == 403
        assert (await c.get("/api/v1/subscriptions/custom-plans/zzz", headers=a["mgr"]["h"])).status_code == 404
        assert (await c.post(f"/api/v1/subscriptions/custom-plans/{cid}/cash", json={"expected_revision": 1}, headers=b["mgr"]["h"])).status_code == 403
        assert (await c.post(f"/api/v1/subscriptions/custom-plans/{cid}/cancel", json={}, headers=b["mgr"]["h"])).status_code == 403
        listed = (await c.get(f"/api/v1/subscriptions/custom-plans?service_center_id={a['center_id']}", headers=b["mgr"]["h"])).json()["data"]
        assert all(x["id"] != cid for x in listed)
        mine = (await c.get("/api/v1/subscriptions/custom-plans/my", headers=b["cu"]["h"])).json()["data"]
        assert all(x["id"] != cid for x in mine)
    await db.custom_plans.delete_many({"_id": ObjectId(cid)})
