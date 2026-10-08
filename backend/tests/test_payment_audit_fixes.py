"""
Payment and plan money bugs found by the 2026-10-06 pre-production audit.
Each test is the audit's reproduction with the outcome now required to be
correct:
  - a per-service pass can't be bought at the plan's flat (cheapest) price;
  - an upgrade can't refill used washes or move to a pricier plan for free;
  - cash a captain collects on an "online" booking they were already paid
    for is debited from their wallet, and the customer's open link dies;
  - a turned-off auto-pay that Razorpay keeps charging is still seen;
  - old links and mandates stay inside the sweeps, links expire;
  - a hatchback pass doesn't wash an XUV, and a pass car can't be retyped;
  - the payment-window expiry doesn't release a slot when Razorpay errors;
  - a paid-then-cancelled booking leaves exactly one refund-due row;
  - plan revenue lands on its own IST day;
  - a manager resets passwords only for their own center's customers.
"""
import itertools
from datetime import timedelta
from types import SimpleNamespace

import pytest
import razorpay.errors
import requests
from bson import ObjectId

from app.controllers.auth_controller import AuthController
from app.core.exceptions import BadRequestException, NotFoundException
from app.models.enums import PaymentMethod
from app.schemas.booking_schema import BookingCreateRequest, PhotoCaptureRequest
from app.schemas.payment_schema import CreateOrderRequest
from app.schemas.profile_schema import VehicleUpdateRequest
from app.schemas.subscription_schema import SubscribeRequest
from app.services import payment_service
from app.services.booking_service import BookingService
from app.services.payment_service import PaymentService
from app.services.profile_service import VehicleService
from app.services.subscription_service import UserSubscriptionService
from app.utils.timezone import now_ist

from tests.factories import (
    get_hatchback_type_id,
    make_address,
    make_captain,
    make_customer,
    make_customer_with_vehicle,
    make_manager,
    make_service_center,
    make_vehicle,
    make_recorded_photo_url,
    own_upload_url,
)

pytestmark = pytest.mark.asyncio
_seq = itertools.count(1)


class _Links:
    def __init__(self):
        self.created, self.fetched, self.cancelled = [], [], []

    def create(self, payload):
        link = {"id": f"plink_fix_{next(_seq):05d}", "short_url": "https://rzp.io/l/fix", **payload}
        self.created.append(payload)
        return link

    def fetch(self, link_id):
        self.fetched.append(link_id)
        return {"id": link_id, "status": "paid", "payments": [{"payment_id": f"pay_{link_id}"}]}

    def cancel(self, link_id):
        self.cancelled.append(link_id)
        return {"id": link_id, "status": "cancelled"}


class _Orders:
    def create(self, payload):
        return {"id": f"order_fix_{next(_seq):06d}", **payload}


class _Mandates:
    def __init__(self, paid_count=1, fail_cancel=False):
        self.paid_count, self.fail_cancel = paid_count, fail_cancel
        self.fetched, self.cancels = [], 0

    def fetch(self, sid):
        self.fetched.append(sid)
        return {"id": sid, "status": "active", "paid_count": self.paid_count, "current_end": None}

    def cancel(self, sid, opts):
        self.cancels += 1
        if self.fail_cancel:
            raise requests.exceptions.ConnectionError("razorpay unreachable")
        return {"id": sid, "status": "cancelled"}


@pytest.fixture
def rzp(monkeypatch):
    client = SimpleNamespace(payment_link=_Links(), order=_Orders(), subscription=_Mandates())
    monkeypatch.setattr(payment_service.settings, "RAZORPAY_KEY_ID", "rzp_test_stub")
    monkeypatch.setattr(payment_service.settings, "RAZORPAY_KEY_SECRET", "stub_secret_key")
    monkeypatch.setattr(payment_service, "_razorpay_client", lambda: client)
    return client


async def _id(db, collection, slug):
    return str((await db[collection].find_one({"slug": slug}))["_id"])


