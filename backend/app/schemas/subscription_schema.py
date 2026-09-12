from typing import Optional

from pydantic import BaseModel, Field

from app.models.enums import BillingCycle


class SubscriptionPlanCreateRequest(BaseModel):
    name: str
    description: Optional[str] = None
    billing_cycle: BillingCycle
    price: float = Field(gt=0)
    discounted_price: Optional[float] = None
    vehicle_type_prices: dict[str, float] = Field(default_factory=dict, description="vehicle_type_id -> plan price for that type, overriding the flat price")
    vehicle_type_discounted_prices: dict[str, float] = Field(default_factory=dict)
    included_service_ids: list[str] = Field(
        default_factory=list,
        description="The services a buyer may choose ONE of for this pass (Deep Cleaning is deliberately not offered).",
    )
    plan_discount_percent: float = Field(default=0.0, ge=0, le=90, description="Saving vs paying for those washes one by one.")
    service_pass_prices: dict[str, dict[str, float]] = Field(
        default_factory=dict,
        description="service_id -> vehicle_type_id -> flat monthly pass price. Overrides the computed price.",
    )
    category_quotas: dict[str, int] = Field(default_factory=dict, description="category_id -> quota count, e.g. {'normal-clean': 4, 'deep-clean': 1}")
    total_service_count: int = Field(default=1, gt=0)
    vehicle_types: list[str] = []
    upgrade_to_plan_ids: list[str] = []
    is_active: bool = True
    is_popular: bool = False
    display_order: int = 0


class SubscriptionPlanUpdateRequest(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    billing_cycle: Optional[BillingCycle] = None
    price: Optional[float] = None
    discounted_price: Optional[float] = None
    vehicle_type_prices: Optional[dict[str, float]] = None
    vehicle_type_discounted_prices: Optional[dict[str, float]] = None
    included_service_ids: Optional[list[str]] = None
    plan_discount_percent: Optional[float] = Field(default=None, ge=0, le=90)
    service_pass_prices: Optional[dict[str, dict[str, float]]] = None
    category_quotas: Optional[dict[str, int]] = None
    total_service_count: Optional[int] = None
    vehicle_types: Optional[list[str]] = None
    upgrade_to_plan_ids: Optional[list[str]] = None
    is_active: Optional[bool] = None
    is_popular: Optional[bool] = None
    display_order: Optional[int] = None


class SubscribeRequest(BaseModel):
    """Buying a monthly pass answers exactly two questions (founder model):
    WHICH CAR and WHICH SERVICE. Everything else — the price, the vehicle
    type, how many washes — is derived server-side from those two."""

    plan_id: str
    # The car this pass belongs to. One car carries one pass.
    vehicle_id: Optional[str] = None
    # The one main service the pass covers, from the plan's own menu.
    service_id: Optional[str] = None
    # LEGACY: the vehicle-TYPE tier, for subscriptions bought before passes
    # named a specific car. Ignored when vehicle_id is given (the car's own
    # type is used instead) — never trust a client-sent type over the
    # vehicle's real one.
    vehicle_type: Optional[str] = None
    auto_renew: bool = False


class AssignSubscriptionRequest(BaseModel):
    """Manager/admin granting a subscription to a customer directly — same
    shape as SubscribeRequest plus an explicit target customer."""
    customer_id: str
    plan_id: str
    vehicle_id: Optional[str] = None
    service_id: Optional[str] = None
    vehicle_type: Optional[str] = None
    auto_renew: bool = False


class PassQuoteRequest(BaseModel):
    """What a pass would cost, before committing to it — the purchase sheet
    reprices live as the buyer switches car or service, and the answer comes
    from the same function that charges them."""

    plan_id: str
    vehicle_id: str
    service_id: str


class PlanEnquiryRequest(BaseModel):
    """"None of these fit us" — a fleet/custom request the team follows up
    on by hand (founder ask: how many cars, which services, how often, when)."""

    name: str = Field(min_length=2, max_length=120)
    phone: str = Field(min_length=10, max_length=15)
    vehicle_count: int = Field(ge=1, le=500)
    services_wanted: str = Field(min_length=2, max_length=500)
    washes_per_month: Optional[int] = Field(default=None, ge=1, le=200)
    preferred_time: Optional[str] = Field(default=None, max_length=120)
    notes: Optional[str] = Field(default=None, max_length=1000)


class UpgradeSubscriptionRequest(BaseModel):
    new_plan_id: str


class AutoPayRequest(BaseModel):
    """Customer-facing auto-pay switch — see
    UserSubscriptionService.set_auto_pay for why only `false` does work."""

    enabled: bool
