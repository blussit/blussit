from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field, field_validator, model_validator

from app.models.enums import BookingPriority, PaymentMethod
from app.schemas.profile_schema import AddressCreateRequest, VehicleCreateRequest
from app.utils.phone import validate_indian_mobile


def _validate_alt_contact_phone(v: Optional[str]) -> Optional[str]:
    """Same canonical 10-digit Indian-mobile rule as every other phone
    field — the secondary/alternate contact typed at booking time is
    someone else's number, not the account holder's, but it still has to
    be a real dialable mobile, not free text."""
    if not v:
        return v
    phone = validate_indian_mobile(v)
    if not phone:
        raise ValueError("Enter a valid 10-digit mobile number")
    return phone


class BookingCreateRequest(BaseModel):
    # Exactly one of these: a saved vehicle record (older flows) or just the
    # VEHICLE TYPE (quick booking — no plate, no brand/model).
    vehicle_id: Optional[str] = None
    vehicle_type: Optional[str] = None
    address_id: str
    service_ids: list[str] = Field(default_factory=list)
    # Per-unit add-on counts (service_id -> qty), e.g. Extra Bike Wash ×3 or
    # Bike Polish ×2. Anything not listed is qty 1; the server decides which
    # services may repeat at all (see BookingService._validate_service_mix).
    service_quantities: dict[str, int] = Field(default_factory=dict)
    combo_id: Optional[str] = None
    scheduled_date: datetime
    scheduled_slot: str
    payment_method: PaymentMethod = PaymentMethod.CASH
    coupon_code: Optional[str] = None
    subscription_id: Optional[str] = None
    customer_notes: Optional[str] = None
    alternate_contact_name: Optional[str] = Field(default=None, max_length=100)
    alternate_contact_phone: Optional[str] = Field(default=None, max_length=20)
    # Slot-hold ticket (AUDIT.md M1): the anonymous/session key the client
    # used when acquiring a hold, so create_booking can convert it.
    hold_key: Optional[str] = Field(default=None, max_length=80)

    _validate_alt_phone = field_validator("alternate_contact_phone")(_validate_alt_contact_phone)

    @model_validator(mode="after")
    def _vehicle_or_type(self) -> "BookingCreateRequest":
        if bool(self.vehicle_id) == bool(self.vehicle_type):
            raise ValueError("Provide exactly one of vehicle_id or vehicle_type")
        return self


class QuickAddress(BaseModel):
    """Where the captain comes to — a pinned point (the LocationPicker)
    plus the pincode that routes it to a service center. line1 is the
    resolved label of that pin, or the typed address in the no-maps
    fallback."""
    line1: str = Field(min_length=3, max_length=300)
    landmark: Optional[str] = Field(default=None, max_length=200)
    city: Optional[str] = Field(default=None, max_length=100)
    state: Optional[str] = Field(default=None, max_length=100)
    # Optional ONLY with a pin: a reverse-geocode can come back without a
    # postal code, and the pin alone is enough to route the booking.
    pincode: Optional[str] = Field(default=None, max_length=10)
    latitude: Optional[float] = None
    longitude: Optional[float] = None

    @model_validator(mode="after")
    def _pin_or_pincode(self) -> "QuickAddress":
        pin = (self.pincode or "").strip()
        if pin and len(pin) < 4:
            raise ValueError("Enter a valid pincode")
        if not pin and (self.latitude is None or self.longitude is None):
            raise ValueError("Enter the pincode or pin the location on the map")
        self.pincode = pin or None
        return self


class QuickBookingLine(BaseModel):
    """One vehicle TYPE on the visit: "2 SUVs, Foam Wash". quantity > 1
    becomes that many bookings, each with the same service(s)."""
    vehicle_type: str = Field(min_length=1)
    quantity: int = Field(default=1, ge=1, le=10)
    service_ids: list[str] = Field(min_length=1)
    service_quantities: dict[str, int] = Field(default_factory=dict)


