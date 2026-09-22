"""
Manager sells a plan — WhatsApp payment link (one-time or auto-pay) or cash:

  - one-time LINK: the customer pays a Razorpay payment link (server-priced,
    optionally discounted or coupon'd); the plan activates the instant it's
    paid — via the SAME signature-verified callback a WhatsApp booking link
    already settles through, or the reminder-loop sweep, and NEVER before;
  - RECURRING (auto-pay): a Razorpay mandate at the plan's full,
    undiscounted rate — no discount, no coupon, ever, enforced at the
    schema. There is no browser to hand back a signature for a hosted
    mandate page opened outside our own checkout, so activation comes
    PURELY from asking Razorpay directly whether the first charge landed
    (sync_pending_manager_mandates) — nothing a customer's browser sends
    can trigger it;
  - CASH: the manager already has the money — the plan activates at once,
    with the price/discount/collector recorded for the books.

Every branch re-validates from scratch (duplicate pass, plan active, tier)
exactly like the customer's own purchase does — the manager's preview is
advisory only. Pure-logic tests: the Razorpay client is stubbed throughout,
per the suite's standing rule (no external call is ever made).
"""
import hashlib
import hmac as hmac_mod
import itertools

import pytest
from bson import ObjectId

from app.core.config import settings
from app.core.exceptions import BadRequestException, NotFoundException
from app.schemas.coupon_schema import CouponCreateRequest
from app.schemas.subscription_schema import ManagerSubscriptionOfferRequest, ManagerSubscriptionPreviewRequest
from app.services import payment_service
from app.services.coupon_service import CouponService
from app.services.payment_service import PaymentService
from app.services.subscription_service import resolve_pass_price
from app.utils.timezone import now_ist

from tests.factories import get_hatchback_type_id, get_star_wash_service_id, make_manager, make_service_center, make_subscription_plan

pytestmark = pytest.mark.asyncio

_seq = itertools.count(1)


class _StubLinks:
    def __init__(self):
        self.status = "created"
        self.cancelled: list[str] = []

    def create(self, payload):
        assert payload["amount"] >= 100 and payload["notify"] == {"sms": False, "email": False}
        link_id = f"plink_stub_{next(_seq):06d}"
        return {"id": link_id, "short_url": f"https://rzp.io/l/{link_id}", **payload}

    def fetch(self, link_id):
        return {"id": link_id, "status": self.status, "payments": [{"payment_id": "pay_linkpay"}]}

    def cancel(self, link_id):
        self.cancelled.append(link_id)
        return {"id": link_id, "status": "cancelled"}


class _StubPlans:
    def create(self, payload):
        assert payload["item"]["amount"] >= 100
        return {"id": f"rzp_plan_{next(_seq):04d}"}


class _StubSubscriptions:
    def __init__(self):
        self.remote = {"status": "created", "paid_count": 0}
        self.cancelled: list[str] = []
        self.no_short_url = False

    def create(self, payload):
        assert payload["plan_id"].startswith("rzp_plan_") and payload["total_count"] > 0
        sid = f"sub_stub_{next(_seq):04d}"
        result = {"id": sid, "status": "created"}
        if not self.no_short_url:
            result["short_url"] = f"https://rzp.io/i/{sid}"
        return result

    def fetch(self, subscription_id):
        return {"id": subscription_id, **self.remote}

    def cancel(self, subscription_id, data=None):
        self.cancelled.append(subscription_id)
        return {"id": subscription_id, "status": "cancelled"}


class _StubClient:
    def __init__(self):
        self.payment_link = _StubLinks()
        self.plan = _StubPlans()
        self.subscription = _StubSubscriptions()


@pytest.fixture
def gateway(monkeypatch):
    monkeypatch.setattr(payment_service.settings, "RAZORPAY_KEY_ID", "rzp_test_stub")
    monkeypatch.setattr(payment_service.settings, "RAZORPAY_KEY_SECRET", "stub_secret_key")
    client = _StubClient()
    monkeypatch.setattr(payment_service, "_razorpay_client", lambda: client)
    return client


