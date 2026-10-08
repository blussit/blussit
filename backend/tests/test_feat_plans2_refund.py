"""PLANS-2 (founder 2026-10-07): refund ONE car of a paid custom plan.

Refundable = the car's unused washes at its per-wash price after its
discount share (whole rupees, never above its paid share minus washes
used); staff may refund less, never more. The car's pass is cancelled (its
washes zeroed), its claim released, the car marked `refunded` on the cart
and the customer wallet credited ONCE (key cp-refund:{cart}:{car}). A pass
with a live booking is refused until those are cancelled. A needs_review
cart's skipped car (no pass) refunds its whole share. Local Mongo only."""
import asyncio
import math
from datetime import datetime, timedelta, timezone

import pytest
from bson import ObjectId

from app.core.exceptions import AppException, BadRequestException
from app.schemas.custom_plan_schema import CustomPlanRenewRequest
from app.schemas.subscription_schema import AssignSubscriptionRequest
from app.services.custom_plan_service import CustomPlanService
from app.services.subscription_service import PASS_SCHEDULED, UserSubscriptionService
from app.utils.timezone import now_ist

from tests.factories import make_subscription_plan
from tests.plans2_factories import (  # noqa: F401 — fixtures
    _tidy,
    activated_cash,
    gateway,
    link_order,
    make_cart,
    mgr,
    paid,
    pass_of,
    rig,
    vehicle_id_of,
)
from tests.society_factories import auth, client

pytestmark = pytest.mark.asyncio


def _expected(car: dict, remaining: dict) -> int:
    factor = float(car["amount"]) / float(car["price"])
    value = sum(int(remaining.get(i["service_id"], 0)) * float(i["unit_price"]) * factor for i in car["items"])
    return int(math.floor(value + 1e-6))


async def _wallet(db, customer: str) -> float:
    w = await db.customer_wallets.find_one({"customer_id": customer})
    return round(float((w or {}).get("balance") or 0), 2)


async def test_refund_value_effect_and_messages(db, rig, gateway):
    cart = await activated_cash(db, rig, discount=101)
    service = CustomPlanService(db)
    sub = await pass_of(db, cart["id"], rig["car_a"])
    star = await db.services.find_one({"_id": ObjectId(rig["svc"]["star-wash"])})
    subs = UserSubscriptionService(db)
    await subs.commit_consumption(str(sub["_id"]), await subs.plan_consumption(str(sub["_id"]), rig["car_a"], [star], rig["customer"]))
    sub = await db.user_subscriptions.find_one({"_id": sub["_id"]})
    car = next(c for c in cart["cars"] if c["vehicle_id"] == rig["car_a"])
    expected = _expected(car, sub["remaining_by_service"])
    used = float(car["amount"]) - expected
    assert 0 < expected < car["amount"] and used > 0
    # Staff see the most they can refund before they type.
    staff_view = await service.view(await service.get(cart["id"]))
    assert next(c for c in staff_view["cars"] if c["vehicle_id"] == rig["car_a"])["refundable_amount"] == expected
    # More than that is refused; nothing moved.
    with pytest.raises(BadRequestException, match=f"₹{expected}"):
        await service.refund_car(cart["id"], rig["car_a"], amount=expected + 1, reason="Moving city", **mgr(rig))
    assert await _wallet(db, rig["customer"]) == 0
    result = await service.refund_car(cart["id"], "0", amount=None, reason="Moving city", **mgr(rig))
    assert result["refund"]["amount"] == expected and not result["already"]
    raw = await db.user_subscriptions.find_one({"_id": sub["_id"]})
    assert raw["status"] == "cancelled" and raw["remaining_service_count"] == 0
    assert all(v == 0 for v in raw["remaining_by_service"].values())
    assert not await db.pass_claims.find_one({"_id": f"vehicle:{rig['customer']}:{rig['car_a']}"})
    view = result["custom_plan"]
    row = next(c for c in view["cars"] if c["vehicle_id"] == rig["car_a"])
    assert row["status"] == "refunded" and row["refund"]["amount"] == expected and row["refund"]["reason"] == "Moving city"
    assert row["refund"]["by"] == rig["manager"] and row["refund"]["at"]
    assert view["status"] == "active"  # the other car still runs
    # Wallet: one credit, the right key and note.
    assert await _wallet(db, rig["customer"]) == expected
    entry = await db.customer_wallet_ledger.find_one({"key": f"cp-refund:{cart['id']}:{rig['car_a']}"})
    plate = car["registration_number"]
    assert entry and entry["amount"] == expected and entry["kind"] == "refund" and entry["booking_id"] is None
    assert entry["note"] == f"Custom plan refund — {plate}"
    # The customer is told (wallet_credited); the car is free for a new plan.
    assert await db.notifications.find_one({"user_id": rig["customer"], "title": "Wallet Credited"})
    assert not await subs._active_pass_for_vehicle(rig["customer"], rig["car_a"])
    # Booking the cancelled pass is refused.
    with pytest.raises(BadRequestException):
        await subs.plan_consumption(str(sub["_id"]), rig["car_a"], [star], rig["customer"])


