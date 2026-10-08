"""
"Payment must work perfectly — if it fails, handle it correctly": every
way money can reach Razorpay without our browser verify running, and every
way it can arrive twice or too late.

  - paid, verify never ran → the reconciliation sweep settles it
    (booking confirmed / plan created) through the same claim as verify;
  - the payment-window expiry asks Razorpay before releasing a slot;
  - a second order can't be minted over an unverified payment;
  - paid twice (two orders, or one order twice) → the extra is parked
    for a refund, never double-applied, and admins are told;
  - webhooks: signature-gated, deduped, and idempotent with verify in
    either order;
  - failed attempts are recorded and shown, and change nothing paid.

Stubbed client only (the suite's standing rule): nothing reaches Razorpay.
"""
import hashlib
import hmac
import itertools
import json
from datetime import timedelta

import pytest
import requests
from bson import ObjectId

from app.core.exceptions import BadRequestException, NotFoundException
from app.models.enums import BookingStatus
from app.schemas.booking_schema import BookingCreateRequest
from app.schemas.payment_schema import CreateOrderRequest, PaymentFailureReport, VerifyPaymentRequest
from app.services import payment_service
from app.services.booking_service import BookingService
from app.services.payment_service import PaymentService, _expected_signature
from app.utils.timezone import now_ist

from tests.factories import (
    get_hatchback_type_id,
    make_customer,
    make_customer_with_vehicle,
    make_service_center,
    make_subscription_plan,
)

pytestmark = pytest.mark.asyncio

_seq = itertools.count(1)


class _Orders:
    def __init__(self):
        self.by_order: dict[str, list[dict]] = {}
        self.amounts: dict[str, int] = {}
        self.lookups: list[str] = []

    def create(self, payload):
        order_id = f"order_rc_{next(_seq):06d}"
        self.by_order[order_id] = []
        self.amounts[order_id] = payload["amount"]
        return {"id": order_id, **payload}

    def payments(self, order_id):
        self.lookups.append(order_id)
        return {"entity": "collection", "items": list(self.by_order.get(order_id, []))}

    def pay(self, order_id, status="captured", amount=None, **extra) -> str:
        # Razorpay records every payment with its amount — the order's own
        # unless a test says otherwise (settlement now checks it, PAY-09).
        payment_id = f"pay_rc_{next(_seq):06d}"
        self.by_order.setdefault(order_id, []).append(
            {"id": payment_id, "status": status, "order_id": order_id, "amount": self.amounts.get(order_id) if amount is None else amount,
             "currency": "INR", "created_at": next(_seq), **extra}
        )
        return payment_id


class _Payments:
    def __init__(self, orders: _Orders):
        self.orders = orders
        self.captured: list[str] = []

    def fetch(self, payment_id):
        """verify asks Razorpay what the signed payment is (PAY-09)."""
        for items in self.orders.by_order.values():
            for p in items:
                if p["id"] == payment_id:
                    return dict(p)
        raise RuntimeError(f"The id provided does not exist: {payment_id}")

    def capture(self, payment_id, amount, data):
        for items in self.orders.by_order.values():
            for p in items:
                if p["id"] == payment_id:
                    p["status"] = "captured"
        self.captured.append(payment_id)
        return {"id": payment_id, "status": "captured"}


class _Links:
    def __init__(self):
        self.status: dict[str, str] = {}
        self.cancelled: list[str] = []

    def create(self, payload):
        link_id = f"plink_rc_{next(_seq):06d}"
        self.status[link_id] = "created"
        return {"id": link_id, "short_url": f"https://rzp.io/l/{link_id}", **payload}

    def fetch(self, link_id):
        status = self.status.get(link_id, "created")
        return {"id": link_id, "status": status, "payments": [{"payment_id": f"pay_{link_id}"}] if status == "paid" else []}

    def cancel(self, link_id):
        if self.status.get(link_id) == "paid":
            raise RuntimeError("Payment link cannot be cancelled as it is already paid")
        self.status[link_id] = "cancelled"
        self.cancelled.append(link_id)
        return {"id": link_id, "status": "cancelled"}