@pytest.fixture
async def rig(db, cleanup, gateway):
    hatchback = await get_hatchback_type_id(db)
    star = await get_star_wash_service_id(db)
    center_id = await make_service_center(db, pincode="452066")
    cleanup.append(("service_centers", {"_id": ObjectId(center_id)}))
    manager_id = await make_manager(db, center_id)
    cleanup.append(("users", {"_id": ObjectId(manager_id)}))
    plan_id = await make_subscription_plan(db, vehicle_types=[hatchback], included_service_ids=[star], total_service_count=1)
    cleanup.append(("subscription_plans", {"_id": ObjectId(plan_id)}))
    cleanup.append(("razorpay_plans", {"plan_id": plan_id}))
    plan = await db.subscription_plans.find_one({"_id": ObjectId(plan_id)})
    service = await db.services.find_one({"_id": ObjectId(star)})
    base_price = resolve_pass_price(plan, service, hatchback)
    return {
        "db": db, "manager_id": manager_id, "center_id": center_id, "hatchback": hatchback, "star": star,
        "plan_id": plan_id, "base_price": base_price,
    }


def _offer(rig, phone, **overrides) -> ManagerSubscriptionOfferRequest:
    payload = dict(
        customer_name="Plan Customer", customer_phone=phone, plan_id=rig["plan_id"],
        vehicle_type=rig["hatchback"], service_id=rig["star"], recurring=False, payment_method="link",
    )
    payload.update(overrides)
    return ManagerSubscriptionOfferRequest(**payload)


def _track(cleanup, phone):
    cleanup.append(("users", {"phone": phone}))


async def _track_customer(cleanup, db, phone) -> dict:
    """user_subscriptions/payment_orders key on customer_id, not phone — this
    resolves the customer (created by the offer call) and registers precise,
    customer-scoped cleanup instead of a blanket collection wipe."""
    customer = await db.users.find_one({"phone": phone})
    cid = str(customer["_id"])
    cleanup.append(("user_subscriptions", {"customer_id": cid}))
    cleanup.append(("payment_orders", {"customer_id": cid}))
    cleanup.append(("notifications", {"user_id": cid}))
    return customer


def _expected_link_signature(link_id: str, reference_id: str, payment_id: str) -> str:
    msg = f"{link_id}|{reference_id}|paid|{payment_id}"
    return hmac_mod.new(settings.RAZORPAY_KEY_SECRET.encode(), msg.encode(), hashlib.sha256).hexdigest()


# ------------------------------------------------------------- preview


async def test_preview_prices_a_plan_before_any_customer_or_money_exists(rig):
    svc = PaymentService(rig["db"])
    result = await svc.manager_subscription_preview(
        ManagerSubscriptionPreviewRequest(plan_id=rig["plan_id"], vehicle_type=rig["hatchback"], service_id=rig["star"])
    )
    assert result["base_price"] == rig["base_price"] == result["final_price"]
    assert result["customer_exists"] is False and result["already_has_pass"] is False
    # Nothing was created by merely previewing.
    assert await rig["db"].payment_orders.count_documents({}) == 0
    assert await rig["db"].user_subscriptions.count_documents({}) == 0


async def test_preview_applies_a_discount_or_a_coupon_never_both_at_once(rig):
    svc = PaymentService(rig["db"])
    discounted = await svc.manager_subscription_preview(
        ManagerSubscriptionPreviewRequest(plan_id=rig["plan_id"], vehicle_type=rig["hatchback"], service_id=rig["star"], discount_amount=50)
    )
    assert discounted["discount"] == 50 and discounted["final_price"] == rig["base_price"] - 50

    coupon = await CouponService(rig["db"]).create(CouponCreateRequest(
        code=f"MGR{next(_seq)}", coupon_type="flat", value=30, valid_from=now_ist(), valid_until=now_ist().replace(year=2099),
    ))
    with_coupon = await svc.manager_subscription_preview(
        ManagerSubscriptionPreviewRequest(plan_id=rig["plan_id"], vehicle_type=rig["hatchback"], service_id=rig["star"], coupon_code=coupon["code"])
    )
    assert with_coupon["coupon_valid"] is True and with_coupon["discount"] == 30

    bad_coupon = await svc.manager_subscription_preview(
        ManagerSubscriptionPreviewRequest(plan_id=rig["plan_id"], vehicle_type=rig["hatchback"], service_id=rig["star"], coupon_code="NOPE")
    )
    assert bad_coupon["coupon_valid"] is False and bad_coupon["discount"] == 0

    # Schema itself refuses discount + coupon together — the manager can't
    # even build a request that carries both.
    with pytest.raises(ValueError):
        ManagerSubscriptionOfferRequest(
            customer_name="X", customer_phone="9000000001", plan_id=rig["plan_id"], vehicle_type=rig["hatchback"],
            service_id=rig["star"], discount_amount=10, coupon_code="X",
        )


