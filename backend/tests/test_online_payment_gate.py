"""
Founder rule: choosing "pay online" makes the payment a PRE-condition of
the booking.

A self-service booking marked for online payment is created
AWAITING_PAYMENT — its slot is held, but it is in no queue, no captain can
be assigned, and neither the customer nor the manager is told about it.
Only a signature-verified payment (or the customer switching it to cash)
turns it into a real booking; an abandoned one is swept away and the slot
handed back.
"""
import itertools
from datetime import timedelta

import pytest
from bson import ObjectId

from app.core.exceptions import BadRequestException
from app.models.enums import BookingStatus, PaymentMethod
from app.schemas.booking_schema import BookingCancelRequest, BookingCreateRequest
from app.schemas.payment_schema import CreateOrderRequest, VerifyPaymentRequest
from app.services import payment_service
from app.services.booking_service import BookingService
from app.services.payment_service import PaymentService, _expected_signature
from app.utils.timezone import now_ist

from tests.factories import (
    get_hatchback_type_id,
    make_customer_with_vehicle,
    make_service_center,
)

pytestmark = pytest.mark.asyncio

_seq = itertools.count(1)


class _StubOrders:
    def create(self, payload):
        return {"id": f"order_gate_{next(_seq):06d}", **payload}


class _StubClient:
    order = _StubOrders()


@pytest.fixture
def gateway(monkeypatch):
    monkeypatch.setattr(payment_service.settings, "RAZORPAY_KEY_ID", "rzp_test_stub")
    monkeypatch.setattr(payment_service.settings, "RAZORPAY_KEY_SECRET", "stub_secret_key")
    monkeypatch.setattr(payment_service, "_razorpay_client", lambda: _StubClient())


@pytest.fixture
async def rig(db, cleanup):
    hatchback = await get_hatchback_type_id(db)
    center_id = await make_service_center(db)
    cleanup.append(("service_centers", {"_id": ObjectId(center_id)}))
    customer_id, vehicle_id, address_id = await make_customer_with_vehicle(db, hatchback)
    cleanup.append(("users", {"_id": ObjectId(customer_id)}))
    cleanup.append(("vehicles", {"owner_id": customer_id}))
    cleanup.append(("addresses", {"owner_id": customer_id}))
    cleanup.append(("bookings", {"customer_id": customer_id}))
    cleanup.append(("payment_orders", {"customer_id": customer_id}))
    cleanup.append(("notifications", {"user_id": customer_id}))
    service = await db.services.find_one({"is_addon": {"$ne": True}, "is_deleted": {"$ne": True}})
    return {
        "db": db, "customer_id": customer_id, "vehicle_id": vehicle_id,
        "address_id": address_id, "service_id": str(service["_id"]), "center_id": center_id,
    }


async def _book(svc, rig, method: str, *, day_offset=2, **kwargs):
    when = (now_ist().date() + timedelta(days=day_offset)).isoformat()
    slots = await svc.available_slots(rig["center_id"], when)
    # SAME-DAY runs legitimately find nothing left once the day's slots have
    # passed their booking cutoff — that's the product working, not a
    # failure. Skip rather than raising StopIteration out of a coroutine
    # (which surfaces as an unrelated-looking RuntimeError).
    slot = next((s["key"] for s in slots if s["status"] == "available"), None)
    if slot is None:
        pytest.skip(f"no bookable slot left for {when} at this time of day")
    return await svc.create_booking(
        rig["customer_id"],
        BookingCreateRequest(
            vehicle_id=rig["vehicle_id"], address_id=rig["address_id"], service_ids=[rig["service_id"]],
            scheduled_date=when, scheduled_slot=slot, payment_method=method,
        ),
        **kwargs,
    )


