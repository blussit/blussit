"""
Subscriptions, second pass (founder calls):

  1. ONE live copy of a plan at a time — buying the same plan again while
     the old one still has time (or visits) left is refused; upgrading is
     the supported move, and a spent or lapsed plan is re-buyable.
  2. AUTO-PAY — a purchase can set up a Razorpay recurring mandate so the
     plan re-bills itself instead of quietly lapsing. Verified with its own
     signature, renewed by polling Razorpay (no webhook), and always
     degrading to a plain one-time order if the gateway won't play.

Pure-logic only, per the suite's standing rule: the Razorpay client is
stubbed, no external call is ever made.
"""
import itertools
from datetime import timedelta

import pytest
from bson import ObjectId

from app.core.exceptions import BadRequestException
from app.schemas.payment_schema import CreateOrderRequest, VerifyPaymentRequest
from app.schemas.subscription_schema import SubscribeRequest
from app.services import payment_service
from app.services.payment_service import PaymentService, _expected_subscription_signature
from app.services.subscription_service import UserSubscriptionService
from app.utils.timezone import now_ist

from tests.factories import get_hatchback_type_id, make_customer, make_subscription_plan

pytestmark = pytest.mark.asyncio

_seq = itertools.count(1)


class _StubPlans:
    def __init__(self, fail=False):
        self.fail = fail
        self.created = []

    def create(self, payload):
        if self.fail:
            raise RuntimeError("Subscriptions are not enabled on this account")
        assert payload["item"]["amount"] >= 100 and payload["period"] in ("monthly", "yearly")
        self.created.append(payload)
        return {"id": f"plan_stub_{next(_seq):04d}"}


class _StubSubscriptions:
    def __init__(self):
        self.remote = {"status": "active", "paid_count": 1, "current_end": None}
        self.cancelled: list[tuple[str, dict]] = []

    def create(self, payload):
        assert payload["plan_id"].startswith("plan_stub_") and payload["total_count"] > 0
        return {"id": f"sub_stub_{next(_seq):04d}", "status": "created"}

    def fetch(self, subscription_id):
        return {"id": subscription_id, **self.remote}

    def cancel(self, subscription_id, data=None):
        self.cancelled.append((subscription_id, data or {}))
        return {"id": subscription_id, "status": "cancelled"}


class _StubOrders:
    def create(self, payload):
        return {"id": f"order_stub_{next(_seq):06d}", **payload}


class _StubClient:
    def __init__(self, *, plans_fail=False):
        self.plan = _StubPlans(fail=plans_fail)
        self.subscription = _StubSubscriptions()
        self.order = _StubOrders()


@pytest.fixture
def gateway(monkeypatch):
    """A stubbed Razorpay with real-looking credentials configured, so the
    signature math and the 'is this configured?' guards behave as in prod."""
    monkeypatch.setattr(payment_service.settings, "RAZORPAY_KEY_ID", "rzp_test_stub")
    monkeypatch.setattr(payment_service.settings, "RAZORPAY_KEY_SECRET", "stub_secret_key")
    client = _StubClient()
    monkeypatch.setattr(payment_service, "_razorpay_client", lambda: client)
    return client


@pytest.fixture
async def rig(db, cleanup):
    hatchback = await get_hatchback_type_id(db)
    customer_id = await make_customer(db)
    cleanup.append(("users", {"_id": ObjectId(customer_id)}))
    cleanup.append(("user_subscriptions", {"customer_id": customer_id}))
    cleanup.append(("payment_orders", {"customer_id": customer_id}))
    plan_id = await make_subscription_plan(db, vehicle_types=[hatchback])
    cleanup.append(("subscription_plans", {"_id": ObjectId(plan_id)}))
    cleanup.append(("razorpay_plans", {"plan_id": plan_id}))
    return {"db": db, "customer_id": customer_id, "plan_id": plan_id, "hatchback": hatchback}


# ------------------------------------------------- one live copy per plan


