"""
Logging for the API process (DEP-09):

  - Every log record is MASKED at creation, whichever logger or handler it
    goes through (ours, uvicorn's, pytest's): Indian phone numbers keep only
    their last four digits; OTP codes, passwords, bearer/JWT tokens, token
    query parameters, bcrypt hashes and database-URI passwords are replaced
    by [REDACTED]. This is a safety net — code must still not log secrets
    (see whatsapp_service.redact_secret for the outbox).
  - A request id per HTTP request/WebSocket (incoming X-Request-ID when it
    is safe, else Cloud Run's trace id, else a fresh one), echoed in the
    X-Request-ID response header and attached to every log record.
  - Optional one-JSON-object-per-line output (LOG_FORMAT; automatic in
    production) with the fields Cloud Logging understands: severity,
    message, time, logger, request_id, source location, trace.
  - An env-gated error-tracking hook (SENTRY_DSN) that adds no dependency:
    it activates only when the sentry-sdk package is installed.
"""
from __future__ import annotations

import json
import logging
import os
import re
import sys
import uuid
from contextvars import ContextVar
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

REQUEST_ID_HEADER = "x-request-id"
request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)
trace_id_var: ContextVar[str | None] = ContextVar("trace_id", default=None)

# ---------------------------------------------------------------------------
# Redaction
# ---------------------------------------------------------------------------

# An Indian mobile number, optionally with +91 / 91 in front — a whole token,
# never digits inside a longer number or an id (pay_AbC9876543210Xy).
_PHONE_RE = re.compile(r"(?<![\w+])(\+?91[\s-]?)?[6-9]\d{5}(\d{4})(?!\w)")
_SECRET_PATTERNS: tuple[tuple[re.Pattern, str], ...] = (
    # Credentials inside a connection string: scheme://user:PASSWORD@host
    (re.compile(r"(//[^:/@\s]+:)[^@/\s]+@"), r"\1[REDACTED]@"),
    # Token-ish query parameters (the WebSocket JWT rides in ?token=).
    (re.compile(r"(?i)([?&](?:token|access_token|refresh_token|signature|razorpay_signature|key)=)[^&\s]+"), r"\1[REDACTED]"),
    (re.compile(r"(?i)\b(bearer)\s+[A-Za-z0-9\-._~+/]+=*"), r"\1 [REDACTED]"),
    # A bare JWT anywhere.
    (re.compile(r"\beyJ[A-Za-z0-9_-]{4,}\.[A-Za-z0-9_-]{4,}\.[A-Za-z0-9_-]+"), "[REDACTED-JWT]"),
    # bcrypt hashes.
    (re.compile(r"\$2[abxy]?\$\d{2}\$[./A-Za-z0-9]{20,}"), "[REDACTED-HASH]"),
    # key=value / "key": "value" for secret-named keys.
    (
        re.compile(
            r"(?i)([\"']?\b(?:password|new_password|old_password|current_password|temp_password|passwd|password_hash|"
            r"hashed_password|secret|client_secret|key_secret|app_secret|webhook_secret|api_key|apikey|auth_key|authkey|"
            r"access_token|refresh_token|id_token|authorization|otp|otp_code|code_hash)\b[\"']?\s*[:=]\s*[\"']?)"
            r"([^\"'\s,&}]+)"
        ),
        r"\1[REDACTED]",
    ),
    # "OTP is 482913", "verification code: 771144", "password 12345678".
    (
        re.compile(r"(?i)\b(otp|one[- ]time (?:password|code)|verification code|passcode|password)\b([^\d\n]{0,15}?)(?<!\w)(\d{4,8})(?!\w)"),
        r"\1\2[REDACTED]",
    ),
)


def _mask_phone(match: re.Match) -> str:
    prefix = match.group(1) or ""
    return f"{'+91' if prefix.startswith('+') else ''}******{match.group(2)}"


def redact(text: str) -> str:
    """Mask phone numbers and secrets in one string."""
    if not text:
        return text
    for pattern, replacement in _SECRET_PATTERNS:
        text = pattern.sub(replacement, text)
    return _PHONE_RE.sub(_mask_phone, text)


_PLAIN = (int, float, bool, type(None))


def _redact_arg(value: Any) -> Any:
    if isinstance(value, str):
        return redact(value)
    if isinstance(value, _PLAIN):
        return value
    # Anything else is rendered with str() by "%s" — only replace it when
    # rendering it would leak, so "%r" of an exception keeps its class name.
    try:
        rendered = str(value)
    except Exception:  # noqa: BLE001 — a broken __str__ must not break logging
        return value
    masked = redact(rendered)
    return value if masked == rendered else masked


