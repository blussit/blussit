"""
Authentication business logic. Backend OTPs go out over WhatsApp (approved
template, or free text inside an open 24h chat) or SMS (SMS_PROVIDER);
when neither can reach a number the request is refused and the browser
sends the code through the MSG91 widget instead (see Msg91WidgetService).
Dev/test use the log-only providers.
"""
import logging
import math
import random
import secrets
import string
from datetime import datetime, timedelta, timezone

from bson import ObjectId
from motor.motor_asyncio import AsyncIOMotorDatabase
from pymongo.errors import DuplicateKeyError

from app.core.exceptions import BadRequestException, ConflictException, ForbiddenException, NotFoundException, PhoneNotVerifiedException, UnauthorizedException
from app.core.security import create_access_token, create_refresh_token, decode_token, hash_password_async, verify_password_async
from app.models.enums import UserRole, UserStatus
from app.repositories.user_repository import UserRepository
from app.schemas.user_schema import ManagerCreateCustomerRequest, RegisterRequest, StaffCreateRequest, UserPublic
from app.services.sms_service import SmsService
from app.services.whatsapp_service import WhatsAppService
from app.utils.phone import BUSINESS_NUMBER_MESSAGE, is_business_whatsapp_number, validate_indian_mobile
from pymongo import ReturnDocument

