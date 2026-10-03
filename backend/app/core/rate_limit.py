"""
Per-IP rate limiting (H1 in AUDIT.md).

In-process fixed-window counters — deliberately dependency-free and
memory-only. Each process counts only the traffic it sees, so on Cloud Run
every limit is PER INSTANCE (N instances = up to N x the limit). A shared
store (Redis) is the step that makes them global.

The client address comes from X-Forwarded-For only when TRUST_PROXY_HEADERS
is set, and then only from the entry the nearest trusted proxy appended
(see _client_ip) — never the left-most, which the client controls.

Rules are (path-prefix, limit, window-seconds). Tighter buckets protect
the endpoints where a flood costs money (OTP sends) or creates junk data
(public forms); a generous catch-all stops raw hammering without ever
touching legitimate use — the busiest real client (the admin CRM inbox
polling) stays far under it.
"""
import ipaddress
import logging
import time
from collections import defaultdict

from fastapi import Request
from fastapi.responses import JSONResponse

from app.core.config import settings

# (prefix, max requests, per seconds, method) — first match wins, so keep
# more specific prefixes above shorter ones they share a stem with.
# `method` is None for "any", or an HTTP verb when only WRITES need the
# limit: /api/v1/bookings is both "create a booking" (expensive, floodable)
# and "show me my bookings" (read, browsed freely), and throttling the
# second to protect the first would break normal use.
#
# OTP buckets are sized for carrier-grade NAT: Indian mobile carriers put
# many customers behind one public IP, so a per-IP cap near one person's
# usage locks out strangers. Abuse of any single number is stopped per phone
# instead: auth_service allows 5 sends/hour and 5 guesses per code, and
# MSG91 caps its own widget OTPs.
RULES: list[tuple[str, int, int, str | None]] = [
    ("/api/v1/auth/forgot-password", 30, 300, None),
    ("/api/v1/auth/verify-phone/request", 30, 300, None),
    ("/api/v1/auth/verify-phone/confirm", 60, 300, None),
    ("/api/v1/auth/verify-phone/widget", 60, 300, None),
    ("/api/v1/auth/reset-password/widget", 30, 300, None),
    ("/api/v1/auth/reset-password", 30, 300, None),
    ("/api/v1/auth/otp/request", 30, 300, None),
    ("/api/v1/auth/otp/verify", 60, 300, None),
    ("/api/v1/auth/add-phone", 60, 300, None),
    ("/api/v1/auth/google", 20, 60, None),
    ("/api/v1/auth/otp-login", 60, 300, None),
    ("/api/v1/auth/booking-access", 20, 60, None),
    ("/api/v1/bookings/verify-phone/request", 30, 300, None),
    ("/api/v1/auth/login", 15, 60, None),
    # Token refresh is cheap but is the one endpoint a stolen refresh token
    # is replayed against; password changes verify a bcrypt hash per call.
    ("/api/v1/auth/refresh", 60, 60, None),
    ("/api/v1/auth/change-password", 10, 300, None),
    ("/api/v1/auth/set-password", 10, 300, None),
    ("/api/v1/auth/register", 10, 300, None),
    # Release first and roomier: freeing a seat must never be refused
    # because the same visitor's holds/renewals used up the hold bucket.
    ("/api/v1/bookings/hold/release", 60, 60, None),
    ("/api/v1/bookings/hold", 30, 60, None),
    # Money-moving and data-creating endpoints. Each create-order call mints
    # a real Razorpay order, and each booking reserves capacity — a flood of
    # either costs us something, while a real customer books once. Generous
    # enough that a retry storm on a flaky connection still gets through.
    ("/api/v1/payments/create-order", 20, 60, "POST"),
    # The no-login quick booking: creates a customer profile AND reserves
    # capacity from a bare phone number, so it gets the tightest bucket of
    # the booking family (still roomy enough for a shared carrier IP). Above
    # the generic /bookings rule on purpose.
    ("/api/v1/bookings/quick", 20, 60, "POST"),
    # Read-only price quote, re-asked as the customer changes the cart — its
    # own bucket so quoting can never use up the budget for actually booking.
    ("/api/v1/bookings/quote", 120, 60, "POST"),
    ("/api/v1/bookings", 20, 60, "POST"),
    ("/api/v1/reviews", 15, 60, "POST"),
    # Public forms: junk-data buckets, same shape as coverage leads.
    ("/api/v1/subscriptions/enquiries", 6, 60, None),
    # Public society form: the enroll POST creates a customer + request
    # (after an OTP); reads are the form itself, re-asked as it reprices.
    ("/api/v1/society-forms", 30, 60, "POST"),
    ("/api/v1/society-forms", 60, 60, None),
    ("/api/v1/coverage-leads", 6, 60, None),
    # Landing-page "bring Blussit to our society" request: a public write
    # (junk-data bucket, deduped per phone + society for 24 h server-side).
    ("/api/v1/society-leads", 6, 60, "POST"),
    # A resident filing society issues — signed in, but each one alerts the
    # managers, so a tap-happy loop mustn't flood them.
    ("/api/v1/society-issues", 10, 60, "POST"),
    ("/api/v1/contact", 6, 60, None),
    # Public visitor beacon — one real ping per page load.
    ("/api/v1/analytics/visit", 20, 60, "POST"),
    ("/api/v1/", 300, 60, None),  # catch-all for the whole API
]

