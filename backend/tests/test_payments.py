"""
Razorpay integration — pure-logic tests (no external API calls, per the
suite's standing rule): signature verification math, the atomic
paid-exactly-once claim, server-side amount resolution, and the
online-only subscription rule.
"""
import itertools

import pytest
from bson import ObjectId

from app.core.exceptions import BadRequestException, NotFoundException
from app.schemas.payment_schema import CreateOrderRequest, VerifyPaymentRequest
from app.services import payment_service
from app.services.payment_service import PaymentService, _expected_signature
from app.utils.timezone import now_ist

from tests.factories import get_hatchback_type_id, make_customer, make_service_center, make_subscription_plan

pytestmark = pytest.mark.asyncio


_stub_seq = itertools.count(1)


class _StubOrders:
    def create(self, payload):
        assert payload["amount"] >= 100 and payload["currency"] == "INR"
        return {"id": f"order_stub_{next(_stub_seq):06d}", **payload}


class _StubLinks:
    def __init__(self):
        self.status = "created"

    def create(self, payload):
        assert payload["amount"] >= 100 and payload["notify"] == {"sms": False, "email": False}
        return {"id": f"plink_stub_{payload['reference_id'][-6:]}", "short_url": "https://rzp.io/l/stub", **payload}

    def fetch(self, link_id):
        return {"id": link_id, "status": self.status, "payments": [{"payment_id": "pay_linkpay"}]}


class _StubClient:
    order = _StubOrders()

    def __init__(self):
        self.payment_link = _StubLinks()


async def _seed_booking(db, customer_id, *, method="online", status="pending", total=349.0):
    res = await db.bookings.insert_one({
        "booking_number": f"BK-PAYT-{str(ObjectId())[-6:]}",
        "customer_id": customer_id, "service_center_id": "ctr-pay", "status": "pending",
        "payment_method": method, "payment_status": status, "total_amount": total,
        "scheduled_date": now_ist().replace(tzinfo=None), "scheduled_slot": "09:00-12:00",
        "is_deleted": False, "created_at": now_ist(),
    })
    return str(res.inserted_id)


async def test_booking_payment_verify_is_signature_gated_and_idempotent(db, cleanup, monkeypatch):
    customer_id = await make_customer(db)
    cleanup.append(("users", {"_id": ObjectId(customer_id)}))
    booking_id = await _seed_booking(db, customer_id)
    cleanup.append(("bookings", {"_id": ObjectId(booking_id)}))
    cleanup.append(("payment_orders", {"customer_id": customer_id}))

    monkeypatch.setattr(payment_service, "_razorpay_client", lambda: _StubClient())
    svc = PaymentService(db)
    order = await svc.create_order(customer_id, CreateOrderRequest(purpose="booking", booking_id=booking_id))
    assert order["amount"] == 34900  # server-resolved from the booking, in paise
    assert order["currency"] == "INR"

    # Forged signature: 400, booking stays unpaid, order marked failed.
    with pytest.raises(BadRequestException, match="signature"):
        await svc.verify_payment(customer_id, VerifyPaymentRequest(
            razorpay_order_id=order["order_id"], razorpay_payment_id="pay_x", razorpay_signature="f" * 64))
    booking = await db.bookings.find_one({"_id": ObjectId(booking_id)})
    assert booking["payment_status"] == "pending"

    # Real signature: booking flips to paid exactly once.
    good = _expected_signature(order["order_id"], "pay_x")
    result = await svc.verify_payment(customer_id, VerifyPaymentRequest(
        razorpay_order_id=order["order_id"], razorpay_payment_id="pay_x", razorpay_signature=good))
    assert result["status"] == "paid" and result["purpose"] == "booking"
    booking = await db.bookings.find_one({"_id": ObjectId(booking_id)})
    assert booking["payment_status"] == "paid" and booking["razorpay_payment_id"] == "pay_x"

    # Replay: idempotent success, no double side effects.
    replay = await svc.verify_payment(customer_id, VerifyPaymentRequest(
        razorpay_order_id=order["order_id"], razorpay_payment_id="pay_x", razorpay_signature=good))
    assert replay.get("already_processed") is True

    # Another customer can't verify someone else's order.
    attacker = await make_customer(db)
    cleanup.append(("users", {"_id": ObjectId(attacker)}))
    with pytest.raises(NotFoundException):
        await svc.verify_payment(attacker, VerifyPaymentRequest(
            razorpay_order_id=order["order_id"], razorpay_payment_id="pay_x", razorpay_signature=good))


