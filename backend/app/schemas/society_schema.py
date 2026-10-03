"""Request shapes for society plans — see docs/SOCIETY_PLANS.md."""
from typing import Annotated, Literal, Optional

from pydantic import BaseModel, Field, field_validator, model_validator

from app.utils.phone import validate_indian_mobile


def _phone(v: Optional[str]) -> Optional[str]:
    if v in (None, ""):
        return None
    phone = validate_indian_mobile(v)
    if not phone:
        raise ValueError("Enter a valid 10-digit mobile number")
    return phone


def _clean(v: Optional[str]) -> Optional[str]:
    if v is None:
        return None
    v = " ".join(v.split())
    return v or None


def _coupon(v: Optional[str]) -> Optional[str]:
    """Coupon codes are stored upper-case; blank = no coupon."""
    if v is None:
        return None
    v = "".join(v.split()).upper()
    return v or None


# -- Societies ---------------------------------------------------------------


class SocietyCreateRequest(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    address_line: str = Field(min_length=3, max_length=300)
    area: Optional[str] = Field(default=None, max_length=120)
    city: str = Field(default="Indore", min_length=2, max_length=80)
    state: str = Field(default="Madhya Pradesh", max_length=80)
    pincode: str = Field(min_length=6, max_length=6, pattern=r"^\d{6}$")
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    contact_name: Optional[str] = Field(default=None, max_length=100)
    contact_phone: Optional[str] = Field(default=None, max_length=20)
    notes: Optional[str] = Field(default=None, max_length=500)
    # Admin only (a manager's society always resolves to their own center).
    service_center_id: Optional[str] = None
    # Registered from a landing-page society request: that request is
    # marked "registered" and linked to the new society.
    lead_id: Optional[str] = Field(default=None, pattern=r"^[0-9a-fA-F]{24}$")

    _p = field_validator("contact_phone")(_phone)
    _n = field_validator("name", "address_line", "area", "city", "contact_name")(_clean)


class SocietyUpdateRequest(BaseModel):
    name: Optional[str] = Field(default=None, min_length=2, max_length=120)
    address_line: Optional[str] = Field(default=None, min_length=3, max_length=300)
    area: Optional[str] = Field(default=None, max_length=120)
    city: Optional[str] = Field(default=None, max_length=80)
    state: Optional[str] = Field(default=None, max_length=80)
    pincode: Optional[str] = Field(default=None, pattern=r"^\d{6}$")
    latitude: Optional[float] = Field(default=None, ge=-90, le=90)
    longitude: Optional[float] = Field(default=None, ge=-180, le=180)
    contact_name: Optional[str] = Field(default=None, max_length=100)
    contact_phone: Optional[str] = Field(default=None, max_length=20)
    notes: Optional[str] = Field(default=None, max_length=500)
    form_enabled: Optional[bool] = None
    is_active: Optional[bool] = None

    _p = field_validator("contact_phone")(_phone)
    _n = field_validator("name", "address_line", "area", "city", "contact_name")(_clean)

    @model_validator(mode="after")
    def _pin_pair(self) -> "SocietyUpdateRequest":
        if (self.latitude is None) != (self.longitude is None):
            raise ValueError("Send both latitude and longitude")
        return self


class SocietyCaptainRequest(BaseModel):
    """captain_id=None clears it. With `date` (YYYY-MM-DD) it is a one-day
    substitute instead of the daily captain."""

    captain_id: Optional[str] = None
    date: Optional[str] = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")


# -- Plans -------------------------------------------------------------------


class CustomCombo(BaseModel):
    bucket_days: int = Field(ge=1, le=31)
    premium_service_id: str
    premium_count: int = Field(ge=1, le=12)


class SocietyCarInput(BaseModel):
    vehicle_type: str = Field(min_length=1, max_length=64)
    registration_number: str = Field(min_length=4, max_length=20)

    @field_validator("registration_number")
    @classmethod
    def _plate(cls, v: str) -> str:
        from app.utils.vehicle_reg import validate_indian_registration

        plate = validate_indian_registration(v)
        if not plate:
            raise ValueError("Enter a valid registration number, e.g. MP09AB1234")
        return plate


class PlanChoice(BaseModel):
    """Exactly one: an existing society plan, or a customised combination."""

    plan_id: Optional[str] = None
    custom: Optional[CustomCombo] = None

    @model_validator(mode="after")
    def _one(self) -> "PlanChoice":
        if bool(self.plan_id) == bool(self.custom):
            raise ValueError("Choose a plan or customise one")
        return self


class SocietyQuoteRequest(PlanChoice):
    vehicle_types: list[Annotated[str, Field(min_length=1, max_length=64)]] = Field(min_length=1, max_length=10)
    # Optional coupon: the quote shows its discount (or why it can't apply).
    coupon_code: Optional[str] = Field(default=None, max_length=30)

    _c = field_validator("coupon_code")(_coupon)


class SocietyEnrollRequest(PlanChoice):
    resident_name: str = Field(min_length=2, max_length=100)
    phone: str = Field(min_length=10, max_length=20)
    flat: str = Field(min_length=1, max_length=60)
    cars: list[SocietyCarInput] = Field(min_length=1, max_length=10)
    pay_now: bool = False
    # Phone proof for a caller who isn't signed in with this phone already.
    phone_otp: Optional[str] = Field(default=None, max_length=10)
    phone_access_token: Optional[str] = Field(default=None, max_length=4000)
    # An existing coupon (CouponService rules); counted on activation.
    coupon_code: Optional[str] = Field(default=None, max_length=30)

    _p = field_validator("phone")(_phone)
    _n = field_validator("resident_name", "flat")(_clean)
    _c = field_validator("coupon_code")(_coupon)

    @model_validator(mode="after")
    def _distinct_plates(self) -> "SocietyEnrollRequest":
        plates = [c.registration_number for c in self.cars]
        if len(set(plates)) != len(plates):
            raise ValueError("Each car can be added once")
        return self


class ManagerEnrollRequest(SocietyEnrollRequest):
    """A manager adding a resident on their behalf — no OTP; `collect_cash`
    activates at once with the money already in hand."""

    collect_cash: bool = False
    pay_now: bool = False


class ActivateEnrollmentRequest(BaseModel):
    """Manager/admin: cash collected for this enrollment (or its renewal)."""

    method: Literal["cash"] = "cash"
    note: Optional[str] = Field(default=None, max_length=300)
    # The request version the manager reviewed (SocietyEnrollment.revision).
    # Activation refuses (409) if the resident changed it since. Renewals
    # ignore it.
    expected_revision: Optional[int] = Field(default=None, ge=1)
    # Mark paid / renew with a coupon: a code applies (or replaces the
    # resident's) coupon; remove_coupon drops the resident's one.
    coupon_code: Optional[str] = Field(default=None, max_length=30)
    remove_coupon: bool = False

    _c = field_validator("coupon_code")(_coupon)


class CouponPreviewRequest(BaseModel):
    """What a coupon takes off this enrollment's activation (or renewal)."""

    coupon_code: Optional[str] = Field(default=None, max_length=30)
    renewal: bool = False

    _c = field_validator("coupon_code")(_coupon)


class CancelEnrollmentRequest(BaseModel):
    vehicle_ids: Optional[list[Annotated[str, Field(max_length=64)]]] = Field(default=None, max_length=10)  # None = the whole enrollment
    reason: Optional[str] = Field(default=None, max_length=300)


class SocietyPlanRequest(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    description: Optional[str] = Field(default=None, max_length=500)
    bucket_days: int = Field(ge=1, le=31)
    premium_service_id: str
    premium_count: int = Field(ge=1, le=12)
    price: float = Field(gt=0, le=100000)  # selling price (flat)
    mrp: Optional[float] = Field(default=None, gt=0, le=100000)
    vehicle_type_prices: dict[str, float] = Field(default_factory=dict)  # selling per type
    vehicle_type_mrps: dict[str, float] = Field(default_factory=dict)
    vehicle_types: list[str] = Field(default_factory=list)
    scope: Literal["template", "society", "customer"] = "template"
    society_ids: list[str] = Field(default_factory=list)
    customer_phone: Optional[str] = Field(default=None, max_length=20)
    is_active: bool = True
    display_order: int = 0

    _p = field_validator("customer_phone")(_phone)

    @model_validator(mode="after")
    def _scope(self) -> "SocietyPlanRequest":
        if self.scope == "society" and not self.society_ids:
            raise ValueError("Pick the society this plan is for")
        if self.scope == "customer" and not self.customer_phone:
            raise ValueError("Enter the customer's phone for a personal plan")
        for mapping in (self.vehicle_type_prices, self.vehicle_type_mrps):
            if any(v <= 0 for v in mapping.values()):
                raise ValueError("Prices must be above zero")
        return self


class SocietyPlanUpdateRequest(BaseModel):
    name: Optional[str] = Field(default=None, min_length=2, max_length=120)
    description: Optional[str] = Field(default=None, max_length=500)
    price: Optional[float] = Field(default=None, gt=0, le=100000)
    mrp: Optional[float] = Field(default=None, gt=0, le=100000)
    vehicle_type_prices: Optional[dict[str, float]] = None
    vehicle_type_mrps: Optional[dict[str, float]] = None
    vehicle_types: Optional[list[str]] = None
    society_ids: Optional[list[str]] = None
    is_active: Optional[bool] = None
    display_order: Optional[int] = Field(default=None, ge=-1000, le=1000)

    @field_validator("vehicle_type_prices", "vehicle_type_mrps")
    @classmethod
    def _positive(cls, v: Optional[dict[str, float]]) -> Optional[dict[str, float]]:
        # 0 clears a type's price (see update_plan); negatives/huge never.
        if v is not None and any(p < 0 or p > 100000 for p in v.values()):
            raise ValueError("Prices must be between 0 and 1,00,000")
        return v


class RateTable(BaseModel):
    default: float = Field(ge=0, le=10000)
    by_type: dict[str, float] = Field(default_factory=dict)

    @field_validator("by_type")
    @classmethod
    def _rates(cls, v: dict[str, float]) -> dict[str, float]:
        if any(r < 0 or r > 10000 for r in v.values()):
            raise ValueError("Per-day rates must be between 0 and 10,000")
        return v


class RateCardRequest(BaseModel):
    bucket_day_price: RateTable
    bucket_day_mrp: RateTable
    premium_discount_percent: float = Field(default=0, ge=0, le=90)
    bucket_day_options: list[int] = Field(min_length=1, max_length=8)
    premium_count_options: list[int] = Field(min_length=1, max_length=8)
    premium_service_ids: list[str] = Field(default_factory=list, max_length=6)
    allow_customise: bool = True

    @field_validator("bucket_day_options")
    @classmethod
    def _days(cls, v: list[int]) -> list[int]:
        if any(d < 1 or d > 31 for d in v):
            raise ValueError("Bucket-wash days must be between 1 and 31")
        return sorted(set(v))

    @field_validator("premium_count_options")
    @classmethod
    def _counts(cls, v: list[int]) -> list[int]:
        if any(c < 1 or c > 12 for c in v):
            raise ValueError("Premium washes must be between 1 and 12")
        return sorted(set(v))


# -- Bookings & attendance ----------------------------------------------------


class PremiumBookingRequest(BaseModel):
    """Book premium washes on one or more of a resident's society cars, all
    on one visit."""

    subscription_ids: list[str] = Field(min_length=1, max_length=10)
    scheduled_date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    scheduled_slot: str = Field(min_length=5, max_length=20)
    notes: Optional[str] = Field(default=None, max_length=300)


class ArriveRequest(BaseModel):
    latitude: Optional[float] = Field(default=None, ge=-90, le=90)
    longitude: Optional[float] = Field(default=None, ge=-180, le=180)
    accuracy_m: Optional[float] = Field(default=None, ge=0, le=100000)


class WashedRequest(BaseModel):
    vehicle_ids: list[str] = Field(default_factory=list, max_length=300)


# -- Resident issues -----------------------------------------------------------

SOCIETY_ISSUE_TYPES = {
    "daily_wash_missed": "Daily wash missed",
    "not_cleaned_properly": "Car not cleaned properly",
    "captain_no_show": "Captain didn't arrive",
    "premium_not_scheduled": "Premium wash not scheduled",
    "billing": "Billing / payment",
    "other": "Other",
}


class SocietyIssueRequest(BaseModel):
    """A resident's issue about their society service — becomes a support
    ticket (complaint) tagged with the society."""

    society_id: str = Field(pattern=r"^[0-9a-fA-F]{24}$")
    issue_type: Literal["daily_wash_missed", "not_cleaned_properly", "captain_no_show", "premium_not_scheduled", "billing", "other"]
    note: Optional[str] = Field(default=None, max_length=1000)
    vehicle_id: Optional[str] = Field(default=None, pattern=r"^[0-9a-fA-F]{24}$")

    @field_validator("note")
    @classmethod
    def _note(cls, v: Optional[str]) -> Optional[str]:
        v = (v or "").strip()
        return v or None

    @model_validator(mode="after")
    def _other_needs_words(self) -> "SocietyIssueRequest":
        if self.issue_type == "other" and len(self.note or "") < 5:
            raise ValueError("Tell us a little about the issue")
        return self


# -- Society requests (landing page leads) ---------------------------------------

LEAD_STATUSES = ("new", "contacted", "registered", "closed")


class SocietyLeadRequest(BaseModel):
    """PUBLIC — 'bring Blussit to our society' from the landing page."""

    society_name: str = Field(min_length=2, max_length=120)
    area: str = Field(min_length=2, max_length=120)
    pincode: str = Field(pattern=r"^\d{6}$")
    contact_name: str = Field(min_length=2, max_length=100)
    phone: str = Field(min_length=10, max_length=20)
    approx_cars: int = Field(ge=1, le=5000)
    note: Optional[str] = Field(default=None, max_length=500)

    _p = field_validator("phone")(_phone)
    _n = field_validator("society_name", "area", "contact_name", "note")(_clean)


class SocietyLeadUpdateRequest(BaseModel):
    status: Optional[Literal["new", "contacted", "registered", "closed"]] = None
    staff_note: Optional[str] = Field(default=None, max_length=500)
