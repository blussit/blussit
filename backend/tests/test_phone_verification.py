"""
Phone-OTP verification gate — a customer's FIRST self-service booking or
subscription purchase is blocked until they complete an OTP (sent over
WhatsApp); once verified, every future purchase skips it. A manager/admin
booking or assigning on the customer's behalf is never gated by this.
"""
from datetime import datetime, timedelta, timezone

import pytest
from bson import ObjectId

from app.core.exceptions import BadRequestException, PhoneNotVerifiedException
from app.schemas.booking_schema import BookingCreateRequest, ManagerBookingCreateRequest
from app.schemas.subscription_schema import AssignSubscriptionRequest, SubscribeRequest
from app.services.auth_service import AuthService
from app.services.booking_service import BookingService
from app.services.subscription_service import SubscriptionPlanService, UserSubscriptionService

from tests.factories import (
    get_star_wash_service_id,
    get_hatchback_type_id,
    make_customer_with_vehicle,
    make_manager,
    make_service_center,
    make_subscription_plan,
)


@pytest.fixture
async def rig(db, cleanup):
    hatchback = await get_hatchback_type_id(db)
    foam = await get_star_wash_service_id(db)
    center_id = await make_service_center(db, working_hours_start="09:00", working_hours_end="21:00", slot_duration_minutes=180, default_slot_capacity=10)
    cleanup.append(("service_centers", {"_id": ObjectId(center_id)}))
    manager_id = await make_manager(db, center_id)
    cleanup.append(("users", {"_id": ObjectId(manager_id)}))
    return {"db": db, "center_id": center_id, "hatchback": hatchback, "foam": foam, "manager_id": manager_id}


def _tomorrow():
    return datetime.now(timezone.utc) + timedelta(days=1)


@pytest.mark.asyncio
async def test_unverified_customer_is_blocked_from_self_service_booking(rig, cleanup):
    customer_id, vehicle_id, address_id = await make_customer_with_vehicle(rig["db"], rig["hatchback"], phone_verified=False)
    cleanup.append(("users", {"_id": ObjectId(customer_id)}))
    cleanup.append(("vehicles", {"owner_id": customer_id}))
    cleanup.append(("addresses", {"owner_id": customer_id}))

    bs = BookingService(rig["db"])
    with pytest.raises(PhoneNotVerifiedException):
        await bs.create_booking(
            customer_id, BookingCreateRequest(vehicle_id=vehicle_id, address_id=address_id, service_ids=[rig["foam"]], scheduled_date=_tomorrow(), scheduled_slot="09:00-12:00")
        )


@pytest.mark.asyncio
async def test_verified_customer_can_book_normally(rig, cleanup):
    customer_id, vehicle_id, address_id = await make_customer_with_vehicle(rig["db"], rig["hatchback"], phone_verified=True)
    cleanup.append(("users", {"_id": ObjectId(customer_id)}))
    cleanup.append(("vehicles", {"owner_id": customer_id}))
    cleanup.append(("addresses", {"owner_id": customer_id}))
    cleanup.append(("bookings", {"customer_id": customer_id}))

    bs = BookingService(rig["db"])
    booking = await bs.create_booking(
        customer_id, BookingCreateRequest(vehicle_id=vehicle_id, address_id=address_id, service_ids=[rig["foam"]], scheduled_date=_tomorrow(), scheduled_slot="09:00-12:00")
    )
    assert booking["status"] == "pending"


@pytest.mark.asyncio
async def test_manager_booking_on_behalf_bypasses_the_gate(rig, cleanup):
    """A manager creating a booking for an UNVERIFIED customer must still
    succeed — the gate only applies to the customer's own self-service
    action, not a staff-initiated one."""
    customer_id, vehicle_id, address_id = await make_customer_with_vehicle(rig["db"], rig["hatchback"], phone_verified=False)
    cleanup.append(("users", {"_id": ObjectId(customer_id)}))
    cleanup.append(("vehicles", {"owner_id": customer_id}))
    cleanup.append(("addresses", {"owner_id": customer_id}))
    cleanup.append(("bookings", {"customer_id": customer_id}))

    bs = BookingService(rig["db"])
    booking = await bs.create_booking_for_customer(
        rig["manager_id"],
        ManagerBookingCreateRequest(
            customer_id=customer_id, vehicle_id=vehicle_id, address_id=address_id, service_ids=[rig["foam"]], scheduled_date=_tomorrow(), scheduled_slot="09:00-12:00"
        ),
    )
    assert booking["status"] == "pending"


@pytest.mark.asyncio
async def test_unverified_customer_is_blocked_from_subscribing(rig, db, cleanup):
    customer_id = await make_customer_with_vehicle(db, rig["hatchback"], phone_verified=False)
    customer_id = customer_id[0]
    cleanup.append(("users", {"_id": ObjectId(customer_id)}))

    plan_id = await make_subscription_plan(db, vehicle_types=[rig["hatchback"]])
    cleanup.append(("subscription_plans", {"_id": ObjectId(plan_id)}))

    svc = UserSubscriptionService(db)
    with pytest.raises(PhoneNotVerifiedException):
        await svc.subscribe(customer_id, SubscribeRequest(plan_id=plan_id))


