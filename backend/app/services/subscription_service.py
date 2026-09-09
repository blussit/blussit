from datetime import datetime, timedelta, timezone

from bson import ObjectId

from motor.motor_asyncio import AsyncIOMotorDatabase

from app.core.exceptions import BadRequestException, ForbiddenException, NotFoundException, PhoneNotVerifiedException
from app.models.enums import BillingCycle, SubscriptionStatus
from app.repositories.subscription_repository import SubscriptionPlanRepository, UserSubscriptionRepository
from app.repositories.user_repository import UserRepository
from app.repositories.vehicle_repository import VehicleRepository
from app.repositories.vehicle_type_repository import VehicleTypeRepository
from app.schemas.subscription_schema import (
    AssignSubscriptionRequest,
    SubscriptionPlanCreateRequest,
    SubscriptionPlanUpdateRequest,
    SubscribeRequest,
)
from app.utils.serializers import serialize_doc, serialize_list
from app.utils.text import slugify
from app.utils.timezone import now_ist

_CYCLE_DAYS = {
    BillingCycle.MONTHLY.value: 30,
    BillingCycle.QUARTERLY.value: 90,
    BillingCycle.YEARLY.value: 365,
}


def resolve_plan_price(plan: dict, vehicle_type: str | None) -> float:
    """What this plan actually costs for a given vehicle type. Per-type
    overrides win when set (a per-type discounted price beating the per-type
    base price); only when NO per-type price exists for this type does the
    flat discounted_price/price apply. Mirrors the shape of
    BookingService._resolve_price but without the first-time dimension —
    plan purchases have no first-time offer."""
    if vehicle_type:
        type_discounted = plan.get("vehicle_type_discounted_prices") or {}
        type_prices = plan.get("vehicle_type_prices") or {}
        if vehicle_type in type_discounted:
            return type_discounted[vehicle_type]
        if vehicle_type in type_prices:
            return type_prices[vehicle_type]
    discounted = plan.get("discounted_price")
    return discounted if discounted is not None else plan.get("price", 0.0)


def tier_allows(plan: dict, purchased_type: str | None, candidate_type: str) -> bool:
    """The tier rule: a subscription bought for one vehicle type can be
    redeemed on that type or any type this plan prices CHEAPER (an XUV-tier
    card works for a sedan or hatchback, never the other way around). "Lower"
    is defined by the plan's own per-type price — the same admin-set numbers
    the buyer chose between at purchase — so there's no separate hierarchy
    to maintain. No purchased tier recorded (legacy subs) = no tier cap."""
    if not purchased_type or candidate_type == purchased_type:
        return True
    return resolve_plan_price(plan, candidate_type) <= resolve_plan_price(plan, purchased_type)


def _with_effective_status(sub: dict) -> dict:
    """Serializes a subscription with an `effective_status` that reflects
    expiry immediately (end_date has passed => "expired"), even though the
    stored `status` field only ever gets lazily flipped at actual
    consumption time (_get_active_subscription). Every list/browse view
    should read `effective_status`, not `status`, or a customer sees a
    stale "Active" badge on a plan that's actually unusable."""
    doc = serialize_doc(sub)
    status = doc.get("status")
    end_date = sub.get("end_date")
    if status == SubscriptionStatus.ACTIVE.value and end_date:
        # end_date is a computed aware-UTC-semantic instant (see
        # _get_active_subscription's comment for why .replace(tzinfo=utc)
        # is the correct treatment here, not to_ist()).
        if end_date.replace(tzinfo=timezone.utc) < now_ist():
            doc["effective_status"] = SubscriptionStatus.EXPIRED.value
            return doc
    doc["effective_status"] = status
    return doc


def _with_effective_statuses(subs: list[dict]) -> list[dict]:
    return [_with_effective_status(s) for s in subs]