async def test_lower_amount_is_allowed(db, rig, gateway):
    cart = await activated_cash(db, rig)
    car_b = await vehicle_id_of(db, rig, rig["plate_b"])
    result = await CustomPlanService(db).refund_car(cart["id"], car_b, amount=10, reason="Goodwill", **mgr(rig))
    assert result["refund"]["amount"] == 10 and await _wallet(db, rig["customer"]) == 10


async def test_refund_double_tap_credits_once(db, rig, gateway):
    cart = await activated_cash(db, rig)
    car = cart["cars"][0]

    async def tap():
        try:
            return await CustomPlanService(db).refund_car(cart["id"], rig["car_a"], amount=None, reason="Double tap", **mgr(rig))
        except AppException as exc:
            return exc

    results = await asyncio.gather(*[tap() for _ in range(4)])
    ok = [r for r in results if isinstance(r, dict)]
    assert ok, results
    assert len([r for r in ok if not r["already"]]) == 1
    assert await db.customer_wallet_ledger.count_documents({"customer_id": rig["customer"]}) == 1
    assert await _wallet(db, rig["customer"]) == math.floor(car["amount"])
    # Once more, later: still once.
    again = await CustomPlanService(db).refund_car(cart["id"], "0", amount=None, reason="Again", **mgr(rig))
    assert again["already"] and await _wallet(db, rig["customer"]) == math.floor(car["amount"])


async def test_refund_refused_while_a_booking_uses_the_pass(db, rig, gateway):
    cart = await activated_cash(db, rig)
    sub = await pass_of(db, cart["id"], rig["car_a"])
    when = datetime.now(timezone.utc) + timedelta(days=2)
    await db.bookings.insert_one({"customer_id": rig["customer"], "service_center_id": rig["center"], "status": "pending", "is_deleted": False,
                                  "booking_number": "BKP2LIVE01", "subscription_id": str(sub["_id"]), "scheduled_date": when,
                                  "created_at": datetime.now(timezone.utc)})
    with pytest.raises(BadRequestException, match="BKP2LIVE01"):
        await CustomPlanService(db).refund_car(cart["id"], rig["car_a"], amount=None, reason="x", **mgr(rig))
    assert (await db.user_subscriptions.find_one({"_id": sub["_id"]}))["status"] == "active"
    assert await _wallet(db, rig["customer"]) == 0


async def test_refund_vs_booking_the_last_wash(db, rig, gateway):
    """A booking spending the last wash and a refund of the same car race:
    never both — either the booking holds the wash (refund refused) or the
    refund wins (the booking's spend fails and it is rolled back)."""
    one = [{"vehicle_id": rig["car_a"], "items": [{"service_id": rig["svc"]["star-wash"], "count": 1}]}]
    star = await db.services.find_one({"_id": ObjectId(rig["svc"]["star-wash"])})
    for attempt in range(6):
        cart = await activated_cash(db, rig, car_list=one)
        sub = await pass_of(db, cart["id"], rig["car_a"])
        sid = str(sub["_id"])
        plan = await UserSubscriptionService(db).plan_consumption(sid, rig["car_a"], [star], rig["customer"])

        async def booking():
            row = await db.bookings.insert_one({"customer_id": rig["customer"], "service_center_id": rig["center"], "status": "pending",
                                                "is_deleted": False, "booking_number": f"BKRACE{attempt}", "subscription_id": sid,
                                                "created_at": datetime.now(timezone.utc)})
            try:
                await UserSubscriptionService(db).commit_consumption(sid, plan)
                return True
            except BadRequestException:
                await db.bookings.delete_one({"_id": row.inserted_id})
                return False

        async def refund():
            try:
                await CustomPlanService(db).refund_car(cart["id"], rig["car_a"], amount=None, reason="race", **mgr(rig))
                return True
            except BadRequestException:
                return False

        booked, refunded = await asyncio.gather(booking(), refund()) if attempt % 2 else (lambda r: (r[1], r[0]))(await asyncio.gather(refund(), booking()))
        assert booked != refunded, (attempt, booked, refunded)
        raw = await db.user_subscriptions.find_one({"_id": sub["_id"]})
        if refunded:
            assert raw["status"] == "cancelled" and raw["remaining_service_count"] == 0
            assert not await db.bookings.find_one({"booking_number": f"BKRACE{attempt}"})
        else:
            assert raw["remaining_service_count"] == 0 and raw["status"] != "cancelled"
            await db.bookings.update_many({"subscription_id": sid}, {"$set": {"status": "completed"}})
        # Free the car for the next round.
        await db.user_subscriptions.update_one({"_id": sub["_id"]}, {"$set": {"status": "cancelled"}})
        await db.pass_claims.delete_many({"subscription_id": sid})


