"""
The /thank-you page's confirmation ticket — must be unguessable, work
right after a real purchase, and stop working once expired. Also verifies
the controllers actually attach one to a real booking/subscription
response (not just that the service method works in isolation).
"""
from datetime import datetime, timedelta, timezone

import pytest
from bson import ObjectId

from app.controllers.booking_controller import BookingController
from app.controllers.subscription_controller import UserSubscriptionController
from app.core.dependencies import CurrentUser
from app.schemas.booking_schema import BookingCreateRequest
from app.schemas.subscription_schema import SubscribeRequest
from app.services.purchase_confirmation_service import PurchaseConfirmationService

from tests.factories import (
    get_star_wash_service_id,
    get_hatchback_type_id,
    make_customer_with_vehicle,
    make_service_center,
    make_subscription_plan,
)


def _tomorrow():
    return datetime.now(timezone.utc) + timedelta(days=1)


@pytest.mark.asyncio
async def test_issue_and_redeem_round_trip(db):
    svc = PurchaseConfirmationService(db)
    token = await svc.issue("booking", "fake-booking-id", "fake-customer-id", {"booking_number": "BK123"})
    await db.purchase_confirmations.delete_many({"token": token})  # ensure isolation regardless of test order
    token = await svc.issue("booking", "fake-booking-id", "fake-customer-id", {"booking_number": "BK123"})
    try:
        result = await svc.redeem(token)
        assert result is not None
        assert result["type"] == "booking"
        assert result["reference_id"] == "fake-booking-id"
        assert result["payload"]["booking_number"] == "BK123"
    finally:
        await db.purchase_confirmations.delete_many({"token": token})


@pytest.mark.asyncio
async def test_redeem_returns_none_for_unknown_token(db):
    svc = PurchaseConfirmationService(db)
    assert await svc.redeem("this-token-was-never-issued") is None


@pytest.mark.asyncio
async def test_redeem_returns_none_once_expired(db):
    svc = PurchaseConfirmationService(db)
    token = await svc.issue("subscription", "fake-sub-id", None, {"plan_name": "Gold"})
    await db.purchase_confirmations.update_one({"token": token}, {"$set": {"expires_at": datetime.now(timezone.utc) - timedelta(minutes=1)}})
    try:
        assert await svc.redeem(token) is None
    finally:
        await db.purchase_confirmations.delete_many({"token": token})


@pytest.mark.asyncio
async def test_a_real_booking_gets_a_redeemable_confirmation_token(db, cleanup):
    hatchback = await get_hatchback_type_id(db)
    foam = await get_star_wash_service_id(db)
    center_id = await make_service_center(db, working_hours_start="09:00", working_hours_end="21:00", slot_duration_minutes=180, default_slot_capacity=10)
    cleanup.append(("service_centers", {"_id": ObjectId(center_id)}))
    customer_id, vehicle_id, address_id = await make_customer_with_vehicle(db, hatchback)
    cleanup.append(("users", {"_id": ObjectId(customer_id)}))
    cleanup.append(("vehicles", {"owner_id": customer_id}))
    cleanup.append(("addresses", {"owner_id": customer_id}))
    cleanup.append(("bookings", {"customer_id": customer_id}))

    controller = BookingController(db)
    current_user = CurrentUser(id=customer_id, role="customer")
    response = await controller.create(
        current_user, BookingCreateRequest(vehicle_id=vehicle_id, address_id=address_id, service_ids=[foam], scheduled_date=_tomorrow(), scheduled_slot="09:00-12:00")
    )
    token = response["data"]["confirmation_token"]
    assert token
    cleanup.append(("purchase_confirmations", {"token": token}))

    confirmation = await PurchaseConfirmationService(db).redeem(token)
    assert confirmation is not None
    assert confirmation["type"] == "booking"
    assert confirmation["reference_id"] == response["data"]["id"]
    assert confirmation["payload"]["booking_number"] == response["data"]["booking_number"]


@pytest.mark.asyncio
async def test_a_real_subscription_gets_a_redeemable_confirmation_token(db, cleanup):
    hatchback = await get_hatchback_type_id(db)
    customer_id = (await make_customer_with_vehicle(db, hatchback))[0]
    cleanup.append(("users", {"_id": ObjectId(customer_id)}))
    plan_id = await make_subscription_plan(db, vehicle_types=[hatchback])
    cleanup.append(("subscription_plans", {"_id": ObjectId(plan_id)}))
    cleanup.append(("user_subscriptions", {"customer_id": customer_id}))

    controller = UserSubscriptionController(db)
    current_user = CurrentUser(id=customer_id, role="customer")
    response = await controller.subscribe(current_user, SubscribeRequest(plan_id=plan_id))
    token = response["data"]["confirmation_token"]
    assert token
    cleanup.append(("purchase_confirmations", {"token": token}))

    confirmation = await PurchaseConfirmationService(db).redeem(token)
    assert confirmation is not None
    assert confirmation["type"] == "subscription"
    assert confirmation["payload"]["plan_name"]
