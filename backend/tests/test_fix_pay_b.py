"""Payments remediation (2026-10-07 audit) — regression tests, part B:
PAY-09 verify settles only captured money, FE-04 / FE-12 no second payable
order while one is confirming (and one order per target under
concurrency), PAY-12 auto-pay mandates a failed verify or a crash used to
strand. Razorpay is the shared local stub from part A.
"""
import asyncio
import hashlib
import hmac
import json
from datetime import timedelta

import pytest
from bson import ObjectId

from app.core.exceptions import AppException, BadRequestException
from app.models.enums import PaymentMethod
from app.schemas.booking_schema import BookingCreateRequest
from app.schemas.payment_schema import CreateOrderRequest, VerifyPaymentRequest
from app.services import payment_service
from app.services.booking_service import BookingService
from app.services.payment_service import PaymentService
from app.services.subscription_service import UserSubscriptionService
from app.utils.timezone import now_ist

from tests.factories import get_hatchback_type_id, make_address, make_service_center, make_vehicle
from tests.test_fix_pay_a import (  # noqa: F401 — gw is a fixture
    WEBHOOK_SECRET,
    gw,
    mandate_sig,
    new_customer,
    open_slots,
    pass_plan_id,
    slug_id,
    verify_req,
)

pytestmark = pytest.mark.asyncio


async def _online_booking(db, cleanup) -> tuple[str, dict]:
    hatch = await get_hatchback_type_id(db)
    star = await slug_id(db, "services", "star-wash")
    center_id = await make_service_center(db)
    cleanup.append(("service_centers", {"_id": ObjectId(center_id)}))
    customer_id = await new_customer(db, cleanup)
    vehicle_id = await make_vehicle(db, customer_id, hatch)
    address_id = await make_address(db, customer_id)
    svc = BookingService(db)
    when, slots = await open_slots(svc, center_id)
    booking = await svc.create_booking(customer_id, BookingCreateRequest(
        vehicle_id=vehicle_id, address_id=address_id, service_ids=[star], scheduled_date=when, scheduled_slot=slots[0],
        payment_method=PaymentMethod.ONLINE), source="app", _allow_pinless=True)
    assert booking["status"] == "awaiting_payment"
    return customer_id, booking


def _code(exc: AppException) -> tuple[int, str]:
    return exc.status_code, exc.error_code


# -- PAY-09: a genuine signature is not proof the money was captured --------

async def test_pay09_captured_payment_settles(db, cleanup, gw):
    customer_id, booking = await _online_booking(db, cleanup)
    payments = PaymentService(db)
    order = await payments.create_order(customer_id, CreateOrderRequest(purpose="booking", booking_id=booking["id"]))
    payment_id = gw.pay(order["order_id"])
    result = await payments.verify_payment(customer_id, verify_req(order["order_id"], payment_id))
    assert result["status"] == "paid"
    assert gw.fetched == [payment_id] and gw.captures == []
    fresh = await db.bookings.find_one({"_id": ObjectId(booking["id"])})
    assert fresh["payment_status"] == "paid" and fresh["status"] == "pending"


async def test_pay09_authorized_payment_is_captured_then_settled(db, cleanup, gw):
    customer_id, booking = await _online_booking(db, cleanup)
    payments = PaymentService(db)
    order = await payments.create_order(customer_id, CreateOrderRequest(purpose="booking", booking_id=booking["id"]))
    payment_id = gw.pay(order["order_id"], status="authorized")
    result = await payments.verify_payment(customer_id, verify_req(order["order_id"], payment_id))
    assert result["status"] == "paid"
    assert gw.captures == [(payment_id, order["amount"], {"currency": "INR"})]
    assert (await db.bookings.find_one({"_id": ObjectId(booking["id"])}))["payment_status"] == "paid"


async def test_pay09_authorized_payment_whose_capture_fails_stays_open(db, cleanup, gw):
    customer_id, booking = await _online_booking(db, cleanup)
    payments = PaymentService(db)
    order = await payments.create_order(customer_id, CreateOrderRequest(purpose="booking", booking_id=booking["id"]))
    payment_id = gw.pay(order["order_id"], status="authorized")
    gw.capture_error = RuntimeError("Capture failed: bank declined")
    with pytest.raises(AppException) as raised:
        await payments.verify_payment(customer_id, verify_req(order["order_id"], payment_id))
    assert _code(raised.value) == (503, "PAYMENT_CONFIRMING")
    doc = await db.payment_orders.find_one({"razorpay_order_id": order["order_id"]})
    assert doc["status"] == "created"
    fresh = await db.bookings.find_one({"_id": ObjectId(booking["id"])})
    assert fresh["payment_status"] == "pending" and fresh["status"] == "awaiting_payment"

    # Once the capture goes through, the sweep (or the next verify) settles it.
    gw.capture_error = None
    result = await payments.verify_payment(customer_id, verify_req(order["order_id"], payment_id))
    assert result["status"] == "paid"