async def test_subscription_created_only_after_verified_payment(db, cleanup, monkeypatch):
    customer_id = await make_customer(db)
    cleanup.append(("users", {"_id": ObjectId(customer_id)}))
    cleanup.append(("user_subscriptions", {"customer_id": customer_id}))
    cleanup.append(("payment_orders", {"customer_id": customer_id}))
    plan_id = await make_subscription_plan(db, vehicle_types=[])
    cleanup.append(("subscription_plans", {"_id": ObjectId(plan_id)}))
    hatchback = await get_hatchback_type_id(db)

    monkeypatch.setattr(payment_service, "_razorpay_client", lambda: _StubClient())
    svc = PaymentService(db)
    order = await svc.create_order(customer_id, CreateOrderRequest(purpose="subscription", plan_id=plan_id, vehicle_type=hatchback))
    assert order["amount"] == 49900  # the plan's tier price (₹499), never the client's number

    # Nothing exists before verify.
    assert await db.user_subscriptions.count_documents({"customer_id": customer_id}) == 0

    good = _expected_signature(order["order_id"], "pay_sub")
    result = await svc.verify_payment(customer_id, VerifyPaymentRequest(
        razorpay_order_id=order["order_id"], razorpay_payment_id="pay_sub", razorpay_signature=good))
    assert result["purpose"] == "subscription" and result["subscription"]["plan_id"] == plan_id
    assert await db.user_subscriptions.count_documents({"customer_id": customer_id}) == 1

    # Replay must not mint a second subscription.
    await svc.verify_payment(customer_id, VerifyPaymentRequest(
        razorpay_order_id=order["order_id"], razorpay_payment_id="pay_sub", razorpay_signature=good))
    assert await db.user_subscriptions.count_documents({"customer_id": customer_id}) == 1


async def test_whatsapp_payment_link_callback_and_sweep(db, cleanup, monkeypatch):
    """The WhatsApp path: a payment LINK settles through either the
    signature-verified browser callback or the Razorpay-API sweep —
    exactly once, whichever lands first."""
    import hashlib
    import hmac as hmac_mod

    from app.core.config import settings

    customer_id = await make_customer(db)
    cleanup.append(("users", {"_id": ObjectId(customer_id)}))
    cleanup.append(("payment_orders", {"customer_id": customer_id}))
    booking_id = await _seed_booking(db, customer_id)
    cleanup.append(("bookings", {"_id": ObjectId(booking_id)}))
    booking = await db.bookings.find_one({"_id": ObjectId(booking_id)})

    stub = _StubClient()
    monkeypatch.setattr(payment_service, "_razorpay_client", lambda: stub)
    svc = PaymentService(db)
    link = await svc.create_payment_link(booking, contact_phone="919800000004", name="WA Customer")
    assert link["short_url"].startswith("https://rzp.io/")
    order = await db.payment_orders.find_one({"razorpay_link_id": link["link_id"]})
    assert order["status"] == "created" and order["amount_paise"] == 34900

    # Callback with a FORGED signature: rejected, nothing applied.
    with pytest.raises(BadRequestException, match="signature"):
        await svc.verify_link_callback({
            "razorpay_payment_link_id": link["link_id"], "razorpay_payment_link_reference_id": order["reference_id"],
            "razorpay_payment_link_status": "paid", "razorpay_payment_id": "pay_l1", "razorpay_signature": "f" * 64,
        })
    assert (await db.bookings.find_one({"_id": ObjectId(booking_id)}))["payment_status"] == "pending"

    # Genuine callback signature: HMAC(link|reference|status|payment).
    msg = f"{link['link_id']}|{order['reference_id']}|paid|pay_l1"
    sig = hmac_mod.new(settings.RAZORPAY_KEY_SECRET.encode(), msg.encode(), hashlib.sha256).hexdigest()
    result = await svc.verify_link_callback({
        "razorpay_payment_link_id": link["link_id"], "razorpay_payment_link_reference_id": order["reference_id"],
        "razorpay_payment_link_status": "paid", "razorpay_payment_id": "pay_l1", "razorpay_signature": sig,
    })
    assert result["status"] == "paid"
    assert (await db.bookings.find_one({"_id": ObjectId(booking_id)}))["payment_status"] == "paid"

    # The sweep finding the same link paid must NOT double-notify.
    stub.payment_link.status = "paid"
    notified = []

    async def notify(order_doc):
        notified.append(order_doc["booking_number"])

    settled = await svc.sync_pending_links(notify)
    assert settled == 0 and notified == []

    # A second, still-pending link settles VIA the sweep (dev path: no
    # public callback URL) and notifies exactly once.
    booking2_id = await _seed_booking(db, customer_id)
    cleanup.append(("bookings", {"_id": ObjectId(booking2_id)}))
    booking2 = await db.bookings.find_one({"_id": ObjectId(booking2_id)})
    await svc.create_payment_link(booking2, contact_phone=None, name=None)
    settled = await svc.sync_pending_links(notify)
    assert settled == 1 and len(notified) == 1
    assert (await db.bookings.find_one({"_id": ObjectId(booking2_id)}))["payment_status"] == "paid"


