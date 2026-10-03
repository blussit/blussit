"""
Multi-vehicle visits: one customer, several of their cars, ONE trip.

The founder's rule is that three cars at one address is ONE slot — the
captain arrives once, sets up once and works through them, so the capacity
a visit consumes is the capacity of a trip. Everything below exists to make
that safe: the seat is taken once, the travel is paid once, but the
captain's CLOCK still reflects every car, so he can't be handed another job
while he's on car three.
"""
from datetime import timedelta

import pytest
from bson import ObjectId

from app.core.exceptions import BadRequestException
from app.schemas.booking_schema import BookingGroupCreateRequest, GroupVehicleRequest
from app.services.booking_service import BookingService, _captain_booking_window
from app.utils.timezone import now_ist

from tests.factories import (
    get_hatchback_type_id,
    make_customer_with_vehicle,
    make_service_center,
    make_vehicle,
)

pytestmark = pytest.mark.asyncio


@pytest.fixture
async def rig(db, cleanup):
    hatchback = await get_hatchback_type_id(db)
    center_id = await make_service_center(db)
    cleanup.append(("service_centers", {"_id": ObjectId(center_id)}))
    customer_id, vehicle_id, address_id = await make_customer_with_vehicle(db, hatchback)
    for coll, q in [("users", {"_id": ObjectId(customer_id)}), ("vehicles", {"owner_id": customer_id}),
                    ("addresses", {"owner_id": customer_id}), ("bookings", {"customer_id": customer_id}),
                    ("slot_capacity", {"service_center_id": center_id}), ("daily_capacity", {"service_center_id": center_id})]:
        cleanup.append((coll, q))
    extra = [await make_vehicle(db, customer_id, hatchback) for _ in range(4)]
    services = await db.services.find({"is_addon": {"$ne": True}, "is_deleted": {"$ne": True}}).to_list(length=10)
    when = (now_ist().date() + timedelta(days=2)).isoformat()
    slots = await BookingService(db).available_slots(center_id, when)
    slot = next(s["key"] for s in slots if s["status"] == "available")
    return {
        "db": db, "customer_id": customer_id, "address_id": address_id, "center_id": center_id,
        "vehicles": [vehicle_id, *extra], "services": services, "when": when, "slot": slot,
    }


def _cars(rig, count, service_index=0):
    return [
        GroupVehicleRequest(vehicle_id=rig["vehicles"][i], service_ids=[str(rig["services"][service_index]["_id"])])
        for i in range(count)
    ]


async def _seat_count(db, rig):
    doc = await db.slot_capacity.find_one(
        {"service_center_id": rig["center_id"], "date": rig["when"], "slot_key": rig["slot"]}
    )
    return (doc or {}).get("booked_count", 0)


async def test_three_cars_take_one_slot_seat(rig, db):
    """The founder's rule, stated as a test: same address, one arrival, one
    seat — however many cars are on the visit."""
    result = await BookingService(db).create_booking_group(
        rig["customer_id"],
        BookingGroupCreateRequest(
            vehicles=_cars(rig, 3), address_id=rig["address_id"],
            scheduled_date=rig["when"], scheduled_slot=rig["slot"], payment_method="cash",
        ),
    )
    assert result["vehicle_count"] == 3
    assert len(result["bookings"]) == 3
    assert await _seat_count(db, rig) == 1, "three cars on one visit must take ONE seat"

    # Every car is a real booking, all sharing the visit.
    group_id = result["booking_group_id"]
    rows = await db.bookings.find({"booking_group_id": group_id}).to_list(length=10)
    assert len(rows) == 3
    assert all(r["customer_id"] == rig["customer_id"] for r in rows)