class _Client:
    def __init__(self):
        self.order = _Orders()
        self.payment = _Payments(self.order)
        self.payment_link = _Links()


@pytest.fixture
def rzp(monkeypatch):
    client = _Client()
    monkeypatch.setattr(payment_service.settings, "RAZORPAY_KEY_ID", "rzp_test_stub")
    monkeypatch.setattr(payment_service.settings, "RAZORPAY_KEY_SECRET", "stub_secret_key")
    monkeypatch.setattr(payment_service, "_razorpay_client", lambda: client)
    monkeypatch.setattr(payment_service, "_sweeps_paused_until", 0.0)
    return client


@pytest.fixture
async def rig(db, cleanup):
    hatchback = await get_hatchback_type_id(db)
    center_id = await make_service_center(db)
    cleanup.append(("service_centers", {"_id": ObjectId(center_id)}))
    customer_id, vehicle_id, address_id = await make_customer_with_vehicle(db, hatchback)
    cleanup.append(("users", {"_id": ObjectId(customer_id)}))
    cleanup.append(("vehicles", {"owner_id": customer_id}))
    cleanup.append(("addresses", {"owner_id": customer_id}))
    cleanup.append(("bookings", {"customer_id": customer_id}))
    cleanup.append(("payment_orders", {"customer_id": customer_id}))
    cleanup.append(("notifications", {"user_id": customer_id}))
    service = await db.services.find_one({"is_addon": {"$ne": True}, "is_deleted": {"$ne": True}})
    return {
        "db": db, "customer_id": customer_id, "vehicle_id": vehicle_id,
        "address_id": address_id, "service_id": str(service["_id"]), "center_id": center_id,
    }


async def _parked_booking(rig, *, day_offset=2) -> dict:
    """A real self-service online booking — AWAITING_PAYMENT, slot held."""
    svc = BookingService(rig["db"])
    when = (now_ist().date() + timedelta(days=day_offset)).isoformat()
    slots = await svc.available_slots(rig["center_id"], when)
    slot = next((s["key"] for s in slots if s["status"] == "available"), None)
    if slot is None:
        pytest.skip(f"no bookable slot left for {when}")
    booking = await svc.create_booking(
        rig["customer_id"],
        BookingCreateRequest(
            vehicle_id=rig["vehicle_id"], address_id=rig["address_id"], service_ids=[rig["service_id"]],
            scheduled_date=when, scheduled_slot=slot, payment_method="online",
        ),
    )
    assert booking["status"] == BookingStatus.AWAITING_PAYMENT.value
    return booking


async def _age(db, order_id: str, minutes: int) -> None:
    await db.payment_orders.update_one(
        {"razorpay_order_id": order_id}, {"$set": {"created_at": now_ist() - timedelta(minutes=minutes)}}
    )


async def _booking(db, booking_id: str) -> dict:
    return await db.bookings.find_one({"_id": ObjectId(booking_id)})


# -- Paid, verify never ran ---------------------------------------------------


async def test_sweep_settles_a_paid_order_whose_verify_never_ran(rig, db, rzp):
    booking = await _parked_booking(rig)
    svc = PaymentService(db)
    order = await svc.create_order(rig["customer_id"], CreateOrderRequest(purpose="booking", booking_id=booking["id"]))
    payment_id = rzp.order.pay(order["order_id"])

    # Verify's head start: a fresh order isn't swept yet.
    assert await svc.sync_pending_orders() == 0
    assert order["order_id"] not in rzp.order.lookups

    await _age(db, order["order_id"], 5)
    assert await svc.sync_pending_orders() == 1
    doc = await _booking(db, booking["id"])
    assert doc["status"] == BookingStatus.PENDING.value and doc["payment_status"] == "paid"
    assert doc["razorpay_payment_id"] == payment_id
    stored = await db.payment_orders.find_one({"razorpay_order_id": order["order_id"]})
    assert stored["status"] == "paid" and stored["settled_via"] == "sweep" and "settling" not in stored

    # The browser comes back late: same success, nothing applied twice.
    late = await svc.verify_payment(rig["customer_id"], VerifyPaymentRequest(
        razorpay_order_id=order["order_id"], razorpay_payment_id=payment_id,
        razorpay_signature=_expected_signature(order["order_id"], payment_id)))
    assert late["status"] == "paid" and late["already_processed"] is True
    assert await db.payment_orders.count_documents({"status": "paid_attention", "customer_id": rig["customer_id"]}) == 0


