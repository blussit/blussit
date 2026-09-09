"""
Meta WhatsApp webhook receiver — the entry point for customers booking
directly over WhatsApp chat (see WhatsAppBotService for the actual
conversation engine and the centralization guarantees).

Two endpoints, exactly the contract Meta requires:
  GET  /whatsapp/webhook — one-time subscription verification handshake:
       Meta calls with hub.mode/hub.verify_token/hub.challenge; we echo
       the challenge back ONLY when the token matches our configured
       WHATSAPP_WEBHOOK_VERIFY_TOKEN.
  POST /whatsapp/webhook — the actual event deliveries. When
       WHATSAPP_APP_SECRET is configured, the X-Hub-Signature-256 header
       (HMAC-SHA256 of the raw body with the app secret) is verified so
       nobody but Meta can drive the booking bot with forged "incoming
       messages". Always answers 200 for well-formed authenticated
       payloads — Meta retries non-200s for up to 7 days, and a
       poison-message redelivery loop is worse than a logged failure
       (per-message errors are already swallowed inside handle_webhook).
"""
import hashlib
import hmac
import json
import logging

from fastapi import APIRouter, Depends, Request
from fastapi.responses import PlainTextResponse
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.core.config import settings
from app.core.dependencies import get_db
from app.core.exceptions import ForbiddenException
from app.services.whatsapp_bot_service import WhatsAppBotService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/whatsapp", tags=["WhatsApp"])


def verify_webhook_subscription(mode: str | None, token: str | None, challenge: str | None) -> str:
    """Pure verification logic, split out so it's directly unit-testable.
    An empty configured token never matches (refuses verification rather
    than accepting anything when unconfigured)."""
    if mode == "subscribe" and settings.WHATSAPP_WEBHOOK_VERIFY_TOKEN and token == settings.WHATSAPP_WEBHOOK_VERIFY_TOKEN:
        return challenge or ""
    raise ForbiddenException("Webhook verification failed")


def verify_webhook_signature(raw_body: bytes, signature_header: str | None) -> bool:
    """True if the payload authenticates. With no app secret configured
    this FAILS CLOSED outside DEBUG: an unsigned webhook in production
    would let anyone who learns the URL impersonate any customer by phone
    number (list/cancel/create their bookings via the bot). Dev keeps the
    old convenience of accepting unsigned payloads for local testing."""
    if not settings.WHATSAPP_APP_SECRET:
        return settings.DEBUG
    if not signature_header or not signature_header.startswith("sha256="):
        return False
    expected = hmac.new(settings.WHATSAPP_APP_SECRET.encode(), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(signature_header[len("sha256="):], expected)


@router.get("/webhook")
async def verify_subscription(request: Request):
    params = request.query_params
    challenge = verify_webhook_subscription(params.get("hub.mode"), params.get("hub.verify_token"), params.get("hub.challenge"))
    # Meta expects the bare challenge string back, not JSON.
    return PlainTextResponse(challenge)


@router.post("/webhook")
async def receive_events(request: Request, db: AsyncIOMotorDatabase = Depends(get_db)):
    raw = await request.body()
    if not verify_webhook_signature(raw, request.headers.get("X-Hub-Signature-256")):
        raise ForbiddenException("Invalid webhook signature")
    try:
        payload = json.loads(raw or b"{}")
    except json.JSONDecodeError:
        logger.warning("WhatsApp webhook delivered non-JSON body")
        return {"success": True, "processed": 0}
    result = await WhatsAppBotService(db).handle_webhook(payload if isinstance(payload, dict) else {})
    return {"success": True, **result}