async def test_recurring_preview_ignores_any_discount_or_coupon_fields(rig):
    svc = PaymentService(rig["db"])
    result = await svc.manager_subscription_preview(
        ManagerSubscriptionPreviewRequest(
            plan_id=rig["plan_id"], vehicle_type=rig["hatchback"], service_id=rig["star"],
            recurring=True, discount_amount=999, coupon_code="ANYTHING",
        )
    )
    assert result["final_price"] == rig["base_price"] and result["discount"] == 0


# --------------------------------------------------------- schema guards


async def test_recurring_cannot_carry_a_discount_coupon_or_cash():
    base = dict(customer_name="Vishal", customer_phone="9111111111", plan_id="p", vehicle_type="v", service_id="s", recurring=True)
    with pytest.raises(ValueError, match="full price"):
        ManagerSubscriptionOfferRequest(**base, discount_amount=10)
    with pytest.raises(ValueError, match="full price"):
        ManagerSubscriptionOfferRequest(**base, coupon_code="X")
    with pytest.raises(ValueError, match="cash can't start"):
        ManagerSubscriptionOfferRequest(**base, payment_method="cash")
    # A negative discount (which would INCREASE the charge) is rejected at
    # the field level before any model validator even runs.
    with pytest.raises(ValueError):
        ManagerSubscriptionOfferRequest(customer_name="Vishal", customer_phone="9111111112", plan_id="p", vehicle_type="v", service_id="s", discount_amount=-1)
    # Absurdly large discount is bounded too (le=100000), not left to the
    # price-clamp alone to save us.
    with pytest.raises(ValueError):
        ManagerSubscriptionOfferRequest(customer_name="Vishal", customer_phone="9111111113", plan_id="p", vehicle_type="v", service_id="s", discount_amount=1_000_000)


# ------------------------------------------------------------ one-time link


async def test_link_activates_only_after_a_genuine_signature_never_a_forged_one(rig, cleanup):
    phone = "9333300001"
    _track(cleanup, phone)
    svc = PaymentService(rig["db"])
    result = await svc.manager_subscription_offer(rig["manager_id"], _offer(rig, phone, discount_amount=20))
    assert result["kind"] == "link" and result["amount"] == rig["base_price"] - 20
    order = await rig["db"].payment_orders.find_one({"_id": ObjectId(result["order_id"])})
    assert order["status"] == "created" and order["issued_by"] == rig["manager_id"] and order["discount_paise"] == 2000

    customer = await _track_customer(cleanup, rig["db"], phone)
    assert customer and customer["full_name"] == "Plan Customer"
    assert await rig["db"].user_subscriptions.count_documents({"customer_id": str(customer["_id"])}) == 0

    # A forged callback signature settles NOTHING.
    with pytest.raises(BadRequestException, match="signature"):
        await svc.verify_link_callback({
            "razorpay_payment_link_id": order["razorpay_link_id"], "razorpay_payment_link_reference_id": order["reference_id"],
            "razorpay_payment_link_status": "paid", "razorpay_payment_id": "pay_forged", "razorpay_signature": "f" * 64,
        })
    assert await rig["db"].user_subscriptions.count_documents({"customer_id": str(customer["_id"])}) == 0

    # The genuine signature settles it — plan created with the discounted price recorded.
    good = _expected_link_signature(order["razorpay_link_id"], order["reference_id"], "pay_real")
    out = await svc.verify_link_callback({
        "razorpay_payment_link_id": order["razorpay_link_id"], "razorpay_payment_link_reference_id": order["reference_id"],
        "razorpay_payment_link_status": "paid", "razorpay_payment_id": "pay_real", "razorpay_signature": good,
    })
    assert out["settled"] is True
    sub = await rig["db"].user_subscriptions.find_one({"customer_id": str(customer["_id"])})
    assert sub["payment_method"] == "online" and sub["amount_paid"] == rig["base_price"] - 20
    assert sub["discount_amount"] == 20 and sub["assigned_by"] == rig["manager_id"]

    # Replaying the callback (or the sweep finding it) must never mint a second plan.
    replay = await svc.verify_link_callback({
        "razorpay_payment_link_id": order["razorpay_link_id"], "razorpay_payment_link_reference_id": order["reference_id"],
        "razorpay_payment_link_status": "paid", "razorpay_payment_id": "pay_real", "razorpay_signature": good,
    })
    assert replay.get("already_processed") is True
    assert await rig["db"].user_subscriptions.count_documents({"customer_id": str(customer["_id"])}) == 1


