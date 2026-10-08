"""Payments remediation (2026-10-07 audit) — regression tests, part C:
PAY-13 / FE-S02 manager plan offers reused, PASS-5 a cash sale voids the
pending link, PAY-14 refund states, SOC-1 society revenue in collections,
NTF-01 / NTF-02 / NTF-04 payment messages, PERF-02 gateway thread pools.
Razorpay is the shared local stub from part A.
"""
import asyncio
import hashlib
import hmac
import json
import threading
import time

import pytest
from bson import ObjectId

from app.core.exceptions import BadRequestException
from app.models.enums import PaymentMethod
from app.schemas.booking_schema import BookingCancelRequest, BookingCreateRequest
from app.schemas.payment_schema import CreateOrderRequest, ResolveAttentionRequest
from app.schemas.subscription_schema import ManagerSubscriptionOfferRequest, ManagerSubscriptionPreviewRequest
from app.services import payment_service
from app.services.booking_service import BookingService
from app.services.payment_service import PaymentService

from tests.factories import get_hatchback_type_id, make_address, make_manager, make_service_center, make_vehicle
from tests.society_factories import activate_cash, enroll, make_center, make_resident, make_society, make_template, staff
from tests.test_fix_pay_a import (  # noqa: F401 — gw is a fixture
    WEBHOOK_SECRET,
    gw,
    new_customer,
    open_slots,
    pass_plan_id,
    slug_id,
    track_customer,
    verify_req,
)

pytestmark = pytest.mark.asyncio


def _webhook(event: dict) -> tuple[bytes, str]:
    body = json.dumps(event).encode()
    return body, hmac.new(WEBHOOK_SECRET.encode(), body, hashlib.sha256).hexdigest()


async def _manager(db, cleanup) -> tuple[str, str]:
    center_id = await make_service_center(db)
    cleanup.append(("service_centers", {"_id": ObjectId(center_id)}))
    manager_id = await make_manager(db, center_id)
    cleanup.append(("users", {"_id": ObjectId(manager_id)}))
    return center_id, manager_id


async def _offer_payload(db, phone: str, **overrides) -> ManagerSubscriptionOfferRequest:
    hatch = await get_hatchback_type_id(db)
    star = await slug_id(db, "services", "star-wash")
    return ManagerSubscriptionOfferRequest(**{
        "customer_phone": phone, "customer_name": "Plan Buyer", "plan_id": await pass_plan_id(db, star),
        "vehicle_type": hatch, "service_id": star, "recurring": False, "payment_method": "link", "send_whatsapp": False,
        **overrides,
    })


_phones = iter(range(10_000, 99_999))


def _phone() -> str:
    return f"97{next(_phones):08d}"


# -- PAY-13 / FE-S02: one open manager offer per customer + pass ------------

async def test_pay13_same_link_offer_is_reused_and_shown_in_preview(db, cleanup, gw):
    center_id, manager_id = await _manager(db, cleanup)
    phone = _phone()
    payments = PaymentService(db)
    payload = await _offer_payload(db, phone)
    first = await payments.manager_subscription_offer(manager_id, payload, actor_center_id=center_id, actor_role="manager")
    customer = await db.users.find_one({"phone": phone})
    track_customer(cleanup, str(customer["_id"]))

    again = await payments.manager_subscription_offer(manager_id, payload, actor_center_id=center_id, actor_role="manager")
    assert again["short_url"] == first["short_url"] and again["order_id"] == first["order_id"] and again["reused"] is True
    assert await db.payment_orders.count_documents({"customer_id": str(customer["_id"]), "status": "created"}) == 1
    assert len(gw.link_payloads) == 1

    preview = await payments.manager_subscription_preview(ManagerSubscriptionPreviewRequest(
        plan_id=payload.plan_id, vehicle_type=payload.vehicle_type, service_id=payload.service_id, customer_phone=phone))
    assert preview["open_offer"]["short_url"] == first["short_url"]
    assert preview["open_offer"]["id"] == first["order_id"] and preview["open_offer"]["kind"] == "link"


