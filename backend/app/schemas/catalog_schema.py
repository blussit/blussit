from typing import Optional

from pydantic import BaseModel, Field

class CategoryCreateRequest(BaseModel):
    name: str
    description: Optional[str] = None
    icon: Optional[str] = None
    display_order: int = 0
    is_active: bool = True


class CategoryUpdateRequest(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    icon: Optional[str] = None
    display_order: Optional[int] = None
    is_active: Optional[bool] = None


class ServiceCreateRequest(BaseModel):
    category_id: str
    name: str
    description: Optional[str] = None
    vehicle_types: list[str] = Field(default_factory=list)
    price: float = Field(gt=0)
    discounted_price: Optional[float] = Field(default=None, ge=0)
    vehicle_type_prices: dict[str, float] = Field(default_factory=dict, description="Per-vehicle-type price overrides; falls back to `price` for any type not listed")
    vehicle_type_discounted_prices: dict[str, float] = Field(default_factory=dict, description="Per-vehicle-type first-time price overrides; falls back to `discounted_price`")
    original_price: Optional[float] = Field(default=None, ge=0, description="Struck-through 'actual' price shown on the website; display only, never charged")
    vehicle_type_original_prices: dict[str, float] = Field(default_factory=dict, description="Per-vehicle-type original-price overrides; falls back to `original_price`")
    is_addon: bool = Field(default=False, description="Optional extra sold only on top of a main service; hidden from landing cards")
    is_waterless: bool = Field(default=False, description="Waterless wash — needs shade; no water/power from the customer.")
    variant_group: Optional[str] = Field(default=None, max_length=60, description="Services sharing a group are variants of one product (e.g. Bike Wash 1–4 bikes)")
    variant_label: Optional[str] = Field(default=None, max_length=40, description="Short label for this variant, e.g. '2 bikes'")
    captain_fee: Optional[float] = Field(default=None, ge=0, description="Flat ₹ paid to captain for this service; falls back to admin default if unset")
    duration_minutes: int = Field(default=30, gt=0)
    image: Optional[str] = None
    is_active: bool = True
    is_featured: bool = False
    display_order: int = 0


class ServiceUpdateRequest(BaseModel):
    category_id: Optional[str] = None
    name: Optional[str] = None
    description: Optional[str] = None
    vehicle_types: Optional[list[str]] = None
    price: Optional[float] = None
    discounted_price: Optional[float] = None
    vehicle_type_prices: Optional[dict[str, float]] = None
    vehicle_type_discounted_prices: Optional[dict[str, float]] = None
    original_price: Optional[float] = Field(default=None, ge=0)
    vehicle_type_original_prices: Optional[dict[str, float]] = None
    is_addon: Optional[bool] = None
    is_waterless: Optional[bool] = None
    variant_group: Optional[str] = Field(default=None, max_length=60)
    variant_label: Optional[str] = Field(default=None, max_length=40)
    captain_fee: Optional[float] = None
    duration_minutes: Optional[int] = None
    image: Optional[str] = None
    is_active: Optional[bool] = None
    is_featured: Optional[bool] = None
    display_order: Optional[int] = None


class ComboOfferCreateRequest(BaseModel):
    name: str
    description: Optional[str] = None
    service_ids: list[str] = Field(min_length=2, description="A combo bundles 2+ services sold together at one price")
    vehicle_types: list[str] = Field(default_factory=list)
    price: float = Field(gt=0)
    discounted_price: Optional[float] = Field(default=None, ge=0)
    vehicle_type_prices: dict[str, float] = Field(default_factory=dict)
    vehicle_type_discounted_prices: dict[str, float] = Field(default_factory=dict)
    image: Optional[str] = None
    is_active: bool = True
    is_featured: bool = False
    display_order: int = 0


class ComboOfferUpdateRequest(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    service_ids: Optional[list[str]] = None
    vehicle_types: Optional[list[str]] = None
    price: Optional[float] = None
    discounted_price: Optional[float] = None
    vehicle_type_prices: Optional[dict[str, float]] = None
    vehicle_type_discounted_prices: Optional[dict[str, float]] = None
    image: Optional[str] = None
    is_active: Optional[bool] = None
    is_featured: Optional[bool] = None
    display_order: Optional[int] = None
