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
        (WhatsAppService), not a transport-level exception.

        NOTE: free-form text like this only reaches a recipient who has
        messaged the business number within the last 24h (WhatsApp's
        "customer service window") — it silently fails for anyone outside
        that window, which in practice means EVERY brand-new customer's
        first-ever OTP. send_template() is the one that actually works for
        a first contact; see WhatsAppService.send_otp for how the two are
        chosen between."""
        raise NotImplementedError

    @abstractmethod
    async def send_template(self, phone: str, template_name: str, language_code: str, body_params: list[str]) -> bool:
        """Sends a pre-approved WhatsApp template message — the only
        message type that can reach a recipient with NO open session
        (works for a genuinely first contact, e.g. a new customer's very
        first OTP). body_params fill the template's {{1}}, {{2}}, ...
        placeholders in order, all as plain text components."""
        raise NotImplementedError

    @abstractmethod
    async def send_interactive_list(self, phone: str, body: str, button: str, rows: list[dict]) -> bool:
        """WhatsApp interactive LIST message (the tap-to-open picker) —
        rows are [{"id", "title", "description"?}], max 10, title <=24
        chars, description <=72 (enforced by truncation here, not left to
        callers). The user's pick comes back on the webhook as
        interactive.list_reply.id — the booking bot's whole state machine
        keys off those ids."""
        raise NotImplementedError

    @abstractmethod
    async def send_buttons(self, phone: str, body: str, buttons: list[dict]) -> bool:
        """WhatsApp interactive reply-BUTTON message — buttons are
        [{"id", "title"}], max 3, title <=20 chars. Reply arrives as
        interactive.button_reply.id."""
        raise NotImplementedError

    @abstractmethod
    async def send_location_request(self, phone: str, body: str) -> bool:
        """WhatsApp's native "share your location" prompt
        (location_request_message) — the recipient gets a Send Location
        button that opens their map; the shared pin arrives on the webhook
        as messages[].location {latitude, longitude}. This is how the
        booking bot collects a doorstep location without the customer
        typing an address."""
        raise NotImplementedError


def _clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _normalize_rows(rows: list[dict]) -> list[dict]:
    out = []
    for row in rows[:10]:
        item = {"id": row["id"], "title": _clip(str(row["title"]), 24)}
        if row.get("description"):
            item["description"] = _clip(str(row["description"]), 72)
        out.append(item)
    return out


def _normalize_buttons(buttons: list[dict]) -> list[dict]:
    return [{"id": b["id"], "title": _clip(str(b["title"]), 20)} for b in buttons[:3]]


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

    async def send_template(self, phone: str, template_name: str, language_code: str, body_params: list[str]) -> bool:
        message = f"[template:{template_name}/{language_code}] " + ", ".join(body_params)
        logger.info("WHATSAPP [log provider, template] -> %s: %s", phone, message)
        await self.db.whatsapp_outbox.insert_one({
            "phone": phone,
            "message": message,
            "template_name": template_name,
            "provider": "log",
            "created_at": datetime.now(timezone.utc),
        })
        return True

    async def send_interactive_list(self, phone: str, body: str, button: str, rows: list[dict]) -> bool:
        rows = _normalize_rows(rows)
        logger.info("WHATSAPP [log provider, list] -> %s: %s %s", phone, body, [r["id"] for r in rows])
        await self.db.whatsapp_outbox.insert_one({
            "phone": phone,
            "message": body,
            "interactive_kind": "list",
            # Stored so tests (and debugging) can assert exactly which
            # options were offered — e.g. "was the admin-closed slot
            # really absent from what the customer saw".
            "options": rows,
            "provider": "log",
            "created_at": datetime.now(timezone.utc),
        })
        return True

    async def send_buttons(self, phone: str, body: str, buttons: list[dict]) -> bool:
        buttons = _normalize_buttons(buttons)
        logger.info("WHATSAPP [log provider, buttons] -> %s: %s %s", phone, body, [b["id"] for b in buttons])
        await self.db.whatsapp_outbox.insert_one({
            "phone": phone,
            "message": body,
            "interactive_kind": "buttons",
            "options": buttons,
            "provider": "log",
            "created_at": datetime.now(timezone.utc),
        })
        return True

    async def send_location_request(self, phone: str, body: str) -> bool:
        logger.info("WHATSAPP [log provider, location_request] -> %s: %s", phone, body)
        await self.db.whatsapp_outbox.insert_one({
            "phone": phone,
            "message": body,
            "interactive_kind": "location_request",
            "provider": "log",
            "created_at": datetime.now(timezone.utc),
        })
        return True


class MetaCloudWhatsAppProvider(WhatsAppProvider):
    """Sends real WhatsApp messages via Meta's Cloud API."""

    def __init__(self, db: AsyncIOMotorDatabase, access_token: str, phone_number_id: str, api_version: str):
        self.db = db
        self.access_token = access_token
        self.phone_number_id = phone_number_id
        self.api_version = api_version

    async def send(self, phone: str, message: str) -> bool:
        body = {
            "messaging_product": "whatsapp",
            "to": _to_e164_digits(phone),
            "type": "text",
            "text": {"body": message},
        }
        return await self._post(phone, message, body)

    async def send_template(self, phone: str, template_name: str, language_code: str, body_params: list[str]) -> bool:
        # Meta rejects template parameters containing newlines, tabs, or
        # 4+ consecutive spaces (error 132000) — flatten every param so a
        # multi-line notification text can never silently kill the send.
        params = [_flatten_param(p) for p in body_params]
        body = {
            "messaging_product": "whatsapp",
            "to": _to_e164_digits(phone),
            "type": "template",
            "template": {
                "name": template_name,
                "language": {"code": language_code},
                "components": [{"type": "body", "parameters": [{"type": "text", "text": p} for p in params]}] if params else [],
            },
        }
        return await self._post(phone, f"[template:{template_name}] {', '.join(params)}", body, template_name=template_name)

    async def send_interactive_list(self, phone: str, body: str, button: str, rows: list[dict]) -> bool:
        rows = _normalize_rows(rows)
        payload = {
            "messaging_product": "whatsapp",
            "to": _to_e164_digits(phone),
            "type": "interactive",
            "interactive": {
                "type": "list",
                "body": {"text": body},
                "action": {"button": _clip(button, 20), "sections": [{"title": "Options", "rows": rows}]},
            },
        }
        return await self._post(phone, f"[list] {body}", payload)

    async def send_buttons(self, phone: str, body: str, buttons: list[dict]) -> bool:
        buttons = _normalize_buttons(buttons)
        payload = {
            "messaging_product": "whatsapp",
            "to": _to_e164_digits(phone),
            "type": "interactive",
            "interactive": {
                "type": "button",
                "body": {"text": body},
                "action": {"buttons": [{"type": "reply", "reply": b} for b in buttons]},
            },
        }
        return await self._post(phone, f"[buttons] {body}", payload)

    async def send_location_request(self, phone: str, body: str) -> bool:
        payload = {
            "messaging_product": "whatsapp",
            "to": _to_e164_digits(phone),
            "type": "interactive",
            "interactive": {
                "type": "location_request_message",
                "body": {"text": body},
                "action": {"name": "send_location"},
            },
        }
        return await self._post(phone, f"[location_request] {body}", payload)

    async def _post(self, phone: str, log_message: str, body: dict, template_name: str | None = None) -> bool:
        url = f"https://graph.facebook.com/{self.api_version}/{self.phone_number_id}/messages"
        headers = {"Authorization": f"Bearer {self.access_token}", "Content-Type": "application/json"}
        outbox_doc = {
            "phone": phone,
            "message": log_message,
            "provider": "meta_cloud",
            "created_at": datetime.now(timezone.utc),
        }
        if template_name:
            outbox_doc["template_name"] = template_name
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                response = await client.post(url, json=body, headers=headers)
            ok = response.status_code < 300
            outbox_doc.update({"status_code": response.status_code, "ok": ok, "response_body": response.text[:2000]})
            # Meta's message id (wamid) — the join key for the delivery
            # `statuses` webhook events later. CRITICAL context: a 200 here
            # only means Meta ACCEPTED the message; free-form text to a
            # number with no open 24h session is accepted and then silently
            # dropped, and the drop is only ever reported via a later
            # statuses webhook (error 131047). Storing the wamid is what
            # lets WhatsAppBotService write that verdict back onto this
            # exact outbox row (delivery_status field) — without it, "ok:
            # true, never arrived" is undiagnosable.
            try:
                wamid = (response.json().get("messages") or [{}])[0].get("id")
                if wamid:
                    outbox_doc["wamid"] = wamid
            except Exception:  # noqa: BLE001 — malformed body already captured above
                pass
            await self.db.whatsapp_outbox.insert_one(outbox_doc)
            if not ok:
                logger.error("WhatsApp send failed (%s): %s", response.status_code, response.text[:500])
            return ok
        except httpx.HTTPError as exc:
            logger.error("WhatsApp send raised %s for %s", exc, phone)
            outbox_doc.update({"ok": False, "error": str(exc)})
            await self.db.whatsapp_outbox.insert_one(outbox_doc)
            return False


