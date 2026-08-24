from typing import Optional

from app.models.base import BusinessRecordBase


class AddressModel(BusinessRecordBase):
    owner_id: str
    label: str = "Home"
    line1: str
    line2: Optional[str] = None
    landmark: Optional[str] = None
    city: str
    state: str
    pincode: str
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    is_default: bool = False