async def test_an_unpaid_order_backs_off_instead_of_being_polled_every_pass(rig, db, rzp):
    booking = await _parked_booking(rig)
    svc = PaymentService(db)
    order = await svc.create_order(rig["customer_id"], CreateOrderRequest(purpose="booking", booking_id=booking["id"]))
    await _age(db, order["order_id"], 5)

    await svc.sync_pending_orders()
    await svc.sync_pending_orders()
    assert rzp.order.lookups.count(order["order_id"]) == 1  # not due again yet
    stored = await db.payment_orders.find_one({"razorpay_order_id": order["order_id"]})
    assert stored["checks"] == 1 and stored["next_check_at"] is not None


async def test_a_plan_paid_without_verify_is_still_created(db, cleanup, rzp):
    customer_id = await make_customer(db)
    cleanup.append(("users", {"_id": ObjectId(customer_id)}))
    cleanup.append(("user_subscriptions", {"customer_id": customer_id}))
    cleanup.append(("payment_orders", {"customer_id": customer_id}))
    cleanup.append(("notifications", {"user_id": customer_id}))
    plan_id = await make_subscription_plan(db, vehicle_types=[])
    cleanup.append(("subscription_plans", {"_id": ObjectId(plan_id)}))
    hatchback = await get_hatchback_type_id(db)
    svc = PaymentService(db)
    order = await svc.create_order(customer_id, CreateOrderRequest(purpose="subscription", plan_id=plan_id, vehicle_type=hatchback))
    payment_id = rzp.order.pay(order["order_id"])
    await _age(db, order["order_id"], 3)

    assert await svc.sync_pending_orders() == 1
    assert await db.user_subscriptions.count_documents({"customer_id": customer_id}) == 1

    # The page that was left open learns about it through the status poll
    # — with the plan and exactly one thank-you ticket between all paths.
    status = await svc.payment_status_for_customer(customer_id, order["order_id"], None)
    assert status["status"] == "paid" and status["subscription"]["plan_id"] == plan_id
    assert status["first_confirmation"] is True
    late = await svc.verify_payment(customer_id, VerifyPaymentRequest(
        razorpay_order_id=order["order_id"], razorpay_payment_id=payment_id,
        razorpay_signature=_expected_signature(order["order_id"], payment_id)))
    assert late["already_processed"] is True and late["subscription"]["plan_id"] == plan_id
    assert late["first_confirmation"] is False
    assert await db.user_subscriptions.count_documents({"customer_id": customer_id}) == 1


async def test_an_authorised_payment_is_captured_before_it_is_settled(rig, db, rzp):
    booking = await _parked_booking(rig)
    svc = PaymentService(db)
    order = await svc.create_order(rig["customer_id"], CreateOrderRequest(purpose="booking", booking_id=booking["id"]))
    payment_id = rzp.order.pay(order["order_id"], status="authorized", amount=order["amount"])
    await _age(db, order["order_id"], 5)

    assert await svc.sync_pending_orders() == 1
    assert rzp.payment.captured == [payment_id]
    assert (await _booking(db, booking["id"]))["payment_status"] == "paid"


# -- The payment window -------------------------------------------------------


async def test_expiry_confirms_a_paid_booking_instead_of_releasing_it(rig, db, rzp):
    booking = await _parked_booking(rig)
    svc = PaymentService(db)
    order = await svc.create_order(rig["customer_id"], CreateOrderRequest(purpose="booking", booking_id=booking["id"]))
    rzp.order.pay(order["order_id"])
    await _age(db, order["order_id"], 20)  # older than the checkout grace
    raw = await _booking(db, booking["id"])

    assert await svc.prepare_expiry(raw, 30) is False
    doc = await _booking(db, booking["id"])
    assert doc["status"] == BookingStatus.PENDING.value and doc["payment_status"] == "paid"


