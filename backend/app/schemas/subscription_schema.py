from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator, model_validator

from app.models.enums import BillingCycle
from app.utils.phone import validate_indian_mobile


def _canonical_phone(v: str) -> str:
    phone = validate_indian_mobile(v)
    if not phone:
        raise ValueError("Enter a valid 10-digit mobile number")
    return phone


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
    # Same rule as ManagerSubscriptionOfferRequest.service_center_id — only
    # read for an admin, who has no center of their own.
    service_center_id: Optional[str] = None


class PassQuoteRequest(BaseModel):
    """What a pass would cost, before committing to it — the purchase sheet
    reprices live as the buyer switches car or service, and the answer comes
    from the same function that charges them."""

    plan_id: str
    # A saved car (older flow) OR a vehicle type (2026-09 model) — one of them.
    vehicle_id: Optional[str] = None
    vehicle_type: Optional[str] = None
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


class ManagerSubscriptionOfferRequest(BaseModel):
    """A manager (or admin) selling a plan to a customer over the phone/at
    the door: name + phone find-or-create the customer (same silent model
    as a quick booking), plan + vehicle type + service price it exactly
    like the customer's own purchase sheet would.

    Three ways it can end:
      - recurring=False, payment_method="link": a Razorpay payment LINK for
        one cycle, optionally discounted, shared on WhatsApp; the plan
        activates the moment it's paid (never before).
      - recurring=True: a Razorpay auto-pay MANDATE at the plan's full,
        undiscounted rate (no discount_amount, no coupon_code — enforced
        below); its hosted authorisation link is shared on WhatsApp, and
        the plan activates once Razorpay reports the first charge captured.
      - recurring=False, payment_method="cash": the manager already has the
        money in hand — the plan activates immediately, same as the
        existing free `assign` grant, but with the price/discount/collector
        recorded for the books.
    """

    customer_name: str = Field(min_length=2, max_length=100)
    customer_phone: str = Field(min_length=10, max_length=20)
    plan_id: str
    vehicle_type: str
    service_id: str
    recurring: bool = False
    payment_method: Literal["link", "cash"] = "link"
    # Rupees off the plan's price — one-time purchases only. Mutually
    # exclusive with coupon_code (pick one way to discount, not both).
    discount_amount: float = Field(default=0, ge=0, le=100000)
    coupon_code: Optional[str] = Field(default=None, max_length=40)
    # True = the customer hears about the link/mandate on WhatsApp. Off
    # still creates it — the manager can read/copy the link over a call.
    send_whatsapp: bool = True
    # A manager selling a plan is always attributed to THEIR OWN center
    # (current_user.service_center_id) — this is ignored for them. It only
    # matters for an admin, who has no single center of their own: the
    # controller requires one or the other to be present, so a plan is
    # never granted with nowhere for a manager's Subscriptions page to find
    # it (see UserSubscriptionService.center_overview).
    service_center_id: Optional[str] = None

    _phone = field_validator("customer_phone")(_canonical_phone)

    @field_validator("customer_name")
    @classmethod
    def _name(cls, v: str) -> str:
        v = " ".join(v.split())
        if len(v) < 2:
            raise ValueError("Enter the customer's name")
        return v

    @field_validator("coupon_code")
    @classmethod
    def _coupon(cls, v: Optional[str]) -> Optional[str]:
        v = (v or "").strip().upper()
        return v or None

    @model_validator(mode="after")
    def _consistent(self) -> "ManagerSubscriptionOfferRequest":
        if self.recurring:
            if self.discount_amount or self.coupon_code:
                raise ValueError("Auto-pay plans are sold at the full price — remove the discount or coupon, or turn recurring off.")
            if self.payment_method != "link":
                raise ValueError("Auto-pay is set up through a payment link — cash can't start a recurring mandate.")
        if self.discount_amount and self.coupon_code:
            raise ValueError("Use either a discount amount or a coupon code, not both.")
        return self


class ManagerSubscriptionPreviewRequest(BaseModel):
    """Read-only quote for the manager's offer form — same inputs as
    ManagerSubscriptionOfferRequest, no customer required yet (a phone the
    manager hasn't finished typing shouldn't error the price preview)."""

    plan_id: str
    vehicle_type: str
    service_id: str
    customer_phone: Optional[str] = None
    recurring: bool = False
    discount_amount: float = Field(default=0, ge=0, le=100000)
    coupon_code: Optional[str] = Field(default=None, max_length=40)

    @field_validator("coupon_code")
    @classmethod
    def _coupon(cls, v: Optional[str]) -> Optional[str]:
        v = (v or "").strip().upper()
        return v or None
