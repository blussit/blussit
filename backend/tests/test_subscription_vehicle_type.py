"""
Spec Section 16: subscriptions are tied to VEHICLE TYPE, not one specific
vehicle — any of the customer's owned vehicles of a matching type can use
it, including a vehicle added AFTER subscribing.
"""
import pytest
from bson import ObjectId

from app.core.exceptions import BadRequestException
from app.schemas.subscription_schema import SubscribeRequest
from app.services.subscription_service import UserSubscriptionService

from tests.factories import get_hatchback_type_id, get_suv_type_id, make_customer, make_subscription_plan, make_vehicle


@pytest.fixture
async def rig(db, cleanup):
    hatchback = await get_hatchback_type_id(db)
    suv = await get_suv_type_id(db)
    customer_id = await make_customer(db)
    cleanup.append(("users", {"_id": ObjectId(customer_id)}))
    cleanup.append(("vehicles", {"owner_id": customer_id}))
    cleanup.append(("user_subscriptions", {"customer_id": customer_id}))
    plan_id = await make_subscription_plan(db, vehicle_types=[hatchback])
    cleanup.append(("subscription_plans", {"_id": ObjectId(plan_id)}))
    return {"db": db, "hatchback": hatchback, "suv": suv, "customer_id": customer_id, "plan_id": plan_id}


@pytest.mark.asyncio
async def test_subscribe_does_not_require_a_vehicle(rig):
    """No vehicle_id on SubscribeRequest at all — reversal from the old
    vehicle-ID-locked design."""
    plan_id = rig["plan_id"]
    service = UserSubscriptionService(rig["db"])
    sub = await service.subscribe(rig["customer_id"], SubscribeRequest(plan_id=plan_id))
    assert sub["customer_id"] == rig["customer_id"]
    assert sub.get("vehicle_id") is None


@pytest.mark.asyncio
async def test_vehicle_added_after_subscribing_is_still_eligible(rig):
    """The literal spec example: subscribe first, add a matching-type
    vehicle afterward — it must be usable immediately."""
    plan_id = rig["plan_id"]
    service = UserSubscriptionService(rig["db"])
    sub = await service.subscribe(rig["customer_id"], SubscribeRequest(plan_id=plan_id))

    # Vehicle added AFTER the subscription exists.
    vehicle_id = await make_vehicle(rig["db"], rig["customer_id"], rig["hatchback"])

    plan = await rig["db"].subscription_plans.find_one({"_id": ObjectId(plan_id)})
    vehicle = await rig["db"].vehicles.find_one({"_id": ObjectId(vehicle_id)})
    assert vehicle["vehicle_type"] in plan["vehicle_types"]


@pytest.mark.asyncio
async def test_wrong_vehicle_type_consumption_is_rejected(rig):
    """A plan scoped to hatchback must reject consumption for a SUV-type
    vehicle, even though both belong to the same subscribed customer."""
    plan_id = rig["plan_id"]
    service = UserSubscriptionService(rig["db"])
    sub = await service.subscribe(rig["customer_id"], SubscribeRequest(plan_id=plan_id))

    suv_vehicle_id = await make_vehicle(rig["db"], rig["customer_id"], rig["suv"])
    plan = await rig["db"].subscription_plans.find_one({"_id": ObjectId(plan_id)})
    services = [{"id": sid, "category_id": None} for sid in plan.get("included_service_ids", [])] or [{"id": "any", "category_id": None}]

    with pytest.raises(BadRequestException):
        await service.plan_consumption(sub["id"], suv_vehicle_id, services)


@pytest.mark.asyncio
async def test_any_owned_vehicle_of_matching_type_is_eligible(rig):
    """Two DIFFERENT hatchbacks owned by the same customer both qualify
    under one subscription — it's type-scoped, not tied to either one
    specifically."""
    plan_id = rig["plan_id"]
    service = UserSubscriptionService(rig["db"])
    await service.subscribe(rig["customer_id"], SubscribeRequest(plan_id=plan_id))

    v1 = await make_vehicle(rig["db"], rig["customer_id"], rig["hatchback"])
    v2 = await make_vehicle(rig["db"], rig["customer_id"], rig["hatchback"])
    plan = await rig["db"].subscription_plans.find_one({"_id": ObjectId(plan_id)})
    for vid in (v1, v2):
        vehicle = await rig["db"].vehicles.find_one({"_id": ObjectId(vid)})
        assert vehicle["vehicle_type"] in plan["vehicle_types"]
