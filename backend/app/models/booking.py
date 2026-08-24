from datetime import datetime
from typing import Optional

from pydantic import BaseModel

from app.models.base import BusinessRecordBase
from app.models.enums import BookingStatus, PaymentMethod, PaymentStatus


class GeoPoint(BaseModel):
    latitude: float
    longitude: float


class PhotoProof(BaseModel):
    """A camera-captured photo with its geo-tagged metadata. Never a file upload —
    the frontend enforces capture-from-camera-only so this can only come from a live camera."""
    image_url: str
    latitude: float
    longitude: float
    captured_at: datetime


class EquipmentUsedItem(BaseModel):
    inventory_item_id: str
    item_name: str
    quantity: float


class BookingModel(BusinessRecordBase):
    booking_number: str
    customer_id: str
    vehicle_id: str
    address_id: str
    service_center_id: str
    captain_id: Optional[str] = None
    service_ids: list[str] = []
    subscription_id: Optional[str] = None
    # Exactly what consume_for_services deducted at creation time (see
    # UserSubscriptionService.plan_consumption/commit_consumption) — snapshotted
    # here so cancel_booking can restore precisely this much, without having to
    # recompute it from service_ids later (which could resolve differently if a
    # service was edited/deleted in the meantime).
    subscription_consumption: Optional[dict] = None

    scheduled_date: datetime
    scheduled_slot: str
    duration_minutes: int = 60  # planned/estimated, summed from service durations at creation
    actual_duration_minutes: Optional[int] = None  # actual before-photo -> after-photo elapsed time, set on completion

    # When this booking most recently entered a "needs a captain" state (set
    # at creation, reset on reschedule) — drives the repeating "still
    # unassigned" manager reminder, independent of how far off the scheduled
    # slot itself still is. See BookingService.find_bookings_unassigned_too_long.
    awaiting_assignment_since: Optional[datetime] = None
    unassigned_reminder_sent_at: Optional[datetime] = None  # throttles that reminder to once per interval

    # Throttles the "assigned but captain hasn't started heading, and the
    # scheduled start time has already passed" repeating nudge — see
    # BookingService.find_bookings_late_to_start.
    late_start_reminder_sent_at: Optional[datetime] = None

    status: BookingStatus = BookingStatus.PENDING
    payment_status: PaymentStatus = PaymentStatus.PENDING
    payment_method: PaymentMethod = PaymentMethod.CASH

    subtotal: float = 0
    discount_amount: float = 0
    tax_amount: float = 0
    total_amount: float = 0
    coupon_code: Optional[str] = None

    # Snapshotted at creation time so fraud checks (first-time-offer eligibility,
    # vehicle-on-arrival verification) never need a join and stay accurate even
    # if the customer edits their profile/vehicle later.
    vehicle_registration_number: Optional[str] = None
    customer_phone: Optional[str] = None
    vehicle_verified: bool = False
    vehicle_verified_at: Optional[datetime] = None

    distance_km: Optional[float] = None
    captain_travel_pay: Optional[float] = None
    captain_service_pay: Optional[float] = None
    captain_earning: Optional[float] = None
    platform_earning: Optional[float] = None
    wallet_settled: bool = False

    customer_notes: Optional[str] = None
    # Optional stand-in contact for this specific visit (e.g. a family member
    # or a driver who'll actually be present at the address) — purely
    # informational for the captain/manager, never used for login or account
    # matters.
    alternate_contact_name: Optional[str] = None
    alternate_contact_phone: Optional[str] = None
    cancellation_reason: Optional[str] = None
    cancelled_by_role: Optional[str] = None

    equipment_used: list[EquipmentUsedItem] = []
    heading_at: Optional[datetime] = None
    heading_location: Optional[GeoPoint] = None
    # Which timing stage the captain started in, and what it cost them — see
    # BookingService.start_heading(). "early"/"on_time" = no penalty,
    # "late"/"severely_late" = a slice of the service fee shifts to the
    # platform and the manager gets notified.
    captain_start_stage: Optional[str] = None
    late_penalty_pct: float = 0
    # Generic issue flag surfaced to the manager — auto-set by the background
    # sweep (captain never started, captain stuck heading out) or by a late
    # start. Cleared automatically when the manager reassigns/reschedules, or
    # explicitly via resolve_issue().
    issue_flag: Optional[str] = None
    issue_notes: Optional[str] = None
    issue_flagged_at: Optional[datetime] = None
    issue_resolved: bool = True
    reminder_sent: bool = False

    before_photo: Optional[PhotoProof] = None
    before_photo_flagged: bool = False
    before_photo_distance_m: Optional[float] = None
    service_started_at: Optional[datetime] = None

    after_photo: Optional[PhotoProof] = None
    after_photo_flagged: bool = False
    after_photo_distance_m: Optional[float] = None
    completed_at: Optional[datetime] = None

    previous_captain_ids: list[str] = []

    is_rated: bool = False


class BookingStatusHistoryModel(BusinessRecordBase):
    booking_id: str
    status: BookingStatus
    changed_by: Optional[str] = None
    note: Optional[str] = None