async def test_expiry_releases_only_a_truly_unpaid_booking_and_closes_its_link(rig, db, rzp):
    booking = await _parked_booking(rig)
    svc = PaymentService(db)
    raw = await _booking(db, booking["id"])
    link = await svc.create_payment_link(raw, contact_phone=None, name=None)
    order = await svc.create_order(rig["customer_id"], CreateOrderRequest(purpose="booking", booking_id=booking["id"]))

    # The customer opened checkout minutes ago — maybe still on the bank page.
    assert await svc.prepare_expiry(raw, 30) is False

    await _age(db, order["order_id"], 20)
    assert await svc.prepare_expiry(raw, 30) is True
    # The WhatsApp link can no longer take money for a released slot.
    assert rzp.payment_link.cancelled == [link["link_id"]]
    assert (await db.payment_orders.find_one({"razorpay_link_id": link["link_id"]}))["status"] == "voided"


async def test_expiry_waits_while_razorpay_is_unreachable(rig, db, rzp, monkeypatch):
    booking = await _parked_booking(rig)
    svc = PaymentService(db)
    order = await svc.create_order(rig["customer_id"], CreateOrderRequest(purpose="booking", booking_id=booking["id"]))
    await _age(db, order["order_id"], 40)

    def down(order_id):
        raise requests.exceptions.ConnectTimeout("connect timeout=5")

    monkeypatch.setattr(rzp.order, "payments", down)
    assert await svc.prepare_expiry(await _booking(db, booking["id"]), 30) is False
    assert (await _booking(db, booking["id"]))["status"] == BookingStatus.AWAITING_PAYMENT.value


async def test_expiry_with_no_checkout_at_all_needs_no_gateway_call(rig, db, rzp):
    booking = await _parked_booking(rig)
    assert await PaymentService(db).prepare_expiry(await _booking(db, booking["id"]), 30) is True
    assert rzp.order.lookups == []


# -- Paid twice ---------------------------------------------------------------


async def test_a_second_order_is_refused_when_an_earlier_one_was_paid_unverified(rig, db, rzp):
    booking = await _parked_booking(rig)
    svc = PaymentService(db)
    first = await svc.create_order(rig["customer_id"], CreateOrderRequest(purpose="booking", booking_id=booking["id"]))
    rzp.order.pay(first["order_id"])  # paid, tab closed before verify

    with pytest.raises(BadRequestException, match="already paid"):
        await svc.create_order(rig["customer_id"], CreateOrderRequest(purpose="booking", booking_id=booking["id"]))
    assert (await _booking(db, booking["id"]))["payment_status"] == "paid"
    assert await db.payment_orders.count_documents({"customer_id": rig["customer_id"], "kind": None}) == 1


async def test_two_orders_paid_for_one_booking_confirm_once_and_credit_the_second(rig, db, rzp, cleanup):
    # Founder 2026-10-07 (wallet model, spec 1.5): the second payment is no
    # longer parked for a refund — it is the customer's wallet credit.
    from app.services.customer_wallet_service import CustomerWalletService

    booking = await _parked_booking(rig)
    svc = PaymentService(db)
    first = await svc.create_order(rig["customer_id"], CreateOrderRequest(purpose="booking", booking_id=booking["id"]))
    # create-order now hands back the still-open order instead of minting a
    # second one (FE-04); two orders for one booking can still exist (one
    # minted before that, or for an amount that changed back), which is the
    # safety net this test covers — so the first is made un-reusable.
    await db.payment_orders.update_one({"razorpay_order_id": first["order_id"]}, {"$unset": {"open_key": ""}})
    second = await svc.create_order(rig["customer_id"], CreateOrderRequest(purpose="booking", booking_id=booking["id"]))
    assert second["order_id"] != first["order_id"]
    p1, p2 = rzp.order.pay(first["order_id"]), rzp.order.pay(second["order_id"])

    await svc.verify_payment(rig["customer_id"], VerifyPaymentRequest(
        razorpay_order_id=first["order_id"], razorpay_payment_id=p1, razorpay_signature=_expected_signature(first["order_id"], p1)))
    out = await svc.verify_payment(rig["customer_id"], VerifyPaymentRequest(
        razorpay_order_id=second["order_id"], razorpay_payment_id=p2, razorpay_signature=_expected_signature(second["order_id"], p2)))
    assert out["status"] == "paid"
    # A replay changes nothing.
    await svc.verify_payment(rig["customer_id"], VerifyPaymentRequest(
        razorpay_order_id=second["order_id"], razorpay_payment_id=p2, razorpay_signature=_expected_signature(second["order_id"], p2)))

    doc = await _booking(db, booking["id"])
    assert doc["razorpay_payment_id"] == p1 and doc["status"] == BookingStatus.PENDING.value
    assert doc["amount_paid"] == first["amount"] / 100
    assert await CustomerWalletService(db).balance(rig["customer_id"]) == second["amount"] / 100
    assert (await db.payment_orders.find_one({"razorpay_order_id": second["order_id"]}))["status"] == "paid"
    assert (await svc.booking_payment_state(rig["customer_id"], booking["id"]))["attention"] is None
    assert await db.notifications.count_documents({"user_id": rig["customer_id"], "title": "Wallet Credited"}) == 1


