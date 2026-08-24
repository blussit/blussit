from datetime import datetime, timedelta, timezone

from motor.motor_asyncio import AsyncIOMotorDatabase

from app.core.authz import ensure_own_center
from app.core.exceptions import BadRequestException, ForbiddenException, NotFoundException
from app.models.enums import BookingStatus, NotificationType, PaymentMethod, PaymentStatus
from app.repositories.address_repository import AddressRepository
from app.repositories.booking_repository import BookingRepository, BookingStatusHistoryRepository
from app.repositories.catalog_repository import ComboOfferRepository, ServiceRepository
from app.repositories.inventory_repository import InventoryRepository
from app.repositories.service_center_repository import ServiceCenterRepository
from app.repositories.user_repository import UserRepository
from app.repositories.vehicle_repository import VehicleRepository
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

_ALLOWED_TRANSITIONS: dict[str, set[str]] = {
    BookingStatus.PENDING.value: {BookingStatus.ASSIGNED.value, BookingStatus.CANCELLED.value},
    BookingStatus.ASSIGNED.value: {BookingStatus.CAPTAIN_ON_THE_WAY.value, BookingStatus.CANCELLED.value, BookingStatus.RESCHEDULED.value},
    BookingStatus.CAPTAIN_ON_THE_WAY.value: {BookingStatus.SERVICE_STARTED.value, BookingStatus.CANCELLED.value},
    BookingStatus.SERVICE_STARTED.value: {BookingStatus.COMPLETED.value},
    BookingStatus.RESCHEDULED.value: {BookingStatus.ASSIGNED.value, BookingStatus.PENDING.value, BookingStatus.CANCELLED.value},
}


def _slot_start_datetime(scheduled_date: datetime, scheduled_slot: str) -> datetime:
    start_str = scheduled_slot.split("-")[0].strip()
    hour, minute = [int(p) for p in start_str.split(":")]
    base = to_ist(scheduled_date)
    return base.replace(hour=hour, minute=minute, second=0, microsecond=0)


