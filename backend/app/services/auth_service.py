"""
Authentication business logic. OTP delivery is real — every OTP and
password-reset temp password goes out over WhatsApp (see WhatsAppService),
with a safe log-only fallback when no WhatsApp credentials are configured
(see get_whatsapp_provider) so this all still works end-to-end in dev/test.
"""
import random
import string
from datetime import datetime, timedelta, timezone

from bson import ObjectId
from motor.motor_asyncio import AsyncIOMotorDatabase
from pymongo.errors import DuplicateKeyError

from app.core.exceptions import BadRequestException, ConflictException, ForbiddenException, NotFoundException, PhoneNotVerifiedException, UnauthorizedException
from app.core.security import create_access_token, create_refresh_token, decode_token, hash_password, verify_password
from app.models.enums import UserRole, UserStatus
from app.repositories.user_repository import UserRepository
from app.schemas.user_schema import ManagerCreateCustomerRequest, RegisterRequest, StaffCreateRequest, UserPublic
from app.services.sms_service import SmsService
from app.services.whatsapp_service import WhatsAppService
from pymongo import ReturnDocument


async def next_captain_employee_id(db: AsyncIOMotorDatabase) -> str:
    """Atomic sequence for the public-facing captain staff id ("CAP-001").
    An atomic counter doc, not a max-scan, so two captains created at the
    same moment can never collide."""
    counter = await db.counters.find_one_and_update(
        {"_id": "captain_employee_id"},
        {"$inc": {"seq": 1}},
        upsert=True,
        return_document=ReturnDocument.AFTER,
    )
    return f"CAP-{counter['seq']:03d}"


async def assign_missing_employee_ids(db: AsyncIOMotorDatabase) -> None:
    """Startup backfill: captains created before employee_id existed get one,
    earliest account first so seniority keeps the low numbers. Idempotent —
    every run only touches captains still missing an id."""
    cursor = db.users.find(
        {"role": UserRole.CAPTAIN.value, "is_deleted": {"$ne": True}, "employee_id": None}
    ).sort("created_at", 1)
    async for captain in cursor:
        await db.users.update_one(
            {"_id": captain["_id"], "employee_id": None},
            {"$set": {"employee_id": await next_captain_employee_id(db)}},
        )