async def test_one_order_paid_twice_records_the_extra_payment_once(rig, db, rzp):
    from app.services.customer_wallet_service import CustomerWalletService

    booking = await _parked_booking(rig)
    svc = PaymentService(db)
    order = await svc.create_order(rig["customer_id"], CreateOrderRequest(purpose="booking", booking_id=booking["id"]))
    p1 = rzp.order.pay(order["order_id"])
    p2 = rzp.order.pay(order["order_id"])
    await _age(db, order["order_id"], 5)

    await svc.sync_pending_orders()
    assert (await _booking(db, booking["id"]))["razorpay_payment_id"] == p1
    dup = await db.payment_orders.find_one({"_id": f"dup_{p2}"})
    # Booking money paid twice: recorded once, credited to the wallet (spec 1.5).
    assert dup["booking_id"] == booking["id"] and dup["credited_to_wallet"] is True and dup["resolved_at"]

    # Seen again (webhook, another pass): still one record, one credit.
    await svc._apply_order_paid(await db.payment_orders.find_one({"razorpay_order_id": order["order_id"]}), p2, via="webhook")
    assert await db.payment_orders.count_documents({"kind": "duplicate_payment", "customer_id": rig["customer_id"]}) == 1
    assert await CustomerWalletService(db).balance(rig["customer_id"]) == order["amount"] / 100


async def test_paying_after_a_cancellation_is_wallet_credit_not_applied(rig, db, rzp):
    from app.services.customer_wallet_service import CustomerWalletService

    booking = await _parked_booking(rig)
    svc = PaymentService(db)
    order = await svc.create_order(rig["customer_id"], CreateOrderRequest(purpose="booking", booking_id=booking["id"]))
    from app.schemas.booking_schema import BookingCancelRequest

    await BookingService(db).cancel_booking(booking["id"], BookingCancelRequest(reason="changed my mind"), rig["customer_id"], "customer")
    rzp.order.pay(order["order_id"])
    await _age(db, order["order_id"], 5)
    await svc.sync_pending_orders()

    doc = await _booking(db, booking["id"])
    assert doc["status"] == BookingStatus.CANCELLED.value and doc["payment_status"] != "paid"
    settled = await db.payment_orders.find_one({"razorpay_order_id": order["order_id"]})
    assert settled["status"] == "paid" and settled["wallet_credit"] == order["amount"] / 100
    assert await CustomerWalletService(db).balance(rig["customer_id"]) == order["amount"] / 100


async def test_an_open_link_for_a_cancelled_booking_is_closed_by_the_sweep(rig, db, rzp):
    booking = await _parked_booking(rig)
    svc = PaymentService(db)
    link = await svc.create_payment_link(await _booking(db, booking["id"]), contact_phone=None, name=None)
    await db.bookings.update_one({"_id": ObjectId(booking["id"])}, {"$set": {"status": "cancelled"}})

    async def nobody(_order):
        raise AssertionError("nothing was paid")

    await svc.sync_pending_links(nobody)
    assert rzp.payment_link.cancelled == [link["link_id"]]


