"""
Server-side verification for the MSG91 OTP widget.

Flow: the widget (loaded on our pages from verify.msg91.com) sends and
verifies the OTP entirely on MSG91's side — SMS / WhatsApp / email
channels, no DLT paperwork on ours. Successful verification hands the
browser a JWT access token; the ONLY thing the backend ever trusts is
MSG91's answer to /api/v5/widget/verifyAccessToken for that token.

Binding matters: a valid token proves *some* identifier completed OTP —
not necessarily the phone the caller claims. After MSG91 confirms the
token, we extract the verified identifier (from MSG91's response when
present, else from the now-MSG91-validated JWT payload) and REQUIRE it
to match the expected phone. No identifier → fail closed.
"""
import asyncio
import base64
import json
import logging

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


def _digits(value: str) -> str:
    return "".join(ch for ch in str(value) if ch.isdigit())


def _local_phone(identifier: str) -> str:
    return validate_indian_mobile(str(identifier)) or _digits(identifier)


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

        # Identifier binding — MSG91 echoes it in some responses; the JWT
        # payload (now known-genuine) carries it otherwise.
        candidates = []
        message = body.get("message")
        if isinstance(message, dict):
            candidates.extend(str(v) for v in message.values())
        elif message:
            candidates.append(str(message))
        payload = _jwt_payload(access_token)
        for key in ("identifier", "mobile", "phone", "identity", "sub"):
            if payload.get(key):
                candidates.append(str(payload[key]))

        expected = _local_phone(expected_phone)
        for candidate in candidates:
            if _local_phone(candidate) == expected and expected:
                return True
        logger.warning(
            "MSG91 token valid but identifier mismatch/absent (expected …%s; response=%s payload_keys=%s)",
            expected[-4:], str(body)[:120], list(payload.keys()),
        )
        return False
