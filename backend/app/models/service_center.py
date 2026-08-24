from typing import Optional

from pydantic import BaseModel

from app.models.base import BusinessRecordBase


class ServiceCenterLocation(BaseModel):
    address: str
    city: str
    state: str
    pincode: str
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    service_pincodes: list[str] = []
    radius_km: float = 6.0


class ServiceCenterModel(BusinessRecordBase):
    name: str
    code: str
    location: ServiceCenterLocation
    manager_id: Optional[str] = None
    contact_phone: Optional[str] = None
    contact_email: Optional[str] = None
    working_hours_start: str = "08:00"
    working_hours_end: str = "20:00"
    is_active: bool = True