# -- Failed attempts ------------------------------------------------------------


async def test_a_failed_attempt_is_recorded_shown_and_retryable(rig, db, rzp):
    booking = await _parked_booking(rig)
    svc = PaymentService(db)
    order = await svc.create_order(rig["customer_id"], CreateOrderRequest(purpose="booking", booking_id=booking["id"]))
    await svc.record_payment_failure(rig["customer_id"], PaymentFailureReport(
        razorpay_order_id=order["order_id"], razorpay_payment_id="pay_failed_1", code="BAD_REQUEST_ERROR",
        description="Your payment was declined by the bank.", reason="payment_failed", step="payment_authorization",
    ))
    state = await svc.booking_payment_state(rig["customer_id"], booking["id"])
    assert state["last_failure"]["reason"] == "Your payment was declined by the bank."
    assert (await _booking(db, booking["id"]))["status"] == BookingStatus.AWAITING_PAYMENT.value

    with pytest.raises(NotFoundException):
        await svc.record_payment_failure(str(ObjectId()), PaymentFailureReport(razorpay_order_id=order["order_id"]))

    # Retry on the SAME order succeeds; the failure is history now.
    payment_id = rzp.order.pay(order["order_id"])
    await svc.verify_payment(rig["customer_id"], VerifyPaymentRequest(
        razorpay_order_id=order["order_id"], razorpay_payment_id=payment_id,
        razorpay_signature=_expected_signature(order["order_id"], payment_id)))
    state = await svc.booking_payment_state(rig["customer_id"], booking["id"])
    assert state["last_failure"] is None and state["payment_status"] == "paid"


async def test_status_poll_settles_as_soon_as_the_money_is_there(rig, db, rzp):
    booking = await _parked_booking(rig)
    svc = PaymentService(db)
    order = await svc.create_order(rig["customer_id"], CreateOrderRequest(purpose="booking", booking_id=booking["id"]))
    pending = await svc.payment_status_for_customer(rig["customer_id"], order["order_id"], None)
    assert pending["status"] == "pending"

    rzp.order.pay(order["order_id"])
    await db.payment_orders.update_one({"razorpay_order_id": order["order_id"]}, {"$unset": {"last_checked_at": ""}})
    done = await svc.payment_status_for_customer(rig["customer_id"], order["order_id"], None)
    assert done["status"] == "paid" and done["booking_id"] == booking["id"]
    with pytest.raises(NotFoundException):
        await svc.payment_status_for_customer(str(ObjectId()), order["order_id"], None)


async def test_an_interrupted_settlement_is_parked_for_a_human(rig, db, rzp):
    booking = await _parked_booking(rig)
    svc = PaymentService(db)
    order = await svc.create_order(rig["customer_id"], CreateOrderRequest(purpose="booking", booking_id=booking["id"]))
    await db.payment_orders.update_one(
        {"razorpay_order_id": order["order_id"]},
        {"$set": {"status": "paid", "settling": True, "paid_at": now_ist() - timedelta(minutes=30)}},
    )
    await svc.sync_pending_orders()
    stored = await db.payment_orders.find_one({"razorpay_order_id": order["order_id"]})
    assert stored["status"] == "paid_attention" and "interrupted" in stored["attention_reason"]


# -- Multi-car visit ------------------------------------------------------------


async def test_paying_one_car_of_an_unpaid_visit_charges_the_whole_visit(rig, db, rzp):
    """Otherwise one car confirms and the rest are released by the expiry."""
    first = await _parked_booking(rig)
    group_id = "grp-" + str(ObjectId())
    await db.bookings.update_one({"_id": ObjectId(first["id"])}, {"$set": {"booking_group_id": group_id}})
    twin = dict(await _booking(db, first["id"]))
    twin.pop("_id")
    # A visit's later cars ride on the first car's seat (only the seat
    # holder carries seat_key / holds_seat — uniq_visit_seat_v1).
    twin.pop("seat_key", None)
    twin.pop("holds_seat", None)
    twin["booking_number"] = twin["booking_number"] + "-2"
    twin["visit_line_key"] = str(ObjectId())  # a second car, not a duplicate of the first
    twin_id = str((await db.bookings.insert_one(twin)).inserted_id)

    order = await PaymentService(db).create_order(rig["customer_id"], CreateOrderRequest(purpose="booking", booking_id=first["id"]))
    stored = await db.payment_orders.find_one({"razorpay_order_id": order["order_id"]})
    assert stored["purpose"] == "booking_group" and stored["booking_group_id"] == group_id
    assert order["amount"] == int(round(first["total_amount"] * 100)) * 2
    assert twin_id


