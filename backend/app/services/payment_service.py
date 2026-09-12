"""
Razorpay Standard Checkout — the platform's first real payment gateway.

Two-endpoint flow (see payment_routes.py):

  1. create-order: the SERVER decides the amount (never the browser) —
     a booking's frozen total, or a plan's tier price — creates a
     Razorpay order, and records it in `payment_orders`. The response
     carries the public KEY_ID so the frontend needs no payment env of
     its own.
  2. verify: the checkout modal hands the browser
     (order_id, payment_id, signature); the signature is
     HMAC-SHA256(order_id + "|" + payment_id, KEY_SECRET) and is
     recomputed and compared here. ONLY a verified signature has side
     effects: a booking flips payment_status -> paid, a subscription
     purchase is actually created. A mismatch marks the order failed
     and changes nothing else.

Business rules (founder-set): service bookings may be cash OR online;
subscription purchases are ONLINE ONLY — the old free self-subscribe
route is gated off (see subscription_routes.subscribe) and the ONLY way
a customer gets a plan themselves is through a verified payment here.
Staff assignment (manager/admin granting a plan) stays as the manual,
audited path.

A subscription can additionally be bought with AUTO-PAY: instead of an
order, checkout authorises a Razorpay *subscription* (a recurring
mandate) that re-bills every cycle on its own. See the "Auto-pay" block
below — it has its own signature message, its own verify path, and a
sweep that turns each new charge into a fresh cycle. If the gateway
can't set a mandate up, create_order silently sells the same cycle as a
one-time order instead, so a purchase never fails over it.

The `payment_orders` doc doubles as the idempotency guard: verify claims
it atomically (created -> paid), so a replayed verify can't double-apply
(double-subscribe, double-mark) — the replay just gets the same success
back.
"""
import hashlib
import hmac
import logging

from bson import ObjectId
import secrets
from datetime import datetime, timezone

from motor.motor_asyncio import AsyncIOMotorDatabase

from app.core.config import settings
from app.core.exceptions import AppException, BadRequestException, NotFoundException
from app.models.enums import NotificationType, PaymentMethod, PaymentStatus
from app.repositories.booking_repository import BookingRepository
from app.repositories.subscription_repository import SubscriptionPlanRepository
from app.schemas.payment_schema import CreateOrderRequest, VerifyPaymentRequest
from app.utils.serializers import serialize_doc
from app.utils.timezone import now_ist

logger = logging.getLogger(__name__)

# Razorpay's own floor — anything below is refused before we even call them.
MIN_ORDER_PAISE = 100

# Auto-pay (Razorpay Subscriptions) mapping from OUR billing cycle to
# Razorpay's (period, interval) pair, plus how many cycles the mandate is
# authorised for. Razorpay caps total_count per period, so these stay well
# inside the limits — when a mandate runs out of cycles the customer simply
# re-subscribes (and we surface the count on the plan card).
_AUTOPAY_SCHEDULE = {
    "monthly": {"period": "monthly", "interval": 1, "total_count": 60},
    "quarterly": {"period": "monthly", "interval": 3, "total_count": 20},
    "yearly": {"period": "yearly", "interval": 1, "total_count": 10},
}



def _razorpay_client():
    """Lazily built so tests (which never call external APIs) can run
    without credentials, and so a misconfigured deployment fails with a
    clear message instead of an SDK auth traceback."""
    if not settings.RAZORPAY_KEY_ID or not settings.RAZORPAY_KEY_SECRET:
        raise BadRequestException("Online payments aren't configured yet — please pay by cash, or contact support.")
    import razorpay

    return razorpay.Client(auth=(settings.RAZORPAY_KEY_ID, settings.RAZORPAY_KEY_SECRET))


def _expected_signature(order_id: str, payment_id: str) -> str:
    return hmac.new(
        settings.RAZORPAY_KEY_SECRET.encode(), f"{order_id}|{payment_id}".encode(), hashlib.sha256
    ).hexdigest()


def _expected_subscription_signature(subscription_id: str, payment_id: str) -> str:
    """Razorpay signs a SUBSCRIPTION checkout the other way round from an
    order — payment_id first, then the subscription id. Getting the operand
    order wrong here would reject every legitimate auto-pay setup."""
    return hmac.new(
        settings.RAZORPAY_KEY_SECRET.encode(), f"{payment_id}|{subscription_id}".encode(), hashlib.sha256
    ).hexdigest()