async def test_online_booking_is_parked_until_the_payment_verifies(rig, db, gateway):
    svc = BookingService(db)
    booking = await _book(svc, rig, "online")
    assert booking["status"] == BookingStatus.AWAITING_PAYMENT.value
    assert booking["total_amount"] > 0

    doc = await db.bookings.find_one({"_id": ObjectId(booking["id"])})
    # Not in the queue, no clocks running, nobody told.
    assert doc["awaiting_assignment_since"] is None
    assert doc["manager_notified_at"] is None
    assert await db.notifications.count_documents({"user_id": rig["customer_id"]}) == 0
    # ...and the manager's queue genuinely doesn't see it.
    assert booking["id"] not in {str(b["_id"]) for b in await svc.find_bookings_unassigned_too_long()}

    # A verified payment is what makes it real.
    payments = PaymentService(db)
    order = await payments.create_order(rig["customer_id"], CreateOrderRequest(purpose="booking", booking_id=booking["id"]))
    await payments.verify_payment(rig["customer_id"], VerifyPaymentRequest(
        razorpay_order_id=order["order_id"], razorpay_payment_id="pay_ok",
        razorpay_signature=_expected_signature(order["order_id"], "pay_ok")))

    doc = await db.bookings.find_one({"_id": ObjectId(booking["id"])})
    assert doc["status"] == BookingStatus.PENDING.value
    assert doc["payment_status"] == "paid"
    assert doc["awaiting_assignment_since"] is not None
    assert await db.notifications.count_documents({"user_id": rig["customer_id"]}) == 1


async def test_cash_and_staff_bookings_are_confirmed_immediately(rig, db):
    """The gate is for the self-service ONLINE choice only — cash confirms
    on the spot, and staff booking on someone's behalf can't complete a
    checkout modal for them."""
    svc = BookingService(db)
    cash = await _book(svc, rig, "cash")
    assert cash["status"] == BookingStatus.PENDING.value

    staff = await _book(svc, rig, "online", day_offset=3, source="staff", _skip_verification_gate=True)
    assert staff["status"] == BookingStatus.PENDING.value


async def test_unpaid_booking_can_be_switched_to_cash(rig, db):
    svc = BookingService(db)
    booking = await _book(svc, rig, "online")

    confirmed = await svc.switch_to_cash(booking["id"], rig["customer_id"])
    assert confirmed["status"] == BookingStatus.PENDING.value
    assert confirmed["payment_method"] == PaymentMethod.CASH.value
    assert confirmed["payment_status"] == "pending"  # collected at the door
    assert await db.notifications.count_documents({"user_id": rig["customer_id"]}) == 1

    # Not a lever anyone can pull twice, or on someone else's booking.
    with pytest.raises(BadRequestException, match="already confirmed"):
        await svc.switch_to_cash(booking["id"], rig["customer_id"])

    from app.core.exceptions import NotFoundException

    with pytest.raises(NotFoundException):
        await svc.switch_to_cash(booking["id"], str(ObjectId()))


async def test_payment_and_switch_to_cash_cannot_both_confirm(rig, db, gateway):
    """The customer taps "pay cash instead" at the same moment their online
    payment verifies. Exactly one confirmation must happen — never two
    "booking confirmed" messages, and never a cash method written over a
    booking that was actually paid online."""
    svc = BookingService(db)
    payments = PaymentService(db)
    booking = await _book(svc, rig, "online")
    order = await payments.create_order(rig["customer_id"], CreateOrderRequest(purpose="booking", booking_id=booking["id"]))

    await payments.verify_payment(rig["customer_id"], VerifyPaymentRequest(
        razorpay_order_id=order["order_id"], razorpay_payment_id="pay_race",
        razorpay_signature=_expected_signature(order["order_id"], "pay_race")))
    # Sequentially the paid-check catches it; a true interleave (status
    # flipped between our read and our guarded write) is caught by
    # confirm_awaiting_payment_booking returning None. Either way the
    # customer is told the payment landed, and nothing is rewritten.
    with pytest.raises(BadRequestException, match="already paid|already confirmed and paid"):
        await svc.switch_to_cash(booking["id"], rig["customer_id"])

    doc = await db.bookings.find_one({"_id": ObjectId(booking["id"])})
    assert doc["payment_method"] == PaymentMethod.ONLINE.value and doc["payment_status"] == "paid"
    assert await db.notifications.count_documents({"user_id": rig["customer_id"]}) == 1