@pytest.mark.parametrize("status", ["failed", "created", "refunded"])
async def test_pay09_payment_not_captured_never_marks_paid(db, cleanup, gw, status):
    customer_id, booking = await _online_booking(db, cleanup)
    payments = PaymentService(db)
    order = await payments.create_order(customer_id, CreateOrderRequest(purpose="booking", booking_id=booking["id"]))
    payment_id = gw.pay(order["order_id"], status=status)
    with pytest.raises(AppException):
        await payments.verify_payment(customer_id, verify_req(order["order_id"], payment_id))
    doc = await db.payment_orders.find_one({"razorpay_order_id": order["order_id"]})
    assert doc["status"] in ("created", "failed")
    assert (await db.bookings.find_one({"_id": ObjectId(booking["id"])}))["payment_status"] == "pending"
    assert gw.captures == []


async def test_pay09_amount_or_order_mismatch_never_marks_paid(db, cleanup, gw):
    customer_id, booking = await _online_booking(db, cleanup)
    payments = PaymentService(db)
    order = await payments.create_order(customer_id, CreateOrderRequest(purpose="booking", booking_id=booking["id"]))

    short = gw.pay(order["order_id"], amount=order["amount"] - 100)
    with pytest.raises(BadRequestException):
        await payments.verify_payment(customer_id, verify_req(order["order_id"], short))
    # The captured-but-wrong money is parked on its own row; the order stays payable.
    stray = await db.payment_orders.find_one({"razorpay_payment_id": short, "kind": "amount_mismatch"})
    assert stray and stray["status"] == "paid_attention"
    assert (await db.payment_orders.find_one({"razorpay_order_id": order["order_id"]}))["status"] == "created"

    # A payment on some OTHER order presented with this order's signature.
    other_order = await payments.create_order(customer_id, CreateOrderRequest(purpose="booking", booking_id=booking["id"]))
    assert other_order["order_id"] == order["order_id"]  # reused, see FE-04
    gw.orders["order_elsewhere"] = {"id": "order_elsewhere", "amount": order["amount"]}
    foreign = gw.pay("order_elsewhere")
    with pytest.raises(AppException):
        await payments.verify_payment(customer_id, verify_req(order["order_id"], foreign))
    assert (await db.bookings.find_one({"_id": ObjectId(booking["id"])}))["payment_status"] == "pending"


async def test_pay09_gateway_unreachable_leaves_the_order_open(db, cleanup, gw):
    import requests

    customer_id, booking = await _online_booking(db, cleanup)
    payments = PaymentService(db)
    order = await payments.create_order(customer_id, CreateOrderRequest(purpose="booking", booking_id=booking["id"]))
    payment_id = gw.pay(order["order_id"])
    gw.fetch_error = requests.exceptions.ConnectTimeout("razorpay down")
    with pytest.raises(AppException) as raised:
        await payments.verify_payment(customer_id, verify_req(order["order_id"], payment_id))
    assert _code(raised.value) == (503, "PAYMENT_CONFIRMING")
    assert (await db.payment_orders.find_one({"razorpay_order_id": order["order_id"]}))["status"] == "created"


async def test_pay09_webhook_settles_only_captured_events(db, cleanup, gw):
    customer_id, booking = await _online_booking(db, cleanup)
    payments = PaymentService(db)
    order = await payments.create_order(customer_id, CreateOrderRequest(purpose="booking", booking_id=booking["id"]))
    payment_id = gw.pay(order["order_id"], status="authorized")

    def event(name, status):
        body = json.dumps({"event": name, "payload": {"payment": {"entity": {
            "id": payment_id, "order_id": order["order_id"], "status": status, "amount": order["amount"]}}}}).encode()
        return body, hmac.new(WEBHOOK_SECRET.encode(), body, hashlib.sha256).hexdigest()

    body, signature = event("payment.authorized", "authorized")
    assert (await payments.handle_webhook(body, signature, f"evt_auth_{payment_id}"))["outcome"] == "ignored"
    assert (await db.payment_orders.find_one({"razorpay_order_id": order["order_id"]}))["status"] == "created"

    body, signature = event("payment.captured", "captured")
    assert (await payments.handle_webhook(body, signature, f"evt_cap_{payment_id}"))["outcome"] == "paid"
    assert (await db.bookings.find_one({"_id": ObjectId(booking["id"])}))["payment_status"] == "paid"


