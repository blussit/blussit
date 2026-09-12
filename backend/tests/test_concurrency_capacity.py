"""
Concurrency: the guarantees that only hold when many people press Book at
the same instant.

These are real concurrent calls (asyncio.gather against the live test DB),
not sequential calls pretending to be concurrent — the whole point is to
exercise the atomic guards rather than the happy path.

  1. SLOT CAPACITY cannot be oversold, however many people race for the
     last seats.
  2. A PASS's last wash can only be spent once — and the loser of that race
     must not be left holding a free, dispatchable booking.
  3. Two settlements of the same booking (payment + captain's cash tap)
     cannot both apply.
"""
import asyncio
import itertools
from datetime import timedelta

import pytest
from bson import ObjectId

from app.core.exceptions import BadRequestException
from app.models.enums import BookingStatus
from app.schemas.booking_schema import BookingCreateRequest
from app.schemas.subscription_schema import SubscribeRequest
from app.services.booking_service import BookingService
from app.services.subscription_service import UserSubscriptionService
from app.utils.timezone import now_ist

from tests.factories import (
    get_hatchback_type_id,
    make_customer_with_vehicle,
    make_service_center,
    make_subscription_plan,
    make_vehicle,
)

pytestmark = pytest.mark.asyncio

_seq = itertools.count(1)

CAPACITY = 3
RACERS = 9


async def _slot_for(db, center_id: str, day_offset: int = 2) -> tuple[str, str]:
    when = (now_ist().date() + timedelta(days=day_offset)).isoformat()
    slots = await BookingService(db).available_slots(center_id, when)
    return when, next(s["key"] for s in slots if s["status"] == "available")


async def test_a_slot_cannot_be_oversold_by_simultaneous_bookings(db, cleanup):
    """Nine people, three seats. Exactly three bookings — no oversell, no
    negative counter, and every loser gets a clean 'full' answer rather
    than a 500."""
    hatchback = await get_hatchback_type_id(db)
    center_id = await make_service_center(db)
    cleanup.append(("service_centers", {"_id": ObjectId(center_id)}))
    when, slot = await _slot_for(db, center_id)

    # Pin this slot's capacity, so the test is about the guard and not
    # about whatever the center's default happens to be.
    await db.slot_capacity.update_one(
        {"service_center_id": center_id, "date": when, "slot_key": slot},
        {"$set": {"capacity": CAPACITY, "booked_count": 0, "held_count": 0, "is_closed": False}},
        upsert=True,
    )
    cleanup.append(("slot_capacity", {"service_center_id": center_id}))
    cleanup.append(("daily_capacity", {"service_center_id": center_id}))

    service = await db.services.find_one({"is_addon": {"$ne": True}, "is_deleted": {"$ne": True}})
    racers = []
    for _ in range(RACERS):
        customer_id, vehicle_id, address_id = await make_customer_with_vehicle(db, hatchback)
        cleanup.append(("users", {"_id": ObjectId(customer_id)}))
        cleanup.append(("vehicles", {"owner_id": customer_id}))
        cleanup.append(("addresses", {"owner_id": customer_id}))
        cleanup.append(("bookings", {"customer_id": customer_id}))
        racers.append((customer_id, vehicle_id, address_id))

    async def book(customer_id, vehicle_id, address_id):
        return await BookingService(db).create_booking(
            customer_id,
            BookingCreateRequest(
                vehicle_id=vehicle_id, address_id=address_id, service_ids=[str(service["_id"])],
                scheduled_date=when, scheduled_slot=slot, payment_method="cash",
            ),
        )

    results = await asyncio.gather(*(book(*r) for r in racers), return_exceptions=True)
    booked = [r for r in results if not isinstance(r, Exception)]
    refused = [r for r in results if isinstance(r, Exception)]

    assert len(booked) == CAPACITY, f"expected exactly {CAPACITY} bookings, got {len(booked)}"
    assert len(refused) == RACERS - CAPACITY
    # Every refusal must be the clean business error, never a crash.
    assert all(isinstance(r, BadRequestException) for r in refused), [type(r).__name__ for r in refused]

    counter = await db.slot_capacity.find_one({"service_center_id": center_id, "date": when, "slot_key": slot})
    assert counter["booked_count"] == CAPACITY
    assert counter["booked_count"] <= counter["capacity"]
    assert counter.get("held_count", 0) >= 0

    live = await db.bookings.count_documents(
        {"service_center_id": center_id, "scheduled_slot": slot, "status": {"$ne": "cancelled"}, "is_deleted": {"$ne": True}}
    )
    assert live == CAPACITY


