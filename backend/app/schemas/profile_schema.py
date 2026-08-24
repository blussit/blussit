from typing import Optional

from pydantic import BaseModel, Field


class VehicleCreateRequest(BaseModel):
    vehicle_type: str  # a VehicleType id — see app/models/vehicle_type.py
    brand: str
    model: str
    registration_number: str = Field(min_length=3, max_length=20)
    color: Optional[str] = None
    image: Optional[str] = None
    is_default: bool = False


class VehicleUpdateRequest(BaseModel):
    vehicle_type: Optional[str] = None
    brand: Optional[str] = None
    model: Optional[str] = None
    registration_number: Optional[str] = None
    color: Optional[str] = None
    image: Optional[str] = None
    is_default: Optional[bool] = None


class AddressCreateRequest(BaseModel):
    label: str = "Home"
    line1: str
    line2: Optional[str] = None
    landmark: Optional[str] = None
    city: str
    state: str
    pincode: str = Field(min_length=4, max_length=10)
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    is_default: bool = False


class AddressUpdateRequest(BaseModel):
    label: Optional[str] = None
    line1: Optional[str] = None
    line2: Optional[str] = None
    landmark: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    pincode: Optional[str] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    is_default: Optional[bool] = None
