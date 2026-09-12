"""
One visit = one trip, all the way through the captain's day.

Creation and assignment already treat several cars at one address as ONE
job (test_multi_vehicle_visit). These tests pin the rest of the lifecycle:

  - heading out for one car is heading out for the visit;
  - releasing one car releases the visit;
  - rescheduling moves the whole visit and moves its ONE seat once;
  - cancelling cars hands back ONE seat, with the last car to leave;
  - the sweeps and reminders speak once per visit, and a visit-wide flag
    shows on every car and clears from every car;
  - the doorstep settlement collects the visit's total once.
"""
from datetime import timedelta

import pytest
from bson import ObjectId

from app.core.exceptions import BadRequestException
from app.schemas.booking_schema import (
    BookingAssignCaptainRequest,
    BookingCancelRequest,
    BookingGroupCreateRequest,
    BookingRescheduleRequest,
    CaptainCancelRequest,
    GroupVehicleRequest,
    HeadingRequest,
    ReassignCaptainRequest,
)
from app.services.booking_service import BookingService
from app.services.payment_service import PaymentService
from app.utils.timezone import now_ist

from tests.factories import get_hatchback_type_id, make_captain, make_customer_with_vehicle, make_service_center, make_vehicle

pytestmark = pytest.mark.asyncio


@pytest.fixture
async def rig(db, cleanup):
    hatchback = await get_hatchback_type_id(db)
    center_id = await make_service_center(db)
    cleanup.append(("service_centers", {"_id": ObjectId(center_id)}))
    customer_id, vehicle_id, address_id = await make_customer_with_vehicle(db, hatchback)
    for coll, q in [("users", {"_id": ObjectId(customer_id)}), ("vehicles", {"owner_id": customer_id}),
                    ("addresses", {"owner_id": customer_id}), ("bookings", {"customer_id": customer_id}),
                    ("notifications", {"user_id": customer_id}),
                    ("slot_capacity", {"service_center_id": center_id}), ("daily_capacity", {"service_center_id": center_id})]:
        cleanup.append((coll, q))
    second = await make_vehicle(db, customer_id, hatchback)
    captain_id = await make_captain(db, center_id)
    cleanup.append(("users", {"_id": ObjectId(captain_id)}))
    cleanup.append(("captain_wallets", {"captain_id": captain_id}))
    cleanup.append(("notifications", {"user_id": captain_id}))
    services = await db.services.find({"is_addon": {"$ne": True}, "is_deleted": {"$ne": True}}).to_list(length=10)
    when = (now_ist().date() + timedelta(days=2)).isoformat()
    slots = await BookingService(db).available_slots(center_id, when)
    available = [s["key"] for s in slots if s["status"] == "available"]
    return {
        "customer_id": customer_id, "address_id": address_id, "center_id": center_id, "captain_id": captain_id,
        "vehicles": [vehicle_id, second], "service": str(services[0]["_id"]), "when": when,
        "slot": available[0], "other_slot": available[1] if len(available) > 1 else None,
    }


async def _visit(rig, db, payment_method="cash"):
    result = await BookingService(db).create_booking_group(
        rig["customer_id"],
        BookingGroupCreateRequest(
            vehicles=[GroupVehicleRequest(vehicle_id=v, service_ids=[rig["service"]]) for v in rig["vehicles"]],
            address_id=rig["address_id"], scheduled_date=rig["when"], scheduled_slot=rig["slot"], payment_method=payment_method,
        ),
    )
    return result["booking_group_id"], [b["id"] for b in result["bookings"]]


async def _cars(db, group_id):
    return sorted(await db.bookings.find({"booking_group_id": group_id}).to_list(length=10), key=lambda b: b.get("group_offset_minutes", 0))


async def _seats(db, rig, slot=None):
    doc = await db.slot_capacity.find_one({"service_center_id": rig["center_id"], "date": rig["when"], "slot_key": slot or rig["slot"]})
    return (doc or {}).get("booked_count", 0)


async def _assign(db, rig, group_id):
    await BookingService(db).assign_captain_to_group(
        group_id, BookingAssignCaptainRequest(captain_id=rig["captain_id"]), "manager-id", "manager", rig["center_id"]
    )


