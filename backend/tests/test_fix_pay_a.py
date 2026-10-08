"""Payments remediation (2026-10-07 audit) — regression tests, part A:
PAY-01 frozen pass pricing, PAY-03 captain QR on a mixed visit, PAY-07 /
PAY-11 / PRICE-07 coupons, PAY-08 / PAY-10 reported plan revenue.

Razorpay is a local stub (`Gateway`, shared with parts B and C): it keeps
the orders, payments, links and mandates it was asked to create, so a test
can say "this order was paid / only authorised" and every server-side
lookup (verify's payment fetch, the sweeps, the captain's QR poll) sees the
same truth. Nothing here calls a real API.
"""
import asyncio
import hashlib
import hmac
import itertools
from datetime import datetime, timedelta, timezone

import pytest
from bson import ObjectId
from pydantic import ValidationError

from app.core.exceptions import BadRequestException
from app.models.enums import PaymentMethod
from app.schemas.booking_schema import BookingCreateRequest, BookingGroupCreateRequest, GroupVehicleRequest
from app.schemas.coupon_schema import CouponCreateRequest
from app.schemas.payment_schema import CreateOrderRequest, VerifyPaymentRequest
from app.schemas.subscription_schema import ManagerSubscriptionOfferRequest, SubscribeRequest
from app.services import payment_service
from app.services.booking_service import BookingService
from app.services.coupon_service import CouponService
from app.services.payment_service import PaymentService
from app.services.subscription_service import UserSubscriptionService
from app.utils.timezone import now_ist

from tests.factories import (
    get_hatchback_type_id,
    make_address,
    make_captain,
    make_customer,
    make_manager,
    make_service_center,
    make_vehicle,
)

pytestmark = pytest.mark.asyncio

_seq = itertools.count(1)
SECRET = "stub_secret_key"
WEBHOOK_SECRET = "whsec_stub"


# -- the Razorpay stub ------------------------------------------------------

class _Orders:
    def __init__(self, gw):
        self.gw = gw

    def create(self, payload):
        order_id = f"order_fx_{next(_seq):06d}"
        self.gw.orders[order_id] = {"id": order_id, **payload}
        return {"id": order_id, **payload}

    def payments(self, order_id):
        return {"items": [dict(p) for p in self.gw.payments.values() if p["order_id"] == order_id]}


class _Payments:
    def __init__(self, gw):
        self.gw = gw

    def fetch(self, payment_id):
        self.gw.fetched.append(payment_id)
        if self.gw.fetch_error:
            raise self.gw.fetch_error
        if payment_id not in self.gw.payments:
            raise RuntimeError(f"The id provided does not exist: {payment_id}")
        return dict(self.gw.payments[payment_id])

    def capture(self, payment_id, amount, data):
        self.gw.captures.append((payment_id, amount, data))
        if self.gw.capture_error:
            raise self.gw.capture_error
        self.gw.payments[payment_id]["status"] = "captured"
        return dict(self.gw.payments[payment_id])


class _Links:
    def __init__(self, gw):
        self.gw = gw

    def create(self, payload):
        link_id = f"plink_fx_{next(_seq):06d}"
        self.gw.links[link_id] = {"status": "created", "payments": []}
        self.gw.link_payloads[link_id] = payload
        return {"id": link_id, "short_url": f"https://rzp.io/l/{link_id}", **payload}

    def fetch(self, link_id):
        state = self.gw.links.get(link_id, {"status": "created", "payments": []})
        return {"id": link_id, **state}

    def cancel(self, link_id):
        if self.gw.links.get(link_id, {}).get("status") == "paid":
            raise RuntimeError("Payment link cannot be cancelled as it is already paid")
        self.gw.links.setdefault(link_id, {"payments": []})["status"] = "cancelled"
        self.gw.cancelled_links.append(link_id)
        return {"id": link_id, "status": "cancelled"}


class _Plans:
    def create(self, payload):
        return {"id": f"plan_fx_{next(_seq):06d}"}


class _Mandates:
    def __init__(self, gw):
        self.gw = gw

    def create(self, payload):
        sid = f"sub_fx_{next(_seq):06d}"
        self.gw.mandates[sid] = {"status": "created", "paid_count": 0, "current_end": None}
        return {"id": sid, "short_url": f"https://rzp.io/i/{sid}"}

    def fetch(self, sid):
        return {"id": sid, **self.gw.mandates.get(sid, {"status": "created", "paid_count": 0})}

    def cancel(self, sid, opts):
        self.gw.cancelled_mandates.append(sid)
        self.gw.mandates.setdefault(sid, {"paid_count": 0})["status"] = "cancelled"
        return {"id": sid, "status": "cancelled"}


