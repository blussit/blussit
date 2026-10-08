import asyncio
import json
import logging
import re
import time
from datetime import datetime, timedelta, timezone

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
from fastapi.staticfiles import StaticFiles
from pymongo.errors import DuplicateKeyError

from app.core.config import RUN_MODES, ensure_valid_settings, settings
from app.core.database import close_mongo_connection, connect_to_mongo, mongodb
from app.core.exceptions import AppException, BadRequestException
from app.core.logging_setup import RedactingFilter, RequestIdMiddleware, configure_logging, init_error_tracking
from app.core.storage import MAX_UPLOAD_BYTES as STORAGE_MAX_UPLOAD_BYTES, upload_root
from app.models.enums import NotificationType
from app.routes.v1 import (
    analytics_routes,
    audit_log_routes,
    auth_routes,
    booking_routes,
    catalog_routes,
    complaint_routes,
    content_routes,
    coupon_routes,
    coverage_lead_routes,
    crm_routes,
    customer_wallet_routes,
    inventory_routes,
    notification_routes,
    booking_policy_routes,
    homepage_config_routes,
    settings_history_routes,
    pricing_routes,
    profile_routes,
    purchase_confirmation_routes,
    review_routes,
    service_center_routes,
    society_routes,
    society_schedule_routes,
    society_support_routes,
    payment_routes,
    staff_directory_routes,
    staff_ops_routes,
    subscription_routes,
    upload_routes,
    user_routes,
    vehicle_type_routes,
    wallet_routes,
    whatsapp_crm_routes,
    zone_routes,
    whatsapp_webhook_routes,
    ws_routes,
)

# Root logging (replaces basicConfig): every record is masked at creation —
# phone numbers to their last four digits; OTPs, passwords, tokens (the
# WebSocket JWT rides in ?token= because browsers can't send an
# Authorization header on the handshake), hashes and URI passwords to
# [REDACTED] — and carries the request id. JSON lines for Cloud Logging in
# production (LOG_FORMAT). See app/core/logging_setup.py.
configure_logging(settings.LOG_FORMAT, settings.APP_ENV)
_redact_access_tokens = RedactingFilter()
logging.getLogger("uvicorn.access").addFilter(_redact_access_tokens)
logging.getLogger("uvicorn.error").addFilter(_redact_access_tokens)
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# RUN_MODE (app/core/config.py): one image, two Cloud Run services.
#   api    -> blussit-api:    routes, webhooks, websockets + relay, health/ready
#   worker -> blussit-worker: background loops, index builds, boot backfills,
#                             health/ready only (everything else 404)
#   all    -> both (local development, tests)
# Read through these helpers (never cached) so a test can switch modes by
# patching settings; an unknown value never gets this far — the start-up
# validator refuses it.
# ---------------------------------------------------------------------------
def _run_mode() -> str:
    mode = str(getattr(settings, "RUN_MODE", "all") or "all").strip().lower()
    return mode if mode in RUN_MODES else "all"


def _runs_background_jobs() -> bool:
    """The reminder/sweep loop, WhatsApp retry, template sync, index builds
    and boot backfills."""
    return _run_mode() in ("worker", "all")


def _serves_public_routes() -> bool:
    """Business routes, webhooks, websockets, uploads."""
    return _run_mode() in ("api", "all")


# The only paths a worker answers (Cloud Run's startup/liveness probes).
WORKER_PATHS = frozenset({"/api/health", "/api/ready"})


app = FastAPI(
    title=settings.APP_NAME,
    description="REST API for India's premium doorstep vehicle care platform.",
    version="1.0.0",
    # API docs are exposed only in debug mode — a public prod API should not
    # advertise its full schema.
    docs_url="/api/docs" if settings.DEBUG else None,
    redoc_url="/api/redoc" if settings.DEBUG else None,
    openapi_url="/api/openapi.json" if settings.DEBUG else None,
)

from app.core.rate_limit import rate_limit_middleware  # noqa: E402

app.middleware("http")(rate_limit_middleware)


# Catalogue endpoints anyone can read without logging in — the data behind the
# landing page and the booking wizard's first screen. They change a few times
# a month, so an anonymous visitor's browser (and Vercel's edge) may reuse a
# response for a short while and refresh it in the background: repeat visits
# and "Book Now" open with no wait. Deliberately NOT applied when the request
# carries a login (admins/managers editing the catalogue must always see
# their own change at once) — and `Vary: Authorization` keeps a cached
# anonymous copy from ever being served to a logged-in request.
_PUBLIC_CATALOGUE = re.compile(
    r"^/api/v1/(homepage-config|testimonials|vehicle-types|subscription-plans|faqs|services|booking-policy|combo-offers|categories|coupons/public/[^/]+)/?$"
)
PUBLIC_CACHE_CONTROL = "public, max-age=30, stale-while-revalidate=120"


@app.middleware("http")
async def security_headers(request, call_next):
    """Baseline hardening on every response, plus a much tighter policy for
    user-uploaded files.

    Uploads are served from our own origin, so nosniff stops a browser ever
    executing a mislabeled upload as HTML/script (the magic-byte check in
    storage.py stops one being stored in the first place — belt and braces)
    and `default-src 'none'` makes the file inert even if one slipped
    through. A blanket CSP is deliberately NOT set on API responses: this
    app also serves the payment-link result page, and the SPA is served by
    its own host, which is where the page-level CSP belongs.

    HSTS is only sent outside DEBUG — pinning `localhost` to HTTPS would
    break every developer's browser for months."""
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    # Nothing here is meant to be framed — an API or a payment result page
    # inside someone else's iframe is only ever clickjacking.
    response.headers.setdefault("X-Frame-Options", "DENY")
    # Nothing on the API host belongs in a search index — JSON, the
    # payment-result pages (their URLs carry payment parameters), uploads.
    # The site (blussit.com) is a different host and unaffected.
    response.headers.setdefault("X-Robots-Tag", "noindex, nofollow")
    # Correct because this app serves ONLY JSON and the payment-result page,
    # none of which need a device. WARNING: the customer app genuinely uses
    # geolocation (address pin, captain GPS) and the camera (before/after
    # photos) — if the SPA is ever served from this process, those two must
    # come out of this list or the booking and captain flows break silently.
    response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=(), payment=()")
    if not settings.DEBUG:
        response.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
    if request.url.path.startswith("/uploads/"):
        response.headers["Content-Security-Policy"] = "default-src 'none'; sandbox"
    if (
        request.method == "GET"
        and response.status_code == 200
        and "authorization" not in request.headers
        and "cache-control" not in response.headers
        and _PUBLIC_CATALOGUE.match(request.url.path)
    ):
        response.headers["Cache-Control"] = PUBLIC_CACHE_CONTROL
        vary = response.headers.get("Vary")
        if not vary or "authorization" not in vary.lower():
            response.headers["Vary"] = f"{vary}, Authorization" if vary else "Authorization"
    return response


SERVER_ERROR_MESSAGE = "Something went wrong — please try again."


def _server_error_response() -> JSONResponse:
    return JSONResponse(status_code=500, content={"success": False, "error_code": "SERVER_ERROR", "message": SERVER_ERROR_MESSAGE})


class CatchUnhandledErrors:
    """Turns any unhandled exception into the JSON 500 INSIDE the CORS
    middleware. Starlette sends an `exception_handler(Exception)` response
    from ServerErrorMiddleware — outside every user middleware, so without
    CORS headers — and the browser then reports a blocked request ("Can't
    reach the server") instead of the error. Pure ASGI (not
    BaseHTTPMiddleware) so streaming responses pass through untouched; a
    failure after the response started can only be re-raised."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        started = False

        async def _send(message):
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
            await send(message)

        try:
            await self.app(scope, receive, _send)
        except Exception as exc:  # noqa: BLE001 — the last net before the client
            if started:
                raise
            logger.exception("Unhandled exception on %s %s: %s", scope.get("method"), scope.get("path"), exc)
            await _server_error_response()(scope, receive, send)


# Added before CORS = sits inside it, so its 500 gets the CORS headers.
app.add_middleware(CatchUnhandledErrors)


class _BodyTooLarge(Exception):
    pass


# Request-body ceilings. FastAPI reads a multipart body BEFORE the route's
# auth dependencies run, and nothing else capped it — anyone could make an
# instance spool gigabytes. Upload routes get their own file cap plus 1 MB
# for the multipart framing; every JSON endpoint fits easily in 2 MB.
_BODY_LIMIT_MARGIN = 1024 * 1024
_DEFAULT_BODY_LIMIT = 2 * 1024 * 1024
_BODY_LIMITS: tuple[tuple[str, int], ...] = (
    (f"{settings.API_V1_PREFIX}/uploads/", STORAGE_MAX_UPLOAD_BYTES + _BODY_LIMIT_MARGIN),
    (f"{settings.API_V1_PREFIX}/whatsapp/crm/media", whatsapp_crm_routes.MAX_UPLOAD_BYTES + _BODY_LIMIT_MARGIN),
)


def _body_limit(path: str) -> int:
    return next((limit for prefix, limit in _BODY_LIMITS if path.startswith(prefix)), _DEFAULT_BODY_LIMIT)


class LimitRequestBody:
    """Refuses an oversized body with 413 before the app reads it: by
    Content-Length up front, and — for a chunked body or a lying header —
    as it streams, the moment the running total crosses the cap (whatever
    the app was doing with the partial body is discarded and replaced by
    the 413). Pure ASGI, so streaming responses pass through untouched."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        limit = _body_limit(scope["path"])
        for name, value in scope.get("headers") or ():
            if name == b"content-length":
                try:
                    if int(value) > limit:
                        await _too_large_response()(scope, receive, send)
                        return
                except ValueError:
                    pass  # the server rejects a malformed length itself
                break

        received = 0
        exceeded = False
        started = False

        async def limited_receive():
            nonlocal received, exceeded
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > limit:
                    exceeded = True
                    raise _BodyTooLarge()
            return message

        async def guarded_send(message):
            nonlocal started
            if exceeded:
                return  # the app's answer to a cut-off body — replaced below
            if message["type"] == "http.response.start":
                started = True
            await send(message)

        try:
            await self.app(scope, limited_receive, guarded_send)
        except Exception:
            if not exceeded:
                raise
        if exceeded and not started:
            await _too_large_response()(scope, receive, send)


