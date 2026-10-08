import re
from datetime import date, datetime
from typing import Annotated, Literal, Optional

from pydantic import BaseModel, Field, field_validator, model_validator

from app.core.storage import MAX_STORED_URL_LENGTH, is_own_upload_url
from app.models.enums import BookingPriority, PaymentMethod
from app.schemas.profile_schema import AddressCreateRequest, VehicleCreateRequest
from app.utils.phone import validate_indian_mobile
from app.utils.timezone import IST

_PLAIN_DAY = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def ist_calendar_day(value) -> datetime:
    """THE one form a booking's scheduled_date takes: the IST business
    calendar day it names, as naive midnight (the form every query and the
    seat counters key on). "2026-10-10", "2026-10-10T00:00:00+05:30" and
    "2026-10-09T18:30:00Z" are all 10 Oct. An aware value was stored as a
    UTC instant before — the previous day at 18:30 — so its booking showed,
    and released its seat, on the wrong day (audit BOOK-01). A naive value
    is IST wall-clock already; only its date counts."""
    if isinstance(value, datetime):
        moment = value
    elif isinstance(value, date):
        return datetime(value.year, value.month, value.day)
    elif isinstance(value, str):
        text = value.strip()
        try:
            if _PLAIN_DAY.match(text):
                day = date.fromisoformat(text)
                return datetime(day.year, day.month, day.day)
            moment = datetime.fromisoformat(text.replace("Z", "+00:00").replace("z", "+00:00"))
        except ValueError:
            raise ValueError("Pick a valid date (YYYY-MM-DD).")
    else:
        raise ValueError("Pick a valid date (YYYY-MM-DD).")
    day = moment.astimezone(IST).date() if moment.tzinfo else moment.date()
    return datetime(day.year, day.month, day.day)


def _ist_day_string(value) -> str:
    return ist_calendar_day(value).strftime("%Y-%m-%d")


def _unique_ids(ids: list[str]) -> list[str]:
    """Each service once, in the order picked. ["star", "star", "star"] used
    to price three washes while a pass waived them all and spent one
    (audit PRICE-02); repeats are what service_quantities is for."""
    return list(dict.fromkeys(ids or []))


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
    # The total the customer was shown (from POST /bookings/quote). When
    # sent, a booking the server would now charge MORE for is refused with
    # 409 PRICE_CHANGED instead of being created at the new price.
    expected_total: Optional[float] = Field(default=None, ge=0)

    _validate_alt_phone = field_validator("alternate_contact_phone")(_validate_alt_contact_phone)
    _day = field_validator("scheduled_date", mode="before")(ist_calendar_day)
    _services = field_validator("service_ids")(_unique_ids)

    @model_validator(mode="after")
    def _vehicle_or_type(self) -> "BookingCreateRequest":
        # A saved car (vehicle_id) may also name its type — a car-bound
        # pass booking sends both; the car's own type must match it
        # (checked by create_booking).
        if not (self.vehicle_id or self.vehicle_type):
            raise ValueError("Provide vehicle_id or vehicle_type")
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
    vehicle_type: str = Field(min_length=1, max_length=64)
    quantity: int = Field(default=1, ge=1, le=10)
    service_ids: list[str] = Field(min_length=1, max_length=20)
    service_quantities: dict[str, int] = Field(default_factory=dict)
    # True (default — every existing caller keeps today's behavior:
    # self-serve booking, the WhatsApp bot, and a manager who didn't send
    # this field) auto-applies the customer's matching pass with nothing to
    # pick, exactly as before. A manager's own booking/log-a-job form sends
    # False for a line the manager explicitly unticked ("use this
    # customer's plan?") — e.g. the customer wants to save the wash and pay
    # cash this once. See BookingService._cars_for_lines.
    use_subscription: bool = True
    # An explicit car-bound pass (a custom multi-car plan or a society pass
    # names ONE car): the customer's saved car and the pass to use for it.
    # Such a pass is never applied automatically and never to another car
    # (BookingService._cars_for_lines); quantity must be 1.
    vehicle_id: Optional[str] = Field(default=None, pattern=r"^[0-9a-fA-F]{24}$")
    subscription_id: Optional[str] = Field(default=None, pattern=r"^[0-9a-fA-F]{24}$")

    _services = field_validator("service_ids")(_unique_ids)

    @model_validator(mode="after")
    def _one_named_car(self) -> "QuickBookingLine":
        if (self.vehicle_id or self.subscription_id) and self.quantity != 1:
            raise ValueError("A line for a specific car (or its pass) books exactly one car")
        return self


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
    # The visit total the customer was shown — see BookingCreateRequest.
    expected_total: Optional[float] = Field(default=None, ge=0)

    _validate_alt_phone = field_validator("alternate_contact_phone")(_validate_alt_contact_phone)

    @field_validator("payment_method")
    @classmethod
    def _cash_or_online(cls, v: PaymentMethod) -> PaymentMethod:
        # "subscription" is set by the server when a plan pays; a client
        # sending it skipped the prepaid gate and settled as non-cash.
        if v not in (PaymentMethod.CASH, PaymentMethod.ONLINE):
            raise ValueError("Payment must be cash or online")
        return v

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


