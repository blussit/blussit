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


# Driver defaults are a 100-connection pool per process, a 30 s hang when
# the cluster is unreachable, and NO socket timeout — a stuck read would
# hold its request (or the reminder loop) forever. Per Cloud Run instance:
# 30 connections is plenty for its request concurrency and keeps N
# instances well under Atlas's connection cap; 5 s to find a server fails a
# request fast instead of letting it pile up; 60 s per socket read is far
# past any legitimate admin aggregation.
_CLIENT_DEFAULTS = {
    "maxPoolSize": 30,
    "serverSelectionTimeoutMS": 5000,
    "connectTimeoutMS": 10000,
    "socketTimeoutMS": 60000,
}


def _client_options(uri: str) -> dict:
    """The defaults above, minus any option MONGO_URI already sets
    (`...?maxPoolSize=50`) — the URI stays the override, no code change."""
    query = uri.split("?", 1)[1] if "?" in uri else ""
    given = {part.split("=", 1)[0].strip().lower() for part in query.split("&") if part}
    return {name: value for name, value in _CLIENT_DEFAULTS.items() if name.lower() not in given}


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
    mongodb.client = AsyncIOMotorClient(settings.MONGO_URI, **_client_options(settings.MONGO_URI))
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


class _IndexBuildCollection:
    """One collection as create_indexes sees it: create_index is never
    fatal (see _IndexBuildDb); everything else passes straight through."""

    def __init__(self, collection):
        self.raw = collection

    def __getattr__(self, name):
        return getattr(self.raw, name)

    async def create_index(self, keys, **kwargs):
        try:
            return await self.raw.create_index(keys, **kwargs)
        except Exception as exc:  # degrade, don't die — the next boot retries it
            logger.warning("Index %s on %s skipped: %s", kwargs.get("name") or keys, self.raw.name, exc)
            return None


class _IndexBuildDb:
    """create_indexes' view of the database. It builds ~100 indexes in one
    sequence, and a single failure (legacy duplicates under a new unique
    index, an equivalent index someone added by hand under another name)
    used to abort the whole function — skipping every index after it. Each
    build is now logged and skipped on its own instead."""

    def __init__(self, db):
        self._db = db

    def __getattr__(self, name):
        return _IndexBuildCollection(getattr(self._db, name))

    def __getitem__(self, name):
        return _IndexBuildCollection(self._db[name])


ACTIVE_SLOT_INDEX = "uniq_active_customer_slot_v3"
VISIT_SEAT_INDEX = "uniq_visit_seat_v1"
_ACTIVE_BOOKING_STATUSES = ("awaiting_payment", "pending", "assigned", "captain_on_the_way", "service_started", "rescheduled")


def mongodb_raw(db):
    """The real database behind create_indexes' degrade-don't-die wrapper."""
    return getattr(db, "_db", db)


async def _ensure_replacing_index(collection, keys, *, name: str, replaces: tuple[str, ...], **options) -> bool:
    """Build `name`, THEN drop the indexes it replaces — never the other way
    round. If the build fails (legacy rows violate a unique index), the old
    indexes stay so the collection keeps whatever guard it had, and the
    failure is logged as an ERROR (missing_critical_indexes reports it to
    the readiness probe). An older server that refuses two indexes on one
    key pattern gets the old ones dropped first, but only after a dry run
    shows the new unique index will build."""
    from pymongo.errors import OperationFailure

    try:
        await collection.create_index(keys, name=name, **options)
    except OperationFailure as exc:
        if exc.code in (85, 86) and options.get("unique"):  # IndexOptionsConflict / IndexKeySpecsConflict
            if await _would_violate(collection, keys, options.get("partialFilterExpression") or {}):
                logger.error("Index %s on %s NOT built: existing rows violate it — old index kept (%s)", name, collection.name, exc)
                return False
            for stale in replaces:
                try:
                    await collection.drop_index(stale)
                except Exception:  # noqa: BLE001 — absent already
                    pass
            try:
                await collection.create_index(keys, name=name, **options)
            except Exception as retry_exc:  # noqa: BLE001
                logger.error("Index %s on %s NOT built: %s", name, collection.name, retry_exc)
                return False
        else:
            logger.error("Index %s on %s NOT built — old index kept: %s", name, collection.name, exc)
            return False
    except Exception as exc:  # noqa: BLE001 — degrade, don't die
        logger.error("Index %s on %s NOT built — old index kept: %s", name, collection.name, exc)
        return False
    for stale in replaces:
        try:
            await collection.drop_index(stale)
        except Exception:  # noqa: BLE001 — absent already
            pass
    return True