def _too_large_response() -> JSONResponse:
    return JSONResponse(
        status_code=413,
        content={"success": False, "error_code": "PAYLOAD_TOO_LARGE", "message": "That request is too large."},
    )


# Inside CORS (added before it), so a browser sees the 413, not a CORS error.
app.add_middleware(LimitRequestBody)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
# JSON compresses ~5x; on a phone connection that is the difference between
# one packet and several for the catalogue responses.
# Level 5: level 9 (the default) costs several times the CPU per response
# for a percent or two less on the wire.
app.add_middleware(GZipMiddleware, minimum_size=1024, compresslevel=5)


class WorkerRouteGuard:
    """In RUN_MODE=worker every path except /api/health and /api/ready gets
    a 404 (websockets: closed before accept) — belt and braces on top of the
    worker not mounting the business routers at all, so a worker that is
    somehow reachable can never take a payment, a webhook or a login. Pure
    ASGI; reads the mode per request (cheap) so tests can switch it."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] in ("http", "websocket") and not _serves_public_routes() and scope.get("path") not in WORKER_PATHS:
            if scope["type"] == "websocket":
                await send({"type": "websocket.close", "code": 1008})
                return
            await JSONResponse(
                status_code=404,
                content={"success": False, "error_code": "NOT_FOUND", "message": "Not found."},
            )(scope, receive, send)
            return
        await self.app(scope, receive, send)


app.add_middleware(WorkerRouteGuard)
# Added last = outermost: the request id is set before anything else runs
# (so every log line of the request carries it) and every response —
# 413s, CORS preflights, 500s — returns it in X-Request-ID.
app.add_middleware(RequestIdMiddleware)

# Serves whatever app.core.storage's "local" provider writes (captain
# before/after photos) back out as plain static files — the directory must
# exist before StaticFiles will mount, hence the mkdir here.
# A worker serves no files (and its 404 guard would refuse them anyway).
if _serves_public_routes():
    (upload_root() / "photos").mkdir(parents=True, exist_ok=True)
    app.mount("/uploads", StaticFiles(directory=str(upload_root())), name="uploads")


_reminder_task: asyncio.Task | None = None
# FAIL-06: strong references to start-up background work (asyncio keeps only
# weak ones) so shutdown can wait for it instead of dropping it mid-write.
_startup_tasks: set[asyncio.Task] = set()
# Set on shutdown: the reminder loop ends at its next checkpoint or sleep
# instead of being cancelled in the middle of a customer message.
_stop_event: asyncio.Event | None = None
# Cloud Run allows 10 s between SIGTERM and SIGKILL; uvicorn spends part of
# it draining requests. In-flight background work gets this long, then is
# cancelled so the clients can still be closed cleanly.
_SHUTDOWN_GRACE_SECONDS = 5.0


def _track_startup_task(task: asyncio.Task) -> asyncio.Task:
    _startup_tasks.add(task)
    task.add_done_callback(_startup_tasks.discard)
    return task


def _stopping() -> bool:
    return _stop_event is not None and _stop_event.is_set()


async def _sleep_unless_stopping(seconds: float) -> None:
    """asyncio.sleep that returns early when shutdown begins."""
    if _stop_event is None:
        await asyncio.sleep(seconds)
        return
    try:
        await asyncio.wait_for(_stop_event.wait(), timeout=seconds)
    except asyncio.TimeoutError:
        pass

# H2 (AUDIT.md): a Mongo lease makes the sweep single-flight across however
# many instances Cloud Run runs. Only the holder sweeps; a crashed holder's
# lease simply lapses. A pass renews it between sweeps (and inside long
# per-item loops), and stops the moment a renewal fails — another instance
# owns the sweeps now, and carrying on would double-send customer messages.
_LEASE_ID = "reminder_loop"
_LEASE_SECONDS = 90
# No new sweep or loop item starts after this long, so a pass always ends
# inside a lease it renewed moments earlier. Whatever it didn't reach is
# still unmarked and is picked up by the next pass. Each Razorpay sync
# stops starting gateway calls after ~15 s (plus one in-flight call, and
# all three pause after a timeout — ~45 s worst case together), so every
# one fits inside the lease renewed right before it.
_PASS_BUDGET_SECONDS = 70
# FAIL-05: each sweep's OWN budget. A sweep with a backlog and slow sends
# (Meta hanging) stops starting new items after this long and leaves the
# rest for the next pass, so it can't eat the whole pass budget and starve
# every sweep after it.
_SWEEP_BUDGET_SECONDS = 15
# FAIL-07: hard ceiling on one sweep call, BELOW the lease. The lease is
# renewed right before each sweep, so a sweep stuck in a single await (no
# checkpoint to renew or stop at) is cancelled while the lease is still
# ours — it can never keep sending after another instance took over.
_SWEEP_TIMEOUT_SECONDS = 60
# Inside a per-item loop the lease is renewed at most this often.
_ITEM_RENEW_SECONDS = 10
# The repeat-booking nudge and the pass wash reminder run hourly. Their last
# runs are stamped on the lease doc, not kept in this process — a fresh
# holder (a new instance) must not re-run them straight away.
_REPEAT_SWEEP_EVERY = timedelta(hours=1)
# The seat-counter check (BookingService.reconcile_slot_counters, report
# only) runs once a day across all instances.
_SEAT_RECONCILE_EVERY = timedelta(hours=24)
# Google-review requests (NotificationService.send_review_requests, ~2 h
# after a completed + paid booking, 10 AM–7 PM): every 10 minutes is plenty.
_REVIEW_SWEEP_EVERY = timedelta(minutes=10)


async def _call_supported(fn, **kwargs):
    """Call `fn` with only the keyword arguments it declares (or all of
    them when it takes **kwargs) — for sweeps owned by other modules whose
    exact signature may grow (a `checkpoint`, a `limit`)."""
    import inspect

    try:
        params = inspect.signature(fn).parameters
    except (TypeError, ValueError):
        params = {}
    if not any(p.kind is inspect.Parameter.VAR_KEYWORD for p in params.values()):
        kwargs = {k: v for k, v in kwargs.items() if k in params}
    result = fn(**kwargs)
    if inspect.isawaitable(result):
        result = await result
    return result


class _StopPass(Exception):
    """A checkpoint's verdict that this pass must end now: the lease was
    lost to another instance, the pass spent its time budget, or the
    process is shutting down."""


class _StopSweep(Exception):
    """A checkpoint's verdict that the CURRENT sweep spent its own budget:
    it stops starting items, and the pass moves on to the next sweep."""


async def _claim_lease(db, holder: str) -> bool:
    """Claim (or renew) the lease at the top of a tick. False = not ours
    this tick: someone else holds it, or the database couldn't be asked
    (logged — an outage must not look like "another instance has it")."""
    now = datetime.now(timezone.utc)
    try:
        result = await db.locks.find_one_and_update(
            {"_id": _LEASE_ID, "$or": [{"holder": holder}, {"expires_at": {"$lt": now}}]},
            {"$set": {"holder": holder, "expires_at": now + timedelta(seconds=_LEASE_SECONDS)}},
        )
        if result is not None:
            return True
        # No claimable doc — either someone else holds a live lease, or the
        # doc doesn't exist yet. Try to create it; losing the race is fine.
        await db.locks.insert_one({"_id": _LEASE_ID, "holder": holder, "expires_at": now + timedelta(seconds=_LEASE_SECONDS)})
        return True
    except DuplicateKeyError:
        return False  # someone else holds a live lease
    except Exception:  # noqa: BLE001 — retried next tick, but never silently
        logger.exception("Reminder lease claim failed (database error) — no sweeps this tick")
        return False


async def _renew_lease(db, holder: str) -> bool:
    """Only the current holder can extend. Another instance has to write its
    own id into `holder` before it sweeps, so a match here means nobody
    else is (or was) running this pass's sweeps."""
    result = await db.locks.update_one(
        {"_id": _LEASE_ID, "holder": holder},
        {"$set": {"expires_at": datetime.now(timezone.utc) + timedelta(seconds=_LEASE_SECONDS)}},
    )
    return result.matched_count == 1