async def test_each_car_can_have_a_different_service(rig, db):
    if len(rig["services"]) < 2:
        pytest.skip("need two services to exercise per-car choice")
    cars = [
        GroupVehicleRequest(vehicle_id=rig["vehicles"][0], service_ids=[str(rig["services"][0]["_id"])]),
        GroupVehicleRequest(vehicle_id=rig["vehicles"][1], service_ids=[str(rig["services"][1]["_id"])]),
    ]
    result = await BookingService(db).create_booking_group(
        rig["customer_id"],
        BookingGroupCreateRequest(
            vehicles=cars, address_id=rig["address_id"],
            scheduled_date=rig["when"], scheduled_slot=rig["slot"], payment_method="cash",
        ),
    )
    picked = {b["service_ids"][0] for b in result["bookings"]}
    assert picked == {str(rig["services"][0]["_id"]), str(rig["services"][1]["_id"])}


async def test_travel_is_paid_once_for_the_trip(rig, db):
    """One journey, one travel payment. Paying it per car would leak margin
    on every multi-car visit."""
    result = await BookingService(db).create_booking_group(
        rig["customer_id"],
        BookingGroupCreateRequest(
            vehicles=_cars(rig, 3), address_id=rig["address_id"],
            scheduled_date=rig["when"], scheduled_slot=rig["slot"], payment_method="cash",
        ),
    )
    travel = [float(b.get("captain_travel_pay") or 0) for b in result["bookings"]]
    assert sum(1 for t in travel if t > 0) <= 1, f"travel paid more than once: {travel}"
    # The service fee is still earned on every car.
    assert all(float(b.get("captain_service_pay") or 0) > 0 for b in result["bookings"])


async def test_the_captains_clock_covers_the_whole_visit(rig, db):
    """Cars are staggered, so the captain's blocked time runs across all of
    them — otherwise he'd be handed another job while still on car three."""
    result = await BookingService(db).create_booking_group(
        rig["customer_id"],
        BookingGroupCreateRequest(
            vehicles=_cars(rig, 3), address_id=rig["address_id"],
            scheduled_date=rig["when"], scheduled_slot=rig["slot"], payment_method="cash",
        ),
    )
    rows = sorted(
        await db.bookings.find({"booking_group_id": result["booking_group_id"]}).to_list(length=10),
        key=lambda r: r.get("group_offset_minutes", 0),
    )
    offsets = [r.get("group_offset_minutes", 0) for r in rows]
    assert offsets == sorted(offsets) and offsets[0] == 0
    assert offsets[-1] > 0, "later cars must start after the earlier ones"

    # Windows follow the offsets and don't overlap each other.
    windows = [_captain_booking_window(r) for r in rows]
    for (_, earlier_end), (later_start, _) in zip(windows, windows[1:]):
        assert later_start >= earlier_end, "cars on one visit must not overlap"
    assert result["total_duration_minutes"] == sum(int(r.get("duration_minutes") or 60) for r in rows)


async def test_more_than_the_limit_is_refused(rig, db):
    with pytest.raises(BadRequestException, match="up to"):
        await BookingService(db).create_booking_group(
            rig["customer_id"],
            BookingGroupCreateRequest(
                vehicles=_cars(rig, 5) + [GroupVehicleRequest(
                    vehicle_id=rig["vehicles"][0], service_ids=[str(rig["services"][0]["_id"])])],
                address_id=rig["address_id"], scheduled_date=rig["when"],
                scheduled_slot=rig["slot"], payment_method="cash",
            ),
        )
    assert await _seat_count(db, rig) == 0, "a refused visit must not hold a seat"


async def test_the_same_car_cannot_be_added_twice(rig, db):
    same = rig["vehicles"][0]
    with pytest.raises(BadRequestException, match="only be added once"):
        await BookingService(db).create_booking_group(
            rig["customer_id"],
            BookingGroupCreateRequest(
                vehicles=[
                    GroupVehicleRequest(vehicle_id=same, service_ids=[str(rig["services"][0]["_id"])]),
                    GroupVehicleRequest(vehicle_id=same, service_ids=[str(rig["services"][0]["_id"])]),
                ],
                address_id=rig["address_id"], scheduled_date=rig["when"],
                scheduled_slot=rig["slot"], payment_method="cash",
            ),
        )