logger = logging.getLogger(__name__)


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
        if payload.email and await self.users.find_by_email(payload.email):
            raise ConflictException("An account with this email already exists")
        if payload.phone and await self.users.find_by_phone(payload.phone):
            raise ConflictException("An account with this phone number already exists")

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

        user_doc = self._strip_absent_contact_fields({
            "full_name": payload.full_name,
            "email": payload.email,
            "phone": payload.phone,
            "password_hash": await hash_password_async(payload.password),
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

    async def ensure_customer_by_phone(self, phone: str, full_name: str) -> dict:
        """Quick-booking model (2026-09): a booking needs no account up front.
        The customer profile is found by phone, or created silently from
        the name + phone typed into the booking — no password (customers
        log in by OTP only), no must_change_password gate, no OTP before
        booking. Returns the raw user doc."""
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
                raise BadRequestException("This number belongs to a staff account — use a customer number to book.")
            if user.get("status") == UserStatus.SUSPENDED.value:
                raise UnauthorizedException("This account has been suspended. Contact support.")
            # Fill in a name the profile never had (WhatsApp/quick accounts
            # start with just a phone); never overwrite one the customer set.
            if name and not (user.get("full_name") or "").strip():
                await self.users.update_by_id(str(user["_id"]), {"full_name": name})
                user["full_name"] = name
            return user

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
        if not user or not await verify_password_async(password, user.get("password_hash")):
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
    # Per-phone ceiling whatever the caller's IP — the per-IP limiter alone
    # can't stop SMS pumping spread across many addresses.
    _OTP_MAX_SENDS_PER_WINDOW = 5
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

    async def request_otp(self, identifier: str, purpose: str = "verification", customer_only: bool = False) -> str:
        """Sends a code to the phone on file for this account (email or phone
        both resolve to the account's real number). Never returns the code —
        callers must not echo it back to the client. customer_only is the
        OTP-login entry: staff can't log in by OTP, so no code is spent on
        them."""
        user = await self._find_user_by_identifier(identifier)
        if not user:
            raise NotFoundException("No account found with this phone number or email.")
        if customer_only:
            if user.get("role") != UserRole.CUSTOMER.value:
                raise BadRequestException("This number belongs to a staff account — use Staff login.")
            if user.get("status") == UserStatus.SUSPENDED.value:
                raise UnauthorizedException("Your account has been suspended. Contact support.")
        phone = user.get("phone")
        if not phone:
            raise BadRequestException("This account has no phone number on file to send a code to.")
        return await self._issue_otp(phone, purpose)

    def _raise_if_cooling_down(self, record: dict | None, now: datetime) -> None:
        last_sent = (record or {}).get("last_sent_at")
        if not last_sent:
            return
        if last_sent.tzinfo is None:
            last_sent = last_sent.replace(tzinfo=timezone.utc)
        remaining = self._OTP_RESEND_COOLDOWN_SECONDS - (now - last_sent).total_seconds()
        if remaining > 0:
            raise BadRequestException(f"Please wait {math.ceil(remaining)}s before requesting another code.")

    async def _issue_otp(self, phone: str, purpose: str) -> str:
        """One live code per phone; returns the channel that delivered it.
        Refused when no backend channel can reach the number, inside the
        resend cooldown, or over the hourly cap — and a code that then fails
        to send is rolled back (no cooldown, not counted), so the client can
        fall back to the MSG91 widget at once."""
        phone = validate_indian_mobile(phone) or phone
        channels = await self._otp_channels(phone)
        if not channels:
            raise BadRequestException(self._OTP_SEND_FAILED)
        now = datetime.now(timezone.utc)
        key = f"otp:{phone}"
        self._raise_if_cooling_down(await self.otp_store.find_one({"identifier": phone}, sort=[("last_sent_at", -1)]), now)
        await self._claim_otp_send(phone)

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
            await self._release_otp_send(phone)
            self._raise_if_cooling_down(await self.otp_store.find_one({"_id": key}), datetime.now(timezone.utc))
            raise BadRequestException(self._OTP_SEND_FAILED)
        # Codes stored before the fixed _id would otherwise shadow this one.
        await self.otp_store.delete_many({"identifier": phone, "_id": {"$ne": key}})

        channel = await self._deliver_otp(phone, otp, purpose, channels)
        if not channel:
            await self.otp_store.delete_one({"_id": key, "otp": otp})
            await self._release_otp_send(phone)
            raise BadRequestException(self._OTP_SEND_FAILED)
        return channel

    async def _claim_otp_send(self, phone: str) -> None:
        """Counts one code toward this phone's hourly allowance, or refuses.
        The counter is its own otp_requests doc keyed by the PHONE (fixed
        _id, so concurrent claims can't both slip under the cap), apart from
        the code doc: consuming a code on verify, or asking by email instead
        of phone, must not reset it. The collection's TTL on expires_at
        clears it when the window ends."""
        key = f"otp-send-cap:{phone}"
        now = datetime.now(timezone.utc)
        for _ in range(3):
            claimed = await self.otp_store.update_one(
                {"_id": key, "expires_at": {"$gt": now}, "sent": {"$lt": self._OTP_MAX_SENDS_PER_WINDOW}},
                {"$inc": {"sent": 1}},
            )
            if claimed.modified_count:
                return
            live = await self.otp_store.find_one({"_id": key, "expires_at": {"$gt": now}})
            if live:
                if live.get("sent", 0) < self._OTP_MAX_SENDS_PER_WINDOW:
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
                    {"$set": {"sent": 1, "expires_at": now + timedelta(seconds=self._OTP_SEND_WINDOW_SECONDS)}},
                    upsert=True,
                )
                return
            except DuplicateKeyError:
                continue
        raise BadRequestException(self._OTP_SEND_FAILED)

    async def _release_otp_send(self, phone: str) -> None:
        """Hands back a claim whose code never went out."""
        await self.otp_store.update_one({"_id": f"otp-send-cap:{phone}", "sent": {"$gt": 0}}, {"$inc": {"sent": -1}})

    async def request_phone_otp(self, phone: str) -> str:
        """Backend OTP for a bare phone number (the confirm-booking popup) —
        the same code, cooldown, cap and delivery as login, minus the account
        lookup (a first-time booker has no account yet)."""
        normalized = validate_indian_mobile(phone)
        if not normalized:
            raise BadRequestException("Enter a valid 10-digit mobile number")
        return await self._issue_otp(normalized, "booking_confirmation")

    async def verify_phone_proof(self, phone: str, otp: str | None, widget_access_token: str | None) -> bool:
        """Proof of phone ownership, exactly as login checks it: the MSG91
        widget's access token (verified server-side, bound to this phone)
        or our own classic OTP. Raises Msg91Unavailable (503) only when
        MSG91 can't be reached — a retry may then succeed."""
        normalized = validate_indian_mobile(phone or "")
        if not normalized:
            return False
        if widget_access_token:
            from app.services.msg91_widget_service import Msg91WidgetService

            return await Msg91WidgetService().verify_access_token(widget_access_token, normalized)
        if otp:
            return await self.verify_otp(normalized, otp)
        return False

    async def require_phone_proof(self, phone: str, otp: str | None, widget_access_token: str | None) -> None:
        if not (otp or widget_access_token):
            raise BadRequestException("Please verify your mobile number with the code we send to confirm your booking.")
        if not await self.verify_phone_proof(phone, otp, widget_access_token):
            raise BadRequestException(self._OTP_INVALID)

    async def verify_otp(self, identifier: str, otp: str) -> bool:
        """Single-use: a match deletes the code, so it can never be replayed
        (it used to stay valid for its full 10 minutes: one observed code
        could reset the password, then log in, then re-verify the phone).
        Every guess, right or wrong, atomically spends one of the attempts,
        so parallel guesses can't exceed the limit and two parallel submits
        of the right code can't both succeed."""
        code = "".join(str(otp or "").split())
        phone = await self._otp_phone(identifier)
        if not code or not phone:
            return False
        record = await self.otp_store.find_one({"identifier": phone}, sort=[("last_sent_at", -1)])
        if not record:
            return False
        record = await self.otp_store.find_one_and_update(
            {"_id": record["_id"], "attempts": {"$lt": self._OTP_MAX_ATTEMPTS}, "expires_at": {"$gt": datetime.now(timezone.utc)}},
            {"$inc": {"attempts": 1}},
            return_document=ReturnDocument.AFTER,
        )
        if not record or not secrets.compare_digest(str(record.get("otp", "")), code):
            return False
        consumed = await self.otp_store.delete_one({"_id": record["_id"], "otp": record["otp"]})
        return consumed.deleted_count == 1

    async def reset_password(self, identifier: str, otp: str, new_password: str) -> None:
        user = await self._find_user_by_identifier(identifier)
        if not user or not user.get("phone"):
            raise BadRequestException("No account found with this phone number or email.")
        if not await self.verify_otp(user["phone"], otp):
            raise BadRequestException(self._OTP_INVALID)
        await self.users.update_by_id(
            str(user["_id"]),
            {
                "password_hash": await hash_password_async(new_password),
                "must_change_password": False,
            },
        )
        # Invalidate every outstanding session/refresh token — the whole
        # point of a panic reset is locking out whoever has the old
        # credentials (change_password already did this; this path didn't).
        await self.users.collection.update_one({"_id": ObjectId(str(user["_id"]))}, {"$inc": {"token_version": 1}})

    async def request_phone_verification(self, user_id: str) -> str:
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
        return await self._issue_otp(user["phone"], "verification")

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
        return await self.users.update_by_id(user_id, {"phone_verified": True, "phone_verified_at": datetime.now(timezone.utc)})

    async def reset_password_widget(self, access_token: str, phone: str, new_password: str) -> None:
        """MSG91-widget variant of forgot-password. The token must verify
        AND be bound to the given phone; only then does the password change
        (and every existing refresh token dies via token_version)."""
        normalized = validate_indian_mobile(phone)
        user = await self.users.find_by_phone(normalized) if normalized else None
        if not user:
            raise BadRequestException("No account found for this phone number")
        if not await self.verify_phone_proof(normalized, None, access_token):
            raise BadRequestException("Verification could not be confirmed — please try again.")
        await self.users.update_by_id(str(user["_id"]), {"password_hash": await hash_password_async(new_password), "must_change_password": False})
        await self.users.collection.update_one({"_id": user["_id"]}, {"$inc": {"token_version": 1}})

    async def confirm_phone_verification(self, user_id: str, otp: str) -> dict:
        user = await self.users.find_by_id(user_id)
        if not user:
            raise NotFoundException("User not found")
        if not user.get("phone"):
            raise BadRequestException("This account has no phone number on file.")
        if not await self.verify_otp(user["phone"], otp):
            raise BadRequestException(self._OTP_INVALID)
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
        await self.users.update_by_id(customer_id, {"password_hash": await hash_password_async(temp_password), "must_change_password": True})
        await self.users.collection.update_one({"_id": ObjectId(customer_id)}, {"$inc": {"token_version": 1}})
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
            raise BadRequestException("Password was reset, but the message couldn't be sent — ask the customer to use 'Forgot password' instead.")

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

        if not await self.verify_phone_proof(normalized, otp, widget_access_token):
            raise BadRequestException(self._OTP_INVALID)

        await self.users.update_by_id(
            str(user["_id"]),
            {"phone_verified": True, "phone_verified_at": datetime.now(timezone.utc), "last_login_at": datetime.now(timezone.utc)},
        )
        fresh = await self.users.find_by_id(str(user["_id"]))
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
        return await self._issue_otp(normalized, "verification")

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

        if not await self.verify_phone_proof(normalized, otp, widget_access_token):
            raise BadRequestException(self._OTP_INVALID)
        try:
            return await self.users.update_by_id(
                user_id,
                {"phone": normalized, "phone_verified": True, "phone_verified_at": datetime.now(timezone.utc)},
            )
        except DuplicateKeyError:
            raise BadRequestException("This phone number is already used by another account.")

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
        await self.users.update_by_id(user_id, {"password_hash": await hash_password_async(new_password), "must_change_password": False})

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