class SubscriptionPlanService:
    def __init__(self, db: AsyncIOMotorDatabase):
        self.repo = SubscriptionPlanRepository(db)

    async def list_all(self, active_only: bool = False) -> list[dict]:
        filters = {"is_active": True} if active_only else None
        items = await self.repo.find_all_no_paginate(filters, sort_by="display_order", sort_order=1)
        return serialize_list(items)

    async def get(self, plan_id: str) -> dict:
        plan = await self.repo.find_by_id(plan_id)
        if not plan:
            raise NotFoundException("Subscription plan not found")
        return serialize_doc(plan)

    async def create(self, payload: SubscriptionPlanCreateRequest) -> dict:
        doc = payload.model_dump()
        doc["billing_cycle"] = payload.billing_cycle.value
        doc["slug"] = slugify(payload.name)
        if payload.category_quotas:
            doc["total_service_count"] = sum(payload.category_quotas.values())
        created = await self.repo.create(doc)
        return serialize_doc(created)

    async def update(self, plan_id: str, payload: SubscriptionPlanUpdateRequest) -> dict:
        data = {k: v for k, v in payload.model_dump(exclude_unset=True).items() if v is not None}
        if "name" in data:
            data["slug"] = slugify(data["name"])
        if "category_quotas" in data and data["category_quotas"]:
            data["total_service_count"] = sum(data["category_quotas"].values())
        updated = await self.repo.update_by_id(plan_id, data)
        if not updated:
            raise NotFoundException("Subscription plan not found")
        return serialize_doc(updated)

    async def delete(self, plan_id: str) -> None:
        if not await self.repo.soft_delete(plan_id):
            raise NotFoundException("Subscription plan not found")