# -- FE-04 / FE-12: no second payable order while one is confirming ----------

async def test_fe04_open_order_is_reused_not_duplicated(db, cleanup, gw):
    customer_id, booking = await _online_booking(db, cleanup)
    payments = PaymentService(db)
    first = await payments.create_order(customer_id, CreateOrderRequest(purpose="booking", booking_id=booking["id"]))
    second = await payments.create_order(customer_id, CreateOrderRequest(purpose="booking", booking_id=booking["id"]))
    assert second["order_id"] == first["order_id"] and second["amount"] == first["amount"]
    assert second["description"] == first["description"] and second.get("reused") is True
    assert await db.payment_orders.count_documents({"booking_id": booking["id"]}) == 1


async def test_fe04_concurrent_create_order_mints_one_order(db, cleanup, gw):
    customer_id, booking = await _online_booking(db, cleanup)
    results = await asyncio.gather(
        *[PaymentService(db).create_order(customer_id, CreateOrderRequest(purpose="booking", booking_id=booking["id"])) for _ in range(4)]
    )
    assert len({r["order_id"] for r in results}) == 1
    assert await db.payment_orders.count_documents({"booking_id": booking["id"]}) == 1
    assert len(gw.orders) == 1


async def test_fe04_refuses_a_new_order_while_a_payment_is_settling_or_under_review(db, cleanup, gw):
    customer_id, booking = await _online_booking(db, cleanup)
    payments = PaymentService(db)
    order = await payments.create_order(customer_id, CreateOrderRequest(purpose="booking", booking_id=booking["id"]))

    # Captured and mid-settlement on another instance.
    await db.payment_orders.update_one({"razorpay_order_id": order["order_id"]}, {"$set": {"status": "paid", "settling": True, "paid_at": now_ist()}})
    with pytest.raises(AppException) as raised:
        await payments.create_order(customer_id, CreateOrderRequest(purpose="booking", booking_id=booking["id"]))
    assert _code(raised.value) == (409, "PAYMENT_CONFIRMING")

    # Parked for a human: still refused (the customer was told not to pay again).
    await db.payment_orders.update_one({"razorpay_order_id": order["order_id"]}, {"$set": {"status": "paid_attention"}, "$unset": {"settling": ""}})
    with pytest.raises(AppException) as raised:
        await payments.create_order(customer_id, CreateOrderRequest(purpose="booking", booking_id=booking["id"]))
    assert _code(raised.value) == (409, "PAYMENT_CONFIRMING")
    assert await db.payment_orders.count_documents({"booking_id": booking["id"]}) == 1


async def test_fe04_refuses_while_razorpay_holds_an_uncaptured_payment(db, cleanup, gw):
    customer_id, booking = await _online_booking(db, cleanup)
    payments = PaymentService(db)
    order = await payments.create_order(customer_id, CreateOrderRequest(purpose="booking", booking_id=booking["id"]))
    gw.pay(order["order_id"], status="authorized")
    gw.capture_error = RuntimeError("capture temporarily failed")
    with pytest.raises(AppException) as raised:
        await payments.create_order(customer_id, CreateOrderRequest(purpose="booking", booking_id=booking["id"]))
    assert _code(raised.value) == (409, "PAYMENT_CONFIRMING")


async def test_fe12_pass_cannot_be_rebought_while_its_payment_is_confirming(db, cleanup, gw):
    hatch = await get_hatchback_type_id(db)
    star = await slug_id(db, "services", "star-wash")
    plan_id = await pass_plan_id(db, star)
    customer_id = await new_customer(db, cleanup)
    payments = PaymentService(db)
    req = CreateOrderRequest(purpose="subscription", plan_id=plan_id, vehicle_type=hatch, service_id=star)

    # Two taps at once: one order.
    first, second = await asyncio.gather(PaymentService(db).create_order(customer_id, req), PaymentService(db).create_order(customer_id, req))
    assert first["order_id"] == second["order_id"]

    # Paid at Razorpay, but the browser never verified: buying again settles
    # the first payment instead of charging twice.
    gw.pay(first["order_id"])
    with pytest.raises(BadRequestException, match="already have"):
        await payments.create_order(customer_id, req)
    assert await db.user_subscriptions.count_documents({"customer_id": customer_id}) == 1
    assert await db.payment_orders.count_documents({"customer_id": customer_id, "purpose": "subscription"}) == 1