async def _claim_hourly_sweep(db, holder: str, stamp: str, every: timedelta = _REPEAT_SWEEP_EVERY) -> bool:
    """True at most once per `every` (an hour by default) across ALL
    instances for the sweep stamped in `stamp` — the stamp and the check
    are one atomic write on the lease doc, and only the live holder can
    make it."""
    now = datetime.now(timezone.utc)
    result = await db.locks.update_one(
        {
            "_id": _LEASE_ID,
            "holder": holder,
            "$or": [{stamp: None}, {stamp: {"$lte": now - every}}],
        },
        {"$set": {stamp: now}},
    )
    return result.modified_count == 1


async def _claim_repeat_sweep(db, holder: str) -> bool:
    return await _claim_hourly_sweep(db, holder, "repeat_sweep_at")


def _customer_hours_open() -> bool:
    """Customer reminders (pass notices, wash nudges) wait for the day —
    10 AM to 7 PM IST. A sweep outside it writes nothing, so everything it
    skipped is simply still due on the first pass after 10."""
    from app.utils.timezone import in_customer_message_hours

    return in_customer_message_hours()


class _SweepPass:
    """One pass of the reminder loop. Each sweep runs in its own try/except
    — one failing sweep is logged and skipped, never takes the rest of the
    pass down with it — behind a checkpoint that renews the lease and
    enforces the time budgets: the pass's, and (FAIL-05) each sweep's own.
    Every sweep call also has a hard timeout below the lease (FAIL-07)."""

    def __init__(self, db, holder: str):
        self.db = db
        self.holder = holder
        self.started = self.renewed = time.monotonic()
        self.sweep_started: float | None = None
        self.sweep_name: str | None = None

    async def checkpoint(self, renew: bool = False) -> None:
        now = time.monotonic()
        if _stopping():
            raise _StopPass("shutting down")
        if now - self.started > _PASS_BUDGET_SECONDS:
            raise _StopPass(f"time budget of {_PASS_BUDGET_SECONDS}s spent")
        if not renew and self.sweep_started is not None and now - self.sweep_started > _SWEEP_BUDGET_SECONDS:
            raise _StopSweep(f"sweep {self.sweep_name!r} used its {_SWEEP_BUDGET_SECONDS}s budget")
        if renew or now - self.renewed >= _ITEM_RENEW_SECONDS:
            try:
                held = await _renew_lease(self.db, self.holder)
            except Exception:  # noqa: BLE001 — can't prove we hold it, so we don't
                logger.exception("Reminder lease renewal failed")
                held = False
            if not held:
                raise _StopPass("lease lost")
            self.renewed = time.monotonic()

    async def run(self, name: str, sweep) -> None:
        await self.checkpoint(renew=True)
        self.sweep_started, self.sweep_name = time.monotonic(), name
        deadline = asyncio.timeout(_SWEEP_TIMEOUT_SECONDS)
        try:
            async with deadline:
                await sweep()
        except _StopPass:
            raise
        except _StopSweep as stop:
            logger.warning("Reminder %s — the rest is picked up next pass", stop)
        except TimeoutError:
            if not deadline.expired():  # a timeout raised by the sweep's own code
                logger.exception("Reminder sweep %r failed — the rest of the pass still runs", name)
            else:
                logger.error(
                    "Reminder sweep %r hit its %ss timeout and was cancelled (lease is %ss) — the rest of the pass still runs",
                    name, _SWEEP_TIMEOUT_SECONDS, _LEASE_SECONDS,
                )
        except Exception:
            logger.exception("Reminder sweep %r failed — the rest of the pass still runs", name)
        finally:
            self.sweep_started = self.sweep_name = None

    async def each(self, items, what: str, handle) -> None:
        """`handle` per item, with a checkpoint before each: one bad row is
        logged and skipped, and a long list can't outrun the lease or its
        sweep's budget."""
        for item in items:
            await self.checkpoint()
            try:
                await handle(item)
            except (_StopPass, _StopSweep):
                raise
            except Exception:
                logger.exception("Reminder sweep: %s failed for %s", what, item.get("booking_number") or item.get("_id"))


async def _society_schedule_sweep(db, p: "_SweepPass") -> None:
    from app.services.society_schedule_service import SocietyScheduleService

    await SocietyScheduleService(db).sweep(checkpoint=p.checkpoint)