async def test_customer_can_always_walk_away_from_an_unpaid_booking(rig, db):
    """The 4-hour cancellation lock protects a DISPATCHED job. A booking
    nobody was ever told about, for money never taken, isn't one — even
    inside the window."""
    svc = BookingService(db)
    booking = await _book(svc, rig, "online", day_offset=0)
    if booking["status"] != BookingStatus.AWAITING_PAYMENT.value:
        pytest.skip("same-day booking wasn't parked — nothing to exercise here")
    cancelled = await svc.cancel_booking(
        booking["id"], BookingCancelRequest(reason="changed my mind"), rig["customer_id"], "customer"
    )
    assert cancelled["status"] == BookingStatus.CANCELLED.value


async def test_abandoned_bookings_expire_and_hand_the_slot_back(rig, db):
    svc = BookingService(db)
    booking = await _book(svc, rig, "online")

    # Nothing to expire while the window is still open.
    assert booking["id"] not in {str(b["_id"]) for b in await svc.find_bookings_payment_expired()}

    await db.bookings.update_one(
        {"_id": ObjectId(booking["id"])}, {"$set": {"created_at": now_ist() - timedelta(hours=2)}}
    )
    due = await svc.find_bookings_payment_expired()
    assert booking["id"] in {str(b["_id"]) for b in due}

    await svc.cancel_booking(
        booking["id"], BookingCancelRequest(reason="Payment wasn't completed in time, so the slot was released."),
        "system", "admin",
    )
    doc = await db.bookings.find_one({"_id": ObjectId(booking["id"])})
    assert doc["status"] == BookingStatus.CANCELLED.value
    # Slot handed back: the same vehicle can book that slot again.
    again = await svc.create_booking(
        rig["customer_id"],
        BookingCreateRequest(
            vehicle_id=rig["vehicle_id"], address_id=rig["address_id"], service_ids=[rig["service_id"]],
            scheduled_date=doc["scheduled_date"].strftime("%Y-%m-%d"), scheduled_slot=doc["scheduled_slot"],
            payment_method="cash",
        ),
    )
    assert again["status"] == BookingStatus.PENDING.value


async def test_rebooking_the_same_slot_points_at_the_unpaid_one(rig, db):
    """An unpaid booking holds its slot, so a retry is refused — but the
    message has to explain WHY, or the customer sees a booking they don't
    think they made."""
    svc = BookingService(db)
    booking = await _book(svc, rig, "online")
    doc = await db.bookings.find_one({"_id": ObjectId(booking["id"])})
    with pytest.raises(BadRequestException, match="waiting for your online payment"):
        await svc.create_booking(
            rig["customer_id"],
            BookingCreateRequest(
                vehicle_id=rig["vehicle_id"], address_id=rig["address_id"], service_ids=[rig["service_id"]],
                scheduled_date=doc["scheduled_date"].strftime("%Y-%m-%d"), scheduled_slot=doc["scheduled_slot"],
                payment_method="cash",
            ),
        )


async def test_no_captain_can_be_assigned_to_an_unpaid_booking(rig, db, cleanup):
    from app.schemas.booking_schema import BookingAssignCaptainRequest
    from tests.factories import make_captain

    svc = BookingService(db)
    booking = await _book(svc, rig, "online")
    captain_id = await make_captain(db, rig["center_id"])
    cleanup.append(("users", {"_id": ObjectId(captain_id)}))

    with pytest.raises(BadRequestException):
        await svc.assign_captain(
            booking["id"], BookingAssignCaptainRequest(captain_id=captain_id),
            "manager-id", "manager", rig["center_id"],
        )
