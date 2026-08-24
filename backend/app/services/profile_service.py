from motor.motor_asyncio import AsyncIOMotorDatabase

from app.core.exceptions import BadRequestException, ForbiddenException, NotFoundException
from app.repositories.address_repository import AddressRepository
from app.repositories.booking_repository import BookingRepository
from app.repositories.vehicle_repository import VehicleRepository
from app.schemas.profile_schema import (
    AddressCreateRequest,
    AddressUpdateRequest,
    VehicleCreateRequest,
    VehicleUpdateRequest,
)
from app.services.subscription_service import UserSubscriptionService
from app.utils.serializers import serialize_doc


class VehicleService:
    def __init__(self, db: AsyncIOMotorDatabase):
        self.repo = VehicleRepository(db)
        self.booking_repo = BookingRepository(db)
        self.subscription_service = UserSubscriptionService(db)

    async def list_my_vehicles(self, owner_id: str) -> list[dict]:
        vehicles = await self.repo.list_by_owner(owner_id)
        return [serialize_doc(v) for v in vehicles]

    async def create(self, owner_id: str, payload: VehicleCreateRequest) -> dict:
        if payload.is_default:
            await self.repo.clear_default(owner_id)
        doc = payload.model_dump()
        doc["owner_id"] = owner_id
        created = await self.repo.create(doc)
        return serialize_doc(created)

    async def update(self, owner_id: str, vehicle_id: str, payload: VehicleUpdateRequest) -> dict:
        existing = await self.repo.find_by_id(vehicle_id)
        if not existing or existing["owner_id"] != owner_id:
            raise NotFoundException("Vehicle not found")
        if payload.is_default:
            await self.repo.clear_default(owner_id)
        data = {k: v for k, v in payload.model_dump(exclude_unset=True).items() if v is not None}
        updated = await self.repo.update_by_id(vehicle_id, data)
        return serialize_doc(updated)

    async def delete(self, owner_id: str, vehicle_id: str) -> None:
        existing = await self.repo.find_by_id(vehicle_id)
        if not existing or existing["owner_id"] != owner_id:
            raise NotFoundException("Vehicle not found")
        blocking = await self.booking_repo.exists_active_for_vehicle_or_address(vehicle_id=vehicle_id)
        if blocking:
            raise BadRequestException(
                f"This vehicle is used by booking {blocking['booking_number']}, which isn't finished yet — "
                "cancel or complete that booking before removing the vehicle."
            )
        if await self.subscription_service.has_active_for_vehicle(vehicle_id):
            raise BadRequestException(
                "This vehicle has an active subscription attached — cancel it (or let it expire) before removing the vehicle."
            )
        await self.repo.soft_delete(vehicle_id, owner_id)


class AddressService:
    def __init__(self, db: AsyncIOMotorDatabase):
        self.repo = AddressRepository(db)
        self.booking_repo = BookingRepository(db)

    async def list_my_addresses(self, owner_id: str) -> list[dict]:
        addresses = await self.repo.list_by_owner(owner_id)
        return [serialize_doc(a) for a in addresses]

    async def create(self, owner_id: str, payload: AddressCreateRequest) -> dict:
        if payload.is_default:
            await self.repo.clear_default(owner_id)
        doc = payload.model_dump()
        doc["owner_id"] = owner_id
        created = await self.repo.create(doc)
        return serialize_doc(created)

    async def update(self, owner_id: str, address_id: str, payload: AddressUpdateRequest) -> dict:
        existing = await self.repo.find_by_id(address_id)
        if not existing or existing["owner_id"] != owner_id:
            raise NotFoundException("Address not found")
        if payload.is_default:
            await self.repo.clear_default(owner_id)
        data = {k: v for k, v in payload.model_dump(exclude_unset=True).items() if v is not None}
        updated = await self.repo.update_by_id(address_id, data)
        return serialize_doc(updated)

    async def delete(self, owner_id: str, address_id: str) -> None:
        existing = await self.repo.find_by_id(address_id)
        if not existing or existing["owner_id"] != owner_id:
            raise NotFoundException("Address not found")
        blocking = await self.booking_repo.exists_active_for_vehicle_or_address(address_id=address_id)
        if blocking:
            raise BadRequestException(
                f"This address is used by booking {blocking['booking_number']}, which isn't finished yet — "
                "cancel or complete that booking before removing the address."
            )
        await self.repo.soft_delete(address_id, owner_id)
