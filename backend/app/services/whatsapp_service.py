"""
WhatsApp messaging — the single place every OTP, temp password, and
customer-facing update gets sent through. Provider-abstracted the same way
app/core/storage.py abstracts Cloudflare R2 vs local disk: callers only ever
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
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import httpx
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.core.config import settings
from app.core.http_client import shared_client
from app.utils.phone import is_business_whatsapp_number, to_whatsapp_e164

logger = logging.getLogger(__name__)

# Meta delivers free-form (non-template) messages only while the customer's
# 24-hour service window is open — outside it the send API still answers
# 200 and the message is silently dropped. A small margin so a send that
# leaves just before the window closes isn't dropped on arrival.
SESSION_WINDOW = timedelta(hours=24) - timedelta(minutes=10)

# Meta error code for "this recipient stopped marketing messages from you".
MARKETING_OPT_OUT_ERROR = 131050
# Meta error codes that mean OUR credentials are the problem (expired or
# revoked token, missing permission) — every send fails the same way until
# someone fixes the configuration, so they're never a per-message refusal.
META_AUTH_ERROR_CODES = frozenset({190, 10, 200})
# Template list pages: Meta returns at most `limit` per page; a WABA with
# more templates than that used to lose everything past the first page.
TEMPLATE_LIST_PAGE_SIZE = 100
TEMPLATE_LIST_MAX_PAGES = 50
# Where the last COMPLETE template sync is recorded (db.settings). Once one
# has run, a configured template name Meta didn't return is a typo or a
# deleted template — unusable — instead of being trusted forever.
TEMPLATE_SYNC_STATE_KEY = "whatsapp_template_sync"
_warned_unknown_templates: set[str] = set()


def mask_phone(phone: str | None) -> str:
    """A phone number as it may appear in a log line: last 4 digits only."""
    digits = "".join(ch for ch in str(phone or "") if ch.isdigit())
    return f"…{digits[-4:]}" if digits else "?"


def _local10(phone: str | None) -> str:
    digits = "".join(ch for ch in str(phone or "") if ch.isdigit())
    return digits[-10:] if len(digits) >= 10 else digits


# Numbers whose 24-hour window is open for the code running inside
# `inbound_session()` — the booking bot answering a message that just
# arrived. Its replies can't be outside the window by definition, so they
# skip the per-send lookup (and still go out if recording the inbound
# message hiccupped). Same ContextVar scoping as whatsapp_muted_for.
_open_sessions: ContextVar[frozenset] = ContextVar("whatsapp_open_sessions", default=frozenset())


@contextmanager
def inbound_session(phone_or_wa_id: str):
    token = _open_sessions.set(_open_sessions.get() | {_local10(phone_or_wa_id)})
    try:
        yield
    finally:
        _open_sessions.reset(token)


async def set_marketing_opt_out(db: AsyncIOMotorDatabase, phone_or_wa_id: str, opted_out: bool, source: str) -> int:
    """Records a customer's marketing choice made ON WhatsApp ("STOP" to the
    bot, Meta's 131050 refusal, the user_preferences webhook). Marketing
    templates check users.marketing_opt_out (NotificationService.notify);
    utility updates are unaffected. Returns how many accounts changed."""
    local = _local10(phone_or_wa_id)
    if not local:
        return 0
    now = datetime.now(timezone.utc)
    result = await db.users.update_many(
        {"phone": {"$in": [local, f"91{local}"]}, "role": "customer"},
        {"$set": {"marketing_opt_out": opted_out, "marketing_opt_out_source": source, "marketing_opt_out_at": now, "updated_at": now}},
    )
    await db.whatsapp_conversations.update_many(
        {"$or": [{"wa_id": f"91{local}"}, {"wa_id": local}, {"phone": local}]}, {"$set": {"marketing_opt_out": opted_out}}
    )
    if result.modified_count:
        logger.info("Marketing opt-out=%s recorded for %s (%s)", opted_out, mask_phone(local), source)
    return result.modified_count


# Header used when a notification's body already names what its title
# names ("Star Wash booked" / "Star Wash on 8 Oct … is confirmed"): the
# generic two-line template would otherwise say it twice.
_UPDATE_HEADERS = {
    "booking": "Booking update",
    "subscription": "Plan update",
    "complaint": "Complaint update",
    "promotion": "BLUSSIT update",
    "system": "BLUSSIT update",
}
_TRIM = " :;,.-–—"


def compose_update(title: str, message: str, notification_type=None) -> tuple[str, str]:
    """(header, body) for the generic update template ("*{{1}}*\n{{2}}")
    and its free-text fallback ("{title}: {message}"), so no notification
    reads "Star Wash booked, Star Wash on …":
      - a body that starts with the whole title keeps the title once;
      - a body that starts with the title's subject (the title minus its
        last, status word) goes under a neutral header for its kind;
      - anything else is sent exactly as written."""
    head = " ".join(str(title or "").split())
    body = " ".join(str(message or "").split())
    kind = getattr(notification_type, "value", notification_type) or "system"
    neutral = _UPDATE_HEADERS.get(str(kind), "BLUSSIT update")
    if not head:
        return neutral, body or neutral
    if not body:
        return head, head
    folded = body.casefold()
    if folded.startswith(head.casefold()):
        rest = body[len(head):].lstrip(_TRIM)
        return (head, rest) if rest else (head, body)
    words = head.split()
    if len(words) >= 2:
        subject = " ".join(words[:-1]).rstrip(_TRIM)
        if len(subject) >= 3 and folded.startswith(subject.casefold() + " "):
            return neutral, body[:1].upper() + body[1:]
    return head, body

# Outbox rows live for a year and log lines leave the box — a one-time code
# or a temporary password must sit in neither in clear. Rows of these kinds
# keep only a non-secret description (the CRM thread already hides them).
# Local dev tools (settings.dev_tools_active) keep the text readable: that
# database is on this machine, and its OTP is the fixed dev code anyway.
SECRET_KINDS = {"otp": "OTP sent (••••••)", "temp_password": "Temporary password sent (••••••)"}


def redact_secret(kind: str | None, text: str, template_name: str | None = None) -> str:
    """What may be stored/logged for a message of this kind."""
    if kind not in SECRET_KINDS or settings.dev_tools_active:
        return text
    label = SECRET_KINDS[kind]
    return f"[template:{template_name}] {label}" if template_name else label


@dataclass(frozen=True)
class SendOutcome:
    """A send attempt in enough detail to choose a fallback: truthy when
    accepted. transport_failure = Meta never answered usefully (timeout,
    connection error, 5xx, throttled) — another send right now would only
    wait out the same outage, unlike a refusal of this one message."""
    ok: bool
    transport_failure: bool = False
    # Why nothing was sent:
    # "no_template" = free text was the only option and the 24-hour window
    #   is closed (production) — the message would have been silently dropped;
    # "business_number" = addressed to our own WhatsApp number (Meta refuses);
    # "auth" = Meta refused OUR credentials (401/403, token expired) — a
    #   configuration problem, not this message;
    # "opted_out" = the template is MARKETING and the customer opted out.
    reason: str | None = None

    def __bool__(self) -> bool:
        return self.ok


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
    async def send_template(
        self, phone: str, template_name: str, language_code: str, body_params: list[str], button_param: str | None = None, otp_button: bool = False
    ) -> bool:
        """Sends a pre-approved WhatsApp template message — the only
        message type that can reach a recipient with NO open session
        (works for a genuinely first contact, e.g. a new customer's very
        first OTP). body_params fill the template's {{1}}, {{2}}, ...
        placeholders in order, all as plain text components.

        button_param: only for a template whose URL button itself has a
        variable suffix (see create_template's has_url_param) — e.g.
        "https://blussit.com/app/bookings/{{1}}" — filled in as that
        button's own parameter. Passing this for a template with a STATIC
        button (no variable) makes Meta reject the whole send, so callers
        must only pass it when the template is known to expect one.

        otp_button: only for an AUTHENTICATION-category template created
        with a COPY_CODE button (see WhatsAppCrmService.create_otp_template)
        — the button component needs a different shape than a URL button
        (sub_type "copy_code", a "coupon_code" parameter instead of
        "text"), filled with the same code as body_params[0]. Mutually
        exclusive with button_param."""
        raise NotImplementedError

    async def send_template_outcome(
        self, phone: str, template_name: str, language_code: str, body_params: list[str], button_param: str | None = None, extra: dict | None = None
    ) -> SendOutcome:
        """send_template, reporting WHY a send failed (see SendOutcome).
        Providers without a network leg can only ever succeed or refuse."""
        return SendOutcome(await self.send_template(phone, template_name, language_code, body_params, button_param=button_param, extra=extra))

    async def send_outcome(self, phone: str, message: str, extra: dict | None = None) -> SendOutcome:
        """send (free text), reporting WHY a send failed."""
        return SendOutcome(await self.send(phone, message, extra=extra))

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

    async def list_templates(self) -> list[dict] | None:
        """Meta's approved templates. None = this provider has no template
        catalogue (the log provider) — sync_templates_if_due then has
        nothing to sync instead of failing every few minutes."""
        return None

    async def fetch_media(self, media_id: str) -> tuple[bytes, str] | None:
        """An inbound message's media. None = unavailable (the log provider
        stores none) — the CRM route answers "Media unavailable", not a 500."""
        return None


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

    async def send(self, phone: str, message: str, extra: dict | None = None) -> bool:
        message = redact_secret((extra or {}).get("kind"), message)
        logger.info("WHATSAPP [log provider] -> %s: %s", mask_phone(phone), message)
        await self.db.whatsapp_outbox.insert_one({
            "phone": phone,
            "message": message,
            "provider": "log",
            "created_at": datetime.now(timezone.utc),
            **(extra or {}),
        })
        return True

    async def send_template(
        self,
        phone: str,
        template_name: str,
        language_code: str,
        body_params: list[str],
        button_param: str | None = None,
        otp_button: bool = False,
        extra: dict | None = None,
    ) -> bool:
        message = f"[template:{template_name}/{language_code}] " + ", ".join(body_params)
        if button_param:
            message += f" [button_param={button_param}]"
        if otp_button:
            message += " [otp_copy_code_button]"
        message = redact_secret((extra or {}).get("kind"), message, template_name)
        logger.info("WHATSAPP [log provider, template] -> %s: %s", mask_phone(phone), message)
        await self.db.whatsapp_outbox.insert_one({
            "phone": phone,
            "message": message,
            "template_name": template_name,
            "provider": "log",
            "created_at": datetime.now(timezone.utc),
            **(extra or {}),
        })
        return True

    async def send_media(self, phone: str, media_type: str, media_id: str, caption: str = "", filename: str = "", extra: dict | None = None) -> bool:
        await self.db.whatsapp_outbox.insert_one({
            "phone": phone,
            "message": caption or f"[{media_type}]",
            "media_type": media_type,
            "media_id": media_id,
            "filename": filename or None,
            "provider": "log",
            "created_at": datetime.now(timezone.utc),
            **(extra or {}),
        })
        return True

    async def upload_media(self, content: bytes, mime_type: str, filename: str = "upload") -> str | None:
        # Dev/test stand-in: no real store, but the send flow stays testable.
        return f"log-media-{datetime.now(timezone.utc).timestamp():.0f}"

    async def send_interactive_list(self, phone: str, body: str, button: str, rows: list[dict]) -> bool:
        rows = _normalize_rows(rows)
        logger.info("WHATSAPP [log provider, list] -> %s: %s %s", mask_phone(phone), body, [r["id"] for r in rows])
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
        logger.info("WHATSAPP [log provider, buttons] -> %s: %s %s", mask_phone(phone), body, [b["id"] for b in buttons])
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
        logger.info("WHATSAPP [log provider, location_request] -> %s: %s", mask_phone(phone), body)
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

    async def send(self, phone: str, message: str, extra: dict | None = None) -> bool:
        return (await self.send_outcome(phone, message, extra=extra)).ok

    async def send_outcome(self, phone: str, message: str, extra: dict | None = None) -> SendOutcome:
        body = {
            "messaging_product": "whatsapp",
            "to": _to_e164_digits(phone),
            "type": "text",
            "text": {"body": message},
        }
        return await self._post_outcome(phone, redact_secret((extra or {}).get("kind"), message), body, extra=extra)

    async def send_template(
        self,
        phone: str,
        template_name: str,
        language_code: str,
        body_params: list[str],
        button_param: str | None = None,
        otp_button: bool = False,
        extra: dict | None = None,
    ) -> bool:
        return (await self._template_outcome(phone, template_name, language_code, body_params, button_param, otp_button, extra)).ok

    async def send_template_outcome(
        self, phone: str, template_name: str, language_code: str, body_params: list[str], button_param: str | None = None, extra: dict | None = None
    ) -> SendOutcome:
        return await self._template_outcome(phone, template_name, language_code, body_params, button_param, False, extra)

    async def _template_outcome(
        self,
        phone: str,
        template_name: str,
        language_code: str,
        body_params: list[str],
        button_param: str | None,
        otp_button: bool,
        extra: dict | None,
    ) -> SendOutcome:
        # Meta rejects template parameters containing newlines, tabs, or
        # 4+ consecutive spaces (error 132000) — flatten every param so a
        # multi-line notification text can never silently kill the send.
        params = [_flatten_param(p) for p in body_params]
        components = [{"type": "body", "parameters": [{"type": "text", "text": p} for p in params]}] if params else []
        if button_param:
            # Fills the {{1}} in a URL button whose template was created
            # with one (see WhatsAppCrmService.create_template's
            # has_url_param) — index "0" is the button's position, always
            # the first (and only) button on every BLUSSIT template.
            components.append({"type": "button", "sub_type": "url", "index": "0", "parameters": [{"type": "text", "text": _flatten_param(button_param)}]})
        elif otp_button and params:
            # The OTP template's "Copy Code" button — a fundamentally
            # different component shape than a URL button (Meta's own
            # design for AUTHENTICATION templates), filled with the same
            # code as the body.
            # Meta (v25) wants this button as a URL-type component carrying the
            # code as text — the old copy_code/coupon_code shape is refused
            # with 132018 "Button at index 0 must be of type Url" (every OTP
            # send failed on it, so logins fell back to SMS).
            components.append({"type": "button", "sub_type": "url", "index": "0", "parameters": [{"type": "text", "text": params[0]}]})
        body = {
            "messaging_product": "whatsapp",
            "to": _to_e164_digits(phone),
            "type": "template",
            "template": {
                "name": template_name,
                "language": {"code": language_code},
                "components": components,
            },
        }
        log_text = f"[template:{template_name}] {', '.join(params)}" + (f" [button={button_param}]" if button_param else "")
        log_text = redact_secret((extra or {}).get("kind"), log_text, template_name)
        return await self._post_outcome(phone, log_text, body, template_name=template_name, extra=extra)

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


    # -- CRM extensions: media + template management ---------------------
    async def send_media(self, phone: str, media_type: str, media_id: str, caption: str = "", filename: str = "", extra: dict | None = None) -> bool:
        if media_type not in ("image", "document", "video", "audio"):
            return False
        media_obj: dict = {"id": media_id}
        if caption and media_type in ("image", "document", "video"):
            media_obj["caption"] = caption
        if filename and media_type == "document":
            media_obj["filename"] = filename
        body = {"messaging_product": "whatsapp", "to": _to_e164_digits(phone), "type": media_type, media_type: media_obj}
        merged = {"media_type": media_type, "media_id": media_id, **({"filename": filename} if filename else {}), **(extra or {})}
        return await self._post(phone, caption or f"[{media_type}]", body, extra=merged)

    async def upload_media(self, content: bytes, mime_type: str, filename: str = "upload") -> str | None:
        url = f"https://graph.facebook.com/{self.api_version}/{self.phone_number_id}/media"
        try:
            r = await _meta_http().post(
                url,
                headers={"Authorization": f"Bearer {self.access_token}"},
                data={"messaging_product": "whatsapp"},
                files={"file": (filename, content, mime_type)},
                timeout=60,
            )
            if r.status_code < 300:
                return r.json().get("id")
            logger.error("WhatsApp media upload failed (%s): %s", r.status_code, r.text[:300])
        except httpx.HTTPError as exc:
            logger.error("WhatsApp media upload raised %s", exc)
        return None

    async def fetch_media(self, media_id: str) -> tuple[bytes, str] | None:
        """Two-step Meta download: resolve the (short-lived, token-gated)
        CDN URL, then fetch the bytes with the same token. The raw URL is
        never handed to the frontend — this proxy is the only exposure."""
        headers = {"Authorization": f"Bearer {self.access_token}"}
        try:
            client = _meta_http()
            meta = await client.get(f"https://graph.facebook.com/{self.api_version}/{media_id}", headers=headers, timeout=30)
            if meta.status_code >= 300:
                return None
            info = meta.json()
            blob = await client.get(info.get("url", ""), headers=headers, timeout=30)
            if blob.status_code >= 300:
                return None
            return blob.content, info.get("mime_type", "application/octet-stream")
        except httpx.HTTPError as exc:
            logger.error("WhatsApp media fetch raised %s for %s", exc, media_id)
            return None

    async def list_templates(self) -> list[dict] | None:
        """EVERY template on the WABA, following Meta's paging. None when
        any page fails — a partial list must never pass for a complete one
        (a template missing from it would look deleted)."""
        if not settings.WHATSAPP_BUSINESS_ACCOUNT_ID:
            return None
        url: str | None = f"https://graph.facebook.com/{self.api_version}/{settings.WHATSAPP_BUSINESS_ACCOUNT_ID}/message_templates"
        params: dict | None = {
            "fields": "name,status,category,language,components,rejected_reason,quality_score",
            "limit": TEMPLATE_LIST_PAGE_SIZE,
        }
        templates: list[dict] = []
        try:
            for _ in range(TEMPLATE_LIST_MAX_PAGES):
                r = await _meta_http().get(url, headers={"Authorization": f"Bearer {self.access_token}"}, params=params, timeout=30)
                if r.status_code >= 300:
                    logger.error("WhatsApp template list failed (%s): %s", r.status_code, r.text[:300])
                    return None
                body = r.json()
                templates.extend(body.get("data") or [])
                url = (body.get("paging") or {}).get("next")
                params = None  # the "next" URL already carries them
                if not url:
                    return templates
            logger.error("WhatsApp template list stopped after %d pages — not treated as complete", TEMPLATE_LIST_MAX_PAGES)
        except (httpx.HTTPError, ValueError) as exc:
            logger.error("WhatsApp template list raised %s", exc)
        return None

    async def create_template(self, payload: dict) -> dict:
        if not settings.WHATSAPP_BUSINESS_ACCOUNT_ID:
            return {"error": "WHATSAPP_BUSINESS_ACCOUNT_ID not configured"}
        url = f"https://graph.facebook.com/{self.api_version}/{settings.WHATSAPP_BUSINESS_ACCOUNT_ID}/message_templates"
        try:
            r = await _meta_http().post(url, headers={"Authorization": f"Bearer {self.access_token}", "Content-Type": "application/json"}, json=payload, timeout=30)
            body = r.json() if r.headers.get("content-type", "").startswith("application/json") else {"raw": r.text[:300]}
            if r.status_code < 300:
                return body
            return {"error": (body.get("error") or {}).get("error_user_msg") or (body.get("error") or {}).get("message") or r.text[:200]}
        except httpx.HTTPError as exc:
            return {"error": str(exc)}

    async def _post(self, phone: str, log_message: str, body: dict, template_name: str | None = None, extra: dict | None = None) -> bool:
        return (await self._post_outcome(phone, log_message, body, template_name=template_name, extra=extra)).ok

    async def _post_outcome(self, phone: str, log_message: str, body: dict, template_name: str | None = None, extra: dict | None = None) -> SendOutcome:
        """One Cloud API send, always recorded in the outbox. A failed row
        carries `failure`: "transport" (timeout/connection/5xx/throttled —
        Meta itself was unreachable) or "rejected" (Meta refused this
        message: bad params, unknown template, invalid recipient)."""
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
        if extra:
            outbox_doc.update(extra)
        if is_business_whatsapp_number(phone):
            # Meta always refuses a send to our own number ("(#100) Invalid
            # parameter") — skip the call, keep the row so it's visible.
            logger.error("WhatsApp send skipped: %s is the business's own WhatsApp number", mask_phone(phone))
            outbox_doc.update({"ok": False, "error": "recipient is the business's own WhatsApp number", "failure": "business_number"})
            await self.db.whatsapp_outbox.insert_one(outbox_doc)
            return SendOutcome(False, reason="business_number")
        try:
            response = await _meta_http().post(url, json=body, headers=headers)
            ok = response.status_code < 300
            transport = response.status_code >= 500 or response.status_code == 429
            auth = not ok and not transport and _is_auth_failure(response)
            outbox_doc.update({"status_code": response.status_code, "ok": ok, "response_body": response.text[:2000]})
            if not ok:
                outbox_doc["failure"] = "transport" if transport else ("auth" if auth else "rejected")
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
            if auth:
                # Not this message: EVERY send fails until the token or its
                # permissions are fixed. Loud, with the fix in the line.
                logger.error(
                    "WhatsApp credentials rejected by Meta (%s) — the access token is expired/revoked or lacks "
                    "permission; check WHATSAPP_ACCESS_TOKEN / WHATSAPP_PHONE_NUMBER_ID. Send to %s not delivered: %s",
                    response.status_code, mask_phone(phone), response.text[:300],
                )
                return SendOutcome(False, reason="auth")
            if not ok:
                logger.error("WhatsApp send failed (%s) to %s: %s", response.status_code, mask_phone(phone), response.text[:500])
                await self._note_marketing_refusal(phone, response)
            return SendOutcome(ok, transport_failure=not ok and transport)
        except httpx.HTTPError as exc:
            # Pool timeouts included (httpx.PoolTimeout is an HTTPError):
            # transient, retried by the delivery queue.
            logger.error("WhatsApp send raised %r for %s", exc, mask_phone(phone))
            outbox_doc.update({"ok": False, "error": str(exc), "failure": "transport"})
            await self.db.whatsapp_outbox.insert_one(outbox_doc)
            return SendOutcome(False, transport_failure=True)


    async def _note_marketing_refusal(self, phone: str, response: httpx.Response) -> None:
        """Meta refusing a send with 131050 means the customer stopped our
        marketing messages — remember it so they aren't sent again."""
        try:
            code = (response.json().get("error") or {}).get("code")
        except Exception:  # noqa: BLE001 — not JSON: nothing to learn
            return
        if code == MARKETING_OPT_OUT_ERROR:
            await set_marketing_opt_out(self.db, phone, True, "meta_131050")


