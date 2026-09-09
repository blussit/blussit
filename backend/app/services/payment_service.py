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

The `payment_orders` doc doubles as the idempotency guard: verify claims
it atomically (created -> paid), so a replayed verify can't double-apply
(double-subscribe, double-mark) — the replay just gets the same success
back.
"""
import hashlib
import hmac
import secrets

from motor.motor_asyncio import AsyncIOMotorDatabase

from app.core.config import settings
from app.core.exceptions import BadRequestException, NotFoundException
from app.models.enums import PaymentMethod, PaymentStatus
from app.repositories.booking_repository import BookingRepository
from app.repositories.subscription_repository import SubscriptionPlanRepository
from app.schemas.payment_schema import CreateOrderRequest, VerifyPaymentRequest
from app.utils.serializers import serialize_doc
from app.utils.timezone import now_ist

# Razorpay's own floor — anything below is refused before we even call them.
MIN_ORDER_PAISE = 100



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
        if payload.purpose == "booking":
            amount_paise, description, reference = await self._booking_order(customer_id, payload)
        else:
            amount_paise, description, reference = await self._subscription_order(customer_id, payload)

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
        # Dry-run the exact create the verify step will perform — raises the
        # same phone-verification / vehicle-type / already-subscribed errors
        # the old direct subscribe did, before any money moves.
        await UserSubscriptionService(self.db).validate_purchase(
            customer_id, SubscribeRequest(plan_id=payload.plan_id, vehicle_type=payload.vehicle_type)
        )
        price = resolve_plan_price(plan, payload.vehicle_type)
        amount_paise = int(round(float(price) * 100))
        description = f"{plan['name']} subscription"
        return amount_paise, description, {
            "receipt": f"sub-{plan.get('slug', payload.plan_id)}"[:40],
            "plan_id": payload.plan_id,
            "vehicle_type": payload.vehicle_type,
        }

    async def verify_payment(self, customer_id: str, payload: VerifyPaymentRequest) -> dict:
        """Recompute the signature and, only on an exact match, apply the
        purchase. The atomic created->paid claim on payment_orders makes
        this replay-safe."""
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

        # Subscription: the verified payment IS the purchase — create it now.
        from app.schemas.subscription_schema import SubscribeRequest
        from app.services.subscription_service import UserSubscriptionService

        try:
            sub = await UserSubscriptionService(self.db).subscribe(
                customer_id, SubscribeRequest(plan_id=order["plan_id"], vehicle_type=order.get("vehicle_type"))
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
        return {"status": "paid", "purpose": "subscription", "subscription": serialize_doc(sub) if "_id" in sub else sub}

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
        return True

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

    async def captain_collect_cash(self, booking_id: str, captain_id: str) -> dict:
        booking = await self._captain_booking(booking_id, captain_id)
        if booking.get("payment_status") == PaymentStatus.PAID.value:
            raise BadRequestException("Already paid — nothing to collect.")
        if booking.get("status") != "completed":
            raise BadRequestException("Finish the service first — cash is collected after the wash is done.")
        updated = await self.booking_repo.update_if(
            booking_id,
            {"payment_status": PaymentStatus.PENDING.value},
            {
                "payment_status": PaymentStatus.PAID.value,
                "payment_method": PaymentMethod.CASH.value,
                "cash_collected_by": captain_id,
                "cash_collected_at": now_ist(),
            },
        )
        if not updated:
            raise BadRequestException("Already paid — nothing to collect.")
        return {"payment_status": "paid", "payment_method": "cash"}

    async def captain_payment_link(self, booking_id: str, captain_id: str) -> dict:
        """The QR's target. Reuses this booking's still-pending link when
        the amount hasn't changed (no link spam from reopening the modal),
        otherwise mints a fresh one."""
        booking = await self._captain_booking(booking_id, captain_id)
        if booking.get("payment_status") == PaymentStatus.PAID.value:
            raise BadRequestException("Already paid — nothing to collect.")
        amount_paise = int(round(float(booking.get("total_amount") or 0) * 100))
        existing = await self.orders.find_one(
            {"kind": "link", "booking_id": booking_id, "status": "created", "amount_paise": amount_paise, "short_url": {"$exists": True}}
        )
        if existing:
            return {"short_url": existing["short_url"], "amount": amount_paise, "link_id": existing["razorpay_link_id"]}
        return await self.create_payment_link(booking, contact_phone=booking.get("customer_phone"), name=booking.get("customer_name"))

    async def captain_check_payment(self, booking_id: str, captain_id: str) -> dict:
        """Polled by the QR modal every few seconds — actively syncs THIS
        booking's pending links against Razorpay (no waiting out the 60s
        background sweep at the customer's doorstep), and reports the
        booking's live payment state however it was paid."""
        booking = await self._captain_booking(booking_id, captain_id)
        if booking.get("payment_status") != PaymentStatus.PAID.value and settings.RAZORPAY_KEY_ID:
            pending = await self.orders.find(
                {"kind": "link", "booking_id": booking_id, "status": "created"}
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
        return {"payment_status": booking.get("payment_status"), "payment_method": booking.get("payment_method")}

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

    async def create_payment_link(self, booking: dict, contact_phone: str | None, name: str | None) -> dict:
        amount_paise = int(round(float(booking.get("total_amount") or 0) * 100))
        if amount_paise < MIN_ORDER_PAISE:
            raise BadRequestException("This amount is below the minimum for online payment (₹1).")
        if booking.get("payment_status") == PaymentStatus.PAID.value:
            raise BadRequestException("This booking is already paid.")

        client = _razorpay_client()
        # reference_id must be unique across ALL links ever created — a
        # re-requested link for the same booking gets a fresh suffix.
        reference_id = f"{booking['booking_number']}-{secrets.token_hex(3)}"
        payload: dict = {
            "amount": amount_paise,
            "currency": "INR",
            "reference_id": reference_id,
            "description": f"Blussit booking {booking['booking_number']}",
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

        await self.orders.insert_one(
            {
                "kind": "link",
                "razorpay_link_id": link["id"],
                "short_url": link["short_url"],
                "reference_id": reference_id,
                "customer_id": booking["customer_id"],
                "purpose": "booking",
                "booking_id": str(booking["_id"]),
                "booking_number": booking["booking_number"],
                "amount_paise": amount_paise,
                "currency": "INR",
                "status": "created",
                "created_at": now_ist(),
            }
        )
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
        settled = await self._settle_booking_payment(claimed, None, payment_id, via="link")
        return {
            "status": "paid" if settled else "needs_attention",
            "booking_number": order.get("booking_number"),
            "booking_id": order["booking_id"],
            "settled": settled,
        }