async def test_link_settles_via_the_sweep_and_announces_exactly_once(rig, cleanup):
    """No public callback URL (the dev/default case) — the reminder-loop
    sweep is what actually activates a WhatsApp-opened link."""
    phone = "9333300002"
    _track(cleanup, phone)
    svc = PaymentService(rig["db"])
    result = await svc.manager_subscription_offer(rig["manager_id"], _offer(rig, phone))
    customer = await _track_customer(cleanup, rig["db"], phone)

    notified = []

    async def notify(order_doc):
        notified.append(order_doc)

    # Not yet paid at Razorpay — sweep is a no-op.
    assert await svc.sync_pending_links(notify) == 0
    assert await rig["db"].user_subscriptions.count_documents({"customer_id": str(customer["_id"])}) == 0

    # Razorpay now reports it paid.
    from app.services import payment_service as ps

    client = ps._razorpay_client()
    client.payment_link.status = "paid"
    settled = await svc.sync_pending_links(notify)
    assert settled == 1
    # Subscription-purpose settlements never fire the generic booking-style
    # "booking is paid" callback (they announce themselves separately).
    assert notified == []
    sub = await rig["db"].user_subscriptions.find_one({"customer_id": str(customer["_id"])})
    assert sub is not None and sub["payment_method"] == "online"
    assert await rig["db"].notifications.count_documents({"user_id": str(customer["_id"]), "title": {"$regex": "is active$"}}) == 1


async def test_coupon_discount_is_locked_in_at_creation_and_usage_recorded_only_on_settlement(rig, cleanup):
    phone = "9333300003"
    _track(cleanup, phone)
    coupon = await CouponService(rig["db"]).create(CouponCreateRequest(
        code=f"MGRC{next(_seq)}", coupon_type="flat", value=25, usage_limit_per_user=1, total_usage_limit=1,
        valid_from=now_ist(), valid_until=now_ist().replace(year=2099),
    ))
    svc = PaymentService(rig["db"])
    result = await svc.manager_subscription_offer(rig["manager_id"], _offer(rig, phone, coupon_code=coupon["code"]))
    customer = await _track_customer(cleanup, rig["db"], phone)
    assert result["amount"] == rig["base_price"] - 25

    # Not consumed yet — the link hasn't been paid.
    fresh = await rig["db"].coupons.find_one({"_id": ObjectId(coupon["id"])})
    assert fresh["total_used"] == 0

    order = await rig["db"].payment_orders.find_one({"_id": ObjectId(result["order_id"])})
    good = _expected_link_signature(order["razorpay_link_id"], order["reference_id"], "pay_c1")
    await svc.verify_link_callback({
        "razorpay_payment_link_id": order["razorpay_link_id"], "razorpay_payment_link_reference_id": order["reference_id"],
        "razorpay_payment_link_status": "paid", "razorpay_payment_id": "pay_c1", "razorpay_signature": good,
    })
    assert (await rig["db"].coupons.find_one({"_id": ObjectId(coupon["id"])}))["total_used"] == 1
    sub = await rig["db"].user_subscriptions.find_one({"customer_id": str(customer["_id"])})
    assert sub["coupon_code"] == coupon["code"] and sub["discount_amount"] == 25

    # Its single global use is now gone — a SECOND manager offer for a
    # different customer with the same coupon is refused up front, before
    # any link is created (the check happens at offer-creation, not later).
    phone2 = "9333300004"
    _track(cleanup, phone2)
    with pytest.raises(BadRequestException, match="usage limit"):
        await svc.manager_subscription_offer(rig["manager_id"], _offer(rig, phone2, coupon_code=coupon["code"]))
    assert await rig["db"].payment_orders.count_documents({"coupon_code": coupon["code"], "customer_id": {"$ne": str(customer["_id"])}}) == 0


async def test_discount_bringing_the_price_below_the_online_minimum_is_refused_not_silently_free(rig, cleanup):
    phone = "9333300005"
    _track(cleanup, phone)
    svc = PaymentService(rig["db"])
    with pytest.raises(BadRequestException, match="minimum"):
        await svc.manager_subscription_offer(rig["manager_id"], _offer(rig, phone, discount_amount=rig["base_price"]))
    assert await rig["db"].payment_orders.count_documents({"customer_id": {"$exists": True}}) == 0