async def test_pay13_concurrent_link_offers_mint_one_link(db, cleanup, gw):
    center_id, manager_id = await _manager(db, cleanup)
    phone = _phone()
    payload = await _offer_payload(db, phone)
    results = await asyncio.gather(*[
        PaymentService(db).manager_subscription_offer(manager_id, payload, actor_center_id=center_id, actor_role="manager")
        for _ in range(3)
    ], return_exceptions=True)
    customer = await db.users.find_one({"phone": phone})
    track_customer(cleanup, str(customer["_id"]))
    urls = {r["short_url"] for r in results if not isinstance(r, BaseException)}
    assert len(urls) == 1, results
    assert await db.payment_orders.count_documents({"customer_id": str(customer["_id"]), "kind": "link", "status": "created"}) == 1


async def test_pay13_changed_discount_replaces_the_open_link(db, cleanup, gw):
    center_id, manager_id = await _manager(db, cleanup)
    phone = _phone()
    payments = PaymentService(db)
    first = await payments.manager_subscription_offer(manager_id, await _offer_payload(db, phone), actor_center_id=center_id, actor_role="manager")
    customer_id = str((await db.users.find_one({"phone": phone}))["_id"])
    track_customer(cleanup, customer_id)
    second = await payments.manager_subscription_offer(
        manager_id, await _offer_payload(db, phone, discount_amount=50), actor_center_id=center_id, actor_role="manager")
    assert second["short_url"] != first["short_url"] and not second.get("reused")
    old = await db.payment_orders.find_one({"_id": ObjectId(first["order_id"])})
    assert old["status"] == "voided" and old["razorpay_link_id"] in gw.cancelled_links
    assert await db.payment_orders.count_documents({"customer_id": customer_id, "status": "created"}) == 1


async def test_pay13_autopay_offer_is_reused(db, cleanup, gw):
    center_id, manager_id = await _manager(db, cleanup)
    phone = _phone()
    payments = PaymentService(db)
    payload = await _offer_payload(db, phone, recurring=True)
    first = await payments.manager_subscription_offer(manager_id, payload, actor_center_id=center_id, actor_role="manager")
    customer_id = str((await db.users.find_one({"phone": phone}))["_id"])
    track_customer(cleanup, customer_id)
    again = await payments.manager_subscription_offer(manager_id, payload, actor_center_id=center_id, actor_role="manager")
    assert again["order_id"] == first["order_id"] and again["short_url"] == first["short_url"] and again["reused"] is True
    assert len(gw.mandates) == 1


# -- PASS-5: a cash sale voids the customer's pending plan link -------------

async def test_pass5_cash_sale_voids_pending_link_and_mandate(db, cleanup, gw):
    center_id, manager_id = await _manager(db, cleanup)
    phone = _phone()
    payments = PaymentService(db)
    link = await payments.manager_subscription_offer(manager_id, await _offer_payload(db, phone), actor_center_id=center_id, actor_role="manager")
    customer_id = str((await db.users.find_one({"phone": phone}))["_id"])
    track_customer(cleanup, customer_id)
    link_doc = await db.payment_orders.find_one({"_id": ObjectId(link["order_id"])})

    cash = await payments.manager_subscription_offer(
        manager_id, await _offer_payload(db, phone, payment_method="cash"), actor_center_id=center_id, actor_role="manager")
    assert cash["kind"] == "cash"
    link_doc = await db.payment_orders.find_one({"_id": link_doc["_id"]})
    assert link_doc["status"] == "voided" and link_doc["razorpay_link_id"] in gw.cancelled_links
    assert await db.payment_orders.count_documents({"customer_id": customer_id, "status": "created"}) == 0


async def test_pass5_cash_sale_refused_when_the_link_was_just_paid(db, cleanup, gw):
    center_id, manager_id = await _manager(db, cleanup)
    phone = _phone()
    payments = PaymentService(db)
    link = await payments.manager_subscription_offer(manager_id, await _offer_payload(db, phone), actor_center_id=center_id, actor_role="manager")
    customer_id = str((await db.users.find_one({"phone": phone}))["_id"])
    track_customer(cleanup, customer_id)
    link_doc = await db.payment_orders.find_one({"_id": ObjectId(link["order_id"])})
    gw.pay_link(link_doc["razorpay_link_id"])

    with pytest.raises(BadRequestException, match="just paid"):
        await payments.manager_subscription_offer(
            manager_id, await _offer_payload(db, phone, payment_method="cash"), actor_center_id=center_id, actor_role="manager")
    subs = await db.user_subscriptions.find({"customer_id": customer_id}).to_list(None)
    assert len(subs) == 1 and subs[0]["payment_method"] == "online"
    assert await db.payment_orders.count_documents({"customer_id": customer_id, "kind": "cash"}) == 0


