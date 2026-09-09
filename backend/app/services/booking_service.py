import re
from datetime import datetime, timedelta, timezone

from motor.motor_asyncio import AsyncIOMotorDatabase
from pymongo.errors import DuplicateKeyError

from app.core.authz import ensure_own_center
from app.core.exceptions import BadRequestException, ForbiddenException, NotFoundException, PhoneNotVerifiedException
from app.core.ws_manager import manager as ws_manager
from app.models.enums import BookingStatus, NotificationType, PaymentMethod, PaymentStatus
from app.repositories.address_repository import AddressRepository
from app.repositories.booking_repository import BookingRepository, BookingStatusHistoryRepository
from app.repositories.captain_location_repository import CaptainLocationRepository
from app.repositories.catalog_repository import ComboOfferRepository, ServiceRepository
from app.repositories.inventory_repository import InventoryRepository
from app.repositories.service_center_repository import ServiceCenterRepository
from app.repositories.slot_capacity_repository import DailyCapacityRepository, SlotCapacityRepository
from app.repositories.user_repository import UserRepository
from app.repositories.vehicle_repository import VehicleRepository
from app.utils.slots import generate_slots
from app.utils.text import normalize_plate
from app.schemas.booking_schema import (
    BookingAssignCaptainRequest,
    BookingCancelRequest,
    BookingCreateRequest,
    BookingRescheduleRequest,
    CaptainCancelRequest,
    HeadingRequest,
    ManagerBookingCreateRequest,
    PhotoCaptureRequest,
    ReassignCaptainRequest,
    ReportRiskRequest,
    VerifyVehicleRequest,
)
from app.services.booking_policy_service import BookingPolicyService
from app.services.capacity_policy_service import CapacityPolicyService
from app.services.coupon_service import CouponService
from app.services.notification_service import NotificationService
from app.services.pricing_service import PricingService
from app.services.profile_service import AddressService, VehicleService
from app.services.subscription_service import UserSubscriptionService
from app.services.wallet_service import WalletService
from app.utils.geo import haversine_km
from app.utils.timezone import from_stored, now_ist, to_ist
from app.utils.serializers import serialize_doc, serialize_list

START_WINDOW_MINUTES = 30
ACTIVE_CAPTAIN_STATUSES = {BookingStatus.ASSIGNED.value, BookingStatus.CAPTAIN_ON_THE_WAY.value, BookingStatus.SERVICE_STARTED.value}
FRAUD_CHECK_EXCLUDED_STATUSES = [BookingStatus.CANCELLED.value]
# CANCELLATION POLICY, phase 1 (the only part enforced in code today):
# customers can self-cancel only while the booking is still unassigned AND
# more than this many hours before the slot starts. Inside the window (or
# once a captain is assigned) the customer-facing cancel path is closed —
# staff can still cancel on their behalf. The published policy's charge
# tiers (₹50 / ₹80 / ₹100, prepaid-only for repeat offenders) are
# documented on the public cancellation-policy page but NOT charged yet.
CUSTOMER_CANCEL_LOCK_HOURS = 4
# Per-unit add-on quantity ceiling — nobody books 50 bike washes at a door.
MAX_ADDON_QTY = 10
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
LATE_START_NUDGE_MINUTES = 5  # nudge both captain and manager this often once the slot's start time has passed with no heading-out

# Flags whose own resolution IS the captain continuing to work the job —
# blocking on them (the way _ensure_no_open_issue does by default) would
# stop a captain from doing the exact thing that clears the flag. Mirrors
# the existing "captain_not_started" exemption on start_heading.
# captain_reported_risk and captain_not_reached are deliberately NOT
# exempted anywhere — those genuinely need a manager decision first.
ARRIVAL_STAGE_EXEMPT_FLAGS = frozenset({"captain_delay", "captain_late_start"})
COMPLETION_EXEMPT_FLAGS = frozenset({"service_overrun", "captain_late_start"})

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
    BookingStatus.PENDING.value: {BookingStatus.ASSIGNED.value, BookingStatus.CANCELLED.value, BookingStatus.RESCHEDULED.value},
    BookingStatus.ASSIGNED.value: {
        BookingStatus.ASSIGNED.value,
        BookingStatus.CAPTAIN_ON_THE_WAY.value,
        BookingStatus.PENDING.value,
        BookingStatus.CANCELLED.value,
        BookingStatus.RESCHEDULED.value,
    },
    BookingStatus.CAPTAIN_ON_THE_WAY.value: {BookingStatus.SERVICE_STARTED.value, BookingStatus.PENDING.value, BookingStatus.CANCELLED.value},
    BookingStatus.SERVICE_STARTED.value: {BookingStatus.COMPLETED.value, BookingStatus.CANCELLED.value},
    BookingStatus.RESCHEDULED.value: {BookingStatus.ASSIGNED.value, BookingStatus.RESCHEDULED.value, BookingStatus.CANCELLED.value},
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
        start = _slot_start_datetime(booking["scheduled_date"], booking["scheduled_slot"])
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
    duration = service_center.get("slot_duration_minutes") or policy["slot_duration_minutes"]
    slots = generate_slots(service_center.get("working_hours_start", "08:00"), service_center.get("working_hours_end", "20:00"), duration)
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
        return slot_start
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
    "captain_start_stage", "late_penalty_pct",
    # The whole issue-flag machinery is internal ops (manager ↔ captain):
    # "Captain started late", geofence flags, missed-window states. The
    # customer must never see their captain publicly flagged (founder
    # call) — they get the status + transparency timeline, nothing else.
    "issue_flag", "issue_notes", "issue_flagged_at", "issue_resolved",
)
_CAPTAIN_HIDDEN_FIELDS = ("platform_earning",)


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


