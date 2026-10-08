from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator, model_validator

from app.models.enums import UserRole, UserStatus
from app.utils.timezone import from_stored


class RegisterRequest(BaseModel):
    full_name: str = Field(min_length=2, max_length=100)
    email: Optional[EmailStr] = None
    phone: Optional[str] = Field(default=None, min_length=10, max_length=15)
    password: str = Field(min_length=8, max_length=72)
    referred_by: Optional[str] = None
    # Guest-wizard silent registration: the password is random and unknown
    # to the person, so the mandatory set-a-password gate must catch them.
    guest: bool = False
    # Proof the phone is the registrant's — one of the two is required.
    phone_otp: Optional[str] = Field(default=None, max_length=10)
    phone_access_token: Optional[str] = Field(default=None, max_length=4096)

    @field_validator("phone")
    @classmethod
    def validate_contact(cls, v, info):
        if v is None:
            return v
        from app.utils.phone import validate_indian_mobile

        phone = validate_indian_mobile(v)
        if not phone:
            raise ValueError("Enter a valid 10-digit Indian mobile number (starts with 6-9)")
        return phone

    @field_validator("email")
    @classmethod
    def validate_email_domain(cls, v):
        # Customer sign-ups are limited to mainstream consumer providers
        # (see app/utils/email_domains.py for the why and the list).
        from app.utils.email_domains import validate_consumer_email

        return validate_consumer_email(v)

    def model_post_init(self, __context) -> None:
        if not self.email and not self.phone:
            raise ValueError("Either email or phone is required")


class LoginRequest(BaseModel):
    identifier: str = Field(description="Email or phone number")
    password: str


class RequestOtpRequest(BaseModel):
    identifier: str = Field(description="Email or phone number to send OTP placeholder to")


class VerifyOtpRequest(BaseModel):
    identifier: str
    otp: str = Field(min_length=4, max_length=6)


class ConfirmPhoneVerificationRequest(BaseModel):
    """Used by the LOGGED-IN user's own phone-verification step (gates
    their first self-service booking/subscription) — no identifier field,
    since it always verifies current_user's own phone, never an arbitrary
    one someone else typed in."""
    otp: str = Field(min_length=4, max_length=6)


class ForgotPasswordRequest(BaseModel):
    identifier: str


class ResetPasswordRequest(BaseModel):
    identifier: str
    otp: str
    new_password: str = Field(min_length=8, max_length=72)


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str = Field(min_length=8, max_length=72)


class RefreshTokenRequest(BaseModel):
    refresh_token: str


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class StaffCreateRequest(BaseModel):
    """Used by admin/manager to create captain/manager accounts."""
    full_name: str = Field(min_length=2, max_length=100)
    email: Optional[EmailStr] = None
    phone: Optional[str] = None
    password: str = Field(min_length=8, max_length=72)
    role: UserRole
    service_center_id: Optional[str] = None
    # Profile picture, uploaded ahead via POST /uploads/photo — for captains
    # this is what customers see on their booking's "Your captain" card.
    photo_url: Optional[str] = Field(default=None, max_length=500)

    @field_validator("email")
    @classmethod
    def lower_email(cls, v):
        # Emails are stored lower-case, so login works however it's typed.
        return v.strip().lower() if v else v

    @field_validator("phone")
    @classmethod
    def validate_phone(cls, v):
        if v is None:
            return v
        from app.utils.phone import validate_indian_mobile

        phone = validate_indian_mobile(v)
        if not phone:
            raise ValueError("Enter a valid 10-digit Indian mobile number (starts with 6-9)")
        return phone

    @model_validator(mode="after")
    def captain_needs_phone(self):
        # Job assignment, urgent-issue pings and the customer's "call your
        # captain" button all go through WhatsApp/phone — a captain account
        # without a number is unreachable in the field, so it's mandatory.
        if self.role == UserRole.CAPTAIN and not self.phone:
            raise ValueError("A captain account needs a phone number — job alerts and customer contact depend on it")
        return self


class StaffPasswordResetRequest(BaseModel):
    """Admin (any staff) / manager (own center's captains) setting a
    temporary password for a staff member who can't reset by code — the
    same shape as StaffCreateRequest.password: typed by the admin, handed
    over privately, replaced by the staff member at the next login."""
    temp_password: str = Field(min_length=8, max_length=72)


class ManagerCreateCustomerRequest(BaseModel):
    """Used by a manager/admin to create a customer account on their behalf
    (e.g. a phone/walk-in booking) with a temp password the customer must
    change on first login — see UserModel.must_change_password."""
    full_name: str = Field(min_length=2, max_length=100)
    email: Optional[EmailStr] = None
    phone: str = Field(min_length=10, max_length=15)
    temp_password: str = Field(min_length=8, max_length=72)

    @field_validator("phone")
    @classmethod
    def _valid_phone(cls, v: str) -> str:
        from app.utils.phone import validate_indian_mobile

        phone = validate_indian_mobile(v)
        if not phone:
            raise ValueError("Enter a valid 10-digit Indian mobile number (starts with 6-9)")
        return phone

    @field_validator("email")
    @classmethod
    def _valid_email_domain(cls, v):
        from app.utils.email_domains import validate_consumer_email

        return validate_consumer_email(v)


class UserUpdateRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    full_name: Optional[str] = Field(None, min_length=2, max_length=100)
    profile_image: Optional[str] = Field(None, max_length=1000, pattern=r"^(https?://|/)\S+$")
    # The "WhatsApp reminders & offers" switch (true = opted out). Stops the
    # marketing-template WhatsApps — "time for a wash?", "N washes left on
    # your pass" — never booking/payment updates or in-app notices.
    marketing_opt_out: Optional[bool] = None


class AdminUserUpdateRequest(BaseModel):
    full_name: Optional[str] = None
    status: Optional[UserStatus] = None
    service_center_id: Optional[str] = None
    role: Optional[UserRole] = None
    # Contact number an admin can correct (staff are created with one and
    # had no way to change it). Blank/None leaves the current number as is.
    phone: Optional[str] = None

    @field_validator("phone")
    @classmethod
    def validate_phone(cls, v):
        if v is None or not str(v).strip():
            return None
        from app.utils.phone import validate_indian_mobile

        phone = validate_indian_mobile(v)
        if not phone:
            raise ValueError("Enter a valid 10-digit Indian mobile number (starts with 6-9)")
        return phone


def _verification_stale(doc: dict) -> bool:
    """90-day OTP freshness (see AuthService.PHONE_REVERIFY_DAYS): stale
    when never verified, or verified with no/old timestamp."""
    from datetime import datetime as _dt, timedelta as _td, timezone as _tz

    if not doc.get("phone_verified"):
        return True
    at = doc.get("phone_verified_at")
    if at is None:
        return True
    if at.tzinfo is None:
        at = at.replace(tzinfo=_tz.utc)
    return _dt.now(_tz.utc) - at > _td(days=90)


class UserPublic(BaseModel):
    id: str
    full_name: str
    email: Optional[str] = None
    phone: Optional[str] = None
    role: UserRole
    status: UserStatus
    profile_image: Optional[str] = None
    service_center_id: Optional[str] = None
    # Auto-assigned staff id for captains (CAP-001, ...) — the public-facing
    # identity shared with a customer when this captain is on their booking.
    employee_id: Optional[str] = None
    must_change_password: bool = False
    phone_verified: bool = False
    # True when phone_verified is absent OR older than the 90-day window —
    # the frontend re-runs the OTP gate on this.
    phone_verification_stale: bool = True
    # The account holder proved THIS phone themselves while signed in. For
    # staff it decides whether "Forgot password" by code works (it doesn't
    # until they verify — AuthService._ensure_code_reset_allowed); the
    # profile page offers "Verify" when it's false.
    phone_self_verified: bool = False
    # See UserUpdateRequest.marketing_opt_out.
    marketing_opt_out: bool = False
    # Managers: "WhatsApp me new bookings" (on unless switched off — see
    # PUT /notifications/preferences). In-app alerts are unaffected.
    whatsapp_new_booking_alerts: bool = True
    created_at: datetime

    @classmethod
    def from_doc(cls, doc: dict) -> "UserPublic":
        return cls(
            id=str(doc["_id"]),
            full_name=doc.get("full_name", ""),
            email=doc.get("email"),
            phone=doc.get("phone"),
            role=doc.get("role", "customer"),
            status=doc.get("status", "active"),
            profile_image=doc.get("profile_image"),
            service_center_id=doc.get("service_center_id"),
            employee_id=doc.get("employee_id"),
            must_change_password=doc.get("must_change_password", False),
            phone_verified=doc.get("phone_verified", False),
            phone_verification_stale=_verification_stale(doc),
            phone_self_verified=bool(doc.get("phone") and doc.get("phone_verified") and doc.get("self_verified_phone") == doc.get("phone")),
            marketing_opt_out=bool(doc.get("marketing_opt_out", False)),
            whatsapp_new_booking_alerts=doc.get("whatsapp_new_booking_alerts") is not False,
            # created_at is a computed timestamp (aware at write time) — Mongo
            # hands it back naive-holding-UTC-digits, so it must go through
            # from_stored() here too. This bypasses serialize_doc entirely
            # (it's built straight from the raw doc), so it needs its own fix
            # — see app/utils/serializers.py's COMPUTED_INSTANT_KEYS for the
            # equivalent handling on every other response path.
            created_at=from_stored(doc["created_at"]) if doc.get("created_at") else doc.get("created_at"),
        )
