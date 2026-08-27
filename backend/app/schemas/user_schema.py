from datetime import datetime
from typing import Optional

from pydantic import BaseModel, EmailStr, Field, field_validator

from app.models.enums import UserRole, UserStatus
from app.utils.timezone import from_stored


class RegisterRequest(BaseModel):
    full_name: str = Field(min_length=2, max_length=100)
    email: Optional[EmailStr] = None
    phone: Optional[str] = Field(default=None, min_length=10, max_length=15)
    password: str = Field(min_length=8, max_length=72)
    referred_by: Optional[str] = None

    @field_validator("phone")
    @classmethod
    def validate_contact(cls, v, info):
        return v

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
    """Used by admin to create captain/manager accounts."""
    full_name: str = Field(min_length=2, max_length=100)
    email: Optional[EmailStr] = None
    phone: Optional[str] = None
    password: str = Field(min_length=8, max_length=72)
    role: UserRole
    service_center_id: Optional[str] = None


class ManagerCreateCustomerRequest(BaseModel):
    """Used by a manager/admin to create a customer account on their behalf
    (e.g. a phone/walk-in booking) with a temp password the customer must
    change on first login — see UserModel.must_change_password."""
    full_name: str = Field(min_length=2, max_length=100)
    email: Optional[EmailStr] = None
    phone: str = Field(min_length=10, max_length=15)
    temp_password: str = Field(min_length=8, max_length=72)


class UserUpdateRequest(BaseModel):
    full_name: Optional[str] = None
    gender: Optional[str] = None
    date_of_birth: Optional[datetime] = None
    profile_image: Optional[str] = None


class AdminUserUpdateRequest(BaseModel):
    full_name: Optional[str] = None
    status: Optional[UserStatus] = None
    service_center_id: Optional[str] = None
    role: Optional[UserRole] = None


class UserPublic(BaseModel):
    id: str
    full_name: str
    email: Optional[str] = None
    phone: Optional[str] = None
    role: UserRole
    status: UserStatus
    profile_image: Optional[str] = None
    service_center_id: Optional[str] = None
    must_change_password: bool = False
    phone_verified: bool = False
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
            must_change_password=doc.get("must_change_password", False),
            phone_verified=doc.get("phone_verified", False),
            # created_at is a computed timestamp (aware at write time) — Mongo
            # hands it back naive-holding-UTC-digits, so it must go through
            # from_stored() here too. This bypasses serialize_doc entirely
            # (it's built straight from the raw doc), so it needs its own fix
            # — see app/utils/serializers.py's COMPUTED_INSTANT_KEYS for the
            # equivalent handling on every other response path.
            created_at=from_stored(doc["created_at"]) if doc.get("created_at") else doc.get("created_at"),
        )