def _find_window_overlap(new_start: datetime, new_end: datetime, buffer: timedelta, existing: list[dict]) -> dict | None:
    """Shared by _captain_conflict and _customer_conflict — returns the
    first existing booking whose [start, end] window (padded by `buffer`
    on both sides) overlaps [new_start, new_end]. The buffer is applied
    ONCE as the minimum required gap, not independently to both windows
    (see _captain_conflict's docstring for why that distinction matters)."""
    for booking in existing:
        existing_duration = booking.get("duration_minutes", 60)
        existing_start = _slot_start_datetime(booking["scheduled_date"], booking["scheduled_slot"])
        existing_end = existing_start + timedelta(minutes=existing_duration)
        if new_start < existing_end + buffer and existing_start < new_end + buffer:
            return booking
    return None


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
    # issue_notes is written for staff — operational detail like "47 minutes
    # after the scheduled slot time" or "reassign a captain or reschedule
    # this booking". The customer still sees the generic issue_flag label
    # (e.g. "Captain started late") via ISSUE_LABELS, just not this detail.
    "issue_notes",
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
    reschedule it to a real, future time first, or cancel it outright."""
    duration = booking.get("duration_minutes", 60)
    slot_end = _slot_start_datetime(booking["scheduled_date"], booking["scheduled_slot"]) + timedelta(minutes=duration)
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


def _normalize_plate(value: str) -> str:
    return "".join(ch for ch in value.upper() if ch.isalnum())


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
        self.user_repo = UserRepository(db)
        self.inventory_repo = InventoryRepository(db)
        self.coupon_service = CouponService(db)
        self.subscription_service = UserSubscriptionService(db)
        self.notifications = NotificationService(db)
        self.pricing_service = PricingService(db)
        self.wallet_service = WalletService(db)
        self.policy_service = BookingPolicyService(db)

    async def create_booking(self, customer_id: str, payload: BookingCreateRequest) -> dict:
        vehicle = await self.vehicle_repo.find_by_id(payload.vehicle_id)
        if not vehicle or vehicle["owner_id"] != customer_id:
            raise NotFoundException("Vehicle not found")

        address = await self.address_repo.find_by_id(payload.address_id)
        if not address or address["owner_id"] != customer_id:
            raise NotFoundException("Address not found")

        customer = await self.user_repo.find_by_id(customer_id)
        if not customer:
            raise NotFoundException("Customer not found")

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

        duration_minutes = sum(s.get("duration_minutes", 30) for s in services) or 30

        policy = await self.policy_service.get_policy()
        self._validate_scheduling(payload.scheduled_date, payload.scheduled_slot, duration_minutes, policy)
        self_conflict = await self._customer_conflict(customer_id, payload.scheduled_date, payload.scheduled_slot, duration_minutes, None)
        if self_conflict:
            raise BadRequestException(
                f"You already have booking {self_conflict['booking_number']} scheduled around this time — "
                "pick a different time or manage that booking first."
            )

        service_center, distance_km = await self._resolve_service_center(address)

        # First-time-offer eligibility is checked against the WHOLE platform, not
        # just this customer's account — the same car plate or phone number
        # having any prior non-cancelled booking (under any account) disqualifies
        # it. This is what stops "new phone number, same car" discount abuse.
        registration_number = _normalize_plate(vehicle["registration_number"])
        phone = customer.get("phone")
        vehicle_seen_before = await self.repo.exists_for_registration(registration_number, FRAUD_CHECK_EXCLUDED_STATUSES)
        phone_seen_before = bool(phone) and await self.repo.exists_for_phone(phone, FRAUD_CHECK_EXCLUDED_STATUSES)
        first_time_eligible = not vehicle_seen_before and not phone_seen_before

        vehicle_type = vehicle["vehicle_type"]
        if combo:
            subtotal = self._resolve_price(combo, vehicle_type, first_time_eligible)
        else:
            subtotal = sum(self._resolve_price(s, vehicle_type, first_time_eligible) for s in services)

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
            subscription_consumption = await self.subscription_service.plan_consumption(payload.subscription_id, payload.vehicle_id, services)
            payment_method = PaymentMethod.SUBSCRIPTION
            discount_amount = await self._subscription_discount(payload.subscription_id, services, vehicle_type, first_time_eligible)
        elif payload.coupon_code:
            coupon, discount_amount = await self.coupon_service.validate_and_compute_discount(
                payload.coupon_code, subtotal, customer_id
            )

        tax_amount = 0.0
        total_amount = round(subtotal - discount_amount + tax_amount, 2)

        primary_service = services[0] if services else None
        split = await self.pricing_service.calculate_split(subtotal, distance_km, primary_service)

        booking_doc = {
            "booking_number": self.repo.generate_booking_number(),
            "customer_id": customer_id,
            "vehicle_id": payload.vehicle_id,
            "address_id": payload.address_id,
            "service_center_id": str(service_center["_id"]),
            "service_ids": service_ids,
            "subscription_id": payload.subscription_id,
            "scheduled_date": payload.scheduled_date,
            "scheduled_slot": payload.scheduled_slot,
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
            "distance_km": split["distance_km"],
            "captain_travel_pay": split["captain_travel_pay"],
            "captain_service_pay": split["captain_service_pay"],
            "captain_earning": split["captain_earning"],
            "platform_earning": split["platform_earning"],
        }
        created = await self.repo.create(booking_doc)
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
        await self.notifications.notify(
            customer_id,
            "Booking confirmed",
            f"Your booking {created['booking_number']} has been received and is pending assignment.",
            NotificationType.BOOKING,
            booking_id,
        )
        if service_center.get("manager_id"):
            await self.notifications.notify(
                service_center["manager_id"],
                "New booking in your area",
                f"Booking {created['booking_number']} needs a captain assigned.",
                NotificationType.BOOKING,
                booking_id,
            )
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
        result = await self.create_booking(payload.customer_id, booking_request)
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

    def _validate_scheduling(self, scheduled_date: datetime, scheduled_slot: str, duration_minutes: int, policy: dict) -> None:
        """No past bookings, no last-minute bookings, and must fit inside the
        admin-configured daily operating window — all enforced server-side so
        a modified/bypassed frontend can't slip one through."""
        try:
            requested = _slot_start_datetime(scheduled_date, scheduled_slot)
        except (ValueError, IndexError):
            raise BadRequestException("Invalid time format — expected 24-hour HH:MM")

        now = now_ist()
        min_lead = timedelta(minutes=policy["min_lead_minutes"])
        if requested < now + min_lead:
            raise BadRequestException(
                f"Please choose a time at least {policy['min_lead_minutes']} minutes from now."
            )

        op_start_h, op_start_m = [int(p) for p in policy["operating_start"].split(":")]
        op_end_h, op_end_m = [int(p) for p in policy["operating_end"].split(":")]
        window_start = requested.replace(hour=op_start_h, minute=op_start_m, second=0, microsecond=0)
        window_end = requested.replace(hour=op_end_h, minute=op_end_m, second=0, microsecond=0)
        requested_end = requested + timedelta(minutes=duration_minutes)
        if requested < window_start or requested_end > window_end:
            raise BadRequestException(
                f"Please choose a time between {policy['operating_start']} and {policy['operating_end']} "
                f"that leaves enough room for a {duration_minutes}-minute service."
            )

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
        services is fully free. Booking something else on the same
        subscription is a same-visit "swap": covered only up to the cheapest
        service the plan actually includes, the rest is a real charge (e.g. a
        plan covering a ₹399 Foam Wash can swap to a ₹799 Deep Clean for a
        ₹400 top-up, not for free) — never blocked outright, never free
        either. Plans with no included_service_ids (legacy/unrestricted, sold
        before this existed) keep the original behavior: fully waived,
        whatever was picked."""
        plan = await self.subscription_service.get_plan(subscription_id)
        included_ids = set((plan or {}).get("included_service_ids") or [])
        if not included_ids:
            return sum(self._resolve_price(s, vehicle_type, first_time_eligible) for s in services)

        included_docs = await self.service_repo.find_by_ids(list(included_ids))
        included_prices = [self._resolve_price(s, vehicle_type, first_time_eligible) for s in included_docs]
        baseline = min(included_prices) if included_prices else 0.0

        discount = 0.0
        for s in services:
            price = self._resolve_price(s, vehicle_type, first_time_eligible)
            discount += price if str(s["_id"]) in included_ids else min(baseline, price)
        return round(discount, 2)

    async def _captain_conflict(
        self,
        captain_id: str,
        scheduled_date: datetime,
        scheduled_slot: str,
        duration_minutes: int,
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

        Pass `session` when called from inside assign_captain/reassign_captain's
        transaction — that's what actually closes the double-booking race (two
        concurrent assignments both reading "no conflict" before either writes);
        called without a session (e.g. the eligible-captains picker, which is
        read-only and doesn't need transactional consistency), it behaves exactly
        as before."""
        buffer = timedelta(minutes=policy["captain_travel_buffer_minutes"])
        new_start = _slot_start_datetime(scheduled_date, scheduled_slot)
        new_end = new_start + timedelta(minutes=duration_minutes)

        existing = await self.repo.find_active_for_captain(captain_id, exclude_booking_id, session=session)
        return _find_window_overlap(new_start, new_end, buffer, existing)

    async def _customer_conflict(
        self, customer_id: str, scheduled_date: datetime, scheduled_slot: str, duration_minutes: int, exclude_booking_id: str | None
    ) -> dict | None:
        """Same overlap math as _captain_conflict, but against the
        customer's OWN other active bookings — nothing previously stopped a
        customer from booking (or rescheduling into) two overlapping slots
        for themselves. No travel buffer here (that's a captain-schedule
        concept, not a customer one) — a customer's two bookings just can't
        literally overlap in time."""
        new_start = _slot_start_datetime(scheduled_date, scheduled_slot)
        new_end = new_start + timedelta(minutes=duration_minutes)
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
            await self.notifications.notify(
                center["manager_id"],
                f"Location flagged on booking {booking['booking_number']}",
                f"The {stage}-photo was captured {distance_m}m from the customer's address — worth a quick review.",
                NotificationType.BOOKING,
                str(booking["_id"]),
            )

    async def _resolve_service_center(self, address: dict) -> tuple[dict, float]:
        if address.get("latitude") is not None and address.get("longitude") is not None:
            match = await self.center_repo.find_nearest(address["latitude"], address["longitude"])
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
        return [_redact_financials(b, "customer") for b in serialize_list(items)], total

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
        service_ids: set[str] = set()
        combo_ids: set[str] = set()
        for b in bookings:
            if b.get("combo_id"):
                combo_ids.add(b["combo_id"])
            else:
                service_ids.update(b.get("service_ids") or [])

        customers = {str(u["_id"]): u for u in await self.user_repo.find_by_ids(list(customer_ids))}
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
                doc["service_names"] = [services[sid]["name"] for sid in (booking.get("service_ids") or []) if sid in services] or None
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

        captain = await self.user_repo.find_by_id(payload.captain_id)
        if not captain or captain["role"] != "captain":
            raise NotFoundException("Captain not found")
        if captain.get("status") != "active":
            raise BadRequestException("This captain's account isn't active and can't be assigned new bookings.")

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
            conflict = await self._captain_conflict(
                payload.captain_id, fresh["scheduled_date"], fresh["scheduled_slot"], fresh.get("duration_minutes", 60), None, policy, session=session
            )
            if conflict:
                raise BadRequestException(
                    f"This captain is already scheduled for booking {conflict['booking_number']} around this time "
                    f"(including travel buffer) — pick a different captain or time."
                )
            await self.repo.update_by_id(booking_id, {"captain_id": payload.captain_id, "status": BookingStatus.ASSIGNED.value}, session=session)

        async with await self.db.client.start_session() as session:
            await session.with_transaction(_do_assign)

        updated = await self.repo.find_by_id(booking_id)
        await self._record_history(booking_id, BookingStatus.ASSIGNED, assigned_by, f"Assigned to captain {captain['full_name']}")
        await self.notifications.notify(payload.captain_id, "New job assigned", f"You have a new booking {booking['booking_number']}.", NotificationType.BOOKING, booking_id)
        await self.notifications.notify(booking["customer_id"], "Captain assigned", "A captain has been assigned to your booking.", NotificationType.BOOKING, booking_id)
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

        policy = await self.policy_service.get_policy()
        _ensure_schedulable(booking, policy)
        if not await self.wallet_service.is_eligible_for_assignment(payload.captain_id, policy):
            raise BadRequestException("This captain's wallet balance is below the required minimum")

        captain = await self.user_repo.find_by_id(payload.captain_id)
        if not captain or captain["role"] != "captain":
            raise NotFoundException("Captain not found")
        if captain.get("status") != "active":
            raise BadRequestException("This captain's account isn't active and can't be assigned new bookings.")

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
            conflict = await self._captain_conflict(
                payload.captain_id,
                fresh["scheduled_date"],
                fresh["scheduled_slot"],
                fresh.get("duration_minutes", 60),
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
        return serialize_doc(updated)

    async def captain_cancel(self, booking_id: str, payload: CaptainCancelRequest, captain_id: str) -> dict:
        booking = await self.repo.find_by_id(booking_id)
        if not booking:
            raise NotFoundException("Booking not found")
        if booking.get("captain_id") != captain_id:
            raise ForbiddenException("You are not assigned to this booking")
        if booking["status"] not in {BookingStatus.ASSIGNED.value, BookingStatus.CAPTAIN_ON_THE_WAY.value}:
            raise BadRequestException("This booking can no longer be released")

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

        policy = await self.policy_service.get_policy()
        duration = booking.get("duration_minutes", 60)
        slot_start = _slot_start_datetime(booking["scheduled_date"], booking["scheduled_slot"])
        slot_end = slot_start + timedelta(minutes=duration)
        window_start = slot_start - timedelta(minutes=START_WINDOW_MINUTES)
        late_grace_minutes = policy.get("late_start_grace_minutes", 30)
        window_end = slot_end + timedelta(minutes=late_grace_minutes)
        now = now_ist()

        if now < window_start:
            minutes_to_wait = int((window_start - now).total_seconds() // 60)
            raise BadRequestException(
                f"Too early to start this booking. You can begin heading out {START_WINDOW_MINUTES} minutes before "
                f"the scheduled time (in about {minutes_to_wait} more minutes)."
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
            travel_pay = booking.get("captain_travel_pay") or 0
            service_pay = booking.get("captain_service_pay") or 0
            penalty_amount = round(service_pay * penalty_pct / 100, 2)
            new_service_pay = round(service_pay - penalty_amount, 2)
            new_captain_earning = round(travel_pay + new_service_pay, 2)
            update_data["captain_service_pay"] = new_service_pay
            update_data["captain_earning"] = new_captain_earning
            update_data["platform_earning"] = round((booking.get("platform_earning") or 0) + penalty_amount, 2)
            # Distinct from "captain_delay" (the stuck-on-the-way sweep,
            # main.py) — this is specifically "started, but late", not
            # "hasn't reached the customer after an unusually long time".
            update_data["issue_flag"] = "captain_late_start"
            update_data["issue_notes"] = (
                f"Captain started {'late' if stage == 'late' else 'very late'} — "
                f"{int((now - slot_start).total_seconds() // 60)} minutes after the scheduled slot time."
            )
            update_data["issue_flagged_at"] = now
            update_data["issue_resolved"] = False

        for item in payload.equipment_used:
            inv = await self.inventory_repo.find_by_id(item.inventory_item_id)
            if inv and inv["quantity_available"] >= item.quantity:
                await self.inventory_repo.adjust_quantity(item.inventory_item_id, -item.quantity)

        updated = await self.repo.update_by_id(booking_id, update_data)
        await self._record_history(
            booking_id,
            BookingStatus.CAPTAIN_ON_THE_WAY,
            captain_id,
            f"Captain is heading to the customer ({stage.replace('_', ' ')})",
        )
        await self.notifications.notify(booking["customer_id"], "Captain on the way", "Your captain has left for your location.", NotificationType.BOOKING, booking_id)

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

        entered = _normalize_plate(payload.registration_number)
        expected = booking.get("vehicle_registration_number") or ""
        if entered != expected:
            raise BadRequestException(
                "That registration number doesn't match this booking. Double-check the plate before proceeding — "
                "if this really is the wrong vehicle, release the job instead of continuing."
            )

        now = now_ist()
        updated = await self.repo.update_by_id(booking_id, {"vehicle_verified": True, "vehicle_verified_at": now})
        await self._record_history(booking_id, BookingStatus.CAPTAIN_ON_THE_WAY, captain_id, "Vehicle registration verified on arrival")
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
        await self._record_history(booking_id, BookingStatus.SERVICE_STARTED, captain_id, "Service started (before photo captured)")
        if flagged:
            await self._notify_location_flag(booking, "before", distance_m)
        await self.notifications.notify(booking["customer_id"], "Service started", "Your captain has started the service.", NotificationType.BOOKING, booking_id)
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

        now = now_ist()
        flagged, distance_m = await self._check_geofence(booking, payload.latitude, payload.longitude)
        update_data = {
            "status": BookingStatus.COMPLETED.value,
            "after_photo": {"image_url": payload.image_url, "latitude": payload.latitude, "longitude": payload.longitude, "captured_at": now},
            "after_photo_flagged": flagged,
            "after_photo_distance_m": distance_m,
            "completed_at": now,
        }

        # Wash-duration KPI capture, for the future analytics dashboard.
        # booking["service_started_at"] just came back from Mongo naive,
        # holding UTC-instant digits (same category as heading_at) — it MUST
        # go through from_stored() before subtracting against the freshly
        # computed aware-IST `now`, or this silently reproduces the exact
        # 5.5-hour bug the from_stored()/to_ist() split exists to prevent, in
        # a brand-new call site.
        if booking.get("service_started_at"):
            started_ist = from_stored(booking["service_started_at"])
            update_data["actual_duration_minutes"] = max(0, round((now - started_ist).total_seconds() / 60))

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
        await self._record_history(booking_id, BookingStatus.COMPLETED, captain_id, "Service completed (after photo captured)")
        if flagged:
            await self._notify_location_flag(booking, "after", distance_m)
        await self.notifications.notify(
            booking["customer_id"],
            "Service completed",
            f"Booking {booking['booking_number']} is complete. Please rate your captain!",
            NotificationType.BOOKING,
            booking_id,
        )
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

        cancel_data: dict = {"status": BookingStatus.CANCELLED.value, "cancellation_reason": payload.reason, "cancelled_by_role": actor_role}
        # A flagged issue is only meant to prompt manager action WHILE the
        # booking is still live — once it's cancelled there's nothing left to
        # act on. Auto-resolve rather than leave it looking like an open
        # problem forever; issue_flag itself is kept as the historical record
        # of what happened.
        if booking.get("issue_flag") and not booking.get("issue_resolved"):
            cancel_data["issue_resolved"] = True
        updated = await self.repo.update_by_id(booking_id, cancel_data)

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

        policy = await self.policy_service.get_policy()
        duration = booking.get("duration_minutes", 60)
        # Same server-side rules create_booking enforces (no past time, must
        # fit the operating window) — reschedule must not be a backdoor
        # around them just because it's editing an existing booking instead
        # of creating a new one.
        self._validate_scheduling(payload.scheduled_date, payload.scheduled_slot, duration, policy)
        self_conflict = await self._customer_conflict(booking["customer_id"], payload.scheduled_date, payload.scheduled_slot, duration, booking_id)
        if self_conflict:
            raise BadRequestException(
                f"This customer already has booking {self_conflict['booking_number']} scheduled around this new time — "
                "pick a different time."
            )

        had_captain = bool(booking.get("captain_id"))
        update_data: dict = {
            "scheduled_date": payload.scheduled_date,
            "scheduled_slot": payload.scheduled_slot,
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

        updated = await self.repo.update_by_id(booking_id, update_data)
        await self._record_history(
            booking_id,
            BookingStatus.RESCHEDULED,
            actor_id,
            "Rescheduled by customer" if actor_role == "customer" else f"Rescheduled by {actor_role}",
        )
        if actor_role != "customer":
            await self.notifications.notify(
                booking["customer_id"],
                "Booking rescheduled",
                f"Booking {booking['booking_number']} has been moved to a new time — we'll confirm your captain shortly.",
                NotificationType.BOOKING,
                booking_id,
            )
        if had_captain and booking.get("captain_id"):
            await self.notifications.notify(
                booking["captain_id"],
                "Booking rescheduled — no longer yours",
                f"Booking {booking['booking_number']} was rescheduled to a new time and needs a new captain assignment.",
                NotificationType.BOOKING,
                booking_id,
            )
        return serialize_doc(updated)

    async def rebook(self, customer_id: str, booking_id: str, scheduled_date: datetime, scheduled_slot: str) -> dict:
        original = await self.repo.find_by_id(booking_id)
        if not original or original["customer_id"] != customer_id:
            raise NotFoundException("Original booking not found")

        payload = BookingCreateRequest(
            vehicle_id=original["vehicle_id"],
            address_id=original["address_id"],
            service_ids=original["service_ids"],
            scheduled_date=scheduled_date,
            scheduled_slot=scheduled_slot,
            payment_method=PaymentMethod(original["payment_method"]) if original["payment_method"] != "subscription" else PaymentMethod.CASH,
        )
        return await self.create_booking(customer_id, payload)

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
        candidates = await self.repo.find_all_no_paginate(
            {
                "status": {"$in": [BookingStatus.PENDING.value, BookingStatus.RESCHEDULED.value]},
                "issue_flag": None,
            }
        )
        now = now_ist()
        overdue = []
        for booking in candidates:
            duration = booking.get("duration_minutes", 60)
            slot_end = _slot_start_datetime(booking["scheduled_date"], booking["scheduled_slot"]) + timedelta(minutes=duration)
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
        threshold = timedelta(minutes=LATE_START_NUDGE_MINUTES)
        due = []
        for booking in candidates:
            slot_start = _slot_start_datetime(booking["scheduled_date"], booking["scheduled_slot"])
            if now <= slot_start:
                continue
            last_reminder = booking.get("late_start_reminder_sent_at")
            if last_reminder and now - from_stored(last_reminder) < threshold:
                continue
            due.append(booking)
        return due

    async def flag_late_to_start(self, booking: dict) -> None:
        scheduled_time = format_slot_start_12h(booking["scheduled_slot"])
        note = f"Booking {booking['booking_number']} was due to start at {scheduled_time} — the captain hasn't headed out yet."
        existing_flag = booking.get("issue_flag")
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
        candidates = await self.repo.find_all_no_paginate(
            {"status": {"$in": [BookingStatus.PENDING.value, BookingStatus.RESCHEDULED.value]}, "awaiting_assignment_since": {"$ne": None}}
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
        candidates = await self.repo.find_all_no_paginate({"status": BookingStatus.SERVICE_STARTED.value, "issue_flag": None})
        now = now_ist()
        overrunning = []
        for booking in candidates:
            started_at = booking.get("service_started_at")
            if not started_at:
                continue
            duration = booking.get("duration_minutes", 60)
            elapsed_minutes = (now - from_stored(started_at)).total_seconds() / 60
            if elapsed_minutes > duration + SERVICE_OVERRUN_MINUTES:
                overrunning.append(booking)
        return overrunning

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
                    f"Attention needed — booking {booking['booking_number']}",
                    note,
                    NotificationType.BOOKING,
                    booking_id,
                )

    async def notify_center_manager_for_booking(self, booking: dict, title: str, message: str) -> None:
        """Notifies the booking's service center manager without touching
        issue_flag — for reminders that are just "please act on this soon"
        (e.g. still-unassigned nudges) rather than a state change that needs
        an explicit resolve/reschedule, which is what flag_issue is for."""
        center = await self.center_repo.find_by_id(booking["service_center_id"])
        if center and center.get("manager_id"):
            await self.notifications.notify(center["manager_id"], title, message, NotificationType.BOOKING, str(booking["_id"]))

    async def resolve_issue(self, booking_id: str, resolved_by: str, note: str, actor_role: str, actor_center_id: str | None) -> dict:
        booking = await self.repo.find_by_id(booking_id)
        if not booking:
            raise NotFoundException("Booking not found")
        ensure_own_center(actor_role, actor_center_id, booking["service_center_id"])
        updated = await self.repo.update_by_id(booking_id, {"issue_flag": None, "issue_resolved": True})
        await self._record_history(booking_id, BookingStatus(booking["status"]), resolved_by, note)
        return serialize_doc(updated)

    async def _record_history(self, booking_id: str, status: BookingStatus, changed_by: str | None, note: str | None) -> None:
        await self.history_repo.create(
            {
                "booking_id": booking_id,
                "status": status.value if hasattr(status, "value") else status,
                "changed_by": changed_by,
                "note": note,
            }
        )
