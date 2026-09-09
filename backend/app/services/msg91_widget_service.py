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
import base64
import json
import logging

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)

VERIFY_URL = "https://control.msg91.com/api/v5/widget/verifyAccessToken"


def _digits(value: str) -> str:
    return "".join(ch for ch in str(value) if ch.isdigit())


def _local_phone(identifier: str) -> str:
    d = _digits(identifier)
    if len(d) == 12 and d.startswith("91"):
        return d[2:]
    return d


def _jwt_payload(token: str) -> dict:
    """Decodes the JWT payload WITHOUT signature verification — only ever
    called after MSG91 has confirmed the token is genuine, purely to read
    which identifier it was issued for."""
    try:
        part = token.split(".")[1]
        part += "=" * (-len(part) % 4)
        return json.loads(base64.urlsafe_b64decode(part))
    except Exception:  # noqa: BLE001
        return {}


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

    async def verify_access_token(self, access_token: str, expected_phone: str) -> bool:
        """True only if MSG91 confirms the token AND it was issued for
        expected_phone. Never raises."""
        if not settings.MSG91_AUTH_KEY or not access_token:
            return False
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                response = await client.post(
                    VERIFY_URL,
                    json={"authkey": settings.MSG91_AUTH_KEY, "access-token": access_token},
                    headers={"Content-Type": "application/json"},
                )
            body = response.json() if response.headers.get("content-type", "").startswith("application/json") else {}
        except (httpx.HTTPError, ValueError) as exc:
            logger.error("MSG91 verifyAccessToken raised %s", exc)
            return False
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
