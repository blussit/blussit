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
    staff_directory_routes,
    staff_ops_routes,
    subscription_routes,
    upload_routes,
    user_routes,
    vehicle_type_routes,
    wallet_routes,
    whatsapp_webhook_routes,
    ws_routes,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(
    title=settings.APP_NAME,
    description="REST API for India's premium doorstep vehicle care platform.",
    version="1.0.0",
    docs_url="/api/docs",
    redoc_url="/api/redoc",
    openapi_url="/api/openapi.json",
)

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

    while True:
        try:
            db = mongodb.db
            if db is not None:
                booking_service = BookingService(db)
                notifications = NotificationService(db)

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
        except Exception:
            logger.exception("Reminder sweep failed")
        await asyncio.sleep(60)


@app.on_event("startup")
async def on_startup() -> None:
    global _reminder_task
    await connect_to_mongo()
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
app.include_router(upload_routes.router, prefix=api_prefix)
app.include_router(vehicle_type_routes.router, prefix=api_prefix)
app.include_router(pricing_routes.router, prefix=api_prefix)
app.include_router(booking_policy_routes.router, prefix=api_prefix)
app.include_router(homepage_config_routes.router, prefix=api_prefix)
app.include_router(ws_routes.router, prefix=api_prefix)
app.include_router(purchase_confirmation_routes.router, prefix=api_prefix)
app.include_router(whatsapp_webhook_routes.router, prefix=api_prefix)
app.include_router(coverage_lead_routes.router, prefix=api_prefix)