async def _sweep_once(db, holder: str) -> None:
    """
    One pass of the in-process reminder sweep — no Redis/Celery required
    (those are explicitly Phase 2). Slot holds and the payment sweeps
    (Razorpay syncs, unpaid-booking expiry/nudges) run first, then:
      1. Pings captains for bookings starting within 30 minutes.
      2. Reminds the manager that a booking whose slot starts within a day
         still has no captain — the first reminder on WhatsApp, repeats
         every 15 minutes in-app only and only during the center's working
         hours.
      3. Flags + notifies BOTH the captain and the manager, right at the
         scheduled start time (repeats in-app only — the captain every 5
         minutes, the manager every 15 — until he heads out or the manager
         resolves it), when an assigned booking's captain hasn't even started
         heading out yet — this is the fast, immediate check; see #4 for the
         much later "never got a captain at all" case.
      4. Flags + notifies the manager for bookings that never got a captain
         in the first place (or got rescheduled and then didn't get one
         either), once the full start window has expired ("captain not
         reached").
      5. Flags + notifies the manager for bookings where a captain has been
         "on the way" for an unusually long time without starting the service.
      6. Flags + notifies the manager for a service that's been running well
         past its planned duration without the after-photo yet.
    Plus the customer's pre-slot reminder (when BOOKING's sweep is in the
    build), pass lifecycle, the recycle-bin purge, the daily seat-counter
    check, Google-review requests (every 10 min, when NOTIFY's sweep is in
    the build) and (hourly, last) the pass wash reminder and the
    repeat-booking nudge. Every customer
    reminder (pass notices, wash nudges) only goes out 10 AM–7 PM IST.
    """
    from bson import ObjectId

    from app.schemas.booking_schema import BookingCancelRequest
    from app.services.audit_service import AuditService
    from app.services.booking_service import UNASSIGNED_REMINDER_MINUTES, BookingService, format_slot_start_12h
    from app.services.notification_service import NotificationService
    from app.services.payment_service import PaymentService
    from app.services.booking_policy_service import DEFAULT_BOOKING_POLICY
    from app.services.subscription_service import (
        find_passes_due_wash_reminder,
        find_subscriptions_ended,
        find_subscriptions_expiring_soon,
        mark_expiry_reminder_sent,
        mark_subscription_expired,
        mark_wash_reminder_sent,
        renewal_continues_text,
        renewal_lined_up,
    )
    from app.services.society_service import SocietyService
    from app.utils.timezone import from_stored

    p = _SweepPass(db, holder)
    booking_service = BookingService(db)
    notifications = NotificationService(db)

    async def release_holds() -> None:
        # Release expired slot holds (theater-seat model) so abandoned
        # checkouts free their seats within a minute.
        await booking_service._sweep_holds()

    async def purge_recycle_bin() -> None:
        # Recycle bin: hard-delete anything soft-deleted more than 30 days
        # ago — same cascade an admin's manual "force delete" already
        # gives, just auto-triggered once the grace window an admin had to
        # restore it has passed.
        purge_cutoff = datetime.now(timezone.utc) - timedelta(days=30)
        # Rows the purge may not hard-delete (captain wallet money references
        # them — ADM-11) are parked with a reason, so they can't fill the
        # batch and fail again every pass; an admin sees the reason.
        overdue = await db.bookings.find(
            {"is_deleted": True, "deleted_at": {"$lt": purge_cutoff}, "purge_blocked_reason": {"$exists": False}}, {"_id": 1}
        ).to_list(length=200)
        # permanently_delete_booking purges a whole visit at once — a
        # multi-car group's OTHER cars are already gone by the time this
        # loop reaches their own row, which would otherwise raise (and log)
        # a spurious NotFoundException for each one.
        already_purged: set[str] = set()

        async def purge(row: dict) -> None:
            booking_id = str(row["_id"])
            if booking_id in already_purged:
                return
            try:
                result = await booking_service.permanently_delete_booking(booking_id, force=True)
            except BadRequestException as refused:
                await db.bookings.update_one(
                    {"_id": row["_id"]},
                    {"$set": {"purge_blocked_reason": refused.message, "purge_blocked_at": datetime.now(timezone.utc)}},
                )
                logger.info("Recycle-bin purge skipped %s: %s", booking_id, refused.message)
                return
            already_purged.update(result.get("booking_ids") or [booking_id])
            await AuditService(db).log_action(
                "system", "system", "PERMANENTLY_DELETE_BOOKING", "bookings", booking_id,
                {"reason": "30-day recycle-bin auto-purge"},
            )

        await p.each(overdue, "recycle-bin auto-purge", purge)

    async def remind_captains() -> None:
        async def remind(booking: dict) -> None:
            if booking.get("captain_id"):
                await notifications.notify(
                    booking["captain_id"],
                    "Get ready — booking starting soon",
                    f"Booking {booking['booking_number']} is scheduled to start at {format_slot_start_12h(booking['scheduled_slot'])}.",
                    NotificationType.BOOKING,
                    str(booking["_id"]),
                    background=True,
                )
            await booking_service.mark_reminder_sent(str(booking["_id"]))

        await p.each(await booking_service.find_bookings_needing_reminder(), "captain start reminder", remind)

    async def remind_customers_before_slot() -> None:
        # Pre-slot customer reminder (BOOKING; the template exists). Wired
        # only when this build has both halves: the finder returns the due
        # bookings, and send_customer_reminder(booking) sends AND marks it
        # (claim-first, so a retry or a second worker never double-sends).
        finder = getattr(booking_service, "find_bookings_needing_customer_reminder", None)
        sender = getattr(booking_service, "send_customer_reminder", None)
        if finder is None or sender is None:
            return
        await p.each(await finder(), "customer pre-slot reminder", sender)

    async def nudge_unassigned() -> None:
        async def nudge(booking: dict) -> None:
            await booking_service.notify_center_manager_for_booking(
                booking,
                f"Still needs a captain — booking {booking['booking_number']}",
                f"Booking {booking['booking_number']} has been waiting for a captain for over "
                f"{UNASSIGNED_REMINDER_MINUTES} minutes. Please assign one.",
                # Only the first reminder is a (billed) WhatsApp; repeats
                # stay in-app — see find_bookings_unassigned_too_long.
                send_whatsapp=booking.get("_first_unassigned_reminder", True),
            )
            await booking_service.mark_unassigned_reminder_sent(str(booking["_id"]))

        await p.each(await booking_service.find_bookings_unassigned_too_long(), "unassigned nudge", nudge)

    async def flag_late_to_start() -> None:
        async def flag(booking: dict) -> None:
            await booking_service.flag_late_to_start(booking)
            await booking_service.mark_late_start_reminder_sent(str(booking["_id"]))

        await p.each(await booking_service.find_bookings_late_to_start(), "late-to-start flag", flag)

    def flagger(issue_flag: str, note):
        """A sweep whose every hit is one flag_issue call (the manager is
        notified by flag_issue itself)."""

        async def flag(booking: dict) -> None:
            await booking_service.flag_issue(str(booking["_id"]), issue_flag, note(booking))

        return flag

    async def flag_not_reached() -> None:
        await p.each(
            await booking_service.find_bookings_captain_not_reached(),
            "captain-not-reached flag",
            flagger(
                "captain_not_reached",
                lambda b: f"Booking {b['booking_number']} never got moving — its scheduled window has fully "
                f"expired with no captain heading out. Reschedule it to a new time, then assign a captain, "
                f"or cancel it.",
            ),
        )

    async def flag_stuck_on_the_way() -> None:
        await p.each(
            await booking_service.find_bookings_stuck_on_the_way(),
            "stuck-on-the-way flag",
            flagger(
                "captain_delay",
                lambda b: f"Captain has been 'on the way' for booking {b['booking_number']} for an unusually "
                f"long time without starting the service — worth checking in.",
            ),
        )

    async def flag_overrunning() -> None:
        await p.each(
            await booking_service.find_bookings_service_overrunning(),
            "service-overrun flag",
            flagger(
                "service_overrun",
                lambda b: f"Booking {b['booking_number']} has been in progress well past its planned duration "
                f"— worth checking in with the captain.",
            ),
        )

    # Anti-moonlighting step-gap sweeps: reached-but-not-started, and
    # walked-off-mid-service (see the finders' docstrings).
    async def flag_idle_after_arrival() -> None:
        await p.each(
            await booking_service.find_bookings_idle_after_arrival(),
            "idle-after-arrival flag",
            flagger(
                "idle_after_arrival",
                lambda b: f"Captain reached the customer for booking {b['booking_number']} but hasn't started "
                f"the service well past the allowed gap — please check what's holding it up.",
            ),
        )

    async def flag_left_site() -> None:
        await p.each(
            await booking_service.find_bookings_captain_left_site(),
            "left-site flag",
            flagger(
                "left_site_during_service",
                lambda b: f"Captain's live location is {b.get('_distance_from_site_m')}m away from booking {b['booking_number']}'s "
                f"address while the service is supposedly in progress — please check in.",
            ),
        )

    async def check_seat_counters() -> None:
        # Nightly safety net for the seat counters: report only — nothing is
        # written without repair=True. Drift means some path took or freed a
        # seat it didn't own; an admin reviews it and repairs it with
        # POST /api/v1/bookings/admin/reconcile-slot-counters?repair=true.
        if not await _claim_hourly_sweep(db, holder, "seat_reconcile_at", every=_SEAT_RECONCILE_EVERY):
            return
        report = await booking_service.reconcile_slot_counters(repair=False)
        drift = report.get("drift") or []
        if drift:
            logger.error(
                "Seat counter drift on %d counter(s), e.g. %s — review and repair via "
                "POST /api/v1/bookings/admin/reconcile-slot-counters?repair=true",
                len(drift), drift[:3],
            )

    async def request_reviews() -> None:
        # "Thanks for choosing Blussit — leave us a Google review" (NOTIFY):
        # once per completed + paid booking, about 2 h later, in the day only.
        # The service picks and claims the bookings itself; this only paces
        # it (every 10 minutes across all instances) and gives it the pass's
        # checkpoint so it stops at the sweep budget / a lost lease.
        sweep = getattr(notifications, "send_review_requests", None)
        if sweep is None or not _customer_hours_open():
            return
        if not await _claim_hourly_sweep(db, holder, "review_request_sweep_at", every=_REVIEW_SWEEP_EVERY):
            return
        result = await _call_supported(sweep, checkpoint=p.checkpoint)
        if isinstance(result, dict) and result.get("sent"):
            logger.info("Review-request sweep: %s", result)

    async def expire_unpaid() -> None:
        # Bookings whose customer chose "pay online" and never finished: the
        # slot has been held for them long enough (booking policy
        # `payment_window_minutes`) — cancel and hand it back. They were
        # never confirmed, so nobody but the customer ever knew about them.
        # Before releasing anything, Razorpay is asked whether the customer
        # actually paid (closed tab, dropped network — verify never ran):
        # paid ones are confirmed instead, and a checkout still open on their
        # screen, or a gateway we can't reach, means "not this pass".
        expired_reason = BookingCancelRequest(reason="Payment wasn't completed in time, so the slot was released.")
        expired_groups: set[str] = set()
        payments = PaymentService(db)
        window = int((await booking_service.policy_service.get_policy()).get("payment_window_minutes", 30))

        async def expire(booking: dict) -> None:
            group_id = booking.get("booking_group_id")
            if group_id:
                # A visit expires as one thing — one cancellation, one
                # message, one seat handed back.
                if group_id in expired_groups:
                    return
                expired_groups.add(group_id)
            if not await payments.prepare_expiry(booking, window):
                return
            # PAY-02: a payment can land between prepare_expiry's look and
            # this write — only_if_unpaid re-checks "still unpaid" IN the
            # cancel write, so a just-paid booking is never cancelled (a
            # silent no-op instead: no message, no seat released).
            if group_id:
                await booking_service.cancel_booking_group(
                    group_id, expired_reason, actor_id="system", actor_role="admin", only_if_unpaid=True
                )
            else:
                await booking_service.cancel_booking(
                    str(booking["_id"]), expired_reason, actor_id="system", actor_role="admin", only_if_unpaid=True
                )

        await p.each(await booking_service.find_bookings_payment_expired(), "unpaid-booking expiry", expire)

    async def remind_unpaid() -> None:
        # One nudge before an unpaid online booking's window closes — a
        # bank page that timed out shouldn't quietly cost the slot.
        policy_now = await booking_service.policy_service.get_policy()

        async def remind(booking: dict) -> None:
            cars = await booking_service._visit_cars(booking)
            reference = booking_service._visit_numbers(cars) if len(cars) > 1 else booking["booking_number"]
            wa_services, wa_reference, _wa_date, _wa_slot, _wa_vehicle, _wa_code = await booking_service._wa_details(booking, cars)
            minutes_left = str(int(policy_now.get("payment_reminder_minutes_before", 10)))
            await notifications.notify(
                booking["customer_id"],
                "Finish your payment",
                f"{reference} is waiting for payment — finish in the next {minutes_left} minutes to keep your slot"
                + ("." if any(c.get("prepaid_only") for c in cars) else ", or choose cash on service."),
                NotificationType.BOOKING,
                str(booking["_id"]),
                wa_event="payment_pending",
                wa_params=[wa_services, wa_reference, minutes_left],
                background=True,
            )
            # A tappable "Pay now" / "Cash instead" on top of the
            # approved-template text above — only actually lands within
            # WhatsApp's 24h session window, which is why it rides ALONGSIDE
            # the template rather than replacing it. Never lets a failure
            # here mark the reminder itself as failed.
            try:
                from app.services.whatsapp_bot_service import WhatsAppBotService

                await WhatsAppBotService(db).send_payment_reminder(booking)
            except Exception:
                logger.exception("Could not send payment-reminder buttons for %s", booking.get("booking_number"))
            await booking_service.mark_payment_reminder_sent(str(booking["_id"]))

        await p.each(await booking_service.find_bookings_payment_reminder_due(), "payment reminder", remind)

    async def settle_paid_links() -> None:
        # Razorpay payment links (WhatsApp bookings): ask Razorpay which
        # pending links got paid and settle them — the reliable half of the
        # two-path design (the browser callback being the other), and the
        # ONLY path in dev where the callback URL isn't publicly reachable.
        # The customer's receipt is sent ONCE by the settle path itself
        # (PaymentService._apply_link_paid → payment_confirmation template),
        # whichever of callback / webhook / this sweep won — no free-text
        # "✅ Payment received" here: outside the 24-hour window it was
        # refused, and inside it, a duplicate.
        await PaymentService(db).sync_pending_links()

    async def sync_pending_orders() -> None:
        # One-time checkout orders (booking or plan) whose browser never
        # reached /payments/verify: asked of Razorpay directly and settled
        # through the same claim verify uses. Runs BEFORE the unpaid expiry
        # so a paid-but-unverified booking is confirmed, not released.
        await PaymentService(db).sync_pending_orders()

    async def sync_autopay_renewals() -> None:
        # Auto-pay renewals: ask Razorpay which recurring mandates charged
        # again since the last pass and refresh those plans for their new
        # cycle (see sync_autopay_renewals). Runs BEFORE the expiry sweep
        # below so a plan that renewed on time is never briefly marked
        # expired.
        await PaymentService(db).sync_autopay_renewals()

    async def sync_pending_mandates() -> None:
        # Auto-pay mandates still awaiting their FIRST charge (a
        # manager-issued WhatsApp link the customer just opened, or a
        # self-serve checkout whose browser closed before verify ran) —
        # activated purely from what Razorpay reports, never a
        # client-supplied claim. See sync_pending_manager_mandates.
        await PaymentService(db).sync_pending_manager_mandates()

    # Passes: a heads-up two days before one ends and a note when it has —
    # each once — then the actual flip to EXPIRED (the stored status used
    # to change only lazily on read/consume, so untouched passes sat
    # "active" in the DB forever).
    async def _pass_ctx(sub: dict) -> tuple[str, str, str]:
        cid, pid = str(sub.get("customer_id") or ""), str(sub.get("plan_id") or "")
        customer = await db.users.find_one({"_id": ObjectId(cid)}) if ObjectId.is_valid(cid) else None
        plan = await db.subscription_plans.find_one({"_id": ObjectId(pid)}) if ObjectId.is_valid(pid) else None
        first = ((customer or {}).get("full_name") or "there").split(" ")[0]
        end = sub.get("end_date")
        # end_date is a computed instant (stored UTC) — from_stored, or a
        # pass ending late evening IST shows the day before.
        return first, (plan or {}).get("name") or "Monthly pass", (from_stored(end).strftime("%d %b") if end else "soon")

    async def warn_passes_ending() -> None:
        if not _customer_hours_open():
            return

        async def warn(sub: dict) -> None:
            first, plan_name, end_str = await _pass_ctx(sub)
            remaining = str(sub.get("remaining_service_count") or 0)
            renewal_start = await renewal_lined_up(db, sub)
            if renewal_start is not None:
                # The next period is already paid (a custom-plan renewal):
                # no "renew to keep going" (that template says it) — a plain
                # update through the generic utility template instead.
                await notifications.notify(
                    sub["customer_id"], f"{plan_name} ends {end_str}",
                    f"{remaining} washes left — book them by {end_str}. {renewal_continues_text(renewal_start)}",
                    NotificationType.SYSTEM, str(sub["_id"]),
                    background=True,
                )
            else:
                await notifications.notify(
                    sub["customer_id"], f"{plan_name} ends {end_str}",
                    f"{remaining} washes left — book them before it ends, or renew to keep going.",
                    NotificationType.SYSTEM, str(sub["_id"]),
                    wa_event="subscription_expiring", wa_params=[first, plan_name, end_str, remaining],
                    # Meta files this template as MARKETING (see EVENT_TEMPLATES):
                    # opted-out customers get the in-app note only.
                    wa_marketing=True,
                    background=True,
                )
            await mark_expiry_reminder_sent(db, str(sub["_id"]))
            if sub.get("enrollment_id"):
                # A society plan is one pass per car — one notice per plan,
                # not one per car.
                await db.user_subscriptions.update_many(
                    {"enrollment_id": sub["enrollment_id"], "customer_id": sub["customer_id"], "status": "active",
                     "expiry_reminder_sent": {"$ne": True}},
                    {"$set": {"expiry_reminder_sent": True}},
                )

        await p.each(await find_subscriptions_expiring_soon(db, days=2), "pass-expiry reminder", warn)

    async def expire_passes() -> None:
        # Each pass is flipped to EXPIRED only right after ITS "has ended"
        # note went out — never in bulk, which used to expire every ended
        # pass while only the first 200 were told. One that fails stays
        # active and is retried next pass; `attempted` keeps it from being
        # re-fetched (and retried in a tight loop) within this one. Waits
        # for daytime like every customer notice — the stored flip rides on
        # the note, and the pass already reads as expired meanwhile
        # (effective_status).
        if not _customer_hours_open():
            return
        attempted: set = set()
        # A society plan is one pass per car; its resident hears once.
        told_enrollments: set = set()

        async def expire(sub: dict) -> None:
            enrollment = sub.get("enrollment_id")
            if sub.get("customer_id") and not (enrollment and enrollment in told_enrollments):
                if enrollment:
                    told_enrollments.add(enrollment)
                first, plan_name, _end = await _pass_ctx(sub)
                renewal_start = await renewal_lined_up(db, sub)
                if renewal_start is not None:
                    # Already renewed (custom plan): no renew nudge.
                    await notifications.notify(
                        sub["customer_id"], f"{plan_name} continues", renewal_continues_text(renewal_start),
                        NotificationType.SYSTEM, str(sub["_id"]),
                        background=True,
                    )
                else:
                    await notifications.notify(
                        sub["customer_id"], f"{plan_name} has ended", "Renew any time to keep your car shining.",
                        NotificationType.SYSTEM, str(sub["_id"]),
                        wa_event="subscription_expired", wa_params=[first, plan_name],
                        background=True,
                    )
            await mark_subscription_expired(db, str(sub["_id"]))

        while batch := await find_subscriptions_ended(db, exclude_ids=attempted):
            attempted.update(sub["_id"] for sub in batch)
            await p.each(batch, "pass-ended note", expire)

    async def remind_pass_washes() -> None:
        # "3 washes left on your pass — book your next wash." Hourly, in the
        # day, at most weekly per pass (see find_passes_due_wash_reminder).
        # WhatsApp reuses the approved blussit_subscription_expiring
        # template, which Meta files as MARKETING — so it goes out only
        # through that template and never to an opted-out customer.
        if not _customer_hours_open() or not await _claim_hourly_sweep(db, holder, "wash_reminder_sweep_at"):
            return

        async def remind(sub: dict) -> None:
            if sub.get("customer_id"):
                first, plan_name, end_str = await _pass_ctx(sub)
                left = int(sub.get("remaining_service_count") or 0)
                washes = f"{left} wash{'' if left == 1 else 'es'}"
                await notifications.notify(
                    sub["customer_id"], "Book your next wash",
                    f"{washes} left on your {plan_name} pass — book your next wash.",
                    NotificationType.SYSTEM, str(sub["_id"]),
                    wa_event="pass_wash_reminder", wa_params=[first, plan_name, end_str, str(left)],
                    wa_marketing=True,
                    background=True,
                )
            await mark_wash_reminder_sent(db, str(sub["_id"]))

        await p.each(await find_passes_due_wash_reminder(db), "pass wash reminder", remind)

    async def nudge_repeat_bookings() -> None:
        # "Time for a wash?" — to customers whose last wash was
        # repeat_reminder_days ago (weekly by default) and who have nothing
        # booked and no live pass; each at most once per that many days.
        # Checked hourly, in the day only. Marketing: goes out on WhatsApp
        # only through the approved template and never to an opted-out
        # customer (NotificationService.notify). Runs LAST so it can never
        # hold up an operational sweep; a batch cut short by the time
        # budget is fine — the customers it didn't reach are still due next
        # hour, and the ones it did are in cooldown.
        policy_now = await booking_service.policy_service.get_policy()
        if not policy_now.get("repeat_reminder_enabled", True) or not _customer_hours_open():
            return
        if not await _claim_repeat_sweep(db, holder):
            return

        async def nudge(customer: dict) -> None:
            first = (customer.get("full_name") or "there").split(" ")[0]
            await notifications.notify(
                str(customer["_id"]),
                "Time for a wash?",
                "It's been a while since your last BLUSSIT wash — book your next one whenever your car needs it.",
                NotificationType.SYSTEM,
                None,
                wa_event="repeat_booking",
                wa_params=[first],
                wa_marketing=True,
                background=True,
            )
            await booking_service.mark_repeat_reminder_sent(str(customer["_id"]))

        days = int(policy_now.get("repeat_reminder_days") or DEFAULT_BOOKING_POLICY["repeat_reminder_days"])
        due = await booking_service.find_customers_due_repeat_reminder(days)
        await p.each(due, "repeat-booking reminder", nudge)

    try:
        await p.run("slot holds", release_holds)
        # Money first: a pass that runs out of budget must cut the
        # notification sweeps short (they're picked up next minute), never
        # the reconciliation that confirms a paid booking before the expiry
        # sweep would release its slot.
        await p.run("pending orders", sync_pending_orders)
        await p.run("unpaid expiry", expire_unpaid)
        await p.run("payment reminders", remind_unpaid)
        await p.run("payment links", settle_paid_links)
        await p.run("autopay renewals", sync_autopay_renewals)
        await p.run("pending mandates", sync_pending_mandates)
        await p.run("captain start reminders", remind_captains)
        await p.run("customer pre-slot reminders", remind_customers_before_slot)
        await p.run("unassigned nudges", nudge_unassigned)
        await p.run("late to start", flag_late_to_start)
        await p.run("captain not reached", flag_not_reached)
        await p.run("stuck on the way", flag_stuck_on_the_way)
        await p.run("service overrun", flag_overrunning)
        await p.run("idle after arrival", flag_idle_after_arrival)
        await p.run("left site", flag_left_site)
        await p.run("pass expiring soon", warn_passes_ending)
        await p.run("pass expired", expire_passes)
        await p.run("society attendance alerts", lambda: SocietyService(db).alert_missing_attendance())
        # Society premium-wash schedule: materialize repeat rules a plan month
        # ahead and book visit days N days out (docs/SOCIETY_PLANS.md §9).
        await p.run("society schedule", lambda: _society_schedule_sweep(db, p))
        await p.run("recycle-bin purge", purge_recycle_bin)
        await p.run("seat counter check", check_seat_counters)
        await p.run("review requests", request_reviews)
        await p.run("pass wash reminder", remind_pass_washes)
        await p.run("repeat-booking nudge", nudge_repeat_bookings)
    except _StopPass as stop:
        logger.warning("Reminder pass stopped early: %s", stop)


