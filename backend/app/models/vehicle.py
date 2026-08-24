from typing import Optional

from app.models.base import BusinessRecordBase


class VehicleModel(BusinessRecordBase):
    owner_id: str
    # References a VehicleType doc's id (see app/models/vehicle_type.py) — admin-
    # managed, not a fixed enum.
    vehicle_type: str
    brand: str
    model: str
    registration_number: str
    color: Optional[str] = None
    image: Optional[str] = None
    is_default: bool = False