async def test_captain_doorstep_settlement(db, cleanup, monkeypatch):
    """The founder's doorstep spec: cash tap only on the captain's OWN
    completed unpaid booking; QR link reused while pending; the status
    poll settles a Razorpay-paid link; everything answers 'already paid'
    instead of double-charging."""
    from tests.factories import make_captain

    customer_id = await make_customer(db)
    cleanup.append(("users", {"_id": ObjectId(customer_id)}))
    center_id = await make_service_center(db)
    cleanup.append(("service_centers", {"_id": ObjectId(center_id)}))
    captain_id = await make_captain(db, center_id)
    other_captain = await make_captain(db, center_id)
    cleanup.append(("users", {"_id": {"$in": [ObjectId(captain_id), ObjectId(other_captain)]}}))
    cleanup.append(("captain_wallets", {"captain_id": {"$in": [captain_id, other_captain]}}))
    cleanup.append(("payment_orders", {"customer_id": customer_id}))

    stub = _StubClient()
    monkeypatch.setattr(payment_service, "_razorpay_client", lambda: stub)
    svc = PaymentService(db)

    booking_id = await _seed_booking(db, customer_id, method="cash")
    cleanup.append(("bookings", {"_id": ObjectId(booking_id)}))
    await db.bookings.update_one({"_id": ObjectId(booking_id)}, {"$set": {"captain_id": captain_id, "status": "service_started"}})

    # Someone else's captain can't touch it; cash needs a COMPLETED wash.
    with pytest.raises(NotFoundException):
        await svc.captain_collect_cash(booking_id, other_captain)
    with pytest.raises(BadRequestException, match="Finish the service"):
        await svc.captain_collect_cash(booking_id, captain_id)

    # QR path mid-service: link minted, then REUSED on a second open.
    link1 = await svc.captain_payment_link(booking_id, captain_id)
    link2 = await svc.captain_payment_link(booking_id, captain_id)
    assert link1["link_id"] == link2["link_id"]

    # Customer scans & pays → the poll syncs it from Razorpay and settles.
    stub.payment_link.status = "paid"
    status = await svc.captain_check_payment(booking_id, captain_id)
    assert status["payment_status"] == "paid" and status["payment_method"] == "online"

    # Paid now — cash tap and fresh links both refuse.
    await db.bookings.update_one({"_id": ObjectId(booking_id)}, {"$set": {"status": "completed"}})
    with pytest.raises(BadRequestException, match="Already paid"):
        await svc.captain_collect_cash(booking_id, captain_id)
    with pytest.raises(BadRequestException, match="Already paid"):
        await svc.captain_payment_link(booking_id, captain_id)

    # And the pure-cash path on a second booking: completed + unpaid → paid-by-cash.
    b2 = await _seed_booking(db, customer_id, method="cash")
    cleanup.append(("bookings", {"_id": ObjectId(b2)}))
    await db.bookings.update_one({"_id": ObjectId(b2)}, {"$set": {"captain_id": captain_id, "status": "completed"}})
    result = await svc.captain_collect_cash(b2, captain_id)
    assert result == {"payment_status": "paid", "payment_method": "cash"}
    fresh = await db.bookings.find_one({"_id": ObjectId(b2)})
    assert fresh["cash_collected_by"] == captain_id and fresh["payment_status"] == "paid"


