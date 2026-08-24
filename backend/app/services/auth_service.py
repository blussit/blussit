"""
Authentication business logic. OTP delivery is a placeholder in Phase 1
(no SMS/WhatsApp provider is wired up yet) — `_generate_otp` simply
stores a fixed-format OTP against the identifier so the flow can be
tested end-to-end and swapped for a real provider later without
touching the API surface.
"""
import random
import string
from datetime import datetime, timedelta, timezone

from motor.motor_asyncio import AsyncIOMotorDatabase

from app.core.exceptions import BadRequestException, ConflictException, UnauthorizedException
from app.core.security import create_access_token, create_refresh_token, decode_token, hash_password, verify_password
from app.models.enums import UserRole, UserStatus
from app.repositories.user_repository import UserRepository
from app.schemas.user_schema import ManagerCreateCustomerRequest, RegisterRequest, StaffCreateRequest, UserPublic


class AuthService:
    def __init__(self, db: AsyncIOMotorDatabase):
        self.db = db
        self.users = UserRepository(db)
        self.otp_store = db["otp_requests"]

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

    async def request_otp(self, identifier: str) -> str:
        """Phase 1 placeholder: generates and stores an OTP, to be delivered
        via SMS/WhatsApp once those integrations land in Phase 2."""
        otp = "".join(random.choices(string.digits, k=6))
        await self.otp_store.update_one(
            {"identifier": identifier},
            {
                "$set": {
                    "identifier": identifier,
                    "otp": otp,
                    "expires_at": datetime.now(timezone.utc) + timedelta(minutes=10),
                    "verified": False,
                }
            },
            upsert=True,
        )
        return otp

    async def verify_otp(self, identifier: str, otp: str) -> bool:
        record = await self.otp_store.find_one({"identifier": identifier})
        if not record or record["otp"] != otp:
            return False
        if record["expires_at"].replace(tzinfo=timezone.utc) < datetime.now(timezone.utc):
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