class QuoteAddress(BaseModel):
    """Just enough of an unsaved address to measure the distance charge."""
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    pincode: Optional[str] = Field(default=None, max_length=10)


class BookingQuoteRequest(BaseModel):
    """What a booking WOULD cost, from the same code that prices it (see
    BookingService.quote_visit). Same lines as QuickBookingRequest; the
    customer is the signed-in one, or (staff / anonymous) whoever owns
    customer_phone."""
    customer_phone: Optional[str] = Field(default=None, max_length=20)
    lines: list[QuickBookingLine] = Field(min_length=1)
    address_id: Optional[str] = None
    address: Optional[QuoteAddress] = None
    coupon_code: Optional[str] = Field(default=None, max_length=20)
    # "log" = a manager-logged done job (no distance charge / prepaid rule /
    # coupon); scheduled_date + service_time then say when it happened.
    mode: str = Field(default="book", pattern=r"^(book|log)$")
    scheduled_date: Optional[str] = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    service_time: Optional[str] = Field(default=None, pattern=r"^([01]\d|2[0-3]):[0-5]\d$")

    @field_validator("customer_phone")
    @classmethod
    def _phone(cls, v: Optional[str]) -> Optional[str]:
        # A half-typed number is simply "not known yet", never an error.
        return validate_indian_mobile(v) if v else None

    @model_validator(mode="after")
    def _vehicle_limit(self) -> "BookingQuoteRequest":
        if sum(line.quantity for line in self.lines) > 10:
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


def _whole_rupee_tip(v: float) -> float:
    """Tips are whole rupees, never paise."""
    from app.utils.money import round_rupees

    return float(round_rupees(v or 0))


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
    # Tip the customer gave for this visit (optional). Added to the visit's
    # total and revenue (founder) — see BookingService._record_tip.
    tip_amount: float = Field(default=0, ge=0, le=100000)
    # How the tip was handed over — its own method, independent of
    # payment_method (MONEY-2). Default cash.
    tip_method: Literal["cash", "online"] = "cash"
    # True = the customer gets ONE WhatsApp: "service is done". Nothing else.
    send_whatsapp: bool = True

    _phone = field_validator("customer_phone")(_canonical_phone)
    _tip = field_validator("tip_amount")(_whole_rupee_tip)

    @field_validator("discount_amount")
    @classmethod
    def _round_discount(cls, v: float) -> float:
        from app.utils.money import round_rupees

        return float(round_rupees(v))  # whole rupees, never paise

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