async def test_hack_paying_a_cancelled_or_drifted_booking_is_parked(db, cleanup, monkeypatch):
    """Money landing on a booking that changed since checkout must NEVER
    silently apply: cancelled-meanwhile and total-drift both park the
    order as needs-attention (admin queue) and leave the booking alone."""
    customer_id = await make_customer(db)
    cleanup.append(("users", {"_id": ObjectId(customer_id)}))
    cleanup.append(("payment_orders", {"customer_id": customer_id}))
    monkeypatch.setattr(payment_service, "_razorpay_client", lambda: _StubClient())
    svc = PaymentService(db)

    # Case 1: cancelled between order and verify.
    b1 = await _seed_booking(db, customer_id)
    cleanup.append(("bookings", {"_id": ObjectId(b1)}))
    order1 = await svc.create_order(customer_id, CreateOrderRequest(purpose="booking", booking_id=b1))
    await db.bookings.update_one({"_id": ObjectId(b1)}, {"$set": {"status": "cancelled"}})
    sig1 = _expected_signature(order1["order_id"], "pay_c1")
    with pytest.raises(BadRequestException, match="flagged"):
        await svc.verify_payment(customer_id, VerifyPaymentRequest(razorpay_order_id=order1["order_id"], razorpay_payment_id="pay_c1", razorpay_signature=sig1))
    fresh1 = await db.bookings.find_one({"_id": ObjectId(b1)})
    assert fresh1["payment_status"] == "pending"  # a cancelled booking never becomes "paid"
    parked1 = await db.payment_orders.find_one({"razorpay_order_id": order1["order_id"]})
    assert parked1["status"] == "paid_attention" and "cancelled" in parked1["attention_reason"]

    # Case 2: total changed between order and verify.
    b2 = await _seed_booking(db, customer_id)
    cleanup.append(("bookings", {"_id": ObjectId(b2)}))
    order2 = await svc.create_order(customer_id, CreateOrderRequest(purpose="booking", booking_id=b2))
    await db.bookings.update_one({"_id": ObjectId(b2)}, {"$set": {"total_amount": 999.0}})
    sig2 = _expected_signature(order2["order_id"], "pay_c2")
    with pytest.raises(BadRequestException):
        await svc.verify_payment(customer_id, VerifyPaymentRequest(razorpay_order_id=order2["order_id"], razorpay_payment_id="pay_c2", razorpay_signature=sig2))
    assert (await db.bookings.find_one({"_id": ObjectId(b2)}))["payment_status"] == "pending"

    # Case 3: online payment races the captain's cash tap — second settle parks.
    center_id = await make_service_center(db)
    cleanup.append(("service_centers", {"_id": ObjectId(center_id)}))
    from tests.factories import make_captain

    captain_id = await make_captain(db, center_id)
    cleanup.append(("users", {"_id": ObjectId(captain_id)}))
    cleanup.append(("captain_wallets", {"captain_id": captain_id}))
    b3 = await _seed_booking(db, customer_id)
    cleanup.append(("bookings", {"_id": ObjectId(b3)}))
    await db.bookings.update_one({"_id": ObjectId(b3)}, {"$set": {"captain_id": captain_id, "status": "completed"}})
    order3 = await svc.create_order(customer_id, CreateOrderRequest(purpose="booking", booking_id=b3))
    await svc.captain_collect_cash(b3, captain_id)  # cash lands first
    sig3 = _expected_signature(order3["order_id"], "pay_c3")
    with pytest.raises(BadRequestException):
        await svc.verify_payment(customer_id, VerifyPaymentRequest(razorpay_order_id=order3["order_id"], razorpay_payment_id="pay_c3", razorpay_signature=sig3))
    fresh3 = await db.bookings.find_one({"_id": ObjectId(b3)})
    assert fresh3["payment_method"] == "cash"  # the first settlement stands untouched
    parked3 = await db.payment_orders.find_one({"razorpay_order_id": order3["order_id"]})
    assert parked3["status"] == "paid_attention"