async def _pass_plan(db, cleanup, star, *, count=4, upgrade_to=None):
    """A plan the way the admin editor makes one today: a service menu and a
    pass discount, `price` = the cheapest pass, no legacy per-type prices."""
    res = await db.subscription_plans.insert_one({
        "name": f"Fix Pass {count}", "slug": f"fix-pass-{next(_seq)}", "billing_cycle": "monthly", "price": 799.0,
        "discounted_price": None, "vehicle_type_prices": {}, "vehicle_type_discounted_prices": {},
        "included_service_ids": [star], "plan_discount_percent": 15.0, "service_pass_prices": {},
        "category_quotas": {}, "total_service_count": count, "vehicle_types": [], "upgrade_to_plan_ids": upgrade_to or [],
        "is_active": True, "is_deleted": False, "display_order": 0,
    })
    cleanup.append(("subscription_plans", {"_id": res.inserted_id}))
    return str(res.inserted_id)


async def _slot(svc, center_id, offset=2):
    when = (now_ist().date() + timedelta(days=offset)).isoformat()
    slots = await svc.available_slots(center_id, when)
    slot = next((s["key"] for s in slots if s["status"] == "available"), None)
    if slot is None:
        pytest.skip("no bookable slot left today")
    return when, slot


def _track_customer(cleanup, customer_id):
    for collection, query in (
        ("users", {"_id": ObjectId(customer_id)}), ("vehicles", {"owner_id": customer_id}), ("addresses", {"owner_id": customer_id}),
        ("bookings", {"customer_id": customer_id}), ("payment_orders", {"customer_id": customer_id}),
        ("user_subscriptions", {"customer_id": customer_id}), ("notifications", {"user_id": customer_id}),
    ):
        cleanup.append((collection, query))


async def test_pass_plan_checkout_needs_the_car_and_the_wash(db, cleanup, rzp):
    star = await _id(db, "services", "star-wash")
    xuv7 = await _id(db, "vehicle_types", "xuv-7-seater")
    plan_id = await _pass_plan(db, cleanup, star)
    customer_id = await make_customer(db)
    _track_customer(cleanup, customer_id)
    payments = PaymentService(db)

    with pytest.raises(BadRequestException, match="vehicle type"):
        await payments.create_order(customer_id, CreateOrderRequest(purpose="subscription", plan_id=plan_id))

    honest = await UserSubscriptionService(db).quote_pass(customer_id, plan_id, None, star, vehicle_type=xuv7)
    order = await payments.create_order(
        customer_id, CreateOrderRequest(purpose="subscription", plan_id=plan_id, vehicle_type=xuv7, service_id=star)
    )
    assert order["amount"] == int(round(honest["price"] * 100))


async def test_upgrade_keeps_used_washes_and_never_moves_to_a_pricier_plan(db, cleanup):
    hatch = await get_hatchback_type_id(db)
    star = await _id(db, "services", "star-wash")
    bigger = await _pass_plan(db, cleanup, star, count=8)
    same_size = await _pass_plan(db, cleanup, star, count=4)
    basic = await _pass_plan(db, cleanup, star, count=4, upgrade_to=[bigger, same_size])
    customer_id = await make_customer(db)
    _track_customer(cleanup, customer_id)
    subs = UserSubscriptionService(db)
    sub = await subs.subscribe(customer_id, SubscribeRequest(plan_id=basic, vehicle_type=hatch, service_id=star))
    await db.user_subscriptions.update_one({"_id": ObjectId(sub["id"])}, {"$set": {"remaining_service_count": 1}})  # 3 of 4 used

    with pytest.raises(BadRequestException, match="costs more"):
        await subs.upgrade(customer_id, sub["id"], bigger)
    moved = await subs.upgrade(customer_id, sub["id"], same_size)
    assert moved["remaining_service_count"] == 1  # the 3 used washes stay used
    assert await db.payment_orders.count_documents({"customer_id": customer_id}) == 0