class QuickBookingRequest(BaseModel):
    """The whole booking in one request, no account needed up front
    (2026-09 quick-booking model): who (name + phone), what (vehicle types
    + services), where (address) and when (date + slot). The customer
    profile is found or created from the phone silently; nothing here
    requires a login or an OTP. The same shape serves the website, the
    WhatsApp bot and a manager booking on a customer's behalf."""
    customer_name: str = Field(min_length=2, max_length=100)
    customer_phone: str = Field(min_length=10, max_length=20)
    # A saved address (logged-in customer / manager picking one) OR a new
    # one — exactly one.
    address_id: Optional[str] = None
    address: Optional[QuickAddress] = None
    lines: list[QuickBookingLine] = Field(min_length=1)
    scheduled_date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    scheduled_slot: str = Field(max_length=20)
    payment_method: PaymentMethod = PaymentMethod.CASH
    coupon_code: Optional[str] = Field(default=None, max_length=20)
    customer_notes: Optional[str] = Field(default=None, max_length=500)
    alternate_contact_name: Optional[str] = Field(default=None, max_length=100)
    alternate_contact_phone: Optional[str] = Field(default=None, max_length=20)
    hold_key: Optional[str] = Field(default=None, max_length=80)
    # Proof customer_phone is theirs — the same two kinds login accepts: our
    # own OTP code, or the MSG91 widget access token. Required for anonymous
    # website bookings only; signed-in customers and managers send neither.
    phone_otp: Optional[str] = Field(default=None, max_length=8)
    phone_access_token: Optional[str] = Field(default=None, max_length=4000)

    _validate_alt_phone = field_validator("alternate_contact_phone")(_validate_alt_contact_phone)

    @field_validator("customer_phone")
    @classmethod
    def _phone(cls, v: str) -> str:
        phone = validate_indian_mobile(v)
        if not phone:
            raise ValueError("Enter a valid 10-digit mobile number")
        return phone

    @field_validator("customer_name")
    @classmethod
    def _name(cls, v: str) -> str:
        v = " ".join(v.split())
        if len(v) < 2:
            raise ValueError("Enter your name")
        return v

    @model_validator(mode="after")
    def _one_address(self) -> "QuickBookingRequest":
        if bool(self.address_id) == bool(self.address):
            raise ValueError("Provide exactly one of address_id or address")
        total = sum(line.quantity for line in self.lines)
        if total > 10:
            raise ValueError("You can book up to 10 vehicles on one visit")
        return self


def _canonical_phone(v: str) -> str:
    phone = validate_indian_mobile(v)
    if not phone:
        raise ValueError("Enter a valid 10-digit mobile number")
    return phone


class BookingPhoneOtpRequest(BaseModel):
    phone: str = Field(min_length=10, max_length=20)
    _phone = field_validator("phone")(_canonical_phone)