# -- PAY-14 / NTF-04: refunds are a state, not a silence --------------------

async def _paid_online_booking(db, cleanup, gw) -> tuple[str, dict, dict]:
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
    payments = PaymentService(db)
    order = await payments.create_order(customer_id, CreateOrderRequest(purpose="booking", booking_id=booking["id"]))
    payment_id = gw.pay(order["order_id"])
    await payments.verify_payment(customer_id, verify_req(order["order_id"], payment_id))
    paid = await db.bookings.find_one({"_id": ObjectId(booking["id"])})
    assert paid["payment_status"] == "paid"
    return customer_id, paid, order


async def test_pay14_cancelled_paid_booking_is_refunded_to_the_wallet(db, cleanup, gw):
    # Founder 2026-10-07 (wallet model, spec 1.2): a paid booking that is
    # cancelled no longer waits on the admin refund queue ("refund_due") —
    # what was paid is credited to the customer's wallet in the cancel's own
    # transaction (the booking reads "refunded", to the wallet). A payout
    # back to the bank is recorded from the wallet (manager/admin).
    from app.services.customer_wallet_service import CustomerWalletService

    customer_id, booking, _order = await _paid_online_booking(db, cleanup, gw)
    booking_id = str(booking["_id"])
    await BookingService(db).cancel_booking(booking_id, BookingCancelRequest(reason="Customer changed plans"), "admin-x", "admin")

    fresh = await db.bookings.find_one({"_id": booking["_id"]})
    assert fresh["status"] == "cancelled" and fresh["payment_status"] == "refunded" and fresh["refunded_to"] == "wallet"
    assert fresh["refunded_amount"] == booking["total_amount"]
    assert await db.payment_orders.find_one({"kind": "refund_due", "booking_id": booking_id}) is None
    assert await CustomerWalletService(db).balance(customer_id) == booking["total_amount"]
    cleanup.append(("customer_wallets", {"customer_id": customer_id}))
    cleanup.append(("customer_wallet_ledger", {"customer_id": customer_id}))

    state = await PaymentService(db).booking_payment_state(customer_id, booking_id)
    assert state["payment_status"] == "refunded" and state["refund"]["status"] == "refunded" and state["refund"]["to"] == "wallet"


async def test_pay14_refund_webhook_for_wallet_refunded_booking_debits_the_wallet_once(db, cleanup, gw):
    # The admin may still refund that payment at Razorpay (dashboard): the
    # verified refund.processed webhook then takes it back off the wallet —
    # once — so the customer is never paid back twice.
    from app.services.customer_wallet_service import CustomerWalletService

    customer_id, booking, order = await _paid_online_booking(db, cleanup, gw)
    booking_id = str(booking["_id"])
    await BookingService(db).cancel_booking(booking_id, BookingCancelRequest(reason="Rain"), "admin-x", "admin")
    cleanup.append(("customer_wallets", {"customer_id": customer_id}))
    cleanup.append(("customer_wallet_ledger", {"customer_id": customer_id}))
    wallet = CustomerWalletService(db)
    assert await wallet.balance(customer_id) == booking["total_amount"]
    payments = PaymentService(db)
    event = {"event": "refund.processed", "payload": {
        "refund": {"entity": {"id": "rfnd_fx_1", "payment_id": booking["razorpay_payment_id"], "amount": order["amount"], "status": "processed"}},
        "payment": {"entity": {"id": booking["razorpay_payment_id"], "order_id": order["order_id"], "status": "refunded"}},
    }}
    body, signature = _webhook(event)
    assert (await payments.handle_webhook(body, signature, "evt_rf_1"))["outcome"] == "refunded"
    assert (await payments.handle_webhook(body, signature, "evt_rf_1"))["status"] == "duplicate"
    # Razorpay re-sending the same refund under a new event id changes nothing.
    assert (await payments.handle_webhook(body, signature, "evt_rf_2"))["outcome"] == "duplicate_refund"
    with pytest.raises(BadRequestException):
        await payments.handle_webhook(body, "0" * 64, "evt_rf_3")

    assert await wallet.balance(customer_id) == 0
    fresh = await db.bookings.find_one({"_id": booking["_id"]})
    assert fresh["payment_status"] == "refunded"
    assert (await db.payment_orders.find_one({"razorpay_order_id": order["order_id"]}))["refunded_amount_paise"] == order["amount"]


