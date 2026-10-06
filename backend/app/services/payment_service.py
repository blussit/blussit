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

A one-time order has three independent ways to become paid, and all three
go through the SAME claim (_apply_order_paid): the browser's verify, the
Razorpay webhook, and the reconciliation sweep (sync_pending_orders) that
asks Razorpay directly — so a customer whose tab closed after paying is
still settled, and whichever path lands first wins while the others are
no-ops.
"""
import asyncio
import functools
import hashlib
import hmac
import json
import logging
import time
from concurrent.futures import ThreadPoolExecutor

from bson import ObjectId
import secrets
from datetime import datetime, timedelta, timezone

from motor.motor_asyncio import AsyncIOMotorDatabase
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

from app.core.config import settings
from app.core.exceptions import AppException, BadRequestException, NotFoundException
from app.models.enums import NotificationType, PaymentMethod, PaymentStatus
from app.repositories.booking_repository import BookingRepository
from app.repositories.subscription_repository import SubscriptionPlanRepository
from app.schemas.payment_schema import CreateOrderRequest, VerifyPaymentRequest
from app.utils.serializers import serialize_doc
from app.utils.timezone import from_stored, now_ist

logger = logging.getLogger(__name__)

# Razorpay's own floor — anything below is refused before we even call them.
MIN_ORDER_PAISE = 100

# The most a MANAGER may knock off a plan they sell by hand ("₹ off" on the
# Sell-a-plan form). An admin may go up to the full price; coupons are
# admin-made and carry their own limits.
MANAGER_MAX_PLAN_DISCOUNT_PERCENT = 50


def manual_plan_discount(amount: float, base_price: float, actor_role: str) -> float:
    """The rupees-off a manager/admin typed for a hand-sold plan, checked
    server-side: never negative, never more than the plan's price, and —
    for a manager — at most MANAGER_MAX_PLAN_DISCOUNT_PERCENT of it.
    Refuses with a clear message rather than silently clamping, so the
    number the manager typed is never quietly changed under them."""
    amount = float(amount or 0)
    if amount <= 0:
        return 0.0
    base_price = float(base_price or 0)
    if amount > base_price:
        raise BadRequestException(f"The discount can't be more than the plan price (₹{base_price:g}).")
    if actor_role != "admin":
        cap = float(int(base_price * MANAGER_MAX_PLAN_DISCOUNT_PERCENT / 100))
        if amount > cap:
            raise BadRequestException(
                f"You can give at most {MANAGER_MAX_PLAN_DISCOUNT_PERCENT}% off a plan — up to ₹{cap:g} on this one. "
                "Ask an admin for a bigger discount."
            )
    return amount

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

# (connect, read) seconds for every SDK request — the SDK sets none itself.
_RZP_TIMEOUT = (5, 15)
_RZP_WORKERS = 8
# Own pool, not asyncio's default one, which also serves DNS lookups for httpx.
_RZP_POOL = ThreadPoolExecutor(max_workers=_RZP_WORKERS, thread_name_prefix="razorpay")
_RZP_CLIENT: tuple | None = None

# Per sweep pass: at most this many gateway lookups, none started after the budget.
_SWEEP_MAX_CALLS = 50
_SWEEP_BUDGET_SECONDS = 15
# Never-checked first, then whatever was checked longest ago.
_SWEEP_ORDER = [("last_checked_at", 1), ("created_at", 1)]
_SWEEP_PAUSE_SECONDS = 30
_sweeps_paused_until = 0.0

# A one-time order is still "open" (payable) in either state: a failed
# attempt keeps the checkout modal open for a retry on the same order.
_OPEN = ["created", "failed"]
# The browser's verify is the fast path; the sweep gives it this long first.
_ORDER_SWEEP_MIN_AGE = timedelta(minutes=2)
_ORDER_SWEEP_MAX_AGE = timedelta(hours=48)
_ORDER_RECHECK_FIRST = timedelta(minutes=2)
_ORDER_RECHECK_MAX = timedelta(hours=2)
# Payment links and pending auto-pay mandates live for days. While this
# fresh the customer is most likely paying right now, so the sweep checks
# every pass; after that they go on the checkout-order backoff — every
# pass for 2-7 days was ~2,900 Razorpay calls per unpaid link. The
# webhook (link paid / mandate activated) and the captain's QR poll still
# settle a late payment straight away.
_PENDING_FRESH = timedelta(minutes=15)
# The captain's QR modal polls every few seconds; Razorpay is asked about a
# given link at most this often.
_CAPTAIN_LINK_CHECK_EVERY = timedelta(seconds=10)
# The expiry sweep leaves a booking alone while a checkout opened this
# recently may still be on a bank/OTP page or in a UPI app...
_CHECKOUT_GRACE = timedelta(minutes=10)
# ...but never longer than this past the payment window.
_EXPIRY_HARD_STOP = timedelta(minutes=20)
# A claim whose settlement never finished (instance killed mid-request).
_STALE_SETTLING = timedelta(minutes=10)

ATTENTION_MESSAGE = (
    "We received this payment, but couldn't apply it automatically (the booking was already paid, cancelled "
    "or changed). Our team has been flagged and will fix or refund it — please don't pay again."
)
PLAN_ATTENTION_MESSAGE = (
    "Payment received, but the plan couldn't be activated automatically — our team has been flagged "
    "and will activate it or refund you."
)


def _razorpay_client():
    """Lazily built (then reused) so tests (which never call external APIs)
    can run without credentials, and so a misconfigured deployment fails
    with a clear message instead of an SDK auth traceback."""
    global _RZP_CLIENT
    if not settings.RAZORPAY_KEY_ID or not settings.RAZORPAY_KEY_SECRET:
        raise BadRequestException("Online payment isn't set up on this server yet. Please contact support.")
    auth = (settings.RAZORPAY_KEY_ID, settings.RAZORPAY_KEY_SECRET)
    if _RZP_CLIENT is None or _RZP_CLIENT[0] != auth:
        _RZP_CLIENT = (auth, _build_razorpay_client(auth))
    return _RZP_CLIENT[1]


def _build_razorpay_client(auth: tuple[str, str]):
    import razorpay
    import requests
    from requests.adapters import HTTPAdapter

    class _TimeoutAdapter(HTTPAdapter):
        def send(self, request, stream=False, timeout=None, **kwargs):
            return super().send(request, stream=stream, timeout=_RZP_TIMEOUT if timeout is None else timeout, **kwargs)

    session = requests.Session()
    session.mount("https://", _TimeoutAdapter(pool_maxsize=_RZP_WORKERS))
    return razorpay.Client(session=session, auth=auth)


async def _rzp(call, *args):
    """Every SDK call goes through here: it blocks, and on the loop it would stall the whole instance."""
    return await asyncio.get_running_loop().run_in_executor(_RZP_POOL, functools.partial(call, *args))


def _gateway_unreachable(exc: BaseException) -> bool:
    """Razorpay itself is down or slow, as opposed to one bad record."""
    import requests

    return isinstance(exc, (requests.exceptions.Timeout, requests.exceptions.ConnectionError))


def _iso(value) -> str | None:
    """A stored instant (read back naive-UTC) as an offset-carrying string."""
    return from_stored(value).isoformat() if isinstance(value, datetime) else None


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
            group_id = await self._unpaid_visit_of(customer_id, payload.booking_id)
            if group_id:
                # One car of a visit whose other cars are unpaid too: the
                # customer pays for the visit once (and its expiry cancels it
                # as one thing), so a single-car order would confirm one car
                # and leave the rest to be released.
                payload = payload.model_copy(update={"purpose": "booking_group", "booking_group_id": group_id})
        if payload.purpose == "booking":
            amount_paise, description, reference = await self._booking_order(customer_id, payload)
        elif payload.purpose == "booking_group":
            amount_paise, description, reference = await self._booking_group_order(customer_id, payload)
        elif payload.purpose == "society":
            from app.services.society_service import SocietyService

            code = "".join((payload.society_coupon_code or "").split()).upper() or None
            amount_paise, description, reference = await SocietyService(self.db).payment_quote(
                customer_id, payload.society_enrollment_id, payload.society_renewal, coupon_code=code if payload.society_renewal else None,
            )
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
            order = await _rzp(
                client.order.create,
                {
                    "amount": amount_paise,
                    "currency": "INR",
                    "receipt": reference["receipt"],
                    "notes": {"purpose": payload.purpose, **{k: v for k, v in reference.items() if k not in ("receipt", "booking_ids", "society_subscription_ids")}},
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
        if booking.get("payment_status") != PaymentStatus.PAID.value and await self._settle_open_payments([payload.booking_id]):
            booking = await self.booking_repo.find_by_id(payload.booking_id) or booking
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
        if await self._settle_open_payments([str(b["_id"]) for b in bookings], payload.booking_group_id):
            bookings = await self.booking_repo.collection.find(
                {"booking_group_id": payload.booking_group_id, "is_deleted": {"$ne": True}}
            ).to_list(length=20)

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
            # So every per-booking lookup (reconciliation, the recycle-bin
            # money guard) finds the visit's order too.
            "booking_ids": [str(b["_id"]) for b in payable],
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
        if payload.service_id and (payload.vehicle_id or payload.vehicle_type):
            # A PASS is priced from the vehicle type and the chosen service —
            # by the same function that will quote it in the purchase sheet,
            # so the customer is never charged a number they weren't shown.
            quote = await subscriptions.quote_pass(
                customer_id, payload.plan_id, payload.vehicle_id, payload.service_id, vehicle_type=payload.vehicle_type
            )
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
        purchase through the shared claim (_apply_order_paid) — the same one
        the webhook and the reconciliation sweep use, so whichever of them
        lands first settles it and the others just read the outcome."""
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

        result = await self._apply_order_paid(
            order, payload.razorpay_payment_id, via="verify", signature=payload.razorpay_signature
        )
        if result["status"] != "paid":
            raise BadRequestException(PLAN_ATTENTION_MESSAGE if result["purpose"] in ("subscription", "society") else ATTENTION_MESSAGE)
        if result["purpose"] == "subscription":
            result["first_confirmation"] = await self._first_client_confirmation(order["_id"])
        return result

    async def _apply_order_paid(self, order: dict, payment_id: str, *, via: str, signature: str | None = None) -> dict:
        """The one way a one-time order becomes paid. The atomic open->paid
        claim decides which caller settles; `settling` stays on the doc until
        the settlement finishes, so an instance killed in between is caught
        by the sweep (see _flag_stale_settlements) instead of leaving money
        with nothing to show for it. Returns the outcome — status "paid" or
        "needs_attention" — never raises for a business problem."""
        fields = {"status": "paid", "paid_at": now_ist(), "razorpay_payment_id": payment_id, "settled_via": via, "settling": True}
        if signature:
            fields["razorpay_signature"] = signature
        claimed = await self.orders.find_one_and_update(
            {"_id": order["_id"], "status": {"$in": _OPEN}}, {"$set": fields}, return_document=ReturnDocument.AFTER
        )
        if not claimed:
            return await self._order_outcome(order["_id"], payment_id, via)

        purpose = claimed["purpose"]
        result: dict = {"purpose": purpose}
        try:
            if purpose == "booking":
                ok = await self._settle_booking_payment(claimed, claimed["razorpay_order_id"], payment_id, via="order")
                result["booking_id"] = claimed["booking_id"]
            elif purpose == "booking_group":
                # Some cars on the visit may not settle. The money is in and
                # the rest of the visit IS paid, so this needs a human rather
                # than a rollback — the order is parked with exactly which
                # cars are short.
                settled, failed = await self._settle_booking_group(claimed, claimed["razorpay_order_id"], payment_id)
                ok = not failed
                result.update(booking_group_id=claimed["booking_group_id"], settled_count=settled)
            elif purpose == "society":
                from app.services.society_service import SocietyService

                outcome = await SocietyService(self.db).on_order_paid(claimed)
                ok = outcome["ok"]
                result["society_enrollment_id"] = claimed.get("society_enrollment_id")
                if not ok:
                    await self._flag_order_attention({"_id": claimed["_id"]}, "paid, but the society plan couldn't be activated/renewed — check it")
            else:
                sub = await self._activate_order_subscription(claimed)
                ok = sub is not None
                if sub:
                    result.update(subscription=sub, subscription_id=sub["id"])
        except Exception:  # noqa: BLE001 — money is in; a crash must park it, not lose it
            logger.exception("Settling Razorpay order %s failed", claimed["razorpay_order_id"])
            await self._flag_order_attention({"_id": claimed["_id"]}, f"paid, but applying the payment failed ({via}) — check it")
            ok = False
        await self.orders.update_one({"_id": claimed["_id"]}, {"$unset": {"settling": ""}})
        return {**result, "status": "paid" if ok else "needs_attention", "settled": ok}

    async def _order_outcome(self, order_id, payment_id: str, via: str) -> dict:
        """What an order that someone else already claimed ended up as. A
        DIFFERENT payment id on it means the same order was paid twice —
        the second one is parked for a refund, never applied."""
        fresh = await self._wait_settled(order_id)
        if not fresh:
            raise NotFoundException("Payment order not found")
        result: dict = {"purpose": fresh.get("purpose"), "already_processed": True}
        for key in ("booking_id", "booking_group_id", "subscription_id", "society_enrollment_id"):
            if fresh.get(key):
                result[key] = fresh[key]
        if fresh.get("razorpay_payment_id") and fresh["razorpay_payment_id"] != payment_id and fresh.get("status") in ("paid", "paid_attention"):
            await self._record_duplicate_payment(fresh, payment_id, via)
            return {**result, "status": "needs_attention", "settled": False, "duplicate": True}
        if fresh.get("status") != "paid":
            return {**result, "status": "needs_attention", "settled": False}
        if fresh.get("purpose") == "subscription" and fresh.get("subscription_id"):
            result["subscription"] = await self._subscription_view(fresh["subscription_id"])
        return {**result, "status": "paid", "settled": True}

    async def _wait_settled(self, order_id, attempts: int = 16) -> dict | None:
        """A concurrent path may be mid-settlement; its outcome (paid or
        parked) is what the caller should report, not the claim alone."""
        for _ in range(attempts):
            doc = await self.orders.find_one({"_id": order_id})
            if not doc or not doc.get("settling"):
                return doc
            await asyncio.sleep(0.25)
        return await self.orders.find_one({"_id": order_id})

    async def _activate_order_subscription(self, order: dict) -> dict | None:
        """A paid one-time order IS the plan purchase — create it now. None
        (order parked for a human) when the plan can't be activated."""
        from app.schemas.subscription_schema import SubscribeRequest
        from app.services.subscription_service import UserSubscriptionService

        try:
            sub = await UserSubscriptionService(self.db).subscribe(
                order["customer_id"],
                SubscribeRequest(
                    plan_id=order["plan_id"], vehicle_id=order.get("vehicle_id"),
                    service_id=order.get("service_id"), vehicle_type=order.get("vehicle_type"),
                ),
            )
        except Exception:  # noqa: BLE001 — deactivated/deleted/duplicate since the order was minted
            await self._flag_order_attention({"_id": order["_id"]}, "paid but subscription could not be activated")
            return None
        await self.orders.update_one({"_id": order["_id"]}, {"$set": {"subscription_id": sub["id"]}})
        await self._announce_subscription(order["customer_id"], sub, renewed=False)
        return serialize_doc(sub) if "_id" in sub else sub

    async def _subscription_view(self, subscription_id: str) -> dict | None:
        from app.services.subscription_service import UserSubscriptionService

        sub = await UserSubscriptionService(self.db).get_subscription(subscription_id)
        if not sub:
            return None
        view = serialize_doc(sub)
        plan = await self.plan_repo.find_by_id(str(sub.get("plan_id") or ""))
        view["plan_name"] = (plan or {}).get("name")
        return view

    async def _first_client_confirmation(self, order_id) -> bool:
        """True exactly once per order — the browser that first learns a
        plan purchase went through gets the thank-you ticket (an ad
        conversion), whichever path actually settled it."""
        result = await self.orders.update_one({"_id": order_id, "client_confirmed_at": None}, {"$set": {"client_confirmed_at": now_ist()}})
        return result.modified_count == 1

    async def _record_duplicate_payment(self, order: dict, payment_id: str, via: str) -> None:
        """A second captured payment for an order that's already paid. Its
        own ledger row (keyed on the payment id, so every path that notices
        it records it once) in the attention queue, for a refund."""
        doc = {
            "_id": f"dup_{payment_id}",
            "kind": "duplicate_payment",
            "purpose": order.get("purpose"),
            "customer_id": order.get("customer_id"),
            "parent_order_id": order.get("razorpay_order_id") or order.get("razorpay_link_id"),
            "razorpay_payment_id": payment_id,
            "amount_paise": order.get("amount_paise", 0),
            "currency": "INR",
            "status": "paid_attention",
            "attention_reason": "second payment for an order that was already paid — refund it",
            "detected_via": via,
            "created_at": now_ist(),
            "flagged_at": now_ist(),
        }
        for key in ("booking_id", "booking_ids", "booking_group_id", "booking_number", "receipt", "plan_id"):
            if order.get(key):
                doc[key] = order[key]
        try:
            await self.orders.insert_one(doc)
        except DuplicateKeyError:
            return
        await self._alert_attention(doc)

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
        created = await _rzp(
            client.plan.create,
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
        mandate = await _rzp(
            client.subscription.create,
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
            fresh = await self.orders.find_one({"razorpay_subscription_id": mandate_id}) or {}
            if fresh.get("status") == "paid_attention":
                raise BadRequestException(PLAN_ATTENTION_MESSAGE)
            sub_id = fresh.get("subscription_id")
            return {
                "status": "paid",
                "purpose": "subscription",
                "already_processed": True,
                "auto_pay": True,
                "subscription_id": sub_id,
                # The mandate sweep may have activated it before this browser
                # got here — it still gets its plan and thank-you ticket.
                **({"subscription": await self._subscription_view(sub_id)} if sub_id else {}),
                "first_confirmation": bool(sub_id) and await self._first_client_confirmation(fresh["_id"]),
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
            "first_confirmation": await self._first_client_confirmation(claimed["_id"]),
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
            vt_doc = (
                await self.db.vehicle_types.find_one({"_id": ObjectId(str(sub["vehicle_type"]))})
                if not vehicle and sub.get("vehicle_type") and ObjectId.is_valid(str(sub["vehicle_type"]))
                else None
            )
            vehicle_label = (
                f"{vehicle.get('brand', '')} {vehicle.get('model', '')} · {vehicle.get('registration_number', '')}".strip(" ·")
                if vehicle else ((vt_doc or {}).get("name") or "your vehicle")
            )
            end = sub.get("end_date")
            # `sub` is usually the ALREADY-SERIALIZED doc subscribe()/assign()
            # return (end_date as an ISO string, not a datetime) — every
            # caller of this method hands that shape in, so parse it back
            # rather than assume a raw Mongo doc and crash to_ist() on a str.
            if isinstance(end, str):
                end = datetime.fromisoformat(end)
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
            await _rzp(_razorpay_client().subscription.cancel, mandate_id, {"cancel_at_cycle_end": 1 if at_cycle_end else 0})
            return True
        except Exception:
            logger.warning("Could not cancel Razorpay mandate %s", mandate_id, exc_info=True)
            return False

    async def _gateway_sweep(self, docs: list[dict], fetch, id_field: str, what: str, schedule=None):
        """Yields (doc, remote record) per doc Razorpay answers for, stamping
        last_checked_at so the next pass starts with whatever waited longest.
        Stops at the time budget, or when Razorpay is unreachable — then the
        other sweeps pause too, so an outage costs one timeout per pass.
        `schedule(doc)` adds fields to that stamp (e.g. a backoff)."""
        global _sweeps_paused_until
        if time.monotonic() < _sweeps_paused_until:
            return
        deadline = time.monotonic() + _SWEEP_BUDGET_SECONDS
        for doc in docs:
            if time.monotonic() > deadline:
                logger.info("Razorpay %s sweep used its %ss budget — the rest go first next pass", what, _SWEEP_BUDGET_SECONDS)
                return
            gateway_id = doc[id_field]
            try:
                remote = await _rzp(fetch, gateway_id)
            except Exception as exc:  # noqa: BLE001
                if _gateway_unreachable(exc):
                    _sweeps_paused_until = time.monotonic() + _SWEEP_PAUSE_SECONDS
                    logger.warning("Razorpay unreachable (%s) — %s sweep stopped for this pass", type(exc).__name__, what)
                    return
                logger.warning("Razorpay %s check failed for %s: %s", what, gateway_id, exc)
                remote = None
            stamp: dict = {"$set": {"last_checked_at": now_ist()}}
            if schedule:
                for op, fields in schedule(doc).items():
                    stamp.setdefault(op, {}).update(fields)
            await self.orders.update_one({"_id": doc["_id"]}, stamp)
            if remote is not None:
                yield doc, remote

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
        ).sort(_SWEEP_ORDER).limit(_SWEEP_MAX_CALLS).to_list(length=_SWEEP_MAX_CALLS)
        if not mandates:
            return 0

        from app.services.subscription_service import UserSubscriptionService

        subscriptions = UserSubscriptionService(self.db)
        client = _razorpay_client()
        renewed = 0
        async for mandate, remote in self._gateway_sweep(
            mandates, client.subscription.fetch, "razorpay_subscription_id", "auto-pay renewal"
        ):
            mandate_id = mandate["razorpay_subscription_id"]
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

            remote_status = remote.get("status")
            if remote_status in ("cancelled", "completed", "expired", "halted"):
                # Claimed atomically: only the pass that flips it off tells
                # anyone, so a second sweeper (or a retry) never re-sends.
                stopped = await self.orders.find_one_and_update(
                    {"_id": mandate["_id"], "auto_pay_active": True},
                    {"$set": {"auto_pay_active": False, "mandate_status": remote_status}},
                )
                if mandate.get("subscription_id"):
                    await subscriptions.mark_auto_renew_off(mandate["subscription_id"])
                # The customer's own turn-off / cancel / upgrade already set
                # auto_pay_active False (cancel_autopay), so this mandate is
                # never even polled — reaching here means Razorpay stopped
                # it (retries exhausted, card/UPI mandate revoked at the bank).
                if stopped and not mandate.get("cancel_requested_at"):
                    await self._announce_autopay_stopped(mandate)
                continue
            if mandate.get("subscription_id"):
                # "pending" = a renewal charge failed and Razorpay is
                # retrying it. The pass is left alone meanwhile (the
                # ended-pass sweep gives it extra grace) and nobody is
                # messaged — a retry that works just renews it, one that
                # doesn't ends as "halted" above.
                await subscriptions.set_autopay_state(
                    mandate["subscription_id"], "pending" if remote_status == "pending" else None
                )
            await self._schedule_next_autopay_check(mandate, subscriptions)
        return renewed

    async def _announce_autopay_stopped(self, mandate: dict) -> None:
        """Auto-pay stopped without the customer asking: they hear it
        (in-app + WhatsApp, the normal utility path) so the pass doesn't
        quietly lapse, and the center's managers see it in-app (a manager's
        WhatsApp carries new bookings only). Best effort."""
        try:
            from app.services.booking_service import BookingService
            from app.services.notification_service import NotificationService

            sub_id = str(mandate.get("subscription_id") or "")
            sub = await self.db.user_subscriptions.find_one({"_id": ObjectId(sub_id)}) if ObjectId.is_valid(sub_id) else None
            plan_id = str((sub or {}).get("plan_id") or mandate.get("plan_id") or "")
            plan = await self.plan_repo.find_by_id(plan_id) if plan_id else None
            plan_name = (plan or {}).get("name") or "Monthly"
            customer_id = str(mandate.get("customer_id") or (sub or {}).get("customer_id") or "")
            notifications = NotificationService(self.db)
            if customer_id:
                await notifications.notify(
                    customer_id, "Auto-pay stopped",
                    f"Auto-pay for your {plan_name} pass stopped — renew from your dashboard.",
                    NotificationType.SYSTEM, sub_id or None,
                )
            # Which center: the one that sold it, else the customer's latest booking's.
            center_id = (sub or {}).get("service_center_id") or mandate.get("service_center_id")
            if not center_id and customer_id:
                last = await self.db.bookings.find_one(
                    {"customer_id": customer_id, "is_deleted": {"$ne": True}}, {"service_center_id": 1}, sort=[("created_at", -1)]
                )
                center_id = (last or {}).get("service_center_id")
            if not center_id:
                return
            center = await self.db.service_centers.find_one({"_id": ObjectId(center_id)}, {"manager_id": 1}) if ObjectId.is_valid(center_id) else None
            customer = await self.db.users.find_one({"_id": ObjectId(customer_id)}, {"full_name": 1, "phone": 1}) if ObjectId.is_valid(customer_id) else None
            who = (customer or {}).get("full_name") or (customer or {}).get("phone") or "A customer"
            for manager_id in await BookingService(self.db)._manager_recipients(center_id, (center or {}).get("manager_id")):
                await notifications.notify(
                    manager_id, "Auto-pay stopped",
                    f"Auto-pay stopped for {who}'s {plan_name} pass. They've been asked to renew.",
                    NotificationType.SYSTEM, sub_id or None,
                )
        except Exception:  # noqa: BLE001
            logger.exception("Could not announce the stopped mandate %s", mandate.get("razorpay_subscription_id"))

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
        updated = await self.booking_repo.update_if(
            order["booking_id"],
            {"payment_status": PaymentStatus.PENDING.value, "status": {"$ne": "cancelled"}},
            update,
        )
        if not updated:
            # Raced by another settlement path (e.g. captain's cash tap or a
            # second order paid a heartbeat earlier) or a cancellation —
            # money in, booking settled/closed another way: needs a human
            # decision, not a silent overwrite.
            current = await self.booking_repo.find_by_id(order["booking_id"])
            if current and current.get("status") == "cancelled":
                reason = "paid for a booking that was cancelled meanwhile"
            else:
                reason = "paid online but the booking was already settled another way"
            await self._flag_order_attention({"_id": order["_id"]}, reason)
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
        # The payment flip and the confirm are two writes; a cancellation
        # (expiry sweep, the customer) landing between them leaves a PAID
        # CANCELLED booking — money for nothing unless someone sees it.
        after = await self.booking_repo.find_by_id(order["booking_id"])
        if after and after.get("status") == "cancelled":
            await self._flag_order_attention({"_id": order["_id"]}, "paid, but the booking was cancelled at the same moment")
            return False
        return True

    async def _settle_booking_group(self, order: dict, gateway_order_id: str, payment_id: str) -> tuple[int, list[str]]:
        """One payment, every car on the visit. Each car is settled through
        the SAME guarded path a single booking uses, so a car that was
        cancelled or re-priced meanwhile is caught rather than silently
        marked paid. Returns (settled, booking numbers that couldn't be)."""
        charged_for = order.get("booking_ids")
        if charged_for:
            # Exactly the cars this order was priced for — a car cancelled
            # BEFORE checkout was never charged and isn't a problem, and the
            # visit's current total must still be what was charged.
            bookings = await self.booking_repo.collection.find(
                {"_id": {"$in": [ObjectId(i) for i in charged_for if ObjectId.is_valid(i)]}}
            ).to_list(length=20)
            now_owed = sum(int(round(float(b.get("total_amount") or 0) * 100)) for b in bookings)
            if len(bookings) != len(charged_for) or now_owed != order.get("amount_paise"):
                await self._flag_order_attention(
                    {"_id": order["_id"]},
                    f"visit paid ₹{order.get('amount_paise', 0) / 100:g} but its vehicles now total ₹{now_owed / 100:g}",
                )
                return 0, [b.get("booking_number") or str(b["_id"]) for b in bookings]
        else:
            bookings = await self.booking_repo.collection.find(
                {"booking_group_id": order["booking_group_id"], "is_deleted": {"$ne": True}}
            ).to_list(length=20)
        settled, failed = 0, []
        for booking in bookings:
            if booking.get("payment_status") == PaymentStatus.PAID.value:
                if charged_for and booking.get("razorpay_payment_id") != payment_id:
                    # Unpaid when the order was minted, paid some other way
                    # since — this payment covered it a second time.
                    failed.append(booking.get("booking_number") or str(booking["_id"]))
                continue  # already settled another way
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
        before = await self.orders.find_one_and_update(
            filter_,
            {"$set": {"status": "paid_attention", "attention_reason": reason, "flagged_at": now_ist()}, "$unset": {"settling": ""}},
        )
        # Re-flags (a visit's per-car reasons, then its summary) update the
        # reason but alert only once.
        if before and before.get("status") != "paid_attention":
            await self._alert_attention({**before, "attention_reason": reason})

    @staticmethod
    def _order_reference(order: dict) -> str:
        if order.get("booking_number"):
            return str(order["booking_number"])
        if order.get("purpose") == "booking" and order.get("receipt"):
            return str(order["receipt"])
        return {"booking_group": "your visit", "subscription": "your plan", "society": "your society plan"}.get(order.get("purpose") or "", "your booking")

    async def _alert_attention(self, order: dict) -> None:
        """Money landed that couldn't be applied: every admin sees it in-app
        (and in the collections attention queue), and the customer is told
        plainly that it's being handled, so they don't pay a second time.
        Best-effort — never undoes or blocks the flag itself."""
        try:
            from app.services.notification_service import NotificationService

            notifications = NotificationService(self.db)
            amount = f"₹{(order.get('amount_paise') or 0) / 100:g}"
            reference = self._order_reference(order)
            admins = await self.db.users.find(
                {"role": "admin", "is_deleted": {"$ne": True}, "is_active": {"$ne": False}}, {"_id": 1}
            ).to_list(length=20)
            for admin in admins:
                await notifications.notify(
                    str(admin["_id"]), "Payment needs attention",
                    f"{amount} · {reference}: {order.get('attention_reason')}. See Collections → needs attention.",
                    NotificationType.SYSTEM, None, send_whatsapp=False,
                )
            if order.get("customer_id"):
                await notifications.notify(
                    order["customer_id"], "Payment received — under review",
                    f"We got your {amount} payment for {reference} but couldn't apply it automatically. "
                    "Our team will fix or refund it — no need to pay again.",
                    NotificationType.SYSTEM, order.get("booking_id"),
                )
        except Exception:  # noqa: BLE001
            logger.exception("Could not send the payment-attention alert for order %s", order.get("_id"))

    # -- Reconciliation: money Razorpay has that we haven't applied -------
    #
    # The browser's verify is only the FAST path. A customer who pays and
    # then loses the tab/network never reaches it, so the truth is asked of
    # Razorpay directly (server-to-server, our own key): by the sweep a few
    # minutes after checkout, by the expiry sweep right before it would
    # release an unpaid slot, before a second order is minted for the same
    # booking, and by the browser's own "confirming…" poll. Every one of
    # them settles through _apply_order_paid / _apply_link_paid, the same
    # claims verify, the webhook and the link callback use.

    async def _unpaid_visit_of(self, customer_id: str, booking_id: str | None) -> str | None:
        """The visit to pay for instead of this one car, when other cars on
        it are still owed for too."""
        booking = await self.booking_repo.find_by_id(booking_id) if booking_id else None
        if not booking or booking.get("customer_id") != customer_id or not booking.get("booking_group_id"):
            return None
        owed = await self.booking_repo.collection.count_documents({
            "booking_group_id": booking["booking_group_id"],
            "is_deleted": {"$ne": True},
            "status": {"$ne": "cancelled"},
            "payment_status": {"$ne": PaymentStatus.PAID.value},
            "total_amount": {"$gt": 0},
        })
        return booking["booking_group_id"] if owed > 1 else None

    async def _open_payments(self, booking_ids: list[str], group_id: str | None = None) -> list[dict]:
        """Still-payable checkout orders and payment links for these cars."""
        match: list[dict] = [{"booking_id": {"$in": booking_ids}}, {"booking_ids": {"$in": booking_ids}}]
        if group_id:
            match.append({"booking_group_id": group_id})
        return await self.orders.find(
            {"$or": match, "status": {"$in": _OPEN}, "kind": {"$in": [None, "link"]}}
        ).sort("created_at", -1).to_list(length=10)

    async def _reconcile_one(self, doc: dict, client, via: str) -> str:
        """Ask Razorpay about one open order/link and settle it if the money
        is in. "paid" | "open" | "pending" (authorised but not captured —
        can't be called yet). Raises on a gateway error."""
        if doc.get("kind") == "link":
            link = await _rzp(client.payment_link.fetch, doc["razorpay_link_id"])
            if link.get("status") != "paid":
                return "open"
            payments = link.get("payments") or []
            await self._apply_link_paid(doc["razorpay_link_id"], (payments[0].get("payment_id") if payments else None) or f"via_{via}")
            return "paid"
        remote = await _rzp(client.order.payments, doc["razorpay_order_id"])
        return await self._apply_order_payments(doc, remote, client, via)

    async def _apply_order_payments(self, doc: dict, remote: dict, client, via: str) -> str:
        items = sorted((remote or {}).get("items") or [], key=lambda p: p.get("created_at") or 0)
        captured = [p for p in items if p.get("status") == "captured"]
        for payment in captured:
            # The first one settles the order; any other is a second
            # payment for the same thing and is parked for a refund.
            await self._apply_order_paid(doc, payment["id"], via=via)
        if captured:
            return "paid"
        authorized = [p for p in items if p.get("status") == "authorized"]
        if authorized:
            payment = authorized[0]
            if not await self._target_still_payable(doc):
                # Uncaptured money is refunded by Razorpay on its own —
                # capturing it for a closed booking would only make a refund
                # case out of it.
                await self.orders.update_one({"_id": doc["_id"]}, {"$set": {"uncaptured_payment_id": payment["id"]}})
                return "open"
            try:
                await _rzp(client.payment.capture, payment["id"], int(payment.get("amount") or doc["amount_paise"]), {"currency": "INR"})
            except Exception as exc:  # noqa: BLE001
                if "already been captured" not in str(exc).lower():
                    if _gateway_unreachable(exc):
                        raise
                    logger.warning("Could not capture authorised payment %s: %s", payment["id"], exc)
                    return "pending"
            await self._apply_order_paid(doc, payment["id"], via=via)
            return "paid"
        failed = [p for p in items if p.get("status") == "failed"]
        if failed and doc.get("status") in _OPEN:
            last = failed[-1]
            await self._record_failure(doc, {
                "payment_id": last.get("id"), "code": last.get("error_code"), "description": last.get("error_description"),
                "reason": last.get("error_reason"), "step": last.get("error_step"), "source": last.get("error_source"),
            }, via)
        return "open"

    async def _target_still_payable(self, doc: dict) -> bool:
        if doc.get("purpose") == "subscription":
            return True
        if doc.get("purpose") == "society":
            return await self._society_order_still_payable(doc)
        if doc.get("purpose") == "booking_group":
            query: dict = {"booking_group_id": doc.get("booking_group_id")}
        else:
            if not ObjectId.is_valid(str(doc.get("booking_id") or "")):
                return False
            query = {"_id": ObjectId(doc["booking_id"])}
        return await self.booking_repo.collection.count_documents({
            **query, "is_deleted": {"$ne": True}, "status": {"$ne": "cancelled"}, "payment_status": {"$ne": PaymentStatus.PAID.value},
        }) > 0

    async def _society_order_still_payable(self, doc: dict) -> bool:
        """A society order is worth CAPTURING only while what it pays for
        still exists unchanged: a first payment while the request is open at
        the revision that was priced, a renewal while the plan is active and
        one of its passes isn't cancelled. Otherwise the authorised money is
        left for Razorpay to refund on its own (never captured into a
        parked refund case for a withdrawn / cash-paid / resubmitted one)."""
        from app.services.society_service import OPEN_STATUSES

        eid = str(doc.get("society_enrollment_id") or "")
        if not ObjectId.is_valid(eid):
            return False
        enrollment = await self.db.society_enrollments.find_one({"_id": ObjectId(eid), "is_deleted": {"$ne": True}})
        if not enrollment:
            return False
        if doc.get("society_renewal"):
            if enrollment.get("status") != "active":
                return False
            sub_ids = [ObjectId(str(x)) for x in doc.get("society_subscription_ids") or [] if ObjectId.is_valid(str(x))]
            return bool(sub_ids) and await self.db.user_subscriptions.count_documents(
                {"_id": {"$in": sub_ids}, "status": {"$ne": "cancelled"}}
            ) > 0
        return enrollment.get("status") in OPEN_STATUSES and int(enrollment.get("revision") or 1) == int(doc.get("society_revision") or 1)

    async def _settle_open_payments(self, booking_ids: list[str], group_id: str | None = None) -> bool:
        """Before a NEW order is minted: did an earlier checkout or link for
        these cars already take the money? Settles it if so (True) — the
        caller then refuses the new order instead of charging twice. A
        Razorpay error just lets the new order through (the paid-twice
        safety net still parks a genuine double payment)."""
        if not settings.RAZORPAY_KEY_ID or not settings.RAZORPAY_KEY_SECRET or time.monotonic() < _sweeps_paused_until:
            return False
        docs = await self._open_payments(booking_ids, group_id)
        if not docs:
            return False
        client = _razorpay_client()
        paid = False
        for doc in docs[:3]:
            try:
                paid = (await self._reconcile_one(doc, client, via="create_order")) == "paid" or paid
            except Exception as exc:  # noqa: BLE001
                logger.warning("Pre-order payment check failed for %s: %s", doc.get("razorpay_order_id") or doc.get("razorpay_link_id"), exc)
                if _gateway_unreachable(exc):
                    break
        return paid

    @staticmethod
    def _order_backoff(doc: dict) -> dict:
        checks = int(doc.get("checks") or 0)
        delay = min(_ORDER_RECHECK_MAX, _ORDER_RECHECK_FIRST * (2 ** min(checks, 12)))
        return {"$set": {"next_check_at": now_ist() + delay}, "$inc": {"checks": 1}}

    @classmethod
    def _pending_backoff(cls, doc: dict) -> dict:
        """Links and mandates: every pass while fresh (_PENDING_FRESH), then
        _order_backoff (2, 4, 8… minutes, capped at every 2 h)."""
        created = doc.get("created_at")
        if created is not None and now_ist() - from_stored(created) < _PENDING_FRESH:
            return {}
        return cls._order_backoff(doc)

    async def sync_pending_orders(self) -> int:
        """Reminder-loop sweep for one-time checkout orders nobody verified:
        from 2 minutes old (verify's head start) up to 2 days, backing off
        per order (2, 4, 8… minutes, then every 2 h), a capped batch per
        pass, longest-unchecked first. Captured → settled exactly like a
        verify; authorised → captured first. Also parks any claim whose
        settlement was interrupted. Returns how many orders it settled."""
        if not settings.RAZORPAY_KEY_ID or not settings.RAZORPAY_KEY_SECRET:
            return 0
        await self._flag_stale_settlements()
        now = now_ist()
        due = await self.orders.find({
            "kind": None,
            "razorpay_order_id": {"$exists": True},
            "status": {"$in": _OPEN},
            "created_at": {"$lte": now - _ORDER_SWEEP_MIN_AGE, "$gte": now - _ORDER_SWEEP_MAX_AGE},
            "$or": [{"next_check_at": None}, {"next_check_at": {"$lte": now}}],
        }).sort(_SWEEP_ORDER).limit(_SWEEP_MAX_CALLS).to_list(length=_SWEEP_MAX_CALLS)
        if not due:
            return 0
        client = _razorpay_client()
        settled = 0
        async for doc, remote in self._gateway_sweep(
            due, client.order.payments, "razorpay_order_id", "checkout order", schedule=self._order_backoff
        ):
            try:
                if await self._apply_order_payments(doc, remote, client, via="sweep") == "paid":
                    settled += 1
            except Exception:  # noqa: BLE001 — one bad order must not end the pass
                logger.exception("Reconciling checkout order %s failed", doc.get("razorpay_order_id"))
        return settled

    async def _flag_stale_settlements(self) -> None:
        stale = await self.orders.find(
            {"settling": True, "paid_at": {"$lt": now_ist() - _STALE_SETTLING}}, {"_id": 1}
        ).to_list(length=20)
        for doc in stale:
            await self._flag_order_attention(
                {"_id": doc["_id"], "settling": True},
                "payment was taken but applying it was interrupted — check the booking/plan",
            )

    async def prepare_expiry(self, booking: dict, window_minutes: int) -> bool:
        """Called by the payment-window sweep right before it releases an
        unpaid booking (or visit). Asks Razorpay about every open order and
        link for it first and settles whatever was actually paid. True only
        when it's safe to cancel: still unpaid, nothing mid-checkout, and
        Razorpay reachable. A payment that lands after this is still settled
        by the usual paths — and, the booking being gone, parked for refund."""
        group_id = booking.get("booking_group_id")
        cars = (
            await self.booking_repo.collection.find({"booking_group_id": group_id, "is_deleted": {"$ne": True}}).to_list(length=20)
            if group_id else [booking]
        )
        ids = [str(c["_id"]) for c in cars]
        now = now_ist()
        created = booking.get("created_at")
        if created is not None and created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        past_hard_stop = created is not None and created < now - timedelta(minutes=window_minutes) - _EXPIRY_HARD_STOP

        docs = await self._open_payments(ids, group_id)
        if docs and settings.RAZORPAY_KEY_ID and settings.RAZORPAY_KEY_SECRET:
            if time.monotonic() < _sweeps_paused_until:
                return False
            client = _razorpay_client()
            for doc in docs:
                try:
                    outcome = await self._reconcile_one(doc, client, via="expiry")
                except Exception as exc:  # noqa: BLE001
                    if _gateway_unreachable(exc):
                        self._pause_sweeps()
                        return False
                    # A record Razorpay doesn't know (e.g. made under other
                    # keys) has no money behind it.
                    logger.warning("Expiry check failed for %s: %s", doc.get("razorpay_order_id") or doc.get("razorpay_link_id"), exc)
                    continue
                if outcome == "pending" and not past_hard_stop:
                    return False

        fresh = await self.booking_repo.collection.find({"_id": {"$in": [ObjectId(i) for i in ids]}}).to_list(length=20)
        live = [c for c in fresh if c.get("status") != "cancelled" and not c.get("is_deleted")]
        if any(c.get("status") != "awaiting_payment" or c.get("payment_status") == PaymentStatus.PAID.value for c in live):
            return False  # a payment just confirmed it (or it changed) — nothing to release

        if not past_hard_stop:
            recent = await self.orders.count_documents({
                "$or": [{"booking_id": {"$in": ids}}, {"booking_group_id": group_id or "__none__"}],
                "kind": None, "status": {"$in": _OPEN}, "created_at": {"$gt": now - _CHECKOUT_GRACE},
            })
            if recent:
                return False  # checkout still open on the customer's screen
        await self.void_open_links(ids, "Payment window expired")
        return True

    @staticmethod
    def _pause_sweeps() -> None:
        global _sweeps_paused_until
        _sweeps_paused_until = time.monotonic() + _SWEEP_PAUSE_SECONDS

    # -- Failed attempts ----------------------------------------------------

    async def _record_failure(self, doc: dict, error: dict, source: str) -> None:
        """A failed attempt on an order that stays open — the customer can
        retry within the window. Recorded (for them and for the admin),
        never changes what's paid."""
        clean = {k: (str(v)[:300] if v is not None else None) for k, v in error.items()}
        update: dict = {"$set": {"last_failure": {**clean, "source_path": source, "at": now_ist()}}}
        if clean.get("payment_id"):
            update["$addToSet"] = {"failed_payment_ids": clean["payment_id"]}
        await self.orders.update_one({"_id": doc["_id"], "status": {"$in": _OPEN}}, update)

    async def record_payment_failure(self, customer_id: str, payload) -> dict:
        """The checkout's own payment.failed event, reported by the browser —
        the reason is shown back to this customer (and the admin); nothing
        here can mark anything paid or unpaid."""
        key = {"razorpay_order_id": payload.razorpay_order_id} if payload.razorpay_order_id else {"razorpay_subscription_id": payload.razorpay_subscription_id}
        doc = await self.orders.find_one(key)
        if not doc or doc.get("customer_id") != customer_id:
            raise NotFoundException("Payment order not found")
        await self._record_failure(doc, {
            "payment_id": payload.razorpay_payment_id, "code": payload.code, "description": payload.description,
            "reason": payload.reason, "step": payload.step, "source": payload.source,
        }, "checkout")
        return {"recorded": True}

    # -- What the customer's screen shows ---------------------------------

    async def payment_status_for_customer(self, customer_id: str, order_id: str | None, subscription_id: str | None) -> dict:
        """Polled by the checkout while it confirms a payment whose verify
        didn't get through (and once when the modal closes). An open order
        is re-checked against Razorpay — at most every few seconds — so this
        settles it as soon as the money is there."""
        key = {"razorpay_order_id": order_id} if order_id else {"razorpay_subscription_id": subscription_id}
        doc = await self.orders.find_one(key) if (order_id or subscription_id) else None
        if not doc or doc.get("customer_id") != customer_id:
            raise NotFoundException("Payment order not found")
        if doc.get("status") in _OPEN and settings.RAZORPAY_KEY_ID and settings.RAZORPAY_KEY_SECRET and time.monotonic() >= _sweeps_paused_until:
            now = now_ist()
            slot = await self.orders.find_one_and_update(
                {"_id": doc["_id"], "status": {"$in": _OPEN}, "$or": [{"last_checked_at": None}, {"last_checked_at": {"$lt": now - timedelta(seconds=4)}}]},
                {"$set": {"last_checked_at": now}},
            )
            if slot:
                client = _razorpay_client()
                try:
                    if doc.get("kind") == "autopay":
                        await self._apply_mandate_state(doc, await _rzp(client.subscription.fetch, doc["razorpay_subscription_id"]))
                    else:
                        await self._reconcile_one(doc, client, via="status")
                except Exception as exc:  # noqa: BLE001 — report what we know; the sweep keeps trying
                    logger.warning("On-demand payment check failed for %s: %s", order_id or subscription_id, exc)
            doc = await self._wait_settled(doc["_id"]) or doc
        status = doc.get("status")
        result: dict = {
            "status": "paid" if status == "paid" else "needs_attention" if status == "paid_attention" else "failed" if status == "failed" else "pending",
            "purpose": doc.get("purpose"),
            "confirming": bool(doc.get("settling")),
            "failure_reason": (doc.get("last_failure") or {}).get("description"),
        }
        for field in ("booking_id", "booking_group_id", "subscription_id", "society_enrollment_id"):
            if doc.get(field):
                result[field] = doc[field]
        if status == "paid" and doc.get("purpose") == "subscription" and doc.get("subscription_id"):
            result["subscription"] = await self._subscription_view(doc["subscription_id"])
            result["first_confirmation"] = await self._first_client_confirmation(doc["_id"])
        if doc.get("kind") == "autopay":
            result["auto_pay"] = True
        return result

    async def booking_payment_state(self, customer_id: str, booking_id: str) -> dict:
        """The honest payment picture for the customer's booking page: the
        last failed attempt (while it can still be retried), and any money
        received that couldn't be applied (being fixed/refunded)."""
        booking = await self.booking_repo.find_by_id(booking_id)
        if not booking or booking.get("customer_id") != customer_id:
            raise NotFoundException("Booking not found")
        group_id = booking.get("booking_group_id")
        ids = [booking_id]
        if group_id:
            ids = [str(c["_id"]) for c in await self.booking_repo.collection.find({"booking_group_id": group_id}, {"_id": 1}).to_list(length=20)]
        match: list[dict] = [{"booking_id": {"$in": ids}}, {"booking_ids": {"$in": ids}}]
        if group_id:
            match.append({"booking_group_id": group_id})
        docs = await self.orders.find({"$or": match}).sort("created_at", -1).to_list(length=30)

        last_failure = None
        if booking.get("payment_status") != PaymentStatus.PAID.value and booking.get("status") != "cancelled":
            # Only the NEWEST attempt counts: an older failure followed by a
            # fresh checkout is history, not the current state.
            newest = next((d for d in docs if not d.get("kind") or d.get("kind") == "link"), None)
            if newest and newest.get("status") in _OPEN and newest.get("last_failure"):
                failure = newest["last_failure"]
                last_failure = {
                    "reason": failure.get("description") or "The payment didn't go through.",
                    "at": _iso(failure.get("at")),
                }
        issues = [d for d in docs if d.get("status") == "paid_attention" and not d.get("resolved_at")]
        attention = None
        if issues:
            attention = {
                "amount": round(sum((d.get("amount_paise") or 0) for d in issues) / 100, 2),
                "message": "We received your payment but couldn't apply it automatically. Our team will fix or refund it — no need to pay again.",
                "at": _iso(issues[0].get("flagged_at")),
            }
        return {
            "booking_id": booking_id,
            "payment_status": booking.get("payment_status"),
            "last_failure": last_failure,
            "attention": attention,
            "confirming": any(d.get("settling") for d in docs),
        }

    # -- Razorpay webhooks ---------------------------------------------------
    #
    # Razorpay pushes events to POST /payments/webhook, signed with the
    # webhook secret (HMAC-SHA256 of the RAW body). It's one more path into
    # the same idempotent claims — usually the first to land — never a
    # different truth: an event only ever settles an order/link we minted,
    # through _apply_order_paid / _apply_link_paid.

    async def handle_webhook(self, raw: bytes, signature: str | None, event_id: str | None) -> dict:
        secret = settings.RAZORPAY_WEBHOOK_SECRET
        if not secret:
            raise NotFoundException("Not found")
        expected = hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
        if not signature or not signature.isascii() or not hmac.compare_digest(expected, signature):
            raise BadRequestException("Invalid webhook signature")
        try:
            event = json.loads(raw)
        except ValueError as exc:
            raise BadRequestException("Malformed webhook body") from exc
        if not isinstance(event, dict):
            raise BadRequestException("Malformed webhook body")
        name = str(event.get("event") or "")
        event_id = event_id or hashlib.sha256(raw).hexdigest()
        events = self.db.razorpay_webhook_events
        try:
            await events.insert_one({"_id": event_id, "event": name, "status": "processing", "received_at": now_ist()})
        except DuplicateKeyError:
            return {"status": "duplicate"}
        try:
            outcome = await self._dispatch_webhook(name, event.get("payload") or {})
        except Exception:
            # Forget it so Razorpay's retry of this delivery is processed.
            await events.delete_one({"_id": event_id})
            raise
        await events.update_one({"_id": event_id}, {"$set": {"status": "processed", "outcome": outcome}})
        return {"status": "ok", "outcome": outcome}

    async def _dispatch_webhook(self, name: str, payload: dict) -> str:
        def entity(key: str) -> dict:
            return (payload.get(key) or {}).get("entity") or {}

        payment = entity("payment")
        if name in ("payment.captured", "order.paid"):
            order_id = payment.get("order_id") or entity("order").get("id")
            if not payment.get("id") or not order_id or payment.get("status") != "captured":
                return "ignored"
            doc = await self.orders.find_one({"razorpay_order_id": order_id})
            if not doc or doc.get("kind"):
                return "unknown_order"  # e.g. a payment link's own order — payment_link.paid covers it
            return (await self._apply_order_paid(doc, payment["id"], via="webhook"))["status"]
        if name == "payment.failed":
            doc = await self.orders.find_one({"razorpay_order_id": payment.get("order_id")}) if payment.get("order_id") else None
            if not doc or doc.get("kind"):
                return "unknown_order"
            await self._record_failure(doc, {
                "payment_id": payment.get("id"), "code": payment.get("error_code"), "description": payment.get("error_description"),
                "reason": payment.get("error_reason"), "step": payment.get("error_step"), "source": payment.get("error_source"),
            }, "webhook")
            return "failure_recorded"
        if name == "payment_link.paid":
            link_id = entity("payment_link").get("id")
            doc = await self.orders.find_one({"razorpay_link_id": link_id}) if link_id else None
            if not doc:
                return "unknown_link"
            result = await self._apply_link_paid(link_id, payment.get("id") or "via_webhook")
            if result.get("settled") and doc.get("purpose") != "subscription":
                try:
                    await self.notify_link_paid(doc)
                except Exception:  # noqa: BLE001 — the money is settled; the ✅ is a courtesy
                    logger.exception("Could not send the link-paid message for %s", link_id)
            return result["status"]
        if name in (
            "subscription.activated", "subscription.charged",
            "subscription.pending", "subscription.halted", "subscription.cancelled", "subscription.completed",
        ):
            mandate_id = entity("subscription").get("id")
            if not mandate_id:
                return "ignored"
            # The mandate sweeps own the cycle bookkeeping (and the "auto-pay
            # stopped" notice); this just moves the mandate to the front of
            # their next pass, where Razorpay's own answer decides.
            result = await self.orders.update_one(
                {"razorpay_subscription_id": mandate_id},
                {"$set": {"next_check_at": now_ist()}, "$unset": {"last_checked_at": ""}},
            )
            return "mandate_nudged" if result.matched_count else "unknown_mandate"
        return "ignored"

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
        if any(c.get("prepaid_only") for c in cars):
            raise BadRequestException("This booking is prepaid — no cash. Show the QR so the customer pays online.")
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
        booking's (or its visit's) pending links against Razorpay (each at
        most every _CAPTAIN_LINK_CHECK_EVERY), no waiting out the background
        sweep at the doorstep — and reports the live payment state however
        it was paid. For a visit, "paid" means every car on it is paid."""
        booking = await self._captain_booking(booking_id, captain_id)
        cars = await self._visit_cars(booking)
        if self._unpaid(cars) and settings.RAZORPAY_KEY_ID:
            pending = await self.orders.find(
                {"kind": "link", "status": "created", "$or": [{"booking_id": booking_id}, {"booking_ids": booking_id}]}
            ).to_list(length=10)
            if pending:
                client = _razorpay_client()
                now = now_ist()
                for order in pending:
                    # Claimed atomically, so a link one of the captain's
                    # polls (or the sweep, on any instance) asked about in
                    # the last few seconds just reports the local state.
                    fresh_check = await self.orders.find_one_and_update(
                        {
                            "_id": order["_id"],
                            "status": "created",
                            "$or": [{"last_checked_at": None}, {"last_checked_at": {"$lt": now - _CAPTAIN_LINK_CHECK_EVERY}}],
                        },
                        {"$set": {"last_checked_at": now}},
                    )
                    if not fresh_check:
                        continue
                    try:
                        link = await _rzp(client.payment_link.fetch, order["razorpay_link_id"])
                    except Exception as exc:  # noqa: BLE001
                        if _gateway_unreachable(exc):
                            break  # the rest would only time out too — report the local state
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
        # Part of online_amount that did NOT go through Razorpay: UPI the
        # manager recorded on a job he did himself — so the admin can
        # reconcile online_amount against Razorpay's settlements.
        "manual_online_amount": {"$sum": {"$cond": [{"$and": [{"$eq": ["$payment_status", "paid"]}, {"$eq": ["$payment_method", "online"]}, {"$eq": ["$completed_by_role", "manager"]}]}, "$total_amount", 0]}},
        # Completed but nobody collected — the surveillance number: a
        # captain sitting on these has taken cash without tapping, or
        # simply forgot to settle.
        "uncollected_amount": {"$sum": {"$cond": [{"$and": [{"$eq": ["$status", "completed"]}, {"$eq": ["$payment_status", "pending"]}]}, "$total_amount", 0]}},
        "uncollected_count": {"$sum": {"$cond": [{"$and": [{"$eq": ["$status", "completed"]}, {"$eq": ["$payment_status", "pending"]}]}, 1, 0]}},
        # Washes actually delivered — separate from the money fields above
        # since a plan-covered wash is usually ₹0 (paid up front when the
        # plan itself was bought), so without this a captain who only did
        # plan washes in the window had nothing in any money field and was
        # silently dropped from the report entirely (see the skip-guard
        # below, which now checks these too).
        "washes_count": {"$sum": {"$cond": [{"$eq": ["$status", "completed"]}, 1, 0]}},
        "plan_washes_count": {"$sum": {"$cond": [{"$and": [{"$eq": ["$status", "completed"]}, {"$ne": ["$subscription_id", None]}]}, 1, 0]}},
    }

    @staticmethod
    def _round_row(row: dict) -> dict:
        for k in ("cash_amount", "online_amount", "manual_online_amount", "uncollected_amount"):
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
            [{"$match": match}, {"$group": {"_id": {"$cond": [{"$eq": ["$completed_by_role", "manager"]}, "manager", "$captain_id"]}, **self._COLLECTION_GROUP}}]
        ).to_list(length=200)

        from bson import ObjectId

        captain_ids = [ObjectId(r["_id"]) for r in rows if r["_id"] and ObjectId.is_valid(r["_id"])]
        captains = {
            str(u["_id"]): u
            for u in await self.db.users.find({"_id": {"$in": captain_ids}}, {"full_name": 1, "employee_id": 1}).to_list(length=200)
        }
        out_rows = []
        totals = {"cash_amount": 0.0, "cash_count": 0, "online_amount": 0.0, "online_count": 0, "manual_online_amount": 0.0, "uncollected_amount": 0.0, "uncollected_count": 0, "washes_count": 0, "plan_washes_count": 0}
        for r in rows:
            if not any(r[k] for k in totals):
                continue  # nothing money OR washes in range for this captain
            captain = captains.get(r["_id"] or "")
            out_rows.append(self._round_row({
                "captain_id": r["_id"],
                "captain_name": captain.get("full_name") if captain else ("Done by manager" if r["_id"] == "manager" else "Not yet assigned" if not r["_id"] else "Unknown"),
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
        totals = {"cash_amount": 0.0, "cash_count": 0, "online_amount": 0.0, "online_count": 0, "manual_online_amount": 0.0, "uncollected_amount": 0.0, "uncollected_count": 0, "washes_count": 0, "plan_washes_count": 0}
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

        # Subscription revenue (payment_orders is the source of truth —
        # subscriptions never touch a booking row). Split cash (a manager
        # collecting money in hand, `kind="cash"`) from online (Razorpay —
        # a one-time order, a WhatsApp link, or an auto-pay charge), so cash
        # a manager sold a plan for isn't silently missing from the total.
        window = self._collections_window(date_from, date_to)
        sub_rows = await self.orders.aggregate([
            {"$match": {"purpose": "subscription", "status": "paid", "created_at": window}},
            {"$group": {
                "_id": None,
                "amount_paise": {"$sum": "$amount_paise"},
                "count": {"$sum": 1},
                "cash_amount_paise": {"$sum": {"$cond": [{"$eq": ["$kind", "cash"]}, "$amount_paise", 0]}},
                "cash_count": {"$sum": {"$cond": [{"$eq": ["$kind", "cash"]}, 1, 0]}},
            }},
        ]).to_list(length=1)
        row = sub_rows[0] if sub_rows else {}
        cash_amount = round((row.get("cash_amount_paise") or 0) / 100, 2)
        total_amount = round((row.get("amount_paise") or 0) / 100, 2)
        subscriptions = {
            "online_amount": round(total_amount - cash_amount, 2),
            "cash_amount": cash_amount,
            "count": row.get("count") or 0,
            "cash_count": row.get("cash_count") or 0,
        }

        open_orders = await self.orders.find({"status": "paid_attention", "resolved_at": None}).sort("flagged_at", -1).to_list(length=50)
        names = await self._attention_names(open_orders)
        attention = [
            {
                **names.get(str(o["_id"]), {}),
                "id": str(o["_id"]),
                "reason": o.get("attention_reason"),
                "booking_number": o.get("booking_number") or (o.get("receipt") if o.get("purpose") == "booking" else None) or o.get("booking_id"),
                "purpose": o.get("purpose"),
                "kind": o.get("kind") or "order",
                "amount": round((o.get("amount_paise") or 0) / 100, 2),
                "customer_id": o.get("customer_id"),
                # What to look up in the Razorpay dashboard to refund/verify.
                "payment_id": o.get("razorpay_payment_id"),
                "gateway_ref": o.get("razorpay_order_id") or o.get("razorpay_link_id") or o.get("razorpay_subscription_id") or o.get("parent_order_id"),
                "flagged_at": _iso(o.get("flagged_at")),
            }
            for o in open_orders
        ]
        return {"rows": out_rows, "totals": self._round_row(totals), "subscriptions": subscriptions, "attention": attention}

    async def _attention_names(self, orders: list[dict]) -> dict[str, dict]:
        """plan_name / vehicle_type_name / service_name for each parked
        payment, so the admin sees WHAT the money was for ("Monthly Shine ·
        Hatchback · Star Wash"). Three batched lookups over the (≤50) rows."""

        async def names(collection, ids) -> dict[str, str]:
            oids = [ObjectId(i) for i in {i for i in ids if isinstance(i, str) and i} if ObjectId.is_valid(i)]
            if not oids:
                return {}
            return {str(d["_id"]): d.get("name") or "" for d in await collection.find({"_id": {"$in": oids}}, {"name": 1}).to_list(length=len(oids))}

        plans, types, services = await asyncio.gather(
            names(self.db.subscription_plans, [o.get("plan_id") for o in orders]),
            names(self.db.vehicle_types, [o.get("vehicle_type") for o in orders]),
            names(self.db.services, [o.get("service_id") for o in orders]),
        )
        return {
            str(o["_id"]): {
                "plan_name": plans.get(o.get("plan_id") or ""),
                "vehicle_type_name": types.get(o.get("vehicle_type") or ""),
                "service_name": services.get(o.get("service_id") or ""),
            }
            for o in orders
        }

    async def resolve_attention(self, order_id: str, actor_id: str, note: str) -> dict:
        """An admin has refunded / activated / otherwise handled a parked
        payment. The status stays paid_attention (money WAS received — the
        recycle-bin guards key on it); it just leaves the open queue."""
        key = ObjectId(order_id) if ObjectId.is_valid(order_id) else order_id
        updated = await self.orders.find_one_and_update(
            {"_id": key, "status": "paid_attention", "resolved_at": None},
            {"$set": {"resolved_at": now_ist(), "resolved_by": actor_id, "resolution_note": note}},
        )
        if not updated:
            raise NotFoundException("No open payment issue with that id")
        return {"id": order_id, "resolved": True}

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
            link = await _rzp(client.payment_link.create, payload)
        except Exception as exc:
            # Razorpay validates the prefill contact aggressively (e.g.
            # "recurring digits disallowed") — the contact is only a
            # convenience prefill, so retry once without it rather than
            # failing the whole payment over it.
            if "customer" in payload:
                payload.pop("customer")
                try:
                    link = await _rzp(client.payment_link.create, payload)
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

    async def void_open_links(self, booking_ids: list[str], reason: str) -> int:
        """A booking was settled another way (the manager did the job and
        took the money) — cancel any still-unpaid payment link for it so the
        customer can't pay a second time. Best effort and never raises: a
        link is only marked voided once Razorpay confirms the cancel, so a
        failed cancel leaves it live for the normal paid-twice safety net."""
        if not booking_ids:
            return 0
        open_links = await self.orders.find(
            {"kind": "link", "status": "created", "$or": [{"booking_id": {"$in": booking_ids}}, {"booking_ids": {"$in": booking_ids}}]}
        ).to_list(length=20)
        voided = 0
        for order in open_links:
            voided += await self._void_link(order, reason)
        return voided

    async def _void_link(self, order: dict, reason: str) -> int:
        try:
            if settings.RAZORPAY_KEY_ID and settings.RAZORPAY_KEY_SECRET:
                await _rzp(_razorpay_client().payment_link.cancel, order["razorpay_link_id"])
            result = await self.orders.update_one(
                {"_id": order["_id"], "status": "created"},
                {"$set": {"status": "voided", "voided_at": now_ist(), "voided_reason": reason}},
            )
            return result.modified_count
        except Exception:  # noqa: BLE001
            # Razorpay refuses to cancel a link that was JUST paid — it stays
            # open and the next sweep settles (or parks) that payment.
            logger.warning("Could not void payment link %s", order.get("razorpay_link_id"), exc_info=True)
            return 0

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

    # -- Society plan payment links (manager) -------------------------
    async def create_society_link(
        self, enrollment_id: str, *, renewal: bool, actor_id: str, actor_role: str, actor_center_id: str | None, send_whatsapp: bool = True,
    ) -> dict:
        """The manager sends a resident a Razorpay link for their society
        plan (first payment, or a renewal). Paying it activates/renews the
        plan by itself — callback, webhook or the link sweep, all through
        _apply_link_paid -> SocietyService.on_order_paid. An unpaid link for
        the same thing and amount is reused (sent again), never duplicated;
        cash, a cancel or a resubmission voids it."""
        from app.services.society_service import SocietyService

        societies = SocietyService(self.db)
        enrollment = await societies.enrollment_for_actor(enrollment_id, actor_role, actor_center_id)
        # Before payment_quote, which moves a request to awaiting_payment —
        # a server without Razorpay keys must change nothing.
        client = _razorpay_client()
        customer_id = enrollment["customer_id"]
        if renewal and enrollment.get("status") != "active":
            raise BadRequestException("Only an active plan can be renewed.")
        amount_paise, description, reference = await societies.payment_quote(customer_id, enrollment_id, renewal)
        if amount_paise < MIN_ORDER_PAISE:
            raise BadRequestException("There's nothing to collect online for this plan.")
        same = {"kind": "link", "purpose": "society", "status": "created", "society_enrollment_id": enrollment_id,
                "society_renewal": renewal, "amount_paise": amount_paise}
        if not renewal:
            same["society_revision"] = reference.get("society_revision")
        existing = await self.orders.find_one(same, sort=[("created_at", -1)])
        if existing:
            order_id, short_url = existing["_id"], existing["short_url"]
        else:
            customer_doc = await self.db.users.find_one({"_id": ObjectId(customer_id)}) if ObjectId.is_valid(customer_id) else None
            reference_id = f"soc-{enrollment_id[-10:]}-{secrets.token_hex(3)}"
            link_payload: dict = {
                "amount": amount_paise, "currency": "INR", "reference_id": reference_id,
                "description": description[:255], "notify": {"sms": False, "email": False},  # WE deliver it, on WhatsApp
            }
            if (customer_doc or {}).get("phone"):
                link_payload["customer"] = {"name": (customer_doc or {}).get("full_name") or "Blussit customer", "contact": customer_doc["phone"]}
            if settings.PUBLIC_BASE_URL:
                link_payload["callback_url"] = f"{settings.PUBLIC_BASE_URL.rstrip('/')}/api/v1/payments/link-callback"
                link_payload["callback_method"] = "get"
            try:
                link = await _rzp(client.payment_link.create, link_payload)
            except Exception:
                # Razorpay rejects some contacts (e.g. repeated digits) — retry without.
                link_payload.pop("customer", None)
                try:
                    link = await _rzp(client.payment_link.create, link_payload)
                except Exception as exc:
                    raise BadRequestException(f"Couldn't create the payment link — please try again. ({type(exc).__name__})") from exc
            society = await self.db.societies.find_one({"_id": ObjectId(enrollment["society_id"])}) if ObjectId.is_valid(str(enrollment.get("society_id"))) else None
            doc = {
                "kind": "link", "purpose": "society",
                "razorpay_link_id": link["id"], "short_url": link["short_url"], "reference_id": reference_id,
                "customer_id": customer_id, "amount_paise": amount_paise, "currency": "INR", "status": "created",
                "channel": "manager", "issued_by": actor_id, "created_at": now_ist(),
                "service_center_id": (society or {}).get("service_center_id"),
                **{k: v for k, v in reference.items() if k != "receipt"},
            }
            order_id = (await self.orders.insert_one(doc)).inserted_id
            short_url = link["short_url"]
        if send_whatsapp:
            await self._send_society_link_whatsapp(enrollment, amount_paise / 100, short_url, renewal)
        return {"short_url": short_url, "amount": amount_paise / 100, "order_id": str(order_id), "reused": bool(existing), "sent": send_whatsapp}

    async def void_society_links(self, enrollment_id: str, reason: str, renewal: bool | None = None) -> int:
        """Cancels the unpaid links for a society plan — it was paid in cash,
        cancelled or changed. A link paid in the same instant can't be
        cancelled at Razorpay; the sweep settles or parks that payment."""
        query: dict = {"kind": "link", "purpose": "society", "status": "created", "society_enrollment_id": enrollment_id}
        if renewal is not None:
            query["society_renewal"] = renewal
        voided = 0
        for order in await self.orders.find(query).to_list(length=20):
            voided += await self._void_link(order, reason)
        return voided

    async def _send_society_link_whatsapp(self, enrollment: dict, amount: float, short_url: str, renewal: bool) -> None:
        """Same approved message as a plan link ("Pay ₹X to activate Y: link")."""
        try:
            from app.services.notification_service import NotificationService

            what = f"{enrollment.get('plan_name') or 'your society plan'}"
            if renewal:
                what = f"{what} for next month"
            amount_text = f"{amount:g}"
            await NotificationService(self.db).notify(
                enrollment["customer_id"], f"Pay to {'renew' if renewal else 'activate'} {enrollment.get('plan_name') or 'your society plan'}",
                f"Pay ₹{amount_text} to {'renew' if renewal else 'activate'} {what}: {short_url}",
                NotificationType.SYSTEM, None, wa_event="subscription_payment_link", wa_params=[amount_text, what, short_url],
            )
        except Exception:  # noqa: BLE001 — the link exists either way
            logger.exception("Could not send the society payment link for enrollment %s", enrollment.get("_id"))

    async def _notify_society_link_paid(self, order: dict) -> None:
        try:
            from app.services.notification_service import NotificationService

            renewal = bool(order.get("society_renewal"))
            await NotificationService(self.db).notify(
                str(order.get("customer_id") or ""), "Society plan renewed" if renewal else "Society plan active",
                f"✅ Payment of ₹{(order.get('amount_paise') or 0) / 100:g} received — your society plan is {'renewed' if renewal else 'active'}. Thank you!",
                NotificationType.SUBSCRIPTION, None,
            )
        except Exception:  # noqa: BLE001
            logger.exception("Could not confirm society payment for order %s", order.get("_id"))

    async def notify_link_paid(self, order: dict) -> None:
        """The WhatsApp ✅ for a settled booking link (sweep or webhook)."""
        from app.services.whatsapp_service import WhatsAppService

        customer_id = str(order.get("customer_id") or "")
        customer = await self.db.users.find_one({"_id": ObjectId(customer_id)}) if ObjectId.is_valid(customer_id) else None
        if customer and customer.get("phone"):
            await WhatsAppService(self.db).send_text(
                customer["phone"],
                f"✅ Payment received — booking *{order.get('booking_number')}* is fully paid. Thank you!",
            )

    async def _link_is_moot(self, order: dict) -> bool:
        """Every booking this link pays for is cancelled/deleted or already
        paid some other way — paying it now could only be a refund case."""
        if order.get("purpose") == "subscription":
            return False
        if order.get("purpose") == "society":
            return not await self._society_order_still_payable(order)
        ids = [i for i in (order.get("booking_ids") or [order.get("booking_id")]) if i and ObjectId.is_valid(i)]
        if not ids:
            return False
        still_payable = await self.booking_repo.collection.count_documents({
            "_id": {"$in": [ObjectId(i) for i in ids]},
            "is_deleted": {"$ne": True},
            "status": {"$ne": "cancelled"},
            "payment_status": {"$ne": PaymentStatus.PAID.value},
        })
        return still_payable == 0

    async def sync_pending_links(self, notify_customer=None) -> int:
        """Reminder-loop sweep: ask Razorpay (server-to-server, our key —
        no signature needed, the API response IS the truth) about the
        still-pending links from the last 2 days — a capped batch per pass,
        longest-unchecked first — and settle the paid ones.
        `notify_customer(order_doc)` sends the 'payment received' message
        (default: notify_link_paid). An unpaid link whose bookings were
        cancelled or paid another way is cancelled at Razorpay, so it can't
        take money for nothing later."""
        if not settings.RAZORPAY_KEY_ID or not settings.RAZORPAY_KEY_SECRET:
            return 0
        notify_customer = notify_customer or self.notify_link_paid

        now = now_ist()
        pending = await self.orders.find({
            "kind": "link",
            "status": "created",
            "created_at": {"$gte": now - timedelta(days=2)},
            "$or": [{"next_check_at": None}, {"next_check_at": {"$lte": now}}],
        }).sort(_SWEEP_ORDER).limit(_SWEEP_MAX_CALLS).to_list(length=_SWEEP_MAX_CALLS)
        if not pending:
            return 0
        client = _razorpay_client()
        settled = 0
        async for order, link in self._gateway_sweep(
            pending, client.payment_link.fetch, "razorpay_link_id", "payment link", schedule=self._pending_backoff
        ):
            if link.get("status") in ("cancelled", "expired"):
                await self.orders.update_one(
                    {"_id": order["_id"], "status": "created"},
                    {"$set": {"status": "voided", "voided_at": now_ist(), "voided_reason": f"link {link.get('status')} at Razorpay"}},
                )
                continue
            if link.get("status") != "paid":
                if await self._link_is_moot(order):
                    await self._void_link(order, "booking cancelled or paid another way")
                continue
            payments = link.get("payments") or []
            payment_id = payments[0].get("payment_id") if payments else None
            result = await self._apply_link_paid(order["razorpay_link_id"], payment_id or "via_link_sync")
            # Message the customer only for a CLEAN settlement — a parked
            # needs-attention order gets a human first, not a "paid ✅".
            # Subscription links announce themselves (_announce_subscription,
            # inside _activate_linked_subscription) — this generic "booking
            # is paid" text is for bookings only, or a subscription purchase
            # would get two different confirmation messages.
            if result.get("settled"):
                settled += 1
                if order.get("purpose") not in ("subscription", "society"):
                    await notify_customer(order)
        return settled

    async def _apply_link_paid(self, link_id: str, payment_id: str) -> dict:
        """Shared by the callback, the sweep, the webhook and the captain's
        QR poll — the atomic created->paid claim guarantees settlement (and
        the customer's ✅ message) happens exactly once no matter which path
        lands first or how often it's retried, and the shared
        _settle_booking_payment guard re-validates the booking before the
        flip (cancelled meanwhile / total changed → parked for admin)."""
        order = await self.orders.find_one({"razorpay_link_id": link_id})
        if not order:
            raise NotFoundException("Payment link not found")
        claimed = await self.orders.find_one_and_update(
            # A link voided a moment too late (the cancel lost to the
            # payment) still has real money behind it — settle it too.
            {"razorpay_link_id": link_id, "status": {"$in": ["created", "voided"]}},
            {"$set": {"status": "paid", "paid_at": now_ist(), "razorpay_payment_id": payment_id, "settling": True}},
        )
        if not claimed:
            fresh = await self._wait_settled(order["_id"]) or order
            return {
                "status": "needs_attention" if fresh.get("status") == "paid_attention" else "paid",
                "booking_number": order.get("booking_number"),
                "already_processed": True,
            }
        try:
            if claimed.get("purpose") == "subscription":
                result = await self._activate_linked_subscription(claimed)
                await self.orders.update_one({"_id": claimed["_id"]}, {"$unset": {"settling": ""}})
                return result
            if claimed.get("purpose") == "society":
                from app.services.society_service import SocietyService

                outcome = await SocietyService(self.db).on_order_paid(claimed)
                settled = bool(outcome.get("ok"))
                if settled:
                    await self._notify_society_link_paid(claimed)
                else:
                    await self._flag_order_attention({"_id": claimed["_id"]}, "link paid, but the society plan couldn't be activated/renewed — check it")
                await self.orders.update_one({"_id": claimed["_id"]}, {"$unset": {"settling": ""}})
                return {"status": "paid" if settled else "needs_attention", "booking_number": None, "settled": settled}
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
        except Exception:  # noqa: BLE001 — money is in; a crash must park it, not lose it
            logger.exception("Settling payment link %s failed", link_id)
            await self._flag_order_attention({"_id": claimed["_id"]}, "link paid, but applying the payment failed — check it")
            settled = False
        await self.orders.update_one({"_id": claimed["_id"]}, {"$unset": {"settling": ""}})
        return {
            "status": "paid" if settled else "needs_attention",
            "booking_number": order.get("booking_number"),
            "booking_id": order["booking_id"],
            "settled": settled,
        }

    async def _activate_linked_subscription(self, order: dict) -> dict:
        """A manager-issued subscription LINK just settled (browser
        callback or the sweep — see _apply_link_paid). The plan is created
        now, at the price the link was minted for, never before. Coupon
        USAGE is recorded here too, but only best-effort: the discount was
        already fixed and paid for at link-creation, so a bookkeeping
        hiccup on the counter must never cost the customer a plan they
        already paid for."""
        from app.schemas.subscription_schema import SubscribeRequest
        from app.services.subscription_service import UserSubscriptionService

        subs = UserSubscriptionService(self.db)
        try:
            sub = await subs.subscribe(
                order["customer_id"],
                SubscribeRequest(
                    plan_id=order["plan_id"], vehicle_id=order.get("vehicle_id"),
                    service_id=order.get("service_id"), vehicle_type=order.get("vehicle_type"),
                ),
                service_center_id=order.get("service_center_id"),
            )
        except Exception:
            # Money is in but the plan can't be activated (deactivated
            # between link creation and payment, or a race with another
            # purchase) — never swallow the money silently.
            await self._flag_order_attention({"_id": order["_id"]}, "paid but subscription could not be activated")
            return {"status": "needs_attention", "settled": False, "purpose": "subscription"}

        money_fields: dict = {"payment_method": "online", "amount_paid": order["amount_paise"] / 100}
        if order.get("discount_paise"):
            money_fields["discount_amount"] = round(order["discount_paise"] / 100, 2)
        if order.get("coupon_code"):
            money_fields["coupon_code"] = order["coupon_code"]
        if order.get("issued_by"):
            money_fields["assigned_by"] = order["issued_by"]
        await subs.repo.update_by_id(sub["id"], money_fields)

        if order.get("coupon_code"):
            try:
                from app.services.coupon_service import CouponService

                coupon_service = CouponService(self.db)
                coupon = await coupon_service.repo.find_by_code(order["coupon_code"])
                if coupon:
                    await coupon_service.record_usage(str(coupon["_id"]), order["customer_id"], sub["id"])
            except Exception:  # noqa: BLE001
                logger.exception("Could not record coupon usage for subscription %s", sub["id"])

        await self.orders.update_one({"_id": order["_id"]}, {"$set": {"subscription_id": sub["id"]}})
        await self._announce_subscription(order["customer_id"], sub, renewed=False)
        return {"status": "paid", "settled": True, "purpose": "subscription", "subscription_id": sub["id"]}

    # -- Manager-issued subscription offers (WhatsApp link / auto-pay / cash) --
    #
    # A manager selling a plan by phone or at the door: find-or-create the
    # customer, price the plan exactly like the customer's own purchase
    # sheet would, then end it one of three ways —
    #   - a Razorpay payment LINK (optionally discounted / coupon'd), the
    #     plan activating the moment it's paid, via the SAME two paths a
    #     WhatsApp booking link already settles through (browser callback,
    #     or the sweep above);
    #   - a Razorpay auto-pay MANDATE at the full undiscounted rate, its
    #     hosted authorisation page shared on WhatsApp — there is no
    #     browser to hand back a signature for a link opened outside our
    #     own checkout, so this settles ONLY via sync_pending_manager_mandates
    #     below, which asks Razorpay directly whether the first charge
    #     actually landed (the same server-to-server trust the rest of this
    #     file already runs on — never a client-supplied claim);
    #   - straight CASH, activating immediately because the money is
    #     already in the manager's hand.
    # In every case the amount is resolved SERVER-SIDE from the plan/quote —
    # the manager only ever picks the discount, never types the final price.

    async def manager_subscription_preview(self, payload, *, actor_role: str = "manager") -> dict:
        """Read-only price for the manager's offer form — no customer
        required yet, no side effects, nothing created. Mirrors exactly
        what manager_subscription_offer will charge, so the number the
        manager sees is the number that gets sent."""
        from app.services.subscription_service import UserSubscriptionService

        subs = UserSubscriptionService(self.db)
        from app.services.subscription_service import service_price_for_type

        plan, service, base_price = await subs.resolve_service_price(payload.plan_id, payload.vehicle_type, payload.service_id)
        result = {
            "plan_name": plan.get("name"),
            "service_name": service.get("name"),
            "visits": int(plan.get("total_service_count") or 1),
            "price_per_wash": service_price_for_type(service, payload.vehicle_type),
            "base_price": base_price,
            "discount": 0.0,
            "final_price": base_price,
            "coupon_valid": None,
            "coupon_error": None,
            "customer_exists": False,
            "already_has_pass": False,
            # The most "₹ off" this actor may type (see manual_plan_discount).
            "max_discount": float(base_price) if actor_role == "admin" else float(int(base_price * MANAGER_MAX_PLAN_DISCOUNT_PERCENT / 100)),
        }
        customer_id = None
        if payload.customer_phone:
            from app.utils.phone import validate_indian_mobile

            normalized = validate_indian_mobile(payload.customer_phone)
            existing = await self.db.users.find_one({"phone": normalized}) if normalized else None
            if existing:
                customer_id = str(existing["_id"])
                result["customer_exists"] = True
                result["already_has_pass"] = await subs.has_active_pass(customer_id, payload.vehicle_type, payload.service_id)
        if payload.recurring:
            return result  # full rate only — discount/coupon fields are ignored on purpose
        if payload.coupon_code:
            from app.services.coupon_service import CouponService

            try:
                _coupon, discount = await CouponService(self.db).validate_and_compute_discount(
                    payload.coupon_code, base_price, customer_id or ""
                )
                result["discount"] = discount
                result["coupon_valid"] = True
            except Exception as exc:  # noqa: BLE001 — surfaced to the manager as text, not a 500
                result["coupon_valid"] = False
                result["coupon_error"] = exc.message if isinstance(exc, AppException) else "Invalid coupon code"
        elif payload.discount_amount:
            result["discount"] = manual_plan_discount(payload.discount_amount, base_price, actor_role)
        result["final_price"] = round(base_price - result["discount"], 2)
        return result

    async def manager_subscription_offer(
        self, actor_id: str, payload, *, actor_center_id: str | None = None, actor_role: str = "manager",
    ) -> dict:
        """Creates the customer (if needed) and either a payment link, an
        auto-pay mandate, or an immediate cash-paid subscription. Every
        branch re-validates from scratch — the preview above is advisory
        only, never trusted."""
        from app.services.auth_service import AuthService
        from app.schemas.subscription_schema import SubscribeRequest
        from app.services.subscription_service import UserSubscriptionService

        subs = UserSubscriptionService(self.db)
        plan, _service, base_price = await subs.resolve_service_price(payload.plan_id, payload.vehicle_type, payload.service_id)
        if not payload.recurring and not payload.coupon_code:
            # Refuse an over-the-limit discount before a customer profile is
            # created for it (the real figure is re-derived below).
            manual_plan_discount(payload.discount_amount, base_price, actor_role)

        customer = await AuthService(self.db).ensure_customer_by_phone(payload.customer_phone, payload.customer_name)
        customer_id = str(customer["_id"])

        # The exact same eligibility gate a self-serve purchase runs through
        # (plan active, valid tier, no duplicate pass on this vehicle type +
        # service) — checked NOW, before any link/mandate/money moves, and
        # re-checked again at the moment of activation (see below / the
        # sweep), exactly like every other payment path in this file.
        subscribe_payload = SubscribeRequest(
            plan_id=payload.plan_id, service_id=payload.service_id, vehicle_type=payload.vehicle_type,
        )
        await subs.validate_purchase(customer_id, subscribe_payload)

        if payload.recurring:
            return await self._create_manager_autopay_mandate(actor_id, customer_id, plan, payload, base_price, actor_center_id)

        discount = 0.0
        coupon_code = None
        if payload.coupon_code:
            from app.services.coupon_service import CouponService

            _coupon, discount = await CouponService(self.db).validate_and_compute_discount(
                payload.coupon_code, base_price, customer_id
            )
            # validate_and_compute_discount only checks past USAGE — a coupon
            # usage row is written at SETTLEMENT, not here, precisely so an
            # abandoned link never burns it (see _activate_linked_subscription).
            # That gap means TWO different pending links for this same
            # customer (different vehicle type/service, so the duplicate-pass
            # guard above doesn't catch it) could both carry this coupon and
            # both later settle, redeeming a usage_limit_per_user=1 coupon
            # twice. Closing it here, at creation: this customer may have at
            # most ONE live (pending or already-paid) offer on this code.
            clash = await self.orders.find_one({
                "customer_id": customer_id, "coupon_code": payload.coupon_code,
                "purpose": "subscription", "status": {"$in": ["created", "paid"]},
            })
            if clash:
                raise BadRequestException("This customer already has a pending or paid offer using this coupon.")
            coupon_code = payload.coupon_code
        elif payload.discount_amount:
            discount = manual_plan_discount(payload.discount_amount, base_price, actor_role)
        final_price = round(base_price - discount, 2)

        if payload.payment_method == "cash":
            return await self._grant_cash_subscription(
                actor_id, customer_id, plan, payload, base_price, discount, coupon_code, final_price, actor_center_id,
            )
        return await self._create_manager_subscription_link(
            actor_id, customer_id, plan, payload, base_price, discount, coupon_code, final_price, actor_center_id,
        )

    async def _create_manager_subscription_link(
        self, actor_id: str, customer_id: str, plan: dict, payload, base_price: float, discount: float, coupon_code: str | None, final_price: float,
        actor_center_id: str | None = None,
    ) -> dict:
        amount_paise = int(round(final_price * 100))
        if amount_paise < MIN_ORDER_PAISE:
            raise BadRequestException(
                f"The amount to charge (₹{final_price:g}) is below the ₹1 minimum for an online link. "
                "Reduce the discount, or collect it as cash instead."
            )
        customer_doc = await self.db.users.find_one({"_id": ObjectId(customer_id)})
        client = _razorpay_client()
        reference_id = f"sub-{(plan.get('slug') or 'plan')[:24]}-{secrets.token_hex(4)}"
        link_payload: dict = {
            "amount": amount_paise,
            "currency": "INR",
            "reference_id": reference_id,
            "description": f"{plan['name']} subscription"[:255],
            "notify": {"sms": False, "email": False},  # WE deliver it, on WhatsApp
        }
        contact = (customer_doc or {}).get("phone")
        if contact:
            link_payload["customer"] = {"name": (customer_doc or {}).get("full_name") or "Blussit customer", "contact": contact}
        if settings.PUBLIC_BASE_URL:
            link_payload["callback_url"] = f"{settings.PUBLIC_BASE_URL.rstrip('/')}/api/v1/payments/link-callback"
            link_payload["callback_method"] = "get"
        try:
            link = await _rzp(client.payment_link.create, link_payload)
        except Exception as exc:
            if "customer" in link_payload:
                link_payload.pop("customer")
                try:
                    link = await _rzp(client.payment_link.create, link_payload)
                except Exception as exc2:
                    raise BadRequestException(f"Couldn't create the payment link — please try again. ({type(exc2).__name__})") from exc2
            else:
                raise BadRequestException(f"Couldn't create the payment link — please try again. ({type(exc).__name__})") from exc

        order_doc = {
            "kind": "link", "purpose": "subscription",
            "razorpay_link_id": link["id"], "short_url": link["short_url"], "reference_id": reference_id,
            "customer_id": customer_id, "plan_id": str(plan["_id"]),
            "service_id": payload.service_id, "vehicle_type": payload.vehicle_type,
            "base_amount_paise": int(round(base_price * 100)), "discount_paise": int(round(discount * 100)),
            "coupon_code": coupon_code, "amount_paise": amount_paise, "currency": "INR", "status": "created",
            "channel": "manager", "issued_by": actor_id, "created_at": now_ist(),
            # Carried through to settlement (browser callback or the sweep,
            # both go through _activate_linked_subscription) — that's the
            # only place left that still knows which manager/center sold
            # this plan, since settlement can happen minutes or days later
            # with no request/current_user in scope at all.
            "service_center_id": actor_center_id,
        }
        result = await self.orders.insert_one(order_doc)
        if payload.send_whatsapp:
            await self._send_subscription_link_whatsapp(customer_id, plan, final_price, link["short_url"], autopay=False)
        return {
            "kind": "link", "recurring": False, "short_url": link["short_url"],
            "order_id": str(result.inserted_id), "amount": final_price,
        }

    async def _grant_cash_subscription(
        self, actor_id: str, customer_id: str, plan: dict, payload, base_price: float, discount: float, coupon_code: str | None, final_price: float,
        actor_center_id: str | None = None,
    ) -> dict:
        from app.schemas.subscription_schema import AssignSubscriptionRequest
        from app.services.subscription_service import UserSubscriptionService

        subs = UserSubscriptionService(self.db)
        # assign() re-runs the same duplicate-pass guard validate_purchase
        # already checked — the last gate before the plan actually exists,
        # exactly like every other creation path in this file.
        sub = await subs.assign(AssignSubscriptionRequest(
            customer_id=customer_id, plan_id=str(plan["_id"]), vehicle_type=payload.vehicle_type, service_id=payload.service_id,
        ), actor_center_id=actor_center_id)
        money_fields: dict = {
            "payment_method": "cash", "amount_paid": final_price, "assigned_by": actor_id,
            "cash_collected_by": actor_id, "cash_collected_at": now_ist(),
        }
        if discount:
            money_fields["discount_amount"] = round(discount, 2)
        if coupon_code:
            money_fields["coupon_code"] = coupon_code
        await subs.repo.update_by_id(sub["id"], money_fields)

        if coupon_code:
            try:
                from app.services.coupon_service import CouponService

                coupon_service = CouponService(self.db)
                coupon = await coupon_service.repo.find_by_code(coupon_code)
                if coupon:
                    await coupon_service.record_usage(str(coupon["_id"]), customer_id, sub["id"])
            except Exception:  # noqa: BLE001
                logger.exception("Could not record coupon usage for subscription %s", sub["id"])

        # A real ledger row — cash collected by a manager is revenue just
        # like a Razorpay payment, and admin_collections reads this
        # collection as the source of truth for subscription revenue.
        await self.orders.insert_one({
            "kind": "cash", "purpose": "subscription", "customer_id": customer_id, "plan_id": str(plan["_id"]),
            "service_id": payload.service_id, "vehicle_type": payload.vehicle_type, "subscription_id": sub["id"],
            "base_amount_paise": int(round(base_price * 100)), "discount_paise": int(round(discount * 100)),
            "coupon_code": coupon_code, "amount_paise": int(round(final_price * 100)), "currency": "INR",
            "status": "paid", "paid_at": now_ist(), "channel": "manager_cash", "issued_by": actor_id,
            "created_at": now_ist(),
        })
        if payload.send_whatsapp:
            await self._announce_subscription(customer_id, sub, renewed=False)
        return {"kind": "cash", "recurring": False, "subscription": sub, "amount": final_price}

    async def _create_manager_autopay_mandate(
        self, actor_id: str, customer_id: str, plan: dict, payload, base_price: float, actor_center_id: str | None = None,
    ) -> dict:
        amount_paise = int(round(base_price * 100))
        if amount_paise < MIN_ORDER_PAISE:
            raise BadRequestException("This plan's price is below the ₹1 minimum for online payment.")
        rzp_plan_id = await self._ensure_razorpay_plan(plan, payload.vehicle_type, amount_paise)
        schedule = _AUTOPAY_SCHEDULE.get(plan.get("billing_cycle") or "monthly", _AUTOPAY_SCHEDULE["monthly"])
        client = _razorpay_client()
        try:
            mandate = await _rzp(client.subscription.create, {
                "plan_id": rzp_plan_id,
                "total_count": schedule["total_count"],
                "quantity": 1,
                "customer_notify": 0,  # WE deliver the link, on WhatsApp — not Razorpay's own email/SMS
                "notes": {
                    "purpose": "subscription", "plan_id": str(plan["_id"]), "vehicle_type": payload.vehicle_type,
                    "customer_id": customer_id, "channel": "manager",
                },
            })
        except Exception as exc:
            raise BadRequestException(f"Couldn't set up auto-pay — please try again. ({type(exc).__name__})") from exc
        short_url = mandate.get("short_url")
        if not short_url:
            # No hosted page to hand the customer — void it at the gateway
            # rather than leave an unreachable mandate lying around.
            try:
                await _rzp(client.subscription.cancel, mandate["id"], {"cancel_at_cycle_end": 0})
            except Exception:  # noqa: BLE001
                pass
            raise BadRequestException("Couldn't create an auto-pay link — please try again, or send a one-time link instead.")
        await self.orders.insert_one({
            "kind": "autopay", "purpose": "subscription", "razorpay_subscription_id": mandate["id"],
            "customer_id": customer_id, "plan_id": str(plan["_id"]), "service_id": payload.service_id,
            "vehicle_type": payload.vehicle_type, "amount_paise": amount_paise, "currency": "INR",
            "status": "created", "cycles_applied": 0, "auto_pay_active": True,
            "channel": "manager", "issued_by": actor_id, "created_at": now_ist(),
            "service_center_id": actor_center_id,
        })
        if payload.send_whatsapp:
            await self._send_subscription_link_whatsapp(customer_id, plan, base_price, short_url, autopay=True)
        return {"kind": "autopay", "recurring": True, "short_url": short_url, "order_id": mandate["id"], "amount": base_price}

    async def _send_subscription_link_whatsapp(self, customer_id: str, plan: dict, amount: float, short_url: str, *, autopay: bool) -> None:
        """The ONLY message this purchase sends before it's paid — a link,
        once. Best-effort: the link/mandate already exists in Razorpay
        either way, a messaging hiccup must never undo that."""
        try:
            from app.services.notification_service import NotificationService

            plan_name = plan.get("name") or "Monthly pass"
            amount_text = f"{amount:g}"
            if autopay:
                title = f"Set up auto-pay for {plan_name}"
                message = f"Auto-pay ₹{amount_text}/mo to activate {plan_name}: {short_url}"
                event, params = "subscription_autopay_link", [amount_text, plan_name, short_url]
            else:
                title = f"Pay to activate {plan_name}"
                message = f"Pay ₹{amount_text} to activate {plan_name}: {short_url}"
                event, params = "subscription_payment_link", [amount_text, plan_name, short_url]
            await NotificationService(self.db).notify(
                customer_id, title, message, NotificationType.SYSTEM, None, wa_event=event, wa_params=params,
            )
        except Exception:  # noqa: BLE001
            logger.exception("Could not send the subscription payment link to customer %s", customer_id)

    async def void_manager_subscription_offer(
        self, order_id: str, actor_id: str, actor_role: str = "admin", actor_center_id: str | None = None,
    ) -> dict:
        """Cancels a still-pending manager-issued link or mandate — the
        customer changed their mind, or the manager made a mistake. Only
        ever touches an order that hasn't been paid yet (guarded on
        status="created"); a paid one has already become a real
        subscription and must be cancelled through the subscription itself,
        not voided here.

        Checks the LIVE gateway status first, not just a best-effort cancel
        call: if the customer's payment landed a moment before this click,
        blindly marking the order "voided" would strand real, collected
        money with no subscription ever created and no entry in the
        admin-collections attention queue (voided orders are never swept
        again). Money in is settled properly instead of cancelled."""
        order = await self.orders.find_one({"_id": ObjectId(order_id)}) if ObjectId.is_valid(order_id) else None
        if not order or order.get("purpose") != "subscription" or order.get("channel") not in ("manager", "manager_cash"):
            raise NotFoundException("Offer not found")
        # A manager voids only THEIR center's offers — the order carries the
        # center it was issued under (resolve_grant_center_id). One with no
        # center is an admin's to clear; 404 rather than 403 so another
        # center's order ids can't be probed.
        if actor_role != "admin" and (not actor_center_id or order.get("service_center_id") != actor_center_id):
            raise NotFoundException("Offer not found")
        if order.get("status") != "created":
            raise BadRequestException("This offer isn't pending any more.")
        if settings.RAZORPAY_KEY_ID and settings.RAZORPAY_KEY_SECRET:
            client = _razorpay_client()
            try:
                if order.get("kind") == "link":
                    remote = await _rzp(client.payment_link.fetch, order["razorpay_link_id"])
                    if remote.get("status") == "paid":
                        payments = remote.get("payments") or []
                        payment_id = (payments[0].get("payment_id") if payments else None) or "via_void_race"
                        await self._apply_link_paid(order["razorpay_link_id"], payment_id)
                        raise BadRequestException("The customer just paid this — it has been activated instead of cancelled.")
                elif order.get("kind") == "autopay":
                    remote = await _rzp(client.subscription.fetch, order["razorpay_subscription_id"])
                    if int(remote.get("paid_count") or 0) >= 1:
                        raise BadRequestException(
                            "The customer just authorised auto-pay — it will activate on its own shortly instead of being cancelled."
                        )
            except BadRequestException:
                raise
            except Exception:  # noqa: BLE001
                logger.warning("Could not check live gateway status for manager offer %s — voiding anyway", order_id, exc_info=True)
            try:
                if order.get("kind") == "link":
                    await _rzp(client.payment_link.cancel, order["razorpay_link_id"])
                elif order.get("kind") == "autopay":
                    await _rzp(client.subscription.cancel, order["razorpay_subscription_id"], {"cancel_at_cycle_end": 0})
            except Exception:  # noqa: BLE001
                logger.warning("Could not cancel manager subscription offer %s at the gateway", order_id, exc_info=True)
        updated = await self.orders.find_one_and_update(
            {"_id": order["_id"], "status": "created"},
            {"$set": {"status": "voided", "voided_at": now_ist(), "voided_by": actor_id}},
        )
        return {"voided": updated is not None}

    async def sync_pending_manager_mandates(self) -> int:
        """A WhatsApp-issued auto-pay mandate has no browser to hand back a
        signature — activation is decided ENTIRELY by asking Razorpay
        directly whether the first charge actually landed (server-to-server,
        our own secret key: exactly the same trust model sync_pending_links
        already runs on for payment links, never a client-supplied claim).
        There is no other path that can mark one of these paid, so a
        customer cannot fabricate activation by visiting any URL — the only
        source of truth is Razorpay's own record of the mandate.

        Bonus fix: this also rescues any self-serve checkout mandate that
        got authorised but never made it back to /payments/verify (closed
        tab, flaky network) — those were previously stuck at "created"
        forever even though Razorpay had already charged the card."""
        if not settings.RAZORPAY_KEY_ID or not settings.RAZORPAY_KEY_SECRET:
            return 0
        from datetime import timedelta

        now = now_ist()
        pending = await self.orders.find({
            "kind": "autopay",
            "status": "created",
            "created_at": {"$gte": now - timedelta(days=7)},
            # The subscription.activated webhook resets next_check_at to now.
            "$or": [{"next_check_at": None}, {"next_check_at": {"$lte": now}}],
        }).sort(_SWEEP_ORDER).limit(_SWEEP_MAX_CALLS).to_list(length=_SWEEP_MAX_CALLS)
        if not pending:
            return 0

        client = _razorpay_client()
        activated = 0
        async for order, remote in self._gateway_sweep(
            pending, client.subscription.fetch, "razorpay_subscription_id", "auto-pay mandate", schedule=self._pending_backoff
        ):
            if await self._apply_mandate_state(order, remote):
                activated += 1
        return activated

    async def _apply_mandate_state(self, order: dict, remote: dict) -> bool:
        """One still-pending mandate against what Razorpay says about it:
        activates the plan once the first charge has landed. True when this
        call activated it."""
        from app.schemas.subscription_schema import SubscribeRequest
        from app.services.subscription_service import UserSubscriptionService

        mandate_id = order["razorpay_subscription_id"]
        if int(remote.get("paid_count") or 0) < 1:
            if remote.get("status") in ("cancelled", "expired", "halted"):
                await self.orders.update_one(
                    {"_id": order["_id"], "status": "created"}, {"$set": {"status": "expired", "auto_pay_active": False}}
                )
            return False
        # Claim FIRST (atomic, guarded on "created") — a concurrent sweep
        # or a late browser verify can only win this race once.
        claimed = await self.orders.find_one_and_update(
            {"_id": order["_id"], "status": "created"},
            {"$set": {"status": "paid", "paid_at": now_ist(), "cycles_applied": 1, "razorpay_payment_id": "via_mandate_sync"}},
        )
        if not claimed:
            return False
        subs = UserSubscriptionService(self.db)
        try:
            sub = await subs.subscribe(
                claimed["customer_id"],
                SubscribeRequest(
                    plan_id=claimed["plan_id"], vehicle_id=claimed.get("vehicle_id"), service_id=claimed.get("service_id"),
                    vehicle_type=claimed.get("vehicle_type"), auto_renew=True,
                ),
                razorpay_subscription_id=mandate_id,
                service_center_id=claimed.get("service_center_id"),
            )
        except Exception:
            await self.cancel_autopay(mandate_id, at_cycle_end=False)
            await self.orders.update_one({"_id": claimed["_id"]}, {"$set": {"auto_pay_active": False}})
            await self._flag_order_attention(
                {"_id": claimed["_id"]}, "auto-pay authorised but subscription could not be activated"
            )
            return False
        money_fields = {"payment_method": "online"}
        if claimed.get("issued_by"):
            money_fields["assigned_by"] = claimed["issued_by"]
        await subs.repo.update_by_id(sub["id"], money_fields)
        await self.orders.update_one({"_id": claimed["_id"]}, {"$set": {"subscription_id": sub["id"]}})
        await self._announce_subscription(claimed["customer_id"], sub, renewed=False)
        return True
