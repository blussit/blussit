"""
SMS delivery for OTPs and temp passwords — the bridge channel while
WhatsApp's Authentication templates are gated behind Meta business
verification, and the permanent fallback after (AuthService tries the
OTP_CHANNEL first, then the other channel automatically).

Provider-abstracted exactly like whatsapp_service.py:
  - LogSmsProvider: records to the sms_outbox collection, never calls a
    real API (dev/tests).
  - Msg91Provider: MSG91's OTP API (needs MSG91_AUTH_KEY; the OTP
    template id is optional — without it MSG91 uses the account's
    default OTP template). The only real SMS provider.

India-reality note kept in one place: proper custom-content SMS requires
TRAI DLT entity+template registration; MSG91's OTP API carries the
DLT-approved OTP template only, so free text still goes over WhatsApp.
"""
import logging
from abc import ABC, abstractmethod
from datetime import datetime, timezone

import httpx
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.core.config import settings

logger = logging.getLogger(__name__)


class SmsProvider(ABC):
    @abstractmethod
    async def send_otp(self, phone: str, code: str) -> bool:
        """Delivers a numeric OTP. Never raises; False = not delivered."""
        raise NotImplementedError

    @abstractmethod
    async def send_text(self, phone: str, message: str) -> bool:
        """Free-text SMS (temp passwords etc.) — providers whose route is
        OTP-only return False so the caller falls back to WhatsApp."""
        raise NotImplementedError


def _digits10(phone: str) -> str:
    digits = "".join(ch for ch in phone if ch.isdigit())
    return digits[-10:] if len(digits) >= 10 else digits


class LogSmsProvider(SmsProvider):
    def __init__(self, db: AsyncIOMotorDatabase):
        self.db = db

    async def _record(self, phone: str, message: str, kind: str) -> bool:
        logger.info("SMS [log provider, %s] -> %s: %s", kind, phone, message)
        await self.db.sms_outbox.insert_one({
            "phone": phone, "message": message, "kind": kind, "provider": "log",
            "created_at": datetime.now(timezone.utc),
        })
        return True

    async def send_otp(self, phone: str, code: str) -> bool:
        return await self._record(phone, f"{code} is your verification code", "otp")

    async def send_text(self, phone: str, message: str) -> bool:
        return await self._record(phone, message, "text")


class Msg91Provider(SmsProvider):
    def __init__(self, db: AsyncIOMotorDatabase, auth_key: str, otp_template_id: str = ""):
        self.db = db
        self.auth_key = auth_key
        self.otp_template_id = otp_template_id

    async def send_otp(self, phone: str, code: str) -> bool:
        params = {"mobile": f"91{_digits10(phone)}", "otp": code}
        if self.otp_template_id:
            params["template_id"] = self.otp_template_id
        outbox = {"phone": phone, "message": f"[msg91 otp] {code}", "kind": "otp", "provider": "msg91",
                  "created_at": datetime.now(timezone.utc)}
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                r = await client.post("https://control.msg91.com/api/v5/otp", params=params, headers={"authkey": self.auth_key})
            body = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
            ok = r.status_code < 300 and body.get("type") == "success"
            outbox.update({"status_code": r.status_code, "ok": ok, "response_body": r.text[:1000]})
            await self.db.sms_outbox.insert_one(outbox)
            if not ok:
                logger.error("MSG91 OTP send failed (%s): %s", r.status_code, r.text[:300])
            return ok
        except httpx.HTTPError as exc:
            logger.error("MSG91 send raised %s for %s", exc, phone)
            outbox.update({"ok": False, "error": str(exc)})
            await self.db.sms_outbox.insert_one(outbox)
            return False

    async def send_text(self, phone: str, message: str) -> bool:
        # Custom-content SMS needs its own DLT-registered template — not
        # wired until one exists; WhatsApp fallback handles it.
        return False


def get_sms_provider(db: AsyncIOMotorDatabase) -> SmsProvider | None:
    """None = SMS channel disabled entirely (the default) — callers treat
    that as 'not configured' and stay WhatsApp-only."""
    if settings.SMS_PROVIDER == "log":
        return LogSmsProvider(db)
    if settings.SMS_PROVIDER == "msg91" and settings.MSG91_AUTH_KEY:
        return Msg91Provider(db, settings.MSG91_AUTH_KEY, settings.MSG91_OTP_TEMPLATE_ID)
    return None


class SmsService:
    def __init__(self, db: AsyncIOMotorDatabase):
        self.provider = get_sms_provider(db)

    @property
    def enabled(self) -> bool:
        return self.provider is not None

    async def send_otp(self, phone: str, code: str) -> bool:
        if not self.provider:
            return False
        return await self.provider.send_otp(phone, code)

    async def send_temp_password(self, phone: str, temp_password: str) -> bool:
        if not self.provider:
            return False
        return await self.provider.send_text(
            phone, f"Your Blussit temporary password is {temp_password}. Log in and change it right away."
        )