async def test_hack_link_paid_after_cancellation_never_pings_customer(db, cleanup, monkeypatch):
    customer_id = await make_customer(db)
    cleanup.append(("users", {"_id": ObjectId(customer_id)}))
    cleanup.append(("payment_orders", {"customer_id": customer_id}))
    stub = _StubClient()
    monkeypatch.setattr(payment_service, "_razorpay_client", lambda: stub)
    svc = PaymentService(db)

    booking_id = await _seed_booking(db, customer_id)
    cleanup.append(("bookings", {"_id": ObjectId(booking_id)}))
    booking = await db.bookings.find_one({"_id": ObjectId(booking_id)})
    await svc.create_payment_link(booking, contact_phone=None, name=None)
    await db.bookings.update_one({"_id": ObjectId(booking_id)}, {"$set": {"status": "cancelled"}})

    stub.payment_link.status = "paid"
    notified = []

    async def notify(order_doc):
        notified.append(order_doc)

    settled = await svc.sync_pending_links(notify)
    assert settled == 0 and notified == []  # no "payment received ✅" for a parked case
    assert (await db.bookings.find_one({"_id": ObjectId(booking_id)}))["payment_status"] == "pending"
    assert await db.payment_orders.count_documents({"booking_id": booking_id, "status": "paid_attention"}) == 1


async def test_hack_subscription_plan_vanishing_after_payment_is_parked(db, cleanup, monkeypatch):
    customer_id = await make_customer(db)
    cleanup.append(("users", {"_id": ObjectId(customer_id)}))
    cleanup.append(("user_subscriptions", {"customer_id": customer_id}))
    cleanup.append(("payment_orders", {"customer_id": customer_id}))
    plan_id = await make_subscription_plan(db, vehicle_types=[])
    cleanup.append(("subscription_plans", {"_id": ObjectId(plan_id)}))
    monkeypatch.setattr(payment_service, "_razorpay_client", lambda: _StubClient())
    svc = PaymentService(db)

    order = await svc.create_order(customer_id, CreateOrderRequest(purpose="subscription", plan_id=plan_id))
    await db.subscription_plans.update_one({"_id": ObjectId(plan_id)}, {"$set": {"is_active": False}})
    sig = _expected_signature(order["order_id"], "pay_dead")
    with pytest.raises(BadRequestException, match="flagged"):
        await svc.verify_payment(customer_id, VerifyPaymentRequest(razorpay_order_id=order["order_id"], razorpay_payment_id="pay_dead", razorpay_signature=sig))
    assert await db.user_subscriptions.count_documents({"customer_id": customer_id}) == 0
    parked = await db.payment_orders.find_one({"razorpay_order_id": order["order_id"]})
    assert parked["status"] == "paid_attention"