def _meta_http() -> httpx.AsyncClient:
    return shared_client("meta_whatsapp", 10)


def _flatten_param(text: str) -> str:
    """Template parameters must be single-line: Meta rejects newlines,
    tabs, and runs of 4+ spaces outright."""
    return " ".join(str(text).split())


def _to_e164_digits(phone: str) -> str:
    """Meta's API wants digits only, with country code. Every spelling of
    an Indian mobile ("09876543210", "+91 98765 43210", "9876543210")
    becomes 919876543210 — a leading trunk 0 used to be sent as-is
    ("09876543210"), which Meta can't route. See utils.phone."""
    return to_whatsapp_e164(phone)


def _is_auth_failure(response: httpx.Response) -> bool:
    """401/403, or Meta's OAuth/permission error codes on any 4xx."""
    if response.status_code in (401, 403):
        return True
    try:
        code = (response.json().get("error") or {}).get("code")
    except Exception:  # noqa: BLE001 — not JSON
        return False
    return code in META_AUTH_ERROR_CODES


async def last_complete_template_sync(db: AsyncIOMotorDatabase) -> datetime | None:
    """When a template sync last fetched Meta's WHOLE list (None = never)."""
    doc = await db.settings.find_one({"key": TEMPLATE_SYNC_STATE_KEY}, {"value": 1})
    return ((doc or {}).get("value") or {}).get("completed_at")