async def test_needs_review_skipped_car_refunds_its_whole_share(db, rig, gateway):
    service = CustomPlanService(db)
    cart = await make_cart(db, rig, discount=50)
    order = await paid(db, await link_order(db, rig, cart))
    plan_id = await make_subscription_plan(db, vehicle_types=[rig["hatch"]], included_service_ids=[rig["svc"]["star-wash"]], total_service_count=2)
    await UserSubscriptionService(db).assign(AssignSubscriptionRequest(
        customer_id=rig["customer"], plan_id=plan_id, vehicle_id=rig["car_a"], service_id=rig["svc"]["star-wash"],
    ), actor_center_id=rig["center"])
    assert not (await service.activate_from_payment(order))["ok"]
    view = await service.view(await service.get(cart["id"]))
    skipped = next(c for c in view["cars"] if c["vehicle_id"] == rig["car_a"])
    assert view["status"] == "needs_review" and skipped["status"] == "skipped"
    assert skipped["refundable_amount"] == math.floor(skipped["amount"])
    result = await service.refund_car(cart["id"], rig["car_a"], amount=None, reason="Had a monthly pass", **mgr(rig))
    assert result["refund"]["amount"] == math.floor(skipped["amount"])
    after = result["custom_plan"]
    assert after["status"] == "active" and after["review"]["resolved"] is True
    # The monthly pass on that car is untouched.
    assert await db.user_subscriptions.count_documents({"vehicle_id": rig["car_a"], "status": "active"}) == 1


async def test_refunding_a_scheduled_renewal_gives_the_car_back_to_the_old_pass(db, rig, gateway):
    service = CustomPlanService(db)
    old = await activated_cash(db, rig)
    renewal = await service.renew(old["id"], CustomPlanRenewRequest(), **mgr(rig))
    await service.mark_cash_paid(renewal["id"], expected_revision=1, note=None, **mgr(rig))
    new_a = await pass_of(db, renewal["id"], rig["car_a"])
    old_a = await pass_of(db, old["id"], rig["car_a"])
    assert new_a["status"] == PASS_SCHEDULED
    car = next(c for c in renewal["cars"] if c["vehicle_id"] == rig["car_a"])
    result = await service.refund_car(renewal["id"], rig["car_a"], amount=None, reason="Not renewing this car", **mgr(rig))
    assert result["refund"]["amount"] == math.floor(car["amount"])  # nothing used yet
    claim = await db.pass_claims.find_one({"_id": f"vehicle:{rig['customer']}:{rig['car_a']}"})
    assert claim and claim["subscription_id"] == str(old_a["_id"])
    old_a = await db.user_subscriptions.find_one({"_id": old_a["_id"]})
    assert old_a["status"] == "active" and not old_a.get("renewed_by_subscription_id") and not old_a.get("renewed_by_custom_plan_id")


async def test_refund_access_audit_and_bad_refs_over_http(db, rig, gateway):
    cart = await activated_cash(db, rig)
    path = f"/api/v1/subscriptions/custom-plans/{cart['id']}/cars/{rig['car_a']}/refund"
    async with client() as http:
        r = await http.post(path, json={"reason": "Sold it"}, headers=auth(rig["customer"], "customer"))
        assert r.status_code == 403, r.text
        r = await http.post(path, json={"reason": "Sold it"}, headers=auth(rig["other_manager"], "manager", rig["other"]))
        assert r.status_code == 403, r.text
        r = await http.post(path, json={}, headers=auth(rig["manager"], "manager", rig["center"]))
        assert r.status_code == 422, r.text  # a reason is required
        r = await http.post(f"/api/v1/subscriptions/custom-plans/{cart['id']}/cars/7/refund", json={"reason": "Sold it"},
                            headers=auth(rig["manager"], "manager", rig["center"]))
        assert r.status_code == 404, r.text
        r = await http.post(path, json={"reason": "Sold the car", "amount": 5}, headers=auth(rig["admin"], "admin"))
        assert r.status_code == 200, r.text
        assert r.json()["data"]["refund"]["amount"] == 5
    audit = await db.audit_logs.find_one({"action": "REFUND_CUSTOM_PLAN_CAR", "target_id": cart["id"]})
    assert audit and audit["details"]["amount"] == 5 and audit["details"]["vehicle_id"] == rig["car_a"]
    # Unpaid carts have nothing to refund.
    unpaid = await make_cart(db, rig, car_list=[{"registration_number": rig["plate_c"], "vehicle_type": rig["hatch"],
                                                  "items": [{"service_id": rig["svc"]["star-wash"], "count": 1}]}])
    with pytest.raises(BadRequestException):
        await CustomPlanService(db).refund_car(unpaid["id"], "0", amount=None, reason="x", **mgr(rig))
    assert now_ist()
