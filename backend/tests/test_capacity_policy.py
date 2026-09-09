"""
Capacity policy effective-dating — Sections 1-3 of the BLUSSIT UX/capacity
update: single source of truth for daily max + per-slot distribution,
auto-distribution, manual override with total validation, and
effective-dated scheduling (apply immediately vs. apply from a future
date) with edit/cancel of a not-yet-active change and an auditable
history.
"""
from datetime import datetime, timedelta, timezone

import pytest
from bson import ObjectId

from app.core.exceptions import BadRequestException, ForbiddenException
from app.schemas.booking_schema import BookingCreateRequest
from app.services.booking_service import BookingService
from app.services.capacity_policy_service import CapacityPolicyService

from app.utils.timezone import now_ist

from tests.factories import get_star_wash_service_id, get_hatchback_type_id, make_customer_with_vehicle, make_service_center


@pytest.fixture
async def rig(db, cleanup):
    hatchback = await get_hatchback_type_id(db)
    foam = await get_star_wash_service_id(db)
    # 09:00-21:00 at 3h -> exactly 4 slots: 09-12, 12-15, 15-18, 18-21.
    center_id = await make_service_center(db, working_hours_start="09:00", working_hours_end="21:00", slot_duration_minutes=180, default_slot_capacity=5)
    cleanup.append(("service_centers", {"_id": ObjectId(center_id)}))
    cleanup.append(("slot_capacity", {"service_center_id": center_id}))
    cleanup.append(("daily_capacity", {"service_center_id": center_id}))
    cleanup.append(("capacity_policy_changes", {"service_center_id": center_id}))
    return {"db": db, "center_id": center_id, "hatchback": hatchback, "foam": foam}


def _tomorrow() -> str:
    return (now_ist().replace(tzinfo=None) + timedelta(days=1)).strftime("%Y-%m-%d")


def _in_days(n: int) -> str:
    return (now_ist().replace(tzinfo=None) + timedelta(days=n)).strftime("%Y-%m-%d")


@pytest.mark.asyncio
async def test_auto_distribution_matches_even_split_with_remainder_last(rig):
    cps = CapacityPolicyService(rig["db"])
    result = await cps.schedule_change(rig["center_id"], _tomorrow(), 40, None, "admin", "admin", None)
    dist = result["slot_distribution"]
    assert sum(dist.values()) == 40
    assert dist["09:00-12:00"] == 10 and dist["12:00-15:00"] == 10 and dist["15:00-18:00"] == 10 and dist["18:00-21:00"] == 10


@pytest.mark.asyncio
async def test_manual_distribution_must_sum_to_daily_max(rig):
    cps = CapacityPolicyService(rig["db"])
    with pytest.raises(BadRequestException, match="add up to exactly"):
        await cps.schedule_change(
            rig["center_id"], _tomorrow(), 40,
            {"09:00-12:00": 10, "12:00-15:00": 10, "15:00-18:00": 12, "18:00-21:00": 9},  # sums to 41, not 40
            "admin", "admin", None,
        )


@pytest.mark.asyncio
async def test_manual_distribution_accepted_when_it_matches_the_example(rig):
    cps = CapacityPolicyService(rig["db"])
    result = await cps.schedule_change(
        rig["center_id"], _tomorrow(), 40,
        {"09:00-12:00": 10, "12:00-15:00": 10, "15:00-18:00": 12, "18:00-21:00": 8},
        "admin", "admin", None,
    )
    assert result["slot_distribution"] == {"09:00-12:00": 10, "12:00-15:00": 10, "15:00-18:00": 12, "18:00-21:00": 8}


@pytest.mark.asyncio
async def test_negative_and_unknown_slot_rejected(rig):
    cps = CapacityPolicyService(rig["db"])
    with pytest.raises(BadRequestException, match="negative"):
        await cps.schedule_change(rig["center_id"], _tomorrow(), -5, None, "admin", "admin", None)
    with pytest.raises(BadRequestException, match="Unknown slot"):
        await cps.schedule_change(rig["center_id"], _tomorrow(), 10, {"99:00-100:00": 10}, "admin", "admin", None)


@pytest.mark.asyncio
async def test_past_effective_date_rejected(rig):
    cps = CapacityPolicyService(rig["db"])
    yesterday = (now_ist().replace(tzinfo=None) - timedelta(days=1)).strftime("%Y-%m-%d")
    with pytest.raises(BadRequestException, match="past"):
        await cps.schedule_change(rig["center_id"], yesterday, 40, None, "admin", "admin", None)


