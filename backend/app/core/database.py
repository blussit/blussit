"""
MongoDB connection lifecycle management.
A single AsyncIOMotorClient is created on startup and reused across the
application (connection pooling handled internally by Motor).
"""
import logging
import re

from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase

from app.core.config import settings

logger = logging.getLogger(__name__)

_CREDENTIALS_RE = re.compile(r"//([^:/@]+):([^@/]+)@")


def _redact_uri(uri: str) -> str:
    """Never log a Mongo URI with its password in plain text."""
    return _CREDENTIALS_RE.sub("//\\1:***@", uri)


class MongoDB:
    client: AsyncIOMotorClient | None = None
    db: AsyncIOMotorDatabase | None = None


mongodb = MongoDB()


async def connect_to_mongo() -> None:
    logger.info("Connecting to MongoDB at %s", _redact_uri(settings.MONGO_URI))
    mongodb.client = AsyncIOMotorClient(settings.MONGO_URI)
    mongodb.db = mongodb.client[settings.MONGO_DB_NAME]
    await create_indexes()
    logger.info("MongoDB connection established.")


async def close_mongo_connection() -> None:
    if mongodb.client:
        mongodb.client.close()
        logger.info("MongoDB connection closed.")


def get_database() -> AsyncIOMotorDatabase:
    if mongodb.db is None:
        raise RuntimeError("Database not initialized. Call connect_to_mongo() first.")
    return mongodb.db


async def create_indexes() -> None:
    """
    Create all required indexes at startup. Idempotent — safe to run
    every boot. Keeping this centralized avoids missing indexes as the
    schema grows.
    """
    db = mongodb.db

    await db.users.create_index("email", unique=True, sparse=True)
    await db.users.create_index("phone", unique=True, sparse=True)
    await db.users.create_index("role")
    await db.users.create_index("service_center_id")

    await db.vehicles.create_index("owner_id")
    # For the semi-unique-registration check (allow the same plate on up to
    # 2 accounts) — plain, not unique, since duplicates up to 2 are
    # intentionally allowed. Populated at write time on the normalized form
    # (uppercased, spaces/hyphens stripped) so the count query is an index
    # lookup, not a collection-wide normalize-and-scan.
    await db.vehicles.create_index("registration_number_normalized")
    await db.addresses.create_index("owner_id")

    await db.bookings.create_index("customer_id")
    await db.bookings.create_index("captain_id")
    await db.bookings.create_index("service_center_id")
    await db.bookings.create_index("status")
    await db.bookings.create_index([("scheduled_date", 1), ("scheduled_slot", 1)])
    await db.bookings.create_index("booking_number", unique=True, sparse=True)
    # Every booking-list endpoint (admin/manager/captain/customer) filters by
    # one of these ids then sorts — without the sort key in the index, Mongo
    # falls back to an in-memory sort of the matched set. Fine at today's
    # volume, but as booking counts grow per center/captain/customer this
    # keeps list queries index-served instead of slowing down again.
    await db.bookings.create_index([("service_center_id", 1), ("created_at", -1)])
    await db.bookings.create_index([("customer_id", 1), ("created_at", -1)])
    await db.bookings.create_index([("captain_id", 1), ("scheduled_date", 1)])
    await db.bookings.create_index([("status", 1), ("created_at", -1)])
    # DB-level duplicate-booking guard: a customer can't hold two ACTIVE
    # bookings for the same vehicle in the same slot, no matter how a
    # double-click/multi-tab/retry races the application-level check in
    # BookingService._customer_conflict — MongoDB rejects the second insert
    # outright (DuplicateKeyError), which create_booking translates into a
    # clean BadRequestException. Partial: only active-status bookings are
    # constrained, so a cancelled/completed one never blocks a fresh rebooking.
    await db.bookings.create_index(
        [("customer_id", 1), ("vehicle_id", 1), ("scheduled_date", 1), ("scheduled_slot", 1)],
        unique=True,
        partialFilterExpression={"status": {"$in": ["pending", "assigned", "captain_on_the_way", "service_started", "rescheduled"]}},
    )

    await db.booking_status_history.create_index("booking_id")

    # Slot/daily capacity reservation counters — see
    # app/models/booking.py's SlotCapacityModel/DailyCapacityModel and
    # BookingService._reserve_slot_capacity. The unique index is what makes
    # BaseRepository.get_or_init's concurrent-first-use race safe.
    await db.slot_capacity.create_index([("service_center_id", 1), ("date", 1), ("slot_key", 1)], unique=True)
    await db.daily_capacity.create_index([("service_center_id", 1), ("date", 1)], unique=True)

    # Effective-dated capacity policy changes — see
    # app/models/capacity_policy.py and CapacityPolicyService. Unique so
    # re-scheduling the same future (or today's) date is naturally an
    # edit-in-place, not a duplicate.
    await db.capacity_policy_changes.create_index([("service_center_id", 1), ("effective_date", 1)], unique=True)

    await db.services.create_index("category_id")
    await db.services.create_index("slug", unique=True, sparse=True)
    await db.categories.create_index("slug", unique=True, sparse=True)
    await db.vehicle_types.create_index("slug", unique=True, sparse=True)

    await db.subscription_plans.create_index("slug", unique=True, sparse=True)
    await db.user_subscriptions.create_index("customer_id")
    await db.user_subscriptions.create_index("status")

    await db.service_centers.create_index("code", unique=True, sparse=True)
    await db.service_centers.create_index([("location.pincode", 1)])

    await db.complaints.create_index("customer_id")
    await db.complaints.create_index("service_center_id")
    await db.complaints.create_index("status")

    await db.reviews.create_index("booking_id")
    await db.reviews.create_index("captain_id")

    await db.coupons.create_index("code", unique=True, sparse=True)

    await db.notifications.create_index("user_id")
    await db.notifications.create_index("is_read")

    await db.audit_logs.create_index("actor_id")
    await db.audit_logs.create_index("created_at")

    await db.inventory.create_index("service_center_id")

    await db.settings.create_index("key", unique=True, sparse=True)

    await db.captain_wallets.create_index("captain_id", unique=True, sparse=True)
    await db.wallet_transactions.create_index("captain_id")
    await db.wallet_transactions.create_index("booking_id")
    await db.withdrawal_requests.create_index("captain_id")
    await db.withdrawal_requests.create_index("status")

    await db.service_centers.create_index([("location.latitude", 1), ("location.longitude", 1)])
    await db.addresses.create_index([("latitude", 1), ("longitude", 1)])

    # Public /thank-you page tickets — token must be unique (it's the
    # entire lookup key for an unauthenticated route), and a TTL index
    # lets Mongo garbage-collect expired ones on its own rather than this
    # collection growing forever.
    await db.purchase_confirmations.create_index("token", unique=True)
    await db.purchase_confirmations.create_index("expires_at", expireAfterSeconds=0)

    await db.whatsapp_outbox.create_index("phone")
    await db.whatsapp_outbox.create_index("created_at")