async def test_a_customer_who_already_holds_the_pass_is_refused_before_any_link_or_money(rig, cleanup):
    phone = "9333300006"
    _track(cleanup, phone)
    svc = PaymentService(rig["db"])
    # First one goes through as cash (immediate), so the customer now holds it.
    await svc.manager_subscription_offer(rig["manager_id"], _offer(rig, phone, payment_method="cash"))
    customer = await _track_customer(cleanup, rig["db"], phone)
    with pytest.raises(BadRequestException, match="already have"):
        await svc.manager_subscription_offer(rig["manager_id"], _offer(rig, phone))
    assert await rig["db"].user_subscriptions.count_documents({"customer_id": str(customer["_id"])}) == 1
    assert await rig["db"].payment_orders.count_documents({"customer_id": str(customer["_id"]), "kind": "link"}) == 0


# --------------------------------------------------------------- cash


async def test_cash_activates_immediately_and_is_recorded_for_the_books(rig, cleanup):
    phone = "9333300007"
    _track(cleanup, phone)
    svc = PaymentService(rig["db"])
    result = await svc.manager_subscription_offer(rig["manager_id"], _offer(rig, phone, payment_method="cash", discount_amount=15))
    await _track_customer(cleanup, rig["db"], phone)
    assert result["kind"] == "cash" and result["amount"] == rig["base_price"] - 15
    sub = result["subscription"]
    stored = await rig["db"].user_subscriptions.find_one({"_id": ObjectId(sub["id"])})
    assert stored["status"] == "active" and stored["payment_method"] == "cash"
    assert stored["cash_collected_by"] == rig["manager_id"] and stored["discount_amount"] == 15
    ledger = await rig["db"].payment_orders.find_one({"subscription_id": sub["id"]})
    assert ledger["kind"] == "cash" and ledger["status"] == "paid" and ledger["amount_paise"] == int(round((rig["base_price"] - 15) * 100))

    # Shows up in the admin collections roll-up, split from Razorpay revenue.
    report = await svc.admin_collections(None, None)
    assert report["subscriptions"]["cash_amount"] >= rig["base_price"] - 15
    assert report["subscriptions"]["cash_count"] >= 1


# ------------------------------------------------------------ auto-pay


async def test_autopay_mandate_activates_only_via_the_sweep_never_a_bare_claim(rig, cleanup, gateway):
    phone = "9333300008"
    _track(cleanup, phone)
    svc = PaymentService(rig["db"])
    result = await svc.manager_subscription_offer(rig["manager_id"], _offer(rig, phone, recurring=True))
    assert result["kind"] == "autopay" and result["amount"] == rig["base_price"] and result["short_url"].startswith("https://rzp.io/")
    order = await rig["db"].payment_orders.find_one({"razorpay_subscription_id": result["order_id"]})
    assert order["status"] == "created" and order["amount_paise"] == int(round(rig["base_price"] * 100))
    customer = await _track_customer(cleanup, rig["db"], phone)

    # Not authorised yet at Razorpay — sweeping changes nothing.
    assert await svc.sync_pending_manager_mandates() == 0
    assert await rig["db"].user_subscriptions.count_documents({"customer_id": str(customer["_id"])}) == 0

    # Customer completes authorisation on Razorpay's hosted page — the
    # ONLY thing that can tell us that is asking Razorpay directly.
    gateway.subscription.remote = {"status": "active", "paid_count": 1}
    activated = await svc.sync_pending_manager_mandates()
    assert activated == 1
    sub = await rig["db"].user_subscriptions.find_one({"customer_id": str(customer["_id"])})
    assert sub["auto_renew"] is True and sub["razorpay_subscription_id"] == result["order_id"]
    assert sub["purchased_price"] == rig["base_price"]  # full rate — never discounted

    # Running the sweep again must not create a second plan or re-charge bookkeeping.
    assert await svc.sync_pending_manager_mandates() == 0
    assert await rig["db"].user_subscriptions.count_documents({"customer_id": str(customer["_id"])}) == 1


async def test_autopay_mandate_that_expires_unauthorised_is_marked_expired_not_left_pending_forever(rig, cleanup, gateway):
    phone = "9333300009"
    _track(cleanup, phone)
    svc = PaymentService(rig["db"])
    result = await svc.manager_subscription_offer(rig["manager_id"], _offer(rig, phone, recurring=True))
    await _track_customer(cleanup, rig["db"], phone)
    gateway.subscription.remote = {"status": "cancelled", "paid_count": 0}
    assert await svc.sync_pending_manager_mandates() == 0
    order = await rig["db"].payment_orders.find_one({"razorpay_subscription_id": result["order_id"]})
    assert order["status"] == "expired"