class ManagerLogBookingRequest(BaseModel):
    """A job the manager already did himself (phone-in or walk-in): it is
    saved directly as COMPLETED — no slot capacity, no captain, no photos.
    The time is a clock time, not a slot; the server files it under the
    center's slot that contains it."""
    customer_name: str = Field(min_length=2, max_length=100)
    customer_phone: str = Field(min_length=10, max_length=20)
    lines: list[QuickBookingLine] = Field(min_length=1)
    scheduled_date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    service_time: str = Field(pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    address_line: str = Field(min_length=3, max_length=300)
    landmark: Optional[str] = Field(default=None, max_length=200)
    payment_method: PaymentMethod = PaymentMethod.CASH
    customer_notes: Optional[str] = Field(default=None, max_length=500)
    # Rupees the manager knocked off the bill (whole visit, optional). Taken
    # off the price BEFORE the money is recorded, so the ledger, revenue and
    # the customer's booking all show what was actually paid.
    discount_amount: float = Field(default=0, ge=0, le=100000)
    # True = the customer gets ONE WhatsApp: "service is done". Nothing else.
    send_whatsapp: bool = True

    _phone = field_validator("customer_phone")(_canonical_phone)

    @field_validator("discount_amount")
    @classmethod
    def _round_discount(cls, v: float) -> float:
        return round(v, 2)

    @field_validator("customer_name")
    @classmethod
    def _name(cls, v: str) -> str:
        v = " ".join(v.split())
        if len(v) < 2:
            raise ValueError("Enter the customer's name")
        return v

    @field_validator("address_line")
    @classmethod
    def _address(cls, v: str) -> str:
        v = " ".join(v.split())
        if len(v) < 3:
            raise ValueError("Enter where the job was done")
        return v

    @field_validator("payment_method")
    @classmethod
    def _paid_method(cls, v: PaymentMethod) -> PaymentMethod:
        if v not in (PaymentMethod.CASH, PaymentMethod.ONLINE):
            raise ValueError("Payment must be cash or online")
        return v

    @model_validator(mode="after")
    def _vehicle_limit(self) -> "ManagerLogBookingRequest":
        if sum(line.quantity for line in self.lines) > 10:
            raise ValueError("You can log up to 10 vehicles on one visit")
        return self


class ManagerMarkDoneRequest(BaseModel):
    send_whatsapp: bool = True


class ManagerBookingCreateRequest(BaseModel):
    """A manager/admin creating a booking on behalf of a customer (new or
    existing) — see BookingService.create_booking_for_customer. Exactly one
    of vehicle_id/new_vehicle and exactly one of address_id/new_address must
    be provided."""
    customer_id: str
    vehicle_id: Optional[str] = None
    new_vehicle: Optional[VehicleCreateRequest] = None
    # Quick-booking model: just the vehicle TYPE, no record at all.
    vehicle_type: Optional[str] = None
    address_id: Optional[str] = None
    new_address: Optional[AddressCreateRequest] = None
    service_ids: list[str] = Field(default_factory=list)
    service_quantities: dict[str, int] = Field(default_factory=dict)
    combo_id: Optional[str] = None
    scheduled_date: datetime
    scheduled_slot: str
    payment_method: PaymentMethod = PaymentMethod.CASH
    coupon_code: Optional[str] = None
    subscription_id: Optional[str] = None
    customer_notes: Optional[str] = None
    alternate_contact_name: Optional[str] = Field(default=None, max_length=100)
    alternate_contact_phone: Optional[str] = Field(default=None, max_length=20)

    _validate_alt_phone = field_validator("alternate_contact_phone")(_validate_alt_contact_phone)

    @model_validator(mode="after")
    def _exactly_one_vehicle_and_address(self) -> "ManagerBookingCreateRequest":
        if sum(bool(x) for x in (self.vehicle_id, self.new_vehicle, self.vehicle_type)) != 1:
            raise ValueError("Provide exactly one of vehicle_id, new_vehicle or vehicle_type")
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


class GroupVehicleRequest(BaseModel):
    """One car on a multi-car visit — its own vehicle and its own choice of
    service. Everything shared by the visit (where, when, how it's paid for)
    lives on BookingGroupCreateRequest."""

    vehicle_id: Optional[str] = None
    # Quick-booking model: a vehicle TYPE instead of a record; quantity > 1
    # expands to that many cars with the same service(s).
    vehicle_type: Optional[str] = None
    quantity: int = Field(default=1, ge=1, le=10)
    service_ids: list[str] = []
    service_quantities: dict[str, int] = Field(default_factory=dict)
    combo_id: Optional[str] = None
    # This car's own pass, if it has one. A pass belongs to one car, so a
    # customer with two passes gets two cars covered and pays for the rest.
    subscription_id: Optional[str] = None

    @model_validator(mode="after")
    def _vehicle_or_type(self) -> "GroupVehicleRequest":
        if bool(self.vehicle_id) == bool(self.vehicle_type):
            raise ValueError("Provide exactly one of vehicle_id or vehicle_type")
        if self.vehicle_id and self.quantity != 1:
            raise ValueError("quantity applies to vehicle_type lines only")
        return self


class BookingGroupCreateRequest(BaseModel):
    """Several of one customer's vehicles washed on ONE visit: one address,
    one slot, one captain, one payment. It takes ONE slot seat however many
    cars are on it — same address, so the travel happens once."""

    vehicles: list[GroupVehicleRequest] = Field(min_length=1)
    address_id: str
    scheduled_date: str
    scheduled_slot: str
    hold_key: Optional[str] = None
    payment_method: Optional[PaymentMethod] = None
    coupon_code: Optional[str] = None
    customer_notes: Optional[str] = None
    alternate_contact_name: Optional[str] = None
    alternate_contact_phone: Optional[str] = None

    _validate_alt_phone = field_validator("alternate_contact_phone")(_validate_alt_contact_phone)


class BookingCancelRequest(BaseModel):
    reason: str = Field(min_length=3, max_length=300)


class BookingAssignCaptainRequest(BaseModel):
    captain_id: str
    # Optional finer-grained start time within the booking's admin slot
    # window (e.g. 9:00 or 10:30 inside a 09:00-12:00 slot) — lets a
    # manager put more than one booking on the same captain within one
    # shared slot without them looking like a schedule conflict. Defaults
    # to the slot's own start if omitted.
    estimated_start_at: Optional[datetime] = None


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


class CaptainLocationPingRequest(BaseModel):
    """Periodic location update while a captain has an active job — see
    BookingService.update_captain_location / has_active_job."""
    latitude: float
    longitude: float


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
    estimated_start_at: Optional[datetime] = None


class VerifyVehicleRequest(BaseModel):
    """Captain types the plate they see on arrival — a deliberate typed check,
    not a yes/no toggle, so a captain can't rubber-stamp past the wrong car.
    This is also the "I've reached" moment, so it carries the device's GPS
    (geofence-checked against the customer's address like the photos).
    Coordinates are optional only for old app versions still in the field."""
    # Quick-booking model: the 4-digit service code the customer received
    # on confirmation. registration_number remains only for bookings made
    # under the older saved-vehicle flow.
    service_code: Optional[str] = Field(default=None, min_length=4, max_length=4, pattern=r"^\d{4}$")
    registration_number: Optional[str] = Field(default=None, min_length=3, max_length=20)
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    accuracy_m: Optional[float] = None

    @model_validator(mode="after")
    def _code_or_plate(self) -> "VerifyVehicleRequest":
        if not self.service_code and not self.registration_number:
            raise ValueError("Enter the customer's 4-digit service code")
        return self


class ResolveIssueRequest(BaseModel):
    # Required, same as BookingCancelRequest.reason — a manager must state
    # why an issue is being dismissed, not just click it away silently.
    note: str = Field(min_length=3, max_length=300)


class PriorityUpdateRequest(BaseModel):
    priority: BookingPriority