class Gateway:
    def __init__(self):
        self.orders: dict[str, dict] = {}
        self.payments: dict[str, dict] = {}
        self.links: dict[str, dict] = {}
        self.link_payloads: dict[str, dict] = {}
        self.mandates: dict[str, dict] = {}
        self.fetched: list[str] = []
        self.captures: list[tuple] = []
        self.cancelled_links: list[str] = []
        self.cancelled_mandates: list[str] = []
        self.fetch_error: Exception | None = None
        self.capture_error: Exception | None = None
        self.order = _Orders(self)
        self.payment = _Payments(self)
        self.payment_link = _Links(self)
        self.plan = _Plans()
        self.subscription = _Mandates(self)

    def pay(self, order_id: str, payment_id: str | None = None, *, status: str = "captured", amount: int | None = None) -> str:
        """A payment on one of our orders, as Razorpay would record it."""
        payment_id = payment_id or f"pay_fx_{next(_seq):06d}"
        self.payments[payment_id] = {
            "id": payment_id, "entity": "payment", "order_id": order_id, "status": status, "currency": "INR",
            "amount": self.orders[order_id]["amount"] if amount is None else amount, "created_at": next(_seq),
        }
        return payment_id

    def pay_link(self, link_id: str, payment_id: str | None = None) -> str:
        payment_id = payment_id or f"pay_{link_id}"
        self.links[link_id] = {"status": "paid", "payments": [{"payment_id": payment_id}]}
        return payment_id

    def charge_mandate(self, sid: str, paid_count: int = 1) -> None:
        self.mandates[sid] = {"status": "active", "paid_count": paid_count, "current_end": None}


@pytest.fixture
def gw(monkeypatch):
    client = Gateway()
    monkeypatch.setattr(payment_service.settings, "RAZORPAY_KEY_ID", "rzp_test_stub")
    monkeypatch.setattr(payment_service.settings, "RAZORPAY_KEY_SECRET", SECRET)
    monkeypatch.setattr(payment_service.settings, "RAZORPAY_WEBHOOK_SECRET", WEBHOOK_SECRET)
    monkeypatch.setattr(payment_service, "_razorpay_client", lambda: client)
    monkeypatch.setattr(payment_service, "_sweeps_paused_until", 0.0)
    return client


def sig(order_id: str, payment_id: str) -> str:
    return hmac.new(SECRET.encode(), f"{order_id}|{payment_id}".encode(), hashlib.sha256).hexdigest()


def mandate_sig(sid: str, payment_id: str) -> str:
    return hmac.new(SECRET.encode(), f"{payment_id}|{sid}".encode(), hashlib.sha256).hexdigest()


def verify_req(order_id: str, payment_id: str) -> VerifyPaymentRequest:
    return VerifyPaymentRequest(razorpay_order_id=order_id, razorpay_payment_id=payment_id, razorpay_signature=sig(order_id, payment_id))


async def slug_id(db, collection: str, slug: str) -> str:
    return str((await db[collection].find_one({"slug": slug}))["_id"])


async def pass_plan_id(db, service_id: str) -> str:
    plan = await db.subscription_plans.find_one({"included_service_ids": service_id, "is_active": True, "plan_type": {"$ne": "society"}})
    assert plan, "seed() should have a public pass plan covering Star Wash"
    return str(plan["_id"])


async def open_slots(svc: BookingService, center_id: str, offset: int = 2) -> tuple[str, list[str]]:
    when = (now_ist().date() + timedelta(days=offset)).isoformat()
    slots = [s["key"] for s in await svc.available_slots(center_id, when) if s["status"] == "available"]
    if not slots:
        pytest.skip("no bookable slot")
    return when, slots


def track_customer(cleanup, customer_id: str) -> None:
    for collection, field in (
        ("bookings", "customer_id"), ("payment_orders", "customer_id"), ("user_subscriptions", "customer_id"),
        ("vehicles", "owner_id"), ("addresses", "owner_id"), ("notifications", "user_id"), ("coupon_usages", "user_id"),
    ):
        cleanup.append((collection, {field: customer_id}))
    cleanup.append(("users", {"_id": ObjectId(customer_id)}))