async def test_a_failed_car_undoes_the_whole_visit(rig, db):
    """All-or-nothing: a half-booked visit (two cars wash, one silently
    missing) is worse than a clean failure, and the seat must go back."""
    bad = str(ObjectId())  # a vehicle this customer doesn't own
    with pytest.raises(Exception):
        await BookingService(db).create_booking_group(
            rig["customer_id"],
            BookingGroupCreateRequest(
                vehicles=[
                    GroupVehicleRequest(vehicle_id=rig["vehicles"][0], service_ids=[str(rig["services"][0]["_id"])]),
                    GroupVehicleRequest(vehicle_id=rig["vehicles"][1], service_ids=[str(rig["services"][0]["_id"])]),
                    GroupVehicleRequest(vehicle_id=bad, service_ids=[str(rig["services"][0]["_id"])]),
                ],
                address_id=rig["address_id"], scheduled_date=rig["when"],
                scheduled_slot=rig["slot"], payment_method="cash",
            ),
        )
    live = await db.bookings.count_documents(
        {"customer_id": rig["customer_id"], "status": {"$ne": "cancelled"}, "is_deleted": {"$ne": True}}
    )
    assert live == 0, "no car may survive a failed visit"
    assert await _seat_count(db, rig) == 0, "the slot seat must be handed back"

# ------------------------------------------------------- paying for a visit


class _StubOrders:
    def create(self, payload):
        return {"id": f"order_visit_{payload['receipt']}", **payload}


class _StubClient:
    order = _StubOrders()


@pytest.fixture
def gateway(monkeypatch):
    from app.services import payment_service

    monkeypatch.setattr(payment_service.settings, "RAZORPAY_KEY_ID", "rzp_test_stub")
    monkeypatch.setattr(payment_service.settings, "RAZORPAY_KEY_SECRET", "stub_secret_key")
    monkeypatch.setattr(payment_service, "_razorpay_client", lambda: _StubClient())


async def test_one_payment_settles_every_car_on_the_visit(rig, db, gateway, cleanup):
    """Three cars, ONE order, ONE signature — and all three end up paid.
    Three separate payments for one visit would be indefensible UX."""
    from app.schemas.payment_schema import CreateOrderRequest, VerifyPaymentRequest
    from app.services.payment_service import PaymentService, _expected_signature

    cleanup.append(("payment_orders", {"customer_id": rig["customer_id"]}))
    result = await BookingService(db).create_booking_group(
        rig["customer_id"],
        BookingGroupCreateRequest(
            vehicles=_cars(rig, 3), address_id=rig["address_id"],
            scheduled_date=rig["when"], scheduled_slot=rig["slot"], payment_method="online",
        ),
    )
    group_id = result["booking_group_id"]
    # Online ⇒ every car is parked until the money lands.
    rows = await db.bookings.find({"booking_group_id": group_id}).to_list(length=10)
    assert all(r["status"] == "awaiting_payment" for r in rows)

    payments = PaymentService(db)
    order = await payments.create_order(
        rig["customer_id"], CreateOrderRequest(purpose="booking_group", booking_group_id=group_id)
    )
    expected = sum(int(round(float(r["total_amount"]) * 100)) for r in rows)
    assert order["amount"] == expected, "the visit is charged as the sum of its cars"

    verified = await payments.verify_payment(rig["customer_id"], VerifyPaymentRequest(
        razorpay_order_id=order["order_id"], razorpay_payment_id="pay_visit",
        razorpay_signature=_expected_signature(order["order_id"], "pay_visit")))
    assert verified["purpose"] == "booking_group" and verified["settled_count"] == 3

    rows = await db.bookings.find({"booking_group_id": group_id}).to_list(length=10)
    assert all(r["payment_status"] == "paid" for r in rows), "every car must be paid"
    assert all(r["status"] == "pending" for r in rows), "and every car must enter the queue"