async def _release_lease(db, holder: str) -> None:
    """On a clean stop: let another instance take over at once instead of
    waiting out the lease."""
    try:
        await db.locks.update_one({"_id": _LEASE_ID, "holder": holder}, {"$set": {"expires_at": datetime.now(timezone.utc)}})
    except Exception:  # noqa: BLE001 — it simply lapses instead
        logger.warning("Could not release the reminder lease on shutdown — it lapses in %ss", _LEASE_SECONDS)


async def _reminder_loop() -> None:
    """Every 60 seconds, the lease holder runs one _sweep_once pass;
    everyone else idles. Ends (between passes or at the pass's next
    checkpoint) when shutdown sets _stop_event."""
    import uuid

    holder = uuid.uuid4().hex
    held = False
    while not _stopping():
        try:
            db = mongodb.db
            if db is None or not await _claim_lease(db, holder):
                held = False
                await _sleep_unless_stopping(30)
                continue
            held = True
            await _sweep_once(db, holder)
        except Exception:
            logger.exception("Reminder sweep failed")
        await _sleep_unless_stopping(60)
    if held and mongodb.db is not None:
        await _release_lease(mongodb.db, holder)


# Lease-free background loops (run on EVERY instance — the work they call
# claims its own rows/locks): kept here so shutdown can stop and await them.
_maintenance_tasks: set[asyncio.Task] = set()
# Failed WhatsApp sends (FAIL-02): retried every ~30 s, outside the reminder
# lease and its budgets. Each call is a short slice so a shutdown never waits
# long and never cuts a pass mid-queue; a pass stops after ~25 s / 100 rows.
_RETRY_EVERY_SECONDS = 30
_RETRY_PASS_SECONDS = 25.0
_RETRY_PASS_ROWS = 100
_RETRY_SLICE_SECONDS = 4.0
_RETRY_SLICE_ROWS = 20
# Hard ceiling on one slice: its budget plus one in-flight send.
_RETRY_SLICE_TIMEOUT_SECONDS = 30
# Approved-template sync with Meta (DEP-04): checked every ~5 minutes;
# sync_templates_if_due itself runs it at most hourly across instances.
_TEMPLATE_SYNC_EVERY_SECONDS = 300
_TEMPLATE_SYNC_TIMEOUT_SECONDS = 60