async def test_same_plan_cannot_be_bought_twice_while_it_is_live(rig, db, cleanup):
    svc = UserSubscriptionService(db)
    first = await svc.subscribe(rig["customer_id"], SubscribeRequest(plan_id=rig["plan_id"]))

    with pytest.raises(BadRequestException, match="already have an active"):
        await svc.subscribe(rig["customer_id"], SubscribeRequest(plan_id=rig["plan_id"]))

    # ...and the check runs BEFORE any money moves, too.
    with pytest.raises(BadRequestException, match="already have an active"):
        await svc.validate_purchase(rig["customer_id"], SubscribeRequest(plan_id=rig["plan_id"]))

    # A DIFFERENT plan is unaffected — plans stack, copies of one don't.
    other_plan = await make_subscription_plan(db, vehicle_types=[rig["hatchback"]])
    cleanup.append(("subscription_plans", {"_id": ObjectId(other_plan)}))
    assert await svc.subscribe(rig["customer_id"], SubscribeRequest(plan_id=other_plan))

    # Spending the last visit expires the card — re-buying is open again.
    await db.user_subscriptions.update_one(
        {"_id": ObjectId(first["id"])}, {"$set": {"remaining_service_count": 0, "status": "expired"}}
    )
    again = await svc.subscribe(rig["customer_id"], SubscribeRequest(plan_id=rig["plan_id"]))
    assert again["remaining_service_count"] > 0

    # So does simply running out of time.
    await db.user_subscriptions.update_one(
        {"_id": ObjectId(again["id"])}, {"$set": {"end_date": now_ist().replace(tzinfo=None) - timedelta(days=1)}}
    )
    assert await svc.subscribe(rig["customer_id"], SubscribeRequest(plan_id=rig["plan_id"]))


async def test_staff_assign_respects_the_same_one_copy_rule(rig, db):
    from app.schemas.subscription_schema import AssignSubscriptionRequest

    svc = UserSubscriptionService(db)
    await svc.assign(AssignSubscriptionRequest(customer_id=rig["customer_id"], plan_id=rig["plan_id"]))
    with pytest.raises(BadRequestException, match="already have an active"):
        await svc.assign(AssignSubscriptionRequest(customer_id=rig["customer_id"], plan_id=rig["plan_id"]))


# ---------------------------------------------------------------- auto-pay


async def test_autopay_purchase_is_signature_gated_and_creates_a_renewing_plan(rig, db, gateway):
    svc = PaymentService(db)
    created = await svc.create_order(
        rig["customer_id"], CreateOrderRequest(purpose="subscription", plan_id=rig["plan_id"], auto_pay=True)
    )
    assert created["auto_pay"] is True
    assert created["subscription_id"].startswith("sub_stub_") and "order_id" not in created
    mandate_id = created["subscription_id"]

    # Forged signature changes nothing.
    with pytest.raises(BadRequestException, match="signature"):
        await svc.verify_payment(rig["customer_id"], VerifyPaymentRequest(
            razorpay_subscription_id=mandate_id, razorpay_payment_id="pay_a", razorpay_signature="f" * 64))
    assert await db.user_subscriptions.count_documents({"customer_id": rig["customer_id"]}) == 0

    good = _expected_subscription_signature(mandate_id, "pay_a")
    result = await svc.verify_payment(rig["customer_id"], VerifyPaymentRequest(
        razorpay_subscription_id=mandate_id, razorpay_payment_id="pay_a", razorpay_signature=good))
    assert result["purpose"] == "subscription" and result["auto_pay"] is True
    sub = await db.user_subscriptions.find_one({"customer_id": rig["customer_id"]})
    assert sub["auto_renew"] is True and sub["razorpay_subscription_id"] == mandate_id
    # The authorisation charge IS cycle 1 — the sweep must not re-apply it.
    order = await db.payment_orders.find_one({"razorpay_subscription_id": mandate_id})
    assert order["status"] == "paid" and order["cycles_applied"] == 1

    # Replay is a no-op success, never a second plan.
    replay = await svc.verify_payment(rig["customer_id"], VerifyPaymentRequest(
        razorpay_subscription_id=mandate_id, razorpay_payment_id="pay_a", razorpay_signature=good))
    assert replay["already_processed"] is True
    assert await db.user_subscriptions.count_documents({"customer_id": rig["customer_id"]}) == 1


async def test_autopay_falls_back_to_a_one_time_order_when_the_gateway_refuses(rig, db, monkeypatch):
    """Razorpay Subscriptions not enabled on the account must never block a
    purchase — the customer buys the cycle outright and is told so."""
    monkeypatch.setattr(payment_service.settings, "RAZORPAY_KEY_ID", "rzp_test_stub")
    monkeypatch.setattr(payment_service.settings, "RAZORPAY_KEY_SECRET", "stub_secret_key")
    monkeypatch.setattr(payment_service, "_razorpay_client", lambda: _StubClient(plans_fail=True))

    created = await PaymentService(db).create_order(
        rig["customer_id"], CreateOrderRequest(purpose="subscription", plan_id=rig["plan_id"], auto_pay=True)
    )
    assert created["auto_pay"] is False and created["auto_pay_unavailable"] is True
    assert created["order_id"].startswith("order_stub_")