async def test_another_customer_cannot_pay_for_someone_elses_visit(rig, db, gateway, cleanup):
    """The group id travels through the browser — ownership is the whole
    defence against reading a stranger's visit total."""
    from app.core.exceptions import NotFoundException
    from app.schemas.payment_schema import CreateOrderRequest
    from app.services.payment_service import PaymentService
    from tests.factories import make_customer

    cleanup.append(("payment_orders", {"customer_id": rig["customer_id"]}))
    result = await BookingService(db).create_booking_group(
        rig["customer_id"],
        BookingGroupCreateRequest(
            vehicles=_cars(rig, 2), address_id=rig["address_id"],
            scheduled_date=rig["when"], scheduled_slot=rig["slot"], payment_method="cash",
        ),
    )
    attacker = await make_customer(db)
    cleanup.append(("users", {"_id": ObjectId(attacker)}))
    with pytest.raises(NotFoundException):
        await PaymentService(db).create_order(
            attacker, CreateOrderRequest(purpose="booking_group", booking_group_id=result["booking_group_id"])
        )


async def test_one_captain_takes_the_whole_visit(rig, db, cleanup):
    """Splitting a visit between two captains would send two people to the
    same gate. Assigning the visit assigns every car — each keeping its own
    staggered start, and without the cars conflicting with one another."""
    from app.schemas.booking_schema import BookingAssignCaptainRequest
    from tests.factories import make_captain

    result = await BookingService(db).create_booking_group(
        rig["customer_id"],
        BookingGroupCreateRequest(
            vehicles=_cars(rig, 3), address_id=rig["address_id"],
            scheduled_date=rig["when"], scheduled_slot=rig["slot"], payment_method="cash",
        ),
    )
    captain_id = await make_captain(db, rig["center_id"])
    cleanup.append(("users", {"_id": ObjectId(captain_id)}))

    assigned = await BookingService(db).assign_captain_to_group(
        result["booking_group_id"], BookingAssignCaptainRequest(captain_id=captain_id),
        "manager-id", "manager", rig["center_id"],
    )
    assert assigned["assigned_count"] == 3

    rows = sorted(
        await db.bookings.find({"booking_group_id": result["booking_group_id"]}).to_list(length=10),
        key=lambda r: r.get("group_offset_minutes", 0),
    )
    assert all(r["captain_id"] == captain_id for r in rows), "one captain takes the visit"
    assert all(r["status"] == "assigned" for r in rows)
    # Staggered, not piled onto one instant.
    starts = [r["estimated_start_at"] for r in rows]
    assert starts == sorted(starts) and starts[0] != starts[-1]


async def test_visit_read_is_scoped_and_redacted(rig, db, cleanup):
    """Reading a visit is a booking read like any other, so it obeys the
    same two rules: only people connected to it can see it, and each role
    only sees the fields their role is allowed.

    A captain on ONE car is on the visit; a captain on none of it is a
    stranger, even though he is staff. And a customer reading their own
    visit must not receive the captain's pay or the internal issue flags —
    the group endpoint was returning raw enriched documents, unlike every
    single-booking read."""
    from app.core.exceptions import NotFoundException
    from app.schemas.booking_schema import BookingAssignCaptainRequest
    from tests.factories import make_captain, make_customer

    bs = BookingService(db)
    result = await bs.create_booking_group(
        rig["customer_id"],
        BookingGroupCreateRequest(
            vehicles=_cars(rig, 2), address_id=rig["address_id"],
            scheduled_date=rig["when"], scheduled_slot=rig["slot"], payment_method="cash",
        ),
    )
    group_id = result["booking_group_id"]

    ours = await make_captain(db, rig["center_id"])
    theirs = await make_captain(db, rig["center_id"])
    for cid in (ours, theirs):
        cleanup.append(("users", {"_id": ObjectId(cid)}))
    await bs.assign_captain_to_group(
        group_id, BookingAssignCaptainRequest(captain_id=ours), "manager-id", "manager", rig["center_id"]
    )

    # The captain working it sees it; the one who isn't gets a 404, not a
    # 403 — a guessed group id must not confirm the visit exists.
    seen = await bs.get_booking_group(group_id, ours, "captain", rig["center_id"])
    assert len(seen) == 2
    assert all("platform_earning" not in b for b in seen), "a captain never sees the platform's margin"
    with pytest.raises(NotFoundException):
        await bs.get_booking_group(group_id, theirs, "captain", rig["center_id"])

    # The customer's own visit, with the internal ops fields stripped.
    mine = await bs.get_booking_group(group_id, rig["customer_id"], "customer", None)
    assert len(mine) == 2
    for field in ("captain_earning", "platform_earning", "issue_flag", "issue_notes"):
        assert all(field not in b for b in mine), f"{field} must not reach a customer"

    stranger = await make_customer(db)
    cleanup.append(("users", {"_id": ObjectId(stranger)}))
    with pytest.raises(NotFoundException):
        await bs.get_booking_group(group_id, stranger, "customer", None)