async def test_pay14_refunded_plan_money_is_not_reported_as_revenue(db, cleanup, gw):
    hatch = await get_hatchback_type_id(db)
    star = await slug_id(db, "services", "star-wash")
    customer_id = await new_customer(db, cleanup)
    payments = PaymentService(db)
    before = (await payments.admin_collections(None, None))["subscriptions"]["online_amount"]
    order = await payments.create_order(customer_id, CreateOrderRequest(
        purpose="subscription", plan_id=await pass_plan_id(db, star), vehicle_type=hatch, service_id=star))
    payment_id = gw.pay(order["order_id"])
    await payments.verify_payment(customer_id, verify_req(order["order_id"], payment_id))
    paid = (await payments.admin_collections(None, None))["subscriptions"]["online_amount"]
    assert paid == pytest.approx(before + order["amount"] / 100)

    body, signature = _webhook({"event": "refund.processed", "payload": {"refund": {"entity": {
        "id": f"rfnd_{payment_id}", "payment_id": payment_id, "amount": order["amount"], "status": "processed"}}}})
    await payments.handle_webhook(body, signature, f"evt_{payment_id}")
    after = await payments.admin_collections(None, None)
    assert after["subscriptions"]["online_amount"] == pytest.approx(before)
    assert after["refunds"]["refunded_amount"] >= order["amount"] / 100


# -- SOC-1: society revenue is its own line in the admin roll-up ------------

async def test_soc1_admin_collections_include_society_revenue(db, cleanup, gw):
    center = await make_center(db, cleanup, spot=3)
    society = await make_society(db, cleanup, center)
    plan = await make_template(db, cleanup)
    manager_id, _ = await staff(db, cleanup, center["id"])
    cleanup.append(("payment_orders", {"purpose": "society"}))
    payments = PaymentService(db)
    before = (await payments.admin_collections(None, None))["society"]

    cash_resident = await make_resident(db, cleanup)
    cash_view = await enroll(db, society, cash_resident, plan["id"], cars=1)
    await activate_cash(db, cash_view["id"])

    online_resident = await make_resident(db, cleanup)
    online_view = await enroll(db, society, online_resident, plan["id"], cars=2)
    link = await payments.create_society_link(online_view["id"], renewal=False, actor_id=manager_id, actor_role="manager",
                                              actor_center_id=center["id"], send_whatsapp=False)
    order = await db.payment_orders.find_one({"_id": ObjectId(link["order_id"])})
    await payments._apply_link_paid(order["razorpay_link_id"], "pay_soc_fx")

    after = (await payments.admin_collections(None, None))["society"]
    assert after["cash_amount"] == pytest.approx(before["cash_amount"] + 1649)
    assert after["online_amount"] == pytest.approx(before["online_amount"] + 2 * 1649)
    assert after["count"] == before["count"] + 2


# -- NTF-01 / NTF-02: one receipt, from the path that settled ---------------

async def test_ntf01_society_link_paid_by_webhook_sends_no_booking_message(db, cleanup, gw):
    center = await make_center(db, cleanup, spot=4)
    society = await make_society(db, cleanup, center)
    plan = await make_template(db, cleanup)
    manager_id, _ = await staff(db, cleanup, center["id"])
    cleanup.append(("payment_orders", {"purpose": "society"}))
    resident = await make_resident(db, cleanup)
    cleanup.append(("whatsapp_outbox", {"phone": resident["phone"]}))
    view = await enroll(db, society, resident, plan["id"], cars=1)
    link = await PaymentService(db).create_society_link(view["id"], renewal=False, actor_id=manager_id, actor_role="manager",
                                                        actor_center_id=center["id"], send_whatsapp=False)
    order = await db.payment_orders.find_one({"_id": ObjectId(link["order_id"])})
    body, signature = _webhook({"event": "payment_link.paid", "payload": {
        "payment_link": {"entity": {"id": order["razorpay_link_id"], "status": "paid"}},
        "payment": {"entity": {"id": "pay_soc_wh", "status": "captured"}},
    }})
    assert (await PaymentService(db).handle_webhook(body, signature, f"evt_{order['razorpay_link_id']}"))["outcome"] == "paid"
    await asyncio.sleep(0.05)
    assert await db.whatsapp_outbox.count_documents({"phone": resident["phone"], "message": {"$regex": "None|fully paid"}}) == 0
    assert await db.notifications.count_documents({"user_id": str(resident["_id"]), "title": "Society plan active"}) == 1


