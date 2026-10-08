from typing import Optional

from pydantic import BaseModel, Field, field_validator


class VehicleCreateRequest(BaseModel):
    vehicle_type: str  # a VehicleType id — see app/models/vehicle_type.py
    brand: str
    model: str
    registration_number: str = Field(min_length=3, max_length=20)

    @field_validator("registration_number")
    @classmethod
    def _valid_indian_plate(cls, v: str) -> str:
        from app.utils.vehicle_reg import validate_indian_registration

        plate = validate_indian_registration(v)
        if not plate:
            raise ValueError(
                "Enter a valid Indian registration number, e.g. MP09AB1234, DL1CXY9876 or 22BH1234AB"
            )
        return plate
    color: Optional[str] = None
    image: Optional[str] = None
    is_default: bool = False
    # Set true only after the customer has seen and confirmed the
    # "already registered with another account" warning (see
    # POST /vehicles/check-registration) — required to actually register a
    # plate that's already on one other account. Never trusted alone: the
    # backend re-counts at write time regardless of this flag.
    acknowledge_shared_registration: bool = False


class RegistrationCheckRequest(BaseModel):
    registration_number: str = Field(min_length=3, max_length=20)


class VehicleUpdateRequest(BaseModel):
    vehicle_type: Optional[str] = None
    brand: Optional[str] = None
    model: Optional[str] = None
    registration_number: Optional[str] = None

    @field_validator("registration_number")
    @classmethod
    def _valid_indian_plate(cls, v):
        if v is None:
            return v
        from app.utils.vehicle_reg import validate_indian_registration

        plate = validate_indian_registration(v)
        if not plate:
            raise ValueError(
                "Enter a valid Indian registration number, e.g. MP09AB1234, DL1CXY9876 or 22BH1234AB"
            )
        return plate
    color: Optional[str] = None
    image: Optional[str] = None
    is_default: Optional[bool] = None
    acknowledge_shared_registration: bool = False


# Same bounds as the booking flow's QuickAddress (VAL-1: these were
# unbounded — a 1.5 MB line1 was stored and shown on every staff screen).
class AddressCreateRequest(BaseModel):
    label: str = Field(default="Home", max_length=50)
    line1: str = Field(min_length=1, max_length=300)
    line2: Optional[str] = Field(default=None, max_length=300)
    landmark: Optional[str] = Field(default=None, max_length=200)
    city: str = Field(max_length=100)
    state: str = Field(max_length=100)
    pincode: str = Field(min_length=4, max_length=10)
    latitude: Optional[float] = Field(default=None, ge=-90, le=90)
    longitude: Optional[float] = Field(default=None, ge=-180, le=180)
    is_default: bool = False


class AddressUpdateRequest(BaseModel):
    label: Optional[str] = Field(default=None, max_length=50)
    line1: Optional[str] = Field(default=None, min_length=1, max_length=300)
    line2: Optional[str] = Field(default=None, max_length=300)
    landmark: Optional[str] = Field(default=None, max_length=200)
    city: Optional[str] = Field(default=None, max_length=100)
    state: Optional[str] = Field(default=None, max_length=100)
    pincode: Optional[str] = Field(default=None, min_length=4, max_length=10)
    latitude: Optional[float] = Field(default=None, ge=-90, le=90)
    longitude: Optional[float] = Field(default=None, ge=-180, le=180)
    is_default: Optional[bool] = None