async def test_autopay_mandate_creation_refuses_when_the_gateway_gives_no_hosted_page(rig, cleanup, gateway):
    phone = "9333300010"
    _track(cleanup, phone)
    gateway.subscription.no_short_url = True
    svc = PaymentService(rig["db"])
    with pytest.raises(BadRequestException, match="try again"):
        await svc.manager_subscription_offer(rig["manager_id"], _offer(rig, phone, recurring=True))
    customer = await _track_customer(cleanup, rig["db"], phone)
    assert await rig["db"].payment_orders.count_documents({"customer_id": str(customer["_id"])}) == 0
    assert gateway.subscription.cancelled  # voided at the gateway rather than left dangling


# --------------------------------------------------------------- void


async def test_voiding_a_pending_link_cancels_it_and_a_paid_one_is_refused(rig, cleanup):
    phone = "9333300011"
    _track(cleanup, phone)
    svc = PaymentService(rig["db"])
    result = await svc.manager_subscription_offer(rig["manager_id"], _offer(rig, phone))
    await _track_customer(cleanup, rig["db"], phone)
    order = await rig["db"].payment_orders.find_one({"_id": ObjectId(result["order_id"])})

    out = await svc.void_manager_subscription_offer(str(order["_id"]), rig["manager_id"])
    assert out["voided"] is True
    assert (await rig["db"].payment_orders.find_one({"_id": order["_id"]}))["status"] == "voided"

    # Voiding it again is a clear refusal, not a second gateway cancel call.
    with pytest.raises(BadRequestException, match="isn't pending"):
        await svc.void_manager_subscription_offer(str(order["_id"]), rig["manager_id"])

    # A booking's link (different channel) can't be voided through this endpoint.
    with pytest.raises(NotFoundException):
        await svc.void_manager_subscription_offer("000000000000000000000000", rig["manager_id"])


# ------------------------------------------------------------- discontinue


async def test_discontinue_only_flips_is_active_never_price_or_contents(rig, cleanup):
    from app.services.subscription_service import SubscriptionPlanService

    svc = SubscriptionPlanService(rig["db"])
    before = await svc.get(rig["plan_id"])
    result = await svc.discontinue(rig["plan_id"])
    assert result["is_active"] is False
    assert result["price"] == before["price"] and result["name"] == before["name"]

    # A discontinued plan can no longer be bought, by anyone, any way.
    pay_svc = PaymentService(rig["db"])
    _track(cleanup, "9333300012")
    with pytest.raises(NotFoundException):
        await pay_svc.manager_subscription_offer(rig["manager_id"], _offer(rig, "9333300012"))
    with pytest.raises(NotFoundException):
        await svc.discontinue("000000000000000000000000")


# --------------------------------------------------------- over HTTP / role security


def _auth(user_id: str, role: str, center_id: str | None) -> dict:
    from app.core.security import create_access_token

    token = create_access_token(user_id, role, {"service_center_id": center_id, "tv": 0})
    return {"Authorization": f"Bearer {token}"}


async def test_hack_a_customer_cannot_reach_any_manager_offer_route(rig, cleanup):
    from httpx import ASGITransport, AsyncClient

    from app.main import app
    from app.services.auth_service import AuthService

    customer = await AuthService(rig["db"]).ensure_customer_by_phone("9333300013", "Attacker")
    cleanup.append(("users", {"_id": customer["_id"]}))
    customer_auth = _auth(str(customer["_id"]), "customer", None)
    manager_auth = _auth(rig["manager_id"], "manager", rig["center_id"])
    body = {
        "customer_name": "Victim", "customer_phone": "9333300014", "plan_id": rig["plan_id"],
        "vehicle_type": rig["hatchback"], "service_id": rig["star"],
    }

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # No token at all.
        assert (await client.post("/api/v1/subscriptions/manager-offers", json=body)).status_code == 401
        # A real customer, logged in as themselves — still refused. This is
        # the actual bypass attempt: a customer trying to grant THEMSELVES
        # a plan through the manager endpoint instead of paying for it.
        assert (await client.post("/api/v1/subscriptions/manager-offers", json=body, headers=customer_auth)).status_code == 403
        assert (await client.post("/api/v1/subscriptions/manager-offers/preview", json=body, headers=customer_auth)).status_code == 403
        assert (await client.post(f"/api/v1/subscription-plans/{rig['plan_id']}/discontinue", headers=customer_auth)).status_code == 403
        assert await rig["db"].user_subscriptions.count_documents({"customer_id": str(customer["_id"])}) == 0

        # The manager themselves, over real HTTP, gets the plan created cleanly.
        cleanup.append(("users", {"phone": "9333300014"}))
        res = await client.post("/api/v1/subscriptions/manager-offers", json={**body, "payment_method": "cash"}, headers=manager_auth)
        assert res.status_code == 200, res.text
        assert res.json()["data"]["kind"] == "cash"
        cleanup.append(("payment_orders", {"customer_id": res.json()["data"]["subscription"]["customer_id"]}))
        cleanup.append(("user_subscriptions", {"customer_id": res.json()["data"]["subscription"]["customer_id"]}))

        # 422 on bad input, never a 500.
        bad = await client.post("/api/v1/subscriptions/manager-offers", json={**body, "recurring": True, "discount_amount": 50}, headers=manager_auth)
        assert bad.status_code == 422