async def test_fe12_pass_order_refused_while_settling(db, cleanup, gw):
    hatch = await get_hatchback_type_id(db)
    star = await slug_id(db, "services", "star-wash")
    plan_id = await pass_plan_id(db, star)
    customer_id = await new_customer(db, cleanup)
    payments = PaymentService(db)
    req = CreateOrderRequest(purpose="subscription", plan_id=plan_id, vehicle_type=hatch, service_id=star)
    order = await payments.create_order(customer_id, req)
    await db.payment_orders.update_one({"razorpay_order_id": order["order_id"]}, {"$set": {"status": "paid", "settling": True, "paid_at": now_ist()}})
    with pytest.raises(AppException) as raised:
        await payments.create_order(customer_id, req)
    assert _code(raised.value) == (409, "PAYMENT_CONFIRMING")


# -- PAY-12: auto-pay mandates are never stranded --------------------------

async def _autopay_mandate(db, cleanup, gw) -> tuple[str, str]:
    hatch = await get_hatchback_type_id(db)
    star = await slug_id(db, "services", "star-wash")
    customer_id = await new_customer(db, cleanup)
    created = await PaymentService(db).create_order(customer_id, CreateOrderRequest(
        purpose="subscription", plan_id=await pass_plan_id(db, star), vehicle_type=hatch, service_id=star, auto_pay=True))
    assert created["auto_pay"] is True
    return customer_id, created["subscription_id"]


async def test_pay12_failed_verify_does_not_strand_a_mandate_razorpay_activates(db, cleanup, gw):
    customer_id, sid = await _autopay_mandate(db, cleanup, gw)
    payments = PaymentService(db)
    with pytest.raises(BadRequestException, match="signature"):
        await payments.verify_payment(customer_id, VerifyPaymentRequest(
            razorpay_subscription_id=sid, razorpay_payment_id="pay_forged", razorpay_signature="0" * 64))
    assert (await db.payment_orders.find_one({"razorpay_subscription_id": sid}))["status"] == "failed"

    gw.charge_mandate(sid)
    assert await payments.sync_pending_manager_mandates() == 1
    mandate = await db.payment_orders.find_one({"razorpay_subscription_id": sid})
    assert mandate["status"] == "paid" and mandate["subscription_id"] and not mandate.get("settling")
    sub = await db.user_subscriptions.find_one({"_id": ObjectId(mandate["subscription_id"])})
    assert sub["amount_paid"] == mandate["amount_paise"] / 100


async def test_pay12_verify_activates_only_once_razorpay_has_charged(db, cleanup, gw):
    customer_id, sid = await _autopay_mandate(db, cleanup, gw)
    payments = PaymentService(db)
    genuine = VerifyPaymentRequest(razorpay_subscription_id=sid, razorpay_payment_id="pay_auth", razorpay_signature=mandate_sig(sid, "pay_auth"))
    with pytest.raises(AppException) as raised:
        await payments.verify_payment(customer_id, genuine)
    assert _code(raised.value) == (503, "PAYMENT_CONFIRMING")
    assert await db.user_subscriptions.count_documents({"customer_id": customer_id}) == 0

    gw.charge_mandate(sid)
    result = await payments.verify_payment(customer_id, genuine)
    assert result["status"] == "paid" and result["auto_pay"] is True and result["subscription"]["id"]
    again = await payments.verify_payment(customer_id, genuine)
    assert again["already_processed"] is True
    assert await db.user_subscriptions.count_documents({"customer_id": customer_id}) == 1


class _Crash(BaseException):
    """The instance dying mid-request (not an ordinary error the code would catch)."""


async def test_pay12_crash_between_mandate_claim_and_subscribe_is_flagged(db, cleanup, gw, monkeypatch):
    customer_id, sid = await _autopay_mandate(db, cleanup, gw)
    gw.charge_mandate(sid)

    async def dies(*args, **kwargs):
        raise _Crash()

    monkeypatch.setattr(UserSubscriptionService, "subscribe", dies)
    with pytest.raises(_Crash):
        await PaymentService(db).sync_pending_manager_mandates()
    mandate = await db.payment_orders.find_one({"razorpay_subscription_id": sid})
    assert mandate["status"] == "paid" and mandate["settling"] is True

    await db.payment_orders.update_one({"_id": mandate["_id"]}, {"$set": {"paid_at": now_ist() - timedelta(minutes=11)}})
    await PaymentService(db).sync_pending_orders()
    mandate = await db.payment_orders.find_one({"_id": mandate["_id"]})
    assert mandate["status"] == "paid_attention" and not mandate.get("settling")
    # Its future charges can't land on a plan that was never created.
    assert mandate["auto_pay_active"] is False and sid in gw.cancelled_mandates