class PaymentService:
    def __init__(self, db: AsyncIOMotorDatabase):
        self.db = db
        self.orders = db.payment_orders
        self.booking_repo = BookingRepository(db)
        self.plan_repo = SubscriptionPlanRepository(db)

    async def create_order(self, customer_id: str, payload: CreateOrderRequest) -> dict:
        """Resolve what's being paid for and how much (server-side, from
        the stored record — the client only names the thing), then mint a
        Razorpay order for it."""
        auto_pay_unavailable = False
        if payload.purpose == "booking":
            amount_paise, description, reference = await self._booking_order(customer_id, payload)
        elif payload.purpose == "booking_group":
            amount_paise, description, reference = await self._booking_group_order(customer_id, payload)
        else:
            amount_paise, description, reference = await self._subscription_order(customer_id, payload)
            if payload.auto_pay:
                # Every business check (phone verified, plan active, tier
                # valid, no live copy of this plan already) has just run
                # inside _subscription_order — anything raised from HERE on is
                # the gateway declining to set up a mandate (Subscriptions not
                # enabled on the account, plan-creation limits, an API blip).
                # That must never block the purchase: fall through and sell
                # the cycle as a one-time payment instead.
                try:
                    return await self._autopay_mandate(customer_id, payload, amount_paise, description)
                except AppException:
                    raise
                except Exception:
                    logger.warning("Auto-pay mandate unavailable — falling back to a one-time order", exc_info=True)
                    auto_pay_unavailable = True

        if amount_paise < MIN_ORDER_PAISE:
            raise BadRequestException("This amount is below the minimum for online payment (₹1).")

        client = _razorpay_client()
        try:
            order = client.order.create(
                {
                    "amount": amount_paise,
                    "currency": "INR",
                    "receipt": reference["receipt"],
                    "notes": {"purpose": payload.purpose, **{k: v for k, v in reference.items() if k != "receipt"}},
                }
            )
        except Exception as exc:  # SDK raises its own error hierarchy — surface a clean 400/500 story
            raise BadRequestException(f"Couldn't start the payment — please try again. ({type(exc).__name__})") from exc

        await self.orders.insert_one(
            {
                "razorpay_order_id": order["id"],
                "customer_id": customer_id,
                "purpose": payload.purpose,
                "amount_paise": amount_paise,
                "currency": "INR",
                "status": "created",
                **reference,
                "created_at": now_ist(),
            }
        )
        return {
            "order_id": order["id"],
            "amount": amount_paise,
            "currency": "INR",
            # Public by design — the checkout modal needs it in the browser.
            "key_id": settings.RAZORPAY_KEY_ID,
            "description": description,
            # So the checkout can say "TEST MODE" out loud — a test payment
            # that looks identical to a real one is how fake revenue gets
            # reported as real.
            "mode": settings.razorpay_mode,
            "auto_pay": False,
            # True only when auto-pay was ASKED for and the gateway couldn't
            # set the mandate up — the UI says "this one cycle only" instead
            # of silently promising a renewal that will never happen.
            "auto_pay_unavailable": auto_pay_unavailable,
        }

    async def _booking_order(self, customer_id: str, payload: CreateOrderRequest) -> tuple[int, str, dict]:
        if not payload.booking_id:
            raise BadRequestException("booking_id is required for a booking payment")
        booking = await self.booking_repo.find_by_id(payload.booking_id)
        if not booking or booking.get("customer_id") != customer_id:
            raise NotFoundException("Booking not found")
        # Founder rule: ANY unpaid booking can be paid online at any point
        # — before, during, or after the service — even one that was
        # booked as cash on delivery (verify flips the method to online).
        if booking.get("payment_status") == PaymentStatus.PAID.value:
            raise BadRequestException("This booking is already paid.")
        if booking.get("status") == "cancelled":
            raise BadRequestException("This booking was cancelled — there's nothing to pay.")
        amount_paise = int(round(float(booking.get("total_amount") or 0) * 100))
        description = f"Booking {booking['booking_number']}"
        return amount_paise, description, {"receipt": booking["booking_number"], "booking_id": payload.booking_id}

    async def _booking_group_order(self, customer_id: str, payload: CreateOrderRequest) -> tuple[int, str, dict]:
        """One order for a whole multi-vehicle visit. The amount is the sum
        of what its still-unpaid cars owe, read from the stored bookings —
        the browser only names the visit."""
        if not payload.booking_group_id:
            raise BadRequestException("booking_group_id is required for a visit payment")
        bookings = await self.booking_repo.collection.find(
            {"booking_group_id": payload.booking_group_id, "is_deleted": {"$ne": True}}
        ).to_list(length=20)
        if not bookings:
            raise NotFoundException("Booking not found")
        if any(b.get("customer_id") != customer_id for b in bookings):
            raise NotFoundException("Booking not found")

        payable = [
            b for b in bookings
            if b.get("payment_status") != PaymentStatus.PAID.value and b.get("status") != "cancelled"
        ]
        if not payable:
            raise BadRequestException("This visit is already paid.")
        amount_paise = sum(int(round(float(b.get("total_amount") or 0) * 100)) for b in payable)
        numbers = ", ".join(b["booking_number"] for b in payable)
        return amount_paise, f"{len(payable)} vehicles — {numbers}"[:255], {
            "receipt": f"grp-{payload.booking_group_id[-12:]}",
            "booking_group_id": payload.booking_group_id,
        }

    async def _subscription_order(self, customer_id: str, payload: CreateOrderRequest) -> tuple[int, str, dict]:
        # Same eligibility the (now payment-gated) subscribe path enforces,
        # checked EARLY so the customer isn't charged for a purchase the
        # verify step would then refuse to create.
        from app.schemas.subscription_schema import SubscribeRequest
        from app.services.subscription_service import UserSubscriptionService, resolve_plan_price

        if not payload.plan_id:
            raise BadRequestException("plan_id is required for a subscription payment")
        plan = await self.plan_repo.find_by_id(payload.plan_id)
        if not plan or not plan.get("is_active", True):
            raise NotFoundException("Subscription plan not found or inactive")
        subscriptions = UserSubscriptionService(self.db)
        # Dry-run the exact create the verify step will perform — raises the
        # same phone-verification / wrong-car / already-has-a-pass errors,
        # before any money moves.
        await subscriptions.validate_purchase(
            customer_id,
            SubscribeRequest(
                plan_id=payload.plan_id, vehicle_id=payload.vehicle_id,
                service_id=payload.service_id, vehicle_type=payload.vehicle_type,
            ),
        )
        if payload.vehicle_id:
            # A PASS is priced from the car's type and the chosen service —
            # by the same function that will quote it in the purchase sheet,
            # so the customer is never charged a number they weren't shown.
            quote = await subscriptions.quote_pass(customer_id, payload.plan_id, payload.vehicle_id, payload.service_id)
            price = quote["price"]
            description = f"{plan['name']} — {quote['service_name']}"
        else:
            price = resolve_plan_price(plan, payload.vehicle_type)
            description = f"{plan['name']} subscription"
        amount_paise = int(round(float(price) * 100))
        return amount_paise, description, {
            "receipt": f"sub-{plan.get('slug', payload.plan_id)}"[:40],
            "plan_id": payload.plan_id,
            "vehicle_id": payload.vehicle_id,
            "service_id": payload.service_id,
            "vehicle_type": payload.vehicle_type,
        }

    async def verify_payment(self, customer_id: str, payload: VerifyPaymentRequest) -> dict:
        """Recompute the signature and, only on an exact match, apply the
        purchase. The atomic created->paid claim on payment_orders makes
        this replay-safe."""
        if payload.razorpay_subscription_id:
            return await self._verify_autopay(customer_id, payload)
        order = await self.orders.find_one({"razorpay_order_id": payload.razorpay_order_id})
        if not order or order.get("customer_id") != customer_id:
            raise NotFoundException("Payment order not found")

        expected = _expected_signature(payload.razorpay_order_id, payload.razorpay_payment_id)
        if not hmac.compare_digest(expected, payload.razorpay_signature):
            # Record the attempt but change NOTHING else — a forged or
            # corrupted signature must never mark anything as paid.
            await self.orders.update_one(
                {"razorpay_order_id": payload.razorpay_order_id, "status": "created"},
                {"$set": {"status": "failed", "failed_at": now_ist(), "razorpay_payment_id": payload.razorpay_payment_id}},
            )
            raise BadRequestException("Payment verification failed — the signature doesn't match. No money was applied.")

        # Atomic claim: exactly one verify applies the side effects.
        claimed = await self.orders.find_one_and_update(
            {"razorpay_order_id": payload.razorpay_order_id, "status": {"$in": ["created", "failed"]}},
            {"$set": {"status": "paid", "paid_at": now_ist(), "razorpay_payment_id": payload.razorpay_payment_id, "razorpay_signature": payload.razorpay_signature}},
        )
        if not claimed:
            # Already applied by an earlier verify — idempotent success.
            fresh = await self.orders.find_one({"razorpay_order_id": payload.razorpay_order_id})
            return {"status": "paid", "purpose": order["purpose"], "already_processed": True, "subscription_id": (fresh or {}).get("subscription_id")}

        if order["purpose"] == "booking":
            applied = await self._settle_booking_payment(
                order, payload.razorpay_order_id, payload.razorpay_payment_id, via="order"
            )
            if not applied:
                raise BadRequestException(
                    "Payment received, but this booking can't be marked paid automatically "
                    "(it changed since the payment started). Our team has been flagged and will sort the refund/credit."
                )
            return {"status": "paid", "purpose": "booking", "booking_id": order["booking_id"]}

        if order["purpose"] == "booking_group":
            settled, failed = await self._settle_booking_group(order, payload.razorpay_order_id, payload.razorpay_payment_id)
            if failed:
                # Some cars on the visit couldn't be marked paid. The money is
                # in and the rest of the visit IS paid, so this needs a human
                # rather than a rollback — the order is parked with exactly
                # which cars are short.
                raise BadRequestException(
                    "Payment received, but not every vehicle on this visit could be marked paid "
                    "(it changed since the payment started). Our team has been flagged and will sort it out."
                )
            return {
                "status": "paid",
                "purpose": "booking_group",
                "booking_group_id": order["booking_group_id"],
                "settled_count": settled,
            }

        # Subscription: the verified payment IS the purchase — create it now.
        from app.schemas.subscription_schema import SubscribeRequest
        from app.services.subscription_service import UserSubscriptionService

        try:
            sub = await UserSubscriptionService(self.db).subscribe(
                customer_id,
                SubscribeRequest(
                    plan_id=order["plan_id"], vehicle_id=order.get("vehicle_id"),
                    service_id=order.get("service_id"), vehicle_type=order.get("vehicle_type"),
                ),
            )
        except Exception:
            # Money is in but the plan can't be activated (deactivated/
            # deleted between order and verify). NEVER swallow the money
            # silently: park the order where the admin collections view
            # surfaces it for a manual refund/activation.
            await self._flag_order_attention(
                {"razorpay_order_id": payload.razorpay_order_id}, "paid but subscription could not be activated"
            )
            raise BadRequestException(
                "Payment received, but the plan couldn't be activated automatically — our team has been flagged "
                "and will activate it or refund you."
            )
        await self.orders.update_one(
            {"razorpay_order_id": payload.razorpay_order_id}, {"$set": {"subscription_id": sub["id"]}}
        )
        await self._announce_subscription(customer_id, sub, renewed=False)
        return {"status": "paid", "purpose": "subscription", "subscription": serialize_doc(sub) if "_id" in sub else sub}

    # -- Auto-pay: Razorpay Subscriptions --------------------------------
    #
    # A one-time order buys ONE cycle and then goes quiet. Auto-pay instead
    # asks the customer's bank/UPI for a recurring mandate once, and Razorpay
    # charges it every cycle from then on. Three moving parts:
    #
    #   1. a Razorpay PLAN per (our plan, vehicle tier, price) — created on
    #      demand and cached in `razorpay_plans`, because Razorpay plans are
    #      immutable and a price edit must mint a new one;
    #   2. a Razorpay SUBSCRIPTION (the mandate) per purchase, opened in
    #      checkout instead of an order and verified with its own signature;
    #   3. a sweep (sync_autopay_renewals) that asks Razorpay how many cycles
    #      it has actually charged and refreshes the local card for each new
    #      one. Same callback-free, poll-the-truth design as payment links.
    #
    # Nothing here is allowed to break a purchase: if the gateway won't set a
    # mandate up, create_order falls back to a plain one-time order.

    async def _ensure_razorpay_plan(self, plan: dict, vehicle_type: str | None, amount_paise: int) -> str:
        """The Razorpay plan id for this (plan, tier, price) combination,
        created once and reused. Keyed on the PRICE too: Razorpay plans are
        immutable, so an admin re-pricing our plan has to mint a new one
        rather than silently keep billing the old amount."""
        key = {"plan_id": str(plan["_id"]), "vehicle_type": vehicle_type or "", "amount_paise": amount_paise}
        cached = await self.db.razorpay_plans.find_one(key)
        if cached and cached.get("razorpay_plan_id"):
            return cached["razorpay_plan_id"]

        schedule = _AUTOPAY_SCHEDULE.get(plan.get("billing_cycle") or "monthly", _AUTOPAY_SCHEDULE["monthly"])
        client = _razorpay_client()
        created = client.plan.create(
            {
                "period": schedule["period"],
                "interval": schedule["interval"],
                "item": {
                    "name": str(plan.get("name") or "Blussit plan")[:64],
                    "amount": amount_paise,
                    "currency": "INR",
                    "description": f"Blussit {plan.get('name')} — auto-renewing subscription"[:255],
                },
                "notes": {k: str(v) for k, v in key.items()},
            }
        )
        # upsert, not insert: two concurrent first-time buyers can both reach
        # the create above — one cached plan wins, the other Razorpay plan is
        # simply never used.
        await self.db.razorpay_plans.update_one(
            key, {"$setOnInsert": {**key, "razorpay_plan_id": created["id"], "created_at": now_ist()}}, upsert=True
        )
        winner = await self.db.razorpay_plans.find_one(key)
        return (winner or {}).get("razorpay_plan_id") or created["id"]

    async def _autopay_mandate(self, customer_id: str, payload, amount_paise: int, description: str) -> dict:
        """Mint the recurring mandate the checkout modal will authorise."""
        if amount_paise < MIN_ORDER_PAISE:
            raise BadRequestException("This amount is below the minimum for online payment (₹1).")
        plan = await self.plan_repo.find_by_id(payload.plan_id)
        if not plan:
            raise NotFoundException("Subscription plan not found or inactive")
        schedule = _AUTOPAY_SCHEDULE.get(plan.get("billing_cycle") or "monthly", _AUTOPAY_SCHEDULE["monthly"])
        rzp_plan_id = await self._ensure_razorpay_plan(plan, payload.vehicle_type, amount_paise)

        client = _razorpay_client()
        mandate = client.subscription.create(
            {
                "plan_id": rzp_plan_id,
                "total_count": schedule["total_count"],
                "quantity": 1,
                "customer_notify": 1,
                "notes": {
                    "purpose": "subscription",
                    "plan_id": str(plan["_id"]),
                    "vehicle_type": payload.vehicle_type or "",
                    "customer_id": customer_id,
                },
            }
        )
        await self.orders.insert_one(
            {
                "kind": "autopay",
                "razorpay_subscription_id": mandate["id"],
                "customer_id": customer_id,
                "purpose": "subscription",
                "plan_id": str(plan["_id"]),
                "vehicle_id": payload.vehicle_id,
                "service_id": payload.service_id,
                "vehicle_type": payload.vehicle_type,
                "amount_paise": amount_paise,
                "currency": "INR",
                "status": "created",
                # How many charges we have already turned into local plan
                # cycles — the ledger the renewal sweep advances.
                "cycles_applied": 0,
                "auto_pay_active": True,
                "created_at": now_ist(),
            }
        )
        return {
            "subscription_id": mandate["id"],
            "amount": amount_paise,
            "currency": "INR",
            "key_id": settings.RAZORPAY_KEY_ID,
            "description": description,
            "mode": settings.razorpay_mode,
            "auto_pay": True,
            "auto_pay_unavailable": False,
            "total_cycles": schedule["total_count"],
        }

    async def _verify_autopay(self, customer_id: str, payload) -> dict:
        """The auto-pay twin of verify_payment: same trust model (only a
        matching signature does anything, the created->paid claim makes it
        replay-safe), different signature message and a mandate id instead of
        an order id."""
        mandate_id = payload.razorpay_subscription_id
        order = await self.orders.find_one({"razorpay_subscription_id": mandate_id})
        if not order or order.get("customer_id") != customer_id:
            raise NotFoundException("Payment order not found")

        expected = _expected_subscription_signature(mandate_id, payload.razorpay_payment_id)
        if not hmac.compare_digest(expected, payload.razorpay_signature):
            await self.orders.update_one(
                {"razorpay_subscription_id": mandate_id, "status": "created"},
                {"$set": {"status": "failed", "failed_at": now_ist(), "razorpay_payment_id": payload.razorpay_payment_id}},
            )
            raise BadRequestException("Payment verification failed — the signature doesn't match. No money was applied.")

        claimed = await self.orders.find_one_and_update(
            {"razorpay_subscription_id": mandate_id, "status": {"$in": ["created", "failed"]}},
            {
                "$set": {
                    "status": "paid",
                    "paid_at": now_ist(),
                    "razorpay_payment_id": payload.razorpay_payment_id,
                    "razorpay_signature": payload.razorpay_signature,
                    # The authorisation charge IS cycle 1 — recorded now so
                    # the renewal sweep only ever acts on cycles 2+.
                    "cycles_applied": 1,
                }
            },
        )
        if not claimed:
            fresh = await self.orders.find_one({"razorpay_subscription_id": mandate_id})
            return {
                "status": "paid",
                "purpose": "subscription",
                "already_processed": True,
                "auto_pay": True,
                "subscription_id": (fresh or {}).get("subscription_id"),
            }

        from app.schemas.subscription_schema import SubscribeRequest
        from app.services.subscription_service import UserSubscriptionService

        try:
            sub = await UserSubscriptionService(self.db).subscribe(
                customer_id,
                SubscribeRequest(
                    plan_id=order["plan_id"], vehicle_id=order.get("vehicle_id"),
                    service_id=order.get("service_id"), vehicle_type=order.get("vehicle_type"), auto_renew=True,
                ),
                razorpay_subscription_id=mandate_id,
            )
        except Exception:
            # Money authorised but the plan can't be created. Kill the
            # mandate so it never charges again, then park it for a human —
            # exactly the treatment a one-time order gets.
            await self.cancel_autopay(mandate_id, at_cycle_end=False)
            await self.orders.update_one({"razorpay_subscription_id": mandate_id}, {"$set": {"auto_pay_active": False}})
            await self._flag_order_attention(
                {"razorpay_subscription_id": mandate_id}, "auto-pay charged but subscription could not be activated"
            )
            raise BadRequestException(
                "Payment received, but the plan couldn't be activated automatically — our team has been flagged "
                "and will activate it or refund you. Auto-pay has been stopped so you won't be charged again."
            )
        await self.orders.update_one({"razorpay_subscription_id": mandate_id}, {"$set": {"subscription_id": sub["id"]}})
        await self._announce_subscription(customer_id, sub, renewed=False)
        return {
            "status": "paid",
            "purpose": "subscription",
            "auto_pay": True,
            "subscription": serialize_doc(sub) if "_id" in sub else sub,
        }

    async def _announce_subscription(self, customer_id: str, sub: dict, *, renewed: bool) -> None:
        """The customer's confirmation for a pass bought or renewed — in-app,
        and on WhatsApp through the dedicated template once approved.
        Best-effort: money has moved and the pass is live; a messaging
        hiccup must never turn that into an error."""
        try:
            from app.services.notification_service import NotificationService
            from app.utils.timezone import to_ist

            plan = await self.plan_repo.find_by_id(str(sub.get("plan_id") or ""))
            plan_name = (plan or {}).get("name") or "Monthly pass"
            customer = await self.db.users.find_one({"_id": ObjectId(customer_id)}) if ObjectId.is_valid(customer_id) else None
            first = ((customer or {}).get("full_name") or "there").split(" ")[0]
            vehicle = await self.db.vehicles.find_one({"_id": ObjectId(sub["vehicle_id"])}) if sub.get("vehicle_id") and ObjectId.is_valid(str(sub["vehicle_id"])) else None
            vehicle_label = (
                f"{vehicle.get('brand', '')} {vehicle.get('model', '')} · {vehicle.get('registration_number', '')}".strip(" ·")
                if vehicle else "your vehicle"
            )
            end = sub.get("end_date")
            valid_till = to_ist(end).strftime("%d %b %Y") if end else "—"
            washes = str(sub.get("remaining_service_count") or sub.get("total_service_count") or "")
            notifications = NotificationService(self.db)
            if renewed:
                await notifications.notify(
                    customer_id, f"{plan_name} renewed",
                    f"Your pass renewed — valid till {valid_till}.",
                    NotificationType.SYSTEM, str(sub.get("_id") or sub.get("id") or ""),
                    wa_event="subscription_renewed", wa_params=[first, plan_name, valid_till],
                )
            else:
                await notifications.notify(
                    customer_id, f"{plan_name} is active",
                    f"{washes} washes for {vehicle_label}, valid till {valid_till}.",
                    NotificationType.SYSTEM, str(sub.get("_id") or sub.get("id") or ""),
                    wa_event="subscription_activated", wa_params=[first, plan_name, vehicle_label, washes, valid_till],
                )
        except Exception:  # noqa: BLE001
            logger.exception("Could not announce subscription for customer %s", customer_id)

    async def cancel_autopay(self, mandate_id: str, at_cycle_end: bool) -> bool:
        """Stop a mandate at Razorpay. Best-effort by design: a customer
        cancelling their plan must succeed even if Razorpay is unreachable
        this second — the renewal sweep notices the local cancellation and
        refuses to renew regardless, and re-tries the gateway cancel."""
        await self.orders.update_one(
            {"razorpay_subscription_id": mandate_id},
            {"$set": {"auto_pay_active": False, "cancel_requested_at": now_ist(), "cancel_at_cycle_end": at_cycle_end}},
        )
        if not settings.RAZORPAY_KEY_ID or not settings.RAZORPAY_KEY_SECRET:
            return False
        try:
            _razorpay_client().subscription.cancel(mandate_id, {"cancel_at_cycle_end": 1 if at_cycle_end else 0})
            return True
        except Exception:
            logger.warning("Could not cancel Razorpay mandate %s", mandate_id, exc_info=True)
            return False

    async def sync_autopay_renewals(self) -> int:
        """Reminder-loop sweep: ask Razorpay how many cycles each live
        mandate has actually charged, and turn every charge we haven't
        applied yet into a fresh cycle on the customer's plan. Server-to-
        server with our own key, so the API response IS the truth — no
        webhook, same design as the payment-link sweep."""
        if not settings.RAZORPAY_KEY_ID or not settings.RAZORPAY_KEY_SECRET:
            return 0
        # Only ask about mandates that are actually DUE a check. Polling
        # every live mandate every minute would cost one Razorpay call per
        # subscriber per minute — fine at ten subscribers, a rate-limit
        # incident at a thousand. `next_check_at` (set at the bottom of this
        # loop) keeps a quiet mandate at ~2 calls a day and only tightens to
        # every 20 minutes around its renewal date.
        mandates = await self.orders.find(
            {
                "kind": "autopay",
                "status": "paid",
                "auto_pay_active": True,
                "$or": [{"next_check_at": {"$exists": False}}, {"next_check_at": {"$lte": now_ist()}}],
            }
        ).to_list(length=500)
        if not mandates:
            return 0

        from app.services.subscription_service import UserSubscriptionService

        subscriptions = UserSubscriptionService(self.db)
        client = _razorpay_client()
        renewed = 0
        for mandate in mandates:
            mandate_id = mandate["razorpay_subscription_id"]
            try:
                remote = client.subscription.fetch(mandate_id)
            except Exception:
                continue  # transient — the next pass retries
            paid_count = int(remote.get("paid_count") or 0)
            applied = int(mandate.get("cycles_applied") or 0)
            cycle_end = remote.get("current_end")
            cycle_end_dt = datetime.fromtimestamp(cycle_end, tz=timezone.utc) if cycle_end else None

            while paid_count > applied:
                # Claim the cycle FIRST (atomic, guarded on the count we
                # read) — if a second process is sweeping too, only one of us
                # gets to apply it, and a crash mid-apply can't double-credit.
                claimed = await self.orders.find_one_and_update(
                    {"_id": mandate["_id"], "cycles_applied": applied},
                    {"$set": {"cycles_applied": applied + 1, "last_cycle_at": now_ist()}},
                )
                if not claimed:
                    break
                applied += 1
                refreshed = await subscriptions.apply_renewal_cycle(mandate.get("subscription_id"), cycle_end_dt)
                if refreshed:
                    await self._announce_subscription(mandate["customer_id"], refreshed, renewed=True)
                if not refreshed:
                    # The plan is gone or the customer cancelled it and the
                    # gateway cancel didn't land — money in, nothing to give.
                    await self.cancel_autopay(mandate_id, at_cycle_end=False)
                    await self._flag_order_attention(
                        {"_id": mandate["_id"]}, "auto-pay renewal charged for a cancelled or missing subscription"
                    )
                    break
                # A ledger row per renewal, so the admin collections roll-up
                # counts recurring revenue the same way it counts a first
                # purchase. `mandate_id` (not razorpay_subscription_id) keeps
                # these rows out of the mandate's unique index.
                await self.orders.insert_one(
                    {
                        "kind": "autopay_cycle",
                        "mandate_id": mandate_id,
                        "customer_id": mandate["customer_id"],
                        "purpose": "subscription",
                        "plan_id": mandate.get("plan_id"),
                        "vehicle_type": mandate.get("vehicle_type"),
                        "subscription_id": mandate.get("subscription_id"),
                        "amount_paise": mandate.get("amount_paise", 0),
                        "currency": "INR",
                        "status": "paid",
                        "cycle": applied,
                        "created_at": now_ist(),
                        "paid_at": now_ist(),
                    }
                )
                renewed += 1

            if remote.get("status") in ("cancelled", "completed", "expired", "halted"):
                await self.orders.update_one(
                    {"_id": mandate["_id"]}, {"$set": {"auto_pay_active": False, "mandate_status": remote.get("status")}}
                )
                if mandate.get("subscription_id"):
                    await subscriptions.mark_auto_renew_off(mandate["subscription_id"])
                continue
            await self._schedule_next_autopay_check(mandate, subscriptions)
        return renewed

    async def _schedule_next_autopay_check(self, mandate: dict, subscriptions) -> None:
        """How long until this mandate is worth asking Razorpay about again:
        tight around the renewal date (where a charge is imminent), lazy the
        rest of the cycle."""
        from datetime import timedelta

        due_soon = True
        sub_id = mandate.get("subscription_id")
        if sub_id:
            local = await subscriptions.repo.find_by_id(sub_id)
            end = (local or {}).get("end_date")
            if end is not None:
                if end.tzinfo is None:
                    end = end.replace(tzinfo=timezone.utc)
                due_soon = end - now_ist() <= timedelta(days=2)
        await self.orders.update_one(
            {"_id": mandate["_id"]},
            {"$set": {"last_checked_at": now_ist(), "next_check_at": now_ist() + (timedelta(minutes=20) if due_soon else timedelta(hours=12))}},
        )

    async def _settle_booking_payment(self, order: dict, gateway_order_id: str | None, payment_id: str, *, via: str) -> bool:
        """The ONLY code that flips a booking to paid-online. Re-validates
        the booking at settlement time — a signature alone isn't enough:
          - a booking CANCELLED after the payment started must not become
            a paid cancelled booking (money for nothing, invisible);
          - a total that CHANGED since the order was minted means the
            charged amount is wrong — never silently accept it.
        Either case parks the order as needs-attention (surfaced in the
        admin collections view) instead of guessing."""
        booking = await self.booking_repo.find_by_id(order["booking_id"])
        problem = None
        if not booking:
            problem = "paid but booking no longer exists"
        elif booking.get("status") == "cancelled":
            problem = "paid for a booking that was cancelled meanwhile"
        elif int(round(float(booking.get("total_amount") or 0) * 100)) != order.get("amount_paise"):
            problem = f"paid ₹{order.get('amount_paise', 0) / 100:g} but the booking now totals ₹{booking.get('total_amount')}"
        if problem:
            await self._flag_order_attention({"_id": order["_id"]}, problem)
            return False
        update: dict = {
            "payment_status": PaymentStatus.PAID.value,
            "payment_method": PaymentMethod.ONLINE.value,
            "razorpay_payment_id": payment_id,
        }
        if gateway_order_id:
            update["razorpay_order_id"] = gateway_order_id
        if via == "link":
            update["razorpay_link_id"] = order["razorpay_link_id"]
        updated = await self.booking_repo.update_if(order["booking_id"], {"payment_status": PaymentStatus.PENDING.value}, update)
        if not updated:
            # Raced by another settlement path (e.g. captain's cash tap a
            # heartbeat earlier) — money in, booking already settled another
            # way: needs a human decision, not a silent overwrite.
            await self._flag_order_attention({"_id": order["_id"]}, "paid online but the booking was already settled another way")
            return False
        # A booking the customer chose to pay online for was parked as
        # awaiting_payment and told nobody about itself. THIS is the moment
        # it becomes a real booking: it enters the manager's queue and both
        # confirmations go out. A no-op for every already-confirmed booking
        # (cash paid online later, a plan top-up, the WhatsApp flow).
        from app.services.booking_service import BookingService

        await BookingService(self.db).confirm_awaiting_payment_booking(
            order["booking_id"], "Online payment received — booking confirmed"
        )
        return True

    async def _settle_booking_group(self, order: dict, gateway_order_id: str, payment_id: str) -> tuple[int, list[str]]:
        """One payment, every car on the visit. Each car is settled through
        the SAME guarded path a single booking uses, so a car that was
        cancelled or re-priced meanwhile is caught rather than silently
        marked paid. Returns (settled, booking numbers that couldn't be)."""
        bookings = await self.booking_repo.collection.find(
            {"booking_group_id": order["booking_group_id"], "is_deleted": {"$ne": True}}
        ).to_list(length=20)
        settled, failed = 0, []
        for booking in bookings:
            if booking.get("payment_status") == PaymentStatus.PAID.value:
                continue  # already settled another way — not a failure
            per_car = {
                **order,
                "booking_id": str(booking["_id"]),
                # The guard compares the charged amount against the booking's
                # own total, so each car is checked against ITS price, not
                # the visit's sum.
                "amount_paise": int(round(float(booking.get("total_amount") or 0) * 100)),
            }
            if await self._settle_booking_payment(per_car, gateway_order_id, payment_id, via="order"):
                settled += 1
            else:
                failed.append(booking.get("booking_number") or str(booking["_id"]))
        if failed:
            await self._flag_order_attention(
                {"_id": order["_id"]}, f"visit paid but these vehicles could not be marked paid: {', '.join(failed)}"
            )
        return settled, failed

    async def _flag_order_attention(self, filter_: dict, reason: str) -> None:
        await self.orders.update_one(filter_, {"$set": {"status": "paid_attention", "attention_reason": reason, "flagged_at": now_ist()}})

    # -- Captain doorstep settlement ------------------------------------
    #
    # When the wash is done and the booking is still unpaid, the captain's
    # phone offers exactly two clear choices (founder spec): "Cash
    # received" or "Show QR". The QR is a Razorpay payment link rendered
    # as a QR on the captain's screen — the customer scans, pays, and the
    # captain's app polls captain_check_payment (which asks Razorpay
    # directly) until the booking flips to paid. A customer who already
    # paid online needs nothing here — every path below answers "already
    # paid" instead of double-charging.

    async def _captain_booking(self, booking_id: str, captain_id: str) -> dict:
        booking = await self.booking_repo.find_by_id(booking_id)
        if not booking or booking.get("captain_id") != captain_id:
            raise NotFoundException("Booking not found")
        if booking.get("status") == "cancelled":
            raise BadRequestException("This booking was cancelled — there's nothing to collect.")
        return booking

    async def _visit_cars(self, booking: dict) -> list[dict]:
        """Every live car on this booking's visit (just the booking itself
        when it isn't on one). The customer pays for the visit once, so the
        captain collects for it once — see the three methods below."""
        group_id = booking.get("booking_group_id")
        if not group_id:
            return [booking]
        rows = await self.booking_repo.collection.find(
            {"booking_group_id": group_id, "is_deleted": {"$ne": True}, "status": {"$ne": "cancelled"}}
        ).sort("group_offset_minutes", 1).to_list(length=20)
        return rows or [booking]

    @staticmethod
    def _unpaid(cars: list[dict]) -> list[dict]:
        return [c for c in cars if c.get("payment_status") != PaymentStatus.PAID.value and float(c.get("total_amount") or 0) > 0]

    @staticmethod
    def _rupees_to_paise(cars: list[dict]) -> int:
        return int(round(sum(float(c.get("total_amount") or 0) for c in cars) * 100))

    async def captain_collect_cash(self, booking_id: str, captain_id: str) -> dict:
        booking = await self._captain_booking(booking_id, captain_id)
        cars = await self._visit_cars(booking)
        unpaid = self._unpaid(cars)
        if not unpaid:
            raise BadRequestException("Already paid — nothing to collect.")
        if any(c.get("status") != "completed" for c in cars):
            raise BadRequestException(
                "Finish every vehicle first — cash for the visit is collected once the last wash is done."
                if len(cars) > 1 else
                "Finish the service first — cash is collected after the wash is done."
            )
        collected = 0
        for car in unpaid:
            updated = await self.booking_repo.update_if(
                str(car["_id"]),
                {"payment_status": PaymentStatus.PENDING.value},
                {
                    "payment_status": PaymentStatus.PAID.value,
                    "payment_method": PaymentMethod.CASH.value,
                    "cash_collected_by": captain_id,
                    "cash_collected_at": now_ist(),
                },
            )
            if updated:
                collected += 1
        if not collected:
            raise BadRequestException("Already paid — nothing to collect.")
        return {
            "payment_status": "paid",
            "payment_method": "cash",
            "amount": round(sum(float(c.get("total_amount") or 0) for c in unpaid), 2),
            "vehicles": len(unpaid),
        }

    async def captain_payment_link(self, booking_id: str, captain_id: str) -> dict:
        """The QR's target — for the whole visit's outstanding amount, so the
        customer scans ONE code. Reuses a still-pending link for the same
        amount (no link spam from reopening the modal), else mints one."""
        booking = await self._captain_booking(booking_id, captain_id)
        cars = await self._visit_cars(booking)
        unpaid = self._unpaid(cars)
        if not unpaid:
            raise BadRequestException("Already paid — nothing to collect.")
        amount_paise = self._rupees_to_paise(unpaid)
        ids = [str(c["_id"]) for c in unpaid]
        existing = await self.orders.find_one(
            {
                "kind": "link", "status": "created", "amount_paise": amount_paise, "short_url": {"$exists": True},
                "$or": [{"booking_id": booking_id, "booking_ids": {"$exists": False}}, {"booking_ids": ids}],
            }
        )
        if existing:
            return {"short_url": existing["short_url"], "amount": amount_paise, "link_id": existing["razorpay_link_id"]}
        return await self.create_payment_link(
            booking, contact_phone=booking.get("customer_phone"), name=booking.get("customer_name"),
            cars=unpaid if len(cars) > 1 else None,
        )

    async def captain_check_payment(self, booking_id: str, captain_id: str) -> dict:
        """Polled by the QR modal every few seconds — actively syncs this
        booking's (or its visit's) pending links against Razorpay, no
        waiting out the 60s background sweep at the doorstep — and reports
        the live payment state however it was paid. For a visit, "paid"
        means every car on it is paid."""
        booking = await self._captain_booking(booking_id, captain_id)
        cars = await self._visit_cars(booking)
        if self._unpaid(cars) and settings.RAZORPAY_KEY_ID:
            pending = await self.orders.find(
                {"kind": "link", "status": "created", "$or": [{"booking_id": booking_id}, {"booking_ids": booking_id}]}
            ).to_list(length=10)
            if pending:
                client = _razorpay_client()
                for order in pending:
                    try:
                        link = client.payment_link.fetch(order["razorpay_link_id"])
                    except Exception:
                        continue
                    if link.get("status") == "paid":
                        payments = link.get("payments") or []
                        await self._apply_link_paid(order["razorpay_link_id"], (payments[0].get("payment_id") if payments else None) or "via_captain_check")
                booking = await self.booking_repo.find_by_id(booking_id)
                cars = await self._visit_cars(booking)
        outstanding = self._unpaid(cars)
        return {
            "payment_status": "pending" if outstanding else "paid",
            "payment_method": booking.get("payment_method"),
            "amount": round(sum(float(c.get("total_amount") or 0) for c in outstanding), 2),
            "vehicles": len(cars),
        }

    # -- Collections reporting (manager surveillance / admin roll-up) ----

    @staticmethod
    def _collections_window(date_from: str | None, date_to: str | None) -> dict:
        """scheduled_date bounds from YYYY-MM-DD strings (naive IST
        wall-clock, matching how scheduled_date is stored). Defaults to
        the last 30 days."""
        from datetime import datetime as _dt, timedelta as _td

        today = now_ist().replace(tzinfo=None, hour=0, minute=0, second=0, microsecond=0)
        start = _dt.strptime(date_from, "%Y-%m-%d") if date_from else today - _td(days=30)
        end = (_dt.strptime(date_to, "%Y-%m-%d") if date_to else today) + _td(days=1)
        return {"$gte": start, "$lt": end}

    _COLLECTION_GROUP = {
        "cash_amount": {"$sum": {"$cond": [{"$and": [{"$eq": ["$payment_status", "paid"]}, {"$eq": ["$payment_method", "cash"]}]}, "$total_amount", 0]}},
        "cash_count": {"$sum": {"$cond": [{"$and": [{"$eq": ["$payment_status", "paid"]}, {"$eq": ["$payment_method", "cash"]}]}, 1, 0]}},
        "online_amount": {"$sum": {"$cond": [{"$and": [{"$eq": ["$payment_status", "paid"]}, {"$in": ["$payment_method", ["online", "online_placeholder"]]}]}, "$total_amount", 0]}},
        "online_count": {"$sum": {"$cond": [{"$and": [{"$eq": ["$payment_status", "paid"]}, {"$in": ["$payment_method", ["online", "online_placeholder"]]}]}, 1, 0]}},
        # Completed but nobody collected — the surveillance number: a
        # captain sitting on these has taken cash without tapping, or
        # simply forgot to settle.
        "uncollected_amount": {"$sum": {"$cond": [{"$and": [{"$eq": ["$status", "completed"]}, {"$eq": ["$payment_status", "pending"]}]}, "$total_amount", 0]}},
        "uncollected_count": {"$sum": {"$cond": [{"$and": [{"$eq": ["$status", "completed"]}, {"$eq": ["$payment_status", "pending"]}]}, 1, 0]}},
    }

    @staticmethod
    def _round_row(row: dict) -> dict:
        for k in ("cash_amount", "online_amount", "uncollected_amount"):
            row[k] = round(float(row.get(k) or 0), 2)
        return row

    async def center_collections(self, service_center_id: str, actor_role: str, actor_center_id: str | None, date_from: str | None, date_to: str | None) -> dict:
        """Per-CAPTAIN cash/online/uncollected for one center — the
        manager's acknowledgment ledger for what each captain handled."""
        from app.core.authz import ensure_own_center

        ensure_own_center(actor_role, actor_center_id, service_center_id)
        match = {
            "service_center_id": service_center_id,
            "is_deleted": {"$ne": True},
            "status": {"$ne": "cancelled"},
            "scheduled_date": self._collections_window(date_from, date_to),
        }
        rows = await self.booking_repo.collection.aggregate(
            [{"$match": match}, {"$group": {"_id": "$captain_id", **self._COLLECTION_GROUP}}]
        ).to_list(length=200)

        from bson import ObjectId

        captain_ids = [ObjectId(r["_id"]) for r in rows if r["_id"] and ObjectId.is_valid(r["_id"])]
        captains = {
            str(u["_id"]): u
            for u in await self.db.users.find({"_id": {"$in": captain_ids}}, {"full_name": 1, "employee_id": 1}).to_list(length=200)
        }
        out_rows = []
        totals = {"cash_amount": 0.0, "cash_count": 0, "online_amount": 0.0, "online_count": 0, "uncollected_amount": 0.0, "uncollected_count": 0}
        for r in rows:
            if not any(r[k] for k in totals):
                continue  # nothing money-related in range for this captain
            captain = captains.get(r["_id"] or "")
            out_rows.append(self._round_row({
                "captain_id": r["_id"],
                "captain_name": captain.get("full_name") if captain else ("Not yet assigned" if not r["_id"] else "Unknown"),
                "employee_id": captain.get("employee_id") if captain else None,
                **{k: r[k] for k in totals},
            }))
            for k in totals:
                totals[k] += r[k]
        out_rows.sort(key=lambda x: -(x["cash_amount"] + x["online_amount"]))
        return {"rows": out_rows, "totals": self._round_row(totals)}

    async def admin_collections(self, date_from: str | None, date_to: str | None) -> dict:
        """The admin roll-up: per-CENTER cash/online/uncollected, platform
        totals, online subscription revenue, and the needs-attention queue
        (payments that landed but couldn't settle cleanly)."""
        match = {
            "is_deleted": {"$ne": True},
            "status": {"$ne": "cancelled"},
            "scheduled_date": self._collections_window(date_from, date_to),
        }
        rows = await self.booking_repo.collection.aggregate(
            [{"$match": match}, {"$group": {"_id": "$service_center_id", **self._COLLECTION_GROUP}}]
        ).to_list(length=500)

        from bson import ObjectId

        center_ids = [ObjectId(r["_id"]) for r in rows if r["_id"] and ObjectId.is_valid(r["_id"])]
        centers = {
            str(c["_id"]): c.get("name", "Center")
            for c in await self.db.service_centers.find({"_id": {"$in": center_ids}}, {"name": 1}).to_list(length=500)
        }
        out_rows = []
        totals = {"cash_amount": 0.0, "cash_count": 0, "online_amount": 0.0, "online_count": 0, "uncollected_amount": 0.0, "uncollected_count": 0}
        for r in rows:
            if not any(r[k] for k in totals):
                continue
            out_rows.append(self._round_row({
                "service_center_id": r["_id"],
                "center_name": centers.get(r["_id"] or "", "Unknown center"),
                **{k: r[k] for k in totals},
            }))
            for k in totals:
                totals[k] += r[k]
        out_rows.sort(key=lambda x: -(x["cash_amount"] + x["online_amount"]))

        # Online subscription revenue (payment_orders is the source of
        # truth — subscriptions never touch a booking row).
        window = self._collections_window(date_from, date_to)
        sub_rows = await self.orders.aggregate([
            {"$match": {"purpose": "subscription", "status": "paid", "created_at": window}},
            {"$group": {"_id": None, "amount_paise": {"$sum": "$amount_paise"}, "count": {"$sum": 1}}},
        ]).to_list(length=1)
        subscriptions = {
            "online_amount": round((sub_rows[0]["amount_paise"] / 100) if sub_rows else 0, 2),
            "count": sub_rows[0]["count"] if sub_rows else 0,
        }

        attention = [
            {
                "reason": o.get("attention_reason"),
                "booking_number": o.get("booking_number") or o.get("booking_id"),
                "purpose": o.get("purpose"),
                "amount": round((o.get("amount_paise") or 0) / 100, 2),
                "flagged_at": o["flagged_at"].isoformat() if o.get("flagged_at") else None,
            }
            for o in await self.orders.find({"status": "paid_attention"}).sort("flagged_at", -1).to_list(length=20)
        ]
        return {"rows": out_rows, "totals": self._round_row(totals), "subscriptions": subscriptions, "attention": attention}

    # -- Payment Links (WhatsApp bookings) ------------------------------
    #
    # A WhatsApp customer never has a browser session with us — the bot
    # sends a Razorpay Payment Link (short_url) right in the chat instead
    # of the embedded modal. Two independent paths mark it paid, either
    # is sufficient:
    #   1. the browser callback (link-callback route, signature-verified),
    #   2. the reminder-loop sweep (sync_pending_links), which asks
    #      Razorpay's API directly — this is the reliable one, and the
    #      only one in dev where the callback URL isn't public.

    async def create_payment_link(self, booking: dict, contact_phone: str | None, name: str | None, cars: list[dict] | None = None) -> dict:
        """`cars` turns this into ONE link for several cars of a visit — the
        amount is their sum and settlement marks each of them paid."""
        cars = cars or [booking]
        amount_paise = self._rupees_to_paise(cars)
        if amount_paise < MIN_ORDER_PAISE:
            raise BadRequestException("This amount is below the minimum for online payment (₹1).")
        if all(c.get("payment_status") == PaymentStatus.PAID.value for c in cars):
            raise BadRequestException("This booking is already paid.")
        booking_number = " + ".join(str(c.get("booking_number") or "") for c in cars)

        client = _razorpay_client()
        # reference_id must be unique across ALL links ever created — a
        # re-requested link for the same booking gets a fresh suffix.
        reference_id = f"{booking['booking_number']}-{secrets.token_hex(3)}"
        payload: dict = {
            "amount": amount_paise,
            "currency": "INR",
            "reference_id": reference_id,
            "description": f"Blussit booking {booking_number}",
            "notify": {"sms": False, "email": False},  # WE deliver it (in the WhatsApp chat)
        }
        if contact_phone:
            payload["customer"] = {"name": name or "Blussit customer", "contact": contact_phone}
        if settings.PUBLIC_BASE_URL:
            payload["callback_url"] = f"{settings.PUBLIC_BASE_URL.rstrip('/')}/api/v1/payments/link-callback"
            payload["callback_method"] = "get"
        try:
            link = client.payment_link.create(payload)
        except Exception as exc:
            # Razorpay validates the prefill contact aggressively (e.g.
            # "recurring digits disallowed") — the contact is only a
            # convenience prefill, so retry once without it rather than
            # failing the whole payment over it.
            if "customer" in payload:
                payload.pop("customer")
                try:
                    link = client.payment_link.create(payload)
                except Exception as exc2:
                    raise BadRequestException(f"Couldn't create the payment link — please try again. ({type(exc2).__name__})") from exc2
            else:
                raise BadRequestException(f"Couldn't create the payment link — please try again. ({type(exc).__name__})") from exc

        order_doc = {
            "kind": "link",
            "razorpay_link_id": link["id"],
            "short_url": link["short_url"],
            "reference_id": reference_id,
            "customer_id": booking["customer_id"],
            "purpose": "booking",
            "booking_id": str(booking["_id"]),
            "booking_number": booking_number,
            "amount_paise": amount_paise,
            "currency": "INR",
            "status": "created",
            "created_at": now_ist(),
        }
        if len(cars) > 1:
            order_doc["purpose"] = "booking_group"
            order_doc["booking_group_id"] = booking.get("booking_group_id")
            order_doc["booking_ids"] = [str(c["_id"]) for c in cars]
        await self.orders.insert_one(order_doc)
        return {"short_url": link["short_url"], "amount": amount_paise, "link_id": link["id"]}

    async def verify_link_callback(self, params: dict) -> dict:
        """The browser lands here after paying a link. Razorpay signs the
        callback as HMAC-SHA256(link_id|reference_id|status|payment_id) —
        only a matching signature with status 'paid' applies anything."""
        required = ("razorpay_payment_link_id", "razorpay_payment_link_reference_id", "razorpay_payment_link_status", "razorpay_payment_id", "razorpay_signature")
        if any(not params.get(k) for k in required):
            raise BadRequestException("Missing payment callback fields")
        message = "|".join([
            params["razorpay_payment_link_id"],
            params["razorpay_payment_link_reference_id"],
            params["razorpay_payment_link_status"],
            params["razorpay_payment_id"],
        ])
        expected = hmac.new(settings.RAZORPAY_KEY_SECRET.encode(), message.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, params["razorpay_signature"]):
            raise BadRequestException("Payment verification failed — the signature doesn't match.")
        if params["razorpay_payment_link_status"] != "paid":
            raise BadRequestException("This payment wasn't completed.")
        return await self._apply_link_paid(params["razorpay_payment_link_id"], params["razorpay_payment_id"])

    async def sync_pending_links(self, notify_customer) -> int:
        """Reminder-loop sweep: ask Razorpay (server-to-server, our key —
        no signature needed, the API response IS the truth) about every
        still-pending link from the last 2 days and settle the paid ones.
        `notify_customer(order_doc)` lets the caller push the WhatsApp
        'payment received' message without this service importing the bot."""
        if not settings.RAZORPAY_KEY_ID or not settings.RAZORPAY_KEY_SECRET:
            return 0
        from datetime import timedelta

        pending = await self.orders.find(
            {"kind": "link", "status": "created", "created_at": {"$gte": now_ist() - timedelta(days=2)}}
        ).to_list(length=50)
        if not pending:
            return 0
        client = _razorpay_client()
        settled = 0
        for order in pending:
            try:
                link = client.payment_link.fetch(order["razorpay_link_id"])
            except Exception:
                continue  # transient API failure — next pass retries
            if link.get("status") != "paid":
                continue
            payments = link.get("payments") or []
            payment_id = payments[0].get("payment_id") if payments else None
            result = await self._apply_link_paid(order["razorpay_link_id"], payment_id or "via_link_sync")
            # Message the customer only for a CLEAN settlement — a parked
            # needs-attention order gets a human first, not a "paid ✅".
            if result.get("settled"):
                settled += 1
                await notify_customer(order)
        return settled

    async def _apply_link_paid(self, link_id: str, payment_id: str) -> dict:
        """Shared by the callback, the sweep, and the captain's QR poll —
        the atomic created->paid claim guarantees settlement (and the
        customer's ✅ message) happens exactly once no matter which path
        lands first or how often it's retried, and the shared
        _settle_booking_payment guard re-validates the booking before the
        flip (cancelled meanwhile / total changed → parked for admin)."""
        order = await self.orders.find_one({"razorpay_link_id": link_id})
        if not order:
            raise NotFoundException("Payment link not found")
        claimed = await self.orders.find_one_and_update(
            {"razorpay_link_id": link_id, "status": "created"},
            {"$set": {"status": "paid", "paid_at": now_ist(), "razorpay_payment_id": payment_id}},
        )
        if not claimed:
            return {"status": "paid", "booking_number": order.get("booking_number"), "already_processed": True}
        if claimed.get("booking_ids"):
            # A visit's link: settle each car through the same guarded path,
            # each checked against ITS own price.
            failed = []
            for car_id in claimed["booking_ids"]:
                car = await self.booking_repo.find_by_id(car_id)
                per_car = {**claimed, "booking_id": car_id, "amount_paise": int(round(float((car or {}).get("total_amount") or 0) * 100))}
                if not await self._settle_booking_payment(per_car, None, payment_id, via="link"):
                    failed.append((car or {}).get("booking_number") or car_id)
            if failed:
                await self._flag_order_attention(
                    {"_id": claimed["_id"]}, f"visit paid but these vehicles could not be marked paid: {', '.join(failed)}"
                )
            settled = not failed
        else:
            settled = await self._settle_booking_payment(claimed, None, payment_id, via="link")
        return {
            "status": "paid" if settled else "needs_attention",
            "booking_number": order.get("booking_number"),
            "booking_id": order["booking_id"],
            "settled": settled,
        }