async def test_ntf02_link_paid_sends_one_receipt_across_callback_webhook_and_sweep(db, cleanup, gw):
    from app.core.config import settings

    customer_id = await new_customer(db, cleanup)
    result = await db.bookings.insert_one({
        "booking_number": f"BK-RCPT-{str(ObjectId())[-6:]}", "customer_id": customer_id, "service_center_id": "ctr-fx",
        "status": "pending", "payment_method": "cash", "payment_status": "pending", "total_amount": 349.0,
        "scheduled_date": payment_service.now_ist().replace(tzinfo=None), "scheduled_slot": "09:00-12:00",
        "is_deleted": False, "created_at": payment_service.now_ist(),
    })
    booking = await db.bookings.find_one({"_id": result.inserted_id})
    payments = PaymentService(db)
    link = await payments.create_payment_link(booking, contact_phone=None, name=None)
    order = await db.payment_orders.find_one({"razorpay_link_id": link["link_id"]})
    payment_id = gw.pay_link(link["link_id"])

    msg = f"{link['link_id']}|{order['reference_id']}|paid|{payment_id}"
    callback = {
        "razorpay_payment_link_id": link["link_id"], "razorpay_payment_link_reference_id": order["reference_id"],
        "razorpay_payment_link_status": "paid", "razorpay_payment_id": payment_id,
        "razorpay_signature": hmac.new(settings.RAZORPAY_KEY_SECRET.encode(), msg.encode(), hashlib.sha256).hexdigest(),
    }
    body, signature = _webhook({"event": "payment_link.paid", "payload": {
        "payment_link": {"entity": {"id": link["link_id"], "status": "paid"}},
        "payment": {"entity": {"id": payment_id, "status": "captured"}},
    }})
    outcomes = await asyncio.gather(
        PaymentService(db).verify_link_callback(callback),
        PaymentService(db).handle_webhook(body, signature, f"evt_{link['link_id']}"),
        PaymentService(db).sync_pending_links(),
        PaymentService(db).verify_link_callback(callback),
        return_exceptions=True,
    )
    assert not any(isinstance(o, BaseException) for o in outcomes), outcomes
    assert (await db.bookings.find_one({"_id": result.inserted_id}))["payment_status"] == "paid"
    receipts = await db.notifications.find({"user_id": customer_id, "title": "Payment Received"}).to_list(None)
    assert len(receipts) == 1 and "₹349" in receipts[0]["message"]


async def test_ntf02_pay_later_checkout_sends_one_receipt(db, cleanup, gw):
    hatch = await get_hatchback_type_id(db)
    star = await slug_id(db, "services", "star-wash")
    center_id = await make_service_center(db)
    cleanup.append(("service_centers", {"_id": ObjectId(center_id)}))
    customer_id = await new_customer(db, cleanup)
    vehicle_id = await make_vehicle(db, customer_id, hatch)
    address_id = await make_address(db, customer_id)
    svc = BookingService(db)
    when, slots = await open_slots(svc, center_id)
    # Booked as cash (already confirmed), paid online later.
    booking = await svc.create_booking(customer_id, BookingCreateRequest(
        vehicle_id=vehicle_id, address_id=address_id, service_ids=[star], scheduled_date=when, scheduled_slot=slots[0],
        payment_method=PaymentMethod.CASH), source="app", _allow_pinless=True)
    payments = PaymentService(db)
    order = await payments.create_order(customer_id, CreateOrderRequest(purpose="booking", booking_id=booking["id"]))
    payment_id = gw.pay(order["order_id"])
    body, signature = _webhook({"event": "payment.captured", "payload": {"payment": {"entity": {
        "id": payment_id, "order_id": order["order_id"], "status": "captured", "amount": order["amount"]}}}})
    doc = await db.payment_orders.find_one({"razorpay_order_id": order["order_id"]})
    outcomes = await asyncio.gather(
        PaymentService(db).verify_payment(customer_id, verify_req(order["order_id"], payment_id)),
        PaymentService(db).handle_webhook(body, signature, f"evt_{order['order_id']}"),
        PaymentService(db)._apply_order_payments(doc, gw.order.payments(order["order_id"]), gw, via="sweep"),
        return_exceptions=True,
    )
    assert not any(isinstance(o, BaseException) for o in outcomes), outcomes
    assert (await db.bookings.find_one({"_id": ObjectId(booking["id"])}))["payment_status"] == "paid"
    receipts = await db.notifications.find({"user_id": customer_id, "title": "Payment Received"}).to_list(None)
    assert len(receipts) == 1 and f"₹{order['amount'] // 100}" in receipts[0]["message"]


