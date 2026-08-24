from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field

from app.models.enums import CouponType


class CouponCreateRequest(BaseModel):
    code: str = Field(min_length=3, max_length=20)
    description: Optional[str] = None
    coupon_type: CouponType
    value: float = Field(gt=0)
    min_order_value: float = 0
    max_discount_amount: Optional[float] = None
    usage_limit_per_user: int = 1
    total_usage_limit: Optional[int] = None
    valid_from: datetime
    valid_until: datetime


class CouponUpdateRequest(BaseModel):
    description: Optional[str] = None
    value: Optional[float] = None
    min_order_value: Optional[float] = None
    max_discount_amount: Optional[float] = None
    usage_limit_per_user: Optional[int] = None
    total_usage_limit: Optional[int] = None
    valid_from: Optional[datetime] = None
    valid_until: Optional[datetime] = None
    is_active: Optional[bool] = None


class CouponValidateRequest(BaseModel):
    code: str
    order_value: float
