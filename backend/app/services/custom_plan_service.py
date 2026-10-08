"""
Custom multi-car plan — a manager's cart for ONE customer
(docs/FEATURE_PLAN_WALLET_EDITS_PLANS_2026-10-07.md §1.6).

Per car: the car (saved car, or plate + type) and per-service counts, e.g.
car A: 2 Star Wash + 2 Deep Cleaning; car B: 1 Deep Cleaning + 2 Star Wash.

- Price: computed HERE, never read from the client — Σ count × that car
  type's STANDARD service price (never the first-time price), less an
  optional whole-rupee manager discount on the total (manual_plan_discount:
  ≤ 50 % for a manager), split per car by price and frozen on the cart at
  its revision.
- Payment: one Razorpay link or cash for the whole cart. A link is minted
  for exactly one revision (`custom_plan_revision` on the order); revising
  the cart afterwards makes that link moot (still_payable -> False, and a
  payment that lands anyway is parked by PaymentService).
- Activation (activate_from_payment / mark_cash_paid): an atomic claim of
  the cart, then one car-bound `user_subscriptions` pass per car with
  per-service quotas (total_by_service / remaining_by_service, flat counts =
  their sum) — all cars start the same day, 30-day period. A car that holds
  another live pass meanwhile is skipped (never double-passed) and the cart
  parks as `needs_review` with the amount to refund. Idempotent: the same
  order activating twice (verify + webhook + sweep) creates nothing twice;
  a crashed activation is resumed by the next attempt.

The passes hang off ONE hidden template plan (plan_type "custom"): never
listed, sold, upgraded to or customer-cancelled through the general paths.

Collections: `custom_plans` (this module). Orders / links / cash rows live
in `payment_orders` with purpose "custom_plan" + custom_plan_id (+ revision,
service_center_id) — PaymentService dispatches a paid one to
activate_from_payment and asks still_payable before capturing.

Renewal (PLANS-2, founder 2026-10-07 — `renew`): staff copy (or edit) a
paid cart into a NEW cart with `renewal_of` = the old id, priced at today's
catalogue and paid like any cart. The old cart holds `renewal_cart_id`
(one open renewal per cart, set atomically). Each car remembers the old
pass it renews (`renews_subscription_id`). On activation a car whose old
pass is still live gets a `scheduled` pass starting the day after the old
pass's Last Booking Day (next_period_start); its claim moves to the new
pass (guarded on still being bound to the old one) and the old pass is
stamped `renewed_by_custom_plan_id` (no more extensions over it). Other
cars start today. See subscription_service "Scheduled passes".

Refund one car (PLANS-2 — `refund_car`): its unused washes at the cart's
per-wash price after its discount share (whole rupees), or a skipped car's
whole share; the pass is cancelled + zeroed, its claim released (a
scheduled renewal hands the car back to the old pass), the car marked
`refunded` and the customer wallet credited — all in ONE transaction, the
wallet key `cp-refund:{cart}:{car}` making a double tap credit once.
"""
import asyncio
import logging
import math
import secrets
from datetime import datetime, timedelta, timezone

from bson import ObjectId
from motor.motor_asyncio import AsyncIOMotorDatabase
from pymongo import ReturnDocument

from app.core.authz import ensure_customer_in_scope, ensure_own_center, resolve_grant_center_id
from app.core.exceptions import BadRequestException, ConflictException, NotFoundException
from app.models.enums import CustomerWalletEntryKind, NotificationType, SubscriptionStatus
from app.utils.money import round_rupees, split_whole_rupees
from app.utils.text import normalize_plate
from app.utils.timezone import day_label, from_stored, now_ist

logger = logging.getLogger(__name__)

COLLECTION = "custom_plans"
PERIOD_DAYS = 30
OPEN_STATUSES = ("draft", "awaiting_payment")
# A claimed activation that never finished (the instance died) is taken over
# by the next attempt after this long.
ACTIVATING_STALE = timedelta(minutes=2)
# The hidden template plan every custom pass points at — keyed by a fixed
# `_id` so concurrent first uses can't create two (no index needed).
TEMPLATE_PLAN_ID = "0000000000000000000c0571"
TEMPLATE_PLAN_NAME = "Custom plan"
# Never a real service id: a booking path that doesn't know custom passes
# (BookingService._subscription_discount's legacy branch) finds nothing
# "included" and waives NOTHING — it fails closed (charges) instead of
# waiving every main service. Custom passes are waived through
# plan_consumption's covered_service_ids.
TEMPLATE_INCLUDED_SENTINEL = "custom-plan-quota"


async def ensure_custom_plan_indexes(db) -> None:
    """Indexes for the cart lists and the per-car resume lookup (called from
    society_repository.ensure_society_indexes, i.e. create_indexes)."""
    await db[COLLECTION].create_index([("service_center_id", 1), ("created_at", -1)])
    await db[COLLECTION].create_index([("customer_id", 1), ("created_at", -1)])
    await db.user_subscriptions.create_index("custom_plan_id", sparse=True)
    # Plan refunds in a KPI window (subscription_service.custom_plan_refunds).
    await db[COLLECTION].create_index([("cars.refund.at", 1)], name="car_refund_at", sparse=True)
    # The scheduled-renewal promotion sweep (promote_scheduled_passes): only
    # scheduled passes are in it, so it stays tiny.
    await db.user_subscriptions.create_index(
        [("start_date", 1)], name="scheduled_pass_start", partialFilterExpression={"status": "scheduled"},
    )


async def ensure_template_plan(db) -> dict:
    """The hidden plan custom passes point at (made on first use)."""
    now = datetime.now(timezone.utc)
    return await db.subscription_plans.find_one_and_update(
        {"_id": ObjectId(TEMPLATE_PLAN_ID)},
        {"$setOnInsert": {
            "name": TEMPLATE_PLAN_NAME, "slug": "__custom-plan-template__", "plan_type": "custom",
            "description": "Hidden template for custom multi-car plans — never sold directly.",
            "billing_cycle": "monthly", "price": 0.0, "discounted_price": None,
            "vehicle_type_prices": {}, "vehicle_type_discounted_prices": {},
            "included_service_ids": [TEMPLATE_INCLUDED_SENTINEL], "category_quotas": {}, "total_service_count": 0,
            "vehicle_types": [], "upgrade_to_plan_ids": [], "is_active": True, "is_popular": False,
            "display_order": 9999, "is_deleted": False, "created_at": now, "updated_at": now,
        }},
        upsert=True, return_document=ReturnDocument.AFTER,
    )


def _iso(value) -> str | None:
    return from_stored(value).isoformat() if isinstance(value, datetime) else value


def _paise(rupees: float) -> int:
    return int(round(float(rupees or 0) * 100))


def _day_label(value) -> str | None:
    return day_label(from_stored(value)) if isinstance(value, datetime) else None


def _day_iso(value) -> str | None:
    return from_stored(value).date().isoformat() if isinstance(value, datetime) else None


# A cart is paid (its passes exist or were skipped) in these.
PAID_STATUSES = ("active", "needs_review")
# A car's pass with one of these bookings still holds a wash on it.
LIVE_BOOKING_STATUSES = ["awaiting_payment", "pending", "assigned", "captain_on_the_way", "service_started", "rescheduled"]


def refundable_for(car: dict, sub: dict | None) -> tuple[int, int]:
    """(whole rupees refundable, washes it pays back) for one car. A car
    with no pass (skipped at activation) gets its whole paid share back.
    Otherwise Σ remaining × unit price × (1 − discount share / price) — the
    per-wash price the customer actually paid — never above the car's paid
    share minus the washes it used, rounded DOWN to a whole rupee."""
    paid = float(car.get("amount") or 0)
    if sub is None:
        return max(0, int(math.floor(round(paid, 2)))), int(car.get("washes") or 0)
    price = float(car.get("price") or 0)
    factor = paid / price if price > 0 else 0.0
    units = {str(i.get("service_id")): float(i.get("unit_price") or 0) for i in car.get("items") or []}
    remaining = {str(k): max(0, int(v)) for k, v in (sub.get("remaining_by_service") or {}).items()}
    totals = {str(k): int(v) for k, v in (sub.get("total_by_service") or {}).items()}
    value = sum(n * units.get(sid, 0.0) * factor for sid, n in remaining.items())
    used = sum(max(0, totals.get(sid, 0) - remaining.get(sid, 0)) * units.get(sid, 0.0) * factor for sid in totals)
    capped = max(0.0, min(value, paid - used))
    return int(math.floor(round(capped, 2))), sum(remaining.values())