async def test_ntf02_payment_that_confirms_a_booking_sends_one_message_not_two(db, cleanup, gw):
    customer_id, booking, order = await _paid_online_booking(db, cleanup, gw)
    body, signature = _webhook({"event": "payment.captured", "payload": {"payment": {"entity": {
        "id": booking["razorpay_payment_id"], "order_id": order["order_id"], "status": "captured", "amount": order["amount"]}}}})
    await PaymentService(db).handle_webhook(body, signature, f"evt_late_{order['order_id']}")
    # "Booking confirmed" (sent by the payment) is the one confirmation.
    assert await db.notifications.count_documents({"user_id": customer_id}) == 1
    assert await db.notifications.count_documents({"user_id": customer_id, "title": "Payment Received"}) == 0


# -- PERF-02: status polls can't starve order creation ----------------------

async def test_perf02_polls_run_on_their_own_pool_with_short_timeouts(gw):
    release = threading.Event()
    seen: dict[str, object] = {}

    def blocking_poll():
        seen["poll_timeout"] = payment_service._rzp_thread_timeout()
        release.wait(5)

    def quick_request():
        seen["request_thread"] = threading.current_thread().name
        seen["request_timeout"] = payment_service._rzp_thread_timeout()
        return "ok"

    async def poll():
        with payment_service._polling():
            await payment_service._rzp(blocking_poll)

    polls = [asyncio.create_task(poll()) for _ in range(payment_service._RZP_WORKERS + payment_service._RZP_POLL_WORKERS)]
    try:
        await asyncio.sleep(0.1)
        started = time.monotonic()
        assert await asyncio.wait_for(payment_service._rzp(quick_request), timeout=2) == "ok"
        assert time.monotonic() - started < 1
    finally:
        release.set()
        await asyncio.gather(*polls)
    assert not str(seen["request_thread"]).startswith("razorpay-poll")
    assert seen["poll_timeout"] == payment_service._RZP_POLL_TIMEOUT
    assert seen["request_timeout"] == payment_service._RZP_TIMEOUT


async def test_pay14_concurrent_refund_events_apply_once(db, cleanup, gw):
    _customer_id, booking, order = await _paid_online_booking(db, cleanup, gw)
    booking_id = str(booking["_id"])
    await BookingService(db).cancel_booking(booking_id, BookingCancelRequest(reason="Rain"), "admin-x", "admin")
    body, signature = _webhook({"event": "refund.processed", "payload": {"refund": {"entity": {
        "id": f"rfnd_{booking_id}", "payment_id": booking["razorpay_payment_id"], "amount": order["amount"], "status": "processed"}}}})
    outcomes = await asyncio.gather(*[
        PaymentService(db).handle_webhook(body, signature, f"evt_{booking_id}_{i}") for i in range(4)
    ])
    assert sorted(o["outcome"] for o in outcomes) == ["duplicate_refund"] * 3 + ["refunded"]
    # Wallet model: the cancel credited the wallet; the one refund took it back once.
    from app.services.customer_wallet_service import CustomerWalletService

    assert await CustomerWalletService(db).balance(_customer_id) == 0
    paid_order = await db.payment_orders.find_one({"razorpay_order_id": order["order_id"]})
    assert paid_order["refunded_amount_paise"] == order["amount"]
    assert (await db.bookings.find_one({"_id": booking["_id"]}))["payment_status"] == "refunded"


async def test_pass5_simultaneous_cash_sales_grant_one_pass(db, cleanup, gw):
    center_id, manager_id = await _manager(db, cleanup)
    phone = _phone()
    payload = await _offer_payload(db, phone, payment_method="cash")
    results = await asyncio.gather(*[
        PaymentService(db).manager_subscription_offer(manager_id, payload, actor_center_id=center_id, actor_role="manager")
        for _ in range(3)
    ], return_exceptions=True)
    customer_id = str((await db.users.find_one({"phone": phone}))["_id"])
    track_customer(cleanup, customer_id)
    assert sum(1 for r in results if not isinstance(r, BaseException)) == 1, results
    assert await db.user_subscriptions.count_documents({"customer_id": customer_id}) == 1
    assert await db.payment_orders.count_documents({"customer_id": customer_id, "kind": "cash"}) == 1
