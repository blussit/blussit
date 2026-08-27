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
    # Uppercased, spaces/hyphens stripped — computed at write time (see
    # VehicleService.create/_normalize_registration) so the same plate
    # can't dodge the "max 2 accounts" rule through formatting differences
    # ("MP09 XX 1234" vs "mp-09-xx-1234"). Indexed (not unique — up to 2
    # accounts are intentionally allowed) for an efficient count query.
    registration_number_normalized: Optional[str] = None
    color: Optional[str] = None
    image: Optional[str] = None
    is_default: bool = False