class BookingTipRequest(BaseModel):
    """Manager/admin recording (or correcting) the tip on a job the manager
    did. 0 clears it. One tip per visit, part of its total — see
    BookingService.set_tip. `tip_method` (MONEY-2): how the tip itself was
    handed over — cash (default) or online — independent of how the job
    was paid; it decides which money bucket the tip is counted in."""
    tip_amount: float = Field(ge=0, le=100000)
    tip_method: Literal["cash", "online"] = "cash"

    _tip = field_validator("tip_amount")(_whole_rupee_tip)


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
    _day = field_validator("scheduled_date", mode="before")(ist_calendar_day)
    _services = field_validator("service_ids")(_unique_ids)

    @model_validator(mode="after")
    def _exactly_one_vehicle_and_address(self) -> "ManagerBookingCreateRequest":
        if sum(bool(x) for x in (self.vehicle_id, self.new_vehicle, self.vehicle_type)) != 1:
            raise ValueError("Provide exactly one of vehicle_id, new_vehicle or vehicle_type")
        if bool(self.address_id) == bool(self.new_address):
            raise ValueError("Provide exactly one of address_id or new_address")
        return self


class BookingUpdateDetailsRequest(BaseModel):
    """Manager/admin correcting the metadata on an existing booking — notes
    and the alternate contact, not anything that drives price, capacity or
    captain assignment (service/vehicle/address/time). See
    BookingService.update_details. All fields optional: only the ones sent
    are changed, so a client can PATCH just the one field it edited."""
    customer_notes: Optional[str] = Field(default=None, max_length=500)
    alternate_contact_name: Optional[str] = Field(default=None, max_length=100)
    alternate_contact_phone: Optional[str] = Field(default=None, max_length=20)

    _validate_alt_phone = field_validator("alternate_contact_phone")(_validate_alt_contact_phone)


class ReportRiskRequest(BaseModel):
    """Captain self-reporting they may not make their next booking on time
    because their current job is running long — see
    BookingService.report_risk."""
    note: Optional[str] = Field(default=None, max_length=300)


class BookingRescheduleRequest(BaseModel):
    scheduled_date: datetime
    scheduled_slot: str = Field(max_length=20)

    _day = field_validator("scheduled_date", mode="before")(ist_calendar_day)


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

    _services = field_validator("service_ids")(_unique_ids)

    @model_validator(mode="after")
    def _vehicle_or_type(self) -> "GroupVehicleRequest":
        # Both may be sent for a saved car (its type must match — see
        # BookingCreateRequest); at least one is required.
        if not (self.vehicle_id or self.vehicle_type):
            raise ValueError("Provide vehicle_id or vehicle_type")
        if self.vehicle_id and self.quantity != 1:
            raise ValueError("quantity applies to vehicle_type lines only")
        return self


class BookingGroupCreateRequest(BaseModel):
    """Several of one customer's vehicles wash on ONE visit: one address,
    one slot, one captain, one payment. It takes ONE slot seat however many
    cars are on it — same address, so the travel happens once."""

    vehicles: list[GroupVehicleRequest] = Field(min_length=1)
    address_id: str
    # Always the plain IST day ("YYYY-MM-DD") once validated — see ist_calendar_day.
    scheduled_date: str
    scheduled_slot: str
    hold_key: Optional[str] = None
    payment_method: Optional[PaymentMethod] = None
    coupon_code: Optional[str] = None
    customer_notes: Optional[str] = None
    alternate_contact_name: Optional[str] = None
    alternate_contact_phone: Optional[str] = None
    # The visit total the customer was shown — see BookingCreateRequest.
    expected_total: Optional[float] = Field(default=None, ge=0)

    _validate_alt_phone = field_validator("alternate_contact_phone")(_validate_alt_contact_phone)
    _day = field_validator("scheduled_date", mode="before")(_ist_day_string)


class BookingCancelRequest(BaseModel):
    reason: str = Field(min_length=3, max_length=300)
    # Staff only (a customer's own cancel is always "at their request"):
    # the customer asked for this cancellation, so the late-cancellation
    # charge applies (GET /bookings/{id}/cancellation-charge-preview).
    # Without it a staff cancel is a business cancel and never charges.
    at_customer_request: bool = False
    # Staff may lower the charge: 0..the policy amount (default the policy
    # amount when at_customer_request). Whole rupees.
    charge_amount: Optional[float] = Field(default=None, ge=0, le=2000)
    # Staff only: hand a plan-covered wash back to the pass even though the
    # customer cancelled inside its last hour (it is used up by default —
    # spec 1.2). Ignored for the customer's own cancel.
    return_plan_wash: Optional[bool] = None

    @field_validator("charge_amount")
    @classmethod
    def _rupees(cls, v: Optional[float]) -> Optional[float]:
        from app.utils.money import round_rupees

        return None if v is None else float(round_rupees(v))