async def _would_violate(collection, keys, partial: dict) -> bool:
    """Does any group of rows share a key the unique index would refuse?"""
    group = {k: f"${k}" for k, _ in keys}
    rows = await collection.aggregate([
        {"$match": partial},
        {"$group": {"_id": group, "n": {"$sum": 1}}},
        {"$match": {"n": {"$gt": 1}}},
        {"$limit": 1},
    ]).to_list(length=1)
    return bool(rows)


# The unique / safety indexes the business rules lean on — duplicate
# bookings, double settlement of one Razorpay order/link/mandate, two
# accounts on one phone/email, a re-delivered WhatsApp message processed
# twice, two counters for one slot/day, two holds by one holder, a visit
# holding a seat twice. Checked by key pattern + uniqueness (not by name:
# an equivalent index built by hand still counts) — and, for the active-slot
# guard, by its partial filter: a legacy v2 on the same keys guards less.
_ACTIVE_SLOT_FILTER = {"status": {"$in": list(_ACTIVE_BOOKING_STATUSES)}, "is_deleted": False}
_CRITICAL_INDEXES: tuple[tuple[str, tuple[str, ...], str], ...] = (
    ("bookings", ("customer_id", "visit_line_key", "scheduled_date", "scheduled_slot"), ACTIVE_SLOT_INDEX),
    ("bookings", ("booking_number",), "booking_number unique"),
    ("bookings", ("booking_group_id", "seat_key.date", "seat_key.slot_key"), VISIT_SEAT_INDEX),
    ("payment_orders", ("razorpay_order_id",), "uniq_rzp_order"),
    ("payment_orders", ("razorpay_link_id",), "uniq_rzp_link"),
    ("payment_orders", ("razorpay_subscription_id",), "uniq_rzp_mandate"),
    ("users", ("phone",), "users.phone unique"),
    ("users", ("email",), "users.email unique"),
    ("whatsapp_message_dedup", ("wamid",), "whatsapp_message_dedup.wamid unique"),
    ("slot_capacity", ("service_center_id", "date", "slot_key"), "slot_capacity unique (center, date, slot)"),
    ("daily_capacity", ("service_center_id", "date"), "daily_capacity unique (center, date)"),
    ("slot_holds", ("service_center_id", "date", "slot_key", "holder_id"), "slot_holds unique (center, date, slot, holder)"),
    ("capacity_policy_changes", ("service_center_id", "effective_date"), "capacity_policy_changes unique (center, date)"),
    ("customer_charges", ("visit_key", "kind"), "uniq_charge_per_visit"),
    # Customer wallet (MoneyService): one wallet per customer, one ledger
    # row per idempotency key — a retried/raced post never moves money twice.
    ("customer_wallets", ("customer_id",), "uniq_customer_wallet"),
    ("customer_wallet_ledger", ("key",), "uniq_wallet_entry_key"),
    # Manager paybacks (MONEY-2): one record per idempotency key — a
    # double submit / retried request never pays back twice.
    ("customer_paybacks", ("key",), "uniq_customer_payback_key"),
)


def _same_filter(actual, expected: dict) -> bool:
    """Partial filters compared as plain data (the server hands back SON)."""
    import json

    def plain(value):
        return json.loads(json.dumps(value, sort_keys=True, default=str))

    return actual is not None and plain(dict(actual)) == plain(expected)