async def _make_headable(db, group_id):
    """Put the visit's clock at 'starts in 20 minutes' so heading out is
    inside the pre-start window — same backdating pattern as
    test_captain_start_window."""
    soon = now_ist() + timedelta(minutes=20)
    for car in await _cars(db, group_id):
        await db.bookings.update_one(
            {"_id": car["_id"]}, {"$set": {"estimated_start_at": soon + timedelta(minutes=car.get("group_offset_minutes", 0))}}
        )


async def test_heading_out_once_covers_every_car(rig, db):
    group_id, ids = await _visit(rig, db)
    await _assign(db, rig, group_id)
    await _make_headable(db, group_id)
    bs = BookingService(db)

    await bs.start_heading(ids[0], HeadingRequest(latitude=22.7, longitude=75.8, equipment_used=[]), rig["captain_id"])

    cars = await _cars(db, group_id)
    assert [c["status"] for c in cars] == ["captain_on_the_way", "captain_on_the_way"], "he left once, for the visit"
    assert all(c.get("heading_at") for c in cars)
    # ...and the customer was told once, not once per car.
    told = await db.notifications.count_documents({"user_id": rig["customer_id"], "title": "Captain on the way"})
    assert told == 1


async def test_the_visit_is_confirmed_and_assigned_once(rig, db):
    group_id, _ = await _visit(rig, db)
    confirmed = await db.notifications.find({"user_id": rig["customer_id"], "title": {"$regex": "booked$"}}).to_list(length=10)
    assert len(confirmed) == 1, "one booking, one confirmation"
    assert "2 vehicles" in confirmed[0]["title"]

    await _assign(db, rig, group_id)
    assert await db.notifications.count_documents({"user_id": rig["captain_id"], "title": {"$regex": "^New job"}}) == 1
    assert await db.notifications.count_documents({"user_id": rig["customer_id"], "title": "Captain assigned"}) == 1


async def test_releasing_one_car_releases_the_visit(rig, db):
    group_id, ids = await _visit(rig, db)
    await _assign(db, rig, group_id)

    await BookingService(db).captain_cancel(ids[1], CaptainCancelRequest(reason="Bike broke down"), rig["captain_id"])

    cars = await _cars(db, group_id)
    assert all(c["status"] == "pending" and c.get("captain_id") is None for c in cars)
    assert await db.notifications.count_documents({"user_id": rig["customer_id"], "title": "Finding you a new captain"}) == 1


async def test_reassigning_one_car_moves_the_visit(rig, db, cleanup):
    group_id, ids = await _visit(rig, db)
    await _assign(db, rig, group_id)
    other = await make_captain(db, rig["center_id"])
    cleanup.append(("users", {"_id": ObjectId(other)}))
    cleanup.append(("captain_wallets", {"captain_id": other}))
    cleanup.append(("notifications", {"user_id": other}))

    await BookingService(db).reassign_captain(ids[0], ReassignCaptainRequest(captain_id=other), "manager-id", "manager", rig["center_id"])

    cars = await _cars(db, group_id)
    assert all(c["captain_id"] == other for c in cars), "one captain takes the whole visit"
    assert await db.notifications.count_documents({"user_id": other, "title": {"$regex": "^New job"}}) == 1


async def test_rescheduling_moves_the_whole_visit_and_one_seat(rig, db):
    if not rig["other_slot"]:
        pytest.skip("need two open slots")
    group_id, ids = await _visit(rig, db)
    assert await _seats(db, rig) == 1

    await BookingService(db).reschedule_booking(
        ids[1], BookingRescheduleRequest(scheduled_date=rig["when"], scheduled_slot=rig["other_slot"]), rig["customer_id"], "customer"
    )

    cars = await _cars(db, group_id)
    assert {c["scheduled_slot"] for c in cars} == {rig["other_slot"]}, "the visit moved together"
    assert all(c["status"] == "rescheduled" for c in cars)
    assert await _seats(db, rig, rig["slot"]) == 0, "the old seat came back once"
    assert await _seats(db, rig, rig["other_slot"]) == 1, "and exactly one seat was taken in the new slot"


async def test_cancelling_hands_back_one_seat_with_the_last_car(rig, db):
    group_id, ids = await _visit(rig, db)
    bs = BookingService(db)
    assert await _seats(db, rig) == 1

    await bs.cancel_booking(ids[0], BookingCancelRequest(reason="Selling this one"), rig["customer_id"], "customer")
    assert await _seats(db, rig) == 1, "one car left the visit; the trip still happens, the seat stays"

    await bs.cancel_booking(ids[1], BookingCancelRequest(reason="Changed plans"), rig["customer_id"], "customer")
    assert await _seats(db, rig) == 0, "the last car out hands the seat back"