logger = logging.getLogger(__name__)

_WINDOWS: dict[tuple, int] = defaultdict(int)
_last_sweep = 0.0
_proxy_choice_logged = False


def _parse_ip(raw: str) -> str | None:
    """One X-Forwarded-For entry as a normalized IP, or None for garbage.
    Tolerates the "1.2.3.4:5678" / "[::1]:5678" forms some proxies write."""
    value = raw.strip().strip('"')
    if value.startswith("[") and "]" in value:
        value = value[1:value.index("]")]
    elif value.count(":") == 1:
        value = value.split(":", 1)[0]
    try:
        return str(ipaddress.ip_address(value))
    except ValueError:
        return None


def _client_ip(request: Request) -> str:
    """The rate-limit key. Without TRUST_PROXY_HEADERS it is the socket
    peer. With it, it is the X-Forwarded-For entry at -TRUSTED_PROXY_COUNT:
    each trusted proxy APPENDS the address it saw, so entries left of that
    are whatever the client sent and would let it mint a fresh "IP" per
    request. A missing, short or garbage header falls back to the peer."""
    peer = request.client.host if request.client else "unknown"
    # getlist: a client-sent header and the proxy's may arrive as separate lines.
    entries = [e for e in ",".join(request.headers.getlist("x-forwarded-for")).split(",") if e.strip()]
    chosen = peer
    if settings.TRUST_PROXY_HEADERS:
        hops = max(1, settings.TRUSTED_PROXY_COUNT)
        if len(entries) >= hops:
            chosen = _parse_ip(entries[-hops]) or peer
    if entries:
        _log_proxy_choice_once(peer, len(entries), chosen)
    return chosen


def _log_proxy_choice_once(peer: str, xff_entries: int, chosen: str) -> None:
    # One line per process so the setting can be confirmed in Cloud Run logs.
    global _proxy_choice_logged
    if _proxy_choice_logged:
        return
    _proxy_choice_logged = True
    logger.warning(
        "rate-limit client ip: peer=%s xff_entries=%d chosen=%s trust_proxy_headers=%s trusted_proxy_count=%d",
        peer, xff_entries, chosen, settings.TRUST_PROXY_HEADERS, settings.TRUSTED_PROXY_COUNT,
    )


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
    # Razorpay's webhook likewise: HMAC-signed, retried by Razorpay, and a
    # throttled delivery would only delay settling a real payment.
    if (
        path.startswith("/api/v1/whatsapp/webhook")
        or path == "/api/v1/payments/webhook"
        or path == "/api/health"
        or path.startswith("/uploads/")
    ):
        return await call_next(request)

    now = time.time()
    _sweep(now)
    ip = _client_ip(request)
    for prefix, limit, window, method in RULES:
        if path.startswith(prefix) and (method is None or request.method == method):
            window_start = int(now // window) * window
            # The rule's method is part of the bucket: a POST-only rule and
            # an any-method rule on the same prefix (/society-forms) must
            # not share one counter. Indices 2/3 stay window/start (_sweep).
            key = (ip, prefix, window, window_start, method)
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
