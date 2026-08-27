from datetime import datetime
from typing import Optional

from app.models.base import BusinessRecordBase
from app.models.enums import BillingCycle, SubscriptionStatus


class SubscriptionPlanModel(BusinessRecordBase):
    name: str
    slug: str
    description: Optional[str] = None
    billing_cycle: BillingCycle
    price: float
    discounted_price: Optional[float] = None
    # Per-vehicle-type overrides on top of the flat price/discounted_price
    # above — e.g. this same plan costs 399 for a hatchback, 449 for a
    # sedan, 499 for an SUV, admin-set, no fixed spread between types. Same
    # pattern as Service/ComboOffer.vehicle_type_prices. Empty = every
    # vehicle type pays the flat price.
    vehicle_type_prices: dict[str, float] = {}
    vehicle_type_discounted_prices: dict[str, float] = {}
    included_service_ids: list[str] = []
    # What the plan actually covers, per category — e.g. {"normal-clean": 4,
    # "deep-clean": 1, "foaming": 1}. This is what makes "4 normal washes + 1
    # deep clean" a real, enforced allowance instead of one flat counter that
    # can't tell a quick wash from a full detail.
    category_quotas: dict[str, int] = {}
    total_service_count: int = 1
    # VehicleType ids (see app/models/vehicle_type.py) this plan is sold for —
    # e.g. a plan restricted to ["sedan-id", "hatchback-id"] can't be bought
    # for, or consumed by, an SUV. Empty list = every vehicle type eligible
    # (the backward-compatible default for plans created before this existed).
    vehicle_types: list[str] = []
    # Explicit admin whitelist of which OTHER plan ids a subscription on THIS
    # plan may be upgraded to — see UserSubscriptionService.upgrade(). Empty =
    # no upgrade path exists from this plan until admin defines one; there is
    # deliberately no implicit "any plan" fallback.
    upgrade_to_plan_ids: list[str] = []
    is_active: bool = True
    is_popular: bool = False
    display_order: int = 0


class UserSubscriptionModel(BusinessRecordBase):
    customer_id: str
    plan_id: str
    # REVERSED from an earlier design: a subscription is no longer locked to
    # one specific vehicle — it's locked to the VEHICLE TYPE(S) its plan
    # covers (plan.vehicle_types). Any of the customer's owned vehicles of a
    # matching type can use it; which vehicle a given booking actually uses
    # is just a normal property of that booking (see
    # UserSubscriptionService.plan_consumption). Kept (not removed) purely
    # for old documents written under the earlier vehicle-locked design —
    # no code reads it as authoritative anymore.
    vehicle_id: Optional[str] = None
    status: SubscriptionStatus = SubscriptionStatus.ACTIVE
    total_service_count: int = 0
    remaining_service_count: int = 0
    # Snapshotted from the plan at purchase (or upgrade) time — the customer's
    # actual entitlement doesn't drift if admin edits the plan mid-cycle.
    total_by_category: dict[str, int] = {}
    remaining_by_category: dict[str, int] = {}
    start_date: datetime
    end_date: datetime
    auto_renew: bool = False
