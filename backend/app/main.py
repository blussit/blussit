import asyncio
import logging

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
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


@app.middleware("http")
async def security_headers(request, call_next):
    """User-uploaded files are served from our own origin — nosniff stops a
    browser from ever executing a mislabeled upload as HTML/script, and the
    magic-byte check in storage.py stops one being stored in the first
    place. Belt and braces."""
    response = await call_next(request)
    if request.url.path.startswith("/uploads/"):
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Content-Security-Policy"] = "default-src 'none'"
    return response

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Serves whatever app.core.storage's "local" provider writes (captain
# before/after photos) back out as plain static files — the directory must
# exist before StaticFiles will mount, hence the mkdir here.
(upload_root() / "photos").mkdir(parents=True, exist_ok=True)
app.mount("/uploads", StaticFiles(directory=str(upload_root())), name="uploads")


_reminder_task: asyncio.Task | None = None


async def _reminder_loop() -> None:
    """
    Lightweight in-process reminder sweep — no Redis/Celery required (those
    are explicitly Phase 2). Every 60 seconds:
      1. Pings captains for bookings starting within 30 minutes.
      2. Reminds the manager, every 15 minutes, that a booking still has no
         captain — independent of how far off its scheduled slot is, so a
         booking scheduled for later today doesn't just sit ignored.
      3. Flags + notifies BOTH the captain and the manager, right at the
         scheduled start time (then every 5 minutes for as long as it stays
         true), when an assigned booking's captain hasn't even started
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
    """
    from app.services.booking_service import UNASSIGNED_REMINDER_MINUTES, BookingService, format_slot_start_12h
    from app.services.notification_service import NotificationService

    # H2 (AUDIT.md): a Mongo lease makes the sweep single-flight across
    # however many processes run this app. Each iteration tries to claim
    # (or renew) the lease; only the holder sweeps, everyone else idles.
    # A crashed holder's lease simply expires 90s later.
    import uuid as _uuid
    from datetime import datetime as _dt, timedelta as _td, timezone as _tz

    holder = _uuid.uuid4().hex

    async def _holds_lease(db) -> bool:
        now = _dt.now(_tz.utc)
        result = await db.locks.find_one_and_update(
            {"_id": "reminder_loop", "$or": [{"holder": holder}, {"expires_at": {"$lt": now}}]},
            {"$set": {"holder": holder, "expires_at": now + _td(seconds=90)}},
        )
        if result is not None:
            return True
        # No claimable doc — either someone else holds a live lease, or
        # the doc doesn't exist yet. Try to create it; losing the race is fine.
        try:
            await db.locks.insert_one({"_id": "reminder_loop", "holder": holder, "expires_at": now + _td(seconds=90)})
            return True
        except Exception:  # noqa: BLE001 — duplicate key = someone else holds it
            return False

    while True:
        try:
            db = mongodb.db
            if not await _holds_lease(db):
                await asyncio.sleep(30)
                continue
            if db is not None:
                booking_service = BookingService(db)
                notifications = NotificationService(db)
                # Release expired slot holds (theater-seat model) so
                # abandoned checkouts free their seats within a minute.
                await booking_service._sweep_holds()

                due = await booking_service.find_bookings_needing_reminder()
                for booking in due:
                    if booking.get("captain_id"):
                        await notifications.notify(
                            booking["captain_id"],
                            "Get ready — booking starting soon",
                            f"Booking {booking['booking_number']} is scheduled to start at {format_slot_start_12h(booking['scheduled_slot'])}.",
                            NotificationType.BOOKING,
                            str(booking["_id"]),
                        )
                    await booking_service.mark_reminder_sent(str(booking["_id"]))

                unassigned_too_long = await booking_service.find_bookings_unassigned_too_long()
                for booking in unassigned_too_long:
                    await booking_service.notify_center_manager_for_booking(
                        booking,
                        f"Still needs a captain — booking {booking['booking_number']}",
                        f"Booking {booking['booking_number']} has been waiting for a captain for over "
                        f"{UNASSIGNED_REMINDER_MINUTES} minutes. Please assign one.",
                    )
                    await booking_service.mark_unassigned_reminder_sent(str(booking["_id"]))

                late_to_start = await booking_service.find_bookings_late_to_start()
                for booking in late_to_start:
                    await booking_service.flag_late_to_start(booking)
                    await booking_service.mark_late_start_reminder_sent(str(booking["_id"]))

                not_reached = await booking_service.find_bookings_captain_not_reached()
                for booking in not_reached:
                    await booking_service.flag_issue(
                        str(booking["_id"]),
                        "captain_not_reached",
                        f"Booking {booking['booking_number']} never got moving — its scheduled window has fully "
                        f"expired with no captain heading out. Reschedule it to a new time, then assign a captain, "
                        f"or cancel it.",
                    )

                stuck = await booking_service.find_bookings_stuck_on_the_way()
                for booking in stuck:
                    await booking_service.flag_issue(
                        str(booking["_id"]),
                        "captain_delay",
                        f"Captain has been 'on the way' for booking {booking['booking_number']} for an unusually "
                        f"long time without starting the service — worth checking in.",
                    )

                overrunning = await booking_service.find_bookings_service_overrunning()
                for booking in overrunning:
                    await booking_service.flag_issue(
                        str(booking["_id"]),
                        "service_overrun",
                        f"Booking {booking['booking_number']} has been in progress well past its planned duration "
                        f"— worth checking in with the captain.",
                    )

                # Anti-moonlighting step-gap sweeps: reached-but-not-started,
                # and walked-off-mid-service (see the finders' docstrings).
                idle_after_arrival = await booking_service.find_bookings_idle_after_arrival()
                for booking in idle_after_arrival:
                    await booking_service.flag_issue(
                        str(booking["_id"]),
                        "idle_after_arrival",
                        f"Captain reached the customer for booking {booking['booking_number']} but hasn't started "
                        f"the service well past the allowed gap — please check what's holding it up.",
                    )

                left_site = await booking_service.find_bookings_captain_left_site()
                for booking in left_site:
                    away_m = booking.get("_distance_from_site_m")
                    await booking_service.flag_issue(
                        str(booking["_id"]),
                        "left_site_during_service",
                        f"Captain's live location is {away_m}m away from booking {booking['booking_number']}'s "
                        f"address while the service is supposedly in progress — please check in.",
                    )

                # Razorpay payment links (WhatsApp bookings): ask Razorpay
                # which pending links got paid and settle them — the
                # reliable half of the two-path design (the browser
                # callback being the other), and the ONLY path in dev
                # where the callback URL isn't publicly reachable. The
                # customer gets their ✅ right in the WhatsApp chat.
                from bson import ObjectId

                from app.services.payment_service import PaymentService
                from app.services.whatsapp_service import WhatsAppService

                wa = WhatsAppService(db)

                async def _confirm_link_paid(order: dict) -> None:
                    customer = await db.users.find_one({"_id": ObjectId(order["customer_id"])}) if ObjectId.is_valid(order["customer_id"]) else None
                    if customer and customer.get("phone"):
                        await wa.send_text(
                            customer["phone"],
                            f"✅ Payment received — booking *{order.get('booking_number')}* is fully paid. Thank you!",
                        )

                await PaymentService(db).sync_pending_links(_confirm_link_paid)

                # Subscriptions past end_date become properly EXPIRED — the
                # stored status used to flip only lazily on read/consume, so
                # untouched subs sat "active" in the DB forever (and
                # auto_renew has no payment gateway to act on, so expiry is
                # the only correct transition). One Mongo-side update_many.
                await db.user_subscriptions.update_many(
                    {"status": "active", "end_date": {"$lt": _dt.now(_tz.utc)}},
                    {"$set": {"status": "expired", "updated_at": _dt.now(_tz.utc)}},
                )
        except Exception:
            logger.exception("Reminder sweep failed")
        await asyncio.sleep(60)