async def test_cash_on_a_credited_online_booking_is_debited_and_its_link_dies(db, cleanup, rzp):
    hatch = await get_hatchback_type_id(db)
    center_id = await make_service_center(db)
    cleanup.append(("service_centers", {"_id": ObjectId(center_id)}))
    customer_id, vehicle_id, address_id = await make_customer_with_vehicle(db, hatch)
    _track_customer(cleanup, customer_id)
    captain_id = await make_captain(db, center_id, wallet_balance=200.0)
    cleanup += [("users", {"_id": ObjectId(captain_id)}), ("captain_wallets", {"captain_id": captain_id}), ("wallet_transactions", {"captain_id": captain_id})]
    svc = BookingService(db)
    star = await _id(db, "services", "star-wash")
    when, slot = await _slot(svc, center_id)
    # Booked "pay online" by the bot/staff: confirmed and still unpaid.
    booking = await svc.create_booking(customer_id, BookingCreateRequest(
        vehicle_id=vehicle_id, address_id=address_id, service_ids=[star], scheduled_date=when, scheduled_slot=slot,
        payment_method=PaymentMethod.ONLINE), source="whatsapp", _allow_pinless=True)
    raw = await db.bookings.find_one({"_id": ObjectId(booking["id"])})
    link = await PaymentService(db).create_payment_link(raw, contact_phone=None, name=None)
    assert rzp.payment_link.created[-1]["expire_by"] > int(now_ist().timestamp())

    await db.bookings.update_one({"_id": ObjectId(booking["id"])}, {"$set": {"captain_id": captain_id, "status": "service_started", "service_started_at": now_ist()}})
    await svc.capture_after_photo_and_complete(booking["id"], PhotoCaptureRequest(image_url=await make_recorded_photo_url(db, captain_id, "y"), latitude=22.7, longitude=75.8), captain_id)
    credited = (await db.captain_wallets.find_one({"captain_id": captain_id}))["balance"]

    await PaymentService(db).captain_collect_cash(booking["id"], captain_id)
    final = (await db.captain_wallets.find_one({"captain_id": captain_id}))["balance"]
    # Paid their earning at completion, now holding all the cash: owes it all back.
    assert final == pytest.approx(credited - booking["total_amount"])
    assert (await db.payment_orders.find_one({"razorpay_link_id": link["link_id"]}))["status"] == "voided"
    assert link["link_id"] in rzp.payment_link.cancelled


async def test_turned_off_auto_pay_that_razorpay_still_charges_is_not_lost(db, cleanup, rzp):
    hatch = await get_hatchback_type_id(db)
    star = await _id(db, "services", "star-wash")
    plan_id = await _pass_plan(db, cleanup, star)
    customer_id = await make_customer(db)
    _track_customer(cleanup, customer_id)
    rzp.subscription = _Mandates(paid_count=1, fail_cancel=True)
    mandate_id = f"sub_fix_{next(_seq)}"
    subs = UserSubscriptionService(db)
    sub = await subs.subscribe(
        customer_id, SubscribeRequest(plan_id=plan_id, vehicle_type=hatch, service_id=star, auto_renew=True), razorpay_subscription_id=mandate_id
    )
    await db.payment_orders.insert_one({
        "kind": "autopay", "razorpay_subscription_id": mandate_id, "customer_id": customer_id, "purpose": "subscription",
        "plan_id": plan_id, "vehicle_type": hatch, "service_id": star, "amount_paise": 118700, "currency": "INR",
        "status": "paid", "cycles_applied": 1, "auto_pay_active": True, "subscription_id": sub["id"], "created_at": now_ist(),
    })
    await subs.set_auto_pay(customer_id, sub["id"], False)  # the gateway cancel fails
    rzp.subscription.paid_count = 3  # ...and Razorpay keeps charging

    await PaymentService(db).sync_autopay_renewals()
    mandate = await db.payment_orders.find_one({"razorpay_subscription_id": mandate_id})
    assert mandate_id in rzp.subscription.fetched
    assert rzp.subscription.cancels >= 2  # the failed cancel is retried
    assert mandate["cycles_applied"] == 3  # both charges applied (or parked) — none silently kept