async def new_customer(db, cleanup) -> str:
    customer_id = await make_customer(db)
    track_customer(cleanup, customer_id)
    return customer_id


async def insert_coupon(db, cleanup, **overrides) -> str:
    now = datetime.now(timezone.utc)
    code = overrides.pop("code", None) or f"FXP{next(_seq):05d}"
    doc = {
        "code": code, "coupon_type": "flat", "value": 100.0, "min_order_value": 0.0, "max_discount_amount": None,
        "usage_limit_per_user": 1, "total_usage_limit": None, "total_used": 0, "valid_from": now - timedelta(days=1),
        "valid_until": now + timedelta(days=5), "is_active": True, "offer_kind": "standard", "is_deleted": False,
        **overrides,
    }
    result = await db.coupons.insert_one(doc)
    cleanup.append(("coupons", {"_id": result.inserted_id}))
    cleanup.append(("coupon_user_usage", {"coupon_id": str(result.inserted_id)}))
    return code


# -- PAY-01: the pass is priced for the type the order froze ---------------

async def test_pay01_pass_order_freezes_vehicle_type_and_parks_on_retype(db, cleanup, gw):
    hatch = await get_hatchback_type_id(db)
    xuv7 = await slug_id(db, "vehicle_types", "xuv-7-seater")
    star = await slug_id(db, "services", "star-wash")
    plan_id = await pass_plan_id(db, star)
    customer_id = await new_customer(db, cleanup)
    vehicle_id = await make_vehicle(db, customer_id, hatch)
    payments = PaymentService(db)

    hatch_price = (await UserSubscriptionService(db).quote_pass(customer_id, plan_id, None, star, vehicle_type=hatch))["price"]
    order = await payments.create_order(customer_id, CreateOrderRequest(purpose="subscription", plan_id=plan_id, vehicle_id=vehicle_id, service_id=star))
    assert order["amount"] == int(round(hatch_price * 100))
    stored = await db.payment_orders.find_one({"razorpay_order_id": order["order_id"]})
    assert stored["priced_vehicle_type"] == hatch and stored["vehicle_id"] == vehicle_id and stored["service_id"] == star

    # The customer retypes the car to an XUV-7 before paying.
    await db.vehicles.update_one({"_id": ObjectId(vehicle_id)}, {"$set": {"vehicle_type": xuv7}})
    payment_id = gw.pay(order["order_id"])
    with pytest.raises(BadRequestException):
        await payments.verify_payment(customer_id, verify_req(order["order_id"], payment_id))

    parked = await db.payment_orders.find_one({"razorpay_order_id": order["order_id"]})
    assert parked["status"] == "paid_attention"
    assert "vehicle type" in parked["attention_reason"]
    # No XUV-7 pass for the hatchback price.
    assert await db.user_subscriptions.count_documents({"customer_id": customer_id}) == 0


async def test_pay01_autopay_mandate_parks_and_stops_when_car_retyped(db, cleanup, gw):
    hatch = await get_hatchback_type_id(db)
    xuv7 = await slug_id(db, "vehicle_types", "xuv-7-seater")
    star = await slug_id(db, "services", "star-wash")
    plan_id = await pass_plan_id(db, star)
    customer_id = await new_customer(db, cleanup)
    vehicle_id = await make_vehicle(db, customer_id, hatch)
    payments = PaymentService(db)

    created = await payments.create_order(customer_id, CreateOrderRequest(
        purpose="subscription", plan_id=plan_id, vehicle_id=vehicle_id, service_id=star, auto_pay=True))
    sid = created["subscription_id"]
    mandate = await db.payment_orders.find_one({"razorpay_subscription_id": sid})
    assert mandate["priced_vehicle_type"] == hatch

    await db.vehicles.update_one({"_id": ObjectId(vehicle_id)}, {"$set": {"vehicle_type": xuv7}})
    gw.charge_mandate(sid)
    assert await payments.sync_pending_manager_mandates() == 0

    mandate = await db.payment_orders.find_one({"razorpay_subscription_id": sid})
    assert mandate["status"] == "paid_attention" and "vehicle type" in mandate["attention_reason"]
    assert mandate["auto_pay_active"] is False and sid in gw.cancelled_mandates
    assert await db.user_subscriptions.count_documents({"customer_id": customer_id}) == 0


