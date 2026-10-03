"""
One account never saves the same car twice: the vehicle create/update path
refuses a plate that's already on the caller's account with a 409, however
it's spaced or cased ("MP09 ab 1234" == "MP09AB1234"). Other accounts are
still governed by the existing shared-registration rule.
"""
import pytest
from bson import ObjectId

from app.core.exceptions import ConflictException
from app.schemas.profile_schema import VehicleCreateRequest, VehicleUpdateRequest
from app.services.profile_service import VehicleService

from tests.factories import get_hatchback_type_id, make_customer
from tests.society_factories import auth, client

pytestmark = pytest.mark.asyncio


async def _customer(db, cleanup) -> str:
    customer_id = await make_customer(db)
    cleanup.append(("users", {"_id": ObjectId(customer_id)}))
    cleanup.append(("vehicles", {"owner_id": customer_id}))
    return customer_id


def _car(vehicle_type: str, plate: str) -> VehicleCreateRequest:
    return VehicleCreateRequest(vehicle_type=vehicle_type, brand="Maruti", model="Swift", registration_number=plate)


async def test_same_plate_twice_on_one_account_is_a_409(db, cleanup):
    owner = await _customer(db, cleanup)
    hatchback = await get_hatchback_type_id(db)
    service = VehicleService(db)
    await service.create(owner, _car(hatchback, "MP09DP4321"))
    with pytest.raises(ConflictException, match="already saved on this account"):
        await service.create(owner, _car(hatchback, "mp09 dp 4321"))
    with pytest.raises(ConflictException):
        await service.create(owner, _car(hatchback, "MP-09-DP-4321"))
    assert await db.vehicles.count_documents({"owner_id": owner, "is_deleted": {"$ne": True}}) == 1


async def test_renaming_a_car_to_a_plate_already_saved_is_a_409(db, cleanup):
    owner = await _customer(db, cleanup)
    hatchback = await get_hatchback_type_id(db)
    service = VehicleService(db)
    first = await service.create(owner, _car(hatchback, "MP09DP5001"))
    second = await service.create(owner, _car(hatchback, "MP09DP5002"))
    with pytest.raises(ConflictException):
        await service.update(owner, second["id"], VehicleUpdateRequest(registration_number="mp09dp 5001"))
    # Saving a car with its own plate (re-typed) and other fields is fine.
    updated = await service.update(owner, first["id"], VehicleUpdateRequest(registration_number="MP09 DP 5001", model="Baleno"))
    assert updated["model"] == "Baleno"


async def test_a_deleted_car_or_another_account_does_not_block(db, cleanup):
    owner = await _customer(db, cleanup)
    other = await _customer(db, cleanup)
    hatchback = await get_hatchback_type_id(db)
    service = VehicleService(db)
    gone = await service.create(owner, _car(hatchback, "MP09DP6001"))
    await service.delete(owner, gone["id"])
    await service.create(owner, _car(hatchback, "MP09DP6001"))  # re-adding after delete is allowed
    shared = VehicleCreateRequest(**{**_car(hatchback, "MP09DP6001").model_dump(), "acknowledge_shared_registration": True})
    await service.create(other, shared)  # another account: the shared-registration rule, not this one


async def test_http_create_returns_409_with_the_message(db, cleanup):
    owner = await _customer(db, cleanup)
    hatchback = await get_hatchback_type_id(db)
    body = {"vehicle_type": hatchback, "brand": "Maruti", "model": "Swift", "registration_number": "MP09DP7001"}
    async with client() as c:
        first = await c.post("/api/v1/vehicles", json=body, headers=auth(owner, "customer"))
        assert first.status_code in (200, 201), first.text
        dup = await c.post("/api/v1/vehicles", json={**body, "registration_number": "mp09 dp 7001"}, headers=auth(owner, "customer"))
        assert dup.status_code == 409
        assert "already saved on this account" in dup.json()["message"]
