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
    # Auto-assigned staff id for captains ("CAP-001", ...) — atomic counter
    # in db.counters, assigned at creation (and backfilled at startup for
    # captains that predate the field). This is the public-facing identity a
    # customer sees when the captain is assigned to their booking.
    employee_id: Optional[str] = None
    last_login_at: Optional[datetime] = None
    # Piggybacked off the real GPS captures that already happen during a
    # captain's job (start_heading, before/after photos) — not a separate
    # continuous location-tracking system (none exists in this app), so
    # this is "as of their last active job action", not truly live. See
    # BookingService's heading/photo capture methods for where it's set,
    # and StaffDirectoryService.eligible_captains_for_booking for how a
    # manager sees it at assignment time.
    last_known_location: Optional[dict] = None  # {"latitude": float, "longitude": float}
    last_location_at: Optional[datetime] = None
    email_verified: bool = False
    phone_verified: bool = False
    must_change_password: bool = False  # set when staff creates the account with a temp password
    # Captain KYC / background verification — submitted by the captain from
    # their profile, reviewed by their CENTER MANAGER (verified/rejected +
    # note). Shape: {photo_url, aadhaar_number, pan_number, aadhaar_doc_url,
    # pan_doc_url, local_address, permanent_address, same_as_local,
    # status: "pending"|"submitted"|"verified"|"rejected",
    # submitted_at, reviewed_by, reviewed_at, review_note}.
    # Aadhaar/PAN are shown MASKED (last 4) everywhere except the manager's
    # review screen and the captain's own profile — see StaffKycService.
    captain_kyc: Optional[dict] = None
