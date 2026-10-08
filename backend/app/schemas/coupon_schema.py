from datetime import datetime, timezone
from typing import Literal, Optional

from pydantic import BaseModel, Field, model_validator

from app.models.enums import CouponType


def _aware(value: datetime) -> datetime:
    """Comparable whichever way the client sent it (with or without "Z")."""
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


class CouponCreateRequest(BaseModel):
    code: str = Field(min_length=3, max_length=20)
    description: Optional[str] = None
    coupon_type: CouponType
    value: float = Field(ge=0)
    min_order_value: float = 0
    max_discount_amount: Optional[float] = None
    usage_limit_per_user: int = 1
    total_usage_limit: Optional[int] = None
    valid_from: datetime
    valid_until: datetime
    offer_kind: Literal["standard", "free_addon_with_service"] = "standard"
    eligible_service_keywords: list[str] = Field(default_factory=list)
    free_addon_keywords: list[str] = Field(default_factory=list)
    # Missing before: this schema had no is_active field at all, so every
    # admin-created coupon (everything but the hand-seeded FREEBIKE launch
    # offer) was stored without the key CouponService._valid_coupon checks —
    # `coupon.get("is_active")` came back None (falsy), so a BRAND NEW
    # coupon always failed with "Invalid coupon code" until someone opened
    # it and flipped the (already-on-looking) toggle off and back on.
    is_active: bool = True

    @model_validator(mode="after")
    def _validate_offer_shape(self) -> "CouponCreateRequest":
        # The same rules CouponService.update enforces on an edit — a new
        # coupon used to slip past them (a 150% coupon was accepted).
        if self.offer_kind == "standard" and self.value <= 0:
            raise ValueError("Standard coupons need a discount value")
        if self.coupon_type == CouponType.PERCENTAGE and self.value > 100:
            raise ValueError("A percentage discount can't be more than 100%")
        if _aware(self.valid_until) <= _aware(self.valid_from):
            raise ValueError("The end date must be after the start date")
        if self.offer_kind == "free_addon_with_service" and (not self.eligible_service_keywords or not self.free_addon_keywords):
            raise ValueError("Free add-on offers need eligible service and free add-on keywords")
        return self


class CouponUpdateRequest(BaseModel):
    description: Optional[str] = None
    # Was missing: the admin form sends it on edit, pydantic dropped it and
    # only `value` landed — "20%" edited to "flat ₹100" became 100% off.
    coupon_type: Optional[CouponType] = None
    value: Optional[float] = Field(default=None, ge=0)
    min_order_value: Optional[float] = None
    max_discount_amount: Optional[float] = None
    usage_limit_per_user: Optional[int] = None
    total_usage_limit: Optional[int] = None
    valid_from: Optional[datetime] = None
    valid_until: Optional[datetime] = None
    is_active: Optional[bool] = None
    offer_kind: Optional[Literal["standard", "free_addon_with_service"]] = None
    eligible_service_keywords: Optional[list[str]] = None
    free_addon_keywords: Optional[list[str]] = None


class CouponValidateRequest(BaseModel):
    code: str
    order_value: float