async def _visit_with_a_clash_on_car_three(rig, db, cleanup):
    """A 3-car visit plus another job of the captain's that starts exactly
    when car 3 would — car 1 fits, a later car doesn't."""
    from app.services.booking_service import _resolve_estimated_start
    from tests.factories import make_captain

    result = await BookingService(db).create_booking_group(
        rig["customer_id"],
        BookingGroupCreateRequest(
            vehicles=_cars(rig, 3), address_id=rig["address_id"],
            scheduled_date=rig["when"], scheduled_slot=rig["slot"], payment_method="cash",
        ),
    )
    captain_id = await make_captain(db, rig["center_id"])
    cleanup.append(("users", {"_id": ObjectId(captain_id)}))
    cleanup.append(("notifications", {"user_id": captain_id}))
    rows = sorted(
        await db.bookings.find({"booking_group_id": result["booking_group_id"]}).to_list(length=10),
        key=lambda r: r.get("group_offset_minutes", 0),
    )
    car3 = rows[2]
    clash = {k: v for k, v in car3.items() if k not in ("_id", "booking_group_id", "group_offset_minutes")}
    clash.update({
        "booking_number": f"BK-CLASH-{ObjectId()}", "captain_id": captain_id, "status": "assigned",
        "customer_id": str(ObjectId()), "visit_line_key": f"clash-{ObjectId()}",
        "estimated_start_at": _resolve_estimated_start(car3, None),
    })
    inserted = await db.bookings.insert_one(clash)
    cleanup.append(("bookings", {"_id": inserted.inserted_id}))
    return result["booking_group_id"], captain_id


async def test_a_clash_on_a_later_car_assigns_no_car_at_all(rig, db, cleanup):
    """Used to: car 1 assigned, car 2 refused — a half-assigned visit."""
    from app.schemas.booking_schema import BookingAssignCaptainRequest

    group_id, captain_id = await _visit_with_a_clash_on_car_three(rig, db, cleanup)
    with pytest.raises(BadRequestException, match="already scheduled"):
        await BookingService(db).assign_captain_to_group(
            group_id, BookingAssignCaptainRequest(captain_id=captain_id), "manager-id", "manager", rig["center_id"],
        )
    rows = await db.bookings.find({"booking_group_id": group_id}).to_list(length=10)
    assert all(r["status"] == "pending" and not r.get("captain_id") for r in rows)
    assert await db.notifications.count_documents({"user_id": captain_id}) == 0