@pytest.mark.asyncio
async def test_verified_customer_can_subscribe_normally(rig, db, cleanup):
    customer_id = (await make_customer_with_vehicle(db, rig["hatchback"], phone_verified=True))[0]
    cleanup.append(("users", {"_id": ObjectId(customer_id)}))
    plan_id = await make_subscription_plan(db, vehicle_types=[rig["hatchback"]])
    cleanup.append(("subscription_plans", {"_id": ObjectId(plan_id)}))
    cleanup.append(("user_subscriptions", {"customer_id": customer_id}))

    svc = UserSubscriptionService(db)
    sub = await svc.subscribe(customer_id, SubscribeRequest(plan_id=plan_id))
    assert sub["status"] == "active"
    assert sub["plan_name"]


@pytest.mark.asyncio
async def test_manager_assign_subscription_bypasses_the_gate(rig, db, cleanup):
    customer_id = (await make_customer_with_vehicle(db, rig["hatchback"], phone_verified=False))[0]
    cleanup.append(("users", {"_id": ObjectId(customer_id)}))
    plan_id = await make_subscription_plan(db, vehicle_types=[rig["hatchback"]])
    cleanup.append(("subscription_plans", {"_id": ObjectId(plan_id)}))
    cleanup.append(("user_subscriptions", {"customer_id": customer_id}))

    svc = UserSubscriptionService(db)
    sub = await svc.assign(AssignSubscriptionRequest(customer_id=customer_id, plan_id=plan_id))
    assert sub["status"] == "active"


@pytest.mark.asyncio
async def test_otp_request_and_confirm_marks_phone_verified(rig, db, cleanup):
    customer_id = (await make_customer_with_vehicle(db, rig["hatchback"], phone_verified=False))[0]
    cleanup.append(("users", {"_id": ObjectId(customer_id)}))
    customer = await db.users.find_one({"_id": ObjectId(customer_id)})
    cleanup.append(("otp_requests", {"identifier": customer["phone"]}))
    cleanup.append(("whatsapp_outbox", {"phone": customer["phone"]}))

    auth = AuthService(db)
    await auth.request_phone_verification(customer_id)

    otp_doc = await db.otp_requests.find_one({"identifier": customer["phone"]})
    assert otp_doc is not None
    real_otp = otp_doc["otp"]

    with pytest.raises(BadRequestException):
        await auth.confirm_phone_verification(customer_id, "000000")  # wrong code

    updated = await auth.confirm_phone_verification(customer_id, real_otp)
    assert updated["phone_verified"] is True

    # Now a booking should succeed without needing to bypass the gate.
    bs = BookingService(db)
    vehicle_id = (await db.vehicles.find_one({"owner_id": customer_id}))
    address_id = (await db.addresses.find_one({"owner_id": customer_id}))
    booking = await bs.create_booking(
        customer_id,
        BookingCreateRequest(vehicle_id=str(vehicle_id["_id"]), address_id=str(address_id["_id"]), service_ids=[rig["foam"]], scheduled_date=_tomorrow(), scheduled_slot="09:00-12:00"),
    )
    cleanup.append(("bookings", {"customer_id": customer_id}))
    assert booking["status"] == "pending"


@pytest.mark.asyncio
async def test_otp_expires_and_is_rejected(rig, db, cleanup):
    customer_id = (await make_customer_with_vehicle(db, rig["hatchback"], phone_verified=False))[0]
    cleanup.append(("users", {"_id": ObjectId(customer_id)}))
    customer = await db.users.find_one({"_id": ObjectId(customer_id)})
    cleanup.append(("otp_requests", {"identifier": customer["phone"]}))
    cleanup.append(("whatsapp_outbox", {"phone": customer["phone"]}))

    auth = AuthService(db)
    await auth.request_phone_verification(customer_id)
    otp_doc = await db.otp_requests.find_one({"identifier": customer["phone"]})

    # Force it into the past.
    await db.otp_requests.update_one({"identifier": customer["phone"]}, {"$set": {"expires_at": datetime.now(timezone.utc) - timedelta(minutes=1)}})

    with pytest.raises(BadRequestException):
        await auth.confirm_phone_verification(customer_id, otp_doc["otp"])


@pytest.mark.asyncio
async def test_otp_resend_is_rate_limited(rig, db, cleanup):
    customer_id = (await make_customer_with_vehicle(db, rig["hatchback"], phone_verified=False))[0]
    cleanup.append(("users", {"_id": ObjectId(customer_id)}))
    customer = await db.users.find_one({"_id": ObjectId(customer_id)})
    cleanup.append(("otp_requests", {"identifier": customer["phone"]}))
    cleanup.append(("whatsapp_outbox", {"phone": customer["phone"]}))

    auth = AuthService(db)
    await auth.request_phone_verification(customer_id)
    with pytest.raises(BadRequestException, match="wait"):
        await auth.request_phone_verification(customer_id)
