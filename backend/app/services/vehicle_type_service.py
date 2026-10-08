from motor.motor_asyncio import AsyncIOMotorDatabase

from app.core.exceptions import ConflictException, NotFoundException
from app.repositories.vehicle_type_repository import VehicleTypeRepository
from app.schemas.vehicle_type_schema import VehicleTypeCreateRequest, VehicleTypeUpdateRequest
from app.utils.serializers import serialize_doc, serialize_list
from app.utils.text import slugify


class VehicleTypeService:
    def __init__(self, db: AsyncIOMotorDatabase):
        self.repo = VehicleTypeRepository(db)

    async def list_all(self, active_only: bool = False) -> list[dict]:
        filters = {"is_active": True} if active_only else None
        items = await self.repo.find_all_no_paginate(filters, sort_by="display_order", sort_order=1)
        return serialize_list(items)

    async def create(self, payload: VehicleTypeCreateRequest) -> dict:
        slug = slugify(payload.name)
        if await self.repo.find_one({"slug": slug}):
            raise ConflictException("A vehicle type with this name already exists")
        doc = payload.model_dump()
        doc["slug"] = slug
        created = await self.repo.create(doc)
        return serialize_doc(created)

    async def update(self, vehicle_type_id: str, payload: VehicleTypeUpdateRequest) -> dict:
        data = {k: v for k, v in payload.model_dump(exclude_unset=True).items() if v is not None}
        if "name" in data:
            data["slug"] = slugify(data["name"])
        updated = await self.repo.update_by_id(vehicle_type_id, data)
        if not updated:
            raise NotFoundException("Vehicle type not found")
        return serialize_doc(updated)

    async def uses(self, vehicle_type_id: str) -> list[str]:
        """What still points at this vehicle type: live bookings, passes and
        plans priced for it, customers' saved cars (ADM-06 — deleting it
        left all of them pointing at a type no lookup resolves)."""
        from app.services.catalog_service import _live_bookings, _LIVE_PASS_STATUSES, _names

        db = self.repo.db
        uses: list[str] = []
        live = await _live_bookings(db, {"vehicle_type": vehicle_type_id})
        if live:
            uses.append(f"{len(live)} live booking(s) ({_names(live, 'booking_number')})")
        passes = await db.user_subscriptions.count_documents(
            {"vehicle_type": vehicle_type_id, "status": {"$in": _LIVE_PASS_STATUSES}, "is_deleted": {"$ne": True}}
        )
        if passes:
            uses.append(f"{passes} active pass(es)")
        plans = await db.subscription_plans.find(
            {"vehicle_types": vehicle_type_id, "is_deleted": {"$ne": True}}, {"name": 1},
        ).to_list(length=50)
        if plans:
            uses.append(f"plan(s) {_names(plans)}")
        cars = await db.vehicles.count_documents({"vehicle_type": vehicle_type_id, "is_deleted": {"$ne": True}})
        if cars:
            uses.append(f"{cars} customer car(s) saved as this type")
        return uses

    async def delete(self, vehicle_type_id: str) -> None:
        if not await self.repo.find_by_id(vehicle_type_id):
            raise NotFoundException("Vehicle type not found")
        uses = await self.uses(vehicle_type_id)
        if uses:
            from app.services.catalog_service import in_use_refusal

            raise in_use_refusal("vehicle type", uses)
        if not await self.repo.soft_delete(vehicle_type_id):
            raise NotFoundException("Vehicle type not found")
