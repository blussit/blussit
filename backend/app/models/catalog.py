from typing import Optional

from app.models.base import BusinessRecordBase


class CategoryModel(BusinessRecordBase):
    name: str
    slug: str
    description: Optional[str] = None
    icon: Optional[str] = None
    display_order: int = 0
    is_active: bool = True


class ServiceModel(BusinessRecordBase):
    category_id: str
    name: str
    slug: str
    description: Optional[str] = None
    # References to VehicleType docs (see app/models/vehicle_type.py) by id —
    # admin-managed, not a fixed enum, so plain strings rather than a Pydantic
    # Enum type. Empty list = every vehicle type accepted.
    vehicle_types: list[str] = []
    price: float
    discounted_price: Optional[float] = None
    # Per-vehicle-type overrides — a hatchback wash and a 7-seater XUV wash cost
    # genuinely different amounts, not a guessed multiplier. Keys are
    # VehicleType ids; any type not present here falls back to `price` /
    # `discounted_price` above.
    vehicle_type_prices: dict[str, float] = {}
    vehicle_type_discounted_prices: dict[str, float] = {}
    captain_fee: Optional[float] = None
    duration_minutes: int = 30
    image: Optional[str] = None
    is_active: bool = True
    is_featured: bool = False
    display_order: int = 0


class ComboOfferModel(BusinessRecordBase):
    """A bundle of services sold as one item at its own price — e.g. 'Exterior +
    Interior + Foam Wax' at ₹899, rather than the sum of three separate prices.
    Distinct from a discount rule: this is its own merchandisable, sellable thing."""
    name: str
    slug: str
    description: Optional[str] = None
    service_ids: list[str] = []
    vehicle_types: list[str] = []
    price: float
    discounted_price: Optional[float] = None
    vehicle_type_prices: dict[str, float] = {}
    vehicle_type_discounted_prices: dict[str, float] = {}
    image: Optional[str] = None
    is_active: bool = True
    is_featured: bool = False
    display_order: int = 0