# -- Webhooks -------------------------------------------------------------------


def _signed(body: dict, secret: str = "whsec_test") -> tuple[bytes, str]:
    raw = json.dumps(body).encode()
    return raw, hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()


@pytest.fixture
def webhook_secret(monkeypatch):
    monkeypatch.setattr(payment_service.settings, "RAZORPAY_WEBHOOK_SECRET", "whsec_test")


async def test_webhook_before_verify_settles_and_verify_still_succeeds(rig, db, rzp, webhook_secret, cleanup):
    booking = await _parked_booking(rig)
    svc = PaymentService(db)
    order = await svc.create_order(rig["customer_id"], CreateOrderRequest(purpose="booking", booking_id=booking["id"]))
    payment_id = rzp.order.pay(order["order_id"])
    event_id = f"evt_{next(_seq)}"
    cleanup.append(("razorpay_webhook_events", {"_id": event_id}))
    raw, sig = _signed({"event": "payment.captured", "payload": {"payment": {"entity": {
        "id": payment_id, "order_id": order["order_id"], "status": "captured", "amount": order["amount"]}}}})

    assert (await svc.handle_webhook(raw, sig, event_id))["outcome"] == "paid"
    assert (await _booking(db, booking["id"]))["status"] == BookingStatus.PENDING.value
    assert (await svc.handle_webhook(raw, sig, event_id))["status"] == "duplicate"

    after = await svc.verify_payment(rig["customer_id"], VerifyPaymentRequest(
        razorpay_order_id=order["order_id"], razorpay_payment_id=payment_id,
        razorpay_signature=_expected_signature(order["order_id"], payment_id)))
    assert after["status"] == "paid" and after["already_processed"] is True


async def test_webhook_after_verify_is_a_no_op(rig, db, rzp, webhook_secret, cleanup):
    booking = await _parked_booking(rig)
    svc = PaymentService(db)
    order = await svc.create_order(rig["customer_id"], CreateOrderRequest(purpose="booking", booking_id=booking["id"]))
    payment_id = rzp.order.pay(order["order_id"])
    await svc.verify_payment(rig["customer_id"], VerifyPaymentRequest(
        razorpay_order_id=order["order_id"], razorpay_payment_id=payment_id,
        razorpay_signature=_expected_signature(order["order_id"], payment_id)))
    event_id = f"evt_{next(_seq)}"
    cleanup.append(("razorpay_webhook_events", {"_id": event_id}))
    raw, sig = _signed({"event": "order.paid", "payload": {
        "payment": {"entity": {"id": payment_id, "order_id": order["order_id"], "status": "captured"}},
        "order": {"entity": {"id": order["order_id"]}}}})
    assert (await svc.handle_webhook(raw, sig, event_id))["outcome"] == "paid"
    assert await db.payment_orders.count_documents({"customer_id": rig["customer_id"], "status": "paid_attention"}) == 0


async def test_webhook_signature_is_the_whole_trust_decision(db, rzp, webhook_secret, monkeypatch):
    svc = PaymentService(db)
    raw, sig = _signed({"event": "payment.captured", "payload": {}})
    with pytest.raises(BadRequestException):
        await svc.handle_webhook(raw, "0" * 64, "evt_forged")
    with pytest.raises(BadRequestException):
        await svc.handle_webhook(raw, None, "evt_unsigned")
    monkeypatch.setattr(payment_service.settings, "RAZORPAY_WEBHOOK_SECRET", "")
    with pytest.raises(NotFoundException):
        await svc.handle_webhook(raw, sig, "evt_disabled")