# Loggers whose formatter unpacks record.args itself (uvicorn's access
# formatter) — their args keep their shape and are masked one by one.
_KEEP_ARGS_SHAPE = frozenset({"uvicorn.access"})


def redact_record(record: logging.LogRecord) -> logging.LogRecord:
    """Mask the message, exception and stack text of a record in place."""
    if getattr(record, "_blussit_redacted", False):
        return record
    if record.name in _KEEP_ARGS_SHAPE:
        if isinstance(record.msg, str):
            record.msg = redact(record.msg)
        if isinstance(record.args, tuple):
            record.args = tuple(_redact_arg(a) for a in record.args)
    else:
        # The whole rendered message, so context split between the format
        # string and its arguments ("OTP for %s is %s") is still caught.
        # Untouched when nothing needed masking.
        try:
            message = record.getMessage()
        except Exception:  # noqa: BLE001 — a bad format is logging's own error to report
            message = None
        if message is not None:
            masked = redact(message)
            if masked != message:
                record.msg, record.args = masked, ()
    if record.exc_info and not record.exc_text:
        try:
            record.exc_text = logging.Formatter().formatException(record.exc_info)
        except Exception:  # noqa: BLE001
            record.exc_text = None
    if record.exc_text:
        record.exc_text = redact(record.exc_text)
    if record.stack_info:
        record.stack_info = redact(record.stack_info)
    if not hasattr(record, "request_id"):
        record.request_id = request_id_var.get()
    record._blussit_redacted = True
    return record


class RedactingFilter(logging.Filter):
    """The same masking as a handler/logger filter (for handlers created
    before the record factory was installed)."""

    def filter(self, record: logging.LogRecord) -> bool:
        redact_record(record)
        return True


_factory_installed = False


def install_record_redaction() -> None:
    """Mask every record at creation time — covers every logger and handler
    in the process, including third-party ones. Idempotent."""
    global _factory_installed
    if _factory_installed:
        return
    previous = logging.getLogRecordFactory()

    def factory(*args, **kwargs):
        return redact_record(previous(*args, **kwargs))

    logging.setLogRecordFactory(factory)
    _factory_installed = True


# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------

_SEVERITY = {
    logging.DEBUG: "DEBUG",
    logging.INFO: "INFO",
    logging.WARNING: "WARNING",
    logging.ERROR: "ERROR",
    logging.CRITICAL: "CRITICAL",
}


class JsonFormatter(logging.Formatter):
    """One JSON object per line, in Cloud Logging's structured format."""

    def format(self, record: logging.LogRecord) -> str:
        redact_record(record)
        message = record.getMessage()
        if record.exc_text:
            message = f"{message}\n{record.exc_text}"
        if record.stack_info:
            message = f"{message}\n{record.stack_info}"
        entry: dict[str, Any] = {
            "severity": _SEVERITY.get(record.levelno, record.levelname),
            "message": message,
            "time": datetime.fromtimestamp(record.created, timezone.utc).isoformat().replace("+00:00", "Z"),
            "logger": record.name,
            "logging.googleapis.com/sourceLocation": {"file": record.pathname, "line": record.lineno, "function": record.funcName},
        }
        request_id = getattr(record, "request_id", None) or request_id_var.get()
        if request_id:
            entry["request_id"] = request_id
        trace_id = trace_id_var.get()
        project = os.environ.get("GOOGLE_CLOUD_PROJECT")
        if trace_id and project:
            entry["logging.googleapis.com/trace"] = f"projects/{project}/traces/{trace_id}"
        return json.dumps(entry, default=str, ensure_ascii=False)


class TextFormatter(logging.Formatter):
    def __init__(self) -> None:
        super().__init__("%(levelname)s:%(name)s:%(request_tag)s%(message)s")

    def format(self, record: logging.LogRecord) -> str:
        redact_record(record)
        request_id = getattr(record, "request_id", None)
        record.request_tag = f"[{request_id}] " if request_id else ""
        return super().format(record)


def wants_json(log_format: str, app_env: str) -> bool:
    if log_format == "json":
        return True
    if log_format == "text":
        return False
    return app_env == "production"


_HANDLER_MARK = "_blussit_handler"
_UVICORN_LOGGERS = ("uvicorn", "uvicorn.error", "uvicorn.access")


