"""
WhatsApp messaging — the single place every OTP, temp password, and
customer-facing update gets sent through. Provider-abstracted the same way
app/core/storage.py abstracts Cloudinary vs local disk: callers only ever
talk to WhatsAppService, never to a specific provider directly, so swapping
"log" for a real API later (or switching API vendors) never touches a
call site.

Two providers ship today:
  - LogWhatsAppProvider (default): never calls a real API. Writes every
    message to the whatsapp_outbox collection and logs it — lets the
    entire OTP/verification/notification flow be built, run, and tested
    end-to-end with zero external dependency, and gives dev/test a place
    to assert "was this actually sent" without mocking HTTP.
  - MetaCloudWhatsAppProvider: the official WhatsApp Cloud API
    (https://developers.facebook.com/docs/whatsapp/cloud-api). Selected
    automatically once WHATSAPP_ACCESS_TOKEN + WHATSAPP_PHONE_NUMBER_ID
    are both set in .env — see get_whatsapp_provider().
"""
import logging
from abc import ABC, abstractmethod
from datetime import datetime, timezone

import httpx
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.core.config import settings

logger = logging.getLogger(__name__)


class WhatsAppProvider(ABC):
    @abstractmethod
    async def send(self, phone: str, message: str) -> bool:
        """Returns True if the message was handed off successfully (queued/
        accepted by the provider), False otherwise. Never raises — a
        delivery failure is a business-logic decision for the caller
        (WhatsAppService), not a transport-level exception."""
        raise NotImplementedError


class LogWhatsAppProvider(WhatsAppProvider):
    def __init__(self, db: AsyncIOMotorDatabase):
        self.db = db

    async def send(self, phone: str, message: str) -> bool:
        logger.info("WHATSAPP [log provider] -> %s: %s", phone, message)
        await self.db.whatsapp_outbox.insert_one({
            "phone": phone,
            "message": message,
            "provider": "log",
            "created_at": datetime.now(timezone.utc),
        })
        return True


class MetaCloudWhatsAppProvider(WhatsAppProvider):
    """Sends a real WhatsApp message via Meta's Cloud API. Requires the
    recipient to have messaged the business number within the last 24h,
    OR the message to use a pre-approved template — plain free-text
    (as sent here) only works inside that 24h session window. Once
    template messages are needed (e.g. the very first OTP to a brand new
    number), swap the text body below for a `template` payload; the
    provider interface (`send(phone, message)`) doesn't need to change,
    only this method's internal request shape."""

    def __init__(self, db: AsyncIOMotorDatabase, access_token: str, phone_number_id: str, api_version: str):
        self.db = db
        self.access_token = access_token
        self.phone_number_id = phone_number_id
        self.api_version = api_version

    async def send(self, phone: str, message: str) -> bool:
        url = f"https://graph.facebook.com/{self.api_version}/{self.phone_number_id}/messages"
        headers = {"Authorization": f"Bearer {self.access_token}", "Content-Type": "application/json"}
        body = {
            "messaging_product": "whatsapp",
            "to": _to_e164_digits(phone),
            "type": "text",
            "text": {"body": message},
        }
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                response = await client.post(url, json=body, headers=headers)
            ok = response.status_code < 300
            await self.db.whatsapp_outbox.insert_one({
                "phone": phone,
                "message": message,
                "provider": "meta_cloud",
                "status_code": response.status_code,
                "ok": ok,
                "response_body": response.text[:2000],
                "created_at": datetime.now(timezone.utc),
            })
            if not ok:
                logger.error("WhatsApp send failed (%s): %s", response.status_code, response.text[:500])
            return ok
        except httpx.HTTPError as exc:
            logger.error("WhatsApp send raised %s for %s", exc, phone)
            await self.db.whatsapp_outbox.insert_one({
                "phone": phone, "message": message, "provider": "meta_cloud", "ok": False, "error": str(exc),
                "created_at": datetime.now(timezone.utc),
            })
            return False


def _to_e164_digits(phone: str) -> str:
    """Meta's API wants digits only (no +, spaces, or dashes), with country
    code. This app stores Indian numbers without a country code in most
    places (see phone validators elsewhere) — default to +91 when the
    number looks like a bare 10-digit Indian mobile number; pass anything
    else through unchanged (already has a country code, or a test/fake
    number that isn't meant to be dialable)."""
    digits = "".join(ch for ch in phone if ch.isdigit())
    if len(digits) == 10:
        return f"91{digits}"
    return digits


def get_whatsapp_provider(db: AsyncIOMotorDatabase) -> WhatsAppProvider:
    if settings.WHATSAPP_PROVIDER == "meta_cloud" and settings.WHATSAPP_ACCESS_TOKEN and settings.WHATSAPP_PHONE_NUMBER_ID:
        return MetaCloudWhatsAppProvider(db, settings.WHATSAPP_ACCESS_TOKEN, settings.WHATSAPP_PHONE_NUMBER_ID, settings.WHATSAPP_API_VERSION)
    # Falls back to the safe log provider for "meta_cloud" selected but
    # half-configured (blank token/phone id) just as much as for the
    # explicit default — a typo'd or incomplete .env should never crash a
    # booking/OTP flow, only silently skip real delivery.
    return LogWhatsAppProvider(db)


class WhatsAppService:
    """Business-facing wrapper — templated messages for every place the
    app needs to reach a user on WhatsApp. Never raises on send failure;
    callers decide whether a failed send should block their flow (OTP
    delivery does — see AuthService.request_otp) or just be logged and
    moved past (a booking confirmation ping shouldn't undo an already-
    successful booking)."""

    def __init__(self, db: AsyncIOMotorDatabase):
        self.provider = get_whatsapp_provider(db)

    async def send_otp(self, phone: str, code: str, purpose: str = "verification") -> bool:
        label = {"verification": "verify your phone", "password_reset": "reset your password"}.get(purpose, "verify your phone")
        return await self.provider.send(phone, f"Your CleanRide code to {label} is {code}. It expires in 10 minutes. Do not share this code with anyone.")

    async def send_temp_password(self, phone: str, temp_password: str) -> bool:
        return await self.provider.send(
            phone,
            f"Your CleanRide account password has been reset by our team. Temporary password: {temp_password}\n"
            "Please log in and change it right away. If you didn't request this, contact support immediately.",
        )

    async def send_generic(self, phone: str, title: str, message: str) -> bool:
        return await self.provider.send(phone, f"{title}: {message}")
