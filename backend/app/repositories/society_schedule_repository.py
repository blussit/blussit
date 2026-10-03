"""Society premium-wash scheduling — collections (docs/SOCIETY_PLANS.md §9)."""
import logging

from app.repositories.base_repository import BaseRepository

logger = logging.getLogger(__name__)


class ScheduleRuleRepository(BaseRepository):
    """Repeat rules: a society's visit-day rotation, or one resident's own
    repeat premium wash."""

    collection_name = "society_schedule_rules"


class SocietyVisitRepository(BaseRepository):
    """Occurrences: one society visit day, or one resident's repeat wash on
    one date. Generated from rules (idempotent: unique occurrence_key) or
    added by hand; single-occurrence changes are stored right on it."""

    collection_name = "society_visits"


class ScheduleRequestRepository(BaseRepository):
    """A resident asking to skip / move one scheduled premium wash."""

    collection_name = "society_schedule_requests"


async def ensure_society_schedule_indexes(db) -> None:
    """Every index a scheduling query needs (called from ensure_society_indexes)."""
    await db.society_schedule_rules.create_index([("society_id", 1), ("kind", 1), ("is_active", 1)])
    await db.society_schedule_rules.create_index([("is_active", 1), ("materialized_until", 1)])
    await db.society_schedule_rules.create_index("enrollment_id", sparse=True)
    # One occurrence per rule per original date, however often the
    # materializer runs (sweep, planner, rule save) — the idempotency key.
    await db.society_visits.create_index(
        "occurrence_key", unique=True, name="uniq_occurrence_key",
        partialFilterExpression={"occurrence_key": {"$type": "string"}},
    )
    await db.society_visits.create_index([("society_id", 1), ("date", 1)])
    await db.society_visits.create_index([("service_center_id", 1), ("date", 1)])
    # The booking sweep: planned visits coming due, oldest date first.
    await db.society_visits.create_index([("status", 1), ("date", 1)])
    # The captain's "my society visits": planned captains, or the captain a
    # booked day actually gave the cars to.
    await db.society_visits.create_index([("captain_ids", 1), ("date", 1)])
    await db.society_visits.create_index([("allocations.captain_id", 1), ("date", 1)], sparse=True)
    await db.society_visits.create_index([("customer_id", 1), ("date", 1)], sparse=True)
    await db.society_visits.create_index([("rule_id", 1), ("date", 1)], sparse=True)
    await db.society_schedule_requests.create_index([("service_center_id", 1), ("status", 1), ("created_at", -1)])
    await db.society_schedule_requests.create_index([("society_id", 1), ("status", 1)])
    await db.society_schedule_requests.create_index([("customer_id", 1), ("created_at", -1)])
    try:
        await db.society_schedule_requests.create_index(
            [("visit_id", 1), ("customer_id", 1)], unique=True, name="one_pending_request",
            partialFilterExpression={"status": "pending"},
        )
    except Exception as exc:  # pre-existing duplicates: keep booting, log it
        logger.warning("Unique pending schedule-request index skipped: %s", exc)
    await db.bookings.create_index("society_visit_id", sparse=True)
