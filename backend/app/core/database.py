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



async def _safe_create_index(collection, keys, **kwargs):
    import pymongo.errors
    try:
        index_info = await collection.index_information()
    except pymongo.errors.OperationFailure:
        index_info = {}
        
    normalized_keys = [(keys, 1)] if isinstance(keys, str) else keys
    
    for name, info in index_info.items():
        if info.get('key') == normalized_keys:
            # Check options mismatch
            expected_options = kwargs
            existing_options = {k: v for k, v in info.items() if k not in ['v', 'key', 'name', 'ns']}
            
            # Simple check if there's a difference (ignoring missing vs None for simplicity, just warn)
            # Reusing existing as requested
            if expected_options or existing_options:
                logger.debug(f"Index for {keys} exists on {collection.name}. Reusing.")
            return name
            
    try:
        return await collection.create_index(keys, **kwargs)
    except pymongo.errors.OperationFailure as e:
        if e.code == 85: # IndexOptionsConflict
            logger.warning(f"Index options conflict for {keys} on {collection.name}: {e}")
            return None
        raise

async def create_indexes() -> None:

    """
    Create all required indexes at startup. Idempotent — safe to run
    every boot. Keeping this centralized avoids missing indexes as the
    schema grows.
    """
    db = mongodb.db

    await _safe_create_index(db.users, "email", unique=True, sparse=True)
    await _safe_create_index(db.users, "phone", unique=True, sparse=True)
    await _safe_create_index(db.users, "role")
    await _safe_create_index(db.users, "service_center_id")

    await _safe_create_index(db.vehicles, "owner_id")
    # For the semi-unique-registration check (allow the same plate on up to
    # 2 accounts) — plain, not unique, since duplicates up to 2 are
    # intentionally allowed. Populated at write time on the normalized form
    # (uppercased, spaces/hyphens stripped) so the count query is an index
    # lookup, not a collection-wide normalize-and-scan.
    await _safe_create_index(db.vehicles, "registration_number_normalized")
    await _safe_create_index(db.addresses, "owner_id")

    await _safe_create_index(db.bookings, "customer_id")
    await _safe_create_index(db.bookings, "captain_id")
    await _safe_create_index(db.bookings, "service_center_id")
    await _safe_create_index(db.bookings, "status")
    await _safe_create_index(db.bookings, [("scheduled_date", 1), ("scheduled_slot", 1)])
    await _safe_create_index(db.bookings, "booking_number", unique=True, sparse=True)
    # Every booking-list endpoint (admin/manager/captain/customer) filters by
    # one of these ids then sorts — without the sort key in the index, Mongo
    # falls back to an in-memory sort of the matched set. Fine at today's
    # volume, but as booking counts grow per center/captain/customer this
    # keeps list queries index-served instead of slowing down again.
    await _safe_create_index(db.bookings, [("service_center_id", 1), ("created_at", -1)])
    await _safe_create_index(db.bookings, [("customer_id", 1), ("created_at", -1)])
    await _safe_create_index(db.bookings, [("captain_id", 1), ("scheduled_date", 1)])
    await _safe_create_index(db.bookings, [("status", 1), ("created_at", -1)])
    # DB-level duplicate-booking guard: a customer can't hold two ACTIVE
    # bookings for the same vehicle in the same slot, no matter how a
    # double-click/multi-tab/retry races the application-level check in
    # BookingService._customer_conflict — MongoDB rejects the second insert
    # outright (DuplicateKeyError), which create_booking translates into a
    # clean BadRequestException. Partial: only active-status bookings are
    # constrained, so a cancelled/completed one never blocks a fresh rebooking.
    await _safe_create_index(db.bookings, 
        [("customer_id", 1), ("vehicle_id", 1), ("scheduled_date", 1), ("scheduled_slot", 1)],
        unique=True,
        partialFilterExpression={"status": {"$in": ["pending", "assigned", "captain_on_the_way", "service_started", "rescheduled"]}},
    )

    await _safe_create_index(db.booking_status_history, "booking_id")

    # Slot/daily capacity reservation counters — see
    # app/models/booking.py's SlotCapacityModel/DailyCapacityModel and
    # BookingService._reserve_slot_capacity. The unique index is what makes
    # BaseRepository.get_or_init's concurrent-first-use race safe.
    await _safe_create_index(db.slot_capacity, [("service_center_id", 1), ("date", 1), ("slot_key", 1)], unique=True)
    await _safe_create_index(db.daily_capacity, [("service_center_id", 1), ("date", 1)], unique=True)

    # Effective-dated capacity policy changes — see
    # app/models/capacity_policy.py and CapacityPolicyService. Unique so
    # re-scheduling the same future (or today's) date is naturally an
    # edit-in-place, not a duplicate.
    await _safe_create_index(db.capacity_policy_changes, [("service_center_id", 1), ("effective_date", 1)], unique=True)

    await _safe_create_index(db.services, "category_id")
    await _safe_create_index(db.services, "slug", unique=True, sparse=True)
    await _safe_create_index(db.categories, "slug", unique=True, sparse=True)
    await _safe_create_index(db.vehicle_types, "slug", unique=True, sparse=True)

    await _safe_create_index(db.subscription_plans, "slug", unique=True, sparse=True)
    await _safe_create_index(db.user_subscriptions, "customer_id")
    await _safe_create_index(db.user_subscriptions, "status")

    await _safe_create_index(db.service_centers, "code", unique=True, sparse=True)
    await _safe_create_index(db.service_centers, [("location.pincode", 1)])

    await _safe_create_index(db.complaints, "customer_id")
    await _safe_create_index(db.complaints, "service_center_id")
    await _safe_create_index(db.complaints, "status")

    await _safe_create_index(db.reviews, "booking_id")
    await _safe_create_index(db.reviews, "captain_id")

    await _safe_create_index(db.coupons, "code", unique=True, sparse=True)

    await _safe_create_index(db.notifications, "user_id")
    await _safe_create_index(db.notifications, "is_read")

    await _safe_create_index(db.audit_logs, "actor_id")
    await _safe_create_index(db.audit_logs, "created_at")

    await _safe_create_index(db.inventory, "service_center_id")

    await _safe_create_index(db.settings, "key", unique=True, sparse=True)

    await _safe_create_index(db.captain_wallets, "captain_id", unique=True, sparse=True)
    await _safe_create_index(db.wallet_transactions, "captain_id")
    await _safe_create_index(db.wallet_transactions, "booking_id")
    await _safe_create_index(db.withdrawal_requests, "captain_id")
    await _safe_create_index(db.withdrawal_requests, "status")

    await _safe_create_index(db.service_centers, [("location.latitude", 1), ("location.longitude", 1)])
    await _safe_create_index(db.addresses, [("latitude", 1), ("longitude", 1)])

    # Public /thank-you page tickets — token must be unique (it's the
    # entire lookup key for an unauthenticated route), and a TTL index
    # lets Mongo garbage-collect expired ones on its own rather than this
    # collection growing forever.
    await _safe_create_index(db.purchase_confirmations, "token", unique=True)
    await _safe_create_index(db.purchase_confirmations, "expires_at", expireAfterSeconds=0)

    await _safe_create_index(db.sms_outbox, "phone")
    await _safe_create_index(db.sms_outbox, "created_at", expireAfterSeconds=31536000)

    await _safe_create_index(db.whatsapp_outbox, "phone")
    await _safe_create_index(db.whatsapp_outbox, "wamid", sparse=True)
    await _safe_create_index(db.whatsapp_outbox, "created_at")

    # WhatsApp booking bot — one conversation doc per sender, and a
    # dedup ledger of processed webhook message ids (Meta redelivers on
    # retry; a retried "Confirm" tap must not double-book). Unique index
    # is what makes the insert-first dedup race-safe; TTL keeps the
    # ledger from growing forever (Meta retries stop after 7 days, keep a
    # comfortable margin over that).
    # Coverage leads (uncovered-area demand capture) — the unique pair
    # is what makes CoverageLeadService.capture's upsert-dedup race-safe.
    await _safe_create_index(db.coverage_leads, [("phone", 1), ("pincode", 1)], unique=True)
    await _safe_create_index(db.coverage_leads, "last_requested_at")

    await _safe_create_index(db.whatsapp_conversations, "wa_id", unique=True)
    await _safe_create_index(db.whatsapp_message_dedup, "wamid", unique=True)
    await _safe_create_index(db.whatsapp_message_dedup, "created_at", expireAfterSeconds=14 * 24 * 3600)