# -- PAY-03: a captain's QR pays exactly the cars that owe ------------------

async def _mixed_visit(db, cleanup):
    """A two-car visit: car A on a pass (₹0), car B paid at the door."""
    hatch = await get_hatchback_type_id(db)
    star = await slug_id(db, "services", "star-wash")
    center_id = await make_service_center(db)
    cleanup.append(("service_centers", {"_id": ObjectId(center_id)}))
    customer_id = await new_customer(db, cleanup)
    address_id = await make_address(db, customer_id)
    captain_id = await make_captain(db, center_id)
    cleanup.append(("users", {"_id": ObjectId(captain_id)}))
    sub = await UserSubscriptionService(db).subscribe(customer_id, SubscribeRequest(plan_id=await pass_plan_id(db, star), vehicle_type=hatch, service_id=star))
    svc = BookingService(db)
    when, slots = await open_slots(svc, center_id)
    visit = await svc.create_booking_group(customer_id, BookingGroupCreateRequest(
        vehicles=[GroupVehicleRequest(vehicle_type=hatch, service_ids=[star], subscription_id=sub["id"]),
                  GroupVehicleRequest(vehicle_type=hatch, service_ids=[star])],
        address_id=address_id, scheduled_date=when, scheduled_slot=slots[0], payment_method=PaymentMethod.CASH), source="whatsapp", allow_pinless=True)
    await db.bookings.update_many({"booking_group_id": visit["booking_group_id"]}, {"$set": {"status": "completed", "captain_id": captain_id}})
    plan_car, paying_car = visit["bookings"]
    assert float(plan_car["total_amount"]) == 0 and float(paying_car["total_amount"]) > 0
    return customer_id, captain_id, plan_car, paying_car


async def test_pay03_captain_qr_on_plan_car_binds_link_to_the_owed_car(db, cleanup, gw):
    _customer_id, captain_id, plan_car, paying_car = await _mixed_visit(db, cleanup)
    payments = PaymentService(db)

    # The captain opened the ₹0 plan car's job card and showed the QR.
    link = await payments.captain_payment_link(plan_car["id"], captain_id)
    order = await db.payment_orders.find_one({"razorpay_link_id": link["link_id"]})
    assert link["amount_paise"] == int(round(float(paying_car["total_amount"]) * 100))
    assert link["amount"] == round(float(paying_car["total_amount"]), 2)  # rupees (2026-10-07 follow-up)
    assert order["booking_id"] == paying_car["id"] and order["booking_ids"] == [paying_car["id"]]
    # Reopening the modal reuses the same link.
    again = await payments.captain_payment_link(plan_car["id"], captain_id)
    assert again["link_id"] == link["link_id"]

    # The customer scans and pays; the captain's poll (still on car A) settles it.
    gw.pay_link(link["link_id"])
    state = await payments.captain_check_payment(plan_car["id"], captain_id)
    assert state["payment_status"] == "paid"
    owed = await db.bookings.find_one({"_id": ObjectId(paying_car["id"])})
    assert owed["payment_status"] == "paid" and owed["payment_method"] == "online"
    order = await db.payment_orders.find_one({"razorpay_link_id": link["link_id"]})
    assert order["status"] == "paid" and not order.get("attention_reason")
    # Nothing to collect any more — no second QR, no cash.
    with pytest.raises(BadRequestException, match="Already paid"):
        await payments.captain_payment_link(plan_car["id"], captain_id)


async def test_pay03_single_car_link_always_records_the_car_it_charges(db, cleanup, gw):
    customer_id = await new_customer(db, cleanup)
    result = await db.bookings.insert_one({
        "booking_number": f"BK-FX-{next(_seq):05d}", "customer_id": customer_id, "service_center_id": "ctr-fx",
        "status": "pending", "payment_method": "cash", "payment_status": "pending", "total_amount": 349.0,
        "scheduled_date": now_ist().replace(tzinfo=None), "scheduled_slot": "09:00-12:00", "is_deleted": False, "created_at": now_ist(),
    })
    booking = await db.bookings.find_one({"_id": result.inserted_id})
    link = await PaymentService(db).create_payment_link(booking, contact_phone=None, name=None)
    order = await db.payment_orders.find_one({"razorpay_link_id": link["link_id"]})
    assert order["booking_id"] == str(result.inserted_id) and order["booking_ids"] == [str(result.inserted_id)]
    assert order["purpose"] == "booking"


