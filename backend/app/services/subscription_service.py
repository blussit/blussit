import asyncio
import secrets
from datetime import date, datetime, timedelta, timezone
from typing import NamedTuple

from bson import ObjectId

from motor.motor_asyncio import AsyncIOMotorDatabase
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

from app.core.exceptions import BadRequestException, ConflictException, ForbiddenException, NotFoundException
from app.models.enums import BillingCycle, SubscriptionStatus
from app.repositories.catalog_repository import ServiceRepository
from app.repositories.subscription_repository import SubscriptionPlanRepository, UserSubscriptionRepository
from app.repositories.user_repository import UserRepository
from app.repositories.vehicle_repository import VehicleRepository
from app.repositories.vehicle_type_repository import VehicleTypeRepository
from app.schemas.subscription_schema import (
    AssignSubscriptionRequest,
    SubscriptionPlanCreateRequest,
    SubscriptionPlanUpdateRequest,
    SubscribeRequest,
)
from app.utils.serializers import serialize_doc, serialize_list
from app.utils.text import slugify
from app.utils.timezone import IST, day_label, from_stored, now_ist, to_ist

# A customer's own pass list: newest first, never unbounded.
CUSTOMER_LIST_LIMIT = 50

_CYCLE_DAYS = {
    BillingCycle.MONTHLY.value: 30,
    BillingCycle.QUARTERLY.value: 90,
    BillingCycle.YEARLY.value: 365,
}


def resolve_plan_price(plan: dict, vehicle_type: str | None) -> float:
    """What this plan actually costs for a given vehicle type. Per-type
    overrides win when set (a per-type discounted price beating the per-type
    base price); only when NO per-type price exists for this type does the
    flat discounted_price/price apply. Mirrors the shape of
    BookingService._resolve_price but without the first-time dimension —
    plan purchases have no first-time offer."""
    if vehicle_type:
        type_discounted = plan.get("vehicle_type_discounted_prices") or {}
        type_prices = plan.get("vehicle_type_prices") or {}
        if vehicle_type in type_discounted:
            return type_discounted[vehicle_type]
        if vehicle_type in type_prices:
            return type_prices[vehicle_type]
    discounted = plan.get("discounted_price")
    return discounted if discounted is not None else plan.get("price", 0.0)


def service_price_for_type(service: dict, vehicle_type: str | None) -> float:
    """What ONE wash of this service costs for this vehicle type, at the
    STANDARD price. Deliberately never `discounted_price` /
    `vehicle_type_discounted_prices` — those are the first-visit offer, and
    pricing a whole month of washes off a one-time introductory rate would
    undercharge every pass sold."""
    if vehicle_type:
        per_type = service.get("vehicle_type_prices") or {}
        if vehicle_type in per_type:
            return float(per_type[vehicle_type])
    return float(service.get("price") or 0.0)


def pass_price_override(plan: dict, service: dict, vehicle_type: str | None) -> float | None:
    """The flat monthly price admin set for this (wash type, vehicle type),
    if they set one. This is the whole price of the pass — not a per-wash
    rate — so the team can sell at a round figure instead of whatever the
    formula produces."""
    if not vehicle_type:
        return None
    by_service = (plan.get("service_pass_prices") or {}).get(str(service.get("_id") or service.get("id") or ""))
    if not by_service:
        return None
    price = by_service.get(vehicle_type)
    return float(price) if price not in (None, "") else None


def resolve_pass_price(plan: dict, service: dict, vehicle_type: str | None) -> float:
    """What a monthly pass costs. An admin-set price for this exact (wash
    type, vehicle type) wins outright; otherwise it's what those washes
    would cost one by one, less the plan's discount — one wash of the CHOSEN
    service at the CHOSEN car's type price, times the monthly visits. Whole
    rupees either way: nobody wants a pass priced ₹1147.20."""
    override = pass_price_override(plan, service, vehicle_type)
    if override is not None:
        return float(round(override))
    per_wash = service_price_for_type(service, vehicle_type)
    visits = int(plan.get("total_service_count") or 1)
    discount = float(plan.get("plan_discount_percent") or 0.0)
    return float(round(per_wash * visits * (100.0 - discount) / 100.0))


def tier_allows(plan: dict, purchased_type: str | None, candidate_type: str) -> bool:
    """The tier rule: a subscription bought for one vehicle type can be
    redeemed on that type or any type this plan prices CHEAPER (an XUV-tier
    card works for a sedan or hatchback, never the other way around). "Lower"
    is defined by the plan's own per-type price — the same admin-set numbers
    the buyer chose between at purchase — so there's no separate hierarchy
    to maintain. No purchased tier recorded (legacy subs) = no tier cap."""
    if not purchased_type or candidate_type == purchased_type:
        return True
    return resolve_plan_price(plan, candidate_type) <= resolve_plan_price(plan, purchased_type)


def pass_tier_allows(plan: dict, service: dict | None, pass_type: str, candidate_type: str) -> bool:
    """tier_allows for a per-service PASS: "cheaper" is judged by what this
    pass would cost on each type (the price the buyer actually paid against),
    not the plan's legacy per-type prices — the admin editor no longer sets
    those, so every type compared equal and a hatchback pass washed an XUV."""
    if candidate_type == pass_type:
        return True
    if not service or not candidate_type:
        return False
    return resolve_pass_price(plan, service, candidate_type) <= resolve_pass_price(plan, service, pass_type)


def is_custom_pass(sub: dict | None) -> bool:
    """A car-bound pass from a custom multi-car plan (per-service quotas)."""
    return bool(sub) and sub.get("plan_kind") == CUSTOM_PLAN_TYPE and sub.get("total_by_service") is not None


def renews_automatically(sub: dict) -> bool:
    """A pass Razorpay will charge again at the end of its cycle: auto-pay
    on AND a live mandate behind it. Such a pass stays ACTIVE at 0 washes
    until the next charge refills it (see commit_consumption) — it is still
    the customer's pass, and the mandate is still billing for it."""
    return bool(sub.get("auto_renew") and sub.get("razorpay_subscription_id"))


# Auto-pay passes get this long past end_date before the ended-pass sweep
# expires them, so a charge that lands a little late never produces a
# "your pass has ended" note. Longer while Razorpay is retrying a failed
# charge (mandate status "pending"); a halted mandate turns auto_renew off,
# after which the normal rule applies.
AUTOPAY_GRACE = timedelta(days=2)
AUTOPAY_RETRY_GRACE = timedelta(days=7)
# The "N washes left — book now" nudge: at most once a week per pass, never
# within this long of a purchase / renewal / booked wash.
WASH_REMINDER_EVERY = timedelta(days=7)
WASH_REMINDER_QUIET_AFTER_USE = timedelta(days=3)


def in_autopay_grace(sub: dict, now: datetime) -> bool:
    """An auto-renewing pass past its end_date while its next charge is
    still expected — AUTOPAY_GRACE, or AUTOPAY_RETRY_GRACE while Razorpay
    retries (same windows the ended-pass sweep waits out)."""
    end = sub.get("end_date")
    if not end or not renews_automatically(sub):
        return False
    grace = AUTOPAY_RETRY_GRACE if sub.get("autopay_state") == "pending" else AUTOPAY_GRACE
    return timedelta(0) <= now - end.replace(tzinfo=timezone.utc) < grace


class PricedVehicleChanged(BadRequestException):
    """The car a pass was PRICED for (frozen on the order at checkout) is
    not the type it is now — the customer retyped it between paying and
    activation (PAY-01). Nothing is created; the payment paths catch it like
    any other activation refusal and park the money for a human."""

    def __init__(self, message: str = "This car's type changed after the pass was priced — it can't be activated at that price.", details: dict | None = None):
        super().__init__(message, details)


# -- Pass validity window (founder rule 2026-10-07) -------------------------
# A pass runs on its own 30-day period: its washes cover bookings SCHEDULED
# inside that period only — the booking's IST calendar day must be before
# the day the pass ends. After that the remaining washes are frozen. A pass
# a manager (its own center) or an admin extended
# (UserSubscriptionService.extend_pass, at most PASS_EXTENSION_MAX_DAYS per
# 30-day period) stays bookable — its remaining washes only — until
# `extended_until`. Society, custom and monthly passes alike (2026-10-07).
PASS_EXTENSION_MAX_DAYS = 10
# Kept for older imports: the cap was society-only before 2026-10-07.
SOCIETY_EXTENSION_MAX_DAYS = PASS_EXTENSION_MAX_DAYS
# Extensions open this long before a period ends (and stay open after it).
PASS_EXTENSION_WINDOW = timedelta(days=3)


def _as_utc(dt: datetime | None) -> datetime | None:
    """A stored computed instant (naive = UTC, as Motor hands it back)."""
    if dt is None:
        return None
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)


def pass_usable_until(sub: dict) -> datetime | None:
    """The instant this pass stops covering bookings: its end_date, or its
    granted extended_until when that is later. None = open-ended."""
    end = _as_utc(sub.get("end_date"))
    if end is None:
        return None
    extended = _as_utc(sub.get("extended_until"))
    return extended if extended is not None and extended > end else end


def usable_until_label(sub: dict) -> str | None:
    """"19 Oct 2026" — the last day a booking can use this pass (its period,
    or its extension when later): the same day every pass screen shows as
    "Until" and `last_bookable_day` carries. None = open-ended."""
    until = pass_usable_until(sub)
    return day_label(from_stored(until).date() - timedelta(days=1)) if until else None


# The founder's wording for that day (2026-10-07): every message and view
# shows a clear DATE — "Last Booking Day: 19 Oct 2026".
LAST_BOOKING_DAY = "Last Booking Day"


def last_booking_day_text(sub: dict) -> str | None:
    """"Last Booking Day: 19 Oct 2026" for this pass (extension included)."""
    label = usable_until_label(sub)
    return f"{LAST_BOOKING_DAY}: {label}" if label else None


def extension_message(days: int, view: dict) -> str:
    """The extend success line (founder wording 2026-10-07): "Extended by 4
    days — Last Booking Day: 19 Oct 2026" — the human date of the view's
    `last_bookable_day` (the last day a wash can be booked, extension
    included)."""
    head = f"Extended by {days} day{'s' if days != 1 else ''}"
    last = view.get("last_bookable_day")
    if not last:
        return head
    return f"{head} — {LAST_BOOKING_DAY}: {day_label(str(last))}"


def next_period_start(sub: dict) -> datetime | None:
    """When a pass's renewal period starts: 00:00 IST on the day after its
    Last Booking Day (the IST date of pass_usable_until) — booking days
    never overlap and never leave a gap. None = open-ended."""
    until = pass_usable_until(sub)
    if until is None:
        return None
    day = from_stored(until).date()
    return datetime(day.year, day.month, day.day, tzinfo=IST)


def in_extension(sub: dict, now: datetime | None = None) -> bool:
    """Past its plan-month end but inside a granted extension window."""
    now = now or now_ist()
    end = _as_utc(sub.get("end_date"))
    until = pass_usable_until(sub)
    return end is not None and until is not None and end <= now < until