async def test_older_links_and_mandates_stay_in_the_sweeps(db, cleanup, rzp):
    customer_id = await make_customer(db)
    _track_customer(cleanup, customer_id)
    star = await _id(db, "services", "star-wash")
    plan_id = await _pass_plan(db, cleanup, star)
    await db.payment_orders.insert_one({
        "kind": "link", "purpose": "subscription", "razorpay_link_id": "plink_fix_old", "short_url": "x", "customer_id": customer_id,
        "plan_id": plan_id, "amount_paise": 79900, "status": "created", "channel": "manager", "created_at": now_ist() - timedelta(days=3),
    })
    await db.payment_orders.insert_one({
        "kind": "autopay", "purpose": "subscription", "razorpay_subscription_id": "sub_fix_old", "customer_id": customer_id,
        "plan_id": plan_id, "amount_paise": 79900, "status": "created", "cycles_applied": 0, "auto_pay_active": True,
        "channel": "manager", "created_at": now_ist() - timedelta(days=6),
    })
    payments = PaymentService(db)
    await payments.sync_pending_links(notify_customer=lambda o: None)
    await payments.sync_pending_manager_mandates()
    assert "plink_fix_old" in rzp.payment_link.fetched
    assert "sub_fix_old" in rzp.subscription.fetched


async def test_a_hatchback_pass_does_not_wash_an_xuv_and_its_car_cant_be_retyped(db, cleanup):
    hatch = await get_hatchback_type_id(db)
    xuv7 = await _id(db, "vehicle_types", "xuv-7-seater")
    star = await _id(db, "services", "star-wash")
    plan_id = await _pass_plan(db, cleanup, star)
    customer_id = await make_customer(db)
    _track_customer(cleanup, customer_id)
    address_id = await make_address(db, customer_id)
    center_id = await make_service_center(db)
    cleanup.append(("service_centers", {"_id": ObjectId(center_id)}))
    subs = UserSubscriptionService(db)
    type_pass = await subs.subscribe(customer_id, SubscribeRequest(plan_id=plan_id, vehicle_type=hatch, service_id=star))
    svc = BookingService(db)
    when, slot = await _slot(svc, center_id)
    with pytest.raises(BadRequestException, match="different vehicle type"):
        await svc.create_booking(customer_id, BookingCreateRequest(
            vehicle_type=xuv7, address_id=address_id, service_ids=[star], scheduled_date=when, scheduled_slot=slot,
            payment_method=PaymentMethod.CASH, subscription_id=type_pass["id"]))

    vehicle_id = await make_vehicle(db, customer_id, hatch)
    await subs.subscribe(customer_id, SubscribeRequest(plan_id=plan_id, vehicle_id=vehicle_id, service_id=star))
    with pytest.raises(BadRequestException, match="active plan"):
        await VehicleService(db).update(customer_id, vehicle_id, VehicleUpdateRequest(vehicle_type=xuv7))


async def test_expiry_keeps_the_slot_when_razorpay_cannot_answer(db, cleanup, rzp, monkeypatch):
    hatch = await get_hatchback_type_id(db)
    center_id = await make_service_center(db)
    cleanup.append(("service_centers", {"_id": ObjectId(center_id)}))
    customer_id, vehicle_id, address_id = await make_customer_with_vehicle(db, hatch)
    _track_customer(cleanup, customer_id)
    svc = BookingService(db)
    when, slot = await _slot(svc, center_id)
    booking = await svc.create_booking(customer_id, BookingCreateRequest(
        vehicle_id=vehicle_id, address_id=address_id, service_ids=[await _id(db, "services", "star-wash")],
        scheduled_date=when, scheduled_slot=slot, payment_method=PaymentMethod.ONLINE))
    assert booking["status"] == "awaiting_payment"
    payments = PaymentService(db)
    order = await payments.create_order(customer_id, CreateOrderRequest(purpose="booking", booking_id=booking["id"]))
    await db.payment_orders.update_one({"razorpay_order_id": order["order_id"]}, {"$set": {"created_at": now_ist() - timedelta(minutes=40)}})
    raw = await db.bookings.find_one({"_id": ObjectId(booking["id"])})

    async def server_error(self, doc, client, via):
        raise razorpay.errors.ServerError("bad gateway")

    monkeypatch.setattr(PaymentService, "_reconcile_one", server_error)
    assert await payments.prepare_expiry(raw, 30) is False  # can't tell if it was paid — keep it

    async def unknown_record(self, doc, client, via):
        raise razorpay.errors.BadRequestError("The id provided does not exist")

    monkeypatch.setattr(PaymentService, "_reconcile_one", unknown_record)
    assert await payments.prepare_expiry(raw, 30) is True  # nothing behind it — release