@pytest.mark.asyncio
async def test_future_change_does_not_affect_today(rig):
    """The core effective-dating guarantee: scheduling a future change must
    never retroactively alter what's already active today."""
    cps = CapacityPolicyService(rig["db"])
    today = now_ist().replace(tzinfo=None).strftime("%Y-%m-%d")
    # No policy scheduled for today -> resolves to the center's legacy flat default (5).
    today_policy = await cps.get_effective_policy(rig["center_id"], today)
    assert today_policy["max_bookings_per_day"] is None  # no legacy max_bookings_per_day set on this test center

    await cps.schedule_change(rig["center_id"], _in_days(3), 50, None, "admin", "admin", None)

    # Today is still untouched.
    today_policy_after = await cps.get_effective_policy(rig["center_id"], today)
    assert today_policy_after["max_bookings_per_day"] is None
    # But the future date now resolves to the new policy.
    future_policy = await cps.get_effective_policy(rig["center_id"], _in_days(3))
    assert future_policy["max_bookings_per_day"] == 50


@pytest.mark.asyncio
async def test_scheduled_change_editable_and_cancellable_before_active(rig):
    cps = CapacityPolicyService(rig["db"])
    future = _in_days(5)
    created = await cps.schedule_change(rig["center_id"], future, 40, None, "admin", "admin", None)

    # Edit before it's active -> same (center, date) record updates in place.
    edited = await cps.schedule_change(rig["center_id"], future, 60, None, "admin", "admin", None)
    assert edited["id"] == created["id"]
    assert edited["max_bookings_per_day"] == 60

    # Cancel before it's active -> succeeds.
    await cps.cancel_scheduled_change(rig["center_id"], edited["id"], "admin", "admin", None)
    resolved = await cps.get_effective_policy(rig["center_id"], future)
    assert resolved["max_bookings_per_day"] is None  # back to the legacy fallback, cancelled change no longer applies


@pytest.mark.asyncio
async def test_cannot_cancel_an_already_active_change(rig):
    cps = CapacityPolicyService(rig["db"])
    today = now_ist().replace(tzinfo=None).strftime("%Y-%m-%d")
    active = await cps.schedule_change(rig["center_id"], today, 40, None, "admin", "admin", None)
    with pytest.raises(BadRequestException, match="already active"):
        await cps.cancel_scheduled_change(rig["center_id"], active["id"], "admin", "admin", None)


@pytest.mark.asyncio
async def test_apply_immediately_is_editable_same_day(rig):
    """'Apply immediately' (effective_date = today) must remain adjustable
    later the same day — re-running it updates the same record rather than
    erroring or creating a duplicate."""
    cps = CapacityPolicyService(rig["db"])
    today = now_ist().replace(tzinfo=None).strftime("%Y-%m-%d")
    first = await cps.schedule_change(rig["center_id"], today, 40, None, "admin", "admin", None)
    second = await cps.schedule_change(rig["center_id"], today, 45, None, "admin", "admin", None)
    assert first["id"] == second["id"]
    assert second["max_bookings_per_day"] == 45


@pytest.mark.asyncio
async def test_history_labels_active_scheduled_and_past(rig):
    cps = CapacityPolicyService(rig["db"])
    today = now_ist().replace(tzinfo=None).strftime("%Y-%m-%d")
    await cps.schedule_change(rig["center_id"], today, 40, None, "admin", "admin", None)
    await cps.schedule_change(rig["center_id"], _in_days(2), 50, None, "admin", "admin", None)

    history = await cps.list_history(rig["center_id"])
    by_date = {h["effective_date"]: h["status"] for h in history}
    assert by_date[today] == "active"
    assert by_date[_in_days(2)] == "scheduled"


@pytest.mark.asyncio
async def test_policy_change_resyncs_an_already_touched_date(rig):
    """Regression for the reported bug: once a date's slot_capacity/
    daily_capacity docs are lazily created (e.g. simply opening the
    Overview tab initializes them via get_or_init), a LATER capacity-policy
    change targeting that same date used to never reach back to update
    them — Overview kept showing stale numbers even after "Apply
    immediately". Applying a policy for an already-touched date must
    resync it right away."""
    cps = CapacityPolicyService(rig["db"])
    bs = BookingService(rig["db"])
    today = now_ist().replace(tzinfo=None).strftime("%Y-%m-%d")

    # Touch today's docs first, under the (as-yet-unconfigured) legacy default.
    before = await bs.admin_slot_capacity(rig["center_id"], today)
    assert before["slots"][0]["capacity"] == 5  # legacy default_slot_capacity

    # Now change the policy for today ("Apply immediately").
    await cps.schedule_change(rig["center_id"], today, 40, None, "admin", "admin", None)

    after = await bs.admin_slot_capacity(rig["center_id"], today)
    dist = {s["key"]: s["capacity"] for s in after["slots"]}
    assert dist == {"09:00-12:00": 10, "12:00-15:00": 10, "15:00-18:00": 10, "18:00-21:00": 10}


