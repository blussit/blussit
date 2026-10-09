"""
Authentication business logic. Backend OTPs go out over WhatsApp (approved
template, or free text inside an open 24h chat) or SMS (SMS_PROVIDER);
when neither can reach a number the request is refused and the browser
sends the code through the MSG91 widget instead (see Msg91WidgetService).
Dev/test use the log-only providers.
"""
import hashlib
import logging
import math
import random
import secrets
import string
from datetime import datetime, timedelta, timezone

from bson import ObjectId
from motor.motor_asyncio import AsyncIOMotorDatabase
from pymongo.errors import DuplicateKeyError

from app.core.authz import account_switched_off
from app.core.config import settings
from app.core.exceptions import (
    BadRequestException,
    ConflictException,
    ForbiddenException,
    NotFoundException,
    PhoneNotVerifiedException,
    StaffResetRefusedException,
    UnauthorizedException,
)
from app.core.rate_limit import current_requester
from app.core.security import create_access_token, create_refresh_token, decode_token, hash_password_async, verify_password_async, verify_password_or_dummy_async
from app.models.enums import UserRole, UserStatus
from app.repositories.user_repository import UserRepository
from app.schemas.user_schema import ManagerCreateCustomerRequest, RegisterRequest, StaffCreateRequest, UserPublic
from app.services.sms_service import SmsService
from app.services.whatsapp_service import WhatsAppService
from app.utils.phone import BUSINESS_NUMBER_MESSAGE, is_business_whatsapp_number, validate_indian_mobile
from pymongo import ReturnDocument

logger = logging.getLogger(__name__)


class StaffAccountPhoneError(BadRequestException):
    """A staff (admin / manager / captain) account's phone reached a path
    that treats a phone as a CUSTOMER's identity — a by-phone booking, a
    society enrolment, a WhatsApp chat. Staff phones are typed in by an
    admin or manager, so such a path must never adopt the account, mark the
    phone proven, wipe its password or end its sessions. A distinct
    error_code lets callers (the WhatsApp bot) answer "this number belongs
    to a staff account" instead of a generic failure."""
    error_code = "STAFF_ACCOUNT_PHONE"

    def __init__(self, message: str = "This number belongs to a staff account — use a customer number.", details: dict | None = None):
        super().__init__(message, details)


class AccountInactiveException(BadRequestException):
    """The account behind a phone typed into a booking / enrolment is
    switched off (suspended or inactive). A 400 with the reason, never a
    401: the CALLER's own session is fine — a 401 made the app log them out
    instead of showing why the booking was refused (FE-13). 400 rather than
    403 matches the manager's log-a-job path, which already answers 400."""
    error_code = "ACCOUNT_INACTIVE"


# What each OTP is FOR, stored with the code (AUTH-04). "verification" is
# the signed-in/login family: /auth/otp/request (OTP login),
# /auth/verify-phone/request (verify my own phone), /auth/add-phone/request.
# "booking_confirmation" is the public, account-less popup code
# (/bookings/verify-phone/request). "password_reset" comes only from
# /auth/forgot-password, after the reset rules (_ensure_code_reset_allowed).
OTP_LOGIN = "verification"
OTP_BOOKING = "booking_confirmation"
OTP_PASSWORD_RESET = "password_reset"

# Which stored purposes each consumer accepts. A booking code reaches any
# number with no account checks at all, so it may prove "I hold this phone"
# for login / booking / enrolment / registration — never reset a password.
# A reset code does one thing only. Self-verification (which unlocks a
# staff member's code reset) takes only a code requested while signed in.
OTP_ACCEPTS: dict[str, frozenset[str]] = {
    "password_reset": frozenset({OTP_PASSWORD_RESET}),
    "login": frozenset({OTP_LOGIN, OTP_BOOKING}),
    "phone_proof": frozenset({OTP_BOOKING, OTP_LOGIN}),      # quick booking, society enrolment, register
    "phone_verification": frozenset({OTP_LOGIN}),            # signed-in verify-my-phone / add-phone
}