async def test_hack_verify_link_callback_ignores_every_field_the_client_controls_except_the_signature(rig, cleanup):
    """Tamper with status/amount-adjacent fields while keeping a stolen
    signature from a DIFFERENT (unrelated) payment — must still fail,
    because the signature covers the exact link id + reference + status +
    payment id as one string, not any field checked in isolation."""
    phone = "9333300015"
    _track(cleanup, phone)
    svc = PaymentService(rig["db"])
    result = await svc.manager_subscription_offer(rig["manager_id"], _offer(rig, phone))
    customer1 = await _track_customer(cleanup, rig["db"], phone)
    order = await rig["db"].payment_orders.find_one({"_id": ObjectId(result["order_id"])})
    good = _expected_link_signature(order["razorpay_link_id"], order["reference_id"], "pay_real")

    # Reuse of a genuine signature against a DIFFERENT payment_id.
    with pytest.raises(BadRequestException, match="signature"):
        await svc.verify_link_callback({
            "razorpay_payment_link_id": order["razorpay_link_id"], "razorpay_payment_link_reference_id": order["reference_id"],
            "razorpay_payment_link_status": "paid", "razorpay_payment_id": "pay_swapped", "razorpay_signature": good,
        })
    # Reuse against a different (also real) link id.
    phone2 = "9333300016"
    other = await svc.manager_subscription_offer(rig["manager_id"], _offer(rig, phone2))
    _track(cleanup, phone2)
    customer2 = await _track_customer(cleanup, rig["db"], phone2)
    other_order = await rig["db"].payment_orders.find_one({"_id": ObjectId(other["order_id"])})
    with pytest.raises(BadRequestException, match="signature"):
        await svc.verify_link_callback({
            "razorpay_payment_link_id": other_order["razorpay_link_id"], "razorpay_payment_link_reference_id": order["reference_id"],
            "razorpay_payment_link_status": "paid", "razorpay_payment_id": "pay_real", "razorpay_signature": good,
        })
    assert await rig["db"].user_subscriptions.count_documents({"customer_id": {"$in": [str(customer1["_id"]), str(customer2["_id"])]}}) == 0


# --------------------------------------------------- coupon double-redemption