# -- PAY-07 / PAY-11 / PRICE-07: coupons -----------------------------------

async def test_pay07_once_per_customer_coupon_cannot_be_redeemed_twice_concurrently(db, cleanup, gw):
    hatch = await get_hatchback_type_id(db)
    star = await slug_id(db, "services", "star-wash")
    center_id = await make_service_center(db)
    cleanup.append(("service_centers", {"_id": ObjectId(center_id)}))
    customer_id = await new_customer(db, cleanup)
    cars = [await make_vehicle(db, customer_id, hatch, is_default=i == 0) for i in range(4)]
    address_id = await make_address(db, customer_id)
    code = await insert_coupon(db, cleanup)

    attempts = []
    for i, vehicle_id in enumerate(cars):
        svc = BookingService(db)
        when, slots = await open_slots(svc, center_id, offset=2 + i)
        attempts.append(svc.create_booking(customer_id, BookingCreateRequest(
            vehicle_id=vehicle_id, address_id=address_id, service_ids=[star], scheduled_date=when, scheduled_slot=slots[0],
            payment_method=PaymentMethod.CASH, coupon_code=code), source="app", _allow_pinless=True))
    results = await asyncio.gather(*attempts, return_exceptions=True)
    booked = [r for r in results if not isinstance(r, BaseException)]
    assert len(booked) == 1, [type(r).__name__ for r in results]
    assert all(isinstance(r, BadRequestException) for r in results if isinstance(r, BaseException))
    assert await db.coupon_usages.count_documents({"user_id": customer_id}) == 1
    coupon = await db.coupons.find_one({"code": code})
    assert coupon["total_used"] == 1


async def test_pay07_record_usage_per_user_limit_is_atomic_and_reversible(db, cleanup):
    customer_id = await new_customer(db, cleanup)
    other_id = await new_customer(db, cleanup)
    code = await insert_coupon(db, cleanup, usage_limit_per_user=2, total_usage_limit=10)
    coupons = CouponService(db)
    coupon = await coupons.repo.find_by_code(code)
    coupon_id = str(coupon["_id"])

    results = await asyncio.gather(
        *[coupons.record_usage(coupon_id, customer_id, f"bk-{i}") for i in range(6)], return_exceptions=True,
    )
    assert sum(1 for r in results if not isinstance(r, BaseException)) == 2
    assert await db.coupon_usages.count_documents({"coupon_id": coupon_id, "user_id": customer_id}) == 2
    assert (await db.coupons.find_one({"_id": coupon["_id"]}))["total_used"] == 2

    # Another customer has their own allowance; a cancelled booking hands one back.
    await coupons.record_usage(coupon_id, other_id, "bk-other")
    used = [r for r in await db.coupon_usages.find({"coupon_id": coupon_id, "user_id": customer_id}).to_list(None)]
    await coupons.reverse_usage(code, customer_id, used[0]["booking_id"])
    await coupons.record_usage(coupon_id, customer_id, "bk-again")
    with pytest.raises(BadRequestException):
        await coupons.record_usage(coupon_id, customer_id, "bk-too-many")
    assert (await db.coupons.find_one({"_id": coupon["_id"]}))["total_used"] == 3


async def test_pay07_counter_starts_from_uses_recorded_before_it_existed(db, cleanup):
    customer_id = await new_customer(db, cleanup)
    code = await insert_coupon(db, cleanup, usage_limit_per_user=1, total_used=1)
    coupon = await db.coupons.find_one({"code": code})
    # A use recorded by the old code path (no per-customer counter yet).
    await db.coupon_usages.insert_one({"coupon_id": str(coupon["_id"]), "user_id": customer_id, "booking_id": "legacy"})
    with pytest.raises(BadRequestException):
        await CouponService(db).record_usage(str(coupon["_id"]), customer_id, "bk-new")


async def test_pay11_coupon_without_total_limit_field_is_usable(db, cleanup, gw):
    hatch = await get_hatchback_type_id(db)
    star = await slug_id(db, "services", "star-wash")
    center_id = await make_service_center(db)
    cleanup.append(("service_centers", {"_id": ObjectId(center_id)}))
    customer_id = await new_customer(db, cleanup)
    vehicle_id = await make_vehicle(db, customer_id, hatch)
    address_id = await make_address(db, customer_id)
    code = await insert_coupon(db, cleanup, value=50.0)
    await db.coupons.update_one({"code": code}, {"$unset": {"total_usage_limit": "", "total_used": ""}})

    svc = BookingService(db)
    when, slots = await open_slots(svc, center_id)
    booking = await svc.create_booking(customer_id, BookingCreateRequest(
        vehicle_id=vehicle_id, address_id=address_id, service_ids=[star], scheduled_date=when, scheduled_slot=slots[0],
        payment_method=PaymentMethod.CASH, coupon_code=code), source="app", _allow_pinless=True)
    assert booking["discount_amount"] == 50
    assert (await db.coupons.find_one({"code": code}))["total_used"] == 1


