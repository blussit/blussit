"""Custom multi-car plan (manager cart) — docs/FEATURE_PLAN_WALLET_EDITS_PLANS_2026-10-07.md §1.6.

The manager only ever sends WHAT is in the cart (cars, per-service counts)
and an optional whole-rupee discount; every price is computed on the server
(CustomPlanService) — a client total is never read."""
from typing import Annotated, Literal, Optional

from pydantic import BaseModel, Field, field_validator, model_validator

from app.utils.phone import validate_indian_mobile

_ObjectId = Annotated[str, Field(pattern=r"^[0-9a-fA-F]{24}$")]


class CustomPlanItem(BaseModel):
    """One service on one car and how many washes of it the period holds."""

    service_id: _ObjectId
    count: int = Field(ge=1, le=30)


class CustomPlanCar(BaseModel):
    """A saved car (`vehicle_id`, the customer's own) or a plate + car type
    (saved on the customer's account when the cart is created)."""

    vehicle_id: Optional[_ObjectId] = None
    registration_number: Optional[str] = Field(default=None, max_length=20)
    vehicle_type: Optional[_ObjectId] = None
    items: list[CustomPlanItem] = Field(min_length=1, max_length=8)

    @field_validator("registration_number")
    @classmethod
    def _plate(cls, v: Optional[str]) -> Optional[str]:
        v = " ".join((v or "").split()).upper()
        return v or None

    @model_validator(mode="after")
    def _car(self):
        if not self.vehicle_id and not self.vehicle_type:
            raise ValueError("Pick the car, or its car type.")
        ids = [i.service_id for i in self.items]
        if len(set(ids)) != len(ids):
            raise ValueError("List each service once per car, with its count.")
        return self


class _CustomPlanCartBody(BaseModel):
    """What preview and create share: the customer and the cars."""

    customer_id: Optional[_ObjectId] = None
    customer_phone: Optional[str] = None
    customer_name: Optional[str] = Field(default=None, max_length=80)
    cars: list[CustomPlanCar] = Field(min_length=1, max_length=10)
    discount_amount: int = Field(default=0, ge=0, le=1_000_000)

    @field_validator("customer_phone")
    @classmethod
    def _phone(cls, v: Optional[str]) -> Optional[str]:
        if not v:
            return None
        phone = validate_indian_mobile(v)
        if not phone:
            raise ValueError("Enter a valid 10-digit mobile number")
        return phone


class CustomPlanPreviewRequest(_CustomPlanCartBody):
    """Read-only price — the customer named exactly as for create
    (`customer_id`, or `customer_phone` + name; QA 2026-10-07). No customer
    is needed for plate + type cars; a saved car (`vehicle_id`) needs the
    customer resolved and known to the actor's center. A preview never
    creates an account.

    `renewal_of` (FINAL-POLISH 2026-10-08): the preview of a renewal — the
    PAID cart being renewed (what `/renew` will build), or a DRAFT renewal
    cart being revised. The cart's customer is used, and a car's own pass
    that the renewal renews doesn't block it (exactly the rule `/renew` and
    revise apply); any other live pass is still refused."""

    renewal_of: Optional[_ObjectId] = None


class CustomPlanCreateRequest(_CustomPlanCartBody):
    """A new cart for one customer: an existing account (`customer_id`) or a
    phone (+ name) — found or created like a manager plan sale."""

    # Admin only (a manager's cart is always their own center's).
    service_center_id: Optional[_ObjectId] = None
    note: Optional[str] = Field(default=None, max_length=300)

    @model_validator(mode="after")
    def _customer(self):
        if not self.customer_id and not self.customer_phone:
            raise ValueError("Pick the customer, or enter their phone number.")
        return self


class CustomPlanReviseRequest(BaseModel):
    """Replace the cart's cars / discount. `expected_revision` is the
    revision the manager is looking at — a cart changed meanwhile is refused
    (409) instead of overwritten."""

    expected_revision: int = Field(ge=1)
    cars: list[CustomPlanCar] = Field(min_length=1, max_length=10)
    discount_amount: int = Field(default=0, ge=0, le=1_000_000)
    note: Optional[str] = Field(default=None, max_length=300)


class CustomPlanLinkRequest(BaseModel):
    expected_revision: int = Field(ge=1)
    send_whatsapp: bool = True


class CustomPlanCashRequest(BaseModel):
    expected_revision: int = Field(ge=1)
    note: Optional[str] = Field(default=None, max_length=300)


class CustomPlanCancelRequest(BaseModel):
    reason: Optional[str] = Field(default=None, max_length=300)


class CustomPlanRenewRequest(BaseModel):
    """Renew a paid cart (staff): a NEW cart for the next 30 days.
    `cars` omitted = the old cart's cars that got a pass, with the same
    per-service counts; given = the edited list (same shape as create —
    cars may be dropped or added). Always priced at TODAY's catalogue.
    `discount_amount` omitted = no discount (capped like any cart)."""

    cars: Optional[list[CustomPlanCar]] = Field(default=None, min_length=1, max_length=10)
    discount_amount: Optional[int] = Field(default=None, ge=0, le=1_000_000)
    note: Optional[str] = Field(default=None, max_length=300)


class CustomPlanRefundRequest(BaseModel):
    """Refund one car of a paid cart to the customer's wallet. `amount`
    omitted = the most refundable (the car's `refundable_amount`); a LOWER
    whole-rupee amount is allowed, a higher one is refused."""

    amount: Optional[int] = Field(default=None, ge=1, le=1_000_000)
    reason: str = Field(min_length=2, max_length=300)

    @field_validator("reason")
    @classmethod
    def _reason(cls, v: str) -> str:
        v = " ".join((v or "").split())
        if len(v) < 2:
            raise ValueError("Say why this car is refunded.")
        return v


CustomPlanStatus = Literal["draft", "awaiting_payment", "activating", "active", "needs_review", "refunded", "cancelled"]
