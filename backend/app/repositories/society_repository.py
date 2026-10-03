import logging
from app.repositories.base_repository import BaseRepository

logger = logging.getLogger(__name__)


class SocietyRepository(BaseRepository):
    collection_name = "societies"


class SocietyEnrollmentRepository(BaseRepository):
    collection_name = "society_enrollments"


class SocietyAttendanceRepository(BaseRepository):
    collection_name = "society_attendance"


class SocietyPaymentRepository(BaseRepository):
    collection_name = "society_payments"


async def ensure_society_indexes(db) -> None:
    """Every index a society query needs (called from create_indexes)."""
    await db.societies.create_index("form_token", unique=True, sparse=True)
    await db.societies.create_index([("service_center_id", 1), ("created_at", -1)])
    await db.societies.create_index("daily_captain_id", sparse=True)
    await db.societies.create_index("substitute.captain_id", sparse=True)
    # The missed-attendance sweep: active societies not yet alerted today.
    await db.societies.create_index([("is_active", 1), ("attendance_alert_date", 1)])
    await db.society_enrollments.create_index([("society_id", 1), ("created_at", -1)])
    await db.society_enrollments.create_index([("customer_id", 1), ("society_id", 1)])
    await db.society_enrollments.create_index([("service_center_id", 1), ("status", 1)])
    # At most one open request per resident per society (SocietyService.enroll
    # merges a resubmit into it) — enforced here so a double-tapped submit
    # can't race two in. One partial index per open status: equality filters
    # work on every MongoDB version, unlike $in.
    for status in ("requested", "awaiting_payment"):
        try:
            await db.society_enrollments.create_index(
                [("society_id", 1), ("customer_id", 1)],
                unique=True,
                name=f"one_open_{status}",
                partialFilterExpression={"status": status, "is_deleted": False},
            )
        except Exception as exc:  # pre-existing duplicates: keep booting, log it
            logger.warning("Unique open-%s enrollment index skipped: %s", status, exc)
    await db.society_attendance.create_index([("society_id", 1), ("date", 1)], unique=True)
    await db.society_attendance.create_index([("captain_id", 1), ("date", -1)])
    await db.society_payments.create_index([("society_id", 1), ("created_at", -1)])
    await db.society_payments.create_index([("service_center_id", 1), ("created_at", -1)])
    await db.society_payments.create_index([("created_at", -1)])
    await db.user_subscriptions.create_index([("society_id", 1), ("status", 1)])
    await db.user_subscriptions.create_index("enrollment_id", sparse=True)
    await db.subscription_plans.create_index([("plan_type", 1), ("is_active", 1)])
    # Premium-wash scheduling (society_schedule_repository).
    from app.repositories.society_schedule_repository import ensure_society_schedule_indexes

    await ensure_society_schedule_indexes(db)
    # Society issues are complaints tagged with the society (open count on
    # the society pages, the "Society issues" filter).
    await db.complaints.create_index([("society_id", 1), ("status", 1)], sparse=True)
    # Society requests from the landing page (one center's list; the 24h
    # same phone + society dedupe).
    await db.society_leads.create_index([("service_center_id", 1), ("status", 1), ("created_at", -1)])
    await db.society_leads.create_index([("phone", 1), ("name_key", 1), ("created_at", -1)])
