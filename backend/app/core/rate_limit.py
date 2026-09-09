"""
Per-IP rate limiting (H1 in AUDIT.md).

In-process sliding-window counters — deliberately dependency-free and
memory-only. That's the right shape for this deployment: the app runs as
a single worker (see the reminder-loop lease in main.py), so one process
sees all traffic. If the app ever goes multi-instance, move these limits
to the edge (Caddy/nginx rate zones) and this middleware becomes a
defense-in-depth backstop rather than the primary control.

Rules are (path-prefix, limit, window-seconds). Tighter buckets protect
the endpoints where a flood costs money (OTP sends) or creates junk data
(public forms); a generous catch-all stops raw hammering without ever
touching legitimate use — the busiest real client (the admin CRM inbox
polling) stays far under it.
"""
import time
from collections import defaultdict

from fastapi import Request
from fastapi.responses import JSONResponse

from app.core.config import settings

# (prefix, max requests, per seconds) — first match wins, so keep more
# specific prefixes above shorter ones they share a stem with.
RULES: list[tuple[str, int, int]] = [
    ("/api/v1/auth/forgot-password", 5, 300),
    ("/api/v1/auth/verify-phone/request", 5, 300),
    ("/api/v1/auth/verify-phone/confirm", 10, 300),
    ("/api/v1/auth/verify-phone/widget", 10, 300),
    ("/api/v1/auth/reset-password/widget", 5, 300),
    ("/api/v1/auth/reset-password", 5, 300),
    ("/api/v1/auth/otp/request", 5, 300),
    ("/api/v1/auth/otp/verify", 10, 300),
    ("/api/v1/auth/add-phone", 10, 300),
    ("/api/v1/auth/google", 20, 60),
    ("/api/v1/auth/otp-login", 10, 300),
    ("/api/v1/auth/booking-access", 20, 60),
    ("/api/v1/auth/login", 15, 60),
    ("/api/v1/auth/register", 10, 300),
    ("/api/v1/bookings/hold", 30, 60),
    ("/api/v1/coverage-leads", 6, 60),
    ("/api/v1/contact", 6, 60),
    ("/api/v1/", 300, 60),  # catch-all for the whole API
]

_WINDOWS: dict[tuple, int] = defaultdict(int)
_last_sweep = 0.0


def _client_ip(request: Request) -> str:
    """The rate-limit key. X-Forwarded-For is only honored when the
    deployment explicitly says a trusted reverse proxy (Caddy/nginx,
    configured to OVERWRITE the header, never append blindly) sits in
    front — otherwise any client could spoof a fresh header per request
    and every limit here would be theater."""
    if settings.TRUST_PROXY_HEADERS:
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _sweep(now: float) -> None:
    """Drop finished windows so memory stays bounded."""
    global _last_sweep
    if now - _last_sweep < 120:
        return
    _last_sweep = now
    stale = [k for k in _WINDOWS if k[3] < now - k[2]]
    for k in stale:
        del _WINDOWS[k]


async def rate_limit_middleware(request: Request, call_next):
    if not settings.RATE_LIMIT_ENABLED:
        return await call_next(request)
    path = request.url.path
    # Meta's webhook must never be limited — Meta retries aggressively
    # and has its own signature auth; health checks likewise.
    if path.startswith("/api/v1/whatsapp/webhook") or path == "/api/health" or path.startswith("/uploads/"):
        return await call_next(request)

    now = time.time()
    _sweep(now)
    ip = _client_ip(request)
    for prefix, limit, window in RULES:
        if path.startswith(prefix):
            window_start = int(now // window) * window
            key = (ip, prefix, window, window_start)
            _WINDOWS[key] += 1
            if _WINDOWS[key] > limit:
                return JSONResponse(
                    status_code=429,
                    content={
                        "success": False,
                        "error_code": "RATE_LIMITED",
                        "message": "Too many requests — please wait a moment and try again.",
                    },
                    headers={"Retry-After": str(int(window_start + window - now) or 1)},
                )
            break
    return await call_next(request)