async def _boxed(name: str, timeout: float, call) -> None:
    """One time-boxed run of a background job — a failure or a timeout is
    logged, never raised (the loop carries on)."""
    deadline = asyncio.timeout(timeout)
    try:
        async with deadline:
            await call()
    except TimeoutError:
        if deadline.expired():
            logger.error("%s hit its %ss timeout and was cancelled", name, timeout)
        else:
            logger.exception("%s failed", name)
    except Exception:  # noqa: BLE001
        logger.exception("%s failed", name)


async def _retry_whatsapp_pass(db) -> None:
    from app.services.notification_service import NotificationService

    service = NotificationService(db)
    started, claimed = time.monotonic(), 0
    while not _stopping() and claimed < _RETRY_PASS_ROWS and time.monotonic() - started < _RETRY_PASS_SECONDS:
        stats = {}

        async def one_slice() -> None:
            nonlocal stats
            stats = await service.retry_outbox(
                limit=min(_RETRY_SLICE_ROWS, _RETRY_PASS_ROWS - claimed), time_budget_seconds=_RETRY_SLICE_SECONDS
            )

        await _boxed("WhatsApp retry", _RETRY_SLICE_TIMEOUT_SECONDS, one_slice)
        got = int((stats or {}).get("claimed") or 0)
        if not got:
            return  # queue drained (or the slice failed — next tick)
        claimed += got


async def _whatsapp_retry_loop() -> None:
    while not _stopping():
        db = mongodb.db
        if db is not None:
            await _retry_whatsapp_pass(db)
        await _sleep_unless_stopping(_RETRY_EVERY_SECONDS)


async def _sync_whatsapp_templates(db) -> None:
    from app.services.whatsapp_crm_service import WhatsAppCrmService

    await WhatsAppCrmService(db).sync_templates_if_due(max_age_minutes=60)


async def _template_sync_loop() -> None:
    # The boot sync runs in _deferred_init; this keeps it fresh afterwards.
    await _sleep_unless_stopping(_TEMPLATE_SYNC_EVERY_SECONDS)
    while not _stopping():
        db = mongodb.db
        if db is not None:
            await _boxed("WhatsApp template sync", _TEMPLATE_SYNC_TIMEOUT_SECONDS, lambda: _sync_whatsapp_templates(db))
        await _sleep_unless_stopping(_TEMPLATE_SYNC_EVERY_SECONDS)


def _start_maintenance(coro) -> asyncio.Task:
    task = asyncio.create_task(coro)
    _maintenance_tasks.add(task)
    task.add_done_callback(_maintenance_tasks.discard)
    return task


# Idempotent one-off migrations/backfills other modules provide, run by the
# worker at boot AFTER the index build. Each entry is (label, candidates):
# the first (module, function) that exists is called with the database; a
# build that has none of them simply skips the step (logged). Every function
# here must be idempotent and safe while the API serves traffic — the
# worker boots first on a deploy (DEPLOYMENT.md), but the API keeps running.
_OPTIONAL_BOOT_TASKS: tuple[tuple[str, tuple[tuple[str, str], ...]], ...] = (
    (
        # MONEY: amount_paid / wallet_applied / amount_due materialised on
        # bookings written before those fields existed (feature plan 1.5).
        "booking money fields",
        (("app.services.money_service", "backfill_booking_money"),),
    ),
    (
        # MONEY: open late-cancellation charges (customer_charges) become
        # customer-wallet debit rows (feature plan 1.1/1.2).
        "open charges -> customer wallet",
        (
            ("app.services.money_service", "migrate_open_charges_to_wallet"),
            ("app.services.customer_wallet_service", "migrate_open_charges_to_wallet"),
            ("app.services.payment_service", "migrate_open_charges_to_wallet"),
            ("app.services.customer_charge_service", "migrate_open_charges_to_wallet"),
        ),
    ),
    (
        # BOOKING: live bookings get the address snapshot they now carry
        # (feature plan 1.3 "address side door closed").
        "booking address snapshots",
        (
            ("app.services.booking_service", "backfill_address_snapshots"),
            ("app.services.booking_service", "backfill_booking_address_snapshots"),
            ("app.services.booking_service", "backfill_address_snapshot"),
            ("app.services.profile_service", "backfill_address_snapshots"),
        ),
    ),
    (
        # Account emails stored lower-case (login is case-insensitive).
        "lower-case account emails",
        (("app.repositories.user_repository", "lowercase_user_emails"),),
    ),
)


def _resolve_optional(candidates) -> tuple[str, object] | None:
    import importlib

    for module_name, attr in candidates:
        try:
            module = importlib.import_module(module_name)
        except ImportError:
            continue
        fn = getattr(module, attr, None)
        if callable(fn):
            return f"{module_name}.{attr}", fn
    return None


async def _run_optional_boot_task(label: str, candidates, db) -> None:
    """One optional boot migration: skipped when this build doesn't have it,
    logged (with its result) when it runs, never raised — it is retried on
    the next boot."""
    import inspect

    found = _resolve_optional(candidates)
    if found is None:
        logger.info("Boot task %r: not in this build — skipped", label)
        return
    name, fn = found
    try:
        result = fn(db)
        if inspect.isawaitable(result):
            result = await result
        logger.info("Boot task %r (%s) done: %s", label, name, result)
    except Exception:  # noqa: BLE001 — idempotent, retried next boot
        logger.exception("Boot task %r (%s) failed — retried next boot", label, name)


