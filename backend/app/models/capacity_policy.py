from typing import Optional

from app.models.base import BusinessRecordBase


class CapacityPolicyChangeModel(BusinessRecordBase):
    """One (service_center_id, effective_date) capacity policy — the
    single source of truth for "what should this center's daily max and
    per-slot distribution be, from this date onward" (Section 1/3 of the
    ops spec: capacity effective-dating, capacity history).

    Resolution rule (see CapacityPolicyService.get_effective_policy): for
    any given date, the policy in effect is the one with the LARGEST
    effective_date that is still <= that date, across all of a center's
    non-deleted policy changes. A date before every policy change falls
    back to the service center's own legacy default_slot_capacity/
    max_bookings_per_day fields (kept for backward compatibility, no
    longer directly editable via the UI).

    Editing/cancelling only ever applies to a change whose effective_date
    is still in the future (not yet active) — see
    CapacityPolicyService.schedule/cancel. Once effective_date <= today it
    becomes part of the immutable history; changing capacity from that
    point forward means scheduling a NEW change with a later date, not
    mutating this one. A unique index on (service_center_id,
    effective_date) means re-scheduling the SAME future date is naturally
    an edit-in-place (upsert), not a duplicate."""

    service_center_id: str
    effective_date: str  # "YYYY-MM-DD", IST calendar date
    max_bookings_per_day: int
    # slot_key -> capacity, e.g. {"09:00-12:00": 10, "12:00-15:00": 10, ...}.
    # Must sum to exactly max_bookings_per_day — enforced in the service
    # layer, never trusted from the client alone (Section 24: "slot totals
    # must remain consistent with daily capacity").
    slot_distribution: dict[str, int] = {}
    note: Optional[str] = None
