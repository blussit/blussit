"""
Server-side verification for the MSG91 OTP widget.

Flow: the widget (loaded on our pages from verify.msg91.com) sends and
verifies the OTP entirely on MSG91's side — SMS / WhatsApp / email
channels, no DLT paperwork on ours. Successful verification hands the
browser a JWT access token; the ONLY thing the backend ever trusts is
MSG91's answer to /api/v5/widget/verifyAccessToken for that token.

Binding matters: a valid token proves *some* identifier completed OTP —
not necessarily the phone the caller claims. After MSG91 confirms the
token, we take the verified identifier from MSG91's OWN response and
REQUIRE it to match the expected phone; when the response names an
identifier that isn't that phone, the token is refused whatever the JWT
says. Only a response that names no identifier at all falls back to the
token's payload (unsigned as far as we can check — MSG91 vouched for the
token, not necessarily for every claim inside it). No identifier anywhere
→ fail closed.

Single use is enforced by the caller (AuthService.verify_phone_proof),
which owns the database.
"""
import asyncio
import base64
import json
import logging
import re

import httpx

from app.core.config import settings
from app.core.exceptions import AppException
from app.utils.phone import validate_indian_mobile

logger = logging.getLogger(__name__)

VERIFY_URL = "https://control.msg91.com/api/v5/widget/verifyAccessToken"
# A customer is waiting on this call (login / Verify & book); a stalled
# MSG91 must turn into a retryable error well before the browser gives up.
_VERIFY_TIMEOUT = httpx.Timeout(8.0, connect=4.0)
_VERIFY_DEADLINE_SECONDS = 10


class Msg91Unavailable(AppException):
    """MSG91 couldn't be asked (timeout, network, its 5xx) — distinct from
    a rejected token so the client keeps the token and lets the person
    retry, instead of telling them their correct code was wrong."""
    status_code = 503
    error_code = "OTP_SERVICE_UNAVAILABLE"

    def __init__(self, message: str = "Couldn't confirm the code right now — please try again.", details: dict | None = None):
        super().__init__(message, details)


# A phone number and nothing else: ASCII digits, an optional leading "+",
# and the separators people type. Anything more (letters, "@", other
# symbols, non-ASCII digits) is not a phone, however many digits it has.
_PHONE_SHAPED = re.compile(r"\+?[0-9\s\-()]+", re.ASCII)
# What counts as MSG91 NAMING an identifier in its answer: something
# phone-shaped with at least 10 digits, or an email. A status sentence
# ("OTP verified successfully") names nobody.
_EMAIL_SHAPED = re.compile(r"[^@\s]+@[^@\s]+")


def _bound_phone(identifier: object) -> str | None:
    """The canonical 10-digit phone `identifier` IS, or None. Never digits
    pulled out of a longer string: MSG91 also verifies emails, and an
    attacker-owned "9876543210@mail.example" must not count as proof of
    9876543210 (that would let anyone reset that number's password)."""
    text = str(identifier).strip()
    if not _PHONE_SHAPED.fullmatch(text):
        return None
    return validate_indian_mobile(text)


def _names_an_identifier(value: object) -> bool:
    """Does this value of MSG91's response name WHO was verified? Any
    non-ASCII digit counts as naming someone (never trust it to be "just a
    message") — it can then only fail the binding, never pass it."""
    text = str(value).strip()
    if not text:
        return False
    if _EMAIL_SHAPED.fullmatch(text):
        return True
    if any(ch.isdigit() and not ch.isascii() for ch in text):
        return True
    return bool(_PHONE_SHAPED.fullmatch(text)) and sum(ch.isdigit() for ch in text) >= 10


def _response_identifiers(body: dict) -> list[str]:
    """The identifiers MSG91's verify response itself names — in "message"
    (a string, or a dict of fields) or a top-level identifier field."""
    values: list = []
    message = body.get("message")
    if isinstance(message, dict):
        values.extend(message.values())
    elif message is not None:
        values.append(message)
    for key in ("identifier", "mobile", "phone", "email"):
        if body.get(key) is not None:
            values.append(body[key])
    return [str(v) for v in values if not isinstance(v, (dict, list)) and _names_an_identifier(v)]


def _jwt_payload(token: str) -> dict:
    """Decodes the JWT payload WITHOUT signature verification — only ever
    called after MSG91 has confirmed the token is genuine, purely to read
    which identifier it was issued for."""
    try:
        part = token.split(".")[1]
        part += "=" * (-len(part) % 4)
        data = json.loads(base64.urlsafe_b64decode(part))
    except Exception:  # noqa: BLE001
        return {}
    return data if isinstance(data, dict) else {}


class Msg91WidgetService:
    @property
    def enabled(self) -> bool:
        return bool(settings.MSG91_AUTH_KEY and settings.MSG91_WIDGET_ID and settings.MSG91_TOKEN_AUTH)

    def public_config(self) -> dict:
        """What the frontend needs to boot the widget. token_auth is
        client-embedded by design (it appears in every page that renders
        the widget); the server-side AUTH KEY is never exposed."""
        return {
            "enabled": self.enabled,
            "widget_id": settings.MSG91_WIDGET_ID if self.enabled else None,
            "token_auth": settings.MSG91_TOKEN_AUTH if self.enabled else None,
        }

    async def _post_verify(self, access_token: str):
        async with httpx.AsyncClient(timeout=_VERIFY_TIMEOUT) as client:
            return await client.post(
                VERIFY_URL,
                json={"authkey": settings.MSG91_AUTH_KEY, "access-token": access_token},
                headers={"Content-Type": "application/json"},
            )

    async def verify_access_token(self, access_token: str, expected_phone: str) -> bool:
        """True only if MSG91 confirms the token AND it was issued for
        expected_phone; False for any rejection. Raises Msg91Unavailable
        only when MSG91 itself can't answer (timeout / network / 5xx)."""
        if not settings.MSG91_AUTH_KEY or not access_token:
            return False
        try:
            response = await asyncio.wait_for(self._post_verify(access_token), _VERIFY_DEADLINE_SECONDS)
        except (httpx.HTTPError, asyncio.TimeoutError) as exc:
            logger.error("MSG91 verifyAccessToken unreachable: %r", exc)
            raise Msg91Unavailable() from exc
        if response.status_code >= 500:
            logger.error("MSG91 verifyAccessToken returned %s", response.status_code)
            raise Msg91Unavailable()
        try:
            body = response.json() if response.headers.get("content-type", "").startswith("application/json") else {}
        except ValueError:
            body = {}
        if not isinstance(body, dict):
            body = {}
        if response.status_code >= 300 or body.get("type") != "success":
            logger.info("MSG91 token rejected (%s): %s", response.status_code, str(body)[:200])
            return False

        # Identifier binding. MSG91's answer is the authority: when it names
        # who was verified, that is the only thing compared — a JWT payload
        # claiming a different number must never outvote it (if MSG91 ever
        # validated by session rather than the exact token, an edited
        # payload would otherwise bind any phone). The payload is read only
        # when the answer names nobody.
        expected = validate_indian_mobile(expected_phone)
        named = _response_identifiers(body)
        payload: dict = {}
        if named:
            candidates = named
        else:
            payload = _jwt_payload(access_token)
            candidates = [str(payload[k]) for k in ("identifier", "mobile", "phone", "identity", "sub") if payload.get(k)]
        if expected and any(_bound_phone(candidate) == expected for candidate in candidates):
            return True
        logger.warning(
            "MSG91 token valid but identifier mismatch/absent (expected …%s; from_response=%s; payload_keys=%s)",
            (expected or "")[-4:], bool(named), list(payload.keys()),
        )
        return False