@pytest.mark.asyncio
async def test_resync_preserves_booked_count_and_respects_manual_override(rig):
    """The resync must never touch booked_count (no existing reservation is
    invalidated) and must never overwrite a slot an admin explicitly
    overrode via set_slot_capacity — that override still wins over the
    baseline policy."""
    cps = CapacityPolicyService(rig["db"])
    bs = BookingService(rig["db"])
    today = now_ist().replace(tzinfo=None).strftime("%Y-%m-%d")

    await bs.admin_slot_capacity(rig["center_id"], today)  # touch
    await rig["db"].slot_capacity.update_one(
        {"service_center_id": rig["center_id"], "date": today, "slot_key": "09:00-12:00"}, {"$set": {"booked_count": 3}}
    )
    await bs.set_slot_capacity(rig["center_id"], today, "12:00-15:00", capacity=99, is_closed=None)

    await cps.schedule_change(rig["center_id"], today, 40, None, "admin", "admin", None)

    result = await bs.admin_slot_capacity(rig["center_id"], today)
    by_key = {s["key"]: s for s in result["slots"]}
    assert by_key["09:00-12:00"]["capacity"] == 10  # resynced to the new policy
    assert by_key["09:00-12:00"]["booked_count"] == 3  # untouched
    assert by_key["12:00-15:00"]["capacity"] == 99  # manual override still wins


@pytest.mark.asyncio
async def test_manager_cannot_schedule_capacity_for_another_center(rig, db, cleanup):
    other_center_id = await make_service_center(db)
    cleanup.append(("service_centers", {"_id": ObjectId(other_center_id)}))
    cps = CapacityPolicyService(db)
    with pytest.raises(ForbiddenException):
        await cps.schedule_change(rig["center_id"], _tomorrow(), 40, None, "manager-x", "manager", other_center_id)


@pytest.mark.asyncio
async def test_booking_flow_picks_up_the_effective_policy_for_its_date(rig, cleanup):
    """End-to-end: a real booking created against a future date honors that
    date's scheduled capacity policy, not the center's legacy default."""
    cps = CapacityPolicyService(rig["db"])
    bs = BookingService(rig["db"])
    target_date_str = _in_days(2)
    await cps.schedule_change(rig["center_id"], target_date_str, 2, {"09:00-12:00": 1, "12:00-15:00": 1, "15:00-18:00": 0, "18:00-21:00": 0}, "admin", "admin", None)

    target_date = datetime.strptime(target_date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    customer_id, vehicle_id, address_id = await make_customer_with_vehicle(rig["db"], rig["hatchback"])
    cleanup.append(("users", {"_id": ObjectId(customer_id)}))
    cleanup.append(("vehicles", {"owner_id": customer_id}))
    cleanup.append(("addresses", {"owner_id": customer_id}))
    cleanup.append(("bookings", {"customer_id": customer_id}))

    booking = await bs.create_booking(customer_id, BookingCreateRequest(vehicle_id=vehicle_id, address_id=address_id, service_ids=[rig["foam"]], scheduled_date=target_date, scheduled_slot="09:00-12:00"))
    assert booking["status"] == "pending"

    doc = await rig["db"].slot_capacity.find_one({"service_center_id": rig["center_id"], "date": target_date_str, "slot_key": "09:00-12:00"})
    assert doc["capacity"] == 1  # picked up from the scheduled policy, not the center's default_slot_capacity=5

    # That slot's capacity is now exhausted (1/1) — a second booking into
    # the SAME slot on the SAME date must be rejected.
    customer2_id, vehicle2_id, address2_id = await make_customer_with_vehicle(rig["db"], rig["hatchback"])
    cleanup.append(("users", {"_id": ObjectId(customer2_id)}))
    cleanup.append(("vehicles", {"owner_id": customer2_id}))
    cleanup.append(("addresses", {"owner_id": customer2_id}))
    cleanup.append(("bookings", {"customer_id": customer2_id}))
    with pytest.raises(BadRequestException):
        await bs.create_booking(customer2_id, BookingCreateRequest(vehicle_id=vehicle2_id, address_id=address2_id, service_ids=[rig["foam"]], scheduled_date=target_date, scheduled_slot="09:00-12:00"))