async def _deferred_init() -> None:
    """Worker (and RUN_MODE=all) only. Index creation (~80 round-trips; real
    BUILDS on a first boot) and the idempotent backfills run AFTER the
    process starts serving its probes — a health probe must never kill the
    container over startup DB work. The API never builds indexes (it would
    race this build); it only checks the critical ones in /api/ready."""
    from app.core.database import create_indexes, get_database

    try:
        await create_indexes()
    except Exception:
        logger.exception("Index creation failed — queries still work, just unindexed until next boot")
    try:
        from app.services.auth_service import assign_missing_employee_ids

        await assign_missing_employee_ids(get_database())
    except Exception:
        logger.exception("Employee-id backfill failed")
    try:
        from app.services.coupon_service import CouponService

        await CouponService(get_database()).ensure_default_launch_offer()
    except Exception:
        logger.exception("Default launch offer initialization failed")
    try:
        from app.services.booking_service import backfill_last_completed_at

        await backfill_last_completed_at(get_database())
    except Exception:
        logger.exception("last_completed_at backfill failed — retried next boot")
    try:
        # Seat ownership for bookings made before holds_seat existed
        # (idempotent, marker seat_ownership_v1) — after the index build.
        from app.services.booking_service import backfill_seat_ownership

        await backfill_seat_ownership(get_database())
    except Exception:
        logger.exception("Seat-ownership backfill failed — retried next boot")
    for label, candidates in _OPTIONAL_BOOT_TASKS:
        if _stopping():
            return
        await _run_optional_boot_task(label, candidates, get_database())
    # DEP-04: pick up template approvals/pauses from Meta at boot (at
    # most hourly across instances; _template_sync_loop keeps it fresh).
    await _boxed("WhatsApp template sync", _TEMPLATE_SYNC_TIMEOUT_SECONDS, lambda: _sync_whatsapp_templates(get_database()))


# The long-running loops this process started, by name — /api/health reports
# a loop that ENDED while the process isn't shutting down (they catch every
# error, so that only happens on a bug), and the worker's liveness probe then
# restarts the container instead of leaving the sweeps silently dead.
_loop_tasks: dict[str, asyncio.Task] = {}


def _dead_loops() -> list[str]:
    if _stopping():
        return []
    return sorted(name for name, task in _loop_tasks.items() if task.done())


@app.on_event("startup")
async def on_startup() -> None:
    global _reminder_task, _stop_event
    # Production misconfiguration must fail LOUDLY at boot, not quietly at
    # exploit time (P0-3 / DEP-06): Razorpay test keys in production "take"
    # test-card payments, the default JWT secret lets anyone forge an admin
    # token, a log-only WhatsApp provider sends nothing... Every rule lives
    # in app.core.config.validate_settings (the deploy script runs the same
    # checks); ALL problems are logged, then the app refuses to start —
    # before it touches the database.
    try:
        ensure_valid_settings(settings)
    except Exception as exc:
        for problem in getattr(exc, "problems", [str(exc)]):
            logger.critical("Configuration: %s", problem)
        raise
    mode = _run_mode()
    logger.warning("RUN_MODE=%s — %s", mode, {
        "api": "serving HTTP/webhooks/websockets; background loops run in blussit-worker",
        "worker": "running background loops, index builds and backfills; only /api/health and /api/ready are served",
        "all": "serving HTTP AND running the background loops in this one process",
    }[mode])
    # Which Razorpay keys are loaded is the single most expensive thing to
    # get wrong in either direction: testing against live moves real money.
    logger.warning("Razorpay is in %s mode", settings.razorpay_mode.upper())
    if settings.dev_tools_active:
        logger.warning(
            "DEV TOOLS ON (local test database %s): every OTP is %s and nothing is sent.",
            settings.MONGO_DB_NAME, settings.DEV_OTP_CODE,
        )
    elif settings.DEV_TOOLS_ENABLED:
        logger.critical("DEV_TOOLS_ENABLED is set but ignored — only honoured with APP_ENV=development and a local database.")
    if not settings.DEBUG:
        if settings.razorpay_mode == "test":
            logger.error("Razorpay TEST keys outside DEBUG (APP_ENV=%s) — no real payment can succeed.", settings.APP_ENV)
        insecure = [o for o in settings.cors_origins_list if o.startswith("http://") and "localhost" not in o and "127.0.0.1" not in o]
        if insecure:
            logger.warning("CORS_ORIGINS has plain-http origins: %s", insecure)
        if settings.WHATSAPP_PROVIDER == "meta_cloud" and not settings.WHATSAPP_APP_SECRET:
            logger.warning("WHATSAPP_APP_SECRET is empty — webhook signature checking is fail-closed, inbound WhatsApp will be rejected until it is set.")
    init_error_tracking(settings.SENTRY_DSN, settings.APP_ENV, settings.SENTRY_TRACES_SAMPLE_RATE)
    _stop_event = asyncio.Event()
    _loop_tasks.clear()
    await connect_to_mongo(build_indexes=False)

    if _runs_background_jobs():
        _track_startup_task(asyncio.create_task(_deferred_init()))
        _reminder_task = asyncio.create_task(_reminder_loop())
        _loop_tasks["reminder"] = _reminder_task
        _loop_tasks["whatsapp_retry"] = _start_maintenance(_whatsapp_retry_loop())
        _loop_tasks["template_sync"] = _start_maintenance(_template_sync_loop())
    # PERF-01: websocket events reach users on every instance. Runs in EVERY
    # mode: on the API it delivers events to this instance's sockets; on the
    # worker it is what makes the sweeps' broadcasts (a booking cancelled by
    # the expiry sweep, a flag raised) reach the API instances at all —
    # ws_manager publishes to ws_events only while its relay is live, and
    # otherwise delivers locally (where a worker has no sockets).
    from app.core.ws_manager import manager as ws_manager

    if mongodb.db is not None:
        _loop_tasks["ws_relay"] = _start_maintenance(ws_manager.run_relay(mongodb.db, _stopping))
    logger.info("%s started in %s mode (RUN_MODE=%s)", settings.APP_NAME, settings.APP_ENV, mode)


async def _drain(tasks: set[asyncio.Task], deadline: float, what: str) -> None:
    """Wait for `tasks` until `deadline` (monotonic), then cancel what's left."""
    pending = {t for t in tasks if not t.done()}
    if not pending:
        return
    remaining = max(0.0, deadline - time.monotonic())
    _done, still = await asyncio.wait(pending, timeout=remaining)
    if still:
        logger.warning("Shutdown: %d %s still running after %ss — cancelled", len(still), what, _SHUTDOWN_GRACE_SECONDS)
        for task in still:
            task.cancel()
        await asyncio.wait(still, timeout=0.5)


@app.on_event("shutdown")
async def on_shutdown() -> None:
    """FAIL-06: stop the reminder loop and the WhatsApp retry / template
    sync loops at their next checkpoint (not in the middle of a send), let
    start-up work and in-flight background WhatsApp sends finish — all within
    _SHUTDOWN_GRACE_SECONDS — and only then close the HTTP clients and the
    database they need."""
    from app.core import http_client
    from app.services import notification_service

    deadline = time.monotonic() + _SHUTDOWN_GRACE_SECONDS
    if _stop_event is not None:
        _stop_event.set()
    loops = set(_maintenance_tasks) | ({_reminder_task} if _reminder_task is not None else set())
    await _drain(loops, deadline, "background loop(s)")
    background = set(getattr(notification_service, "_background_tasks", ()) or ())
    await _drain(set(_startup_tasks) | background, deadline, "background task(s)")
    await http_client.close_shared_clients()
    await close_mongo_connection()


@app.exception_handler(AppException)
async def app_exception_handler(request: Request, exc: AppException) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content={"success": False, "error_code": exc.error_code, "message": exc.message, "details": exc.details},
    )


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    # pydantic v2 tucks the ORIGINAL exception object into ctx.error for any
    # `raise ValueError(...)` from a field/model validator (e.g. every
    # "one of X or Y, not both" cross-field check in this codebase) — a
    # bare ValueError isn't JSON-serializable, so building this response
    # with exc.errors() untouched crashed instead of returning 422, taking
    # the whole request down with it (this endpoint's own error handler
    # failing has no further net under it). Stringify ctx.error only;
    # everything else in the error dict is already JSON-safe.
    errors = exc.errors()
    for error in errors:
        ctx = error.get("ctx")
        if isinstance(ctx, dict) and isinstance(ctx.get("error"), Exception):
            ctx["error"] = str(ctx["error"])
        # The echoed `input` is whatever the client sent — a NaN/Infinity
        # (JSON bodies may carry them) can't be written back out as JSON,
        # which turned this 422 into a 500. Drop it rather than echo it.
        if "input" in error:
            try:
                json.dumps(error["input"], allow_nan=False)
            except (TypeError, ValueError):
                error.pop("input")
    return JSONResponse(
        status_code=422,
        content={
            "success": False,
            "error_code": "VALIDATION_ERROR",
            "message": "Request validation failed",
            "details": {"errors": errors},
        },
    )


