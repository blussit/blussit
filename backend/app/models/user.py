from datetime import datetime
from typing import Optional

from pydantic import EmailStr, Field

from app.models.base import BusinessRecordBase
from app.models.enums import UserRole, UserStatus


class UserModel(BusinessRecordBase):
    full_name: str
    email: Optional[EmailStr] = None
    phone: Optional[str] = None
    password_hash: str
    role: UserRole = UserRole.CUSTOMER
    status: UserStatus = UserStatus.ACTIVE
    profile_image: Optional[str] = None
    gender: Optional[str] = None
    date_of_birth: Optional[datetime] = None
    referral_code: Optional[str] = None
    referred_by: Optional[str] = None
    service_center_id: Optional[str] = None  # applicable to captain/manager
    last_login_at: Optional[datetime] = None
    email_verified: bool = False
    phone_verified: bool = False
    must_change_password: bool = False  # set when staff creates the account with a temp password