def _redact_financials(booking: dict, actor_role: str) -> dict:
    if actor_role in {"admin", "manager"}:
        return booking
    hidden = _CUSTOMER_HIDDEN_FIELDS if actor_role == "customer" else _CAPTAIN_HIDDEN_FIELDS
    for field in hidden:
        booking.pop(field, None)
    return booking


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

    _BIKE_WORD = re.compile(r"bike|scooter|two.?wheeler", re.IGNORECASE)

    async def _validate_service_mix(self, services: list[dict], vehicle: dict, raw_quantities: dict) -> dict[str, int]:
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
        booking_is_bike = is_bike_type.get(vehicle["vehicle_type"], False)

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
        def adds_bikes(svc: dict) -> bool:
            name = svc.get("name", "")
            return is_addon(svc) and bool(self._BIKE_WORD.search(name)) and "polish" not in name.lower()

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

    async def create_booking(self, customer_id: str, payload: BookingCreateRequest, _skip_verification_gate: bool = False, source: str = "app", _allow_pinless: bool = False) -> dict:
        vehicle = await self.vehicle_repo.find_by_id(payload.vehicle_id)
        if not vehicle or vehicle["owner_id"] != customer_id:
            raise NotFoundException("Vehicle not found")

        address = await self.address_repo.find_by_id(payload.address_id)
        if not address or address["owner_id"] != customer_id:
            raise NotFoundException("Address not found")

        customer = await self.user_repo.find_by_id(customer_id)
        if not customer:
            raise NotFoundException("Customer not found")
        # Phone verification gate: a customer must complete an OTP
        # (POST /auth/verify-phone/request + /confirm) before their FIRST
        # self-service booking; verification now EXPIRES after 90 days
        # so every booking after that skips this. _skip_verification_gate
        # is set only by create_booking_for_customer (a manager/admin
        # booking on the customer's behalf) — staff-initiated bookings are
        # never gated on the customer's own verification status.
        from app.services.auth_service import AuthService

        if not _skip_verification_gate and not AuthService.phone_verification_fresh(customer):
            raise PhoneNotVerifiedException()

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
            quantities = await self._validate_service_mix(services, vehicle, payload.service_quantities or {})

        duration_minutes = sum(s.get("duration_minutes", 30) * quantities[str(s["_id"])] for s in services) or 30

        policy = await self.policy_service.get_policy()
        # Service center must be known BEFORE slot validation — slots are
        # generated from the CENTER's own working hours/slot duration, not
        # a global policy window (see _resolve_slot_window).
        service_center, distance_km = await self._resolve_service_center(address, allow_pinless=_allow_pinless)
        _ensure_within_advance_window(payload.scheduled_date, policy)
        date_str = to_ist(payload.scheduled_date).strftime("%Y-%m-%d")
        slot_start, slot_end = _resolve_slot_window(service_center, payload.scheduled_date, payload.scheduled_slot, policy)
        if _slot_cutoff_passed(slot_end, policy):
            raise BadRequestException("This slot is no longer available to book — please pick another slot.")

        self_conflict = await self._customer_conflict(customer_id, slot_start, slot_end, None)
        if self_conflict:
            raise BadRequestException(
                f"You already have booking {self_conflict['booking_number']} scheduled around this time — "
                "pick a different time or manage that booking first."
            )

        # First-time-offer eligibility is checked against the WHOLE platform, not
        # just this customer's account — the same car plate or phone number
        # having any prior non-cancelled booking (under any account) disqualifies
        # it. This is what stops "new phone number, same car" discount abuse.
        registration_number = normalize_plate(vehicle["registration_number"])
        phone = customer.get("phone")
        vehicle_seen_before = await self.repo.exists_for_registration(registration_number, FRAUD_CHECK_EXCLUDED_STATUSES)
        phone_seen_before = bool(phone) and await self.repo.exists_for_phone(phone, FRAUD_CHECK_EXCLUDED_STATUSES)
        first_time_eligible = not vehicle_seen_before and not phone_seen_before

        vehicle_type = vehicle["vehicle_type"]
        if combo:
            subtotal = self._resolve_price(combo, vehicle_type, first_time_eligible)
        else:
            subtotal = sum(self._resolve_price(s, vehicle_type, first_time_eligible) * quantities[str(s["_id"])] for s in services)

        discount_amount = 0.0
        payment_method = payload.payment_method

        coupon: dict | None = None
        subscription_consumption: dict | None = None
        if payload.subscription_id:
            # Validate-only here — no write yet. The actual deduction
            # (commit_consumption) happens after the booking below is
            # durably created, so a failure in between (pricing split,
            # the insert itself, anything) can never leave a subscription
            # silently decremented for a booking that doesn't exist.
            # customer_id here is the BOOKING's customer (also the target
            # customer when a manager books on someone's behalf) — the
            # subscription must belong to exactly that person.
            subscription_consumption = await self.subscription_service.plan_consumption(
                payload.subscription_id, payload.vehicle_id, services, customer_id
            )
            payment_method = PaymentMethod.SUBSCRIPTION
            discount_amount = await self._subscription_discount(payload.subscription_id, services, vehicle_type, first_time_eligible)
        elif payload.coupon_code:
            coupon, discount_amount = await self.coupon_service.validate_and_compute_discount(
                payload.coupon_code, subtotal, customer_id
            )

        tax_amount = 0.0
        # A discount can legitimately be computed above the subtotal (e.g. a
        # combo priced below the sum of its parts under a full-waive legacy
        # plan) — clamp it, or the booking books a NEGATIVE total and the
        # `total <= 0 → PAID` rule below marks it paid while poisoning every
        # revenue aggregate downstream.
        discount_amount = min(discount_amount, subtotal)
        total_amount = round(max(0.0, subtotal - discount_amount) + tax_amount, 2)

        primary_service = services[0] if services else None
        split = await self.pricing_service.calculate_split(subtotal, distance_km, primary_service)

        manager_id = service_center.get("manager_id")
        from app.services.route_service import road_distance_eta

        _center_lat, _center_lng = self._center_coords(service_center)
        _route = await road_distance_eta(
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
            "address_id": payload.address_id,
            "service_center_id": str(service_center["_id"]),
            "service_ids": service_ids,
            # Only quantities above 1 are stored — {sid: 3} means "×3".
            "service_quantities": {sid: q for sid, q in quantities.items() if q > 1},
            "subscription_id": payload.subscription_id,
            "scheduled_date": payload.scheduled_date,
            "scheduled_slot": payload.scheduled_slot,
            "slot_start": slot_start,
            "slot_end": slot_end,
            "duration_minutes": duration_minutes,
            "status": BookingStatus.PENDING.value,
            "awaiting_assignment_since": now_ist(),
            # A subscription booking is only fully PAID if it was entirely
            # waived — a same-visit swap to a costlier service (see
            # _subscription_discount) leaves a real total_amount owed, same
            # as any other pending payment, not a false "already paid".
            "payment_status": PaymentStatus.PAID.value if payment_method == PaymentMethod.SUBSCRIPTION and total_amount <= 0 else PaymentStatus.PENDING.value,
            "payment_method": payment_method.value if hasattr(payment_method, "value") else payment_method,
            "subtotal": round(subtotal, 2),
            "discount_amount": round(discount_amount, 2),
            "tax_amount": tax_amount,
            "total_amount": total_amount,
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
            "manager_notified_at": now_ist() if manager_id else None,
        }

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
        async def _do_create(session):
            await self._reserve_slot_capacity(
                session, service_center, date_str, payload.scheduled_slot,
                holder_ids=[customer_id, getattr(payload, "hold_key", None)],
            )
            return await self.repo.create(booking_doc, session=session)

        try:
            async with await self.db.client.start_session() as session:
                created = await session.with_transaction(_do_create)
        except DuplicateKeyError:
            raise BadRequestException("You already have a booking for this vehicle in this slot.")
        booking_id = str(created["_id"])

        if subscription_consumption is not None:
            await self.subscription_service.commit_consumption(payload.subscription_id, subscription_consumption)

        if coupon is not None:
            # record_usage needs the coupon's real _id, not its human-readable
            # code — passing payload.coupon_code here used to raise an
            # uncaught bson.errors.InvalidId on every coupon-code booking,
            # AFTER the booking above had already been created (so the
            # customer saw a hard error for a booking that actually existed),
            # and the mis-keyed usage row meant per-user usage limits could
            # never actually be enforced (count_for_user looks up by the real
            # coupon _id and would never find it).
            await self.coupon_service.record_usage(str(coupon["_id"]), customer_id, booking_id)

        await self._record_history(booking_id, BookingStatus.PENDING, customer_id, "Booking created")
        wa_name, wa_services = await self._wa_ctx(created)
        await self.notifications.notify(
            customer_id,
            "Booking confirmed",
            f"Your booking {created['booking_number']} has been received and is pending assignment.",
            NotificationType.BOOKING,
            booking_id,
            wa_event="booking_confirmed",
            wa_params=[wa_name, wa_services, date_str, created.get("scheduled_slot", ""), created["booking_number"]],
        )
        if service_center.get("manager_id"):
            await self.notifications.notify(
                service_center["manager_id"],
                "New booking in your area",
                f"Booking {created['booking_number']} needs a captain assigned.",
                NotificationType.BOOKING,
                booking_id,
            )
        await self._broadcast_booking_changed(created)
        await self._broadcast_slots_changed(str(service_center["_id"]), date_str)
        return serialize_doc(created)

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
            created_address = await AddressService(self.db).create(payload.customer_id, payload.new_address)
            address_id = created_address["id"]

        booking_request = BookingCreateRequest(
            vehicle_id=vehicle_id,
            address_id=address_id,
            service_ids=payload.service_ids,
            service_quantities=payload.service_quantities or {},
            combo_id=payload.combo_id,
            scheduled_date=payload.scheduled_date,
            scheduled_slot=payload.scheduled_slot,
            payment_method=payload.payment_method,
            coupon_code=payload.coupon_code,
            subscription_id=payload.subscription_id,
            customer_notes=payload.customer_notes,
            alternate_contact_name=payload.alternate_contact_name,
            alternate_contact_phone=payload.alternate_contact_phone,
        )
        # Staff-initiated — never gated on the customer's own phone
        # verification (see create_booking's _skip_verification_gate).
        result = await self.create_booking(payload.customer_id, booking_request, _skip_verification_gate=True, source="staff", _allow_pinless=True)
        await self._record_history(
            result["id"], BookingStatus.PENDING, actor_id, f"Booking created by staff on behalf of customer {payload.customer_id}"
        )
        return result

    async def report_risk(self, booking_id: str, captain_id: str, note: str | None) -> dict:
        """Captain self-reports they're at risk of running late for an
        upcoming booking (still `assigned`, hasn't started heading yet)
        because their current job is running long. Reuses the same
        flag_issue()/notify path as the automatic captain_not_reached/
        captain_delay sweeps, so the manager sees it through one channel."""
        booking = await self.repo.find_by_id(booking_id)
        if not booking:
            raise NotFoundException("Booking not found")
        if booking.get("captain_id") != captain_id:
            raise ForbiddenException("You are not assigned to this booking")
        if booking["status"] != BookingStatus.ASSIGNED.value:
            raise BadRequestException(
                "Only an upcoming booking you haven't started heading to yet can be flagged this way — "
                "if you've already started heading, release the job instead if you truly can't make it."
            )
        if booking.get("issue_flag") and not booking.get("issue_resolved", True):
            raise BadRequestException("This booking already has an open issue flagged.")

        note_text = note or "Captain reported they may run late for this booking due to their current job."
        await self.flag_issue(booking_id, "captain_reported_risk", note_text)
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
        if first_time_eligible and first_time_price is not None:
            return first_time_price
        return base_price

    async def _subscription_discount(self, subscription_id: str, services: list[dict], vehicle_type: str, first_time_eligible: bool) -> float:
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
        add-ons still cost money."""
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
        return _find_window_overlap(new_start, new_end, buffer, existing, window_fn=_captain_booking_window)

    async def _customer_conflict(
        self, customer_id: str, new_start: datetime, new_end: datetime, exclude_booking_id: str | None
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
        existing = await self.repo.find_active_for_customer(customer_id, exclude_booking_id)
        return _find_window_overlap(new_start, new_end, timedelta(0), existing)

    async def _check_geofence(self, booking: dict, latitude: float, longitude: float) -> tuple[bool, float | None]:
        """Flags (never blocks) a photo whose GPS is further than the policy
        radius from the booking's address — the manager sees the flag and
        distance, service addresses aren't pinpoint-accurate so we don't want
        false positives stopping a captain mid-job."""
        address = await self.address_repo.find_by_id(booking["address_id"])
        if not address or address.get("latitude") is None or address.get("longitude") is None:
            return False, None
        policy = await self.policy_service.get_policy()
        distance_km = haversine_km(latitude, longitude, address["latitude"], address["longitude"])
        distance_m = round(distance_km * 1000, 1)
        return distance_m > policy["photo_geofence_radius_m"], distance_m

    async def _notify_location_flag(self, booking: dict, stage: str, distance_m: float | None) -> None:
        center = await self.center_repo.find_by_id(booking["service_center_id"])
        if center and center.get("manager_id"):
            what = (
                "The captain pressed 'reached'"
                if stage == "arrival"
                else f"The {stage}-photo was captured"
            )
            await self.notifications.notify(
                center["manager_id"],
                f"🚨 Location flagged — booking {booking['booking_number']}",
                f"{what} {distance_m}m from the customer's address — worth a quick review.",
                NotificationType.BOOKING,
                str(booking["_id"]),
            )

    async def available_slots(self, service_center_id: str, date_str: str) -> list[dict]:
        """Customer-facing availability for one center/date — deliberately
        never returns the raw total capacity (see the "status"/"remaining"
        shape below), only ever the exact wording the UI needs. remaining
        is populated ONLY when status is "low" (<=5 left) or "full" (0);
        callers must never infer total capacity from any combination of
        these fields."""
        center = await self.center_repo.find_by_id(service_center_id)
        if not center:
            raise NotFoundException("Service center not found")
        policy = await self.policy_service.get_policy()
        duration = center.get("slot_duration_minutes") or policy["slot_duration_minutes"]
        raw_slots = generate_slots(center.get("working_hours_start", "08:00"), center.get("working_hours_end", "20:00"), duration)

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
        results = []
        for slot in raw_slots:
            slot_start, slot_end = _resolve_slot_window(center, scheduled_date, slot["key"], policy)
            cutoff_passed = _slot_cutoff_passed(slot_end, policy)
            doc = day_docs.get(slot["key"])
            capacity = doc["capacity"] if doc else await self._default_slot_capacity(center, date_str, slot["key"])
            booked = doc["booked_count"] if doc else 0
            held = (doc or {}).get("held_count", 0) or 0
            is_closed = bool(doc and doc.get("is_closed"))
            remaining_actual = max(capacity - booked - held, 0)

            if cutoff_passed or is_closed or remaining_actual <= 0:
                status, remaining = "full", 0
            elif remaining_actual <= 5:
                status, remaining = "low", remaining_actual
            else:
                status, remaining = "available", None
            results.append({"key": slot["key"], "start": slot["start"], "end": slot["end"], "status": status, "remaining": remaining})
        return results

    async def admin_slot_capacity(self, service_center_id: str, date_str: str) -> dict:
        """Staff-facing capacity view for one center/date — unlike
        available_slots (customer-facing, never leaks raw numbers), this
        one exists specifically so a manager/admin CAN see capacity,
        booked count, and remaining, per Section 2/22's admin visibility
        requirement. Also creates each slot's capacity doc if it doesn't
        exist yet, so what's shown here is exactly what a subsequent edit
        via set_slot_capacity will act on."""
        center = await self.center_repo.find_by_id(service_center_id)
        if not center:
            raise NotFoundException("Service center not found")
        policy = await self.policy_service.get_policy()
        duration = center.get("slot_duration_minutes") or policy["slot_duration_minutes"]
        raw_slots = generate_slots(center.get("working_hours_start", "08:00"), center.get("working_hours_end", "20:00"), duration)

        results = []
        for slot in raw_slots:
            default_capacity = await self._default_slot_capacity(center, date_str, slot["key"])
            doc = await self.slot_capacity_repo.get_or_init(
                {"service_center_id": service_center_id, "date": date_str, "slot_key": slot["key"]},
                {"capacity": default_capacity, "booked_count": 0, "is_closed": False},
            )
            results.append({
                "key": slot["key"],
                "start": slot["start"],
                "end": slot["end"],
                "capacity": doc["capacity"],
                "booked_count": doc["booked_count"],
                "remaining": max(doc["capacity"] - doc["booked_count"], 0),
                "is_closed": doc.get("is_closed", False),
            })

        max_daily = await self._effective_daily_max(center, date_str)
        daily = None
        if max_daily:
            day_doc = await self.daily_capacity_repo.get_or_init(
                {"service_center_id": service_center_id, "date": date_str}, {"capacity": max_daily, "booked_count": 0}
            )
            daily = {"capacity": day_doc["capacity"], "booked_count": day_doc["booked_count"], "remaining": max(day_doc["capacity"] - day_doc["booked_count"], 0)}
        return {"slots": results, "daily": daily}

    async def set_slot_capacity(self, service_center_id: str, date_str: str, slot_key: str, capacity: int | None, is_closed: bool | None) -> dict:
        """Admin/manager override for one specific (center, date, slot) —
        increase/decrease capacity, or temporarily close/reopen it.
        Immediately affects future reservations (BookingService.
        _reserve_slot_capacity reads this same field live on every
        attempt); never retroactively invalidates bookings already
        holding a spot in this slot."""
        center = await self.center_repo.find_by_id(service_center_id)
        if not center:
            raise NotFoundException("Service center not found")
        filter_ = {"service_center_id": service_center_id, "date": date_str, "slot_key": slot_key}
        default_capacity = await self._default_slot_capacity(center, date_str, slot_key)
        await self.slot_capacity_repo.get_or_init(filter_, {"capacity": default_capacity, "booked_count": 0, "is_closed": False})
        update: dict = {}
        if capacity is not None:
            if capacity < 0:
                raise BadRequestException("Capacity can't be negative")
            update["capacity"] = capacity
            # Marks this exact (date, slot) as an explicit admin override so
            # a later capacity-policy change never silently resyncs it back
            # to the baseline — see CapacityPolicyService._resync_touched_date.
            update["is_override"] = True
        if is_closed is not None:
            update["is_closed"] = is_closed
        if not update:
            raise BadRequestException("Nothing to update")
        updated = await self.slot_capacity_repo.update_by_id(str((await self.slot_capacity_repo.find_one(filter_))["_id"]), update)
        await self._broadcast_slots_changed(service_center_id, date_str)
        return serialize_doc(updated)

    async def _default_slot_capacity(self, service_center: dict, date_str: str, slot_key: str) -> int:
        """What a not-yet-touched (date, slot) should start out with —
        consults the center's effective-dated capacity policy first (see
        CapacityPolicyService), and only falls back to the legacy flat
        default_slot_capacity field (or unrestricted-in-practice, 999) for
        a center that has never had a policy scheduled at all. Zero as the
        ultimate fallback would make every never-configured slot look
        fully booked the moment this feature ships, silently blocking
        bookings a center never intended to block."""
        policy = await self.capacity_policy_service.get_effective_policy(str(service_center["_id"]), date_str)
        if slot_key in policy["slot_distribution"]:
            return policy["slot_distribution"][slot_key]
        return service_center.get("default_slot_capacity") or 999

    async def _effective_daily_max(self, service_center: dict, date_str: str) -> int | None:
        """Same resolution as _default_slot_capacity, for the optional
        center-wide daily cap — the effective-dated policy's
        max_bookings_per_day wins when a policy exists for this date,
        otherwise the center's legacy flat max_bookings_per_day field (or
        None, meaning no daily cap at all, only per-slot capacity)."""
        policy = await self.capacity_policy_service.get_effective_policy(str(service_center["_id"]), date_str)
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

    async def hold_slot(self, holder_id: str, service_center_id: str, date_str: str, slot_key: str) -> dict:
        """Acquires (or renews) a temporary hold on one slot unit."""
        from bson import ObjectId as _OID

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
        _resolve_slot_window(center, parsed_date, slot_key, policy)  # raises on an invented key
        slot_filter = self._hold_filter(service_center_id, date_str, slot_key)
        await self._sweep_holds(slot_filter)
        expires_at = datetime.now(timezone.utc) + timedelta(minutes=self.HOLD_MINUTES)

        # Renewal: the same holder re-picking their held slot just extends it.
        renewed = await self.db.slot_holds.find_one_and_update(
            {**slot_filter, "holder_id": holder_id}, {"$set": {"expires_at": expires_at}}
        )
        if renewed:
            return {"held": True, "renewed": True, "expires_at": expires_at.isoformat(), "hold_seconds": self.HOLD_MINUTES * 60}

        default_capacity = await self._default_slot_capacity(center, date_str, slot_key)
        slot_doc = await self.slot_capacity_repo.get_or_init(
            slot_filter, {"capacity": default_capacity, "booked_count": 0, "held_count": 0, "is_closed": False}
        )
        if slot_doc.get("is_closed"):
            raise BadRequestException("This slot has been closed for booking — please pick another.")
        claimed = await self.slot_capacity_repo.increment_if(
            {**slot_filter, "is_closed": False},
            {"held_count": 1},
            expr_guard=["$lt", self._OCCUPIED_EXPR, "$capacity"],
        )
        if claimed is None:
            raise BadRequestException("This slot just became fully booked — please pick another.")
        try:
            await self.db.slot_holds.insert_one({
                **slot_filter, "holder_id": holder_id,
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

    async def _reserve_slot_capacity(self, session, service_center: dict, date_str: str, slot_key: str, holder_ids: list[str] | None = None) -> None:
        """Atomically reserves one spot in this slot (and, if the center
        has a daily cap configured, one spot in the day too) — single
        find_one_and_update per counter, no retry loop needed since the
        whole success condition ("count < capacity") is expressible
        directly in the filter (see BaseRepository.increment_if). Raises
        BadRequestException if either is already at capacity or closed.
        Call ONLY from inside create_booking's transaction (or
        reschedule's "moving into a new slot" path) — never from
        captain_cancel/reassign_captain, which don't change the slot."""
        center_id = str(service_center["_id"])
        slot_key_filter = {"service_center_id": center_id, "date": date_str, "slot_key": slot_key}
        default_capacity = await self._default_slot_capacity(service_center, date_str, slot_key)
        slot_doc = await self.slot_capacity_repo.get_or_init(
            slot_key_filter, {"capacity": default_capacity, "booked_count": 0, "is_closed": False}, session=session
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

        max_daily = await self._effective_daily_max(service_center, date_str)
        if max_daily:
            day_filter = {"service_center_id": center_id, "date": date_str}
            await self.daily_capacity_repo.get_or_init(day_filter, {"capacity": max_daily, "booked_count": 0}, session=session)
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

    async def _release_slot_capacity(self, service_center_id: str, date_str: str, slot_key: str, session=None) -> None:
        """Reverses _reserve_slot_capacity — called on cancellation, or when
        a reschedule moves a booking OUT of a slot it was holding. Guarded
        at 0 (never goes negative); is_closed is irrelevant here since
        closing only blocks NEW reservations, never invalidates a release
        of a booking that already existed."""
        slot_key_filter = {"service_center_id": service_center_id, "date": date_str, "slot_key": slot_key}
        await self.slot_capacity_repo.increment_if(slot_key_filter, {"booked_count": -1}, expr_guard=["$gt", "$booked_count", 0], session=session)
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
            return centers[0], 0.0

        raise BadRequestException("Doorstep service is not yet available in your area")

    async def get_booking(self, booking_id: str) -> dict:
        booking = await self.repo.find_by_id(booking_id)
        if not booking:
            raise NotFoundException("Booking not found")
        enriched = await self._enrich_bookings([booking])
        return enriched[0]

    async def get_booking_with_history(self, booking_id: str, actor_id: str, actor_role: str) -> dict:
        booking = await self.get_booking(booking_id)
        if actor_role not in {"admin", "manager"} and actor_id not in {booking.get("customer_id"), booking.get("captain_id")}:
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

    async def list_for_captain(self, captain_id: str, status: str | None, page: int, page_size: int):
        items, total = await self.repo.list_for_captain(captain_id, status, page, page_size)
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
        vehicle_ids = {b.get("vehicle_id") for b in bookings}
        address_ids = {b.get("address_id") for b in bookings}
        captain_ids = {b.get("captain_id") for b in bookings if b.get("captain_id")}
        service_ids: set[str] = set()
        combo_ids: set[str] = set()
        for b in bookings:
            if b.get("combo_id"):
                combo_ids.add(b["combo_id"])
            else:
                service_ids.update(b.get("service_ids") or [])

        customers = {str(u["_id"]): u for u in await self.user_repo.find_by_ids(list(customer_ids))}
        captains = {str(u["_id"]): u for u in await self.user_repo.find_by_ids(list(captain_ids))} if captain_ids else {}
        vehicles = {str(v["_id"]): v for v in await self.vehicle_repo.find_by_ids(list(vehicle_ids))}
        addresses = {str(a["_id"]): a for a in await self.address_repo.find_by_ids(list(address_ids))}
        services = {str(s["_id"]): s for s in await self.service_repo.find_by_ids(list(service_ids))} if service_ids else {}
        combos = {str(c["_id"]): c for c in await self.combo_repo.find_by_ids(list(combo_ids))} if combo_ids else {}

        results = []
        for booking in bookings:
            doc = serialize_doc(booking)
            customer = customers.get(booking.get("customer_id"))
            vehicle = vehicles.get(booking.get("vehicle_id"))
            address = addresses.get(booking.get("address_id"))

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
            doc["vehicle_snapshot"] = (
                {
                    "vehicle_type": vehicle.get("vehicle_type"),
                    "brand": vehicle.get("brand"),
                    "model": vehicle.get("model"),
                    "registration_number": vehicle.get("registration_number"),
                }
                if vehicle
                else None
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
            results.append(doc)
        return results

    async def list_for_center(
        self, service_center_id: str, status: str | None, page: int, page_size: int, actor_role: str, actor_center_id: str | None
    ):
        ensure_own_center(actor_role, actor_center_id, service_center_id)
        items, total = await self.repo.list_for_center(service_center_id, status, page, page_size)
        enriched = await self._enrich_bookings(items)
        return enriched, total

    async def list_subscribers_for_center(self, service_center_id: str, actor_role: str, actor_center_id: str | None) -> list[dict]:
        """One row per customer who has used a subscription at this center —
        answers 'who purchased which plan and got served from my store'."""
        ensure_own_center(actor_role, actor_center_id, service_center_id)
        from app.repositories.subscription_repository import SubscriptionPlanRepository, UserSubscriptionRepository

        bookings = await self.repo.list_subscription_bookings_for_center(service_center_id)
        sub_repo = UserSubscriptionRepository(self.repo.db)
        plan_repo = SubscriptionPlanRepository(self.repo.db)

        rows: dict[str, dict] = {}
        for booking in bookings:
            customer_id = booking["customer_id"]
            if customer_id in rows:
                rows[customer_id]["visits"] += 1
                continue
            customer = await self.user_repo.find_by_id(customer_id)
            subscription = await sub_repo.find_by_id(booking["subscription_id"]) if booking.get("subscription_id") else None
            plan = await plan_repo.find_by_id(subscription["plan_id"]) if subscription else None
            rows[customer_id] = {
                "customer_id": customer_id,
                "customer_name": customer.get("full_name") if customer else "Unknown",
                "customer_phone": customer.get("phone") if customer else None,
                "plan_name": plan.get("name") if plan else "Unknown plan",
                "subscription_status": subscription.get("status") if subscription else None,
                "remaining_service_count": subscription.get("remaining_service_count") if subscription else None,
                "last_visit": booking["created_at"],
                "visits": 1,
            }
        return sorted(rows.values(), key=lambda r: r["last_visit"], reverse=True)

    async def list_all(self, filters: dict, page: int, page_size: int):
        items, total = await self.repo.list_all(filters, page, page_size)
        return serialize_list(items), total

    async def assign_captain(
        self, booking_id: str, payload: BookingAssignCaptainRequest, assigned_by: str, actor_role: str, actor_center_id: str | None
    ) -> dict:
        booking = await self.repo.find_by_id(booking_id)
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
            fresh = await self.repo.find_by_id(booking_id, session=session)
            if not fresh or fresh["status"] not in {BookingStatus.PENDING.value, BookingStatus.RESCHEDULED.value}:
                raise BadRequestException("This booking is no longer available to assign — someone else may have just assigned it.")
            estimated_start_at = _resolve_estimated_start(fresh, payload.estimated_start_at)
            duration = fresh.get("duration_minutes", 60)
            conflict = await self._captain_conflict(
                payload.captain_id, estimated_start_at, estimated_start_at + timedelta(minutes=duration), None, policy, session=session
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
        await self.notifications.notify(payload.captain_id, "New job assigned", f"You have a new booking {booking['booking_number']}.", NotificationType.BOOKING, booking_id)
        wa_name, wa_services = await self._wa_ctx(booking)
        await self.notifications.notify(
            booking["customer_id"], "Captain assigned", "A captain has been assigned to your booking.",
            NotificationType.BOOKING, booking_id,
            wa_event="captain_assigned",
            wa_params=[wa_name, captain.get("full_name", "Your captain"), wa_services, booking.get("scheduled_slot", "")],
        )
        await self._broadcast_booking_changed(updated)
        return serialize_doc(updated)

    async def reassign_captain(
        self, booking_id: str, payload: ReassignCaptainRequest, actor_id: str, actor_role: str, actor_center_id: str | None
    ) -> dict:
        booking = await self.repo.find_by_id(booking_id)
        if not booking:
            raise NotFoundException("Booking not found")
        ensure_own_center(actor_role, actor_center_id, booking["service_center_id"])
        if booking["status"] not in {BookingStatus.PENDING.value, BookingStatus.ASSIGNED.value}:
            raise BadRequestException("This booking cannot be reassigned in its current state")
        _ensure_transition_allowed(booking["status"], BookingStatus.ASSIGNED.value)

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
                },
                session=session,
            )

        async with await self.db.client.start_session() as session:
            await session.with_transaction(_do_reassign)

        updated = await self.repo.find_by_id(booking_id)
        await self._record_history(booking_id, BookingStatus.ASSIGNED, actor_id, f"Reassigned to captain {captain['full_name']}")
        await self.notifications.notify(payload.captain_id, "New job assigned", f"You have a new booking {booking['booking_number']}.", NotificationType.BOOKING, booking_id)
        if outgoing_captain_id:
            await self.notifications.notify(
                outgoing_captain_id,
                "Booking reassigned",
                f"Booking {booking['booking_number']} has been reassigned to another captain.",
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

    async def captain_cancel(self, booking_id: str, payload: CaptainCancelRequest, captain_id: str) -> dict:
        booking = await self.repo.find_by_id(booking_id)
        if not booking:
            raise NotFoundException("Booking not found")
        if booking.get("captain_id") != captain_id:
            raise ForbiddenException("You are not assigned to this booking")
        if booking["status"] not in {BookingStatus.ASSIGNED.value, BookingStatus.CAPTAIN_ON_THE_WAY.value}:
            raise BadRequestException("This booking can no longer be released")
        _ensure_transition_allowed(booking["status"], BookingStatus.PENDING.value)

        previous = list(booking.get("previous_captain_ids", []))
        if captain_id not in previous:
            previous.append(captain_id)

        updated = await self.repo.update_by_id(
            booking_id,
            {"captain_id": None, "status": BookingStatus.PENDING.value, "previous_captain_ids": previous, "heading_at": None, "heading_location": None},
        )
        await self._record_history(booking_id, BookingStatus.PENDING, captain_id, f"Captain released the booking: {payload.reason}")

        center = await self.center_repo.find_by_id(booking["service_center_id"])
        if center and center.get("manager_id"):
            await self.notifications.notify(
                center["manager_id"],
                "Captain unavailable — reassignment needed",
                f"Booking {booking['booking_number']} needs a new captain: {payload.reason}",
                NotificationType.BOOKING,
                booking_id,
            )
        # The customer had already been told a captain was assigned/on the
        # way — leaving them with no signal that changed until (or unless) a
        # new captain gets assigned is a silent, confusing gap.
        await self.notifications.notify(
            booking["customer_id"],
            "Finding you a new captain",
            f"Your captain for booking {booking['booking_number']} is no longer available — we're assigning a replacement.",
            NotificationType.BOOKING,
            booking_id,
        )
        await self._broadcast_booking_changed(updated)
        await ws_manager.broadcast(f"user:{captain_id}", {"type": "changed", "channel": f"user:{captain_id}", "booking_id": booking_id})
        return serialize_doc(updated)

    async def start_heading(self, booking_id: str, payload: HeadingRequest, captain_id: str) -> dict:
        booking = await self.repo.find_by_id(booking_id)
        if not booking:
            raise NotFoundException("Booking not found")
        if booking.get("captain_id") != captain_id:
            raise ForbiddenException("You are not assigned to this booking")
        # "captain_not_started" exists specifically to prompt exactly this
        # action — it must not block the captain from taking it.
        _ensure_no_open_issue(booking, exempt_flags=frozenset({"captain_not_started"}))
        if booking["status"] != BookingStatus.ASSIGNED.value:
            raise BadRequestException("This booking is not ready to start heading out")
        _ensure_transition_allowed(booking["status"], BookingStatus.CAPTAIN_ON_THE_WAY.value)

        policy = await self.policy_service.get_policy()
        duration = booking.get("duration_minutes", 60)
        # Anchored at THIS booking's own estimated_start_at (set at
        # assignment — see _resolve_estimated_start), not the coarse
        # customer-facing admin slot (e.g. 09:00-12:00) — several bookings
        # can share one slot for the same captain, each with its own
        # estimated_start_at, so lateness has to be measured per-job, not
        # against the whole shared window. Falls back to the legacy
        # exact-time derivation for a booking assigned before this field
        # existed.
        raw_start = from_stored(booking["estimated_start_at"]) if booking.get("estimated_start_at") else _slot_start_datetime(booking["scheduled_date"], booking["scheduled_slot"])
        # Last-minute assignment: lateness (stages, penalties, lockout) is
        # measured from _effective_start_anchor — a captain assigned at
        # 6:35 into a 4-7 slot has 15 clean minutes to head out; only past
        # that does the late/penalty machinery engage. The "too early"
        # check below deliberately keeps the RAW anchor: heading out ahead
        # of the real slot is unaffected by when assignment happened.
        slot_start = _effective_start_anchor(booking, raw_start, policy)
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
            # two concurrent deductions drive stock negative.
            await self.inventory_repo.collection.update_one(
                {"_id": ObjectId(item.inventory_item_id), "quantity_available": {"$gte": item.quantity}},
                {"$inc": {"quantity_available": -item.quantity}},
            )
        await self.update_captain_location(captain_id, payload.latitude, payload.longitude, source="heading", booking_id=booking_id)
        await self._record_history(
            booking_id,
            BookingStatus.CAPTAIN_ON_THE_WAY,
            captain_id,
            f"Captain is heading to the customer ({stage.replace('_', ' ')})",
        )
        wa_name, _ = await self._wa_ctx(booking)
        await self.notifications.notify(
            booking["customer_id"], "Captain on the way", "Your captain has left for your location.",
            NotificationType.BOOKING, booking_id, wa_event="captain_on_the_way", wa_params=[wa_name],
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

    async def verify_vehicle(self, booking_id: str, payload: VerifyVehicleRequest, captain_id: str) -> dict:
        """Captain types the plate they see on arrival — compared against the
        registration number snapshotted on the booking. This is a deliberate
        typed check, not a yes/no toggle, so a captain can't rubber-stamp past
        the wrong car. Required before the before-photo can be captured."""
        booking = await self.repo.find_by_id(booking_id)
        if not booking:
            raise NotFoundException("Booking not found")
        if booking.get("captain_id") != captain_id:
            raise ForbiddenException("You are not assigned to this booking")
        _ensure_no_open_issue(booking, exempt_flags=ARRIVAL_STAGE_EXEMPT_FLAGS)
        if booking["status"] != BookingStatus.CAPTAIN_ON_THE_WAY.value:
            raise BadRequestException("Vehicle verification happens after you've started heading to the customer")

        entered = normalize_plate(payload.registration_number)
        expected = booking.get("vehicle_registration_number") or ""
        if entered != expected:
            raise BadRequestException(
                "That registration number doesn't match this booking. Double-check the plate before proceeding — "
                "if this really is the wrong vehicle, release the job instead of continuing."
            )

        now = now_ist()
        update_data: dict = {"vehicle_verified": True, "vehicle_verified_at": now}
        flagged = False
        distance_m: float | None = None
        # This is also the "I've reached" press — the GPS it carries gets the
        # same geofence treatment as the photos: flag + tell the manager,
        # never block (service addresses aren't pinpoint-accurate).
        if payload.latitude is not None and payload.longitude is not None:
            flagged, distance_m = await self._check_geofence(booking, payload.latitude, payload.longitude)
            update_data.update({
                "arrival_location": {"latitude": payload.latitude, "longitude": payload.longitude},
                "arrival_flagged": flagged,
                "arrival_distance_m": distance_m,
            })
        updated = await self.repo.update_by_id(booking_id, update_data)
        if payload.latitude is not None and payload.longitude is not None:
            await self.update_captain_location(captain_id, payload.latitude, payload.longitude, source="arrival", booking_id=booking_id)
        await self._record_history(booking_id, BookingStatus.CAPTAIN_ON_THE_WAY, captain_id, "Vehicle registration verified on arrival")
        if flagged:
            await self._notify_location_flag(booking, "arrival", distance_m)
        return serialize_doc(updated)

    async def capture_before_photo(self, booking_id: str, payload: PhotoCaptureRequest, captain_id: str) -> dict:
        booking = await self.repo.find_by_id(booking_id)
        if not booking:
            raise NotFoundException("Booking not found")
        if booking.get("captain_id") != captain_id:
            raise ForbiddenException("You are not assigned to this booking")
        _ensure_no_open_issue(booking, exempt_flags=ARRIVAL_STAGE_EXEMPT_FLAGS)
        if booking["status"] != BookingStatus.CAPTAIN_ON_THE_WAY.value:
            raise BadRequestException("Reach the customer's location before starting the service")
        if not booking.get("vehicle_verified"):
            raise BadRequestException("Verify the vehicle's registration number before starting the service")
        _ensure_transition_allowed(booking["status"], BookingStatus.SERVICE_STARTED.value)

        now = now_ist()
        flagged, distance_m = await self._check_geofence(booking, payload.latitude, payload.longitude)
        updated = await self.repo.update_by_id(
            booking_id,
            {
                "status": BookingStatus.SERVICE_STARTED.value,
                "before_photo": {"image_url": payload.image_url, "latitude": payload.latitude, "longitude": payload.longitude, "captured_at": now},
                "before_photo_flagged": flagged,
                "before_photo_distance_m": distance_m,
                "service_started_at": now,
            },
        )
        await self.update_captain_location(captain_id, payload.latitude, payload.longitude, source="before_photo", booking_id=booking_id)
        await self._record_history(booking_id, BookingStatus.SERVICE_STARTED, captain_id, "Service started (before photo captured)")
        if flagged:
            await self._notify_location_flag(booking, "before", distance_m)
        await self.notifications.notify(booking["customer_id"], "Service started", "Your captain has started the service.", NotificationType.BOOKING, booking_id)
        await self._broadcast_booking_changed(updated)
        return serialize_doc(updated)

    async def capture_after_photo_and_complete(self, booking_id: str, payload: PhotoCaptureRequest, captain_id: str) -> dict:
        booking = await self.repo.find_by_id(booking_id)
        if not booking:
            raise NotFoundException("Booking not found")
        if booking.get("captain_id") != captain_id:
            raise ForbiddenException("You are not assigned to this booking")
        _ensure_no_open_issue(booking, exempt_flags=COMPLETION_EXEMPT_FLAGS)
        if booking["status"] != BookingStatus.SERVICE_STARTED.value:
            raise BadRequestException("The service must be in progress before it can be completed")
        _ensure_transition_allowed(booking["status"], BookingStatus.COMPLETED.value)

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
        if booking.get("service_started_at"):
            started_ist = from_stored(booking["service_started_at"])
            actual_duration = max(0, round((now - started_ist).total_seconds() / 60))
            update_data["actual_duration_minutes"] = actual_duration
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

        if not booking.get("wallet_settled") and booking.get("captain_earning") is not None:
            # Atomically claim the settlement slot BEFORE moving any money —
            # a duplicate/retried after-photo submission (realistic on a
            # captain's spotty field connection) could otherwise have both
            # requests read wallet_settled=False before either commits, and
            # both credit or debit the wallet, double-paying (or
            # double-charging) the captain. Only the request that wins this
            # atomic claim proceeds to actually touch the wallet; a losing
            # request just continues on to the rest of the completion below
            # (status/photo), which is safe to write redundantly.
            claimed = await self.repo.update_if(booking_id, {"wallet_settled": {"$ne": True}}, {"wallet_settled": True})
            if claimed:
                if booking["payment_method"] == PaymentMethod.CASH.value:
                    await self.wallet_service.debit(
                        captain_id,
                        booking["platform_earning"],
                        booking_id,
                        f"Platform share for cash booking {booking['booking_number']}",
                        allow_negative=True,
                    )
                else:
                    await self.wallet_service.credit(
                        captain_id,
                        booking["captain_earning"],
                        booking_id,
                        f"Payout for booking {booking['booking_number']}",
                    )
                update_data["payment_status"] = PaymentStatus.PAID.value

        updated = await self.repo.update_by_id(booking_id, update_data)
        await self.update_captain_location(captain_id, payload.latitude, payload.longitude, source="after_photo", booking_id=booking_id)
        await self._record_history(booking_id, BookingStatus.COMPLETED, captain_id, "Service completed (after photo captured)")
        if flagged:
            await self._notify_location_flag(booking, "after", distance_m)
        wa_name, _ = await self._wa_ctx(booking)
        await self.notifications.notify(
            booking["customer_id"],
            "Service completed",
            f"Booking {booking['booking_number']} is complete. Please rate your captain!",
            NotificationType.BOOKING,
            booking_id,
            wa_event="service_completed",
            wa_params=[wa_name],
        )
        await self._broadcast_booking_changed(updated)
        return serialize_doc(updated)

    async def cancel_booking(
        self, booking_id: str, payload: BookingCancelRequest, actor_id: str, actor_role: str, actor_center_id: str | None = None
    ) -> dict:
        booking = await self.repo.find_by_id(booking_id)
        if not booking:
            raise NotFoundException("Booking not found")
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
        if actor_role == "customer":
            # CANCELLATION POLICY phase 1 (see CUSTOMER_CANCEL_LOCK_HOURS):
            # self-cancel only while unassigned (pending/rescheduled) AND
            # >4h before the slot. Staff cancelling on the customer's
            # behalf is unaffected.
            if booking["status"] not in {BookingStatus.PENDING.value, BookingStatus.RESCHEDULED.value}:
                raise BadRequestException("A captain is already on this booking — it can no longer be cancelled online.")
            slot_start, _slot_end = _booking_window(booking)
            if now_ist() > slot_start - timedelta(hours=CUSTOMER_CANCEL_LOCK_HOURS):
                raise BadRequestException(
                    f"Cancellations close {CUSTOMER_CANCEL_LOCK_HOURS} hours before your slot. "
                    "Please message us on WhatsApp and our team will help you with this booking."
                )
        _ensure_transition_allowed(booking["status"], BookingStatus.CANCELLED.value)

        cancel_data: dict = {
            "status": BookingStatus.CANCELLED.value,
            "cancellation_reason": payload.reason,
            "cancelled_by_role": actor_role,
            "closed_at": now_ist(),
        }
        # A flagged issue is only meant to prompt manager action WHILE the
        # booking is still live — once it's cancelled there's nothing left to
        # act on. Auto-resolve rather than leave it looking like an open
        # problem forever; issue_flag itself is kept as the historical record
        # of what happened.
        if booking.get("issue_flag") and not booking.get("issue_resolved"):
            cancel_data["issue_resolved"] = True
        # Guarded on the exact status we validated: a concurrent duplicate
        # cancel (double-click, retry) loses this write and stops HERE —
        # before it can release the slot's capacity a second time (which
        # silently overbooked the slot) or double-restore coupon/plan value.
        updated = await self.repo.update_if(booking_id, {"status": booking["status"]}, cancel_data)
        if updated is None:
            raise BadRequestException("This booking just changed state — refresh and try again.")

        # This slot is no longer held — free it up for someone else, using
        # the booking's own snapshotted date/slot (not "now"), whether or
        # not it ever had slot_start/slot_end set (a pre-migration booking
        # simply has no matching capacity doc to decrement, which
        # increment_if's guard handles as a harmless no-op).
        date_str = to_ist(booking["scheduled_date"]).strftime("%Y-%m-%d")
        await self._release_slot_capacity(booking["service_center_id"], date_str, booking["scheduled_slot"])

        # A cancelled booking never actually happened — whatever it charged
        # against a coupon's usage cap or a subscription's remaining quota
        # must come back, or the customer (and the coupon's other
        # would-be users) permanently lose value for a service that was
        # never performed.
        if booking.get("coupon_code"):
            await self.coupon_service.reverse_usage(booking["coupon_code"], booking["customer_id"], booking_id)
        if booking.get("subscription_id") and booking.get("subscription_consumption"):
            await self.subscription_service.restore_consumption(booking["subscription_id"], booking["subscription_consumption"])

        await self._record_history(booking_id, BookingStatus.CANCELLED, actor_id, payload.reason)
        await self.notifications.notify(booking["customer_id"], "Booking cancelled", f"Booking {booking['booking_number']} has been cancelled.", NotificationType.BOOKING, booking_id)
        if booking.get("captain_id"):
            await self.notifications.notify(booking["captain_id"], "Booking cancelled", f"Booking {booking['booking_number']} was cancelled.", NotificationType.BOOKING, booking_id)
        await self._broadcast_booking_changed(updated)
        await self._broadcast_slots_changed(booking["service_center_id"], date_str)
        return serialize_doc(updated)

    async def reschedule_booking(
        self,
        booking_id: str,
        payload: BookingRescheduleRequest,
        actor_id: str,
        actor_role: str = "customer",
        actor_center_id: str | None = None,
    ) -> dict:
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
        # A captain who's already on the way or mid-service has real,
        # unfinished work invested in this booking — pulling it out from
        # under them here would orphan that work with no notice. Release
        # the captain (captain_cancel) or cancel the booking outright
        # instead; reschedule is only for a booking that hasn't reached
        # that point yet.
        if booking["status"] in {BookingStatus.CAPTAIN_ON_THE_WAY.value, BookingStatus.SERVICE_STARTED.value}:
            raise BadRequestException(
                "This booking's captain is already on the way or mid-service — cancel the booking or have the "
                "captain release it before rescheduling."
            )
        _ensure_transition_allowed(booking["status"], BookingStatus.RESCHEDULED.value)

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
        new_slot_start, new_slot_end = _resolve_slot_window(service_center, payload.scheduled_date, payload.scheduled_slot, policy)
        if _slot_cutoff_passed(new_slot_end, policy):
            raise BadRequestException("This slot is no longer available to book — please pick another slot.")
        self_conflict = await self._customer_conflict(booking["customer_id"], new_slot_start, new_slot_end, booking_id)
        if self_conflict:
            raise BadRequestException(
                f"This customer already has booking {self_conflict['booking_number']} scheduled around this new time — "
                "pick a different time."
            )

        center_id = booking["service_center_id"]
        old_date_str = to_ist(booking["scheduled_date"]).strftime("%Y-%m-%d")
        new_date_str = to_ist(payload.scheduled_date).strftime("%Y-%m-%d")
        same_slot = old_date_str == new_date_str and booking["scheduled_slot"] == payload.scheduled_slot

        had_captain = bool(booking.get("captain_id"))
        update_data: dict = {
            "scheduled_date": payload.scheduled_date,
            "scheduled_slot": payload.scheduled_slot,
            "slot_start": new_slot_start,
            "slot_end": new_slot_end,
            "estimated_start_at": None,
            "status": BookingStatus.RESCHEDULED.value,
            "captain_id": None,
            "reminder_sent": False,
            "issue_flag": None,
            "issue_resolved": True,
            "captain_start_stage": None,
            "late_penalty_pct": 0,
            # Just re-entered "needs a captain" — restart the clock on
            # the repeating unassigned-reminder sweep rather than having
            # it fire again immediately off the original (now stale)
            # wait time.
            "awaiting_assignment_since": now_ist(),
            "unassigned_reminder_sent_at": None,
        }
        if had_captain:
            # Whoever gets assigned next must re-verify the vehicle and
            # re-capture their own before-photo — carrying over the
            # previous captain's "already verified"/photo state would let
            # a new captain silently skip the on-arrival plate check.
            update_data.update(
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

        # A reschedule that keeps the exact same center/date/slot never
        # touches capacity at all (it already holds that reservation) — only
        # an actual move needs to release the old slot and reserve the new
        # one, in that order (reserve first, so a full new slot aborts the
        # whole thing with the old reservation still intact, nothing
        # changed) atomically with the booking update itself.
        async def _do_reschedule(session):
            if not same_slot:
                await self._reserve_slot_capacity(session, service_center, new_date_str, payload.scheduled_slot)
                await self._release_slot_capacity(center_id, old_date_str, booking["scheduled_slot"], session=session)
            # Guarded on what we validated OUTSIDE the transaction — two
            # concurrent reschedules of one booking used to both reserve a
            # new slot and both release the same old one. The loser now
            # aborts here and the transaction rolls its capacity ops back.
            result = await self.repo.update_if(
                booking_id,
                {"status": booking["status"], "scheduled_slot": booking["scheduled_slot"]},
                update_data,
                session=session,
            )
            if result is None:
                raise BadRequestException("This booking just changed — refresh and try rescheduling again.")
            return result

        try:
            async with await self.db.client.start_session() as session:
                updated = await session.with_transaction(_do_reschedule)
        except DuplicateKeyError:
            raise BadRequestException("This customer already has a booking for this vehicle in this slot.")

        await self._record_history(
            booking_id,
            BookingStatus.RESCHEDULED,
            actor_id,
            "Rescheduled by customer" if actor_role == "customer" else f"Rescheduled by {actor_role}",
        )
        if actor_role != "customer":
            wa_name, _ = await self._wa_ctx(booking)
            await self.notifications.notify(
                booking["customer_id"],
                "Booking rescheduled",
                f"Booking {booking['booking_number']} has been moved to a new time — we'll confirm your captain shortly.",
                NotificationType.BOOKING,
                booking_id,
                wa_event="reschedule_confirmation",
                wa_params=[wa_name, new_date_str, payload.scheduled_slot, booking["booking_number"]],
            )
        if had_captain and booking.get("captain_id"):
            await self.notifications.notify(
                booking["captain_id"],
                "Booking rescheduled — no longer yours",
                f"Booking {booking['booking_number']} was rescheduled to a new time and needs a new captain assignment.",
                NotificationType.BOOKING,
                booking_id,
            )
        await self._broadcast_booking_changed(updated)
        if not same_slot:
            await self._broadcast_slots_changed(center_id, old_date_str)
            await self._broadcast_slots_changed(center_id, new_date_str)
        return serialize_doc(updated)

    async def find_bookings_needing_reminder(self) -> list[dict]:
        candidates = await self.repo.find_all_no_paginate({"status": BookingStatus.ASSIGNED.value, "reminder_sent": {"$ne": True}})
        now = now_ist()
        due = []
        for booking in candidates:
            slot_start = _slot_start_datetime(booking["scheduled_date"], booking["scheduled_slot"])
            remind_at = slot_start - timedelta(minutes=START_WINDOW_MINUTES)
            if now >= remind_at:
                due.append(booking)
        return due

    async def mark_reminder_sent(self, booking_id: str) -> None:
        await self.repo.update_by_id(booking_id, {"reminder_sent": True})

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
        candidates = await self.repo.find_all_no_paginate(
            {
                "status": {"$in": [BookingStatus.PENDING.value, BookingStatus.RESCHEDULED.value]},
                "issue_flag": None,
                "scheduled_date": {"$gte": now_naive - timedelta(days=7), "$lte": now_naive + timedelta(days=1)},
            }
        )
        now = now_ist()
        overdue = []
        for booking in candidates:
            # No captain (and so no estimated_start_at) exists for these yet —
            # the relevant "has this booking's whole window expired" boundary
            # is the admin slot's own end (_booking_window), not a per-captain
            # anchor that hasn't been set.
            _, slot_end = _booking_window(booking)
            window_end = slot_end + timedelta(minutes=policy.get("late_start_grace_minutes", 30))
            if now > window_end:
                overdue.append(booking)
        return overdue

    async def find_bookings_late_to_start(self) -> list[dict]:
        """An ASSIGNED booking whose scheduled start time has already passed
        and the captain still hasn't even started heading out — flagged and
        notified immediately (not the much longer slot_end + grace wait
        find_bookings_captain_not_reached uses for a booking that never got
        a captain at all), then re-nudged every LATE_START_NUDGE_MINUTES for
        as long as it stays unstarted. Deliberately does NOT filter on
        issue_flag being unset — this needs to keep firing on its own
        throttle even while flagged, which find_bookings_captain_not_reached
        doesn't need since it only ever fires once."""
        candidates = await self.repo.find_all_no_paginate({"status": BookingStatus.ASSIGNED.value})
        now = now_ist()
        policy = await self.policy_service.get_policy()
        threshold = timedelta(minutes=LATE_START_NUDGE_MINUTES)
        due = []
        for booking in candidates:
            # ASSIGNED means a captain (and estimated_start_at) exists —
            # anchor to that, not the coarse shared slot, same reasoning as
            # start_heading. Shifted forward for last-minute assignments
            # (_effective_start_anchor): a captain handed the job at 6:35
            # for a slot that began at 4 is not "late" at 6:36.
            slot_start = from_stored(booking["estimated_start_at"]) if booking.get("estimated_start_at") else _slot_start_datetime(booking["scheduled_date"], booking["scheduled_slot"])
            if now <= _effective_start_anchor(booking, slot_start, policy):
                continue
            last_reminder = booking.get("late_start_reminder_sent_at")
            if last_reminder and now - from_stored(last_reminder) < threshold:
                continue
            due.append(booking)
        return due

    async def flag_late_to_start(self, booking: dict) -> None:
        scheduled_time = format_slot_start_12h(booking["scheduled_slot"])
        existing_flag = booking.get("issue_flag")

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
            note = (
                f"Booking {booking['booking_number']} was due to start at {scheduled_time} and the captain never headed "
                f"out — the window expired too long ago for them to start it now. Reschedule it to a new time, then "
                f"assign a captain, or cancel it."
            )
            # Always (re)set here, even over an existing captain_not_started —
            # this IS the more specific issue once locked out, superseding
            # the earlier nudge rather than being blocked by it.
            await self.flag_issue(str(booking["_id"]), "captain_missed_window", note)
            return

        note = f"Booking {booking['booking_number']} was due to start at {scheduled_time} — the captain hasn't headed out yet."
        # Don't clobber a different, still-open flag (e.g. the captain's own
        # report_risk) — only set/refresh captain_not_started when there
        # isn't a more specific issue already active.
        is_different_open_flag = bool(existing_flag) and existing_flag != "captain_not_started" and not booking.get("issue_resolved")
        if not is_different_open_flag:
            await self.flag_issue(str(booking["_id"]), "captain_not_started", note)
        if booking.get("captain_id"):
            await self.notifications.notify(
                booking["captain_id"],
                "You haven't started this booking yet",
                f"Booking {booking['booking_number']} was due to start at {scheduled_time}. Please start heading out now.",
                NotificationType.BOOKING,
                str(booking["_id"]),
            )

    async def mark_late_start_reminder_sent(self, booking_id: str) -> None:
        await self.repo.update_by_id(booking_id, {"late_start_reminder_sent_at": now_ist()})

    async def find_bookings_unassigned_too_long(self) -> list[dict]:
        """A booking that's simply been sitting without a captain for too
        long — independent of how far off its scheduled slot still is (that
        proximity-based case is find_bookings_captain_not_reached above,
        which only fires once the whole scheduled window has expired). This
        one is a repeating nudge: as long as a booking stays unassigned, the
        manager gets reminded every UNASSIGNED_REMINDER_MINUTES, not just
        once — a booking scheduled for tomorrow evening still shouldn't sit
        untouched in the queue all day."""
        # Time-bounded IN THE QUERY: an abandoned booking whose slot is days
        # in the past is a zombie, not a nudge target — without this bound
        # every such booking was re-fetched and re-parsed every 60s forever.
        # Flagged bookings are excluded too (the manager already has a
        # louder signal for those). scheduled_date is naive IST wall-clock.
        floor = now_ist().replace(tzinfo=None) - timedelta(days=2)
        candidates = await self.repo.find_all_no_paginate(
            {
                "status": {"$in": [BookingStatus.PENDING.value, BookingStatus.RESCHEDULED.value]},
                "awaiting_assignment_since": {"$ne": None},
                "issue_flag": None,
                "scheduled_date": {"$gte": floor},
            }
        )
        now = now_ist()
        threshold = timedelta(minutes=UNASSIGNED_REMINDER_MINUTES)
        due = []
        for booking in candidates:
            since = booking.get("awaiting_assignment_since")
            if not since or now - from_stored(since) < threshold:
                continue
            last_reminder = booking.get("unassigned_reminder_sent_at")
            if last_reminder and now - from_stored(last_reminder) < threshold:
                continue
            due.append(booking)
        return due

    async def mark_unassigned_reminder_sent(self, booking_id: str) -> None:
        await self.repo.update_by_id(booking_id, {"unassigned_reminder_sent_at": now_ist()})

    async def find_bookings_stuck_on_the_way(self) -> list[dict]:
        """Captain started heading out but hasn't reached/started the service in
        a long time — worth nudging the manager even though nothing's broken
        yet ('notify him if he takes unnecessary time')."""
        candidates = await self.repo.find_all_no_paginate({"status": BookingStatus.CAPTAIN_ON_THE_WAY.value, "issue_flag": None})
        now = now_ist()
        stuck = []
        for booking in candidates:
            heading_at = booking.get("heading_at")
            if not heading_at:
                continue
            if now - from_stored(heading_at) > timedelta(minutes=STUCK_ON_THE_WAY_MINUTES):
                stuck.append(booking)
        return stuck

    async def find_bookings_service_overrunning(self) -> list[dict]:
        """A wash that's been running noticeably longer than its planned
        duration (before-photo captured, no after-photo yet) — the same
        'nudge the manager if it's taking unusual time' idea as the
        stuck-on-the-way sweep above, just for the actual service instead of
        the drive over."""
        policy = await self.policy_service.get_policy()
        tolerance = policy.get("delay_tolerance_minutes", SERVICE_OVERRUN_MINUTES)
        candidates = await self.repo.find_all_no_paginate({"status": BookingStatus.SERVICE_STARTED.value, "issue_flag": None})
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
            {"status": BookingStatus.CAPTAIN_ON_THE_WAY.value, "vehicle_verified": True, "issue_flag": None}
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
        candidates = await self.repo.find_all_no_paginate({"status": BookingStatus.SERVICE_STARTED.value, "issue_flag": None})
        if not candidates:
            return []
        # Batched lookups — this sweep runs every 60s; two find_by_id calls
        # per in-progress booking was the loop's biggest query multiplier.
        addresses = {
            str(a["_id"]): a
            for a in await self.address_repo.find_by_ids([b["address_id"] for b in candidates if b.get("address_id")])
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
            address = addresses.get(booking.get("address_id") or "")
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

    async def flag_issue(self, booking_id: str, issue_flag: str, note: str) -> None:
        await self.repo.update_by_id(
            booking_id,
            {"issue_flag": issue_flag, "issue_notes": note, "issue_flagged_at": now_ist(), "issue_resolved": False},
        )
        booking = await self.repo.find_by_id(booking_id)
        if booking:
            center = await self.center_repo.find_by_id(booking["service_center_id"])
            if center and center.get("manager_id"):
                await self.notifications.notify(
                    center["manager_id"],
                    f"🚨 Urgent — booking {booking['booking_number']}",
                    note,
                    NotificationType.BOOKING,
                    booking_id,
                )
            # The single hook every automated sweep in main.py's
            # _reminder_loop goes through (captain_not_started,
            # captain_not_reached, captain_delay, service_overrun) as well
            # as the manual report_risk path — covers the manager queue's
            # "Flagged issues" section with a live push for all of them at
            # once, not just the manager-initiated actions above.
            await self._broadcast_booking_changed(booking)

    async def notify_center_manager_for_booking(self, booking: dict, title: str, message: str) -> None:
        """Notifies the booking's service center manager without touching
        issue_flag — for reminders that are just "please act on this soon"
        (e.g. still-unassigned nudges) rather than a state change that needs
        an explicit resolve/reschedule, which is what flag_issue is for."""
        center = await self.center_repo.find_by_id(booking["service_center_id"])
        if center and center.get("manager_id"):
            await self.notifications.notify(center["manager_id"], title, message, NotificationType.BOOKING, str(booking["_id"]))

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
        await self._broadcast_booking_changed(updated)
        return serialize_doc(updated), old_priority

    async def resolve_issue(self, booking_id: str, resolved_by: str, note: str, actor_role: str, actor_center_id: str | None) -> dict:
        booking = await self.repo.find_by_id(booking_id)
        if not booking:
            raise NotFoundException("Booking not found")
        ensure_own_center(actor_role, actor_center_id, booking["service_center_id"])
        updated = await self.repo.update_by_id(booking_id, {"issue_flag": None, "issue_resolved": True})
        await self._record_history(booking_id, BookingStatus(booking["status"]), resolved_by, note)
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

        address = await self.address_repo.find_by_id(booking["address_id"]) if booking.get("address_id") else None
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

    async def _record_history(self, booking_id: str, status: BookingStatus, changed_by: str | None, note: str | None) -> None:
        await self.history_repo.create(
            {
                "booking_id": booking_id,
                "status": status.value if hasattr(status, "value") else status,
                "changed_by": changed_by,
                "note": note,
            }
        )