class CustomPlanService:
    def __init__(self, db: AsyncIOMotorDatabase):
        self.db = db
        self.carts = db[COLLECTION]

    # ------------------------------------------------------------------
    # Pricing (server-side only)
    # ------------------------------------------------------------------

    async def _vehicle_types(self) -> dict[str, dict]:
        rows = await self.db.vehicle_types.find({"is_deleted": {"$ne": True}}, {"name": 1, "is_active": 1}).to_list(length=200)
        return {str(r["_id"]): r for r in rows}

    async def _price(
        self, cars, discount_amount: int, actor_role: str, customer_id: str | None, *, resolve: bool, saved_cars: bool = True,
    ) -> dict:
        """The cart priced exactly as it will be charged. `resolve`: find or
        save each plate on the customer's account (create / revise); the
        preview only looks the plate up (same refusals, nothing saved).
        `saved_cars`: the customer is known to the actor's center, so their
        saved cars (`vehicle_id`) may be named."""
        from app.services.payment_service import manual_plan_discount
        from app.services.subscription_service import service_price_for_type

        types = await self._vehicle_types()
        service_ids = [ObjectId(i.service_id) for c in cars for i in c.items]
        services = {str(d["_id"]): d for d in await self.db.services.find({"_id": {"$in": service_ids}, "is_deleted": {"$ne": True}}).to_list(length=len(service_ids))}
        priced: list[dict] = []
        seen: set[str] = set()
        for car in cars:
            vehicle = None
            if car.vehicle_id:
                if not customer_id:
                    raise BadRequestException("Pick the customer first to use one of their saved cars.")
                vehicle = await self.db.vehicles.find_one(
                    {"_id": ObjectId(car.vehicle_id), "owner_id": customer_id, "is_deleted": {"$ne": True}}
                ) if saved_cars else None
                if not vehicle:
                    raise NotFoundException("Vehicle not found")
                vehicle_type = vehicle.get("vehicle_type")
                plate = vehicle.get("registration_number") or ""
            else:
                vehicle_type = car.vehicle_type
                plate = car.registration_number or ""
                if resolve and not plate:
                    raise BadRequestException("Enter each car's number plate.")
            vt = types.get(vehicle_type or "")
            if not vt or not vt.get("is_active", True):
                raise BadRequestException("Pick a valid car type for every car.")
            if vehicle is None and customer_id and (resolve or plate):
                from app.services.society_service import SocietyService

                # Create saves the plate on the account; the preview only
                # finds an already-saved car (its live pass is then refused).
                vehicle = await SocietyService(self.db)._ensure_vehicle(
                    customer_id, vehicle_type, plate, vt.get("name") or "Car", retype=False, create=resolve,
                )
            key = str(vehicle["_id"]) if vehicle else (normalize_plate(plate) or f"#{len(priced)}")
            if key in seen:
                raise BadRequestException(f"{plate or 'A car'} is in this plan twice — list each car once.")
            seen.add(key)
            items = []
            for item in car.items:
                service = services.get(item.service_id)
                if not service or not service.get("is_active", True) or service.get("is_addon"):
                    raise BadRequestException("Only active main services can go on a custom plan (add-ons are paid at the booking).")
                offered = service.get("vehicle_types") or []
                if offered and vehicle_type not in offered:
                    raise BadRequestException(f"{service.get('name')} isn't offered for {vt.get('name') or 'that car type'}.")
                unit = round(service_price_for_type(service, vehicle_type), 2)
                items.append({
                    "service_id": item.service_id, "service_name": service.get("name"), "count": int(item.count),
                    "unit_price": unit, "line_total": round(unit * int(item.count), 2),
                })
            priced.append({
                "vehicle_id": str(vehicle["_id"]) if vehicle else None,
                "registration_number": plate or None,
                "vehicle_type": vehicle_type,
                "vehicle_type_name": vt.get("name"),
                "items": items,
                "washes": sum(i["count"] for i in items),
                "price": round(sum(i["line_total"] for i in items), 2),
            })
        subtotal = round(sum(c["price"] for c in priced), 2)
        discount = int(round_rupees(manual_plan_discount(discount_amount, subtotal, actor_role))) if discount_amount else 0
        shares = split_whole_rupees(discount, [c["price"] for c in priced], [c["price"] for c in priced]) if discount else [0] * len(priced)
        for car, share in zip(priced, shares):
            car["discount_share"] = int(share)
            car["amount"] = round(car["price"] - share, 2)
        return {
            "cars": priced, "subtotal": subtotal, "discount_amount": discount,
            "total_amount": round(subtotal - discount, 2), "period_days": PERIOD_DAYS,
        }

    async def preview(self, payload, *, actor_role: str, actor_center_id: str | None) -> dict:
        """Read-only: what create would charge for this cart, refused exactly
        where create would refuse it — the same customer resolution (id, or
        phone + name), saved cars only for a customer known to the center,
        a car already on a live pass — but nothing is written: no account,
        no saved car (QA 2026-10-07)."""
        if getattr(payload, "renewal_of", None):
            return await self._preview_renewal(payload, actor_role=actor_role, actor_center_id=actor_center_id)
        customer, saved_cars = await self._customer_for(payload, actor_role, actor_center_id, create=False)
        customer_id = str(customer["_id"]) if customer else None
        priced = await self._price(payload.cars, payload.discount_amount, actor_role, customer_id, resolve=False, saved_cars=saved_cars)
        if customer_id:
            await self._refuse_cars_on_live_pass(customer_id, priced["cars"])
        return priced

    async def _preview_renewal(self, payload, *, actor_role: str, actor_center_id: str | None) -> dict:
        """The preview of a renewal (`renewal_of`): either the PAID cart about
        to be renewed (what `renew` would build — no cart id yet) or a DRAFT
        renewal cart being revised (what `revise` would save — its own id).
        Priced for the cart's customer and refused exactly where renew /
        revise refuse: a car's own pass that this renewal renews passes, any
        other live pass doesn't. Nothing is written (a new plate is only
        looked up)."""
        cart = await self.for_actor(payload.renewal_of, actor_role, actor_center_id)
        if cart.get("status") in OPEN_STATUSES and cart.get("renewal_of"):
            links, cart_id = await self._renewal_links(cart.get("renewal_of")), str(cart["_id"])
        elif cart.get("status") in PAID_STATUSES:
            links, cart_id = await self._renewal_links(str(cart["_id"])), None
        else:
            raise BadRequestException("Only a paid custom plan can be renewed.")
        customer_id = cart["customer_id"]
        if payload.customer_id and payload.customer_id != customer_id:
            raise BadRequestException("This renewal is for the plan's own customer.")
        priced = await self._price(payload.cars, payload.discount_amount, actor_role, customer_id, resolve=False)
        cars = [{**c, "renews_subscription_id": links.get(c.get("vehicle_id") or "")} for c in priced["cars"]]
        await self._refuse_cars_on_live_pass(customer_id, cars, cart_id=cart_id)
        return priced

    # ------------------------------------------------------------------
    # Cart lifecycle (manager of its center, or admin)
    # ------------------------------------------------------------------

    async def _customer_for(self, payload, actor_role: str, actor_center_id: str | None, *, create: bool = True) -> tuple[dict | None, bool]:
        """(the cart's customer, may their saved cars be named). Shared by
        preview (`create=False`: a new number stays unknown — None) and
        create. A customer picked by id must be known to the actor's center;
        one found by phone may be anyone's, so their saved cars are usable
        only when the center knows them."""
        if payload.customer_id:
            await ensure_customer_in_scope(self.db, actor_role, actor_center_id, payload.customer_id)
            customer = await self.db.users.find_one({"_id": ObjectId(payload.customer_id), "is_deleted": {"$ne": True}})
            if not customer or customer.get("role") != "customer":
                raise NotFoundException("Customer not found")
            return customer, True
        if not payload.customer_phone:
            return None, False
        from app.services.auth_service import AuthService

        customer = await AuthService(self.db).ensure_customer_by_phone(payload.customer_phone, payload.customer_name or "", create=create)
        if not customer:
            return None, False
        try:
            await ensure_customer_in_scope(self.db, actor_role, actor_center_id, str(customer["_id"]))
        except NotFoundException:
            return customer, False
        return customer, True

    async def _refuse_cars_on_live_pass(self, customer_id: str, cars: list[dict], cart_id: str | None = None) -> None:
        """A car already on a live pass can't go on a cart — except, on a
        RENEWAL cart, the very pass that car renews (`renews_subscription_id`)
        while no other cart has renewed it."""
        from app.services.subscription_service import UserSubscriptionService, last_booking_day_text

        subs = UserSubscriptionService(self.db)
        for car in cars:
            if not car.get("vehicle_id"):
                continue
            live = await subs._active_pass_for_vehicle(customer_id, car["vehicle_id"])
            if not live:
                continue
            plate = car.get("registration_number") or "A car"
            renews = car.get("renews_subscription_id")
            if renews and str(live["_id"]) == renews:
                other = live.get("renewed_by_custom_plan_id")
                if not other or other == cart_id:
                    continue
                raise BadRequestException(f"{plate}'s plan is already renewed by another custom plan — remove it from this cart.")
            # The day its washes can still be booked until — an extension
            # included (founder wording: "Last Booking Day: 19 Oct 2026").
            last = last_booking_day_text(live)
            until = f" ({last})" if last else ""
            raise BadRequestException(
                f"{plate} already has an active plan{until} — one car carries one plan. Remove it from this cart."
            )

    async def create(self, payload, *, actor_id: str, actor_role: str, actor_center_id: str | None) -> dict:
        center_id = resolve_grant_center_id(actor_role, actor_center_id, payload.service_center_id)
        if not await self.db.service_centers.find_one({"_id": ObjectId(center_id), "is_deleted": {"$ne": True}}, {"_id": 1}):
            raise BadRequestException("Pick a valid service center for this plan.")
        customer, saved_cars = await self._customer_for(payload, actor_role, actor_center_id)
        customer_id = str(customer["_id"])
        priced = await self._price(payload.cars, payload.discount_amount, actor_role, customer_id, resolve=True, saved_cars=saved_cars)
        await self._refuse_cars_on_live_pass(customer_id, priced["cars"])
        now = now_ist()
        doc = {
            "customer_id": customer_id,
            "service_center_id": center_id,
            "created_by": actor_id,
            "created_by_role": actor_role,
            "status": "draft",
            "revision": 1,
            **priced,
            "cars": [{**c, "status": "pending", "subscription_id": None} for c in priced["cars"]],
            "note": (payload.note or "").strip() or None,
            "link_order_ids": [],
            "payment": None,
            "created_at": now,
            "updated_at": now,
            "is_deleted": False,
        }
        result = await self.carts.insert_one(doc)
        return await self.view(await self.carts.find_one({"_id": result.inserted_id}))

    async def get(self, cart_id: str) -> dict:
        cart = await self.carts.find_one({"_id": ObjectId(cart_id), "is_deleted": {"$ne": True}}) if ObjectId.is_valid(cart_id or "") else None
        if not cart:
            raise NotFoundException("Custom plan not found")
        return cart

    async def for_actor(self, cart_id: str, actor_role: str, actor_center_id: str | None) -> dict:
        cart = await self.get(cart_id)
        ensure_own_center(actor_role, actor_center_id, cart.get("service_center_id"))
        return cart

    async def revise(self, cart_id: str, payload, *, actor_id: str, actor_role: str, actor_center_id: str | None) -> dict:
        """Replace the cart's contents. Only while unpaid; guarded on the
        revision the manager saw. Any link already sent is for the OLD
        revision: it is voided (and a payment that lands on it anyway can't
        activate this cart — it is parked for a refund)."""
        cart = await self.for_actor(cart_id, actor_role, actor_center_id)
        self._ensure_open(cart)
        priced = await self._price(payload.cars, payload.discount_amount, actor_role, cart["customer_id"], resolve=True)
        links = await self._renewal_links(cart.get("renewal_of"))
        new_cars = [{**c, "status": "pending", "subscription_id": None, "renews_subscription_id": links.get(c.get("vehicle_id") or "")}
                    for c in priced["cars"]]
        await self._refuse_cars_on_live_pass(cart["customer_id"], new_cars, cart_id=str(cart["_id"]))
        fields = {
            **priced,
            "cars": new_cars,
            "status": "draft",
            "revised_by": actor_id,
            "updated_at": now_ist(),
        }
        if payload.note is not None:
            fields["note"] = payload.note.strip() or None
        updated = await self.carts.find_one_and_update(
            {"_id": cart["_id"], "status": {"$in": list(OPEN_STATUSES)}, "revision": int(payload.expected_revision)},
            {"$set": fields, "$inc": {"revision": 1}},
            return_document=ReturnDocument.AFTER,
        )
        if not updated:
            await self._refuse_stale(cart_id)
        await self._void_links(updated, "custom plan changed")
        return await self.view(updated)

    async def cancel(self, cart_id: str, *, reason: str | None, actor_id: str, actor_role: str, actor_center_id: str | None) -> dict:
        """Before payment only. A link paid at this very moment loses to the
        cancel (its payment is parked for a refund) or wins (then this
        refuses: the plan is paid)."""
        cart = await self.for_actor(cart_id, actor_role, actor_center_id)
        self._ensure_open(cart)
        updated = await self.carts.find_one_and_update(
            {"_id": cart["_id"], "status": {"$in": list(OPEN_STATUSES)}},
            {"$set": {"status": "cancelled", "cancelled_at": now_ist(), "cancelled_by": actor_id,
                      "cancel_reason": (reason or "").strip() or None, "updated_at": now_ist()}},
            return_document=ReturnDocument.AFTER,
        )
        if not updated:
            self._ensure_open(await self.get(cart_id))
            raise ConflictException("This plan just changed — reload and try again.")
        if updated.get("renewal_of") and ObjectId.is_valid(updated["renewal_of"]):
            # The old plan can be renewed again.
            await self.carts.update_one(
                {"_id": ObjectId(updated["renewal_of"]), "renewal_cart_id": str(updated["_id"])}, {"$set": {"renewal_cart_id": None}},
            )
        await self._void_links(updated, "custom plan cancelled")
        return await self.view(updated)

    # ------------------------------------------------------------------
    # Renewal (staff): a new cart for the next 30 days of a paid one
    # ------------------------------------------------------------------

    async def _renewal_links(self, old_cart_id: str | None) -> dict[str, str]:
        """{vehicle_id: the old pass it renews} for a renewal cart — the old
        cart's cars that got a pass (never a refunded or skipped one)."""
        if not old_cart_id or not ObjectId.is_valid(old_cart_id):
            return {}
        old = await self.carts.find_one({"_id": ObjectId(old_cart_id)}, {"cars": 1})
        return {
            c["vehicle_id"]: c["subscription_id"]
            for c in (old or {}).get("cars") or []
            if c.get("vehicle_id") and c.get("subscription_id") and c.get("status") == "active"
        }

    async def renew(self, cart_id: str, payload, *, actor_id: str, actor_role: str, actor_center_id: str | None) -> dict:
        """A NEW cart renewing a paid one (`renewal_of`): the old cars and
        per-service counts by default (or the edited `cars`), priced at
        TODAY's catalogue for each car type, `discount_amount` (default 0,
        capped like any cart), draft until a link or cash pays it. One open
        renewal per cart (the old cart's `renewal_cart_id`, set atomically;
        cancelling the renewal frees it). The cars' own live passes — the
        ones being renewed — don't block it; any other live pass does."""
        from app.schemas.custom_plan_schema import CustomPlanCar

        old = await self.for_actor(cart_id, actor_role, actor_center_id)
        if old.get("status") not in PAID_STATUSES:
            raise BadRequestException("Only a paid custom plan can be renewed.")
        stale = old.get("renewal_cart_id")
        if stale:
            current = await self.carts.find_one({"_id": ObjectId(stale)}, {"status": 1}) if ObjectId.is_valid(stale) else None
            if current and current.get("status") != "cancelled":
                raise BadRequestException(
                    "This plan was already renewed — open its renewal "
                    + ("to send the link or take cash." if current.get("status") in OPEN_STATUSES else "(it's paid).")
                )
        links = await self._renewal_links(str(old["_id"]))
        if payload.cars:
            car_inputs = payload.cars
        else:
            car_inputs = [
                CustomPlanCar(vehicle_id=c["vehicle_id"], items=[{"service_id": i["service_id"], "count": int(i["count"])} for i in c.get("items") or []])
                for c in old.get("cars") or [] if c.get("vehicle_id") in links
            ]
            if not car_inputs:
                raise BadRequestException("No car on this plan can be renewed as it is — list the cars to renew.")
        priced = await self._price(car_inputs, payload.discount_amount or 0, actor_role, old["customer_id"], resolve=True)
        new_id = ObjectId()
        cars = [{**c, "status": "pending", "subscription_id": None, "renews_subscription_id": links.get(c.get("vehicle_id") or "")}
                for c in priced["cars"]]
        await self._refuse_cars_on_live_pass(old["customer_id"], cars, cart_id=str(new_id))
        slot = await self.carts.find_one_and_update(
            {"_id": old["_id"], "status": {"$in": list(PAID_STATUSES)}, "renewal_cart_id": {"$in": [None, stale]}},
            {"$set": {"renewal_cart_id": str(new_id), "updated_at": now_ist()}},
        )
        if slot is None:
            raise BadRequestException("This plan was just renewed by someone else — reload it.")
        now = now_ist()
        doc = {
            "_id": new_id,
            "customer_id": old["customer_id"],
            "service_center_id": old.get("service_center_id"),
            "created_by": actor_id,
            "created_by_role": actor_role,
            "status": "draft",
            "revision": 1,
            **priced,
            "cars": cars,
            "renewal_of": str(old["_id"]),
            "note": (payload.note or "").strip() or None,
            "link_order_ids": [],
            "payment": None,
            "created_at": now,
            "updated_at": now,
            "is_deleted": False,
        }
        try:
            await self.carts.insert_one(doc)
        except BaseException:
            await self.carts.update_one({"_id": old["_id"], "renewal_cart_id": str(new_id)}, {"$set": {"renewal_cart_id": stale}})
            raise
        return await self.view(await self.carts.find_one({"_id": new_id}))

    # ------------------------------------------------------------------
    # Refund one car of a paid cart (staff) — to the customer wallet
    # ------------------------------------------------------------------

    @staticmethod
    def _car_index(cart: dict, car_ref: str) -> int:
        cars = cart.get("cars") or []
        ref = str(car_ref or "").strip()
        if ref.isdigit() and int(ref) < len(cars):
            return int(ref)
        for i, car in enumerate(cars):
            if ref and car.get("vehicle_id") == ref:
                return i
        raise NotFoundException("That car isn't on this plan.")

    async def refund_car(self, cart_id: str, car_ref: str, *, amount: int | None, reason: str, actor_id: str, actor_role: str,
                         actor_center_id: str | None) -> dict:
        """Refund ONE car of a paid cart to the customer's wallet: at most
        `refundable_for` (its unused washes at the per-wash price it paid,
        or a skipped car's whole share); staff may refund less. In ONE
        transaction: the car's pass is cancelled with its washes zeroed (and
        its claim released — a scheduled renewal hands the car back to the
        pass it renewed), the car is marked `refunded` with {amount, by, at,
        reason}, and the wallet is credited (key cp-refund:{cart}:{car}).
        A double tap credits once (the second sees the car refunded:
        `already`). A pass with a live booking on it is refused — those
        bookings are cancelled first. Returns {custom_plan, refund, already,
        wallet}."""
        from app.services.customer_wallet_service import CustomerWalletService

        cart = await self.for_actor(cart_id, actor_role, actor_center_id)
        idx = self._car_index(cart, car_ref)
        car0 = cart["cars"][idx]
        plate = car0.get("registration_number") or "this car"
        key = f"cp-refund:{cart['_id']}:{car0.get('vehicle_id') or f'#{idx}'}"
        wallets = CustomerWalletService(self.db)
        reason = (reason or "").strip()

        async def _do(s):
            fresh = await self.carts.find_one({"_id": cart["_id"]}, session=s)
            car = (fresh.get("cars") or [])[idx]
            if car.get("status") == "refunded":
                return {"already": True, "refund": car.get("refund"), "wallet": None}
            if fresh.get("status") not in PAID_STATUSES or car.get("status") not in ("active", "skipped"):
                raise BadRequestException("Only a paid car can be refunded — this plan isn't paid yet." if fresh.get("status") in OPEN_STATUSES
                                          else f"{plate} has nothing to refund.")
            sub = None
            if car.get("status") == "active":
                sub = await self.db.user_subscriptions.find_one({"_id": ObjectId(car["subscription_id"])}, session=s)
                if sub is None:
                    raise NotFoundException("Plan not found")
                if sub.get("status") == SubscriptionStatus.CANCELLED.value:
                    raise BadRequestException(f"{plate}'s plan was already cancelled.")
                await self._refuse_live_bookings(sub, plate, s)
            value, washes = refundable_for(car, sub)
            if value < 1:
                raise BadRequestException(f"Nothing is left to refund on {plate} — its washes are used.")
            paid_back = value if amount is None else int(amount)
            if paid_back > value:
                raise BadRequestException(f"At most ₹{value} can be refunded for {plate} ({washes} unused wash{'' if washes == 1 else 'es'}).")
            now = now_ist()
            refund = {"amount": paid_back, "max_amount": value, "washes": washes, "reason": reason, "by": actor_id, "by_role": actor_role,
                      "at": now, "subscription_id": car.get("subscription_id"), "key": key}
            if sub is not None:
                done = await self.db.user_subscriptions.update_one(
                    {"_id": sub["_id"], "status": sub.get("status"), "updated_at": sub.get("updated_at")},
                    {"$set": {
                        "status": SubscriptionStatus.CANCELLED.value, "remaining_service_count": 0,
                        "remaining_by_service": {k: 0 for k in (sub.get("remaining_by_service") or {})},
                        "cancelled_at": now, "cancelled_by": actor_id, "cancel_reason": f"Refunded: {reason}",
                        "refund": refund, "auto_renew": False, "updated_at": now,
                    }},
                    session=s,
                )
                if done.modified_count != 1:
                    raise ConflictException("This car's plan just changed — reload and try again.")
                await self._release_refunded_claim(sub, s)
            cars_after = [*(fresh.get("cars") or [])]
            cars_after[idx] = {**car, "status": "refunded"}
            statuses = [c.get("status") for c in cars_after]
            new_status = "needs_review" if "skipped" in statuses else ("active" if "active" in statuses else "refunded")
            fields = {f"cars.{idx}.status": "refunded", f"cars.{idx}.refund": refund, f"cars.{idx}.refund_due": 0,
                      "status": new_status, "updated_at": now}
            if fresh.get("review"):
                left = [c for c in cars_after if c.get("status") == "skipped"]
                fields["review.refund_due"] = round(sum(float(c.get("amount") or 0) for c in left), 2)
                fields["review.skipped"] = [c.get("registration_number") for c in left]
                if not left:
                    fields["review.resolved"] = True
                    fields["review.resolved_at"] = now
            marked = await self.carts.update_one(
                {"_id": fresh["_id"], f"cars.{idx}.status": car.get("status"), f"cars.{idx}.vehicle_id": car.get("vehicle_id")},
                {"$set": fields}, session=s,
            )
            if marked.modified_count != 1:
                raise ConflictException("This plan just changed — reload and try again.")
            entry = await wallets.post(
                fresh["customer_id"], float(paid_back), CustomerWalletEntryKind.REFUND.value, key=key, booking_id=None, actor_id=actor_id, actor_role=actor_role,
                note=f"Custom plan refund — {plate}", session=s,
                meta={"custom_plan_id": str(fresh["_id"]), "vehicle_id": car.get("vehicle_id"), "subscription_id": car.get("subscription_id")},
            )
            return {"already": False, "refund": refund, "wallet": entry}

        async with await self.db.client.start_session() as session:
            outcome = await session.with_transaction(_do)
        if not outcome["already"] and (outcome.get("wallet") or {}).get("created"):
            await wallets.announce(cart["customer_id"], outcome["wallet"], reason=f"Custom plan refund — {plate}")
        refund = outcome.get("refund") or {}
        return {
            "already": outcome["already"],
            "refund": self._refund_view(refund, staff=True),
            "wallet": {"balance": (outcome.get("wallet") or {}).get("balance")} if outcome.get("wallet") else None,
            "vehicle_id": car0.get("vehicle_id"),
            "registration_number": car0.get("registration_number"),
            "custom_plan": await self.view(await self.get(cart_id)),
        }

    async def _refuse_live_bookings(self, sub: dict, plate: str, session) -> None:
        rows = await self.db.bookings.find(
            {"customer_id": sub.get("customer_id"), "subscription_id": str(sub["_id"]), "status": {"$in": LIVE_BOOKING_STATUSES},
             "is_deleted": {"$ne": True}},
            {"booking_number": 1, "scheduled_date": 1}, session=session,
        ).sort("scheduled_date", 1).to_list(length=10)
        if not rows:
            return
        listed = ", ".join(
            f"{r.get('booking_number') or 'a booking'}" + (f" on {day_label(from_stored(r['scheduled_date']))}" if isinstance(r.get("scheduled_date"), datetime) else "")
            for r in rows
        )
        n = len(rows)
        raise BadRequestException(
            f"{plate} has {n} booked plan wash{'' if n == 1 else 'es'} ({listed}) — cancel {'it' if n == 1 else 'them'} first, then refund."
        )

    async def _release_refunded_claim(self, sub: dict, session) -> None:
        """The refunded pass lets go of its car. A renewal that never started
        hands the car back to the pass it renewed (still live), which may
        then be extended again."""
        from app.services.subscription_service import PASS_SCHEDULED, pass_blocks_new, pass_claim_keys_of

        sid = str(sub["_id"])
        keys = pass_claim_keys_of(sub)
        pred_id = sub.get("renewal_of_subscription_id")
        if pred_id and sub.get("status") == PASS_SCHEDULED and ObjectId.is_valid(pred_id):
            pred = await self.db.user_subscriptions.find_one({"_id": ObjectId(pred_id)}, session=session)
            if pred is not None and pass_blocks_new(pred) and pred.get("renewed_by_custom_plan_id") == sub.get("custom_plan_id"):
                await self.db.pass_claims.update_many({"_id": {"$in": keys}, "subscription_id": sid}, {"$set": {"subscription_id": pred_id}}, session=session)
                await self._unmark_renewed(pred_id, sub.get("custom_plan_id"), session=session)
                return
        await self.db.pass_claims.delete_many({"_id": {"$in": keys}, "subscription_id": sid}, session=session)

    @staticmethod
    def _refund_view(refund: dict | None, *, staff: bool) -> dict | None:
        if not refund:
            return None
        view = {"amount": refund.get("amount"), "at": _iso(refund.get("at")), "reason": refund.get("reason"), "washes": refund.get("washes")}
        if staff:
            view.update({"by": refund.get("by"), "by_role": refund.get("by_role"), "max_amount": refund.get("max_amount")})
        return view

    @staticmethod
    def _ensure_open(cart: dict) -> None:
        status = cart.get("status")
        if status in OPEN_STATUSES:
            return
        if status == "cancelled":
            raise BadRequestException("This custom plan was cancelled.")
        raise BadRequestException("This custom plan is already paid — it can't be changed or cancelled here.")

    async def _refuse_stale(self, cart_id: str) -> None:
        fresh = await self.get(cart_id)
        self._ensure_open(fresh)
        raise ConflictException("This plan was changed since you opened it — reload it and try again.")

    # ------------------------------------------------------------------
    # Payment: link (one for the whole cart) or cash
    # ------------------------------------------------------------------

    async def send_link(self, cart_id: str, *, expected_revision: int, send_whatsapp: bool, actor_id: str, actor_role: str,
                        actor_center_id: str | None) -> dict:
        """A Razorpay link for exactly this revision's total. An unpaid link
        for the same revision and amount is re-sent, never duplicated."""
        from app.services.payment_service import MIN_ORDER_PAISE, PaymentService

        cart = await self.for_actor(cart_id, actor_role, actor_center_id)
        self._ensure_open(cart)
        if int(cart.get("revision") or 1) != int(expected_revision):
            raise ConflictException("This plan was changed since you opened it — reload it and try again.")
        amount_paise = _paise(cart["total_amount"])
        if amount_paise < MIN_ORDER_PAISE:
            raise BadRequestException("There's nothing to collect online for this plan — mark it paid in cash instead.")
        await self._refuse_cars_on_live_pass(cart["customer_id"], cart["cars"], cart_id=str(cart["_id"]))
        existing = await self._open_link(cart, amount_paise)
        if existing:
            order_id, short_url, reused = existing["_id"], existing["short_url"], True
        else:
            # MONEY's public link helper (PaymentService.create_link_order):
            # the Razorpay link + its payment_orders row, reused per revision.
            made = await PaymentService(self.db).create_link_order(
                purpose="custom_plan", customer_id=cart["customer_id"], amount_paise=amount_paise,
                description=self._describe(cart),
                reference={
                    "plan_id": TEMPLATE_PLAN_ID,
                    "custom_plan_id": str(cart["_id"]), "custom_plan_revision": int(cart.get("revision") or 1),
                    "car_count": len(cart.get("cars") or []),
                    "base_amount_paise": _paise(cart.get("subtotal")), "discount_paise": _paise(cart.get("discount_amount")),
                },
                issued_by=actor_id, service_center_id=cart.get("service_center_id"),
                reference_prefix=f"cpl-{str(cart['_id'])[-10:]}",
            )
            order_id, short_url, reused = ObjectId(made["order_id"]), made["short_url"], made["reused"]
        # Frozen to this revision: a revise/cash/cancel racing this send wins
        # or loses atomically — a link for a stale cart is voided at once.
        marked = await self.carts.find_one_and_update(
            {"_id": cart["_id"], "status": {"$in": list(OPEN_STATUSES)}, "revision": int(expected_revision)},
            {"$set": {"status": "awaiting_payment", "updated_at": now_ist()}, "$addToSet": {"link_order_ids": str(order_id)}},
            return_document=ReturnDocument.AFTER,
        )
        if not marked:
            from app.services.payment_service import PaymentService

            fresh_order = await self.orders.find_one({"_id": order_id})
            if fresh_order and fresh_order.get("status") == "created":
                await PaymentService(self.db).void_link_order(fresh_order, "custom plan changed while the link was being sent")
            await self._refuse_stale(cart_id)
        if send_whatsapp:
            await self._send_link_message(marked, amount_paise / 100, short_url)
        return {
            "short_url": short_url, "amount": amount_paise / 100, "order_id": str(order_id), "reused": reused,
            "sent": send_whatsapp, "custom_plan": await self.view(marked),
        }

    @property
    def orders(self):
        return self.db.payment_orders

    async def _open_link(self, cart: dict, amount_paise: int) -> dict | None:
        ids = [ObjectId(i) for i in cart.get("link_order_ids") or [] if ObjectId.is_valid(i)]
        if not ids:
            return None
        return await self.orders.find_one(
            {"_id": {"$in": ids}, "kind": "link", "status": "created", "custom_plan_revision": int(cart.get("revision") or 1),
             "amount_paise": amount_paise},
            sort=[("created_at", -1)],
        )

    async def _void_links(self, cart: dict, reason: str) -> int:
        """Best effort: unpaid links for this cart are cancelled at Razorpay.
        One paid at the same instant can't be — its payment then reaches
        activate_from_payment, which refuses it (parked for a refund)."""
        from app.services.payment_service import PaymentService

        ids = [ObjectId(i) for i in cart.get("link_order_ids") or [] if ObjectId.is_valid(i)]
        if not ids:
            return 0
        payments = PaymentService(self.db)
        voided = 0
        for order in await self.orders.find({"_id": {"$in": ids}, "kind": "link", "status": "created"}).to_list(length=50):
            try:
                voided += await payments.void_link_order(order, reason)
            except Exception:  # noqa: BLE001
                logger.exception("Could not void custom-plan link %s", order.get("_id"))
        return voided

    @staticmethod
    def _describe(cart: dict) -> str:
        cars = cart.get("cars") or []
        washes = sum(int(c.get("washes") or 0) for c in cars)
        head = "Custom plan renewal" if cart.get("renewal_of") else "Custom plan"
        return f"{head} — {len(cars)} car{'s' if len(cars) != 1 else ''}, {washes} washes ({PERIOD_DAYS} days)"

    async def _send_link_message(self, cart: dict, amount: float, short_url: str) -> None:
        """Same approved message as a plan link ("Pay ₹X to activate Y: link");
        a renewal reads "Pay ₹X to activate Custom plan renewal — …"."""
        try:
            from app.services.notification_service import NotificationService

            amount_text = f"{amount:g}"
            what = self._describe(cart)
            renewal = bool(cart.get("renewal_of"))
            title = "Pay to renew your custom plan" if renewal else "Pay to activate your custom plan"
            await NotificationService(self.db).notify(
                cart["customer_id"], title, f"Pay ₹{amount_text} to activate {what}: {short_url}",
                NotificationType.SYSTEM, str(cart["_id"]), wa_event="subscription_payment_link", wa_params=[amount_text, what, short_url],
            )
        except Exception:  # noqa: BLE001 — the link exists either way
            logger.exception("Could not send the custom-plan link for %s", cart.get("_id"))

    async def mark_cash_paid(self, cart_id: str, *, expected_revision: int, note: str | None, actor_id: str, actor_role: str,
                             actor_center_id: str | None) -> dict:
        """The manager took the money in cash: activate now. Refused BEFORE
        any money is recorded when a car already holds another plan (fix the
        cart first). Any link still out for this cart is voided."""
        cart = await self.for_actor(cart_id, actor_role, actor_center_id)
        self._ensure_open(cart)
        if int(cart.get("revision") or 1) != int(expected_revision):
            raise ConflictException("This plan was changed since you opened it — reload it and try again.")
        await self._refuse_cars_on_live_pass(cart["customer_id"], cart["cars"], cart_id=str(cart["_id"]))
        now = now_ist()
        claimed = await self.carts.find_one_and_update(
            {"_id": cart["_id"], "status": {"$in": list(OPEN_STATUSES)}, "revision": int(expected_revision)},
            {"$set": {
                "status": "activating", "activating_at": now, "period_start": now, "updated_at": now,
                "payment": {"method": "cash", "amount": cart["total_amount"], "collected_by": actor_id, "at": now,
                            "note": (note or "").strip() or None, "order_id": None},
            }},
            return_document=ReturnDocument.AFTER,
        )
        if not claimed:
            await self._refuse_stale(cart_id)
        # The ledger row: cash a manager collected is plan revenue like a
        # Razorpay payment (one row for the whole cart — counted once).
        row = await self.orders.insert_one({
            "kind": "cash", "purpose": "custom_plan", "customer_id": claimed["customer_id"], "plan_id": TEMPLATE_PLAN_ID,
            "custom_plan_id": str(claimed["_id"]), "custom_plan_revision": int(claimed.get("revision") or 1),
            "car_count": len(claimed.get("cars") or []),
            "base_amount_paise": _paise(claimed.get("subtotal")), "discount_paise": _paise(claimed.get("discount_amount")),
            "amount_paise": _paise(claimed.get("total_amount")), "currency": "INR",
            "status": "paid", "paid_at": now, "channel": "manager_cash", "issued_by": actor_id,
            "created_at": now, "service_center_id": claimed.get("service_center_id"),
        })
        claimed = await self.carts.find_one_and_update(
            {"_id": claimed["_id"]}, {"$set": {"payment.order_id": str(row.inserted_id)}}, return_document=ReturnDocument.AFTER,
        )
        result = await self._activate_cars(claimed, method="cash", actor_id=actor_id)
        await self._void_links(claimed, "paid in cash")
        return {**result, "custom_plan": await self.view(await self.get(cart_id))}

    # ------------------------------------------------------------------
    # PaymentService hooks
    # ------------------------------------------------------------------

    async def still_payable(self, order: dict) -> bool:
        """Is this order (link / checkout) still worth capturing? Only while
        the cart is unpaid AT THE REVISION and amount it was minted for."""
        cid = str(order.get("custom_plan_id") or "")
        if not ObjectId.is_valid(cid):
            return False
        cart = await self.carts.find_one({"_id": ObjectId(cid), "is_deleted": {"$ne": True}})
        if not cart or cart.get("status") not in OPEN_STATUSES:
            return False
        return (
            int(cart.get("revision") or 1) == int(order.get("custom_plan_revision") or 0)
            and _paise(cart.get("total_amount")) == int(order.get("amount_paise") or 0)
        )

    async def activate_from_payment(self, order: dict) -> dict:
        """A paid order for a custom plan. Returns {"ok": bool, ...}; not ok
        -> PaymentService parks the order for a human (the money is in):
        cart missing / cancelled / paid another way / revised since the link
        (revision or amount mismatch) / a car skipped (another live plan).
        Idempotent per order: verify + webhook + sweep (or a retry after a
        crash) activate once; a crashed activation is resumed."""
        cid = str(order.get("custom_plan_id") or "")
        oid = str(order.get("_id") or "")
        base = {"ok": False, "custom_plan_id": cid or None}
        if not ObjectId.is_valid(cid) or not oid:
            return base
        for _ in range(24):
            cart = await self.carts.find_one({"_id": ObjectId(cid), "is_deleted": {"$ne": True}})
            if not cart:
                return base
            payment = cart.get("payment") or {}
            if payment.get("order_id") == oid:
                status = cart.get("status")
                if status in ("active", "needs_review"):
                    return {**base, "ok": status == "active", "already": True, **self._outcome(cart)}
                if status == "activating":
                    started = cart.get("activating_at")
                    started = started.replace(tzinfo=timezone.utc) if isinstance(started, datetime) and started.tzinfo is None else started
                    if started and now_ist() - started > ACTIVATING_STALE:
                        # The activation that claimed it died: take it over
                        # (guarded on the stamp we judged) and finish it.
                        taken = await self.carts.find_one_and_update(
                            {"_id": cart["_id"], "status": "activating", "activating_at": cart.get("activating_at")},
                            {"$set": {"activating_at": now_ist()}}, return_document=ReturnDocument.AFTER,
                        )
                        if taken:
                            result = await self._activate_cars(taken, method="online", actor_id=None)
                            return {**base, "ok": result["all_active"], **result}
                        continue
                    await asyncio.sleep(0.25)
                    continue
                return base
            if cart.get("status") not in OPEN_STATUSES:
                return base
            if int(cart.get("revision") or 1) != int(order.get("custom_plan_revision") or 0):
                return {**base, "reason": "revision"}
            if _paise(cart.get("total_amount")) != int(order.get("amount_paise") or 0):
                return {**base, "reason": "amount"}
            now = now_ist()
            claimed = await self.carts.find_one_and_update(
                {"_id": cart["_id"], "status": {"$in": list(OPEN_STATUSES)}, "revision": int(cart.get("revision") or 1)},
                {"$set": {
                    "status": "activating", "activating_at": now, "period_start": now, "updated_at": now,
                    "payment": {"method": "online", "amount": round(int(order.get("amount_paise") or 0) / 100, 2), "order_id": oid,
                                "razorpay_payment_id": order.get("razorpay_payment_id"), "kind": order.get("kind"), "at": now},
                }},
                return_document=ReturnDocument.AFTER,
            )
            if not claimed:
                continue  # someone else moved it: judge the fresh state
            result = await self._activate_cars(claimed, method="online", actor_id=None)
            await self._void_links(claimed, "paid online")
            return {**base, "ok": result["all_active"], **result}
        return {**base, "reason": "busy"}

    @staticmethod
    def _outcome(cart: dict) -> dict:
        cars = cart.get("cars") or []
        return {
            "activated": len([c for c in cars if c.get("status") == "active"]),
            "skipped": [c.get("registration_number") for c in cars if c.get("status") == "skipped"],
        }

    async def _activate_cars(self, cart: dict, *, method: str, actor_id: str | None) -> dict:
        """One car-bound pass per car. Resumable: a car whose pass already
        exists for this cart is reused, never created twice; a car that
        holds another live pass is skipped (insert-first claim, PASS-1).
        A renewal car whose old pass is still live gets a SCHEDULED pass
        right after it (_schedule_after); every other car starts today."""
        from app.services.subscription_service import (
            PassClaimConflict,
            UserSubscriptionService,
            bind_pass_claim,
            claim_pass,
            pass_claim_keys,
            release_pass_claim,
        )

        await ensure_template_plan(self.db)
        subs = UserSubscriptionService(self.db)
        cid = str(cart["_id"])
        customer_id = cart["customer_id"]
        # Cars that start today start the moment the cart was claimed (kept
        # across a resumed activation).
        start = cart.get("period_start") or cart.get("activating_at") or now_ist()
        start = start.replace(tzinfo=timezone.utc) if start.tzinfo is None else start
        cars: list[dict] = []
        created: list[dict] = []
        for car in cart.get("cars") or []:
            if car.get("status") == "active" and car.get("subscription_id"):
                cars.append(car)
                continue
            mine = await self.db.user_subscriptions.find_one(
                {"custom_plan_id": cid, "vehicle_id": car["vehicle_id"], "is_deleted": {"$ne": True}, "status": {"$ne": SubscriptionStatus.CANCELLED.value}}
            )
            if mine:
                if mine.get("renewal_of_subscription_id"):
                    await self._link_successor(customer_id, car["vehicle_id"], mine["renewal_of_subscription_id"], str(mine["_id"]), cid)
                cars.append(self._car_activated(car, mine))
                continue
            skip = {**car, "status": "skipped", "note": "Car already had another active plan", "refund_due": car.get("amount")}
            old = await self._live_renewed_pass(car, customer_id)
            if old is not None:
                outcome = await self._schedule_after(cart, car, old, method=method, actor_id=actor_id)
                if outcome is None:
                    cars.append({**skip, "note": "Car's plan was renewed by another custom plan"})
                    continue
                if outcome is not False:
                    created.append(outcome)
                    cars.append(self._car_activated(car, outcome))
                    continue
                # False: the old pass ended meanwhile — it starts today below.
            if await subs._active_pass_for_vehicle(customer_id, car["vehicle_id"]):
                cars.append(skip)
                continue
            try:
                claim = await claim_pass(self.db, pass_claim_keys(customer_id, vehicle_id=car["vehicle_id"]), customer_id)
            except PassClaimConflict:
                cars.append(skip)
                continue
            doc = self._pass_doc(cart, car, start=start, status=SubscriptionStatus.ACTIVE.value, method=method, actor_id=actor_id)
            try:
                sub = await subs.repo.create(doc)
            except BaseException:
                await release_pass_claim(self.db, claim)
                raise
            await bind_pass_claim(self.db, claim, str(sub["_id"]))
            created.append(sub)
            cars.append(self._car_activated(car, sub))
        active = [c for c in cars if c.get("status") == "active"]
        skipped = [c for c in cars if c.get("status") == "skipped"]
        all_active = bool(active) and not skipped
        starts = [c["starts_at"] for c in active if isinstance(c.get("starts_at"), datetime)]
        ends = [c["ends_at"] for c in active if isinstance(c.get("ends_at"), datetime)]
        period_start = min(starts) if starts else start
        period_end = max(ends) if ends else start + timedelta(days=PERIOD_DAYS)
        update: dict = {"cars": cars, "status": "active" if all_active else "needs_review", "activated_at": now_ist(), "updated_at": now_ist(),
                        "period_start": period_start, "period_end": period_end}
        if not all_active:
            update["review"] = {
                "reason": "Some cars already had another active plan when this was paid — refund their share or sort it out with the customer.",
                "skipped": [c.get("registration_number") for c in skipped],
                "refund_due": round(sum(float(c.get("amount") or 0) for c in skipped), 2),
            }
        await self.carts.update_one({"_id": cart["_id"], "status": "activating"}, {"$set": update})
        if created:
            await self._announce(cart, active, period_end)
        if skipped:
            await self._tell_staff(cart, skipped)
        return {"all_active": all_active, "activated": len(active), "skipped": [c.get("registration_number") for c in skipped],
                "subscription_ids": [c["subscription_id"] for c in active]}

    @staticmethod
    def _car_activated(car: dict, sub: dict) -> dict:
        start, end = sub.get("start_date"), sub.get("end_date")
        start = start.replace(tzinfo=timezone.utc) if isinstance(start, datetime) and start.tzinfo is None else start
        end = end.replace(tzinfo=timezone.utc) if isinstance(end, datetime) and end.tzinfo is None else end
        return {**car, "status": "active", "subscription_id": str(sub["_id"]), "note": None, "starts_at": start, "ends_at": end}

    def _pass_doc(self, cart: dict, car: dict, *, start: datetime, status: str, method: str, actor_id: str | None) -> dict:
        from app.services.subscription_service import CUSTOM_PLAN_TYPE

        quotas = {i["service_id"]: int(i["count"]) for i in car.get("items") or []}
        share = int(car.get("discount_share") or 0)
        return {
            "customer_id": cart["customer_id"],
            "plan_id": TEMPLATE_PLAN_ID,
            "service_center_id": cart.get("service_center_id"),
            "vehicle_id": car["vehicle_id"],
            "service_id": None,
            "vehicle_type": car.get("vehicle_type"),
            "purchased_price": float(car.get("price") or 0),
            "amount_paid": float(car.get("amount") or 0),
            "discount_amount": float(share) if share else None,
            "payment_method": method,
            "status": status,
            "total_service_count": sum(quotas.values()),
            "remaining_service_count": sum(quotas.values()),
            "total_by_category": {},
            "remaining_by_category": {},
            "total_by_service": dict(quotas),
            "remaining_by_service": dict(quotas),
            "start_date": start,
            "end_date": start + timedelta(days=PERIOD_DAYS),
            "auto_renew": False,
            "razorpay_subscription_id": None,
            "renewal_count": 0,
            "plan_kind": CUSTOM_PLAN_TYPE,
            "custom_plan_id": str(cart["_id"]),
            "custom_plan_revision": int(cart.get("revision") or 1),
            "assigned_by": cart.get("created_by"),
            "granted_by": actor_id,
            "cash_collected_by": actor_id if method == "cash" else None,
            "cash_collected_at": now_ist() if method == "cash" else None,
            "extension_days": 0,
            "extended_until": None,
        }

    async def _live_renewed_pass(self, car: dict, customer_id: str) -> dict | None:
        """The old pass this renewal car renews — only while it is still
        live (active, inside its period or extension)."""
        from app.services.subscription_service import pass_blocks_new

        old_id = car.get("renews_subscription_id")
        if not old_id or not ObjectId.is_valid(old_id):
            return None
        old = await self.db.user_subscriptions.find_one(
            {"_id": ObjectId(old_id), "customer_id": customer_id, "vehicle_id": car.get("vehicle_id"), "is_deleted": {"$ne": True}}
        )
        if old is None or old.get("status") != SubscriptionStatus.ACTIVE.value or not pass_blocks_new(old):
            return None
        return old

    async def _schedule_after(self, cart: dict, car: dict, old: dict, *, method: str, actor_id: str | None):
        """The next period for a car whose old pass is still live. Returns
        the new pass; None when another cart already renewed that pass
        (skip); False when the old pass ended meanwhile (start today).

        1. Stamp the old pass `renewed_by_custom_plan_id` (guarded: no other
           cart's) — from here it can't be extended, so its end is final.
        2. Start = the day after its Last Booking Day (00:00 IST);
           `scheduled` while that is ahead.
        3. Take the car's claim from the old pass (guarded on still being
           bound to it — it stays bound to the old pass until the new one is
           bound, so the car is never unheld), create, bind."""
        from app.services.subscription_service import (
            PASS_SCHEDULED,
            PassClaim,
            PassClaimConflict,
            UserSubscriptionService,
            bind_pass_claim,
            claim_pass,
            next_period_start,
            pass_blocks_new,
            pass_claim_keys,
            release_pass_claim,
        )

        cid = str(cart["_id"])
        customer_id = cart["customer_id"]
        old_id = str(old["_id"])
        marked = await self.db.user_subscriptions.find_one_and_update(
            {"_id": old["_id"], "status": SubscriptionStatus.ACTIVE.value, "renewed_by_custom_plan_id": {"$in": [None, cid]}},
            {"$set": {"renewed_by_custom_plan_id": cid}},
            return_document=ReturnDocument.AFTER,
        )
        if marked is None:
            fresh = await self.db.user_subscriptions.find_one({"_id": old["_id"]})
            if fresh and fresh.get("renewed_by_custom_plan_id") not in (None, cid):
                return None
            return False
        if not pass_blocks_new(marked):
            await self._unmark_renewed(old_id, cid)
            return False
        start = next_period_start(marked)
        now = now_ist()
        status = PASS_SCHEDULED if start > now else SubscriptionStatus.ACTIVE.value
        keys = pass_claim_keys(customer_id, vehicle_id=car["vehicle_id"])
        token = secrets.token_hex(12)
        swapped = await self.db.pass_claims.find_one_and_update(
            {"_id": keys[0], "subscription_id": old_id}, {"$set": {"token": token, "successor_custom_plan_id": cid}},
        )
        claim = PassClaim(token, keys) if swapped else None
        if claim is None:
            try:
                claim = await claim_pass(self.db, keys, customer_id)
            except PassClaimConflict:
                await self._unmark_renewed(old_id, cid)
                return None
        doc = self._pass_doc(cart, car, start=start, status=status, method=method, actor_id=actor_id)
        doc["renewal_of_subscription_id"] = old_id
        try:
            sub = await UserSubscriptionService(self.db).repo.create(doc)
        except BaseException:
            if not swapped:
                await release_pass_claim(self.db, claim)
            await self._unmark_renewed(old_id, cid)
            raise
        await bind_pass_claim(self.db, claim, str(sub["_id"]))
        await self._link_successor(customer_id, car["vehicle_id"], old_id, str(sub["_id"]), cid)
        return sub

    async def _link_successor(self, customer_id: str, vehicle_id: str, old_id: str, new_id: str, cid: str) -> None:
        """Idempotent tail of _schedule_after (also run when a crashed
        activation is resumed): the car's claim is the new pass's, and the
        old pass names its successor."""
        from app.services.subscription_service import pass_claim_keys

        keys = pass_claim_keys(customer_id, vehicle_id=vehicle_id)
        await self.db.pass_claims.update_many({"_id": {"$in": keys}, "subscription_id": old_id}, {"$set": {"subscription_id": new_id}})
        await self.db.user_subscriptions.update_one(
            {"_id": ObjectId(old_id), "renewed_by_custom_plan_id": cid}, {"$set": {"renewed_by_subscription_id": new_id}},
        )

    async def _unmark_renewed(self, old_id: str, cid: str, session=None) -> None:
        await self.db.user_subscriptions.update_one(
            {"_id": ObjectId(old_id), "renewed_by_custom_plan_id": cid},
            {"$set": {"renewed_by_custom_plan_id": None, "renewed_by_subscription_id": None}}, session=session,
        )

    async def _announce(self, cart: dict, active: list[dict], end: datetime) -> None:
        """The customer's one confirmation for the whole cart (in-app, and
        WhatsApp through the approved plan-activated template). A renewal
        says when its new period starts."""
        try:
            from app.services.notification_service import NotificationService

            customer = await self.db.users.find_one({"_id": ObjectId(cart["customer_id"])}) if ObjectId.is_valid(cart["customer_id"]) else None
            first = ((customer or {}).get("full_name") or "there").split(" ")[0]
            plates = ", ".join(c.get("registration_number") or "your car" for c in active)
            washes = str(sum(int(c.get("washes") or 0) for c in active))
            valid_till = day_label(from_stored(end))
            title, body = f"{TEMPLATE_PLAN_NAME} is active", f"{washes} washes for {plates}, valid till {valid_till}."
            if cart.get("renewal_of"):
                starts = [c["starts_at"] for c in active if isinstance(c.get("starts_at"), datetime)]
                later = [s for s in starts if s > now_ist()]
                begins = f"from {day_label(from_stored(min(later)))}" if later else "from today"
                title, body = f"{TEMPLATE_PLAN_NAME} renewed", f"{washes} washes for {plates}, {begins}, valid till {valid_till}."
            await NotificationService(self.db).notify(
                cart["customer_id"], title, body,
                NotificationType.SUBSCRIPTION, str(cart["_id"]),
                wa_event="subscription_activated", wa_params=[first, TEMPLATE_PLAN_NAME, plates, washes, valid_till],
            )
        except Exception:  # noqa: BLE001 — the passes are live either way
            logger.exception("Could not announce custom plan %s", cart.get("_id"))

    async def _tell_staff(self, cart: dict, skipped: list[dict]) -> None:
        """In-app only: the manager who built the cart learns which cars were
        skipped and what to refund."""
        if not cart.get("created_by"):
            return
        try:
            from app.services.notification_service import NotificationService

            plates = ", ".join(c.get("registration_number") or "a car" for c in skipped)
            refund = round(sum(float(c.get("amount") or 0) for c in skipped), 2)
            await NotificationService(self.db).notify(
                cart["created_by"], "Custom plan needs review",
                f"{plates} already had another active plan, so {'it was' if len(skipped) == 1 else 'they were'} skipped — refund ₹{refund:g}.",
                NotificationType.SYSTEM, str(cart["_id"]), send_whatsapp=False,
            )
        except Exception:  # noqa: BLE001
            logger.exception("Could not alert staff about custom plan %s", cart.get("_id"))

    # ------------------------------------------------------------------
    # Views
    # ------------------------------------------------------------------

    async def view(self, cart: dict, *, staff: bool = True) -> dict:
        return (await self._views([cart], await self._subs_for([cart]), staff=staff))[0]

    @staticmethod
    def _projected_start(car: dict, subs: dict[str, dict], now: datetime) -> datetime | None:
        """An unpaid renewal car's period start if it were paid now: the day
        after its old pass's Last Booking Day while that pass is live,
        otherwise today. None for a car that isn't renewing anything."""
        from app.services.subscription_service import next_period_start, pass_blocks_new

        old = subs.get(car.get("renews_subscription_id") or "")
        if old is not None and old.get("status") == SubscriptionStatus.ACTIVE.value and pass_blocks_new(old, now):
            return next_period_start(old)
        return now

    async def _views(self, carts: list[dict], subs: dict[str, dict], *, staff: bool) -> list[dict]:
        from app.services.subscription_service import _with_effective_status

        customer_ids = [ObjectId(c["customer_id"]) for c in carts if ObjectId.is_valid(c.get("customer_id") or "")]
        users = {
            str(u["_id"]): u
            for u in await self.db.users.find({"_id": {"$in": customer_ids}}, {"full_name": 1, "phone": 1}).to_list(length=len(customer_ids))
        } if customer_ids else {}
        link_ids = [ObjectId(i) for c in carts for i in c.get("link_order_ids") or [] if ObjectId.is_valid(i)]
        links = await self.orders.find(
            {"_id": {"$in": link_ids}, "status": "created"}, {"short_url": 1, "amount_paise": 1, "custom_plan_revision": 1, "created_at": 1}
        ).to_list(length=len(link_ids)) if link_ids else []
        now = now_ist()
        out = []
        for cart in carts:
            is_open = cart.get("status") in OPEN_STATUSES
            renewal = bool(cart.get("renewal_of"))
            cars = []
            for car in cart.get("cars") or []:
                sub = subs.get(car.get("subscription_id") or "")
                pass_view = None
                if sub:
                    v = _with_effective_status(sub)
                    left = sub.get("remaining_by_service") or {}
                    pass_view = {
                        "id": str(sub["_id"]),
                        "status": v.get("effective_status"),
                        "remaining": int(sub.get("remaining_service_count") or 0),
                        "total": int(sub.get("total_service_count") or 0),
                        "remaining_by_service": {k: int(x) for k, x in left.items()},
                        "start_date": v.get("start_date"),
                        "end_date": v.get("end_date"),
                        "extension_days": v.get("extension_days"),
                        "extended_until": v.get("extended_until"),
                        "last_bookable_day": v.get("last_bookable_day"),
                        # Shown as "Last Booking Day: 19 Oct 2026".
                        "last_booking_day_label": v.get("last_booking_day_label"),
                        "in_extension": bool(v.get("in_extension")),
                        # A renewal lined up after this pass (its own cart).
                        "renewed_by_subscription_id": sub.get("renewed_by_subscription_id"),
                    }
                    if staff:
                        pass_view["can_extend"] = v.get("can_extend")
                        pass_view["extension_days_left"] = v.get("extension_days_left")
                # When this car's 30 days start: its pass's start, or — an
                # unpaid renewal — when it would start if paid now.
                if sub:
                    starts = sub.get("start_date")
                elif is_open and renewal:
                    starts = self._projected_start(car, subs, now)
                else:
                    starts = None
                row = {
                    "vehicle_id": car.get("vehicle_id"),
                    "registration_number": car.get("registration_number"),
                    "vehicle_type": car.get("vehicle_type"),
                    "vehicle_type_name": car.get("vehicle_type_name"),
                    "items": [
                        {**i, "remaining": int((pass_view or {}).get("remaining_by_service", {}).get(i["service_id"], i["count"] if not sub else 0))}
                        for i in car.get("items") or []
                    ],
                    "washes": car.get("washes"),
                    "price": car.get("price"),
                    "discount_share": car.get("discount_share"),
                    "amount": car.get("amount"),
                    "status": car.get("status"),
                    "note": car.get("note"),
                    "refund_due": car.get("refund_due") if staff else None,
                    "refund": self._refund_view(car.get("refund"), staff=staff),
                    "renews_subscription_id": car.get("renews_subscription_id"),
                    "starts_on": _day_iso(starts),
                    "starts_on_label": _day_label(starts),
                    "subscription_id": car.get("subscription_id"),
                    "subscription": pass_view,
                }
                if staff:
                    # The most a refund of this car can be (None: nothing to
                    # refund — unpaid, refunded, or its plan already cancelled).
                    refundable = None
                    if cart.get("status") in PAID_STATUSES and car.get("status") == "skipped":
                        refundable = refundable_for(car, None)[0]
                    elif cart.get("status") in PAID_STATUSES and car.get("status") == "active" and sub and sub.get("status") != SubscriptionStatus.CANCELLED.value:
                        refundable = refundable_for(car, sub)[0]
                    row["refundable_amount"] = refundable
                cars.append(row)
            holder = users.get(cart.get("customer_id") or "", {})
            open_link = next(
                (lk for lk in sorted(links, key=lambda x: x.get("created_at") or datetime.min, reverse=True)
                 if str(lk["_id"]) in (cart.get("link_order_ids") or []) and int(lk.get("custom_plan_revision") or 0) == int(cart.get("revision") or 1)),
                None,
            )
            payment = cart.get("payment") or None
            car_starts = [c["starts_on"] for c in cars if c.get("starts_on")]
            first_start = min(car_starts) if car_starts else None
            total = cart.get("total_amount")
            view = {
                "id": str(cart["_id"]),
                "customer_id": cart.get("customer_id"),
                "customer_name": holder.get("full_name"),
                "customer_phone": holder.get("phone"),
                "service_center_id": cart.get("service_center_id"),
                "plan_name": TEMPLATE_PLAN_NAME,
                "status": cart.get("status"),
                "revision": int(cart.get("revision") or 1),
                "cars": cars,
                "car_count": len(cars),
                "washes": sum(int(c.get("washes") or 0) for c in cars),
                "subtotal": cart.get("subtotal"),
                "discount_amount": cart.get("discount_amount"),
                "total_amount": total,
                "period_days": cart.get("period_days") or PERIOD_DAYS,
                "period_start": _iso(cart.get("period_start")),
                "period_end": _iso(cart.get("period_end")),
                "note": cart.get("note"),
                # Renewal (PLANS-2): this cart renews `renewal_of`; an old cart
                # names its renewal in `renewal_cart_id`.
                "renewal_of": cart.get("renewal_of"),
                "renewal_cart_id": cart.get("renewal_cart_id"),
                "renewal_starts_on": first_start if renewal else None,
                "renewal_starts_on_label": (
                    day_label(first_start) if renewal and first_start else None
                ),
                # The customer's button while unpaid: "Pay ₹1499 To Renew".
                "pay_label": (
                    f"Pay ₹{float(total or 0):g} To {'Renew' if renewal else 'Activate'}" if is_open else None
                ),
                "payment": {
                    "method": payment.get("method"), "amount": payment.get("amount"), "order_id": payment.get("order_id"),
                    "at": _iso(payment.get("at")),
                } if payment else None,
                "payment_link": {
                    "short_url": open_link.get("short_url"), "amount": round((open_link.get("amount_paise") or 0) / 100, 2),
                    "order_id": str(open_link["_id"]),
                } if open_link else None,
                "refunded_amount": sum(int((c.get("refund") or {}).get("amount") or 0) for c in cars),
                "created_at": _iso(cart.get("created_at")),
                "activated_at": _iso(cart.get("activated_at")),
                "cancelled_at": _iso(cart.get("cancelled_at")),
            }
            if staff:
                view.update({
                    "created_by": cart.get("created_by"),
                    "created_by_role": cart.get("created_by_role"),
                    "review": cart.get("review"),
                    "cancel_reason": cart.get("cancel_reason"),
                })
            out.append(view)
        return out

    async def _subs_for(self, carts: list[dict]) -> dict[str, dict]:
        """Every pass the carts' cars hold — and, for a renewal, the old pass
        each car renews (its projected start)."""
        ids = {
            ObjectId(x)
            for cart in carts for c in cart.get("cars") or []
            for x in (c.get("subscription_id"), c.get("renews_subscription_id"))
            if x and ObjectId.is_valid(x)
        }
        if not ids:
            return {}
        return {str(s["_id"]): s for s in await self.db.user_subscriptions.find({"_id": {"$in": list(ids)}}).to_list(length=len(ids))}

    async def list_for_staff(self, *, actor_role: str, actor_center_id: str | None, service_center_id: str | None = None,
                             status: str | None = None, customer_id: str | None = None, page: int = 1, page_size: int = 20) -> tuple[list[dict], int]:
        """A manager: their own center's carts only (never a client-chosen
        center); an admin: any, optionally one center."""
        from app.core.authz import manager_center_or_raise

        own = manager_center_or_raise(actor_role, actor_center_id)
        query: dict = {"is_deleted": {"$ne": True}}
        center = own or service_center_id
        if center:
            query["service_center_id"] = center
        if status:
            query["status"] = status
        if customer_id:
            query["customer_id"] = customer_id
        page_size = max(1, min(int(page_size or 20), 100))
        page = max(1, int(page or 1))
        total, carts = await asyncio.gather(
            self.carts.count_documents(query),
            self.carts.find(query).sort("created_at", -1).skip((page - 1) * page_size).limit(page_size).to_list(length=page_size),
        )
        return await self._views(carts, await self._subs_for(carts), staff=True), total

    async def list_mine(self, customer_id: str) -> list[dict]:
        """The customer's custom plans: paid ones (their passes, per-service
        remaining) and any waiting on payment (with the link to pay)."""
        carts = await self.carts.find(
            {"customer_id": customer_id, "is_deleted": {"$ne": True}, "status": {"$in": ["awaiting_payment", "activating", "active", "needs_review", "refunded"]}}
        ).sort("created_at", -1).limit(20).to_list(length=20)
        return await self._views(carts, await self._subs_for(carts), staff=False)