async def test_cancelling_the_visit_speaks_once(rig, db):
    group_id, _ = await _visit(rig, db)
    await BookingService(db).cancel_booking_group(group_id, BookingCancelRequest(reason="Away that week"), rig["customer_id"], "customer")
    cars = await _cars(db, group_id)
    assert all(c["status"] == "cancelled" for c in cars)
    assert await _seats(db, rig) == 0
    assert await db.notifications.count_documents({"user_id": rig["customer_id"], "title": {"$regex": "cancelled$"}}) == 1


async def test_sweeps_flag_the_visit_once_and_on_every_car(rig, db):
    group_id, ids = await _visit(rig, db)
    await _assign(db, rig, group_id)
    bs = BookingService(db)
    # Both cars' start times are in the past and nobody headed out.
    for car in await _cars(db, group_id):
        await db.bookings.update_one({"_id": car["_id"]}, {"$set": {"estimated_start_at": now_ist() - timedelta(minutes=40), "assigned_at": now_ist() - timedelta(hours=2)}})

    due = [b for b in await bs.find_bookings_late_to_start() if b.get("booking_group_id") == group_id]
    assert len(due) == 1, "the sweep names the visit once, not once per car"

    await bs.flag_late_to_start(due[0])
    await bs.mark_late_start_reminder_sent(str(due[0]["_id"]))
    cars = await _cars(db, group_id)
    assert all(c.get("issue_flag") == "captain_not_started" and not c.get("issue_resolved") for c in cars), "a trip-level flag shows on every car"
    assert all(c.get("late_start_reminder_sent_at") for c in cars), "...and the reminder is stamped on every car"
    assert await db.notifications.count_documents({"user_id": rig["captain_id"], "title": "You haven't started this booking yet"}) == 1

    await bs.resolve_issue(ids[1], "manager-id", "Called him, on the way", "manager", rig["center_id"])
    cars = await _cars(db, group_id)
    assert all(c.get("issue_flag") is None for c in cars), "resolving on one car resolves the visit"


async def test_completion_is_announced_when_the_last_car_is_done(rig, db):
    group_id, ids = await _visit(rig, db)
    await _assign(db, rig, group_id)
    bs = BookingService(db)
    # Skip straight to mid-service on both cars.
    for car in await _cars(db, group_id):
        await db.bookings.update_one(
            {"_id": car["_id"]},
            {"$set": {"status": "service_started", "vehicle_verified": True, "service_started_at": now_ist(), "heading_at": now_ist()}},
        )
    from app.schemas.booking_schema import PhotoCaptureRequest

    photo = PhotoCaptureRequest(image_url="https://example.com/after.jpg", latitude=22.7, longitude=75.8)
    await bs.capture_after_photo_and_complete(ids[0], photo, rig["captain_id"])
    assert await db.notifications.count_documents({"user_id": rig["customer_id"], "title": "Service completed"}) == 0, "first car done — the visit isn't"
    await bs.capture_after_photo_and_complete(ids[1], photo, rig["captain_id"])
    done = await db.notifications.find({"user_id": rig["customer_id"], "title": "Service completed"}).to_list(length=5)
    assert len(done) == 1 and "2 vehicles" in done[0]["message"]


async def test_doorstep_cash_collects_the_visit_once(rig, db):
    group_id, ids = await _visit(rig, db)
    await _assign(db, rig, group_id)
    pay = PaymentService(db)
    cars = await _cars(db, group_id)
    await db.bookings.update_one({"_id": cars[0]["_id"]}, {"$set": {"status": "completed"}})

    with pytest.raises(BadRequestException, match="every vehicle"):
        await pay.captain_collect_cash(ids[0], rig["captain_id"]), "cash is for the finished visit"

    await db.bookings.update_one({"_id": cars[1]["_id"]}, {"$set": {"status": "completed"}})
    result = await pay.captain_collect_cash(ids[1], rig["captain_id"])
    assert result["vehicles"] == 2
    assert result["amount"] == round(sum(float(c["total_amount"]) for c in cars), 2)
    fresh = await _cars(db, group_id)
    assert all(c["payment_status"] == "paid" and c["payment_method"] == "cash" and c["cash_collected_by"] == rig["captain_id"] for c in fresh)

    status = await pay.captain_check_payment(ids[0], rig["captain_id"])
    assert status["payment_status"] == "paid" and status["vehicles"] == 2