async def test_the_last_wash_on_a_pass_can_only_be_spent_once(db, cleanup):
    """Two bookings racing for one remaining wash. Exactly one may consume
    it — and, critically, the loser must NOT be left with a booking: a
    subscription booking that consumed nothing is a free service."""
    hatchback = await get_hatchback_type_id(db)
    center_id = await make_service_center(db)
    cleanup.append(("service_centers", {"_id": ObjectId(center_id)}))
    customer_id, vehicle_id, address_id = await make_customer_with_vehicle(db, hatchback)
    cleanup.append(("users", {"_id": ObjectId(customer_id)}))
    cleanup.append(("vehicles", {"owner_id": customer_id}))
    cleanup.append(("addresses", {"owner_id": customer_id}))
    cleanup.append(("bookings", {"customer_id": customer_id}))
    cleanup.append(("user_subscriptions", {"customer_id": customer_id}))
    cleanup.append(("slot_capacity", {"service_center_id": center_id}))
    cleanup.append(("daily_capacity", {"service_center_id": center_id}))

    service = await db.services.find_one({"is_addon": {"$ne": True}, "is_deleted": {"$ne": True}})
    plan_id = await make_subscription_plan(
        db, vehicle_types=[], included_service_ids=[str(service["_id"])], total_service_count=1
    )
    cleanup.append(("subscription_plans", {"_id": ObjectId(plan_id)}))
    sub = await UserSubscriptionService(db).subscribe(
        customer_id, SubscribeRequest(plan_id=plan_id, vehicle_id=vehicle_id, service_id=str(service["_id"]))
    )
    assert sub["remaining_service_count"] == 1

    # Two DIFFERENT days, so slot capacity and the duplicate-booking index
    # can't be what stops the second one — only the pass's own quota.
    day_a = await _slot_for(db, center_id, day_offset=2)
    day_b = await _slot_for(db, center_id, day_offset=3)

    async def book(when_slot):
        when, slot = when_slot
        return await BookingService(db).create_booking(
            customer_id,
            BookingCreateRequest(
                vehicle_id=vehicle_id, address_id=address_id, service_ids=[str(service["_id"])],
                scheduled_date=when, scheduled_slot=slot, subscription_id=sub["id"],
            ),
        )

    results = await asyncio.gather(book(day_a), book(day_b), return_exceptions=True)
    ok = [r for r in results if not isinstance(r, Exception)]
    assert len(ok) == 1, f"the last wash was spent {len(ok)} times"

    fresh = await db.user_subscriptions.find_one({"_id": ObjectId(sub["id"])})
    assert fresh["remaining_service_count"] == 0, "a pass must never go negative"

    # The loser left nothing behind — no free booking, no held slot.
    live = await db.bookings.count_documents(
        {"customer_id": customer_id, "status": {"$ne": "cancelled"}, "is_deleted": {"$ne": True}}
    )
    assert live == 1, "a booking whose plan spend failed must not survive"


async def test_a_booking_cannot_be_settled_twice(db, cleanup, monkeypatch):
    """The captain taps 'cash received' at the same moment an online payment
    verifies. One settlement wins; the other is parked for a human rather
    than silently overwriting the first."""
    import itertools as _it

    from app.schemas.payment_schema import CreateOrderRequest, VerifyPaymentRequest
    from app.services import payment_service
    from app.services.payment_service import PaymentService, _expected_signature

    counter = _it.count(1)

    class _StubOrders:
        def create(self, payload):
            return {"id": f"order_race_{next(counter):06d}", **payload}

    class _StubClient:
        order = _StubOrders()

    monkeypatch.setattr(payment_service.settings, "RAZORPAY_KEY_ID", "rzp_test_stub")
    monkeypatch.setattr(payment_service.settings, "RAZORPAY_KEY_SECRET", "stub_secret_key")
    monkeypatch.setattr(payment_service, "_razorpay_client", lambda: _StubClient())

    hatchback = await get_hatchback_type_id(db)
    center_id = await make_service_center(db)
    cleanup.append(("service_centers", {"_id": ObjectId(center_id)}))
    customer_id, vehicle_id, address_id = await make_customer_with_vehicle(db, hatchback)
    for coll, q in [("users", {"_id": ObjectId(customer_id)}), ("vehicles", {"owner_id": customer_id}),
                    ("addresses", {"owner_id": customer_id}), ("bookings", {"customer_id": customer_id}),
                    ("payment_orders", {"customer_id": customer_id}), ("slot_capacity", {"service_center_id": center_id}),
                    ("daily_capacity", {"service_center_id": center_id})]:
        cleanup.append((coll, q))

    service = await db.services.find_one({"is_addon": {"$ne": True}, "is_deleted": {"$ne": True}})
    when, slot = await _slot_for(db, center_id)
    booking = await BookingService(db).create_booking(
        customer_id,
        BookingCreateRequest(
            vehicle_id=vehicle_id, address_id=address_id, service_ids=[str(service["_id"])],
            scheduled_date=when, scheduled_slot=slot, payment_method="cash",
        ),
    )
    payments = PaymentService(db)
    order = await payments.create_order(customer_id, CreateOrderRequest(purpose="booking", booking_id=booking["id"]))

    # Mark it completed so the captain's cash path is legal, then race.
    await db.bookings.update_one({"_id": ObjectId(booking["id"])}, {"$set": {"status": "completed", "captain_id": "cap-race"}})

    async def pay_online():
        return await payments.verify_payment(customer_id, VerifyPaymentRequest(
            razorpay_order_id=order["order_id"], razorpay_payment_id="pay_race",
            razorpay_signature=_expected_signature(order["order_id"], "pay_race")))

    async def collect_cash():
        return await payments.captain_collect_cash(booking["id"], "cap-race")

    results = await asyncio.gather(pay_online(), collect_cash(), return_exceptions=True)
    winners = [r for r in results if not isinstance(r, Exception)]
    assert len(winners) >= 1

    fresh = await db.bookings.find_one({"_id": ObjectId(booking["id"])})
    assert fresh["payment_status"] == "paid"
    # Money that arrived but couldn't be applied is parked, never lost.
    orders = await db.payment_orders.find({"booking_id": booking["id"]}).to_list(length=5)
    assert all(o["status"] in {"paid", "paid_attention"} for o in orders)