async def test_a_business_refusal_is_not_swallowed_by_the_fallback(rig, db, gateway):
    """The fallback covers GATEWAY failures only. A real refusal (here: the
    customer already holds this plan) must still surface as a 400 — silently
    selling a one-time order instead would sell exactly what we just said
    they can't have."""
    await UserSubscriptionService(db).subscribe(rig["customer_id"], SubscribeRequest(plan_id=rig["plan_id"]))
    with pytest.raises(BadRequestException, match="already have an active"):
        await PaymentService(db).create_order(
            rig["customer_id"], CreateOrderRequest(purpose="subscription", plan_id=rig["plan_id"], auto_pay=True)
        )


async def test_renewal_sweep_applies_each_charge_exactly_once(rig, db, gateway):
    svc = PaymentService(db)
    created = await svc.create_order(
        rig["customer_id"], CreateOrderRequest(purpose="subscription", plan_id=rig["plan_id"], auto_pay=True)
    )
    mandate_id = created["subscription_id"]
    await svc.verify_payment(rig["customer_id"], VerifyPaymentRequest(
        razorpay_subscription_id=mandate_id, razorpay_payment_id="pay_a",
        razorpay_signature=_expected_subscription_signature(mandate_id, "pay_a")))

    sub_id = (await db.user_subscriptions.find_one({"customer_id": rig["customer_id"]}))["_id"]
    # Customer spends the cycle down; the plan is nearly used up.
    await db.user_subscriptions.update_one({"_id": sub_id}, {"$set": {"remaining_service_count": 0, "status": "expired"}})
    before = await db.user_subscriptions.find_one({"_id": sub_id})

    # Razorpay has now charged the mandate a second time — its current_end
    # is where CYCLE 2 ends, i.e. another month past the first cycle.
    new_end = now_ist() + timedelta(days=60)
    gateway.subscription.remote = {"status": "active", "paid_count": 2, "current_end": int(new_end.timestamp())}

    assert await svc.sync_autopay_renewals() == 1
    after = await db.user_subscriptions.find_one({"_id": sub_id})
    assert after["status"] == "active"
    assert after["remaining_service_count"] == after["total_service_count"] > 0
    assert after["end_date"] > before["end_date"]
    assert after["renewal_count"] == 1
    # ...and the renewal is in the money ledger, so admin collections see it.
    assert await db.payment_orders.count_documents({"kind": "autopay_cycle", "mandate_id": mandate_id}) == 1

    # A second sweep with the same remote count changes nothing. Clear the
    # throttle first, so this really re-runs the apply path rather than
    # passing because the mandate wasn't due for a check yet.
    await db.payment_orders.update_one({"razorpay_subscription_id": mandate_id}, {"$unset": {"next_check_at": ""}})
    assert await svc.sync_autopay_renewals() == 0
    assert (await db.user_subscriptions.find_one({"_id": sub_id}))["renewal_count"] == 1

    # ...and the throttle is now set, so the next minute's sweep doesn't
    # call Razorpay again at all (one API call per subscriber per MINUTE is
    # what this avoids).
    order = await db.payment_orders.find_one({"razorpay_subscription_id": mandate_id})
    assert order["next_check_at"] is not None
    assert await svc.sync_autopay_renewals() == 0


async def test_renewal_for_a_cancelled_plan_is_parked_for_a_human(rig, db, gateway):
    svc = PaymentService(db)
    created = await svc.create_order(
        rig["customer_id"], CreateOrderRequest(purpose="subscription", plan_id=rig["plan_id"], auto_pay=True)
    )
    mandate_id = created["subscription_id"]
    await svc.verify_payment(rig["customer_id"], VerifyPaymentRequest(
        razorpay_subscription_id=mandate_id, razorpay_payment_id="pay_a",
        razorpay_signature=_expected_subscription_signature(mandate_id, "pay_a")))
    sub_id = (await db.user_subscriptions.find_one({"customer_id": rig["customer_id"]}))["_id"]
    await db.user_subscriptions.update_one({"_id": sub_id}, {"$set": {"status": "cancelled"}})

    gateway.subscription.remote = {"status": "active", "paid_count": 2, "current_end": None}
    assert await svc.sync_autopay_renewals() == 0

    order = await db.payment_orders.find_one({"razorpay_subscription_id": mandate_id})
    assert order["status"] == "paid_attention" and "cancelled" in order["attention_reason"]
    assert order["auto_pay_active"] is False
    assert mandate_id in [c[0] for c in gateway.subscription.cancelled]  # mandate killed, no third charge
    # The cancelled plan stays cancelled — a renewal never revives it.
    assert (await db.user_subscriptions.find_one({"_id": sub_id}))["status"] == "cancelled"


