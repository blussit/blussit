from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field, model_validator

from app.models.enums import PaymentMethod
from app.schemas.profile_schema import AddressCreateRequest, VehicleCreateRequest


class BookingCreateRequest(BaseModel):
    vehicle_id: str
    address_id: str
    service_ids: list[str] = Field(default_factory=list)
    combo_id: Optional[str] = None
    scheduled_date: datetime
    scheduled_slot: str
    payment_method: PaymentMethod = PaymentMethod.CASH
    coupon_code: Optional[str] = None
    subscription_id: Optional[str] = None
    customer_notes: Optional[str] = None
    alternate_contact_name: Optional[str] = Field(default=None, max_length=100)
    alternate_contact_phone: Optional[str] = Field(default=None, max_length=20)


class ManagerBookingCreateRequest(BaseModel):
    """A manager/admin creating a booking on behalf of a customer (new or
    existing) — see BookingService.create_booking_for_customer. Exactly one
    of vehicle_id/new_vehicle and exactly one of address_id/new_address must
    be provided."""
    customer_id: str
    vehicle_id: Optional[str] = None
    new_vehicle: Optional[VehicleCreateRequest] = None
    address_id: Optional[str] = None
    new_address: Optional[AddressCreateRequest] = None
    service_ids: list[str] = Field(default_factory=list)
    combo_id: Optional[str] = None
    scheduled_date: datetime
    scheduled_slot: str
    payment_method: PaymentMethod = PaymentMethod.CASH
    coupon_code: Optional[str] = None
    subscription_id: Optional[str] = None
    customer_notes: Optional[str] = None
    alternate_contact_name: Optional[str] = Field(default=None, max_length=100)
    alternate_contact_phone: Optional[str] = Field(default=None, max_length=20)

    @model_validator(mode="after")
    def _exactly_one_vehicle_and_address(self) -> "ManagerBookingCreateRequest":
        if bool(self.vehicle_id) == bool(self.new_vehicle):
            raise ValueError("Provide exactly one of vehicle_id or new_vehicle")
        if bool(self.address_id) == bool(self.new_address):
            raise ValueError("Provide exactly one of address_id or new_address")
        return self


class ReportRiskRequest(BaseModel):
    """Captain self-reporting they may not make their next booking on time
    because their current job is running long — see
    BookingService.report_risk."""
    note: Optional[str] = Field(default=None, max_length=300)


class BookingRescheduleRequest(BaseModel):
    scheduled_date: datetime
    scheduled_slot: str


class BookingCancelRequest(BaseModel):
    reason: str = Field(min_length=3, max_length=300)


class BookingAssignCaptainRequest(BaseModel):
    captain_id: str


class EquipmentUsedInput(BaseModel):
    inventory_item_id: str
    item_name: str
    quantity: float


class HeadingRequest(BaseModel):
    """Captain taps 'Heading to customer' — captures departure time + their
    current GPS location, and the equipment they're carrying from the store."""
    latitude: float
    longitude: float
    equipment_used: list[EquipmentUsedInput] = []


class PhotoCaptureRequest(BaseModel):
    """Used for both the before-service and after-service photo steps. The
    image must come from a live camera capture on the frontend (never a
    file picker) and always carries the device's GPS coordinates."""
    image_url: str
    latitude: float
    longitude: float


class CaptainCancelRequest(BaseModel):
    reason: str = Field(min_length=3, max_length=300)


class ReassignCaptainRequest(BaseModel):
    captain_id: str


class VerifyVehicleRequest(BaseModel):
    """Captain types the plate they see on arrival — a deliberate typed check,
    not a yes/no toggle, so a captain can't rubber-stamp past the wrong car."""
    registration_number: str = Field(min_length=3, max_length=20)


class ResolveIssueRequest(BaseModel):
    # Required, same as BookingCancelRequest.reason — a manager must state
    # why an issue is being dismissed, not just click it away silently.
    note: str = Field(min_length=3, max_length=300)
