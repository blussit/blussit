from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator, model_validator

from app.models.enums import CouponType
from app.schemas.coupon_rules_schema import RulesCreate, RulesUpdate
from app.utils.timezone import IST, now_ist


def _ist(value: datetime | None) -> datetime | None:
    """A time typed without a zone is IST (that is what the admin typed)."""
    if value is None:
        return None
    return value.replace(tzinfo=IST) if value.tzinfo is None else value


def _keywords(values: list[str] | None) -> list[str]:
    return [v.strip().lower() for v in (values or []) if v and v.strip()]


class _Benefit(BaseModel):
    """The discount every code in a bundle gives — the same fields a regular
    coupon has, so a bundle code behaves exactly like one."""

    description: Optional[str] = Field(default=None, max_length=300)
    offer_kind: Literal["standard", "free_addon_with_service"] = "standard"
    coupon_type: CouponType = CouponType.FLAT
    value: float = Field(default=0, ge=0, le=1_000_000)
    min_order_value: float = Field(default=0, ge=0, le=1_000_000)
    max_discount_amount: Optional[float] = Field(default=None, ge=0, le=1_000_000)
    eligible_service_keywords: list[str] = Field(default_factory=list, max_length=30)
    free_addon_keywords: list[str] = Field(default_factory=list, max_length=30)


def _check_benefit(kind: str, ctype, value: float, eligible: list[str], free: list[str]) -> None:
    if kind == "standard":
        if value <= 0:
            raise ValueError("Standard coupons need a discount value")
        if ctype == CouponType.PERCENTAGE and value > 100:
            raise ValueError("A percentage discount can't be more than 100%")
    elif not eligible or not free:
        raise ValueError("Free add-on offers need eligible service and free add-on keywords")


class BundleCreateRequest(_Benefit, RulesCreate):
    name: str = Field(min_length=2, max_length=80)
    # Whole-bundle expiry; null = no expiry. A zone-less time is IST.
    valid_until: Optional[datetime] = None
    uses_per_coupon: int = Field(default=1, ge=1, le=100_000)
    # How many times ONE customer may use the same code (never more than
    # uses_per_coupon in effect).
    usage_limit_per_user: int = Field(default=1, ge=1, le=100_000)
    # The codes the admin ticked in the preview (re-validated server side).
    codes: list[str] = Field(min_length=1)

    @field_validator("name")
    @classmethod
    def _name(cls, v: str) -> str:
        v = " ".join(v.split())
        if len(v) < 2:
            raise ValueError("Give the bundle a name")
        return v

    @field_validator("valid_until")
    @classmethod
    def _expiry(cls, v: datetime | None) -> datetime | None:
        v = _ist(v)
        if v is not None and v <= now_ist():
            raise ValueError("The expiry must be in the future")
        return v

    @model_validator(mode="after")
    def _shape(self) -> "BundleCreateRequest":
        self.eligible_service_keywords = _keywords(self.eligible_service_keywords)
        self.free_addon_keywords = _keywords(self.free_addon_keywords)
        _check_benefit(self.offer_kind, self.coupon_type, self.value, self.eligible_service_keywords, self.free_addon_keywords)
        return self


class BundleUpdateRequest(RulesUpdate):
    """Every field optional; a field that is sent (even null, where null
    means something) is applied to the whole bundle in one write."""

    name: Optional[str] = Field(default=None, min_length=2, max_length=80)
    description: Optional[str] = Field(default=None, max_length=300)
    offer_kind: Optional[Literal["standard", "free_addon_with_service"]] = None
    coupon_type: Optional[CouponType] = None
    value: Optional[float] = Field(default=None, ge=0, le=1_000_000)
    min_order_value: Optional[float] = Field(default=None, ge=0, le=1_000_000)
    max_discount_amount: Optional[float] = Field(default=None, ge=0, le=1_000_000)
    eligible_service_keywords: Optional[list[str]] = Field(default=None, max_length=30)
    free_addon_keywords: Optional[list[str]] = Field(default=None, max_length=30)
    valid_until: Optional[datetime] = None  # null = no expiry
    uses_per_coupon: Optional[int] = Field(default=None, ge=1, le=100_000)
    usage_limit_per_user: Optional[int] = Field(default=None, ge=1, le=100_000)
    is_active: Optional[bool] = None

    @field_validator("name")
    @classmethod
    def _name(cls, v: str | None) -> str | None:
        return " ".join(v.split()) if v else v

    @field_validator("valid_until")
    @classmethod
    def _expiry(cls, v: datetime | None) -> datetime | None:
        return _ist(v)


class BundleAddCodesRequest(BaseModel):
    codes: list[str] = Field(min_length=1)


class CodeActiveRequest(BaseModel):
    is_active: bool

