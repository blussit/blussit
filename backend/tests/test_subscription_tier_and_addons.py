"""
Vehicle-type-tiered subscriptions + paid add-ons on plan visits.

Purchase: a plan is bought FOR one vehicle type (its tier) at that type's
plan price — stored on the subscription (vehicle_type / purchased_price).

Redemption: usable on the purchased type or any type the plan prices
CHEAPER (an SUV-tier card washes a hatchback; a hatchback-tier card never
washes an SUV). Using it on a cheaper type still burns one full visit.

Add-ons on a plan visit (extra bike wash ₹60/bike, polish, ...): always a
real charge, never covered, and never counted against the visit quota —
unless the admin explicitly listed the add-on in included_service_ids.
"""
import pytest
from bson import ObjectId

from app.core.exceptions import BadRequestException
from app.schemas.subscription_schema import SubscribeRequest
from app.services.booking_service import BookingService
from app.services.subscription_service import UserSubscriptionService, resolve_plan_price, tier_allows

from tests.factories import get_hatchback_type_id, get_suv_type_id, make_customer, make_subscription_plan, make_vehicle


async def _svc(db, name: str) -> dict:
    doc = await db.services.find_one({"name": name, "is_deleted": {"$ne": True}})
    assert doc, f"seed() should have created the '{name}' service"
    return doc


@pytest.fixture
async def rig(db, cleanup):
    hatchback = await get_hatchback_type_id(db)
    suv = await get_suv_type_id(db)
    customer_id = await make_customer(db)
    cleanup.append(("users", {"_id": ObjectId(customer_id)}))
    cleanup.append(("vehicles", {"owner_id": customer_id}))
    cleanup.append(("user_subscriptions", {"customer_id": customer_id}))
    plan_id = await make_subscription_plan(
        db,
        vehicle_types=[hatchback, suv],
        vehicle_type_prices={hatchback: 399.0, suv: 599.0},
    )
    cleanup.append(("subscription_plans", {"_id": ObjectId(plan_id)}))
    return {"db": db, "hatchback": hatchback, "suv": suv, "customer_id": customer_id, "plan_id": plan_id}


# ------------------------------------------------------------- pure helpers


def test_resolve_plan_price_prefers_per_type():
    plan = {"price": 500.0, "vehicle_type_prices": {"suv": 700.0}}
    assert resolve_plan_price(plan, "suv") == 700.0
    assert resolve_plan_price(plan, "hatchback") == 500.0
    assert resolve_plan_price(plan, None) == 500.0


def test_tier_allows_is_price_ordered():
    plan = {"price": 500.0, "vehicle_type_prices": {"hatch": 399.0, "suv": 599.0}}
    assert tier_allows(plan, "suv", "hatch")  # bigger tier covers smaller
    assert not tier_allows(plan, "hatch", "suv")  # never the other way
    assert tier_allows(plan, None, "suv")  # legacy sub: no tier cap
    assert tier_allows(plan, "hatch", "hatch")


# ---------------------------------------------------------------- purchase


@pytest.mark.asyncio
async def test_subscribe_stores_tier_and_price(rig):
    service = UserSubscriptionService(rig["db"])
    sub = await service.subscribe(
        rig["customer_id"], SubscribeRequest(plan_id=rig["plan_id"], vehicle_type=rig["suv"])
    )
    assert sub["vehicle_type"] == rig["suv"]
    assert sub["purchased_price"] == 599.0


@pytest.mark.asyncio
async def test_subscribe_rejects_type_not_sold_for_plan(rig, db, cleanup):
    hatch_only_plan = await make_subscription_plan(db, vehicle_types=[rig["hatchback"]])
    cleanup.append(("subscription_plans", {"_id": ObjectId(hatch_only_plan)}))
    service = UserSubscriptionService(db)
    with pytest.raises(BadRequestException, match="isn't sold"):
        await service.subscribe(
            rig["customer_id"], SubscribeRequest(plan_id=hatch_only_plan, vehicle_type=rig["suv"])
        )


# -------------------------------------------------------------- redemption


@pytest.mark.asyncio
async def test_hatchback_tier_cannot_wash_suv(rig):
    service = UserSubscriptionService(rig["db"])
    sub = await service.subscribe(
        rig["customer_id"], SubscribeRequest(plan_id=rig["plan_id"], vehicle_type=rig["hatchback"])
    )
    suv_vehicle = await make_vehicle(rig["db"], rig["customer_id"], rig["suv"])
    with pytest.raises(BadRequestException, match="smaller vehicle type"):
        await service.plan_consumption(sub["id"], suv_vehicle, [{"_id": "x", "category_id": None}], rig["customer_id"])


@pytest.mark.asyncio
async def test_suv_tier_covers_hatchback_and_burns_one_visit(rig):
    service = UserSubscriptionService(rig["db"])
    sub = await service.subscribe(
        rig["customer_id"], SubscribeRequest(plan_id=rig["plan_id"], vehicle_type=rig["suv"])
    )
    hatch_vehicle = await make_vehicle(rig["db"], rig["customer_id"], rig["hatchback"])
    consumption = await service.plan_consumption(sub["id"], hatch_vehicle, [{"_id": "x", "category_id": None}], rig["customer_id"])
    assert consumption == {"flat_count": 1}  # full visit, no credit