class AuthService:
    def __init__(self, db: AsyncIOMotorDatabase):
        self.db = db
        self.users = UserRepository(db)
        self.otp_store = db["otp_requests"]
        self.whatsapp = WhatsAppService(db)
        self.sms = SmsService(db)

    async def _deliver_otp(self, phone: str, otp: str, purpose: str) -> bool:
        """OTP delivery with channel fallback: try settings.OTP_CHANNEL
        first, then the other channel if the first fails or isn't
        configured. SMS disabled (the default) degrades to exactly the
        old WhatsApp-only behavior."""
        from app.core.config import settings

        order = ["sms", "whatsapp"] if settings.OTP_CHANNEL == "sms" else ["whatsapp", "sms"]
        for channel in order:
            if channel == "whatsapp":
                if await self.whatsapp.send_otp(phone, otp, purpose):
                    return True
            elif channel == "sms" and self.sms.enabled:
                if await self.sms.send_otp(phone, otp):
                    return True
        return False

    async def register_customer(self, payload: RegisterRequest) -> dict:
        if payload.email and await self.users.find_by_email(payload.email):
            raise ConflictException("An account with this email already exists")
        if payload.phone and await self.users.find_by_phone(payload.phone):
            raise ConflictException("An account with this phone number already exists")

        referral_code = self._generate_referral_code(payload.full_name)
        user_doc = self._strip_absent_contact_fields({
            "full_name": payload.full_name,
            "email": payload.email,
            "phone": payload.phone,
            "password_hash": hash_password(payload.password),
            "role": UserRole.CUSTOMER.value,
            "status": UserStatus.ACTIVE.value,
            "referral_code": referral_code,
            "referred_by": payload.referred_by,
            **({"must_change_password": True} if payload.guest else {}),
        })
        try:
            created = await self.users.create(user_doc)
        except DuplicateKeyError:
            # The existence checks above are check-then-act, not atomic — a
            # near-simultaneous second request for the same phone/email
            # (a double-tapped "Verify & book", a retried network request)
            # can both pass the check before either has inserted. Only the
            # unique index actually catches it, and an uncaught
            # DuplicateKeyError here is a raw 500 with no CORS headers
            # (Starlette's own error path, which the CORS middleware never
            # gets a chance to wrap) — the browser reports that as a
            # confusing "blocked by CORS policy" network error instead of
            # the real, simple "this account already exists".
            raise ConflictException("An account with this email or phone number already exists")
        return self._issue_tokens(created)

    async def create_staff_account(self, payload: StaffCreateRequest, created_by: str, creator_role: str = "admin") -> dict:
        if payload.role == UserRole.CUSTOMER:
            raise BadRequestException("Use the public registration endpoint for customers")
        if creator_role == "manager" and payload.role != UserRole.CAPTAIN:
            raise BadRequestException("Managers can only create captain accounts")
        if payload.email and await self.users.find_by_email(payload.email):
            raise ConflictException("An account with this email already exists")
        if payload.phone and await self.users.find_by_phone(payload.phone):
            raise ConflictException("An account with this phone number already exists")

        user_doc = self._strip_absent_contact_fields({
            "full_name": payload.full_name,
            "email": payload.email,
            "phone": payload.phone,
            "password_hash": hash_password(payload.password),
            "role": payload.role.value,
            "status": UserStatus.ACTIVE.value,
            "service_center_id": payload.service_center_id,
            "created_by": created_by,
        })
        if payload.photo_url:
            # Same field UserUpdateRequest edits and the enriched booking's
            # captain_profile falls back to (kyc.photo_url or profile_image).
            user_doc["profile_image"] = payload.photo_url
        if payload.role == UserRole.CAPTAIN:
            user_doc["employee_id"] = await next_captain_employee_id(self.db)
        try:
            created = await self.users.create(user_doc)
        except DuplicateKeyError:
            # Same check-then-act race as register_customer — see there.
            raise ConflictException("An account with this email or phone number already exists")
        return UserPublic.from_doc(created).model_dump()

    async def create_customer_by_staff(self, payload: ManagerCreateCustomerRequest, created_by: str) -> dict:
        """A manager/admin booking on behalf of a customer who doesn't have an
        account yet. Sets a temp password and must_change_password=True so the
        customer is forced through a real password change on first login —
        this account is not passwordless."""
        if payload.email and await self.users.find_by_email(payload.email):
            raise ConflictException("An account with this email already exists")
        if await self.users.find_by_phone(payload.phone):
            raise ConflictException("An account with this phone number already exists")

        referral_code = self._generate_referral_code(payload.full_name)
        user_doc = self._strip_absent_contact_fields({
            "full_name": payload.full_name,
            "email": payload.email,
            "phone": payload.phone,
            "password_hash": hash_password(payload.temp_password),
            "role": UserRole.CUSTOMER.value,
            "status": UserStatus.ACTIVE.value,
            "referral_code": referral_code,
            "must_change_password": True,
            "created_by": created_by,
        })
        try:
            created = await self.users.create(user_doc)
        except DuplicateKeyError:
            # Same check-then-act race as register_customer — see there.
            raise ConflictException("An account with this email or phone number already exists")
        return UserPublic.from_doc(created).model_dump()

    LOGIN_MAX_FAILURES = 5
    LOGIN_LOCK_MINUTES = 5
    PHONE_REVERIFY_DAYS = 90

    @staticmethod
    def phone_verification_fresh(user: dict) -> bool:
        """The 90-day rule: verification counts only while fresh. Missing
        timestamp (legacy rows are backfilled by migration) = stale."""
        if not user.get("phone_verified"):
            return False
        at = user.get("phone_verified_at")
        if at is None:
            return False
        if at.tzinfo is None:
            at = at.replace(tzinfo=timezone.utc)
        return datetime.now(timezone.utc) - at <= timedelta(days=AuthService.PHONE_REVERIFY_DAYS)

    async def login(self, identifier: str, password: str) -> dict:
        user = await self.users.find_by_identifier(identifier)
        if user and user.get("login_locked_until"):
            locked_until = user["login_locked_until"]
            if locked_until.tzinfo is None:
                locked_until = locked_until.replace(tzinfo=timezone.utc)
            if locked_until > datetime.now(timezone.utc):
                raise UnauthorizedException("Too many failed attempts. Try again in a few minutes.")
        if not user or not verify_password(password, user["password_hash"]):
            if user:
                failures = user.get("failed_login_attempts", 0) + 1
                update = {"failed_login_attempts": failures}
                if failures >= self.LOGIN_MAX_FAILURES:
                    update["login_locked_until"] = datetime.now(timezone.utc) + timedelta(minutes=self.LOGIN_LOCK_MINUTES)
                    update["failed_login_attempts"] = 0
                await self.users.update_by_id(str(user["_id"]), update)
            raise UnauthorizedException("Invalid credentials")
        if user.get("status") == UserStatus.SUSPENDED.value:
            raise UnauthorizedException("Your account has been suspended. Contact support.")

        await self.users.update_by_id(
            str(user["_id"]),
            {"last_login_at": datetime.now(timezone.utc), "failed_login_attempts": 0, "login_locked_until": None},
        )
        return self._issue_tokens(user)

    async def refresh(self, refresh_token: str) -> dict:
        try:
            payload = decode_token(refresh_token)
        except ValueError as exc:
            raise UnauthorizedException("Invalid or expired refresh token") from exc
        if payload.get("type") != "refresh":
            raise UnauthorizedException("Invalid token type")

        user = await self.users.find_by_id(payload["sub"])
        if not user:
            raise UnauthorizedException("User no longer exists")
        # A refresh is the one guaranteed DB round-trip in the token
        # lifecycle — enforce account standing here so suspension and
        # password changes actually bite within one access-token TTL.
        if user.get("status") == UserStatus.SUSPENDED.value or user.get("is_deleted"):
            raise UnauthorizedException("Your account has been suspended. Contact support.")
        if payload.get("tv", 0) != user.get("token_version", 0):
            raise UnauthorizedException("Session expired — please log in again.")

        access_token = create_access_token(
            str(user["_id"]),
            user["role"],
            {"service_center_id": user.get("service_center_id"), "tv": user.get("token_version", 0)},
        )
        new_refresh = create_refresh_token(str(user["_id"]), user["role"], token_version=user.get("token_version", 0))
        return {"access_token": access_token, "refresh_token": new_refresh, "token_type": "bearer"}

    async def logout(self, user_id: str) -> None:
        """Invalidates every refresh token the user holds (token_version
        bump) — the server-side half of logging out. Access tokens expire on
        their own within ACCESS_TOKEN_EXPIRE_MINUTES."""
        await self.users.collection.update_one({"_id": ObjectId(user_id)}, {"$inc": {"token_version": 1}})

    async def change_password(self, user_id: str, current_password: str, new_password: str) -> None:
        user = await self.users.find_by_id(user_id)
        if not user or not verify_password(current_password, user["password_hash"]):
            raise BadRequestException("Current password is incorrect")
        await self.users.update_by_id(user_id, {"password_hash": hash_password(new_password), "must_change_password": False})
        # Invalidate every refresh token issued before this change.
        await self.users.collection.update_one({"_id": ObjectId(user_id)}, {"$inc": {"token_version": 1}})

    _OTP_RESEND_COOLDOWN_SECONDS = 30
    _OTP_MAX_ATTEMPTS = 5

    async def request_otp(self, identifier: str, purpose: str = "verification") -> None:
        """Generates an OTP and sends it via WhatsApp to the phone on file
        for this identifier (email or phone both resolve to the same
        account's real phone number — WhatsApp is the only delivery
        channel this app has, so that's where every OTP goes regardless of
        which identifier the caller typed). Raises if the account has no
        phone number at all, or if a code was just sent (cooldown) — never
        returns the code itself; callers must not echo it back to the
        client (that was Phase 1's placeholder and is exactly the security
        hole real delivery closes)."""
        user = await self.users.find_by_identifier(identifier)
        if not user:
            raise NotFoundException("No account found for this identifier")
        phone = user.get("phone")
        if not phone:
            raise BadRequestException("This account has no phone number on file to send a verification code to.")

        existing = await self.otp_store.find_one({"identifier": identifier})
        now = datetime.now(timezone.utc)
        if existing and existing.get("last_sent_at"):
            last_sent = existing["last_sent_at"]
            if last_sent.tzinfo is None:
                last_sent = last_sent.replace(tzinfo=timezone.utc)
            elapsed = (now - last_sent).total_seconds()
            if elapsed < self._OTP_RESEND_COOLDOWN_SECONDS:
                raise BadRequestException(f"Please wait {int(self._OTP_RESEND_COOLDOWN_SECONDS - elapsed)}s before requesting another code.")

        otp = "".join(random.choices(string.digits, k=6))
        await self.otp_store.update_one(
            {"identifier": identifier},
            {
                "$set": {
                    "identifier": identifier,
                    "otp": otp,
                    "purpose": purpose,
                    "expires_at": now + timedelta(minutes=10),
                    "last_sent_at": now,
                    "attempts": 0,
                    "verified": False,
                }
            },
            upsert=True,
        )
        sent = await self._deliver_otp(phone, otp, purpose)
        if not sent:
            raise BadRequestException("Couldn't send the verification code — please try again in a moment.")

    async def verify_otp(self, identifier: str, otp: str) -> bool:
        record = await self.otp_store.find_one({"identifier": identifier})
        if not record:
            return False
        if record.get("attempts", 0) >= self._OTP_MAX_ATTEMPTS:
            return False
        expires_at = record["expires_at"]
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        if expires_at < datetime.now(timezone.utc):
            return False
        if record["otp"] != otp:
            await self.otp_store.update_one({"identifier": identifier}, {"$inc": {"attempts": 1}})
            return False
        # CONSUME the code — a successful verification deletes the record so
        # the same OTP can never be replayed (it used to stay valid for its
        # full 10 minutes: one observed code could reset the password, then
        # log in, then re-verify the phone). Every caller verifies exactly
        # once and acts immediately, so single-use is the correct contract.
        await self.otp_store.delete_one({"identifier": identifier, "otp": otp})
        return True

    async def reset_password(self, identifier: str, otp: str, new_password: str) -> None:
        verified = await self.verify_otp(identifier, otp)
        if not verified:
            raise BadRequestException("Invalid or expired OTP")
        user = await self.users.find_by_identifier(identifier)
        if not user:
            raise BadRequestException("No account found for this identifier")
        await self.users.update_by_id(
            str(user["_id"]),
            {
                "password_hash": hash_password(new_password),
                "must_change_password": False,
            },
        )
        # Invalidate every outstanding session/refresh token — the whole
        # point of a panic reset is locking out whoever has the old
        # credentials (change_password already did this; this path didn't).
        await self.users.collection.update_one({"_id": ObjectId(str(user["_id"]))}, {"$inc": {"token_version": 1}})

    async def request_phone_verification(self, user_id: str) -> None:
        """Gates a customer's first self-service booking/subscription
        (Section: phone verification) — same underlying OTP store as
        forgot-password, just keyed by the logged-in user's own phone
        (not an arbitrary typed-in identifier) and a distinct purpose so
        the WhatsApp message reads correctly."""
        user = await self.users.find_by_id(user_id)
        if not user:
            raise NotFoundException("User not found")
        if not user.get("phone"):
            raise BadRequestException("Add a phone number to your profile before verifying it.")
        await self.request_otp(user["phone"], purpose="verification")

    async def confirm_phone_verification_widget(self, user_id: str, access_token: str) -> dict:
        """MSG91-widget variant of phone verification: the widget already
        delivered + checked the OTP on MSG91's channels (SMS/WhatsApp/
        email); we accept only if MSG91 confirms the token AND it was
        issued for THIS user's phone."""
        from app.services.msg91_widget_service import Msg91WidgetService

        user = await self.users.find_by_id(user_id)
        if not user:
            raise NotFoundException("User not found")
        if not user.get("phone"):
            raise BadRequestException("This account has no phone number on file.")
        if not await Msg91WidgetService().verify_access_token(access_token, user["phone"]):
            raise BadRequestException("Verification could not be confirmed — please try again.")
        return await self.users.update_by_id(user_id, {"phone_verified": True, "phone_verified_at": datetime.now(timezone.utc)})

    async def reset_password_widget(self, access_token: str, phone: str, new_password: str) -> None:
        """MSG91-widget variant of forgot-password. The token must verify
        AND be bound to the given phone; only then does the password change
        (and every existing refresh token dies via token_version)."""
        from app.services.msg91_widget_service import Msg91WidgetService

        if not await Msg91WidgetService().verify_access_token(access_token, phone):
            raise BadRequestException("Verification could not be confirmed — please try again.")
        user = await self.users.find_by_phone(phone) or await self.users.find_by_identifier(phone)
        if not user:
            raise BadRequestException("No account found for this phone number")
        await self.users.update_by_id(str(user["_id"]), {"password_hash": hash_password(new_password), "must_change_password": False})
        await self.users.collection.update_one({"_id": user["_id"]}, {"$inc": {"token_version": 1}})

    async def confirm_phone_verification(self, user_id: str, otp: str) -> dict:
        user = await self.users.find_by_id(user_id)
        if not user:
            raise NotFoundException("User not found")
        if not user.get("phone"):
            raise BadRequestException("This account has no phone number on file.")
        if not await self.verify_otp(user["phone"], otp):
            raise BadRequestException("Invalid or expired code.")
        updated = await self.users.update_by_id(user_id, {"phone_verified": True, "phone_verified_at": datetime.now(timezone.utc)})
        return updated

    async def staff_reset_customer_password(self, customer_id: str, actor_id: str) -> None:
        """A manager/admin resetting a customer's forgotten password on
        their behalf. Deliberately returns nothing — the generated temp
        password is NEVER handed back in the API response, so it never
        appears in the manager's UI, network tab, or logs on their side;
        it goes straight to the customer's own WhatsApp instead. The
        customer is forced through a real password change on next login
        (must_change_password), same as any other temp-password account."""
        customer = await self.users.find_by_id(customer_id)
        if not customer:
            raise NotFoundException("Customer not found")
        if customer.get("role") != UserRole.CUSTOMER.value:
            raise ForbiddenException("This action is only for customer accounts.")
        phone = customer.get("phone")
        if not phone:
            raise BadRequestException("This customer has no phone number on file to send a temporary password to.")

        temp_password = "".join(random.choices(string.ascii_uppercase + string.ascii_lowercase + string.digits, k=10))
        await self.users.update_by_id(customer_id, {"password_hash": hash_password(temp_password), "must_change_password": True})
        await self.users.collection.update_one({"_id": ObjectId(customer_id)}, {"$inc": {"token_version": 1}})
        # Same channel-order fallback as OTPs (note: Fast2SMS's DLT-exempt
        # route can't carry free text, so its send_temp_password returns
        # False and WhatsApp handles it — MSG91-with-template or WhatsApp
        # are the real carriers for this one).
        from app.core.config import settings as _settings
        order = ["sms", "whatsapp"] if _settings.OTP_CHANNEL == "sms" else ["whatsapp", "sms"]
        sent = False
        for channel in order:
            if channel == "whatsapp":
                sent = await self.whatsapp.send_temp_password(phone, temp_password)
            elif channel == "sms" and self.sms.enabled:
                sent = await self.sms.send_temp_password(phone, temp_password)
            if sent:
                break
        if not sent:
            raise BadRequestException("Password was reset, but the message couldn't be sent — ask the customer to use 'Forgot password' instead.")

    async def booking_access_mode(self, phone: str) -> dict:
        """What should the guest wizard do for this phone?
        - "register": no account -> normal silent registration path.
        - "otp": account exists but verification is missing/stale (the
          abandoned-signup case, or 90-day expiry) -> prove ownership by
          OTP, never by a password that may have never been set.
        - "password": account exists with fresh verification -> log in.
        Reveals no more than the register endpoint's 409 already does."""
        from app.utils.phone import validate_indian_mobile

        normalized = validate_indian_mobile(phone)
        if not normalized:
            raise BadRequestException("Enter a valid 10-digit mobile number")
        user = await self.users.find_by_phone(normalized)
        if not user or user.get("is_deleted"):
            return {"mode": "register"}
        if user.get("role") != UserRole.CUSTOMER.value:
            return {"mode": "password"}
        return {"mode": "password" if self.phone_verification_fresh(user) else "otp"}

    async def otp_login(self, phone: str, otp: str | None = None, widget_access_token: str | None = None) -> dict:
        """Logs a CUSTOMER in by proving phone ownership (classic OTP or
        MSG91 widget token) — the recovery path for accounts whose
        password was never really set (abandoned guest signup, WhatsApp
        auto-created) and the 90-day re-verification path. Marks the
        phone freshly verified. must_change_password is preserved, so the
        mandatory set-a-password gate still catches unknown-password
        accounts afterwards."""
        from app.utils.phone import validate_indian_mobile

        normalized = validate_indian_mobile(phone)
        if not normalized:
            raise BadRequestException("Enter a valid 10-digit mobile number")
        user = await self.users.find_by_phone(normalized)
        if not user or user.get("is_deleted") or user.get("role") != UserRole.CUSTOMER.value:
            raise BadRequestException("No customer account found for this phone number")
        if user.get("status") == UserStatus.SUSPENDED.value:
            raise UnauthorizedException("Your account has been suspended. Contact support.")

        verified = False
        if widget_access_token:
            from app.services.msg91_widget_service import Msg91WidgetService

            verified = await Msg91WidgetService().verify_access_token(widget_access_token, normalized)
        elif otp:
            verified = await self.verify_otp(normalized, otp)
        if not verified:
            raise BadRequestException("Invalid or expired code.")

        await self.users.update_by_id(
            str(user["_id"]),
            {"phone_verified": True, "phone_verified_at": datetime.now(timezone.utc), "last_login_at": datetime.now(timezone.utc)},
        )
        fresh = await self.users.find_by_id(str(user["_id"]))
        return self._issue_tokens(fresh)

    async def add_phone_request(self, user_id: str, phone: str) -> None:
        """Step 1 of attaching a phone to a phoneless account (Google
        sign-ins): validate + uniqueness-check the number, then send an
        OTP to it. The OTP is keyed by the NEW phone in the same otp_store
        the rest of auth uses (cooldown included)."""
        from app.utils.phone import validate_indian_mobile

        normalized = validate_indian_mobile(phone)
        if not normalized:
            raise BadRequestException("Enter a valid 10-digit Indian mobile number")
        user = await self.users.find_by_id(user_id)
        if not user:
            raise NotFoundException("User not found")
        taken = await self.users.find_by_phone(normalized)
        if taken and str(taken["_id"]) != user_id:
            raise BadRequestException("This phone number is already used by another account.")

        existing = await self.otp_store.find_one({"identifier": normalized})
        now = datetime.now(timezone.utc)
        if existing and existing.get("last_sent_at"):
            last_sent = existing["last_sent_at"]
            if last_sent.tzinfo is None:
                last_sent = last_sent.replace(tzinfo=timezone.utc)
            if (now - last_sent).total_seconds() < self._OTP_RESEND_COOLDOWN_SECONDS:
                raise BadRequestException("Please wait a moment before requesting another code.")
        otp = "".join(random.choices(string.digits, k=6))
        await self.otp_store.update_one(
            {"identifier": normalized},
            {"$set": {"otp": otp, "expires_at": now + timedelta(minutes=10), "attempts": 0, "last_sent_at": now, "purpose": "add_phone"}},
            upsert=True,
        )
        await self._deliver_otp(normalized, otp, "verification")

    async def add_phone_confirm(self, user_id: str, phone: str, otp: str | None = None, widget_access_token: str | None = None) -> dict:
        """Step 2: prove ownership (classic OTP or MSG91 widget token bound
        to this exact number), then attach it as the account's verified
        primary contact."""
        from app.utils.phone import validate_indian_mobile

        normalized = validate_indian_mobile(phone)
        if not normalized:
            raise BadRequestException("Enter a valid 10-digit Indian mobile number")
        user = await self.users.find_by_id(user_id)
        if not user:
            raise NotFoundException("User not found")
        taken = await self.users.find_by_phone(normalized)
        if taken and str(taken["_id"]) != user_id:
            raise BadRequestException("This phone number is already used by another account.")

        verified = False
        if widget_access_token:
            from app.services.msg91_widget_service import Msg91WidgetService

            verified = await Msg91WidgetService().verify_access_token(widget_access_token, normalized)
        elif otp:
            verified = await self.verify_otp(normalized, otp)
        if not verified:
            raise BadRequestException("Invalid or expired code.")
        return await self.users.update_by_id(
            user_id,
            {"phone": normalized, "phone_verified": True, "phone_verified_at": datetime.now(timezone.utc)},
        )

    async def set_initial_password(self, user_id: str, new_password: str) -> None:
        """Password setup WITHOUT the current password — allowed only while
        must_change_password is set (temp-password logins and OTP-logins,
        which already proved identity). Clears the flag; keeps this
        session alive (no token_version bump — it's the same person)."""
        user = await self.users.find_by_id(user_id)
        if not user:
            raise NotFoundException("User not found")
        if not user.get("must_change_password"):
            raise BadRequestException("Use the normal change-password option (current password required).")
        await self.users.update_by_id(user_id, {"password_hash": hash_password(new_password), "must_change_password": False})

    def _issue_tokens(self, user: dict) -> dict:
        access_token = create_access_token(
            str(user["_id"]),
            user["role"],
            {
                "email": user.get("email"),
                "phone": user.get("phone"),
                "service_center_id": user.get("service_center_id"),
                "tv": user.get("token_version", 0),
            },
        )
        refresh_token = create_refresh_token(str(user["_id"]), user["role"], token_version=user.get("token_version", 0))
        return {
            "access_token": access_token,
            "refresh_token": refresh_token,
            "token_type": "bearer",
            "user": UserPublic.from_doc(user).model_dump(),
        }

    @staticmethod
    def _strip_absent_contact_fields(user_doc: dict) -> dict:
        """email/phone both carry a unique+sparse index — sparse only
        excludes documents where the field is entirely ABSENT, not ones
        where it's present with value None. Writing "email": None on every
        phone-only account collides on the second such account
        (E11000 duplicate key on {email: null}). Drop the key outright
        instead of writing None, so a document with no email is genuinely
        absent from that index, as sparse expects."""
        return {k: v for k, v in user_doc.items() if not (k in ("email", "phone") and v is None)}

    @staticmethod
    def _generate_referral_code(full_name: str) -> str:
        prefix = "".join(ch for ch in full_name.upper() if ch.isalpha())[:4] or "USER"
        suffix = "".join(random.choices(string.digits, k=4))
        return f"{prefix}{suffix}"