class BookingAssignCaptainRequest(BaseModel):
    captain_id: str
    # Optional finer-grained start time within the booking's admin slot
    # window (e.g. 9:00 or 10:30 inside a 09:00-12:00 slot) — lets a
    # manager put more than one booking on the same captain within one
    # shared slot without them looking like a schedule conflict. Defaults
    # to the slot's own start if omitted.
    estimated_start_at: Optional[datetime] = None


class EquipmentUsedInput(BaseModel):
    # Checked against the booking's center inventory before the job moves
    # (audit VAL-3: a bad id was a 500 after the status had changed).
    inventory_item_id: str = Field(pattern=r"^[0-9a-fA-F]{24}$")
    item_name: str = Field(max_length=120)
    # Taken FROM the store — never zero or negative (a negative quantity
    # topped the stock up).
    quantity: float = Field(gt=0, le=1000)


# A real position on Earth (audit CAP-04: any float was stored as the
# captain's location and fed the geofence/breadcrumb maths).
Latitude = Annotated[float, Field(ge=-90, le=90)]
Longitude = Annotated[float, Field(ge=-180, le=180)]


class HeadingRequest(BaseModel):
    """Captain taps 'Heading to customer' — captures departure time + their
    current GPS location, and the equipment they're carrying from the store."""
    latitude: Latitude
    longitude: Longitude
    equipment_used: list[EquipmentUsedInput] = []


class CaptainLocationPingRequest(BaseModel):
    """Periodic location update while a captain has an active job — see
    BookingService.update_captain_location / has_active_job."""
    latitude: Latitude
    longitude: Longitude


class PhotoCaptureRequest(BaseModel):
    """Used for both the before-service and after-service photo steps. The
    image must come from a live camera capture on the frontend (never a
    file picker) and always carries the device's GPS coordinates."""
    image_url: str = Field(max_length=MAX_STORED_URL_LENGTH)
    latitude: Latitude
    longitude: Longitude

    @field_validator("image_url")
    @classmethod
    def _uploaded_by_us(cls, v: str) -> str:
        # The proof photo is whatever POST /uploads/photo returned — any
        # other URL would put an arbitrary (or someone else's) image on the
        # job card the customer and manager trust.
        if not is_own_upload_url(v):
            raise ValueError("Please take the photo again — it didn't upload to our storage.")
        return v


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
    latitude: Optional[float] = Field(default=None, ge=-90, le=90)
    longitude: Optional[float] = Field(default=None, ge=-180, le=180)
    accuracy_m: Optional[float] = Field(default=None, ge=0, le=100000)

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


_OBJECT_ID = r"^[0-9a-fA-F]{24}$"


class BookingCarEdit(BaseModel):
    """One car's changes in a customer edit (spec 1.3). Only the fields
    sent change. `booking_id` names the car (required on
    PATCH /bookings/group/{id}; on PATCH /bookings/{id} it defaults to that
    booking). A plan-covered car can't change its service or car."""
    booking_id: Optional[str] = Field(default=None, pattern=_OBJECT_ID)
    vehicle_type: Optional[str] = Field(default=None, min_length=1, max_length=64)
    vehicle_id: Optional[str] = Field(default=None, pattern=_OBJECT_ID)
    service_ids: Optional[list[str]] = Field(default=None, min_length=1, max_length=20)
    service_quantities: Optional[dict[str, int]] = None

    _services = field_validator("service_ids")(lambda v: _unique_ids(v) if v is not None else v)

    def changes_car(self) -> bool:
        return any(v is not None for v in (self.vehicle_type, self.vehicle_id, self.service_ids, self.service_quantities))