def _flatten_param(text: str) -> str:
    """Template parameters must be single-line: Meta rejects newlines,
    tabs, and runs of 4+ spaces outright."""
    return " ".join(str(text).split())


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
        # A template message (WHATSAPP_OTP_TEMPLATE_NAME) is the ONLY kind
        # that reaches a recipient with no open 24h session — i.e. the one
        # that actually works for a brand-new customer's first-ever OTP.
        # Free text below still works for anyone who already has an open
        # session (e.g. re-verifying, or testing against your own number
        # that's messaged the business account), and is the safe default
        # until a real "authentication"-category template is approved.
        if settings.WHATSAPP_OTP_TEMPLATE_NAME:
            return await self.provider.send_template(phone, settings.WHATSAPP_OTP_TEMPLATE_NAME, settings.WHATSAPP_OTP_TEMPLATE_LANGUAGE, [code])
        label = {"verification": "verify your phone", "password_reset": "reset your password"}.get(purpose, "verify your phone")
        return await self.provider.send(phone, f"Your CleanRide code to {label} is {code}. It expires in 10 minutes. Do not share this code with anyone.")

    async def send_temp_password(self, phone: str, temp_password: str) -> bool:
        # Template first (reaches a recipient with no open session — the
        # normal case for someone who just called the manager because they
        # can't log in); free text only as the unconfigured fallback.
        if settings.WHATSAPP_TEMP_PASSWORD_TEMPLATE_NAME:
            return await self.provider.send_template(
                phone, settings.WHATSAPP_TEMP_PASSWORD_TEMPLATE_NAME, settings.WHATSAPP_TEMPLATE_LANGUAGE, [temp_password]
            )
        return await self.provider.send(
            phone,
            f"Your CleanRide account password has been reset by our team. Temporary password: {temp_password}\n"
            "Please log in and change it right away. If you didn't request this, contact support immediately.",
        )

    async def send_generic(self, phone: str, title: str, message: str) -> bool:
        # The whole NotificationService→WhatsApp bridge flows through here
        # (booking updates to customers, alerts to managers/captains).
        # Recipients frequently have NO open session — a website customer
        # who never chatted with us, and staff essentially never — so
        # without the template these are accepted by Meta and silently
        # dropped. Template body must be "{{1}}: {{2}}".
        if settings.WHATSAPP_UPDATE_TEMPLATE_NAME:
            return await self.provider.send_template(
                phone, settings.WHATSAPP_UPDATE_TEMPLATE_NAME, settings.WHATSAPP_TEMPLATE_LANGUAGE, [title, message]
            )
        return await self.provider.send(phone, f"{title}: {message}")

    # Plain passthroughs used by the WhatsApp booking bot (see
    # whatsapp_bot_service.py) — kept on this wrapper so the bot, like
    # every other caller in the app, never touches a provider directly.
    async def send_text(self, phone: str, message: str) -> bool:
        return await self.provider.send(phone, message)

    async def send_list(self, phone: str, body: str, button: str, rows: list[dict]) -> bool:
        return await self.provider.send_interactive_list(phone, body, button, rows)

    async def send_buttons(self, phone: str, body: str, buttons: list[dict]) -> bool:
        return await self.provider.send_buttons(phone, body, buttons)

    async def send_location_request(self, phone: str, body: str) -> bool:
        return await self.provider.send_location_request(phone, body)
