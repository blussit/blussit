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
    # THE MENU the buyer picks ONE service from when they buy this pass
    # (founder model, 2026-09-11: "which car, which service" — Jet Wash,
    # Waterless, Star Wash; deliberately NOT Deep Cleaning). The chosen one
    # is stored on the subscription as `service_id` and is the only main
    # service that pass ever covers.
    #
    # Older plans that predate the pass model use this list the previous
    # way — "everything here is covered" — and keep working unchanged; the
    # difference is entirely whether the subscription carries a service_id.
    included_service_ids: list[str] = []
    # Pass pricing = the chosen service's price FOR THE CHOSEN VEHICLE'S TYPE
    # x total_service_count, less this discount. So a pass costs what those
    # washes would cost, minus the reason to buy a pass.
    plan_discount_percent: float = 0.0
    # ...unless admin has set the monthly price for a wash type OUTRIGHT.
    # service_id -> vehicle_type_id -> the whole monthly price. A number
    # here WINS over the formula above, so the team can price a pass at a
    # round, sellable figure ("₹999 for a hatchback jet-wash pass") instead
    # of whatever the multiplication happens to produce. Anything not listed
    # keeps falling back to the formula.
    service_pass_prices: dict[str, dict[str, float]] = {}
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
    # AUTHORITATIVE AGAIN (founder model, 2026-09-11): a pass belongs to ONE
    # car, named by registration at purchase, and one car carries one pass.
    # (This reverses an intermediate design where a subscription was scoped
    # to vehicle TYPES instead. Subscriptions written under that design have
    # vehicle_id None and still redeem by type — see plan_consumption, which
    # branches on whether this field is set.)
    vehicle_id: Optional[str] = None
    # The ONE main service this pass covers, chosen at purchase from the
    # plan's included_service_ids. None on pre-pass subscriptions.
    service_id: Optional[str] = None
    # The vehicle-type TIER this subscription was purchased at (VehicleType
    # id) — plans are priced per type (hatchback < sedan < SUV...), the buyer
    # picks the type they're paying for, and redemption is then allowed on
    # that type or any type the plan prices CHEAPER, never a costlier one
    # (see UserSubscriptionService.plan_consumption / tier_allows). Using it
    # on a cheaper type still burns one full visit — no credit for the gap.
    # None = purchased before tiers existed; plan.vehicle_types alone governs.
    # On a pass this is simply the named vehicle's type, snapshotted so
    # pricing history survives the customer editing the vehicle later.
    vehicle_type: Optional[str] = None
    # What was actually charged for that tier at purchase time — a snapshot,
    # immune to later plan-price edits.
    purchased_price: Optional[float] = None
    status: SubscriptionStatus = SubscriptionStatus.ACTIVE
    total_service_count: int = 0
    remaining_service_count: int = 0
    # Snapshotted from the plan at purchase (or upgrade) time — the customer's
    # actual entitlement doesn't drift if admin edits the plan mid-cycle.
    total_by_category: dict[str, int] = {}
    remaining_by_category: dict[str, int] = {}
    start_date: datetime
    end_date: datetime
    # Auto-pay (Razorpay Subscriptions): True once a mandate is active, so
    # the plan re-bills itself on end_date instead of silently lapsing.
    auto_renew: bool = False
    # The Razorpay subscription (mandate) id backing auto_renew — the handle
    # used to poll for renewal charges and to cancel the mandate. None for a
    # one-time purchase or a staff-granted plan.
    razorpay_subscription_id: Optional[str] = None
    # How many times auto-pay has re-billed and refreshed this card (0 = the
    # original cycle the customer paid for at checkout).
    renewal_count: int = 0
    last_renewed_at: Optional[datetime] = None
