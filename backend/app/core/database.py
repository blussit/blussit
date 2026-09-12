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


async def connect_to_mongo(build_indexes: bool = True) -> None:
    """`build_indexes=False` lets the app defer the ~74 index round-trips
    (and any genuine first-boot index BUILDS) to a background task so the
    health check answers immediately — a platform health probe killing the
    container mid-index-build was a real deployment failure mode. Tests and
    scripts keep the synchronous default."""
    logger.info("Connecting to MongoDB at %s", _redact_uri(settings.MONGO_URI))
    mongodb.client = AsyncIOMotorClient(settings.MONGO_URI)
    mongodb.db = mongodb.client[settings.MONGO_DB_NAME]
    if build_indexes:
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

    # GPS breadcrumb trail (see CaptainLocationModel) — short-lived by
    # design: rows self-expire after 30 days via the TTL index.
    await db.captain_locations.create_index([("captain_id", 1), ("at", 1)])
    await db.captain_locations.create_index("at", expireAfterSeconds=30 * 24 * 3600)

    # Captain staff ids are unique across the platform (sparse — customers
    # and managers never carry one).
    await db.users.create_index("employee_id", unique=True, sparse=True)
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
    # The partial filter GREW (awaiting_payment joined the active set) and
    # Mongo won't re-spec a partialFilterExpression in place — drop the
    # original auto-named index once, then build the named replacement.
    try:
        await db.bookings.drop_index("customer_id_1_vehicle_id_1_scheduled_date_1_scheduled_slot_1")
    except Exception:
        pass
    await db.bookings.create_index(
        [("customer_id", 1), ("vehicle_id", 1), ("scheduled_date", 1), ("scheduled_slot", 1)],
        unique=True,
        name="uniq_active_customer_slot",
        partialFilterExpression={"status": {"$in": ["awaiting_payment", "pending", "assigned", "captain_on_the_way", "service_started", "rescheduled"]}},
    )

    # Custom-plan enquiries — one row per phone (upserted), newest first.
    await db.plan_enquiries.create_index("phone", unique=True)
    await db.plan_enquiries.create_index([("last_requested_at", -1)])
    # A pass belongs to ONE car and one car carries ONE pass — this is the
    # index behind _active_pass_for_vehicle, the guard that enforces it.
    await db.user_subscriptions.create_index([("customer_id", 1), ("vehicle_id", 1), ("status", 1)])
    await db.user_subscriptions.create_index([("customer_id", 1), ("plan_id", 1), ("status", 1)])

    # Multi-vehicle visits: every read of a group ("show me the other cars
    # on this visit") goes through this.
    await db.bookings.create_index("booking_group_id", sparse=True)

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
    # Race guards: one live review per booking, one attendance row per
    # captain per day. Graceful on legacy duplicates — the index build
    # fails, we log, the service-level guards still apply; clean the dupes
    # and the next boot gets the index.
    for build in (
        lambda: db.reviews.create_index(
            "booking_id", unique=True, name="uniq_live_review_per_booking",
            partialFilterExpression={"is_deleted": {"$eq": False}},
        ),
        lambda: db.attendance.create_index(
            [("captain_id", 1), ("attendance_date", 1)], unique=True, name="uniq_attendance_per_day",
        ),
    ):
        try:
            await build()
        except Exception as exc:  # duplicate legacy data — degrade, don't die
            logger.warning("Unique index build skipped: %s", exc)

    await db.coupons.create_index("code", unique=True, sparse=True)

    await db.notifications.create_index("user_id")
    await db.notifications.create_index("is_read")
    # The bell polls this exact shape every 30s for every logged-in user —
    # the single hottest read in the app.
    await db.notifications.create_index([("user_id", 1), ("is_read", 1), ("created_at", -1)])

    await db.audit_logs.create_index([("actor_id", 1), ("created_at", -1)])
    await db.audit_logs.create_index([("module", 1), ("created_at", -1)])
    await db.audit_logs.create_index("created_at")

    # Hot-path gap pack (these queries ran as full collection scans):
    # first-time-offer fraud checks on EVERY booking price...
    await db.bookings.create_index("vehicle_registration_number", sparse=True)
    await db.bookings.create_index("customer_phone", sparse=True)
    # ...vehicle/address delete guards...
    await db.bookings.create_index("vehicle_id")
    await db.bookings.create_index("address_id")
    # ...pincode dispatch (multikey on the array actually queried)...
    await db.service_centers.create_index("location.service_pincodes")
    # ...coupon per-user usage caps, leave listings, review lookups,
    # complaint→captain KPI joins, wallet statements.
    await db.coupon_usages.create_index([("coupon_id", 1), ("user_id", 1)])
    await db.leave_requests.create_index([("captain_id", 1), ("created_at", -1)])
    await db.leave_requests.create_index("status")
    await db.reviews.create_index("customer_id")
    await db.reviews.create_index([("is_published", 1), ("created_at", -1)])
    await db.complaints.create_index("booking_id")
    await db.user_subscriptions.create_index([("customer_id", 1), ("status", 1)])
    # OTP store: looked up by identifier on every request/verify, and rows
    # self-delete at their own expiry instant (TTL 0 on expires_at).
    await db.otp_requests.create_index("identifier")
    await db.otp_requests.create_index("expires_at", expireAfterSeconds=0)

    await db.inventory.create_index("service_center_id")

    await db.settings.create_index("key", unique=True, sparse=True)

    await db.captain_wallets.create_index("captain_id", unique=True, sparse=True)
    # Slot holds (theater-seat model): one hold per holder per slot; the
    # sweeper scans by expiry. Deliberately NOT a TTL index — held_count
    # must be decremented in the same breath as the delete, which only the
    # sweeper can do.
    await db.slot_holds.create_index(
        [("service_center_id", 1), ("date", 1), ("slot_key", 1), ("holder_id", 1)], unique=True
    )
    await db.slot_holds.create_index("expires_at")
    # Service zones: polygon coverage checks via $geoIntersects.
    await db.service_zones.create_index([("polygon", "2dsphere")])
    await db.service_zones.create_index("service_center_id")
    # M6 (AUDIT.md): bounded growth for ephemeral rows. In-app notifications
    # expire after 90 days, SMS logs after a year. WhatsApp inbox/outbox and
    # audit logs are deliberately NOT expired — chat history is customer
    # context and audit trails are business records.
    async def _ensure_ttl(collection, field: str, seconds: int) -> None:
        # A plain index on the same key may predate the TTL decision —
        # Mongo refuses the option change, so drop-and-recreate once.
        from pymongo.errors import OperationFailure

        try:
            await collection.create_index(field, expireAfterSeconds=seconds)
        except OperationFailure:
            await collection.drop_index(f"{field}_1")
            await collection.create_index(field, expireAfterSeconds=seconds)

    await _ensure_ttl(db.notifications, "created_at", 90 * 24 * 3600)
    await _ensure_ttl(db.sms_outbox, "created_at", 365 * 24 * 3600)
    # WhatsApp traffic logs were the fastest-growing unbounded collections
    # (a row per message, both providers). One year of history is plenty
    # for the CRM inbox and template analytics; older rows age out.
    await _ensure_ttl(db.whatsapp_outbox, "created_at", 365 * 24 * 3600)
    await _ensure_ttl(db.whatsapp_inbox, "created_at", 365 * 24 * 3600)
    await db.wallet_transactions.create_index([("captain_id", 1), ("created_at", -1)])
    await db.wallet_transactions.create_index("captain_id")
    await db.wallet_transactions.create_index("booking_id")

    # Razorpay orders/links — one doc per checkout attempt; the unique ids
    # are what the atomic created->paid claims key on. PARTIAL uniqueness:
    # a modal-checkout doc has no link id and a payment-link doc has no
    # order id, so a plain unique index would collide on the nulls.
    try:
        # Migrate away from the short-lived plain unique index.
        await db.payment_orders.drop_index("razorpay_order_id_1")
    except Exception:
        pass
    await db.payment_orders.create_index(
        "razorpay_order_id", unique=True, name="uniq_rzp_order",
        partialFilterExpression={"razorpay_order_id": {"$exists": True}},
    )
    await db.payment_orders.create_index(
        "razorpay_link_id", unique=True, name="uniq_rzp_link",
        partialFilterExpression={"razorpay_link_id": {"$exists": True}},
    )
    await db.payment_orders.create_index(
        "razorpay_subscription_id", unique=True, name="uniq_rzp_mandate",
        partialFilterExpression={"razorpay_subscription_id": {"$exists": True}},
    )
    await db.payment_orders.create_index([("customer_id", 1), ("created_at", -1)])
    await db.payment_orders.create_index([("kind", 1), ("status", 1), ("created_at", -1)])
    # Auto-pay: one Razorpay plan per (our plan, vehicle tier, price) — the
    # unique key is what keeps _ensure_razorpay_plan from minting a new
    # gateway plan on every purchase.
    await db.razorpay_plans.create_index(
        [("plan_id", 1), ("vehicle_type", 1), ("amount_paise", 1)], unique=True, name="uniq_autopay_plan"
    )
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

    await db.sms_outbox.create_index("phone")

    await db.whatsapp_outbox.create_index("phone")
    await db.whatsapp_outbox.create_index("wamid", sparse=True)
    # created_at index carries the 365d TTL (see _ensure_ttl above) — do
    # not also create a plain one, the name would conflict.

    # WhatsApp booking bot — one conversation doc per sender, and a
    # dedup ledger of processed webhook message ids (Meta redelivers on
    # retry; a retried "Confirm" tap must not double-book). Unique index
    # is what makes the insert-first dedup race-safe; TTL keeps the
    # ledger from growing forever (Meta retries stop after 7 days, keep a
    # comfortable margin over that).
    # Coverage leads (uncovered-area demand capture) — the unique pair
    # is what makes CoverageLeadService.capture's upsert-dedup race-safe.
    await db.coverage_leads.create_index([("phone", 1), ("pincode", 1)], unique=True)
    await db.coverage_leads.create_index("last_requested_at")

    await db.whatsapp_conversations.create_index("wa_id", unique=True)
    await db.whatsapp_message_dedup.create_index("wamid", unique=True)
    await db.whatsapp_message_dedup.create_index("created_at", expireAfterSeconds=14 * 24 * 3600)
    # WhatsApp CRM: inbound message store + template cache
    await db.whatsapp_inbox.create_index([("wa_id", 1), ("created_at", -1)])
    # created_at index carries the 365d TTL (see _ensure_ttl above).
    await db.whatsapp_templates.create_index("name", unique=True)
    await db.whatsapp_conversations.create_index([("last_message_at", -1)])