async def test_webhook_records_failures_and_settles_links(rig, db, rzp, webhook_secret, cleanup):
    booking = await _parked_booking(rig)
    svc = PaymentService(db)
    order = await svc.create_order(rig["customer_id"], CreateOrderRequest(purpose="booking", booking_id=booking["id"]))
    ids = [f"evt_{next(_seq)}" for _ in range(3)]
    cleanup.append(("razorpay_webhook_events", {"_id": {"$in": ids}}))

    raw, sig = _signed({"event": "payment.failed", "payload": {"payment": {"entity": {
        "id": "pay_wf", "order_id": order["order_id"], "status": "failed", "error_description": "Card declined"}}}})
    assert (await svc.handle_webhook(raw, sig, ids[0]))["outcome"] == "failure_recorded"
    assert (await svc.booking_payment_state(rig["customer_id"], booking["id"]))["last_failure"]["reason"] == "Card declined"

    link = await svc.create_payment_link(await _booking(db, booking["id"]), contact_phone=None, name=None)
    raw, sig = _signed({"event": "payment_link.paid", "payload": {
        "payment_link": {"entity": {"id": link["link_id"], "status": "paid"}},
        "payment": {"entity": {"id": "pay_wl", "status": "captured"}}}})
    assert (await svc.handle_webhook(raw, sig, ids[1]))["outcome"] == "paid"
    assert (await _booking(db, booking["id"]))["payment_status"] == "paid"

    raw, sig = _signed({"event": "refund.processed", "payload": {}})
    assert (await svc.handle_webhook(raw, sig, ids[2]))["outcome"] == "ignored"


async def test_webhook_route_needs_no_login_and_checks_the_signature(db, rzp, webhook_secret, cleanup):
    from httpx import ASGITransport, AsyncClient

    from app.main import app

    event_id = f"evt_{next(_seq)}"
    cleanup.append(("razorpay_webhook_events", {"_id": event_id}))
    raw, sig = _signed({"event": "payment.authorized", "payload": {}})
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        bad = await client.post("/api/v1/payments/webhook", content=raw, headers={"X-Razorpay-Signature": "f" * 64})
        assert bad.status_code == 400
        ok = await client.post(
            "/api/v1/payments/webhook", content=raw,
            headers={"X-Razorpay-Signature": sig, "X-Razorpay-Event-Id": event_id, "Content-Type": "application/json"},
        )
        assert ok.status_code == 200 and ok.json()["outcome"] == "ignored"


async def test_a_visit_with_a_car_cancelled_before_checkout_settles_cleanly(rig, db, rzp):
    first = await _parked_booking(rig)
    group_id = "grp-" + str(ObjectId())
    await db.bookings.update_one({"_id": ObjectId(first["id"])}, {"$set": {"booking_group_id": group_id}})
    base = dict(await _booking(db, first["id"]))
    base.pop("_id")
    # Later cars ride on the first car's seat (see uniq_visit_seat_v1).
    base.pop("seat_key", None)
    base.pop("holds_seat", None)
    ids = []
    for suffix, status in (("-2", "awaiting_payment"), ("-3", "cancelled")):
        car = {**base, "booking_number": base["booking_number"] + suffix, "visit_line_key": str(ObjectId()), "status": status}
        ids.append(str((await db.bookings.insert_one(car)).inserted_id))

    svc = PaymentService(db)
    order = await svc.create_order(rig["customer_id"], CreateOrderRequest(purpose="booking_group", booking_group_id=group_id))
    assert order["amount"] == int(round(first["total_amount"] * 100)) * 2  # the cancelled car isn't charged
    payment_id = rzp.order.pay(order["order_id"])
    result = await svc.verify_payment(rig["customer_id"], VerifyPaymentRequest(
        razorpay_order_id=order["order_id"], razorpay_payment_id=payment_id,
        razorpay_signature=_expected_signature(order["order_id"], payment_id)))
    assert result["status"] == "paid" and result["settled_count"] == 2
    assert (await _booking(db, first["id"]))["payment_status"] == "paid"
    assert (await _booking(db, ids[0]))["payment_status"] == "paid"
    assert (await _booking(db, ids[1]))["payment_status"] != "paid"