async def test_price07_coupon_create_refuses_percentage_over_100_and_bad_dates():
    now = datetime.now(timezone.utc)
    base = dict(code="PCT150", coupon_type="percentage", value=150, valid_from=now, valid_until=now + timedelta(days=3))
    with pytest.raises(ValidationError, match="100%"):
        CouponCreateRequest(**base)
    with pytest.raises(ValidationError, match="after the start"):
        CouponCreateRequest(**{**base, "value": 10, "valid_until": now - timedelta(days=1)})
    assert CouponCreateRequest(**{**base, "value": 100}).value == 100
    assert CouponCreateRequest(**{**base, "coupon_type": "flat", "value": 150}).value == 150


# -- PAY-08 / PAY-10: what a plan reports as paid is what was charged -------

async def test_pay08_manager_autopay_pass_reports_the_charged_amount(db, cleanup, gw):
    hatch = await get_hatchback_type_id(db)
    star = await slug_id(db, "services", "star-wash")
    center_id = await make_service_center(db)
    cleanup.append(("service_centers", {"_id": ObjectId(center_id)}))
    manager_id = await make_manager(db, center_id)
    cleanup.append(("users", {"_id": ObjectId(manager_id)}))
    phone = f"98{next(_seq):08d}"
    payload = ManagerSubscriptionOfferRequest(
        customer_phone=phone, customer_name="Autopay Buyer", plan_id=await pass_plan_id(db, star),
        vehicle_type=hatch, service_id=star, recurring=True, payment_method="link", send_whatsapp=False,
    )
    res = await PaymentService(db).manager_subscription_offer(manager_id, payload, actor_center_id=center_id, actor_role="manager")
    customer = await db.users.find_one({"phone": phone})
    track_customer(cleanup, str(customer["_id"]))
    gw.charge_mandate(res["order_id"])
    assert await PaymentService(db).sync_pending_manager_mandates() == 1

    order = await db.payment_orders.find_one({"razorpay_subscription_id": res["order_id"]})
    sub = await db.user_subscriptions.find_one({"_id": ObjectId(order["subscription_id"])})
    assert sub["amount_paid"] == order["amount_paise"] / 100
    assert UserSubscriptionService._amount_paid(sub) == order["amount_paise"] / 100


async def test_pay10_self_serve_pass_reports_the_charged_price_not_todays(db, cleanup, gw):
    hatch = await get_hatchback_type_id(db)
    star = await slug_id(db, "services", "star-wash")
    plan_id = await pass_plan_id(db, star)
    customer_id = await new_customer(db, cleanup)
    payments = PaymentService(db)
    order = await payments.create_order(customer_id, CreateOrderRequest(purpose="subscription", plan_id=plan_id, vehicle_type=hatch, service_id=star))
    service = await db.services.find_one({"_id": ObjectId(star)})
    old_price = (service.get("vehicle_type_prices") or {}).get(hatch)
    await db.services.update_one({"_id": ObjectId(star)}, {"$set": {f"vehicle_type_prices.{hatch}": 449.0}})
    try:
        payment_id = gw.pay(order["order_id"])
        result = await payments.verify_payment(customer_id, verify_req(order["order_id"], payment_id))
    finally:
        if old_price is None:
            await db.services.update_one({"_id": ObjectId(star)}, {"$unset": {f"vehicle_type_prices.{hatch}": ""}})
        else:
            await db.services.update_one({"_id": ObjectId(star)}, {"$set": {f"vehicle_type_prices.{hatch}": old_price}})
    sub = await db.user_subscriptions.find_one({"_id": ObjectId(result["subscription"]["id"])})
    assert sub["amount_paid"] == order["amount"] / 100
    assert sub["purchased_price"] == order["amount"] / 100
    assert UserSubscriptionService._amount_paid(sub) == order["amount"] / 100