async def test_a_car_failing_mid_way_puts_the_earlier_cars_back(rig, db, cleanup, monkeypatch):
    """The belt-and-braces path: if a car still fails after the dry run (a
    race with another manager), the cars already moved are restored and
    nobody is told about a job that didn't happen."""
    from app.schemas.booking_schema import BookingAssignCaptainRequest

    group_id, captain_id = await _visit_with_a_clash_on_car_three(rig, db, cleanup)
    service = BookingService(db)

    async def _skip_dry_run(*args, **kwargs):
        return None

    monkeypatch.setattr(service, "_precheck_group_conflicts", _skip_dry_run)
    with pytest.raises(BadRequestException, match="already scheduled"):
        await service.assign_captain_to_group(
            group_id, BookingAssignCaptainRequest(captain_id=captain_id), "manager-id", "manager", rig["center_id"],
        )
    rows = await db.bookings.find({"booking_group_id": group_id}).to_list(length=10)
    assert all(r["status"] == "pending" and not r.get("captain_id") and not r.get("estimated_start_at") for r in rows), rows
    assert await db.notifications.count_documents({"user_id": captain_id}) == 0
    undone = await db.booking_status_history.count_documents({
        "booking_id": {"$in": [str(r["_id"]) for r in rows]}, "note": {"$regex": "^Assignment undone"},
    })
    assert undone >= 1


async def test_a_whole_visit_assignment_announces_once(rig, db, cleanup):
    from app.schemas.booking_schema import BookingAssignCaptainRequest
    from tests.factories import make_captain

    result = await BookingService(db).create_booking_group(
        rig["customer_id"],
        BookingGroupCreateRequest(
            vehicles=_cars(rig, 3), address_id=rig["address_id"],
            scheduled_date=rig["when"], scheduled_slot=rig["slot"], payment_method="cash",
        ),
    )
    captain_id = await make_captain(db, rig["center_id"])
    cleanup.append(("users", {"_id": ObjectId(captain_id)}))
    cleanup.append(("notifications", {"user_id": {"$in": [captain_id, rig["customer_id"]]}}))
    await BookingService(db).assign_captain_to_group(
        result["booking_group_id"], BookingAssignCaptainRequest(captain_id=captain_id), "manager-id", "manager", rig["center_id"],
    )
    assert await db.notifications.count_documents({"user_id": captain_id, "title": {"$regex": "^New job"}}) == 1
    assert await db.notifications.count_documents({"user_id": rig["customer_id"], "title": "Captain assigned"}) == 1


async def test_reassigning_one_car_moves_the_whole_visit_or_nothing(rig, db, cleanup):
    """Reassigning a car of an ASSIGNED visit used to move car 1 and then
    loop the siblings — a clash on a later car left the visit split
    between two captains."""
    from app.schemas.booking_schema import BookingAssignCaptainRequest, ReassignCaptainRequest
    from tests.factories import make_captain

    group_id, busy_captain = await _visit_with_a_clash_on_car_three(rig, db, cleanup)
    first_captain = await make_captain(db, rig["center_id"])
    cleanup.append(("users", {"_id": ObjectId(first_captain)}))
    service = BookingService(db)
    await service.assign_captain_to_group(
        group_id, BookingAssignCaptainRequest(captain_id=first_captain), "manager-id", "manager", rig["center_id"],
    )
    rows = sorted(await db.bookings.find({"booking_group_id": group_id}).to_list(length=10), key=lambda r: r.get("group_offset_minutes", 0))

    with pytest.raises(BadRequestException, match="already scheduled"):
        await service.reassign_captain(
            str(rows[0]["_id"]), ReassignCaptainRequest(captain_id=busy_captain), "manager-id", "manager", rig["center_id"],
        )
    after = await db.bookings.find({"booking_group_id": group_id}).to_list(length=10)
    assert all(r["captain_id"] == first_captain and r["status"] == "assigned" for r in after)

    # A free captain takes every car of the visit in one go.
    free_captain = await make_captain(db, rig["center_id"])
    cleanup.append(("users", {"_id": ObjectId(free_captain)}))
    cleanup.append(("notifications", {"user_id": {"$in": [first_captain, free_captain]}}))
    await service.reassign_captain(
        str(rows[1]["_id"]), ReassignCaptainRequest(captain_id=free_captain), "manager-id", "manager", rig["center_id"],
    )
    after = await db.bookings.find({"booking_group_id": group_id}).to_list(length=10)
    assert all(r["captain_id"] == free_captain for r in after)
    assert all(first_captain in (r.get("previous_captain_ids") or []) for r in after)
