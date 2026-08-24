from motor.motor_asyncio import AsyncIOMotorDatabase

from app.core.authz import ensure_own_center
from app.core.exceptions import BadRequestException, NotFoundException
from app.repositories.inventory_repository import InventoryRepository
from app.schemas.inventory_schema import InventoryAdjustRequest, InventoryCreateRequest, InventoryUpdateRequest
from app.utils.serializers import serialize_doc, serialize_list


class InventoryService:
    def __init__(self, db: AsyncIOMotorDatabase):
        self.repo = InventoryRepository(db)

    async def list_for_center(
        self,
        service_center_id: str,
        page: int,
        page_size: int,
        low_stock_only: bool = False,
        actor_role: str | None = None,
        actor_center_id: str | None = None,
    ):
        # actor_role=None (only the captain's own-center lookup at
        # GET /inventory/my-center passes this) means the caller already
        # resolved service_center_id from their own account — nothing to
        # check against. Every manager-facing center-scoped call passes a
        # real role and must be checked.
        if actor_role is not None:
            ensure_own_center(actor_role, actor_center_id, service_center_id)
        items, total = await self.repo.list_for_center(service_center_id, page, page_size, low_stock_only)
        return serialize_list(items), total

    async def create(self, payload: InventoryCreateRequest) -> dict:
        created = await self.repo.create(payload.model_dump())
        return serialize_doc(created)

    async def update(self, item_id: str, payload: InventoryUpdateRequest) -> dict:
        data = {k: v for k, v in payload.model_dump(exclude_unset=True).items() if v is not None}
        updated = await self.repo.update_by_id(item_id, data)
        if not updated:
            raise NotFoundException("Inventory item not found")
        return serialize_doc(updated)

    async def adjust(self, item_id: str, payload: InventoryAdjustRequest) -> dict:
        item = await self.repo.find_by_id(item_id)
        if not item:
            raise NotFoundException("Inventory item not found")
        if item["quantity_available"] + payload.delta < 0:
            raise BadRequestException("Insufficient stock for this adjustment")
        updated = await self.repo.adjust_quantity(item_id, payload.delta)
        return serialize_doc(updated)

    async def delete(self, item_id: str) -> None:
        if not await self.repo.soft_delete(item_id):
            raise NotFoundException("Inventory item not found")
