from datetime import datetime
from typing import Optional

from pydantic import Field

from app.models.base import BusinessRecordBase
from app.models.enums import CouponType


class CouponModel(BusinessRecordBase):
    code: str
    description: Optional[str] = None
    coupon_type: CouponType
    value: float
    min_order_value: float = 0
    max_discount_amount: Optional[float] = None
    usage_limit_per_user: int = 1
    total_usage_limit: Optional[int] = None
    total_used: int = 0
    valid_from: datetime
    valid_until: datetime
    is_active: bool = True
    offer_kind: str = "standard"
    eligible_service_keywords: list[str] = Field(default_factory=list)
    free_addon_keywords: list[str] = Field(default_factory=list)
