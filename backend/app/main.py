import asyncio
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

from app.core.config import settings
from app.core.database import close_mongo_connection, connect_to_mongo, mongodb
from app.core.exceptions import AppException
from app.core.storage import upload_root
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

logging.basicConfig(level=logging.INFO)
# WebSocket authentication uses a query token because browsers cannot attach
# an Authorization header during the handshake. Uvicorn's access logger would
# otherwise write that token into every request log line.


class _RedactAccessTokens(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = re.sub(r"([?&]token=)[^&\s]+", r"\1[REDACTED]", record.msg)
        if isinstance(record.args, tuple):
            record.args = tuple(
                re.sub(r"([?&]token=)[^&\s]+", r"\1[REDACTED]", value) if isinstance(value, str) else value
                for value in record.args
            )
        return True


_redact_access_tokens = _RedactAccessTokens()
logging.getLogger("uvicorn.access").addFilter(_redact_access_tokens)
logging.getLogger("uvicorn.error").addFilter(_redact_access_tokens)
logger = logging.getLogger(__name__)

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

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
# JSON compresses ~5x; on a phone connection that is the difference between
# one packet and several for the catalogue responses. Added last = outermost.
# Level 5: level 9 (the default) costs several times the CPU per response
# for a percent or two less on the wire.
app.add_middleware(GZipMiddleware, minimum_size=1024, compresslevel=5)

# Serves whatever app.core.storage's "local" provider writes (captain
# before/after photos) back out as plain static files — the directory must
# exist before StaticFiles will mount, hence the mkdir here.
(upload_root() / "photos").mkdir(parents=True, exist_ok=True)
app.mount("/uploads", StaticFiles(directory=str(upload_root())), name="uploads")


_reminder_task: asyncio.Task | None = None

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
# Inside a per-item loop the lease is renewed at most this often.
_ITEM_RENEW_SECONDS = 10
# The repeat-booking nudge and the pass wash reminder run hourly. Their last
# runs are stamped on the lease doc, not kept in this process — a fresh
# holder (a new instance) must not re-run them straight away.
_REPEAT_SWEEP_EVERY = timedelta(hours=1)


class _StopPass(Exception):
    """A checkpoint's verdict that this pass must end now: the lease was
    lost to another instance, or the pass spent its time budget."""


async def _claim_lease(db, holder: str) -> bool:
    """Claim (or renew) the lease at the top of a tick."""
    now = datetime.now(timezone.utc)
    result = await db.locks.find_one_and_update(
        {"_id": _LEASE_ID, "$or": [{"holder": holder}, {"expires_at": {"$lt": now}}]},
        {"$set": {"holder": holder, "expires_at": now + timedelta(seconds=_LEASE_SECONDS)}},
    )
    if result is not None:
        return True
    # No claimable doc — either someone else holds a live lease, or the doc
    # doesn't exist yet. Try to create it; losing the race is fine.
    try:
        await db.locks.insert_one({"_id": _LEASE_ID, "holder": holder, "expires_at": now + timedelta(seconds=_LEASE_SECONDS)})
        return True
    except Exception:  # noqa: BLE001 — duplicate key = someone else holds it
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


async def _claim_hourly_sweep(db, holder: str, stamp: str) -> bool:
    """True at most once per _REPEAT_SWEEP_EVERY across ALL instances for
    the sweep stamped in `stamp` — the stamp and the check are one atomic
    write on the lease doc, and only the live holder can make it."""
    now = datetime.now(timezone.utc)
    result = await db.locks.update_one(
        {
            "_id": _LEASE_ID,
            "holder": holder,
            "$or": [{stamp: None}, {stamp: {"$lte": now - _REPEAT_SWEEP_EVERY}}],
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
    enforces the time budget."""

    def __init__(self, db, holder: str):
        self.db = db
        self.holder = holder
        self.started = self.renewed = time.monotonic()

    async def checkpoint(self, renew: bool = False) -> None:
        now = time.monotonic()
        if now - self.started > _PASS_BUDGET_SECONDS:
            raise _StopPass(f"time budget of {_PASS_BUDGET_SECONDS}s spent")
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
        try:
            await sweep()
        except _StopPass:
            raise
        except Exception:
            logger.exception("Reminder sweep %r failed — the rest of the pass still runs", name)

    async def each(self, items, what: str, handle) -> None:
        """`handle` per item, with a checkpoint before each: one bad row is
        logged and skipped, and a long list can't outrun the lease."""
        for item in items:
            await self.checkpoint()
            try:
                await handle(item)
            except _StopPass:
                raise
            except Exception:
                logger.exception("Reminder sweep: %s failed for %s", what, item.get("booking_number") or item.get("_id"))


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
    Plus pass lifecycle, the recycle-bin purge and (hourly, last) the
    pass wash reminder and the repeat-booking nudge. Every customer
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
    )
    from app.services.whatsapp_service import WhatsAppService
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
        overdue = await db.bookings.find({"is_deleted": True, "deleted_at": {"$lt": purge_cutoff}}, {"_id": 1}).to_list(length=200)
        # permanently_delete_booking purges a whole visit at once — a
        # multi-car group's OTHER cars are already gone by the time this
        # loop reaches their own row, which would otherwise raise (and log)
        # a spurious NotFoundException for each one.
        already_purged: set[str] = set()

        async def purge(row: dict) -> None:
            booking_id = str(row["_id"])
            if booking_id in already_purged:
                return
            result = await booking_service.permanently_delete_booking(booking_id, force=True)
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
                )
            await booking_service.mark_reminder_sent(str(booking["_id"]))

        await p.each(await booking_service.find_bookings_needing_reminder(), "captain start reminder", remind)

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
            if group_id:
                await booking_service.cancel_booking_group(group_id, expired_reason, actor_id="system", actor_role="admin")
            else:
                await booking_service.cancel_booking(str(booking["_id"]), expired_reason, actor_id="system", actor_role="admin")

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
        # The customer gets their ✅ right in the WhatsApp chat.
        wa = WhatsAppService(db)

        async def _confirm_link_paid(order: dict) -> None:
            customer = await db.users.find_one({"_id": ObjectId(order["customer_id"])}) if ObjectId.is_valid(order["customer_id"]) else None
            if customer and customer.get("phone"):
                await wa.send_text(
                    customer["phone"],
                    f"✅ Payment received — booking *{order.get('booking_number')}* is fully paid. Thank you!",
                )

        await PaymentService(db).sync_pending_links(_confirm_link_paid)

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
            await notifications.notify(
                sub["customer_id"], f"{plan_name} ends {end_str}",
                f"{remaining} washes left — book them before it ends, or renew to keep going.",
                NotificationType.SYSTEM, str(sub["_id"]),
                wa_event="subscription_expiring", wa_params=[first, plan_name, end_str, remaining],
            )
            await mark_expiry_reminder_sent(db, str(sub["_id"]))

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

        async def expire(sub: dict) -> None:
            if sub.get("customer_id"):
                first, plan_name, _end = await _pass_ctx(sub)
                await notifications.notify(
                    sub["customer_id"], f"{plan_name} has ended", "Renew any time to keep your car shining.",
                    NotificationType.SYSTEM, str(sub["_id"]),
                    wa_event="subscription_expired", wa_params=[first, plan_name],
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
        await p.run("unassigned nudges", nudge_unassigned)
        await p.run("late to start", flag_late_to_start)
        await p.run("captain not reached", flag_not_reached)
        await p.run("stuck on the way", flag_stuck_on_the_way)
        await p.run("service overrun", flag_overrunning)
        await p.run("idle after arrival", flag_idle_after_arrival)
        await p.run("left site", flag_left_site)
        await p.run("pass expiring soon", warn_passes_ending)
        await p.run("pass expired", expire_passes)
        await p.run("recycle-bin purge", purge_recycle_bin)
        await p.run("pass wash reminder", remind_pass_washes)
        await p.run("repeat-booking nudge", nudge_repeat_bookings)
    except _StopPass as stop:
        logger.warning("Reminder pass stopped early: %s", stop)


async def _reminder_loop() -> None:
    """Every 60 seconds, the lease holder runs one _sweep_once pass;
    everyone else idles."""
    import uuid

    holder = uuid.uuid4().hex
    while True:
        try:
            db = mongodb.db
            if db is None or not await _claim_lease(db, holder):
                await asyncio.sleep(30)
                continue
            await _sweep_once(db, holder)
        except Exception:
            logger.exception("Reminder sweep failed")
        await asyncio.sleep(60)


@app.on_event("startup")
async def on_startup() -> None:
    global _reminder_task
    # Production misconfiguration must fail LOUDLY at boot, not quietly at
    # exploit time: with the default secret anyone can forge an admin JWT.
    # Which Razorpay keys are loaded is the single most expensive thing to
    # get wrong in either direction: testing against live moves real money,
    # and shipping to production on test keys silently takes none.
    logger.warning("Razorpay is in %s mode", settings.razorpay_mode.upper())
    if not settings.DEBUG:
        if settings.razorpay_mode == "test":
            logger.error(
                "PRODUCTION IS RUNNING ON RAZORPAY TEST KEYS — no real payment can succeed. "
                "Copy RAZORPAY_LIVE_KEY_ID/SECRET into RAZORPAY_KEY_ID/SECRET."
            )
        if settings.JWT_SECRET_KEY == "change-this-super-secret-key-in-production":
            raise RuntimeError("Refusing to start: JWT_SECRET_KEY is still the default. Set a real secret in .env.")
        if settings.WHATSAPP_PROVIDER == "meta_cloud" and not settings.WHATSAPP_APP_SECRET:
            logger.warning("WHATSAPP_APP_SECRET is empty — webhook signature checking is fail-closed, inbound WhatsApp will be rejected until it is set.")
    await connect_to_mongo(build_indexes=False)

    async def _deferred_init() -> None:
        """Index creation (74 round-trips; real BUILDS on a first boot) and
        the employee-id backfill run AFTER the app starts serving — a
        health probe must never kill the container over startup DB work."""
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

    asyncio.create_task(_deferred_init())
    _reminder_task = asyncio.create_task(_reminder_loop())
    logger.info("%s started in %s mode", settings.APP_NAME, settings.APP_ENV)


@app.on_event("shutdown")
async def on_shutdown() -> None:
    from app.core.http_client import close_shared_clients

    if _reminder_task:
        _reminder_task.cancel()
    await close_shared_clients()
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
    return JSONResponse(
        status_code=422,
        content={
            "success": False,
            "error_code": "VALIDATION_ERROR",
            "message": "Request validation failed",
            "details": {"errors": errors},
        },
    )


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.exception("Unhandled exception: %s", exc)
    return JSONResponse(
        status_code=500,
        content={"success": False, "error_code": "SERVER_ERROR", "message": "Something went wrong. Please try again."},
    )


@app.get("/api/health", tags=["Health"])
async def health_check():
    return {"success": True, "message": f"{settings.APP_NAME} API is running", "env": settings.APP_ENV}


api_prefix = settings.API_V1_PREFIX
app.include_router(auth_routes.router, prefix=api_prefix)
app.include_router(user_routes.router, prefix=api_prefix)
app.include_router(profile_routes.vehicle_router, prefix=api_prefix)
app.include_router(profile_routes.address_router, prefix=api_prefix)
app.include_router(catalog_routes.category_router, prefix=api_prefix)
app.include_router(catalog_routes.service_router, prefix=api_prefix)
app.include_router(catalog_routes.combo_router, prefix=api_prefix)
app.include_router(service_center_routes.router, prefix=api_prefix)
app.include_router(booking_routes.router, prefix=api_prefix)
app.include_router(subscription_routes.plan_router, prefix=api_prefix)
app.include_router(subscription_routes.subscription_router, prefix=api_prefix)
app.include_router(coupon_routes.router, prefix=api_prefix)
app.include_router(complaint_routes.router, prefix=api_prefix)
app.include_router(review_routes.router, prefix=api_prefix)
app.include_router(inventory_routes.router, prefix=api_prefix)
app.include_router(notification_routes.router, prefix=api_prefix)
app.include_router(staff_ops_routes.attendance_router, prefix=api_prefix)
app.include_router(staff_ops_routes.leave_router, prefix=api_prefix)
app.include_router(staff_directory_routes.router, prefix=api_prefix)
app.include_router(crm_routes.router, prefix=api_prefix)
app.include_router(analytics_routes.router, prefix=api_prefix)
app.include_router(content_routes.faq_router, prefix=api_prefix)
app.include_router(content_routes.testimonial_router, prefix=api_prefix)
app.include_router(content_routes.settings_router, prefix=api_prefix)
app.include_router(content_routes.public_router, prefix=api_prefix)
app.include_router(content_routes.contact_router, prefix=api_prefix)
app.include_router(audit_log_routes.router, prefix=api_prefix)
app.include_router(wallet_routes.router, prefix=api_prefix)
app.include_router(payment_routes.router, prefix=api_prefix)
app.include_router(upload_routes.router, prefix=api_prefix)
app.include_router(vehicle_type_routes.router, prefix=api_prefix)
app.include_router(pricing_routes.router, prefix=api_prefix)
app.include_router(booking_policy_routes.router, prefix=api_prefix)
app.include_router(homepage_config_routes.router, prefix=api_prefix)
app.include_router(settings_history_routes.router, prefix=api_prefix)
app.include_router(ws_routes.router, prefix=api_prefix)
app.include_router(purchase_confirmation_routes.router, prefix=api_prefix)
app.include_router(whatsapp_webhook_routes.router, prefix=api_prefix)
app.include_router(whatsapp_crm_routes.router, prefix=api_prefix)
app.include_router(zone_routes.router, prefix=api_prefix)
app.include_router(coverage_lead_routes.router, prefix=api_prefix)