@app.exception_handler(DuplicateKeyError)
async def duplicate_key_handler(request: Request, exc: DuplicateKeyError) -> JSONResponse:
    """A unique index refusing a write is the caller's conflict, not a
    server fault — e.g. re-adding a service / vehicle type / category whose
    name (slug) is taken, including by a deleted one the index still
    holds. Paths that care give their own message; this is the floor so
    none of them surfaces as "Something went wrong"."""
    # Field NAMES only — keyValue can be a phone number or an email.
    logger.info("Duplicate key on %s %s: %s", request.method, request.url.path, sorted(((exc.details or {}).get("keyPattern") or {}).keys()))
    return JSONResponse(
        status_code=409,
        content={"success": False, "error_code": "CONFLICT", "message": "That name (or number) is already in use — pick a different one."},
    )


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    # Outer net only (an error raised by a middleware outside CORS) — the
    # CatchUnhandledErrors middleware answers everything else, with CORS.
    logger.exception("Unhandled exception: %s", exc)
    return _server_error_response()


@app.get("/robots.txt", include_in_schema=False)
async def robots_txt():
    # The API host is never crawled (see the X-Robots-Tag above); the
    # website's own robots.txt lives on blussit.com.
    from fastapi.responses import PlainTextResponse

    return PlainTextResponse("User-agent: *\nDisallow: /\n")


@app.get("/api/health", tags=["Health"])
async def health_check():
    """LIVENESS: the process is up and serving. Deliberately touches no
    dependency — a database blip must not get healthy containers restarted.
    Readiness (can this instance take traffic?) is /api/ready.

    The one in-process check: a background loop this process started has
    ENDED (they catch every error, so only a bug does that) -> 503, and the
    worker's liveness probe restarts the container rather than leave the
    sweeps silently dead. In RUN_MODE=api there are no such loops except
    the websocket relay."""
    mode = _run_mode()
    dead = _dead_loops()
    if dead:
        logger.error("Liveness: background loop(s) stopped unexpectedly: %s", dead)
        return JSONResponse(
            status_code=503,
            content={"success": False, "error_code": "LOOP_DEAD", "message": "A background loop stopped.", "mode": mode, "dead_loops": dead},
        )
    return {"success": True, "message": f"{settings.APP_NAME} API is running", "env": settings.APP_ENV, "mode": mode}


# Readiness (DEP-05 / DEP-08). Answers fast even when Mongo hangs.
_READY_PING_TIMEOUT_SECONDS = 2.0
_READY_INDEX_TIMEOUT_SECONDS = 5.0
# A positive index check is reused this long — it costs one listIndexes per
# collection, and indexes don't disappear between probes.
_INDEX_OK_CACHE_SECONDS = 60.0
_indexes_ok_until = 0.0


async def _missing_critical_indexes(db) -> list[str] | None:
    """The CORE-owned checker in app.core.database, if this build has it.
    None = not checked. Accepts a sync or async function, with or without
    the database argument."""
    import inspect

    from app.core import database

    checker = getattr(database, "missing_critical_indexes", None)
    if checker is None:
        return None
    # Hand it the database the probe just pinged when it takes one.
    params = [p for p in inspect.signature(checker).parameters.values() if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)]
    result = checker(db) if params else checker()
    if inspect.isawaitable(result):
        result = await result
    return [str(name) for name in (result or [])]


@app.get("/api/ready", tags=["Health"])
async def readiness_check():
    """READINESS: 200 only when this instance can serve real requests — the
    database answers a ping within 2 s and the critical unique indexes (the
    duplicate-booking / payment-uniqueness guards) exist. 503 with the
    failing check otherwise. Used by the Cloud Run startup probe and the
    deploy script's post-deploy check; never exposes connection details.

    Same check in every RUN_MODE. Only the worker (or `all`) BUILDS
    indexes; an API instance just waits for them — on a brand-new database
    the API is not ready until the worker's first index build finishes,
    which is why a deploy rolls out blussit-worker before blussit-api."""
    global _indexes_ok_until
    checks: dict[str, str] = {"database": "not_connected", "indexes": "not_checked"}
    body: dict = {}
    db = mongodb.db
    if db is not None:
        try:
            await asyncio.wait_for(db.command("ping"), timeout=_READY_PING_TIMEOUT_SECONDS)
            checks["database"] = "ok"
        except asyncio.TimeoutError:
            checks["database"] = "timeout"
        except Exception as exc:  # noqa: BLE001 — any failure = not ready
            logger.warning("Readiness: database ping failed (%s)", type(exc).__name__)
            checks["database"] = "error"
    if checks["database"] == "ok":
        if time.monotonic() < _indexes_ok_until:
            checks["indexes"] = "ok"
        else:
            try:
                missing = await asyncio.wait_for(_missing_critical_indexes(db), timeout=_READY_INDEX_TIMEOUT_SECONDS)
                if missing is None:
                    checks["indexes"] = "not_checked"
                elif missing:
                    checks["indexes"] = "missing"
                    body["missing_indexes"] = missing
                else:
                    checks["indexes"] = "ok"
                    _indexes_ok_until = time.monotonic() + _INDEX_OK_CACHE_SECONDS
            except Exception as exc:  # noqa: BLE001
                logger.warning("Readiness: critical-index check failed (%s)", type(exc).__name__)
                checks["indexes"] = "error"
    ready = checks["database"] == "ok" and checks["indexes"] in ("ok", "not_checked")
    body["mode"] = _run_mode()
    if ready:
        return {"success": True, "message": "ready", "checks": checks, "mode": body["mode"]}
    if checks["indexes"] == "missing":
        logger.error("Readiness: critical indexes missing: %s", body.get("missing_indexes"))
    return JSONResponse(
        status_code=503,
        content={"success": False, "error_code": "NOT_READY", "message": "Not ready to serve traffic.", "checks": checks, **body},
    )


def include_business_routers(target: FastAPI) -> None:
    """Every public/business router — mounted only where RUN_MODE serves
    them (api, all). A worker mounts none: its HTTP surface is the health
    and readiness probes above."""
    api_prefix = settings.API_V1_PREFIX
    target.include_router(auth_routes.router, prefix=api_prefix)
    target.include_router(user_routes.router, prefix=api_prefix)
    target.include_router(profile_routes.vehicle_router, prefix=api_prefix)
    target.include_router(profile_routes.address_router, prefix=api_prefix)
    target.include_router(catalog_routes.category_router, prefix=api_prefix)
    target.include_router(catalog_routes.service_router, prefix=api_prefix)
    target.include_router(catalog_routes.combo_router, prefix=api_prefix)
    target.include_router(service_center_routes.router, prefix=api_prefix)
    target.include_router(booking_routes.router, prefix=api_prefix)
    target.include_router(booking_routes.charge_router, prefix=api_prefix)
    target.include_router(subscription_routes.plan_router, prefix=api_prefix)
    target.include_router(subscription_routes.subscription_router, prefix=api_prefix)
    target.include_router(coupon_routes.router, prefix=api_prefix)
    target.include_router(complaint_routes.router, prefix=api_prefix)
    target.include_router(review_routes.router, prefix=api_prefix)
    target.include_router(inventory_routes.router, prefix=api_prefix)
    target.include_router(notification_routes.router, prefix=api_prefix)
    target.include_router(staff_ops_routes.attendance_router, prefix=api_prefix)
    target.include_router(staff_ops_routes.leave_router, prefix=api_prefix)
    target.include_router(staff_directory_routes.router, prefix=api_prefix)
    target.include_router(crm_routes.router, prefix=api_prefix)
    target.include_router(analytics_routes.router, prefix=api_prefix)
    target.include_router(content_routes.faq_router, prefix=api_prefix)
    target.include_router(content_routes.testimonial_router, prefix=api_prefix)
    target.include_router(content_routes.settings_router, prefix=api_prefix)
    target.include_router(content_routes.public_router, prefix=api_prefix)
    target.include_router(content_routes.contact_router, prefix=api_prefix)
    target.include_router(audit_log_routes.router, prefix=api_prefix)
    target.include_router(wallet_routes.router, prefix=api_prefix)
    target.include_router(customer_wallet_routes.router, prefix=api_prefix)
    target.include_router(payment_routes.router, prefix=api_prefix)
    target.include_router(upload_routes.router, prefix=api_prefix)
    target.include_router(vehicle_type_routes.router, prefix=api_prefix)
    target.include_router(pricing_routes.router, prefix=api_prefix)
    target.include_router(booking_policy_routes.router, prefix=api_prefix)
    target.include_router(homepage_config_routes.router, prefix=api_prefix)
    target.include_router(settings_history_routes.router, prefix=api_prefix)
    target.include_router(ws_routes.router, prefix=api_prefix)
    target.include_router(purchase_confirmation_routes.router, prefix=api_prefix)
    target.include_router(whatsapp_webhook_routes.router, prefix=api_prefix)
    target.include_router(whatsapp_crm_routes.router, prefix=api_prefix)
    target.include_router(zone_routes.router, prefix=api_prefix)
    target.include_router(coverage_lead_routes.router, prefix=api_prefix)
    target.include_router(society_routes.form_router, prefix=api_prefix)
    target.include_router(society_routes.society_router, prefix=api_prefix)
    target.include_router(society_routes.enrollment_router, prefix=api_prefix)
    target.include_router(society_routes.plan_router, prefix=api_prefix)
    target.include_router(society_schedule_routes.router, prefix=api_prefix)
    target.include_router(society_support_routes.issue_router, prefix=api_prefix)
    target.include_router(society_support_routes.lead_router, prefix=api_prefix)


if _serves_public_routes():
    include_business_routers(app)