async def test_assigning_a_split_visit_converges_on_one_captain(rig, db, cleanup):
    """The real bug this test pins: a flat booking table lets a manager act
    on ONE row of a visit at a time. If car 1 already has a captain and the
    manager clicks "Assign captain" on car 2's still-pending row and picks
    a DIFFERENT captain, the visit must not end up split between two
    captains — car 1 has to move too. (Reassigning from the already-
    assigned row already handled the mirror case; assigning from the
    pending row did not — this is the gap that produced exactly what a
    user reported: one car "assigned" to a captain, the other "pending".)

    Assigning the group while EVERY car is still pending correctly puts
    both onto the same captain in one shot, so the drift this test pins
    can't arise from ordinary use — it's fabricated below (a raw revert to
    "pending") to stand in for however such a split actually happened
    (a manual correction, data predating this fix, or a bug like this
    one), and proves the healing path copes with it regardless of cause."""
    from tests.factories import make_captain

    group_id, ids = await _visit(rig, db)
    first_captain = rig["captain_id"]
    second_captain = await make_captain(db, rig["center_id"])
    cleanup.append(("users", {"_id": ObjectId(second_captain)}))
    cleanup.append(("captain_wallets", {"captain_id": second_captain}))
    cleanup.append(("notifications", {"user_id": second_captain}))

    bs = BookingService(db)
    await bs.assign_captain(ids[0], BookingAssignCaptainRequest(captain_id=first_captain), "manager-id", "manager", rig["center_id"])
    # Both cars are actually assigned to first_captain at this point — fine,
    # correct behavior. Fabricate the drift: revert car 2 to pending, as if
    # it had never been touched.
    await db.bookings.update_one({"_id": ObjectId(ids[1])}, {"$set": {"status": "pending", "captain_id": None, "assigned_at": None}})
    cars = await _cars(db, group_id)
    assert cars[0]["status"] == "assigned" and cars[0]["captain_id"] == first_captain
    assert cars[1]["status"] == "pending"

    # Manager acts on the STILL-PENDING row and picks a DIFFERENT captain.
    await bs.assign_captain(ids[1], BookingAssignCaptainRequest(captain_id=second_captain), "manager-id", "manager", rig["center_id"])

    cars = await _cars(db, group_id)
    assert all(c["status"] == "assigned" and c["captain_id"] == second_captain for c in cars), "the whole visit converged on the newly-picked captain"
    # Exactly one "New job" landed on the new captain, and the old captain
    # was told he lost the visit — not silently dropped.
    assert await db.notifications.count_documents({"user_id": second_captain, "title": {"$regex": "^New job"}}) == 1
    assert await db.notifications.count_documents({"user_id": first_captain, "title": "Booking reassigned"}) == 1


async def test_assigning_a_split_visit_to_the_already_assigned_captain_moves_only_the_pending_car(rig, db, cleanup):
    """Picking the SAME captain that's already on car 1 while assigning car
    2 must not re-reassign car 1 (no pointless history row, no redundant
    notification) — only the genuinely pending car moves, and the visit
    ends up converged either way."""
    group_id, ids = await _visit(rig, db)
    bs = BookingService(db)
    await bs.assign_captain(ids[0], BookingAssignCaptainRequest(captain_id=rig["captain_id"]), "manager-id", "manager", rig["center_id"])
    await db.bookings.update_one({"_id": ObjectId(ids[1])}, {"$set": {"status": "pending", "captain_id": None, "assigned_at": None}})

    await bs.assign_captain(ids[1], BookingAssignCaptainRequest(captain_id=rig["captain_id"]), "manager-id", "manager", rig["center_id"])

    cars = await _cars(db, group_id)
    assert all(c["status"] == "assigned" and c["captain_id"] == rig["captain_id"] for c in cars)
    assert await db.notifications.count_documents({"user_id": rig["captain_id"], "title": {"$regex": "^New job"}}) == 2, (
        "one for the first car's own assignment, one for the visit-wide notice when the second car joins"
    )