STAFF_CODE_RESET_REFUSED = (
    "This is a staff account and its phone number hasn't been verified by you yet — "
    "ask your admin to reset your password."
)


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

    async def _whatsapp_can_reach(self, phone: str) -> bool:
        """Can a WhatsApp OTP actually LAND for this number right now? With an
        approved OTP template configured, always. Without one, only a
        free-text message can be sent, and Meta silently drops free text
        outside an open 24h customer-service window — the send API still
        says 200, so the only way to know is to check for a recent inbound
        message from that number ourselves. Saying "no" here is what lets
        the caller fall back to SMS immediately instead of after a code
        that never arrives."""
        from app.core.config import settings
        from app.services.whatsapp_service import LogWhatsAppProvider

        if isinstance(self.whatsapp.provider, LogWhatsAppProvider):
            # "log" on purpose (tests / local) "delivers" everything; meta_cloud
            # with a missing token/phone id silently falls back to the log
            # provider, and an OTP "sent" there never reaches anyone.
            return settings.WHATSAPP_PROVIDER == "log"
        if await self.whatsapp.otp_template_name():
            return True
        since = datetime.now(timezone.utc) - timedelta(hours=23, minutes=30)
        convo = await self.db.whatsapp_conversations.find_one(
            {"$or": [{"phone": phone}, {"wa_id": f"91{phone}"}, {"wa_id": phone}], "last_inbound_at": {"$gte": since}}
        )
        return convo is not None

    async def _otp_channels(self, phone: str) -> list[str]:
        """The channels that can plausibly deliver a code to this phone right
        now, in try-order: WhatsApp first, SMS second (settings.OTP_CHANNEL=
        "sms" flips it). Empty = the backend can't send at all — the caller
        refuses before touching the cooldown or the hourly cap, and the
        client falls back to the MSG91 widget (SMS)."""
        from app.core.config import settings

        order = ["sms", "whatsapp"] if settings.OTP_CHANNEL == "sms" else ["whatsapp", "sms"]
        channels = []
        for channel in order:
            if channel == "whatsapp" and await self._whatsapp_can_reach(phone):
                channels.append(channel)
            elif channel == "sms" and self.sms.enabled:
                channels.append(channel)
        return channels

    async def _deliver_otp(self, phone: str, otp: str, purpose: str, channels: list[str]) -> str | None:
        """Tries each channel in order; returns the one that accepted the code."""
        for channel in channels:
            try:
                if channel == "whatsapp" and await self.whatsapp.send_otp(phone, otp, purpose):
                    return channel
                if channel == "sms" and await self.sms.send_otp(phone, otp):
                    return channel
            except Exception:  # noqa: BLE001 — one broken channel must not stop the fallback
                logger.exception("OTP delivery via %s raised for …%s", channel, phone[-4:])
        return None

    async def register_customer(self, payload: RegisterRequest) -> dict:
        # A password account on a number nobody proved was the way to take
        # over a stranger's future account: their OTP bookings and logins
        # adopted it, and the planted password kept working. The phone must
        # be proven first — the same OTP / widget proof a booking uses.
        if not payload.phone:
            raise BadRequestException("Enter your mobile number to create an account")
        if payload.email and await self.users.find_by_email(payload.email):
            raise ConflictException("An account with this email already exists")
        if await self.users.find_by_phone(payload.phone):
            raise ConflictException("An account with this phone number already exists")
        await self.require_phone_proof(payload.phone, payload.phone_otp, payload.phone_access_token)

        referral_code = self._generate_referral_code(payload.full_name)
        user_doc = self._strip_absent_contact_fields({
            "full_name": payload.full_name,
            "email": payload.email,
            "phone": payload.phone,
            "password_hash": await hash_password_async(payload.password),
            "role": UserRole.CUSTOMER.value,
            "status": UserStatus.ACTIVE.value,
            "referral_code": referral_code,
            "referred_by": payload.referred_by,
            "phone_verified": True,
            "phone_verified_at": datetime.now(timezone.utc),
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
        if payload.phone and is_business_whatsapp_number(payload.phone):
            raise BadRequestException(BUSINESS_NUMBER_MESSAGE)
        if payload.phone and await self.users.find_by_phone(payload.phone):
            raise ConflictException("An account with this phone number already exists")
        if payload.service_center_id:
            await self._ensure_live_center(payload.service_center_id)

        user_doc = self._strip_absent_contact_fields({
            "full_name": payload.full_name,
            "email": payload.email,
            "phone": payload.phone,
            "password_hash": await hash_password_async(payload.password),
            "role": payload.role.value,
            "status": UserStatus.ACTIVE.value,
            "service_center_id": payload.service_center_id,
            "created_by": created_by,
            # The creator chose (and knows) this password — like a
            # temp-password customer, the new captain/manager must replace
            # it on first login (MandatoryGates in every portal shell).
            "must_change_password": True,
            # Typed in by the creator, never proven: the staff member
            # verifies it themselves (signed in) before a code can reset
            # their password — see _ensure_code_reset_allowed.
            "phone_verified": False,
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

    async def _ensure_live_center(self, center_id: str) -> None:
        """A staff account may only be attached to a center that exists and
        is switched on — an unknown id made an account no center screen
        ever lists, an inactive one a manager of nothing (ADM-02)."""
        center = await self.db.service_centers.find_one(
            {"_id": ObjectId(center_id), "is_deleted": {"$ne": True}}, {"is_active": 1}
        ) if ObjectId.is_valid(center_id) else None
        if not center:
            raise BadRequestException("That service center doesn't exist.")
        if center.get("is_active") is False:
            raise BadRequestException("That service center is switched off — switch it on before adding staff to it.")

    async def create_customer_by_staff(self, payload: ManagerCreateCustomerRequest, created_by: str) -> dict:
        """A manager/admin booking on behalf of a customer who doesn't have an
        account yet. Sets a temp password and must_change_password=True so the
        customer is forced through a real password change on first login —
        this account is not passwordless."""
        if payload.email and await self.users.find_by_email(payload.email):
            raise ConflictException("An account with this email already exists")
        if is_business_whatsapp_number(payload.phone):
            raise BadRequestException(BUSINESS_NUMBER_MESSAGE)
        if await self.users.find_by_phone(payload.phone):
            raise ConflictException("An account with this phone number already exists")

        referral_code = self._generate_referral_code(payload.full_name)
        user_doc = self._strip_absent_contact_fields({
            "full_name": payload.full_name,
            "email": payload.email,
            "phone": payload.phone,
            "password_hash": await hash_password_async(payload.temp_password),
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

    async def ensure_customer_by_phone(self, phone: str, full_name: str, *, create: bool = True) -> dict | None:
        """Quick-booking model (2026-09): a booking needs no account up front.
        The customer profile is found by phone, or created silently from
        the name + phone typed into the booking — no password (customers
        log in by OTP only), no must_change_password gate, no OTP before
        booking. Returns the raw user doc.

        `create=False` (a read-only preview): the same refusals for an
        existing number, but nothing is written — None for a new number."""
        from app.utils.phone import validate_indian_mobile

        normalized = validate_indian_mobile(phone)
        if not normalized:
            raise BadRequestException("Enter a valid 10-digit mobile number")
        name = " ".join((full_name or "").split())[:100]
        user = await self.users.find_by_phone(normalized)
        if user:
            if user.get("is_deleted"):
                raise BadRequestException("This number can't be used to book — please contact support.")
            if user.get("role") != UserRole.CUSTOMER.value:
                raise StaffAccountPhoneError("This number belongs to a staff account — use a customer number to book.")
            standing = account_switched_off(user)
            if standing:
                # 403 with the reason — a 401 here read as "your session
                # expired" and bounced the person booking to /login (FE-13).
                raise AccountInactiveException(f"The account for this number is {user['status']} — contact support.")
            # Fill in a name the profile never had (WhatsApp/quick accounts
            # start with just a phone); never overwrite one the customer set.
            if create and name and not (user.get("full_name") or "").strip():
                await self.users.update_by_id(str(user["_id"]), {"full_name": name})
                user["full_name"] = name
            return user
        if not create:
            return None

        user_doc = self._strip_absent_contact_fields({
            "full_name": name or "Customer",
            "phone": normalized,
            # No password at all: customers sign in with a phone OTP.
            "password_hash": None,
            "role": UserRole.CUSTOMER.value,
            "status": UserStatus.ACTIVE.value,
            "referral_code": self._generate_referral_code(name or "USER"),
            "phone_verified": False,
            "account_source": "quick_booking",
        })
        try:
            return await self.users.create(user_doc)
        except DuplicateKeyError:
            # Two near-simultaneous bookings from the same new number — the
            # loser of the race just picks up the account the winner made.
            existing = await self.users.find_by_phone(normalized)
            if existing:
                return existing
            raise ConflictException("An account with this phone number already exists")

    async def mark_phone_proven(self, user: dict, **extra) -> dict:
        """Records that whoever is acting just proved they own this
        account's phone (OTP, widget token, or messaging us from it). The
        FIRST proof on an account also revokes everything set up on it
        without that proof — a password chosen before the number was ever
        verified (anyone could register a stranger's number) and every
        session issued so far — so the real owner takes the account over
        cleanly instead of sharing it. Returns the fresh user doc.

        CUSTOMER accounts only. A staff phone was typed in by an admin or
        manager: messaging the business WhatsApp from it (or typing it into
        a booking) must not adopt, "prove", strip the password of, or sign
        out a manager or captain (AUTH-05) — StaffAccountPhoneError, and
        nothing is written."""
        if user.get("role") != UserRole.CUSTOMER.value:
            raise StaffAccountPhoneError()
        now = datetime.now(timezone.utc)
        updates = {"phone_verified": True, "phone_verified_at": now, "updated_at": now, **extra}
        ops: dict = {"$set": updates}
        if not user.get("phone_verified"):
            if user.get("password_hash"):
                updates["password_hash"] = None
            ops["$inc"] = {"token_version": 1}
        await self.users.collection.update_one({"_id": user["_id"]}, ops)
        return await self.users.find_by_id(str(user["_id"]))

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
        # Always exactly one bcrypt check — against a throwaway hash when
        # there's no account (or no password) — so the response time no
        # longer says whether the identifier exists (ENUM-1: 18 ms vs 1 s).
        if not await verify_password_or_dummy_async(password, (user or {}).get("password_hash")) or not user:
            if user:
                # Counted with $inc: a read-then-write counter lost every
                # concurrent miss, so a parallel burst of guesses never
                # tripped the lock.
                counted = await self.users.collection.find_one_and_update(
                    {"_id": user["_id"]}, {"$inc": {"failed_login_attempts": 1}}, return_document=ReturnDocument.AFTER
                )
                if counted and counted.get("failed_login_attempts", 0) >= self.LOGIN_MAX_FAILURES:
                    await self.users.collection.update_one(
                        {"_id": user["_id"], "failed_login_attempts": {"$gte": self.LOGIN_MAX_FAILURES}},
                        {"$set": {
                            "login_locked_until": datetime.now(timezone.utc) + timedelta(minutes=self.LOGIN_LOCK_MINUTES),
                            "failed_login_attempts": 0,
                        }},
                    )
            raise UnauthorizedException("Invalid credentials")
        standing = account_switched_off(user)
        if standing:
            raise UnauthorizedException(standing)

        # The lock is checked again as the success is recorded: a correct
        # guess racing inside a burst that has just locked the account
        # must not get through.
        now = datetime.now(timezone.utc)
        signed_in = await self.users.collection.find_one_and_update(
            {"_id": user["_id"], "$or": [{"login_locked_until": None}, {"login_locked_until": {"$lte": now}}]},
            {"$set": {"last_login_at": now, "failed_login_attempts": 0, "login_locked_until": None, "updated_at": now}},
        )
        if not signed_in:
            raise UnauthorizedException("Too many failed attempts. Try again in a few minutes.")
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
        if user.get("is_deleted"):
            raise UnauthorizedException("Your account is no longer active.")
        standing = account_switched_off(user)
        if standing:
            raise UnauthorizedException(standing)
        if payload.get("tv", 0) != user.get("token_version", 0):
            raise UnauthorizedException("Session expired — please log in again.")
        family = await self._spend_refresh_token(payload, refresh_token)

        access_token = create_access_token(
            str(user["_id"]),
            user["role"],
            {"service_center_id": user.get("service_center_id"), "tv": user.get("token_version", 0)},
        )
        new_refresh = create_refresh_token(str(user["_id"]), user["role"], token_version=user.get("token_version", 0), family=family)
        return {"access_token": access_token, "refresh_token": new_refresh, "token_type": "bearer"}

    # Two tabs (or a retried request) refreshing with the SAME token within
    # this window is a benign race, not theft — both get a fresh pair.
    REFRESH_REUSE_GRACE_SECONDS = 30

    async def _spend_refresh_token(self, payload: dict, raw_token: str = "") -> str | None:
        """Refresh-token rotation with reuse detection. Every refresh token
        is single-use: spending it records its jti (TTL'd to the token's own
        expiry). Presenting an already-spent one AFTER the grace window means
        a copy of it is in someone else's hands — the whole chain (family)
        is revoked, which logs out both the thief and that one device, and
        the event is audit-logged. Other devices (other families) are
        untouched. Returns the family the next token continues.

        A legacy token minted before jti existed carries none: it is honoured
        ONCE — keyed by a hash of the token itself — and its successor joins
        a fresh family (they all expire within REFRESH_TOKEN_EXPIRE_DAYS of
        this shipping). It used to be honoured every time it was shown, so a
        stolen pre-rotation token kept minting fresh chains."""
        jti, family = payload.get("jti"), payload.get("fam")
        now = datetime.now(timezone.utc)
        exp = payload.get("exp")
        expires_at = datetime.fromtimestamp(exp, timezone.utc) if isinstance(exp, (int, float)) else now + timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS)
        spent = self.db["spent_refresh_tokens"]
        if not jti or not family:
            import hashlib

            legacy_id = "legacy:" + hashlib.sha256((raw_token or "").encode()).hexdigest()
            try:
                await spent.insert_one({"_id": legacy_id, "user_id": payload.get("sub"), "spent_at": now, "expires_at": expires_at})
                return None
            except DuplicateKeyError:
                prior = await spent.find_one({"_id": legacy_id})
            spent_at = (prior or {}).get("spent_at")
            if spent_at is not None and spent_at.tzinfo is None:
                spent_at = spent_at.replace(tzinfo=timezone.utc)
            if spent_at is not None and now - spent_at <= timedelta(seconds=self.REFRESH_REUSE_GRACE_SECONDS):
                return None
            raise UnauthorizedException("Session expired — please log in again.")
        families = self.db["refresh_token_families"]
        if await families.find_one({"_id": family, "revoked": True}, {"_id": 1}):
            raise UnauthorizedException("Session expired — please log in again.")
        try:
            await spent.insert_one({"_id": jti, "family": family, "user_id": payload.get("sub"), "spent_at": now, "expires_at": expires_at})
            return family
        except DuplicateKeyError:
            prior = await spent.find_one({"_id": jti})
        spent_at = (prior or {}).get("spent_at")
        if spent_at is not None and spent_at.tzinfo is None:
            spent_at = spent_at.replace(tzinfo=timezone.utc)
        if spent_at is not None and now - spent_at <= timedelta(seconds=self.REFRESH_REUSE_GRACE_SECONDS):
            return family
        await families.update_one(
            {"_id": family},
            {"$set": {"revoked": True, "revoked_at": now, "user_id": payload.get("sub"),
                      "expires_at": now + timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS)}},
            upsert=True,
        )
        from app.services.audit_service import AuditService

        await AuditService(self.db).log_action(
            payload.get("sub") or "", payload.get("role") or "", "REFRESH_TOKEN_REUSE", "auth", payload.get("sub"),
            {"family": family, "first_spent_at": spent_at.isoformat() if spent_at else None},
        )
        logger.warning("Refresh token reuse detected for user %s — family %s revoked", payload.get("sub"), family)
        raise UnauthorizedException("Session expired — please log in again.")

    async def logout(self, user_id: str) -> None:
        """Invalidates every refresh token the user holds (token_version
        bump) — the server-side half of logging out. Access tokens expire on
        their own within ACCESS_TOKEN_EXPIRE_MINUTES."""
        await self.users.collection.update_one({"_id": ObjectId(user_id)}, {"$inc": {"token_version": 1}})

    async def change_password(self, user_id: str, current_password: str, new_password: str) -> None:
        user = await self.users.find_by_id(user_id)
        if user and not user.get("password_hash"):
            raise BadRequestException("This account doesn't have a password yet — use 'Forgot password' to set one.")
        if not user or not await verify_password_async(current_password, user["password_hash"]):
            raise BadRequestException("Current password is incorrect")
        await self.users.update_by_id(user_id, {"password_hash": await hash_password_async(new_password), "must_change_password": False})
        # Invalidate every refresh token issued before this change.
        await self.users.collection.update_one({"_id": ObjectId(user_id)}, {"$inc": {"token_version": 1}})

    _OTP_RESEND_COOLDOWN_SECONDS = 30
    _OTP_TTL_MINUTES = 10
    _OTP_MAX_ATTEMPTS = 5
    # Hourly allowance per (phone, requester) — the requester being the
    # caller's IP bucket for anonymous requests, the account for signed-in
    # ones (see _claim_otp_send). A stranger asking for codes to someone
    # else's number uses up only THEIR share, never the owner's (AUTH-06:
    # five booking-popup codes used to lock a customer out of OTP login for
    # an hour).
    _OTP_MAX_SENDS_PER_WINDOW = 5
    # Per-phone ceiling whatever the requester — the per-IP limiter alone
    # can't stop SMS pumping spread across many addresses.
    _OTP_MAX_SENDS_PER_PHONE_WINDOW = 15
    _OTP_SEND_WINDOW_SECONDS = 3600
    _OTP_SEND_FAILED = "Couldn't send the verification code — please try again in a moment."
    _OTP_INVALID = "Invalid or expired code."

    async def _find_user_by_identifier(self, identifier: str) -> dict | None:
        """Email or phone in whatever format was typed: "+91 98765-43210",
        "09876543210" and "9876543210" find the same account, and an email
        matches case-insensitively (customer emails are stored lower-cased,
        staff emails may not be)."""
        raw = (identifier or "").strip()
        if not raw:
            return None
        if "@" in raw:
            return await self.users.find_one({"email": {"$in": list({raw, raw.lower()})}})
        phone = validate_indian_mobile(raw)
        return await self.users.find_by_phone(phone) if phone else await self.users.find_by_identifier(raw)

    async def _otp_phone(self, identifier: str) -> str | None:
        """The canonical phone a code for this identifier lives under. Codes
        are always keyed by the phone they were SENT to, so one asked for by
        email verifies by phone and vice versa, whatever the formatting."""
        raw = (identifier or "").strip()
        if "@" in raw:
            user = await self._find_user_by_identifier(raw)
            return user.get("phone") if user else None
        return validate_indian_mobile(raw) or raw or None

    async def request_otp(self, identifier: str, purpose: str = OTP_LOGIN, customer_only: bool = False, requester: str | None = None) -> str:
        """Sends a code to the phone on file for this account (email or phone
        both resolve to the account's real number). Never returns the code —
        callers must not echo it back to the client. customer_only is the
        OTP-login entry: staff can't log in by OTP, so no code is spent on
        them. A password_reset code is refused (nothing sent) unless the
        account may reset by code at all — _ensure_code_reset_allowed."""
        user = await self._find_user_by_identifier(identifier)
        if not user:
            new_phone = validate_indian_mobile((identifier or "").strip()) if "@" not in (identifier or "") else None
            if customer_only and new_phone and purpose == OTP_LOGIN:
                # First visit by phone: the code goes out like any other and
                # the account is created (after asking the name) once it is
                # proven — see otp_login.
                if is_business_whatsapp_number(new_phone):
                    raise BadRequestException(BUSINESS_NUMBER_MESSAGE)
                return await self._issue_otp(new_phone, purpose, requester)
            raise NotFoundException("No account found with this phone number or email.")
        if customer_only:
            if user.get("role") != UserRole.CUSTOMER.value:
                raise BadRequestException("This number belongs to a staff account — use Staff login.")
            standing = account_switched_off(user)
            if standing:
                raise UnauthorizedException(standing)
        if purpose == OTP_PASSWORD_RESET:
            self._ensure_code_reset_allowed(user)
        phone = user.get("phone")
        if not phone:
            raise BadRequestException("This account has no phone number on file to send a code to.")
        return await self._issue_otp(phone, purpose, requester)

    @staticmethod
    def phone_self_verified(user: dict) -> bool:
        """Has the account holder proven THIS phone themselves, while signed
        in (verify-phone confirm/widget, add-phone)? Bound to the number:
        any later change of phone — by an admin or anyone — no longer
        matches, so the proof can't outlive the number it was for."""
        phone = user.get("phone")
        return bool(phone and user.get("phone_verified") and user.get("self_verified_phone") == phone)

    def _ensure_code_reset_allowed(self, user: dict) -> None:
        """Self-service password reset by code (OTP or MSG91 widget) — the
        AUTH-01 / P0-2 rule. A staff account's phone was typed in by an admin
        or manager (the seed staff even carry placeholder numbers), so a code
        sent there proves nothing about who is asking: whoever held that SIM
        could take over an admin. Staff may reset by code only after proving
        the number themselves while signed in; otherwise their admin resets
        it (POST /auth/staff/{id}/reset-password). A customer's phone IS
        their identity — the same code already signs them in (otp_login) —
        so their reset is unchanged."""
        if user.get("is_deleted"):
            raise BadRequestException("No account found with this phone number or email.")
        if user.get("role") != UserRole.CUSTOMER.value and not self.phone_self_verified(user):
            raise StaffResetRefusedException(STAFF_CODE_RESET_REFUSED)

    def _raise_if_cooling_down(self, record: dict | None, now: datetime) -> None:
        last_sent = (record or {}).get("last_sent_at")
        if not last_sent:
            return
        if last_sent.tzinfo is None:
            last_sent = last_sent.replace(tzinfo=timezone.utc)
        remaining = self._OTP_RESEND_COOLDOWN_SECONDS - (now - last_sent).total_seconds()
        if remaining > 0:
            raise BadRequestException(f"Please wait {math.ceil(remaining)}s before requesting another code.")

    async def _issue_otp(self, phone: str, purpose: str, requester: str | None = None) -> str:
        """One live code per phone, stored with its purpose; returns the
        channel that delivered it. Refused when no backend channel can reach
        the number, inside the resend cooldown, or over the hourly caps —
        and a code that then fails to send is rolled back (no cooldown, not
        counted), so the client can fall back to the MSG91 widget at once."""
        phone = validate_indian_mobile(phone) or phone
        if settings.dev_tools_active:
            return await self._issue_dev_otp(phone, purpose)
        channels = await self._otp_channels(phone)
        if not channels:
            raise BadRequestException(self._OTP_SEND_FAILED)
        now = datetime.now(timezone.utc)
        key = f"otp:{phone}"
        self._raise_if_cooling_down(await self.otp_store.find_one({"identifier": phone}, sort=[("last_sent_at", -1)]), now)
        await self._claim_otp_send(phone, requester)

        otp = f"{secrets.randbelow(1_000_000):06d}"
        cutoff = now - timedelta(seconds=self._OTP_RESEND_COOLDOWN_SECONDS)
        try:
            # Fixed _id: a code sent inside the cooldown turns this upsert
            # into a duplicate-key insert, so two racing sends can't both win.
            await self.otp_store.update_one(
                {"_id": key, "$or": [{"last_sent_at": {"$lte": cutoff}}, {"last_sent_at": None}]},
                {
                    "$set": {
                        "identifier": phone,
                        "otp": otp,
                        "purpose": purpose,
                        "expires_at": now + timedelta(minutes=self._OTP_TTL_MINUTES),
                        "last_sent_at": now,
                        "attempts": 0,
                        "verified": False,
                    }
                },
                upsert=True,
            )
        except DuplicateKeyError:
            await self._release_otp_send(phone, requester)
            self._raise_if_cooling_down(await self.otp_store.find_one({"_id": key}), datetime.now(timezone.utc))
            raise BadRequestException(self._OTP_SEND_FAILED)
        # Codes stored before the fixed _id would otherwise shadow this one.
        await self.otp_store.delete_many({"identifier": phone, "_id": {"$ne": key}})

        channel = await self._deliver_otp(phone, otp, purpose, channels)
        if not channel:
            await self.otp_store.delete_one({"_id": key, "otp": otp})
            await self._release_otp_send(phone, requester)
            raise BadRequestException(self._OTP_SEND_FAILED)
        return channel

    async def _issue_dev_otp(self, phone: str, purpose: str) -> str:
        """LOCAL TESTING ONLY (settings.dev_tools_active): the code is always
        DEV_OTP_CODE, nothing is sent, and there's no cooldown or hourly cap
        — every verify path below works unchanged against it."""
        now = datetime.now(timezone.utc)
        await self.otp_store.update_one(
            {"_id": f"otp:{phone}"},
            {"$set": {
                "identifier": phone, "otp": settings.DEV_OTP_CODE, "purpose": purpose,
                "expires_at": now + timedelta(minutes=self._OTP_TTL_MINUTES),
                "last_sent_at": now, "attempts": 0, "verified": False,
            }},
            upsert=True,
        )
        await self.otp_store.delete_many({"identifier": phone, "_id": {"$ne": f"otp:{phone}"}})
        logger.warning("DEV TOOLS: OTP for %s is %s (not sent)", phone, settings.DEV_OTP_CODE)
        return "whatsapp"

    @staticmethod
    def _requester_slot(requester: str | None) -> str:
        """Who is asking, as a short opaque key: the explicit requester
        ("user:<id>" for signed-in sends), else the anonymous caller's IP
        bucket the rate limiter resolved for this request, else "-" (no
        request context — scripts, tests). Hashed: a field name must not
        carry dots, and the cap doc has no business storing raw IPs."""
        who = requester or current_requester() or "-"
        return hashlib.sha256(who.encode()).hexdigest()[:16]

    async def _claim_otp_send(self, phone: str, requester: str | None = None) -> None:
        """Counts one code toward this phone's hourly allowances, or
        refuses. Two caps in ONE doc keyed by the PHONE (fixed _id, so
        concurrent claims can't both slip under either): `by.<requester>`
        per requester (_OTP_MAX_SENDS_PER_WINDOW) and `sent` for the phone
        as a whole (_OTP_MAX_SENDS_PER_PHONE_WINDOW). Apart from the code
        doc: consuming a code on verify, or asking by email instead of
        phone, must not reset it. The collection's TTL on expires_at clears
        it when the window ends."""
        key = f"otp-send-cap:{phone}"
        slot = self._requester_slot(requester)
        mine = f"by.{slot}"
        now = datetime.now(timezone.utc)
        for _ in range(3):
            claimed = await self.otp_store.update_one(
                {
                    "_id": key, "expires_at": {"$gt": now},
                    "sent": {"$lt": self._OTP_MAX_SENDS_PER_PHONE_WINDOW},
                    mine: {"$not": {"$gte": self._OTP_MAX_SENDS_PER_WINDOW}},
                },
                {"$inc": {"sent": 1, mine: 1}},
            )
            if claimed.modified_count:
                return
            live = await self.otp_store.find_one({"_id": key, "expires_at": {"$gt": now}})
            if live:
                if (live.get("sent", 0) < self._OTP_MAX_SENDS_PER_PHONE_WINDOW
                        and (live.get("by") or {}).get(slot, 0) < self._OTP_MAX_SENDS_PER_WINDOW):
                    continue
                window_end = live["expires_at"]
                if window_end.tzinfo is None:
                    window_end = window_end.replace(tzinfo=timezone.utc)
                minutes = max(1, math.ceil((window_end - now).total_seconds() / 60))
                raise BadRequestException(f"Too many codes requested for this number — try again in {minutes} minute{'s' if minutes != 1 else ''}.")
            try:
                # Upsert over a stale (not yet TTL-swept) window or none at
                # all; a live one makes the insert collide instead.
                await self.otp_store.update_one(
                    {"_id": key, "expires_at": {"$lte": now}},
                    {"$set": {"sent": 1, "by": {slot: 1}, "expires_at": now + timedelta(seconds=self._OTP_SEND_WINDOW_SECONDS)}},
                    upsert=True,
                )
                return
            except DuplicateKeyError:
                continue
        raise BadRequestException(self._OTP_SEND_FAILED)

    async def _release_otp_send(self, phone: str, requester: str | None = None) -> None:
        """Hands back a claim whose code never went out (same requester
        resolution as the claim, so it lands on the same counter)."""
        slot = self._requester_slot(requester)
        await self.otp_store.update_one(
            {"_id": f"otp-send-cap:{phone}", "sent": {"$gt": 0}, f"by.{slot}": {"$gt": 0}},
            {"$inc": {"sent": -1, f"by.{slot}": -1}},
        )

    async def request_phone_otp(self, phone: str, requester: str | None = None) -> str:
        """Backend OTP for a bare phone number (the confirm-booking popup) —
        the same code, cooldown, caps and delivery as login, minus the
        account lookup (a first-time booker has no account yet). Its purpose
        (booking_confirmation) proves the phone for booking / login /
        enrolment — never for a password reset (OTP_ACCEPTS)."""
        normalized = validate_indian_mobile(phone)
        if not normalized:
            raise BadRequestException("Enter a valid 10-digit mobile number")
        return await self._issue_otp(normalized, OTP_BOOKING, requester)

    async def verify_phone_proof(
        self, phone: str, otp: str | None, widget_access_token: str | None, accept: frozenset[str] = OTP_ACCEPTS["phone_proof"],
        consume: bool = True,
    ) -> bool:
        """Proof of phone ownership, exactly as login checks it: the MSG91
        widget's access token (verified server-side, bound to this phone,
        single use) or our own classic OTP issued for one of the `accept`
        purposes. Raises Msg91Unavailable (503) only when MSG91 can't be
        reached — a retry may then succeed (the token isn't spent)."""
        normalized = validate_indian_mobile(phone or "")
        if not normalized:
            return False
        if widget_access_token:
            from app.services.msg91_widget_service import Msg91WidgetService

            if not await Msg91WidgetService().verify_access_token(widget_access_token, normalized):
                return False
            return await self._spend_widget_token(widget_access_token) if consume else True
        if otp:
            return await self.verify_otp(normalized, otp, accept=accept, consume=consume)
        return False

    async def _spend_widget_token(self, access_token: str) -> bool:
        """MSG91 widget tokens are single-use on OUR side (AUTH-03): MSG91
        will confirm the same token again for as long as it lives, so one
        token used to reset a password, then log in, then reset again. Its
        hash is inserted before the caller acts — the unique _id makes a
        second use (or two parallel ones) fail. Kept until the token itself
        would have expired (TTL on expires_at)."""
        from app.services.msg91_widget_service import _jwt_payload

        now = datetime.now(timezone.utc)
        exp = _jwt_payload(access_token).get("exp")
        expires_at = now + timedelta(days=1)
        if isinstance(exp, (int, float)):
            # Never shorter than a day (the payload's own claim decides
            # nothing on its own), never longer than a month.
            expires_at = min(max(datetime.fromtimestamp(exp, timezone.utc), expires_at), now + timedelta(days=30))
        try:
            await self.db["used_widget_tokens"].insert_one({
                "_id": hashlib.sha256(access_token.encode()).hexdigest(), "used_at": now, "expires_at": expires_at,
            })
        except DuplicateKeyError:
            logger.warning("MSG91 widget token presented again after it was used — refused")
            return False
        return True

    async def require_phone_proof(
        self, phone: str, otp: str | None, widget_access_token: str | None, accept: frozenset[str] = OTP_ACCEPTS["phone_proof"],
    ) -> None:
        if not (otp or widget_access_token):
            raise BadRequestException("Please verify your mobile number with the code we send to confirm your booking.")
        if not await self.verify_phone_proof(phone, otp, widget_access_token, accept):
            raise BadRequestException(self._OTP_INVALID)

    async def verify_otp(self, identifier: str, otp: str, accept: frozenset[str] | None = None, consume: bool = True) -> bool:
        """Single-use: a match deletes the code, so it can never be replayed
        (it used to stay valid for its full 10 minutes: one observed code
        could reset the password, then log in, then re-verify the phone).
        Every guess, right or wrong, atomically spends one of the attempts,
        so parallel guesses can't exceed the limit and two parallel submits
        of the right code can't both succeed.

        `accept`: the purposes this consumer takes (OTP_ACCEPTS). A code
        issued for anything else is not even compared — no attempt spent,
        nothing consumed, so it still works for the job it was sent for.
        None = any purpose (the bare /auth/otp/verify check)."""
        code = "".join(str(otp or "").split())
        phone = await self._otp_phone(identifier)
        if not code or not phone:
            return False
        record = await self.otp_store.find_one({"identifier": phone}, sort=[("last_sent_at", -1)])
        if not record:
            return False
        guard: dict = {"_id": record["_id"], "attempts": {"$lt": self._OTP_MAX_ATTEMPTS}, "expires_at": {"$gt": datetime.now(timezone.utc)}}
        if accept is not None:
            guard["purpose"] = {"$in": sorted(accept)}
        record = await self.otp_store.find_one_and_update(guard, {"$inc": {"attempts": 1}}, return_document=ReturnDocument.AFTER)
        if not record or not secrets.compare_digest(str(record.get("otp", "")), code):
            return False
        if not consume:
            # A peek (new-account login asks the name AFTER the code checks
            # out, then presents the same code again): the guess still spent
            # an attempt above, but the code stays for the real use.
            return True
        consumed = await self.otp_store.delete_one({"_id": record["_id"], "otp": record["otp"]})
        return consumed.deleted_count == 1

    async def _apply_reset_password(self, user: dict, new_password: str) -> None:
        """The write behind both reset flows, after the code/token checked
        out: new password, gate cleared, every outstanding session and
        refresh token revoked (token_version) — the whole point of a panic
        reset is locking out whoever has the old credentials. One atomic
        write, and only while the account still has the phone that was
        just proven (and, for staff, still self-verified) — an admin
        changing the number in between makes the code worthless."""
        guard: dict = {"_id": user["_id"], "phone": user["phone"]}
        if user.get("role") != UserRole.CUSTOMER.value:
            guard.update({"phone_verified": True, "self_verified_phone": user["phone"]})
        done = await self.users.collection.update_one(guard, {
            "$set": {"password_hash": await hash_password_async(new_password), "must_change_password": False, "updated_at": datetime.now(timezone.utc)},
            "$inc": {"token_version": 1},
        })
        if not done.modified_count:
            raise BadRequestException(self._OTP_INVALID)

    async def reset_password(self, identifier: str, otp: str, new_password: str) -> None:
        user = await self._find_user_by_identifier(identifier)
        if not user or not user.get("phone"):
            raise BadRequestException("No account found with this phone number or email.")
        self._ensure_code_reset_allowed(user)
        if not await self.verify_otp(user["phone"], otp, accept=OTP_ACCEPTS["password_reset"]):
            raise BadRequestException(self._OTP_INVALID)
        await self._apply_reset_password(user, new_password)

    async def request_phone_verification(self, user_id: str) -> str:
        """Verify-my-phone while signed in — any role. For a customer it
        clears the 90-day re-verification gate; for staff it is the ONLY
        way their phone counts as theirs (a code reset needs it — see
        _ensure_code_reset_allowed). Always the logged-in user's own phone
        (never a typed-in identifier); the send counts against this account,
        not an IP, so strangers flooding the number can't use it up."""
        user = await self.users.find_by_id(user_id)
        if not user:
            raise NotFoundException("User not found")
        if not user.get("phone"):
            raise BadRequestException("Add a phone number to your profile before verifying it.")
        return await self._issue_otp(user["phone"], OTP_LOGIN, requester=f"user:{user_id}")

    async def _mark_self_verified(self, user: dict) -> dict:
        """The signed-in account holder just proved the phone on file.
        Written only if that is still the phone on file (a concurrent change
        must not inherit the proof)."""
        now = datetime.now(timezone.utc)
        await self.users.collection.update_one(
            {"_id": user["_id"], "phone": user["phone"]},
            {"$set": {"phone_verified": True, "phone_verified_at": now, "self_verified_phone": user["phone"], "updated_at": now}},
        )
        return await self.users.find_by_id(str(user["_id"]))

    async def confirm_phone_verification_widget(self, user_id: str, access_token: str) -> dict:
        """MSG91-widget variant of phone verification: the widget already
        delivered + checked the OTP on MSG91's channels (SMS/WhatsApp/
        email); we accept only if MSG91 confirms the token AND it was
        issued for THIS user's phone."""
        user = await self.users.find_by_id(user_id)
        if not user:
            raise NotFoundException("User not found")
        if not user.get("phone"):
            raise BadRequestException("This account has no phone number on file.")
        if not await self.verify_phone_proof(user["phone"], None, access_token):
            raise BadRequestException("Verification could not be confirmed — please try again.")
        return await self._mark_self_verified(user)

    async def reset_password_widget(self, access_token: str, phone: str, new_password: str) -> None:
        """MSG91-widget variant of forgot-password. Same account rule as the
        OTP reset (checked BEFORE the token is spent); then the token must
        verify, be bound to the given phone and be unused — only then does
        the password change (and every existing refresh token dies via
        token_version)."""
        normalized = validate_indian_mobile(phone)
        user = await self.users.find_by_phone(normalized) if normalized else None
        if not user:
            raise BadRequestException("No account found for this phone number")
        self._ensure_code_reset_allowed(user)
        if not await self.verify_phone_proof(normalized, None, access_token):
            raise BadRequestException("Verification could not be confirmed — please try again.")
        await self._apply_reset_password(user, new_password)

    async def confirm_phone_verification(self, user_id: str, otp: str) -> dict:
        user = await self.users.find_by_id(user_id)
        if not user:
            raise NotFoundException("User not found")
        if not user.get("phone"):
            raise BadRequestException("This account has no phone number on file.")
        if not await self.verify_otp(user["phone"], otp, accept=OTP_ACCEPTS["phone_verification"]):
            raise BadRequestException(self._OTP_INVALID)
        return await self._mark_self_verified(user)

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
        # Refuse BEFORE touching the password when the message can't reach
        # them (no approved template and no open 24 h chat — DEP-04): it used
        # to reset first and only then say "couldn't be sent", leaving the
        # customer locked out of a password nobody could read.
        if not await self.whatsapp.can_deliver_temp_password(phone):
            raise BadRequestException(
                "A temporary password can't be sent to this customer's WhatsApp right now — ask them to use 'Forgot password' instead."
            )

        temp_password = "".join(secrets.choice(string.ascii_uppercase + string.ascii_lowercase + string.digits) for _ in range(10))
        new_hash = await hash_password_async(temp_password)
        await self.users.collection.update_one(
            {"_id": customer["_id"]},
            {"$set": {"password_hash": new_hash, "must_change_password": True, "updated_at": datetime.now(timezone.utc)},
             "$inc": {"token_version": 1}},
        )
        # Same channel-order fallback as OTPs (note: MSG91's OTP API can't
        # carry free text, so send_temp_password returns False and
        # WhatsApp handles it).
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
            # The pre-check passed but the send still failed (Meta error):
            # put the old password back — unless something changed it since
            # — so the customer isn't locked out of one nobody can read.
            await self.users.collection.update_one(
                {"_id": customer["_id"], "password_hash": new_hash},
                {"$set": {"password_hash": customer.get("password_hash"), "must_change_password": bool(customer.get("must_change_password"))}},
            )
            raise BadRequestException("The temporary password couldn't be sent, so their password is unchanged — ask the customer to use 'Forgot password' instead.")

    async def staff_reset_staff_password(
        self, target_id: str, temp_password: str, actor_id: str, actor_role: str, actor_center_id: str | None,
    ) -> dict:
        """The staff recovery path (AUTH-01): a staff member who can't reset
        by code (phone not self-verified) gets a temporary password from
        their admin — or, for a captain, their center's manager — exactly
        like account creation: the admin types it and hands it over in
        person, it is never sent to the (unproven) phone on file, every
        session dies (token_version) and must_change_password forces the
        staff member to replace it at the next login (set_initial_password
        then signs out whoever else knew it). Returns the target doc."""
        from app.core.authz import manager_center_or_raise

        if target_id == actor_id:
            raise BadRequestException("Use Change Password for your own account.")
        target = await self.users.find_by_id(target_id)
        # 404 for anything out of reach — never confirm an id exists.
        if not target or target.get("role") == UserRole.CUSTOMER.value:
            raise NotFoundException("Staff member not found")
        if actor_role != UserRole.ADMIN.value:
            center = manager_center_or_raise(actor_role, actor_center_id)
            if target.get("role") != UserRole.CAPTAIN.value or target.get("service_center_id") != center:
                raise NotFoundException("Staff member not found")
        now = datetime.now(timezone.utc)
        await self.users.collection.update_one(
            {"_id": target["_id"]},
            {"$set": {"password_hash": await hash_password_async(temp_password), "must_change_password": True, "updated_at": now},
             "$inc": {"token_version": 1}},
        )
        return target

    async def booking_access_mode(self, phone: str) -> dict:
        """What should the guest wizard do for this phone?
        - "register": no account -> normal silent registration path.
        - "otp": no real, chosen password to fall back on (a quick-booking/
          WhatsApp/Google account with no password at all, or a guest
          account whose must_change_password is true because nobody ever
          set one on purpose), or a real account whose
          phone-ownership proof has gone stale -> prove ownership by OTP
          instead and keep booking smooth. This is what stops "you already
          have an account" from ever dead-ending someone who never
          knowingly registered — silently created accounts always take
          this path, regardless of how recently they were "verified".
        - "password": a real account (their own chosen password) that was
          phone-verified recently enough to trust -> log in normally,
          which is less friction than an OTP round-trip for someone who
          already knows their password.
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
        if user.get("must_change_password") or not user.get("password_hash"):
            return {"mode": "otp"}
        return {"mode": "password" if self.phone_verification_fresh(user) else "otp"}

    async def otp_login(
        self, phone: str, otp: str | None = None, widget_access_token: str | None = None, full_name: str | None = None
    ) -> dict:
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
        if not user:
            # A number with no account yet: prove it, ask the name, then
            # create the account. The first call (no name) only CHECKS the
            # code — it stays valid — and answers needs_name; the second
            # presents the same code with the name and signs them up.
            if is_business_whatsapp_number(normalized):
                raise BadRequestException(BUSINESS_NUMBER_MESSAGE)
            name = " ".join((full_name or "").split())[:100]
            if len(name) < 2:
                if not await self.verify_phone_proof(normalized, otp, widget_access_token, OTP_ACCEPTS["login"], consume=False):
                    raise BadRequestException(self._OTP_INVALID)
                return {"needs_name": True}
            if not await self.verify_phone_proof(normalized, otp, widget_access_token, OTP_ACCEPTS["login"]):
                raise BadRequestException(self._OTP_INVALID)
            created = await self.ensure_customer_by_phone(normalized, name)
            fresh = await self.mark_phone_proven(created, last_login_at=datetime.now(timezone.utc))
            return self._issue_tokens(fresh)
        if user.get("is_deleted") or user.get("role") != UserRole.CUSTOMER.value:
            raise BadRequestException("No customer account found for this phone number")
        standing = account_switched_off(user)
        if standing:
            raise UnauthorizedException(standing)

        if not await self.verify_phone_proof(normalized, otp, widget_access_token, OTP_ACCEPTS["login"]):
            raise BadRequestException(self._OTP_INVALID)

        fresh = await self.mark_phone_proven(user, last_login_at=datetime.now(timezone.utc))
        return self._issue_tokens(fresh)

    async def add_phone_request(self, user_id: str, phone: str) -> str:
        """Step 1 of attaching a phone to a phoneless account (Google
        sign-ins) or changing to a new one (staff profile page): validate +
        uniqueness-check the number, then send an
        OTP to it. The OTP is keyed by the NEW phone in the same otp_store
        the rest of auth uses (cooldown, hourly cap and delivery included)."""
        normalized = validate_indian_mobile(phone)
        if not normalized:
            raise BadRequestException("Enter a valid 10-digit Indian mobile number")
        if is_business_whatsapp_number(normalized):
            raise BadRequestException(BUSINESS_NUMBER_MESSAGE)
        user = await self.users.find_by_id(user_id)
        if not user:
            raise NotFoundException("User not found")
        taken = await self.users.find_by_phone(normalized)
        if taken and str(taken["_id"]) != user_id:
            raise BadRequestException("This phone number is already used by another account.")
        return await self._issue_otp(normalized, OTP_LOGIN, requester=f"user:{user_id}")

    async def add_phone_confirm(self, user_id: str, phone: str, otp: str | None = None, widget_access_token: str | None = None) -> dict:
        """Step 2: prove ownership (classic OTP or MSG91 widget token bound
        to this exact number), then attach it as the account's verified
        primary contact."""
        normalized = validate_indian_mobile(phone)
        if not normalized:
            raise BadRequestException("Enter a valid 10-digit Indian mobile number")
        if is_business_whatsapp_number(normalized):
            raise BadRequestException(BUSINESS_NUMBER_MESSAGE)
        user = await self.users.find_by_id(user_id)
        if not user:
            raise NotFoundException("User not found")
        taken = await self.users.find_by_phone(normalized)
        if taken and str(taken["_id"]) != user_id:
            raise BadRequestException("This phone number is already used by another account.")

        if not await self.verify_phone_proof(normalized, otp, widget_access_token, OTP_ACCEPTS["phone_verification"]):
            raise BadRequestException(self._OTP_INVALID)
        try:
            # Proven by the signed-in account holder — for staff, this is what
            # lets a code reset their password later (phone_self_verified).
            return await self.users.update_by_id(
                user_id,
                {"phone": normalized, "phone_verified": True, "phone_verified_at": datetime.now(timezone.utc), "self_verified_phone": normalized},
            )
        except DuplicateKeyError:
            raise BadRequestException("This phone number is already used by another account.")

    async def set_initial_password(self, user_id: str, new_password: str) -> None:
        """Password setup WITHOUT the current password — allowed only while
        must_change_password is set (temp-password logins and OTP-logins,
        which already proved identity). Clears the flag and signs out every
        OTHER session (token_version bump): whoever chose the temporary
        password — the manager who created this captain, say — may have
        logged in with it, and that session must not outlive the owner's
        own password. The caller hands this device a fresh token pair."""
        user = await self.users.find_by_id(user_id)
        if not user:
            raise NotFoundException("User not found")
        if not user.get("must_change_password"):
            raise BadRequestException("Use the normal change-password option (current password required).")
        claimed = await self.users.collection.update_one(
            {"_id": ObjectId(user_id), "must_change_password": True},
            {
                "$set": {"password_hash": await hash_password_async(new_password), "must_change_password": False},
                "$inc": {"token_version": 1},
            },
        )
        if not claimed.modified_count:
            raise BadRequestException("Use the normal change-password option (current password required).")

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
