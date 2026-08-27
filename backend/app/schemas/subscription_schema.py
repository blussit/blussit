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
    included_service_ids: list[str] = []
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
    category_quotas: Optional[dict[str, int]] = None
    total_service_count: Optional[int] = None
    vehicle_types: Optional[list[str]] = None
    upgrade_to_plan_ids: Optional[list[str]] = None
    is_active: Optional[bool] = None
    is_popular: Optional[bool] = None
    display_order: Optional[int] = None


class SubscribeRequest(BaseModel):
    plan_id: str
    auto_renew: bool = False


class AssignSubscriptionRequest(BaseModel):
    """Manager/admin granting a subscription to a customer directly — same
    shape as SubscribeRequest plus an explicit target customer."""
    customer_id: str
    plan_id: str
    auto_renew: bool = False


class UpgradeSubscriptionRequest(BaseModel):
    new_plan_id: str
