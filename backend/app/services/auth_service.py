"""
Authentication business logic. OTP delivery is real — every OTP and
password-reset temp password goes out over WhatsApp (see WhatsAppService),
with a safe log-only fallback when no WhatsApp credentials are configured
(see get_whatsapp_provider) so this all still works end-to-end in dev/test.
"""
import random
import string
from datetime import datetime, timedelta, timezone

from motor.motor_asyncio import AsyncIOMotorDatabase

from app.core.exceptions import BadRequestException, ConflictException, ForbiddenException, NotFoundException, PhoneNotVerifiedException, UnauthorizedException
from app.core.security import create_access_token, create_refresh_token, decode_token, hash_password, verify_password
from app.models.enums import UserRole, UserStatus
from app.repositories.user_repository import UserRepository
from app.schemas.user_schema import ManagerCreateCustomerRequest, RegisterRequest, StaffCreateRequest, UserPublic
from app.services.whatsapp_service import WhatsAppService


class AuthService:
    def __init__(self, db: AsyncIOMotorDatabase):
        self.db = db
        self.users = UserRepository(db)
        self.otp_store = db["otp_requests"]
        self.whatsapp = WhatsAppService(db)

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
        })
        created = await self.users.create(user_doc)
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
        created = await self.users.create(user_doc)
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
        created = await self.users.create(user_doc)
        return UserPublic.from_doc(created).model_dump()

    async def login(self, identifier: str, password: str) -> dict:
        user = await self.users.find_by_identifier(identifier)
        if not user or not verify_password(password, user["password_hash"]):
            raise UnauthorizedException("Invalid credentials")
        if user.get("status") == UserStatus.SUSPENDED.value:
            raise UnauthorizedException("Your account has been suspended. Contact support.")

        await self.users.update_by_id(str(user["_id"]), {"last_login_at": datetime.now(timezone.utc)})
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

        access_token = create_access_token(str(user["_id"]), user["role"], {"service_center_id": user.get("service_center_id")})
        new_refresh = create_refresh_token(str(user["_id"]), user["role"])
        return {"access_token": access_token, "refresh_token": new_refresh, "token_type": "bearer"}

    async def change_password(self, user_id: str, current_password: str, new_password: str) -> None:
        user = await self.users.find_by_id(user_id)
        if not user or not verify_password(current_password, user["password_hash"]):
            raise BadRequestException("Current password is incorrect")
        await self.users.update_by_id(user_id, {"password_hash": hash_password(new_password), "must_change_password": False})

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
        sent = await self.whatsapp.send_otp(phone, otp, purpose)
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
        await self.otp_store.update_one({"identifier": identifier}, {"$set": {"verified": True}})
        return True

    async def reset_password(self, identifier: str, otp: str, new_password: str) -> None:
        verified = await self.verify_otp(identifier, otp)
        if not verified:
            raise BadRequestException("Invalid or expired OTP")
        user = await self.users.find_by_identifier(identifier)
        if not user:
            raise BadRequestException("No account found for this identifier")
        await self.users.update_by_id(str(user["_id"]), {"password_hash": hash_password(new_password), "must_change_password": False})

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

    async def confirm_phone_verification(self, user_id: str, otp: str) -> dict:
        user = await self.users.find_by_id(user_id)
        if not user:
            raise NotFoundException("User not found")
        if not user.get("phone"):
            raise BadRequestException("This account has no phone number on file.")
        if not await self.verify_otp(user["phone"], otp):
            raise BadRequestException("Invalid or expired code.")
        updated = await self.users.update_by_id(user_id, {"phone_verified": True})
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
        sent = await self.whatsapp.send_temp_password(phone, temp_password)
        if not sent:
            raise BadRequestException("Password was reset, but the WhatsApp message couldn't be sent — ask the customer to use 'Forgot password' instead.")

    def _issue_tokens(self, user: dict) -> dict:
        access_token = create_access_token(
            str(user["_id"]),
            user["role"],
            {"email": user.get("email"), "phone": user.get("phone"), "service_center_id": user.get("service_center_id")},
        )
        refresh_token = create_refresh_token(str(user["_id"]), user["role"])
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