async def record_complete_template_sync(db: AsyncIOMotorDatabase, count: int) -> None:
    now = datetime.now(timezone.utc)
    await db.settings.update_one(
        {"key": TEMPLATE_SYNC_STATE_KEY},
        {"$set": {"value": {"completed_at": now, "count": count}, "description": "Last complete WhatsApp template sync (automatic)", "updated_at": now},
         "$setOnInsert": {"created_at": now}},
        upsert=True,
    )


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
        self.db = db
        self.provider = get_whatsapp_provider(db)

    # -- the 24-hour rule (DEP-04) ---------------------------------------

    async def session_open(self, phone: str) -> bool:
        """Has this number messaged us within the last 24 hours? Only then
        does Meta deliver free-form text (and interactive messages)."""
        local = _local10(phone)
        if not local:
            return False
        if local in _open_sessions.get():
            return True
        since = datetime.now(timezone.utc) - SESSION_WINDOW
        convo = await self.db.whatsapp_conversations.find_one(
            {"$or": [{"phone": local}, {"wa_id": f"91{local}"}, {"wa_id": local}], "last_inbound_at": {"$gte": since}}, {"_id": 1}
        )
        return convo is not None

    async def free_text_allowed(self, phone: str) -> bool:
        """Production never sends free text into a closed window — Meta
        would accept it and drop it, and our records would say "sent".
        Dev/test keep sending (log provider, testers' own phones)."""
        if settings.APP_ENV != "production":
            return True
        return await self.session_open(phone)

    async def _refuse(self, phone: str, text: str, extra: dict | None, reason: str, error: str) -> SendOutcome:
        """Records a message we deliberately did NOT send, visibly failed
        (CRM thread shows FAILED, delivery health counts it)."""
        kind = (extra or {}).get("kind")
        logger.warning("WhatsApp %s to %s not sent: %s", kind or "message", mask_phone(phone), error)
        await self.db.whatsapp_outbox.insert_one({
            "phone": phone,
            "message": redact_secret(kind, text),
            "provider": settings.WHATSAPP_PROVIDER,
            "ok": False,
            "failure": reason,
            "error": error,
            "created_at": datetime.now(timezone.utc),
            **(extra or {}),
        })
        return SendOutcome(False, reason=reason)

    async def _usable_template(self, name: str) -> dict | None:
        """A configured template name, unless Meta/admin say it can't be
        used (rejected, paused, disabled…). A name with no local row is
        trusted only until a template sync has completed: after that, a
        name Meta didn't return is a typo or a deleted template — sending
        it would fail on every message — so it's unusable (and logged).
        Returns {"name", "body", "language", "category"}."""
        if not name:
            return None
        tpl = await self.db.whatsapp_templates.find_one(
            {"name": name}, {"status": 1, "disabled": 1, "body": 1, "language": 1, "category": 1}
        )
        if tpl and ((tpl.get("status") and tpl["status"] != "APPROVED") or tpl.get("disabled")):
            return None
        if not tpl and await last_complete_template_sync(self.db):
            if name not in _warned_unknown_templates:
                _warned_unknown_templates.add(name)
                logger.error("WhatsApp template %r is configured but Meta doesn't have it (last sync) — not used", name)
            return None
        return {
            "name": name,
            "body": (tpl or {}).get("body") or "",
            "language": (tpl or {}).get("language") or settings.WHATSAPP_TEMPLATE_LANGUAGE,
            "category": (tpl or {}).get("category"),
        }

    async def otp_template_name(self) -> str:
        """The OTP template WhatsApp can use right now: the configured
        WHATSAPP_OTP_TEMPLATE_NAME, or — when none is configured —
        `blussit_otp` the moment Meta has approved it (Sync in the CRM
        Templates tab). "" = no template yet, WhatsApp OTP is free-text
        only and reaches just numbers with an open 24h chat."""
        if settings.WHATSAPP_OTP_TEMPLATE_NAME:
            return settings.WHATSAPP_OTP_TEMPLATE_NAME
        tpl = await self.db.whatsapp_templates.find_one(
            {"name": "blussit_otp", "status": "APPROVED", "category": "AUTHENTICATION", "disabled": {"$ne": True}}
        )
        return tpl["name"] if tpl else ""

    async def send_otp(self, phone: str, code: str, purpose: str = "verification") -> bool:
        # A template message (WHATSAPP_OTP_TEMPLATE_NAME) is the ONLY kind
        # that reaches a recipient with no open 24h session — i.e. the one
        # that actually works for a brand-new customer's first-ever OTP.
        # Free text below still works for anyone who already has an open
        # session (e.g. re-verifying, or testing against your own number
        # that's messaged the business account), and is the safe default
        # until a real "authentication"-category template is approved.
        #
        # A BRAND-NEW customer has never messaged us, so THIS free-text
        # fallback silently fails for exactly the people who need their
        # first OTP most — until WHATSAPP_OTP_TEMPLATE_NAME points at an
        # approved template (see create_otp_template), only re-verifies
        # and testers who already have an open session actually receive
        # this path's message; a first-time signup does not.
        template = await self.otp_template_name()
        if template:
            tpl = await self.db.whatsapp_templates.find_one({"name": template})
            otp_button = bool(tpl and tpl.get("category") == "AUTHENTICATION")
            return await self.provider.send_template(
                phone, template, settings.WHATSAPP_OTP_TEMPLATE_LANGUAGE, [code], otp_button=otp_button, extra={"kind": "otp"}
            )
        label = {"verification": "verify your phone", "password_reset": "reset your password"}.get(purpose, "verify your phone")
        text = f"Your Blussit code to {label} is {code}. It expires in 10 minutes. Do not share this code with anyone."
        if not await self.free_text_allowed(phone):
            await self._refuse(phone, text, {"kind": "otp"}, "no_template", "No approved OTP template and the 24-hour window is closed")
            return False
        return await self.provider.send(phone, text, extra={"kind": "otp"})

    async def can_deliver_temp_password(self, phone: str) -> bool:
        """Will send_temp_password actually reach this number? Lets a caller
        refuse BEFORE resetting anyone's password (DEP-04)."""
        if await self._usable_template(settings.WHATSAPP_TEMP_PASSWORD_TEMPLATE_NAME):
            return True
        return await self.free_text_allowed(phone)

    async def send_temp_password_outcome(self, phone: str, temp_password: str) -> SendOutcome:
        """send_temp_password with the reason when it can't go out:
        reason "no_template" = no usable temp-password template and (in
        production) the number has no open 24-hour chat, so nothing was
        sent — the caller must tell the staff member it wasn't delivered."""
        # Template first (reaches a recipient with no open session — the
        # normal case for someone who just called the manager because they
        # can't log in); free text only as the unconfigured fallback.
        template = await self._usable_template(settings.WHATSAPP_TEMP_PASSWORD_TEMPLATE_NAME)
        if template:
            return await self.provider.send_template_outcome(
                phone, template["name"], template["language"], [temp_password], extra={"kind": "temp_password"}
            )
        text = (
            f"Your Blussit account password has been reset by our team. Temporary password: {temp_password}\n"
            "Please log in and change it right away. If you didn't request this, contact support immediately."
        )
        if not await self.free_text_allowed(phone):
            return await self._refuse(
                phone, text, {"kind": "temp_password"}, "no_template",
                "No approved temp-password template and the 24-hour window is closed",
            )
        return await self.provider.send_outcome(phone, text, extra={"kind": "temp_password"})

    async def send_temp_password(self, phone: str, temp_password: str) -> bool:
        return (await self.send_temp_password_outcome(phone, temp_password)).ok

    async def update_template(self) -> dict | None:
        """The generic update template if it can be used right now."""
        return await self._usable_template(settings.WHATSAPP_UPDATE_TEMPLATE_NAME)

    async def send_generic_outcome(
        self, phone: str, title: str, message: str, notification_type=None, queue_id: str | None = None,
        marketing_opt_out: bool = False,
    ) -> SendOutcome:
        """The whole NotificationService→WhatsApp bridge flows through here
        (booking updates to customers, alerts to managers/captains).
        Recipients frequently have NO open session — a website customer
        who never chatted with us, and staff essentially never — so
        without the template these are accepted by Meta and silently
        dropped: production refuses free text then (reason "no_template")
        instead of pretending it was sent. Template body: "*{{1}}*\n{{2}}"
        (or the older "{{1}}: {{2}}.")."""
        head, body = compose_update(title, message, notification_type)
        extra = {"kind": "notify", **({"queue_id": queue_id} if queue_id else {})}
        template = await self.update_template()
        if template:
            if template.get("category") == "MARKETING" and marketing_opt_out:
                # Meta re-filed the update template as MARKETING: it may no
                # longer go to a customer who opted out of marketing.
                return await self._refuse(
                    phone, f"{head}: {body}", extra, "opted_out",
                    "The update template is categorised MARKETING and the customer opted out",
                )
            if "{{2}}." in template["body"]:
                body = body.rstrip(".") or body  # "…confirmed.." otherwise
            return await self.provider.send_template_outcome(
                phone, template["name"], template["language"], [head, body], extra=extra
            )
        text = f"{head}: {body}"
        if not await self.free_text_allowed(phone):
            return await self._refuse(
                phone, text, extra, "no_template",
                "No approved update template (WHATSAPP_UPDATE_TEMPLATE_NAME) and the 24-hour window is closed",
            )
        return await self.provider.send_outcome(phone, text, extra=extra)

    async def send_generic(self, phone: str, title: str, message: str, notification_type=None) -> bool:
        return (await self.send_generic_outcome(phone, title, message, notification_type)).ok

    # Plain passthroughs used by the WhatsApp booking bot (see
    # whatsapp_bot_service.py) — kept on this wrapper so the bot, like
    # every other caller in the app, never touches a provider directly.
    # All four are session messages: in production they go out only into
    # an open 24-hour window (always true for a bot reply — see
    # inbound_session); a proactive one into a closed window is recorded
    # as failed instead of being silently dropped by Meta.
    async def _session_send_allowed(self, phone: str, text: str) -> bool:
        if await self.free_text_allowed(phone):
            return True
        await self._refuse(phone, text, None, "window_closed", "The 24-hour window is closed — only a template can reach this number")
        return False

    async def send_text(self, phone: str, message: str) -> bool:
        if not await self._session_send_allowed(phone, message):
            return False
        return await self.provider.send(phone, message)

    async def send_list(self, phone: str, body: str, button: str, rows: list[dict]) -> bool:
        if not await self._session_send_allowed(phone, body):
            return False
        return await self.provider.send_interactive_list(phone, body, button, rows)

    async def send_buttons(self, phone: str, body: str, buttons: list[dict]) -> bool:
        if not await self._session_send_allowed(phone, body):
            return False
        return await self.provider.send_buttons(phone, body, buttons)

    async def send_location_request(self, phone: str, body: str) -> bool:
        if not await self._session_send_allowed(phone, body):
            return False
        return await self.provider.send_location_request(phone, body)

    # -- CRM passthroughs (agent-attributed sends, media, templates) -----
    async def send_agent_text(self, phone: str, message: str, agent_id: str) -> bool:
        return await self.provider.send(phone, message, extra={"kind": "agent", "agent_id": agent_id})

    async def send_agent_template(self, phone: str, template_name: str, language: str, params: list[str], agent_id: str) -> bool:
        return await self.provider.send_template(phone, template_name, language, params, extra={"kind": "agent", "agent_id": agent_id})

    async def send_agent_media(self, phone: str, media_type: str, media_id: str, caption: str, filename: str, agent_id: str) -> bool:
        return await self.provider.send_media(phone, media_type, media_id, caption, filename, extra={"kind": "agent", "agent_id": agent_id})

    async def send_event_template(self, phone: str, template_name: str, params: list[str], button_param: str | None = None) -> bool:
        return (await self.send_event_template_outcome(phone, template_name, params, button_param=button_param)).ok

    async def send_event_template_outcome(
        self, phone: str, template_name: str, params: list[str], button_param: str | None = None, queue_id: str | None = None,
        language: str | None = None,
    ) -> SendOutcome:
        """send_event_template, saying whether a failure was Meta being
        unreachable (NotificationService then retries later instead of
        trying its generic fallback into the same outage). `language`: the
        template row's own language (a template approved as "en" isn't
        found under "en_US")."""
        extra = {"kind": "event", **({"queue_id": queue_id} if queue_id else {})}
        return await self.provider.send_template_outcome(
            phone, template_name, language or settings.WHATSAPP_TEMPLATE_LANGUAGE, params, button_param=button_param, extra=extra
        )

    async def upload_media(self, content: bytes, mime_type: str, filename: str) -> str | None:
        return await self.provider.upload_media(content, mime_type, filename)

    async def fetch_media(self, media_id: str) -> tuple[bytes, str] | None:
        return await self.provider.fetch_media(media_id)

    async def list_templates(self) -> list[dict] | None:
        return await self.provider.list_templates()

    async def create_template(self, payload: dict) -> dict:
        return await self.provider.create_template(payload)