async def test_hack_the_same_single_use_coupon_cannot_ride_two_different_pending_links(rig, cleanup):
    """A single-use-per-customer coupon usage row is only written at
    SETTLEMENT (see test_coupon_discount_is_locked_in_at_creation...), so
    without this guard a manager could open TWO pending links for the same
    customer on different (vehicle_type, service) pairs — which the
    duplicate-PASS guard doesn't catch, since they're for different passes —
    both carrying the same coupon, and both would redeem it once paid."""
    phone = "9333300017"
    _track(cleanup, phone)
    coupon = await CouponService(rig["db"]).create(CouponCreateRequest(
        code=f"MGRONE{next(_seq)}", coupon_type="flat", value=25, usage_limit_per_user=1,
        valid_from=now_ist(), valid_until=now_ist().replace(year=2099),
    ))
    cleanup.append(("coupons", {"code": coupon["code"]}))
    cleanup.append(("coupon_usages", {"coupon_id": coupon["id"]}))
    svc = PaymentService(rig["db"])

    # A second vehicle type/service pass on the SAME plan — a different
    # (vehicle_type, service) pair, so the duplicate-pass guard alone
    # wouldn't stop a second offer.
    other_star = await rig["db"].services.find_one({"slug": "star-wash"})
    other_type = await rig["db"].vehicle_types.find_one({"slug": {"$ne": "hatchback"}, "is_deleted": {"$ne": True}})
    await rig["db"].subscription_plans.update_one({"_id": ObjectId(rig["plan_id"])}, {"$set": {"vehicle_types": []}})

    first = await svc.manager_subscription_offer(rig["manager_id"], _offer(rig, phone, coupon_code=coupon["code"]))
    assert first["kind"] == "link"

    with pytest.raises(BadRequestException, match="already has a pending or paid offer"):
        await svc.manager_subscription_offer(
            rig["manager_id"], _offer(rig, phone, coupon_code=coupon["code"], vehicle_type=str(other_type["_id"]), service_id=str(other_star["_id"])),
        )
    assert await rig["db"].payment_orders.count_documents({"coupon_code": coupon["code"]}) == 1

    # Settling the first, then trying a THIRD fresh offer with the same
    # coupon (now "paid", not merely "created") is refused the same way.
    order = await rig["db"].payment_orders.find_one({"_id": ObjectId(first["order_id"])})
    good = _expected_link_signature(order["razorpay_link_id"], order["reference_id"], "pay_one_coupon")
    await svc.verify_link_callback({
        "razorpay_payment_link_id": order["razorpay_link_id"], "razorpay_payment_link_reference_id": order["reference_id"],
        "razorpay_payment_link_status": "paid", "razorpay_payment_id": "pay_one_coupon", "razorpay_signature": good,
    })
    await _track_customer(cleanup, rig["db"], phone)
    # Now genuinely used up (usage_limit_per_user=1, and that row now
    # exists) — the ORDINARY per-user coupon check catches this one; the
    # new guard above exists for the narrower window before that row exists.
    with pytest.raises(BadRequestException, match="already used this coupon"):
        await svc.manager_subscription_offer(
            rig["manager_id"], _offer(rig, phone, coupon_code=coupon["code"], vehicle_type=str(other_type["_id"]), service_id=str(other_star["_id"])),
        )
    assert (await rig["db"].coupons.find_one({"_id": ObjectId(coupon["id"])}))["total_used"] == 1


# --------------------------------------------------------- void race safety


async def test_hack_voiding_an_offer_the_instant_it_gets_paid_settles_it_instead_of_stranding_the_money(rig, cleanup, gateway):
    phone = "9333300018"
    _track(cleanup, phone)
    svc = PaymentService(rig["db"])
    result = await svc.manager_subscription_offer(rig["manager_id"], _offer(rig, phone))
    await _track_customer(cleanup, rig["db"], phone)
    order = await rig["db"].payment_orders.find_one({"_id": ObjectId(result["order_id"])})

    # The customer pays at Razorpay the instant before the manager clicks void.
    gateway.payment_link.status = "paid"

    # A clear "it was just paid" refusal, not a silent void — and it was
    # activated instead: a real subscription exists, and the order reflects
    # "paid", never silently "voided" with money stuck.
    with pytest.raises(BadRequestException, match="just paid"):
        await svc.void_manager_subscription_offer(str(order["_id"]), rig["manager_id"])
    fresh_order = await rig["db"].payment_orders.find_one({"_id": order["_id"]})
    assert fresh_order["status"] == "paid"
    customer = await rig["db"].users.find_one({"phone": phone})
    assert await rig["db"].user_subscriptions.count_documents({"customer_id": str(customer["_id"])}) == 1


async def test_hack_voiding_an_autopay_mandate_the_instant_it_authorises_refuses_instead_of_orphaning_it(rig, cleanup, gateway):
    phone = "9333300019"
    _track(cleanup, phone)
    svc = PaymentService(rig["db"])
    result = await svc.manager_subscription_offer(rig["manager_id"], _offer(rig, phone, recurring=True))
    await _track_customer(cleanup, rig["db"], phone)
    order = await rig["db"].payment_orders.find_one({"razorpay_subscription_id": result["order_id"]})

    gateway.subscription.remote = {"status": "active", "paid_count": 1}
    with pytest.raises(BadRequestException, match="just authorised"):
        await svc.void_manager_subscription_offer(str(order["_id"]), rig["manager_id"])
    # Still pending locally (the sweep, not this call, activates it) — not voided.
    assert (await rig["db"].payment_orders.find_one({"_id": order["_id"]}))["status"] == "created"
    assert await svc.sync_pending_manager_mandates() == 1