class UserSubscriptionService:
    def __init__(self, db: AsyncIOMotorDatabase):
        self.repo = UserSubscriptionRepository(db)
        self.plan_repo = SubscriptionPlanRepository(db)
        self.vehicle_repo = VehicleRepository(db)
        self.vehicle_type_repo = VehicleTypeRepository(db)
        self.user_repo = UserRepository(db)

    async def list_my_subscriptions(self, customer_id: str) -> list[dict]:
        subs = await self.repo.list_for_customer(customer_id)
        return _with_effective_statuses(subs)

    async def list_for_customer(self, customer_id: str) -> list[dict]:
        """Manager/admin-facing equivalent of list_my_subscriptions for an
        arbitrary customer — used by the manager booking flow and the
        manager assign-a-plan action."""
        subs = await self.repo.list_for_customer(customer_id)
        return _with_effective_statuses(subs)

    async def subscribe(self, customer_id: str, payload: SubscribeRequest) -> dict:
        # Same phone-verification gate as a customer's first self-service
        # booking (BookingService.create_booking) — a subscription is a
        # real purchase too. assign() (manager/admin granting a plan) is
        # a completely separate method and is never gated by this.
        customer = await self.user_repo.find_by_id(customer_id)
        if not customer:
            raise NotFoundException("Customer not found")
        from app.services.auth_service import AuthService

        if not AuthService.phone_verification_fresh(customer):
            raise PhoneNotVerifiedException("Please verify your phone number with an OTP before purchasing a subscription.")
        return await self._create_subscription(customer_id, payload.plan_id, payload.auto_renew, payload.vehicle_type)

    async def validate_purchase(self, customer_id: str, payload: SubscribeRequest) -> None:
        """Dry-run of subscribe()'s validation — every check, no write.
        The payment flow runs this BEFORE creating a Razorpay order so a
        customer is never charged for a purchase the post-payment create
        would then refuse (unverified phone, inactive plan, invalid tier)."""
        customer = await self.user_repo.find_by_id(customer_id)
        if not customer:
            raise NotFoundException("Customer not found")
        from app.services.auth_service import AuthService

        if not AuthService.phone_verification_fresh(customer):
            raise PhoneNotVerifiedException("Please verify your phone number with an OTP before purchasing a subscription.")
        plan = await self.plan_repo.find_by_id(payload.plan_id)
        if not plan or not plan.get("is_active"):
            raise NotFoundException("Subscription plan not found or inactive")
        if payload.vehicle_type:
            vt_doc = await self.vehicle_type_repo.find_by_id(payload.vehicle_type)
            if not vt_doc or not vt_doc.get("is_active", True):
                raise BadRequestException("Pick a valid vehicle type for this plan.")
            plan_types = plan.get("vehicle_types") or []
            if plan_types and payload.vehicle_type not in plan_types:
                raise BadRequestException("This plan isn't sold for that vehicle type.")

    async def assign(self, payload: AssignSubscriptionRequest) -> dict:
        """Manager/admin granting a subscription to a customer directly —
        same validation as self-purchase, just with an explicit target
        customer instead of the caller themselves."""
        return await self._create_subscription(payload.customer_id, payload.plan_id, payload.auto_renew, payload.vehicle_type)

    async def _create_subscription(self, customer_id: str, plan_id: str, auto_renew: bool, vehicle_type: str | None = None) -> dict:
        """Subscribing no longer names a vehicle at all — a subscription is
        tied to the vehicle TYPE(S) its plan covers (plan.vehicle_types),
        not one specific vehicle. Which vehicle actually gets used is
        decided per-booking later (see plan_consumption), checked against
        whatever vehicles the customer owns at that time — including ones
        added after the subscription was purchased. This also means there's
        no more "one active subscription per vehicle" guard to enforce
        (that only made sense when a subscription pointed at one vehicle);
        a customer can hold multiple concurrent subscriptions freely."""
        plan = await self.plan_repo.find_by_id(plan_id)
        if not plan or not plan.get("is_active"):
            raise NotFoundException("Subscription plan not found or inactive")

        # The purchased TIER: which vehicle type this card is being paid
        # for. It sets the price charged now and caps redemption later
        # (tier_allows). Validated against the real vehicle-type catalog and
        # against the plan's own covered-type list.
        if vehicle_type:
            vt_doc = await self.vehicle_type_repo.find_by_id(vehicle_type)
            if not vt_doc or not vt_doc.get("is_active", True):
                raise BadRequestException("Pick a valid vehicle type for this plan.")
            plan_types = plan.get("vehicle_types") or []
            if plan_types and vehicle_type not in plan_types:
                raise BadRequestException("This plan isn't sold for that vehicle type.")

        now = now_ist()
        days = _CYCLE_DAYS.get(plan["billing_cycle"], 30)
        category_quotas = plan.get("category_quotas") or {}
        total = sum(category_quotas.values()) if category_quotas else plan["total_service_count"]
        doc = {
            "customer_id": customer_id,
            "plan_id": plan_id,
            "vehicle_type": vehicle_type,
            "purchased_price": resolve_plan_price(plan, vehicle_type) if vehicle_type else None,
            "status": SubscriptionStatus.ACTIVE.value,
            "total_service_count": total,
            "remaining_service_count": total,
            "total_by_category": dict(category_quotas),
            "remaining_by_category": dict(category_quotas),
            "start_date": now,
            "end_date": now + timedelta(days=days),
            "auto_renew": auto_renew,
        }
        created = await self.repo.create(doc)
        result = _with_effective_status(created)
        # Denormalized for callers that want to show/send the plan name
        # without a second lookup (e.g. the purchase-confirmation ticket
        # issued right after subscribe() — see UserSubscriptionController).
        result["plan_name"] = plan.get("name")
        return result

    async def get_plan(self, subscription_id: str) -> dict | None:
        """Returns the plan doc backing a subscription — used by booking
        creation to price a same-visit "swap to a different service" charge
        against exactly what the plan actually includes (see
        BookingService._subscription_discount)."""
        sub = await self.repo.find_by_id(subscription_id)
        if not sub:
            return None
        return await self.plan_repo.find_by_id(sub["plan_id"])

    async def plan_consumption(self, subscription_id: str, vehicle_id: str, services: list[dict], customer_id: str) -> dict:
        """Validates the subscription can actually cover this booking's
        services and computes exactly what WOULD be deducted, without
        writing anything yet. Falls back to the flat counter for older
        plans with no category quotas (category_quotas empty) so
        pre-Phase-3 subscriptions keep working. The write itself happens
        separately (commit_consumption), only after the booking this
        belongs to has been durably created — see BookingService.create_booking
        for why the split matters (a booking-creation failure between
        planning and committing must never leave a subscription silently
        decremented for a booking that doesn't exist).

        Vehicle eligibility is checked by TYPE here, not by a specific
        vehicle_id — the subscription itself no longer names one vehicle
        (see UserSubscriptionModel.vehicle_id's docstring). Vehicle
        ownership is already verified by create_booking before this is
        called, so it isn't re-checked here."""
        sub = await self._get_active_subscription(subscription_id)
        # OWNERSHIP — the single most important line in this method. The
        # subscription_id arrives from the client; without this check any
        # customer could pass someone else's id, get their booking waived,
        # and drain the victim's remaining visits (a real IDOR we shipped).
        # 404, not 403, so a guessed id doesn't confirm the sub exists.
        if sub.get("customer_id") != customer_id:
            raise NotFoundException("Subscription not found")
        plan = await self.plan_repo.find_by_id(sub["plan_id"])
        allowed_types = (plan or {}).get("vehicle_types") or []
        purchased_type = sub.get("vehicle_type")
        if allowed_types or purchased_type:
            vehicle = await self.vehicle_repo.find_by_id(vehicle_id)
            if not vehicle or (allowed_types and vehicle["vehicle_type"] not in allowed_types):
                raise BadRequestException("This subscription doesn't cover this vehicle's type and can't be used for this booking.")
            # Tier cap: a plan bought at hatchback price can't wash an SUV.
            # The other direction (bigger tier used on a smaller vehicle) is
            # allowed and still burns one full visit — the buyer's call.
            if plan and not tier_allows(plan, purchased_type, vehicle["vehicle_type"]):
                raise BadRequestException(
                    "Your plan was purchased for a smaller vehicle type — it works for that type and below. "
                    "Book this vehicle normally, or upgrade your plan."
                )
        # Add-ons riding on a plan visit (extra bikes, polish...) are PAID
        # extras — they never consume plan quota (and are never discounted,
        # see BookingService._subscription_discount). The one exception is a
        # service the admin explicitly listed in the plan's own
        # included_service_ids: that's part of the covered visit.
        included_ids = set((plan or {}).get("included_service_ids") or [])
        countable = [s for s in services if str(s.get("_id") or s.get("id") or "") in included_ids or not s.get("is_addon")]
        by_category = dict(sub.get("remaining_by_category") or {})
        if by_category:
            needed: dict[str, int] = {}
            for service in countable:
                cat = service["category_id"]
                needed[cat] = needed.get(cat, 0) + 1
            for cat, count in needed.items():
                if by_category.get(cat, 0) < count:
                    raise BadRequestException(
                        "Your plan doesn't have enough remaining services in this category for this booking."
                    )
            return {"by_category": needed}
        if sub["remaining_service_count"] < len(countable):
            raise BadRequestException("No remaining services left on this subscription")
        return {"flat_count": len(countable)}

    async def commit_consumption(self, subscription_id: str, consumption: dict) -> None:
        """Applies a consumption plan produced by plan_consumption. Booking
        creation calls this right after the booking document itself is
        successfully created — never before.

        Atomic via optimistic concurrency (guard the write on the document's
        own `updated_at` not having changed since we read it, retrying
        against a fresh read on conflict) — this is what actually closes the
        race two bookings both trying to spend the LAST remaining unit could
        otherwise hit (same race class already fixed for coupon usage this
        session): without it, both could read "1 remaining" before either
        commits, and both would succeed, taking the count negative."""
        for _ in range(5):
            sub = await self.repo.find_by_id(subscription_id)
            if not sub:
                return
            update_data = self._apply_delta(sub, consumption, sign=-1)
            if update_data.get("remaining_service_count", 0) < 0 or any(v < 0 for v in update_data.get("remaining_by_category", {}).values()):
                raise BadRequestException("This subscription's remaining services just ran out — someone else may have just booked with it.")
            new_remaining = update_data.get("remaining_service_count", sub.get("remaining_service_count", 0))
            if new_remaining <= 0:
                update_data["status"] = SubscriptionStatus.EXPIRED.value
            result = await self.repo.update_if(subscription_id, {"updated_at": sub["updated_at"]}, update_data)
            if result is not None:
                return
        raise BadRequestException("Couldn't update this subscription right now — please try again.")

    async def restore_consumption(self, subscription_id: str, consumption: dict) -> None:
        """Reverses commit_consumption — called when a subscription-paid
        booking is cancelled, so the credit it consumed isn't permanently
        lost for a service that was never actually performed. Re-activates
        the subscription if it had been auto-expired by this exact
        consumption. Clamped so a pre-upgrade booking's late cancellation
        can't credit past what the (possibly since-upgraded) plan currently
        allows. Best-effort: cancel_booking must still succeed even if this
        can't win its optimistic lock after retries — a booking cancellation
        should never fail because of a subscription-credit bookkeeping
        hiccup, so this silently gives up rather than raising."""
        for _ in range(5):
            sub = await self.repo.find_by_id(subscription_id)
            if not sub:
                return
            update_data = self._apply_delta(sub, consumption, sign=1)
            total_by_category = sub.get("total_by_category") or {}
            if "remaining_by_category" in update_data:
                clamped = {cat: min(val, total_by_category.get(cat, val)) for cat, val in update_data["remaining_by_category"].items()}
                update_data["remaining_by_category"] = clamped
                update_data["remaining_service_count"] = sum(clamped.values())
            else:
                cap = sub.get("total_service_count", update_data["remaining_service_count"])
                update_data["remaining_service_count"] = min(update_data["remaining_service_count"], cap)
            new_remaining = update_data.get("remaining_service_count", sub.get("remaining_service_count", 0))
            if sub["status"] == SubscriptionStatus.EXPIRED.value and new_remaining > 0:
                update_data["status"] = SubscriptionStatus.ACTIVE.value
            result = await self.repo.update_if(subscription_id, {"updated_at": sub["updated_at"]}, update_data)
            if result is not None:
                return

    @staticmethod
    def _apply_delta(sub: dict, consumption: dict, sign: int) -> dict:
        """sign=-1 to consume, sign=+1 to restore — same arithmetic either way."""
        if "by_category" in consumption:
            by_category = dict(sub.get("remaining_by_category") or {})
            for cat, count in consumption["by_category"].items():
                by_category[cat] = by_category.get(cat, 0) + sign * count
            return {"remaining_by_category": by_category, "remaining_service_count": sum(by_category.values())}
        flat_count = consumption.get("flat_count", 0)
        return {"remaining_service_count": sub.get("remaining_service_count", 0) + sign * flat_count}

    async def upgrade(self, customer_id: str, subscription_id: str, new_plan_id: str) -> dict:
        """Mid-cycle plan switch. Simplified proration: the billing cycle
        (end_date) doesn't reset, but the remaining quota resets to the NEW
        plan's full allowance for the rest of that cycle — no cash refund/credit
        math, since there's no payment gateway wired in yet to action a partial
        refund. Documented here so it's a known, deliberate simplification."""
        sub = await self.repo.find_by_id(subscription_id)
        if not sub or sub["customer_id"] != customer_id:
            raise NotFoundException("Subscription not found")
        if sub["status"] != SubscriptionStatus.ACTIVE.value:
            raise BadRequestException("Only an active subscription can be upgraded")

        current_plan = await self.plan_repo.find_by_id(sub["plan_id"])
        allowed_upgrades = (current_plan or {}).get("upgrade_to_plan_ids") or []
        if new_plan_id not in allowed_upgrades:
            raise ForbiddenException("This plan can't be upgraded to the one you picked — check what upgrades are available.")

        new_plan = await self.plan_repo.find_by_id(new_plan_id)
        if not new_plan or not new_plan.get("is_active"):
            raise NotFoundException("New plan not found or inactive")

        # No single vehicle to re-check against anymore — eligibility is
        # by type, verified again per-booking at plan_consumption time
        # whichever vehicle a given booking actually uses. Nothing to
        # validate here beyond the plan itself being active.

        category_quotas = new_plan.get("category_quotas") or {}
        total = sum(category_quotas.values()) if category_quotas else new_plan["total_service_count"]
        updated = await self.repo.update_by_id(
            subscription_id,
            {
                "plan_id": new_plan_id,
                "total_service_count": total,
                "remaining_service_count": total,
                "total_by_category": dict(category_quotas),
                "remaining_by_category": dict(category_quotas),
            },
        )
        return _with_effective_status(updated)

    async def cancel(self, customer_id: str, subscription_id: str) -> dict:
        sub = await self.repo.find_by_id(subscription_id)
        if not sub or sub["customer_id"] != customer_id:
            raise NotFoundException("Subscription not found")
        updated = await self.repo.update_by_id(subscription_id, {"status": SubscriptionStatus.CANCELLED.value})
        return _with_effective_status(updated)

    async def list_all_for_admin(self, page: int, page_size: int):
        items, total = await self.repo.list_all(page, page_size)
        return _with_effective_statuses(items), total

    async def center_overview(self, service_center_id: str, actor_role: str, actor_center_id: str | None) -> dict:
        """The manager's subscription dashboard: every subscription held by
        a customer this center has ever served (any booking dispatched
        here, not only plan redemptions — a plan-holder who hasn't
        redeemed yet is exactly who the manager wants to call), rolled up
        into KPIs (active / expiring within 14 days / expired) plus a
        per-plan breakdown, with one detail row per subscription."""
        from app.core.authz import ensure_own_center

        ensure_own_center(actor_role, actor_center_id, service_center_id)

        customer_ids = await self.repo.db.bookings.distinct(
            "customer_id", {"service_center_id": service_center_id, "is_deleted": {"$ne": True}}
        )
        if not customer_ids:
            return {"kpis": {"total": 0, "active": 0, "expiring_soon": 0, "expired": 0}, "plan_breakdown": [], "rows": []}

        subs = _with_effective_statuses(
            await self.repo.collection.find({"customer_id": {"$in": customer_ids}, "is_deleted": {"$ne": True}})
            .sort("created_at", -1)
            .to_list(length=2000)
        )

        plan_ids = {ObjectId(s["plan_id"]) for s in subs if s.get("plan_id") and ObjectId.is_valid(s["plan_id"])}
        plans = {
            str(p["_id"]): p
            for p in await self.plan_repo.collection.find({"_id": {"$in": list(plan_ids)}}).to_list(length=500)
        }
        holder_ids = {ObjectId(s["customer_id"]) for s in subs if ObjectId.is_valid(s["customer_id"])}
        users = {
            str(u["_id"]): u
            for u in await self.user_repo.collection.find(
                {"_id": {"$in": list(holder_ids)}}, {"full_name": 1, "phone": 1}
            ).to_list(length=2000)
        }
        type_names = {
            str(t["_id"]): t.get("name", "")
            for t in await self.vehicle_type_repo.collection.find({}, {"name": 1}).to_list(length=200)
        }

        now = now_ist()
        rows = []
        active = expiring_soon = expired = 0
        plan_counts: dict[str, int] = {}
        for s in subs:
            plan = plans.get(s.get("plan_id") or "")
            holder = users.get(s["customer_id"], {})
            end_date = s.get("end_date")
            days_left = None
            if end_date:
                # serialize_doc already stamped end_date as a tz-aware ISO
                # string (COMPUTED_INSTANT_KEYS) — parse it back; only a
                # still-naive value carries UTC semantics.
                end = datetime.fromisoformat(end_date) if isinstance(end_date, str) else end_date
                if end.tzinfo is None:
                    end = end.replace(tzinfo=timezone.utc)
                days_left = (end - now).days
            status = s.get("effective_status")
            if status == SubscriptionStatus.ACTIVE.value:
                active += 1
                plan_name = plan.get("name") if plan else "Unknown plan"
                plan_counts[plan_name] = plan_counts.get(plan_name, 0) + 1
                if days_left is not None and days_left <= 14:
                    expiring_soon += 1
            elif status == SubscriptionStatus.EXPIRED.value:
                expired += 1
            rows.append({
                "subscription_id": s["id"],
                "customer_id": s["customer_id"],
                "customer_name": holder.get("full_name", "Unknown"),
                "customer_phone": holder.get("phone"),
                "plan_id": s.get("plan_id"),
                "plan_name": plan.get("name") if plan else "Unknown plan",
                "status": status,
                "vehicle_type": s.get("vehicle_type"),
                "vehicle_type_name": type_names.get(s.get("vehicle_type") or ""),
                "purchased_price": s.get("purchased_price"),
                "remaining_service_count": s.get("remaining_service_count"),
                "total_service_count": s.get("total_service_count"),
                "start_date": s.get("start_date"),
                "end_date": s.get("end_date"),
                "days_left": days_left,
            })
        return {
            "kpis": {"total": len(rows), "active": active, "expiring_soon": expiring_soon, "expired": expired},
            "plan_breakdown": sorted(
                ({"plan_name": name, "active_count": count} for name, count in plan_counts.items()),
                key=lambda x: -x["active_count"],
            ),
            "rows": rows,
        }

    async def _get_active_subscription(self, subscription_id: str) -> dict:
        sub = await self.repo.find_by_id(subscription_id)
        if not sub:
            raise NotFoundException("Subscription not found")
        if sub["status"] != SubscriptionStatus.ACTIVE.value:
            raise BadRequestException("This subscription is not active")
        # end_date was computed as an absolute instant (now_ist() + timedelta), so
        # when Mongo hands it back naive it represents UTC, not IST wall-clock —
        # tag it as UTC here; only *user-entered* wall-clock values (like a
        # booking's scheduled_slot) get tagged as IST on read.
        if sub["end_date"].replace(tzinfo=timezone.utc) < now_ist():
            await self.repo.update_by_id(subscription_id, {"status": SubscriptionStatus.EXPIRED.value})
            raise BadRequestException("This subscription has expired")
        return sub