@pytest.mark.asyncio
async def test_legacy_sub_without_tier_is_uncapped(rig):
    """Subscriptions from before tiers existed (vehicle_type=None) keep
    working on any type the plan covers."""
    service = UserSubscriptionService(rig["db"])
    sub = await service.subscribe(rig["customer_id"], SubscribeRequest(plan_id=rig["plan_id"]))
    suv_vehicle = await make_vehicle(rig["db"], rig["customer_id"], rig["suv"])
    consumption = await service.plan_consumption(sub["id"], suv_vehicle, [{"_id": "x", "category_id": None}], rig["customer_id"])
    assert consumption == {"flat_count": 1}


# ----------------------------------------------------- add-ons on a plan


@pytest.mark.asyncio
async def test_addons_do_not_consume_quota(rig, db):
    service = UserSubscriptionService(db)
    sub = await service.subscribe(
        rig["customer_id"], SubscribeRequest(plan_id=rig["plan_id"], vehicle_type=rig["hatchback"])
    )
    hatch_vehicle = await make_vehicle(db, rig["customer_id"], rig["hatchback"])
    star = await _svc(db, "Star Wash")
    extra_bike = await _svc(db, "Extra Bike Wash")
    polish = await _svc(db, "Exterior Polish")
    consumption = await service.plan_consumption(sub["id"], hatch_vehicle, [star, extra_bike, polish], rig["customer_id"])
    assert consumption == {"flat_count": 1}  # only the main wash counts


@pytest.mark.asyncio
async def test_addons_never_discounted_fixed_plan(rig, db, cleanup):
    """A fixed plan covering Star Wash: the wash is free, the add-ons ride
    at full price — the discount covers exactly the main service."""
    star = await _svc(db, "Star Wash")
    polish = await _svc(db, "Exterior Polish")
    plan_id = await make_subscription_plan(
        db, vehicle_types=[], included_service_ids=[str(star["_id"])]
    )
    cleanup.append(("subscription_plans", {"_id": ObjectId(plan_id)}))
    service = UserSubscriptionService(db)
    sub = await service.subscribe(rig["customer_id"], SubscribeRequest(plan_id=plan_id))

    bs = BookingService(db)
    discount = await bs._subscription_discount(sub["id"], [star, polish], rig["hatchback"], False)
    star_price = (star.get("vehicle_type_prices") or {}).get(rig["hatchback"], star["price"])
    assert discount == round(star_price, 2)  # polish contributes nothing


@pytest.mark.asyncio
async def test_addons_never_discounted_legacy_plan(rig, db):
    """Even a legacy/unrestricted plan (waives any MAIN service) never
    covers an add-on."""
    star = await _svc(db, "Star Wash")
    polish = await _svc(db, "Exterior Polish")
    service = UserSubscriptionService(db)
    sub = await service.subscribe(rig["customer_id"], SubscribeRequest(plan_id=rig["plan_id"]))

    bs = BookingService(db)
    discount = await bs._subscription_discount(sub["id"], [star, polish], rig["hatchback"], False)
    star_price = (star.get("vehicle_type_prices") or {}).get(rig["hatchback"], star["price"])
    assert discount == round(star_price, 2)


@pytest.mark.asyncio
async def test_cheaper_swap_is_covered_costlier_pays_gap(rig, db, cleanup):
    """Fixed plan covering the costlier service: swapping down is fully
    covered (no credit); a plan covering the cheaper one charges only the
    gap when swapping up."""
    star = await _svc(db, "Star Wash")
    deep = await _svc(db, "Deep Cleaning")
    hatch = rig["hatchback"]
    price_of = lambda s: (s.get("vehicle_type_prices") or {}).get(hatch, s["price"])  # noqa: E731

    service = UserSubscriptionService(db)
    bs = BookingService(db)

    # Plan covers Deep Cleaning (costlier) -> booking Star Wash is covered.
    plan_deep = await make_subscription_plan(db, vehicle_types=[], included_service_ids=[str(deep["_id"])])
    cleanup.append(("subscription_plans", {"_id": ObjectId(plan_deep)}))
    sub_deep = await service.subscribe(rig["customer_id"], SubscribeRequest(plan_id=plan_deep))
    discount = await bs._subscription_discount(sub_deep["id"], [star], hatch, False)
    assert discount == round(price_of(star), 2)  # fully covered, nothing credited

    # Plan covers Star Wash (cheaper) -> booking Deep Cleaning pays the gap.
    plan_star = await make_subscription_plan(db, vehicle_types=[], included_service_ids=[str(star["_id"])])
    cleanup.append(("subscription_plans", {"_id": ObjectId(plan_star)}))
    sub_star = await service.subscribe(rig["customer_id"], SubscribeRequest(plan_id=plan_star))
    discount = await bs._subscription_discount(sub_star["id"], [deep], hatch, False)
    assert discount == round(price_of(star), 2)  # covered only up to the plan's own service