async def test_paid_then_cancelled_booking_leaves_one_refund_row(db, cleanup):
    customer_id = await make_customer(db)
    _track_customer(cleanup, customer_id)
    car = {
        "_id": ObjectId(), "customer_id": customer_id, "booking_number": "BKFIX1", "total_amount": 349.0,
        "payment_status": "paid", "payment_method": "online", "razorpay_payment_id": "pay_fix_1",
    }
    await db.payment_orders.insert_one({
        "razorpay_order_id": "order_fix_refund", "purpose": "booking", "booking_id": str(car["_id"]), "customer_id": customer_id,
        "amount_paise": 34900, "status": "paid", "paid_at": now_ist(), "created_at": now_ist(),
    })
    payments = PaymentService(db)
    assert await payments.flag_refund_due([car], "cancelled by customer") == 1
    assert await payments.flag_refund_due([car], "cancelled by customer") == 0  # a retry records nothing new
    row = await db.payment_orders.find_one({"kind": "refund_due", "booking_id": str(car["_id"])})
    assert row["status"] == "paid_attention" and row["amount_paise"] == 34900
    # A cash booking has nothing to refund.
    assert await payments.flag_refund_due([{**car, "_id": ObjectId(), "payment_method": "cash"}], "x") == 0


async def test_plan_revenue_lands_on_its_own_ist_day(db, cleanup):
    yesterday = now_ist().replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=1)
    sale = await db.payment_orders.insert_one({
        "purpose": "subscription", "status": "paid", "amount_paise": 123400, "kind": "cash",
        "customer_id": "fix-tz", "created_at": yesterday + timedelta(hours=2),  # 02:00 IST
    })
    cleanup.append(("payment_orders", {"_id": sale.inserted_id}))
    day = yesterday.strftime("%Y-%m-%d")
    prev = (yesterday - timedelta(days=1)).strftime("%Y-%m-%d")
    payments = PaymentService(db)
    assert (await payments.admin_collections(day, day))["subscriptions"]["cash_amount"] >= 1234
    assert (await payments.admin_collections(prev, prev))["subscriptions"]["cash_amount"] < 1234


async def test_manager_resets_passwords_only_for_their_centers_customers(db, cleanup):
    center_a = await make_service_center(db)
    center_b = await make_service_center(db)
    cleanup += [("service_centers", {"_id": ObjectId(center_a)}), ("service_centers", {"_id": ObjectId(center_b)})]
    manager_id = await make_manager(db, center_a)
    cleanup.append(("users", {"_id": ObjectId(manager_id)}))
    stranger = await make_customer(db)
    _track_customer(cleanup, stranger)
    await db.bookings.insert_one({"customer_id": stranger, "service_center_id": center_b, "status": "completed", "is_deleted": False})
    manager = SimpleNamespace(id=manager_id, role="manager", service_center_id=center_a)
    with pytest.raises(NotFoundException):
        await AuthController(db).staff_reset_customer_password(manager, stranger)
    assert (await db.users.find_one({"_id": ObjectId(stranger)})).get("must_change_password") is not True
