"""
Admin price-editing fields must accept any real-world rupee figure, not
just round numbers. The bug this guards: the service/plan edit forms had
an HTML `step={20}` on every price <input>, which makes the BROWSER reject
anything that isn't an exact multiple of 20 (₹999, ₹1499, ₹249 all got
"Enter a valid value" and silently refused to submit) before the request
was ever sent — see AdminServicesPage.tsx and AdminSubscriptionPlansPage.tsx.
That specific bug is frontend-only and can't be exercised from here, but
these tests lock in the backend half of the contract: the schemas and
services that receive those prices must never grow their own multiple-of
constraint, since the whole point of "type any figure" is that ₹999 is
just as valid as ₹1000.
"""
import pytest
from bson import ObjectId

from app.schemas.catalog_schema import ServiceCreateRequest, ServiceUpdateRequest
from app.schemas.subscription_schema import SubscriptionPlanCreateRequest, SubscriptionPlanUpdateRequest
from app.services.catalog_service import ServiceCatalogService
from app.services.subscription_service import SubscriptionPlanService

from tests.factories import get_hatchback_type_id, get_suv_type_id

NOT_MULTIPLES_OF_20 = [999.0, 1499.0, 249.0, 1.0, 349.5]


@pytest.mark.parametrize("price", NOT_MULTIPLES_OF_20)
def test_service_create_schema_accepts_prices_that_are_not_multiples_of_20(price):
    payload = ServiceCreateRequest(
        category_id=str(ObjectId()), name="Test wash", price=price,
        discounted_price=price, original_price=price, captain_fee=price,
        vehicle_type_prices={str(ObjectId()): price},
    )
    assert payload.price == price
    assert payload.discounted_price == price
    assert payload.original_price == price
    assert payload.captain_fee == price
    assert list(payload.vehicle_type_prices.values())[0] == price


@pytest.mark.parametrize("price", NOT_MULTIPLES_OF_20)
def test_service_update_schema_accepts_prices_that_are_not_multiples_of_20(price):
    payload = ServiceUpdateRequest(price=price, original_price=price, captain_fee=price)
    assert payload.price == price
    assert payload.original_price == price
    assert payload.captain_fee == price


@pytest.mark.parametrize("price", NOT_MULTIPLES_OF_20)
def test_subscription_plan_create_schema_accepts_prices_that_are_not_multiples_of_20(price):
    payload = SubscriptionPlanCreateRequest(
        name="Test plan", billing_cycle="monthly", price=price,
        service_pass_prices={str(ObjectId()): {str(ObjectId()): price}},
    )
    assert payload.price == price
    assert list(list(payload.service_pass_prices.values())[0].values())[0] == price


@pytest.mark.parametrize("price", NOT_MULTIPLES_OF_20)
def test_subscription_plan_update_schema_accepts_prices_that_are_not_multiples_of_20(price):
    payload = SubscriptionPlanUpdateRequest(price=price)
    assert payload.price == price


@pytest.fixture
async def rig(db, cleanup):
    category = await db.categories.find_one({"is_deleted": {"$ne": True}})
    assert category, "seed() should have created at least one category"
    hatchback = await get_hatchback_type_id(db)
    suv = await get_suv_type_id(db)
    return {"db": db, "category_id": str(category["_id"]), "hatchback": hatchback, "suv": suv}


@pytest.mark.asyncio
async def test_creating_a_service_with_a_non_round_price_stores_it_unchanged(rig):
    payload = ServiceCreateRequest(
        category_id=rig["category_id"], name="Odd Price Wash", price=999.0,
        discounted_price=249.0, original_price=1499.0, captain_fee=349.5,
        vehicle_type_prices={rig["hatchback"]: 999.0, rig["suv"]: 1499.0},
    )
    created = await ServiceCatalogService(rig["db"]).create(payload)
    try:
        assert created["price"] == 999.0
        assert created["discounted_price"] == 249.0
        assert created["original_price"] == 1499.0
        assert created["captain_fee"] == 349.5
        assert created["vehicle_type_prices"][rig["hatchback"]] == 999.0
        assert created["vehicle_type_prices"][rig["suv"]] == 1499.0

        updated = await ServiceCatalogService(rig["db"]).update(created["id"], ServiceUpdateRequest(price=1249.0))
        assert updated["price"] == 1249.0
    finally:
        await rig["db"].services.delete_one({"_id": ObjectId(created["id"])})


@pytest.mark.asyncio
async def test_creating_a_subscription_plan_with_a_non_round_pass_price_stores_it_unchanged(rig):
    payload = SubscriptionPlanCreateRequest(
        name="Odd Price Plan", billing_cycle="monthly", price=999.0,
        vehicle_types=[rig["hatchback"]],
        service_pass_prices={"some-service-id": {rig["hatchback"]: 349.0}},
    )
    created = await SubscriptionPlanService(rig["db"]).create(payload)
    try:
        assert created["price"] == 999.0
        assert created["service_pass_prices"]["some-service-id"][rig["hatchback"]] == 349.0

        updated = await SubscriptionPlanService(rig["db"]).update(
            created["id"], SubscriptionPlanUpdateRequest(price=1249.0)
        )
        assert updated["price"] == 1249.0
    finally:
        await rig["db"].subscription_plans.delete_one({"_id": ObjectId(created["id"])})