def configure_logging(log_format: str = "", app_env: str = "development", level: int = logging.INFO) -> None:
    """Install masking + the request-id/JSON formatter on the root logger.
    Replaces `logging.basicConfig(level=INFO)`; idempotent."""
    install_record_redaction()
    use_json = wants_json(log_format, app_env)
    root = logging.getLogger()
    root.setLevel(level)
    ours = [h for h in root.handlers if getattr(h, _HANDLER_MARK, False)]
    handler = ours[0] if ours else logging.StreamHandler(sys.stdout if use_json else sys.stderr)
    setattr(handler, _HANDLER_MARK, True)
    handler.setFormatter(JsonFormatter() if use_json else TextFormatter())
    if not any(isinstance(f, RedactingFilter) for f in handler.filters):
        handler.addFilter(RedactingFilter())
    if not ours:
        root.addHandler(handler)

    for name in _UVICORN_LOGGERS:
        uv = logging.getLogger(name)
        if use_json:
            # One format for everything: uvicorn's lines go through the root
            # handler as JSON instead of its own plain-text handler.
            for h in list(uv.handlers):
                uv.removeHandler(h)
            uv.propagate = True
        else:
            for h in uv.handlers:
                if not any(isinstance(f, RedactingFilter) for f in h.filters):
                    h.addFilter(RedactingFilter())
        if not any(isinstance(f, RedactingFilter) for f in uv.filters):
            uv.addFilter(RedactingFilter())


# ---------------------------------------------------------------------------
# Request id
# ---------------------------------------------------------------------------

_SAFE_REQUEST_ID = re.compile(r"^[A-Za-z0-9._\-]{1,128}$")
_TRACE_ID = re.compile(r"^([0-9a-fA-F]{8,64})(?:/|$)")


def _header(scope, name: bytes) -> str | None:
    for key, value in scope.get("headers") or ():
        if key == name:
            try:
                return value.decode("latin-1").strip()
            except Exception:  # noqa: BLE001
                return None
    return None


def request_id_from_scope(scope) -> tuple[str, str | None]:
    """(request id, Cloud trace id or None)."""
    trace_header = _header(scope, b"x-cloud-trace-context") or ""
    trace_match = _TRACE_ID.match(trace_header)
    trace_id = trace_match.group(1).lower() if trace_match else None
    incoming = _header(scope, b"x-request-id")
    if incoming and _SAFE_REQUEST_ID.match(incoming):
        return incoming, trace_id
    return (trace_id or uuid.uuid4().hex), trace_id


class RequestIdMiddleware:
    """Pure ASGI (streaming responses and WebSockets pass through): sets the
    request id for everything that runs inside the request and returns it
    in the X-Request-ID header. Must be the OUTERMOST middleware."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return
        request_id, trace_id = request_id_from_scope(scope)
        rid_token = request_id_var.set(request_id)
        trace_token = trace_id_var.set(trace_id)
        header = (REQUEST_ID_HEADER.encode("latin-1"), request_id.encode("latin-1"))

        async def send_with_id(message):
            if message["type"] == "http.response.start":
                headers = [(k, v) for k, v in (message.get("headers") or []) if k.lower() != header[0]]
                headers.append(header)
                message = {**message, "headers": headers}
            await send(message)

        try:
            await self.app(scope, receive, send_with_id if scope["type"] == "http" else send)
        finally:
            request_id_var.reset(rid_token)
            trace_id_var.reset(trace_token)


# ---------------------------------------------------------------------------
# Error tracking (optional, no new dependency)
# ---------------------------------------------------------------------------

_DROP_REQUEST_KEYS = ("cookies", "headers", "query_string", "data", "env")


def _scrub(value: Any) -> Any:
    if isinstance(value, str):
        return redact(value)
    if isinstance(value, dict):
        return {k: _scrub(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_scrub(v) for v in value]
    if isinstance(value, tuple):
        return tuple(_scrub(v) for v in value)
    return value


def scrub_event(event: dict, _hint: Any) -> dict:
    """Sentry before_send: no request headers/cookies/query/body (tokens,
    OTPs, phones), and every remaining string masked."""
    request = event.get("request")
    if isinstance(request, dict):
        for key in _DROP_REQUEST_KEYS:
            request.pop(key, None)
    return _scrub(event)


def _import_sentry():
    try:
        import sentry_sdk  # type: ignore[import-not-found]
    except ImportError:
        return None
    return sentry_sdk


def init_error_tracking(dsn: str, environment: str, traces_sample_rate: float = 0.0) -> bool:
    """Start Sentry when SENTRY_DSN is set AND sentry-sdk is installed.
    Returns True when error tracking is on."""
    if not dsn:
        return False
    sentry_sdk = _import_sentry()
    if sentry_sdk is None:
        logger.warning("SENTRY_DSN is set but the sentry-sdk package isn't installed — error tracking is OFF.")
        return False
    try:
        sentry_sdk.init(
            dsn=dsn,
            environment=environment,
            traces_sample_rate=traces_sample_rate,
            send_default_pii=False,
            before_send=scrub_event,
        )
    except Exception:  # noqa: BLE001 — never block startup on the tracker
        logger.exception("Sentry initialisation failed — error tracking is OFF")
        return False
    logger.info("Error tracking (Sentry) is on for %s", environment)
    return True