async def test_collections_reports_for_manager_and_admin(db, cleanup, monkeypatch):
    """The surveillance ledger: per-captain rows for the manager, per-center
    + attention queue + subscription revenue for the admin; cancelled
    bookings excluded; cross-center manager refused."""
    from app.core.exceptions import ForbiddenException
    from tests.factories import make_captain

    center_id = await make_service_center(db)
    cleanup.append(("service_centers", {"_id": ObjectId(center_id)}))
    cap_a = await make_captain(db, center_id)
    cap_b = await make_captain(db, center_id)
    cleanup.append(("users", {"_id": {"$in": [ObjectId(cap_a), ObjectId(cap_b)]}}))
    cleanup.append(("captain_wallets", {"captain_id": {"$in": [cap_a, cap_b]}}))
    customer_id = await make_customer(db)
    cleanup.append(("users", {"_id": ObjectId(customer_id)}))
    cleanup.append(("payment_orders", {"customer_id": customer_id}))

    async def seed(slot, captain, **over):
        res = await db.bookings.insert_one({
            "booking_number": f"BK-COLL-{slot}", "customer_id": customer_id, "service_center_id": center_id,
            "captain_id": captain, "status": "completed", "payment_method": "cash", "payment_status": "pending",
            "total_amount": 100.0, "scheduled_date": now_ist().replace(tzinfo=None, hour=0, minute=0, second=0, microsecond=0),
            "scheduled_slot": slot, "is_deleted": False, "created_at": now_ist(), **over,
        })
        cleanup.append(("bookings", {"_id": res.inserted_id}))
        return str(res.inserted_id)

    await seed("07:00-08:00", cap_a, payment_status="paid", payment_method="cash", total_amount=349.0)
    await seed("08:00-09:00", cap_a, payment_status="paid", payment_method="online", total_amount=500.0)
    await seed("09:00-10:00", cap_b, total_amount=250.0)  # completed, uncollected
    await seed("10:00-11:00", cap_a, status="cancelled", payment_status="paid", total_amount=999.0)  # must be excluded

    svc = PaymentService(db)
    report = await svc.center_collections(center_id, "manager", center_id, None, None)
    by_captain = {r["captain_id"]: r for r in report["rows"]}
    assert by_captain[cap_a]["cash_amount"] == 349.0 and by_captain[cap_a]["cash_count"] == 1
    assert by_captain[cap_a]["online_amount"] == 500.0 and by_captain[cap_a]["online_count"] == 1
    assert by_captain[cap_b]["uncollected_amount"] == 250.0 and by_captain[cap_b]["uncollected_count"] == 1
    assert report["totals"]["cash_amount"] == 349.0 and report["totals"]["online_amount"] == 500.0
    assert report["totals"]["uncollected_amount"] == 250.0  # the cancelled ₹999 never appears

    with pytest.raises(ForbiddenException):
        await svc.center_collections(center_id, "manager", str(ObjectId()), None, None)

    # Admin roll-up: this center's row matches, and a paid subscription
    # order lands in the subscription revenue block.
    await db.payment_orders.insert_one({
        "razorpay_order_id": "order_admin_sub", "customer_id": customer_id, "purpose": "subscription",
        "amount_paise": 49900, "currency": "INR", "status": "paid", "plan_id": "p", "created_at": now_ist(),
    })
    admin = await svc.admin_collections(None, None)
    row = next(r for r in admin["rows"] if r["service_center_id"] == center_id)
    assert row["cash_amount"] == 349.0 and row["online_amount"] == 500.0 and row["uncollected_amount"] == 250.0
    assert admin["subscriptions"]["online_amount"] >= 499.0
    assert isinstance(admin["attention"], list)


async def test_create_order_guards(db, cleanup, monkeypatch):
    customer_id = await make_customer(db)
    other_id = await make_customer(db)
    cleanup.append(("users", {"_id": {"$in": [ObjectId(customer_id), ObjectId(other_id)]}}))
    cleanup.append(("payment_orders", {"customer_id": {"$in": [customer_id, other_id]}}))
    monkeypatch.setattr(payment_service, "_razorpay_client", lambda: _StubClient())
    svc = PaymentService(db)

    # Someone else's booking: 404, never an order.
    booking_id = await _seed_booking(db, other_id)
    cleanup.append(("bookings", {"_id": ObjectId(booking_id)}))
    with pytest.raises(NotFoundException):
        await svc.create_order(customer_id, CreateOrderRequest(purpose="booking", booking_id=booking_id))

    # Founder rule: even a cash-on-delivery booking can be paid online at
    # any time — creating an order for it must WORK, not be refused.
    cash_id = await _seed_booking(db, customer_id, method="cash")
    cleanup.append(("bookings", {"_id": ObjectId(cash_id)}))
    cash_order = await svc.create_order(customer_id, CreateOrderRequest(purpose="booking", booking_id=cash_id))
    assert cash_order["amount"] == 34900

    # Already paid: refuse.
    paid_id = await _seed_booking(db, customer_id, status="paid")
    cleanup.append(("bookings", {"_id": ObjectId(paid_id)}))
    with pytest.raises(BadRequestException, match="already paid"):
        await svc.create_order(customer_id, CreateOrderRequest(purpose="booking", booking_id=paid_id))

    # Below Razorpay's 100-paise floor.
    tiny_id = await _seed_booking(db, customer_id, total=0.5)
    cleanup.append(("bookings", {"_id": ObjectId(tiny_id)}))
    with pytest.raises(BadRequestException, match="minimum"):
        await svc.create_order(customer_id, CreateOrderRequest(purpose="booking", booking_id=tiny_id))