class BookingEditRequest(BaseModel):
    """PATCH /bookings/{id} and PATCH /bookings/group/{id} — the customer
    changes their booking (spec 1.3) until 1 hour before the slot.
    Visit-wide: scheduled_date + scheduled_slot (together), the address
    (a saved address_id or a new pinned `address`; it must be served by the
    SAME center) and notes. Per car: `cars` (or, on the single-booking
    endpoint, the top-level vehicle_type / vehicle_id / service_ids /
    service_quantities). `expected_total`: the visit total the customer was
    shown — a higher new total is refused with 409 PRICE_CHANGED."""
    scheduled_date: Optional[datetime] = None
    scheduled_slot: Optional[str] = Field(default=None, max_length=20)
    address_id: Optional[str] = Field(default=None, pattern=_OBJECT_ID)
    address: Optional[QuickAddress] = None
    customer_notes: Optional[str] = Field(default=None, max_length=500)
    vehicle_type: Optional[str] = Field(default=None, min_length=1, max_length=64)
    vehicle_id: Optional[str] = Field(default=None, pattern=_OBJECT_ID)
    service_ids: Optional[list[str]] = Field(default=None, min_length=1, max_length=20)
    service_quantities: Optional[dict[str, int]] = None
    cars: Optional[list[BookingCarEdit]] = Field(default=None, max_length=10)
    expected_total: Optional[float] = Field(default=None, ge=0)
    # Preview only: the same validation, locks and pricing as the real save,
    # but nothing is written (no seat move, no money, no messages, no
    # history) — the UI shows the exact new price before the customer saves.
    dry_run: bool = False

    _services = field_validator("service_ids")(lambda v: _unique_ids(v) if v is not None else v)

    @field_validator("scheduled_date", mode="before")
    @classmethod
    def _day(cls, v):
        return None if v is None else ist_calendar_day(v)

    @model_validator(mode="after")
    def _consistent(self) -> "BookingEditRequest":
        if (self.scheduled_date is None) != (self.scheduled_slot is None):
            raise ValueError("Send the new date and slot together")
        if self.address_id and self.address:
            raise ValueError("Provide address_id or address, not both")
        top_car = any(v is not None for v in (self.vehicle_type, self.vehicle_id, self.service_ids, self.service_quantities))
        if top_car and self.cars:
            raise ValueError("Send car changes either at the top level or in cars, not both")
        if not (
            self.scheduled_date or self.address_id or self.address or self.customer_notes is not None or top_car
            or any(c.changes_car() for c in (self.cars or []))
        ):
            raise ValueError("Nothing to change")
        return self

    def car_edits(self, default_booking_id: str | None) -> list[BookingCarEdit]:
        """Every car edit as BookingCarEdit (the top-level fields become one
        for `default_booking_id`)."""
        if any(v is not None for v in (self.vehicle_type, self.vehicle_id, self.service_ids, self.service_quantities)):
            return [BookingCarEdit(
                booking_id=default_booking_id, vehicle_type=self.vehicle_type, vehicle_id=self.vehicle_id,
                service_ids=self.service_ids, service_quantities=self.service_quantities,
            )]
        return [c if c.booking_id else c.model_copy(update={"booking_id": default_booking_id}) for c in (self.cars or []) if c.changes_car()]


class AddServicesRequest(BaseModel):
    """POST /bookings/{id}/add-services — services/add-ons added on site by
    the assigned captain (after arrival) or by the center's manager/admin
    (spec 1.4). quantities: {service_id: n} for per-unit add-ons."""
    service_ids: list[str] = Field(min_length=1, max_length=10)
    quantities: dict[str, int] = Field(default_factory=dict)
    note: Optional[str] = Field(default=None, max_length=300)

    _services = field_validator("service_ids")(_unique_ids)

    @field_validator("service_ids")
    @classmethod
    def _ids(cls, v: list[str]) -> list[str]:
        if any(not re.match(_OBJECT_ID, i or "") for i in v):
            raise ValueError("Pick valid services")
        return v