def whatsapp_config_problems() -> list[str]:
    """Production settings without which messages are refused (not sent)
    outside a customer's 24-hour window — for the boot/deploy checks."""
    if settings.APP_ENV != "production":
        return []
    problems = []
    if settings.WHATSAPP_PROVIDER != "meta_cloud" or not (settings.WHATSAPP_ACCESS_TOKEN and settings.WHATSAPP_PHONE_NUMBER_ID):
        problems.append("WhatsApp provider is not meta_cloud with a token and phone number id")
    if not settings.WHATSAPP_UPDATE_TEMPLATE_NAME:
        problems.append("WHATSAPP_UPDATE_TEMPLATE_NAME is empty — generic updates reach only open 24-hour chats")
    if not settings.WHATSAPP_TEMP_PASSWORD_TEMPLATE_NAME:
        problems.append("WHATSAPP_TEMP_PASSWORD_TEMPLATE_NAME is empty — staff password resets reach only open 24-hour chats")
    if not settings.WHATSAPP_BUSINESS_ACCOUNT_ID:
        problems.append("WHATSAPP_BUSINESS_ACCOUNT_ID is empty — template sync (and the dedicated event templates) cannot work")
    if not settings.WHATSAPP_BUSINESS_NUMBER:
        problems.append(
            "WHATSAPP_BUSINESS_NUMBER is empty — a staff phone equal to the business's own WhatsApp number "
            "can't be refused, and every message to it fails"
        )
    return problems