@app.on_event("startup")
async def on_startup() -> None:
    global _reminder_task
    # Production misconfiguration must fail LOUDLY at boot, not quietly at
    # exploit time: with the default secret anyone can forge an admin JWT.
    if not settings.DEBUG:
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

    asyncio.create_task(_deferred_init())
    _reminder_task = asyncio.create_task(_reminder_loop())
    logger.info("%s started in %s mode", settings.APP_NAME, settings.APP_ENV)


@app.on_event("shutdown")
async def on_shutdown() -> None:
    if _reminder_task:
        _reminder_task.cancel()
    await close_mongo_connection()


@app.exception_handler(AppException)
async def app_exception_handler(request: Request, exc: AppException) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content={"success": False, "error_code": exc.error_code, "message": exc.message, "details": exc.details},
    )


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    return JSONResponse(
        status_code=422,
        content={
            "success": False,
            "error_code": "VALIDATION_ERROR",
            "message": "Request validation failed",
            "details": {"errors": exc.errors()},
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
app.include_router(ws_routes.router, prefix=api_prefix)
app.include_router(purchase_confirmation_routes.router, prefix=api_prefix)
app.include_router(whatsapp_webhook_routes.router, prefix=api_prefix)
app.include_router(whatsapp_crm_routes.router, prefix=api_prefix)
app.include_router(zone_routes.router, prefix=api_prefix)
app.include_router(coverage_lead_routes.router, prefix=api_prefix)