async def test_turning_auto_pay_off_cancels_the_mandate_at_cycle_end(rig, db, gateway):
    svc = PaymentService(db)
    created = await svc.create_order(
        rig["customer_id"], CreateOrderRequest(purpose="subscription", plan_id=rig["plan_id"], auto_pay=True)
    )
    mandate_id = created["subscription_id"]
    await svc.verify_payment(rig["customer_id"], VerifyPaymentRequest(
        razorpay_subscription_id=mandate_id, razorpay_payment_id="pay_a",
        razorpay_signature=_expected_subscription_signature(mandate_id, "pay_a")))

    subscriptions = UserSubscriptionService(db)
    sub = await db.user_subscriptions.find_one({"customer_id": rig["customer_id"]})
    off = await subscriptions.set_auto_pay(rig["customer_id"], str(sub["_id"]), False)
    assert off["auto_renew"] is False
    assert gateway.subscription.cancelled[-1] == (mandate_id, {"cancel_at_cycle_end": 1})
    # The visits already paid for survive — only the renewal stops.
    assert off["remaining_service_count"] == sub["remaining_service_count"]

    # Switching it back on needs a fresh authorisation, not a flag flip.
    with pytest.raises(BadRequestException, match="fresh payment authorisation"):
        await subscriptions.set_auto_pay(rig["customer_id"], str(sub["_id"]), True)


async def test_cancelling_the_plan_stops_the_money_too(rig, db, gateway):
    svc = PaymentService(db)
    created = await svc.create_order(
        rig["customer_id"], CreateOrderRequest(purpose="subscription", plan_id=rig["plan_id"], auto_pay=True)
    )
    mandate_id = created["subscription_id"]
    await svc.verify_payment(rig["customer_id"], VerifyPaymentRequest(
        razorpay_subscription_id=mandate_id, razorpay_payment_id="pay_a",
        razorpay_signature=_expected_subscription_signature(mandate_id, "pay_a")))
    sub = await db.user_subscriptions.find_one({"customer_id": rig["customer_id"]})

    cancelled = await UserSubscriptionService(db).cancel(rig["customer_id"], str(sub["_id"]))
    assert cancelled["status"] == "cancelled" and cancelled["auto_renew"] is False
    assert gateway.subscription.cancelled[-1] == (mandate_id, {"cancel_at_cycle_end": 0})


async def test_another_customers_mandate_cannot_be_claimed(rig, db, gateway, cleanup):
    """The mandate id travels through the browser — verifying one that
    belongs to somebody else must 404, not hand over their plan."""
    from app.core.exceptions import NotFoundException

    svc = PaymentService(db)
    created = await svc.create_order(
        rig["customer_id"], CreateOrderRequest(purpose="subscription", plan_id=rig["plan_id"], auto_pay=True)
    )
    mandate_id = created["subscription_id"]
    attacker = await make_customer(db)
    cleanup.append(("users", {"_id": ObjectId(attacker)}))
    cleanup.append(("user_subscriptions", {"customer_id": attacker}))

    with pytest.raises(NotFoundException):
        await svc.verify_payment(attacker, VerifyPaymentRequest(
            razorpay_subscription_id=mandate_id, razorpay_payment_id="pay_a",
            razorpay_signature=_expected_subscription_signature(mandate_id, "pay_a")))
    assert await db.user_subscriptions.count_documents({"customer_id": attacker}) == 0


async def test_verify_demands_exactly_one_reference():
    """An order id and a mandate id are signed over DIFFERENT messages —
    accepting both at once would let a caller pick which check runs."""
    import pydantic

    with pytest.raises(pydantic.ValidationError):
        VerifyPaymentRequest(razorpay_payment_id="pay_a", razorpay_signature="x" * 64)
    with pytest.raises(pydantic.ValidationError):
        VerifyPaymentRequest(razorpay_order_id="order_1", razorpay_subscription_id="sub_1",
                             razorpay_payment_id="pay_a", razorpay_signature="x" * 64)
