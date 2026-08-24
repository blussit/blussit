from datetime import timedelta, timezone

from motor.motor_asyncio import AsyncIOMotorDatabase

from app.core.exceptions import BadRequestException, ForbiddenException, NotFoundException
from app.models.enums import BillingCycle, SubscriptionStatus
from app.repositories.subscription_repository import SubscriptionPlanRepository, UserSubscriptionRepository
from app.repositories.vehicle_repository import VehicleRepository
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
        return await self._create_subscription(customer_id, payload.plan_id, payload.vehicle_id, payload.auto_renew)

    async def assign(self, payload: AssignSubscriptionRequest) -> dict:
        """Manager/admin granting a subscription to a customer directly —
        same validation as self-purchase, just with an explicit target
        customer instead of the caller themselves."""
        return await self._create_subscription(payload.customer_id, payload.plan_id, payload.vehicle_id, payload.auto_renew)

    async def _create_subscription(self, customer_id: str, plan_id: str, vehicle_id: str, auto_renew: bool) -> dict:
        plan = await self.plan_repo.find_by_id(plan_id)
        if not plan or not plan.get("is_active"):
            raise NotFoundException("Subscription plan not found or inactive")

        vehicle = await self.vehicle_repo.find_by_id(vehicle_id)
        if not vehicle or vehicle["owner_id"] != customer_id:
            raise NotFoundException("Vehicle not found")

        # Empty plan.vehicle_types = every type eligible (backward-compatible
        # default for plans that predate this restriction).
        allowed_types = plan.get("vehicle_types") or []
        if allowed_types and vehicle["vehicle_type"] not in allowed_types:
            raise BadRequestException("This plan doesn't cover this vehicle's type — pick a different plan or vehicle.")

        # One active (and not-yet-expired) subscription per vehicle at a
        # time — a plain read-then-write check, not a hard atomic guard;
        # subscription purchases aren't a realistic concurrent-race surface
        # the way booking assignment or coupon redemption are.
        existing = await self.repo.find_all_no_paginate({"vehicle_id": vehicle_id, "status": SubscriptionStatus.ACTIVE.value})
        now = now_ist()
        still_active = [s for s in existing if s["end_date"].replace(tzinfo=timezone.utc) >= now]
        if still_active:
            blocking_plan = await self.plan_repo.find_by_id(still_active[0]["plan_id"])
            plan_name = blocking_plan["name"] if blocking_plan else "another plan"
            raise BadRequestException(f"This vehicle already has an active subscription ({plan_name}) — cancel or wait for it to expire first.")

        days = _CYCLE_DAYS.get(plan["billing_cycle"], 30)
        category_quotas = plan.get("category_quotas") or {}
        total = sum(category_quotas.values()) if category_quotas else plan["total_service_count"]
        doc = {
            "customer_id": customer_id,
            "plan_id": plan_id,
            "vehicle_id": vehicle_id,
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
        return _with_effective_status(created)

    async def get_plan(self, subscription_id: str) -> dict | None:
        """Returns the plan doc backing a subscription — used by booking
        creation to price a same-visit "swap to a different service" charge
        against exactly what the plan actually includes (see
        BookingService._subscription_discount)."""
        sub = await self.repo.find_by_id(subscription_id)
        if not sub:
            return None
        return await self.plan_repo.find_by_id(sub["plan_id"])

    async def plan_consumption(self, subscription_id: str, vehicle_id: str, services: list[dict]) -> dict:
        """Validates the subscription can actually cover this booking's
        services and computes exactly what WOULD be deducted, without
        writing anything yet. Falls back to the flat counter for older
        plans with no category quotas (category_quotas empty) so
        pre-Phase-3 subscriptions keep working. The write itself happens
        separately (commit_consumption), only after the booking this
        belongs to has been durably created — see BookingService.create_booking
        for why the split matters (a booking-creation failure between
        planning and committing must never leave a subscription silently
        decremented for a booking that doesn't exist)."""
        sub = await self._get_active_subscription(subscription_id)
        if sub["vehicle_id"] != vehicle_id:
            raise BadRequestException("This subscription is linked to a different vehicle and can't be used for this booking.")
        by_category = dict(sub.get("remaining_by_category") or {})
        if by_category:
            needed: dict[str, int] = {}
            for service in services:
                cat = service["category_id"]
                needed[cat] = needed.get(cat, 0) + 1
            for cat, count in needed.items():
                if by_category.get(cat, 0) < count:
                    raise BadRequestException(
                        "Your plan doesn't have enough remaining services in this category for this booking."
                    )
            return {"by_category": needed}
        if sub["remaining_service_count"] < len(services):
            raise BadRequestException("No remaining services left on this subscription")
        return {"flat_count": len(services)}

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

        # The vehicle doesn't change on upgrade — the new plan must still
        # actually cover it.
        vehicle = await self.vehicle_repo.find_by_id(sub["vehicle_id"])
        new_allowed_types = new_plan.get("vehicle_types") or []
        if vehicle and new_allowed_types and vehicle["vehicle_type"] not in new_allowed_types:
            raise BadRequestException("The new plan doesn't cover this subscription's vehicle type.")

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

    async def has_active_for_vehicle(self, vehicle_id: str) -> bool:
        """Used by VehicleService.delete to block removing a vehicle an
        active, not-yet-expired subscription still depends on."""
        existing = await self.repo.find_all_no_paginate({"vehicle_id": vehicle_id, "status": SubscriptionStatus.ACTIVE.value})
        now = now_ist()
        return any(s["end_date"].replace(tzinfo=timezone.utc) >= now for s in existing)

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
