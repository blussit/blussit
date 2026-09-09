"""
Spec Section 17: subscription consumption is only deducted after a
CONFIRMED booking state, never for failed/cancelled bookings, and the
concurrent last-unit-remaining race must never take the counter negative.
"""
import asyncio

import pytest
from bson import ObjectId

from app.core.exceptions import BadRequestException
from app.schemas.subscription_schema import SubscribeRequest
from app.services.subscription_service import UserSubscriptionService

from tests.factories import get_hatchback_type_id, make_customer, make_subscription_plan


@pytest.fixture
async def rig(db, cleanup):
    hatchback = await get_hatchback_type_id(db)
    customer_id = await make_customer(db)
    cleanup.append(("users", {"_id": ObjectId(customer_id)}))
    cleanup.append(("user_subscriptions", {"customer_id": customer_id}))
    plan_id = await make_subscription_plan(db, vehicle_types=[], total_service_count=1)
    cleanup.append(("subscription_plans", {"_id": ObjectId(plan_id)}))
    return {"db": db, "hatchback": hatchback, "customer_id": customer_id, "plan_id": plan_id}


@pytest.mark.asyncio
async def test_commit_consumption_decrements_remaining_count(rig):
    service = UserSubscriptionService(rig["db"])
    sub = await service.subscribe(rig["customer_id"], SubscribeRequest(plan_id=rig["plan_id"]))
    assert sub["remaining_service_count"] == 1

    consumption = await service.plan_consumption(sub["id"], "irrelevant-vehicle-id", [{"id": "svc", "category_id": None}], rig["customer_id"])
    await service.commit_consumption(sub["id"], consumption)

    updated = await rig["db"].user_subscriptions.find_one({"_id": ObjectId(sub["id"])})
    assert updated["remaining_service_count"] == 0
    assert updated["status"] == "expired"  # 0 remaining -> auto-expired


@pytest.mark.asyncio
async def test_restore_consumption_reverses_a_cancelled_booking(rig):
    """Section 17: consumption must be restored if the booking it paid for
    is later cancelled — the customer shouldn't lose the credit for a
    service that was never performed."""
    service = UserSubscriptionService(rig["db"])
    sub = await service.subscribe(rig["customer_id"], SubscribeRequest(plan_id=rig["plan_id"]))
    consumption = await service.plan_consumption(sub["id"], "irrelevant-vehicle-id", [{"id": "svc", "category_id": None}], rig["customer_id"])
    await service.commit_consumption(sub["id"], consumption)

    await service.restore_consumption(sub["id"], consumption)
    restored = await rig["db"].user_subscriptions.find_one({"_id": ObjectId(sub["id"])})
    assert restored["remaining_service_count"] == 1


@pytest.mark.asyncio
async def test_concurrent_last_unit_consumption_never_goes_negative(rig, db, cleanup):
    """Two bookings racing to spend the LAST remaining credit — only one
    may win; the counter must never go negative."""
    hatchback = rig["hatchback"]
    plan_id = await make_subscription_plan(db, vehicle_types=[], total_service_count=1)
    cleanup.append(("subscription_plans", {"_id": ObjectId(plan_id)}))

    service = UserSubscriptionService(db)
    sub = await service.subscribe(rig["customer_id"], SubscribeRequest(plan_id=plan_id))

    async def try_consume():
        try:
            consumption = await UserSubscriptionService(db).plan_consumption(sub["id"], "v", [{"id": "svc", "category_id": None}], rig["customer_id"])
            await UserSubscriptionService(db).commit_consumption(sub["id"], consumption)
            return "ok"
        except BadRequestException:
            return "rejected"

    results = await asyncio.gather(*[try_consume() for _ in range(6)])
    assert results.count("ok") == 1
    assert results.count("rejected") == 5

    final = await db.user_subscriptions.find_one({"_id": ObjectId(sub["id"])})
    assert final["remaining_service_count"] == 0
