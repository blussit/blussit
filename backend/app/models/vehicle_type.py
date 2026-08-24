from app.models.base import BusinessRecordBase


class VehicleTypeModel(BusinessRecordBase):
    """Admin-managed vehicle body types (Hatchback, Sedan, SUV, XUV 5-Seater, ...) —
    referenced by id/slug from Vehicle.vehicle_type, Service/ComboOffer's per-type
    pricing dicts, and SubscriptionPlan.vehicle_types. Deliberately a real collection,
    not a fixed enum, so admins can add/rename/reorder types without a code change."""

    name: str
    slug: str
    display_order: int = 0
    is_active: bool = True