async def missing_critical_indexes(db: AsyncIOMotorDatabase | None = None) -> list[str]:
    """The critical unique indexes that do NOT exist right now, as
    "collection: label" strings — empty when all are in place. For the
    readiness probe (audit DEP-08: index builds only log a warning on
    failure, so a guard could be silently absent). `db` defaults to the
    app's database. Never raises for a missing collection (no indexes ->
    reported missing)."""
    db = db if db is not None else get_database()
    missing: list[str] = []
    cache: dict[str, list[dict]] = {}
    for collection, keys, label in _CRITICAL_INDEXES:
        if collection not in cache:
            try:
                cache[collection] = await db[collection].list_indexes().to_list(length=None)
            except Exception:  # noqa: BLE001 — collection not created yet
                cache[collection] = []
        found = any(
            index.get("unique") and tuple(index.get("key", {}).keys()) == keys
            and (label != ACTIVE_SLOT_INDEX or _same_filter(index.get("partialFilterExpression"), _ACTIVE_SLOT_FILTER))
            for index in cache[collection]
        )
        if not found:
            missing.append(f"{collection}: {label}")
    return missing


async def create_indexes() -> None:
    """
    Create all required indexes at startup. Idempotent — safe to run
    every boot. Keeping this centralized avoids missing indexes as the
    schema grows.
    """
    db = _IndexBuildDb(mongodb.db)

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
    # v3 stopped counting recycle-bin rows; Mongo won't re-spec a
    # partialFilterExpression in place. v3 is built FIRST and the old
    # indexes are dropped only once it exists (audit DEP-08: dropping first
    # left the collection with NO duplicate-booking guard whenever legacy
    # duplicates made the v3 build fail).
    # Keyed on visit_line_key (see BookingModel) rather than vehicle_id:
    # bookings no longer need a vehicle record, and two SUVs on one visit
    # must not collide with each other while a double-submitted single
    # booking still must.
    await _ensure_replacing_index(
        mongodb_raw(db).bookings,
        [("customer_id", 1), ("visit_line_key", 1), ("scheduled_date", 1), ("scheduled_slot", 1)],
        name=ACTIVE_SLOT_INDEX,
        unique=True,
        partialFilterExpression=_ACTIVE_SLOT_FILTER,
        replaces=("customer_id_1_vehicle_id_1_scheduled_date_1_scheduled_slot_1", "uniq_active_customer_slot", "uniq_active_customer_slot_v2"),
    )
    # Explicit seat ownership (BookingService._take_seat/_release_seat): the
    # reconciliation recount and the daily-cap init count seats owned per
    # (center, day, slot) — partial, only owners are indexed...
    await db.bookings.create_index(
        [("seat_key.service_center_id", 1), ("seat_key.date", 1), ("seat_key.slot_key", 1)],
        name="seat_owners_v1", partialFilterExpression={"holds_seat": True},
    )
    # ...and the database itself refuses a visit holding the same seat twice.
    await db.bookings.create_index(
        [("booking_group_id", 1), ("seat_key.date", 1), ("seat_key.slot_key", 1)],
        name=VISIT_SEAT_INDEX, unique=True,
        partialFilterExpression={"holds_seat": True, "booking_group_id": {"$type": "string"}},
    )
    # BookingService._claim_booking_lock: short-lived insert-first claims
    # (e.g. one manager "log a done job" per customer+minute). The claimant
    # deletes its own; this only sweeps up after a crashed request.
    await db.booking_locks.create_index("expires_at", expireAfterSeconds=0)

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

    # Recycle bin (admin soft-delete/restore) + the 30-day auto-purge sweep
    # in main.py's _reminder_loop, which runs this exact query every 60s,
    # forever, for as long as the app is up. Partial: almost no booking is
    # ever soft-deleted, so indexing only the deleted ones keeps this tiny
    # instead of scanning the whole collection on every tick.
    await db.bookings.create_index(
        "deleted_at", name="recycle_bin_deleted_at",
        partialFilterExpression={"is_deleted": True},
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
    # "What did anyone (incl. an admin working a center's queue) do in THIS
    # center" — the audit page's center filter (AuditService attribution).
    await db.audit_logs.create_index([("service_center_id", 1), ("created_at", -1)], sparse=True)
    # Refresh-token rotation ledger (AuthService._spend_refresh_token): a
    # spent jti only matters until the token itself would have expired.
    await db.spent_refresh_tokens.create_index("expires_at", expireAfterSeconds=0)
    await db.refresh_token_families.create_index("expires_at", expireAfterSeconds=0)

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
    # Single-use MSG91 widget tokens (AuthService; _id = the token's sha256)
    # — remembered only until the token itself would have expired.
    await db.used_widget_tokens.create_index("expires_at", expireAfterSeconds=0)
    # Road-distance cache for the customer distance charge (route_service).
    await db.road_distance_cache.create_index("expires_at", expireAfterSeconds=0)

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
        # Mongo refuses the option change, so drop-and-recreate once. (On
        # the raw collection: this one needs to SEE the refusal.)
        from pymongo.errors import OperationFailure

        collection = getattr(collection, "raw", collection)
        try:
            await collection.create_index(field, expireAfterSeconds=seconds)
        except OperationFailure:
            try:
                await collection.drop_index(f"{field}_1")
                await collection.create_index(field, expireAfterSeconds=seconds)
            except Exception as exc:  # degrade, don't die
                logger.warning("TTL index on %s.%s skipped: %s", collection.name, field, exc)
        except Exception as exc:  # degrade, don't die
            logger.warning("TTL index on %s.%s skipped: %s", collection.name, field, exc)

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
    # Auto-pay renewal sweep (PaymentService.sync_autopay_renewals): paid
    # autopay mandates whose next_check_at is due — grows with subscribers
    # and runs every minute.
    await db.payment_orders.create_index([("kind", 1), ("status", 1), ("next_check_at", 1)])
    # Plan-revenue reporting: admin dashboard's plan-revenue tile and the
    # manager Sales section (both KPI figure and drill-down list) all match
    # on purpose+status+created_at, which no other index above starts with —
    # without this every dashboard load was a full collection scan.
    await db.payment_orders.create_index([("purpose", 1), ("status", 1), ("created_at", -1)])
    # PaymentService: one open checkout per customer + target (open_key),
    # and the refund queue newest-refunded first.
    await db.payment_orders.create_index([("open_key", 1), ("customer_id", 1), ("status", 1)])
    await db.payment_orders.create_index(
        [("refund_status", 1), ("refunded_at", -1)], partialFilterExpression={"refund_status": {"$exists": True}},
    )
    # Recycle-bin delete/permanent-delete money-attached checks — an $or
    # across these two fields, each served by its own sparse index (only
    # group-payment orders carry booking_ids; only single-booking orders
    # carry booking_id).
    await db.payment_orders.create_index("booking_id", sparse=True)
    await db.payment_orders.create_index("booking_ids", sparse=True)
    # PaymentService._flag_stale_settlements, every sweep pass: the rare
    # claim whose settlement never finished (only those docs carry it).
    await db.payment_orders.create_index("settling", sparse=True)
    # Razorpay webhook dedupe (keyed on the event id) — 30 days is far past
    # Razorpay's retry window.
    await _ensure_ttl(db.razorpay_webhook_events, "received_at", 30 * 24 * 3600)
    # Auto-pay: one Razorpay plan per (our plan, vehicle tier, price) — the
    # unique key is what keeps _ensure_razorpay_plan from minting a new
    # gateway plan on every purchase.
    await db.razorpay_plans.create_index(
        [("plan_id", 1), ("vehicle_type", 1), ("amount_paise", 1)], unique=True, name="uniq_autopay_plan"
    )
    await db.withdrawal_requests.create_index("captain_id")
    await db.withdrawal_requests.create_index("status")
    # One PENDING withdrawal per captain, enforced by Mongo: the service's
    # count-then-insert let parallel requests each see zero and all insert.
    try:
        await db.withdrawal_requests.create_index(
            "captain_id", unique=True, name="uniq_pending_withdrawal_per_captain",
            partialFilterExpression={"status": "pending"},
        )
    except Exception as exc:  # duplicate legacy data — degrade, don't die
        logger.warning("Unique index build skipped: %s", exc)
    # Who uploaded which identity document (upload_routes authorizes
    # downloads on it), and the per-account daily upload counter.
    await db.uploaded_documents.create_index("key")
    await db.uploaded_documents.create_index([("url", 1), ("owner_id", 1)])
    await db.upload_counters.create_index("expires_at", expireAfterSeconds=0)
    # Photo upload records (app.core.storage, audit CAP-02) — a captain's
    # uploads newest first. No TTL: it is the audit trail of job photos.
    await db.uploaded_photos.create_index([("uploader_id", 1), ("created_at", -1)])
    # PERF-01: cross-instance websocket events live a few minutes only.
    from app.core.ws_manager import WS_EVENT_TTL_SECONDS

    await db.ws_events.create_index("at", expireAfterSeconds=WS_EVENT_TTL_SECONDS)

    # Customer account charges (late cancellation — CustomerChargeService):
    # "this customer's open charges" (quote + the next booking's claim),
    # the center's / admin's charge list newest first, a carrying booking's
    # charges (release on cancel/delete), and ONE charge per cancelled visit.
    await db.customer_charges.create_index([("customer_id", 1), ("status", 1)])
    await db.customer_charges.create_index([("service_center_id", 1), ("status", 1), ("created_at", -1)])
    await db.customer_charges.create_index([("created_at", -1)])
    await db.customer_charges.create_index("applied_to_booking_id", sparse=True)
    await db.customer_charges.create_index([("visit_key", 1), ("kind", 1)], unique=True, name="uniq_charge_per_visit")

    # Customer wallet (CustomerWalletService / MoneyService): one balance
    # per customer; an immutable ledger, idempotent on `key`; a customer's
    # ledger newest first; the admin payouts list; a booking's entries.
    await db.customer_wallets.create_index("customer_id", unique=True, name="uniq_customer_wallet")
    await db.customer_wallet_ledger.create_index("key", unique=True, name="uniq_wallet_entry_key")
    await db.customer_wallet_ledger.create_index([("customer_id", 1), ("created_at", -1)])
    await db.customer_wallet_ledger.create_index([("kind", 1), ("created_at", -1)])
    await db.customer_wallet_ledger.create_index("booking_id", sparse=True)
    # Manager paybacks (MONEY-2, CustomerWalletService.payback): unique
    # idempotency key; the admin list (newest first, by center); a
    # booking's / customer's paybacks; the collections line per center.
    await db.customer_paybacks.create_index("key", unique=True, name="uniq_customer_payback_key")
    await db.customer_paybacks.create_index([("created_at", -1)])
    await db.customer_paybacks.create_index([("service_center_id", 1), ("created_at", -1)])
    await db.customer_paybacks.create_index("booking_id")
    await db.customer_paybacks.create_index([("customer_id", 1), ("created_at", -1)])
    # NOTIFY: once-per-event notification claims expire after 60 days; the
    # WhatsApp queue's delivery-failure view (failed rows, newest first).
    await db.notification_claims.create_index("created_at", expireAfterSeconds=60 * 24 * 3600)
    await db.whatsapp_queue.create_index([("failure", 1), ("updated_at", -1)])
    # Captain-wallet delta postings carry a key (cw:{booking}:{rev}) — the
    # same delta can never be posted twice.
    await db.wallet_transactions.create_index(
        "key", unique=True, name="uniq_wallet_txn_key", partialFilterExpression={"key": {"$type": "string"}},
    )

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
    # Website visitor KPI — the unique pair is what makes
    # SiteVisitService.record's upsert count a device once per IST day, and
    # its `date` prefix serves the stats range queries (no scan).
    await db.site_visits.create_index([("date", 1), ("device_id", 1)], unique=True)

    # Scale pack — each one names the query it serves. Built one by one and
    # never fatal: an equivalent index someone added by hand under another
    # name makes create_index raise, and that must not stop the rest.
    scale_pack = (
        # KpiService._bookings_between: an $or whose FIRST branch is
        # created_at alone (no status/center prefix) — without this the
        # whole $or collection-scans on every dashboard load. Also the
        # unfiltered admin booking list's default sort.
        (db.bookings, [("created_at", -1)], {}),
        # ...and its SECOND branch (status=completed + closed_at range), the
        # same shape as the "completed in period" drill-down in
        # booking_routes._apply_period_filters.
        (db.bookings, [("status", 1), ("closed_at", -1)], {}),
        # Every _reminder_loop finder (status + a scheduled_date window) —
        # runs every 60 s, forever.
        (db.bookings, [("status", 1), ("scheduled_date", 1)], {}),
        # Manager "today" counts (AnalyticsService.manager_summary) and the
        # per-center collections ledger (PaymentService.center_collections).
        (db.bookings, [("service_center_id", 1), ("status", 1), ("scheduled_date", 1)], {}),
        # Pass lifecycle sweeps: status=active + an end_date window.
        (db.user_subscriptions, [("status", 1), ("end_date", 1)], {}),
        # Template usage counts (WhatsAppCrmService.list_local_templates)
        # and its analytics' "templates sent since" query.
        (db.whatsapp_outbox, [("template_name", 1), ("created_at", -1)], {}),
        # Admin's unread-conversations badge, polled on every console page —
        # only conversations with unread messages are ever in it.
        (db.whatsapp_conversations, [("unread_count", 1)], {"name": "unread_conversations", "partialFilterExpression": {"unread_count": {"$gt": 0}}}),
        # Admin user lists (UserService: role filter, newest first).
        (db.users, [("role", 1), ("created_at", -1)], {}),
        # KpiService.customers(): the all-time per-customer history $group
        # reads ONLY these fields, so this index makes it index-only
        # (explain: PROJECTION_COVERED, 0 documents examined) instead of a
        # full scan of every booking document.
        (db.bookings, [("is_deleted", 1), ("customer_id", 1), ("created_at", 1), ("status", 1), ("total_amount", 1)], {"name": "kpi_customer_history"}),
        # CRM thread (outbox by phone, newest first) and the analytics'
        # first-reply probe (outbox by phone, oldest after a time).
        (db.whatsapp_outbox, [("phone", 1), ("created_at", -1)], {}),
        # CRM search by number (anchored prefix / exact variants).
        (db.whatsapp_conversations, [("phone", 1)], {}),
        # KPI rating windows and center review lists / summaries.
        (db.reviews, [("created_at", -1)], {}),
        (db.reviews, [("service_center_id", 1), ("created_at", -1)], {}),
        # Complaint lists (admin newest-first, manager per center) and the
        # KPI operations window.
        (db.complaints, [("created_at", -1)], {}),
        (db.complaints, [("service_center_id", 1), ("created_at", -1)], {}),
        # Subscription reports: admin list newest-first, and the manager
        # overview's "sold by this center" branch.
        (db.user_subscriptions, [("created_at", -1)], {}),
        (db.user_subscriptions, [("service_center_id", 1), ("created_at", -1)], {}),
        # KPI marketing: coverage leads captured in the period.
        (db.coverage_leads, [("created_at", -1)], {}),
        # Center-scoped "completed in period" branch of KpiService's window
        # $or (manager Sales KPIs) and the center revenue drill-down: without
        # it the branch scanned every completed booking of the center.
        (db.bookings, [("service_center_id", 1), ("status", 1), ("closed_at", -1)], {}),
        # Manager Subscribers page: distinct customers a center has served
        # (UserSubscriptionService.center_overview) — a covered DISTINCT_SCAN
        # instead of fetching every booking the center ever had.
        (db.bookings, [("service_center_id", 1), ("customer_id", 1), ("is_deleted", 1)], {}),
        # Manager queue's "late starts" scope (center_queue_filters:
        # captain_start_stage in late/severely_late), default newest-first —
        # otherwise it walked every booking the center ever had.
        (db.bookings, [("service_center_id", 1), ("captain_start_stage", 1), ("created_at", -1)], {}),
        # Manager/admin queue pages (BookingRepository.list_queue): one per
        # _QUEUE_SORTS spec, each ENDING in the same keys as the sort — the
        # _id tiebreak included, or Mongo fetched and sorted the center's
        # whole history in memory to return 20 rows. scheduled_desc walks
        # the scheduled one backwards; the late-starts scope sorts
        # scheduled_desc too.
        (db.bookings, [("service_center_id", 1), ("created_at", -1), ("_id", -1)], {}),
        (db.bookings, [("service_center_id", 1), ("scheduled_date", 1), ("scheduled_slot", 1), ("_id", 1)], {}),
        (db.bookings, [("service_center_id", 1), ("captain_start_stage", 1), ("scheduled_date", 1), ("scheduled_slot", 1), ("_id", 1)], {}),
        # A captain's live jobs: has_active_job (every 25 s location ping),
        # find_active_for_captain (inside the assign transaction) and his
        # job list (list_for_captain's scheduled sorts) — instead of his
        # whole lifetime history.
        (db.bookings, [("captain_id", 1), ("status", 1), ("scheduled_date", 1), ("scheduled_slot", 1), ("_id", 1)], {}),
        # The reminder-loop finders (BookingService._sweep_scan): status +
        # a date window, streamed earliest slot first.
        (db.bookings, [("status", 1), ("scheduled_date", 1), ("scheduled_slot", 1), ("_id", 1)], {}),
        # Repeat-booking nudge (find_customers_due_repeat_reminder): lapsed
        # customers, longest-lapsed first — reads users, not every booking.
        (db.users, [("role", 1), ("last_completed_at", 1)], {}),
        # "Is a wash already booked on this pass?" — the wash reminder's
        # $lookup (an $expr equality, which a partial index can't serve) and
        # the used-all-washes check.
        (db.bookings, [("subscription_id", 1), ("status", 1)], {}),
        # Customer-facing lists, each sorted in the DB and capped: my passes
        # (newest first), my vehicles / addresses (default first, then
        # newest), my reviews, and the full notification list (no is_read
        # filter, so the (user_id, is_read, created_at) index can't sort it).
        (db.user_subscriptions, [("customer_id", 1), ("created_at", -1)], {}),
        (db.vehicles, [("owner_id", 1), ("is_default", -1), ("created_at", -1)], {}),
        (db.addresses, [("owner_id", 1), ("is_default", -1), ("created_at", -1)], {}),
        (db.reviews, [("customer_id", 1), ("created_at", -1)], {}),
        (db.notifications, [("user_id", 1), ("created_at", -1)], {}),
    )
    for collection, keys, options in scale_pack:
        try:
            await collection.create_index(keys, **options)
        except Exception as exc:  # an equivalent index under another name — degrade, don't die
            logger.warning("Index %s on %s skipped: %s", keys, collection.name, exc)

    # Durable WhatsApp send queue (NotificationService) — also created
    # lazily by its worker; built here so the first sends never wait on it.
    try:
        from app.services.notification_service import ensure_queue_indexes

        await ensure_queue_indexes(mongodb_raw(db))
    except Exception as exc:  # degrade, don't die — same rule as the scale pack
        logger.warning("WhatsApp queue indexes skipped: %s", exc)

    # Society plans (docs/SOCIETY_PLANS.md) keep their own index list.
    from app.repositories.society_repository import ensure_society_indexes

    try:
        await ensure_society_indexes(db)
    except Exception as exc:  # degrade, don't die — same rule as the scale pack
        logger.warning("Society indexes skipped: %s", exc)

