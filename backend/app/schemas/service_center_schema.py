from typing import Optional

from pydantic import BaseModel


class ServiceCenterLocationSchema(BaseModel):
    address: str
    city: str
    state: str
    pincode: str
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    service_pincodes: list[str] = []
    radius_km: float = 6.0


class ServiceCenterCreateRequest(BaseModel):
    name: str
    location: ServiceCenterLocationSchema
    manager_id: Optional[str] = None
    contact_phone: Optional[str] = None
    contact_email: Optional[str] = None
    working_hours_start: str = "08:00"
    working_hours_end: str = "20:00"


class ServiceCenterUpdateRequest(BaseModel):
    name: Optional[str] = None
    location: Optional[ServiceCenterLocationSchema] = None
    manager_id: Optional[str] = None
    contact_phone: Optional[str] = None
    contact_email: Optional[str] = None
    working_hours_start: Optional[str] = None
    working_hours_end: Optional[str] = None
    is_active: Optional[bool] = None
