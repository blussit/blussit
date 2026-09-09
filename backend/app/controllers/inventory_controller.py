from motor.motor_asyncio import AsyncIOMotorDatabase

from app.core.dependencies import CurrentUser, PaginationParams
from app.core.responses import paginated, success
from app.schemas.inventory_schema import InventoryAdjustRequest, InventoryCreateRequest, InventoryUpdateRequest
from app.services.audit_service import AuditService
from app.services.inventory_service import InventoryService


class InventoryController:
    def __init__(self, db: AsyncIOMotorDatabase):
        self.service = InventoryService(db)
        self.audit = AuditService(db)

    async def list_for_center(self, current_user: CurrentUser, service_center_id: str, pagination: PaginationParams, low_stock_only: bool):
        items, total = await self.service.list_for_center(
            service_center_id, pagination.page, pagination.page_size, low_stock_only, current_user.role, current_user.service_center_id
        )
        return paginated(items, pagination.page, pagination.page_size, total)

    async def create(self, current_user: CurrentUser, payload: InventoryCreateRequest):
        result = await self.service.create(payload, current_user.role, current_user.service_center_id)
        await self.audit.log_action(current_user.id, current_user.role, "CREATE_INVENTORY_ITEM", "inventory", result["id"])
        return success(result, "Inventory item added successfully")

    async def update(self, current_user: CurrentUser, item_id: str, payload: InventoryUpdateRequest):
        result = await self.service.update(item_id, payload, current_user.role, current_user.service_center_id)
        return success(result, "Inventory item updated successfully")

    async def adjust(self, current_user: CurrentUser, item_id: str, payload: InventoryAdjustRequest):
        result = await self.service.adjust(item_id, payload, current_user.role, current_user.service_center_id)
        await self.audit.log_action(current_user.id, current_user.role, "ADJUST_INVENTORY", "inventory", item_id, {"delta": payload.delta, "reason": payload.reason})
        return success(result, "Inventory adjusted successfully")

    async def delete(self, current_user: CurrentUser, item_id: str):
        await self.service.delete(item_id, current_user.role, current_user.service_center_id)
        return success(None, "Inventory item removed successfully")
