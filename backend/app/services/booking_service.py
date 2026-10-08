import asyncio
import logging
import re
import secrets
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from bson import ObjectId
from pymongo import ReturnDocument
from motor.motor_asyncio import AsyncIOMotorDatabase
from pymongo.errors import DuplicateKeyError

from app.core.authz import account_switched_off, ensure_own_center
from app.core.exceptions import BadRequestException, ConflictException, ForbiddenException, NotFoundException
from app.core.ws_manager import manager as ws_manager
from app.models.enums import BookingStatus, NotificationType, PaymentMethod, PaymentStatus, UserStatus
from app.models.service_center import DEFAULT_WORKING_HOURS_END, DEFAULT_WORKING_HOURS_START
from app.repositories.address_repository import AddressRepository
from app.repositories.booking_repository import BookingRepository, BookingStatusHistoryRepository
from app.repositories.captain_location_repository import CaptainLocationRepository
from app.repositories.catalog_repository import ComboOfferRepository, ServiceRepository
from app.repositories.inventory_repository import InventoryRepository
from app.repositories.service_center_repository import ServiceCenterRepository
from app.repositories.slot_capacity_repository import DailyCapacityRepository, SlotCapacityRepository
from app.repositories.user_repository import UserRepository
from app.repositories.vehicle_repository import VehicleRepository
from app.repositories.vehicle_type_repository import VehicleTypeRepository
from app.utils.slots import format_slot_12h, generate_slots
from app.utils.text import normalize_plate
from app.schemas.booking_schema import (
    BookingGroupCreateRequest,
    BookingAssignCaptainRequest,
    BookingCancelRequest,
    BookingCreateRequest,
    BookingRescheduleRequest,
    BookingUpdateDetailsRequest,
    CaptainCancelRequest,
    GroupVehicleRequest,
    HeadingRequest,
    ManagerBookingCreateRequest,
    ManagerLogBookingRequest,
    PhotoCaptureRequest,
    QuickBookingRequest,
    ReassignCaptainRequest,
    ReportRiskRequest,
    VerifyVehicleRequest,
)
from app.schemas.profile_schema import AddressCreateRequest
from app.services.booking_policy_service import BookingPolicyService
from app.services.capacity_policy_service import CapacityPolicyService
from app.services import booking_money as bm
from app.services.coupon_service import CouponService
from app.services.customer_charge_service import CustomerChargeService, late_cancellation_quote
from app.services.money_service import MoneyService
from app.services.notification_service import NotificationService
from app.services.pricing_service import PricingService
from app.services.profile_service import AddressService, VehicleService, address_snapshot
from app.services.subscription_service import UserSubscriptionService
from app.services.wallet_service import WalletService
from app.utils.geo import haversine_km
from app.utils.money import round_rupees, split_whole_rupees
from app.utils.serializers import serialize_doc, serialize_list
from app.utils.timezone import from_stored, now_ist, to_ist

logger = logging.getLogger(__name__)

START_WINDOW_MINUTES = 30
ACTIVE_CAPTAIN_STATUSES = {BookingStatus.ASSIGNED.value, BookingStatus.CAPTAIN_ON_THE_WAY.value, BookingStatus.SERVICE_STARTED.value}
FRAUD_CHECK_EXCLUDED_STATUSES = [BookingStatus.CANCELLED.value]
# Founder rules 2026-10-07 (docs/FEATURE_PLAN_WALLET_EDITS_PLANS_2026-10-07.md
# §1.2–1.3) for the customer's own changes:
#   - cancel: any time until the captain starts heading out (no car of the
#     visit on the way / in service / done — _ensure_customer_may_cancel),
#     charged by the policy tier; more than CUSTOMER_CANCEL_LOCK_HOURS
#     before the slot is the FREE tier (late_cancellation_quote) — despite
#     the name it is not a lock;
#   - edit (and the older reschedule): refused within
#     CUSTOMER_EDIT_LOCK_MINUTES of the slot start, or once the captain is
#     on the way / working / done (_ensure_customer_may_edit);
#   - a plan-covered wash cancelled (at the customer's request) less than
#     PLAN_WASH_FORFEIT_MINUTES before its slot is used up, not returned.
CUSTOMER_CANCEL_LOCK_HOURS = 4
CUSTOMER_EDIT_LOCK_MINUTES = 60
PLAN_WASH_FORFEIT_MINUTES = 60
_CAPTAIN_HEADED = frozenset({"captain_on_the_way", "service_started", "completed"})
CUSTOMER_CANCEL_HEADED_MESSAGE = (
    "Your captain is already on the way, so this booking can't be cancelled online. "
    "Please message us on WhatsApp and our team will help you."
)
CUSTOMER_EDIT_LOCKED_MESSAGE = (
    "Bookings can be changed up to 1 hour before the slot starts. "
    "You can still cancel it, or message us on WhatsApp and our team will help."
)
# A prepaid visit (see BookingService._car_terms) is never settled in cash.
PREPAID_CASH_REFUSAL = "This booking is prepaid — it can only be paid online."
# ...and neither are the extras on a plan wash (BookingService._plan_extras_online_only).
PLAN_EXTRAS_CASH_REFUSAL = "The extras on this plan booking are paid online before we come — cash isn't available for them."
# Per-unit add-on quantity ceiling — nobody books 50 bike washes at a door.
MAX_ADDON_QTY = 10
# A manager can log a job he already did — but not one from months ago.
LOG_MAX_AGE_DAYS = 90
# How captain lateness affects their payout for this job — see start_heading().
# Starting within the slot's own window (or up to 30 min early) costs nothing;
# starting inside the grace period after the slot costs a slice of the service
# fee and flags the manager; starting after the grace period has fully expired
# costs more and flags the manager more urgently. Travel pay is never touched —
# only the service-fee portion, since travel time isn't the lateness itself.
LATE_START_PENALTY_PCT = 25.0
SEVERE_LATE_START_PENALTY_PCT = 50.0
STUCK_ON_THE_WAY_MINUTES = 90  # captain heading out but no before-photo yet — worth a manager nudge
SERVICE_OVERRUN_MINUTES = 20  # actual wash running this many minutes past its planned duration — worth a manager nudge
UNASSIGNED_REMINDER_MINUTES = 15  # nudge the manager this often for as long as a booking stays without a captain
UNASSIGNED_REMINDER_HORIZON_HOURS = 24  # ...but only once its slot starts within this long
LATE_START_NUDGE_MINUTES = 5  # nudge both captain and manager this often once the slot's start time has passed with no heading-out
LATE_START_MANAGER_REPEAT_MINUTES = 15  # ...but the manager's (in-app) repeat of that alert at most this often

# "Only N left" shows on a slot only when it's truly scarce (founder:
# 5 left isn't news, 2 left is).
LOW_SLOT_THRESHOLD = 2

# A slot whose capacity was never configured (no capacity policy names it,
# no center default) takes NO bookings — it used to silently get 999 seats.
# The arrival check's row in booking_status_history (a history-only marker,
# never a booking status).
ARRIVAL_HISTORY_MARKER = "captain_arrived"

UNCONFIGURED_CAPACITY_MESSAGE = (
    "Bookings aren't open for this time yet — the center's slot capacity hasn't been set up. "
    "Please pick another time or contact us."
)


class DuplicateBookingException(BadRequestException):
    """The customer already has a live booking for this time (or this car in
    this slot). details: {booking_id, booking_number} of that booking, so
    the client can open it instead of only showing the message."""
    error_code = "DUPLICATE_BOOKING"


class PriceChangedException(ConflictException):
    """The server would now charge more than the total the customer was
    shown (audit PRICE-04). Nothing was created and no seat was taken;
    details carry the new total so the client can show it and ask again."""
    error_code = "PRICE_CHANGED"


def center_hours(center: dict | None) -> tuple[str, str]:
    """A center's opening hours, falling back to the platform's real hours
    (7:00 AM – 7:00 PM) for a record that has none stored."""
    center = center or {}
    return center.get("working_hours_start") or DEFAULT_WORKING_HOURS_START, center.get("working_hours_end") or DEFAULT_WORKING_HOURS_END


def center_slots(center: dict | None, policy: dict) -> list[dict]:
    """THE slot list of a center — its own hours and slot length (or the
    platform slot length). Every place that needs a center's slots reads
    this one function."""
    opens, closes = center_hours(center)
    duration = (center or {}).get("slot_duration_minutes") or policy["slot_duration_minutes"]
    return generate_slots(opens, closes, duration)

# Flags whose own resolution IS the captain continuing to work the job —
# blocking on them (the way _ensure_no_open_issue does by default) would
# stop a captain from doing the exact thing that clears the flag. Mirrors
# the existing "captain_not_started" exemption on start_heading.
# captain_reported_risk and captain_not_reached are deliberately NOT
# exempted anywhere — those genuinely need a manager decision first.
ARRIVAL_STAGE_EXEMPT_FLAGS = frozenset({"captain_delay", "captain_late_start"})
COMPLETION_EXEMPT_FLAGS = frozenset({"service_overrun", "captain_late_start", "completed_too_fast"})
# Flags that describe the TRIP rather than one car's wash — "never headed
# out", "stuck on the way", "may run late". On a multi-car visit these are
# mirrored onto every car, because the manager is looking at one job, and
# resolving it on one car resolves it for the visit. Per-car work flags
# (overrun, idle after arrival, walked off, geofence) stay on their car.
VISIT_WIDE_FLAGS = frozenset({
    "captain_not_reached", "captain_not_started", "captain_missed_window", "captain_reported_risk", "captain_delay",
    # One code per visit, one lock per visit (CAP-02).
    "arrival_code_locked",
})

# The single canonical map of every status transition this service actually
# performs anywhere below — audited directly against each transition
# method's own inline guard (assign_captain, reassign_captain,
# captain_cancel, start_heading, capture_before_photo,
# capture_after_photo_and_complete, cancel_booking, reschedule_booking) so
# this table is provably accurate, not aspirational. Includes the two
# self-loops that a naive "linear pipeline" table would miss: ASSIGNED ->
# ASSIGNED (reassign_captain swaps the captain without changing status) and
# RESCHEDULED -> RESCHEDULED (a rescheduled-but-not-yet-reassigned booking
# can be rescheduled again). _ensure_transition_allowed() below consults
# this as a defense-in-depth assertion inside every transition method, in
# addition to (not instead of) that method's own specific, user-facing
# error message — so this catches any FUTURE drift without changing any
# current behavior or wording today.
_ALLOWED_TRANSITIONS: dict[str, set[str]] = {
    # An unpaid booking can only become a real one (payment verified, or the
    # customer switched it to cash) or go away. It can never be assigned,
    # started or completed while it's in this state.
    BookingStatus.AWAITING_PAYMENT.value: {BookingStatus.PENDING.value, BookingStatus.CANCELLED.value},
    # -> COMPLETED from the not-yet-started states is manager_mark_done only:
    # the manager did the job himself, so there is no captain workflow.
    # -> AWAITING_PAYMENT is create_booking_group parking a ₹0 plan car with
    # the rest of a visit that is waiting for its online payment.
    BookingStatus.PENDING.value: {
        BookingStatus.ASSIGNED.value, BookingStatus.CANCELLED.value, BookingStatus.RESCHEDULED.value,
        BookingStatus.COMPLETED.value, BookingStatus.AWAITING_PAYMENT.value,
    },
    BookingStatus.ASSIGNED.value: {
        BookingStatus.ASSIGNED.value,
        BookingStatus.CAPTAIN_ON_THE_WAY.value,
        BookingStatus.PENDING.value,
        BookingStatus.CANCELLED.value,
        BookingStatus.RESCHEDULED.value,
        BookingStatus.COMPLETED.value,
    },
    BookingStatus.CAPTAIN_ON_THE_WAY.value: {BookingStatus.SERVICE_STARTED.value, BookingStatus.PENDING.value, BookingStatus.CANCELLED.value},
    BookingStatus.SERVICE_STARTED.value: {BookingStatus.COMPLETED.value, BookingStatus.CANCELLED.value},
    BookingStatus.RESCHEDULED.value: {BookingStatus.ASSIGNED.value, BookingStatus.RESCHEDULED.value, BookingStatus.CANCELLED.value, BookingStatus.COMPLETED.value},
}


def _ensure_transition_allowed(current_status: str, new_status: str) -> None:
    """Defense-in-depth assertion consulted by every transition method
    below, on top of (never instead of) that method's own specific guard —
    see _ALLOWED_TRANSITIONS' docstring. A mismatch here means either this
    table drifted from reality or a new code path forgot to update it;
    either way it's a bug worth surfacing loudly rather than silently
    allowing an unmodeled state jump."""
    allowed = _ALLOWED_TRANSITIONS.get(current_status, set())
    if new_status not in allowed:
        raise BadRequestException(f"Invalid booking state transition: {current_status} -> {new_status}")


def _slot_start_datetime(scheduled_date: datetime, scheduled_slot: str) -> datetime:
    start_str = scheduled_slot.split("-")[0].strip()
    hour, minute = [int(p) for p in start_str.split(":")]
    base = to_ist(scheduled_date)
    return base.replace(hour=hour, minute=minute, second=0, microsecond=0)


def _booking_window(booking: dict) -> tuple[datetime, datetime]:
    """The window to use for overlap/conflict math. Prefers the booking's
    own stored slot_start/slot_end (set at creation from the admin slot
    config — a real, timezone-aware-at-write-time instant, so it's read
    back via from_stored(), same category as heading_at/completed_at/etc.)
    and falls back to the legacy exact-time-string derivation for any
    booking created before that field existed."""
    if booking.get("slot_start") and booking.get("slot_end"):
        return from_stored(booking["slot_start"]), from_stored(booking["slot_end"])
    start = _slot_start_datetime(booking["scheduled_date"], booking["scheduled_slot"])
    return start, start + timedelta(minutes=booking.get("duration_minutes", 60))


def _captain_booking_window(booking: dict) -> tuple[datetime, datetime]:
    """An EXISTING booking's window for captain-conflict purposes
    specifically: its own estimated_start_at + duration_minutes (the actual
    per-job slice of time a captain is occupied), NOT the shared
    customer-facing admin slot bucket (_booking_window) — several bookings
    legitimately share one bucket for the same captain at different
    estimated_start_at values, and comparing against the whole shared
    bucket would make that impossible (every booking in the same slot
    would always look like a conflict). Falls back to the legacy
    exact-time derivation for a booking assigned before estimated_start_at
    existed."""
    duration = booking.get("duration_minutes", 60)
    if booking.get("estimated_start_at"):
        start = from_stored(booking["estimated_start_at"])
    else:
        # Not assigned yet. On a multi-car visit each car still begins where
        # the previous one ended, so an UNASSIGNED visit already presents
        # non-overlapping windows — otherwise every car would look like it
        # starts at the slot start and the visit would conflict with itself
        # the moment a manager tried to assign it.
        start = _slot_start_datetime(booking["scheduled_date"], booking["scheduled_slot"]) + timedelta(
            minutes=int(booking.get("group_offset_minutes") or 0)
        )
    return start, start + timedelta(minutes=duration)


def _find_window_overlap(new_start: datetime, new_end: datetime, buffer: timedelta, existing: list[dict], window_fn=_booking_window) -> dict | None:
    """Shared by _captain_conflict and _customer_conflict — returns the
    first existing booking whose window (padded by `buffer` on both sides)
    overlaps [new_start, new_end]. The buffer is applied ONCE as the
    minimum required gap, not independently to both windows (see
    _captain_conflict's docstring for why that distinction matters).
    window_fn resolves each EXISTING booking's own window — defaults to
    the shared admin-slot-bucket resolver (_booking_window, correct for
    _customer_conflict's "two of my own bookings can't overlap" check);
    _captain_conflict passes _captain_booking_window instead, since a
    captain's conflicts are about their own per-job timing, not the bucket."""
    for booking in existing:
        existing_start, existing_end = window_fn(booking)
        if new_start < existing_end + buffer and existing_start < new_end + buffer:
            return booking
    return None


def _hhmm_to_dt(base: datetime, hhmm: str) -> datetime:
    """base at 00:00 (its own date/tz) plus the given HH:MM offset — added
    via timedelta rather than .replace(hour=...) so an edge-case "24:00"
    slot boundary (midnight of the next day) never raises instead of
    silently being mishandled."""
    hour, minute = [int(p) for p in hhmm.split(":")]
    midnight = base.replace(hour=0, minute=0, second=0, microsecond=0)
    return midnight + timedelta(hours=hour, minutes=minute)


def _resolve_slot_window(service_center: dict, scheduled_date: datetime, slot_key: str, policy: dict) -> tuple[datetime, datetime]:
    """Regenerates the admin slot list for this center+date and returns the
    (start, end) IST instants for slot_key — the exact same derivation the
    customer availability endpoint uses (see BookingService.available_slots),
    so the two can never disagree. Raises if slot_key isn't a real,
    currently-generated slot for this center's own working hours/duration."""
    slots = center_slots(service_center, policy)
    match = next((s for s in slots if s["key"] == slot_key), None)
    if not match:
        raise BadRequestException("That slot isn't available at this service center — pick a valid slot.")
    base = to_ist(scheduled_date)
    return _hhmm_to_dt(base, match["start"]), _hhmm_to_dt(base, match["end"])


def _resolve_estimated_start(booking: dict, requested: datetime | None) -> datetime:
    """The actual instant a captain is expected to start THIS booking —
    defaults to the booking's own slot_start (the common case: most
    assignments don't need finer scheduling than "sometime in this slot"),
    or an explicit manager-chosen instant validated to fall within
    [slot_start, slot_end]. Falls back to the legacy exact-time derivation
    for a pre-migration booking with no slot_start/slot_end at all."""
    slot_start, slot_end = _booking_window(booking)
    if requested is None:
        # On a multi-car visit each car starts where the previous one ended
        # (group_offset_minutes, summed at creation) — so the captain's
        # blocked time covers the WHOLE visit, not just one wash, and he
        # can't be handed another job while he's still on car three. The
        # slot_end ceiling below deliberately doesn't apply to this default:
        # five 45-minute cars legitimately run past a three-hour slot.
        return slot_start + timedelta(minutes=int(booking.get("group_offset_minutes") or 0))
    # A manager-chosen estimated_start_at is a user-entered wall-clock pick
    # (same category as scheduled_date), not a computed instant — normalize
    # via to_ist() the same way, in case it arrives naive (no explicit
    # timezone in the request) rather than crashing on an aware/naive
    # comparison.
    requested = to_ist(requested)
    if not (slot_start <= requested <= slot_end):
        raise BadRequestException("The estimated start time must fall within this booking's slot window.")
    return requested


def _slot_cutoff_passed(slot_end: datetime, policy: dict) -> bool:
    """A slot remains bookable until slot_booking_cutoff_minutes before its
    OWN end — derived per-slot from its real end time, never a hardcoded
    minute value. This single check also naturally covers an entirely past
    date/slot (its end is necessarily further in the past too)."""
    return now_ist() > slot_end - timedelta(minutes=policy["slot_booking_cutoff_minutes"])


def _within_working_hours(center: dict | None, now: datetime) -> bool:
    """Is `now` (IST) inside the center's opening hours?"""

    def minutes(hhmm: str) -> int:
        hour, minute = [int(p) for p in hhmm.split(":")]
        return hour * 60 + minute

    open_at, close_at = center_hours(center)
    opens = minutes(open_at)
    closes = minutes(close_at)
    at = now.hour * 60 + now.minute
    return opens <= at < closes if opens <= closes else (at >= opens or at < closes)


def _effective_start_anchor(booking: dict, slot_start: datetime, policy: dict) -> datetime:
    """The moment a captain's lateness is measured from. Normally the
    booking's own anchor (estimated_start_at / slot start) — but when the
    captain was only ASSIGNED near or after that time (last-minute booking:
    customer books at 6:30 inside a 4-7 slot, manager assigns 6:35), he
    cannot be late for a time that predates him having the job. The late
    clock then starts late_assignment_grace_minutes (default 15) after the
    assignment instead. max() means the grace can only ever EXTEND the
    deadline for genuinely late assignments — a captain assigned the day
    before keeps the normal slot anchor."""
    assigned_at = booking.get("assigned_at")
    if not assigned_at:
        return slot_start
    grace = timedelta(minutes=policy.get("late_assignment_grace_minutes", 15))
    return max(slot_start, from_stored(assigned_at) + grace)


def _ensure_within_advance_window(scheduled_date: datetime, policy: dict) -> None:
    """Bookings (and slot holds) are only accepted from today IST through
    max_advance_days-1 days out — a 7-day policy means today + the next 6
    days. Applied at EVERY scheduling entry point (create, manager
    on-behalf create, reschedule, guest slot hold, slot availability), so
    no path is a backdoor around the window."""
    max_days = int(policy.get("max_advance_days", 7))
    today = now_ist().date()
    target = to_ist(scheduled_date).date() if scheduled_date.tzinfo else scheduled_date.date()
    if target < today:
        raise BadRequestException("That date has already passed — please pick a new date.")
    if target > today + timedelta(days=max_days - 1):
        raise BadRequestException(
            f"Bookings open up to {max_days} days in advance — please pick a date within the next {max_days} days."
        )


def _ist_today() -> str:
    """Today's IST calendar day, "YYYY-MM-DD" — the day a manager-done job
    may be closed on (or after), never before (MGR-06)."""
    return now_ist().strftime("%Y-%m-%d")


def format_slot_start_12h(scheduled_slot: str) -> str:
    """"17:00-17:30" -> "5:00 PM" — for human-facing messages (reminder
    notifications, etc.) that should state the actual time rather than a
    vague "in the next N minutes", which goes stale the moment the sweep's
    fixed lead time doesn't match how soon it actually fires."""
    try:
        return _slot_start_datetime(datetime(2000, 1, 1), scheduled_slot).strftime("%-I:%M %p")
    except (ValueError, IndexError):
        return scheduled_slot


# Internal payout/margin fields — never meant for the customer, and only
# ever meant for a captain as their OWN fee, never the platform's cut. Hiding
# this only in the frontend still leaks it to anyone opening devtools/the
# network tab, so it's stripped from the actual API response here, not just
# left unrendered client-side.
_CUSTOMER_HIDDEN_FIELDS = (
    "captain_earning", "platform_earning", "captain_travel_pay", "captain_service_pay", "wallet_settled",
    "wallet_settled_as", "captain_start_stage", "late_penalty_pct", "late_penalty_amount",
    # The whole issue-flag machinery is internal ops (manager ↔ captain):
    # "Captain started late", geofence flags, missed-window states. The
    # customer must never see their captain publicly flagged (founder
    # call) — they get the status + transparency timeline, nothing else.
    "issue_flag", "issue_notes", "issue_flagged_at", "issue_resolved",
    # The arrival-check lockout, a missing arrival GPS and a too-quick
    # completion are between the captain and the manager.
    "arrival_code_failures", "arrival_code_locked", "arrival_code_locked_at", "arrival_code_unlocked_by", "arrival_code_unlocked_at",
    "arrival_no_gps", "quick_completion_flagged",
    # Captain-wallet bookkeeping (MoneyService.settle_captain_wallet):
    # captain_wallet_posted is his earning minus the cash he holds — the
    # captain's pay by another name (security review 2026-10-07).
    "captain_wallet_posted", "captain_cash_collected", "captain_wallet_captain_id", "captain_wallet_rev",
)
_CAPTAIN_HIDDEN_FIELDS = (
    "platform_earning",
    # Money a manager paid BACK to the customer for this booking (MONEY-2):
    # between the center and the customer — the captain never sees it. The
    # customer's own view keeps it (it's their money).
    "manager_paybacks", "paid_back_total",
)


def _ensure_no_open_issue(booking: dict, exempt_flags: frozenset[str] = frozenset()) -> None:
    """A flagged-and-unresolved booking (captain never reached, stuck on the
    way, self-reported delay risk, etc.) needs a manager's attention FIRST —
    either resolve (confirm things are actually fine) or reschedule (pick a
    new, valid time) — before the captain can keep progressing it. Without
    this gate a stuck booking stays silently actionable indefinitely, with
    a scheduled time that's gone stale (sometimes by days) and nobody ever
    corrected — which is exactly how a booking still shows "13 Aug" on the
    19th and lets the captain click straight through it.

    exempt_flags lets a specific caller allow through a flag whose actual
    resolution IS the action being gated — e.g. "captain_not_started" exists
    specifically to prompt the captain to start heading out, so start_heading
    itself must not be blocked by it (see its call site)."""
    flag = booking.get("issue_flag")
    if flag and not booking.get("issue_resolved") and flag not in exempt_flags:
        raise BadRequestException(
            "This booking is flagged for your manager's attention and can't be progressed until they resolve or "
            "reschedule it — contact your manager."
        )


def _ensure_schedulable(booking: dict, policy: dict) -> None:
    """A manager can't assign/reassign a captain directly onto a booking
    whose scheduled window has already fully passed (same "fully expired"
    threshold the captain-not-reached sweep uses) — that would just create
    an assignment against a time that's already gone. They have to
    reschedule it to a real, future time first, or cancel it outright.

    Uses the booking's own admin-slot end (_booking_window — the 09:00-12:00
    style window, falling back to the legacy exact-time+duration derivation
    for pre-slot bookings), NOT duration_minutes alone — a booking is still
    perfectly assignable well after its ~40-minute service duration would
    have elapsed, as long as it's still within its 3-hour admin slot."""
    _, slot_end = _booking_window(booking)
    window_end = slot_end + timedelta(minutes=policy.get("late_start_grace_minutes", 30))
    if now_ist() > window_end:
        raise BadRequestException(
            "This booking's scheduled time has already passed — reschedule it to a new time before assigning a "
            "captain, or cancel it instead."
        )


_NEEDS_CAPTAIN = [BookingStatus.PENDING.value, BookingStatus.RESCHEDULED.value]
# Not finished yet — what a manager can still act on.
_ACTIVE_STATUSES = _NEEDS_CAPTAIN + [BookingStatus.ASSIGNED.value, BookingStatus.CAPTAIN_ON_THE_WAY.value, BookingStatus.SERVICE_STARTED.value]
_OPEN_ISSUE = {
    "status": {"$in": [BookingStatus.AWAITING_PAYMENT.value, *_ACTIVE_STATUSES]},
    "issue_flag": {"$nin": [None, ""]},
    "issue_resolved": {"$ne": True},
}
_QUEUE_SCOPES = {
    "needs_captain": {"status": {"$in": _NEEDS_CAPTAIN}},
    "open_issues": _OPEN_ISSUE,
    "attention": {"$or": [{"status": {"$in": _NEEDS_CAPTAIN}}, _OPEN_ISSUE]},
    "active": {"status": {"$in": _ACTIVE_STATUSES}},
    "late_starts": {"captain_start_stage": {"$in": ["late", "severely_late"]}},
}


async def center_queue_filters(
    db: AsyncIOMotorDatabase, *, scope: str | None = None, date_from: str | None = None, date_to: str | None = None, search: str | None = None
) -> list[dict]:
    """Server-side manager-queue filters (GET /bookings/center/{id}) as
    clauses to AND together. Mirrors the queue page's own definitions
    (lib/constants.ts needsCaptain / isOpenIssue). Raises ValueError on bad
    input."""
    clauses: list[dict] = []
    if scope:
        if scope not in _QUEUE_SCOPES:
            raise ValueError("Unknown scope")
        clauses.append(_QUEUE_SCOPES[scope])
    # scheduled_date holds the IST calendar day as naive midnight digits.
    window: dict = {}
    try:
        if date_from:
            window["$gte"] = datetime.strptime(date_from, "%Y-%m-%d")
        if date_to:
            window["$lt"] = datetime.strptime(date_to, "%Y-%m-%d") + timedelta(days=1)
    except ValueError:
        raise ValueError("Dates must be YYYY-MM-DD")
    if window:
        clauses.append({"scheduled_date": window})
    q = (search or "").strip()
    if q:
        digits = re.sub(r"\D", "", q)
        ors: list[dict] = []
        number = q.upper().replace(" ", "")
        ors.append({"booking_number": {"$regex": "^" + re.escape(number if number.startswith("BK") else f"BK{number}")}})
        if digits and len(digits) == len(q.replace(" ", "").lstrip("+")):
            # "12" finds BK0012 — numbers are zero-padded. The exact padded
            # forms (an $in point lookup on the unique index), never a
            # ^BK0*12$ regex: every number starts with BK, so that walked
            # the whole index on every search.
            ors.append({"booking_number": {"$in": BookingRepository.number_candidates(digits)}})
            if len(digits) >= 4:
                phone = digits[2:] if len(digits) == 12 and digits.startswith("91") else digits
                ors.append({"customer_phone": {"$regex": "^" + re.escape(phone)}})
        if len(q) >= 2 and not digits:
            ids = await db.users.find(
                {"role": "customer", "full_name": {"$regex": re.escape(q), "$options": "i"}, "is_deleted": {"$ne": True}}, {"_id": 1}
            ).limit(50).to_list(length=50)
            if ids:
                ors.append({"customer_id": {"$in": [str(u["_id"]) for u in ids]}})
        clauses.append({"$or": ors})
    return clauses


def _redact_financials(booking: dict, actor_role: str) -> dict:
    if actor_role in {"admin", "manager"}:
        return booking
    hidden = _CUSTOMER_HIDDEN_FIELDS if actor_role == "customer" else _CAPTAIN_HIDDEN_FIELDS
    for field in hidden:
        booking.pop(field, None)
    if actor_role == "captain":
        # The 4-digit service code is the CUSTOMER's proof that the captain
        # is standing at the right car — a captain who can read it off his
        # own app could "verify" an arrival without ever meeting anyone. He
        # only needs to know which check to ask for (code vs. legacy plate).
        booking["requires_service_code"] = bool(booking.pop("service_code", None))
    return booking


def redact_for_captain(booking: dict) -> dict:
    """What a captain's own action endpoints (heading, arrival, photos,
    release, report-risk) send back — the same redaction as his job list."""
    return _redact_financials(dict(booking), "captain")


class _EditDryRun(Exception):
    """Raised at the end of a dry-run edit's transaction to abort it; carries
    the preview (BookingService.edit_booking)."""

    def __init__(self, result: dict):
        super().__init__("dry run")
        self.result = result


class _HeldCouponCheck(CouponService):
    """Re-checks a coupon a booking ALREADY holds (a customer edit, spec
    1.3) through CouponService's own discount rules — without the usage
    limits its own use already counts against (that use is this booking's).
    Validity window and limits were checked when it was applied; only
    whether it still applies to the new services/subtotal is asked."""

    def __init__(self, db: AsyncIOMotorDatabase, coupon: dict):
        super().__init__(db)
        self._held = coupon

    async def _valid_coupon(self, code: str, order_value: float, user_id: str | None = None) -> dict:
        minimum = float(self._held.get("min_order_value", 0) or 0)
        if order_value < minimum:
            raise BadRequestException(f"Minimum order value of ₹{minimum:g} required for this coupon")
        return self._held


class BookingService:
    def __init__(self, db: AsyncIOMotorDatabase):
        self.db = db
        self.repo = BookingRepository(db)
        self.history_repo = BookingStatusHistoryRepository(db)
        self.vehicle_repo = VehicleRepository(db)
        self.address_repo = AddressRepository(db)
        self.service_repo = ServiceRepository(db)
        self.combo_repo = ComboOfferRepository(db)
        self.center_repo = ServiceCenterRepository(db)
        self.slot_capacity_repo = SlotCapacityRepository(db)
        self.daily_capacity_repo = DailyCapacityRepository(db)
        self.user_repo = UserRepository(db)
        self.inventory_repo = InventoryRepository(db)
        self.coupon_service = CouponService(db)
        self.subscription_service = UserSubscriptionService(db)
        self.notifications = NotificationService(db)
        self.pricing_service = PricingService(db)
        self.wallet_service = WalletService(db)
        self.policy_service = BookingPolicyService(db)
        self.capacity_policy_service = CapacityPolicyService(db)
        self.location_repo = CaptainLocationRepository(db)
        self.vehicle_type_repo = VehicleTypeRepository(db)
        self.charges = CustomerChargeService(db)
        # Every movement of a booking's money against the customer and
        # captain wallets (MONEY's contract, spec §2).
        self.money = MoneyService(db)

    _BIKE_WORD = re.compile(r"bike|scooter|two.?wheeler", re.IGNORECASE)

    @staticmethod
    def new_service_code() -> str:
        """The 4-digit code the customer shares with the captain on arrival
        (quick-booking model) — one per visit. Purely a "you're at the right
        door" check against THIS booking, so collisions across bookings
        don't matter."""
        return f"{secrets.randbelow(9000) + 1000:04d}"

    @classmethod
    def _adds_bikes(cls, svc: dict) -> bool:
        """The "add a bike" line (an add-on with "bike" in its name, no
        "polish"): its quantity is the number of extra bikes."""
        name = svc.get("name", "")
        return bool(svc.get("is_addon")) and bool(cls._BIKE_WORD.search(name)) and "polish" not in name.lower()

    @classmethod
    def _ensure_offered_for_type(cls, services: list[dict], vehicle_type_id: str | None, *, bike_type: bool = False) -> None:
        """On-site add-ons and booking edits take only what the booking page
        offers this vehicle type (frontend lib/serviceMix `offeredAddons` +
        `baseGroups`, QA 2026-10-07): services listed for the type (or for
        every type). The add-a-bike line (Extra Bike Wash) is the bike
        COUNTER of a BIKE booking — the page books 3+ bikes as the smallest
        variant + that line × the rest (`composeBikeLine`) — so on a bike
        type (`bike_type`) it is accepted with its quantity; on a car it is
        never a pick (it was offered on cars in the plan-wash, edit and
        add-service sheets)."""
        for svc in services:
            if cls._adds_bikes(svc):
                if bike_type:
                    continue
                raise BadRequestException(f"{svc.get('name') or 'This service'} isn't offered for this vehicle type.")
            listed = svc.get("vehicle_types") or []
            if listed and vehicle_type_id not in listed:
                raise BadRequestException(f"{svc.get('name') or 'This service'} isn't offered for this vehicle type.")

    async def _is_bike_type(self, vehicle_type_id: str | None) -> bool:
        """A bike-class vehicle type (classified from its name, as
        _validate_service_mix does)."""
        if not vehicle_type_id:
            return False
        vt = await self.vehicle_type_repo.find_by_id(vehicle_type_id)
        return bool(vt and self._BIKE_WORD.search(vt.get("name", "")))

    async def _validate_service_mix(self, services: list[dict], vehicle_type_id: str, raw_quantities: dict) -> dict[str, int]:
        """The catalogue's add-on/base/variant rules, enforced server-side so
        no client (app, guest wizard, staff panel, WhatsApp bot) can compose
        an impossible booking:

          - at least one NON-add-on base service (an add-on never rides alone);
          - one pick per variant_group (you can't take "2 bikes" AND "4 bikes");
          - every service must fit the booking vehicle's class (car vs bike,
            classified from the vehicle-type name) — with ONE deliberate combo
            exception: a CAR booking that adds bikes via a car-class
            bike-wash add-on (Extra Bike Wash, ₹60/bike) may also carry the
            bike-class polish add-on for those bikes;
          - quantities: only per-bike add-ons may repeat; bike polish can
            never exceed the number of bikes actually in the booking
            (variant label count on a bike booking, added-bike count on a
            car booking).

        Returns {service_id: qty>=1} for every selected service.
        """
        type_docs = await self.db.vehicle_types.find({}).to_list(length=None)
        is_bike_type = {str(t["_id"]): bool(self._BIKE_WORD.search(t.get("name", ""))) for t in type_docs}
        booking_is_bike = is_bike_type.get(vehicle_type_id, False)

        def classes_of(svc: dict) -> set[str]:
            ids = svc.get("vehicle_types") or []
            if not ids:
                return {"car", "bike"}  # unrestricted service
            return {"bike" if is_bike_type.get(i) else "car" for i in ids}

        def is_addon(svc: dict) -> bool:
            return bool(svc.get("is_addon"))

        # The "add a bike" line (an add-on with "bike" in its name, no
        # "polish") — its quantity IS the number of extra bikes joining the
        # booking. Valid on BOTH classes: a car wash adds bikes at ₹60
        # each, and a bike wash counts bikes as base + N extras (₹99 +
        # ₹60 per additional bike — the UI's single −/+ counter).
        adds_bikes = self._adds_bikes

        def is_bike_polish(svc: dict) -> bool:
            return is_addon(svc) and classes_of(svc) == {"bike"} and "polish" in svc.get("name", "").lower()

        if not any(not is_addon(s) for s in services):
            raise BadRequestException("Add-on services can only be booked along with a main service — pick a wash first.")

        seen_groups: set[str] = set()
        for s in services:
            group = s.get("variant_group")
            if group:
                if group in seen_groups:
                    raise BadRequestException(f"Pick just one option for {s['name'].split('(')[0].strip()}.")
                seen_groups.add(group)

        # Normalize quantities first — bike-count math below depends on them.
        quantities: dict[str, int] = {}
        for s in services:
            sid = str(s["_id"])
            qty = int(raw_quantities.get(sid, 1) or 1)
            per_unit = adds_bikes(s) or is_bike_polish(s)
            if qty < 1 or qty > MAX_ADDON_QTY or (qty > 1 and not per_unit):
                raise BadRequestException(f"Invalid quantity for {s['name']}.")
            quantities[sid] = qty

        # How many bikes are in this booking? Extra-bike add-ons count on
        # BOTH classes; on a bike booking they stack on the base wash's
        # variant count (normally 1 — the −/+ counter books base + extras).
        extra_bikes = sum(quantities[str(s["_id"])] for s in services if adds_bikes(s))
        if booking_is_bike:
            bike_count = 1
            for s in services:
                if not is_addon(s) and s.get("variant_label"):
                    m = re.search(r"\d+", s["variant_label"])
                    if m:
                        bike_count = int(m.group())
            bike_count += extra_bikes
        else:
            bike_count = extra_bikes

        booking_class = "bike" if booking_is_bike else "car"
        for s in services:
            svc_classes = classes_of(s)
            if booking_class in svc_classes:
                continue
            if adds_bikes(s):
                continue  # the add-a-bike line rides on both classes
            if not booking_is_bike and is_bike_polish(s) and bike_count > 0:
                continue  # the combo exception: polish for the added bikes
            raise BadRequestException(
                f"{s['name']} isn't available for this vehicle"
                + (" — add a bike to the booking first." if is_bike_polish(s) else ".")
            )

        for s in services:
            if is_bike_polish(s) and quantities[str(s["_id"])] > bike_count:
                raise BadRequestException(
                    f"Bike polish is per bike — this booking has {bike_count} bike{'s' if bike_count != 1 else ''}."
                )

        return quantities

    # -- offer terms (admin data on the Service: prepaid_only, charges_travel)

    _NO_TERMS = {"charges_travel": False, "prepaid_service": None}

    @classmethod
    def _car_terms(cls, services: list[dict], plan_covered: bool) -> dict:
        """What one car's services impose on its visit. A plan-covered car
        imposes nothing — the plan already paid for the trip."""
        if plan_covered:
            return dict(cls._NO_TERMS)
        return {
            "charges_travel": any(s.get("charges_travel") for s in services),
            "prepaid_service": next((s.get("name") or "This service" for s in services if s.get("prepaid_only")), None),
        }

    async def _visit_terms(self, cars) -> dict:
        """Merged terms for every car of a visit (GroupVehicleRequest-like
        objects), read before any car is created."""
        terms = dict(self._NO_TERMS)
        for car in cars:
            ids = list(car.service_ids or [])
            if car.combo_id:
                combo = await self.combo_repo.find_by_id(car.combo_id)
                ids = list((combo or {}).get("service_ids") or [])
            services = [s for s in [await self.service_repo.find_by_id(sid) for sid in ids] if s]
            car_terms = self._car_terms(services, bool(car.subscription_id))
            terms["charges_travel"] = terms["charges_travel"] or car_terms["charges_travel"]
            terms["prepaid_service"] = terms["prepaid_service"] or car_terms["prepaid_service"]
        return terms

    @staticmethod
    def _paying_index(cars) -> int:
        """The car of a visit that carries what the visit pays ONCE (the
        coupon, the distance charge, late-cancellation charges): the first
        car no plan covers, else the first car. create_booking_group and
        quote_visit both use it, so the quote and the booking agree."""
        return next((i for i, car in enumerate(cars) if not getattr(car, "subscription_id", None)), 0)

    @staticmethod
    def _ensure_prepaid_method(terms: dict, source: str, payment_method) -> None:
        """Web and staff bookings must choose online for a prepaid visit; the
        WhatsApp bot has no payment question, so create_booking just makes
        its prepaid visits online."""
        method = payment_method.value if hasattr(payment_method, "value") else payment_method
        if terms["prepaid_service"] and source != "whatsapp" and method in (None, PaymentMethod.CASH.value):
            raise BadRequestException(f"{terms['prepaid_service']} is prepaid — choose Pay online.")

    async def visit_is_prepaid(self, booking: dict) -> bool:
        return any(c.get("prepaid_only") for c in await self._visit_cars(booking))

    async def create_booking(
        self,
        customer_id: str,
        payload: BookingCreateRequest,
        _skip_verification_gate: bool = False,
        source: str = "app",
        _allow_pinless: bool = False,
        _group: dict | None = None,
        notify_background: bool = True,
        _completed_by: dict | None = None,
        apply_charges: bool | None = None,
    ) -> dict:
        """`apply_charges`: add the customer's open late-cancellation
        charges (CustomerChargeService) to this booking, claimed inside its
        create transaction. None = only a customer's own booking (source
        "app" — the app, the website, a resident's society premium wash);
        create_quick_booking / create_booking_for_customer pass True for the
        bot and staff booking on the customer's behalf. Never a logged job,
        and on a visit only the car create_booking_group picked
        (`_group["carry_charges"]`) — so an automatic society-schedule
        booking (source "staff") never takes one.

        `_completed_by` is set ONLY by create_manager_logged_visit: the
        manager already did this job himself, so it is stored directly as
        COMPLETED at `service_at` — no advance-window / cutoff / conflict
        checks, no slot seat, no captain money, no announcements. Everything
        else (pricing, service-mix rules, pass redemption, the document
        shape) is the ordinary path, so a logged job can never drift from a
        real one. Keys: id, service_center, service_at, payment_method.

        `_group` is set ONLY by create_booking_group, and marks this
        booking as one car of a multi-car visit:
          {"id": <group id>, "offset_minutes": int, "charge_travel": bool,
           "line_index": int, "service_code": str}
        It changes exactly three things — the slot seat is NOT reserved here
        (the visit reserved one seat for all its cars), the captain's travel
        pay is on the first car only (one trip), and the car carries its
        start offset. Everything else about a car in a group is an ordinary
        booking: its own arrival check, photos, pass redemption and review.

        Quick-booking model (2026-09): the payload names a vehicle TYPE, not
        a vehicle record — no plate, no brand/model, and NO phone-OTP gate
        (OTP is for logging in, never for booking). vehicle_id is still
        honoured for the older saved-vehicle paths."""
        vehicle: dict | None = None
        if payload.vehicle_id:
            vehicle = await self.vehicle_repo.find_by_id(payload.vehicle_id)
            if not vehicle or vehicle["owner_id"] != customer_id:
                raise NotFoundException("Vehicle not found")
            vehicle_type = vehicle["vehicle_type"]
            if payload.vehicle_type and payload.vehicle_type != vehicle_type:
                raise BadRequestException("That car is saved as a different vehicle type — pick its own type.")
            vt_doc = await self.vehicle_type_repo.find_by_id(vehicle_type)
        else:
            vehicle_type = payload.vehicle_type or ""
            vt_doc = await self.vehicle_type_repo.find_by_id(vehicle_type) if vehicle_type else None
            if not vt_doc or not vt_doc.get("is_active", True):
                raise BadRequestException("Pick a valid vehicle type.")
        vehicle_label = (vt_doc or {}).get("name") or "Vehicle"

        address = await self.address_repo.find_by_id(payload.address_id)
        if not address or address["owner_id"] != customer_id:
            raise NotFoundException("Address not found")

        customer = await self.user_repo.find_by_id(customer_id)
        if not customer:
            raise NotFoundException("Customer not found")

        # "subscription" is the server's own label for a plan-paid car, and
        # "online_placeholder" is the pre-gateway spelling of online. Taken
        # from a client as-is, either made a CONFIRMED unpaid non-cash
        # booking: no payment gate, and the captain credited at completion
        # as if the money had come in.
        if payload.payment_method == PaymentMethod.ONLINE_PLACEHOLDER:
            payload = payload.model_copy(update={"payment_method": PaymentMethod.ONLINE})
        if payload.payment_method == PaymentMethod.SUBSCRIPTION and not payload.subscription_id:
            raise BadRequestException("Pick the plan this booking uses, or choose cash or online.")

        # Resolve what's actually being purchased: either a combo bundle (its own
        # price, expands to the services inside it) or an explicit list of services.
        combo = None
        if payload.combo_id:
            combo = await self.combo_repo.find_by_id(payload.combo_id)
            if not combo or not combo.get("is_active"):
                raise NotFoundException("Combo offer not found or inactive")
            service_ids = combo["service_ids"]
        else:
            service_ids = payload.service_ids
        if not service_ids:
            raise BadRequestException("Select at least one service or combo offer")

        services = []
        for service_id in service_ids:
            service = await self.service_repo.find_by_id(service_id)
            if not service or not service.get("is_active"):
                raise NotFoundException(f"Service not found or inactive: {service_id}")
            services.append(service)

        # Add-on/base/variant rules + per-unit quantities — combos are
        # admin-curated bundles and skip the mix rules by design.
        if combo:
            quantities = {str(s["_id"]): 1 for s in services}
        else:
            quantities = await self._validate_service_mix(services, vehicle_type, payload.service_quantities or {})

        # Offer terms are decided for the whole VISIT (one trip, one
        # payment): a group passes them in, a single car is its own visit.
        # A manager-logged job is exempt from both.
        visit_terms = (_group or {}).get("terms") or self._car_terms(services, bool(payload.subscription_id))
        if _completed_by:
            visit_terms = self._NO_TERMS
        elif _group is None:
            self._ensure_prepaid_method(visit_terms, source, payload.payment_method)
        prepaid = bool(visit_terms["prepaid_service"])

        duration_minutes = sum(s.get("duration_minutes", 30) * quantities[str(s["_id"])] for s in services) or 30

        policy = await self.policy_service.get_policy()
        # Service center must be known BEFORE slot validation — slots are
        # generated from the CENTER's own working hours/slot duration, not
        # a global policy window (see _resolve_slot_window).
        if _completed_by:
            service_center, distance_km = _completed_by["service_center"], 0.0
        else:
            service_center, distance_km = await self._resolve_service_center(address, allow_pinless=_allow_pinless)
            _ensure_within_advance_window(payload.scheduled_date, policy)
            # A society pass's premium wash needs a day's notice on customer
            # channels (docs/SOCIETY_PLANS.md) — staff are exempt.
            from app.services.society_service import ensure_society_lead_time

            await ensure_society_lead_time(self.db, payload.subscription_id, payload.scheduled_date, source)
        date_str = to_ist(payload.scheduled_date).strftime("%Y-%m-%d")
        slot_start, slot_end = _resolve_slot_window(service_center, payload.scheduled_date, payload.scheduled_slot, policy)
        if not _completed_by and _slot_cutoff_passed(slot_end, policy):
            raise BadRequestException("This slot is no longer available to book — please pick another slot.")
        # The customer's own-overlap check runs INSIDE the create transaction
        # below (after a write to the customer's doc), so two tabs can't both
        # pass it — see _raise_if_customer_conflict.

        registration_number = normalize_plate(vehicle["registration_number"]) if vehicle and vehicle.get("registration_number") else None
        phone = customer.get("phone")
        first_time_eligible = await self._first_time_eligible(phone, registration_number)

        # Validate-only for a plan — the actual deduction (commit_consumption)
        # happens after the booking below is durably created, so a failure in
        # between can never leave a subscription silently decremented for a
        # booking that doesn't exist.
        priced = await self._price_services(
            customer_id=customer_id,
            services=services,
            quantities=quantities,
            combo=combo,
            vehicle_type=vehicle_type,
            vehicle_id=payload.vehicle_id,
            first_time_eligible=first_time_eligible,
            subscription_id=payload.subscription_id,
            coupon_code=payload.coupon_code,
            as_of=_completed_by["service_at"] if _completed_by else None,
            scheduled_date=payload.scheduled_date,
        )
        subtotal = priced["subtotal"]
        discount_amount = priced["discount_amount"]
        coupon = priced["coupon"]
        subscription_consumption = priced["subscription_consumption"]
        payment_method = PaymentMethod.SUBSCRIPTION if payload.subscription_id else payload.payment_method

        # The WhatsApp bot has no payment question: a prepaid visit it books
        # is simply an online one (app/staff cash was refused above).
        if prepaid and payment_method == PaymentMethod.CASH:
            payment_method = PaymentMethod.ONLINE

        # Customer distance charge: once per visit, on the car that carries
        # the trip's CHARGE — the first car that pays (create_booking_group,
        # audit PRICE-06: it used to ride on a ₹0 plan car) — added AFTER
        # any discount so no coupon or plan ever reduces it. The captain's
        # travel PAY is a separate thing and stays on the visit's first car.
        carries_trip = not _group or bool(_group.get("charge_travel"))
        captain_travel = not _group or bool(_group.get("captain_travel", _group.get("charge_travel")))
        travel = None
        if visit_terms["charges_travel"] and carries_trip:
            charge_km, charge_source = await self.charge_distance_km(service_center, address, distance_km)
            travel = {**await self.pricing_service.travel_quote(charge_km), "source": charge_source}
        travel_charge = float(travel["charge"]) if travel else 0.0

        tax_amount = 0.0
        total_amount = round(max(0.0, subtotal - discount_amount) + tax_amount + travel_charge, 2)
        # What this car costs on its own — a previous negative wallet balance
        # it may carry (decided inside the create transaction below) comes
        # on top; wallet credit spent on it is recorded beside it.
        base_total = total_amount
        # The customer's wallet (spec 1.1, MoneyService.apply_wallet_at_create)
        # meets this booking only on an interactive booking for the customer
        # (`apply_charges`: their app/website, the WhatsApp bot, staff on
        # their behalf) — never a manager-logged job, never an automatic
        # society-schedule booking. On a visit: credit is spent on any car,
        # a previous balance due rides on ONE car (the first that pays).
        if _group is not None:
            carry_due = bool(_group.get("carry_charges"))
            use_wallet = bool(_group.get("use_wallet", carry_due))
        else:
            if apply_charges is None:
                apply_charges = source == "app"
            carry_due = use_wallet = bool(apply_charges)
        if _completed_by:
            carry_due = use_wallet = False

        # Founder rule: choosing "pay online" makes the payment a PRE-condition
        # of the booking, not an afterthought. Such a booking is created
        # AWAITING_PAYMENT — its slot is genuinely held, but it is in no
        # queue, no captain can be assigned and nobody is notified until the
        # payment verifies (or the customer switches it to cash). Cash and
        # plan-paid bookings are confirmed immediately, exactly as before;
        # a zero-total booking has nothing to wait for either.
        # ...on the SELF-SERVICE path only. A manager booking on someone's
        # behalf can't complete a checkout modal for them, and the WhatsApp
        # bot confirms first and sends a pay link afterwards — parking either
        # of those would leave a booking nobody can dispatch.
        # A PASS booking with add-ons is prepaid too (founder rule: "no cash
        # on delivery option available" for what a pass booking adds) — the
        # visit itself is already paid for by the pass, so the only money
        # left is the extras, and those are settled before we come out.
        # A PREPAID visit (prepaid_only service, not plan-covered) is parked
        # on every channel: staff and the bot get a payment link sent to the
        # customer instead of a confirmed cash-or-link booking.
        method_value = payment_method.value if hasattr(payment_method, "value") else payment_method
        chosen_value = payload.payment_method.value if hasattr(payload.payment_method, "value") else payload.payment_method

        def money_state(carried: float, applied: float = 0.0) -> dict:
            """Everything about this booking that depends on what it
            finally costs — decided again inside the create transaction once
            the wallet is applied: a previous balance due it carries
            (`carried`, part of its total) and wallet credit spent on it
            (`applied`). A plan wash whose only cost is a carried balance is
            paid like any booking (cash after the wash, or online if the
            customer chose online); a plan wash with extras keeps the
            founder's online-only rule for them. Nothing left to pay (free,
            or covered by wallet credit) never waits for a payment."""
            total = round(base_total + carried, 2)
            due = round(total - applied, 2)
            if due <= 0:
                awaiting = False
            elif prepaid:
                awaiting = True
            elif source != "app":
                awaiting = False
            elif method_value == PaymentMethod.ONLINE.value:
                awaiting = True
            elif method_value == PaymentMethod.SUBSCRIPTION.value:
                awaiting = base_total > 0 or chosen_value == PaymentMethod.ONLINE.value
            else:
                awaiting = False
            # A ₹0 car (plan-covered) of a visit that may still wait for its
            # online payment is created ALREADY waiting with it — never
            # briefly confirmed first (audit STATE-02: in that window a
            # manager could take it and the visit could then never expire).
            # create_booking_group confirms it again if nothing on the visit is owed.
            parked = bool((_group or {}).get("park_free")) and not _completed_by and due <= 0
            awaiting = awaiting or parked
            status = BookingStatus.COMPLETED if _completed_by else (BookingStatus.AWAITING_PAYMENT if awaiting else BookingStatus.PENDING)
            if applied > 0 or carried > 0:
                pay_status = bm.status_for(total, applied, 0)
            else:
                pay_status = PaymentStatus.PAID.value if payment_method == PaymentMethod.SUBSCRIPTION and total <= 0 else PaymentStatus.PENDING.value
            return {"total": total, "due": max(0.0, due), "awaiting": awaiting, "parked": parked, "status": status, "payment_status": pay_status}

        state = money_state(0.0)
        awaiting_payment, parked_free, initial_status = state["awaiting"], state["parked"], state["status"]

        primary_service = services[0] if services else None
        # One trip, one travel payment: cars after the first on the same
        # visit earn the service fee only. Paying travel five times for one
        # journey would be a straight leak out of the platform's margin.
        if _completed_by:
            # No captain did this job: nothing is owed to one, and the whole
            # amount is the platform's (the manager collected it himself).
            split = {"distance_km": 0.0, "captain_travel_pay": 0.0, "captain_service_pay": 0.0, "captain_earning": 0.0, "platform_earning": total_amount}
        else:
            split = await self.pricing_service.calculate_split(
                subtotal, distance_km if captain_travel else 0.0, primary_service
            )
            # The platform's share is whatever the customer actually pays
            # minus the captain's fee: the captain keeps his fee, and the
            # platform absorbs a coupon or plan waiver (it can go negative —
            # a ₹0 plan wash still pays the captain) and keeps the distance
            # charge. Pricing it off the pre-discount subtotal debited the
            # discount from the captain's wallet on every cash job.
            split["platform_earning"] = round(total_amount - split["captain_earning"], 2)

        manager_id = service_center.get("manager_id")
        from app.services.route_service import road_distance_eta

        _center_lat, _center_lng = self._center_coords(service_center)
        _route = None if _completed_by else await road_distance_eta(
            _center_lat, _center_lng,
            address.get("latitude"), address.get("longitude"),
        )
        travel_estimate = {
            "travel_distance_km": _route["km"] if _route else None,
            "travel_eta_minutes": _route["minutes"] if _route else None,
            "travel_estimate_source": _route["source"] if _route else None,
        }

        booking_doc = {
            "booking_number": await self.repo.generate_unique_booking_number(),
            "customer_id": customer_id,
            "vehicle_id": payload.vehicle_id,
            "vehicle_type": vehicle_type,
            "vehicle_label": vehicle_label,
            "visit_line_key": payload.vehicle_id or f"{vehicle_type}#{int((_group or {}).get('line_index') or 0)}",
            # One code per visit — every car on it carries the same one.
            "service_code": (_group or {}).get("service_code") or self.new_service_code(),
            "address_id": payload.address_id,
            # The place this booking is FOR, frozen now (spec 1.3): editing
            # the saved address later never moves a live booking.
            "address_snapshot": address_snapshot(address),
            "service_center_id": str(service_center["_id"]),
            "service_ids": service_ids,
            # Only quantities above 1 are stored — {sid: 3} means "×3".
            "service_quantities": {sid: q for sid, q in quantities.items() if q > 1},
            # The bundle the customer actually chose, when they chose one:
            # service_ids above is its expansion, which alone can't say
            # whether those services were bought loose or as a combo. Read
            # by _enrich_bookings for combo_name, and by "Book again" to
            # replay the booking as the thing it was sold as.
            "combo_id": payload.combo_id,
            "subscription_id": payload.subscription_id,
            "booking_group_id": (_group or {}).get("id"),
            "group_offset_minutes": int((_group or {}).get("offset_minutes") or 0),
            "scheduled_date": payload.scheduled_date,
            "scheduled_slot": payload.scheduled_slot,
            "slot_start": slot_start,
            "slot_end": slot_end,
            "duration_minutes": duration_minutes,
            "status": initial_status.value,
            # Both of these start the clocks the manager-facing sweeps read.
            # An unpaid booking hasn't entered the queue, so neither clock
            # starts until confirm_awaiting_payment_booking says so.
            "awaiting_assignment_since": None if awaiting_payment else now_ist(),
            # A subscription booking is only fully PAID if it was entirely
            # waived — a same-visit swap to a costlier service (see
            # _subscription_discount) leaves a real total_amount owed, same
            # as any other pending payment, not a false "already paid".
            "payment_status": state["payment_status"],
            "payment_method": payment_method.value if hasattr(payment_method, "value") else payment_method,
            # Money received / wallet (spec 1.5, booking_money): set again by
            # the wallet inside the create transaction.
            "wallet_applied": 0.0,
            "amount_paid": 0.0,
            "wallet_due_carried": 0.0,
            "amount_due": state["due"],
            # Whether this car was priced as the customer's first wash — kept
            # so an edit re-prices it the same way (spec 1.3).
            "first_time_eligible": bool(first_time_eligible),
            "subtotal": round(subtotal, 2),
            "discount_amount": round(discount_amount, 2),
            "tax_amount": tax_amount,
            "travel_charge": travel_charge,
            "travel_charge_km": travel["distance_km"] if travel else None,
            "travel_charge_source": travel["source"] if travel else None,
            "prepaid_only": prepaid,
            "total_amount": total_amount,
            # Legacy (before the wallet): late-cancellation charges a booking
            # carried as part of its total. New bookings carry a previous
            # balance as wallet_due_carried instead.
            "cancellation_charge": 0.0,
            "cancellation_charge_ids": [],
            "coupon_code": payload.coupon_code if not payload.subscription_id else None,
            "subscription_consumption": subscription_consumption,
            "customer_notes": payload.customer_notes,
            "alternate_contact_name": payload.alternate_contact_name,
            "alternate_contact_phone": payload.alternate_contact_phone,
            "vehicle_registration_number": registration_number,
            "customer_phone": phone,
            # Which channel this booking came in through ("app" |
            # "whatsapp" | "staff") — display/analytics metadata ONLY. It
            # deliberately changes nothing about how the booking behaves:
            # a WhatsApp booking IS a normal booking (same capacity
            # reservation above, same manager queue, same slot policy),
            # never a parallel second system.
            "source": source,
            # Real-road distance + ETA (Routes API; haversine-marked
            # fallback) — captain job card + KPI travel stats. Pricing
            # keeps using distance_km below, unchanged.
            **travel_estimate,
            "distance_km": split["distance_km"],
            "captain_travel_pay": split["captain_travel_pay"],
            "captain_service_pay": split["captain_service_pay"],
            "captain_earning": split["captain_earning"],
            "platform_earning": split["platform_earning"],
            # Set here (not via a follow-up write) since the actual notify()
            # call happens synchronously right after the transaction below —
            # this timestamp and that call are effectively the same instant.
            "manager_notified_at": None if awaiting_payment else (now_ist() if manager_id else None),
        }

        if _completed_by:
            done_at = _completed_by["service_at"]
            paid_cash = booking_doc["payment_method"] == PaymentMethod.CASH.value
            booking_doc.update({
                "status": BookingStatus.COMPLETED.value,
                "payment_status": PaymentStatus.PAID.value,
                "awaiting_assignment_since": None,
                "manager_notified_at": None,
                # Revenue reads closed_at, booking counts read created_at —
                # both are the service moment, so a job logged a day late
                # still lands in the day it was actually done.
                "created_at": done_at,
                "completed_at": done_at,
                "closed_at": done_at,
                "logged_at": now_ist(),
                "completed_by_id": _completed_by["id"],
                "completed_by_role": "manager",
                "wallet_settled": True,
                # The manager took the whole bill (spec 1.5 money fields).
                "amount_paid": total_amount,
                "amount_due": 0.0,
                "paid_cash": total_amount if paid_cash else 0.0,
                "paid_online": 0.0 if paid_cash else total_amount,
                **({"cash_collected_by": _completed_by["id"], "cash_collected_at": done_at} if paid_cash else {}),
            })

        # Everything above this point is read-only/pure computation — safe
        # to have run before entering the transaction. From here, exactly
        # two writes happen atomically: reserve this slot's (and the day's,
        # if configured) capacity, then insert the booking that consumes
        # it. If anything else fails after the reservation but before the
        # insert commits, MongoDB rolls the reservation back automatically —
        # no manual compensation needed for that path (see
        # _reserve_slot_capacity's docstring for the one exception, the
        # daily-cap-after-slot-cap case, which self-corrects even outside
        # a transaction).
        holds_seat = not _completed_by and (_group is None or bool(_group.get("reserve_seat")))
        # Seat OWNERSHIP is recorded on the booking itself, in the same
        # transaction as the counter it took (audit root cause 1: releases
        # used to infer it from stale reads and sibling positions). A visit's
        # later cars ride on the first car's seat; a logged job never holds one.
        booking_doc["holds_seat"] = False
        booking_doc["seat_key"] = None
        # The money fields as priced without the wallet — each transaction
        # attempt starts again from these.
        base_money = {k: booking_doc[k] for k in (
            "status", "awaiting_assignment_since", "payment_status", "total_amount", "platform_earning",
            "manager_notified_at", "cancellation_charge", "cancellation_charge_ids",
            "wallet_applied", "amount_paid", "wallet_due_carried", "amount_due",
        )}
        final_state = dict(state)

        async def _do_create(session):
            nonlocal final_state
            # Serializes every booking change of this customer (see
            # _touch_customer): two tabs creating overlapping bookings used to
            # both pass the overlap check, which ran before either existed.
            await self._touch_customer(customer_id, session)
            await self._raise_if_customer_conflict(customer_id, slot_start, slot_end, None, (_group or {}).get("id"), session=session)
            if holds_seat:
                await self._reserve_slot_capacity(
                    session, service_center, date_str, payload.scheduled_slot,
                    holder_ids=[customer_id, getattr(payload, "hold_key", None)],
                )
                booking_doc["holds_seat"] = True
                booking_doc["seat_key"] = self._seat_key(str(service_center["_id"]), date_str, payload.scheduled_slot)
            booking_doc.update(base_money)
            final_state = dict(state)
            if use_wallet or carry_due:
                # The customer's wallet meets this booking in THIS
                # transaction (MoneyService.apply_wallet_at_create, spec 1.1):
                # a negative balance rides on it as "previous balance due"
                # (part of its total — platform money, never a captain's
                # pay, never touched by a coupon or plan), a positive one is
                # spent on it (wallet_applied, debited now, guarded on the
                # balance — two concurrent bookings can't spend it twice). A
                # create that rolls back leaves the wallet untouched.
                wallet = await self.money.wallet.summary(customer_id, session=session)
                owes = wallet["previous_balance_due"] > bm.EPSILON
                if (owes and carry_due) or (not owes and use_wallet and wallet["credit_available"] > bm.EPSILON):
                    await self.money.apply_wallet_at_create(
                        customer_id, [booking_doc], session, actor={"id": customer_id, "role": "customer"},
                    )
                    carried = round(float(booking_doc.get("wallet_due_carried") or 0), 2)
                    applied = round(float(booking_doc.get("wallet_applied") or 0), 2)
                    final_state = money_state(carried, applied)
                    booking_doc.update({
                        "total_amount": final_state["total"],
                        "status": final_state["status"].value,
                        "awaiting_assignment_since": None if final_state["awaiting"] else now_ist(),
                        "payment_status": final_state["payment_status"],
                        "amount_due": final_state["due"],
                        "manager_notified_at": None if final_state["awaiting"] else (now_ist() if manager_id else None),
                    })
            return await self.repo.create(booking_doc, session=session)

        # The coupon use is RESERVED before the booking exists, on the id
        # the booking is about to get: record_usage's atomic total-limit
        # guard is the coupon's last-seat race, and recording it after the
        # insert let the loser of that race keep its discounted booking
        # anyway (the customer saw an error, the booking stood). record_usage
        # needs the coupon's real _id, not its human-readable code.
        booking_doc["_id"] = ObjectId()
        booking_id = str(booking_doc["_id"])
        if coupon is not None:
            await self.coupon_service.record_usage(str(coupon["_id"]), customer_id, booking_id)

        try:
            if _completed_by:
                # A finished job holds no capacity and sits outside the
                # active-slot unique index — a plain insert, no transaction.
                created = await self.repo.create(booking_doc)
            else:
                try:
                    async with await self.db.client.start_session() as session:
                        created = await session.with_transaction(_do_create)
                except DuplicateKeyError:
                    raise await self._duplicate_booking_error(
                        customer_id, booking_doc["visit_line_key"], payload.scheduled_date, payload.scheduled_slot,
                    )
        except Exception:
            if coupon is not None:
                await self._release_coupon_reservation(coupon, customer_id, booking_id)
            raise
        awaiting_payment, parked_free, initial_status = final_state["awaiting"], final_state["parked"], final_state["status"]

        if subscription_consumption is not None:
            try:
                await self.subscription_service.commit_consumption(payload.subscription_id, subscription_consumption)
            except Exception:
                # The pass ran out BETWEEN validating it and spending it —
                # two bookings racing for the last wash, which is exactly
                # what commit_consumption's optimistic lock is there to
                # catch. The booking row and its slot reservation are
                # already committed by the transaction above, so the loser
                # of that race would otherwise keep a live, dispatchable
                # booking that no plan ever paid for. Undo both.
                #
                # Safe to hard-delete here: nothing has been announced yet
                # (no history, no notification, no broadcast — those all
                # happen below), so no one has ever seen this booking.
                await self._rollback_uncommitted_booking(created, date_str, holds_seat=holds_seat)
                if coupon is not None:
                    await self._release_coupon_reservation(coupon, customer_id, booking_id)
                raise

        # From here the booking EXISTS (committed, plan wash spent). Nothing
        # below may turn that into an error for the caller (audit FAIL-04: a
        # database blip on the history row answered 500, the customer retried
        # into "you already have a booking") — each side effect is isolated
        # and logged instead.
        await self._post_commit(
            "history row",
            created,
            self._record_history(
                booking_id,
                initial_status,
                _completed_by["id"] if _completed_by else customer_id,
                "Logged as completed by the manager" if _completed_by else (
                    "Waiting for the visit's online payment" if parked_free
                    else "Booking created — waiting for online payment" if awaiting_payment else "Booking created"
                ),
            ),
        )
        # NOTHING is announced for an unpaid booking: no "booking confirmed"
        # to the customer, no "needs a captain" to the manager. Both go out
        # from confirm_awaiting_payment_booking the moment it becomes real.
        # A car on a visit is announced by create_booking_group, once, for
        # the whole visit — never one message per car.
        if not awaiting_payment and _group is None and not _completed_by:
            await self._post_commit(
                "announcement", created,
                self._announce_confirmed_booking(created, service_center.get("manager_id"), date_str, background=notify_background),
            )
        await self._post_commit("broadcast", created, self._broadcast_booking_changed(created))
        if not _completed_by:
            await self._post_commit("slot broadcast", created, self._broadcast_slots_changed(str(service_center["_id"]), date_str))
        return serialize_doc(created)

    @staticmethod
    async def _post_commit(what: str, booking: dict, step) -> None:
        """Run one side effect of an already-committed booking change —
        logged, never raised (FAIL-04)."""
        try:
            await step
        except Exception:  # noqa: BLE001
            logger.exception("Booking %s is saved, but its %s failed", (booking or {}).get("booking_number"), what)

    async def _duplicate_booking_error(self, customer_id: str, line_key: str | None, scheduled_date, scheduled_slot: str) -> "DuplicateBookingException":
        """The unique (customer, car line, day, slot) guard refused a second
        live booking — name the one that already holds it."""
        existing = None
        try:
            existing = await self.repo.collection.find_one(
                {"customer_id": customer_id, "visit_line_key": line_key, "scheduled_date": scheduled_date,
                 "scheduled_slot": scheduled_slot, "is_deleted": {"$ne": True},
                 "status": {"$in": list(BookingRepository.OPEN_STATUSES)}},
                {"booking_number": 1},
            )
        except Exception:  # noqa: BLE001 — the refusal itself is what matters
            logger.exception("Could not look up the duplicate booking")
        number = (existing or {}).get("booking_number")
        return DuplicateBookingException(
            f"You already have booking {number} for this vehicle in this slot." if number
            else "You already have a booking for this vehicle in this slot.",
            {"booking_id": str(existing["_id"]) if existing else None, "booking_number": number},
        )

    async def _void_payment_links(self, booking_ids: list[str], reason: str) -> None:
        """Cancel any still-unpaid payment link for these bookings, so nobody
        can pay for a job that isn't happening. Never blocks the caller: a
        gateway failure leaves the link to the reminder sweep's voiding."""
        if not booking_ids:
            return
        try:
            from app.services.payment_service import PaymentService

            await PaymentService(self.db).void_open_links(booking_ids, reason)
        except Exception:  # noqa: BLE001
            logger.exception("Could not void open payment links for %s", booking_ids)

    async def _rollback_uncommitted_booking(self, created: dict, date_str: str, *, holds_seat: bool) -> None:
        """Undo a booking that was inserted but couldn't be completed —
        remove the row and hand back its slot seat IF it took one
        (`holds_seat`: a visit's later cars ride on the first car's seat,
        and a logged job never takes one — releasing for those freed a
        stranger's seat). Best-effort and never raising: the caller is
        already raising the REAL error, and masking that with a cleanup
        failure would only make the problem harder to diagnose. A leaked
        reservation is visible in capacity reports; a swallowed root cause
        is not.

        The seat goes back only if the row being removed RECORDS holding it
        (holds_seat), in the same transaction as the delete — `holds_seat`
        is kept for callers' readability, the record decides."""
        async def _undo(session):
            # Wallet credit it spent goes back, a previous balance it carried
            # goes back onto the wallet's open debt — before the row goes.
            if float(created.get("wallet_applied") or 0) > 0 or float(created.get("wallet_due_carried") or 0) > 0:
                await self.money.on_booking_cancelled(
                    [created["_id"]], charge_amount=0, actor={"id": "system", "role": "system"}, session=session,
                    reason="booking could not be completed",
                )
            gone = await self.repo.collection.find_one_and_delete({"_id": created["_id"]}, session=session)
            if gone and gone.get("holds_seat"):
                key = self._seat_key_of(gone)
                await self._release_slot_capacity(key["service_center_id"], key["date"], key["slot_key"], session=session)
            if gone:
                # Charges it claimed go back on the customer's account.
                await self.charges.release_for_booking(session, gone, reason="was not completed")

        try:
            async with await self.db.client.start_session() as session:
                await session.with_transaction(_undo)
        except Exception:  # noqa: BLE001
            logger.exception("Could not roll back half-created booking %s", created.get("booking_number"))

    async def _release_coupon_reservation(self, coupon: dict, customer_id: str, booking_id: str) -> None:
        """Hand back a coupon use reserved for a booking that never came to
        be. Best-effort, same reasoning as _rollback_uncommitted_booking."""
        try:
            await self.coupon_service.reverse_usage(coupon["code"], customer_id, booking_id)
        except Exception:  # noqa: BLE001
            logger.exception("Could not release coupon %s reserved for booking %s", coupon.get("code"), booking_id)

    async def _announce_confirmed_booking(self, booking: dict, manager_id: str | None, date_str: str, background: bool = True) -> None:
        """The two messages a REAL booking sends: the customer's confirmation
        and the manager's "needs a captain". Shared by immediate confirmation
        (cash/plan) and by confirm_awaiting_payment_booking, so a booking
        confirmed by a payment reads exactly like any other.

        `background` defaults True: create_booking is on the customer's
        critical path to their Thank You page, and a slow (or momentarily
        down) WhatsApp API must never make them sit on a spinner waiting
        for a message that has nothing to do with whether their booking
        itself succeeded. The in-app notification row (and the manager's
        queue entry) is still written before this returns — only the
        outbound WhatsApp call happens after.

        The ONE caller that passes background=False is the WhatsApp bot
        (see create_booking's notify_background) — it immediately sends
        its OWN follow-up messages in the same conversation right after
        this returns ("Booking confirmed!", "How would you like to pay?"),
        so this announcement's WhatsApp send has to actually finish first
        or the customer can see them arrive out of order."""
        booking_id = str(booking["_id"])
        cars = await self._visit_cars(booking)
        if len(cars) > 1:
            # A visit is confirmed ONCE, from its first confirmed car. Cars
            # confirm one after another (an online visit settles car by
            # car), so "first" means first among those already confirmed —
            # including this one, which the read above may not show yet.
            confirmed = [c for c in cars if c.get("status") != BookingStatus.AWAITING_PAYMENT.value]
            if not any(str(c["_id"]) == booking_id for c in confirmed):
                confirmed = sorted(confirmed + [booking], key=lambda c: int(c.get("group_offset_minutes") or 0))
            if not self._leads_visit(booking, confirmed):
                return
        wa_services, wa_reference, wa_date, wa_slot, wa_vehicle, wa_code = await self._wa_details(booking, cars)
        reference = self._visit_numbers(cars) if len(cars) > 1 else booking["booking_number"]
        code = booking.get("service_code")
        code_line = f" Your service code is {code} — share it with the captain when they arrive." if code else ""
        # Worded like every other customer message — "12 Oct 2026", the car
        # type, the service once (audit NTF-07: it read "Star Wash booked,
        # Star Wash on 2026-10-08 at …", raw ISO date, no car).
        await self.notifications.notify(
            booking["customer_id"],
            "Booking confirmed",
            f"{wa_services} for your {wa_vehicle} on {wa_date}, {wa_slot} is confirmed. ({reference}){code_line}",
            NotificationType.BOOKING,
            booking_id,
            wa_event="booking_confirmed",
            wa_params=[wa_services, wa_reference, wa_date, wa_slot, wa_vehicle, wa_code],
            background=background,
        )
        await self._notify_managers_of_new_booking(booking, cars, manager_id, reference, wa_services, wa_date, wa_slot, background)

    async def _manager_recipients(self, center_id: str | None, primary_manager_id: str | None) -> list[str]:
        """Who hears about a new booking: the center's named manager plus
        every other active manager on that center (the two links are stored
        separately and drift), de-duplicated. Nobody at all -> the admins,
        so a booking never lands unseen.

        The center's stored manager_id counts only while it names a REAL,
        working manager: a deleted account, a garbage id or a user who is
        no longer a manager used to be kept as the only "recipient" — the
        alert went nowhere and the admin fallback below never fired
        (spec 1.7)."""
        ids: list[str] = []
        if primary_manager_id:
            pid = str(primary_manager_id)
            primary = (
                await self.user_repo.collection.find_one({"_id": ObjectId(pid)}, {"status": 1, "role": 1, "is_deleted": 1})
                if ObjectId.is_valid(pid) else None
            )
            # A switched-off (suspended / inactive) named manager hears nothing.
            if primary and primary.get("role") == "manager" and not primary.get("is_deleted") and not account_switched_off(primary):
                ids.append(pid)
        if center_id:
            rows, _ = await self.user_repo.find_many({"role": "manager", "service_center_id": center_id}, page=1, page_size=50)
            for u in rows:
                if not account_switched_off(u) and str(u["_id"]) not in ids:
                    ids.append(str(u["_id"]))
        if not ids:
            rows, _ = await self.user_repo.find_many({"role": "admin"}, page=1, page_size=3)
            ids = [str(u["_id"]) for u in rows if not account_switched_off(u)]
        return ids

    @staticmethod
    def _vehicle_summary(cars: list[dict]) -> str:
        counts: dict[str, int] = {}
        for c in cars:
            label = str(c.get("vehicle_label") or c.get("vehicle_type") or "Vehicle")
            counts[label] = counts.get(label, 0) + 1
        return ", ".join(label if n == 1 else f"{n} × {label}" for label, n in counts.items())

    @staticmethod
    def _short_area(address: dict | None) -> str:
        """The locality, not the whole geocoded label: the first two parts
        of line1 plus the city."""
        if not address:
            return "—"
        parts = [p.strip() for p in str(address.get("line1") or "").split(",") if p.strip()]
        area = ", ".join(parts[:2])
        city = str(address.get("city") or "").strip()
        if city and city != "—" and city.lower() not in area.lower():
            area = f"{area}, {city}" if area else city
        return (area or "—")[:90]

    async def _notify_managers_of_new_booking(
        self, booking: dict, cars: list[dict], primary_manager_id: str | None, reference: str,
        wa_services: str, wa_date: str, wa_slot: str, background: bool,
    ) -> None:
        """One short alert per manager: who, phone, what, when, where. Best
        effort — the booking is already committed, so nothing here may raise."""
        from app.utils.quiet_alerts import manager_new_booking_alerts_muted

        if manager_new_booking_alerts_muted():
            # A society visit day the manager scheduled himself — he gets
            # one line for the whole day instead (society_schedule_service).
            return
        try:
            recipients = await self._manager_recipients(booking.get("service_center_id"), primary_manager_id)
            if not recipients:
                return
            customer = await self.user_repo.find_by_id(booking["customer_id"]) or {}
            name = str(customer.get("full_name") or "Customer")
            phone = str(booking.get("customer_phone") or customer.get("phone") or "—")
            vehicles = self._vehicle_summary(cars)
            area = self._short_area(await self._address_of(booking))
            when = f"{wa_date} · {wa_slot}".strip(" ·") or "—"
            line = " · ".join([name, phone, vehicles, wa_services, when, area])
            # Every manager at once — one slow or failing send must neither
            # delay the others nor hold up the customer's own confirmation.
            # Each failure is logged with its manager (spec 1.7: they used
            # to vanish inside return_exceptions).
            results = await asyncio.gather(
                *(
                    self.notifications.notify(
                        user_id,
                        f"New booking — {wa_services}",
                        f"{line} — needs a captain. ({reference})",
                        NotificationType.BOOKING,
                        str(booking["_id"]),
                        wa_event="manager_new_booking",
                        wa_params=[name, phone, vehicles, wa_services, when, area],
                        background=background,
                    )
                    for user_id in recipients
                ),
                return_exceptions=True,
            )
            for user_id, result in zip(recipients, results):
                if isinstance(result, BaseException):
                    logger.error(
                        "New-booking alert for %s to manager %s failed: %r",
                        booking.get("booking_number"), user_id, result, exc_info=result,
                    )
        except Exception:  # noqa: BLE001
            logger.exception("Could not alert managers about new booking %s", booking.get("booking_number"))

    async def confirm_awaiting_payment_booking(self, booking_id: str, note: str, extra_update: dict | None = None, notify_background: bool = True) -> dict | None:
        """Promote an unpaid booking into the real queue. Exactly two callers:
        a signature-verified online payment (PaymentService._settle_booking_payment)
        and the customer choosing cash instead (switch_to_cash).

        The whole promotion — including any field the caller wants changed
        with it, e.g. payment_method -> cash — is ONE update guarded on the
        status we're leaving. So if a payment lands in the same instant the
        customer taps "pay cash instead", exactly one of them promotes the
        booking and the other is a no-op returning None, rather than both
        announcing it or the method being rewritten under a paid booking."""
        booking = await self.repo.find_by_id(booking_id)
        if not booking or booking.get("status") != BookingStatus.AWAITING_PAYMENT.value:
            return None
        _ensure_transition_allowed(booking["status"], BookingStatus.PENDING.value)

        service_center = await self.center_repo.find_by_id(booking["service_center_id"])
        manager_id = (service_center or {}).get("manager_id")
        updated = await self.repo.update_if(
            booking_id,
            {"status": BookingStatus.AWAITING_PAYMENT.value},
            {
                **(extra_update or {}),
                "status": BookingStatus.PENDING.value,
                "awaiting_assignment_since": now_ist(),
                "manager_notified_at": now_ist() if manager_id else None,
            },
        )
        if updated is None:
            return None

        date_str = to_ist(updated["scheduled_date"]).strftime("%Y-%m-%d")
        await self._record_history(booking_id, BookingStatus.PENDING, updated["customer_id"], note)
        # The visit's ₹0 plan cars were parked with it (create_booking_group)
        # and have nothing of their own to pay — they come in with it.
        promoted = [updated, *await self._confirm_parked_free_cars(updated, manager_id)]
        # Announced once per visit: from the first of the cars confirmed
        # just now, and only if no car of the visit was confirmed (and so
        # announced) by an earlier call — cars settle one by one, in any order.
        promoted_ids = {str(c["_id"]) for c in promoted}
        announced_before = any(
            str(c["_id"]) not in promoted_ids and c.get("status") != BookingStatus.AWAITING_PAYMENT.value
            for c in await self._visit_cars(updated)
        )
        if not announced_before:
            lead = min(promoted, key=lambda c: int(c.get("group_offset_minutes") or 0))
            await self._announce_confirmed_booking(lead, manager_id, date_str, background=notify_background)
        await self._broadcast_booking_changed(updated)
        await self._broadcast_slots_changed(updated["service_center_id"], date_str)
        return serialize_doc(updated)

    @staticmethod
    def _owes_nothing(car: dict) -> bool:
        return car.get("payment_status") == PaymentStatus.PAID.value or float(car.get("total_amount") or 0) <= 0

    async def _confirm_parked_free_cars(self, booking: dict, manager_id: str | None) -> list[dict]:
        """Confirm the cars of this booking's visit that wait only on the
        visit's payment (₹0 plan cars parked by create_booking_group), now
        that nothing on the visit is owed any more. Guarded per car, so a
        concurrent confirm promotes each one once. Returns those promoted."""
        if not booking.get("booking_group_id"):
            return []
        cars = await self._visit_cars(booking)
        if any(c.get("status") == BookingStatus.AWAITING_PAYMENT.value and not self._owes_nothing(c) for c in cars):
            return []
        promoted: list[dict] = []
        for car in cars:
            if str(car["_id"]) == str(booking["_id"]) or car.get("status") != BookingStatus.AWAITING_PAYMENT.value:
                continue
            done = await self.repo.update_if(
                str(car["_id"]),
                {"status": BookingStatus.AWAITING_PAYMENT.value},
                {"status": BookingStatus.PENDING.value, "awaiting_assignment_since": now_ist(), "manager_notified_at": now_ist() if manager_id else None},
            )
            if done:
                await self._record_history(str(car["_id"]), BookingStatus.PENDING, done["customer_id"], "Confirmed with the rest of the visit")
                await self._broadcast_booking_changed(done)
                promoted.append(done)
        return promoted

    async def switch_to_cash(self, booking_id: str, customer_id: str, notify_background: bool = True) -> dict:
        """"I couldn't finish the online payment — just let me pay the
        captain." Turns an unpaid online booking into a normal cash booking
        and confirms it, so an abandoned payment costs the customer their
        booking only if they want it to."""
        booking = await self.repo.find_by_id(booking_id)
        if not booking or booking.get("customer_id") != customer_id:
            raise NotFoundException("Booking not found")
        if booking.get("payment_status") == PaymentStatus.PAID.value:
            raise BadRequestException("This booking is already paid — there's nothing left to collect.")
        if await self.visit_is_prepaid(booking):
            raise BadRequestException(PREPAID_CASH_REFUSAL)
        if self._plan_extras_online_only(self._plan_rows(await self._visit_cars(booking))):
            raise BadRequestException(PLAN_EXTRAS_CASH_REFUSAL)
        if booking.get("status") != BookingStatus.AWAITING_PAYMENT.value:
            raise BadRequestException("This booking is already confirmed.")
        result = await self.confirm_awaiting_payment_booking(
            booking_id,
            "Switched to cash on service — confirmed without online payment",
            # A plan wash keeps its "subscription" label (what it owes is a
            # late-cancellation charge, collected in cash after the wash).
            {} if booking.get("subscription_id") else {"payment_method": PaymentMethod.CASH.value},
            notify_background=notify_background,
        )
        if result is None:
            # Lost a race — tell the customer what actually happened to it.
            now = await self.repo.find_by_id(booking_id)
            if now and now.get("payment_status") == PaymentStatus.PAID.value:
                raise BadRequestException("Your online payment just went through — this booking is already confirmed and paid.")
            if not now or now.get("status") == BookingStatus.CANCELLED.value:
                raise BadRequestException("This booking was released because its payment window ran out — please book again.")
            raise BadRequestException("This booking is already confirmed.")
        # The customer will pay the captain now — the payment link they were
        # sent must stop taking money, or paying it later is a second payment
        # (audit PAY-06).
        await self._void_payment_links([booking_id], "Switched to cash on service")
        return result

    @staticmethod
    def _plan_extras_online_only(rows: list[tuple[bool, float]]) -> bool:
        """Founder rule: what a PLAN wash adds on top of the plan (add-ons,
        a costlier swap) is paid before we come out — no cash on delivery.
        `rows` are (plan-covered, still-to-pay) per car or quote line. One
        rule for the quote (quote_visit's online_only) and for refusing a
        later switch to cash."""
        return any(covered and total > 0 for covered, total in rows)

    @staticmethod
    def _plan_rows(cars: list[dict]) -> list[tuple[bool, float]]:
        # A late-cancellation charge (legacy) or a previous wallet balance
        # (wallet_due_carried) a plan car carries is not one of the plan's
        # extras — it is paid like any booking (cash or online).
        return [
            (bool(c.get("subscription_id")), round(
                float(c.get("total_amount") or 0) - float(c.get("cancellation_charge") or 0) - float(c.get("wallet_due_carried") or 0), 2,
            ))
            for c in cars
        ]

    async def find_bookings_payment_reminder_due(self) -> list[dict]:
        """Unpaid bookings whose payment window is about to close and that
        haven't been nudged yet — one nudge, a few minutes before the slot
        would be released, so an abandoned checkout gets a second chance."""
        policy = await self.policy_service.get_policy()
        window = int(policy.get("payment_window_minutes", 30))
        before = int(policy.get("payment_reminder_minutes_before", 10))
        if before <= 0 or before >= window:
            return []
        cutoff = now_ist() - timedelta(minutes=window - before)
        rows = await self.repo.collection.find(
            {
                "status": BookingStatus.AWAITING_PAYMENT.value,
                "is_deleted": {"$ne": True},
                "payment_reminder_sent_at": None,
                "created_at": {"$lt": cutoff},
            }
        ).to_list(length=100)
        return self._one_per_visit(rows)

    async def mark_payment_reminder_sent(self, booking_id: str) -> None:
        await self._mark_visit(booking_id, {"payment_reminder_sent_at": now_ist()})

    async def find_customers_due_repeat_reminder(self, days: int, cooldown_days: int | None = None, limit: int = 200) -> list[dict]:
        """Customers whose last completed wash was `days` ago or more, with
        nothing booked and no live pass (a pass holder gets the pass wash
        reminder instead) — the ones a "time for a wash?" nudge is for.
        Each customer is nudged at most once per `cooldown_days` — by
        default the same `days`, so the weekly setting means a weekly
        nudge — and never if they've opted out of marketing.

        Reads users.last_completed_at (stamped by every completion path,
        see _after_visit_completed, and backfilled once at boot by
        backfill_last_completed_at) through the (role, last_completed_at)
        index, longest-lapsed first — no scan of the bookings collection.
        Every exclusion runs IN the pipeline, before the batch is cut:
        filtering after a fixed "longest-lapsed first" cut meant that once
        those customers sat in their cooldown, nobody behind them was ever
        reached. Each $lookup is an indexed equality match
        (bookings.customer_id, user_subscriptions.customer_id), and the
        stream stops as soon as `limit` customers pass."""
        now = now_ist()
        # last_completed_at / last_repeat_reminder_at are computed instants
        # (stored UTC) — compare against aware instants, not IST digits.
        cutoff = now - timedelta(days=days)
        reminded_floor = now - timedelta(days=cooldown_days if cooldown_days is not None else days)
        live_statuses = [
            BookingStatus.AWAITING_PAYMENT.value, BookingStatus.PENDING.value, BookingStatus.RESCHEDULED.value,
            BookingStatus.ASSIGNED.value, BookingStatus.CAPTAIN_ON_THE_WAY.value, BookingStatus.SERVICE_STARTED.value,
        ]

        def _none_for(collection: str, match: dict, alias: str) -> list[dict]:
            """Keep only customers with NO row in `collection` matching `match`."""
            return [
                {"$lookup": {
                    "from": collection,
                    # bookings/passes carry customer_id as a string; users are keyed by ObjectId.
                    "let": {"cid": {"$toString": "$_id"}},
                    "pipeline": [
                        {"$match": {"$expr": {"$eq": ["$customer_id", "$$cid"]}, **match}},
                        {"$limit": 1},
                        {"$project": {"_id": 1}},
                    ],
                    "as": alias,
                }},
                {"$match": {alias: {"$size": 0}}},
            ]

        pipeline = [
            {"$match": {
                "role": "customer",
                "last_completed_at": {"$lte": cutoff},
                "is_deleted": {"$ne": True},
                "is_active": {"$ne": False},
                # A switched-off account (suspended or inactive) gets no nudge.
                "status": {"$nin": [UserStatus.SUSPENDED.value, UserStatus.INACTIVE.value]},
                "marketing_opt_out": {"$ne": True},
                # Never reminded, or last reminded before the cooldown began.
                "last_repeat_reminder_at": {"$not": {"$gt": reminded_floor}},
            }},
            {"$sort": {"last_completed_at": 1}},
            *_none_for("bookings", {"status": {"$in": live_statuses}, "is_deleted": {"$ne": True}}, "_live_booking"),
            *_none_for("user_subscriptions", {"status": "active", "is_deleted": {"$ne": True}}, "_live_pass"),
            {"$limit": limit},
            {"$project": {"_live_booking": 0, "_live_pass": 0}},
        ]
        rows = await self.user_repo.collection.aggregate(pipeline).to_list(length=limit)
        return [{**row, "_last_completed": row.get("last_completed_at")} for row in rows]

    async def mark_repeat_reminder_sent(self, customer_id: str) -> None:
        await self.user_repo.update_by_id(customer_id, {"last_repeat_reminder_at": now_ist()})

    async def find_bookings_payment_expired(self) -> list[dict]:
        """Unpaid bookings whose payment window has run out — the sweep
        cancels these so a slot isn't held forever by an abandoned checkout."""
        policy = await self.policy_service.get_policy()
        cutoff = now_ist() - timedelta(minutes=policy.get("payment_window_minutes", 30))
        return await self.repo.collection.find(
            {
                "status": BookingStatus.AWAITING_PAYMENT.value,
                "is_deleted": {"$ne": True},
                "created_at": {"$lt": cutoff},
            }
        ).to_list(length=100)

    async def create_booking_group(
        self, customer_id: str, payload: BookingGroupCreateRequest, source: str = "app", allow_pinless: bool = False,
        notify_background: bool = True, apply_charges: bool | None = None,
    ) -> dict:
        """Several of one customer's cars wash on ONE visit.

        Each car becomes a REAL booking — its own plate verification, its own
        before/after photos, its own pass redemption, its own review. What
        makes them a visit rather than N separate jobs:

          - they share ONE slot seat. It's one address and one arrival, so
            the capacity a visit consumes is the capacity of a trip, not of
            five trips (founder call).
          - the captain's travel is paid ONCE, on the first car.
          - each car starts where the previous one finished
            (group_offset_minutes), so the captain's blocked time covers the
            whole visit and he can't be handed another job mid-way through.
          - they are created all-or-nothing: a failure on car four undoes
            cars one to three and hands the slot seat back.

        Cars are priced in ORDER, deliberately: the first-visit offer keys on
        "this phone has no earlier booking", so pricing sequentially means it
        applies to one car, not to all five at once."""
        policy = await self.policy_service.get_policy()
        limit = int(policy.get("max_vehicles_per_booking", 5))
        # A type line with quantity N is N cars ("2 SUVs, Foam Wash" = two
        # bookings with the same service) — expand before counting.
        cars: list[GroupVehicleRequest] = []
        for line in payload.vehicles:
            cars.extend([line] * (line.quantity if line.vehicle_type else 1))
        if len(cars) > limit:
            raise BadRequestException(f"You can book up to {limit} vehicles on one visit.")

        vehicle_ids = [v.vehicle_id for v in cars if v.vehicle_id]
        if len(set(vehicle_ids)) != len(vehicle_ids):
            raise BadRequestException("Each vehicle can only be added once to a visit.")

        # One trip, one payment: the distance charge and the prepaid rule
        # are decided for the whole visit before any car exists.
        terms = await self._visit_terms(cars)
        self._ensure_prepaid_method(terms, source, payload.payment_method)

        group_id = str(ObjectId())
        service_code = self.new_service_code()
        service_center = None
        date_str = payload.scheduled_date
        created: list[dict] = []
        offset = 0
        # What the VISIT pays once — the coupon, the customer's distance
        # charge and any late-cancellation charges on the account — rides on
        # the first car that pays (one not covered by a plan), else the first
        # car. A coupon on a ₹0 plan car was silently dropped, and the
        # distance charge on one made a plan car payable (audit PRICE-05/06).
        # quote_visit picks the same car.
        paying = self._paying_index(cars)
        if apply_charges is None:
            apply_charges = source == "app"
        try:
            for index, car in enumerate(cars):
                single = BookingCreateRequest(
                    vehicle_id=car.vehicle_id,
                    vehicle_type=car.vehicle_type,
                    address_id=payload.address_id,
                    service_ids=car.service_ids or None,
                    service_quantities=car.service_quantities or {},
                    combo_id=car.combo_id,
                    scheduled_date=payload.scheduled_date,
                    scheduled_slot=payload.scheduled_slot,
                    hold_key=payload.hold_key,
                    subscription_id=car.subscription_id,
                    # The visit's method is optional on the group request;
                    # a single car's isn't (None is refused) — default it
                    # the way a single booking does.
                    payment_method=payload.payment_method or PaymentMethod.CASH,
                    # A coupon applies to the visit, not to every car on it.
                    coupon_code=payload.coupon_code if index == paying else None,
                    customer_notes=payload.customer_notes,
                    alternate_contact_name=payload.alternate_contact_name,
                    alternate_contact_phone=payload.alternate_contact_phone,
                )
                # The FIRST car reserves the visit's single slot seat and
                # earns the trip's travel pay; every car after it rides on
                # both. All of them carry the group id from insert time, so
                # the conflict checks can tell siblings from strangers.
                booking = await self.create_booking(
                    customer_id,
                    single,
                    source=source,
                    _allow_pinless=allow_pinless,
                    _group={
                        "id": group_id,
                        "offset_minutes": offset,
                        "charge_travel": index == paying,
                        "captain_travel": index == 0,
                        # A previous balance due rides on the car that pays;
                        # wallet credit is spent on every car in order.
                        "carry_charges": bool(apply_charges) and index == paying,
                        "use_wallet": bool(apply_charges),
                        "reserve_seat": index == 0,
                        "line_index": index,
                        "service_code": service_code,
                        "terms": terms,
                        # Only a visit that CAN wait for an online payment
                        # parks its ₹0 cars (see create_booking).
                        "park_free": source == "app" or bool(terms["prepaid_service"]),
                    },
                )
                if index == 0:
                    service_center = booking["service_center_id"]
                created.append(booking)
                offset += int(booking.get("duration_minutes") or 60)

        except Exception:
            # All-or-nothing: a half-booked visit is worse than none, and the
            # slot seat must go back whether or not anything survived.
            # Quietly: the customer is being told this visit was refused —
            # a "Booking cancelled" for it too would contradict that.
            for booking in created:
                try:
                    await self.cancel_booking(
                        booking["id"],
                        BookingCancelRequest(reason="Multi-vehicle visit could not be completed."),
                        actor_id="system",
                        actor_role="admin",
                        _quiet=True,
                    )
                except Exception:  # noqa: BLE001
                    logger.exception("Could not undo booking %s from a failed visit", booking.get("booking_number"))
            raise

        # One visit, one payment state: a ₹0 plan car was created waiting
        # with its visit (create_booking's park_free) and is confirmed with
        # the car that pays (_confirm_parked_free_cars). When nothing on the
        # visit turned out to owe money, it is confirmed right here instead
        # — before anyone is told about the visit.
        if not any(b.get("status") == BookingStatus.AWAITING_PAYMENT.value and not self._owes_nothing(b) for b in created):
            for index, booking in enumerate(created):
                if booking.get("status") != BookingStatus.AWAITING_PAYMENT.value:
                    continue
                confirmed = await self.repo.update_if(
                    booking["id"],
                    {"status": BookingStatus.AWAITING_PAYMENT.value},
                    {"status": BookingStatus.PENDING.value, "awaiting_assignment_since": now_ist(), "manager_notified_at": now_ist()},
                )
                if confirmed:
                    await self._post_commit(
                        "history row", confirmed, self._record_history(booking["id"], BookingStatus.PENDING, customer_id, "Booking created"),
                    )
                    created[index] = serialize_doc(confirmed)

        # ONE confirmation for the visit. A cash/plan visit is real the
        # moment it exists; an online one announces itself from the payment
        # (confirm_awaiting_payment_booking), also once. Best effort: the
        # visit is saved (FAIL-04).
        async def _announce_visit() -> None:
            first = await self.repo.find_by_id(created[0]["id"])
            if first and first.get("status") != BookingStatus.AWAITING_PAYMENT.value:
                center_doc = await self.center_repo.find_by_id(first["service_center_id"])
                await self._announce_confirmed_booking(first, (center_doc or {}).get("manager_id"), date_str, background=notify_background)

        await self._post_commit("announcement", created[0], _announce_visit())

        total = round(sum(float(b.get("total_amount") or 0) for b in created), 2)
        return {
            "booking_group_id": group_id,
            "bookings": created,
            "vehicle_count": len(created),
            "service_code": service_code,
            "total_amount": total,
            "cancellation_charge": round(sum(float(b.get("cancellation_charge") or 0) for b in created), 2),
            "wallet_applied": round(sum(float(b.get("wallet_applied") or 0) for b in created), 2),
            "wallet_due_carried": round(sum(float(b.get("wallet_due_carried") or 0) for b in created), 2),
            "amount_due": round(sum(float(b.get("amount_due") or 0) for b in created), 2),
            # How long the whole visit runs — what the customer is told, and
            # what stops five cars being sold as a 45-minute job.
            "total_duration_minutes": offset,
            "service_center_id": service_center,
            "scheduled_date": date_str,
            "scheduled_slot": payload.scheduled_slot,
            "confirmation_token": created[0].get("confirmation_token"),
        }

    async def create_quick_booking(self, payload: QuickBookingRequest, *, customer: dict, source: str = "app", allow_pinless: bool = False, notify_background: bool = True) -> dict:
        """The 2026-09 quick-booking model, end to end: a customer profile
        (found or created by the caller from the phone — see
        AuthService.ensure_customer_by_phone), an address (saved or created
        here), and one visit of one or more vehicle TYPES. No vehicle
        records, no OTP, no login.

          - lines expand to cars ("2 SUVs" = two bookings, same service);
            ONE car goes through create_booking, more through
            create_booking_group — both existing, tested paths.
          - a logged-in customer's matching pass is applied automatically
            (same vehicle type + main service) — nothing to pick.
          - online payment: the visit is created awaiting payment and a
            Razorpay payment link is returned (and the customer is
            reminded on WhatsApp) — paying it confirms the visit, exactly
            like the WhatsApp bot's own pay link.

        Returns the same shape for one car or many: bookings, totals, the
        4-digit service_code, and payment_link when there's one to pay."""
        customer_id = str(customer["_id"])
        saved_address = None
        if payload.address_id:
            saved_address = await self.address_repo.find_by_id(payload.address_id)
            if not saved_address or saved_address.get("owner_id") != customer_id:
                raise NotFoundException("Address not found")

        # -- the price the customer saw (audit PRICE-04) -------------------
        # Checked before ANYTHING is written (address, booking, seat): the
        # same pricing code the booking runs (quote_visit), for this customer.
        if payload.expected_total is not None:
            preview_address = saved_address or (
                {**payload.address.model_dump(), "pincode": payload.address.pincode or ""} if payload.address else None
            )
            preview = await self.quote_visit(
                customer_id=customer_id, phone=customer.get("phone"), lines=payload.lines, address=preview_address,
                coupon_code=payload.coupon_code, source=source, scheduled_date=payload.scheduled_date,
            )
            self._ensure_expected_total(payload.expected_total, preview["total_amount"], preview.get("previous_balance_due"))

        # -- where --------------------------------------------------------
        if payload.address_id:
            address_id = payload.address_id
        else:
            addr = payload.address
            # A returning customer's same place (saved, or used on an earlier
            # booking) is reused — never a fresh copy per booking. Every car
            # of the visit then shares this one address.
            match, has_saved = await AddressService(self.db).find_same_place(customer_id, addr.model_dump())
            if match:
                address_id = str(match["_id"])
            else:
                pincode = (addr.pincode or "").strip()
                if not pincode:
                    # A dropped pin whose reverse-geocode had no postal code:
                    # the pin alone decides coverage, and the covering
                    # center's own pincode stands in on the address record.
                    center, _ = await self._resolve_service_center(
                        {"latitude": addr.latitude, "longitude": addr.longitude, "pincode": ""}, allow_pinless=allow_pinless
                    )
                    pincode = str((center.get("location") or {}).get("pincode") or center.get("pincode") or "000000")
                created_addr = await AddressService(self.db).create(
                    customer_id,
                    AddressCreateRequest(
                        label="Home",
                        line1=addr.line1.strip(),
                        landmark=addr.landmark,
                        city=addr.city or "—",
                        state=addr.state or "—",
                        pincode=pincode,
                        latitude=addr.latitude,
                        longitude=addr.longitude,
                        is_default=not has_saved,
                    ),
                )
                address_id = created_addr["id"]

        # -- what: type lines -> cars, with the customer's passes applied ---
        cars = await self._cars_for_lines(customer_id, payload.lines, scheduled_date=payload.scheduled_date)

        scheduled_date = datetime.strptime(payload.scheduled_date, "%Y-%m-%d")
        group_id: str | None = None
        if len(cars) == 1:
            car = cars[0]
            booking = await self.create_booking(
                customer_id,
                BookingCreateRequest(
                    vehicle_type=car.vehicle_type,
                    vehicle_id=car.vehicle_id,
                    address_id=address_id,
                    service_ids=car.service_ids,
                    service_quantities=car.service_quantities,
                    scheduled_date=scheduled_date,
                    scheduled_slot=payload.scheduled_slot,
                    payment_method=payload.payment_method,
                    coupon_code=payload.coupon_code,
                    subscription_id=car.subscription_id,
                    customer_notes=payload.customer_notes,
                    alternate_contact_name=payload.alternate_contact_name,
                    alternate_contact_phone=payload.alternate_contact_phone,
                    hold_key=payload.hold_key,
                ),
                source=source,
                _allow_pinless=allow_pinless,
                notify_background=notify_background,
                # Every caller is an interactive booking for this customer
                # (website/app, the WhatsApp bot, staff on their behalf).
                apply_charges=True,
            )
            raw_cars = [await self.repo.find_by_id(booking["id"])]
            service_code = booking.get("service_code")
        else:
            visit = await self.create_booking_group(
                customer_id,
                BookingGroupCreateRequest(
                    vehicles=cars,
                    address_id=address_id,
                    scheduled_date=payload.scheduled_date,
                    scheduled_slot=payload.scheduled_slot,
                    hold_key=payload.hold_key,
                    payment_method=payload.payment_method,
                    coupon_code=payload.coupon_code,
                    customer_notes=payload.customer_notes,
                    alternate_contact_name=payload.alternate_contact_name,
                    alternate_contact_phone=payload.alternate_contact_phone,
                ),
                source=source,
                allow_pinless=allow_pinless,
                notify_background=notify_background,
                apply_charges=True,
            )
            group_id = visit["booking_group_id"]
            raw_cars = [await self.repo.find_by_id(b["id"]) for b in visit["bookings"]]
            service_code = visit.get("service_code")

        raw_cars = [c for c in raw_cars if c]
        bookings = await self._enrich_bookings(raw_cars)
        # Staff and bot bookings park only for a prepaid visit; the link is
        # how that customer pays (settlement confirms it, the payment-window
        # sweep releases it otherwise).
        extra = await self._park_or_confirm_result(raw_cars, customer, source)

        return {
            "booking_group_id": group_id,
            "bookings": bookings,
            "vehicle_count": len(bookings),
            "total_amount": extra["visit_total"],
            "service_code": service_code,
            "booking_numbers": [b.get("booking_number") for b in bookings],
            "scheduled_date": payload.scheduled_date,
            "scheduled_slot": payload.scheduled_slot,
            "awaiting_payment": extra["awaiting_payment"],
            "payment_link": extra["payment_link"],
            "customer_id": customer_id,
            "travel_charge": extra["travel_charge"],
            "prepaid_only": extra["prepaid_only"],
            "cancellation_charge": extra["cancellation_charge"],
            "wallet_applied": extra["wallet_applied"],
            "wallet_due_carried": extra["wallet_due_carried"],
            "amount_due": extra["amount_due"],
            "payment_method": payload.payment_method.value if hasattr(payload.payment_method, "value") else payload.payment_method,
        }

    async def precheck_quick_booking(self, payload: QuickBookingRequest, *, source: str = "app", allow_pinless: bool = False) -> None:
        """The refusals a quick booking can hit that don't depend on WHO is
        booking — what, where, when and how it's paid. Run before an
        anonymous caller's one-time code is checked, because checking a code
        spends it: a booking refused for being out of area, past its slot,
        prepaid-but-cash or on a retired service must leave the code usable
        for the corrected retry. create_quick_booking repeats every check;
        this only moves the cheap ones ahead of the code. Deliberately
        identity-free (no passes, no account lookups), so an unproven caller
        learns nothing about the phone's owner from a refusal."""
        cars = [
            GroupVehicleRequest(
                vehicle_type=line.vehicle_type, quantity=1, service_ids=list(line.service_ids),
                service_quantities=dict(line.service_quantities or {}),
            )
            for line in payload.lines
            for _ in range(line.quantity)
        ]
        policy = await self.policy_service.get_policy()
        limit = int(policy.get("max_vehicles_per_booking", 5))
        if len(cars) > limit:
            raise BadRequestException(f"You can book up to {limit} vehicles on one visit.")
        for car in cars:
            vt_doc = await self.vehicle_type_repo.find_by_id(car.vehicle_type) if car.vehicle_type else None
            if not vt_doc or not vt_doc.get("is_active", True):
                raise BadRequestException("Pick a valid vehicle type.")
            await self._car_services(car)
        self._ensure_prepaid_method(await self._visit_terms(cars), source, payload.payment_method)

        if payload.address is not None:
            addr = payload.address
            probe = {"latitude": addr.latitude, "longitude": addr.longitude, "pincode": (addr.pincode or "").strip()}
            center, _ = await self._resolve_service_center(probe, allow_pinless=allow_pinless)
        else:
            saved = await self.address_repo.find_by_id(payload.address_id) if payload.address_id else None
            if not saved:
                return  # create_quick_booking reports it, with the owner check
            center, _ = await self._resolve_service_center(saved, allow_pinless=allow_pinless)
        try:
            scheduled_date = datetime.strptime(payload.scheduled_date, "%Y-%m-%d")
        except ValueError:
            raise BadRequestException("Pick a valid date.")
        _ensure_within_advance_window(scheduled_date, policy)
        _, slot_end = _resolve_slot_window(center, scheduled_date, payload.scheduled_slot, policy)
        if _slot_cutoff_passed(slot_end, policy):
            raise BadRequestException("This slot is no longer available to book — please pick another slot.")
        # Read-only mirror of _reserve_slot_capacity's guard: the booker's own
        # hold (by hold_key) is theirs to convert; anyone else's counts.
        slot_filter = {"service_center_id": str(center["_id"]), "date": to_ist(scheduled_date).strftime("%Y-%m-%d"), "slot_key": payload.scheduled_slot}
        seat = await self.slot_capacity_repo.find_one(slot_filter)
        if seat:
            if seat.get("is_closed"):
                raise BadRequestException("This slot has been closed for booking — please pick another.")
            mine = bool(payload.hold_key) and await self.db.slot_holds.find_one({**slot_filter, "holder_id": payload.hold_key}) is not None
            occupied = int(seat.get("booked_count") or 0) + (0 if mine else int(seat.get("held_count") or 0))
            if occupied >= int(seat.get("capacity") or 0):
                raise BadRequestException("This slot just became fully booked — please pick another.")

    async def staff_booking_center_id(self, *, address_id: str | None, address=None, allow_pinless: bool = True) -> str | None:
        """Which center a staff-created booking would land in — resolved
        exactly like the booking itself resolves it (_resolve_service_center
        on the saved or typed address). Lets the controller refuse a
        MANAGER booking into another center's slots before anything is
        written. None when there's nothing to resolve yet (the create path
        reports that itself)."""
        if address is not None:
            probe = {
                "latitude": getattr(address, "latitude", None),
                "longitude": getattr(address, "longitude", None),
                "pincode": (getattr(address, "pincode", None) or "").strip(),
            }
        elif address_id:
            probe = await self.address_repo.find_by_id(address_id)
            if not probe:
                return None
        else:
            return None
        center, _ = await self._resolve_service_center(probe, allow_pinless=allow_pinless)
        return str(center["_id"])

    async def _send_visit_payment_link(self, raw_cars: list[dict], customer: dict, source: str) -> str | None:
        """Mint one Razorpay link for a parked visit's unpaid cars and tell
        the customer (payment_pending). Best effort: None when the link
        can't be made — the booking stays parked and payable from the app."""
        from app.services.payment_service import PaymentService

        # The link's booking must be one that actually owes money — a
        # pass-covered car on the same visit is already PAID (₹0), and
        # binding the link to it would fail settlement on amount.
        unpaid = [c for c in raw_cars if c.get("payment_status") != PaymentStatus.PAID.value]
        first = unpaid[0] if unpaid else raw_cars[0]
        try:
            link = await PaymentService(self.db).create_payment_link(
                first, contact_phone=customer.get("phone"), name=customer.get("full_name"), cars=unpaid
            )
        except Exception:  # noqa: BLE001
            logger.exception("Could not create a payment link for booking %s", first.get("booking_number"))
            return None
        payment_link = link["short_url"]
        policy = await self.policy_service.get_policy()
        window = int(policy.get("payment_window_minutes", 30))
        reference = " + ".join(str(c.get("booking_number") or "") for c in raw_cars)
        wa_services, wa_reference, _wa_date, _wa_slot, _wa_vehicle, _wa_code = await self._wa_details(first, raw_cars)
        await self.notifications.notify(
            str(customer["_id"]),
            "Finish paying to confirm your booking",
            f"Your booking {reference} is waiting for payment. Pay within {window} minutes to keep your slot: {payment_link}",
            NotificationType.BOOKING,
            str(first["_id"]),
            wa_event="payment_pending",
            wa_params=[wa_services, wa_reference, str(window)],
            background=True,
            # The bot posts the link in its own chat reply.
            send_whatsapp=source != "whatsapp",
        )
        return payment_link

    # ------------------------------------------------------------------
    # Jobs the MANAGER did himself (phone-in / walk-in). Two entry points:
    #   create_manager_logged_visit — a new job, saved directly as COMPLETED
    #   manager_mark_done           — an existing, not-yet-started booking
    # Neither runs the captain workflow, so neither needs photos, and the
    # customer hears ONE thing at most: "service done" (a switch).
    # ------------------------------------------------------------------

    _MANAGER_DONE_FROM = frozenset({BookingStatus.PENDING.value, BookingStatus.RESCHEDULED.value, BookingStatus.ASSIGNED.value})

    async def create_manager_logged_visit(
        self, payload: ManagerLogBookingRequest, *, manager_id: str, manager_center_id: str | None
    ) -> dict:
        if not manager_center_id:
            raise BadRequestException("Your account isn't linked to a service center yet — ask the admin to assign one.")
        center = await self.center_repo.find_by_id(manager_center_id)
        if not center:
            raise NotFoundException("Service center not found")

        # -- when: a clock time in the past, filed under the slot holding it --
        try:
            service_at = to_ist(datetime.strptime(f"{payload.scheduled_date} {payload.service_time}", "%Y-%m-%d %H:%M"))
        except ValueError:
            raise BadRequestException("Enter a valid date and time.")
        now = now_ist()
        if service_at > now:
            raise BadRequestException("You can only log a job that has already happened — pick a time that has passed.")
        if service_at < now - timedelta(days=LOG_MAX_AGE_DAYS):
            raise BadRequestException(f"Jobs older than {LOG_MAX_AGE_DAYS} days can't be logged.")
        policy = await self.policy_service.get_policy()
        open_at, close_at = center_hours(center)
        slot = next((sl for sl in center_slots(center, policy) if sl["start"] <= payload.service_time < sl["end"]), None)
        if not slot:
            raise BadRequestException(f"Pick a time within working hours ({open_at}–{close_at}).")

        # Only now — every check that can refuse the job has passed, so a
        # refusal never leaves a stray customer profile behind.
        from app.services.auth_service import AuthService

        # A suspended customer is a refusal of THIS job: ensure_customer_by_phone
        # answers 400 ACCOUNT_INACTIVE (never a 401 that would log the manager out).
        customer = await AuthService(self.db).ensure_customer_by_phone(payload.customer_phone, payload.customer_name)
        customer_id = str(customer["_id"])

        # A double-tap or a retried timeout must not log the same job twice
        # (and message the customer twice). Two separate jobs for one
        # customer at the exact same minute isn't a real case. The check
        # below alone was a find-then-insert two concurrent submits both
        # passed — so the (customer, minute) is claimed first, through a
        # unique _id, for as long as this log is being written.
        lock_id = f"manager_log:{customer_id}:{service_at.isoformat()}"
        if not await self._claim_booking_lock(lock_id):
            raise BadRequestException("This job is already being logged for that customer at that time — check the booking queue.")
        try:
            if await self.repo.collection.find_one(
                {"customer_id": customer_id, "completed_by_role": "manager", "created_at": service_at, "is_deleted": {"$ne": True}},
                {"_id": 1},
            ):
                raise BadRequestException("This job is already logged for that customer at that time — check the booking queue.")
            return await self._log_visit_for(customer_id, payload, center, service_at, slot, manager_id)
        finally:
            await self.db.booking_locks.delete_one({"_id": lock_id})

    # A claim older than this belongs to a request that died mid-write.
    BOOKING_LOCK_SECONDS = 120

    async def _claim_booking_lock(self, lock_id: str) -> bool:
        """Insert-first mutual exclusion across instances: the unique _id is
        the lock. A stale claim (its request crashed) is taken over."""
        now = datetime.now(timezone.utc)
        try:
            await self.db.booking_locks.insert_one({"_id": lock_id, "created_at": now, "expires_at": now + timedelta(seconds=self.BOOKING_LOCK_SECONDS)})
            return True
        except DuplicateKeyError:
            taken = await self.db.booking_locks.find_one_and_update(
                {"_id": lock_id, "expires_at": {"$lt": now}},
                {"$set": {"created_at": now, "expires_at": now + timedelta(seconds=self.BOOKING_LOCK_SECONDS)}},
            )
            return taken is not None

    async def _log_visit_for(
        self, customer_id: str, payload: ManagerLogBookingRequest, center: dict, service_at: datetime, slot: dict, manager_id: str
    ) -> dict:
        """create_manager_logged_visit's write half, run while it holds the
        (customer, minute) claim."""
        # -- where: typed text on the center's own pincode; no pin, no coverage check
        line1 = payload.address_line.strip()
        loc = center.get("location") or {}
        # Same-place reuse on the text alone: the center's pincode is only a
        # stand-in here, so it must not veto a saved copy's real pincode.
        match, has_saved = await AddressService(self.db).find_same_place(
            customer_id, {"line1": line1, "landmark": payload.landmark, "city": loc.get("city"), "state": loc.get("state")}
        )
        if match:
            address_id = str(match["_id"])
        else:
            created_addr = await AddressService(self.db).create(
                customer_id,
                AddressCreateRequest(
                    label="Home", line1=line1, landmark=payload.landmark,
                    city=loc.get("city") or "—", state=loc.get("state") or "—",
                    pincode=str(loc.get("pincode") or "000000"), is_default=not has_saved,
                ),
            )
            address_id = created_addr["id"]

        cars = await self._cars_for_lines(
            customer_id, payload.lines, as_of=service_at, scheduled_date=payload.scheduled_date, log_center_id=str(center["_id"]),
        )
        scheduled_date = datetime.strptime(payload.scheduled_date, "%Y-%m-%d")
        completed_by = {"id": manager_id, "service_center": center, "service_at": service_at, "payment_method": payload.payment_method}
        group_id = str(ObjectId()) if len(cars) > 1 else None
        service_code = self.new_service_code()
        created: list[dict] = []
        offset = 0
        try:
            for index, car in enumerate(cars):
                booking = await self.create_booking(
                    customer_id,
                    BookingCreateRequest(
                        vehicle_type=car.vehicle_type,
                        vehicle_id=car.vehicle_id,
                        address_id=address_id,
                        service_ids=car.service_ids,
                        service_quantities=car.service_quantities,
                        scheduled_date=scheduled_date,
                        scheduled_slot=slot["key"],
                        payment_method=payload.payment_method,
                        subscription_id=car.subscription_id,
                        customer_notes=payload.customer_notes,
                    ),
                    source="staff",
                    _group=(
                        {"id": group_id, "offset_minutes": offset, "charge_travel": index == 0, "reserve_seat": False,
                         "line_index": index, "service_code": service_code}
                        if group_id else None
                    ),
                    _completed_by=completed_by,
                )
                created.append(booking)
                offset += int(booking.get("duration_minutes") or 60)
            if payload.discount_amount > 0:
                created = await self._apply_logged_discount(created, payload.discount_amount, manager_id)
        except Exception:
            # All-or-nothing, like a real visit: nothing was announced, so
            # the half-logged cars are simply removed (and any pass value
            # they spent handed back).
            for booking in created:
                await self._discard_logged_booking(booking["id"])
            raise

        if payload.tip_amount > 0:
            await self._record_tip(created[0]["id"], [b["id"] for b in created], payload.tip_amount, manager_id, payload.tip_method)
        raw_cars = [c for c in [await self.repo.find_by_id(b["id"]) for b in created] if c]
        # A wash taken off the customer's pass is ALWAYS told to them (MGR-01)
        # — the "service done" message carries the plan note ("3 washes
        # left"), on WhatsApp too even with the switch off.
        plan_used = any(c.get("subscription_id") for c in raw_cars)
        await self._notify_service_done(raw_cars, customer_id, payload.send_whatsapp or plan_used)
        await self._after_visit_completed(customer_id, raw_cars, service_at, payload.send_whatsapp or plan_used)
        bookings = await self._enrich_bookings(raw_cars)
        return {
            "booking_group_id": group_id,
            "bookings": bookings,
            "vehicle_count": len(bookings),
            "total_amount": round(sum(float(b.get("total_amount") or 0) for b in bookings), 2),
            "booking_numbers": [b.get("booking_number") for b in bookings],
            "scheduled_date": payload.scheduled_date,
            "scheduled_slot": slot["key"],
            "customer_id": customer_id,
        }

    async def _apply_logged_discount(self, created: list[dict], discount: float, actor_id: str | None = None) -> list[dict]:
        """The manager gave the customer `discount` rupees off this visit.
        Split across the cars in proportion to what each costs, in WHOLE
        rupees that add up to exactly the discount (₹50 on ₹349 + ₹499 is
        ₹21 + ₹29, never ₹20.58 + ₹29.42), and recorded as the booking's own
        discount so total, platform earning and the cash ledger all show
        what was really paid. Runs before anything is announced; a refusal
        here unwinds the whole visit like any other failure."""
        totals = [round(float(b.get("total_amount") or 0), 2) for b in created]
        bill = round(sum(totals), 2)
        discount = round_rupees(discount)
        if discount > bill:
            raise BadRequestException(
                f"The discount can't be more than the bill (₹{bill:g})." if bill > 0 else "There is nothing to discount on this job."
            )
        shares = split_whole_rupees(discount, totals, caps=totals)
        updated: list[dict] = []
        now = now_ist()
        for index, booking in enumerate(created):
            share = float(shares[index])
            new_total = round(totals[index] - share, 2)
            # A logged job pays no captain (captain_earning 0): the discount
            # comes off the platform's share only. Who gave it, and when, is
            # recorded for the admin.
            fields = {
                "total_amount": new_total,
                "platform_earning": new_total,
                "discount_amount": round(float(booking.get("discount_amount") or 0) + share, 2),
                "manager_discount": share,
                "manager_discount_by": actor_id,
                "manager_discount_at": now,
                # What the manager actually took is the discounted bill.
                "amount_paid": new_total,
                "amount_due": 0.0,
                "paid_cash": new_total if booking.get("payment_method") == PaymentMethod.CASH.value else 0.0,
                "paid_online": 0.0 if booking.get("payment_method") == PaymentMethod.CASH.value else new_total,
            }
            await self.repo.update_by_id(booking["id"], fields)
            updated.append({**booking, **fields})
        return updated

    async def _discard_logged_booking(self, booking_id: str) -> None:
        try:
            raw = await self.repo.find_by_id(booking_id)
            if raw and raw.get("subscription_id") and raw.get("subscription_consumption"):
                await self.subscription_service.restore_consumption(raw["subscription_id"], raw["subscription_consumption"])
            await self.repo.collection.delete_one({"_id": ObjectId(booking_id)})
            await self.history_repo.collection.delete_many({"booking_id": booking_id})
        except Exception:  # noqa: BLE001
            logger.exception("Could not discard half-logged booking %s", booking_id)

    async def _plan_usage_note(self, car: dict) -> str:
        """One extra sentence for the service-done message when this wash
        was covered by a plan — "Booked from your Monthly Shine plan · 3
        washes left · valid till 12 Oct." Empty string when the visit
        wasn't plan-covered, or best-effort empty on any lookup failure —
        this must never block the completion notification itself."""
        subscription_id = car.get("subscription_id")
        if not subscription_id or not ObjectId.is_valid(subscription_id):
            return ""
        try:
            sub = await self.repo.db.user_subscriptions.find_one({"_id": ObjectId(subscription_id)})
            if not sub:
                return ""
            plan_name = "your plan"
            if sub.get("plan_id") and ObjectId.is_valid(sub["plan_id"]):
                plan = await self.repo.db.subscription_plans.find_one({"_id": ObjectId(sub["plan_id"])}, {"name": 1})
                if plan and plan.get("name"):
                    plan_name = plan["name"]
            parts = [f"Booked from your {plan_name} plan"]
            remaining = sub.get("remaining_service_count")
            if remaining is not None:
                parts.append(f"{remaining} wash{'es' if remaining != 1 else ''} left")
            end_date = sub.get("end_date")
            if end_date:
                parts.append(f"valid till {from_stored(end_date).strftime('%d %b')}")
            return " · ".join(parts) + "."
        except Exception:  # noqa: BLE001
            logger.exception("Could not build a plan-usage note for subscription %s", subscription_id)
            return ""

    async def _after_visit_completed(self, customer_id: str | None, cars: list[dict], at: datetime, send_whatsapp: bool = True) -> None:
        """Bookkeeping every completion path shares (captain after-photo,
        manager mark-done, manager-logged job): moves the customer's
        last_completed_at forward — what the repeat-booking nudge reads —
        and, when the wash just done was a pass's last one, sends the
        one-time "used all washes" note. Best effort: the job is saved."""
        try:
            if customer_id and ObjectId.is_valid(customer_id):
                # $max: a backdated logged job never moves it backwards.
                await self.user_repo.collection.update_one({"_id": ObjectId(customer_id)}, {"$max": {"last_completed_at": at}})
        except Exception:  # noqa: BLE001
            logger.exception("Could not stamp last_completed_at for customer %s", customer_id)
        for subscription_id in {c.get("subscription_id") for c in cars if c.get("subscription_id")}:
            try:
                await self._notify_pass_used_up(subscription_id, send_whatsapp)
            except Exception:  # noqa: BLE001
                logger.exception("Could not send the used-all-washes note for pass %s", subscription_id)

    async def _notify_pass_used_up(self, subscription_id: str, send_whatsapp: bool = True) -> None:
        """"You've used all washes" — once per pass, and only once its last
        wash is actually DONE (another booked wash still to come means it
        isn't used up yet; a cancelled one hands the wash back). An auto-pay
        pass never gets it: it stays active at 0 until its refill."""
        from app.services.subscription_service import claim_used_up_notice

        live = [
            BookingStatus.AWAITING_PAYMENT.value, BookingStatus.PENDING.value, BookingStatus.RESCHEDULED.value,
            BookingStatus.ASSIGNED.value, BookingStatus.CAPTAIN_ON_THE_WAY.value, BookingStatus.SERVICE_STARTED.value,
        ]
        if await self.repo.collection.find_one(
            {"subscription_id": subscription_id, "status": {"$in": live}, "is_deleted": {"$ne": True}}, {"_id": 1}
        ):
            return
        sub = await claim_used_up_notice(self.db, subscription_id)
        if not sub or not sub.get("customer_id"):
            return
        plan_id = str(sub.get("plan_id") or "")
        plan = await self.db.subscription_plans.find_one({"_id": ObjectId(plan_id)}, {"name": 1}) if ObjectId.is_valid(plan_id) else None
        plan_name = (plan or {}).get("name") or "Monthly"
        await self.notifications.notify(
            sub["customer_id"],
            "All washes used",
            f"You've used all washes on your {plan_name} pass. Buy it again from your dashboard.",
            NotificationType.SYSTEM,
            subscription_id,
            background=True,
            send_whatsapp=send_whatsapp,
        )

    async def _notify_service_done(self, cars: list[dict], customer_id: str, send_whatsapp: bool) -> None:
        """The ONLY customer message for a manager-done job: "service done",
        once per visit. With the switch off the in-app row is still written
        and WhatsApp stays silent. Best effort — the job is already saved."""
        if not cars:
            return
        try:
            lead = cars[0]
            wa_services, wa_reference, wa_date, wa_slot, wa_vehicle, wa_code = await self._wa_details(lead, cars)
            reference = self._visit_numbers(cars) if len(cars) > 1 else str(lead.get("booking_number") or "")
            done = f"Booking {reference} is complete." if len(cars) == 1 else f"All {len(cars)} vehicles are done ({reference})."
            plan_note = await self._plan_usage_note(lead)
            message = f"{done} {plan_note} Thanks for choosing Blussit!" if plan_note else f"{done} Thanks for choosing Blussit!"
            await self.notifications.notify(
                customer_id,
                "Service completed",
                message,
                NotificationType.BOOKING,
                str(lead["_id"]),
                wa_event="service_completed",
                wa_params=[wa_services, wa_reference, wa_date, wa_slot, wa_vehicle, wa_code],
                send_whatsapp=send_whatsapp,
            )
        except Exception:  # noqa: BLE001
            logger.exception("Could not send the service-done message for %s", cars[0].get("booking_number"))

    @staticmethod
    def _mark_done_refusal(status: str) -> str:
        return {
            BookingStatus.AWAITING_PAYMENT.value: "This booking is still waiting for the customer's online payment, so it can't be marked done yet.",
            BookingStatus.CAPTAIN_ON_THE_WAY.value: "The captain is already on the way — let them finish it from their app, or cancel the booking.",
            BookingStatus.SERVICE_STARTED.value: "The captain has already started this job — let them finish it from their app, or cancel the booking.",
            BookingStatus.COMPLETED.value: "This booking is already completed.",
            BookingStatus.CANCELLED.value: "This booking was cancelled.",
        }.get(status, "This booking can't be marked done right now.")

    @staticmethod
    def _manager_done_fields(car: dict, actor_id: str, now: datetime) -> dict:
        total = round(float(car.get("total_amount") or 0), 2)
        fields: dict = {
            "status": BookingStatus.COMPLETED.value,
            "completed_at": now,
            "closed_at": now,
            "completed_by_id": actor_id,
            "completed_by_role": "manager",
            "awaiting_assignment_since": None,
            # The manager did the job: no captain is paid, and nothing must
            # ever settle a wallet for this booking later.
            "captain_id": None,
            "captain_earning": 0.0,
            "captain_travel_pay": 0.0,
            "captain_service_pay": 0.0,
            "platform_earning": total,
            "wallet_settled": True,
            # No captain timeline: a stale assigned_at (kept after a release)
            # must not feed the captain completion-time averages.
            "assigned_at": None,
            "estimated_start_at": None,
        }
        if car.get("captain_id"):
            previous = list(car.get("previous_captain_ids", []))
            if car["captain_id"] not in previous:
                previous.append(car["captain_id"])
            fields.update({"previous_captain_ids": previous, "heading_at": None, "heading_location": None})
        if car.get("issue_flag") and not car.get("issue_resolved"):
            fields["issue_resolved"] = True
        # The money: what is still due is recorded as cash the manager
        # collected — by _close_as_manager_done, through
        # MoneyService.apply_payment in the same transaction (spec 1.5).
        return fields

    # How often a money-guarded close re-reads after losing to a concurrent
    # payment write before giving up (each loss means the money changed).
    _MONEY_GUARD_ATTEMPTS = 3

    async def _close_as_manager_done(self, car: dict, actor_id: str, now: datetime) -> tuple[dict | None, dict]:
        """One car's manager-done close, in ONE transaction: the car is
        re-read, closed guarded on the state it was read in, and whatever it
        still owes (amount_due — after wallet credit and anything already
        paid online) is recorded as cash the manager collected
        (MoneyService.apply_payment). An online payment landing at the same
        moment conflicts with this transaction and is retried, so it is
        never rewritten as cash the manager took (audit PAY-05) — and an
        amount already paid is never collected twice. Returns (written doc,
        or None when the car left the manager-done states; the state it was
        closed from)."""
        car_id = str(car["_id"])
        seen: dict = {"car": car}

        async def _do(session):
            fresh = await self.repo.find_by_id(car_id, session=session)
            if not fresh or fresh.get("status") not in self._MANAGER_DONE_FROM:
                seen["car"] = fresh or car
                return None
            seen["car"] = fresh
            done = await self.repo.update_if(
                car_id, {"status": fresh["status"], "captain_id": fresh.get("captain_id")},
                self._manager_done_fields(fresh, actor_id, now), session=session,
            )
            if done is None:
                return None
            due = bm.amount_due(done)
            if due > bm.EPSILON:
                await self.money.apply_payment(
                    [car_id], due, method=PaymentMethod.CASH.value, key=f"manager_done:{car_id}", session=session,
                    actor_id=actor_id, actor_role="manager", collected_by=actor_id,
                    note=f"Collected by the manager for {done.get('booking_number')}",
                )
            return await self.repo.find_by_id(car_id, session=session)

        async with await self.db.client.start_session() as session:
            updated = await session.with_transaction(_do)
        return updated, seen["car"]

    async def manager_mark_done(
        self, booking_id: str, actor_id: str, actor_role: str, actor_center_id: str | None, send_whatsapp: bool = True
    ) -> dict:
        """The manager did an existing booking himself. Only jobs the captain
        hasn't started qualify (pending / rescheduled / assigned): once a
        captain is on the way or working, the captain closes it with photos.
        A visit closes as a whole; an assigned captain is released, told,
        and earns nothing for it."""
        booking = await self.repo.find_by_id(booking_id)
        if not booking:
            raise NotFoundException("Booking not found")
        ensure_own_center(actor_role, actor_center_id, booking["service_center_id"])
        if booking["status"] not in self._MANAGER_DONE_FROM:
            raise BadRequestException(self._mark_done_refusal(booking["status"]))
        # A job can't be done before its day (audit MGR-06) — today (IST) or earlier.
        if self._seat_key_of(booking)["date"] > _ist_today():
            raise BadRequestException("This booking is for a later date — it can be marked done on its day, not before.")
        cars = await self._visit_cars(booking)
        if any(c["status"] not in self._MANAGER_DONE_FROM and c["status"] != BookingStatus.COMPLETED.value for c in cars):
            raise BadRequestException(
                "Another vehicle on this visit is already in progress or awaiting payment — finish or cancel it first."
            )
        now = now_ist()
        done: list[dict] = []
        # The tapped car goes first: if it lost a race nothing was closed yet,
        # so the refusal is clean (siblings that lose theirs are just skipped).
        for car in sorted([c for c in cars if c["status"] in self._MANAGER_DONE_FROM], key=lambda c: str(c["_id"]) != booking_id):
            car_id = str(car["_id"])
            _ensure_transition_allowed(car["status"], BookingStatus.COMPLETED.value)
            updated, car = await self._close_as_manager_done(car, actor_id, now)
            if updated is None:
                if car_id == booking_id:
                    raise BadRequestException("This booking just changed state — refresh and try again.")
                continue
            await self._record_history(car_id, BookingStatus.COMPLETED, actor_id, "Marked done by the manager")
            done.append(updated)
            await self._broadcast_booking_changed(updated)
            released = car.get("captain_id")
            # Skip telling a self-assigned "captain" that their own job was
            # "completed by the manager" — that's the same person who just
            # completed it themselves; a real released captain still needs
            # the heads-up.
            if released and released != actor_id:
                await ws_manager.broadcast(f"user:{released}", {"type": "changed", "channel": f"user:{released}", "booking_id": car_id})
                try:
                    await self.notifications.notify(
                        released,
                        "Job completed by the manager",
                        f"{car['booking_number']} was completed by the manager — no action needed.",
                        NotificationType.BOOKING,
                        car_id,
                        send_whatsapp=False,
                    )
                except Exception:  # noqa: BLE001
                    logger.exception("Could not tell captain %s that %s was closed", released, car.get("booking_number"))
        await self._void_payment_links([str(d["_id"]) for d in done], "Marked done by the manager")
        await self._notify_service_done(done, booking["customer_id"], send_whatsapp)
        if done:
            await self._after_visit_completed(booking["customer_id"], done, now, send_whatsapp)
        return {"completed": len(done), "booking_numbers": [d.get("booking_number") for d in done]}

    async def _cars_for_lines(
        self, customer_id: str, lines, as_of: datetime | None = None, scheduled_date=None, *, log_center_id: str | None = None,
    ) -> list[GroupVehicleRequest]:
        """Type lines -> one car each ("2 SUVs" = two), with the customer's
        matching pass (same vehicle type + main service) attached to each car
        it covers.

        `as_of` — ONLY set by create_manager_logged_visit, the real
        (possibly backdated) time the job happened. A pass bought/granted
        AFTER that moment is skipped here rather than matched and then
        refused by plan_consumption — the booking should just charge
        normally, not blow up over an automatic match the manager never
        asked for.

        `log_center_id` — ONLY for a manager-logged job ("Log A Done Job",
        and its quote): a pass is spent only when the line asks for it
        EXPLICITLY (use_subscription sent true — audit MGR-01: it used to
        spend any matching pass silently), and only a self-serve pass or one
        the manager's own center sold."""
        passes = await self._usable_passes(customer_id)
        if log_center_id is not None:
            passes = [p for p in passes if p.get("service_center_id") in (None, "", log_center_id)]
        # A pass a line names explicitly is never auto-taken by another line.
        named = {getattr(line, "subscription_id", None) for line in lines if getattr(line, "subscription_id", None)}
        for sub in passes:
            if str(sub["_id"]) in named:
                sub["_used"] = True
        cars: list[GroupVehicleRequest] = []
        for line in lines:
            wants_pass = getattr(line, "use_subscription", True)
            if log_center_id is not None:
                wants_pass = bool(wants_pass) and "use_subscription" in line.model_fields_set
            if getattr(line, "vehicle_id", None) or getattr(line, "subscription_id", None):
                # A named car and/or an explicitly chosen pass (custom
                # multi-car plan, society pass): exactly that pass on
                # exactly that car — never moved to another car
                # (plan_consumption refuses a car-bound pass on any other
                # car). A named car with no pass chosen takes ITS OWN
                # standard pass, never another car's.
                car = await self._explicit_pass_car(customer_id, line, log_center_id=log_center_id)
                if car.vehicle_id and not car.subscription_id and wants_pass:
                    own = self._take_pass(
                        passes, car.vehicle_type, list(car.service_ids), as_of, scheduled_date, vehicle_id=car.vehicle_id,
                    )
                    if own:
                        car = car.model_copy(update={"subscription_id": str(own["_id"])})
                cars.append(car)
                continue
            for _ in range(line.quantity):
                sub = self._take_pass(passes, line.vehicle_type, list(line.service_ids), as_of, scheduled_date) if wants_pass else None
                cars.append(
                    GroupVehicleRequest(
                        # A car-bound standard pass books ITS car (the
                        # car-bound invariant): the line becomes that car.
                        vehicle_id=(sub or {}).get("vehicle_id") or None,
                        vehicle_type=line.vehicle_type,
                        quantity=1,
                        service_ids=list(line.service_ids),
                        service_quantities=dict(line.service_quantities or {}),
                        subscription_id=str(sub["_id"]) if sub else None,
                    )
                )
        return cars

    async def _explicit_pass_car(self, customer_id: str, line, *, log_center_id: str | None = None) -> GroupVehicleRequest:
        """One quick-booking line that names its car (vehicle_id) and/or the
        pass to use (subscription_id). The car must be the customer's and
        of the line's type; the pass must be the customer's, and on a
        manager-logged job a self-serve one or one this center sold
        (MGR-01). Whether the pass covers THIS car and service is
        plan_consumption's call at quote and create alike."""
        vehicle_id = getattr(line, "vehicle_id", None)
        if vehicle_id:
            vehicle = await self.vehicle_repo.find_by_id(vehicle_id)
            if not vehicle or vehicle.get("owner_id") != customer_id:
                raise NotFoundException("Vehicle not found")
            if line.vehicle_type and vehicle.get("vehicle_type") != line.vehicle_type:
                raise BadRequestException("That car is saved as a different vehicle type — pick its own type.")
        subscription_id = getattr(line, "subscription_id", None)
        if subscription_id:
            sub = await self.subscription_service.get_subscription(subscription_id)
            if not sub or sub.get("customer_id") != customer_id:
                raise NotFoundException("Subscription not found")
            if log_center_id is not None and sub.get("service_center_id") not in (None, "", log_center_id):
                raise BadRequestException("This pass was sold by another service center — it can't be used on a job logged here.")
        return GroupVehicleRequest(
            vehicle_id=vehicle_id,
            vehicle_type=line.vehicle_type,
            quantity=1,
            service_ids=list(line.service_ids),
            service_quantities=dict(line.service_quantities or {}),
            subscription_id=subscription_id,
        )

    @staticmethod
    def _take_pass(
        passes: list[dict], vehicle_type: str | None, service_ids: list[str], as_of: datetime | None = None, scheduled_date=None,
        *, vehicle_id: str | None = None,
    ) -> dict | None:
        """Claim the first unused pass (in _usable_passes' deterministic
        order) for this vehicle type whose service is on the car — marks it
        used so the next car can't take it too — and return it. With
        `vehicle_id` (a line that names its car) only a pass bound to that
        very car qualifies. A pass whose period doesn't cover the booking's
        day is never auto-applied (PASS-3) — the wash is simply charged."""
        from app.services.subscription_service import booking_day, pass_covers_date

        day = booking_day(scheduled_date)
        for sub in passes:
            if sub.get("_used"):
                continue
            if sub.get("service_id") not in service_ids:
                continue
            if vehicle_id is not None:
                if sub.get("vehicle_id") != vehicle_id:
                    continue
            elif sub.get("_vehicle_type", sub.get("vehicle_type")) != vehicle_type:
                continue
            if not pass_covers_date(sub, day):
                continue
            if as_of is not None:
                start = sub.get("start_date")
                # Calendar-DATE comparison, not exact-instant — see
                # plan_consumption's identical check for why.
                if start is not None and from_stored(start).date() > to_ist(as_of).date():
                    continue  # didn't exist yet, calendar-date-wise, at this logged time
            sub["_used"] = True
            return sub
        return None

    async def _with_passes(self, customer_id: str, cars: list[GroupVehicleRequest], scheduled_date=None) -> list[GroupVehicleRequest]:
        """The older entry points (POST /bookings, /bookings/group,
        /bookings/manager-create) name their cars directly. A vehicle-TYPE
        car that names no plan gets the customer's matching pass exactly as
        a quick-booking line does, so no door books the same wash at a
        different price. Saved-vehicle / combo cars keep what they asked for."""
        passes = await self._usable_passes(customer_id)
        taken = {c.subscription_id for c in cars if c.subscription_id}
        for sub in passes:
            if str(sub["_id"]) in taken:
                sub["_used"] = True
        out: list[GroupVehicleRequest] = []
        for car in cars:
            if car.vehicle_type and not car.vehicle_id and not car.combo_id and not car.subscription_id:
                sub = self._take_pass(passes, car.vehicle_type, list(car.service_ids or []), scheduled_date=scheduled_date)
                if sub:
                    # A car-bound standard pass books its own car.
                    car = car.model_copy(update={"subscription_id": str(sub["_id"]), "vehicle_id": sub.get("vehicle_id") or None})
            out.append(car)
        return out

    async def _park_or_confirm_result(self, raw_cars: list[dict], customer: dict, source: str) -> dict:
        """What every create path reports after its cars exist: the payment
        link for a visit parked awaiting payment (sent to the customer on
        WhatsApp too), and the visit totals."""
        raw_cars = [c for c in raw_cars if c]
        total = round(sum(float(c.get("total_amount") or 0) for c in raw_cars), 2)
        awaiting = any(c.get("status") == BookingStatus.AWAITING_PAYMENT.value for c in raw_cars)
        # What is still to pay after wallet credit (spec 1.5) — a visit the
        # wallet fully covers needs no link.
        due = round(sum(bm.amount_due(c) for c in raw_cars), 2)
        link = await self._send_visit_payment_link(raw_cars, customer, source) if awaiting and due >= 1 else None
        return {
            "awaiting_payment": awaiting,
            "payment_link": link,
            "visit_total": total,
            "service_code": (raw_cars[0].get("service_code") if raw_cars else None),
            "travel_charge": round(sum(float(c.get("travel_charge") or 0) for c in raw_cars), 2),
            "prepaid_only": any(c.get("prepaid_only") for c in raw_cars),
            "cancellation_charge": round(sum(float(c.get("cancellation_charge") or 0) for c in raw_cars), 2),
            # The wallet on this visit (spec 1.1): credit spent, a previous
            # balance due carried (inside total), and what is left to pay.
            "wallet_applied": round(sum(float(c.get("wallet_applied") or 0) for c in raw_cars), 2),
            "wallet_due_carried": round(sum(float(c.get("wallet_due_carried") or 0) for c in raw_cars), 2),
            "amount_due": round(sum(bm.amount_due(c) for c in raw_cars), 2),
        }

    async def create_self_service_booking(self, customer_id: str, payload: BookingCreateRequest) -> dict:
        """POST /bookings — one car, the older request shape. Same pass
        auto-apply, same payment-link rule and the same result fields as
        create_quick_booking."""
        customer = await self.user_repo.find_by_id(customer_id)
        if not customer:
            raise NotFoundException("Customer not found")
        if payload.vehicle_type and not payload.subscription_id and not payload.combo_id:
            car = GroupVehicleRequest(vehicle_type=payload.vehicle_type, service_ids=list(payload.service_ids))
            matched = (await self._with_passes(customer_id, [car], scheduled_date=payload.scheduled_date))[0]
            # A pass bound to ANOTHER car than the one this booking names is
            # never applied to it.
            if matched.subscription_id and not (payload.vehicle_id and matched.vehicle_id and matched.vehicle_id != payload.vehicle_id):
                payload = payload.model_copy(update={
                    "subscription_id": matched.subscription_id, "vehicle_id": payload.vehicle_id or matched.vehicle_id,
                })
        if payload.expected_total is not None:
            car = GroupVehicleRequest(
                vehicle_id=payload.vehicle_id, vehicle_type=None if payload.vehicle_id else payload.vehicle_type,
                service_ids=list(payload.service_ids), service_quantities=dict(payload.service_quantities or {}),
                combo_id=payload.combo_id, subscription_id=payload.subscription_id,
            )
            await self._check_expected_total(
                customer, [car], payload.address_id, payload.coupon_code, payload.expected_total, scheduled_date=payload.scheduled_date,
            )
        result = await self.create_booking(customer_id, payload, apply_charges=True)
        extra = await self._park_or_confirm_result([await self.repo.find_by_id(result["id"])], customer, "app")
        return {**result, **{k: v for k, v in extra.items() if k != "visit_total"}}

    async def create_self_service_group(self, customer_id: str, payload: BookingGroupCreateRequest) -> dict:
        """POST /bookings/group — several cars, the older request shape.
        Type lines are expanded here so each car can take its own pass."""
        customer = await self.user_repo.find_by_id(customer_id)
        if not customer:
            raise NotFoundException("Customer not found")
        cars: list[GroupVehicleRequest] = []
        for line in payload.vehicles:
            copies = line.quantity if line.vehicle_type else 1
            cars.extend(line.model_copy(update={"quantity": 1}) for _ in range(copies))
        payload = payload.model_copy(update={"vehicles": await self._with_passes(customer_id, cars, scheduled_date=payload.scheduled_date)})
        if payload.expected_total is not None:
            await self._check_expected_total(
                customer, payload.vehicles, payload.address_id, payload.coupon_code, payload.expected_total, scheduled_date=payload.scheduled_date,
            )
        result = await self.create_booking_group(customer_id, payload, apply_charges=True)
        raw = [await self.repo.find_by_id(b["id"]) for b in result["bookings"]]
        extra = await self._park_or_confirm_result(raw, customer, "app")
        return {**result, **{k: v for k, v in extra.items() if k not in ("visit_total", "service_code")}}

    @staticmethod
    def _ensure_expected_total(expected: float | None, actual: float, carried: float | None = None) -> None:
        """Audit PRICE-04: a price raised between the quote and the booking
        was applied silently. When the client says what it showed, a total
        ABOVE it is refused (409 PRICE_CHANGED, the new total in details) —
        nothing is created and no seat is taken. A total at or below what was
        shown goes through: the customer never pays more than they saw (a
        first-wash price confirmed by their number only ever lowers it)."""
        if expected is None:
            return
        if round(float(actual), 2) > round(float(expected), 2) + 0.004:
            charge = round(float(carried or 0), 2)
            raise PriceChangedException(
                (
                    f"The total is now ₹{float(actual):g} — it includes ₹{charge:g} previous balance due on your Blussit "
                    "wallet. Please check and book again."
                ) if charge > 0 else
                f"The price changed since you saw it — the total is now ₹{float(actual):g}. Please check and book again.",
                {"total_amount": round(float(actual), 2), "expected_total": round(float(expected), 2),
                 "previous_balance_due": charge, "cancellation_charge": charge},
            )

    async def _check_expected_total(
        self, customer: dict, cars: list[GroupVehicleRequest], address_id: str, coupon_code: str | None, expected: float, scheduled_date=None,
    ) -> None:
        """Preview what the older single/group create paths will charge,
        through the same quote code (quote_visit) the booking screens use."""
        customer_id = str(customer["_id"])
        address = await self.address_repo.find_by_id(address_id)
        if not address or address.get("owner_id") != customer_id:
            raise NotFoundException("Address not found")
        preview = await self.quote_visit(
            customer_id=customer_id, phone=customer.get("phone"), cars=cars, address=address, coupon_code=coupon_code, source="app",
            scheduled_date=scheduled_date,
        )
        self._ensure_expected_total(expected, preview["total_amount"], preview.get("previous_balance_due"))

    async def _usable_passes(self, customer_id: str) -> list[dict]:
        """This customer's live STANDARD passes (service scoped) with washes
        left — what the type-only booking paths apply automatically
        (_cars_for_lines, _with_passes: quote, quick booking, the bot,
        manager-quick, the older single/group create).

        - Society passes and custom multi-car passes are explicit-only (the
          line names vehicle_id + subscription_id) — SOC-4 and the custom
          plan contract; never auto-applied.
        - A car-bound standard pass (a monthly pass bought for one car) IS
          auto-applied to a type-only line of its car's type, and the line
          becomes that car (vehicle_id stamped by the caller), so the
          car-bound invariant holds. Its car must still be the customer's,
          live, and of the pass's type.
        - Order is deterministic, so a quote and the create that follows
          pick the same pass: type-scoped (no car) passes first, then most
          washes left, then the earliest end, then the oldest."""
        from app.services.subscription_service import HIDDEN_PLAN_TYPES, is_custom_pass

        try:
            subs = await self.subscription_service.repo.collection.find(
                {"customer_id": customer_id, "status": "active", "is_deleted": {"$ne": True}, "service_id": {"$ne": None}}
            ).to_list(length=50)
            plan_ids = [ObjectId(p) for p in {s.get("plan_id") for s in subs} if isinstance(p, str) and ObjectId.is_valid(p)]
            hidden_plans = {
                str(p["_id"]) for p in await self.db.subscription_plans.find(
                    {"_id": {"$in": plan_ids}, "plan_type": {"$in": list(HIDDEN_PLAN_TYPES)}}, {"_id": 1},
                ).to_list(length=len(plan_ids))
            } if plan_ids else set()
            vehicle_ids = [ObjectId(v) for v in {s.get("vehicle_id") for s in subs} if isinstance(v, str) and ObjectId.is_valid(v)]
            vehicles = {
                str(v["_id"]): v for v in await self.db.vehicles.find(
                    {"_id": {"$in": vehicle_ids}}, {"owner_id": 1, "vehicle_type": 1, "is_deleted": 1},
                ).to_list(length=len(vehicle_ids))
            } if vehicle_ids else {}
        except Exception:  # noqa: BLE001
            return []
        now = datetime.now(timezone.utc)
        usable = []
        for sub in subs:
            end = sub.get("end_date")
            if end is not None and end.replace(tzinfo=timezone.utc) <= now:
                continue
            if int(sub.get("remaining_service_count") or 0) < 1:
                continue
            if sub.get("society_id") or sub.get("plan_kind") in HIDDEN_PLAN_TYPES or is_custom_pass(sub) or sub.get("plan_id") in hidden_plans:
                continue  # explicit-only
            if sub.get("vehicle_id"):
                car = vehicles.get(sub["vehicle_id"])
                if not car or car.get("is_deleted") or car.get("owner_id") != customer_id:
                    continue
                if sub.get("vehicle_type") and car.get("vehicle_type") != sub.get("vehicle_type"):
                    continue  # the car was retyped — the pass no longer fits it
                sub["_vehicle_type"] = car.get("vehicle_type")
            else:
                sub["vehicle_id"] = None
            usable.append(sub)
        far = datetime.max.replace(tzinfo=timezone.utc)
        usable.sort(key=lambda s: (
            bool(s.get("vehicle_id")),
            -int(s.get("remaining_service_count") or 0),
            s["end_date"].replace(tzinfo=timezone.utc) if s.get("end_date") else far,
            str(s["_id"]),
        ))
        return usable

    async def create_booking_for_customer(self, actor_id: str, payload: ManagerBookingCreateRequest) -> dict:
        """A manager/admin creating a booking on behalf of a customer (new or
        existing). Reuses create_booking() for all the actual pricing/fraud/
        scheduling logic — this only resolves (or inline-creates) the
        vehicle/address, then delegates."""
        customer = await self.user_repo.find_by_id(payload.customer_id)
        if not customer or customer.get("role") != "customer":
            raise NotFoundException("Customer not found")

        vehicle_id = payload.vehicle_id
        if payload.new_vehicle:
            created_vehicle = await VehicleService(self.db).create(payload.customer_id, payload.new_vehicle)
            vehicle_id = created_vehicle["id"]

        address_id = payload.address_id
        if payload.new_address:
            # The customer's same place is reused, not copied (see find_same_place).
            addresses = AddressService(self.db)
            match, _ = await addresses.find_same_place(payload.customer_id, payload.new_address.model_dump())
            address_id = str(match["_id"]) if match else (await addresses.create(payload.customer_id, payload.new_address))["id"]

        subscription_id = payload.subscription_id
        vehicle_type = payload.vehicle_type if not vehicle_id else None
        if vehicle_type and not subscription_id and not payload.combo_id:
            car = GroupVehicleRequest(vehicle_type=vehicle_type, service_ids=list(payload.service_ids))
            matched = (await self._with_passes(payload.customer_id, [car], scheduled_date=payload.scheduled_date))[0]
            subscription_id = matched.subscription_id
            if matched.vehicle_id:
                vehicle_id = matched.vehicle_id  # a car-bound pass books its own car

        booking_request = BookingCreateRequest(
            vehicle_id=vehicle_id,
            vehicle_type=vehicle_type,
            address_id=address_id,
            service_ids=payload.service_ids,
            service_quantities=payload.service_quantities or {},
            combo_id=payload.combo_id,
            scheduled_date=payload.scheduled_date,
            scheduled_slot=payload.scheduled_slot,
            payment_method=payload.payment_method,
            coupon_code=payload.coupon_code,
            subscription_id=subscription_id,
            customer_notes=payload.customer_notes,
            alternate_contact_name=payload.alternate_contact_name,
            alternate_contact_phone=payload.alternate_contact_phone,
        )
        # Staff-initiated — never gated on the customer's own phone
        # verification (see create_booking's _skip_verification_gate).
        result = await self.create_booking(
            payload.customer_id, booking_request, _skip_verification_gate=True, source="staff", _allow_pinless=True, apply_charges=True,
        )
        await self._post_commit("history row", result, self._record_history(
            result["id"], BookingStatus(result["status"]), actor_id, f"Booking created by staff on behalf of customer {payload.customer_id}"
        ))
        # A prepaid booking is parked until paid — send the customer the link.
        extra = await self._park_or_confirm_result([await self.repo.find_by_id(result["id"])], customer, "staff")
        return {**result, **{k: v for k, v in extra.items() if k != "visit_total"}}

    async def report_risk(self, booking_id: str, captain_id: str, note: str | None) -> dict:
        """Captain self-reports they're at risk of running late for an
        upcoming booking (still `assigned`, hasn't started heading yet)
        because their current job is running long. Reuses the same
        flag_issue()/notify path as the automatic captain_not_reached/
        captain_delay sweeps, so the manager sees it through one channel."""
        booking = await self.repo.find_by_id(booking_id)
        if not booking:
            raise NotFoundException("Booking not found")
        await self._ensure_captain_job(booking, captain_id)
        if booking["status"] != BookingStatus.ASSIGNED.value:
            raise BadRequestException(
                "Only an upcoming booking you haven't started heading to yet can be flagged this way — "
                "if you've already started heading, release the job instead if you truly can't make it."
            )
        if booking.get("issue_flag") and not booking.get("issue_resolved", True):
            raise BadRequestException("This booking already has an open issue flagged.")

        note_text = note or "Captain reported they may run late for this booking due to their current job."
        if not await self.flag_issue(
            booking_id, "captain_reported_risk", note_text, expected={"status": BookingStatus.ASSIGNED.value, "captain_id": captain_id}
        ):
            raise BadRequestException("This booking just changed — refresh your jobs list.")
        await self._record_history(booking_id, BookingStatus(booking["status"]), captain_id, f"Captain self-reported risk of delay: {note_text}")
        return serialize_doc(await self.repo.find_by_id(booking_id))

    @staticmethod
    def _resolve_price(item: dict, vehicle_type: str, first_time_eligible: bool) -> float:
        """Vehicle-type overrides win when set; otherwise fall back to the flat
        price. First-time price only applies if this vehicle+phone genuinely
        haven't been served before (see the fraud check in create_booking)."""
        type_prices = item.get("vehicle_type_prices") or {}
        type_discounted = item.get("vehicle_type_discounted_prices") or {}
        base_price = type_prices.get(vehicle_type, item["price"])
        first_time_price = type_discounted.get(vehicle_type, item.get("discounted_price"))
        # PRICE-03: a first-wash price counts only when it is a real discount
        # (0 < price < regular) — the same rule the website shows prices by
        # (landing/shared.tsx priceView), so a mistyped 0 or a value at/above
        # the regular price can never make the server charge differently.
        if first_time_eligible and first_time_price is not None and 0 < float(first_time_price) < float(base_price):
            return first_time_price
        return base_price

    @classmethod
    def price_view(cls, item: dict, vehicle_type: str, first_time_eligible: bool) -> dict:
        """What one service shows on a card or chat row: the price this
        customer pays, and what to strike through — the regular price when a
        first-wash price applies, else the display-only MRP (original_price),
        which is never charged. Mirrors the website's priceForType."""
        regular = float(cls._resolve_price(item, vehicle_type, False))
        price = float(cls._resolve_price(item, vehicle_type, first_time_eligible))
        mrp = (item.get("vehicle_type_original_prices") or {}).get(vehicle_type, item.get("original_price"))
        struck = regular if price < regular else (float(mrp) if mrp is not None and float(mrp) > price else None)
        return {"price": price, "regular": regular, "struck": struck, "first_time": price < regular}

    async def _first_time_eligible(self, phone: str | None, registration_number: str | None = None) -> bool:
        """First-time-offer eligibility is checked against the WHOLE platform,
        not one account — the same plate or phone having any earlier
        non-cancelled booking (under any account) disqualifies it, which is
        what stops "new phone number, same car" discount abuse."""
        if registration_number and await self.repo.exists_for_registration(registration_number, FRAUD_CHECK_EXCLUDED_STATUSES):
            return False
        if phone and await self.repo.exists_for_phone(phone, FRAUD_CHECK_EXCLUDED_STATUSES):
            return False
        return True

    async def _price_services(
        self,
        *,
        customer_id: str | None,
        services: list[dict],
        quantities: dict[str, int],
        combo: dict | None,
        vehicle_type: str,
        vehicle_id: str | None,
        first_time_eligible: bool,
        subscription_id: str | None,
        coupon_code: str | None,
        as_of: datetime | None = None,
        scheduled_date=None,
    ) -> dict:
        """THE price of one car: per-vehicle-type price (or the first-time
        price), then EITHER its plan's waiver OR a coupon. create_booking
        charges it and quote_visit shows it — one function, so no screen or
        chat can quote a price the booking won't honour. `scheduled_date`:
        the booking's day — a pass covers only washes inside its own period
        (PASS-3), checked by plan_consumption for quote and create alike."""
        def subtotal_at(first_time: bool) -> float:
            if combo:
                return float(self._resolve_price(combo, vehicle_type, first_time))
            return float(sum(self._resolve_price(s, vehicle_type, first_time) * quantities[str(s["_id"])] for s in services))

        subtotal = subtotal_at(first_time_eligible)
        discount = 0.0
        coupon: dict | None = None
        consumption: dict | None = None
        if subscription_id:
            # The subscription must belong to exactly this booking's customer
            # (also the target customer when staff book on someone's behalf).
            consumption = await self.subscription_service.plan_consumption(
                subscription_id, vehicle_id, services, customer_id, vehicle_type=vehicle_type, as_of=as_of,
                scheduled_date=scheduled_date,
            )
            discount = await self._subscription_discount(
                subscription_id, services, vehicle_type, first_time_eligible, consumption=consumption, quantities=quantities,
            )
        elif coupon_code:
            coupon, discount = await self.coupon_service.validate_and_compute_booking_discount(
                coupon_code,
                subtotal,
                customer_id,
                services=services,
                quantities=quantities,
                vehicle_type=vehicle_type,
                first_time_eligible=first_time_eligible,
                price_resolver=self._resolve_price,
            )
        # A discount can legitimately be computed above the subtotal (a combo
        # priced below the sum of its parts under a full-waive legacy plan) —
        # clamp it, or the booking books a NEGATIVE total and the `total <= 0
        # → PAID` rule marks it paid while poisoning revenue aggregates.
        return {
            "subtotal": subtotal,
            "regular_subtotal": subtotal_at(False),
            "discount_amount": min(discount, subtotal),
            "coupon": coupon,
            "subscription_consumption": consumption,
        }

    async def _car_services(self, car) -> tuple[list[dict], dict | None, dict[str, int]]:
        """A GroupVehicleRequest-like car's services, combo and validated
        quantities — the same checks create_booking makes before pricing."""
        combo = None
        if car.combo_id:
            combo = await self.combo_repo.find_by_id(car.combo_id)
            if not combo or not combo.get("is_active"):
                raise NotFoundException("Combo offer not found or inactive")
            service_ids = combo["service_ids"]
        else:
            service_ids = list(car.service_ids or [])
        if not service_ids:
            raise BadRequestException("Select at least one service or combo offer")
        services = []
        for service_id in service_ids:
            service = await self.service_repo.find_by_id(service_id)
            if not service or not service.get("is_active"):
                raise NotFoundException(f"Service not found or inactive: {service_id}")
            services.append(service)
        if combo:
            return services, combo, {str(s["_id"]): 1 for s in services}
        return services, None, await self._validate_service_mix(services, car.vehicle_type, dict(car.service_quantities or {}))

    async def quote_visit(
        self,
        *,
        customer_id: str | None,
        phone: str | None,
        lines=None,
        address: dict | None = None,
        coupon_code: str | None = None,
        source: str = "app",
        log_mode: bool = False,
        as_of: datetime | None = None,
        cars: list[GroupVehicleRequest] | None = None,
        anonymous: bool = False,
        scheduled_date=None,
        log_center_id: str | None = None,
    ) -> dict:
        """The bill a visit WILL get, before it is booked — every screen and
        the WhatsApp bot show this instead of doing their own maths. Mirrors
        create_quick_booking step for step: lines expand to cars in order,
        each car takes the customer's matching pass (_cars_for_lines), cars
        are priced in order so only the FIRST can get the first-time price
        (the second already sees the first's booking under this phone), the
        coupon rides on the first car only, the distance charge is charged
        once, and a prepaid service makes the visit online-only.

        `customer_id` None (an anonymous visitor): no passes are looked up —
        a stranger's quote must never reveal someone's plan. `phone` None:
        the visitor hasn't typed it yet, so they are quoted as new.
        `log_mode`: a manager-logged job — no distance charge, no prepaid
        rule, no coupon (create_booking's _completed_by exemptions).

        `cars`: already-expanded cars (the older /bookings and
        /bookings/group shapes — saved vehicles, combos, chosen passes)
        instead of `lines`; each car is its own line row. Used to preview
        what those create paths will charge (PRICE-04).

        `anonymous` (audit PAY-15): an unproven caller. The phone is NOT
        consulted — "is this number new?" must not be answerable by anyone
        who types a number — so the visit is quoted at the regular price and
        `first_time_pending` says the first-wash price is confirmed with the
        customer's number when they book."""
        policy = await self.policy_service.get_policy()
        units: list[tuple[int, GroupVehicleRequest]] = []
        first_plate: str | None = None
        if cars is not None:
            resolved: list[GroupVehicleRequest] = []
            for car in cars:
                if car.vehicle_id and not car.vehicle_type:
                    vehicle = await self.vehicle_repo.find_by_id(car.vehicle_id)
                    if not vehicle or vehicle.get("owner_id") != customer_id:
                        raise NotFoundException("Vehicle not found")
                    if not resolved and vehicle.get("registration_number"):
                        first_plate = normalize_plate(vehicle["registration_number"])
                    car = car.model_copy(update={"vehicle_type": vehicle["vehicle_type"]})
                resolved.append(car)
            cars = resolved
            units = list(enumerate(cars))
            line_specs = [(car.vehicle_type, 1) for car in cars]
        else:
            if customer_id:
                # A logged job's quote takes passes exactly as the log will
                # (explicit use_subscription, self-serve or this center's).
                cars = await self._cars_for_lines(
                    customer_id, lines, as_of=as_of, scheduled_date=scheduled_date,
                    log_center_id=(log_center_id or "") if log_mode else None,
                )
            else:
                cars = [
                    GroupVehicleRequest(
                        vehicle_type=line.vehicle_type, quantity=1, service_ids=list(line.service_ids),
                        service_quantities=dict(line.service_quantities or {}),
                    )
                    for line in lines
                    for _ in range(line.quantity)
                ]
            position = 0
            for line_index, line in enumerate(lines):
                for _ in range(line.quantity):
                    units.append((line_index, cars[position]))
                    position += 1
            line_specs = [(line.vehicle_type, line.quantity) for line in lines]
        limit = int(policy.get("max_vehicles_per_booking", 5))
        if len(units) > limit:
            raise BadRequestException(f"You can book up to {limit} vehicles on one visit.")

        if anonymous:
            first_time = False
        else:
            first_time = await self._first_time_eligible(phone, first_plate) if phone else True
        terms = dict(self._NO_TERMS) if log_mode else await self._visit_terms(cars)
        coupon_error: str | None = None
        coupon_applied: str | None = None
        # The coupon rides on the first car that pays — create_booking_group
        # gives it to the same car (audit PRICE-05: on a visit whose first
        # car a plan covers it was silently dropped).
        paying = self._paying_index([car for _line, car in units])
        if coupon_code and not log_mode and all(car.subscription_id for _line, car in units):
            coupon_error = "Your plan already covers this wash — a coupon can't be used on it."
        line_rows = [
            {"line_index": i, "vehicle_type": vehicle_type, "quantity": quantity, "subtotal": 0.0,
             "regular_subtotal": 0.0, "plan_discount": 0.0, "coupon_discount": 0.0, "plan_covered": 0, "total": 0.0}
            for i, (vehicle_type, quantity) in enumerate(line_specs)
        ]
        for index, (line_index, car) in enumerate(units):
            vt_doc = await self.vehicle_type_repo.find_by_id(car.vehicle_type) if car.vehicle_type else None
            if not vt_doc or not vt_doc.get("is_active", True):
                raise BadRequestException("Pick a valid vehicle type.")
            services, combo, quantities = await self._car_services(car)
            code = coupon_code if index == paying and not log_mode and not car.subscription_id else None
            common = dict(
                customer_id=customer_id, services=services, quantities=quantities, combo=combo,
                vehicle_type=car.vehicle_type, vehicle_id=car.vehicle_id, first_time_eligible=first_time and index == 0,
                subscription_id=car.subscription_id, as_of=as_of, scheduled_date=scheduled_date,
            )
            try:
                priced = await self._price_services(**common, coupon_code=code)
            except BadRequestException as exc:
                if not code:
                    raise
                # A refused coupon is shown next to the field, not as a
                # failed quote — the rest of the bill is still true.
                coupon_error = exc.message
                priced = await self._price_services(**common, coupon_code=None)
            if priced["coupon"] is not None:
                coupon_applied = priced["coupon"]["code"]
            row = line_rows[line_index]
            row["subtotal"] += priced["subtotal"]
            row["regular_subtotal"] += priced["regular_subtotal"]
            if car.subscription_id:
                row["plan_covered"] += 1
                row["plan_discount"] += priced["discount_amount"]
            else:
                row["coupon_discount"] += priced["discount_amount"]
            row["total"] += max(0.0, priced["subtotal"] - priced["discount_amount"])

        travel = None
        if terms["charges_travel"] and address:
            center, distance_km = await self._resolve_service_center(address, allow_pinless=True)
            charge_km, _source = await self.charge_distance_km(center, address, distance_km)
            travel = await self.pricing_service.travel_quote(charge_km)
        travel_charge = float(travel["charge"]) if travel else 0.0

        for row in line_rows:
            for key in ("subtotal", "regular_subtotal", "plan_discount", "coupon_discount", "total"):
                row[key] = round(row[key], 2)
        subtotal = round(sum(r["subtotal"] for r in line_rows), 2)
        regular = round(sum(r["regular_subtotal"] for r in line_rows), 2)
        # The customer's wallet (spec 1.1) — exactly what create_booking
        # will do with it: a negative balance rides on this booking as
        # "Previous Balance Due" (inside the total), a positive one is spent
        # on it. Never for an unproven caller, never on a manager-logged job.
        wallet = {"previous_balance_due": 0.0, "wallet_credit": 0.0, "balance": 0.0}
        if not (anonymous or log_mode or not customer_id):
            wallet = await self.money.quote_lines(customer_id)
        carried = round(float(wallet.get("previous_balance_due") or 0), 2)
        total = round(sum(r["total"] for r in line_rows) + travel_charge + carried, 2)
        wallet_applied = round(min(float(wallet.get("wallet_credit") or 0), total), 2) if carried <= 0 else 0.0
        payable = round(total - wallet_applied, 2)
        # Mirrors create_booking's AWAITING_PAYMENT rule: a prepaid service
        # on any channel, or (self-service) a plan wash that still costs
        # something — its extras are paid before we come out.
        plan_extras = source == "app" and self._plan_extras_online_only([(bool(r["plan_covered"]), r["total"]) for r in line_rows])
        return {
            "vehicle_count": len(units),
            "first_time_eligible": first_time,
            # True only for an anonymous quote: priced at the regular price,
            # the first-wash price (if the number is new) is applied when the
            # booking is made with the verified number.
            "first_time_pending": bool(anonymous),
            # The first-wash price above is final (the customer is known),
            # as opposed to pending confirmation with their number.
            "first_time_confirmed": not anonymous,
            "lines": line_rows,
            "subtotal": subtotal,
            "regular_subtotal": regular,
            "first_time_savings": round(regular - subtotal, 2),
            "plan_discount": round(sum(r["plan_discount"] for r in line_rows), 2),
            "coupon_code": coupon_applied,
            "coupon_discount": round(sum(r["coupon_discount"] for r in line_rows), 2),
            "coupon_error": coupon_error,
            "travel": travel,
            "travel_charge": travel_charge,
            # The visit charges travel but no address was given to measure it.
            "travel_pending": bool(terms["charges_travel"]) and not address,
            # Deprecated (the old "added to your next booking" charge, now
            # settled through the wallet) — always 0; kept for old clients.
            "cancellation_charge": 0.0,
            # Wallet lines (spec 1.1): a negative balance carried INTO
            # total_amount ("Previous Balance Due ₹X"), credit spent on it,
            # and what is left to pay after that credit.
            "previous_balance_due": carried,
            "wallet_due_carried": carried,
            "wallet_credit_available": round(float(wallet.get("wallet_credit") or 0), 2),
            "wallet_applied": wallet_applied,
            "wallet_balance": round(float(wallet.get("balance") or 0), 2),
            "total_amount": total,
            "amount_payable": payable,
            "prepaid_service": terms["prepaid_service"],
            "online_only": payable > 0 and bool(terms["prepaid_service"] or plan_extras),
        }

    async def _subscription_discount(
        self, subscription_id: str, services: list[dict], vehicle_type: str, first_time_eligible: bool,
        *, consumption: dict | None = None, quantities: dict[str, int] | None = None,
    ) -> float:
        """How much of this booking's subtotal a subscription actually waives.

        A plan with `included_service_ids` set names EXACTLY what it covers —
        admin decides that, not the customer at booking time. Any of those
        services is fully free. Booking a different MAIN service on the same
        subscription is a same-visit "swap": covered only up to the cheapest
        service the plan actually includes, the rest is a real charge (e.g. a
        plan covering a ₹399 Foam Wash can swap to a ₹799 Deep Clean for a
        ₹400 top-up, not for free) — never blocked outright, never free
        either. A CHEAPER swap is simply covered: it still consumes one full
        visit and the gap is never credited back — the customer's call.

        ADD-ONS are never covered by a plan (unless the admin explicitly put
        one in included_service_ids): riding a bike wash (₹60/bike), polish,
        etc. on a plan visit is always a real extra charge — mirrored by
        plan_consumption, which likewise doesn't count add-ons against the
        visit quota. This applies to legacy/unrestricted plans (no
        included_service_ids) too: those waive any MAIN service picked, but
        add-ons still cost money.

        A CUSTOM multi-car pass (spec 1.6, PLANS' plan_consumption) names
        what THIS booking takes from its quotas: `consumption` carries
        covered_service_ids, and exactly those services are waived (each
        for the units its quota pays, at most the booked quantity) —
        anything else on the car, add-ons included, is paid."""
        covered_ids = (consumption or {}).get("covered_service_ids")
        if covered_ids is not None:
            # PLANS' contract: each covered service is waived once (price ×
            # 1 — one quota unit); everything else on the car is paid.
            covered = {str(i) for i in covered_ids}
            return round(sum(
                self._resolve_price(s, vehicle_type, first_time_eligible) for s in services if str(s["_id"]) in covered
            ), 2)
        # A PASS waives exactly its own service — no swap arithmetic, because
        # a pass books the wash it was bought for and nothing else
        # (plan_consumption refuses anything else outright). Add-ons ride
        # along at full price, as everywhere else.
        sub = await self.subscription_service.get_subscription(subscription_id)
        covered_service_id = (sub or {}).get("service_id")
        if covered_service_id and ((sub or {}).get("vehicle_id") or (sub or {}).get("vehicle_type")):
            return round(
                sum(
                    self._resolve_price(s, vehicle_type, first_time_eligible)
                    for s in services
                    if str(s["_id"]) == covered_service_id and not s.get("is_addon")
                ),
                2,
            )

        plan = await self.subscription_service.get_plan(subscription_id)
        included_ids = set((plan or {}).get("included_service_ids") or [])
        if not included_ids:
            return round(sum(self._resolve_price(s, vehicle_type, first_time_eligible) for s in services if not s.get("is_addon")), 2)

        included_docs = await self.service_repo.find_by_ids(list(included_ids))
        included_prices = [self._resolve_price(s, vehicle_type, first_time_eligible) for s in included_docs]
        baseline = min(included_prices) if included_prices else 0.0

        discount = 0.0
        for s in services:
            price = self._resolve_price(s, vehicle_type, first_time_eligible)
            if str(s["_id"]) in included_ids:
                discount += price
            elif s.get("is_addon"):
                continue  # paid extra, always — see docstring
            else:
                discount += min(baseline, price)
        return round(discount, 2)

    async def _captain_conflict(
        self,
        captain_id: str,
        new_start: datetime,
        new_end: datetime,
        exclude_booking_id: str | None,
        policy: dict,
        session=None,
        group_id: str | None = None,
    ) -> dict | None:
        """Returns the conflicting booking if this captain's job window doesn't
        leave a single travel-buffer gap before/after another job of theirs.
        The buffer is required ONCE, between the end of whichever job finishes
        first and the start of whichever job starts next — it must NOT be
        added independently to both windows (that was a real bug: padding
        both sides of both bookings silently required 2x the configured
        buffer as the effective gap, e.g. a captain finishing a 40-minute wash
        at 1:40 PM with a 15-minute buffer couldn't be booked again until
        2:10 PM instead of the intended 1:55 PM).

        Callers pass the captain's actual (or trial) ESTIMATED_START_AT
        window, never the coarse customer-facing slot_start/slot_end bucket
        directly — several bookings can legitimately share one admin slot
        for the same captain (e.g. a 9:00 job and a 10:30 job both inside a
        09:00-12:00 slot); comparing against the shared bucket itself would
        make that impossible by always looking like a conflict.

        Pass `session` when called from inside assign_captain/reassign_captain's
        transaction — that's what actually closes the double-booking race (two
        concurrent assignments both reading "no conflict" before either writes);
        called without a session (e.g. the eligible-captains picker, which is
        read-only and doesn't need transactional consistency), it behaves exactly
        as before."""
        buffer = timedelta(minutes=policy["captain_travel_buffer_minutes"])

        existing = await self.repo.find_active_for_captain(captain_id, exclude_booking_id, session=session)
        if group_id:
            # Cars wash on the SAME visit sit back to back at one address:
            # there is no travel between them, so requiring a travel gap
            # would make a captain conflict with himself and no multi-car
            # visit could ever be assigned. They also can't overlap — each
            # car's window is staggered by the one before it.
            existing = [b for b in existing if b.get("booking_group_id") != group_id]
        return _find_window_overlap(new_start, new_end, buffer, existing, window_fn=_captain_booking_window)

    async def _customer_conflict(
        self,
        customer_id: str,
        new_start: datetime,
        new_end: datetime,
        exclude_booking_id: str | None,
        group_id: str | None = None,
        session=None,
    ) -> dict | None:
        """Same overlap math as _captain_conflict, but against the
        customer's OWN other active bookings — nothing previously stopped a
        customer from booking (or rescheduling into) two overlapping slots
        for themselves, e.g. a 9-12 slot at one service center and a
        different center's overlapping slot the same morning. No travel
        buffer here (that's a captain-schedule concept, not a customer
        one) — a customer's two bookings just can't literally overlap in
        time. Callers pass the NEW booking's already-resolved slot window
        (see _resolve_slot_window) rather than a raw scheduled_slot string,
        since that string is now an admin capacity-bucket key, not
        something this method should re-derive an exact instant from
        itself."""
        existing = await self.repo.find_active_for_customer(customer_id, exclude_booking_id, session=session)
        if group_id:
            # Other cars on the SAME visit are the whole point — they share
            # one slot deliberately. This guard exists to stop a customer
            # holding two DIFFERENT appointments that overlap.
            existing = [b for b in existing if b.get("booking_group_id") != group_id]
        return _find_window_overlap(new_start, new_end, timedelta(0), existing)

    async def _raise_if_customer_conflict(
        self, customer_id: str, new_start: datetime, new_end: datetime, exclude_booking_id: str | None,
        group_id: str | None, session=None,
    ) -> None:
        """The customer's own-overlap refusal. Callers run it inside their
        transaction right after _touch_customer, so a concurrent booking of
        the same customer is either committed (and seen here) or serialized
        behind this one (audit BOOK-05: two tabs, two car types)."""
        conflict = await self._customer_conflict(customer_id, new_start, new_end, exclude_booking_id, group_id=group_id, session=session)
        if not conflict:
            return
        details = {"booking_id": str(conflict["_id"]), "booking_number": conflict.get("booking_number"), "status": conflict.get("status")}
        if conflict.get("status") == BookingStatus.AWAITING_PAYMENT.value:
            raise DuplicateBookingException(
                f"Booking {conflict['booking_number']} for this time is still waiting for your online payment. "
                "Open it from My bookings to pay, switch it to cash, or cancel it — then book again.",
                details,
            )
        raise DuplicateBookingException(
            f"You already have booking {conflict['booking_number']} scheduled around this time — "
            "pick a different time or manage that booking first.",
            details,
        )

    async def _address_of(self, booking: dict) -> dict | None:
        """Where this booking is: its own address_snapshot (frozen at create
        and on every booking edit), else — a booking from before snapshots
        that backfill_address_snapshots hasn't reached — the saved address."""
        if booking.get("address_snapshot"):
            return booking["address_snapshot"]
        return await self.address_repo.find_by_id(booking["address_id"]) if booking.get("address_id") else None

    async def _check_geofence(self, booking: dict, latitude: float, longitude: float) -> tuple[bool, float | None]:
        """Flags (never blocks) a photo whose GPS is further than the policy
        radius from the booking's address — the manager sees the flag and
        distance, service addresses aren't pinpoint-accurate so we don't want
        false positives stopping a captain mid-job."""
        address = await self._address_of(booking)
        if not address or address.get("latitude") is None or address.get("longitude") is None:
            return False, None
        policy = await self.policy_service.get_policy()
        distance_km = haversine_km(latitude, longitude, address["latitude"], address["longitude"])
        distance_m = round(distance_km * 1000, 1)
        return distance_m > policy["photo_geofence_radius_m"], distance_m

    async def _notify_location_flag(self, booking: dict, stage: str, distance_m: float | None) -> None:
        center = await self.center_repo.find_by_id(booking["service_center_id"])
        if center and center.get("manager_id"):
            if stage == "arrival_no_gps":
                text = "The captain pressed 'reached' without sharing his location — worth a quick call."
            else:
                what = (
                    "The captain pressed 'reached'"
                    if stage == "arrival"
                    else f"The {stage}-photo was captured"
                )
                text = f"{what} {distance_m}m from the customer's address — worth a quick review."
            await self.notifications.notify(
                center["manager_id"],
                f"🚨 Location flagged — booking {booking['booking_number']}",
                text,
                NotificationType.BOOKING,
                str(booking["_id"]),
                background=True,  # raised mid-request on the captain's phone
            )

    async def available_slots(self, service_center_id: str, date_str: str) -> list[dict]:
        """Customer-facing availability for one center/date — deliberately
        never returns the raw total capacity (see the "status"/"remaining"
        shape below), only ever the exact wording the UI needs. remaining
        is populated ONLY when status is "low" (<= LOW_SLOT_THRESHOLD left) or "full" (0);
        callers must never infer total capacity from any combination of
        these fields.

        A center-wide daily cap counts too (audit SLOT-05): once the day is
        full every slot reads "full" — it used to show "available" and then
        refuse the booking. A slot whose capacity was never configured reads
        "full" (fail closed, see _slot_capacity_defaults)."""
        center = await self.center_repo.find_by_id(service_center_id)
        if not center:
            raise NotFoundException("Service center not found")
        policy = await self.policy_service.get_policy()
        raw_slots = center_slots(center, policy)

        try:
            scheduled_date = datetime.strptime(date_str, "%Y-%m-%d")
        except ValueError:
            raise BadRequestException("Invalid date")
        # Same window every booking path enforces — showing slots the
        # submit would reject is just a broken promise to the customer.
        _ensure_within_advance_window(scheduled_date, policy)
        # Expired holds must never make a slot look busier than it is —
        # sweep this center/date before computing (the reminder loop also
        # sweeps globally as a backstop).
        await self._sweep_holds({"service_center_id": service_center_id, "date": date_str})
        # One query for the whole day's capacity docs — this endpoint is
        # public and polled every 60s per open slot picker; a find per slot
        # multiplied that traffic by ~12.
        day_docs = {
            d["slot_key"]: d
            for d in await self.slot_capacity_repo.collection.find(
                {"service_center_id": service_center_id, "date": date_str}
            ).to_list(length=200)
        }
        # The starting capacity of every untouched slot AND the daily cap come
        # from ONE per-day policy lookup (this endpoint is public and polled).
        cap_policy = await self.capacity_policy_service.get_effective_policy(service_center_id, date_str)
        defaults = await self._slot_capacity_defaults(center, date_str, [s["key"] for s in raw_slots], policy=policy, cap_policy=cap_policy)
        day_left = await self._day_seats_left(center, date_str, cap_policy=cap_policy)
        results = []
        for slot in raw_slots:
            slot_start, slot_end = _resolve_slot_window(center, scheduled_date, slot["key"], policy)
            cutoff_passed = _slot_cutoff_passed(slot_end, policy)
            doc = day_docs.get(slot["key"])
            capacity = int(doc["capacity"]) if doc else int(defaults.get(slot["key"]) or 0)
            booked = doc["booked_count"] if doc else 0
            held = (doc or {}).get("held_count", 0) or 0
            is_closed = bool(doc and doc.get("is_closed"))
            remaining_actual = max(capacity - booked - held, 0)
            if day_left is not None:
                remaining_actual = min(remaining_actual, day_left)

            if cutoff_passed or is_closed or remaining_actual <= 0:
                status, remaining = "full", 0
            elif remaining_actual <= LOW_SLOT_THRESHOLD:
                status, remaining = "low", remaining_actual
            else:
                status, remaining = "available", None
            results.append({"key": slot["key"], "start": slot["start"], "end": slot["end"], "status": status, "remaining": remaining})
        return results

    async def _day_seats_left(self, center: dict, date_str: str, cap_policy: dict | None = None) -> int | None:
        """Seats left under the center-wide daily cap for that date, or None
        when there is no daily cap. Read-only: a day nobody has booked into
        yet has no counter doc, so it counts the seats already owned.
        `cap_policy`: the day's effective capacity policy, if already loaded."""
        max_daily = await self._effective_daily_max(center, date_str, cap_policy=cap_policy)
        if not max_daily:
            return None
        center_id = str(center["_id"])
        doc = await self.daily_capacity_repo.collection.find_one({"service_center_id": center_id, "date": date_str})
        if doc:
            return max(int(doc.get("capacity") or 0) - int(doc.get("booked_count") or 0), 0)
        return max(int(max_daily) - await self._seats_owned_on(center_id, date_str), 0)

    async def _seats_owned_on(self, center_id: str, date_str: str, session=None) -> int:
        """How many bookings RECORD holding a seat at this center that day."""
        return await self.repo.collection.count_documents(
            {"holds_seat": True, "seat_key.service_center_id": center_id, "seat_key.date": date_str}, session=session
        )

    @staticmethod
    def _parse_override_day(date_str: str) -> datetime:
        try:
            return datetime.strptime(date_str, "%Y-%m-%d")
        except (TypeError, ValueError):
            raise BadRequestException("Pick a valid date (YYYY-MM-DD).")

    async def admin_slot_capacity(self, service_center_id: str, date_str: str) -> dict:
        """Staff-facing capacity view for one center/date — unlike
        available_slots (customer-facing, never leaks raw numbers), this
        one exists specifically so a manager/admin CAN see capacity,
        booked count, and remaining, per Section 2/22's admin visibility
        requirement.

        READ-ONLY (audit MGR-05): a slot nobody has touched yet shows the
        capacity it WOULD start with (_slot_capacity_defaults — the same
        figure set_slot_capacity and the first booking initialize it to);
        nothing is written, so browsing junk or far dates leaves no docs.
        `configured` is False for a slot that takes no bookings because its
        capacity was never set up."""
        center = await self.center_repo.find_by_id(service_center_id)
        if not center:
            raise NotFoundException("Service center not found")
        self._parse_override_day(date_str)
        policy = await self.policy_service.get_policy()
        raw_slots = center_slots(center, policy)
        docs = {
            d["slot_key"]: d
            for d in await self.slot_capacity_repo.collection.find({"service_center_id": service_center_id, "date": date_str}).to_list(length=200)
        }
        cap_policy = await self.capacity_policy_service.get_effective_policy(service_center_id, date_str)
        defaults = await self._slot_capacity_defaults(center, date_str, [s["key"] for s in raw_slots], policy=policy, cap_policy=cap_policy)

        results = []
        for slot in raw_slots:
            doc = docs.get(slot["key"])
            capacity = int(doc["capacity"]) if doc else int(defaults.get(slot["key"]) or 0)
            booked = int(doc["booked_count"]) if doc else 0
            results.append({
                "key": slot["key"],
                "start": slot["start"],
                "end": slot["end"],
                "capacity": capacity,
                "booked_count": booked,
                "remaining": max(capacity - booked, 0),
                "is_closed": bool((doc or {}).get("is_closed", False)),
                "configured": doc is not None or defaults.get(slot["key"]) is not None,
            })

        max_daily = await self._effective_daily_max(center, date_str, cap_policy=cap_policy)
        daily = None
        if max_daily:
            day_doc = await self.daily_capacity_repo.collection.find_one({"service_center_id": service_center_id, "date": date_str})
            day_cap = int(day_doc["capacity"]) if day_doc else int(max_daily)
            day_booked = int(day_doc["booked_count"]) if day_doc else await self._seats_owned_on(service_center_id, date_str)
            daily = {"capacity": day_cap, "booked_count": day_booked, "remaining": max(day_cap - day_booked, 0)}
        return {"slots": results, "daily": daily}

    # How far ahead a per-date override may be set — a real planning
    # horizon, not "year 9999" (audit SLOT-04).
    OVERRIDE_MAX_DAYS_AHEAD = 366

    async def set_slot_capacity(self, service_center_id: str, date_str: str, slot_key: str, capacity: int | None, is_closed: bool | None) -> dict:
        """Admin/manager override for one specific (center, date, slot) —
        increase/decrease capacity, or temporarily close/reopen it.
        Immediately affects future reservations (BookingService.
        _reserve_slot_capacity reads this same field live on every
        attempt); never retroactively invalidates bookings already
        holding a spot in this slot.

        Only for a real date (today .. a year out) and one of the center's
        CURRENT slots (audit SLOT-04: junk dates/keys minted docs forever)."""
        center = await self.center_repo.find_by_id(service_center_id)
        if not center:
            raise NotFoundException("Service center not found")
        day = self._parse_override_day(date_str).date()
        today = now_ist().date()
        if day < today:
            raise BadRequestException("That date has already passed — capacity can be changed for today or later.")
        if day > today + timedelta(days=self.OVERRIDE_MAX_DAYS_AHEAD):
            raise BadRequestException("Pick a date within the next year.")
        policy = await self.policy_service.get_policy()
        if slot_key not in {s["key"] for s in center_slots(center, policy)}:
            raise BadRequestException("That isn't one of this center's slots — pick a slot from the list.")
        if capacity is not None and capacity < 0:
            raise BadRequestException("Capacity can't be negative")
        update: dict = {}
        if capacity is not None:
            update["capacity"] = capacity
            # Marks this exact (date, slot) as an explicit admin override so
            # a later capacity-policy change never silently resyncs it back
            # to the baseline — see CapacityPolicyService._resync_from.
            update["is_override"] = True
        if is_closed is not None:
            update["is_closed"] = is_closed
        if not update:
            raise BadRequestException("Nothing to update")
        filter_ = {"service_center_id": service_center_id, "date": date_str, "slot_key": slot_key}
        default_capacity = await self._default_slot_capacity(center, date_str, slot_key)
        await self.slot_capacity_repo.get_or_init(filter_, {"capacity": default_capacity or 0, "booked_count": 0, "is_closed": False})
        updated = await self.slot_capacity_repo.update_by_id(str((await self.slot_capacity_repo.find_one(filter_))["_id"]), update)
        await self._broadcast_slots_changed(service_center_id, date_str)
        return serialize_doc(updated)

    async def _slot_capacity_defaults(
        self, service_center: dict, date_str: str, slot_keys: list[str], policy: dict | None = None, cap_policy: dict | None = None,
    ) -> dict[str, int | None]:
        """What not-yet-touched (date, slot)s start out with — ONE capacity
        policy lookup for any number of slots. In order:

          1. the day's effective capacity policy names the slot -> its number;
          2. the policy exists but doesn't name this key (slot length or hours
             changed under it — audit SLOT-01) -> the policy's own daily
             figure spread over the center's CURRENT slots (a stale key gets
             0). Never more than the day the admin set;
          3. no policy: the center's default_slot_capacity;
          4. a legacy center-wide daily cap alone: that cap (the daily
             counter bounds the day anyway);
          5. nothing configured -> None: the slot takes NO bookings (fail
             closed). This used to be 999 seats.

        `cap_policy`: the day's effective capacity policy, if already loaded."""
        if cap_policy is None:
            cap_policy = await self.capacity_policy_service.get_effective_policy(str(service_center["_id"]), date_str)
        distribution = cap_policy.get("slot_distribution") or {}
        daily = cap_policy.get("max_bookings_per_day")
        default = service_center.get("default_slot_capacity")
        spread: dict[str, int] | None = None
        out: dict[str, int | None] = {}
        for key in slot_keys:
            if key in distribution:
                out[key] = int(distribution[key])
            elif distribution:
                if spread is None:
                    booking_policy = policy or await self.policy_service.get_policy()
                    current = [s["key"] for s in center_slots(service_center, booking_policy)]
                    spread = self.capacity_policy_service.auto_distribute(int(daily or 0), current)
                out[key] = int(spread.get(key, 0))
            elif default is not None:
                out[key] = int(default)
            elif daily:
                out[key] = int(daily)
            else:
                out[key] = None
        return out

    async def _default_slot_capacity(self, service_center: dict, date_str: str, slot_key: str) -> int | None:
        """One slot's starting capacity — see _slot_capacity_defaults. None
        means "never configured": callers refuse the booking/hold."""
        return (await self._slot_capacity_defaults(service_center, date_str, [slot_key]))[slot_key]

    async def _effective_daily_max(self, service_center: dict, date_str: str, cap_policy: dict | None = None) -> int | None:
        """Same resolution as _default_slot_capacity, for the optional
        center-wide daily cap — the effective-dated policy's
        max_bookings_per_day wins when a policy exists for this date,
        otherwise the center's legacy flat max_bookings_per_day field (or
        None, meaning no daily cap at all, only per-slot capacity)."""
        policy = cap_policy if cap_policy is not None else await self.capacity_policy_service.get_effective_policy(
            str(service_center["_id"]), date_str
        )
        return policy["max_bookings_per_day"] or service_center.get("max_bookings_per_day")

    # ------------------------------------------------------------------
    # Slot holds — the theater-seat model (AUDIT.md M1). A hold claims one
    # unit of a slot's capacity for HOLD_MINUTES while the customer walks
    # the rest of the flow; every other customer (web AND WhatsApp) sees
    # the slot shrink immediately. Confirming converts the hold into a
    # booking; abandoning releases it; expiry is swept both by the
    # reminder loop and opportunistically on every availability read.
    # ------------------------------------------------------------------

    HOLD_MINUTES = 5
    _OCCUPIED_EXPR = {"$add": ["$booked_count", {"$ifNull": ["$held_count", 0]}]}

    def _hold_filter(self, center_id: str, date_str: str, slot_key: str) -> dict:
        return {"service_center_id": center_id, "date": date_str, "slot_key": slot_key}

    async def _sweep_holds(self, extra_filter: dict | None = None) -> int:
        """Releases expired holds (delete doc + decrement held_count) —
        one at a time so a concurrent sweeper can never double-decrement:
        only whoever actually deleted the doc decrements."""
        query = {"expires_at": {"$lt": datetime.now(timezone.utc)}, **(extra_filter or {})}
        released = 0
        # Drain in batches until empty (bounded at 10 batches/pass as a
        # safety valve) — a single capped batch meant a burst of >200
        # expiring holds per minute never fully drained, leaving slots
        # permanently over-reserved.
        for _ in range(10):
            batch = await self.db.slot_holds.find(query).limit(200).to_list(length=200)
            if not batch:
                break
            for hold in batch:
                result = await self.db.slot_holds.delete_one({"_id": hold["_id"]})
                if result.deleted_count:
                    await self.slot_capacity_repo.increment_if(
                        self._hold_filter(hold["service_center_id"], hold["date"], hold["slot_key"]),
                        {"held_count": -1},
                        expr_guard=["$gt", {"$ifNull": ["$held_count", 0]}, 0],
                    )
                    released += 1
            if len(batch) < 200:
                break
        return released

    # Holds are a checkout courtesy, never a way to empty a slot: one
    # browser holds one seat at a time, one address at most a few, and
    # holds together may only take part of a slot's seats — the rest stay
    # bookable however many holds anyone mints (the endpoint is public).
    MAX_HOLDS_PER_IP = 5

    @staticmethod
    def _hold_cap(capacity: int) -> int:
        capacity = int(capacity or 0)
        return capacity if capacity <= 1 else max(1, capacity // 2)

    async def hold_slot(self, holder_id: str, service_center_id: str, date_str: str, slot_key: str, client_ip: str | None = None) -> dict:
        """Acquires (or renews) a temporary hold on one slot unit."""
        from bson import ObjectId as _OID
        from app.core.exceptions import TooManyRequestsException

        if not _OID.is_valid(service_center_id):
            raise NotFoundException("Service center not found")
        center = await self.center_repo.find_by_id(service_center_id)
        if not center:
            raise NotFoundException("Service center not found")
        # This endpoint is unauthenticated by design (guests hold slots
        # while filling the wizard) — so the date and slot key MUST be
        # validated against the center's real generated slots. Accepting
        # arbitrary strings minted junk slot_capacity docs forever and let
        # an attacker "hold" invented slots.
        try:
            parsed_date = datetime.strptime(date_str, "%Y-%m-%d")
        except ValueError:
            raise BadRequestException("Invalid date")
        policy = await self.policy_service.get_policy()
        _ensure_within_advance_window(parsed_date, policy)
        _slot_start, slot_end = _resolve_slot_window(center, parsed_date, slot_key, policy)  # raises on an invented key
        if _slot_cutoff_passed(slot_end, policy):
            raise BadRequestException("This slot is no longer available to book — please pick another slot.")
        slot_filter = self._hold_filter(service_center_id, date_str, slot_key)
        await self._sweep_holds(slot_filter)
        expires_at = datetime.now(timezone.utc) + timedelta(minutes=self.HOLD_MINUTES)

        # Renewal: the same holder re-picking their held slot just extends it.
        renewed = await self.db.slot_holds.find_one_and_update(
            {**slot_filter, "holder_id": holder_id}, {"$set": {"expires_at": expires_at}}
        )
        if renewed:
            return {"held": True, "renewed": True, "expires_at": expires_at.isoformat(), "hold_seconds": self.HOLD_MINUTES * 60}

        # One seat per browser: claiming a new one gives back any other.
        for other in await self.db.slot_holds.find({"holder_id": holder_id}).to_list(length=20):
            await self.release_hold(holder_id, other["service_center_id"], other["date"], other["slot_key"])
        if client_ip and await self.db.slot_holds.count_documents(
            {"client_ip": client_ip, "expires_at": {"$gt": datetime.now(timezone.utc)}}
        ) >= self.MAX_HOLDS_PER_IP:
            raise TooManyRequestsException("Too many times are being held from this connection — please book or wait a few minutes.")

        default_capacity = await self._default_slot_capacity(center, date_str, slot_key)
        if default_capacity is None and not await self.slot_capacity_repo.collection.find_one(slot_filter):
            raise BadRequestException(UNCONFIGURED_CAPACITY_MESSAGE)
        slot_doc = await self.slot_capacity_repo.get_or_init(
            slot_filter, {"capacity": default_capacity or 0, "booked_count": 0, "held_count": 0, "is_closed": False}
        )
        if slot_doc.get("is_closed"):
            raise BadRequestException("This slot has been closed for booking — please pick another.")
        slot_capacity = int(slot_doc.get("capacity") or 0)
        hold_cap = self._hold_cap(slot_capacity)
        held_now = int(slot_doc.get("held_count") or 0)
        if held_now + int(slot_doc.get("booked_count") or 0) >= slot_capacity:
            raise BadRequestException("This slot just became fully booked — please pick another.")
        if held_now >= hold_cap:
            # Others are mid-checkout on the seats holds may take — no hold
            # now, but the slot itself stays bookable (the booking checks
            # real capacity). 429 = "no hold", not "slot gone".
            raise TooManyRequestsException("Others are booking this time right now — you can still book it.")
        claimed = await self.slot_capacity_repo.increment_if(
            {**slot_filter, "is_closed": False, "held_count": {"$not": {"$gte": hold_cap}}},
            {"held_count": 1},
            expr_guard=["$lt", self._OCCUPIED_EXPR, "$capacity"],
        )
        if claimed is None:
            raise BadRequestException("This slot just became fully booked — please pick another.")
        try:
            await self.db.slot_holds.insert_one({
                **slot_filter, "holder_id": holder_id, "client_ip": client_ip,
                "created_at": datetime.now(timezone.utc), "expires_at": expires_at,
            })
        except DuplicateKeyError:
            # Same holder double-tapped concurrently: give back the extra
            # unit we just claimed and treat it as a renewal.
            await self.slot_capacity_repo.increment_if(
                slot_filter, {"held_count": -1}, expr_guard=["$gt", {"$ifNull": ["$held_count", 0]}, 0]
            )
            await self.db.slot_holds.update_one({**slot_filter, "holder_id": holder_id}, {"$set": {"expires_at": expires_at}})
        return {"held": True, "renewed": False, "expires_at": expires_at.isoformat(), "hold_seconds": self.HOLD_MINUTES * 60}

    async def release_hold(self, holder_id: str, service_center_id: str, date_str: str, slot_key: str) -> dict:
        slot_filter = self._hold_filter(service_center_id, date_str, slot_key)
        result = await self.db.slot_holds.delete_one({**slot_filter, "holder_id": holder_id})
        if result.deleted_count:
            await self.slot_capacity_repo.increment_if(
                slot_filter, {"held_count": -1}, expr_guard=["$gt", {"$ifNull": ["$held_count", 0]}, 0]
            )
        return {"released": bool(result.deleted_count)}

    async def _reserve_slot_capacity(
        self, session, service_center: dict, date_str: str, slot_key: str, holder_ids: list[str] | None = None, day: bool = True,
    ) -> None:
        """Atomically reserves one spot in this slot (and, if the center
        has a daily cap configured, one spot in the day too) — single
        find_one_and_update per counter, no retry loop needed since the
        whole success condition ("count < capacity") is expressible
        directly in the filter (see BaseRepository.increment_if). Raises
        BadRequestException if either is already at capacity or closed, or
        if the slot's capacity was never configured (fail closed).

        This is ONLY the counter half of taking a seat: callers record the
        ownership (holds_seat/seat_key) on the booking in the same
        transaction — see _take_seat. `day=False` skips the daily counter
        (a move within the same day keeps its place in the day)."""
        center_id = str(service_center["_id"])
        slot_key_filter = {"service_center_id": center_id, "date": date_str, "slot_key": slot_key}
        default_capacity = await self._default_slot_capacity(service_center, date_str, slot_key)
        if default_capacity is None and not await self.slot_capacity_repo.collection.find_one(slot_key_filter, session=session):
            raise BadRequestException(UNCONFIGURED_CAPACITY_MESSAGE)
        slot_doc = await self.slot_capacity_repo.get_or_init(
            slot_key_filter, {"capacity": default_capacity or 0, "booked_count": 0, "is_closed": False}, session=session
        )
        if slot_doc.get("is_closed"):
            raise BadRequestException("This slot has been closed for booking — please pick another.")
        # Consume the booker's own hold (if any) so it converts instead of
        # blocking them; everyone else's holds count as occupied. A booker
        # WITH a hold is guaranteed a seat by the hold invariant
        # (booked + held <= capacity), so their guard is the plain
        # booked < capacity; a booker without one must clear booked+held.
        had_hold = False
        if holder_ids:
            deleted = await self.db.slot_holds.delete_one(
                {**slot_key_filter, "holder_id": {"$in": [h for h in holder_ids if h]}}, session=session
            )
            had_hold = bool(deleted.deleted_count)
        if had_hold:
            reserved = await self.slot_capacity_repo.increment_if(
                {**slot_key_filter, "is_closed": False},
                {"booked_count": 1, "held_count": -1},
                expr_guard=["$lt", "$booked_count", "$capacity"],
                session=session,
            )
        else:
            reserved = await self.slot_capacity_repo.increment_if(
                {**slot_key_filter, "is_closed": False},
                {"booked_count": 1},
                expr_guard=["$lt", self._OCCUPIED_EXPR, "$capacity"],
                session=session,
            )
        if reserved is None:
            if had_hold:
                # The consumed hold's unit must not leak.
                await self.slot_capacity_repo.increment_if(
                    slot_key_filter, {"held_count": -1}, expr_guard=["$gt", {"$ifNull": ["$held_count", 0]}, 0], session=session
                )
            raise BadRequestException("This slot just became fully booked — please pick another.")

        max_daily = await self._effective_daily_max(service_center, date_str) if day else None
        if max_daily:
            day_filter = {"service_center_id": center_id, "date": date_str}
            if not await self.daily_capacity_repo.collection.find_one(day_filter, session=session):
                # A cap configured after the day already had bookings starts
                # from the seats those bookings own, not from zero.
                owned = await self._seats_owned_on(center_id, date_str, session=session)
                await self.daily_capacity_repo.get_or_init(day_filter, {"capacity": max_daily, "booked_count": owned}, session=session)
            reserved_day = await self.daily_capacity_repo.increment_if(
                day_filter, {"booked_count": 1}, expr_guard=["$lt", "$booked_count", "$capacity"], session=session
            )
            if reserved_day is None:
                # Roll back the slot reservation we just took — the
                # transaction as a whole is about to raise/abort anyway,
                # but making the compensating decrement explicit here means
                # the invariant holds even if this method is ever called
                # outside a transaction in the future.
                await self.slot_capacity_repo.increment_if(slot_key_filter, {"booked_count": -1}, session=session)
                raise BadRequestException("This service center is fully booked for the day — please pick another date.")

    async def _release_slot_capacity(self, service_center_id: str, date_str: str, slot_key: str, session=None, day: bool = True) -> None:
        """Reverses _reserve_slot_capacity's COUNTERS only — guarded at 0
        (never goes negative); is_closed is irrelevant here since closing
        only blocks NEW reservations. Bookings never call this directly:
        they go through _release_seat, which first claims the booking's
        recorded ownership so a seat is handed back exactly once."""
        slot_key_filter = {"service_center_id": service_center_id, "date": date_str, "slot_key": slot_key}
        await self.slot_capacity_repo.increment_if(slot_key_filter, {"booked_count": -1}, expr_guard=["$gt", "$booked_count", 0], session=session)
        if day:
            day_filter = {"service_center_id": service_center_id, "date": date_str}
            await self.daily_capacity_repo.increment_if(day_filter, {"booked_count": -1}, expr_guard=["$gt", "$booked_count", 0], session=session)

    async def _resolve_service_center(self, address: dict, allow_pinless: bool = False) -> tuple[dict, float]:
        from app.services.zone_service import ZoneService
        from app.utils.geo import haversine_km

        lat, lng = address.get("latitude"), address.get("longitude")

        # Polygon zones are the authority WHEN they exist and the address
        # has a real pin: the pin either falls inside a drawn zone or the
        # area isn't served — a mistyped pincode can no longer smuggle an
        # out-of-area booking in, and can no longer block an in-area one.
        zones = ZoneService(self.db)
        zoned = await zones.zones_exist()
        # Once zones exist, a typed-only address (no pin) can't prove where
        # it actually is — customer self-service REQUIRES the pin. Staff
        # bookings (allow_pinless) keep the legacy pincode path for
        # phone-in customers a manager vouches for.
        if zoned and (lat is None or lng is None) and not allow_pinless:
            raise BadRequestException(
                "Please select your area from the suggestions or pin your location on the map — "
                "typed addresses alone can't be verified against our service area. [PIN_REQUIRED]"
            )
        if lat is not None and lng is not None and zoned:
            matches = await zones.zones_for_point(lat, lng)
            if not matches:
                raise BadRequestException("Doorstep service is not yet available at this exact location")
            best: tuple[dict, float] | None = None
            for zone in matches:
                center = await self.center_repo.find_by_id(zone["service_center_id"])
                if not center or not center.get("is_active", True):
                    continue
                c_lat, c_lng = self._center_coords(center)
                distance = haversine_km(lat, lng, c_lat, c_lng) if c_lat is not None else 0.0
                if best is None or distance < best[1]:
                    best = (center, round(distance, 2))
            if best:
                return best
            raise BadRequestException("Doorstep service is not yet available at this exact location")

        # Legacy behavior (no zones drawn, or an address without a pin):
        # nearest center by coordinates, else pincode match.
        if lat is not None and lng is not None:
            match = await self.center_repo.find_nearest(lat, lng)
            if match:
                return match

        centers = await self.center_repo.find_by_pincode(address["pincode"])
        if centers:
            # A pin matched only by pincode (outside the center's radius) still
            # has a real distance — the distance charge and captain travel pay need it.
            c_lat, c_lng = self._center_coords(centers[0])
            if lat is not None and lng is not None and c_lat is not None:
                return centers[0], round(haversine_km(lat, lng, c_lat, c_lng), 2)
            return centers[0], 0.0

        raise BadRequestException("Doorstep service is not yet available in your area")

    async def get_booking(self, booking_id: str) -> dict:
        booking = await self.repo.find_by_id(booking_id)
        if not booking:
            raise NotFoundException("Booking not found")
        enriched = await self._enrich_bookings([booking])
        return enriched[0]

    async def get_booking_with_history(
        self, booking_id: str, actor_id: str, actor_role: str, actor_center_id: str | None = None
    ) -> dict:
        booking = await self.get_booking(booking_id)
        if actor_role == "manager":
            ensure_own_center(actor_role, actor_center_id, booking.get("service_center_id"))
        elif actor_role != "admin" and actor_id not in {booking.get("customer_id"), booking.get("captain_id")}:
            raise ForbiddenException("You don't have access to this booking")
        history = await self.history_repo.list_for_booking(booking_id)
        booking["status_history"] = serialize_list(history)
        return _redact_financials(booking, actor_role)

    async def list_for_customer(self, customer_id: str, status: str | None, page: int, page_size: int):
        items, total = await self.repo.list_for_customer(customer_id, status, page, page_size)
        # Enriched exactly like the captain/staff lists (service_names,
        # vehicle snapshot…) — the customer's own booking cards need the
        # real service names, not a "Service" fallback.
        enriched = await self._enrich_bookings(items)
        return [_redact_financials(b, "customer") for b in enriched], total

    async def list_for_captain(self, captain_id: str, status: str | None, page: int, page_size: int, scope: str | None = None):
        items, total = await self.repo.list_for_captain(captain_id, status, page, page_size, scope=scope)
        enriched = await self._enrich_bookings(items)
        return [_redact_financials(b, "captain") for b in enriched], total

    async def _enrich_bookings(self, bookings: list[dict]) -> list[dict]:
        """Denormalizes customer name/phone, a vehicle snapshot, a flattened
        address, and service/combo names onto each booking so a captain (or
        anyone viewing a single booking) doesn't have to separately resolve
        raw customer_id/vehicle_id/address_id/service_ids — none of which
        they're otherwise authorized to look up directly. Batched via
        find_by_ids to avoid an N+1 query per booking in the list case."""
        if not bookings:
            return []
        customer_ids = {b.get("customer_id") for b in bookings}
        vehicle_ids = {b.get("vehicle_id") for b in bookings if b.get("vehicle_id")}
        # Only bookings from before address snapshots read the live address.
        address_ids = {b.get("address_id") for b in bookings if not b.get("address_snapshot")}
        captain_ids = {b.get("captain_id") for b in bookings if b.get("captain_id")}
        # Who put a tip / a manager discount on a job (admin visibility).
        staff_ids = {b.get(f) for b in bookings for f in ("tip_updated_by", "manager_discount_by") if b.get(f)}
        service_ids: set[str] = set()
        combo_ids: set[str] = set()
        for b in bookings:
            if b.get("combo_id"):
                combo_ids.add(b["combo_id"])
            else:
                service_ids.update(b.get("service_ids") or [])

        customers = {str(u["_id"]): u for u in await self.user_repo.find_by_ids(list(customer_ids))}
        captains = {str(u["_id"]): u for u in await self.user_repo.find_by_ids(list(captain_ids | staff_ids))} if captain_ids or staff_ids else {}
        vehicles = {str(v["_id"]): v for v in await self.vehicle_repo.find_by_ids(list(vehicle_ids))} if vehicle_ids else {}
        addresses = {str(a["_id"]): a for a in await self.address_repo.find_by_ids(list(address_ids))} if address_ids else {}
        services = {str(s["_id"]): s for s in await self.service_repo.find_by_ids(list(service_ids))} if service_ids else {}
        combos = {str(c["_id"]): c for c in await self.combo_repo.find_by_ids(list(combo_ids))} if combo_ids else {}
        # The car TYPE name ("Sedan") on every row — staff lists show it
        # even for an older saved-vehicle booking whose label is "Brand
        # Model". One batched lookup; a retired type still resolves.
        type_ids = {b.get("vehicle_type") for b in bookings if b.get("vehicle_type")}
        type_ids.update(v.get("vehicle_type") for v in vehicles.values() if v.get("vehicle_type"))
        type_oids = [ObjectId(t) for t in type_ids if isinstance(t, str) and ObjectId.is_valid(t)]
        type_names = (
            {
                str(t["_id"]): t.get("name")
                for t in await self.vehicle_type_repo.collection.find({"_id": {"$in": type_oids}}, {"name": 1}).to_list(length=len(type_oids))
            }
            if type_oids
            else {}
        )
        # A wash on a society pass reads "Star Wash · Society plan (Green
        # Acres)" everywhere a booking is listed (docs/SOCIETY_PLANS.md).
        from app.services.society_service import society_names_for_subscriptions, society_plan_label

        society_of = await society_names_for_subscriptions(self.repo.db, [b.get("subscription_id") for b in bookings if b.get("subscription_id")])

        results = []
        for booking in bookings:
            doc = serialize_doc(booking)
            customer = customers.get(booking.get("customer_id"))
            vehicle = vehicles.get(booking.get("vehicle_id"))
            address = booking.get("address_snapshot") or addresses.get(booking.get("address_id"))

            doc["customer_name"] = customer.get("full_name") if customer else None
            # customer_phone is already snapshotted on the booking itself at
            # creation time — kept as-is, no lookup needed for it.
            # The captain's public card, deliberately shared with the
            # CUSTOMER once someone is assigned — photo, staff id and phone
            # so they know exactly who is coming to their door. Only these
            # five fields; never location, KYC numbers, or anything else
            # from the captain's user doc.
            captain = captains.get(booking.get("captain_id") or "")
            kyc = (captain or {}).get("captain_kyc") or {}
            doc["captain_profile"] = (
                {
                    "full_name": captain.get("full_name"),
                    "phone": captain.get("phone"),
                    "employee_id": captain.get("employee_id"),
                    "photo_url": kyc.get("photo_url") or captain.get("profile_image"),
                    "verified": kyc.get("status") == "verified",
                }
                if captain
                else None
            )
            # A saved vehicle record when the booking has one; otherwise the
            # type the booking itself carries (quick-booking model) — the
            # label ("SUV") is what every screen should show.
            doc["vehicle_snapshot"] = (
                {
                    "vehicle_type": vehicle.get("vehicle_type"),
                    "brand": vehicle.get("brand"),
                    "model": vehicle.get("model"),
                    "registration_number": vehicle.get("registration_number"),
                    "label": f"{vehicle.get('brand') or ''} {vehicle.get('model') or ''}".strip() or booking.get("vehicle_label") or "Vehicle",
                }
                if vehicle
                else {
                    "vehicle_type": booking.get("vehicle_type"),
                    "brand": None,
                    "model": None,
                    "registration_number": None,
                    "label": booking.get("vehicle_label") or "Vehicle",
                }
            )
            doc["vehicle_type_name"] = (
                type_names.get(booking.get("vehicle_type") or "")
                or type_names.get((vehicle or {}).get("vehicle_type") or "")
                or (booking.get("vehicle_label") if not vehicle else None)
            )
            doc["address_snapshot"] = (
                {
                    "line1": address.get("line1"),
                    "landmark": address.get("landmark"),
                    "city": address.get("city"),
                    "state": address.get("state"),
                    "pincode": address.get("pincode"),
                    "latitude": address.get("latitude"),
                    "longitude": address.get("longitude"),
                }
                if address
                else None
            )
            if booking.get("combo_id"):
                combo = combos.get(booking["combo_id"])
                doc["combo_name"] = combo.get("name") if combo else None
                doc["service_names"] = None
            else:
                doc["combo_name"] = None
                qty_map = booking.get("service_quantities") or {}
                doc["service_names"] = [
                    services[sid]["name"] + (f" ×{qty_map[sid]}" if qty_map.get(sid, 1) > 1 else "")
                    for sid in (booking.get("service_ids") or [])
                    if sid in services
                ] or None
            society = society_of.get(booking.get("subscription_id") or "")
            if society:
                doc["society_id"] = society["society_id"]
                doc["society_name"] = society["society_name"]
                doc["plan_label"] = society_plan_label(society["society_name"])
            # Money every screen shows the same way: a late-cancellation
            # charge this booking carries (part of total_amount — also what
            # the captain collects), and on a manager-done job the tip and
            # the manager's discount with who gave them and when.
            doc["cancellation_charge"] = float(booking.get("cancellation_charge") or 0)
            doc["tip_amount"] = float(booking.get("tip_amount") or 0)
            doc["manager_discount"] = float(booking.get("manager_discount") or 0)
            # The money every screen reads (spec 1.5, booking_money): what it
            # costs, wallet credit spent on it, what was received, what is
            # still due — derived the same way for old and new bookings.
            money = bm.money_view(booking)
            doc["wallet_applied"] = money["wallet_applied"]
            doc["amount_paid"] = money["amount_paid"]
            doc["amount_due"] = money["amount_due"]
            doc["wallet_due_carried"] = money["wallet_due_carried"]
            doc["added_services_total"] = float(booking.get("added_services_total") or 0)
            if doc.get("manager_paybacks"):
                # Older paybacks were stored without their label — every row
                # reads "Service Delayed", never the raw code (FINAL-POLISH).
                from app.services.customer_wallet_service import REASON_LABELS

                doc["manager_paybacks"] = [
                    {**p, "reason_label": p.get("reason_label") or REASON_LABELS.get(p.get("reason") or "")} if isinstance(p, dict) else p
                    for p in doc["manager_paybacks"]
                ]
            doc["tip_updated_by_name"] = (captains.get(booking.get("tip_updated_by") or "") or {}).get("full_name")
            doc["manager_discount_by_name"] = (captains.get(booking.get("manager_discount_by") or "") or {}).get("full_name")
            results.append(doc)
        return results

    async def list_for_center(
        self,
        service_center_id: str,
        extra_filters: dict,
        page: int,
        page_size: int,
        actor_role: str,
        actor_center_id: str | None,
        queue: list[dict] | None = None,
        sort: str | None = None,
    ):
        """extra_filters carries status and/or the period-window filter the
        route builds (see booking_routes.py's _apply_period_filters) —
        same shape list_all (the admin GET /bookings) uses, so a manager's
        own KPI drill-down can reuse the exact same RevenueDrillModal
        component, just scoped to their center. `queue` = extra clauses from
        center_queue_filters (ANDed, so their own $or never collides with
        the period filter's)."""
        ensure_own_center(actor_role, actor_center_id, service_center_id)
        filters: dict = {"service_center_id": service_center_id, **extra_filters}
        if queue:
            filters["$and"] = list(queue)
        try:
            items, total = await self.repo.list_queue(filters, page, page_size, sort)
        except ValueError as exc:
            raise BadRequestException(str(exc))
        enriched = await self._enrich_bookings(items)
        return enriched, total

    # The subscribers page lists the most recent customers first; beyond this
    # many it would be a data dump, not a page (PERF-03).
    SUBSCRIBERS_LIMIT = 1000

    async def list_subscribers_for_center(self, service_center_id: str, actor_role: str, actor_center_id: str | None) -> list[dict]:
        """One row per customer who has used a subscription at this center —
        answers 'who purchased which plan and got served from my store'.
        One grouped query + three batched lookups (PERF-03: it used to read
        every plan booking the center ever had, then 3 queries per customer)."""
        ensure_own_center(actor_role, actor_center_id, service_center_id)
        groups = await self.repo.collection.aggregate([
            {"$match": {"service_center_id": service_center_id, "subscription_id": {"$nin": [None, ""]}, "is_deleted": {"$ne": True}}},
            {"$sort": {"created_at": -1}},
            {"$group": {
                "_id": "$customer_id",
                "visits": {"$sum": 1},
                "last_visit": {"$first": "$created_at"},
                "subscription_id": {"$first": "$subscription_id"},
            }},
            {"$sort": {"last_visit": -1}},
            {"$limit": self.SUBSCRIBERS_LIMIT},
        ]).to_list(length=self.SUBSCRIBERS_LIMIT)

        def _oids(values) -> list[ObjectId]:
            return [ObjectId(v) for v in {str(v) for v in values if v} if ObjectId.is_valid(v)]

        customers = {
            str(u["_id"]): u
            for u in await self.db.users.find({"_id": {"$in": _oids(g["_id"] for g in groups)}}, {"full_name": 1, "phone": 1}).to_list(length=None)
        }
        subscriptions = {
            str(sub["_id"]): sub
            for sub in await self.db.user_subscriptions.find(
                {"_id": {"$in": _oids(g.get("subscription_id") for g in groups)}}, {"plan_id": 1, "status": 1, "remaining_service_count": 1}
            ).to_list(length=None)
        }
        plans = {
            str(p["_id"]): p
            for p in await self.db.subscription_plans.find(
                {"_id": {"$in": _oids(sub.get("plan_id") for sub in subscriptions.values())}}, {"name": 1}
            ).to_list(length=None)
        }
        rows = []
        for g in groups:
            customer = customers.get(str(g["_id"]))
            subscription = subscriptions.get(str(g.get("subscription_id")))
            plan = plans.get(str(subscription.get("plan_id"))) if subscription else None
            rows.append({
                "customer_id": g["_id"],
                "customer_name": customer.get("full_name") if customer else "Unknown",
                "customer_phone": customer.get("phone") if customer else None,
                "plan_name": plan.get("name") if plan else "Unknown plan",
                "subscription_status": subscription.get("status") if subscription else None,
                "remaining_service_count": subscription.get("remaining_service_count") if subscription else None,
                "last_visit": g["last_visit"],
                "visits": g["visits"],
            })
        return rows

    async def list_all(self, filters: dict, page: int, page_size: int):
        items, total = await self.repo.list_all(filters, page, page_size)
        # Same enrichment every other booking list applies (customer name,
        # vehicle/address/service labels) — without it this admin-only list
        # (and anything built on it, like the KPI drill-down modals) showed
        # a bare "—" instead of the customer's name.
        enriched = await self._enrich_bookings(items)
        return enriched, total

    async def list_recycle_bin(self, page: int, page_size: int) -> tuple[list[dict], int]:
        """Soft-deleted bookings, most-recently-deleted first. Each row
        gets days_remaining until the 30-day auto-purge sweep (main.py)
        removes it for good."""
        items, total = await self.repo.find_many(
            {"is_deleted": True}, page, page_size, sort_by="deleted_at", sort_order=-1, include_deleted=True,
        )
        enriched = await self._enrich_bookings(items)
        now = now_ist()
        for row, item in zip(enriched, items):
            deleted_at = item.get("deleted_at")
            row["days_remaining"] = max(0, round(30 - (now - from_stored(deleted_at)).total_seconds() / 86400)) if deleted_at else None
        return enriched, total

    async def get_booking_group(self, booking_group_id: str, actor_id: str, actor_role: str, actor_center_id: str | None) -> list[dict]:
        """Every car on a visit, enriched the same way a single booking is.
        A customer sees their own visit; staff see one their center serves."""
        bookings = await self.repo.collection.find(
            {"booking_group_id": booking_group_id, "is_deleted": {"$ne": True}}
        ).sort("group_offset_minutes", 1).to_list(length=20)
        if not bookings:
            raise NotFoundException("Booking not found")
        if actor_role == "customer":
            # 404 rather than 403: a guessed group id must not confirm that
            # somebody else's visit exists.
            if any(b.get("customer_id") != actor_id for b in bookings):
                raise NotFoundException("Booking not found")
        elif actor_role == "manager":
            ensure_own_center(actor_role, actor_center_id, bookings[0]["service_center_id"])
        elif actor_role == "captain":
            # A captain sees a visit he is actually working — being on ONE
            # car is enough (that IS the visit), being on none is not.
            if not any(b.get("captain_id") == actor_id for b in bookings):
                raise NotFoundException("Booking not found")
        enriched = await self._enrich_bookings(bookings)
        # Same as get_booking_with_history — without this, the "Status
        # timeline" card on a multi-vehicle visit had nothing to show for
        # ANY car (each one's status_history was simply never attached),
        # even though the single-booking read right next to it always has
        # it.
        for doc in enriched:
            history = await self.history_repo.list_for_booking(doc["id"])
            doc["status_history"] = serialize_list(history)
        # Same redaction every other booking read applies: a customer never
        # sees the captain's pay or the internal issue flags, a captain never
        # sees the platform's margin.
        return [_redact_financials(doc, actor_role) for doc in enriched]

    async def switch_group_to_cash(self, booking_group_id: str, customer_id: str, notify_background: bool = True) -> dict:
        """"Let me pay the captain instead" for a whole visit — one decision
        covers every car on it, because the customer made one booking."""
        bookings = await self.repo.collection.find(
            {"booking_group_id": booking_group_id, "is_deleted": {"$ne": True}}
        ).to_list(length=20)
        if not bookings or any(b.get("customer_id") != customer_id for b in bookings):
            raise NotFoundException("Booking not found")
        if any(b.get("prepaid_only") and b.get("status") != BookingStatus.CANCELLED.value for b in bookings):
            raise BadRequestException(PREPAID_CASH_REFUSAL)
        # Decided for the whole visit up front, or a refusal on car 2 left
        # car 1 already switched.
        if self._plan_extras_online_only(self._plan_rows([b for b in bookings if b.get("status") != BookingStatus.CANCELLED.value])):
            raise BadRequestException(PLAN_EXTRAS_CASH_REFUSAL)
        switched = 0
        for booking in bookings:
            if booking.get("status") != BookingStatus.AWAITING_PAYMENT.value:
                continue
            if self._owes_nothing(booking):
                continue  # a ₹0 plan car has no payment to switch — it is confirmed with the car that pays
            await self.switch_to_cash(str(booking["_id"]), customer_id, notify_background=notify_background)
            switched += 1
        if not switched:
            raise BadRequestException("This visit is already confirmed.")
        return {"booking_group_id": booking_group_id, "switched_count": switched}

    async def cancel_booking_group(
        self, booking_group_id: str, payload: BookingCancelRequest, actor_id: str, actor_role: str, actor_center_id: str | None = None,
        only_if_unpaid: bool = False,
    ) -> dict:
        """Cancelling a visit cancels every car on it — in ONE transaction,
        so it can't stop half-way (a car that changed state meanwhile
        refuses the whole cancel) and the visit's single seat goes back once.
        Leaving one car behind would send a captain out for half a job the
        customer thinks they called off.

        `only_if_unpaid` — the payment-window expiry sweep (audit PAY-02):
        each car is released only while it is STILL unpaid, in the same
        write; a car that got paid (or confirmed) meanwhile is skipped
        silently, and so are the visit's ₹0 plan cars while any paying car
        stands — they are confirmed with it instead.

        Spec 1.2: the customer cancels until the captain heads out; plan
        cars follow the plan rule (wash used up inside the last hour), the
        paid part pays the tier once per visit; the visit's money goes to
        the customer's wallet in the same transaction."""
        bookings = await self.repo.collection.find(
            {"booking_group_id": booking_group_id, "is_deleted": {"$ne": True}}
        ).to_list(length=20)
        if not bookings:
            if only_if_unpaid:
                return {"booking_group_id": booking_group_id, "cancelled_count": 0}
            raise NotFoundException("Booking not found")
        live = [b for b in bookings if b.get("status") not in {BookingStatus.CANCELLED.value, BookingStatus.COMPLETED.value}]
        if not only_if_unpaid:
            if not live:
                raise BadRequestException("This visit can no longer be cancelled.")
            for booking in live:
                self._validate_cancel(booking, actor_id, actor_role, actor_center_id)
            if actor_role == "customer":
                self._ensure_customer_may_cancel([b for b in bookings if b.get("status") != BookingStatus.CANCELLED.value])
        validated = {str(b["_id"]): b for b in live}
        lead_row = bookings[0]
        # The whole visit is called off — the late-cancellation charge, if
        # any, is the visit's (one charge, its tier from every car on it).
        # A visit where a car was already done went ahead: no charge.
        ends_visit = not any(b.get("status") == BookingStatus.COMPLETED.value for b in bookings)
        plan = await self._cancel_charge_plan(
            live or bookings, payload, actor_role, only_if_unpaid=only_if_unpaid, ends_visit=ends_visit,
        )
        forfeits = self._plan_forfeits(live, payload, actor_role, only_if_unpaid=only_if_unpaid)
        by_customer = (actor_role == "customer" or bool(payload.at_customer_request and actor_role in ("manager", "admin"))) and not only_if_unpaid
        actor = await self.charges.actor(actor_id, actor_role)

        async def _do_cancel(session):
            await self._touch_customer(lead_row["customer_id"], session)
            if actor_role == "customer" and not only_if_unpaid:
                rows = await self._hold_visit_for_customer_cancel(lead_row, session)
            else:
                rows = await self._visit_rows(lead_row, session=session)
            await self._backfill_seats(rows, session)
            if only_if_unpaid:
                paying_open = [c for c in rows if float(c.get("total_amount") or 0) > 0 and self._still_unpaid(c)]
                standing = [c for c in rows if float(c.get("total_amount") or 0) > 0 and not self._still_unpaid(c)]
                free_parked = [
                    c for c in rows if float(c.get("total_amount") or 0) <= 0 and c.get("status") == BookingStatus.AWAITING_PAYMENT.value
                ]
                targets = paying_open + ([] if standing else free_parked)
            else:
                targets = [c for c in rows if c.get("status") not in {BookingStatus.CANCELLED.value, BookingStatus.COMPLETED.value}]
                for car in targets:
                    seen = validated.get(str(car["_id"]))
                    if seen is None or seen["status"] != car["status"]:
                        raise BadRequestException("This visit just changed — refresh and try again.")
            done: list[tuple[dict, dict]] = []
            for car in sorted(targets, key=lambda c: int(c.get("group_offset_minutes") or 0)):
                guard = {"status": car["status"], "scheduled_date": car["scheduled_date"], "scheduled_slot": car["scheduled_slot"]}
                if only_if_unpaid:
                    guard.update(self._UNPAID_GUARD)
                result = await self.repo.update_if(
                    str(car["_id"]), guard,
                    self._cancel_fields(car, payload.reason, actor_role, forfeit=str(car["_id"]) in forfeits, by_customer=by_customer),
                    session=session,
                )
                if result is None:
                    if only_if_unpaid:
                        continue
                    raise BadRequestException("This visit just changed — refresh and try again.")
                done.append((car, result))
            # Hand seats back only after every car's new state is written, so
            # the seat never "moves" to a car this same cancel is closing.
            for _car, result in done:
                await self._release_seat(result, session)
                await self.charges.release_for_booking(session, result, actor=actor, reason="cancelled")
            charge = None
            if plan and done and not any(
                c.get("status") != BookingStatus.CANCELLED.value and str(c["_id"]) not in {str(x["_id"]) for x, _r in done} for c in rows
            ):
                charge = await self.charges.record_late_cancellation(
                    session, cars=[car for car, _r in done], visit_key=booking_group_id,
                    tier=plan["tier"], amount=plan["charge"], original_amount=plan["amount"], actor=actor,
                )
            money = None
            if done:
                money = await self._cancel_money(
                    session, [r for _c, r in done], charge,
                    plan_ids=[str(c["_id"]) for c, _r in done if c.get("subscription_id")], actor=actor, reason=payload.reason,
                )
            return done, bool(only_if_unpaid and [c for c in rows if c.get("status") == BookingStatus.AWAITING_PAYMENT.value]), charge, money

        async with await self.db.client.start_session() as session:
            done, had_parked, charge, money = await session.with_transaction(_do_cancel)
        if not done:
            if only_if_unpaid:
                return {"booking_group_id": booking_group_id, "cancelled_count": 0}
            raise BadRequestException("This visit can no longer be cancelled.")
        if charge:
            await self._post_commit("charge audit", lead_row, self.charges.audit_created(charge, actor))

        for before, after in done:
            await self._after_cancel(before, after, payload.reason, actor_id)
            await self._broadcast_booking_changed(after)
        cancelled = [before for before, _after in done]
        await self._post_commit("cancel audit", lead_row, self._audit_cancel(
            actor, "CANCEL_BOOKING_GROUP", booking_group_id, cancelled[0], payload, charge=charge, money=money,
            forfeited=sorted(forfeits), extra={"vehicles": len(done)},
        ))
        if only_if_unpaid and had_parked:
            # Its unpaid cars are gone; a ₹0 plan car that waited on them
            # rides with the car that DID pay.
            await self._confirm_parked_cars_of(lead_row)
        lead = cancelled[0]
        await self._post_commit("cancel messages", lead, self._announce_cancel(
            cancelled, lead, payload.reason, actor_role, by_customer, charge, money, forfeits & {str(c["_id"]) for c in cancelled},
        ))
        await self._broadcast_slots_changed(lead["service_center_id"], self._seat_key_of(lead)["date"])
        return {
            "booking_group_id": booking_group_id, "cancelled_count": len(done),
            "late_cancellation_charge": self._charge_summary(charge),
            "wallet": self._wallet_summary(money),
            "plan_wash_forfeited": len(forfeits & {str(c["_id"]) for c in cancelled}),
        }

    async def assign_captain_to_group(
        self, booking_group_id: str, payload: BookingAssignCaptainRequest, assigned_by: str, actor_role: str, actor_center_id: str | None
    ) -> dict:
        """One captain takes the whole visit. Cars on a visit are worked back
        to back at one address, so splitting them between two captains would
        mean two people driving to the same gate — the manager assigns the
        visit, not the cars.

        This is also the ONE place that HEALS a split visit — a car already
        ASSIGNED to a different captain than the one chosen here (state that
        should never arise, but a manager acting on a single row of a flat
        booking table could: "Assign captain" on car 2 used to only ever
        look at PENDING cars, so it happily left car 1's different captain
        untouched) is swapped onto this captain too, via the same guarded
        reassign path. A car already past ASSIGNED (on the way, mid-service,
        done, cancelled) is left alone — real work is underway there and
        swapping its captain now would orphan it.

        Deliberately a LOOP over the ordinary assign_captain/reassign_captain
        rather than a new transactional path: those already carry the
        conflict check, the double-booking race guard and the notification,
        and the conflict check already knows that cars sharing a group can't
        clash with each other. Each car keeps its own staggered start, and
        exactly one notification pair goes out for the whole trip regardless
        of how many cars actually moved."""
        bookings = await self.repo.collection.find(
            {"booking_group_id": booking_group_id, "is_deleted": {"$ne": True}}
        ).sort("group_offset_minutes", 1).to_list(length=20)
        if not bookings:
            raise NotFoundException("Booking not found")

        fresh = [b for b in bookings if b.get("status") in {BookingStatus.PENDING.value, BookingStatus.RESCHEDULED.value}]
        mismatched = [
            b for b in bookings
            if b.get("status") == BookingStatus.ASSIGNED.value and b.get("captain_id") != payload.captain_id
        ]
        if not fresh and not mismatched:
            raise BadRequestException("Every vehicle on this visit is already assigned to this captain.")

        # ALL-OR-NOTHING. Each car has its own staggered start, so a captain
        # free for car 1 can still clash with another job by car 3 — and a
        # plain loop then left the visit half-assigned (two people at one
        # gate, or one car nobody comes for). So: every car's schedule is
        # checked BEFORE anything is written; if a car still fails mid-way
        # (a race with another manager), the cars already moved are put
        # back exactly as they were; and the one "new job" announcement is
        # sent only once the WHOLE visit is on this captain.
        policy = await self.policy_service.get_policy()
        await self._precheck_group_conflicts(fresh, mismatched, payload, actor_role, actor_center_id, policy)

        assigned: list[dict] = []
        moved: list[dict] = []  # pre-write snapshots of cars this call actually moved
        try:
            for index, booking in enumerate(fresh):
                assigned.append(
                    await self.assign_captain(
                        str(booking["_id"]),
                        # Only the FIRST car may carry a manager-chosen start
                        # time; the rest follow their own offset, or the
                        # whole visit would pile onto one instant.
                        payload if index == 0 else BookingAssignCaptainRequest(captain_id=payload.captain_id),
                        assigned_by,
                        actor_role,
                        actor_center_id,
                        _group_pass=True,
                        _announce=False,
                    )
                )
                moved.append(booking)
            for index, booking in enumerate(mismatched):
                assigned.append(
                    await self.reassign_captain(
                        str(booking["_id"]),
                        ReassignCaptainRequest(
                            captain_id=payload.captain_id,
                            estimated_start_at=payload.estimated_start_at if (index == 0 and not fresh) else None,
                        ),
                        assigned_by,
                        actor_role,
                        actor_center_id,
                        _group_pass=True,
                        _announce=False,
                    )
                )
                moved.append(booking)
        except Exception:
            await self._undo_group_assignment(moved, payload.captain_id, assigned_by)
            raise

        captain = await self.user_repo.find_by_id(payload.captain_id)
        # The customer hears who is coming on a reassignment too — they were
        # told a name before, and it just changed.
        await self._announce_captain_assigned((fresh or mismatched)[0], payload.captain_id, captain or {}, tell_customer=True)
        return {"booking_group_id": booking_group_id, "assigned_count": len(assigned), "bookings": assigned}

    async def _precheck_group_conflicts(
        self, fresh: list[dict], mismatched: list[dict], payload: BookingAssignCaptainRequest,
        actor_role: str, actor_center_id: str | None, policy: dict,
    ) -> None:
        """Read-only dry run of every car's schedule check (the only check
        that differs car to car — each starts at its own offset) so a
        clash on car 3 refuses the visit before car 1 is touched."""
        for index, booking in enumerate([*fresh, *mismatched]):
            ensure_own_center(actor_role, actor_center_id, booking["service_center_id"])
            requested = payload.estimated_start_at if index == 0 else None
            start = _resolve_estimated_start(booking, requested)
            end = start + timedelta(minutes=booking.get("duration_minutes", 60))
            conflict = await self._captain_conflict(
                payload.captain_id, start, end, str(booking["_id"]), policy, group_id=booking.get("booking_group_id"),
            )
            if conflict:
                raise BadRequestException(
                    f"This captain is already scheduled for booking {conflict['booking_number']} around this time "
                    f"(including travel buffer) — pick a different captain or time. No car on this visit was assigned."
                )

    _ASSIGNMENT_FIELDS = (
        "captain_id", "status", "estimated_start_at", "assigned_at", "previous_captain_ids",
        "issue_flag", "issue_resolved", "resolved_issue_flag", "self_assigned",
    )

    async def _undo_group_assignment(self, moved: list[dict], captain_id: str, actor_id: str) -> None:
        """Puts cars this call already moved back exactly as they were —
        only while they still carry THIS assignment (never clobbering a
        change someone else made since). Best effort per car."""
        for snapshot in moved:
            booking_id = str(snapshot["_id"])
            restore = {f: snapshot[f] for f in self._ASSIGNMENT_FIELDS if f in snapshot}
            unset = {f: "" for f in self._ASSIGNMENT_FIELDS if f not in snapshot}
            update: dict = {"$set": restore}
            if unset:
                update["$unset"] = unset
            try:
                res = await self.repo.collection.update_one(
                    {"_id": snapshot["_id"], "captain_id": captain_id, "status": BookingStatus.ASSIGNED.value}, update,
                )
                if res.modified_count:
                    await self._record_history(
                        booking_id, BookingStatus(snapshot["status"]), actor_id,
                        "Assignment undone — the rest of this visit couldn't be given to the same captain",
                    )
                    restored = await self.repo.find_by_id(booking_id)
                    if restored:
                        await self._broadcast_booking_changed(restored)
            except Exception:  # noqa: BLE001
                logger.exception("Could not undo the half-done group assignment of booking %s", booking_id)

    async def _announce_captain_assigned(self, booking: dict, captain_id: str, captain: dict, *, tell_customer: bool) -> None:
        """One "new job" for the trip, labelled as the trip — the captain
        (and, for a first assignment, the customer) hear it once however
        many cars are on it."""
        cars = await self._visit_cars(booking)
        label = await self._visit_label(cars)
        reference = self._visit_numbers(cars) if len(cars) > 1 else booking["booking_number"]
        booking_id = str(booking["_id"])
        await self.notifications.notify(
            captain_id,
            f"New job — {label}",
            f"{format_slot_12h(booking.get('scheduled_slot'))} on {to_ist(booking['scheduled_date']).strftime('%d %b')} ({reference})",
            NotificationType.BOOKING,
            booking_id,
        )
        if tell_customer:
            wa_services, wa_reference, wa_date, wa_slot, wa_vehicle, wa_code = await self._wa_details(booking, cars)
            await self.notifications.notify(
                booking["customer_id"], "Captain assigned", "A captain has been assigned to your booking.",
                NotificationType.BOOKING, booking_id,
                wa_event="captain_assigned",
                wa_params=[captain.get("full_name", "Your captain"), wa_services, wa_reference, wa_date, wa_slot, wa_vehicle, wa_code],
            )

    async def assign_captain(
        self,
        booking_id: str,
        payload: BookingAssignCaptainRequest,
        assigned_by: str,
        actor_role: str,
        actor_center_id: str | None,
        _group_pass: bool = False,
        _announce: bool = True,
    ) -> dict:
        booking = await self.repo.find_by_id(booking_id)
        if booking and booking.get("booking_group_id") and not _group_pass:
            # Assigning one car of a visit assigns the visit — a manager
            # clicking the single-booking endpoint on a car must not send a
            # captain out for half a job.
            result = await self.assign_captain_to_group(booking["booking_group_id"], payload, assigned_by, actor_role, actor_center_id)
            mine = next((b for b in result["bookings"] if b.get("id") == booking_id), None)
            return mine or serialize_doc(await self.repo.find_by_id(booking_id))
        if not booking:
            raise NotFoundException("Booking not found")
        ensure_own_center(actor_role, actor_center_id, booking["service_center_id"])
        if booking["status"] not in {BookingStatus.PENDING.value, BookingStatus.RESCHEDULED.value}:
            raise BadRequestException("Only pending bookings can be assigned to a captain")
        _ensure_transition_allowed(booking["status"], BookingStatus.ASSIGNED.value)

        captain = await self.user_repo.find_by_id(payload.captain_id)
        if not captain or captain["role"] != "captain":
            raise NotFoundException("Captain not found")
        if captain.get("status") != "active":
            raise BadRequestException("This captain's account isn't active and can't be assigned new bookings.")
        # A booking is dispatched to ONE center; its captain must come from
        # that same center — for admins too, since a cross-center assignment
        # sends a captain to a job their own manager can't see and settles
        # cash against the wrong store's books.
        if captain.get("service_center_id") != booking.get("service_center_id"):
            raise BadRequestException("This captain belongs to a different service center than the booking.")
        await self._ensure_captain_not_on_leave(payload.captain_id, booking)

        policy = await self.policy_service.get_policy()
        _ensure_schedulable(booking, policy)
        if not await self.wallet_service.is_eligible_for_assignment(payload.captain_id, policy):
            raise BadRequestException(
                "This captain's wallet balance is below the required minimum and cannot be assigned new bookings "
                "until they top up."
            )

        # The conflict check + write happen atomically inside one transaction —
        # otherwise two concurrent assignment requests for the same captain
        # (two managers acting near-simultaneously, or a retried request
        # racing the original) can both read "no conflict" before either
        # commits, double-booking the captain into overlapping jobs. Also
        # re-checks the booking's own status inside the transaction, in case
        # someone else assigned it in the gap between the read above and here.
        async def _do_assign(session):
            # A transaction alone does NOT serialize this against a
            # concurrent assignment of the same captain onto a DIFFERENT
            # booking — MongoDB only detects write-write conflicts on the
            # SAME document, and the two competing requests write to two
            # different booking documents, so nothing would otherwise force
            # them to run one-after-another (confirmed by testing: without
            # this, two concurrent assign-captain calls for overlapping
            # bookings both succeeded). Writing to the captain's own user
            # document first — a document every concurrent assignment of
            # THIS captain necessarily touches — gives MongoDB something to
            # actually conflict on, so the loser gets a real
            # TransientTransactionError and with_transaction retries it
            # with a fresh snapshot that now sees the winner's committed
            # assignment.
            await self.user_repo.update_by_id(payload.captain_id, {}, session=session)
            await self._ensure_captain_assignable(payload.captain_id, booking["service_center_id"], session)
            fresh = await self.repo.find_by_id(booking_id, session=session)
            if not fresh or fresh["status"] not in {BookingStatus.PENDING.value, BookingStatus.RESCHEDULED.value}:
                raise BadRequestException("This booking is no longer available to assign — someone else may have just assigned it.")
            estimated_start_at = _resolve_estimated_start(fresh, payload.estimated_start_at)
            duration = fresh.get("duration_minutes", 60)
            conflict = await self._captain_conflict(
                payload.captain_id, estimated_start_at, estimated_start_at + timedelta(minutes=duration), None, policy,
                session=session, group_id=fresh.get("booking_group_id"),
            )
            if conflict:
                raise BadRequestException(
                    f"This captain is already scheduled for booking {conflict['booking_number']} around this time "
                    f"(including travel buffer) — pick a different captain or time."
                )
            await self.repo.update_by_id(
                booking_id,
                {
                    "captain_id": payload.captain_id,
                    "status": BookingStatus.ASSIGNED.value,
                    "estimated_start_at": estimated_start_at,
                    "assigned_at": now_ist(),
                },
                session=session,
            )

        async with await self.db.client.start_session() as session:
            await session.with_transaction(_do_assign)

        updated = await self.repo.find_by_id(booking_id)
        await self._record_history(booking_id, BookingStatus.ASSIGNED, assigned_by, f"Assigned to captain {captain['full_name']}")
        if _announce:
            await self._announce_captain_assigned(booking, payload.captain_id, captain, tell_customer=True)
        await self._broadcast_booking_changed(updated)
        return serialize_doc(updated)

    async def _ensure_captain_assignable(self, captain_id: str, center_id: str | None, session) -> None:
        """Re-read INSIDE the assignment transaction, after the captain-row
        write that serializes it: still a captain, still active, still at
        the booking's center. A suspend / move of the captain (which writes
        the same row first, in its own transaction, and refuses while he
        has live jobs) can no longer slip between the checks above and this
        write."""
        captain = await self.user_repo.find_by_id(captain_id, session=session)
        if not captain or captain.get("role") != "captain":
            raise NotFoundException("Captain not found")
        if captain.get("status") != UserStatus.ACTIVE.value:
            raise BadRequestException("This captain's account isn't active and can't be assigned new bookings.")
        if not center_id or captain.get("service_center_id") != center_id:
            raise BadRequestException("This captain belongs to a different service center than the booking.")

    async def reassign_captain(
        self,
        booking_id: str,
        payload: ReassignCaptainRequest,
        actor_id: str,
        actor_role: str,
        actor_center_id: str | None,
        _group_pass: bool = False,
        _announce: bool = True,
    ) -> dict:
        booking = await self.repo.find_by_id(booking_id)
        if not booking:
            raise NotFoundException("Booking not found")
        ensure_own_center(actor_role, actor_center_id, booking["service_center_id"])
        if booking["status"] not in {BookingStatus.PENDING.value, BookingStatus.ASSIGNED.value}:
            raise BadRequestException("This booking cannot be reassigned in its current state")
        _ensure_transition_allowed(booking["status"], BookingStatus.ASSIGNED.value)
        if booking.get("booking_group_id") and not _group_pass:
            # One trip, one captain: reassigning a car of a visit moves the
            # whole visit — through the group path, so it's all-or-nothing
            # (a clash on car 3 used to leave cars 1-2 with the new captain
            # and car 3 with the old one).
            result = await self.assign_captain_to_group(
                booking["booking_group_id"],
                BookingAssignCaptainRequest(captain_id=payload.captain_id, estimated_start_at=payload.estimated_start_at),
                actor_id, actor_role, actor_center_id,
            )
            mine = next((b for b in result["bookings"] if b.get("id") == booking_id), None)
            return mine or serialize_doc(await self.repo.find_by_id(booking_id))

        policy = await self.policy_service.get_policy()
        _ensure_schedulable(booking, policy)
        if not await self.wallet_service.is_eligible_for_assignment(payload.captain_id, policy):
            raise BadRequestException("This captain's wallet balance is below the required minimum")

        captain = await self.user_repo.find_by_id(payload.captain_id)
        if not captain or captain["role"] != "captain":
            raise NotFoundException("Captain not found")
        if captain.get("status") != "active":
            raise BadRequestException("This captain's account isn't active and can't be assigned new bookings.")
        # Same center rule as assign_captain — see the comment there.
        if captain.get("service_center_id") != booking.get("service_center_id"):
            raise BadRequestException("This captain belongs to a different service center than the booking.")
        await self._ensure_captain_not_on_leave(payload.captain_id, booking)

        outgoing_captain_id = booking.get("captain_id")
        previous = list(booking.get("previous_captain_ids", []))
        if outgoing_captain_id and outgoing_captain_id not in previous:
            previous.append(outgoing_captain_id)

        # Same reasoning as assign_captain — the conflict check + write must
        # be one atomic transaction, not a separate check then a separate
        # write, or two concurrent reassignments can double-book the captain.
        async def _do_reassign(session):
            # Same lock-via-write reasoning as _do_assign above.
            await self.user_repo.update_by_id(payload.captain_id, {}, session=session)
            await self._ensure_captain_assignable(payload.captain_id, booking["service_center_id"], session)
            fresh = await self.repo.find_by_id(booking_id, session=session)
            if not fresh or fresh["status"] not in {BookingStatus.PENDING.value, BookingStatus.ASSIGNED.value}:
                raise BadRequestException("This booking can no longer be reassigned — its state just changed.")
            estimated_start_at = _resolve_estimated_start(fresh, payload.estimated_start_at)
            duration = fresh.get("duration_minutes", 60)
            conflict = await self._captain_conflict(
                payload.captain_id,
                estimated_start_at,
                estimated_start_at + timedelta(minutes=duration),
                booking_id,
                policy,
                session=session,
                group_id=fresh.get("booking_group_id"),
            )
            if conflict:
                raise BadRequestException(
                    f"This captain is already scheduled for booking {conflict['booking_number']} around this time "
                    f"(including travel buffer) — pick a different captain or time."
                )
            await self.repo.update_by_id(
                booking_id,
                {
                    "captain_id": payload.captain_id,
                    "status": BookingStatus.ASSIGNED.value,
                    "estimated_start_at": estimated_start_at,
                    "assigned_at": now_ist(),
                    "previous_captain_ids": previous,
                    "issue_flag": None,
                    "issue_resolved": True,
                    # A fresh captain's own lateness is a new situation, not
                    # a repeat of whatever the PREVIOUS captain's was — an
                    # acknowledged issue must not silently suppress it.
                    "resolved_issue_flag": None,
                    # Handing a self-assigned booking to a REAL captain
                    # (reassign_captain only ever accepts role="captain",
                    # see the check above) means it now DOES follow the
                    # normal heading-out/verify flow — resume the nudge.
                    "self_assigned": False,
                },
                session=session,
            )

        async with await self.db.client.start_session() as session:
            await session.with_transaction(_do_reassign)

        updated = await self.repo.find_by_id(booking_id)
        await self._record_history(booking_id, BookingStatus.ASSIGNED, actor_id, f"Reassigned to captain {captain['full_name']}")
        # (A car of a multi-car visit never gets here un-grouped — see the
        # delegation to assign_captain_to_group above.)
        if _announce:
            # The customer was told who was coming — tell them who is now.
            await self._announce_captain_assigned(booking, payload.captain_id, captain, tell_customer=True)
        # Whoever is actually displaced from THIS car deserves to know,
        # independent of whether this call also carries the visit's one
        # "New job" notice — a captain who loses a booking to a visit-wide
        # convergence (assign_captain_to_group healing a split visit) must
        # not be silently dropped just because a sibling car's assignment
        # already fired the shared announcement.
        if outgoing_captain_id and outgoing_captain_id != payload.captain_id:
            cars = await self._visit_cars(booking)
            reference = self._visit_numbers(cars) if len(cars) > 1 else booking["booking_number"]
            await self.notifications.notify(
                outgoing_captain_id,
                "Booking reassigned",
                f"{reference} has been reassigned to another captain.",
                NotificationType.BOOKING,
                booking_id,
            )
        await self._broadcast_booking_changed(updated)
        if outgoing_captain_id:
            # The outgoing captain no longer appears in `updated`, so the
            # general broadcast above wouldn't reach them — their own jobs
            # list still needs to know this one is gone.
            await ws_manager.broadcast(f"user:{outgoing_captain_id}", {"type": "changed", "channel": f"user:{outgoing_captain_id}", "booking_id": booking_id})
        return serialize_doc(updated)

    async def self_assign(self, booking_id: str, actor_id: str, actor_role: str, actor_center_id: str | None) -> dict:
        """A manager assigning a booking to THEMSELVES — for delivering it
        personally when needed (short-staffed, a captain fell through,
        urgent). Deliberately bypasses every captain-specific check (role,
        active status, wallet eligibility, KYC, schedule-conflict
        detection, same-center-as-captain) — none of that machinery is
        about the MANAGER, who isn't paid through the captain wallet at
        all. manager_mark_done already unconditionally zeroes
        captain_earning/captain_id when a manager closes a job out, exactly
        because it was written to not care whether a captain was ever
        really involved — self-assigning into that same field is safe for
        the identical reason. A visit is claimed as a whole; a car already
        past ASSIGNED (a captain is actually on the way or working) is left
        alone rather than yanked out from under them."""
        booking = await self.repo.find_by_id(booking_id)
        if not booking:
            raise NotFoundException("Booking not found")
        ensure_own_center(actor_role, actor_center_id, booking["service_center_id"])
        cars = await self._visit_cars(booking)
        policy = await self.policy_service.get_policy()
        claimed: list[dict] = []
        for car in cars:
            if car["status"] not in {BookingStatus.PENDING.value, BookingStatus.RESCHEDULED.value, BookingStatus.ASSIGNED.value}:
                continue
            _ensure_transition_allowed(car["status"], BookingStatus.ASSIGNED.value)
            _ensure_schedulable(car, policy)
            car_id = str(car["_id"])
            outgoing = car.get("captain_id")
            previous = list(car.get("previous_captain_ids", []))
            if outgoing and outgoing not in previous:
                previous.append(outgoing)
            # Guarded on the status and captain read above: a customer cancel
            # or a captain heading out in between must win, not be overwritten
            # (that resurrected a cancelled booking as ASSIGNED, and yanked a
            # job from a captain already on the road). And on the slot read:
            # the start time below is computed from it, and a reschedule
            # landing in between (RESCHEDULED -> RESCHEDULED keeps the status)
            # anchored the job to the OLD slot (audit STATE-01).
            seat_as_read = {"scheduled_date": car["scheduled_date"], "scheduled_slot": car["scheduled_slot"]}
            updated = await self.repo.update_if(car_id, {"status": car["status"], "captain_id": outgoing, **seat_as_read}, {
                "captain_id": actor_id,
                "status": BookingStatus.ASSIGNED.value,
                # The booking's own slot start, exactly like assign_captain
                # computes it (_resolve_estimated_start) — NOT now_ist().
                # Hardcoding "now" here meant self-assigning a booking days
                # in the future immediately anchored its lateness check to
                # the moment of assignment, so find_bookings_late_to_start
                # flagged it "hasn't started — past scheduled time" within
                # the very next sweep tick, sometimes days before the
                # actual slot. A real bug, not a stale-data artifact.
                "estimated_start_at": _resolve_estimated_start(car, None),
                "assigned_at": now_ist(),
                # A manager who takes a job personally never goes through
                # the captain heading-out/vehicle-verify flow at all (those
                # endpoints are require_captain-gated, unreachable to a
                # manager even for their own booking) — this flag lets
                # find_bookings_late_to_start exempt them from the "captain
                # hasn't started heading out" nudge, which assumes exactly
                # that flow and makes no sense for a self-delivered job.
                "self_assigned": True,
                "previous_captain_ids": previous,
                "issue_flag": None,
                "issue_resolved": True,
                # A manager taking the job over is a fresh start, same as
                # reassign_captain/reschedule_booking — nothing carried
                # over from a previous captain's history should linger
                # (moot today since self_assigned bookings are exempt from
                # the sweep that reads this, but kept consistent in case
                # that ever changes).
                "resolved_issue_flag": None,
            })
            if updated is None:
                continue
            await self._record_history(car_id, BookingStatus.ASSIGNED, actor_id, "Self-assigned by the manager")
            await self._broadcast_booking_changed(updated)
            if outgoing and outgoing != actor_id:
                await self.notifications.notify(
                    outgoing, "Booking reassigned",
                    f"{car['booking_number']} has been reassigned — the manager is delivering it personally.",
                    NotificationType.BOOKING, car_id, send_whatsapp=False,
                )
                await ws_manager.broadcast(f"user:{outgoing}", {"type": "changed", "channel": f"user:{outgoing}", "booking_id": car_id})
            claimed.append(updated)
        if not claimed:
            raise BadRequestException("This booking can't be self-assigned in its current state — a captain may already be on the way.")

        # Same WhatsApp template assign_captain's "captain assigned" event
        # uses (unchanged — it already puts the manager's name in param 1),
        # but the in-app title/message names the manager directly instead
        # of the generic captain-assigned wording, which reads oddly when
        # there's no captain at all, just the manager handling it himself.
        manager = await self.user_repo.find_by_id(actor_id)
        manager_name = (manager or {}).get("full_name") or "Our manager"
        wa_services, wa_reference, wa_date, wa_slot, wa_vehicle, wa_code = await self._wa_details(booking, cars)
        await self.notifications.notify(
            booking["customer_id"], "Booking assigned", f"{manager_name} will personally handle your booking.",
            NotificationType.BOOKING, booking_id,
            wa_event="captain_assigned",
            wa_params=[manager_name, wa_services, wa_reference, wa_date, wa_slot, wa_vehicle, wa_code],
        )
        return {"claimed": len(claimed), "booking_numbers": [c.get("booking_number") for c in claimed]}

    async def captain_cancel(self, booking_id: str, payload: CaptainCancelRequest, captain_id: str) -> dict:
        booking = await self.repo.find_by_id(booking_id)
        if not booking:
            raise NotFoundException("Booking not found")
        await self._ensure_captain_job(booking, captain_id)
        if booking["status"] not in {BookingStatus.ASSIGNED.value, BookingStatus.CAPTAIN_ON_THE_WAY.value}:
            raise BadRequestException("This booking can no longer be released")
        _ensure_transition_allowed(booking["status"], BookingStatus.PENDING.value)

        # Releasing one car of a visit releases the visit: the captain is
        # walking away from the trip, and a half-released visit would leave
        # the other cars assigned to someone who isn't coming.
        releasable = {BookingStatus.ASSIGNED.value, BookingStatus.CAPTAIN_ON_THE_WAY.value}
        to_release = [booking] + [
            s for s in await self._visit_siblings(booking) if s.get("status") in releasable and s.get("captain_id") == captain_id
        ]
        updated = None
        for car in to_release:
            previous = list(car.get("previous_captain_ids", []))
            if captain_id not in previous:
                previous.append(captain_id)
            # The next captain starts clean, exactly as after a reschedule:
            # he re-checks the code on arrival (a carried-over
            # vehicle_verified let him skip it), gets his own start verdict,
            # and is paid in full — this captain's late-start penalty left
            # with this captain.
            release = {
                "captain_id": None, "status": BookingStatus.PENDING.value, "previous_captain_ids": previous,
                "heading_at": None, "heading_location": None,
                "vehicle_verified": False, "vehicle_verified_at": None,
                "arrival_location": None, "arrival_flagged": False, "arrival_distance_m": None,
                "captain_start_stage": None, "late_penalty_pct": 0, "late_penalty_amount": 0,
                "issue_flag": None, "issue_resolved": True, "resolved_issue_flag": None,
            }
            penalty = float(car.get("late_penalty_amount") or 0)
            if penalty:
                release["captain_service_pay"] = round(float(car.get("captain_service_pay") or 0) + penalty, 2)
                release["captain_earning"] = round(float(car.get("captain_earning") or 0) + penalty, 2)
                release["platform_earning"] = round(float(car.get("platform_earning") or 0) - penalty, 2)
            # Guarded on the exact status read: a manager may close this job
            # (mark done) in the same instant — never drag a completed
            # booking back to pending — and the pay restored above is the
            # pay of exactly that state.
            result = await self.repo.update_if(
                str(car["_id"]),
                {"status": car["status"], "captain_id": captain_id},
                release,
            )
            if result is None:
                if str(car["_id"]) == booking_id:
                    raise BadRequestException("This booking can no longer be released")
                continue
            await self._record_history(str(car["_id"]), BookingStatus.PENDING, captain_id, f"Captain released the booking: {payload.reason}")
            if str(car["_id"]) == booking_id:
                updated = result
            else:
                await self._broadcast_booking_changed(result)

        reference = self._visit_numbers(to_release) if len(to_release) > 1 else booking["booking_number"]
        center = await self.center_repo.find_by_id(booking["service_center_id"])
        if center and center.get("manager_id"):
            await self.notifications.notify(
                center["manager_id"],
                "Captain unavailable — reassignment needed",
                f"{reference} needs a new captain: {payload.reason}",
                NotificationType.BOOKING,
                booking_id,
            )
        # The customer had already been told a captain was assigned/on the
        # way — leaving them with no signal that changed until (or unless) a
        # new captain gets assigned is a silent, confusing gap.
        wa_services, wa_reference, wa_date, wa_slot, wa_vehicle, _wa_code = await self._wa_details(booking, to_release)
        await self.notifications.notify(
            booking["customer_id"],
            "Finding you a new captain",
            f"Your captain for {reference} is no longer available — we're assigning a replacement.",
            NotificationType.BOOKING,
            booking_id,
            wa_event="captain_released",
            wa_params=[wa_services, wa_reference, wa_date, wa_slot, wa_vehicle],
        )
        await self._broadcast_booking_changed(updated)
        await ws_manager.broadcast(f"user:{captain_id}", {"type": "changed", "channel": f"user:{captain_id}", "booking_id": booking_id})
        return serialize_doc(updated)

    async def start_heading(self, booking_id: str, payload: HeadingRequest, captain_id: str) -> dict:
        booking = await self.repo.find_by_id(booking_id)
        if not booking:
            raise NotFoundException("Booking not found")
        await self._ensure_captain_job(booking, captain_id)
        # "captain_not_started" exists specifically to prompt exactly this
        # action — it must not block the captain from taking it.
        _ensure_no_open_issue(booking, exempt_flags=frozenset({"captain_not_started"}))
        if booking["status"] != BookingStatus.ASSIGNED.value:
            raise BadRequestException("This booking is not ready to start heading out")
        _ensure_transition_allowed(booking["status"], BookingStatus.CAPTAIN_ON_THE_WAY.value)

        policy = await self.policy_service.get_policy()
        # On a multi-car visit the captain leaves ONCE, for the first car —
        # so the clock (too early? late?) is the visit's clock: anchored on
        # the first car's start and running for every car's duration.
        # Whichever car he tapped, he's heading out for all of them.
        visit = await self._visit_cars(booking)
        anchor = visit[0] if len(visit) > 1 else booking
        duration = sum(int(c.get("duration_minutes") or 60) for c in visit) if len(visit) > 1 else booking.get("duration_minutes", 60)
        # Anchored at the booking's own estimated_start_at (set at
        # assignment — see _resolve_estimated_start), not the coarse
        # customer-facing admin slot (e.g. 09:00-12:00) — several bookings
        # can share one slot for the same captain, each with its own
        # estimated_start_at, so lateness has to be measured per-job, not
        # against the whole shared window. Falls back to the legacy
        # exact-time derivation for a booking assigned before this field
        # existed.
        raw_start = from_stored(anchor["estimated_start_at"]) if anchor.get("estimated_start_at") else _slot_start_datetime(anchor["scheduled_date"], anchor["scheduled_slot"])
        # Last-minute assignment: lateness (stages, penalties, lockout) is
        # measured from _effective_start_anchor — a captain assigned at
        # 6:35 into a 4-7 slot has 15 clean minutes to head out; only past
        # that does the late/penalty machinery engage. The "too early"
        # check below deliberately keeps the RAW anchor: heading out ahead
        # of the real slot is unaffected by when assignment happened.
        slot_start = _effective_start_anchor(anchor, raw_start, policy)
        slot_end = slot_start + timedelta(minutes=duration)
        window_start = raw_start - timedelta(minutes=START_WINDOW_MINUTES)
        late_grace_minutes = policy.get("late_start_grace_minutes", 30)
        window_end = slot_end + timedelta(minutes=late_grace_minutes)
        now = now_ist()

        if now < window_start:
            minutes_to_wait = int((window_start - now).total_seconds() // 60)
            raise BadRequestException(
                f"Too early to start this booking. You can begin heading out {START_WINDOW_MINUTES} minutes before "
                f"the scheduled time (in about {minutes_to_wait} more minutes)."
            )

        # A captain must NOT be able to just "start heading" on a booking
        # whose window expired so long ago it's effectively abandoned — a
        # penalty label alone (below) isn't a real safeguard for something
        # that's been sitting for hours or days; the customer may no longer
        # even be expecting it that day. Past this point the booking needs
        # an actual manager decision (reschedule to a real new time, or
        # reassign/cancel) before anyone can act on it again — see
        # find_bookings_late_to_start/flag_late_to_start for the matching
        # sweep-side flag that surfaces this in the manager queue.
        lockout_hours = policy.get("captain_start_lockout_hours", 4)
        lockout_at = window_end + timedelta(hours=lockout_hours)
        if now > lockout_at:
            raise BadRequestException(
                "This booking's scheduled window expired too long ago to start now — a manager needs to reschedule "
                "or reassign it first."
            )

        # Which stage the captain is starting in, and what it costs them —
        # e.g. an 11:30 slot needing 40 minutes runs 11:00–11:30 (early/on-time,
        # no penalty), 11:30–12:10 (on-time window, no penalty), 12:10–12:40
        # (late grace, partial penalty + manager notified), beyond 12:40
        # (severely late, bigger penalty + manager notified more urgently).
        if now < slot_start:
            stage, penalty_pct = "early", 0.0
        elif now < slot_end:
            stage, penalty_pct = "on_time", 0.0
        elif now < window_end:
            stage, penalty_pct = "late", LATE_START_PENALTY_PCT
        else:
            stage, penalty_pct = "severely_late", SEVERE_LATE_START_PENALTY_PCT

        update_data: dict = {
            "status": BookingStatus.CAPTAIN_ON_THE_WAY.value,
            "heading_at": now,
            "heading_location": {"latitude": payload.latitude, "longitude": payload.longitude},
            "equipment_used": [item.model_dump() for item in payload.equipment_used],
            "captain_start_stage": stage,
            "late_penalty_pct": penalty_pct,
            # What the penalty actually took (below) — captain_cancel gives it
            # back if this captain releases the job.
            "late_penalty_amount": 0.0,
            # Starting heading out is the resolution for a "captain hasn't
            # started yet" flag — clear it by default. Overwritten below if
            # this start is itself late enough to earn its own captain_delay
            # flag instead. Without this explicit reset, update_by_id only
            # touches the keys given here, so a prior flag would otherwise
            # sit there unresolved forever even though the problem it named
            # is now gone.
            "issue_flag": None,
            "issue_resolved": True,
        }

        if penalty_pct > 0:
            service_pay = booking.get("captain_service_pay") or 0
            current_earning = booking.get("captain_earning") or 0
            # The penalty comes OUT OF the stored earning and the platform
            # gains exactly what the captain loses — conservation is the
            # invariant. Recomputing from travel_pay + service_pay (the old
            # code) broke on low-priced jobs where the split had CLAMPED
            # earning below the sum of its parts: a "penalized" captain
            # could end up paid MORE than before, on a job worth less.
            penalty_amount = min(round(service_pay * penalty_pct / 100, 2), current_earning)
            update_data["captain_service_pay"] = round(max(0.0, service_pay - penalty_amount), 2)
            update_data["captain_earning"] = round(current_earning - penalty_amount, 2)
            update_data["platform_earning"] = round((booking.get("platform_earning") or 0) + penalty_amount, 2)
            update_data["late_penalty_amount"] = penalty_amount
            # Distinct from "captain_delay" (the stuck-on-the-way sweep,
            # main.py) — this is specifically "started, but late", not
            # "hasn't reached the customer after an unusually long time".
            update_data["issue_flag"] = "captain_late_start"
            update_data["issue_notes"] = (
                f"Captain started {'late' if stage == 'late' else 'very late'} — "
                f"{int((now - slot_start).total_seconds() // 60)} minutes past his start deadline."
            )
            update_data["issue_flagged_at"] = now
            update_data["issue_resolved"] = False

        # The equipment he's taking must be real stock of THIS booking's
        # center — checked before anything is written (audit VAL-3: a bad
        # id was a 500 AFTER the job had already moved to "on the way").
        await self._ensure_equipment_in_store(booking, payload)

        # CLAIM the transition atomically: exactly one "start heading" wins
        # (a field-connection retry used to re-run the penalty math and
        # re-deduct inventory). Everything with side effects happens only
        # after this guarded write succeeds.
        updated = await self.repo.update_if(
            booking_id,
            {"status": BookingStatus.ASSIGNED.value, "captain_id": captain_id},
            update_data,
        )
        if updated is None:
            raise BadRequestException("This booking is no longer waiting to be started — refresh your jobs list.")

        for item in payload.equipment_used:
            # Guarded single-op decrement — the old check-then-act pair let
            # two concurrent deductions drive stock negative. Only THIS
            # booking's center's store (CAP-03), only a positive quantity
            # (the schema refuses ≤ 0 too).
            if item.quantity <= 0:
                continue
            await self.inventory_repo.collection.update_one(
                {
                    "_id": ObjectId(item.inventory_item_id),
                    "service_center_id": booking["service_center_id"],
                    "is_deleted": {"$ne": True},
                    "quantity_available": {"$gte": item.quantity},
                },
                {"$inc": {"quantity_available": -item.quantity}},
            )
        await self.update_captain_location(captain_id, payload.latitude, payload.longitude, source="heading", booking_id=booking_id)
        await self._record_history(
            booking_id,
            BookingStatus.CAPTAIN_ON_THE_WAY,
            captain_id,
            f"Captain is heading to the customer ({stage.replace('_', ' ')})",
        )
        # One trip: heading out for this car IS heading out for every other
        # car on the visit. They take the same departure stamp and the same
        # lateness verdict (their own penalty amounts) — he left once.
        for sib in await self._visit_siblings(booking):
            if sib.get("status") != BookingStatus.ASSIGNED.value or sib.get("captain_id") != captain_id:
                continue
            sib_update = {
                k: v for k, v in update_data.items()
                if k not in {"captain_service_pay", "captain_earning", "platform_earning", "late_penalty_amount", "equipment_used"}
            }
            sib_update["equipment_used"] = []
            sib_update["late_penalty_amount"] = 0.0
            if penalty_pct > 0:
                sib_service_pay = sib.get("captain_service_pay") or 0
                sib_earning = sib.get("captain_earning") or 0
                sib_penalty = min(round(sib_service_pay * penalty_pct / 100, 2), sib_earning)
                sib_update["captain_service_pay"] = round(max(0.0, sib_service_pay - sib_penalty), 2)
                sib_update["captain_earning"] = round(sib_earning - sib_penalty, 2)
                sib_update["platform_earning"] = round((sib.get("platform_earning") or 0) + sib_penalty, 2)
                sib_update["late_penalty_amount"] = sib_penalty
            sib_updated = await self.repo.update_if(
                str(sib["_id"]), {"status": BookingStatus.ASSIGNED.value, "captain_id": captain_id}, sib_update
            )
            if sib_updated:
                await self._record_history(
                    str(sib["_id"]), BookingStatus.CAPTAIN_ON_THE_WAY, captain_id,
                    f"Captain is heading to the customer — same visit ({stage.replace('_', ' ')})",
                )
                await self._broadcast_booking_changed(sib_updated)
        wa_services, wa_reference, wa_date, wa_slot, wa_vehicle, wa_code = await self._wa_details(booking, visit)
        await self.notifications.notify(
            booking["customer_id"], "Captain on the way", "Your captain has left for your location.",
            NotificationType.BOOKING, booking_id, wa_event="captain_on_the_way",
            wa_params=[wa_services, wa_reference, wa_date, wa_slot, wa_vehicle, wa_code],
        )

        if penalty_pct > 0:
            center = await self.center_repo.find_by_id(booking["service_center_id"])
            if center and center.get("manager_id"):
                await self.notifications.notify(
                    center["manager_id"],
                    f"Captain started late — booking {booking['booking_number']}",
                    update_data["issue_notes"],
                    NotificationType.BOOKING,
                    booking_id,
                )
        await self._broadcast_booking_changed(updated)
        return serialize_doc(updated)

    async def _ensure_captain_job(self, booking: dict, captain_id: str) -> dict:
        """Every captain job step: he is the booking's captain AND still a
        working captain of the booking's center (audit CAP-01: one moved to
        another center, or switched off, kept working — and being paid for —
        his old center's jobs, out of its manager's sight). Center from his
        CURRENT user record, never from the token or the booking."""
        if booking.get("captain_id") != captain_id:
            raise ForbiddenException("You are not assigned to this booking")
        captain = await self.user_repo.find_by_id(captain_id)
        if not captain or captain.get("role") != "captain" or account_switched_off(captain):
            raise ForbiddenException("Your account can't work this job — contact your manager.")
        try:
            ensure_own_center("captain", captain.get("service_center_id"), booking.get("service_center_id"))
        except ForbiddenException:
            raise ForbiddenException("This job belongs to another service center — you can't work it any more. Contact your manager.")
        return captain

    async def _ensure_equipment_in_store(self, booking: dict, payload: HeadingRequest) -> None:
        if not payload.equipment_used:
            return
        ids = [ObjectId(item.inventory_item_id) for item in payload.equipment_used]
        found = {
            str(doc["_id"])
            for doc in await self.inventory_repo.collection.find(
                {"_id": {"$in": ids}, "service_center_id": booking["service_center_id"], "is_deleted": {"$ne": True}}, {"_id": 1}
            ).to_list(length=len(ids))
        }
        missing = [item.item_name for item in payload.equipment_used if item.inventory_item_id not in found]
        if missing:
            raise BadRequestException(
                f"{', '.join(missing)} isn't in your center's store — refresh the equipment list and try again."
            )

    # A completion this soon after the before-photo is flagged (CAP-02).
    QUICK_COMPLETION_MINUTES = 5

    async def _claim_step_photo(self, booking: dict, image_url: str, captain_id: str, step: str) -> bool:
        """A before/after photo must be one THIS captain uploaded (POST
        /uploads/photo records it in uploaded_photos), recently
        (PHOTO_CLAIM_MAX_AGE), never used by any job step before — so the
        after photo can never be the before photo (CAP-02: one old URL
        completed a plan job without anyone going). The claim is one atomic
        write on the upload record, so two steps racing for one file can't
        both win. Returns True when THIS call claimed it (the caller hands
        it back with _unclaim_step_photo if its own guarded write then
        loses); a retry of the very step that already claimed it is let
        through (False)."""
        from app.core.storage import PHOTO_CLAIM_MAX_AGE, PHOTO_RECORDS, claim_uploaded_photo, photo_key_for_url

        booking_id = str(booking["_id"])
        if step == "after" and (booking.get("before_photo") or {}).get("image_url") == image_url:
            raise BadRequestException("The after photo must be a new photo of the finished car — take it again.")
        claimed = await claim_uploaded_photo(
            self.db, image_url, captain_id, booking_id=booking_id, step=step, max_age=PHOTO_CLAIM_MAX_AGE,
        )
        if claimed is not None:
            return True
        key = photo_key_for_url(image_url)
        record = await self.db[PHOTO_RECORDS].find_one({"_id": key}) if key else None
        used = (record or {}).get("used_by") or {}
        if record and record.get("uploader_id") == captain_id and used.get("booking_id") == booking_id and used.get("step") == step:
            return False  # this same step, retried
        if not record or record.get("uploader_id") != captain_id:
            raise BadRequestException("Please take the photo again from the job screen — this one wasn't uploaded from your app.")
        if used:
            raise BadRequestException("This photo was already used on a job — take a new photo.")
        raise BadRequestException("This photo is too old — take a fresh photo of the car now.")

    async def _unclaim_step_photo(self, booking_id: str, image_url: str, step: str) -> None:
        """The step that claimed this photo didn't happen (its guarded write
        lost to a cancel/release): the photo is free again, so the captain
        isn't sent to take another one for nothing. Only this step's claim."""
        from app.core.storage import PHOTO_RECORDS, photo_key_for_url

        key = photo_key_for_url(image_url)
        if not key:
            return
        try:
            await self.db[PHOTO_RECORDS].update_one(
                {"_id": key, "used_by.booking_id": booking_id, "used_by.step": step}, {"$set": {"used_by": None}},
            )
        except Exception:  # noqa: BLE001 — the step's own refusal is the error to surface
            logger.exception("Could not hand back photo %s", key)

    # Wrong arrival codes allowed per visit before the check locks (CAP-02:
    # 60 wrong tries then the right one used to be accepted — 4 digits are
    # brute-forced in minutes). A manager unlocks it (unlock_arrival_code).
    ARRIVAL_CODE_MAX_ATTEMPTS = 5
    ARRIVAL_LOCKED_MESSAGE = (
        "Too many wrong codes — the arrival check is locked for this booking. Your manager has been alerted and can unlock it."
    )

    async def verify_vehicle(self, booking_id: str, payload: VerifyVehicleRequest, captain_id: str) -> dict:
        """Captain types the plate they see on arrival — compared against the
        registration number snapshotted on the booking. This is a deliberate
        typed check, not a yes/no toggle, so a captain can't rubber-stamp past
        the wrong car. Required before the before-photo can be captured.

        Wrong codes are counted per visit (on its lead car, atomically);
        the 5th locks the check, flags the job and alerts the center's
        managers. GPS stays optional, but an arrival without it is flagged
        to the manager (never blocked)."""
        booking = await self.repo.find_by_id(booking_id)
        if not booking:
            raise NotFoundException("Booking not found")
        await self._ensure_captain_job(booking, captain_id)
        visit = await self._visit_cars(booking)
        if any(c.get("arrival_code_locked") for c in visit + [booking]):
            raise BadRequestException(self.ARRIVAL_LOCKED_MESSAGE)
        _ensure_no_open_issue(booking, exempt_flags=ARRIVAL_STAGE_EXEMPT_FLAGS)
        if booking["status"] != BookingStatus.CAPTAIN_ON_THE_WAY.value:
            raise BadRequestException("Vehicle verification happens after you've started heading to the customer")

        # Quick-booking model: the customer's 4-digit service code is the
        # arrival check. Plate matching remains only for bookings made
        # under the older saved-vehicle flow (they have no code).
        expected_code = booking.get("service_code")
        expected_plate = booking.get("vehicle_registration_number") or ""
        if payload.service_code:
            if not expected_code or payload.service_code != expected_code:
                await self._failed_arrival_check(booking, visit, captain_id, (
                    "That code doesn't match this booking. Ask the customer to check the code in their confirmation message — "
                    "if this really isn't their booking, release the job instead of continuing."
                ))
        elif payload.registration_number and expected_plate:
            # Older saved-vehicle bookings can still be verified by plate.
            if normalize_plate(payload.registration_number) != expected_plate:
                await self._failed_arrival_check(booking, visit, captain_id, (
                    "That registration number doesn't match this booking. Double-check the plate before proceeding — "
                    "if this really is the wrong vehicle, release the job instead of continuing."
                ))
        else:
            raise BadRequestException("Ask the customer for their 4-digit service code and enter it to start.")

        now = now_ist()
        update_data: dict = {"vehicle_verified": True, "vehicle_verified_at": now}
        flagged = False
        distance_m: float | None = None
        has_gps = payload.latitude is not None and payload.longitude is not None
        # This is also the "I've reached" press — the GPS it carries gets the
        # same geofence treatment as the photos: flag + tell the manager,
        # never block (service addresses aren't pinpoint-accurate).
        if has_gps:
            flagged, distance_m = await self._check_geofence(booking, payload.latitude, payload.longitude)
            update_data.update({
                "arrival_location": {"latitude": payload.latitude, "longitude": payload.longitude},
                "arrival_flagged": flagged,
                "arrival_distance_m": distance_m,
                "arrival_no_gps": False,
            })
        else:
            # No position at all is a location flag of its own (CAP-02) —
            # the manager sees it like an out-of-range arrival; the captain
            # carries on (GPS stays optional: old apps, a denied permission).
            update_data.update({"arrival_location": None, "arrival_flagged": True, "arrival_distance_m": None, "arrival_no_gps": True})
        first_arrival = not any(c.get("vehicle_verified") for c in visit if str(c["_id"]) != booking_id)
        # Guarded on the state checked above: a cancel or the captain's own
        # release landing in between must not be stamped "verified".
        on_the_way = {"status": BookingStatus.CAPTAIN_ON_THE_WAY.value, "captain_id": captain_id, "arrival_code_locked": {"$ne": True}}
        updated = await self.repo.update_if(booking_id, on_the_way, update_data)
        if updated is None:
            raise BadRequestException("This booking just changed — it may have been cancelled or released. Refresh your jobs list.")
        if has_gps:
            await self.update_captain_location(captain_id, payload.latitude, payload.longitude, source="arrival", booking_id=booking_id)
        # Its own history marker, not a second "captain_on_the_way" row
        # (audit E2E-03: the timeline read "on the way" twice). Not a booking
        # status — the booking stays captain_on_the_way until the photo.
        await self._record_history(
            booking_id, ARRIVAL_HISTORY_MARKER, captain_id,
            "Service code verified on arrival" if payload.service_code else "Vehicle registration verified on arrival",
        )
        # ONE code per visit: entering it once verifies every car on the
        # visit, so the captain can then start whichever car the customer
        # points him to first (before/after photos stay per car). Only
        # cars that are actually here with him (same captain, on the way).
        if payload.service_code:
            for sib in await self._visit_siblings(booking):
                if sib.get("captain_id") != captain_id or sib.get("status") != BookingStatus.CAPTAIN_ON_THE_WAY.value:
                    continue
                if sib.get("vehicle_verified") or sib.get("service_code") != expected_code:
                    continue
                if not await self.repo.update_if(str(sib["_id"]), on_the_way, update_data):
                    continue
                await self._record_history(
                    str(sib["_id"]), ARRIVAL_HISTORY_MARKER, captain_id, "Service code verified on arrival (visit)"
                )
        if not has_gps:
            await self._notify_location_flag(booking, "arrival_no_gps", None)
        elif flagged:
            await self._notify_location_flag(booking, "arrival", distance_m)
        if first_arrival:
            # "Your captain has arrived" — once per visit (NTF-03).
            await self._post_commit("arrival message", booking, self._notify_captain_arrived(booking, visit))
        return serialize_doc(updated)

    async def _notify_captain_arrived(self, booking: dict, visit: list[dict]) -> None:
        wa_services, wa_reference, wa_date, wa_slot, wa_vehicle, _wa_code = await self._wa_details(booking, visit)
        await self.notifications.notify(
            booking["customer_id"], "Captain arrived", f"Your captain has arrived for {wa_reference}.",
            NotificationType.BOOKING, str(booking["_id"]),
            wa_event="captain_arrived",
            wa_params=[wa_services, wa_reference, wa_date, wa_slot, wa_vehicle],
            background=True,  # the captain's app must not wait on Meta
        )

    async def _failed_arrival_check(self, booking: dict, visit: list[dict], captain_id: str, message: str) -> None:
        """Count one wrong arrival code/plate for this visit (on its lead
        car, one atomic $inc) and refuse. The attempt that reaches the limit
        locks the check — the one write that flips the lock flags the job
        and alerts the center's managers. Always raises."""
        lead = visit[0] if visit else booking
        counted = await self.repo.collection.find_one_and_update(
            {"_id": lead["_id"]},
            {"$inc": {"arrival_code_failures": 1}, "$set": {"updated_at": datetime.now(timezone.utc)}},
            return_document=ReturnDocument.AFTER,
        )
        failures = int((counted or {}).get("arrival_code_failures") or 0)
        if failures < self.ARRIVAL_CODE_MAX_ATTEMPTS:
            left = self.ARRIVAL_CODE_MAX_ATTEMPTS - failures
            raise BadRequestException(f"{message} ({left} {'try' if left == 1 else 'tries'} left)")
        locked = await self.repo.collection.update_one(
            {"_id": lead["_id"], "arrival_code_locked": {"$ne": True}},
            {"$set": {"arrival_code_locked": True, "arrival_code_locked_at": now_ist()}},
        )
        if locked.modified_count:
            note = (
                f"Arrival check locked on {self._visit_numbers(visit) if len(visit) > 1 else booking['booking_number']}: "
                f"{failures} wrong codes entered by the captain. Call the customer and the captain, then unlock it or reassign the job."
            )
            await self.flag_issue(
                str(booking["_id"]), "arrival_code_locked", note, send_whatsapp=False,
                expected={"status": BookingStatus.CAPTAIN_ON_THE_WAY.value},
            )
            await self._alert_other_managers(booking, f"🚨 Arrival check locked — booking {booking['booking_number']}", note)
            await self._record_history(str(booking["_id"]), BookingStatus(booking["status"]), captain_id, note)
        raise BadRequestException(self.ARRIVAL_LOCKED_MESSAGE)

    async def _alert_other_managers(self, booking: dict, title: str, message: str) -> None:
        """In-app alert to every working manager of the booking's center
        other than the named one (flag_issue already told that one)."""
        try:
            center = await self.center_repo.find_by_id(booking["service_center_id"])
            primary = (center or {}).get("manager_id")
            for user_id in await self._manager_recipients(booking.get("service_center_id"), primary):
                if user_id == primary:
                    continue
                await self.notifications.notify(
                    user_id, title, message, NotificationType.BOOKING, str(booking["_id"]), send_whatsapp=False, background=True,
                )
        except Exception:  # noqa: BLE001 — the lock itself already stands
            logger.exception("Could not alert managers about booking %s", booking.get("booking_number"))

    async def unlock_arrival_code(self, booking_id: str, actor_id: str, actor_role: str, actor_center_id: str | None) -> dict:
        """POST /bookings/{id}/unlock-arrival-code — the manager has checked
        with the customer and the captain: the visit's wrong-code count goes
        back to 0 and the lock (and its flag) is cleared."""
        booking = await self.repo.find_by_id(booking_id)
        if not booking:
            raise NotFoundException("Booking not found")
        ensure_own_center(actor_role, actor_center_id, booking["service_center_id"])
        cars = await self._visit_cars(booking)
        if not any(c.get("arrival_code_locked") or int(c.get("arrival_code_failures") or 0) for c in cars + [booking]):
            raise BadRequestException("The arrival check on this booking isn't locked.")
        ids = list({c["_id"] for c in cars + [booking]})
        now = now_ist()
        await self.repo.collection.update_many(
            {"_id": {"$in": ids}},
            {"$set": {"arrival_code_locked": False, "arrival_code_failures": 0, "arrival_code_unlocked_by": actor_id,
                      "arrival_code_unlocked_at": now, "updated_at": datetime.now(timezone.utc)}},
        )
        await self.repo.collection.update_many(
            {"_id": {"$in": ids}, "issue_flag": "arrival_code_locked"},
            {"$set": {"issue_flag": None, "issue_resolved": True, "resolved_issue_flag": "arrival_code_locked"}},
        )
        for car_id in ids:
            car = await self.repo.find_by_id(str(car_id))
            if car:
                await self._record_history(str(car_id), BookingStatus(car["status"]), actor_id, "Arrival check unlocked by the manager")
                await self._broadcast_booking_changed(car)
        return serialize_doc(await self.repo.find_by_id(booking_id))

    async def capture_before_photo(self, booking_id: str, payload: PhotoCaptureRequest, captain_id: str) -> dict:
        booking = await self.repo.find_by_id(booking_id)
        if not booking:
            raise NotFoundException("Booking not found")
        await self._ensure_captain_job(booking, captain_id)
        if booking["status"] == BookingStatus.SERVICE_STARTED.value and (booking.get("before_photo") or {}).get("image_url") == payload.image_url:
            return serialize_doc(booking)  # a double-submitted photo: the first one started it
        _ensure_no_open_issue(booking, exempt_flags=ARRIVAL_STAGE_EXEMPT_FLAGS)
        if booking["status"] != BookingStatus.CAPTAIN_ON_THE_WAY.value:
            raise BadRequestException("Reach the customer's location before starting the service")
        if not booking.get("vehicle_verified"):
            raise BadRequestException("Verify the vehicle's registration number before starting the service")
        _ensure_transition_allowed(booking["status"], BookingStatus.SERVICE_STARTED.value)
        # Order: claim the photo, then the guarded transition; a lost
        # transition hands the photo back (_unclaim_step_photo).
        claimed_photo = await self._claim_step_photo(booking, payload.image_url, captain_id, "before")

        now = now_ist()
        flagged, distance_m = await self._check_geofence(booking, payload.latitude, payload.longitude)
        # CLAIM the transition: a manager cancel or the captain's own release
        # landing between the check above and this write used to be
        # overwritten — a cancelled job came back as SERVICE_STARTED (or
        # started with nobody assigned to finish it).
        updated = await self.repo.update_if(
            booking_id,
            {"status": BookingStatus.CAPTAIN_ON_THE_WAY.value, "captain_id": captain_id, "vehicle_verified": True},
            {
                "status": BookingStatus.SERVICE_STARTED.value,
                "before_photo": {"image_url": payload.image_url, "latitude": payload.latitude, "longitude": payload.longitude, "captured_at": now},
                "before_photo_flagged": flagged,
                "before_photo_distance_m": distance_m,
                "service_started_at": now,
            },
        )
        if updated is None:
            current = await self.repo.find_by_id(booking_id)
            if current and current.get("status") == BookingStatus.SERVICE_STARTED.value and current.get("captain_id") == captain_id:
                return serialize_doc(current)  # a double-submitted photo: the first one started it
            if claimed_photo:
                await self._unclaim_step_photo(booking_id, payload.image_url, "before")
            raise BadRequestException("This booking just changed — it may have been cancelled or released. Refresh your jobs list.")
        await self.update_captain_location(captain_id, payload.latitude, payload.longitude, source="before_photo", booking_id=booking_id)
        await self._record_history(booking_id, BookingStatus.SERVICE_STARTED, captain_id, "Service started (before photo captured)")
        if flagged:
            await self._notify_location_flag(booking, "before", distance_m)
        # "Started" is said once per visit — when the first car starts, not
        # again for each of the others.
        if not any(c.get("service_started_at") for c in await self._visit_siblings(booking)):
            cars = await self._visit_cars(booking)
            wa_services, wa_reference, wa_date, wa_slot, wa_vehicle, wa_code = await self._wa_details(booking, cars)
            await self.notifications.notify(
                booking["customer_id"], "Service started", "Your captain has started the service.",
                NotificationType.BOOKING, booking_id,
                wa_event="service_started",
                wa_params=[wa_services, wa_reference, wa_date, wa_slot, wa_vehicle, wa_code],
            )
        await self._broadcast_booking_changed(updated)
        return serialize_doc(updated)

    async def capture_after_photo_and_complete(self, booking_id: str, payload: PhotoCaptureRequest, captain_id: str) -> dict:
        booking = await self.repo.find_by_id(booking_id)
        if not booking:
            raise NotFoundException("Booking not found")
        await self._ensure_captain_job(booking, captain_id)
        if booking["status"] == BookingStatus.COMPLETED.value:
            # A retried/double-tapped after-photo: the first one already
            # finished the job — answer with it, quietly (no second
            # "Service completed", no second history row).
            return serialize_doc(booking)
        _ensure_no_open_issue(booking, exempt_flags=COMPLETION_EXEMPT_FLAGS)
        if booking["status"] != BookingStatus.SERVICE_STARTED.value:
            raise BadRequestException("The service must be in progress before it can be completed")
        _ensure_transition_allowed(booking["status"], BookingStatus.COMPLETED.value)
        # Order: claim the photo, then the guarded completion; a lost
        # completion hands the photo back. A completion reverted for a
        # wallet failure keeps it — the retry of this same step may use it.
        claimed_photo = await self._claim_step_photo(booking, payload.image_url, captain_id, "after")

        now = now_ist()
        policy = await self.policy_service.get_policy()
        flagged, distance_m = await self._check_geofence(booking, payload.latitude, payload.longitude)
        update_data = {
            "status": BookingStatus.COMPLETED.value,
            "after_photo": {"image_url": payload.image_url, "latitude": payload.latitude, "longitude": payload.longitude, "captured_at": now},
            "after_photo_flagged": flagged,
            "after_photo_distance_m": distance_m,
            "completed_at": now,
            "closed_at": now,
        }

        # Wash-duration KPI capture. booking["service_started_at"] just came
        # back from Mongo naive, holding UTC-instant digits (same category
        # as heading_at) — it MUST go through from_stored() before
        # subtracting against the freshly computed aware-IST `now`, or this
        # silently reproduces the exact 5.5-hour bug the from_stored()/
        # to_ist() split exists to prevent, in a brand-new call site.
        quick_minutes: float | None = None
        if booking.get("service_started_at"):
            started_ist = from_stored(booking["service_started_at"])
            actual_duration = max(0, round((now - started_ist).total_seconds() / 60))
            update_data["actual_duration_minutes"] = actual_duration
            # A wash "done" within minutes of its before-photo is flagged to
            # the manager (CAP-02) — never blocked: a genuinely quick add-on
            # job exists, a two-photo fake completion should be looked at.
            elapsed = (now - started_ist).total_seconds() / 60
            if elapsed < self.QUICK_COMPLETION_MINUTES:
                quick_minutes = max(0.0, round(elapsed, 1))
            # Finalizes what the sweep (find_bookings_service_overrunning)
            # may have already flagged mid-flight, and gives KPI queries a
            # persisted number instead of recomputing from raw timestamps
            # every time. None (not 0) when not actually delayed.
            expected = booking.get("duration_minutes", 60)
            tolerance = policy.get("delay_tolerance_minutes", 20)
            overrun = actual_duration - expected - tolerance
            update_data["delay_minutes"] = overrun if overrun > 0 else None

        # Same reasoning as cancel_booking: a flag exists to prompt manager
        # action while the job is still live. The captain finishing it means
        # there's nothing left to act on — auto-resolve rather than leave a
        # completed job looking like an unresolved problem forever.
        # issue_flag itself is kept as the historical record.
        if booking.get("issue_flag") and not booking.get("issue_resolved"):
            update_data["issue_resolved"] = True
        if quick_minutes is not None:
            update_data.update({
                "quick_completion_flagged": True,
                "issue_flag": "completed_too_fast",
                "issue_notes": f"Completed {quick_minutes:g} minutes after the before-photo — check the photos.",
                "issue_flagged_at": now,
                "issue_resolved": False,
            })

        # Spec 1.5 (founder 2026-10-07): completion no longer marks a cash
        # booking paid — the captain then COLLECTS what is due (cash or a QR
        # for exactly amount_due, PaymentService.captain_collect_cash /
        # captain_payment_link), and that collection is what pays it. Here
        # only the job closes, and the captain's wallet gets the DELTA this
        # booking now owes it (MoneyService.settle_captain_wallet: his
        # earning once done − cash he holds − what was already posted).
        #
        # CLAIM the completion atomically: the write only lands while the
        # job is still in progress with this captain (a manager cancel racing
        # the photo used to be overwritten — the booking ended COMPLETED
        # after the cancel had refunded its pass and freed its seat). The
        # wallet delta is posted in the SAME transaction, so a failed post
        # leaves the job in progress for the retry, and a duplicate submit
        # loses the claim and posts nothing (PAY-04: a payment landing at
        # the same moment conflicts with this transaction and is retried —
        # the delta is always computed from the money actually on the
        # booking).
        async def _do_complete(session):
            done = await self.repo.update_if(
                booking_id, {"status": BookingStatus.SERVICE_STARTED.value, "captain_id": captain_id}, update_data, session=session,
            )
            if done is None:
                return None
            await self.money.settle_captain_wallet([booking_id], session=session)
            return await self.repo.find_by_id(booking_id, session=session)

        async with await self.db.client.start_session() as session:
            updated = await session.with_transaction(_do_complete)
        if updated is None:
            current = await self.repo.find_by_id(booking_id)
            if current and current.get("status") == BookingStatus.COMPLETED.value and current.get("captain_id") == captain_id:
                return serialize_doc(current)
            if claimed_photo:
                await self._unclaim_step_photo(booking_id, payload.image_url, "after")
            raise BadRequestException(
                "This booking just changed — it may have been cancelled or reassigned. Refresh your jobs list."
            )

        await self.update_captain_location(captain_id, payload.latitude, payload.longitude, source="after_photo", booking_id=booking_id)
        await self._record_history(booking_id, BookingStatus.COMPLETED, captain_id, "Service completed (after photo captured)")
        if flagged:
            await self._notify_location_flag(booking, "after", distance_m)
        if quick_minutes is not None:
            note = f"{booking['booking_number']} was completed {quick_minutes:g} minutes after its before-photo — worth checking the photos."
            await self._post_commit("quick-completion alert", booking, self.notify_center_manager_for_booking(
                booking, f"⚠️ Very quick completion — booking {booking['booking_number']}", note, send_whatsapp=False,
            ))
            await self._post_commit("quick-completion alert", booking, self._alert_other_managers(
                booking, f"⚠️ Very quick completion — booking {booking['booking_number']}", note,
            ))
        # "Complete" is said when the VISIT is complete — the last car's
        # after-photo, not each car's. Until then the customer sees each
        # car's progress live on the booking page anyway.
        still_open = [c for c in await self._visit_siblings(booking) if c.get("status") != BookingStatus.COMPLETED.value]
        if not still_open:
            cars = await self._visit_cars(booking)
            reference = self._visit_numbers(cars) if len(cars) > 1 else booking["booking_number"]
            done = f"All {len(cars)} vehicles are done ({reference})." if len(cars) > 1 else f"Booking {reference} is complete."
            wa_services, wa_reference, wa_date, wa_slot, wa_vehicle, wa_code = await self._wa_details(booking, cars)
            plan_note = await self._plan_usage_note(updated)
            message = f"{done} {plan_note} Please rate your captain!" if plan_note else f"{done} Please rate your captain!"
            await self.notifications.notify(
                booking["customer_id"],
                "Service completed",
                message,
                NotificationType.BOOKING,
                booking_id,
                wa_event="service_completed",
                wa_params=[wa_services, wa_reference, wa_date, wa_slot, wa_vehicle, wa_code],
                background=True,  # the captain's app must not wait on Meta
            )
        await self._after_visit_completed(booking["customer_id"], [updated], now)
        await self._broadcast_booking_changed(updated)
        return serialize_doc(updated)

    @staticmethod
    def _ensure_customer_may_cancel(cars: list[dict]) -> None:
        """Founder rule 2026-10-07 (spec 1.2): a customer cancels their own
        booking — before OR after the slot start, charged by the policy tier
        (late_cancellation_quote) — until the captain starts heading out:
        no car of the visit on the way, in service or done. Staff are not
        bound by this (they cancel with "at the customer's request")."""
        if any(c.get("status") in _CAPTAIN_HEADED for c in cars):
            raise BadRequestException(CUSTOMER_CANCEL_HEADED_MESSAGE)

    @staticmethod
    def _ensure_customer_may_edit(cars: list[dict], now: datetime | None = None) -> None:
        """Spec 1.3: a customer changes their booking (edit, or the older
        reschedule) only while no car of the visit has the captain on the
        way / working / done, and more than CUSTOMER_EDIT_LOCK_MINUTES
        before the slot starts. Enforced on the server for every channel
        (app, website, WhatsApp bot) — never a UI-only lock."""
        if any(c.get("status") in _CAPTAIN_HEADED for c in cars):
            raise BadRequestException(
                "Your captain is already on the way — this booking can't be changed now. Message us on WhatsApp if you need help."
            )
        if any(c.get("status") == BookingStatus.CANCELLED.value for c in cars):
            raise BadRequestException("This booking was cancelled — it can't be changed.")
        if not cars:
            return
        lead = min(cars, key=lambda c: int(c.get("group_offset_minutes") or 0))
        slot_start, _slot_end = _booking_window(lead)
        if (now or now_ist()) > slot_start - timedelta(minutes=CUSTOMER_EDIT_LOCK_MINUTES):
            raise BadRequestException(CUSTOMER_EDIT_LOCKED_MESSAGE)

    async def _touch_customer(self, customer_id: str | None, session) -> None:
        """A write every concurrent change to this customer's visit makes
        inside its transaction, so MongoDB serializes them (see _do_cancel)."""
        if customer_id and ObjectId.is_valid(customer_id):
            await self.user_repo.collection.update_one(
                {"_id": ObjectId(customer_id)}, {"$set": {"updated_at": datetime.now(timezone.utc)}}, session=session
            )

    # -- Seat ownership -------------------------------------------------
    #
    # A booking that took a slot seat RECORDS it: holds_seat=True plus
    # seat_key {service_center_id, date, slot_key} — the exact counter it
    # incremented — written in the same transaction as the increment. A
    # visit holds ONE seat, on one of its cars. Every hand-back is a guarded
    # claim on that record (holds_seat True -> False) in the same
    # transaction as the decrement, so a seat is returned exactly once no
    # matter how cancel / delete / reschedule / restore / mark-done race
    # (audit BOOK-02/03/04/06, STATE-02). Releases used to infer ownership
    # from stale reads, sibling positions and completed_by_role.

    @staticmethod
    def _seat_key(service_center_id: str, date_str: str, slot_key: str) -> dict:
        return {"service_center_id": service_center_id, "date": date_str, "slot_key": slot_key}

    @classmethod
    def _seat_key_of(cls, booking: dict) -> dict:
        """The counter this booking holds (or would hold). The recorded key
        when there is one; otherwise derived — from the slot's own stored
        start instant when present (right even for a BOOK-01 row whose
        scheduled_date was stored shifted to the previous day), else from
        scheduled_date."""
        if booking.get("seat_key"):
            return dict(booking["seat_key"])
        if booking.get("slot_start"):
            day = from_stored(booking["slot_start"]).strftime("%Y-%m-%d")
        else:
            day = to_ist(booking["scheduled_date"]).strftime("%Y-%m-%d")
        return cls._seat_key(booking["service_center_id"], day, booking["scheduled_slot"])

    @classmethod
    def _legacy_seat_holders(cls, cars: list[dict]) -> set[str]:
        """Which of these rows (one booking, or the cars of ONE visit) held a
        seat under the rules before ownership was recorded: a live or done
        booking that isn't in the recycle bin and isn't a manager-LOGGED job
        held one — one per visit per seat, on its first car. An explicitly
        recorded holder always wins. Used once per row (lazily or by
        backfill_seat_ownership), never again after."""
        taken = {tuple(cls._seat_key_of(c).values()) for c in cars if c.get("holds_seat") is True}
        candidates = sorted(
            (
                c for c in cars
                if "holds_seat" not in c and not c.get("is_deleted") and not c.get("logged_at")
                and c.get("status") in cls._SEAT_HOLDING_STATUSES
            ),
            key=lambda c: (int(c.get("group_offset_minutes") or 0), str(c["_id"])),
        )
        holders: set[str] = set()
        for car in candidates:
            key = tuple(cls._seat_key_of(car).values())
            if key in taken:
                continue
            taken.add(key)
            holders.add(str(car["_id"]))
        return holders

    async def _backfill_seats(self, cars: list[dict], session=None) -> None:
        """Record ownership on rows written before it existed (in place on
        the given dicts too). A no-op for every row created since."""
        legacy = [c for c in cars if "holds_seat" not in c]
        if not legacy:
            return
        holders = self._legacy_seat_holders(cars)
        for car in legacy:
            holds = str(car["_id"]) in holders
            key = self._seat_key_of(car)
            await self.repo.collection.update_one(
                {"_id": car["_id"], "holds_seat": {"$exists": False}},
                {"$set": {"holds_seat": holds, "seat_key": key}},
                session=session,
            )
            car["holds_seat"], car["seat_key"] = holds, key

    async def _take_seat(self, session, center: dict, booking_id, date_str: str, slot_key: str, *, day: bool = True) -> dict:
        """Reserve the counter AND record the booking as its holder — one
        unit of work, inside the caller's transaction. Raises (and the
        transaction aborts) when the slot is full, closed or unconfigured."""
        await self._reserve_slot_capacity(session, center, date_str, slot_key, day=day)
        key = self._seat_key(str(center["_id"]), date_str, slot_key)
        claimed = await self.repo.collection.update_one(
            {"_id": ObjectId(str(booking_id)), "holds_seat": {"$ne": True}},
            {"$set": {"holds_seat": True, "seat_key": key, "updated_at": datetime.now(timezone.utc)}},
            session=session,
        )
        if not claimed.modified_count:
            raise BadRequestException("This booking just changed — refresh and try again.")
        return key

    async def _release_seat(self, booking: dict, session, *, keep_day: bool = False) -> str:
        """Hand back the seat this booking HOLDS, once: the holds_seat claim
        is the guard. On a visit whose other cars still sit in that slot
        (live, or done there), the seat moves to one of them instead of
        going back — a visit holds its seat until its last car leaves.
        Call AFTER writing the booking's own new state, inside the same
        transaction. Returns "released", "transferred" or "none".
        `keep_day`: leave the daily counter alone (a same-day move)."""
        claim = await self.repo.collection.find_one_and_update(
            {"_id": booking["_id"], "holds_seat": True},
            {"$set": {"holds_seat": False, "updated_at": datetime.now(timezone.utc)}},
            session=session,
        )
        if claim is None:
            return "none"
        key = self._seat_key_of(claim)
        if claim.get("booking_group_id"):
            # A sibling still sitting in THAT seat — matched on the recorded
            # key, not on this car's own fields (a reschedule has already
            # moved them by the time it hands the old seat back). The date
            # window tolerates a legacy row stored shifted (BOOK-01).
            seat_day = datetime.strptime(key["date"], "%Y-%m-%d")
            heir = await self.repo.collection.find_one_and_update(
                {
                    "booking_group_id": claim["booking_group_id"],
                    "_id": {"$ne": claim["_id"]},
                    "is_deleted": {"$ne": True},
                    "status": {"$in": list(self._SEAT_HOLDING_STATUSES)},
                    "holds_seat": {"$ne": True},
                    "service_center_id": key["service_center_id"],
                    "scheduled_date": {"$gte": seat_day - timedelta(hours=12), "$lt": seat_day + timedelta(days=1)},
                    "scheduled_slot": key["slot_key"],
                },
                {"$set": {"holds_seat": True, "seat_key": key, "updated_at": datetime.now(timezone.utc)}},
                session=session,
            )
            if heir is not None:
                return "transferred"
        await self._release_slot_capacity(key["service_center_id"], key["date"], key["slot_key"], session=session, day=not keep_day)
        return "released"

    async def _visit_rows(self, booking: dict, session=None, *, include_cancelled: bool = False) -> list[dict]:
        """Every non-deleted row of this booking's visit as of `session`
        (cancelled ones too when asked) — the set seat ownership is decided
        over. Just the booking itself (re-read) for a single booking."""
        group_id = booking.get("booking_group_id")
        query: dict = {"booking_group_id": group_id} if group_id else {"_id": booking["_id"]}
        query["is_deleted"] = {"$ne": True}
        if not include_cancelled:
            query["status"] = {"$ne": BookingStatus.CANCELLED.value}
        return await self.repo.collection.find(query, session=session).sort("group_offset_minutes", 1).to_list(length=20)

    async def _claim_consumption_refund(self, booking_id: str) -> bool:
        """The pass wash a booking spent is handed back ONCE, whichever of
        cancel / recycle-bin delete gets there first: the flag is claimed
        atomically (deliberately not via update_if — a just-deleted booking
        must still be claimable). restore_booking re-spends it and clears it."""
        if not ObjectId.is_valid(booking_id):
            return False
        result = await self.repo.collection.update_one(
            # A wash FORFEITED by a late cancel (spec 1.2) is never handed back.
            {"_id": ObjectId(booking_id), "consumption_restored": {"$ne": True}, "consumption_forfeited": {"$ne": True}},
            {"$set": {"consumption_restored": True}},
        )
        return result.modified_count == 1

    async def _flag_refunds_due(self, cars: list[dict], reason: str) -> None:
        """Hand cars that were paid online to the admin refund queue
        (PaymentService.flag_refund_due filters to paid-online itself and
        records each car once). Best effort — the cancel already happened."""
        try:
            from app.services.payment_service import PaymentService

            await PaymentService(self.db).flag_refund_due(cars, reason)
        except Exception:  # noqa: BLE001
            logger.exception("Could not flag refunds for %s", [c.get("booking_number") for c in cars])

    async def _confirm_parked_cars_of(self, booking: dict) -> None:
        """After a car of a visit stops waiting for payment (cancelled), its
        ₹0 plan siblings that waited on it are confirmed and announced."""
        if not booking.get("booking_group_id"):
            return
        live = await self._visit_cars(booking)
        waiting = [c for c in live if c.get("status") == BookingStatus.AWAITING_PAYMENT.value]
        if not waiting or any(not self._owes_nothing(c) for c in waiting):
            return
        try:
            await self.confirm_awaiting_payment_booking(str(waiting[0]["_id"]), "Confirmed — nothing left to pay on this visit")
        except Exception:  # noqa: BLE001
            logger.exception("Could not confirm the rest of visit %s", booking.get("booking_group_id"))

    def _validate_cancel(self, booking: dict, actor_id: str, actor_role: str, actor_center_id: str | None) -> None:
        """Who may cancel this car, and whether it still can be. The
        customer's own "until the captain heads out" rule needs the whole
        visit — cancel_booking / cancel_booking_group check it
        (_ensure_customer_may_cancel), again inside their transaction."""
        if actor_role == "customer" and booking["customer_id"] != actor_id:
            raise ForbiddenException("You cannot cancel someone else's booking")
        # This route has no role restriction (customers cancel their own;
        # manager/admin cancel as a remediation) — captains have their own
        # dedicated release-the-job flow (captain_cancel) and must not fall
        # through here with no ownership check at all.
        if actor_role == "captain":
            raise ForbiddenException("Captains cannot cancel bookings — release the job instead if there's a problem")
        # Customers are already scoped by the customer_id check above (and
        # have no service_center_id of their own to compare) — the center
        # check only applies to staff acting on someone else's booking.
        if actor_role == "manager":
            ensure_own_center(actor_role, actor_center_id, booking["service_center_id"])
        if booking["status"] in {BookingStatus.COMPLETED.value, BookingStatus.CANCELLED.value}:
            raise BadRequestException("This booking can no longer be cancelled")
        if actor_role == "customer" and booking["status"] in _CAPTAIN_HEADED:
            raise BadRequestException(CUSTOMER_CANCEL_HEADED_MESSAGE)
        _ensure_transition_allowed(booking["status"], BookingStatus.CANCELLED.value)

    async def _hold_visit_for_customer_cancel(self, booking: dict, session) -> list[dict]:
        """Inside a customer's cancel transaction: write every car of the
        visit (a no-op touch) BEFORE checking that none has the captain on
        the way. start_heading's guarded write on any car then either landed
        first — this transaction conflicts, retries and sees it, refusing —
        or waits behind it and finds the cancelled cars gone. So "cancel"
        and "captain heads out" at the same moment never both win. Returns
        the visit's live rows as of this transaction."""
        group_id = booking.get("booking_group_id")
        query: dict = {"booking_group_id": group_id} if group_id else {"_id": booking["_id"]}
        await self.repo.collection.update_many(
            {**query, "is_deleted": {"$ne": True}, "status": {"$ne": BookingStatus.CANCELLED.value}},
            {"$set": {"updated_at": datetime.now(timezone.utc)}},
            session=session,
        )
        rows = await self._visit_rows(booking, session=session)
        self._ensure_customer_may_cancel(rows)
        return rows

    @staticmethod
    def _cancel_fields(booking: dict, reason: str, actor_role: str, *, forfeit: bool = False, by_customer: bool = False) -> dict:
        data: dict = {
            "status": BookingStatus.CANCELLED.value,
            "cancellation_reason": reason,
            "cancelled_by_role": actor_role,
            # Whether this was the customer's own decision (their cancel, or
            # staff "at the customer's request") — the message says "by you"
            # vs "by us", and a plan wash may be forfeited only then.
            "cancelled_at_customer_request": by_customer,
            "closed_at": now_ist(),
        }
        if forfeit:
            # A plan wash cancelled inside the last hour is used up (spec
            # 1.2): it is NOT handed back to the pass — _after_cancel and
            # the recycle bin's _claim_consumption_refund both respect this.
            data.update({"consumption_forfeited": True, "consumption_forfeited_at": now_ist()})
        # A flagged issue is only meant to prompt manager action WHILE the
        # booking is still live — once it's cancelled there's nothing left to
        # act on. Auto-resolve rather than leave it looking like an open
        # problem forever; issue_flag itself is kept as the historical record
        # of what happened.
        if booking.get("issue_flag") and not booking.get("issue_resolved"):
            data["issue_resolved"] = True
        return data

    # The payment-window sweep's guard (audit PAY-02): a car is released only
    # while it is STILL unpaid — awaiting payment with nothing paid, or a ₹0
    # plan car parked with its visit. Evaluated in the same atomic write as
    # the cancel, so a payment landing after the sweep's last check makes
    # the cancel a no-op instead of cancelling a paid booking.
    _UNPAID_GUARD = {
        "status": BookingStatus.AWAITING_PAYMENT.value,
        "$or": [{"payment_status": PaymentStatus.PENDING.value}, {"total_amount": {"$lte": 0}}],
    }

    @staticmethod
    def _still_unpaid(car: dict) -> bool:
        return car.get("status") == BookingStatus.AWAITING_PAYMENT.value and (
            car.get("payment_status") == PaymentStatus.PENDING.value or float(car.get("total_amount") or 0) <= 0
        )

    async def _after_cancel(self, before: dict, after: dict, reason: str, actor_id: str) -> None:
        """What a cancelled car hands back once its cancel committed — coupon
        use, pass wash (once, see _claim_consumption_refund; never a
        forfeited one) and open payment links. The MONEY that was paid is
        settled inside the cancel transaction itself (MoneyService.
        on_booking_cancelled → the customer's wallet), so there is no
        refund-queue row any more."""
        booking_id = str(before["_id"])
        if before.get("coupon_code"):
            await self.coupon_service.reverse_usage(before["coupon_code"], before["customer_id"], booking_id)
        if (
            before.get("subscription_id") and before.get("subscription_consumption") and not after.get("consumption_forfeited")
            and await self._claim_consumption_refund(booking_id)
        ):
            await self.subscription_service.restore_consumption(before["subscription_id"], before["subscription_consumption"])
        await self._void_payment_links([booking_id], "Booking cancelled")
        await self._record_history(booking_id, BookingStatus.CANCELLED, actor_id, reason)

    def _plan_forfeits(self, cars: list[dict], payload: BookingCancelRequest, actor_role: str, *, only_if_unpaid: bool) -> set[str]:
        """The plan-covered cars of this cancel whose wash is USED UP: the
        customer asked to cancel (their own cancel, or staff "at the
        customer's request") less than PLAN_WASH_FORFEIT_MINUTES before the
        slot (or after it started). 61 minutes before: returned; 59: used.
        Business / system / expiry cancels always return it, and staff may
        return it anyway (return_plan_wash)."""
        if only_if_unpaid or actor_role not in ("customer", "manager", "admin"):
            return set()
        at_request = actor_role == "customer" or bool(getattr(payload, "at_customer_request", False))
        if not at_request or (actor_role != "customer" and getattr(payload, "return_plan_wash", None)):
            return set()
        now = now_ist()
        out: set[str] = set()
        for car in cars:
            if not (car.get("subscription_id") and car.get("subscription_consumption")):
                continue
            slot_start, _ = _booking_window(car)
            if now > slot_start - timedelta(minutes=PLAN_WASH_FORFEIT_MINUTES):
                out.add(str(car["_id"]))
        return out

    async def _cancel_money(self, session, cars: list[dict], charge: dict | None, *, plan_ids: list[str], actor: dict, reason: str) -> dict:
        """Settle the money of these just-cancelled cars inside the cancel's
        transaction (MoneyService.on_booking_cancelled): what was paid +
        wallet credit spent − the charge goes to the customer's wallet (a
        debit when the charge is bigger). Business cancels pass no charge."""
        return await self.money.on_booking_cancelled(
            cars,
            charge_amount=float((charge or {}).get("amount") or 0),
            plan_car_ids=plan_ids,
            actor=actor,
            session=session,
            charge_id=str(charge["_id"]) if charge else None,
            reason=reason,
        )

    async def cancel_booking(
        self,
        booking_id: str,
        payload: BookingCancelRequest,
        actor_id: str,
        actor_role: str,
        actor_center_id: str | None = None,
        _quiet: bool = False,
        only_if_unpaid: bool = False,
    ) -> dict | None:
        """`_quiet` is set by cancel_booking_group / a failed visit create,
        which tell the customer and captain about the visit once themselves.

        `only_if_unpaid` — the payment-window expiry sweep (main.py): the
        cancel happens only if the booking is STILL awaiting payment with
        nothing paid, checked in the same write. A payment that landed after
        the sweep's last look makes this a silent no-op returning None — no
        exception, no notification, no seat released (audit PAY-02).

        A customer cancels until the captain heads out (spec 1.2), charged
        by the policy tier; a plan wash cancelled in its last hour is used
        up; every rupee paid (or wallet credit spent) goes back to the
        customer's wallet minus the charge — all in ONE transaction."""
        booking = await self.repo.find_by_id(booking_id)
        if not booking:
            if only_if_unpaid:
                return None
            raise NotFoundException("Booking not found")
        if only_if_unpaid:
            if not self._still_unpaid(booking):
                return None
        else:
            self._validate_cancel(booking, actor_id, actor_role, actor_center_id)
            if actor_role == "customer":
                self._ensure_customer_may_cancel(await self._visit_rows(booking))
        # The late-cancellation charge (founder 2026-10-07): only when this
        # cancel ends the whole visit — one car of a visit that still goes
        # ahead carries none.
        ends_visit = not [c for c in await self._visit_siblings(booking) if c.get("status") != BookingStatus.CANCELLED.value]
        plan = await self._cancel_charge_plan([booking], payload, actor_role, only_if_unpaid=only_if_unpaid, ends_visit=ends_visit)
        forfeits = self._plan_forfeits([booking], payload, actor_role, only_if_unpaid=only_if_unpaid)
        by_customer = actor_role == "customer" or bool(payload.at_customer_request and actor_role in ("manager", "admin"))
        actor = await self.charges.actor(actor_id, actor_role)
        cancel_data = self._cancel_fields(
            booking, payload.reason, actor_role, forfeit=booking_id in forfeits, by_customer=by_customer and not only_if_unpaid,
        )
        # Guarded on the exact status we validated: a concurrent duplicate
        # cancel (double-click, retry) loses this write and stops HERE —
        # before it can hand back a seat, coupon or plan wash a second time.
        # And on the slot read: a reschedule landing in between moved it.
        guard = {"status": booking["status"], "scheduled_date": booking["scheduled_date"], "scheduled_slot": booking["scheduled_slot"]}
        if only_if_unpaid:
            guard.update(self._UNPAID_GUARD)

        async def _do_cancel(session):
            # Every change to this customer's bookings writes their user doc
            # first, so concurrent changes of one visit (cancel two cars,
            # cancel vs delete, cancel vs a visit reschedule) serialize.
            await self._touch_customer(booking["customer_id"], session)
            if actor_role == "customer" and not only_if_unpaid:
                rows = await self._hold_visit_for_customer_cancel(booking, session)
            else:
                rows = await self._visit_rows(booking, session=session)
            await self._backfill_seats(rows, session)
            result = await self.repo.update_if(booking_id, guard, cancel_data, session=session)
            if result is None:
                if only_if_unpaid:
                    return None, None, None
                raise BadRequestException("This booking just changed state — refresh and try again.")
            await self._release_seat(result, session)
            # Legacy: late-cancellation charges this booking carried (the old
            # "added to the next booking" model) are settled on the wallet.
            await self.charges.release_for_booking(session, result, actor=actor, reason="cancelled")
            charge = None
            if plan and not [r for r in rows if str(r["_id"]) != booking_id]:
                charge = await self.charges.record_late_cancellation(
                    session, cars=[booking], visit_key=booking.get("booking_group_id") or booking_id,
                    tier=plan["tier"], amount=plan["charge"], original_amount=plan["amount"], actor=actor,
                )
            money = await self._cancel_money(
                session, [result], charge, plan_ids=[booking_id] if booking.get("subscription_id") else [],
                actor=actor, reason=payload.reason,
            )
            return result, charge, money

        async with await self.db.client.start_session() as session:
            updated, charge, money = await session.with_transaction(_do_cancel)
        if updated is None:
            return None

        if charge:
            await self._post_commit("charge audit", booking, self.charges.audit_created(charge, actor))
        await self._after_cancel(booking, updated, payload.reason, actor_id)
        await self._post_commit("cancel audit", booking, self._audit_cancel(
            actor, "CANCEL_BOOKING", booking_id, booking, payload, charge=charge, money=money, forfeited=sorted(forfeits),
        ))
        if not _quiet and booking["status"] == BookingStatus.AWAITING_PAYMENT.value:
            # The car this visit's ₹0 plan cars were waiting on is gone —
            # nothing is owed any more, so they're confirmed now.
            await self._confirm_parked_cars_of(booking)
        if not _quiet:
            await self._post_commit("cancel messages", booking, self._announce_cancel(
                [booking], booking, payload.reason, actor_role, by_customer and not only_if_unpaid, charge, money, forfeits,
            ))
        await self._broadcast_booking_changed(updated)
        await self._broadcast_slots_changed(booking["service_center_id"], self._seat_key_of(updated)["date"])
        result = serialize_doc(await self.repo.find_by_id(booking_id) or updated)
        if actor_role == "customer":
            # Like every other customer read of a booking (security review
            # 2026-10-07: this answered with the raw document).
            result = _redact_financials(result, "customer")
        result["late_cancellation_charge"] = self._charge_summary(charge)
        result["wallet"] = self._wallet_summary(money)
        result["plan_wash_forfeited"] = booking_id in forfeits
        return result

    @staticmethod
    def _wallet_summary(money: dict | None) -> dict | None:
        """What a cancel response says about the customer's wallet."""
        if not money:
            return None
        return {k: money.get(k) for k in ("credited", "charge", "net", "balance", "wallet_line")}

    async def _audit_cancel(
        self, actor: dict, action: str, target_id: str, lead: dict, payload: BookingCancelRequest, *,
        charge: dict | None, money: dict | None, forfeited: list[str], extra: dict | None = None,
    ) -> None:
        """One audit row per cancel, written by the service so EVERY channel
        (app, website, WhatsApp bot, staff) leaves one — the bot's cancels
        used to leave none."""
        from app.services.audit_service import AuditService

        await AuditService(self.db).log_action(
            actor.get("id") or "system", actor.get("role") or "system", action, "bookings", target_id,
            {
                "reason": payload.reason,
                "at_customer_request": bool(payload.at_customer_request) or actor.get("role") == "customer",
                "late_cancellation_charge": self._charge_summary(charge),
                "wallet": self._wallet_summary(money),
                "plan_wash_forfeited": forfeited,
                "booking_number": lead.get("booking_number"),
                **(extra or {}),
            },
            service_center_id=lead.get("service_center_id"),
        )

    async def _announce_cancel(
        self, cancelled: list[dict], lead: dict, reason: str, actor_role: str, by_customer: bool,
        charge: dict | None, money: dict | None, forfeits: set[str],
    ) -> None:
        """The customer's WhatsApp (booking_cancelled_v5: who cancelled and
        the wallet line) and in-app row, the captain's in-app row, and — for
        the customer's own cancel — an in-app row for the center's managers
        (spec 1.2: managers never get WhatsApp for a cancel)."""
        numbers = self._visit_numbers(cancelled) if len(cancelled) > 1 else str(lead.get("booking_number") or "")
        label = await self._visit_label(cancelled)
        wa_services, wa_reference, wa_date, wa_slot, _wa_vehicle, _wa_code = await self._wa_details(lead, cancelled)
        who = "by you" if by_customer else "by us"
        wallet_line = str((money or {}).get("wallet_line") or "")
        plan_line = ""
        if forfeits:
            plan_line = " The plan wash was used up (cancelled within an hour of the slot)."
        elif any(c.get("subscription_id") for c in cancelled):
            plan_line = " Your plan wash is back on your pass."
        detail = f"Cancelled {who}." + (f" {wallet_line}" if wallet_line else "") + plan_line
        await self.notifications.notify(
            lead["customer_id"],
            f"{label} cancelled" if len(cancelled) > 1 else "Booking cancelled",
            f"{label} — {numbers} has been cancelled. {detail}".strip(),
            NotificationType.BOOKING,
            str(lead["_id"]),
            wa_event="booking_cancelled_v5",
            wa_params=[wa_services, wa_reference, wa_date, wa_slot, detail],
            background=True,
        )
        for captain_id in {c.get("captain_id") for c in cancelled if c.get("captain_id")}:
            await self.notifications.notify(
                captain_id, "Booking cancelled", f"{numbers} was cancelled.", NotificationType.BOOKING, str(lead["_id"]),
                background=True,
            )
        if actor_role == "customer":
            center = await self.center_repo.find_by_id(lead["service_center_id"])
            charge_note = f" — ₹{float(charge['amount']):g} late-cancellation charge" if charge else ""
            for manager_id in await self._manager_recipients(lead.get("service_center_id"), (center or {}).get("manager_id")):
                await self.notifications.notify(
                    manager_id, "Customer cancelled", f"The customer cancelled {numbers}{charge_note}. Reason: {reason}",
                    NotificationType.BOOKING, str(lead["_id"]), send_whatsapp=False, background=True,
                )

    @staticmethod
    def _charge_summary(charge: dict | None) -> dict | None:
        """What a cancel response says about the charge it put on the
        customer's account (None when it put none)."""
        if not charge:
            return None
        return {"id": str(charge["_id"]), "amount": float(charge.get("amount") or 0), "tier": charge.get("tier"), "status": charge.get("status")}

    async def _cancel_charge_plan(
        self, cars: list[dict], payload: BookingCancelRequest, actor_role: str, *, only_if_unpaid: bool, ends_visit: bool,
    ) -> dict | None:
        """Does this cancel put a late-cancellation charge on the customer's
        account, and how much? None = no charge.

          - the customer's own cancel (app or WhatsApp): the policy tier,
            always — they can't choose it;
          - staff: only `at_customer_request`; `charge_amount` (0..the
            policy amount) lowers it, default the policy amount;
          - expiry, system, society-schedule and every other business cancel
            (no at_customer_request): never;
          - one car of a visit that still goes ahead: never;
          - plan-covered cars carry no money charge (spec 1.2) — a visit of
            only plan cars is never charged; a mixed visit pays the tier
            once for its paid part."""
        if only_if_unpaid or actor_role not in ("customer", "manager", "admin"):
            return None
        at_request = actor_role == "customer" or bool(getattr(payload, "at_customer_request", False))
        asked = None if actor_role == "customer" else getattr(payload, "charge_amount", None)
        if not at_request:
            if asked:
                raise BadRequestException("A cancellation charge applies only when the customer asked to cancel.")
            return None
        if not ends_visit:
            if asked:
                raise BadRequestException(
                    "Cancelling one vehicle of a visit carries no charge — cancel the whole visit to charge the customer."
                )
            return None
        if cars and all(c.get("subscription_id") for c in cars):
            if asked:
                raise BadRequestException("A plan-covered wash carries no cancellation charge.")
            return None
        quote = late_cancellation_quote(cars, await self.policy_service.get_policy())
        amount = float(quote["amount"])
        if asked is not None:
            if float(asked) > amount + 0.004:
                raise BadRequestException(f"The charge can't be more than ₹{amount:g} — the policy amount for this cancellation.")
            amount = float(round_rupees(asked))
        if amount <= 0:
            return None
        return {**quote, "charge": amount}

    async def cancellation_charge_preview(
        self, booking_id: str, actor_id: str, actor_role: str, actor_center_id: str | None, *, whole_visit: bool = False,
    ) -> dict:
        """GET /bookings/{id}/cancellation-charge-preview — what cancelling
        this booking (or, `whole_visit`, its whole visit) would cost the
        customer right now, per the policy table, before they confirm.

        Returns the tier fields (tier, amount, captain_left,
        minutes_to_slot, window_hours) plus: ends_visit (a cancel of one car
        while the rest goes ahead is free); can_cancel / refusal (the
        CUSTOMER's own rule — until the captain heads out); plan_covered,
        plan_wash_returned / plan_wash_forfeited_count (a plan wash
        cancelled inside its last hour is used up); and the wallet effect —
        wallet_credit (what was paid + wallet credit spent), net (credit −
        charge; negative = a debit) and wallet_balance_after."""
        booking = await self.repo.find_by_id(booking_id)
        if not booking:
            raise NotFoundException("Booking not found")
        if actor_role == "customer":
            if booking.get("customer_id") != actor_id:
                raise NotFoundException("Booking not found")
        elif actor_role == "manager":
            ensure_own_center(actor_role, actor_center_id, booking.get("service_center_id"))
        elif actor_role != "admin":
            raise ForbiddenException("You don't have access to this booking")
        visit = await self._visit_rows(booking)
        cars = await self._visit_cars(booking) if whole_visit else [booking]
        ends_visit = whole_visit or not [
            c for c in await self._visit_siblings(booking) if c.get("status") != BookingStatus.CANCELLED.value
        ]
        quote = late_cancellation_quote(cars, await self.policy_service.get_policy())
        plan_covered = bool(cars) and all(c.get("subscription_id") for c in cars)
        if not ends_visit or plan_covered:
            quote = {**quote, "amount": 0.0}
        refusal = None
        if booking["status"] in {BookingStatus.COMPLETED.value, BookingStatus.CANCELLED.value}:
            refusal = "This booking can no longer be cancelled"
        elif actor_role == "customer" and any(c.get("status") in _CAPTAIN_HEADED for c in visit):
            refusal = CUSTOMER_CANCEL_HEADED_MESSAGE
        # The customer's own cancel is always "at their request" — staff
        # preview the at-request case (the dialog's default).
        forfeits = self._plan_forfeits(
            cars, BookingCancelRequest(reason="preview", at_customer_request=True), "customer" if actor_role == "customer" else actor_role,
            only_if_unpaid=False,
        )
        credit = round(sum(bm.refundable(c) for c in cars), 2)
        net = round(credit - float(quote["amount"]), 2)
        balance = await self.money.wallet.balance(booking["customer_id"]) if booking.get("customer_id") else 0.0
        return {
            **quote,
            "ends_visit": ends_visit,
            "can_cancel": refusal is None,
            "refusal": refusal,
            "plan_covered": plan_covered,
            "plan_wash_forfeited_count": len(forfeits),
            "plan_wash_returned": any(c.get("subscription_id") for c in cars) and not forfeits,
            "wallet_credit": credit,
            "net": net,
            "wallet_balance_after": round(float(balance or 0) + net, 2),
        }

    async def _seat_move_begin(
        self, session, rows: list[dict], mover_ids: list[str], service_center: dict, new_date_str: str, new_slot: str,
    ) -> dict:
        """First half of moving a visit's ONE seat to a new date/slot inside
        the caller's transaction (reschedule and the customer's edit share
        it): reserve the NEW seat first — a full slot aborts the whole move
        with nothing changed. Returns the context _seat_move_finish needs."""
        by_id = {str(c["_id"]): c for c in rows}
        lead = by_id[mover_ids[0]]
        old_key = self._seat_key_of(lead)
        same_slot = old_key["date"] == new_date_str and lead["scheduled_slot"] == new_slot
        holder = next((c for c in rows if c.get("holds_seat") is True), None)
        # The day's count stays put only when the visit's old seat really
        # goes back on the same day — not when a car already done there
        # keeps it (then the move is one MORE seat that day).
        keep_day = (
            old_key["date"] == new_date_str and holder is not None and str(holder["_id"]) in mover_ids
            and all(str(c["_id"]) in mover_ids for c in rows)
        )
        if not same_slot:
            await self._reserve_slot_capacity(session, service_center, new_date_str, new_slot, day=not keep_day)
        return {"same_slot": same_slot, "holder": holder, "keep_day": keep_day}

    async def _seat_move_finish(
        self, session, move: dict, mover_ids: list[str], moved_ids: set[str], center_id: str, new_date_str: str, new_slot: str,
    ) -> bool:
        """Second half, after every mover's new date/slot is written: the
        old seat is handed back by its holder if the holder moved (or passed
        to a car already done there), then the new seat is recorded on the
        first mover — one seat for the visit. Returns True when it wrote."""
        if move["same_slot"]:
            return False
        holder = move["holder"]
        if holder is not None and str(holder["_id"]) in moved_ids:
            await self._release_seat(holder, session, keep_day=move["keep_day"])
        await self.repo.collection.update_one(
            {"_id": ObjectId(mover_ids[0])},
            {"$set": {"holds_seat": True, "seat_key": self._seat_key(center_id, new_date_str, new_slot)}},
            session=session,
        )
        return True

    @staticmethod
    def _reschedule_fields(car: dict, payload: BookingRescheduleRequest, new_slot_start: datetime, new_slot_end: datetime) -> dict:
        data: dict = {
            "scheduled_date": payload.scheduled_date,
            "scheduled_slot": payload.scheduled_slot,
            "slot_start": new_slot_start,
            "slot_end": new_slot_end,
            "estimated_start_at": None,
            "status": BookingStatus.RESCHEDULED.value,
            "captain_id": None,
            "reminder_sent": False,
            # The new time gets its own pre-slot customer reminder.
            "customer_reminder_sent_at": None,
            "issue_flag": None,
            "issue_resolved": True,
            # A clean slate — whoever gets assigned next (self-assigned or
            # not, late or not) is a fresh situation, never suppressed by
            # whatever was acknowledged/self-assigned before the move.
            "resolved_issue_flag": None,
            "self_assigned": False,
            "captain_start_stage": None,
            "late_penalty_pct": 0,
            # Just re-entered "needs a captain" — restart the clock on
            # the repeating unassigned-reminder sweep rather than having
            # it fire again immediately off the original (now stale)
            # wait time.
            "awaiting_assignment_since": now_ist(),
            "unassigned_reminder_sent_at": None,
        }
        if car.get("captain_id"):
            # Whoever gets assigned next must re-verify the vehicle and
            # re-capture their own before-photo — carrying over the
            # previous captain's "already verified"/photo state would let
            # a new captain silently skip the on-arrival plate check.
            data.update(
                {
                    "vehicle_verified": False,
                    "vehicle_verified_at": None,
                    "before_photo": None,
                    "before_photo_flagged": False,
                    "before_photo_distance_m": None,
                    "heading_at": None,
                    "heading_location": None,
                    "service_started_at": None,
                }
            )
        return data

    async def reschedule_booking(
        self,
        booking_id: str,
        payload: BookingRescheduleRequest,
        actor_id: str,
        actor_role: str = "customer",
        actor_center_id: str | None = None,
    ) -> dict:
        """On a multi-car visit the whole visit moves: one address, one
        slot, one captain — moving one car would split the trip. ONE
        transaction moves every car and the visit's single seat together,
        after writing the customer's doc so it serializes with a concurrent
        cancel of one of its cars (audit BOOK-04: the per-car loop let a
        cancel land in between and the old seat was handed back twice). A
        car already done in the old slot keeps that seat; the movers take a
        new one."""
        booking = await self.repo.find_by_id(booking_id)
        if not booking:
            raise NotFoundException("Booking not found")
        if actor_role == "customer" and booking["customer_id"] != actor_id:
            raise ForbiddenException("You cannot reschedule someone else's booking")
        if actor_role == "captain":
            raise ForbiddenException("Captains cannot reschedule bookings — release the job instead if there's a problem")
        if actor_role == "manager":
            ensure_own_center(actor_role, actor_center_id, booking["service_center_id"])
        if booking["status"] in {BookingStatus.COMPLETED.value, BookingStatus.CANCELLED.value}:
            raise BadRequestException("This booking can no longer be rescheduled")
        cars = await self._visit_cars(booking)
        if not any(str(c["_id"]) == booking_id for c in cars):
            cars = [booking]
        # A captain who's already on the way or mid-service has real,
        # unfinished work invested in this visit — pulling it out from
        # under them here would orphan that work with no notice. Release
        # the captain (captain_cancel) or cancel the booking outright
        # instead; reschedule is only for a visit that hasn't reached
        # that point yet.
        if any(c.get("status") in {BookingStatus.CAPTAIN_ON_THE_WAY.value, BookingStatus.SERVICE_STARTED.value} for c in cars):
            if len(cars) > 1:
                raise BadRequestException(
                    "The captain is already on the way or mid-service for this visit — cancel it or have the "
                    "captain release it before rescheduling."
                )
            raise BadRequestException(
                "This booking's captain is already on the way or mid-service — cancel the booking or have the "
                "captain release it before rescheduling."
            )
        movers = [c for c in cars if c.get("status") not in {BookingStatus.COMPLETED.value, BookingStatus.CANCELLED.value}]
        if actor_role == "customer":
            # A move is a date/slot EDIT (spec 1.3): the same lock as
            # PATCH /bookings/{id} — more than an hour before the slot and
            # the captain not on the way. An assigned captain is released.
            self._ensure_customer_may_edit(cars)
        for car in movers:
            _ensure_transition_allowed(car["status"], BookingStatus.RESCHEDULED.value)

        policy = await self.policy_service.get_policy()
        service_center = await self.center_repo.find_by_id(booking["service_center_id"])
        if not service_center:
            raise NotFoundException("Service center not found")
        # Same server-side rules create_booking enforces (a real,
        # currently-bookable admin slot, not past cutoff, within the
        # advance-booking window) — reschedule must not be a backdoor
        # around them just because it's editing an existing booking
        # instead of creating a new one.
        _ensure_within_advance_window(payload.scheduled_date, policy)
        if actor_role == "customer":
            # A society premium wash keeps its day's notice when moved too —
            # otherwise booking tomorrow and rescheduling to today skips it.
            from app.services.society_service import ensure_society_lead_time

            await ensure_society_lead_time(self.db, booking.get("subscription_id"), payload.scheduled_date, "app")
        # A pass covers washes inside its own period only — for staff moves
        # too, and for every car of the visit that rides on one (PASS-3).
        from app.services.subscription_service import ensure_subscription_covers_date

        for car in movers:
            await ensure_subscription_covers_date(self.db, car.get("subscription_id"), payload.scheduled_date)
        new_slot_start, new_slot_end = _resolve_slot_window(service_center, payload.scheduled_date, payload.scheduled_slot, policy)
        if _slot_cutoff_passed(new_slot_end, policy):
            raise BadRequestException("This slot is no longer available to book — please pick another slot.")

        center_id = booking["service_center_id"]
        new_date_str = to_ist(payload.scheduled_date).strftime("%Y-%m-%d")
        mover_ids = [str(c["_id"]) for c in movers]

        async def _do_reschedule(session):
            await self._touch_customer(booking["customer_id"], session)
            rows = await self._visit_rows(booking, session=session)
            by_id = {str(c["_id"]): c for c in rows}
            # The cars validated above must still be exactly as validated.
            for car in movers:
                now_car = by_id.get(str(car["_id"]))
                if (
                    not now_car or now_car["status"] != car["status"]
                    or now_car["scheduled_date"] != car["scheduled_date"] or now_car["scheduled_slot"] != car["scheduled_slot"]
                ):
                    raise BadRequestException("This booking just changed — refresh and try rescheduling again.")
            if any(c.get("status") in {BookingStatus.CAPTAIN_ON_THE_WAY.value, BookingStatus.SERVICE_STARTED.value} for c in rows):
                raise BadRequestException("This booking just changed — refresh and try rescheduling again.")
            await self._raise_if_customer_conflict(
                booking["customer_id"], new_slot_start, new_slot_end, booking_id, booking.get("booking_group_id"), session=session
            )
            await self._backfill_seats(rows, session)
            move = await self._seat_move_begin(session, rows, mover_ids, service_center, new_date_str, payload.scheduled_slot)
            results: dict[str, dict] = {}
            for car_id in mover_ids:
                car = by_id[car_id]
                # Guarded on what was just read in this transaction — the
                # DATE too: a double-submitted move to a new day with the
                # same slot key passed a status+slot guard twice.
                result = await self.repo.update_if(
                    car_id,
                    {"status": car["status"], "scheduled_slot": car["scheduled_slot"], "scheduled_date": car["scheduled_date"]},
                    self._reschedule_fields(car, payload, new_slot_start, new_slot_end),
                    session=session,
                )
                if result is None:
                    raise BadRequestException("This booking just changed — refresh and try rescheduling again.")
                results[car_id] = result
            if await self._seat_move_finish(session, move, mover_ids, set(results), center_id, new_date_str, payload.scheduled_slot):
                results[mover_ids[0]] = await self.repo.find_by_id(mover_ids[0], session=session)
            return results

        try:
            async with await self.db.client.start_session() as session:
                moved = await session.with_transaction(_do_reschedule)
        except DuplicateKeyError:
            raise BadRequestException("This customer already has a booking for this vehicle in this slot.")

        note = "Rescheduled by customer" if actor_role == "customer" else f"Rescheduled by {actor_role}"
        old_day = self._seat_key_of(booking)["date"]
        changes = [
            {"field": "scheduled_date", "from": old_day, "to": new_date_str},
            {"field": "scheduled_slot", "from": booking["scheduled_slot"], "to": payload.scheduled_slot},
        ]
        for car_id in mover_ids:
            await self._record_history(car_id, BookingStatus.RESCHEDULED, actor_id, note, changes=changes)
        # The audit row is written HERE so every channel leaves one (the
        # WhatsApp bot's reschedule used to leave none).
        from app.services.audit_service import AuditService

        await self._post_commit("reschedule audit", booking, AuditService(self.db).log_action(
            actor_id, actor_role, "RESCHEDULE_BOOKING", "bookings", booking_id,
            {"new_date": new_date_str, "new_slot": payload.scheduled_slot, "old_date": old_day, "old_slot": booking["scheduled_slot"],
             "booking_numbers": [moved[i].get("booking_number") for i in mover_ids if moved.get(i)]},
            service_center_id=booking.get("service_center_id"),
        ))
        # Messages go out once per visit, never once per car.
        lead_doc = moved[mover_ids[0]]
        visit = await self._visit_cars(lead_doc)
        reference = self._visit_numbers(visit) if len(visit) > 1 else lead_doc["booking_number"]
        if actor_role != "customer":
            # The NEW date and time, worded like every other message
            # ("12 Oct 2026", "9:00 AM – 12:00 PM") — not the raw ISO date
            # and 24-hour slot key.
            wa_services, wa_reference, wa_date, wa_slot, wa_vehicle, wa_code = await self._wa_details(lead_doc, visit)
            await self.notifications.notify(
                booking["customer_id"],
                "Booking rescheduled",
                f"{reference} has been moved to a new time — we'll confirm your captain shortly.",
                NotificationType.BOOKING,
                booking_id,
                wa_event="reschedule_confirmation",
                wa_params=[wa_services, wa_reference, wa_date, wa_slot, wa_vehicle, wa_code],
            )
        for captain_id in {c.get("captain_id") for c in movers if c.get("captain_id")}:
            await self.notifications.notify(
                captain_id,
                "Booking rescheduled — no longer yours",
                f"{reference} was rescheduled to a new time and needs a new captain assignment.",
                NotificationType.BOOKING,
                booking_id,
            )
        for doc in moved.values():
            await self._broadcast_booking_changed(doc)
        old_date_str = self._seat_key_of(booking)["date"]
        if old_date_str != new_date_str or booking["scheduled_slot"] != payload.scheduled_slot:
            await self._broadcast_slots_changed(center_id, old_date_str)
            await self._broadcast_slots_changed(center_id, new_date_str)
        result = serialize_doc(moved.get(booking_id) or await self.repo.find_by_id(booking_id))
        # Same as cancel (SEC-2): a customer never sees the earnings split.
        return _redact_financials(result, "customer") if actor_role == "customer" else result

    # -- Customer edits a booking (spec 1.3) ------------------------------
    #
    # PATCH /bookings/{id} and /bookings/group/{gid}: date/slot, address,
    # services / add-ons / quantities, car type / car, notes — until an hour
    # before the slot and while no captain is on the way. Re-priced by the
    # SAME code as quote/create (_price_services, travel_quote) — but only
    # what the edit touches: a car whose services/car didn't change keeps
    # the price it was booked at (an edit of the notes never pulls in a
    # catalogue price raised since), and the distance charge is re-measured
    # on an address change (or when the services that impose it change).
    # One transaction: seats move like a reschedule, every car's money goes
    # through MoneyService.on_price_change (a paid booking priced down is
    # credited to the wallet, priced up the difference becomes due).

    # What the edit was computed from — a concurrent edit/add-on/assignment
    # that changed any of these makes this one refuse ("just changed").
    _EDIT_FINGERPRINT = (
        "scheduled_date", "scheduled_slot", "total_amount", "service_ids", "service_quantities", "combo_id",
        "vehicle_type", "vehicle_id", "address_id", "subscription_id", "added_services_total",
    )
    PLAN_EDIT_MESSAGE = (
        "This wash is covered by your plan, so its service or car can't be changed — you can still change the date, "
        "time, address or notes. To book something else, cancel it and book again."
    )
    OTHER_CENTER_MESSAGE = (
        "That address is served by a different Blussit center, so this booking can't move there. "
        "Please cancel this booking and book again for the new address."
    )

    async def _first_time_of(self, car: dict, visit_ids: set[str]) -> bool:
        """Was this car priced as the customer's first wash? Stored at create
        since 2026-10-07 (first_time_eligible). Older bookings: the visit's
        first car whose phone had no earlier live booking. Kept as at create
        on purpose — an edit neither takes a promised first-wash price away
        nor hands one to a car that didn't have it."""
        if "first_time_eligible" in car:
            return bool(car["first_time_eligible"])
        if int(car.get("group_offset_minutes") or 0) != 0 or not car.get("customer_phone"):
            return False
        earlier = await self.repo.collection.find_one({
            "customer_phone": car["customer_phone"], "status": {"$ne": BookingStatus.CANCELLED.value},
            "created_at": {"$lt": car.get("created_at") or datetime.now(timezone.utc)},
            "_id": {"$nin": [ObjectId(i) for i in visit_ids]},
        }, {"_id": 1})
        return earlier is None

    async def _held_coupon_discount(self, car: dict, subtotal: float, services, quantities, vehicle_type, first_time) -> tuple[float, str | None]:
        """Re-validate the coupon this booking already HOLDS against its new
        services/subtotal — applicability only (minimum order, the offer's
        own service rules); its use was already counted at create, so the
        usage limits it consumed don't refuse it. Returns (discount, why it
        no longer applies or None)."""
        code = car.get("coupon_code")
        coupon = await self.coupon_service.repo.find_by_code(code) if code else None
        if not coupon:
            return 0.0, f"Coupon {code} is no longer available"
        try:
            _c, discount = await _HeldCouponCheck(self.db, coupon).validate_and_compute_booking_discount(
                code, subtotal, None, services=services, quantities=quantities, vehicle_type=vehicle_type,
                first_time_eligible=first_time, price_resolver=self._resolve_price,
            )
        except BadRequestException as exc:
            return 0.0, exc.message
        return float(discount), None

    async def _edit_car_plan(self, car: dict, edit, customer_id: str) -> dict:
        """What one car becomes (vehicle and services) — validated exactly
        like create_booking validates them. Unchanged parts are kept."""
        vehicle_id = car.get("vehicle_id")
        vehicle_type = car.get("vehicle_type")
        vehicle_label = car.get("vehicle_label")
        registration = car.get("vehicle_registration_number")
        if edit is not None and edit.vehicle_id:
            vehicle = await self.vehicle_repo.find_by_id(edit.vehicle_id)
            if not vehicle or vehicle.get("owner_id") != customer_id:
                raise NotFoundException("Vehicle not found")
            if edit.vehicle_type and edit.vehicle_type != vehicle.get("vehicle_type"):
                raise BadRequestException("That car is saved as a different vehicle type — pick its own type.")
            vehicle_id, vehicle_type = edit.vehicle_id, vehicle["vehicle_type"]
            registration = normalize_plate(vehicle["registration_number"]) if vehicle.get("registration_number") else None
        elif edit is not None and edit.vehicle_type:
            vehicle_id, vehicle_type, registration = None, edit.vehicle_type, None
        if vehicle_type != car.get("vehicle_type") or (edit is not None and edit.vehicle_id):
            vt_doc = await self.vehicle_type_repo.find_by_id(vehicle_type) if vehicle_type else None
            if not vt_doc or not vt_doc.get("is_active", True):
                raise BadRequestException("Pick a valid vehicle type.")
            vehicle_label = vt_doc.get("name") or "Vehicle"

        combo = None
        if edit is not None and edit.service_ids is not None:
            service_ids = list(edit.service_ids)
            raw_quantities = dict(edit.service_quantities or {})
        else:
            if car.get("combo_id") and (edit is None or edit.service_quantities is None):
                combo = await self.combo_repo.find_by_id(car["combo_id"])
            service_ids = list((combo or {}).get("service_ids") or car.get("service_ids") or [])
            stored = dict(car.get("service_quantities") or {})
            raw_quantities = dict(edit.service_quantities) if (edit is not None and edit.service_quantities is not None) else stored
        services = []
        for sid in service_ids:
            svc = await self.service_repo.find_by_id(sid)
            if not svc or not svc.get("is_active"):
                raise NotFoundException(f"Service not found or inactive: {sid}")
            services.append(svc)
        if edit is not None and edit.service_ids is not None:
            # New picks must be ones the booking page offers this type; what
            # the car already carries (same type) may stay.
            kept = set(car.get("service_ids") or []) if vehicle_type == car.get("vehicle_type") else set()
            self._ensure_offered_for_type(
                [s for s in services if str(s["_id"]) not in kept], vehicle_type, bike_type=await self._is_bike_type(vehicle_type),
            )
        if combo:
            quantities = {str(s["_id"]): 1 for s in services}
        else:
            quantities = await self._validate_service_mix(services, vehicle_type, raw_quantities)
        line_key = car.get("visit_line_key") or ""
        index = line_key.rsplit("#", 1)[1] if "#" in line_key else "0"
        return {
            "vehicle_id": vehicle_id, "vehicle_type": vehicle_type, "vehicle_label": vehicle_label,
            "vehicle_registration_number": registration, "visit_line_key": vehicle_id or f"{vehicle_type}#{index}",
            "services": services, "service_ids": [str(s["_id"]) for s in services] if not combo else list(car.get("service_ids") or []),
            "quantities": quantities, "combo": combo,
            "duration_minutes": sum(s.get("duration_minutes", 30) * quantities[str(s["_id"])] for s in services) or 30,
        }

    async def edit_booking(
        self, booking_id: str | None, payload, actor_id: str, actor_role: str, actor_center_id: str | None = None, *,
        group_id: str | None = None,
    ) -> dict:
        """The customer's own change to a booking (one car, PATCH
        /bookings/{id}) or a visit (PATCH /bookings/group/{gid}). Date/slot,
        address and notes are the VISIT's (one trip); services and the car
        are per car. See the section comment above for pricing/money.

        Returns {booking_group_id, bookings (updated, as the customer sees
        them), changes [{booking_id, booking_number, field, from, to}],
        notices (e.g. a coupon that no longer applies), total_amount,
        amount_due, wallet_credit}."""
        from app.services.society_service import ensure_society_lead_time
        from app.services.subscription_service import ensure_subscription_covers_date

        dry_run = bool(getattr(payload, "dry_run", False))
        if group_id:
            rows = await self.repo.collection.find({"booking_group_id": group_id, "is_deleted": {"$ne": True}}).to_list(length=20)
            if not rows or (actor_role == "customer" and any(r.get("customer_id") != actor_id for r in rows)):
                raise NotFoundException("Booking not found")
            live_rows = [r for r in rows if r.get("status") != BookingStatus.CANCELLED.value]
            if not live_rows:
                raise BadRequestException("This booking was cancelled — it can't be changed.")
            lead = min(live_rows, key=lambda c: int(c.get("group_offset_minutes") or 0))
        else:
            lead = await self.repo.find_by_id(booking_id) if booking_id else None
            if not lead or (actor_role == "customer" and lead.get("customer_id") != actor_id):
                raise NotFoundException("Booking not found")
        if actor_role == "manager":
            ensure_own_center(actor_role, actor_center_id, lead["service_center_id"])
        if lead["status"] in {BookingStatus.COMPLETED.value, BookingStatus.CANCELLED.value}:
            raise BadRequestException("This booking can no longer be changed.")
        customer_id = lead["customer_id"]
        visit = await self._visit_cars(lead)
        if not any(str(c["_id"]) == str(lead["_id"]) for c in visit):
            visit = [lead]
        visit = sorted(visit, key=lambda c: int(c.get("group_offset_minutes") or 0))
        if actor_role == "customer":
            self._ensure_customer_may_edit(visit)
        by_id = {str(c["_id"]): c for c in visit}
        visit_ids = set(by_id)

        car_edits: dict[str, object] = {}
        for edit in payload.car_edits(None if group_id else str(lead["_id"])):
            if not edit.booking_id:
                raise BadRequestException("Say which vehicle each change is for (booking_id).")
            if edit.booking_id not in by_id:
                raise NotFoundException("Booking not found")
            if by_id[edit.booking_id].get("subscription_id"):
                raise BadRequestException(self.PLAN_EDIT_MESSAGE)
            car_edits[edit.booking_id] = edit

        policy = await self.policy_service.get_policy()
        center = await self.center_repo.find_by_id(lead["service_center_id"])
        if not center:
            raise NotFoundException("Service center not found")

        # -- when ----------------------------------------------------------
        old_day = self._seat_key_of(lead)["date"]
        move = False
        new_day = old_day
        new_slot = lead["scheduled_slot"]
        new_start = new_end = None
        if payload.scheduled_date is not None:
            new_day = to_ist(payload.scheduled_date).strftime("%Y-%m-%d")
            new_slot = payload.scheduled_slot
            move = new_day != old_day or new_slot != lead["scheduled_slot"]
        if move:
            _ensure_within_advance_window(payload.scheduled_date, policy)
            await ensure_society_lead_time(self.db, lead.get("subscription_id"), payload.scheduled_date, "app" if actor_role == "customer" else "staff")
            for car in visit:
                await ensure_subscription_covers_date(self.db, car.get("subscription_id"), payload.scheduled_date)
            new_start, new_end = _resolve_slot_window(center, payload.scheduled_date, new_slot, policy)
            if _slot_cutoff_passed(new_end, policy):
                raise BadRequestException("This slot is no longer available to book — please pick another slot.")

        # -- where ---------------------------------------------------------
        current_address = await self._address_of(lead) or {}
        new_address: dict | None = None
        distance_km: float | None = None
        if payload.address_id or payload.address:
            if payload.address_id:
                saved = await self.address_repo.find_by_id(payload.address_id)
                if not saved or saved.get("owner_id") != customer_id:
                    raise NotFoundException("Address not found")
                probe = saved
            else:
                a = payload.address
                probe = {"latitude": a.latitude, "longitude": a.longitude, "pincode": (a.pincode or "").strip()}
            served_by, distance_km = await self._resolve_service_center(probe, allow_pinless=actor_role != "customer")
            if str(served_by["_id"]) != lead["service_center_id"]:
                raise BadRequestException(self.OTHER_CENTER_MESSAGE)
            if payload.address is not None:
                addresses = AddressService(self.db)
                incoming = {**payload.address.model_dump(), "pincode": (payload.address.pincode or "").strip()}
                match, has_saved = await addresses.find_same_place(customer_id, incoming)
                if match:
                    saved = match
                elif dry_run:
                    # A preview never saves the address — priced from the
                    # very fields the real save would store.
                    a = payload.address
                    saved = {
                        "_id": ObjectId(), "owner_id": customer_id, "label": "Home", "line1": a.line1.strip(), "line2": None,
                        "landmark": a.landmark, "city": a.city or "—", "state": a.state or "—",
                        "pincode": (a.pincode or "").strip() or str((center.get("location") or {}).get("pincode") or center.get("pincode") or "000000"),
                        "latitude": a.latitude, "longitude": a.longitude,
                    }
                else:
                    a = payload.address
                    made = await addresses.create(customer_id, AddressCreateRequest(
                        label="Home", line1=a.line1.strip(), landmark=a.landmark, city=a.city or "—", state=a.state or "—",
                        pincode=(a.pincode or "").strip() or str((center.get("location") or {}).get("pincode") or center.get("pincode") or "000000"),
                        latitude=a.latitude, longitude=a.longitude, is_default=not has_saved,
                    ))
                    saved = await self.address_repo.find_by_id(made["id"])
            snap = address_snapshot(saved)
            if snap != lead.get("address_snapshot") or str(saved["_id"]) != lead.get("address_id"):
                new_address = saved
        address_changed = new_address is not None

        # -- what: per car -------------------------------------------------
        plans: dict[str, dict] = {}
        for cid, car in by_id.items():
            if cid in car_edits:
                plans[cid] = await self._edit_car_plan(car, car_edits[cid], customer_id)
        paying = self._paying_index([SimpleNamespace(subscription_id=c.get("subscription_id")) for c in visit])
        # Visit terms with the NEW services (a plan car imposes none).
        terms = dict(self._NO_TERMS)
        for cid, car in by_id.items():
            if cid in plans:
                services = plans[cid]["services"]
            else:
                ids = list(car.get("service_ids") or [])
                services = [s for s in [await self.service_repo.find_by_id(i) for i in ids] if s]
            car_terms = self._car_terms(services, bool(car.get("subscription_id")))
            terms["charges_travel"] = terms["charges_travel"] or car_terms["charges_travel"]
            terms["prepaid_service"] = terms["prepaid_service"] or car_terms["prepaid_service"]
        if terms["prepaid_service"] and not lead.get("prepaid_only"):
            raise BadRequestException(
                f"{terms['prepaid_service']} is prepaid, so it can't be added by changing this booking — "
                "cancel it and book again with online payment."
            )

        reprice_travel = address_changed or bool(plans)
        target_address = new_address or current_address
        if reprice_travel and distance_km is None:
            # Straight-line distance from the center (captain travel pay and
            # the pinless fallback of the distance charge) — as at create.
            try:
                _served, distance_km = await self._resolve_service_center(target_address, allow_pinless=True)
            except BadRequestException:
                distance_km = float(lead.get("distance_km") or 0)
        travel = None
        if reprice_travel and terms["charges_travel"]:
            charge_km, charge_source = await self.charge_distance_km(center, target_address, float(distance_km or 0))
            travel = {**await self.pricing_service.travel_quote(charge_km), "source": charge_source}

        notices: list[str] = []
        dropped_coupons: list[dict] = []
        planned: dict[str, dict] = {}
        for index, car in enumerate(visit):
            cid = str(car["_id"])
            fields: dict = {}
            plan = plans.get(cid)
            old_travel = float(car.get("travel_charge") or 0)
            new_travel = old_travel
            if reprice_travel:
                new_travel = float(travel["charge"]) if (travel and index == paying) else 0.0
                fields.update({
                    "travel_charge": new_travel,
                    "travel_charge_km": travel["distance_km"] if (travel and index == paying) else None,
                    "travel_charge_source": travel["source"] if (travel and index == paying) else None,
                })
            subtotal = float(car.get("subtotal") or 0)
            discount = float(car.get("discount_amount") or 0)
            if plan is not None:
                first_time = await self._first_time_of(car, visit_ids)
                priced = await self._price_services(
                    customer_id=customer_id, services=plan["services"], quantities=plan["quantities"], combo=plan["combo"],
                    vehicle_type=plan["vehicle_type"], vehicle_id=plan["vehicle_id"], first_time_eligible=first_time,
                    subscription_id=None, coupon_code=None, scheduled_date=payload.scheduled_date or car["scheduled_date"],
                )
                subtotal, discount = float(priced["subtotal"]), 0.0
                if car.get("coupon_code"):
                    discount, why = await self._held_coupon_discount(
                        car, subtotal, plan["services"], plan["quantities"], plan["vehicle_type"], first_time,
                    )
                    if why:
                        notices.append(f"Coupon {car['coupon_code']} no longer applies to this booking ({why}) — it was removed.")
                        dropped_coupons.append(car)
                        fields["coupon_code"] = None
                discount = min(discount, subtotal)
                fields.update({
                    "vehicle_id": plan["vehicle_id"], "vehicle_type": plan["vehicle_type"], "vehicle_label": plan["vehicle_label"],
                    "vehicle_registration_number": plan["vehicle_registration_number"], "visit_line_key": plan["visit_line_key"],
                    "service_ids": plan["service_ids"], "combo_id": car.get("combo_id") if plan["combo"] else None,
                    "service_quantities": {sid: q for sid, q in plan["quantities"].items() if q > 1},
                    "subtotal": round(subtotal, 2), "discount_amount": round(discount, 2),
                    "duration_minutes": plan["duration_minutes"],
                })
            if plan is not None or (reprice_travel and abs(new_travel - old_travel) > 0.004) or (address_changed and index == 0):
                base = round(max(0.0, subtotal - discount) + new_travel, 2)
                extras = round(
                    float(car.get("wallet_due_carried") or 0) + float(car.get("cancellation_charge") or 0)
                    + float(car.get("added_services_total") or 0), 2,
                )
                total = round(base + extras, 2)
                # The captain's pay for the job as it now is — the same split
                # create_booking makes (travel pay on the visit's first car).
                services_for_split = plan["services"] if plan else [
                    s for s in [await self.service_repo.find_by_id(i) for i in (car.get("service_ids") or [])[:1]] if s
                ]
                split = await self.pricing_service.calculate_split(
                    subtotal, float(distance_km or car.get("distance_km") or 0) if index == 0 else 0.0,
                    services_for_split[0] if services_for_split else None,
                )
                fields.update({
                    "distance_km": split["distance_km"], "captain_travel_pay": split["captain_travel_pay"],
                    "captain_service_pay": split["captain_service_pay"], "captain_earning": split["captain_earning"],
                    "total_amount": total,
                    "platform_earning": round(total - float(split["captain_earning"] or 0), 2),
                })
            planned[cid] = fields

        # Group offsets follow the new durations (each car starts where the
        # one before it ends) — and an assigned car's start moves with it.
        offset = 0
        for car in visit:
            cid = str(car["_id"])
            duration = int(planned[cid].get("duration_minutes") or car.get("duration_minutes") or 60)
            if int(car.get("group_offset_minutes") or 0) != offset:
                planned[cid]["group_offset_minutes"] = offset
                if car.get("estimated_start_at") and not move:
                    shift = timedelta(minutes=offset - int(car.get("group_offset_minutes") or 0))
                    planned[cid]["estimated_start_at"] = from_stored(car["estimated_start_at"]) + shift
            offset += duration

        if address_changed:
            for cid in planned:
                planned[cid].update({"address_id": str(new_address["_id"]), "address_snapshot": address_snapshot(new_address)})
        if payload.customer_notes is not None:
            for cid in planned:
                planned[cid]["customer_notes"] = payload.customer_notes

        new_visit_total = round(sum(
            float(planned[str(c["_id"])].get("total_amount", c.get("total_amount")) or 0) for c in visit
        ), 2)
        self._ensure_expected_total(payload.expected_total, new_visit_total, sum(float(c.get("wallet_due_carried") or 0) for c in visit))

        changes = await self._edit_diff(visit, planned, move=move, old_day=old_day, new_day=new_day, new_slot=new_slot,
                                        old_address=current_address, new_address=new_address, notes=payload.customer_notes)
        if not changes:
            if dry_run:
                return self._edit_preview(lead, visit, [], notices, [])
            return await self._edit_result(lead, visit, [], notices, [])
        fields_changed = sorted({c["field"] for c in changes})
        now = now_ist()
        actor = await self.charges.actor(actor_id, actor_role)
        fingerprints = {cid: {k: car.get(k) for k in self._EDIT_FINGERPRINT} for cid, car in by_id.items()}
        mover_ids = [str(c["_id"]) for c in visit]
        released: dict[str, str] = {}

        async def _do_edit(session):
            released.clear()
            await self._touch_customer(customer_id, session)
            rows = await self._visit_rows(lead, session=session)
            now_by_id = {str(r["_id"]): r for r in rows}
            for cid, fp in fingerprints.items():
                fresh = now_by_id.get(cid)
                if fresh is None or any(fresh.get(k) != v for k, v in fp.items()):
                    raise BadRequestException("This booking just changed — refresh and try again.")
            if actor_role == "customer":
                self._ensure_customer_may_edit([r for r in rows if str(r["_id"]) in fingerprints])
            move_ctx = None
            if move:
                await self._raise_if_customer_conflict(customer_id, new_start, new_end, str(lead["_id"]), lead.get("booking_group_id"), session=session)
                await self._backfill_seats(rows, session)
                move_ctx = await self._seat_move_begin(session, rows, mover_ids, center, new_day, new_slot)
            money_results: list[dict] = []
            written: dict[str, dict] = {}
            for cid in mover_ids:
                fresh = now_by_id[cid]
                fields = dict(planned[cid])
                if move:
                    fields.update(self._reschedule_fields(
                        fresh, BookingRescheduleRequest(scheduled_date=payload.scheduled_date, scheduled_slot=new_slot), new_start, new_end,
                    ))
                    if fresh.get("status") == BookingStatus.AWAITING_PAYMENT.value:
                        # Still unpaid: it moves, but stays out of the queue.
                        fields.update({"status": BookingStatus.AWAITING_PAYMENT.value, "awaiting_assignment_since": None})
                    else:
                        _ensure_transition_allowed(fresh["status"], BookingStatus.RESCHEDULED.value)
                    if fresh.get("captain_id"):
                        released[cid] = fresh["captain_id"]
                fields.update({"customer_edited_at": now, "customer_edited_fields": fields_changed, "customer_edited_by_role": actor_role})
                if "total_amount" in fields and abs(float(fields["total_amount"]) - float(fresh.get("total_amount") or 0)) > 0.004:
                    result = await self.money.on_price_change(
                        fresh, {**fresh, **fields}, actor=actor, reason="Booking changed by the customer", session=session,
                    )
                    fields.update(result["fields"])
                    money_results.append(result)
                done = await self.repo.update_if(
                    cid, {"total_amount": fresh.get("total_amount"), "scheduled_slot": fresh["scheduled_slot"]}, fields, session=session,
                )
                if done is None:
                    raise BadRequestException("This booking just changed — refresh and try again.")
                written[cid] = done
            if move_ctx is not None and await self._seat_move_finish(session, move_ctx, mover_ids, set(written), str(center["_id"]), new_day, new_slot):
                written[mover_ids[0]] = await self.repo.find_by_id(mover_ids[0], session=session)
            if dry_run:
                # Everything above ran for real — seat reserve, the money,
                # the guarded writes — inside this transaction; raising
                # aborts it, so the preview's numbers are the save's numbers
                # and nothing stays written.
                docs = [await self.repo.find_by_id(cid, session=session) or written[cid] for cid in mover_ids]
                raise _EditDryRun(self._edit_preview(lead, docs, changes, notices, money_results))
            return written, money_results

        try:
            async with await self.db.client.start_session() as session:
                written, money_results = await session.with_transaction(_do_edit)
        except DuplicateKeyError:
            raise BadRequestException("You already have a booking for this vehicle in that slot.")
        except _EditDryRun as preview:
            return preview.result

        await self._after_edit(lead, visit, written, changes, money_results, dropped_coupons, released, actor, actor_role, move, old_day, new_day)
        return await self._edit_result(lead, list(written.values()), changes, notices, money_results)

    async def _edit_diff(
        self, visit: list[dict], planned: dict[str, dict], *, move: bool, old_day: str, new_day: str, new_slot: str,
        old_address: dict, new_address: dict | None, notes: str | None = None,
    ) -> list[dict]:
        """The field-by-field diff of an edit: [{booking_id, booking_number,
        field, from, to}] in words a person reads (names, 12-hour times)."""
        out: list[dict] = []

        def row(car: dict, field: str, before, after) -> None:
            out.append({"booking_id": str(car["_id"]), "booking_number": car.get("booking_number"), "field": field, "from": before, "to": after})

        lead = visit[0]
        if move:
            if old_day != new_day:
                row(lead, "date", datetime.strptime(old_day, "%Y-%m-%d").strftime("%d %b %Y"), datetime.strptime(new_day, "%Y-%m-%d").strftime("%d %b %Y"))
            if lead["scheduled_slot"] != new_slot:
                row(lead, "slot", format_slot_12h(lead["scheduled_slot"]), format_slot_12h(new_slot))
        if new_address is not None:
            row(lead, "address", self._short_area(old_address), self._short_area(new_address))
        if notes is not None and (notes or "") != (lead.get("customer_notes") or ""):
            row(lead, "notes", lead.get("customer_notes") or "", notes)
        for car in visit:
            f = planned[str(car["_id"])]
            if "vehicle_type" in f and (f["vehicle_type"] != car.get("vehicle_type") or f.get("vehicle_id") != car.get("vehicle_id")):
                row(car, "vehicle", car.get("vehicle_label") or "Vehicle", f.get("vehicle_label") or "Vehicle")
            if "service_ids" in f and (
                f["service_ids"] != list(car.get("service_ids") or [])
                or f.get("service_quantities", {}) != dict(car.get("service_quantities") or {})
            ):
                row(car, "services", await self._names_with_qty(car.get("service_ids"), car.get("service_quantities")),
                    await self._names_with_qty(f["service_ids"], f.get("service_quantities")))
            if "total_amount" in f and abs(float(f["total_amount"]) - float(car.get("total_amount") or 0)) > 0.004:
                row(car, "total", float(car.get("total_amount") or 0), float(f["total_amount"]))
        return out

    async def _names_with_qty(self, ids, quantities) -> str:
        names = []
        for sid in ids or []:
            svc = await self.service_repo.find_by_id(sid)
            qty = int((quantities or {}).get(sid, 1) or 1)
            names.append(((svc or {}).get("name") or "Service") + (f" ×{qty}" if qty > 1 else ""))
        return ", ".join(names)

    @staticmethod
    def _diff_line(changes: list[dict]) -> str:
        """"Date 10 Oct 2026 → 11 Oct 2026 · Services Star Wash → Deep Clean"."""
        parts = []
        for c in changes:
            before, after = c["from"], c["to"]
            if c["field"] == "total":
                before, after = f"₹{float(before):g}", f"₹{float(after):g}"
            parts.append(f"{c['field'].capitalize()} {before or '—'} → {after or '—'}")
        return " · ".join(parts)

    async def _after_edit(
        self, lead: dict, visit: list[dict], written: dict[str, dict], changes: list[dict], money_results: list[dict],
        dropped_coupons: list[dict], released: dict[str, str], actor: dict, actor_role: str, move: bool, old_day: str, new_day: str,
    ) -> None:
        """Everything after an edit committed — each step isolated
        (FAIL-04): coupon uses dropped, money side effects (open links for
        the old amount voided, wallet credit announced), history rows with
        the diff, the audit row, the managers' in-app flag (never
        WhatsApp), the captain told, the customer's WhatsApp."""
        for car in dropped_coupons:
            await self._post_commit("coupon release", car, self.coupon_service.reverse_usage(car["coupon_code"], car["customer_id"], str(car["_id"])))
        if money_results:
            await self._post_commit("price-change money", lead, self.money.after_price_change(money_results, "Booking changed by the customer"))
        line = self._diff_line(changes)
        by_car: dict[str, list[dict]] = {}
        for c in changes:
            by_car.setdefault(c["booking_id"], []).append(c)
        for cid, doc in written.items():
            mine = by_car.get(cid) or [c for c in changes if c["field"] in ("date", "slot", "address", "notes")]
            await self._post_commit("history row", doc, self._record_history(
                cid, BookingStatus(doc["status"]), actor.get("id"), f"Changed by the {actor_role}: {self._diff_line(mine) or line}", changes=mine,
            ))
        from app.services.audit_service import AuditService

        target = lead.get("booking_group_id") or str(lead["_id"])
        await self._post_commit("edit audit", lead, AuditService(self.db).log_action(
            actor.get("id") or "system", actor_role, "CUSTOMER_EDIT_BOOKING", "bookings", target,
            {"changes": changes, "booking_numbers": [c.get("booking_number") for c in visit],
             "wallet_credit": round(sum(float(r.get("wallet_credit") or 0) for r in money_results), 2)},
            service_center_id=lead.get("service_center_id"),
        ))
        cars = sorted(written.values(), key=lambda c: int(c.get("group_offset_minutes") or 0))
        numbers = self._visit_numbers(cars) if len(cars) > 1 else str(cars[0].get("booking_number") or "")

        async def _tell_managers() -> None:
            center = await self.center_repo.find_by_id(lead["service_center_id"])
            for manager_id in await self._manager_recipients(lead.get("service_center_id"), (center or {}).get("manager_id")):
                await self.notifications.notify(
                    manager_id, "Customer edited a booking", f"Customer edited {numbers} — {line}",
                    NotificationType.BOOKING, str(cars[0]["_id"]), send_whatsapp=False, background=True,
                )

        await self._post_commit("manager edit notice", lead, _tell_managers())

        async def _tell_captains() -> None:
            for captain_id in set(released.values()):
                await self.notifications.notify(
                    captain_id, "Booking rescheduled — no longer yours",
                    f"{numbers} was moved to a new time by the customer and needs a new captain assignment.",
                    NotificationType.BOOKING, str(cars[0]["_id"]), background=True,
                )
                await ws_manager.broadcast(f"user:{captain_id}", {"type": "changed", "channel": f"user:{captain_id}", "booking_id": str(cars[0]["_id"])})
            for captain_id in {c.get("captain_id") for c in cars if c.get("captain_id")} - set(released.values()):
                await self.notifications.notify(
                    captain_id, "Booking changed", f"The customer changed {numbers}: {line}",
                    NotificationType.BOOKING, str(cars[0]["_id"]), background=True, send_whatsapp=False,
                )

        await self._post_commit("captain edit notice", lead, _tell_captains())

        async def _tell_customer() -> None:
            wa_services, wa_reference, wa_date, wa_slot, wa_vehicle, _code = await self._wa_details(cars[0], cars)
            total = round(sum(float(c.get("total_amount") or 0) for c in cars), 2)
            await self.notifications.notify(
                lead["customer_id"], "Booking updated",
                f"{numbers} is updated: {wa_services} for your {wa_vehicle} on {wa_date}, {wa_slot}. New total ₹{total:g}.",
                NotificationType.BOOKING, str(cars[0]["_id"]),
                wa_event="booking_edited",
                # NOTIFY's template: the amount as a plain number (the body has the ₹).
                wa_params=[wa_services, wa_reference, wa_date, wa_slot, wa_vehicle, f"{total:g}"],
                background=True,
            )

        await self._post_commit("customer edit message", lead, _tell_customer())
        for doc in cars:
            await self._broadcast_booking_changed(doc)
        if move:
            await self._broadcast_slots_changed(lead["service_center_id"], old_day)
            await self._broadcast_slots_changed(lead["service_center_id"], new_day)

    async def _edit_result(self, lead: dict, docs: list[dict], changes: list[dict], notices: list[str], money_results: list[dict]) -> dict:
        fresh = [d for d in [await self.repo.find_by_id(str(c["_id"])) for c in (docs or [lead])] if d]
        enriched = [_redact_financials(b, "customer") for b in await self._enrich_bookings(fresh)]
        return {
            "booking_group_id": lead.get("booking_group_id"),
            "bookings": enriched,
            **self._edit_numbers(fresh, changes, notices, money_results),
        }

    @staticmethod
    def _edit_numbers(docs: list[dict], changes: list[dict], notices: list[str], money_results: list[dict]) -> dict:
        """The money an edit ends at — one builder for the real save and
        its dry run, so the two can't drift."""
        return {
            "changes": changes,
            "notices": notices,
            "total_amount": round(sum(float(b.get("total_amount") or 0) for b in docs), 2),
            "amount_due": round(sum(bm.amount_due(b) for b in docs), 2),
            "wallet_credit": round(sum(float(r.get("wallet_credit") or 0) for r in money_results), 2),
            # The visit's distance charge after the edit (it rides on the
            # visit's paying car — per car below, as stored).
            "travel_charge": round(sum(float(b.get("travel_charge") or 0) for b in docs), 2),
            "cars": [
                {"booking_id": str(b["_id"]), "booking_number": b.get("booking_number"),
                 "total_amount": round(float(b.get("total_amount") or 0), 2), "amount_due": bm.amount_due(b),
                 "travel_charge": round(float(b.get("travel_charge") or 0), 2)}
                for b in docs
            ],
        }

    def _edit_preview(self, lead: dict, docs: list[dict], changes: list[dict], notices: list[str], money_results: list[dict]) -> dict:
        """What PATCH …{dry_run: true} answers: the numbers the save would
        produce, nothing written."""
        return {"dry_run": True, "booking_group_id": lead.get("booking_group_id"),
                **self._edit_numbers(docs or [lead], changes, notices, money_results)}

    # -- On-site add-ons (spec 1.4) -----------------------------------------
    #
    # POST /bookings/{id}/add-services. The assigned captain adds services
    # for THIS car once he has verified his arrival, until the visit is
    # fully paid; the center's manager (or an admin) any time the booking
    # isn't cancelled — including after a prepaid job is done, when the
    # added amount simply becomes due (collected by QR/cash, or paid online
    # by the customer). Priced at the regular price for the car's type —
    # the same _resolve_price quote/create use; no coupon, no plan waiver,
    # no first-wash price (an extra at the door is never a first wash).
    # The captain's earning is UNCHANGED (founder: extra pay is a future
    # feature) — the whole amount is platform money.

    _ADDON_CAPTAIN_STATES = frozenset({BookingStatus.CAPTAIN_ON_THE_WAY.value, BookingStatus.SERVICE_STARTED.value})

    async def _ensure_may_add_services(self, booking: dict, visit: list[dict], actor_id: str, actor_role: str, actor_center_id: str | None) -> None:
        if actor_role == "captain":
            await self._ensure_captain_job(booking, actor_id)
            status = booking.get("status")
            if status in self._ADDON_CAPTAIN_STATES:
                if not booking.get("vehicle_verified"):
                    raise BadRequestException("Verify your arrival with the customer's code first — then you can add services.")
                return
            if status == BookingStatus.COMPLETED.value:
                if any(bm.owes(c) for c in visit):
                    return
                raise BadRequestException("This visit is fully paid — ask your manager to add anything more.")
            raise BadRequestException("You can add services once you've reached the customer and verified the code.")
        if actor_role == "manager":
            ensure_own_center(actor_role, actor_center_id, booking["service_center_id"])
        elif actor_role != "admin":
            raise ForbiddenException("You can't add services to this booking")
        if booking.get("status") == BookingStatus.CANCELLED.value:
            raise BadRequestException("This booking was cancelled — nothing can be added to it.")

    @staticmethod
    def _services_fingerprint(doc: dict) -> tuple:
        """What a car's service mix was validated against."""
        return (
            tuple(doc.get("service_ids") or []), doc.get("combo_id"),
            tuple(sorted((doc.get("service_quantities") or {}).items())),
            tuple((e.get("service_id"), int(e.get("qty") or 1)) for e in doc.get("added_services") or []),
        )

    async def add_services_on_site(
        self, booking_id: str, service_ids: list[str], quantities: dict[str, int], actor_id: str, actor_role: str,
        actor_center_id: str | None = None, *, note: str | None = None,
    ) -> dict:
        """Add services/add-ons to one car (see the section comment). One
        transaction: the car's total, added_services[], duration (later cars
        of the visit start that much later) and its money
        (MoneyService.on_price_change — what was already paid stays paid,
        the added amount becomes due) — guarded on the total it was computed
        from, so an online payment or another add-on landing at the same
        moment makes it retry cleanly, never double-add.

        Returns {booking (as the caller may see it), added [entries],
        added_total, amount_due, visit_amount_due}."""
        booking = await self.repo.find_by_id(booking_id)
        if not booking:
            raise NotFoundException("Booking not found")
        visit = await self._visit_cars(booking)
        await self._ensure_may_add_services(booking, visit, actor_id, actor_role, actor_center_id)
        vehicle_type = booking.get("vehicle_type")
        if not vehicle_type and booking.get("vehicle_id"):
            vehicle_type = (await self.vehicle_repo.find_by_id(booking["vehicle_id"]) or {}).get("vehicle_type")

        new_services: list[dict] = []
        for sid in service_ids:
            svc = await self.service_repo.find_by_id(sid)
            if not svc or not svc.get("is_active"):
                raise NotFoundException(f"Service not found or inactive: {sid}")
            new_services.append(svc)
        # The same mix rules as a booking (car vs bike, one pick per variant,
        # per-unit quantities, bike polish ≤ bikes) on the car as it will be
        # — what it was booked with, anything added before, plus this.
        existing_ids = list(booking.get("service_ids") or [])
        if booking.get("combo_id"):
            combo = await self.combo_repo.find_by_id(booking["combo_id"])
            existing_ids = list((combo or {}).get("service_ids") or existing_ids)
        merged_qty: dict[str, int] = {sid: int((booking.get("service_quantities") or {}).get(sid, 1) or 1) for sid in existing_ids}
        for entry in booking.get("added_services") or []:
            merged_qty[entry["service_id"]] = merged_qty.get(entry["service_id"], 0) + int(entry.get("qty") or 1)
        wanted: dict[str, int] = {}
        for svc in new_services:
            sid = str(svc["_id"])
            qty = int(quantities.get(sid, 1) or 1)
            if qty < 1 or qty > MAX_ADDON_QTY:
                raise BadRequestException(f"Invalid quantity for {svc['name']}.")
            wanted[sid] = qty
            merged_qty[sid] = merged_qty.get(sid, 0) + qty
        self._ensure_offered_for_type(new_services, vehicle_type, bike_type=await self._is_bike_type(vehicle_type))
        merged_services = [s for s in [await self.service_repo.find_by_id(i) for i in merged_qty] if s]
        try:
            await self._validate_service_mix(merged_services, vehicle_type, merged_qty)
        except BadRequestException as exc:
            if any(merged_qty[str(s["_id"])] > 1 and str(s["_id"]) in existing_ids for s in new_services):
                raise BadRequestException(f"{exc.message} It's already on this booking.")
            raise

        actor = await self.charges.actor(actor_id, actor_role)
        now = now_ist()
        entries = []
        for svc in new_services:
            sid = str(svc["_id"])
            unit = float(self._resolve_price(svc, vehicle_type, False))
            entries.append({
                "service_id": sid, "name": svc.get("name"), "qty": wanted[sid], "unit_price": round(unit, 2),
                "amount": round(unit * wanted[sid], 2), "duration_minutes": int(svc.get("duration_minutes", 30)) * wanted[sid],
                "is_addon": bool(svc.get("is_addon")), "by": actor_id, "by_name": actor.get("name"), "role": actor_role,
                "at": now, "stage": booking.get("status"), "note": note,
            })
        added_total = round(sum(e["amount"] for e in entries), 2)
        added_minutes = sum(e["duration_minutes"] for e in entries)

        async def _do_add(session):
            await self._touch_customer(booking["customer_id"], session)
            fresh = await self.repo.find_by_id(booking_id, session=session)
            if not fresh:
                raise NotFoundException("Booking not found")
            rows = await self._visit_rows(fresh, session=session)
            # The rules again, on the state this transaction writes over.
            await self._ensure_may_add_services(fresh, rows or [fresh], actor_id, actor_role, actor_center_id)
            # The service mix was checked on `booking`: a concurrent add (a
            # double tap, a retry) or edit committed since would make this
            # transaction's retry add the same one-per-car add-on twice
            # (security review 2026-10-07) — refuse instead.
            if self._services_fingerprint(fresh) != self._services_fingerprint(booking):
                raise BadRequestException("This booking just changed — refresh and try again.")
            new_total = round(float(fresh.get("total_amount") or 0) + added_total, 2)
            fields = {
                "total_amount": new_total,
                # Captain earning unchanged: the whole extra is platform money.
                "platform_earning": round(float(fresh.get("platform_earning") or 0) + added_total, 2),
                "added_services_total": round(float(fresh.get("added_services_total") or 0) + added_total, 2),
                "duration_minutes": int(fresh.get("duration_minutes") or 60) + added_minutes,
                "updated_at": datetime.now(timezone.utc),
            }
            money = await self.money.on_price_change(
                fresh, {**fresh, **fields}, actor=actor, reason="Services added on site", session=session,
            )
            fields.update(money["fields"])
            done = await self.repo.collection.find_one_and_update(
                {"_id": fresh["_id"], "total_amount": fresh.get("total_amount"), "status": fresh.get("status")},
                {"$set": fields, "$push": {"added_services": {"$each": entries}}},
                session=session, return_document=ReturnDocument.AFTER,
            )
            if done is None:
                raise BadRequestException("This booking just changed — refresh and try again.")
            # Later cars of the visit start that much later (the captain's
            # blocked time keeps covering the whole visit).
            for car in rows:
                if int(car.get("group_offset_minutes") or 0) <= int(fresh.get("group_offset_minutes") or 0) or str(car["_id"]) == booking_id:
                    continue
                if car.get("status") not in {BookingStatus.PENDING.value, BookingStatus.ASSIGNED.value, BookingStatus.RESCHEDULED.value,
                                             BookingStatus.CAPTAIN_ON_THE_WAY.value, BookingStatus.AWAITING_PAYMENT.value}:
                    continue
                shift: dict = {"group_offset_minutes": int(car.get("group_offset_minutes") or 0) + added_minutes}
                if car.get("estimated_start_at"):
                    shift["estimated_start_at"] = from_stored(car["estimated_start_at"]) + timedelta(minutes=added_minutes)
                await self.repo.collection.update_one({"_id": car["_id"]}, {"$set": shift}, session=session)
            return done, money

        async with await self.db.client.start_session() as session:
            updated, money = await session.with_transaction(_do_add)

        await self._post_commit("price-change money", updated, self.money.after_price_change([money], "Services added on site"))
        what = ", ".join(e["name"] + (f" ×{e['qty']}" if e["qty"] > 1 else "") for e in entries)
        changes = [{"booking_id": booking_id, "booking_number": updated.get("booking_number"), "field": "added_services",
                    "from": None, "to": what}, {"booking_id": booking_id, "booking_number": updated.get("booking_number"),
                    "field": "total", "from": float(booking.get("total_amount") or 0), "to": float(updated.get("total_amount") or 0)}]
        who = f"{'Captain' if actor_role == 'captain' else actor_role.title()} {actor.get('name') or ''}".strip()
        after_wash = " after the wash" if booking.get("status") == BookingStatus.COMPLETED.value else ""
        line = f"{who} added {what}{after_wash} — ₹{added_total:g} (new total ₹{float(updated.get('total_amount') or 0):g})."
        await self._post_commit("history row", updated, self._record_history(
            booking_id, BookingStatus(updated["status"]), actor_id, line, changes=changes,
        ))
        from app.services.audit_service import AuditService

        await self._post_commit("add-on audit", updated, AuditService(self.db).log_action(
            actor_id, actor_role, "ADD_SERVICES_ON_SITE", "bookings", booking_id,
            {"added": [{k: e[k] for k in ("service_id", "name", "qty", "unit_price", "amount")} for e in entries],
             "added_total": added_total, "before_total": float(booking.get("total_amount") or 0),
             "after_total": float(updated.get("total_amount") or 0), "amount_due": money.get("amount_due"), "stage": booking.get("status")},
            service_center_id=booking.get("service_center_id"),
        ))

        async def _tell() -> None:
            center = await self.center_repo.find_by_id(booking["service_center_id"])
            for manager_id in await self._manager_recipients(booking.get("service_center_id"), (center or {}).get("manager_id")):
                if manager_id == actor_id:
                    continue
                await self.notifications.notify(
                    manager_id, f"Services added — {updated.get('booking_number')}", line,
                    NotificationType.BOOKING, booking_id, send_whatsapp=False, background=True,
                )
            await self.notifications.notify(
                booking["customer_id"], "Added to your booking",
                f"{what} added to {updated.get('booking_number')} — ₹{added_total:g}. New total ₹{float(updated.get('total_amount') or 0):g}.",
                NotificationType.BOOKING, booking_id, send_whatsapp=False, background=True,
            )

        await self._post_commit("add-on notices", updated, _tell())
        await self._broadcast_booking_changed(updated)
        # The booking exactly as GET /bookings/{id} shows it (names, money
        # view — amount_due, amount_paid, wallet_applied …), redacted for the
        # caller's role like every other read.
        view = _redact_financials((await self._enrich_bookings([updated]))[0], actor_role)
        visit_now = await self._visit_cars(updated)
        next_job_conflict = None
        try:
            next_job_conflict = await self._next_job_conflict_after_growth(updated, visit_now, added_minutes, actor_id)
        except Exception:  # noqa: BLE001 — the add-on stands; this is advice
            logger.exception("Could not check %s's captain schedule after an add-on", updated.get("booking_number"))
        return {
            "booking": view,
            "added": [serialize_doc(e) for e in entries],
            "added_total": added_total,
            "amount_due": bm.amount_due(updated),
            "visit_amount_due": round(sum(bm.amount_due(c) for c in visit_now), 2),
            "next_job_conflict": next_job_conflict,
        }

    async def _next_job_conflict_after_growth(
        self, booking: dict, visit: list[dict], grew_by: int, actor_id: str | None,
    ) -> dict | None:
        """An on-site add-on made this captain's job longer. Never blocks the
        add-on: when the visit's new end (plus the travel buffer, the same
        gap assignment requires) runs into the captain's NEXT assigned job,
        the center's managers get an in-app heads-up and the caller gets
        {booking_id, booking_number, starts_at, late_by_minutes,
        grew_by_minutes}; otherwise None."""
        captain_id = booking.get("captain_id")
        live = {BookingStatus.ASSIGNED.value, BookingStatus.CAPTAIN_ON_THE_WAY.value, BookingStatus.SERVICE_STARTED.value}
        if not captain_id or grew_by <= 0 or booking.get("status") not in live:
            return None
        mine = [c for c in visit if c.get("captain_id") == captain_id and c.get("status") in live] or [booking]
        windows = [_captain_booking_window(c) for c in mine]
        start, end = min(w[0] for w in windows), max(w[1] for w in windows)
        if booking.get("status") != BookingStatus.ASSIGNED.value:
            # Already on the job: whatever the plan said, the added work is
            # still ahead of him.
            end = max(end, now_ist() + timedelta(minutes=grew_by))
        visit_ids = {str(c["_id"]) for c in mine} | {str(booking["_id"])}
        group = booking.get("booking_group_id")
        later = []
        for other in await self.repo.find_active_for_captain(captain_id):
            if str(other["_id"]) in visit_ids or (group and other.get("booking_group_id") == group):
                continue
            other_start, _other_end = _captain_booking_window(other)
            if other_start >= start:
                later.append((other_start, other))
        if not later:
            return None
        next_start, nxt = min(later, key=lambda pair: pair[0])
        policy = await self.policy_service.get_policy()
        buffer = timedelta(minutes=int(policy.get("captain_travel_buffer_minutes") or 0))
        if end + buffer <= next_start:
            return None
        late_by = max(1, int(round((end + buffer - next_start).total_seconds() / 60)))
        captain = await self.user_repo.find_by_id(captain_id)
        name = (captain or {}).get("full_name") or "The captain"
        conflict = {
            "booking_id": str(nxt["_id"]),
            "booking_number": nxt.get("booking_number"),
            "starts_at": next_start.isoformat(),
            "late_by_minutes": late_by,
            "grew_by_minutes": int(grew_by),
            "captain_id": captain_id,
            "captain_name": name,
        }
        message = (
            f"Captain {name}'s next job {nxt.get('booking_number')} may start late — "
            f"{booking.get('booking_number')} grew by {int(grew_by)} min."
        )
        center = await self.center_repo.find_by_id(booking["service_center_id"])
        # Every manager of the center — the one who added it too: the add-on
        # screen doesn't show the captain's next job, the bell does.
        for manager_id in await self._manager_recipients(booking.get("service_center_id"), (center or {}).get("manager_id")):
            await self._post_commit("next-job warning", booking, self.notifications.notify(
                manager_id, f"Next job may start late — {nxt.get('booking_number')}", message,
                NotificationType.BOOKING, str(nxt["_id"]), send_whatsapp=False, background=True,
            ))
        return conflict

    # -- Tips ----------------------------------------------------------
    # A tip on a job the MANAGER did (Log A Done Job): what the customer
    # handed over on top of the bill. Founder: it counts — it's added to the
    # job's total_amount and (a manager-done job's earning being its total)
    # platform_earning, so revenue and the cash/online ledgers include it.
    # One tip per VISIT, kept on a single car so it's never counted twice.
    # MONEY-2: the tip has its OWN method (`tip_method`, cash | online,
    # default cash), independent of how the job was paid — it is money
    # received in THAT bucket (paid_cash / paid_online). A tip saved before
    # the field existed sits in the job's payment-method bucket (the old
    # rule) until backfill_booking_money moves it to cash.
    @staticmethod
    def _tip_bucket(car: dict) -> str:
        """Where this car's current tip is counted: paid_cash / paid_online."""
        method = car.get("tip_method")
        if method is None:
            method = "cash" if car.get("payment_method") == PaymentMethod.CASH.value else "online"
        return "paid_online" if method == "online" else "paid_cash"

    async def _record_tip(
        self, primary_id: str, visit_ids: list[str], amount: float, actor_id: str, tip_method: str = "cash",
    ) -> dict | None:
        from app.services.money_service import received_by_method

        tip_method = "online" if tip_method == "online" else "cash"
        now = now_ist()
        updated = None
        for vid in dict.fromkeys([*visit_ids, primary_id]):
            car = await self.repo.find_by_id(vid)
            if not car:
                continue
            old = float(car.get("tip_amount") or 0)
            target = float(amount) if vid == primary_id else 0.0
            new_method = tip_method if target > 0 else None
            if old == target and vid != primary_id:
                continue
            delta = round(target - old, 2)
            inc = {"total_amount": delta}
            fields = {"tip_amount": target, "tip_method": new_method, "tip_updated_by": actor_id, "tip_updated_at": now, "updated_at": now}
            guard = {"_id": car["_id"], "tip_amount": car.get("tip_amount"), "tip_method": car.get("tip_method")}
            if car.get("completed_by_role") == "manager":
                inc["platform_earning"] = delta
                # The tip was handed over with the bill: it is money received
                # — in the bucket of ITS method, not the job's.
                if car.get("amount_paid") is not None:
                    inc["amount_paid"] = delta
                    cash, online = received_by_method(car)
                    if car.get("paid_cash") is None and car.get("paid_online") is None:
                        # Written before the per-method fields: all it
                        # received sits under its payment method.
                        received = bm.amount_paid_of(car)
                        cash, online = (received, 0.0) if car.get("payment_method") == PaymentMethod.CASH.value else (0.0, received)
                    buckets = {"paid_cash": cash, "paid_online": online}
                    buckets[self._tip_bucket(car)] = round(buckets[self._tip_bucket(car)] - old, 2)
                    buckets["paid_online" if tip_method == "online" else "paid_cash"] += target
                    fields.update({k: max(0.0, round(v, 2)) for k, v in buckets.items()})
                    # Guarded on the buckets we read: a payment landing in
                    # between makes this edit retry, never lose its money.
                    guard.update({"paid_cash": car.get("paid_cash"), "paid_online": car.get("paid_online")})
            # Guarded on the tip we read: two quick edits can't both add
            # their difference to the total.
            result = await self.repo.collection.find_one_and_update(
                guard, {"$set": fields, "$inc": inc}, return_document=ReturnDocument.AFTER,
            )
            if result is None:
                raise BadRequestException("This job's tip just changed — refresh and try again.")
            if vid == primary_id:
                updated = result
        return updated

    async def set_tip(
        self, booking_id: str, amount: float, actor_id: str, actor_role: str, actor_center_id: str | None, tip_method: str = "cash",
    ) -> dict:
        """Add or correct the tip on a job the manager did (0 removes it),
        and how it was handed over (`tip_method`, cash by default). Captain
        jobs never carry one — that side has nothing to do with tips."""
        booking = await self.repo.find_by_id(booking_id)
        if not booking:
            raise NotFoundException("Booking not found")
        if actor_role == "manager":
            ensure_own_center(actor_role, actor_center_id, booking["service_center_id"])
        if booking["status"] != BookingStatus.COMPLETED.value or booking.get("completed_by_role") != "manager":
            raise BadRequestException("Tips are recorded only on jobs done by the manager.")
        cars = await self._visit_cars(booking)
        # The visit's tip stays on whichever car already holds it.
        holder = next((c for c in cars if float(c.get("tip_amount") or 0) > 0), None)
        primary = str(holder["_id"]) if holder else booking_id
        updated = await self._record_tip(primary, [str(c["_id"]) for c in cars], amount, actor_id, tip_method)
        await self._broadcast_booking_changed(updated or booking)
        return serialize_doc(updated) if updated else serialize_doc(booking)

    async def update_details(
        self, booking_id: str, payload: BookingUpdateDetailsRequest, actor_role: str, actor_center_id: str | None,
    ) -> dict:
        """Manager/admin correcting notes or the alternate contact — pure
        metadata, never price/capacity/assignment (see the schema's own
        docstring for why those stay out of scope), so unlike
        reschedule_booking this never touches slot capacity or status and
        needs no transaction. Locked once the booking is done — "editing"
        a completed job's paperwork isn't a real edit, it's rewriting
        history."""
        booking = await self.repo.find_by_id(booking_id)
        if not booking:
            raise NotFoundException("Booking not found")
        if actor_role == "manager":
            ensure_own_center(actor_role, actor_center_id, booking["service_center_id"])
        if booking["status"] in {BookingStatus.COMPLETED.value, BookingStatus.CANCELLED.value}:
            raise BadRequestException("This booking is already closed and can no longer be edited")

        update_data = payload.model_dump(exclude_unset=True)
        if not update_data:
            return serialize_doc(booking)

        # customer_notes/alternate_contact are per-VISIT, denormalized onto
        # every car at creation (create_booking_group passes the SAME
        # values to each) — editing only the clicked car would silently
        # leave its siblings out of sync with what's now "the" note for
        # the visit. A car already completed is left alone (its own
        # paperwork is locked, same reasoning as the guard above); every
        # other live car on the visit gets the same update.
        cars = await self._visit_cars(booking)
        updated = None
        for car in cars:
            if car["status"] == BookingStatus.COMPLETED.value:
                continue
            result = await self.repo.update_by_id(str(car["_id"]), update_data)
            await self._broadcast_booking_changed(result or car)
            if str(car["_id"]) == booking_id:
                updated = result
        return serialize_doc(updated) if updated else serialize_doc(booking)

    # -- Recycle bin (admin only) --------------------------------------
    # Statuses where a booking actually holds a slot-capacity seat —
    # mirrors app/scripts/delete_booking.py's own _ACTIVE_SEAT_STATUSES: a
    # manager-logged job never took a seat, and a cancelled one already
    # gave it back.
    # An unpaid booking holds its seat too (that's the point of parking it)
    # — leaving it out leaked the seat of every deleted awaiting_payment one.
    _SEAT_HOLDING_STATUSES = {
        BookingStatus.AWAITING_PAYMENT.value,
        BookingStatus.PENDING.value, BookingStatus.ASSIGNED.value, BookingStatus.CAPTAIN_ON_THE_WAY.value,
        BookingStatus.SERVICE_STARTED.value, BookingStatus.RESCHEDULED.value, BookingStatus.COMPLETED.value,
    }

    async def soft_delete_booking(self, booking_id: str, actor_id: str) -> dict:
        """Admin-only "delete" — moves this booking, and every other live
        car on its visit (_visit_cars), into the recycle bin. Reversible
        for 30 days (see main.py's purge sweep), so unlike
        permanently_delete_booking this is NEVER blocked by attached
        captain-wallet money, a paid online payment, or a complaint — it's
        the irreversible step that gates on those, not this one. Still
        hands back what each car was holding (slot seat, subscription
        consumption, coupon use) immediately, the same way a hard delete
        already does, since a "deleted" booking must stop counting against
        capacity/plan-balance/coupon-limit right away, not just disappear
        from the UI."""
        booking = await self.repo.find_by_id(booking_id)
        if not booking:
            raise NotFoundException("Booking not found")
        cars = await self._visit_cars(booking)
        if not any(str(c["_id"]) == booking_id for c in cars):
            cars.append(booking)  # a cancelled car isn't "on" its visit any more, but it IS what was deleted
        car_ids = [str(c["_id"]) for c in cars]

        had_wallet = await self.db.wallet_transactions.count_documents({"booking_id": {"$in": car_ids}}) > 0
        had_paid_payment = await self.db.payment_orders.count_documents(
            {"$or": [{"booking_id": {"$in": car_ids}}, {"booking_ids": {"$in": car_ids}}], "status": {"$in": ["paid", "paid_attention"]}}
        ) > 0
        had_complaints = await self.db.complaints.count_documents({"booking_id": {"$in": car_ids}}) > 0
        # Informational only (shown as a warning chip in the recycle-bin
        # UI) — never blocks the soft delete itself, see the docstring.
        deleted_flags = {"had_captain_earning": had_wallet, "had_paid_online_payment": had_paid_payment, "had_complaints": had_complaints}

        target_ids = [c["_id"] for c in cars]

        async def _do_delete(session):
            # Serialized with every other change of this customer's bookings
            # (a cancel racing this delete used to hand the seat back twice —
            # audit BOOK-02), and ONE transaction for the whole visit.
            await self._touch_customer(booking["customer_id"], session)
            rows = await self._visit_rows(booking, session=session, include_cancelled=True)
            await self._backfill_seats(rows, session)
            claimed: list[dict] = []
            for car_oid in target_ids:
                # Atomic claim — only a still-live doc flips; a concurrent
                # duplicate delete gets None and skips every side effect.
                result = await self.repo.update_if(
                    str(car_oid), {}, {"is_deleted": True, "deleted_at": now_ist(), "updated_by": actor_id, "deleted_flags": deleted_flags},
                    session=session,
                )
                if result is not None:
                    claimed.append(result)
            # The seat goes back only from the car that RECORDS holding it
            # (a done-by-the-manager job included — audit BOOK-06), after
            # every car is marked deleted so it can't pass to one of them.
            for result in claimed:
                await self._release_seat(result, session)
                # Late-cancellation charges it carried go back on the
                # customer's account (unless it was done and paid).
                await self.charges.release_for_booking(
                    session, result, actor={"id": actor_id, "role": "admin", "name": None}, reason="deleted",
                )
            # Customer-wallet money on a deleted booking that was never paid
            # (spec 1.1): credit spent on it comes back, a previous balance it
            # carried goes back onto the wallet's open debt. (Money actually
            # PAID for a deleted booking stays on the admin refund list.)
            wallet_rows = [
                r for r in claimed
                if r.get("status") not in {BookingStatus.COMPLETED.value, BookingStatus.CANCELLED.value}
                and bm.amount_paid_of(r) <= bm.EPSILON
                and (float(r.get("wallet_applied") or 0) > 0 or float(r.get("wallet_due_carried") or 0) > 0)
            ]
            if wallet_rows:
                await self.money.on_booking_cancelled(
                    [r["_id"] for r in wallet_rows], charge_amount=0,
                    actor={"id": actor_id, "role": "admin", "name": None}, session=session, reason="booking deleted",
                )
                await self.repo.collection.update_many(
                    {"_id": {"$in": [r["_id"] for r in wallet_rows]}}, {"$set": {"delete_money_settled": True}}, session=session,
                )
            return claimed

        async with await self.db.client.start_session() as session:
            claimed_cars = await session.with_transaction(_do_delete)
        for car in claimed_cars:
            car_id = str(car["_id"])
            # Once only: a CANCELLED booking already handed its wash back.
            if car.get("subscription_id") and car.get("subscription_consumption") and await self._claim_consumption_refund(car_id):
                await UserSubscriptionService(self.db).restore_consumption(car["subscription_id"], car["subscription_consumption"])
            if car.get("coupon_code"):
                await CouponService(self.db).reverse_usage(car["coupon_code"], car["customer_id"], car_id)
            await self._broadcast_booking_changed(car)
        if claimed_cars:
            await self._broadcast_slots_changed(booking["service_center_id"], self._seat_key_of(booking)["date"])
        cars = claimed_cars
        car_ids = [str(c["_id"]) for c in cars]
        await self._void_payment_links(car_ids, "Booking deleted")
        # Paid online for a job that now won't happen: on the refund list.
        # A done job was delivered (nothing to refund); a cancelled one was
        # flagged by its cancel already (flag_refund_due records each once).
        await self._flag_refunds_due([c for c in cars if c.get("status") != BookingStatus.COMPLETED.value], "Booking deleted")

        # Same courtesy cancel_booking already extends to the customer and
        # any assigned captain — from their side, "deleted" and
        # "cancelled" look identical: the job simply isn't happening.
        # Without this, a captain already on the way to a car that just
        # got deleted from under them had no way to find out except
        # noticing it silently vanished from their job list. Skipped for
        # an already-completed/cancelled visit — nothing live to warn
        # anyone about, this is just admin record-keeping on history.
        live_cars = [c for c in cars if c["status"] not in {BookingStatus.COMPLETED.value, BookingStatus.CANCELLED.value}]
        if live_cars:
            lead = live_cars[0]
            label = await self._visit_label(live_cars)
            reference = self._visit_numbers(live_cars) if len(live_cars) > 1 else str(lead.get("booking_number") or "")
            wa_services, wa_reference, wa_date, wa_slot, wa_vehicle, _wa_code = await self._wa_details(lead, live_cars)
            await self.notifications.notify(
                lead["customer_id"], f"{label} removed", f"Your booking ({reference}) has been removed by our team.",
                NotificationType.BOOKING, str(lead["_id"]),
                wa_event="booking_cancelled", wa_params=[wa_services, wa_reference, wa_date, wa_slot, wa_vehicle],
            )
            for captain_id in {c.get("captain_id") for c in live_cars if c.get("captain_id")}:
                await self.notifications.notify(
                    captain_id, f"{label} removed", f"Booking {reference} was removed by an admin — no action needed.",
                    NotificationType.BOOKING, str(lead["_id"]), send_whatsapp=False,
                )
        return {"deleted_count": len(cars), "booking_ids": car_ids, "deleted_flags": deleted_flags}

    async def restore_booking(self, booking_id: str) -> dict:
        """Reverses soft_delete_booking — brings the whole visit back
        together, and re-spends what the delete handed back: the pass wash
        (commit_consumption) and the coupon use of every car that is still
        a live or done job. If the pass has no wash left (or the coupon hit
        its limit, or the customer has since booked that same slot) the
        restore is REFUSED and nothing changes — a restored booking never
        rides free.

        A live (not done, not cancelled) visit takes its slot seat back in
        the SAME transaction that restores it — or the restore is refused
        when the slot is full (audit BOOK-03: it used to come back over
        capacity, and a later cancel then freed a seat it never held, so the
        slot was resold)."""
        booking = await self.repo.find_by_id(booking_id, include_deleted=True)
        if not booking:
            raise NotFoundException("Booking not found")
        if not booking.get("is_deleted"):
            raise BadRequestException("This booking isn't in the recycle bin")
        group_id = booking.get("booking_group_id")
        center = await self.center_repo.find_by_id(booking["service_center_id"])
        live_states = {BookingStatus.COMPLETED.value, BookingStatus.CANCELLED.value}

        async def _do_restore(session):
            await self._touch_customer(booking["customer_id"], session)
            cars = (
                await self.repo.collection.find({"booking_group_id": group_id, "is_deleted": True}, session=session).to_list(length=20)
                if group_id
                else [await self.repo.find_by_id(booking_id, include_deleted=True, session=session)]
            )
            cars = [c for c in cars if c and c.get("is_deleted")]
            # Ownership is decided on the rows as they are IN the bin (a
            # deleted row never holds a seat), before they come back.
            await self._backfill_seats(cars, session)
            restored: list[dict] = []
            for car in cars:
                try:
                    result = await self.repo.collection.update_one(
                        {"_id": car["_id"], "is_deleted": True},
                        {"$set": {"is_deleted": False, "updated_at": datetime.now(timezone.utc)}, "$unset": {"deleted_at": ""}},
                        session=session,
                    )
                except DuplicateKeyError:
                    raise BadRequestException(
                        f"{car.get('booking_number')} can't be restored — the customer already has another booking for "
                        "that vehicle in that slot."
                    )
                if result.modified_count:
                    restored.append(car)
            live = sorted(
                (c for c in restored if c["status"] not in live_states),
                key=lambda c: int(c.get("group_offset_minutes") or 0),
            )
            for car in restored:
                if car["status"] != BookingStatus.CANCELLED.value:
                    await self._reclaim_charges(car, session)
                    await self._redue_after_delete(car, session)
            if live:
                key = self._seat_key_of(live[0])
                holder = await self.repo.collection.find_one(
                    {"booking_group_id": group_id, "holds_seat": True, "is_deleted": {"$ne": True},
                     "seat_key.date": key["date"], "seat_key.slot_key": key["slot_key"]},
                    {"_id": 1}, session=session,
                ) if group_id else None
                if holder is None:
                    if not center:
                        raise BadRequestException(f"{live[0].get('booking_number')} can't be restored — its service center no longer exists.")
                    try:
                        await self._take_seat(session, center, live[0]["_id"], key["date"], key["slot_key"])
                    except BadRequestException:
                        raise BadRequestException(
                            f"{live[0].get('booking_number')} can't be restored — its slot ({format_slot_12h(key['slot_key'])} on "
                            f"{datetime.strptime(key['date'], '%Y-%m-%d').strftime('%d %b %Y')}) has no free seat: it is full, "
                            "closed or not set up. Free a seat or raise that slot's capacity, then restore it."
                        )
            return restored

        async with await self.db.client.start_session() as session:
            restored = await session.with_transaction(_do_restore)

        respent: list[dict] = []
        recouponed: list[dict] = []
        try:
            for car in restored:
                car_id = str(car["_id"])
                if car["status"] == BookingStatus.CANCELLED.value:
                    continue  # a cancelled job stays cancelled — nothing to re-spend
                # The delete handed this wash back (a recycle-bin car from
                # before the flag existed did too — hence the default).
                if car.get("subscription_id") and car.get("subscription_consumption") and car.get("consumption_restored", True):
                    try:
                        await self.subscription_service.commit_consumption(car["subscription_id"], car["subscription_consumption"])
                    except BadRequestException:
                        raise BadRequestException(
                            f"{car.get('booking_number')} can't be restored — the customer's pass has no wash left for it. "
                            "Book it again instead."
                        )
                    respent.append(car)
                    await self.repo.collection.update_one({"_id": car["_id"]}, {"$set": {"consumption_restored": False}})
                if car.get("coupon_code"):
                    coupon = await self.coupon_service.repo.find_by_code(car["coupon_code"])
                    if coupon:
                        try:
                            await self.coupon_service.record_usage(str(coupon["_id"]), car["customer_id"], car_id)
                        except BadRequestException:
                            raise BadRequestException(
                                f"{car.get('booking_number')} can't be restored — its coupon {car['coupon_code']} has reached "
                                "its usage limit."
                            )
                        recouponed.append(car)
        except Exception:
            # All or nothing: put back everything this call already did —
            # the cars go back to the bin and the seat they took is returned.
            for car in respent:
                await self.subscription_service.restore_consumption(car["subscription_id"], car["subscription_consumption"])
                await self.repo.collection.update_one({"_id": car["_id"]}, {"$set": {"consumption_restored": True}})
            for car in recouponed:
                await self.coupon_service.reverse_usage(car["coupon_code"], car["customer_id"], str(car["_id"]))
            await self._return_to_bin(booking["customer_id"], restored)
            raise

        restored_ids = [str(car["_id"]) for car in restored]
        if restored_ids:
            # Paid online and flagged for a refund by the delete: it is a
            # paid booking again, and its refund row leaves the queue.
            try:
                from app.services.payment_service import PaymentService

                await PaymentService(self.db).clear_refund_due(restored_ids, "Booking restored")
            except Exception:  # noqa: BLE001 — the restore stands; the queue row stays for a human
                logger.exception("Could not clear refund-due for restored bookings %s", restored_ids)
        for car_id in restored_ids:
            restored_doc = await self.repo.find_by_id(car_id)
            if restored_doc:
                await self._broadcast_booking_changed(restored_doc)
        if restored:
            await self._broadcast_slots_changed(booking["service_center_id"], self._seat_key_of(booking)["date"])

        # Closes the loop on soft_delete_booking's own "removed" notice —
        # without this, a delete-then-restore (the exact "oops, undo that"
        # case the recycle bin exists for) left the customer's last word on
        # it being "removed" forever, with no follow-up once it came back.
        # Same channel as that notice: the "removed" went out on WhatsApp, so
        # the "it's back on" does too (as the booking's confirmation) — an
        # unpaid one stays in-app, it isn't confirmed.
        live_cars = [c for c in restored if c["status"] not in live_states]
        if live_cars:
            lead = live_cars[0]
            label = await self._visit_label(live_cars)
            reference = self._visit_numbers(live_cars) if len(live_cars) > 1 else str(lead.get("booking_number") or "")
            wa_services, wa_reference, wa_date, wa_slot, wa_vehicle, wa_code = await self._wa_details(lead, live_cars)
            await self.notifications.notify(
                lead["customer_id"], f"{label} restored", f"Your booking ({reference}) is back on — sorry for the mix-up.",
                NotificationType.BOOKING, str(lead["_id"]),
                wa_event="booking_confirmed",
                wa_params=[wa_services, wa_reference, wa_date, wa_slot, wa_vehicle, wa_code],
                send_whatsapp=all(c["status"] != BookingStatus.AWAITING_PAYMENT.value for c in live_cars),
            )
        return {"restored_count": len(restored_ids), "booking_ids": restored_ids}

    async def _redue_after_delete(self, car: dict, session) -> None:
        """A restored booking whose delete gave its wallet money back
        (soft_delete_booking) is DUE again: the wallet credit it had spent
        stays in the wallet (applied again only by a new booking), and the
        previous balance it carried stays on the wallet's open debt — so
        neither is counted twice. Its total drops by that carried balance."""
        if not car.get("delete_money_settled"):
            return
        carried = round(float(car.get("wallet_due_carried") or 0), 2)
        total = round(max(0.0, float(car.get("total_amount") or 0) - carried), 2)
        # Only never-paid bookings had their wallet money returned on delete
        # (its status now says "refunded" — that was the wallet credit).
        paid = round(float(car.get("amount_paid") or 0), 2)
        fields = {
            "total_amount": total,
            "platform_earning": round(float(car.get("platform_earning") or 0) - carried, 2),
            "wallet_due_carried": 0.0,
            "wallet_applied": 0.0,
            "amount_due": max(0.0, round(total - paid, 2)),
            "payment_status": bm.status_for(total, 0, paid),
            "cancel_money_settled": False,
            "delete_money_settled": False,
            "updated_at": datetime.now(timezone.utc),
        }
        await self.repo.collection.update_one(
            {"_id": car["_id"]}, {"$set": fields, "$unset": {"refunded_to": "", "refunded_amount": "", "refunded_at": ""}}, session=session,
        )
        car.update(fields)

    async def _reclaim_charges(self, car: dict, session) -> None:
        """A restored, still-open booking takes back the late-cancellation
        charges it carried before it was deleted — each only if it is still
        on the account. One no longer open (applied elsewhere meanwhile,
        reduced or waived) is NOT charged again: while the booking is
        unpaid its total drops to what it really carries now."""
        if not car.get("cancellation_charge_ids") and not float(car.get("cancellation_charge") or 0):
            return
        if self.charges.keeps_charges(car):
            return  # done and paid: its delete never handed them back
        carried, ids = await self.charges.reclaim_for_booking(session, car)
        before = round(float(car.get("cancellation_charge") or 0), 2)
        delta = round(carried - before, 2)
        if not delta and ids == list(car.get("cancellation_charge_ids") or []):
            return
        if car.get("payment_status") == PaymentStatus.PAID.value:
            # Already paid with the old charge in it — the money question is a
            # refund for a human, never a silent second charge.
            logger.warning("Restored paid booking %s carried ₹%s of charges, now ₹%s", car.get("booking_number"), before, carried)
            return
        fields = {
            "cancellation_charge": carried,
            "cancellation_charge_ids": ids,
            "total_amount": round(max(0.0, float(car.get("total_amount") or 0) + delta), 2),
            "platform_earning": round(float(car.get("platform_earning") or 0) + delta, 2),
            "updated_at": datetime.now(timezone.utc),
        }
        if fields["total_amount"] <= 0 and car.get("payment_method") == PaymentMethod.SUBSCRIPTION.value:
            fields["payment_status"] = PaymentStatus.PAID.value
        await self.repo.collection.update_one({"_id": car["_id"]}, {"$set": fields}, session=session)
        car.update(fields)

    async def _return_to_bin(self, customer_id: str, cars: list[dict]) -> None:
        """Undo a restore that couldn't finish: the cars go back to the bin
        and any seat the restore took is handed back, in one transaction."""
        if not cars:
            return

        async def _undo(session):
            await self._touch_customer(customer_id, session)
            for car in cars:
                result = await self.repo.collection.find_one_and_update(
                    {"_id": car["_id"], "is_deleted": False},
                    {"$set": {"is_deleted": True, "deleted_at": car.get("deleted_at") or now_ist()}},
                    session=session,
                )
                if result is not None:
                    await self._release_seat(result, session)
                    await self.charges.release_for_booking(session, result, reason="went back to the recycle bin")

        try:
            async with await self.db.client.start_session() as session:
                await session.with_transaction(_undo)
        except Exception:  # noqa: BLE001 — the restore's own error is the one to surface
            logger.exception("Could not put restored bookings back in the recycle bin: %s", [str(c["_id"]) for c in cars])

    async def permanently_delete_booking(self, booking_id: str, *, force: bool = False) -> dict:
        """Irreversible — only ever reachable from the recycle bin (a
        booking must be soft-deleted first) or the 30-day auto-purge sweep.
        Mirrors app/scripts/delete_booking.py's own guard and cascade for a
        single visit (keep the two in sync if either one changes): refuses
        unless force=True when a paid online payment or a complaint is
        attached — those need a human decision, not a delete — and ALWAYS
        when captain-wallet money is (audit ADM-11: force left the wallet
        rows orphaned)."""
        booking = await self.repo.find_by_id(booking_id, include_deleted=True)
        if not booking:
            raise NotFoundException("Booking not found")
        if not booking.get("is_deleted"):
            raise BadRequestException("This booking isn't in the recycle bin — delete it first")
        group_id = booking.get("booking_group_id")
        cars = (
            await self.repo.collection.find({"booking_group_id": group_id, "is_deleted": True}).to_list(length=20)
            if group_id
            else [booking]
        )
        car_ids = [str(c["_id"]) for c in cars]

        wallet = await self.db.wallet_transactions.count_documents({"booking_id": {"$in": car_ids}})
        if wallet:
            # Captain money stays explained (audit ADM-11): force used to
            # delete the booking and leave its wallet rows pointing at
            # nothing. Never — not even with force. Reversing a captain's pay
            # to make a delete possible is a money decision, not a cleanup.
            raise BadRequestException(
                f"Can't permanently delete — {wallet} captain wallet transaction(s) are attached to this booking. "
                "It stays in the recycle bin (it no longer counts anywhere); settle or adjust the captain's wallet "
                "with a reason first if this really must go."
            )
        paid_orders = await self.db.payment_orders.count_documents(
            {"$or": [{"booking_id": {"$in": car_ids}}, {"booking_ids": {"$in": car_ids}}], "status": {"$in": ["paid", "paid_attention"]}}
        )
        complaints = await self.db.complaints.count_documents({"booking_id": {"$in": car_ids}})
        if (wallet or paid_orders or complaints) and not force:
            reasons = []
            if wallet:
                reasons.append(f"{wallet} captain wallet transaction(s)")
            if paid_orders:
                reasons.append(f"{paid_orders} paid online payment(s)")
            if complaints:
                reasons.append(f"{complaints} complaint(s)")
            raise BadRequestException(f"Can't permanently delete — {', '.join(reasons)} attached. Use force to delete anyway.")

        child_filters = {
            "booking_status_history": {"booking_id": {"$in": car_ids}},
            "notifications": {"reference_id": {"$in": car_ids}},
            "reviews": {"booking_id": {"$in": car_ids}},
            "captain_locations": {"booking_id": {"$in": car_ids}},
            "purchase_confirmations": {"reference_id": {"$in": car_ids}},
            "payment_orders": {
                "$or": [{"booking_id": {"$in": car_ids}}, {"booking_ids": {"$in": car_ids}}],
                "status": {"$nin": ["paid", "paid_attention"]},
            },
        }
        for name, flt in child_filters.items():
            await self.db[name].delete_many(flt)
        for car_id in car_ids:
            await self.repo.hard_delete(car_id)
        return {"deleted_count": len(car_ids), "booking_ids": car_ids}

    # Every finder main.py's _reminder_loop calls bounds scheduled_date IN
    # THE QUERY (served by the {status, scheduled_date} index): the loop
    # runs them every 60s, forever, and a booking whose day is long gone is
    # a zombie, not a nudge target — unbounded, each one was re-read (and
    # some re-alerted the manager) every pass indefinitely. They also sort
    # on scheduled_date (order means nothing to a sweep) so that one index
    # serves both the filter and the sort. SWEEP_MAX_ROWS is the hard cap on
    # what one finder loads per pass — oldest slot first; a bad day's
    # overflow is simply picked up by the next pass.
    SWEEP_IN_FLIGHT_DAYS = 7  # on-the-way / in-service sweeps, same floor as captain_not_reached
    SWEEP_MAX_ROWS = 500
    # Earliest slot first, _id as the final tiebreak — served by the
    # (status, scheduled_date, scheduled_slot, _id) index.
    _SWEEP_ORDER = [("scheduled_date", 1), ("scheduled_slot", 1), ("_id", 1)]

    async def _sweep_scan(self, query: dict, is_due) -> list[dict]:
        """Every candidate in a sweep's (already date-bounded) window,
        earliest slot first, streamed in batches until it runs out — the
        "is it due" check is applied to ALL of them. Cutting the candidate
        list at SWEEP_MAX_ROWS before that check (sorted on the day alone)
        meant that on a busy day the due bookings beyond the first 500
        rows were never even looked at. What's capped now is the DUE list a
        pass acts on; the rest is picked up next pass. `is_due` may be async."""
        due: list[dict] = []
        cursor = self.repo.collection.find({**query, "is_deleted": {"$ne": True}}).sort(self._SWEEP_ORDER).batch_size(self.SWEEP_MAX_ROWS)
        try:
            async for booking in cursor:
                verdict = is_due(booking)
                if asyncio.iscoroutine(verdict):
                    verdict = await verdict
                if verdict:
                    due.append(booking)
                    if len(due) >= self.SWEEP_MAX_ROWS:
                        break
        finally:
            await cursor.close()
        return due

    @staticmethod
    def _sweep_day(offset_days: int) -> datetime:
        """Today's IST midnight shifted by `offset_days`, naive — the same
        wall-clock form scheduled_date is stored in."""
        return now_ist().replace(hour=0, minute=0, second=0, microsecond=0, tzinfo=None) + timedelta(days=offset_days)

    # A captain's "starting soon" reminder is not sent once the slot began
    # longer ago than this (NTF-06).
    REMINDER_STALE_MINUTES = 30

    async def find_bookings_needing_reminder(self) -> list[dict]:
        # Yesterday..tomorrow: nothing outside that can start within 30
        # minutes, and a stale one from last week must not be pinged at all.
        now = now_ist()

        def is_due(booking: dict) -> bool:
            # Due from 30 minutes before the slot starts — and no longer once
            # it started more than REMINDER_STALE_MINUTES ago: after downtime
            # a "starting soon" for a 9 AM slot at 2 PM is noise (NTF-06).
            slot_start = _slot_start_datetime(booking["scheduled_date"], booking["scheduled_slot"])
            return slot_start - timedelta(minutes=START_WINDOW_MINUTES) <= now <= slot_start + timedelta(minutes=self.REMINDER_STALE_MINUTES)

        due = await self._sweep_scan(
            {
                "status": BookingStatus.ASSIGNED.value,
                "reminder_sent": {"$ne": True},
                "scheduled_date": {"$gte": self._sweep_day(-1), "$lt": self._sweep_day(2)},
            },
            is_due,
        )
        return self._one_per_visit(due)

    async def mark_reminder_sent(self, booking_id: str) -> None:
        await self._mark_visit(booking_id, {"reminder_sent": True})

    # -- Pre-slot CUSTOMER reminder ("booking_reminder" template) ---------
    # About two hours before the slot starts, once per visit, and only
    # between 7 AM and 7 PM IST (no WhatsApp at night: a 7 AM slot gets
    # none). A booking made inside that lead time just got its
    # confirmation — it isn't reminded again. ARCH runs the sweep in the
    # worker loop: for b in find_bookings_needing_customer_reminder():
    # await send_customer_reminder(b).
    CUSTOMER_REMINDER_LEAD_MINUTES = 120
    CUSTOMER_REMINDER_HOURS = (7, 19)  # [from, until) IST hour
    _CUSTOMER_REMINDER_STATUSES = (BookingStatus.PENDING.value, BookingStatus.ASSIGNED.value, BookingStatus.RESCHEDULED.value)

    async def find_bookings_needing_customer_reminder(self, now: datetime | None = None) -> list[dict]:
        """Confirmed bookings whose slot starts within the next
        CUSTOMER_REMINDER_LEAD_MINUTES and whose visit hasn't been reminded
        — one car per visit. Empty outside 7 AM–7 PM."""
        now = now or now_ist()
        start_hour, end_hour = self.CUSTOMER_REMINDER_HOURS
        if not (start_hour <= now.hour < end_hour):
            return []
        lead = timedelta(minutes=self.CUSTOMER_REMINDER_LEAD_MINUTES)

        def is_due(booking: dict) -> bool:
            slot_start, _ = _booking_window(booking)
            if not (slot_start - lead <= now < slot_start):
                return False
            created = booking.get("created_at")
            # Booked inside the lead time: the confirmation was the reminder.
            return not (created is not None and from_stored(created) > slot_start - lead)

        due = await self._sweep_scan(
            {
                "status": {"$in": list(self._CUSTOMER_REMINDER_STATUSES)},
                "customer_reminder_sent_at": None,
                "scheduled_date": {"$gte": self._sweep_day(0), "$lt": self._sweep_day(2)},
            },
            is_due,
        )
        return self._one_per_visit(due)

    async def send_customer_reminder(self, booking: dict) -> bool:
        """Claim, then send, the visit's one reminder. The claim is a
        guarded write on the car the finder returned (still unreminded, still
        in a confirmed state), so two worker passes — or two workers — can
        never send it twice; a booking cancelled/started since the finder
        read it is skipped. Returns True when this call sent it."""
        now = now_ist()
        claimed = await self.repo.collection.find_one_and_update(
            {
                "_id": booking["_id"], "customer_reminder_sent_at": None,
                "status": {"$in": list(self._CUSTOMER_REMINDER_STATUSES)}, "is_deleted": {"$ne": True},
            },
            {"$set": {"customer_reminder_sent_at": now, "updated_at": datetime.now(timezone.utc)}},
            return_document=ReturnDocument.AFTER,
        )
        if claimed is None:
            return False
        await self._mark_visit(str(claimed["_id"]), {"customer_reminder_sent_at": now})
        try:
            cars = await self._visit_cars(claimed)
            wa_services, wa_reference, wa_date, wa_slot, wa_vehicle, _wa_code = await self._wa_details(claimed, cars)
            await self.notifications.notify(
                claimed["customer_id"],
                "Booking reminder",
                f"{wa_services} for your {wa_vehicle} today, {wa_slot}. ({wa_reference})",
                NotificationType.BOOKING,
                str(claimed["_id"]),
                wa_event="booking_reminder",
                wa_params=[wa_services, wa_reference, wa_date, wa_slot, wa_vehicle],
                background=True,
            )
        except Exception:  # noqa: BLE001 — claimed once; a failed send is the outbox's to retry
            logger.exception("Could not send the pre-slot reminder for %s", claimed.get("booking_number"))
        return True

    async def find_bookings_captain_not_reached(self) -> list[dict]:
        """Bookings that never even got a captain (PENDING) — or got
        RESCHEDULED and then didn't get one either — whose full start
        window (up to late_start_grace_minutes past the slot's end) has
        completely expired. (An ASSIGNED booking whose captain hasn't
        headed out is caught much earlier, right at the scheduled start
        time, by find_bookings_late_to_start below — this is only for the
        "never got a captain in the first place" case.) These get
        auto-flagged so the manager finds out proactively instead of the
        customer just... waiting, and so the booking can't just sit there
        silently offering 'assign a captain' for a time that's already gone
        (see the time-based gate in assign_captain)."""
        policy = await self.policy_service.get_policy()
        # Query-side date window: only bookings whose slot day is recent
        # enough to have JUST expired (7-day floor keeps resolved-then-idle
        # zombies out of every pass) and not in the future (tomorrow's
        # window can't have expired yet).
        now_naive = now_ist().replace(tzinfo=None)
        now = now_ist()

        def is_overdue(booking: dict) -> bool:
            # No captain (and so no estimated_start_at) exists for these yet —
            # the relevant "has this booking's whole window expired" boundary
            # is the admin slot's own end (_booking_window), not a per-captain
            # anchor that hasn't been set.
            _, slot_end = _booking_window(booking)
            return now > slot_end + timedelta(minutes=policy.get("late_start_grace_minutes", 30))

        overdue = await self._sweep_scan(
            {
                "status": {"$in": [BookingStatus.PENDING.value, BookingStatus.RESCHEDULED.value]},
                "issue_flag": None,
                "scheduled_date": {"$gte": now_naive - timedelta(days=7), "$lte": now_naive + timedelta(days=1)},
            },
            is_overdue,
        )
        return self._one_per_visit(overdue)

    async def find_bookings_late_to_start(self) -> list[dict]:
        """An ASSIGNED booking whose scheduled start time has already passed
        and the captain still hasn't even started heading out — flagged and
        notified immediately (not the much longer slot_end + grace wait
        find_bookings_captain_not_reached uses for a booking that never got
        a captain at all), then re-nudged every LATE_START_NUDGE_MINUTES for
        as long as it stays unstarted (in-app only after the first alert —
        see flag_late_to_start). Deliberately does NOT filter on
        issue_flag being unset — this needs to keep firing on its own
        throttle even while flagged, which find_bookings_captain_not_reached
        doesn't need since it only ever fires once. Excludes self_assigned
        bookings entirely — a manager delivering it personally never goes
        through the heading-out flow this nudge is about (see self_assign's
        own comment)."""
        # Yesterday..tomorrow, like its siblings: a booking whose day has
        # gone drops out instead of re-alerting the manager every
        # LATE_START_NUDGE_MINUTES forever, and next week's assigned jobs
        # (which can't be late yet) aren't re-read every minute.
        now = now_ist()
        policy = await self.policy_service.get_policy()
        threshold = timedelta(minutes=LATE_START_NUDGE_MINUTES)

        def is_due(booking: dict) -> bool:
            # ASSIGNED means a captain (and estimated_start_at) exists —
            # anchor to that, not the coarse shared slot, same reasoning as
            # start_heading. Shifted forward for last-minute assignments
            # (_effective_start_anchor): a captain handed the job at 6:35
            # for a slot that began at 4 is not "late" at 6:36.
            slot_start = from_stored(booking["estimated_start_at"]) if booking.get("estimated_start_at") else _slot_start_datetime(booking["scheduled_date"], booking["scheduled_slot"])
            if now <= _effective_start_anchor(booking, slot_start, policy):
                return False
            last_reminder = booking.get("late_start_reminder_sent_at")
            return not (last_reminder and now - from_stored(last_reminder) < threshold)

        due = await self._sweep_scan(
            {
                "status": BookingStatus.ASSIGNED.value,
                "self_assigned": {"$ne": True},
                "scheduled_date": {"$gte": self._sweep_day(-1), "$lt": self._sweep_day(2)},
            },
            is_due,
        )
        return self._one_per_visit(due)

    async def flag_late_to_start(self, booking: dict) -> None:
        scheduled_time = format_slot_start_12h(booking["scheduled_slot"])
        existing_flag = booking.get("issue_flag")
        # The sweep read this booking a moment ago; every write and nudge
        # below is for THAT state — still assigned, to the same captain.
        as_read = {"status": BookingStatus.ASSIGNED.value, "captain_id": booking.get("captain_id")}
        if not await self.repo.collection.find_one({"_id": booking["_id"], "is_deleted": {"$ne": True}, **as_read}, {"_id": 1}):
            return  # he headed out (or it was cancelled/reassigned) meanwhile

        # Once a booking crosses the SAME lockout threshold start_heading
        # itself enforces, nudging the captain to "start heading out now" is
        # pointless — they literally can't anymore. Switch to a distinct
        # flag that tells the manager this needs an actual decision
        # (reschedule/reassign), not just a reminder — and notify the
        # manager, not the captain, from this point on.
        policy = await self.policy_service.get_policy()
        duration = booking.get("duration_minutes", 60)
        # Same late-assignment shift as the sweep and start_heading — the
        # lockout countdown must not start before the captain even had the
        # job.
        raw_start = from_stored(booking["estimated_start_at"]) if booking.get("estimated_start_at") else _slot_start_datetime(booking["scheduled_date"], booking["scheduled_slot"])
        slot_start = _effective_start_anchor(booking, raw_start, policy)
        slot_end = slot_start + timedelta(minutes=duration)
        window_end = slot_end + timedelta(minutes=policy.get("late_start_grace_minutes", 30))
        lockout_at = window_end + timedelta(hours=policy.get("captain_start_lockout_hours", 4))
        is_locked_out = now_ist() > lockout_at

        if is_locked_out:
            # Escalate ONCE. The open flag already sits in the manager's
            # queue, and a Resolve must stick (same reason as the
            # captain_not_started check below) — re-flagging on every nudge
            # sent an urgent WhatsApp every 5 minutes until the booking aged
            # out of the sweep, ~400 of them.
            if existing_flag == "captain_missed_window" or (
                existing_flag is None and booking.get("resolved_issue_flag") == "captain_missed_window"
            ):
                return
            note = (
                f"Booking {booking['booking_number']} was due to start at {scheduled_time} and the captain never headed "
                f"out — the window expired too long ago for them to start it now. Reschedule it to a new time, then "
                f"assign a captain, or cancel it."
            )
            # Always (re)set here, even over an existing captain_not_started —
            # this IS the more specific issue once locked out, superseding
            # the earlier nudge rather than being blocked by it.
            await self.flag_issue(str(booking["_id"]), "captain_missed_window", note, expected=as_read)
            return

        note = f"Booking {booking['booking_number']} was due to start at {scheduled_time} — the captain hasn't headed out yet."
        # Don't clobber a different, still-open flag (e.g. the captain's own
        # report_risk) — only set/refresh captain_not_started when there
        # isn't a more specific issue already active.
        is_different_open_flag = bool(existing_flag) and existing_flag != "captain_not_started" and not booking.get("issue_resolved")
        # Once a manager has explicitly resolved THIS exact nudge, re-
        # flagging the identical still-ongoing situation every
        # LATE_START_NUDGE_MINUTES would silently undo their own Resolve
        # click — flag_issue always resets issue_resolved=False and sends
        # a fresh "🚨 Urgent" manager ping, so calling it again here is
        # exactly the "keeps notifying after I acknowledged it" spam
        # reported. It only becomes relevant again once the situation
        # actually escalates (captain_missed_window, a distinct flag) or
        # the booking gets reassigned (which resets these fields) — a
        # genuinely new event, not a repeat of this one.
        # existing_flag/issue_resolved alone can't tell "already resolved"
        # apart from "never flagged at all" — resolve_issue clears
        # issue_flag to None in BOTH cases, so resolved_issue_flag (the
        # flag value resolve_issue last cleared) is the only reliable
        # signal (see the model field's own comment).
        already_acknowledged = existing_flag is None and booking.get("resolved_issue_flag") == "captain_not_started"
        if already_acknowledged:
            return  # the manager handled it — the captain isn't nagged on either
        # Only the first alert of this assignment is a (billed) WhatsApp, to
        # the captain and the manager; repeats are in-app only — the
        # founder's "continuous notifications" complaint. A reassignment
        # (new assigned_at) starts over.
        last_alert = booking.get("late_start_reminder_sent_at")
        assigned_at = booking.get("assigned_at")
        first_alert = not last_alert or (assigned_at is not None and from_stored(last_alert) < from_stored(assigned_at))
        flagged_at = booking.get("issue_flagged_at")
        manager_alerted_recently = (
            existing_flag == "captain_not_started"
            and not booking.get("issue_resolved")
            and flagged_at is not None
            and now_ist() - from_stored(flagged_at) < timedelta(minutes=LATE_START_MANAGER_REPEAT_MINUTES)
        )
        if not is_different_open_flag and not manager_alerted_recently:
            if not await self.flag_issue(str(booking["_id"]), "captain_not_started", note, send_whatsapp=first_alert, expected=as_read):
                return
        if booking.get("captain_id"):
            await self.notifications.notify(
                booking["captain_id"],
                "You haven't started this booking yet",
                f"Booking {booking['booking_number']} was due to start at {scheduled_time}. Please start heading out now.",
                NotificationType.BOOKING,
                str(booking["_id"]),
                send_whatsapp=first_alert,
            )

    async def mark_late_start_reminder_sent(self, booking_id: str) -> None:
        await self._mark_visit(booking_id, {"late_start_reminder_sent_at": now_ist()})

    async def find_bookings_unassigned_too_long(self) -> list[dict]:
        """A booking that's been sitting without a captain for too long and
        whose slot starts within UNASSIGNED_REMINDER_HORIZON_HOURS (the
        "window has fully expired" case is find_bookings_captain_not_reached
        above). A repeating nudge, every UNASSIGNED_REMINDER_MINUTES while it
        stays unassigned — but only the FIRST reminder of each wait goes to
        WhatsApp (each one is a billed message; ~96 a day per booking when
        they all did); repeats are in-app only and are held outside the
        center's working hours, so the first pass after opening catches up.

        Each returned booking carries `_first_unassigned_reminder`, which
        the reminder loop turns into send_whatsapp."""
        # Time-bounded IN THE QUERY: an abandoned booking whose slot is days
        # in the past is a zombie, not a nudge target — without this bound
        # every such booking was re-fetched and re-parsed every 60s forever.
        # Nothing past tomorrow can start within the horizon. Flagged
        # bookings are excluded too (the manager already has a louder signal
        # for those). scheduled_date is naive IST wall-clock.
        floor = now_ist().replace(tzinfo=None) - timedelta(days=2)
        centers: dict[str, dict | None] = {}  # looked up once per center, on first need
        now = now_ist()
        threshold = timedelta(minutes=UNASSIGNED_REMINDER_MINUTES)
        horizon = timedelta(hours=UNASSIGNED_REMINDER_HORIZON_HOURS)

        async def is_due(booking: dict) -> bool:
            since = booking.get("awaiting_assignment_since")
            if not since or now - from_stored(since) < threshold:
                return False
            slot_start, _ = _booking_window(booking)
            if slot_start - now > horizon:
                return False
            last_reminder = booking.get("unassigned_reminder_sent_at")
            # A reminder from an earlier wait (before a reschedule or a
            # captain releasing it) doesn't make this wait's first one a repeat.
            first = not last_reminder or from_stored(last_reminder) < from_stored(since)
            if not first:
                if now - from_stored(last_reminder) < threshold:
                    return False
                center_id = booking.get("service_center_id") or ""
                if center_id not in centers:
                    centers[center_id] = await self.center_repo.find_by_id(center_id)
                if not _within_working_hours(centers[center_id], now):
                    return False
            booking["_first_unassigned_reminder"] = first
            return True

        due = await self._sweep_scan(
            {
                "status": {"$in": [BookingStatus.PENDING.value, BookingStatus.RESCHEDULED.value]},
                "awaiting_assignment_since": {"$ne": None},
                "issue_flag": None,
                "scheduled_date": {"$gte": floor, "$lt": self._sweep_day(2)},
            },
            is_due,
        )
        return self._one_per_visit(due)

    async def mark_unassigned_reminder_sent(self, booking_id: str) -> None:
        await self._mark_visit(booking_id, {"unassigned_reminder_sent_at": now_ist()})

    async def find_bookings_stuck_on_the_way(self) -> list[dict]:
        """Captain started heading out but hasn't reached/started the service in
        a long time — worth nudging the manager even though nothing's broken
        yet ('notify him if he takes unnecessary time')."""
        candidates = await self.repo.find_all_no_paginate(
            {
                "status": BookingStatus.CAPTAIN_ON_THE_WAY.value,
                "issue_flag": None,
                "scheduled_date": {"$gte": self._sweep_day(-self.SWEEP_IN_FLIGHT_DAYS)},
            },
            sort_by="scheduled_date",
            sort_order=1,
            limit=self.SWEEP_MAX_ROWS,
        )
        now = now_ist()
        stuck = []
        for booking in candidates:
            heading_at = booking.get("heading_at")
            if not heading_at:
                continue
            if booking.get("booking_group_id"):
                # Every car on a visit is "on the way" from the one departure,
                # and stays so while the earlier cars are wash. He has
                # reached the address if ANY car on it has been reached.
                cars = await self._visit_cars(booking)
                if any(c.get("vehicle_verified") or c.get("service_started_at") or c.get("status") == BookingStatus.COMPLETED.value for c in cars):
                    continue
            if now - from_stored(heading_at) > timedelta(minutes=STUCK_ON_THE_WAY_MINUTES):
                stuck.append(booking)
        return self._one_per_visit(stuck)

    async def find_bookings_service_overrunning(self) -> list[dict]:
        """A wash that's been running noticeably longer than its planned
        duration (before-photo captured, no after-photo yet) — the same
        'nudge the manager if it's taking unusual time' idea as the
        stuck-on-the-way sweep above, just for the actual service instead of
        the drive over."""
        policy = await self.policy_service.get_policy()
        tolerance = policy.get("delay_tolerance_minutes", SERVICE_OVERRUN_MINUTES)
        candidates = await self.repo.find_all_no_paginate(
            {
                "status": BookingStatus.SERVICE_STARTED.value,
                "issue_flag": None,
                "scheduled_date": {"$gte": self._sweep_day(-self.SWEEP_IN_FLIGHT_DAYS)},
            },
            sort_by="scheduled_date",
            sort_order=1,
            limit=self.SWEEP_MAX_ROWS,
        )
        now = now_ist()
        overrunning = []
        for booking in candidates:
            started_at = booking.get("service_started_at")
            if not started_at:
                continue
            duration = booking.get("duration_minutes", 60)
            elapsed_minutes = (now - from_stored(started_at)).total_seconds() / 60
            if elapsed_minutes > duration + tolerance:
                overrunning.append(booking)
        return overrunning

    async def find_bookings_idle_after_arrival(self) -> list[dict]:
        """Captain pressed "reached" (vehicle verified) but hasn't captured
        the before-photo well past the tolerance — the classic side-job
        window: he's AT the address, neighbours ask for a wash, the booked
        car waits. Distance checks can't catch this (he's inside the
        geofence); the clock can."""
        policy = await self.policy_service.get_policy()
        tolerance = policy.get("arrival_to_start_tolerance_minutes", 15)
        candidates = await self.repo.find_all_no_paginate(
            {
                "status": BookingStatus.CAPTAIN_ON_THE_WAY.value,
                "vehicle_verified": True,
                "issue_flag": None,
                "scheduled_date": {"$gte": self._sweep_day(-self.SWEEP_IN_FLIGHT_DAYS)},
            },
            sort_by="scheduled_date",
            sort_order=1,
            limit=self.SWEEP_MAX_ROWS,
        )
        now = now_ist()
        idle = []
        for booking in candidates:
            verified_at = booking.get("vehicle_verified_at")
            if not verified_at or booking.get("service_started_at"):
                continue
            if (now - from_stored(verified_at)).total_seconds() / 60 > tolerance:
                idle.append(booking)
        return idle

    async def find_bookings_captain_left_site(self) -> list[dict]:
        """Mid-service walk-off: the wash is running (before-photo captured)
        but the captain's latest live ping is far from the booking's address.
        Only a FRESH ping counts (≤3 min old) — a stale position must never
        flag someone whose phone just stopped pinging. Radius is 2× the
        photo geofence so ordinary GPS wobble around the address never
        fires."""
        policy = await self.policy_service.get_policy()
        radius_m = policy.get("photo_geofence_radius_m", 300) * 2
        candidates = await self.repo.find_all_no_paginate(
            {
                "status": BookingStatus.SERVICE_STARTED.value,
                "issue_flag": None,
                "scheduled_date": {"$gte": self._sweep_day(-self.SWEEP_IN_FLIGHT_DAYS)},
            },
            sort_by="scheduled_date",
            sort_order=1,
            limit=self.SWEEP_MAX_ROWS,
        )
        if not candidates:
            return []
        # Batched lookups — this sweep runs every 60s; two find_by_id calls
        # per in-progress booking was the loop's biggest query multiplier.
        addresses = {
            str(a["_id"]): a
            for a in await self.address_repo.find_by_ids(
                [b["address_id"] for b in candidates if b.get("address_id") and not b.get("address_snapshot")]
            )
        }
        captains = {
            str(u["_id"]): u
            for u in await self.user_repo.find_by_ids([b["captain_id"] for b in candidates if b.get("captain_id")])
        }
        now = now_ist()
        away = []
        for booking in candidates:
            captain_id = booking.get("captain_id")
            if not captain_id:
                continue
            address = booking.get("address_snapshot") or addresses.get(booking.get("address_id") or "")
            if not address or address.get("latitude") is None or address.get("longitude") is None:
                continue
            captain = captains.get(captain_id)
            loc = (captain or {}).get("last_known_location") or {}
            loc_at = (captain or {}).get("last_location_at")
            if not loc or loc.get("latitude") is None or loc_at is None:
                continue
            if (now - from_stored(loc_at)).total_seconds() > 180:
                continue  # stale — no verdict
            distance_m = haversine_km(loc["latitude"], loc["longitude"], address["latitude"], address["longitude"]) * 1000
            if distance_m > radius_m:
                booking["_distance_from_site_m"] = round(distance_m)
                away.append(booking)
        return away

    # The state each flag describes. A sweep flags from a read that can be
    # a minute old by the time it writes: the captain may have headed out,
    # arrived or finished meanwhile — a stale "hasn't started" landing on an
    # on-the-way job then blocked his arrival check. Without an explicit
    # `expected`, flag_issue writes only while the booking is still here.
    _FLAG_STATES: dict[str, tuple[str, ...]] = {
        "captain_not_started": (BookingStatus.ASSIGNED.value,),
        "captain_missed_window": (BookingStatus.ASSIGNED.value,),
        "captain_reported_risk": (BookingStatus.ASSIGNED.value,),
        "captain_not_reached": (BookingStatus.PENDING.value, BookingStatus.RESCHEDULED.value),
        "captain_delay": (BookingStatus.CAPTAIN_ON_THE_WAY.value,),
        "idle_after_arrival": (BookingStatus.CAPTAIN_ON_THE_WAY.value,),
        "service_overrun": (BookingStatus.SERVICE_STARTED.value,),
        "left_site_during_service": (BookingStatus.SERVICE_STARTED.value,),
        "arrival_code_locked": (BookingStatus.CAPTAIN_ON_THE_WAY.value,),
    }

    async def flag_issue(self, booking_id: str, issue_flag: str, note: str, send_whatsapp: bool = True, expected: dict | None = None) -> bool:
        """`expected`: the fields (status, captain_id) the caller read — the
        flag is written only while they still hold. Returns False (and
        notifies nobody) when the booking has moved on."""
        fields = {"issue_flag": issue_flag, "issue_notes": note, "issue_flagged_at": now_ist(), "issue_resolved": False}
        if expected is None:
            states = self._FLAG_STATES.get(issue_flag)
            expected = {"status": {"$in": list(states)}} if states else {}
        booking = await self.repo.update_if(booking_id, dict(expected), fields)
        if booking is None:
            return False
        if issue_flag in VISIT_WIDE_FLAGS:
            # The trip has the problem, so every car on it shows it — and
            # the sweeps, which skip already-flagged cars, won't raise the
            # same alarm again from the next car on the next pass.
            for sib in await self._visit_siblings(booking):
                if sib.get("status") in {BookingStatus.COMPLETED.value, BookingStatus.CANCELLED.value}:
                    continue
                mirrored = await self.repo.update_by_id(str(sib["_id"]), fields)
                if mirrored:
                    await self._broadcast_booking_changed(mirrored)
        if booking:
            center = await self.center_repo.find_by_id(booking["service_center_id"])
            if center and center.get("manager_id"):
                await self.notifications.notify(
                    center["manager_id"],
                    f"🚨 Urgent — booking {booking['booking_number']}",
                    note,
                    NotificationType.BOOKING,
                    booking_id,
                    send_whatsapp=send_whatsapp,
                )
            # The single hook every automated sweep in main.py's
            # _reminder_loop goes through (captain_not_started,
            # captain_not_reached, captain_delay, service_overrun) as well
            # as the manual report_risk path — covers the manager queue's
            # "Flagged issues" section with a live push for all of them at
            # once, not just the manager-initiated actions above.
            await self._broadcast_booking_changed(booking)
        return True

    async def notify_center_manager_for_booking(self, booking: dict, title: str, message: str, send_whatsapp: bool = True) -> None:
        """Notifies the booking's service center manager without touching
        issue_flag — for reminders that are just "please act on this soon"
        (e.g. still-unassigned nudges) rather than a state change that needs
        an explicit resolve/reschedule, which is what flag_issue is for."""
        center = await self.center_repo.find_by_id(booking["service_center_id"])
        if center and center.get("manager_id"):
            await self.notifications.notify(
                center["manager_id"], title, message, NotificationType.BOOKING, str(booking["_id"]), send_whatsapp=send_whatsapp
            )

    async def update_priority(self, booking_id: str, priority: str, actor_id: str, actor_role: str, actor_center_id: str | None) -> tuple[dict, str]:
        """Manager/admin can set priority on any booking in their own
        center; a captain may only set it on a booking currently assigned
        to them (e.g. flagging something urgent they discovered on-site).
        Customers can't reach this at all — no route exposes it to them.
        Returns (updated_booking, old_priority) so the caller can audit
        the actual before/after value."""
        booking = await self.repo.find_by_id(booking_id)
        if not booking:
            raise NotFoundException("Booking not found")
        if actor_role == "captain":
            if booking.get("captain_id") != actor_id:
                raise ForbiddenException("You can only update priority on your own assigned booking")
        else:
            ensure_own_center(actor_role, actor_center_id, booking["service_center_id"])
        old_priority = booking.get("priority", "medium")
        updated = await self.repo.update_by_id(booking_id, {"priority": priority})
        # Priority is the visit's — a manager marks the trip urgent, not
        # one of the cars on it.
        for sib in await self._visit_siblings(booking):
            mirrored = await self.repo.update_by_id(str(sib["_id"]), {"priority": priority})
            if mirrored:
                await self._broadcast_booking_changed(mirrored)
        await self._broadcast_booking_changed(updated)
        return serialize_doc(updated), old_priority

    async def resolve_issue(self, booking_id: str, resolved_by: str, note: str, actor_role: str, actor_center_id: str | None) -> dict:
        booking = await self.repo.find_by_id(booking_id)
        if not booking:
            raise NotFoundException("Booking not found")
        ensure_own_center(actor_role, actor_center_id, booking["service_center_id"])
        # Remember WHICH flag this was, even after clearing issue_flag —
        # resolved_issue_flag is what lets flag_late_to_start later tell
        # "captain_not_started was already acknowledged" apart from "never
        # flagged at all" (see the model field's own comment).
        resolved_flag = {"issue_flag": None, "issue_resolved": True, "resolved_issue_flag": booking.get("issue_flag")}
        updated = await self.repo.update_by_id(booking_id, resolved_flag)
        await self._record_history(booking_id, BookingStatus(booking["status"]), resolved_by, note)
        # Resolving a visit-wide flag on one car resolves it on the others
        # carrying the same flag — the manager fixed the trip, not a car.
        if booking.get("issue_flag") in VISIT_WIDE_FLAGS:
            for sib in await self._visit_siblings(booking):
                if sib.get("issue_flag") != booking.get("issue_flag") or sib.get("issue_resolved"):
                    continue
                mirrored = await self.repo.update_by_id(str(sib["_id"]), resolved_flag)
                await self._record_history(str(sib["_id"]), BookingStatus(sib["status"]), resolved_by, note)
                if mirrored:
                    await self._broadcast_booking_changed(mirrored)
        await self._broadcast_booking_changed(updated)
        return serialize_doc(updated)

    async def update_captain_location(self, captain_id: str, latitude: float, longitude: float,
                                      source: str = "ping", booking_id: str | None = None) -> None:
        """Updates the captain's own user document with a real GPS
        position. Originally called only from the three discrete moments
        this service already captures live GPS (start_heading, before/after
        photos); now ALSO called periodically while a captain has an active
        job, from the dedicated ping endpoint (see
        BookingController.ping_captain_location / ws_routes' "captain-location"
        channel) — see UserModel.last_known_location's docstring for the
        full history of what this is and isn't. Pushed live over the
        "captain-location:{captain_id}" channel directly (not just a
        "changed" ping) since the payload itself is small and safe to send
        as-is — a manager/admin watching doesn't need a separate fetch.

        Every call also appends a breadcrumb row to captain_locations (the
        auditable trail behind the manager map — see CaptainLocationModel);
        `source`/`booking_id` say which capture moment produced it."""
        now = now_ist()
        await self.user_repo.update_by_id(captain_id, {"last_known_location": {"latitude": latitude, "longitude": longitude}, "last_location_at": now})
        await self.location_repo.record(captain_id, latitude, longitude, now, source=source, booking_id=booking_id)
        await ws_manager.broadcast(
            f"captain-location:{captain_id}",
            {"type": "captain_location", "channel": f"captain-location:{captain_id}", "latitude": latitude, "longitude": longitude, "captured_at": now.isoformat()},
        )

    async def _ensure_captain_not_on_leave(self, captain_id: str, booking: dict) -> None:
        """APPROVED leave finally MEANS something: a captain on leave for the
        booking's date can't be assigned to it. Leave dates are stored as
        ISO date strings, so plain string comparison is correct."""
        date_key = to_ist(booking["scheduled_date"]).strftime("%Y-%m-%d")
        on_leave = await self.db.leave_requests.find_one({
            "captain_id": captain_id,
            "status": "approved",
            "start_date": {"$lte": date_key},
            "end_date": {"$gte": date_key},
            "is_deleted": {"$ne": True},
        })
        if on_leave:
            raise BadRequestException(
                f"This captain is on approved leave from {on_leave['start_date']} to {on_leave['end_date']} — pick someone else."
            )

    async def has_active_job(self, captain_id: str) -> bool:
        """Whether this captain currently has a booking in one of the
        "actively working" statuses — gates the location-ping endpoint so
        this never becomes always-on background tracking, only tracking
        tied to a real, current job (same restraint as the discrete-capture
        design it's extending)."""
        count = await self.repo.count({"captain_id": captain_id, "status": {"$in": list(ACTIVE_CAPTAIN_STATUSES)}})
        return count > 0

    async def _broadcast_booking_changed(self, booking: dict) -> None:
        """Fired after every write that changes a booking's status,
        assignment, or priority — a single "go refetch" ping (never the
        payload itself, see ws_manager's module docstring for why) to every
        channel that could plausibly be showing this booking right now:
        the booking's own detail view, its center's manager queue, and the
        customer's/captain's own lists."""
        booking_id = str(booking.get("_id") or booking.get("id") or "")
        if not booking_id:
            return
        payload = {"type": "changed", "booking_id": booking_id}
        await ws_manager.broadcast(f"booking:{booking_id}", {**payload, "channel": f"booking:{booking_id}"})
        center_id = booking.get("service_center_id")
        if center_id:
            await ws_manager.broadcast(f"center-bookings:{center_id}", {**payload, "channel": f"center-bookings:{center_id}"})
        customer_id = booking.get("customer_id")
        if customer_id:
            await ws_manager.broadcast(f"user:{customer_id}", {**payload, "channel": f"user:{customer_id}"})
        captain_id = booking.get("captain_id")
        if captain_id:
            await ws_manager.broadcast(f"user:{captain_id}", {**payload, "channel": f"user:{captain_id}"})

    async def _broadcast_slots_changed(self, service_center_id: str, date_str: str) -> None:
        """Fired after any write that changes a slot's booked_count,
        capacity, or is_closed state — customer/manager slot pickers
        subscribed to this exact (center, date) refetch immediately instead
        of waiting out their polling interval."""
        await ws_manager.broadcast(
            f"slots:{service_center_id}:{date_str}",
            {"type": "changed", "channel": f"slots:{service_center_id}:{date_str}", "service_center_id": service_center_id, "date": date_str},
        )

    async def charge_distance_km(self, center: dict | None, address: dict | None, straight_km: float) -> tuple[float, str]:
        """The distance a customer's distance charge is priced on: the ROAD
        distance from the center to their pin (route_service.charge_road_km,
        cached so quote and booking agree). A pinless address keeps the
        straight-line estimate it was matched on."""
        from app.services.route_service import charge_road_km

        c_lat, c_lng = self._center_coords(center)
        road = await charge_road_km(self.db, c_lat, c_lng, (address or {}).get("latitude"), (address or {}).get("longitude"))
        if not road:
            return float(straight_km or 0.0), "straight_line"
        return float(road["km"]), road["source"]

    @staticmethod
    def _center_coords(center: dict | None) -> tuple[float | None, float | None]:
        """Centers store coordinates under location.* (admin-created) or at
        the top level (some fixtures) — accept both."""
        if not center:
            return None, None
        loc = center.get("location") or {}
        return (
            center.get("latitude", loc.get("latitude")),
            center.get("longitude", loc.get("longitude")),
        )

    async def travel_status(self, booking_id: str, actor_id: str, actor_role: str, actor_center_id: str | None = None) -> dict:
        """Live distance/ETA picture for one booking:
          - store -> customer (persisted at creation; computed lazily here
            for older bookings that predate the field);
          - captain -> customer (Routes API against the captain's last
            pinged position) while a captain is assigned/en route — this
            is what the customer's "your captain is ~12 min away" reads.
        Authz mirrors get_booking: customers only their own, managers only
        their center, the assigned captain, admin everything."""
        booking = await self.repo.find_by_id(booking_id)
        if not booking:
            raise NotFoundException("Booking not found")
        if actor_role == "customer" and booking["customer_id"] != actor_id:
            raise NotFoundException("Booking not found")
        if actor_role == "captain" and booking.get("captain_id") != actor_id:
            raise NotFoundException("Booking not found")
        if actor_role == "manager":
            ensure_own_center(actor_role, actor_center_id, booking["service_center_id"])

        from app.services.route_service import road_distance_eta

        address = await self._address_of(booking)
        dest_lat = (address or {}).get("latitude")
        dest_lng = (address or {}).get("longitude")

        # Store -> customer: persisted at creation; older bookings get it
        # computed once here and written back.
        store = {
            "km": booking.get("travel_distance_km"),
            "minutes": booking.get("travel_eta_minutes"),
            "source": booking.get("travel_estimate_source"),
        }
        if store["km"] is None and dest_lat is not None:
            center = await self.center_repo.find_by_id(booking["service_center_id"])
            c_lat, c_lng = self._center_coords(center)
            computed = await road_distance_eta(c_lat, c_lng, dest_lat, dest_lng)
            if computed:
                store = computed
                await self.repo.update_by_id(booking_id, {
                    "travel_distance_km": computed["km"],
                    "travel_eta_minutes": computed["minutes"],
                    "travel_estimate_source": computed["source"],
                })

        # Captain -> customer, only while it means something.
        captain_leg = None
        if booking.get("captain_id") and booking.get("status") in (
            BookingStatus.ASSIGNED.value, BookingStatus.CAPTAIN_ON_THE_WAY.value,
        ) and dest_lat is not None:
            captain = await self.user_repo.find_by_id(booking["captain_id"])
            loc = (captain or {}).get("last_known_location")
            if loc:
                leg = await road_distance_eta(loc.get("latitude"), loc.get("longitude"), dest_lat, dest_lng)
                if leg:
                    captain_leg = {
                        **leg,
                        "captain_name": (captain or {}).get("full_name"),
                        "location_updated_at": from_stored(captain["last_location_at"]).isoformat() if captain.get("last_location_at") else None,
                    }

        return {
            "status": booking.get("status"),
            "store_to_customer": store if store.get("km") is not None else None,
            "captain_to_customer": captain_leg,
        }

    async def _service_label(self, booking: dict) -> str:
        """What this booking IS, in the customer's words — "Jet Wash", not
        "BK0014". Notifications lead with this: a booking number means
        nothing to someone glancing at their phone. Best-effort; the
        booking number stays alongside as the reference."""
        try:
            if booking.get("combo_name"):
                return str(booking["combo_name"])
            names = []
            for sid in booking.get("service_ids") or []:
                svc = await self.service_repo.find_by_id(sid)
                if svc and svc.get("name"):
                    names.append(svc["name"])
            return ", ".join(names) or "Your service"
        except Exception:  # noqa: BLE001
            return "Your service"

    async def _wa_ctx(self, booking: dict) -> tuple[str, str]:
        """(customer first name, service names) for the per-event WhatsApp
        templates — best-effort, never raises."""
        try:
            customer = await self.user_repo.find_by_id(booking["customer_id"])
            name = ((customer or {}).get("full_name") or "there").split(" ")[0]
            names = []
            for sid in booking.get("service_ids") or []:
                svc = await self.service_repo.find_by_id(sid)
                if svc:
                    names.append(svc.get("name", ""))
            return name, ", ".join(n for n in names if n) or "Vehicle care"
        except Exception:  # noqa: BLE001
            return "there", "Vehicle care"

    async def _vehicle_label_for_wa(self, booking: dict) -> str:
        """Short car label for customer WhatsApp notifications."""
        try:
            if booking.get("vehicle_label"):
                return str(booking["vehicle_label"])
            if booking.get("vehicle_registration_number"):
                return str(booking["vehicle_registration_number"])
            vehicle_id = booking.get("vehicle_id")
            if vehicle_id:
                vehicle = await self.vehicle_repo.find_by_id(vehicle_id)
                if vehicle:
                    bits = [vehicle.get("brand"), vehicle.get("model"), vehicle.get("registration_number")]
                    label = " ".join(str(b) for b in bits if b)
                    if label:
                        return label
            vehicle_type = booking.get("vehicle_type")
            if vehicle_type:
                vt = await self.vehicle_type_repo.find_by_id(vehicle_type)
                if vt and vt.get("name"):
                    return str(vt["name"])
                return str(vehicle_type)
        except Exception:  # noqa: BLE001
            pass
        return "Vehicle"

    async def _wa_details(self, booking: dict, cars: list[dict] | None = None) -> tuple[str, str, str, str, str, str]:
        """Shared compact fields for service WhatsApp templates.

        Returns: service label, booking reference, date, slot, car label,
        service code. It is intentionally best-effort because notifications
        must never block the booking state change they describe.
        """
        try:
            cars = cars if cars is not None else await self._visit_cars(booking)
            service = await self._visit_label(cars)
            reference = self._visit_numbers(cars) if len(cars) > 1 else str(booking.get("booking_number") or "")
            date_value = booking.get("scheduled_date")
            date = to_ist(date_value).strftime("%d %b %Y") if date_value else ""
            slot = format_slot_12h(booking.get("scheduled_slot"))
            vehicle = f"{len(cars)} vehicles" if len(cars) > 1 else await self._vehicle_label_for_wa(booking)
            code = str(booking.get("service_code") or "-")
            return service, reference, date, slot, vehicle, code
        except Exception:  # noqa: BLE001
            return "Vehicle care", str(booking.get("booking_number") or ""), "", str(booking.get("scheduled_slot") or ""), "Vehicle", str(booking.get("service_code") or "-")

    # -- Visits: several cars, ONE trip ----------------------------------
    #
    # A multi-car visit is N real bookings sharing a booking_group_id. Each
    # car keeps its own plate check, photos and proof of work, but the
    # captain drives once, the customer booked once and the manager
    # dispatches once. Everything below exists so those "once" things
    # happen once: one departure, one release, one reschedule, one seat,
    # one reminder, one confirmation.

    async def _visit_cars(self, booking: dict, session=None) -> list[dict]:
        """Every live car on this booking's visit in working order — or just
        the booking itself when it isn't on one. Cancelled cars are left
        out: they're no longer on the trip."""
        group_id = booking.get("booking_group_id")
        if not group_id:
            return [booking]
        rows = await self.repo.collection.find(
            {"booking_group_id": group_id, "is_deleted": {"$ne": True}, "status": {"$ne": BookingStatus.CANCELLED.value}},
            session=session,
        ).sort("group_offset_minutes", 1).to_list(length=20)
        return rows or [booking]

    async def _visit_siblings(self, booking: dict) -> list[dict]:
        """The OTHER live cars on this booking's visit (none for a single)."""
        return [b for b in await self._visit_cars(booking) if str(b["_id"]) != str(booking["_id"])]

    @staticmethod
    def _leads_visit(booking: dict, cars: list[dict]) -> bool:
        """Is this the first car in working order? Whatever should happen
        ONCE per trip happens on this car and stays quiet on the others."""
        return not cars or str(cars[0]["_id"]) == str(booking["_id"])

    @staticmethod
    def _one_per_visit(bookings: list[dict]) -> list[dict]:
        """Collapse a sweep's candidates to one car per visit (the first in
        working order) — the manager hears about the trip once."""
        seen: set[str] = set()
        out: list[dict] = []
        for b in sorted(bookings, key=lambda b: int(b.get("group_offset_minutes") or 0)):
            gid = b.get("booking_group_id")
            if gid:
                if gid in seen:
                    continue
                seen.add(gid)
            out.append(b)
        return out

    async def _visit_label(self, cars: list[dict]) -> str:
        """"2 vehicles · Waterless Service + Deep Cleaning" for a visit; the
        plain service name for a single booking."""
        labels = [await self._service_label(c) for c in cars]
        if len(cars) <= 1:
            return labels[0] if labels else "Your service"
        return f"{len(cars)} vehicles · " + " + ".join(labels)

    @staticmethod
    def _visit_numbers(cars: list[dict]) -> str:
        return " + ".join(str(c.get("booking_number") or "") for c in cars)

    async def _mark_visit(self, booking_id: str, fields: dict) -> None:
        """Stamp a bookkeeping field on a booking AND every other car on its
        visit — a reminder sent for the trip is sent for all of them, or
        the next sweep pass would send it again from the next car."""
        booking = await self.repo.find_by_id(booking_id)
        if not booking:
            return
        ids = [c["_id"] for c in await self._visit_cars(booking)]
        if booking["_id"] not in ids:
            ids.append(booking["_id"])
        await self.repo.collection.update_many({"_id": {"$in": ids}}, {"$set": {**fields, "updated_at": datetime.now(timezone.utc)}})

    # -- Slot configuration changes (audit SLOT-01) ---------------------

    async def slot_change_blockers(self, center: dict, changed: dict | None = None, *, new_platform_slot_minutes: int | None = None) -> list[str]:
        """Why a center's slots can't change shape right now. Slot keys are
        the capacity bookkeeping's identity: a new slot length or new hours
        mint new keys, and every live booking, override and scheduled
        capacity change filed under the old ones silently lost its limit
        (6 more sold into a full slot). Empty list when the change leaves
        the center's slots exactly as they are, or touches nothing live.

        `changed`: the center's fields after the edit. `new_platform_slot_minutes`:
        the platform "Slot Length" after the edit (centers without their own)."""
        policy = await self.policy_service.get_policy()
        new_policy = dict(policy)
        if new_platform_slot_minutes:
            new_policy["slot_duration_minutes"] = new_platform_slot_minutes
        old_keys = [x["key"] for x in center_slots(center, policy)]
        new_keys = [x["key"] for x in center_slots(changed or center, new_policy)]
        if old_keys == new_keys:
            return []
        center_id = str(center["_id"])
        today = now_ist().strftime("%Y-%m-%d")
        today_start = self._sweep_day(0) - timedelta(hours=6)  # tolerates legacy rows stored shifted (BOOK-01)
        reasons: list[str] = []

        live_query = {
            "service_center_id": center_id, "is_deleted": {"$ne": True},
            "status": {"$in": list(BookingRepository.OPEN_STATUSES)}, "scheduled_date": {"$gte": today_start},
        }
        upcoming = await self.repo.collection.count_documents(live_query)
        if upcoming:
            first = await self.repo.collection.find_one(live_query, {"scheduled_date": 1}, sort=[("scheduled_date", 1)])
            first_day = to_ist(first["scheduled_date"]).strftime("%d %b %Y")
            reasons.append(
                f"{upcoming} upcoming booking{'s' if upcoming != 1 else ''} (the first on {first_day}) would lose their slot and "
                "its capacity limit — move, finish or cancel them first, or make this change after the last one"
            )
        gone = sorted(set(old_keys) - set(new_keys))
        override_query = {
            "service_center_id": center_id, "date": {"$gte": today}, "slot_key": {"$in": gone},
            "$or": [{"is_override": True}, {"is_closed": True}, {"held_count": {"$gt": 0}}, {"booked_count": {"$gt": 0}}],
        }
        overrides = await self.slot_capacity_repo.collection.count_documents(override_query)
        if overrides:
            first = await self.slot_capacity_repo.collection.find_one(override_query, {"date": 1}, sort=[("date", 1)])
            reasons.append(
                f"{overrides} slot capacity override{'s' if overrides != 1 else ''} or closure{'s' if overrides != 1 else ''} "
                f"(from {datetime.strptime(first['date'], '%Y-%m-%d').strftime('%d %b %Y')}) would be lost — clear them first"
            )
        scheduled = await self.capacity_policy_service.repo.collection.find(
            {"service_center_id": center_id, "effective_date": {"$gt": today}, "is_deleted": {"$ne": True}}
        ).sort("effective_date", 1).to_list(length=50)
        stale = [c for c in scheduled if set((c.get("slot_distribution") or {}).keys()) - set(new_keys)]
        if stale:
            day = datetime.strptime(stale[0]["effective_date"], "%Y-%m-%d").strftime("%d %b %Y")
            reasons.append(f"the capacity change scheduled for {day} is split over the current slots — cancel it first and schedule it again afterwards")
        return reasons

    # -- Counter reconciliation (audit item 8 — the safety net) -----------

    async def reconcile_slot_counters(self, repair: bool = False, *, actor_id: str = "system", actor_role: str = "system") -> dict:
        """Recount, for today and every later date, how many bookings OWN a
        seat (holds_seat + seat_key) against each stored slot counter
        (booked_count), how many slot holds exist against held_count, and
        each daily counter against its day's owned seats.

        repair=False (the default, and the nightly run): report only, never
        writes. repair=True: first records ownership on any legacy rows
        (backfill_seat_ownership), then rewrites each drifted counter inside
        a transaction that recounts it — so a booking racing the repair is
        either counted or conflicts and retries — logs it and writes an
        audit row. A correct counter is never written.

        Returns {"as_of", "repair", "checked", "drift": [{service_center_id,
        date, slot_key (None for a day row), kind "slot"|"held"|"daily",
        stored, expected, repaired}], "repaired", "legacy_rows"}."""
        today = now_ist().strftime("%Y-%m-%d")
        legacy_rows = 0
        if repair:
            legacy_rows = (await backfill_seat_ownership(self.db, force=True))["bookings"]

        owned: dict[tuple, int] = {}
        async for row in self.repo.collection.aggregate([
            {"$match": {"holds_seat": True, "seat_key.date": {"$gte": today}}},
            {"$group": {"_id": {"c": "$seat_key.service_center_id", "d": "$seat_key.date", "s": "$seat_key.slot_key"}, "n": {"$sum": 1}}},
        ]):
            owned[(row["_id"]["c"], row["_id"]["d"], row["_id"]["s"])] = int(row["n"])
        if not repair:
            # Rows from before ownership was recorded, judged by the same
            # rule the backfill will apply — so a report run before the
            # migration doesn't call every legacy seat a leak.
            for visit in await self._legacy_visits(self._sweep_day(-1)):
                legacy_rows += sum(1 for c in visit if "holds_seat" not in c)
                holder_ids = self._legacy_seat_holders(visit)
                for holder in (c for c in visit if str(c["_id"]) in holder_ids):
                    key = self._seat_key_of(holder)
                    if key["date"] >= today:
                        k = (key["service_center_id"], key["date"], key["slot_key"])
                        owned[k] = owned.get(k, 0) + 1
        held: dict[tuple, int] = {}
        async for row in self.db.slot_holds.aggregate([
            {"$match": {"date": {"$gte": today}}},
            {"$group": {"_id": {"c": "$service_center_id", "d": "$date", "s": "$slot_key"}, "n": {"$sum": 1}}},
        ]):
            held[(row["_id"]["c"], row["_id"]["d"], row["_id"]["s"])] = int(row["n"])
        counters = {
            (d["service_center_id"], d["date"], d["slot_key"]): d
            for d in await self.slot_capacity_repo.collection.find({"date": {"$gte": today}}).to_list(length=None)
        }

        drift: list[dict] = []
        for key in sorted(set(counters) | set(owned)):
            doc = counters.get(key)
            stored = int(doc.get("booked_count") or 0) if doc else None
            if stored != owned.get(key, 0) and not (doc is None and owned.get(key, 0) == 0):
                drift.append({"service_center_id": key[0], "date": key[1], "slot_key": key[2], "kind": "slot",
                              "stored": stored, "expected": owned.get(key, 0), "repaired": False})
            stored_held = int((doc or {}).get("held_count") or 0)
            if doc is not None and stored_held != held.get(key, 0):
                drift.append({"service_center_id": key[0], "date": key[1], "slot_key": key[2], "kind": "held",
                              "stored": stored_held, "expected": held.get(key, 0), "repaired": False})
        day_owned: dict[tuple, int] = {}
        for (center_id, day, _slot), n in owned.items():
            day_owned[(center_id, day)] = day_owned.get((center_id, day), 0) + n
        for doc in await self.daily_capacity_repo.collection.find({"date": {"$gte": today}}).to_list(length=None):
            key = (doc["service_center_id"], doc["date"])
            if int(doc.get("booked_count") or 0) != day_owned.get(key, 0):
                drift.append({"service_center_id": key[0], "date": key[1], "slot_key": None, "kind": "daily",
                              "stored": int(doc.get("booked_count") or 0), "expected": day_owned.get(key, 0), "repaired": False})

        repaired = 0
        if repair:
            for row in drift:
                if await self._repair_counter(row):
                    row["repaired"] = True
                    repaired += 1
                    logger.warning("Slot counter repaired: %s", row)
                    from app.services.audit_service import AuditService

                    await AuditService(self.db).log_action(
                        actor_id, actor_role, "RECONCILE_SLOT_COUNTERS", "bookings", None,
                        {k: row[k] for k in ("date", "slot_key", "kind", "stored", "expected")},
                        service_center_id=row["service_center_id"],
                    )
        elif drift:
            logger.warning("Slot counter drift (report only): %s rows, e.g. %s", len(drift), drift[:3])
        return {
            "as_of": today, "repair": repair, "checked": len(set(counters) | set(owned)),
            "drift": drift, "repaired": repaired, "legacy_rows": legacy_rows,
        }

    async def _repair_counter(self, row: dict) -> bool:
        """Rewrite ONE counter from a recount made inside the same
        transaction. False when it turned out to be right already."""
        center_id, day, slot = row["service_center_id"], row["date"], row["slot_key"]

        async def _fix(session):
            now = datetime.now(timezone.utc)
            if row["kind"] == "daily":
                expected = await self._seats_owned_on(center_id, day, session=session)
                result = await self.daily_capacity_repo.collection.update_one(
                    {"service_center_id": center_id, "date": day, "booked_count": {"$ne": expected}},
                    {"$set": {"booked_count": expected, "updated_at": now}}, session=session,
                )
                return bool(result.modified_count)
            key_filter = {"service_center_id": center_id, "date": day, "slot_key": slot}
            if row["kind"] == "held":
                expected = await self.db.slot_holds.count_documents(key_filter, session=session)
                result = await self.slot_capacity_repo.collection.update_one(
                    {**key_filter, "held_count": {"$ne": expected}}, {"$set": {"held_count": expected, "updated_at": now}}, session=session,
                )
                return bool(result.modified_count)
            expected = await self.repo.collection.count_documents(
                {"holds_seat": True, "seat_key.service_center_id": center_id, "seat_key.date": day, "seat_key.slot_key": slot}, session=session,
            )
            if not await self.slot_capacity_repo.collection.find_one(key_filter, {"_id": 1}, session=session):
                center = await self.center_repo.find_by_id(center_id)
                capacity = await self._default_slot_capacity(center, day, slot) if center else 0
                await self.slot_capacity_repo.get_or_init(
                    key_filter, {"capacity": capacity or 0, "booked_count": expected, "held_count": 0, "is_closed": False}, session=session,
                )
                return True
            result = await self.slot_capacity_repo.collection.update_one(
                {**key_filter, "booked_count": {"$ne": expected}}, {"$set": {"booked_count": expected, "updated_at": now}}, session=session,
            )
            return bool(result.modified_count)

        async with await self.db.client.start_session() as session:
            return await session.with_transaction(_fix)

    async def _legacy_visits(self, since: datetime) -> list[list[dict]]:
        """Bookings dated `since` or later that carry no ownership record
        yet, grouped by visit WITH their recorded siblings."""
        rows = await self.repo.collection.find(
            {"holds_seat": {"$exists": False}, "scheduled_date": {"$gte": since - timedelta(hours=6)}}
        ).to_list(length=None)
        visits: dict[str, list[dict]] = {}
        for row in rows:
            visits.setdefault(row.get("booking_group_id") or str(row["_id"]), []).append(row)
        out: list[list[dict]] = []
        for key, cars in visits.items():
            if cars[0].get("booking_group_id"):
                cars = await self.repo.collection.find({"booking_group_id": key}).to_list(length=20)
            out.append(cars)
        return out

    async def _record_history(
        self, booking_id: str, status: BookingStatus, changed_by: str | None, note: str | None, *, changes: list[dict] | None = None,
    ) -> None:
        """`changes`: a field-by-field diff [{field, from, to}] (booking
        edits, reschedules, on-site add-ons) shown on the timeline."""
        row = {
            "booking_id": booking_id,
            "status": status.value if hasattr(status, "value") else status,
            "changed_by": changed_by,
            "note": note,
        }
        if changes:
            row["changes"] = changes
        await self.history_repo.create(row)


# -- One-time data migrations (run from main's deferred startup) -------------

_LAST_COMPLETED_MIGRATION = "users_last_completed_at_v1"


async def backfill_last_completed_at(db: AsyncIOMotorDatabase, batch_size: int = 500) -> int:
    """users.last_completed_at for the completed bookings that existed
    before completion started stamping it (see _after_visit_completed).
    Idempotent: $max only ever moves it forward, and the done-marker in
    `migrations` turns every later boot into a single find_one. Two
    instances booting at once both running it is harmless for the same
    reason. Returns how many customers it matched."""
    from pymongo import UpdateOne

    if await db.migrations.find_one({"_id": _LAST_COMPLETED_MIGRATION}):
        return 0
    cursor = db.bookings.aggregate(
        [
            {"$match": {"status": BookingStatus.COMPLETED.value, "is_deleted": {"$ne": True}, "completed_at": {"$ne": None}}},
            {"$group": {"_id": "$customer_id", "last": {"$max": "$completed_at"}}},
        ],
        allowDiskUse=True,
        batchSize=batch_size,
    )
    ops: list = []
    matched = 0
    async for row in cursor:
        customer_id, last = row.get("_id"), row.get("last")
        if not isinstance(customer_id, str) or not ObjectId.is_valid(customer_id) or last is None:
            continue
        ops.append(UpdateOne({"_id": ObjectId(customer_id)}, {"$max": {"last_completed_at": last}}))
        if len(ops) >= batch_size:
            matched += (await db.users.bulk_write(ops, ordered=False)).matched_count
            ops = []
    if ops:
        matched += (await db.users.bulk_write(ops, ordered=False)).matched_count
    await db.migrations.update_one(
        {"_id": _LAST_COMPLETED_MIGRATION},
        {"$set": {"done_at": datetime.now(timezone.utc), "customers": matched}},
        upsert=True,
    )
    logger.info("Backfilled last_completed_at for %s customers", matched)
    return matched


_SEAT_OWNERSHIP_MIGRATION = "seat_ownership_v1"


async def backfill_seat_ownership(db: AsyncIOMotorDatabase, *, force: bool = False) -> dict:
    """One-time migration for explicit seat ownership (audit BOOK-01..06).

    Bookings written before this release carry no holds_seat/seat_key. For
    every such booking dated yesterday or later it:
      1. repairs a scheduled_date that was stored shifted by an aware input
         (BOOK-01: "2026-10-10T00:00+05:30" stored as 9 Oct 18:30) — the
         true day is read from the slot's own stored start instant;
      2. records who holds the seat, by the rules the counters were kept
         under until now (BookingService._legacy_seat_holders): a live or
         done booking that isn't deleted and isn't a manager-LOGGED job
         holds one; a visit holds ONE, on its first car.
    Counters are NOT touched — where the old rules leaked (BOOK-02/03/06),
    reconcile_slot_counters reports the drift and repair=True fixes it.

    Idempotent and safe to run from every instance at boot (a done marker
    turns later runs into one find_one; `force` re-scans, e.g. before a
    repair). Bookings touched lazily before it runs (any cancel / delete /
    reschedule / restore) get the identical treatment then."""
    if not force and await db.migrations.find_one({"_id": _SEAT_OWNERSHIP_MIGRATION}):
        return {"bookings": 0, "dates_fixed": 0, "skipped": True}
    service = BookingService(db)
    since = service._sweep_day(-1)
    dates_fixed = 0
    touched = 0
    for visit in await service._legacy_visits(since):
        for car in visit:
            if "holds_seat" in car or not car.get("slot_start"):
                continue
            true_day = from_stored(car["slot_start"])
            midnight = datetime(true_day.year, true_day.month, true_day.day)
            if car.get("scheduled_date") != midnight:
                await db.bookings.update_one(
                    {"_id": car["_id"], "holds_seat": {"$exists": False}}, {"$set": {"scheduled_date": midnight}}
                )
                car["scheduled_date"] = midnight
                dates_fixed += 1
        before = sum(1 for c in visit if "holds_seat" not in c)
        await service._backfill_seats(visit)
        touched += before
    await db.migrations.update_one(
        {"_id": _SEAT_OWNERSHIP_MIGRATION},
        {"$set": {"done_at": datetime.now(timezone.utc), "bookings": touched, "dates_fixed": dates_fixed}},
        upsert=True,
    )
    if touched:
        logger.info("Seat ownership recorded on %s legacy bookings (%s dates repaired)", touched, dates_fixed)
    return {"bookings": touched, "dates_fixed": dates_fixed, "skipped": False}


_ADDRESS_SNAPSHOT_MIGRATION = "booking_address_snapshot_v1"


async def backfill_address_snapshots(db: AsyncIOMotorDatabase, *, force: bool = False, batch_size: int = 500) -> int:
    """Every LIVE booking created before bookings kept their own address
    (spec 1.3, the "address side door") gets address_snapshot = its saved
    address as it is now, so a later edit of that saved address can no
    longer move it. Idempotent: only rows without a snapshot are written
    (guarded per row), and a done marker turns later boots into one
    find_one — safe from every instance at once. Finished/cancelled rows
    are left alone (nothing reads their address for work any more).
    Returns how many bookings it wrote."""
    if not force and await db.migrations.find_one({"_id": _ADDRESS_SNAPSHOT_MIGRATION}):
        return 0
    from pymongo import UpdateOne

    written = 0
    cursor = db.bookings.find(
        {"status": {"$in": list(BookingRepository.OPEN_STATUSES)}, "address_snapshot": {"$exists": False}},
        {"_id": 1, "address_id": 1},
    ).batch_size(batch_size)
    pending: list[dict] = []

    async def _flush(rows: list[dict]) -> int:
        ids = [ObjectId(r["address_id"]) for r in rows if r.get("address_id") and ObjectId.is_valid(r["address_id"])]
        found = {str(a["_id"]): a for a in await db.addresses.find({"_id": {"$in": ids}}).to_list(length=None)} if ids else {}
        ops = [
            UpdateOne(
                {"_id": r["_id"], "address_snapshot": {"$exists": False}},
                {"$set": {"address_snapshot": address_snapshot(found[r["address_id"]])}},
            )
            for r in rows if r.get("address_id") in found
        ]
        if not ops:
            return 0
        return (await db.bookings.bulk_write(ops, ordered=False)).modified_count

    async for row in cursor:
        pending.append(row)
        if len(pending) >= batch_size:
            written += await _flush(pending)
            pending = []
    if pending:
        written += await _flush(pending)
    await db.migrations.update_one(
        {"_id": _ADDRESS_SNAPSHOT_MIGRATION}, {"$set": {"done_at": datetime.now(timezone.utc), "bookings": written}}, upsert=True,
    )
    if written:
        logger.info("Address snapshot recorded on %s live bookings", written)
    return written