def booking_day(scheduled_date) -> date | None:
    """A booking's IST calendar day from what the booking paths carry: a
    date, a naive IST wall-clock datetime, an aware datetime, or
    'YYYY-MM-DD…'."""
    if scheduled_date is None:
        return None
    if isinstance(scheduled_date, datetime):
        return to_ist(scheduled_date).date()
    if isinstance(scheduled_date, date):
        return scheduled_date
    try:
        return datetime.strptime(str(scheduled_date)[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def pass_covers_date(sub: dict, day: date | None) -> bool:
    """True when a booking on `day` falls inside this pass's own period
    (or its society extension)."""
    until = pass_usable_until(sub)
    return day is None or until is None or day < from_stored(until).date()


def ensure_pass_covers_date(sub: dict, scheduled_date) -> None:
    day = booking_day(scheduled_date)
    if pass_covers_date(sub, day):
        return
    ends = from_stored(pass_usable_until(sub))
    last = ends.date() - timedelta(days=1)
    raise BadRequestException(
        f"This pass covers washes booked up to {day_label(last)} — its period ends on {day_label(ends, year=False)}. "
        "Pick an earlier day, or book this one as a normal paid wash."
    )


async def ensure_subscription_covers_date(db, subscription_id: str | None, scheduled_date) -> None:
    """The booking hooks' entry point: refuses a pass booking dated on/after
    the end of the pass's period (PASS-3). Unknown ids are left to the
    booking's own pass lookup (which 404s them)."""
    if not subscription_id or not ObjectId.is_valid(str(subscription_id)):
        return
    sub = await db.user_subscriptions.find_one(
        {"_id": ObjectId(str(subscription_id))}, {"end_date": 1, "extended_until": 1, "society_id": 1}
    )
    if sub:
        ensure_pass_covers_date(sub, scheduled_date)


def pass_blocks_new(sub: dict, now: datetime | None = None) -> bool:
    """Whether this pass still counts as the live one for its car / vehicle
    type + service (the one-live-pass rule): active and inside its period
    (or a society extension), or past it while auto-pay's renewal is due.
    A SCHEDULED pass (a custom-plan renewal waiting for the old period to
    end) holds its car too, from the moment it exists to its own end."""
    now = now or now_ist()
    if sub.get("is_deleted") or sub.get("status") not in LIVE_PASS_STATUSES:
        return False
    until = pass_usable_until(sub)
    return until is None or until > now or in_autopay_grace(sub, now)


# -- Scheduled passes (custom-plan renewal, PLANS-2 2026-10-07) -------------
# A renewal paid while the car's old pass is still live is created with
# status "scheduled", starting the day after the old pass's Last Booking Day
# (next_period_start). The car's one-live-pass claim moves to it at once, so
# the car stays held through both periods; the old pass keeps its washes
# until its own end and can no longer be extended. The scheduled pass is
# never bookable before its start; it is promoted to "active" by the
# ended-pass sweep (promote_scheduled_passes, called from
# find_subscriptions_ended) or lazily the first time it is used
# (_get_active_subscription) — both guarded and idempotent; views read it as
# active from its start either way.
PASS_SCHEDULED = SubscriptionStatus.SCHEDULED.value
LIVE_PASS_STATUSES = (SubscriptionStatus.ACTIVE.value, PASS_SCHEDULED)


async def promote_scheduled_passes(db) -> int:
    """Scheduled passes whose start has come become active. Cheap (partial
    index on start_date where status == scheduled, built by
    ensure_custom_plan_indexes) and idempotent."""
    now = datetime.now(timezone.utc)
    result = await db.user_subscriptions.update_many(
        {"status": PASS_SCHEDULED, "start_date": {"$lte": now}, "is_deleted": {"$ne": True}},
        {"$set": {"status": SubscriptionStatus.ACTIVE.value, "promoted_at": now, "updated_at": now}},
    )
    return result.modified_count


def pass_started(sub: dict, now: datetime | None = None) -> bool:
    start = _as_utc(sub.get("start_date"))
    return start is None or start <= (now or now_ist())


# -- One live pass per identity: an insert-first claim (PASS-1/4, SOC-5) ----
# Check-then-insert let simultaneous sales/checkouts/activations each see "no
# pass yet" and each create one. Every pass creation first INSERTS a claim
# keyed by the live-pass identity (`_id` — unique without any extra index);
# only the inserter creates the pass. A claim whose pass has since ended,
# been used up or cancelled is stale and is taken over atomically (guarded on
# the holder's token); a claim with no pass yet is a creation in flight
# (refused) — or, past PASS_CLAIM_STALE, a creator that died (taken over).
PASS_CLAIMS = "pass_claims"
PASS_CLAIM_STALE = timedelta(minutes=2)


def pass_claim_keys(
    customer_id: str, *, plan_id: str | None = None, vehicle_id: str | None = None, vehicle_type: str | None = None,
    service_id: str | None = None, society_plate: str | None = None,
) -> list[str]:
    """The identities one pass occupies: the named car (one car carries one
    pass), else the vehicle TYPE + service (2026-09 pass model), else the
    plan (pre-pass subscriptions). A society pass also holds its normalized
    plate across accounts (one plate, one society pass)."""
    if vehicle_id:
        keys = [f"vehicle:{customer_id}:{vehicle_id}"]
    elif vehicle_type and service_id:
        keys = [f"type:{customer_id}:{vehicle_type}:{service_id}"]
    else:
        keys = [f"plan:{customer_id}:{plan_id}"]
    if society_plate:
        keys.append(f"society-plate:{society_plate}")
    return keys


class PassClaimConflict(BadRequestException):
    """Another live (or in-flight) pass already holds this identity."""

    def __init__(self, message: str = "This vehicle already has an active pass.", details: dict | None = None):
        super().__init__(message, details)


async def _claim_holder_live(db, held: dict, now: datetime) -> bool:
    sub_id = held.get("subscription_id")
    if not sub_id:
        created = _as_utc(held.get("created_at"))
        return created is not None and created > now - PASS_CLAIM_STALE
    sub = await db.user_subscriptions.find_one({"_id": ObjectId(sub_id)}) if ObjectId.is_valid(str(sub_id)) else None
    return bool(sub) and pass_blocks_new(sub, now)


class PassClaim(NamedTuple):
    """What claim_pass took: the identities and the token proving it."""

    token: str
    keys: list[str]


async def claim_pass(db, keys: list[str], customer_id: str | None = None) -> PassClaim:
    """Claims every key for a pass about to be created (bind_pass_claim /
    release_pass_claim take the result). All or nothing: a key held by a
    live pass releases the ones already taken and raises PassClaimConflict.
    Every read/write is by `_id` — no other index needed."""
    token = secrets.token_hex(12)
    taken: list[str] = []
    try:
        for key in keys:
            await _claim_one(db, key, token, customer_id)
            taken.append(key)
    except BaseException:
        if taken:
            await db[PASS_CLAIMS].delete_many({"_id": {"$in": taken}, "token": token})
        raise
    return PassClaim(token, list(keys))


async def _claim_one(db, key: str, token: str, customer_id: str | None) -> None:
    claims = db[PASS_CLAIMS]
    for _ in range(4):
        now = datetime.now(timezone.utc)
        fresh = {"token": token, "subscription_id": None, "customer_id": customer_id, "created_at": now}
        try:
            await claims.insert_one({"_id": key, **fresh})
            return
        except DuplicateKeyError:
            pass
        held = await claims.find_one({"_id": key})
        if held is None:
            continue  # released meanwhile — insert again
        if await _claim_holder_live(db, held, now):
            raise PassClaimConflict()
        # Stale: take it over, guarded on the token we judged — a racer that
        # took it over first makes this a no-op and we judge again.
        if await claims.find_one_and_update({"_id": key, "token": held.get("token")}, {"$set": fresh}):
            return
    raise PassClaimConflict()


async def bind_pass_claim(db, claim: PassClaim, subscription_id: str) -> None:
    await db[PASS_CLAIMS].update_many({"_id": {"$in": claim.keys}, "token": claim.token}, {"$set": {"subscription_id": subscription_id}})


async def release_pass_claim(db, claim: PassClaim) -> None:
    await db[PASS_CLAIMS].delete_many({"_id": {"$in": claim.keys}, "token": claim.token})


async def release_pass_claims_for(db, subs: list[tuple[dict, list[str]]]) -> None:
    """Passes cancelled: their identities are free at once — only claims
    still bound to that very pass (a lazily-judged stale claim would be
    taken over anyway; this just keeps it tidy). `subs`: (pass, its keys)."""
    for sub, keys in subs:
        if keys:
            await db[PASS_CLAIMS].delete_many({"_id": {"$in": keys}, "subscription_id": str(sub["_id"])})


def pass_claim_keys_of(sub: dict, society_plate: str | None = None) -> list[str]:
    """The identities an existing pass holds (what _create_subscription /
    society activation claimed for it)."""
    has_service = bool(sub.get("service_id"))
    return pass_claim_keys(
        sub.get("customer_id"), plan_id=sub.get("plan_id"), vehicle_id=sub.get("vehicle_id"),
        vehicle_type=sub.get("vehicle_type") if has_service else None, service_id=sub.get("service_id") if has_service else None,
        society_plate=society_plate,
    )


def can_extend(sub: dict, now: datetime | None = None) -> bool:
    """Whether a manager/admin could extend this pass right now (the same
    rules extend_pass enforces, for the staff screens' button): active or
    ended (never cancelled), not on auto-pay, washes left, inside the last
    PASS_EXTENSION_WINDOW of its period or after it, days left this period,
    and not so long ended that even the full cap is already over."""
    now = now or now_ist()
    end = _as_utc(sub.get("end_date"))
    used = int(sub.get("extension_days") or 0)
    return bool(
        sub.get("status") in (SubscriptionStatus.ACTIVE.value, SubscriptionStatus.EXPIRED.value)
        and not sub.get("is_deleted") and not renews_automatically(sub)
        # A renewal is lined up right after it: no stretching over it.
        and not sub.get("renewed_by_custom_plan_id")
        and int(sub.get("remaining_service_count") or 0) > 0
        and end is not None and end - now <= PASS_EXTENSION_WINDOW
        and used < PASS_EXTENSION_MAX_DAYS
        and end + timedelta(days=PASS_EXTENSION_MAX_DAYS) > now
    )


def _extension_fields(sub: dict, doc: dict) -> dict:
    """Extension fields on a serialized pass (any kind since 2026-10-07), as
    aware ISO strings (extended_until is a computed instant the generic
    serializer doesn't know about)."""
    extended = _as_utc(sub.get("extended_until"))
    used = int(sub.get("extension_days") or 0)
    doc["extension_days"] = used
    doc["extension_days_left"] = max(0, PASS_EXTENSION_MAX_DAYS - used)
    doc["can_extend"] = can_extend(sub)
    doc["extended_until"] = from_stored(extended).isoformat() if extended else None
    until = pass_usable_until(sub)
    doc["usable_until"] = from_stored(until).isoformat() if until else None
    # The last day a booking may be dated on ("Book remaining washes until").
    doc["last_bookable_day"] = (from_stored(until).date() - timedelta(days=1)).isoformat() if until else None
    # Its human form — shown as "Last Booking Day: 19 Oct 2026".
    doc["last_booking_day_label"] = usable_until_label(sub)
    if sub.get("extensions"):
        doc["extensions"] = [extension_view(e) for e in sub["extensions"]]
    return doc


def extension_view(entry: dict) -> dict:
    """One extension history row (who, role, days, when, note, for which
    plan-month end), instants as aware ISO strings."""

    def instant(value):
        return from_stored(value).isoformat() if isinstance(value, datetime) else value

    return {
        "days": entry.get("days"),
        "by": entry.get("by"),
        "by_name": entry.get("by_name"),
        "role": entry.get("role"),
        "note": entry.get("note"),
        "at": instant(entry.get("at")),
        "period_end": instant(entry.get("period_end")),
        "extended_until": instant(entry.get("extended_until")),
    }


def _with_effective_status(sub: dict) -> dict:
    """Serializes a subscription with an `effective_status` that reflects
    expiry immediately (end_date has passed => "expired"), even though the
    stored `status` field only ever gets lazily flipped at actual
    consumption time (_get_active_subscription). Every list/browse view
    should read `effective_status`, not `status`, or a customer sees a
    stale "Active" badge on a plan that's actually unusable.

    A society pass inside a granted extension reads "active" (its remaining
    premium washes are still bookable) with `in_extension` set."""
    doc = _extension_fields(sub, serialize_doc(sub))
    status = doc.get("status")
    end_date = sub.get("end_date")
    if status == PASS_SCHEDULED:
        start = _as_utc(sub.get("start_date"))
        if start is not None and start > now_ist():
            # A renewal waiting for the old period to end: "Starts 18 Oct 2026".
            doc["effective_status"] = PASS_SCHEDULED
            doc["starts_on"] = from_stored(start).date().isoformat()
            doc["starts_on_label"] = day_label(from_stored(start))
            return doc
        # Its start has come (the promotion just hasn't been stored yet).
        status = SubscriptionStatus.ACTIVE.value
    if status == SubscriptionStatus.ACTIVE.value and end_date:
        # end_date is a computed aware-UTC-semantic instant (see
        # _get_active_subscription's comment for why .replace(tzinfo=utc)
        # is the correct treatment here, not to_ist()).
        ended_at = end_date.replace(tzinfo=timezone.utc)
        if ended_at < now_ist():
            if in_extension(sub):
                doc["effective_status"] = status
                doc["in_extension"] = True
                return doc
            doc["effective_status"] = SubscriptionStatus.EXPIRED.value
            # Auto-pay's next charge is due/retrying (the ended sweep waits
            # for it): unusable right now, but the app says "Renewing".
            if in_autopay_grace(sub, now_ist()):
                doc["renewal_pending"] = True
            return doc
    doc["effective_status"] = status
    return doc


def _with_effective_statuses(subs: list[dict]) -> list[dict]:
    return [_with_effective_status(s) for s in subs]


def _car_columns(view: dict) -> dict:
    """The car a pass is bound to and (custom pass) its per-service quotas,
    on a staff report row (already-serialized view + _with_pass_details)."""
    return {
        "vehicle_id": view.get("vehicle_id"),
        "registration_number": view.get("registration_number"),
        "plan_kind": view.get("plan_kind"),
        "custom_plan_id": view.get("custom_plan_id"),
        "total_by_service": view.get("total_by_service"),
        "remaining_by_service": view.get("remaining_by_service"),
        "services": view.get("services"),
    }


def _extension_columns(view: dict) -> dict:
    """A society pass's extension on a staff report row (already-serialized
    view): days granted this plan month, until when, and the history."""
    return {
        "extension_days": view.get("extension_days") or 0,
        "extension_days_left": view.get("extension_days_left"),
        "can_extend": bool(view.get("can_extend")),
        "extended_until": view.get("extended_until"),
        "extensions": view.get("extensions") or [],
        "in_extension": bool(view.get("in_extension")),
        # The same pass-view fields the customer 360 shows (_extension_fields
        # → pass_usable_until): when it stops being usable, the last day a
        # wash may be booked on, and the society it belongs to (None for a
        # regular / custom pass).
        "usable_until": view.get("usable_until"),
        "last_bookable_day": view.get("last_bookable_day"),
        "society_id": view.get("society_id"),
    }


# Society plans (docs/SOCIETY_PLANS.md) live in subscription_plans with
# plan_type "society". They're sold ONLY through a society enrollment —
# never listed on the website and never bought through any general path.
SOCIETY_PLAN_TYPE = "society"
# Custom multi-car plans (CustomPlanService, docs/FEATURE_PLAN_WALLET_EDITS_
# PLANS_2026-10-07.md §1.6) hang their car-bound passes off ONE hidden
# template plan with this type — never listed, sold, upgraded to or
# cancelled through the general paths, exactly like society plans.
CUSTOM_PLAN_TYPE = "custom"
HIDDEN_PLAN_TYPES = (SOCIETY_PLAN_TYPE, CUSTOM_PLAN_TYPE)
_NOT_SOCIETY = {"plan_type": {"$nin": list(HIDDEN_PLAN_TYPES)}}
# payment_orders purposes that are plan money (KPIs, plan-sales lists): a
# custom cart is ONE order for all its cars, so it counts once.
PLAN_ORDER_PURPOSES = ["subscription", "custom_plan"]


async def custom_plan_refunds(
    db, s: datetime, e: datetime, service_center_id: str | None = None, *, by_day_tz: str | None = None,
) -> dict:
    """Plan money given back (PLANS-2): custom-plan cars refunded to the
    customer wallet in [s, e), dated by the refund (cars[].refund.at — the
    same transaction that wrote the wallet ledger row, kind "refund",
    meta.custom_plan_id). Plan revenue figures stay GROSS (what was paid
    for plans in the window); these are shown beside them as
    `plan_refunds`, with `plan_revenue_net` = gross − refunds.
    Returns {"amount": rupees, "count": cars refunded, "by_day": {iso day:
    rupees}} (by_day only when `by_day_tz` is given). `service_center_id`
    scopes to carts sold by that center."""
    window = {"$gte": s, "$lt": e}
    match: dict = {"cars.refund.at": window, "is_deleted": {"$ne": True}}
    if service_center_id:
        match["service_center_id"] = service_center_id
    day = (
        {"$dateToString": {"format": "%Y-%m-%d", "date": "$cars.refund.at", "timezone": by_day_tz}}
        if by_day_tz else None
    )
    rows = await db.custom_plans.aggregate([
        {"$match": match},
        {"$unwind": "$cars"},
        {"$match": {"cars.status": "refunded", "cars.refund.at": window}},
        {"$group": {"_id": day, "amount": {"$sum": {"$ifNull": ["$cars.refund.amount", 0]}}, "n": {"$sum": 1}}},
    ]).to_list(length=None)
    out = {
        "amount": round(sum(float(r["amount"] or 0) for r in rows), 2),
        "count": sum(int(r["n"] or 0) for r in rows),
    }
    if by_day_tz:
        out["by_day"] = {r["_id"]: round(float(r["amount"] or 0), 2) for r in rows if r["_id"]}
    return out


async def custom_plan_refunded_by_cart(db, cart_ids) -> dict[str, float]:
    """Everything refunded so far on each of these carts (any date) — the
    per-row refund on a plan-purchase list."""
    oids = [ObjectId(c) for c in {c for c in cart_ids if isinstance(c, str)} if ObjectId.is_valid(c)]
    if not oids:
        return {}
    rows = await db.custom_plans.aggregate([
        {"$match": {"_id": {"$in": oids}, "cars.status": "refunded"}},
        {"$unwind": "$cars"},
        {"$match": {"cars.status": "refunded"}},
        {"$group": {"_id": "$_id", "amount": {"$sum": {"$ifNull": ["$cars.refund.amount", 0]}}}},
    ]).to_list(length=len(oids))
    return {str(r["_id"]): round(float(r["amount"] or 0), 2) for r in rows}


def ensure_public_plan(plan: dict | None) -> None:
    if plan and plan.get("plan_type") in HIDDEN_PLAN_TYPES:
        raise NotFoundException("Subscription plan not found or inactive")


class SubscriptionPlanService:
    def __init__(self, db: AsyncIOMotorDatabase):
        self.repo = SubscriptionPlanRepository(db)

    async def list_all(self, active_only: bool = False) -> list[dict]:
        filters = {"is_active": True, **_NOT_SOCIETY} if active_only else dict(_NOT_SOCIETY)
        items = await self.repo.find_all_no_paginate(filters, sort_by="display_order", sort_order=1)
        return serialize_list(items)

    async def get(self, plan_id: str) -> dict:
        plan = await self.repo.find_by_id(plan_id)
        if not plan or plan.get("plan_type") in HIDDEN_PLAN_TYPES:
            raise NotFoundException("Subscription plan not found")
        return serialize_doc(plan)

    @staticmethod
    def _ensure_monthly(cycle) -> None:
        """Founder rule: BLUSSIT sells monthly passes only. Quarterly/yearly
        stay in the enum so subscriptions sold under them keep renewing and
        reporting correctly, but no new plan can be created or switched to
        one — a fleet customer who wants something else goes through the
        custom-plan enquiry instead."""
        value = cycle.value if hasattr(cycle, "value") else cycle
        if value and value != BillingCycle.MONTHLY.value:
            raise BadRequestException("Only monthly passes are sold. For anything else, use a custom plan enquiry.")

    async def create(self, payload: SubscriptionPlanCreateRequest) -> dict:
        self._ensure_monthly(payload.billing_cycle)
        doc = payload.model_dump()
        doc["billing_cycle"] = payload.billing_cycle.value
        doc["slug"] = slugify(payload.name)
        if payload.category_quotas:
            doc["total_service_count"] = sum(payload.category_quotas.values())
        created = await self.repo.create(doc)
        return serialize_doc(created)

    async def update(self, plan_id: str, payload: SubscriptionPlanUpdateRequest) -> dict:
        if payload.billing_cycle is not None:
            self._ensure_monthly(payload.billing_cycle)
        current = await self.repo.find_by_id(plan_id)
        if current and current.get("plan_type") == CUSTOM_PLAN_TYPE:
            # The hidden custom-plan template is not an editable product.
            raise NotFoundException("Subscription plan not found")
        # description sent as null = cleared.
        data = {k: v for k, v in payload.model_dump(exclude_unset=True).items() if v is not None or k == "description"}
        if "name" in data:
            data["slug"] = slugify(data["name"])
        if "category_quotas" in data and data["category_quotas"]:
            data["total_service_count"] = sum(data["category_quotas"].values())
        updated = await self.repo.update_by_id(plan_id, data)
        if not updated:
            raise NotFoundException("Subscription plan not found")
        return serialize_doc(updated)

    async def delete(self, plan_id: str) -> None:
        # A deleted plan disappears from every lookup (find_by_id skips
        # tombstones): a live subscriber's auto-pay renewal would then find
        # no plan and park the charge for refund, and Purchased plans would
        # read "Unknown plan". Refuse while anyone still holds it — switching
        # the plan off (is_active) stops new sales without breaking them.
        holders = await self.repo.db.user_subscriptions.count_documents({
            "plan_id": plan_id,
            "is_deleted": {"$ne": True},
            "$or": [{"status": {"$in": ["active", "paused"]}}, {"auto_renew": True, "status": {"$ne": "cancelled"}}],
        })
        if holders:
            raise ConflictException(
                f"{holders} customer(s) still hold this plan — switch it off instead (it stops new sales and keeps their plans working)."
            )
        if not await self.repo.soft_delete(plan_id):
            raise NotFoundException("Subscription plan not found")

    async def discontinue(self, plan_id: str) -> dict:
        """Stop SELLING a plan — is_active only, nothing else. This is the
        manager-safe half of `update` (which stays admin-only, full-edit):
        a manager can pull a plan off the shelf without being able to touch
        its price or included services. A subscription already bought on it
        keeps working exactly as before; only new purchases refuse it.

        Society plans are NOT reachable here: they're admin-owned (Society
        plans page) and shared across centers — a manager pulling one would
        block every society's renewals on it."""
        ensure_public_plan(await self.repo.find_by_id(plan_id))
        updated = await self.repo.update_by_id(plan_id, {"is_active": False})
        if not updated:
            raise NotFoundException("Subscription plan not found")
        return serialize_doc(updated)


class UserSubscriptionService:
    def __init__(self, db: AsyncIOMotorDatabase):
        self.repo = UserSubscriptionRepository(db)
        self.plan_repo = SubscriptionPlanRepository(db)
        self.vehicle_repo = VehicleRepository(db)
        self.vehicle_type_repo = VehicleTypeRepository(db)
        self.user_repo = UserRepository(db)
        self.service_repo = ServiceRepository(db)

    async def list_my_subscriptions(self, customer_id: str) -> list[dict]:
        subs = await self.repo.list_for_customer(customer_id, limit=CUSTOMER_LIST_LIMIT)
        views = _with_effective_statuses(subs)
        for v in views:
            # Who extended a pass is staff detail; the customer gets
            # extension_days / extended_until ("book remaining washes until").
            v.pop("extensions", None)
            v.pop("can_extend", None)
            v.pop("extension_days_left", None)
        return await self._with_pass_details(await self._with_society_labels(await self._with_plan_names(views)))

    async def _with_pass_details(self, subs: list[dict]) -> list[dict]:
        """The car's plate on every car-bound pass, and on a custom pass its
        per-service quotas as rows ({service_id, service_name, total,
        remaining}) — two batched lookups for the whole list."""
        vehicle_ids = [ObjectId(v) for v in {s.get("vehicle_id") for s in subs} if isinstance(v, str) and ObjectId.is_valid(v)]
        service_ids = [ObjectId(x) for x in {sid for s in subs for sid in (s.get("total_by_service") or {})} if ObjectId.is_valid(x)]
        vehicles, services = await asyncio.gather(
            self.vehicle_repo.collection.find({"_id": {"$in": vehicle_ids}}, {"registration_number": 1}).to_list(length=len(vehicle_ids))
            if vehicle_ids else asyncio.sleep(0, []),
            self.service_repo.collection.find({"_id": {"$in": service_ids}}, {"name": 1}).to_list(length=len(service_ids))
            if service_ids else asyncio.sleep(0, []),
        )
        plates = {str(v["_id"]): v.get("registration_number") for v in vehicles}
        names = {str(d["_id"]): d.get("name") for d in services}
        for s in subs:
            if s.get("vehicle_id") in plates:
                s.setdefault("registration_number", plates[s["vehicle_id"]])
            if s.get("total_by_service") is not None:
                left = s.get("remaining_by_service") or {}
                s["services"] = [
                    {"service_id": sid, "service_name": names.get(sid) or "Service", "total": int(total), "remaining": int(left.get(sid, 0))}
                    for sid, total in (s.get("total_by_service") or {}).items()
                ]
        return subs

    async def _with_society_labels(self, subs: list[dict]) -> list[dict]:
        """A society pass reads "<plan> — <society>" and carries the
        society's name, so the customer's plan list labels it clearly."""
        ids = list({s.get("society_id") for s in subs if s.get("society_id")})
        if not ids:
            return subs
        rows = await self.repo.db.societies.find(
            {"_id": {"$in": [ObjectId(i) for i in ids if ObjectId.is_valid(i)]}},
            {"name": 1, "form_token": 1, "form_enabled": 1, "is_active": 1},
        ).to_list(length=len(ids))
        by_id = {str(r["_id"]): r for r in rows}
        for s in subs:
            society = by_id.get(s.get("society_id") or "")
            if society:
                s["society_name"] = society.get("name")
                # The resident's hub: it opens for anyone holding a plan or
                # request there even with the form switched off (or a
                # society switched off) — see SocietyService.society_by_token
                # — so the link only needs the society to have one and the
                # pass not to be cancelled (a cancelled plan has no hub).
                live_link = society.get("form_token") and s.get("status") != SubscriptionStatus.CANCELLED.value
                s["society_form_path"] = f"/society/{society.get('form_token')}" if live_link else None
                if s.get("plan_name"):
                    s["plan_name"] = f"{s['plan_name']} — {society.get('name')}"
        return subs

    async def _with_plan_names(self, subs: list[dict]) -> list[dict]:
        """plan_name on each pass (one batched lookup), so the dashboard
        can title a pass — even one whose plan is no longer on sale —
        without fetching the whole plan catalogue."""
        plan_ids = list({s.get("plan_id") for s in subs if s.get("plan_id")})
        plans = await self.plan_repo.find_by_ids(plan_ids) if plan_ids else []
        names = {str(p["_id"]): p.get("name") for p in plans}
        for s in subs:
            s.setdefault("plan_name", names.get(s.get("plan_id") or ""))
        return subs

    async def list_for_customer(self, customer_id: str, actor_role: str = "admin", actor_center_id: str | None = None) -> list[dict]:
        """Manager/admin-facing equivalent of list_my_subscriptions for an
        arbitrary customer — used by the manager booking flow and the
        manager assign-a-plan action. A manager sees the same slice the
        customer 360 shows them (CRMService.get_customer_360): self-serve
        plans (no center) and their own center's grants — never a plan
        another center sold (amount, who collected the cash)."""
        subs = await self.repo.list_for_customer(customer_id, limit=CUSTOMER_LIST_LIMIT)
        if actor_role != "admin":
            subs = [x for x in subs if x.get("service_center_id") in (None, "") or (actor_center_id and x.get("service_center_id") == actor_center_id)]
        # plan_name (+ the society label) so the manager's picker can title
        # each pass, and the plate / per-service quotas a custom pass books by.
        return await self._with_pass_details(await self._with_society_labels(await self._with_plan_names(_with_effective_statuses(subs))))

    async def subscribe(
        self, customer_id: str, payload: SubscribeRequest, razorpay_subscription_id: str | None = None,
        *, service_center_id: str | None = None, expected_vehicle_type: str | None = None, amount_paid: float | None = None,
    ) -> dict:
        """`expected_vehicle_type`: the vehicle type the payment was PRICED
        for (frozen on the order). If the car is a different type now, this
        raises PricedVehicleChanged and creates nothing (PAY-01).
        `amount_paid`: what the customer actually paid — stamped on the pass
        so reports never show ₹0 or a re-priced figure (PAY-08/10)."""
        # A purchase needs a signed-in customer (the route enforces that);
        # customers sign in by phone OTP, so there is no separate
        # verification gate here any more (2026-09 quick model).
        customer = await self.user_repo.find_by_id(customer_id)
        if not customer:
            raise NotFoundException("Customer not found")
        return await self._create_subscription(
            customer_id, payload.plan_id, payload.auto_renew, payload.vehicle_type, razorpay_subscription_id,
            vehicle_id=payload.vehicle_id, service_id=payload.service_id, service_center_id=service_center_id,
            expected_vehicle_type=expected_vehicle_type, amount_paid=amount_paid,
        )

    async def validate_purchase(self, customer_id: str, payload: SubscribeRequest) -> None:
        """Dry-run of subscribe()'s validation — every check, no write.
        The payment flow runs this BEFORE creating a Razorpay order so a
        customer is never charged for a purchase the post-payment create
        would then refuse (unverified phone, inactive plan, invalid tier)."""
        customer = await self.user_repo.find_by_id(customer_id)
        if not customer:
            raise NotFoundException("Customer not found")
        plan = await self.plan_repo.find_by_id(payload.plan_id)
        if not plan or not plan.get("is_active"):
            raise NotFoundException("Subscription plan not found or inactive")
        ensure_public_plan(plan)
        if payload.vehicle_id:
            # Pass purchase: the car and the service are the whole spec, and
            # both are re-validated here BEFORE any money moves.
            await self._resolve_pass_target(customer_id, plan, payload.vehicle_id, payload.service_id)
        elif payload.vehicle_type and payload.service_id:
            # 2026-09 pass model: a vehicle TYPE + one service.
            await self._resolve_pass_service(plan, payload.vehicle_type, payload.service_id)
        elif payload.vehicle_type:
            vt_doc = await self.vehicle_type_repo.find_by_id(payload.vehicle_type)
            if not vt_doc or not vt_doc.get("is_active", True):
                raise BadRequestException("Pick a valid vehicle type for this plan.")
            plan_types = plan.get("vehicle_types") or []
            if plan_types and payload.vehicle_type not in plan_types:
                raise BadRequestException("This plan isn't sold for that vehicle type.")
        await self._guard_duplicate_pass(customer_id, payload.plan_id, plan, payload.vehicle_id, payload.vehicle_type, payload.service_id)

    async def resolve_service_price(self, plan_id: str, vehicle_type: str, service_id: str) -> tuple[dict, dict, float]:
        """Plan + service + its price for a (plan, vehicle type, service)
        combo — the pricing half of quote_pass, usable before a customer is
        even known (the manager offer preview may run before the phone
        number is finished). Raises the same errors quote_pass would for
        an invalid plan/type/service combination."""
        plan = await self.plan_repo.find_by_id(plan_id)
        if not plan or not plan.get("is_active"):
            raise NotFoundException("Subscription plan not found or inactive")
        ensure_public_plan(plan)
        service = await self._resolve_pass_service(plan, vehicle_type, service_id)
        return plan, service, resolve_pass_price(plan, service, vehicle_type)

    async def has_active_pass(self, customer_id: str, vehicle_type: str, service_id: str) -> bool:
        return await self._active_pass_for_type(customer_id, vehicle_type, service_id) is not None

    async def assign(
        self, payload: AssignSubscriptionRequest, *, actor_center_id: str | None = None,
        expected_vehicle_type: str | None = None, amount_paid: float | None = None,
    ) -> dict:
        """Manager/admin granting a subscription to a customer directly —
        same validation as self-purchase, just with an explicit target
        customer instead of the caller themselves. `actor_center_id` (the
        granting manager's own center) is what lets this show up on that
        center's Subscriptions page — a directly-granted plan has no
        booking yet for the usual booking-history linkage to find (see
        center_overview). `expected_vehicle_type` / `amount_paid`: as in
        subscribe()."""
        return await self._create_subscription(
            payload.customer_id, payload.plan_id, payload.auto_renew, payload.vehicle_type,
            vehicle_id=payload.vehicle_id, service_id=payload.service_id, service_center_id=actor_center_id,
            expected_vehicle_type=expected_vehicle_type, amount_paid=amount_paid,
        )

    async def _create_subscription(
        self,
        customer_id: str,
        plan_id: str,
        auto_renew: bool,
        vehicle_type: str | None = None,
        razorpay_subscription_id: str | None = None,
        vehicle_id: str | None = None,
        service_id: str | None = None,
        service_center_id: str | None = None,
        *,
        expected_vehicle_type: str | None = None,
        amount_paid: float | None = None,
    ) -> dict:
        """A monthly pass names ONE car and ONE service (founder model): the
        car sets the vehicle type that prices it, the service is the only
        main wash it ever covers, and one car carries one pass.

        Subscriptions created before passes existed carry no vehicle_id and
        no service_id — they redeem by vehicle TYPE against the plan's whole
        included list, and every branch below keeps that path working.

        One live pass per identity is enforced by an insert-first claim
        (claim_pass) taken after every check and before the insert — so two
        simultaneous sales/checkouts can never both create one."""
        plan = await self.plan_repo.find_by_id(plan_id)
        if not plan or not plan.get("is_active"):
            raise NotFoundException("Subscription plan not found or inactive")
        ensure_public_plan(plan)

        service: dict | None = None
        if vehicle_id:
            vehicle, service = await self._resolve_pass_target(customer_id, plan, vehicle_id, service_id)
            # The CAR's own type prices the pass — never a type the client
            # sent alongside it.
            vehicle_type = vehicle.get("vehicle_type")
        elif vehicle_type and service_id:
            # 2026-09 pass model: a vehicle TYPE + one service, no car record.
            service = await self._resolve_pass_service(plan, vehicle_type, service_id)
        if expected_vehicle_type and vehicle_type != expected_vehicle_type:
            # Priced (and paid) for one type, the car is another now.
            raise PricedVehicleChanged()

        # Re-checked HERE, not only in validate_purchase, because this is the
        # last gate before a pass is actually created, whichever path got
        # here (a verified payment, a staff grant, a second tab racing the
        # first).
        await self._guard_duplicate_pass(customer_id, plan_id, plan, vehicle_id, vehicle_type, service_id if service else None)

        # LEGACY tier path: which vehicle type this card is being paid for
        # when no specific car was named. Validated against the real
        # vehicle-type catalog and the plan's own covered-type list.
        if vehicle_type and not vehicle_id and not service:
            vt_doc = await self.vehicle_type_repo.find_by_id(vehicle_type)
            if not vt_doc or not vt_doc.get("is_active", True):
                raise BadRequestException("Pick a valid vehicle type for this plan.")
            plan_types = plan.get("vehicle_types") or []
            if plan_types and vehicle_type not in plan_types:
                raise BadRequestException("This plan isn't sold for that vehicle type.")

        now = now_ist()
        days = _CYCLE_DAYS.get(plan["billing_cycle"], 30)
        category_quotas = plan.get("category_quotas") or {}
        total = sum(category_quotas.values()) if category_quotas else plan["total_service_count"]
        doc = {
            "customer_id": customer_id,
            "plan_id": plan_id,
            # Only ever set for a STAFF-granted plan (cash/link/auto-pay/free
            # assign) — the manager's own center at the moment of granting.
            # A self-serve purchase has none; center_overview finds those
            # through the customer's booking history instead (see there).
            "service_center_id": service_center_id,
            "vehicle_id": vehicle_id,
            "service_id": service_id if service else None,
            "vehicle_type": vehicle_type,
            "purchased_price": (
                resolve_pass_price(plan, service, vehicle_type)
                if service
                else (resolve_plan_price(plan, vehicle_type) if vehicle_type else None)
            ),
            "status": SubscriptionStatus.ACTIVE.value,
            "total_service_count": total,
            "remaining_service_count": total,
            "total_by_category": dict(category_quotas),
            "remaining_by_category": dict(category_quotas),
            "start_date": now,
            "end_date": now + timedelta(days=days),
            "auto_renew": auto_renew,
            "razorpay_subscription_id": razorpay_subscription_id,
            "renewal_count": 0,
        }
        if amount_paid is not None:
            doc["amount_paid"] = float(amount_paid)
        keys = pass_claim_keys(
            customer_id, plan_id=plan_id, vehicle_id=vehicle_id,
            vehicle_type=vehicle_type if service else None, service_id=service_id if service else None,
        )
        try:
            claim = await claim_pass(self.repo.db, keys, customer_id)
        except PassClaimConflict:
            # Lost the race to a simultaneous creation: refuse with the same
            # words the duplicate guard uses (it sees the winner's pass now).
            await self._guard_duplicate_pass(customer_id, plan_id, plan, vehicle_id, vehicle_type, service_id if service else None)
            raise PassClaimConflict("This pass is already being set up — check your plans in a moment instead of buying it again.")
        try:
            created = await self.repo.create(doc)
        except BaseException:
            await release_pass_claim(self.repo.db, claim)
            raise
        await bind_pass_claim(self.repo.db, claim, str(created["_id"]))
        result = _with_effective_status(created)
        # Denormalized for callers that want to show/send the plan name
        # without a second lookup (e.g. the purchase-confirmation ticket
        # issued right after subscribe() — see UserSubscriptionController).
        result["plan_name"] = plan.get("name")
        return result

    async def get_subscription(self, subscription_id: str) -> dict | None:
        """The raw subscription doc — booking pricing needs to know whether
        this is a PASS (a named car + a named service) or a legacy
        type-scoped plan before it can work out what's waived."""
        return await self.repo.find_by_id(subscription_id)

    async def get_plan(self, subscription_id: str) -> dict | None:
        """Returns the plan doc backing a subscription — used by booking
        creation to price a same-visit "swap to a different service" charge
        against exactly what the plan actually includes (see
        BookingService._subscription_discount)."""
        sub = await self.repo.find_by_id(subscription_id)
        if not sub:
            return None
        return await self.plan_repo.find_by_id(sub["plan_id"])

    async def usage_history(self, subscription_id: str, actor_role: str = "admin", actor_center_id: str | None = None) -> dict:
        """A manager/admin clicking one plan: every booking that actually
        SPENT a visit off it, most recent first — "when was it last used,
        how many are left" made concrete instead of just a bare count.
        A manager: only a self-serve plan or their own center's grant, and
        only the visits their own center delivered."""
        sub = await self.repo.find_by_id(subscription_id)
        if not sub:
            raise NotFoundException("Subscription not found")
        booking_match: dict = {"subscription_id": subscription_id, "is_deleted": {"$ne": True}}
        if actor_role != "admin":
            if not actor_center_id or sub.get("service_center_id") not in (None, "", actor_center_id):
                raise NotFoundException("Subscription not found")
            booking_match["service_center_id"] = actor_center_id
        bookings = await self.repo.db.bookings.find(booking_match).sort("scheduled_date", -1).to_list(length=200)
        rows = [
            {
                "booking_id": str(b["_id"]),
                "booking_number": b.get("booking_number"),
                "status": b.get("status"),
                "scheduled_date": b.get("scheduled_date"),
                "scheduled_slot": b.get("scheduled_slot"),
                "total_amount": b.get("total_amount"),
            }
            for b in bookings
        ]
        last_used = next((r["scheduled_date"] for r in rows if r["status"] == "completed"), None)
        return {
            "subscription_id": subscription_id,
            "remaining_service_count": sub.get("remaining_service_count"),
            "total_service_count": sub.get("total_service_count"),
            "last_used_at": last_used.isoformat() if last_used else None,
            "bookings": serialize_list(rows),
        }

    # ------------------------------------------------------------------
    # Pass extension (founder rule 2026-10-07, generalised from society)
    # ------------------------------------------------------------------

    async def extend_pass(
        self, subscription_id: str, days: int, *, note: str | None, actor_id: str, actor_role: str,
        actor_center_id: str | None, society_id: str | None = None,
    ) -> dict:
        """A manager (of the pass's own center — a society pass: of its
        society's center) or an admin gives a pass a few more days so its
        REMAINING washes can still be booked: at most PASS_EXTENSION_MAX_DAYS
        in total per 30-day period, only in the period's last
        PASS_EXTENSION_WINDOW or after it ended, and only while washes
        remain. Customers never extend. Every grant is recorded on the pass
        (who, role, days, when, note); the route audit-logs it.

        Atomic: written guarded on the extension total and end it read, so
        simultaneous grants can never add up past the cap. Reviving an ENDED
        pass first re-takes its car / identity claim — a car that picked up
        another live pass meanwhile is refused (two live passes on one car).

        `society_id`: the society route's own society — the pass must be
        one of its residents' (404 otherwise). Returns the society pass view
        for a society pass, else the pass's own view."""
        from app.core.authz import ensure_own_center

        if actor_role not in ("manager", "admin"):
            raise ForbiddenException("Only a manager or an admin can extend a pass.")
        days = int(days)
        if days < 1 or days > PASS_EXTENSION_MAX_DAYS:
            raise BadRequestException(f"Extend by 1 to {PASS_EXTENSION_MAX_DAYS} days.")
        societies = None
        if society_id:
            from app.services.society_service import SocietyService

            societies = SocietyService(self.repo.db)
            await societies.society_for_actor(society_id, actor_role, actor_center_id)
        sub = await self.repo.find_by_id(subscription_id) if ObjectId.is_valid(subscription_id or "") else None
        if not sub or (society_id and sub.get("society_id") != society_id):
            raise NotFoundException("Plan not found")
        if sub.get("society_id"):
            if societies is None:
                from app.services.society_service import SocietyService

                societies = SocietyService(self.repo.db)
                await societies.society_for_actor(sub["society_id"], actor_role, actor_center_id)
        else:
            # A self-serve pass (no center) is admin-only: fails closed.
            ensure_own_center(actor_role, actor_center_id, sub.get("service_center_id"))
        actor = await self.user_repo.collection.find_one({"_id": ObjectId(actor_id)}, {"full_name": 1}) if ObjectId.is_valid(actor_id or "") else None
        for _ in range(6):
            if not sub:
                raise NotFoundException("Plan not found")
            self._ensure_extendable(sub)
            now = now_ist()
            end = _as_utc(sub.get("end_date"))
            used = int(sub.get("extension_days") or 0)
            if used + days > PASS_EXTENSION_MAX_DAYS:
                left = PASS_EXTENSION_MAX_DAYS - used
                raise BadRequestException(
                    f"A plan's 30 days can be extended by {PASS_EXTENSION_MAX_DAYS} days at most — "
                    + (f"only {left} more day{'s' if left != 1 else ''} can be added." if left > 0 else f"this one already has all {PASS_EXTENSION_MAX_DAYS}.")
                )
            until = end + timedelta(days=used + days)
            if until <= now:
                raise BadRequestException(
                    f"This plan ended on {day_label(from_stored(end), year=False)} — even {PASS_EXTENSION_MAX_DAYS} days from then are already over. "
                    "Sell a new plan instead."
                )
            if not pass_blocks_new(sub, now):
                # Coming back to life: its car must still be its own.
                await self._retake_pass_identity(sub)
            entry = {
                "days": days, "by": actor_id, "by_name": (actor or {}).get("full_name"), "role": actor_role,
                "note": (note or "").strip() or None, "at": now, "period_end": end, "extended_until": until,
            }
            guard: dict = {
                "_id": sub["_id"], "is_deleted": {"$ne": True}, "end_date": sub["end_date"],
                "status": {"$in": [SubscriptionStatus.ACTIVE.value, SubscriptionStatus.EXPIRED.value]},
                "extension_days": sub.get("extension_days") if "extension_days" in sub else {"$exists": False},
                # A custom-plan renewal lined up meanwhile wins: its start was
                # computed from this pass's end (never stretched over it).
                "renewed_by_custom_plan_id": None,
            }
            updated = await self.repo.collection.find_one_and_update(
                guard,
                {
                    # An ended pass the sweep already expired comes back to
                    # life for its remaining washes only (a society pass's
                    # daily bucket wash / schedule stay off — sub_is_live).
                    "$set": {"status": SubscriptionStatus.ACTIVE.value, "extension_days": used + days, "extended_until": until, "updated_at": now},
                    "$push": {"extensions": entry},
                },
                return_document=ReturnDocument.AFTER,
            )
            if updated is not None:
                if updated.get("society_id") and societies is not None:
                    return await societies.pass_view(updated)
                return (await self._with_pass_details(await self._with_plan_names([_with_effective_status(updated)])))[0]
            sub = await self.repo.find_by_id(subscription_id)
        raise ConflictException("This plan was just changed by someone else — reload and try again.")

    @staticmethod
    def _ensure_extendable(sub: dict) -> None:
        if sub.get("status") == PASS_SCHEDULED:
            start = _as_utc(sub.get("start_date"))
            raise BadRequestException(
                f"This plan hasn't started yet — it starts on {day_label(from_stored(start))}." if start else "This plan hasn't started yet."
            )
        if sub.get("status") not in (SubscriptionStatus.ACTIVE.value, SubscriptionStatus.EXPIRED.value):
            raise BadRequestException("This plan was cancelled — it can't be extended.")
        if sub.get("renewed_by_custom_plan_id"):
            start = next_period_start(sub)
            when = f" on {day_label(from_stored(start))}" if start else ""
            raise BadRequestException(
                f"This car's renewal starts{when} — the old plan can't be extended over it. "
                f"Book its remaining washes by its {last_booking_day_text(sub) or LAST_BOOKING_DAY}."
            )
        if renews_automatically(sub):
            raise BadRequestException("This plan renews automatically on auto-pay — it doesn't need an extension.")
        if int(sub.get("remaining_service_count") or 0) < 1:
            raise BadRequestException("No washes are left on this plan — there's nothing to extend it for.")
        end = _as_utc(sub.get("end_date"))
        if end is None:
            raise BadRequestException("This plan has no end date to extend.")
        if end - now_ist() > PASS_EXTENSION_WINDOW:
            raise BadRequestException(
                f"Extensions open in the plan's last {PASS_EXTENSION_WINDOW.days} days — this one's {last_booking_day_text(sub)}."
            )

    async def _retake_pass_identity(self, sub: dict) -> None:
        """An ended pass is about to be usable again: the one-live-pass rule
        must still hold. Refused when its car (or type + service / plan, or
        a society plate) already carries ANOTHER live pass; otherwise its
        claims are re-bound to this pass (a stale claim is taken over), so
        no new pass can be created for that car while the extension runs."""
        db = self.repo.db
        sid = str(sub["_id"])
        if await self._other_live_pass(sub):
            raise BadRequestException(
                "This car already has another active plan — the old one can't be extended over it."
            )
        keys = pass_claim_keys_of(sub)
        if sub.get("society_id") and sub.get("vehicle_id"):
            from app.services.society_service import SocietyService

            keys = await SocietyService(db)._claim_keys(sub["customer_id"], sub["vehicle_id"], None)
        for key in keys:
            for attempt in range(10):
                try:
                    claim = await claim_pass(db, [key], sub.get("customer_id"))
                except PassClaimConflict:
                    held = await db[PASS_CLAIMS].find_one({"_id": key})
                    if held and held.get("subscription_id") == sid:
                        break
                    if held and not held.get("subscription_id") and attempt < 9:
                        # Another grant (or a sale) is binding it right now.
                        await asyncio.sleep(0.1)
                        continue
                    raise BadRequestException(
                        "This car already has another active plan — the old one can't be extended over it."
                    )
                await bind_pass_claim(db, claim, sid)
                break

    async def _other_live_pass(self, sub: dict) -> dict | None:
        """A DIFFERENT live pass on the identity this pass occupies."""
        customer_id = sub.get("customer_id")
        if sub.get("vehicle_id"):
            other = await self._active_pass_for_vehicle(customer_id, sub["vehicle_id"])
            if other is None and sub.get("society_id"):
                from app.services.society_service import SocietyService

                other = await SocietyService(self.repo.db)._plate_on_society_pass(sub["vehicle_id"], now_ist())
        elif sub.get("vehicle_type") and sub.get("service_id"):
            other = await self._active_pass_for_type(customer_id, sub["vehicle_type"], sub["service_id"])
        else:
            other = await self._active_same_plan(customer_id, sub.get("plan_id"))
        if other is not None and other.get("_id") == sub.get("_id"):
            return None
        return other

    async def plan_consumption(
        self, subscription_id: str, vehicle_id: str | None, services: list[dict], customer_id: str,
        vehicle_type: str | None = None, as_of: datetime | None = None, scheduled_date=None,
    ) -> dict:
        """Validates the subscription can actually cover this booking's
        services and computes exactly what WOULD be deducted, without
        writing anything yet. Falls back to the flat counter for older
        plans with no category quotas (category_quotas empty) so
        pre-Phase-3 subscriptions keep working. The write itself happens
        separately (commit_consumption), only after the booking this
        belongs to has been durably created — see BookingService.create_booking
        for why the split matters (a booking-creation failure between
        planning and committing must never leave a subscription silently
        decremented for a booking that doesn't exist).

        Vehicle eligibility is checked by TYPE here, not by a specific
        vehicle_id — the subscription itself no longer names one vehicle
        (see UserSubscriptionModel.vehicle_id's docstring). Vehicle
        ownership is already verified by create_booking before this is
        called, so it isn't re-checked here.

        `as_of` — ONLY set for a manager logging a job that already
        happened (create_manager_logged_visit passes the real, possibly
        backdated, service time) — a pass can't retroactively cover a wash
        from before it was even bought. A forward-looking booking never
        passes this.

        `scheduled_date` — the booking's date (date / datetime / 'YYYY-MM-DD').
        A pass covers only bookings dated inside its own period (or a society
        pass's granted extension): a date on/after its end is refused
        (founder rule, PASS-3). None (older callers) skips the date check —
        the booking hooks still enforce it (ensure_subscription_covers_date)."""
        sub = await self._get_active_subscription(subscription_id)
        if scheduled_date is not None:
            ensure_pass_covers_date(sub, scheduled_date)
        if as_of is not None:
            ensure_pass_covers_date(sub, as_of)
            start = sub.get("start_date")
            # Calendar-DATE comparison (IST), not exact-instant — service_at
            # is a manager-entered wall-clock time with only minute
            # precision, while start_date carries full second/microsecond
            # precision from the moment it was granted. Comparing exact
            # instants would wrongly refuse a plan sold and immediately used
            # inside the same clock minute; the founder's actual concern
            # ("bought TODAY, logged a job from before that") is calendar
            # days, not seconds.
            if start is not None and from_stored(start).date() > to_ist(as_of).date():
                raise BadRequestException(
                    f"This pass wasn't active yet on that date — it started on {from_stored(start).strftime('%d %b %Y')}."
                )
        # OWNERSHIP — the single most important line in this method. The
        # subscription_id arrives from the client; without this check any
        # customer could pass someone else's id, get their booking waived,
        # and drain the victim's remaining visits (a real IDOR we shipped).
        # 404, not 403, so a guessed id doesn't confirm the sub exists.
        if sub.get("customer_id") != customer_id:
            raise NotFoundException("Subscription not found")
        if renews_automatically(sub) and int(sub.get("remaining_service_count") or 0) < 1:
            raise BadRequestException(
                f"All washes on this pass are used. It refills on {from_stored(sub['end_date']).strftime('%d %b')} when auto-pay renews."
            )
        # ---- CUSTOM PASS: one named car, per-service quotas (custom
        # multi-car plan, 2026-10-07) ---------------------------------------
        if is_custom_pass(sub):
            return await self._custom_consumption(sub, vehicle_id, services)

        plan = await self.plan_repo.find_by_id(sub["plan_id"])

        # ---- PASS PATH: one vehicle TYPE (2026-09 model) or one named car
        # (older passes), one named service -------------------------------
        named_vehicle = sub.get("vehicle_id")
        covered_service_id = sub.get("service_id")
        pass_type = sub.get("vehicle_type")
        if covered_service_id and (named_vehicle or pass_type):
            # A pass that names a car pays for THAT car only — never for a
            # booking that names just a vehicle type (SOC-4: a resident's
            # society pass was spent on a type-only booking anywhere).
            if named_vehicle and vehicle_id != named_vehicle:
                owner_vehicle = await self.vehicle_repo.find_by_id(named_vehicle)
                plate = (owner_vehicle or {}).get("registration_number") or "another car"
                if not vehicle_id:
                    raise BadRequestException(
                        f"This pass is for {plate} only — book that car with it, or turn off the plan to book this as a normal paid wash."
                    )
                raise BadRequestException(
                    f"This pass belongs to {plate}. Book that car with it, or book this one as a normal service."
                )
            if not named_vehicle or not vehicle_id:
                # Type-scoped pass: the booking's vehicle type must be the
                # pass's type or a type the plan prices cheaper.
                booking_type = vehicle_type
                if not booking_type and vehicle_id:
                    v = await self.vehicle_repo.find_by_id(vehicle_id)
                    booking_type = (v or {}).get("vehicle_type")
                covered_doc = await self.service_repo.find_by_id(covered_service_id) if covered_service_id else None
                if pass_type and booking_type != pass_type and not (plan and pass_tier_allows(plan, covered_doc, pass_type, booking_type)):
                    raise BadRequestException(
                        "This pass was bought for a different vehicle type. Book that type with it, or book this one as a normal service."
                    )
            def _sid(service: dict) -> str:
                return str(service.get("_id") or service.get("id") or "")

            main_services = [s for s in services if not s.get("is_addon")]
            wrong = [s for s in main_services if _sid(s) != covered_service_id]
            if wrong:
                covered = await self.service_repo.find_by_id(covered_service_id)
                name = (covered or {}).get("name") or "its own service"
                raise BadRequestException(
                    f"This pass covers {name}. Add-ons are welcome on top, but a different wash has to be booked normally."
                )
            if not main_services:
                raise BadRequestException("Add the service this pass covers to use it.")
            # A pass visit is exactly one visit, whatever add-ons ride along.
            by_category = dict(sub.get("remaining_by_category") or {})
            if by_category:
                category = (main_services[0] or {}).get("category_id")
                if by_category.get(category, 0) < 1:
                    raise BadRequestException("Your plan doesn't have enough remaining services in this category for this booking.")
                return {"by_category": {category: 1}}
            if sub["remaining_service_count"] < 1:
                raise BadRequestException("No remaining services left on this subscription")
            return {"flat_count": 1}

        # ---- LEGACY PATH: scoped by vehicle TYPE, covers the plan's list --
        allowed_types = (plan or {}).get("vehicle_types") or []
        purchased_type = sub.get("vehicle_type")
        if allowed_types or purchased_type:
            booking_type = vehicle_type
            if not booking_type and vehicle_id:
                vehicle = await self.vehicle_repo.find_by_id(vehicle_id)
                booking_type = (vehicle or {}).get("vehicle_type")
            if not booking_type or (allowed_types and booking_type not in allowed_types):
                raise BadRequestException("This subscription doesn't cover this vehicle's type and can't be used for this booking.")
            # Tier cap: a plan bought at hatchback price can't wash an SUV.
            # The other direction (bigger tier used on a smaller vehicle) is
            # allowed and still burns one full visit — the buyer's call.
            if plan and not tier_allows(plan, purchased_type, booking_type):
                raise BadRequestException(
                    "Your plan was purchased for a smaller vehicle type — it works for that type and below. "
                    "Book this vehicle normally, or upgrade your plan."
                )
        # Add-ons riding on a plan visit (extra bikes, polish...) are PAID
        # extras — they never consume plan quota (and are never discounted,
        # see BookingService._subscription_discount). The one exception is a
        # service the admin explicitly listed in the plan's own
        # included_service_ids: that's part of the covered visit.
        included_ids = set((plan or {}).get("included_service_ids") or [])
        countable = [s for s in services if str(s.get("_id") or s.get("id") or "") in included_ids or not s.get("is_addon")]
        by_category = dict(sub.get("remaining_by_category") or {})
        if by_category:
            needed: dict[str, int] = {}
            for service in countable:
                cat = service["category_id"]
                needed[cat] = needed.get(cat, 0) + 1
            for cat, count in needed.items():
                if by_category.get(cat, 0) < count:
                    raise BadRequestException(
                        "Your plan doesn't have enough remaining services in this category for this booking."
                    )
            return {"by_category": needed}
        if sub["remaining_service_count"] < len(countable):
            raise BadRequestException("No remaining services left on this subscription")
        return {"flat_count": len(countable)}

    async def _custom_consumption(self, sub: dict, vehicle_id: str | None, services: list[dict]) -> dict:
        """A custom pass covers ONLY its own car (never a type-only booking
        or another car), and only the services it has a quota for: each
        main service on the booking with a quota spends one of it; a main
        service the pass has no quota for at all is a normal PAID line (it
        is simply not in covered_service_ids); add-ons are always paid. A
        quota service with nothing left is refused rather than silently
        charged — the customer picked the plan to use it."""
        named = sub.get("vehicle_id")
        plate = None
        if named:
            plate = ((await self.vehicle_repo.find_by_id(named)) or {}).get("registration_number")
        plate = plate or "its own car"
        if not vehicle_id or vehicle_id != named:
            raise BadRequestException(
                f"This plan is for {plate} only — book that car with it, or book this one as a normal paid wash."
            )
        totals = {str(k): int(v) for k, v in (sub.get("total_by_service") or {}).items()}
        remaining = {str(k): int(v) for k, v in (sub.get("remaining_by_service") or {}).items()}
        needed: dict[str, int] = {}
        for service in services:
            if service.get("is_addon"):
                continue
            sid = str(service.get("_id") or service.get("id") or "")
            if sid in totals:
                needed[sid] = needed.get(sid, 0) + 1
        if not needed:
            names = await self._service_name_map(list(totals))
            on_plan = ", ".join(n for sid, n in names.items() if remaining.get(sid, 0) > 0) or "nothing more"
            raise BadRequestException(
                f"This plan covers {on_plan} for {plate}. Add one of those to use it, or book without the plan."
            )
        short = [sid for sid, n in needed.items() if remaining.get(sid, 0) < n]
        if short:
            names = await self._service_name_map(short)
            raise BadRequestException(
                f"No {', '.join(names.get(sid) or 'such' for sid in short)} washes are left on this plan for {plate} — "
                "book it without the plan (it'll be charged)."
            )
        return {"by_service": needed, "covered_service_ids": list(needed)}

    async def _service_name_map(self, ids: list[str]) -> dict[str, str]:
        docs = await self.service_repo.find_by_ids(ids) if ids else []
        names = {str(d["_id"]): d.get("name") or "Service" for d in docs}
        return {sid: names.get(sid, "Service") for sid in ids}

    async def commit_consumption(self, subscription_id: str, consumption: dict) -> None:
        """Applies a consumption plan produced by plan_consumption. Booking
        creation calls this right after the booking document itself is
        successfully created — never before.

        Atomic via optimistic concurrency (guard the write on the document's
        own `updated_at` not having changed since we read it, retrying
        against a fresh read on conflict) — this is what actually closes the
        race two bookings both trying to spend the LAST remaining unit could
        otherwise hit (same race class already fixed for coupon usage this
        session): without it, both could read "1 remaining" before either
        commits, and both would succeed, taking the count negative."""
        for _ in range(5):
            sub = await self.repo.find_by_id(subscription_id)
            if not sub:
                return
            update_data = self._apply_delta(sub, consumption, sign=-1)
            if (
                update_data.get("remaining_service_count", 0) < 0
                or any(v < 0 for v in update_data.get("remaining_by_category", {}).values())
                or any(v < 0 for v in update_data.get("remaining_by_service", {}).values())
            ):
                raise BadRequestException("This subscription's remaining services just ran out — someone else may have just booked with it.")
            new_remaining = update_data.get("remaining_service_count", sub.get("remaining_service_count", 0))
            # An auto-pay pass stays ACTIVE at 0 until the next charge
            # refills it: expiring it would let the customer buy a second
            # pass while the mandate keeps billing the first.
            # A SOCIETY pass stays active at 0 premium washes too: its daily
            # bucket washes run until end_date (docs/SOCIETY_PLANS.md).
            if new_remaining <= 0 and not renews_automatically(sub) and not sub.get("society_id"):
                update_data["status"] = SubscriptionStatus.EXPIRED.value
            # When a wash was last booked on it — the wash reminder leaves a
            # pass alone for a few days after.
            update_data["last_used_at"] = now_ist()
            result = await self.repo.update_if(subscription_id, {"updated_at": sub["updated_at"]}, update_data)
            if result is not None:
                return
        raise BadRequestException("Couldn't update this subscription right now — please try again.")

    async def restore_consumption(self, subscription_id: str, consumption: dict) -> None:
        """Reverses commit_consumption — called when a subscription-paid
        booking is cancelled, so the credit it consumed isn't permanently
        lost for a service that was never actually performed. Re-activates
        the subscription if it had been auto-expired by this exact
        consumption. Clamped so a pre-upgrade booking's late cancellation
        can't credit past what the (possibly since-upgraded) plan currently
        allows. Best-effort: cancel_booking must still succeed even if this
        can't win its optimistic lock after retries — a booking cancellation
        should never fail because of a subscription-credit bookkeeping
        hiccup, so this silently gives up rather than raising."""
        for _ in range(5):
            sub = await self.repo.find_by_id(subscription_id)
            if not sub or sub.get("refund"):
                # A refunded pass (custom plan, one car) was paid back for
                # its unused washes — nothing to give back to.
                return
            update_data = self._apply_delta(sub, consumption, sign=1)
            total_by_category = sub.get("total_by_category") or {}
            total_by_service = sub.get("total_by_service") or {}
            if "remaining_by_service" in update_data:
                # Never past what this car's custom pass was sold with.
                clamped = {sid: min(val, int(total_by_service.get(sid, val))) for sid, val in update_data["remaining_by_service"].items()}
                update_data["remaining_by_service"] = clamped
                update_data["remaining_service_count"] = sum(clamped.values())
            elif "remaining_by_category" in update_data:
                clamped = {cat: min(val, total_by_category.get(cat, val)) for cat, val in update_data["remaining_by_category"].items()}
                update_data["remaining_by_category"] = clamped
                update_data["remaining_service_count"] = sum(clamped.values())
            else:
                cap = sub.get("total_service_count", update_data["remaining_service_count"])
                update_data["remaining_service_count"] = min(update_data["remaining_service_count"], cap)
            new_remaining = update_data.get("remaining_service_count", sub.get("remaining_service_count", 0))
            if sub["status"] == SubscriptionStatus.EXPIRED.value and new_remaining > 0:
                update_data["status"] = SubscriptionStatus.ACTIVE.value
            result = await self.repo.update_if(subscription_id, {"updated_at": sub["updated_at"]}, update_data)
            if result is not None:
                return

    @staticmethod
    def _apply_delta(sub: dict, consumption: dict, sign: int) -> dict:
        """sign=-1 to consume, sign=+1 to restore — same arithmetic either way.
        A custom pass (by_service) keeps the flat count as the sum of its
        per-service quotas, so every list/reminder reading the flat count
        stays right."""
        if "by_service" in consumption:
            by_service = {str(k): int(v) for k, v in (sub.get("remaining_by_service") or {}).items()}
            for sid, count in consumption["by_service"].items():
                by_service[str(sid)] = by_service.get(str(sid), 0) + sign * int(count)
            return {"remaining_by_service": by_service, "remaining_service_count": sum(by_service.values())}
        if "by_category" in consumption:
            by_category = dict(sub.get("remaining_by_category") or {})
            for cat, count in consumption["by_category"].items():
                by_category[cat] = by_category.get(cat, 0) + sign * count
            return {"remaining_by_category": by_category, "remaining_service_count": sum(by_category.values())}
        flat_count = consumption.get("flat_count", 0)
        return {"remaining_service_count": sub.get("remaining_service_count", 0) + sign * flat_count}

    async def _resolve_pass_target(self, customer_id: str, plan: dict, vehicle_id: str, service_id: str | None) -> tuple[dict, dict]:
        """The two questions a pass purchase answers, validated: which CAR
        (must be one this customer owns, and its own type is what prices the
        pass — never a type the client claims) and which SERVICE (must be on
        this plan's menu, and must be a real, active, non-add-on service)."""
        vehicle = await self.vehicle_repo.find_by_id(vehicle_id)
        if not vehicle or vehicle.get("owner_id") != customer_id:
            raise NotFoundException("Vehicle not found")
        vehicle_type = vehicle.get("vehicle_type")
        plan_types = plan.get("vehicle_types") or []
        if plan_types and vehicle_type not in plan_types:
            raise BadRequestException("This pass isn't sold for that vehicle type.")

        menu = plan.get("included_service_ids") or []
        if not service_id:
            raise BadRequestException("Choose which service this pass should cover.")
        if menu and service_id not in menu:
            raise BadRequestException("That service isn't available on this pass.")
        service = await self.service_repo.find_by_id(service_id)
        if not service or not service.get("is_active", True) or service.get("is_addon"):
            raise BadRequestException("That service isn't available on this pass.")
        # THE CAR AND THE SERVICE HAVE TO FIT EACH OTHER. A bike wash on a
        # Thar is not a pass anyone can ever redeem, and hiding the option
        # in the UI is not the same as refusing it — the sheet is just a
        # convenience, this is the rule. (Empty vehicle_types = the service
        # is sold for every type, so nothing to check.)
        service_types = service.get("vehicle_types") or []
        if service_types and vehicle_type not in service_types:
            plate = vehicle.get("registration_number") or "this vehicle"
            raise BadRequestException(
                f"{service.get('name')} isn't offered for {plate} — pick a service that fits this vehicle."
            )
        return vehicle, service

    async def _resolve_pass_service(self, plan: dict, vehicle_type: str, service_id: str | None) -> dict:
        """2026-09 pass model — the two things a pass is: a vehicle TYPE
        and ONE service. Validates both against the catalog and the plan's
        own menu; returns the service doc."""
        vt_doc = await self.vehicle_type_repo.find_by_id(vehicle_type)
        if not vt_doc or not vt_doc.get("is_active", True):
            raise BadRequestException("Pick a valid vehicle type for this pass.")
        plan_types = plan.get("vehicle_types") or []
        if plan_types and vehicle_type not in plan_types:
            raise BadRequestException("This pass isn't sold for that vehicle type.")
        menu = plan.get("included_service_ids") or []
        if not service_id:
            raise BadRequestException("Choose which service this pass should cover.")
        if menu and service_id not in menu:
            raise BadRequestException("That service isn't available on this pass.")
        service = await self.service_repo.find_by_id(service_id)
        if not service or not service.get("is_active", True) or service.get("is_addon"):
            raise BadRequestException("That service isn't available on this pass.")
        service_types = service.get("vehicle_types") or []
        if service_types and vehicle_type not in service_types:
            raise BadRequestException(f"{service.get('name')} isn't offered for {vt_doc.get('name') or 'this vehicle type'}.")
        return service

    async def _active_pass_for_type(self, customer_id: str, vehicle_type: str, service_id: str) -> dict | None:
        """The live TYPE pass (no car named) this customer already holds for
        this vehicle type + service, if any. A pass that names a car (a
        society pass, an older per-car pass) pays only for that car (SOC-4),
        so it never stands in for — or blocks — a type pass."""
        subs = await self.repo.collection.find(
            {
                "customer_id": customer_id,
                "vehicle_type": vehicle_type,
                "service_id": service_id,
                "vehicle_id": {"$in": [None, ""]},
                "status": SubscriptionStatus.ACTIVE.value,
                "is_deleted": {"$ne": True},
            }
        ).to_list(length=50)
        now = now_ist()
        # Live = inside its period (or a society extension), or past it while
        # auto-pay's charge is still due — the mandate will renew it, so a
        # second pass would bill twice. Same rule the creation claim judges.
        return next((c for c in subs if pass_blocks_new(c, now)), None)

    async def quote_pass(self, customer_id: str, plan_id: str, vehicle_id: str | None, service_id: str, vehicle_type: str | None = None) -> dict:
        """What this pass would cost — priced by the SAME code that charges
        for it, so the number in the purchase sheet is the number on the
        card. Read-only; buys nothing. Quoted for a saved car (older flow)
        or straight for a vehicle TYPE (2026-09 model)."""
        plan = await self.plan_repo.find_by_id(plan_id)
        if not plan or not plan.get("is_active"):
            raise NotFoundException("Subscription plan not found or inactive")
        ensure_public_plan(plan)
        if vehicle_id:
            vehicle, service = await self._resolve_pass_target(customer_id, plan, vehicle_id, service_id)
            vt = vehicle.get("vehicle_type")
            existing = await self._active_pass_for_vehicle(customer_id, vehicle_id)
        else:
            if not vehicle_type:
                raise BadRequestException("Pick a vehicle type for this pass.")
            service = await self._resolve_pass_service(plan, vehicle_type, service_id)
            vt = vehicle_type
            existing = await self._active_pass_for_type(customer_id, vehicle_type, service_id)
        return {
            "plan_id": plan_id,
            "plan_name": plan.get("name"),
            "vehicle_id": vehicle_id,
            "vehicle_type": vt,
            "service_id": service_id,
            "service_name": service.get("name"),
            "visits": int(plan.get("total_service_count") or 1),
            "price_per_wash": service_price_for_type(service, vt),
            "price": resolve_pass_price(plan, service, vt),
            # An admin-set price has no "% off" story to tell — the UI shows
            # the figure plainly instead of inventing a saving.
            "discount_percent": (
                0.0
                if pass_price_override(plan, service, vt) is not None
                else float(plan.get("plan_discount_percent") or 0.0)
            ),
            # So the sheet can say "you already have this pass" instead of
            # only finding out when the payment is refused.
            "vehicle_has_pass": existing is not None,
        }

    async def _active_pass_for_vehicle(self, customer_id: str, vehicle_id: str) -> dict | None:
        """The live pass on this car, if any — ONE car carries ONE pass. A
        scheduled renewal holds the car too (the running pass is returned
        first when both exist)."""
        subs = await self.repo.collection.find(
            {
                "customer_id": customer_id,
                "vehicle_id": vehicle_id,
                "status": {"$in": list(LIVE_PASS_STATUSES)},
                "is_deleted": {"$ne": True},
            }
        ).to_list(length=50)
        now = now_ist()
        subs.sort(key=lambda c: c.get("status") != SubscriptionStatus.ACTIVE.value)
        return next((c for c in subs if pass_blocks_new(c, now)), None)

    async def _active_same_plan(self, customer_id: str, plan_id: str) -> dict | None:
        """The still-usable copy of `plan_id` this customer already holds, if
        any. "Still usable" = stored status active AND end_date in the future:
        spending the last visit already flips the status to expired
        (commit_consumption), so a fully-used card is immediately re-buyable,
        and so is a lapsed one."""
        subs = await self.repo.collection.find(
            {
                "customer_id": customer_id,
                "plan_id": plan_id,
                "status": SubscriptionStatus.ACTIVE.value,
                "is_deleted": {"$ne": True},
            }
        ).to_list(length=50)
        now = now_ist()
        return next((c for c in subs if pass_blocks_new(c, now)), None)

    async def _guard_duplicate_pass(
        self, customer_id: str, plan_id: str, plan: dict, vehicle_id: str | None,
        vehicle_type: str | None = None, service_id: str | None = None,
    ) -> None:
        """Founder rule: ONE CAR CARRIES ONE PASS. Buy as many passes as you
        have cars — never two on the same car, whatever plan they're on,
        because a second pass on one car is money the customer can't spend
        any faster. 2026-09 model: the same rule per vehicle TYPE + service
        (a second SUV foam-wash pass can't be spent any faster either). For
        pre-pass subscriptions (no car named) the older one-copy-per-plan
        rule still applies."""
        if not vehicle_id and vehicle_type and service_id:
            existing = await self._active_pass_for_type(customer_id, vehicle_type, service_id)
            if not existing:
                return
            end = existing.get("end_date")
            until = f" until {from_stored(end).strftime('%d %b %Y')}" if end else ""
            left = existing.get("remaining_service_count") or 0
            if renews_automatically(existing) and end and from_stored(end) <= now_ist():
                raise BadRequestException("You already have this pass on auto-pay and it's renewing now — no need to buy it again.")
            if renews_automatically(existing) and left < 1 and end:
                raise BadRequestException(
                    f"You already have this pass on auto-pay. It refills on {from_stored(end).strftime('%d %b %Y')} — no need to buy it again."
                )
            raise BadRequestException(
                f"You already have an active pass for this vehicle type and service ({left} wash{'' if left == 1 else 'es'} left{until}). "
                "Use it up or let it expire first."
            )
        if vehicle_id:
            existing = await self._active_pass_for_vehicle(customer_id, vehicle_id)
            if not existing:
                return
            vehicle = await self.vehicle_repo.find_by_id(vehicle_id)
            plate = (vehicle or {}).get("registration_number") or "this car"
            last = last_booking_day_text(existing)
            until = f" ({last})" if last else ""
            raise BadRequestException(
                f"{plate} already has an active pass{until}. One car carries one pass — "
                "use it up or let it expire, or buy a pass for a different car."
            )
        await self._guard_duplicate_plan(customer_id, plan_id, plan)

    async def _guard_duplicate_plan(self, customer_id: str, plan_id: str, plan: dict) -> None:
        """Pre-pass fallback: one live copy of a given plan when no specific
        car is named (legacy purchases, staff grants without a vehicle)."""
        existing = await self._active_same_plan(customer_id, plan_id)
        if not existing:
            return
        left = existing.get("remaining_service_count") or 0
        end = existing.get("end_date")
        until = from_stored(end).strftime("%d %b %Y") if end else None
        detail = f"{left} visit{'' if left == 1 else 's'} left"
        if until:
            detail += f", valid until {until}"
        raise BadRequestException(
            f"You already have an active {plan.get('name') or 'plan'} ({detail}). "
            "You can upgrade it, or buy this plan again once it runs out or expires."
        )

    # -- Auto-pay (Razorpay mandate) lifecycle ---------------------------

    async def apply_renewal_cycle(self, subscription_id: str, cycle_end: datetime | None = None) -> dict | None:
        """A renewal charge landed at Razorpay — refresh the card for the new
        cycle: full quota again, end_date pushed out, status back to active.
        Unused visits from the old cycle are NOT carried over (a monthly plan
        is an allowance, not a wallet) — the same thing that happens when a
        cycle simply ends. Returns None when the subscription can't be
        renewed (gone, or cancelled by the customer), so the caller can park
        the charge for a refund instead of silently reviving a dead plan."""
        sub = await self.repo.find_by_id(subscription_id)
        if not sub or sub.get("status") == SubscriptionStatus.CANCELLED.value:
            return None
        plan = await self.plan_repo.find_by_id(sub["plan_id"])
        if not plan:
            return None
        quotas = plan.get("category_quotas") or {}
        total = sum(quotas.values()) if quotas else plan.get("total_service_count", 1)
        now = now_ist()
        current_end = sub.get("end_date")
        if current_end is not None and current_end.tzinfo is None:
            current_end = current_end.replace(tzinfo=timezone.utc)
        # Chain from the old end_date when the renewal charge arrives BEFORE
        # the cycle actually lapsed (Razorpay bills a little early), never
        # from a date already in the past.
        anchor = current_end if current_end and current_end > now else now
        new_end = cycle_end or (anchor + timedelta(days=_CYCLE_DAYS.get(plan["billing_cycle"], 30)))
        # A paid renewal can only ever EXTEND the window. Razorpay's
        # current_end is the authority on where the new cycle ends, but a
        # clock skew or an early charge must never hand the customer less
        # time than they already have.
        new_end = max(new_end, anchor)
        updated = await self.repo.update_by_id(
            subscription_id,
            {
                "status": SubscriptionStatus.ACTIVE.value,
                "total_service_count": total,
                "remaining_service_count": total,
                "total_by_category": dict(quotas),
                "remaining_by_category": dict(quotas),
                "end_date": new_end,
                "auto_renew": True,
                "renewal_count": (sub.get("renewal_count") or 0) + 1,
                "last_renewed_at": now,
                # A new cycle: its own "ends soon" / "washes left" notices,
                # and whatever retry state the last charge was in is over.
                "expiry_reminder_sent": False,
                "wash_reminder_sent_at": None,
                "used_up_notice_sent_at": None,
                "autopay_state": None,
            },
        )
        return _with_effective_status(updated) if updated else None

    async def mark_auto_renew_off(self, subscription_id: str) -> None:
        """The mandate is gone at Razorpay (cancelled/completed/halted) — stop
        promising the customer it will renew."""
        sub = await self.repo.find_by_id(subscription_id)
        if sub:
            await self.repo.update_by_id(subscription_id, self._auto_pay_off_fields(sub))

    @staticmethod
    def _auto_pay_off_fields(sub: dict) -> dict:
        """Auto-pay going off. A pass that was waiting at 0 washes for its
        refill has nothing left to use and no refill coming — it's spent,
        exactly like a one-time pass at 0 (and re-buyable at once)."""
        fields: dict = {"auto_renew": False, "autopay_state": None}
        if sub.get("status") == SubscriptionStatus.ACTIVE.value and int(sub.get("remaining_service_count") or 0) <= 0:
            fields["status"] = SubscriptionStatus.EXPIRED.value
        return fields

    async def set_autopay_state(self, subscription_id: str, state: str | None) -> None:
        """"pending" while Razorpay retries a failed renewal charge (the
        ended-pass sweep waits longer for those), None once it's settled."""
        if not ObjectId.is_valid(subscription_id or ""):
            return
        await self.repo.collection.update_one(
            {"_id": ObjectId(subscription_id), "autopay_state": {"$ne": state}},
            {"$set": {"autopay_state": state, "updated_at": now_ist()}},
        )

    async def set_auto_pay(self, customer_id: str, subscription_id: str, enabled: bool) -> dict:
        """Customer-facing auto-pay switch. Turning it OFF cancels the
        Razorpay mandate at the END of the paid cycle — the visits already
        paid for stay usable. Turning it ON again isn't possible without a
        fresh mandate (a UPI/card authorisation the customer has to approve),
        so that direction points them at the purchase flow instead of
        pretending a dead mandate can be revived."""
        sub = await self.repo.find_by_id(subscription_id)
        if not sub or sub["customer_id"] != customer_id:
            raise NotFoundException("Subscription not found")
        if enabled:
            if sub.get("auto_renew") and sub.get("razorpay_subscription_id"):
                return _with_effective_status(sub)
            raise BadRequestException(
                "Auto-pay needs a fresh payment authorisation — start it from the plan's Subscribe step."
            )
        mandate_id = sub.get("razorpay_subscription_id")
        if mandate_id:
            from app.services.payment_service import PaymentService

            await PaymentService(self.repo.db).cancel_autopay(mandate_id, at_cycle_end=True)
        updated = await self.repo.update_by_id(subscription_id, self._auto_pay_off_fields(sub))
        return _with_effective_status(updated or sub)

    async def upgrade(self, customer_id: str, subscription_id: str, new_plan_id: str) -> dict:
        """Mid-cycle plan switch, free of charge: the billing cycle (end_date)
        doesn't reset, the new plan's allowance applies less the washes
        already used, and a plan that costs more is refused (there is no
        pay-the-difference flow, so it would be a free upgrade)."""
        sub = await self.repo.find_by_id(subscription_id)
        if not sub or sub["customer_id"] != customer_id:
            raise NotFoundException("Subscription not found")
        if sub["status"] != SubscriptionStatus.ACTIVE.value:
            raise BadRequestException("Only an active subscription can be upgraded")
        if sub.get("custom_plan_id"):
            raise BadRequestException("A custom plan is changed by your service center — please call them.")

        current_plan = await self.plan_repo.find_by_id(sub["plan_id"])
        allowed_upgrades = (current_plan or {}).get("upgrade_to_plan_ids") or []
        if new_plan_id not in allowed_upgrades:
            raise ForbiddenException("This plan can't be upgraded to the one you picked — check what upgrades are available.")

        new_plan = await self.plan_repo.find_by_id(new_plan_id)
        if not new_plan or not new_plan.get("is_active") or new_plan.get("plan_type") in HIDDEN_PLAN_TYPES:
            raise NotFoundException("New plan not found or inactive")
        if sub.get("society_id"):
            raise BadRequestException("A society plan is changed by your society manager.")

        # The new plan must still be able to redeem THIS pass: its wash on the
        # new plan's menu and its vehicle type among the new plan's types —
        # otherwise the pass would carry washes no booking could ever spend
        # (or that only plan_consumption's legacy path would wrongly accept).
        new_menu = new_plan.get("included_service_ids") or []
        if sub.get("service_id") and new_menu and sub["service_id"] not in new_menu:
            raise BadRequestException("That plan doesn't cover the wash this pass is for — pick another plan.")
        new_types = new_plan.get("vehicle_types") or []
        if sub.get("vehicle_type") and new_types and sub["vehicle_type"] not in new_types:
            raise BadRequestException("That plan isn't sold for this pass's vehicle type — pick another plan.")

        # An upgrade is not charged (no difference-payment flow exists), so
        # it must never be worth more than what was paid: a pricier plan is
        # bought outright when this one ends. Same price functions the
        # checkout uses, for this pass's own vehicle type and service.
        def _price(plan_doc: dict, service_doc: dict | None) -> float:
            vt = sub.get("vehicle_type")
            return resolve_pass_price(plan_doc, service_doc, vt) if service_doc else resolve_plan_price(plan_doc, vt)

        service_doc = await self.service_repo.find_by_id(sub["service_id"]) if sub.get("service_id") else None
        if _price(new_plan, service_doc) > _price(current_plan or {}, service_doc):
            raise BadRequestException("This plan costs more than your current one — buy it once your current plan ends.")

        category_quotas = dict(new_plan.get("category_quotas") or {})
        total = sum(category_quotas.values()) if category_quotas else new_plan["total_service_count"]
        # An auto-pay mandate is priced for the OLD plan at Razorpay and
        # can't be re-priced in place — leaving it live would re-bill the
        # cheaper plan forever. Retire it at the end of the paid cycle and
        # tell the caller, so the UI can offer auto-pay again on the new plan.
        mandate_id = sub.get("razorpay_subscription_id")
        auto_pay_retired = False
        if mandate_id and sub.get("auto_renew"):
            from app.services.payment_service import PaymentService

            await PaymentService(self.repo.db).cancel_autopay(mandate_id, at_cycle_end=True)
            auto_pay_retired = True

        # Washes already used this cycle stay used — per category too:
        # resetting to the new plan's full allowance let two plans be
        # switched back and forth to refill the quota for free (PASS-2).
        # Guarded on the doc it was computed from, like commit_consumption,
        # so a booking spending a wash in between is never written over.
        for _ in range(5):
            data = {
                "plan_id": new_plan_id,
                **self._upgraded_counts(sub, category_quotas, total),
                **({"auto_renew": False, "razorpay_subscription_id": None} if auto_pay_retired else {}),
            }
            updated = await self.repo.update_if(
                subscription_id, {"updated_at": sub["updated_at"], "status": SubscriptionStatus.ACTIVE.value}, data
            )
            if updated is not None:
                break
            sub = await self.repo.find_by_id(subscription_id)
            if not sub or sub["status"] != SubscriptionStatus.ACTIVE.value:
                raise BadRequestException("Only an active subscription can be upgraded")
        else:
            raise BadRequestException("Couldn't switch this plan right now — please try again.")
        result = _with_effective_status(updated)
        result["auto_pay_retired"] = auto_pay_retired
        return result

    @staticmethod
    def _upgraded_counts(sub: dict, quotas: dict, total: int) -> dict:
        """The new plan's allowance less what this cycle already used:
        category -> category per category (new remaining = max(0, new quota
        − used in that category)); a flat count -> categories takes the used
        washes off the categories in a fixed order; anything -> flat takes
        them off the flat total. Never more than the new plan's quota."""
        old_totals = sub.get("total_by_category") or {}
        old_remaining = sub.get("remaining_by_category") or {}
        flat_used = max(0, int(sub.get("total_service_count") or 0) - int(sub.get("remaining_service_count") or 0))
        if not quotas:
            return {
                "total_service_count": total, "remaining_service_count": max(0, int(total) - flat_used),
                "total_by_category": {}, "remaining_by_category": {},
            }
        remaining: dict[str, int] = {}
        if old_totals:
            for cat, quota in quotas.items():
                used = max(0, int(old_totals.get(cat, 0)) - int(old_remaining.get(cat, 0)))
                remaining[cat] = max(0, int(quota) - used)
        else:
            left = flat_used
            for cat in sorted(quotas):
                take = min(left, int(quotas[cat]))
                remaining[cat] = int(quotas[cat]) - take
                left -= take
        return {
            "total_service_count": total, "remaining_service_count": sum(remaining.values()),
            "total_by_category": dict(quotas), "remaining_by_category": remaining,
        }

    async def cancel(self, customer_id: str, subscription_id: str) -> dict:
        sub = await self.repo.find_by_id(subscription_id)
        if not sub or sub["customer_id"] != customer_id:
            raise NotFoundException("Subscription not found")
        if sub.get("society_id"):
            raise BadRequestException("A society plan is cancelled by your society manager — please call them.")
        if sub.get("custom_plan_id"):
            raise BadRequestException("A custom plan is cancelled by your service center — please call them.")
        # Cancelling the plan must also stop the money: an auto-pay mandate
        # left alive would keep charging a card the customer just killed.
        # Immediate (not at-cycle-end) — they asked for it to stop now.
        mandate_id = sub.get("razorpay_subscription_id")
        if mandate_id and sub.get("auto_renew"):
            from app.services.payment_service import PaymentService

            await PaymentService(self.repo.db).cancel_autopay(mandate_id, at_cycle_end=False)
        updated = await self.repo.update_by_id(
            subscription_id, {"status": SubscriptionStatus.CANCELLED.value, "auto_renew": False}
        )
        await release_pass_claims_for(self.repo.db, [(sub, pass_claim_keys_of(sub))])
        return _with_effective_status(updated)

    async def list_all_for_admin(self, page: int, page_size: int):
        items, total = await self.repo.list_all(page, page_size)
        return _with_effective_statuses(items), total

    # ------------------------------------------------------------------
    # Reports (manager Subscribers page, admin Purchased plans page,
    # plan-revenue KPIs). KPIs are aggregated on the server over every
    # matching subscription; only one page of detail rows is ever loaded.
    # ------------------------------------------------------------------

    @staticmethod
    def _effective_status_expr(now: datetime) -> dict:
        """Mongo twin of _with_effective_status: a stored-"active" pass whose
        end_date has passed reads as "expired"; a "scheduled" renewal whose
        start has come (promotion not stored yet) reads as "active" — one
        still waiting for its start stays "scheduled" (its own KPI group)."""
        active = SubscriptionStatus.ACTIVE.value
        started = {"$and": [{"$eq": ["$status", PASS_SCHEDULED]}, {"$lte": [{"$ifNull": ["$start_date", now]}, now]}]}
        status_now = {"$cond": [started, active, "$status"]}
        lapsed = {"$and": [
            {"$eq": [status_now, active]}, {"$ifNull": ["$end_date", False]}, {"$lt": ["$end_date", now]},
            # A society pass inside a granted extension is still usable.
            {"$not": [{"$gt": ["$extended_until", now]}]},
        ]}
        return {"$cond": [lapsed, SubscriptionStatus.EXPIRED.value, status_now]}

    @staticmethod
    def _status_filter(status: str | None, now: datetime) -> dict:
        active = SubscriptionStatus.ACTIVE.value
        if not status or status == "all":
            return {}
        if status == "active":
            return {"$or": [
                {"status": active, "$or": [{"end_date": None}, {"end_date": {"$gte": now}}, {"extended_until": {"$gte": now}}]},
                # A renewal whose start has come reads active before the
                # promotion sweep stores it.
                {"status": PASS_SCHEDULED, "start_date": {"$lte": now}, "end_date": {"$gte": now}},
            ]}
        if status == PASS_SCHEDULED:
            # Renewals paid ahead, waiting for the old period to end.
            return {"status": PASS_SCHEDULED, "start_date": {"$gt": now}}
        if status == "expiring":
            # days_left = (end - now).days <= 14  <=>  end < now + 15 days
            return {"status": active, "end_date": {"$gte": now, "$lt": now + timedelta(days=15)}}
        if status == "expired":
            return {"$or": [
                {"status": SubscriptionStatus.EXPIRED.value},
                {"status": active, "end_date": {"$lt": now}, "extended_until": {"$not": {"$gte": now}}},
            ]}
        return {"status": status}

    async def _search_filter(self, search: str | None) -> dict:
        """Customer name/phone or plan name -> a filter on the subscription
        rows. Customers are resolved through the users collection first
        (subscriptions carry no names)."""
        from app.repositories.base_repository import build_search_filter

        text = (search or "").strip()
        if not text:
            return {}
        digits = "".join(ch for ch in text if ch.isdigit())
        if len(digits) == 12 and digits.startswith("91"):
            text = digits[2:]
        users = await self.user_repo.collection.find(
            {"role": "customer", **build_search_filter(text, ["full_name", "phone", "email"])}, {"_id": 1}
        ).limit(500).to_list(length=500)
        plans = await self.plan_repo.collection.find(build_search_filter(text, ["name"]), {"_id": 1}).to_list(length=200)
        return {"$or": [
            {"customer_id": {"$in": [str(u["_id"]) for u in users]}},
            {"plan_id": {"$in": [str(p["_id"]) for p in plans]}},
        ]}

    async def _page_of_subscriptions(self, query: dict, page: int, page_size: int) -> tuple[list[dict], int]:
        total, docs = await asyncio.gather(
            self.repo.collection.count_documents(query),
            self.repo.collection.find(query).sort("created_at", -1).skip((page - 1) * page_size).limit(page_size).to_list(length=page_size),
        )
        return _with_effective_statuses(docs), total

    async def _kpi_groups(self, base: dict, now: datetime) -> list[dict]:
        """Per-plan counters over every subscription matching `base`."""
        active = SubscriptionStatus.ACTIVE.value
        effective = self._effective_status_expr(now)
        no_center = {"$not": [{"$and": ["$service_center_id", {"$ne": ["$service_center_id", ""]}]}]}
        paid = {"$cond": [
            {"$and": [{"$eq": [{"$ifNull": ["$amount_paid", None]}, None]}, no_center]},
            {"$ifNull": ["$purchased_price", 0]},
            {"$ifNull": ["$amount_paid", 0]},
        ]}
        return await self.repo.collection.aggregate([
            {"$match": base},
            {"$project": {"plan_id": 1, "end_date": 1, "eff": effective, "paid": paid}},
            {"$group": {
                "_id": "$plan_id",
                "total": {"$sum": 1},
                "active": {"$sum": {"$cond": [{"$eq": ["$eff", active]}, 1, 0]}},
                "expired": {"$sum": {"$cond": [{"$eq": ["$eff", SubscriptionStatus.EXPIRED.value]}, 1, 0]}},
                "scheduled": {"$sum": {"$cond": [{"$eq": ["$eff", PASS_SCHEDULED]}, 1, 0]}},
                "expiring": {"$sum": {"$cond": [{"$and": [
                    {"$eq": ["$eff", active]}, {"$ifNull": ["$end_date", False]}, {"$lt": ["$end_date", now + timedelta(days=15)]},
                ]}, 1, 0]}},
                "revenue": {"$sum": "$paid"},
            }},
        ], allowDiskUse=True).to_list(length=None)

    async def _plan_names(self, plan_ids) -> dict[str, str]:
        oids = [ObjectId(p) for p in plan_ids if isinstance(p, str) and ObjectId.is_valid(p)]
        if not oids:
            return {}
        return {str(p["_id"]): p.get("name") for p in await self.plan_repo.collection.find({"_id": {"$in": oids}}, {"name": 1}).to_list(length=len(oids))}

    async def _type_and_service_names(self, type_ids, service_ids) -> tuple[dict[str, str], dict[str, str]]:
        """Display names for one page of plan rows — the car type a pass
        was bought for and the one wash it covers. Two batched lookups,
        never one per row; a since-deleted type/service still resolves, so
        an old sale keeps its label."""

        async def names(collection, ids) -> dict[str, str]:
            oids = [ObjectId(i) for i in {i for i in ids if isinstance(i, str) and i} if ObjectId.is_valid(i)]
            if not oids:
                return {}
            docs = await collection.find({"_id": {"$in": oids}}, {"name": 1}).to_list(length=len(oids))
            return {str(d["_id"]): d.get("name") or "" for d in docs}

        type_names, service_names = await asyncio.gather(
            names(self.vehicle_type_repo.collection, type_ids), names(self.service_repo.collection, service_ids)
        )
        return type_names, service_names

    async def _holders(self, subs: list[dict]) -> dict[str, dict]:
        ids = [ObjectId(s["customer_id"]) for s in subs if ObjectId.is_valid(s.get("customer_id") or "")]
        if not ids:
            return {}
        return {str(u["_id"]): u for u in await self.user_repo.collection.find({"_id": {"$in": ids}}, {"full_name": 1, "phone": 1}).to_list(length=len(ids))}

    @staticmethod
    def _amount_paid(s: dict):
        # What the customer actually PAID — never just the plan's list
        # price. A manager-sold plan almost always differs from
        # purchased_price (a discount, a coupon); a genuine self-serve
        # purchase never stamps amount_paid, so purchased_price IS what they
        # paid in that one case.
        amount_paid = s.get("amount_paid")
        if amount_paid is None and not s.get("service_center_id"):
            amount_paid = s.get("purchased_price")
        return amount_paid

    @staticmethod
    def _meta(page: int, page_size: int, total: int) -> dict:
        return {"page": page, "page_size": page_size, "total": total, "total_pages": max((total + page_size - 1) // page_size, 1) if total else 0}

    async def center_overview(
        self, service_center_id: str, actor_role: str, actor_center_id: str | None, *,
        page: int = 1, page_size: int = 50, search: str | None = None, status: str | None = None,
        plan_id: str | None = None,
    ) -> dict:
        """The manager's subscription dashboard: every subscription held by
        a customer this center has ever served (any booking dispatched
        here, not only plan redemptions — a plan-holder who hasn't
        redeemed yet is exactly who the manager wants to call), rolled up
        into KPIs (active / expiring within 14 days / expired) plus a
        per-plan breakdown over ALL of them, and one page of detail rows
        (filterable by `status` — active/expiring/expired/cancelled/scheduled — and
        searchable by customer name/phone or plan name)."""
        from app.core.authz import ensure_own_center

        ensure_own_center(actor_role, actor_center_id, service_center_id)

        customer_ids = await self.repo.db.bookings.distinct(
            "customer_id", {"service_center_id": service_center_id, "is_deleted": {"$ne": True}}
        )
        # A plan a manager grants/sells directly (cash / WhatsApp link /
        # auto-pay) is tagged with their own center at creation — that
        # customer may never have a booking here at all yet, so the
        # booking-history list above alone would hide them. Union both so
        # a just-assigned plan shows up immediately, not only after the
        # customer's first visit.
        base: dict = {"is_deleted": {"$ne": True}, "$or": [{"service_center_id": service_center_id}]}
        if customer_ids:
            # A customer this center served may also hold a plan ANOTHER
            # center sold (its price, discount, who collected the cash): a
            # manager sees only self-serve plans (no center) besides their
            # own center's sales — the list_for_customer / customer-360 rule
            # (PASS-6). An admin still sees everything those customers hold.
            served: dict = {"customer_id": {"$in": customer_ids}}
            if actor_role != "admin":
                served["service_center_id"] = {"$in": [None, ""]}
            base["$or"].append(served)
        now = now_ist()
        filters = [f for f in (base, self._status_filter(status, now), {"plan_id": plan_id} if plan_id else {}, await self._search_filter(search)) if f]
        query = {"$and": filters} if len(filters) > 1 else base

        groups, (subs, total) = await asyncio.gather(self._kpi_groups(base, now), self._page_of_subscriptions(query, page, page_size))
        plan_names = await self._plan_names({g["_id"] for g in groups} | {s.get("plan_id") for s in subs})
        plan_counts: dict[str, int] = {}
        for g in groups:
            if g["active"]:
                name = plan_names.get(g["_id"] or "") or "Unknown plan"
                plan_counts[name] = plan_counts.get(name, 0) + g["active"]
        kpis = {
            "total": sum(g["total"] for g in groups),
            "active": sum(g["active"] for g in groups),
            "expiring_soon": sum(g["expiring"] for g in groups),
            "expired": sum(g["expired"] for g in groups),
            # Renewals paid ahead (custom-plan PLANS-2): not usable until
            # their start, so their own group — never inside "active".
            "scheduled": sum(g["scheduled"] for g in groups),
        }

        users = await self._holders(subs)
        subs = await self._with_pass_details(subs)
        type_names, service_names = await self._type_and_service_names(
            [s.get("vehicle_type") for s in subs], [s.get("service_id") for s in subs]
        )
        rows = []
        for s in subs:
            holder = users.get(s["customer_id"], {})
            end_date = s.get("end_date")
            days_left = None
            if end_date:
                # serialize_doc already stamped end_date as a tz-aware ISO
                # string (COMPUTED_INSTANT_KEYS) — parse it back; only a
                # still-naive value carries UTC semantics.
                end = datetime.fromisoformat(end_date) if isinstance(end_date, str) else end_date
                if end.tzinfo is None:
                    end = end.replace(tzinfo=timezone.utc)
                days_left = (end - now).days
            rows.append({
                "subscription_id": s["id"],
                "customer_id": s["customer_id"],
                "customer_name": holder.get("full_name", "Unknown"),
                "customer_phone": holder.get("phone"),
                "plan_id": s.get("plan_id"),
                "plan_name": plan_names.get(s.get("plan_id") or "") or "Unknown plan",
                "status": s.get("effective_status"),
                "vehicle_type": s.get("vehicle_type"),
                "vehicle_type_name": type_names.get(s.get("vehicle_type") or ""),
                # The one wash a monthly pass covers (None on a legacy
                # tier plan, which covers the plan's whole list).
                "service_id": s.get("service_id"),
                "service_name": service_names.get(s.get("service_id") or ""),
                "purchased_price": s.get("purchased_price"),
                "amount_paid": self._amount_paid(s),
                "discount_amount": s.get("discount_amount"),
                "coupon_code": s.get("coupon_code"),
                "payment_method": s.get("payment_method"),
                "remaining_service_count": s.get("remaining_service_count"),
                "total_service_count": s.get("total_service_count"),
                "start_date": s.get("start_date"),
                "end_date": s.get("end_date"),
                "days_left": days_left,
                **_car_columns(s),
                **_extension_columns(s),
            })
        return {
            "kpis": kpis,
            "plan_breakdown": sorted(
                ({"plan_name": name, "active_count": count} for name, count in plan_counts.items()),
                key=lambda x: (-x["active_count"], x["plan_name"]),
            ),
            "plans": sorted(({"plan_id": pid, "plan_name": name} for pid, name in plan_names.items()), key=lambda p: p["plan_name"] or ""),
            "rows": rows,
            "meta": self._meta(page, page_size, total),
        }

    async def admin_overview(
        self, *, page: int = 1, page_size: int = 50, search: str | None = None,
        status: str | None = None, plan_id: str | None = None,
    ) -> dict:
        """Every plan ever purchased or granted, platform-wide — the admin's
        answer to "who bought what, for how much". Unlike center_overview
        (one manager's own center, booking-history-derived customer list)
        this has no center scope at all. KPIs and the per-plan breakdown
        cover every subscription; rows are one filtered/searched page."""
        base = {"is_deleted": {"$ne": True}}
        now = now_ist()
        filters = [f for f in (base, self._status_filter(status, now), {"plan_id": plan_id} if plan_id else {}, await self._search_filter(search)) if f]
        query = {"$and": filters} if len(filters) > 1 else base

        groups, (subs, total) = await asyncio.gather(self._kpi_groups(base, now), self._page_of_subscriptions(query, page, page_size))
        plan_names = await self._plan_names({g["_id"] for g in groups} | {s.get("plan_id") for s in subs})
        plan_counts: dict[str, int] = {}
        for g in groups:
            name = plan_names.get(g["_id"] or "") or "Unknown plan"
            plan_counts[name] = plan_counts.get(name, 0) + g["total"]
        kpis = {
            "total": sum(g["total"] for g in groups),
            "active": sum(g["active"] for g in groups),
            "expired": sum(g["expired"] for g in groups),
            "scheduled": sum(g["scheduled"] for g in groups),
            "total_revenue": round(sum(float(g["revenue"] or 0) for g in groups), 2),
        }

        users = await self._holders(subs)
        subs = await self._with_pass_details(subs)
        center_ids = {s["service_center_id"] for s in subs if s.get("service_center_id")}
        centers = {
            str(c["_id"]): c.get("name")
            for c in await self.repo.db.service_centers.find(
                {"_id": {"$in": [ObjectId(c) for c in center_ids if ObjectId.is_valid(c)]}}, {"name": 1}
            ).to_list(length=200)
        } if center_ids else {}
        type_names, service_names = await self._type_and_service_names(
            [s.get("vehicle_type") for s in subs], [s.get("service_id") for s in subs]
        )
        rows = []
        for s in subs:
            holder = users.get(s["customer_id"], {})
            rows.append({
                "subscription_id": s["id"],
                "customer_id": s["customer_id"],
                "customer_name": holder.get("full_name", "Unknown"),
                "customer_phone": holder.get("phone"),
                "plan_id": s.get("plan_id"),
                "plan_name": plan_names.get(s.get("plan_id") or "") or "Unknown plan",
                "status": s.get("effective_status"),
                "vehicle_type": s.get("vehicle_type"),
                "vehicle_type_name": type_names.get(s.get("vehicle_type") or ""),
                "service_id": s.get("service_id"),
                "service_name": service_names.get(s.get("service_id") or ""),
                "amount_paid": self._amount_paid(s),
                "discount_amount": s.get("discount_amount"),
                "coupon_code": s.get("coupon_code"),
                "payment_method": s.get("payment_method"),
                "auto_renew": s.get("auto_renew", False),
                "service_center_id": s.get("service_center_id"),
                "service_center_name": centers.get(s.get("service_center_id") or ""),
                "remaining_service_count": s.get("remaining_service_count"),
                "total_service_count": s.get("total_service_count"),
                "start_date": s.get("start_date"),
                "end_date": s.get("end_date"),
                **_car_columns(s),
                **_extension_columns(s),
            })
        return {
            "kpis": kpis,
            "plan_breakdown": sorted(
                ({"plan_name": name, "count": count} for name, count in plan_counts.items()),
                key=lambda x: (-x["count"], x["plan_name"]),
            ),
            "plans": sorted(({"plan_id": pid, "plan_name": name} for pid, name in plan_names.items()), key=lambda p: p["plan_name"] or ""),
            "rows": rows,
            "meta": self._meta(page, page_size, total),
        }

    @staticmethod
    def _center_orders(match: dict, service_center_id: str) -> list[dict]:
        """payment_orders in `match` that belong to one center. A manager-
        issued link or auto-pay order stamps service_center_id directly; a
        cash sale, an autopay renewal row and a self-serve autopay checkout
        never do — for those the center lives on the subscription the order
        settled into, joined here server-side."""
        own_center = {"$and": ["$service_center_id", {"$ne": ["$service_center_id", ""]}]}
        return [
            {"$match": match},
            {"$addFields": {"_sub_oid": {"$cond": [
                own_center, None,
                {"$convert": {"input": "$subscription_id", "to": "objectId", "onError": None, "onNull": None}},
            ]}}},
            {"$lookup": {
                "from": "user_subscriptions", "localField": "_sub_oid", "foreignField": "_id",
                "pipeline": [{"$project": {"service_center_id": 1}}], "as": "_sub",
            }},
            {"$match": {"$expr": {"$eq": [
                {"$cond": [own_center, "$service_center_id", {"$first": "$_sub.service_center_id"}]},
                service_center_id,
            ]}}},
        ]

    async def center_plan_revenue(self, service_center_id: str, s: datetime, e: datetime) -> tuple[float, int]:
        """This center's plan revenue + count of plans sold in the window —
        the manager-KPI sibling of KpiService._plan_revenue (platform-
        wide), resolved per order through _center_orders."""
        match = {"purpose": {"$in": PLAN_ORDER_PURPOSES}, "status": "paid", "created_at": {"$gte": s, "$lt": e}}
        rows = await self.repo.db.payment_orders.aggregate([
            *self._center_orders(match, service_center_id),
            {"$group": {"_id": None, "paise": {"$sum": {"$ifNull": ["$amount_paise", 0]}}, "n": {"$sum": 1}}},
        ]).to_list(length=1)
        if not rows:
            return 0.0, 0
        return round(rows[0]["paise"] / 100, 2), rows[0]["n"]

    async def center_plan_refunds(self, service_center_id: str, s: datetime, e: datetime) -> float:
        """Custom-plan car refunds of this center's carts in the window —
        shown beside center_plan_revenue (which stays gross)."""
        return (await custom_plan_refunds(self.repo.db, s, e, service_center_id))["amount"]

    async def plan_purchases(
        self, s: datetime, e: datetime, page: int, page_size: int, service_center_id: str | None = None,
        extra: dict | None = None,
    ) -> tuple[list[dict], int]:
        """Every individual plan PAYMENT settled in the window — literally
        the same payment_orders match KpiService._plan_revenue sums, so
        this list's total and that dashboard tile's number always agree.
        Deliberately NOT admin_overview: that lists every subscription
        ever (all-time, no period) and sums a different field
        (user_subscriptions.amount_paid, which a manager-granted FREE plan
        has but a payment_orders row never will — the two would silently
        disagree with the revenue tile if reused here).

        service_center_id (optional): scopes to one center's own sales —
        a manager's own drill-down, resolved per order by _center_orders.

        extra (optional): equality filters on the order itself (plan_id /
        service_id / vehicle_type) — the admin KPI explorer's plan chart
        drill-down, same slice KpiService.explorer counted."""
        match = {"purpose": {"$in": PLAN_ORDER_PURPOSES}, "status": "paid", "created_at": {"$gte": s, "$lt": e}, **(extra or {})}
        if service_center_id is None:
            total = await self.repo.db.payment_orders.count_documents(match)
            orders = (
                await self.repo.db.payment_orders.find(match)
                .sort("created_at", -1)
                .skip((page - 1) * page_size)
                .limit(page_size)
                .to_list(length=page_size)
            )
        else:
            facet = await self.repo.db.payment_orders.aggregate([
                *self._center_orders(match, service_center_id),
                {"$sort": {"created_at": -1}},
                {"$facet": {
                    "total": [{"$count": "n"}],
                    "page": [{"$skip": (page - 1) * page_size}, {"$limit": page_size}, {"$project": {"_sub": 0, "_sub_oid": 0}}],
                }},
            ], allowDiskUse=True).to_list(length=1)
            total = facet[0]["total"][0]["n"] if facet and facet[0]["total"] else 0
            orders = facet[0]["page"] if facet else []
        if not orders:
            return [], total

        customer_ids = [ObjectId(o["customer_id"]) for o in orders if o.get("customer_id") and ObjectId.is_valid(o["customer_id"])]
        plan_ids = [ObjectId(o["plan_id"]) for o in orders if o.get("plan_id") and ObjectId.is_valid(o["plan_id"])]
        users = (
            {
                str(u["_id"]): u
                for u in await self.user_repo.collection.find(
                    {"_id": {"$in": customer_ids}}, {"full_name": 1, "phone": 1}
                ).to_list(length=len(customer_ids))
            }
            if customer_ids
            else {}
        )
        plans = (
            {str(p["_id"]): p for p in await self.plan_repo.collection.find({"_id": {"$in": plan_ids}}).to_list(length=len(plan_ids))}
            if plan_ids
            else {}
        )
        # Car type + the one wash the pass covers. Stamped on the order by
        # every checkout path; an order that predates that (or a renewal
        # ledger row) falls back to the subscription it settled into —
        # one batched lookup for the whole page, only for the rows missing it.
        missing_sub_ids = [
            ObjectId(o["subscription_id"])
            for o in orders
            if (not o.get("vehicle_type") or not o.get("service_id")) and ObjectId.is_valid(o.get("subscription_id") or "")
        ]
        subs_by_id = (
            {
                str(sub["_id"]): sub
                for sub in await self.repo.collection.find(
                    {"_id": {"$in": missing_sub_ids}}, {"vehicle_type": 1, "service_id": 1}
                ).to_list(length=len(missing_sub_ids))
            }
            if missing_sub_ids
            else {}
        )

        def _pass_target(o: dict) -> tuple[str | None, str | None]:
            sub = subs_by_id.get(o.get("subscription_id") or "", {})
            return o.get("vehicle_type") or sub.get("vehicle_type"), o.get("service_id") or sub.get("service_id")

        targets = {str(o["_id"]): _pass_target(o) for o in orders}
        # A custom cart's cars refunded to the wallet since (PLANS-2) —
        # `amount` stays what was paid; `refunded_amount` / `net_amount`
        # sit beside it.
        refunded = await custom_plan_refunded_by_cart(
            self.repo.db, [o.get("custom_plan_id") for o in orders if o.get("purpose") == "custom_plan"]
        )
        type_names, service_names = await self._type_and_service_names(
            [t for t, _ in targets.values()], [sv for _, sv in targets.values()]
        )

        # payment_orders.kind -> a label a manager/admin actually reads on
        # screen (never "manager_cash" verbatim).
        payment_method_labels = {"cash": "Cash", "manager_cash": "Cash", "link": "Online", "autopay": "Online (auto-pay)"}
        rows = []
        for o in orders:
            holder = users.get(o.get("customer_id") or "", {})
            plan = plans.get(o.get("plan_id") or "", {})
            vehicle_type, service_id = targets[str(o["_id"])]
            rows.append(
                serialize_doc(
                    {
                        "_id": o["_id"],
                        "customer_id": o.get("customer_id"),
                        "customer_name": holder.get("full_name", "Unknown"),
                        "customer_phone": holder.get("phone"),
                        "plan_id": o.get("plan_id"),
                        "plan_name": plan.get("name") or ("Custom plan" if o.get("purpose") == "custom_plan" else "Unknown plan"),
                        "vehicle_type": vehicle_type,
                        "vehicle_type_name": type_names.get(vehicle_type or ""),
                        "service_id": service_id,
                        "service_name": service_names.get(service_id or ""),
                        "amount": round((o.get("amount_paise") or 0) / 100, 2),
                        "discount": round((o.get("discount_paise") or 0) / 100, 2) if o.get("discount_paise") else None,
                        "coupon_code": o.get("coupon_code"),
                        "payment_method": payment_method_labels.get(o.get("kind") or "", o.get("kind")),
                        "subscription_id": o.get("subscription_id"),
                        # A custom multi-car plan is ONE payment for all its
                        # cars (one row, counted once).
                        "custom_plan_id": o.get("custom_plan_id"),
                        "car_count": o.get("car_count"),
                        "refunded_amount": refunded.get(o.get("custom_plan_id") or "", 0.0),
                        "net_amount": round(
                            (o.get("amount_paise") or 0) / 100 - refunded.get(o.get("custom_plan_id") or "", 0.0), 2
                        ),
                        "created_at": o.get("created_at"),
                    }
                )
            )
        return rows, total

    async def _get_active_subscription(self, subscription_id: str) -> dict:
        sub = await self.repo.find_by_id(subscription_id)
        if not sub:
            raise NotFoundException("Subscription not found")
        if sub["status"] == PASS_SCHEDULED:
            if not pass_started(sub):
                start = from_stored(_as_utc(sub["start_date"]))
                raise BadRequestException(
                    f"This plan starts on {day_label(start)} — book its washes from that day."
                )
            # Its start has come: promote it now (guarded, idempotent) — the
            # sweep may not have run yet (it waits for daytime).
            await self.repo.update_if(subscription_id, {"status": PASS_SCHEDULED}, {"status": SubscriptionStatus.ACTIVE.value})
            sub = await self.repo.find_by_id(subscription_id)
        if sub["status"] != SubscriptionStatus.ACTIVE.value:
            raise BadRequestException("This subscription is not active")
        # end_date was computed as an absolute instant (now_ist() + timedelta), so
        # when Mongo hands it back naive it represents UTC, not IST wall-clock —
        # tag it as UTC here; only *user-entered* wall-clock values (like a
        # booking's scheduled_slot) get tagged as IST on read.
        # A society pass inside a granted extension is still usable (its
        # remaining washes only) — pass_usable_until covers both.
        until = pass_usable_until(sub)
        if until is not None and until < now_ist():
            # Guarded: never overwrite a renewal/extension that landed since
            # this read (it would expire a pass that was just made usable).
            await self.repo.update_if(
                subscription_id, {"status": SubscriptionStatus.ACTIVE.value, "end_date": sub["end_date"], "extended_until": sub.get("extended_until")},
                {"status": SubscriptionStatus.EXPIRED.value},
            )
            raise BadRequestException("This subscription has expired")
        return sub


# -- Pass lifecycle sweeps (called from main._reminder_loop) ----------------

# Live bookings — a pass with one of these already has its next wash on the way.
_LIVE_BOOKING_STATUSES = ["awaiting_payment", "pending", "assigned", "captain_on_the_way", "service_started", "rescheduled"]

# Mongo twin of renews_automatically(): auto-pay on and a mandate on file.
_NOT_AUTO_RENEWING = {"$or": [{"auto_renew": {"$ne": True}}, {"razorpay_subscription_id": {"$in": [None, ""]}}]}


async def find_subscriptions_expiring_soon(db, days: int = 2, limit: int = 200) -> list[dict]:
    """Active passes that end within `days` and haven't had their heads-up
    yet — one message per pass, sent by the reminder loop, soonest first.
    A pass on auto-pay is skipped: it isn't ending, it's renewing, and
    "renew to keep going" would be wrong."""
    now = datetime.now(timezone.utc)
    return await db.user_subscriptions.find(
        {
            "status": SubscriptionStatus.ACTIVE.value,
            "is_deleted": {"$ne": True},
            "expiry_reminder_sent": {"$ne": True},
            "end_date": {"$gt": now, "$lte": now + timedelta(days=days)},
            **_NOT_AUTO_RENEWING,
        }
    ).sort("end_date", 1).limit(limit).to_list(length=limit)


async def renewal_lined_up(db, sub: dict) -> datetime | None:
    """When the next period ALREADY PAID for this pass starts — a custom-plan
    renewal (PLANS-2: `renewed_by_custom_plan_id` / `renewed_by_subscription_id`
    on the old pass). None when no renewal is lined up, or the renewal pass
    was since refunded / cancelled. The pass-ending and pass-ended notes use
    it to say "Your plan continues on 18 Oct 2026 — next period already
    paid" instead of nudging a renewal the customer already bought."""
    cid = sub.get("renewed_by_custom_plan_id")
    sid = sub.get("renewed_by_subscription_id")
    if not cid and not sid:
        return None
    projection = {"status": 1, "start_date": 1, "is_deleted": 1}
    successor = None
    if sid and ObjectId.is_valid(str(sid)):
        successor = await db.user_subscriptions.find_one({"_id": ObjectId(str(sid))}, projection)
    if successor is None and cid:
        successor = await db.user_subscriptions.find_one(
            {"renewal_of_subscription_id": str(sub["_id"]), "custom_plan_id": cid}, projection,
        )
        if successor is None:
            # Marked but its pass not created yet (an activation in flight):
            # the renewal starts the day after this pass's Last Booking Day.
            return next_period_start(sub)
    if successor is None or successor.get("is_deleted") or successor.get("status") not in LIVE_PASS_STATUSES:
        return None
    return _as_utc(successor.get("start_date")) or next_period_start(sub)


def renewal_continues_text(start: datetime) -> str:
    return f"Your plan continues on {day_label(from_stored(start))} — next period already paid."


async def mark_expiry_reminder_sent(db, subscription_id: str) -> None:
    await db.user_subscriptions.update_one({"_id": ObjectId(subscription_id)}, {"$set": {"expiry_reminder_sent": True}})


async def find_subscriptions_ended(db, limit: int = 100, exclude_ids=()) -> list[dict]:
    """Passes still stored "active" whose end_date has passed — the loop
    tells each customer once, then flips that one pass to expired
    (mark_subscription_expired). Oldest first; `exclude_ids` lets a pass
    page past rows it already tried without re-fetching a failing one.

    An auto-pay pass is left alone for AUTOPAY_GRACE past its end (the
    charge is usually minutes away), AUTOPAY_RETRY_GRACE while Razorpay is
    retrying a failed charge — a renewal that lands in that window just
    refreshes it, and the customer never hears "your pass has ended".

    Also the sweep that promotes scheduled custom-plan renewals whose start
    has come (promote_scheduled_passes: guarded, idempotent)."""
    try:
        await promote_scheduled_passes(db)
    except Exception:  # noqa: BLE001 — never block the ended-pass notes
        import logging

        logging.getLogger(__name__).exception("Could not promote scheduled passes")
    now = datetime.now(timezone.utc)
    query: dict = {
        "status": SubscriptionStatus.ACTIVE.value,
        "end_date": {"$lt": now},
        # A society pass inside a granted extension isn't over yet.
        "extended_until": {"$not": {"$gt": now}},
        "$or": [
            *_NOT_AUTO_RENEWING["$or"],
            {"autopay_state": {"$ne": "pending"}, "end_date": {"$lt": now - AUTOPAY_GRACE}},
            {"end_date": {"$lt": now - AUTOPAY_RETRY_GRACE}},
        ],
    }
    if exclude_ids:
        query["_id"] = {"$nin": list(exclude_ids)}
    return await db.user_subscriptions.find(query).sort("end_date", 1).to_list(length=limit)


async def mark_subscription_expired(db, subscription_id: str) -> None:
    """Guarded on still-active so it never overwrites a status something
    else set meanwhile (a renewal, a cancel)."""
    now = datetime.now(timezone.utc)
    await db.user_subscriptions.update_one(
        {"_id": ObjectId(subscription_id), "status": SubscriptionStatus.ACTIVE.value, "end_date": {"$lt": now},
         "extended_until": {"$not": {"$gt": now}}},
        {"$set": {"status": SubscriptionStatus.EXPIRED.value, "updated_at": now}},
    )


async def find_passes_due_wash_reminder(db, limit: int = 200, expiring_days: int = 2) -> list[dict]:
    """Live passes with washes left and nothing booked on them — the ones a
    "3 washes left — book your next wash" nudge is for. Skips a pass that:
      - was reminded within WASH_REMINDER_EVERY;
      - was bought, renewed or booked on within WASH_REMINDER_QUIET_AFTER_USE
        (someone who just booked doesn't need telling);
      - ends within `expiring_days` (the "ends soon" notice covers it);
      - has an upcoming or in-progress booking already using it.
    Soonest-ending first. Every exclusion runs in the pipeline before the
    batch is cut, so passes behind a skipped one are still reached; the
    booking check is an indexed lookup (subscription_id + status)."""
    now = datetime.now(timezone.utc)
    quiet_floor = now - WASH_REMINDER_QUIET_AFTER_USE
    reminded_floor = now - WASH_REMINDER_EVERY
    pipeline = [
        {"$match": {
            "status": SubscriptionStatus.ACTIVE.value,
            "end_date": {"$gt": now + timedelta(days=expiring_days)},
            "is_deleted": {"$ne": True},
            "remaining_service_count": {"$gt": 0},
            "start_date": {"$lte": quiet_floor},
            "last_used_at": {"$not": {"$gt": quiet_floor}},
            "last_renewed_at": {"$not": {"$gt": quiet_floor}},
            "wash_reminder_sent_at": {"$not": {"$gt": reminded_floor}},
        }},
        {"$sort": {"end_date": 1}},
        {"$lookup": {
            "from": "bookings",
            "let": {"sid": {"$toString": "$_id"}},
            "pipeline": [
                {"$match": {
                    "$expr": {"$eq": ["$subscription_id", "$$sid"]},
                    "status": {"$in": _LIVE_BOOKING_STATUSES},
                    "is_deleted": {"$ne": True},
                }},
                {"$limit": 1},
                {"$project": {"_id": 1}},
            ],
            "as": "_live_booking",
        }},
        {"$match": {"_live_booking": {"$size": 0}}},
        {"$limit": limit},
        {"$project": {"_live_booking": 0}},
    ]
    return await db.user_subscriptions.aggregate(pipeline).to_list(length=limit)


async def mark_wash_reminder_sent(db, subscription_id: str) -> None:
    await db.user_subscriptions.update_one(
        {"_id": ObjectId(subscription_id)}, {"$set": {"wash_reminder_sent_at": datetime.now(timezone.utc)}}
    )


async def claim_used_up_notice(db, subscription_id: str) -> dict | None:
    """The "you've used all washes" notice goes out once per pass: this
    stamps it atomically and returns the pass only to the caller that won —
    and only when the pass really is spent and expired (an auto-pay pass at
    0 is still active, waiting for its refill)."""
    if not ObjectId.is_valid(subscription_id or ""):
        return None
    return await db.user_subscriptions.find_one_and_update(
        {
            "_id": ObjectId(subscription_id),
            "status": SubscriptionStatus.EXPIRED.value,
            "remaining_service_count": {"$lte": 0},
            "used_up_notice_sent_at": None,
            "is_deleted": {"$ne": True},
        },
        {"$set": {"used_up_notice_sent_at": datetime.now(timezone.utc)}},
    )
