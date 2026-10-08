from motor.motor_asyncio import AsyncIOMotorDatabase

from app.core.dependencies import CurrentUser, PaginationParams
from app.core.responses import paginated, success
from app.schemas.catalog_schema import (
    CategoryCreateRequest,
    CategoryUpdateRequest,
    ComboOfferCreateRequest,
    ComboOfferUpdateRequest,
    ServiceCreateRequest,
    ServiceUpdateRequest,
)
from app.services.audit_service import AuditService, field_changes
from app.services.catalog_service import CategoryService, ComboOfferService, ServiceCatalogService, public_service


class CategoryController:
    def __init__(self, db: AsyncIOMotorDatabase):
        self.service = CategoryService(db)
        self.audit = AuditService(db)

    async def list(self, active_only: bool):
        return success(await self.service.list_all(active_only))

    async def create(self, current_user: CurrentUser, payload: CategoryCreateRequest):
        result = await self.service.create(payload)
        await self.audit.log_action(current_user.id, current_user.role, "CREATE_CATEGORY", "categories", result["id"])
        return success(result, "Category created successfully")

    async def update(self, current_user: CurrentUser, category_id: str, payload: CategoryUpdateRequest):
        before = await self.service.repo.find_by_id(category_id)
        result = await self.service.update(category_id, payload)
        # Before/after for every field that really changed (ADM-08).
        changes = field_changes(before, result, [*payload.model_dump(exclude_unset=True), "slug"])
        await self.audit.log_action(current_user.id, current_user.role, "UPDATE_CATEGORY", "categories", category_id, {"changes": changes})
        return success(result, "Category updated successfully")

    async def delete(self, current_user: CurrentUser, category_id: str):
        before = await self.service.repo.find_by_id(category_id)
        await self.service.delete(category_id)
        await self.audit.log_action(current_user.id, current_user.role, "DELETE_CATEGORY", "categories", category_id, {"name": (before or {}).get("name")})
        return success(None, "Category deleted successfully")


class ServiceController:
    def __init__(self, db: AsyncIOMotorDatabase):
        self.service = ServiceCatalogService(db)
        self.audit = AuditService(db)

    async def list(self, pagination: PaginationParams, category_id: str | None, vehicle_type: str | None, active_only: bool = True, public: bool = False):
        items, total = await self.service.list_services(pagination.page, pagination.page_size, pagination.search, category_id, vehicle_type, active_only)
        if public:
            items = [public_service(i) for i in items]
        return paginated(items, pagination.page, pagination.page_size, total)

    async def get(self, service_id: str, public: bool = False):
        service = await self.service.get(service_id)
        return success(public_service(service) if public else service)

    async def create(self, current_user: CurrentUser, payload: ServiceCreateRequest):
        result = await self.service.create(payload)
        await self.audit.log_action(current_user.id, current_user.role, "CREATE_SERVICE", "services", result["id"])
        return success(result, "Service created successfully")

    async def update(self, current_user: CurrentUser, service_id: str, payload: ServiceUpdateRequest):
        before = await self.service.repo.find_by_id(service_id)
        result = await self.service.update(service_id, payload)
        # Before/after for every field that really changed (ADM-08).
        changes = field_changes(before, result, [*payload.model_dump(exclude_unset=True), "slug"])
        await self.audit.log_action(current_user.id, current_user.role, "UPDATE_SERVICE", "services", service_id, {"changes": changes})
        return success(result, "Service updated successfully")

    async def delete(self, current_user: CurrentUser, service_id: str):
        before = await self.service.repo.find_by_id(service_id)
        await self.service.delete(service_id)
        await self.audit.log_action(current_user.id, current_user.role, "DELETE_SERVICE", "services", service_id, {"name": (before or {}).get("name")})
        return success(None, "Service deleted successfully")


class ComboOfferController:
    def __init__(self, db: AsyncIOMotorDatabase):
        self.service = ComboOfferService(db)
        self.audit = AuditService(db)

    async def list(self, active_only: bool):
        return success(await self.service.list_all(active_only))

    async def get(self, combo_id: str):
        return success(await self.service.get(combo_id))

    async def create(self, current_user: CurrentUser, payload: ComboOfferCreateRequest):
        result = await self.service.create(payload)
        await self.audit.log_action(current_user.id, current_user.role, "CREATE_COMBO_OFFER", "combo_offers", result["id"])
        return success(result, "Combo offer created successfully")

    async def update(self, current_user: CurrentUser, combo_id: str, payload: ComboOfferUpdateRequest):
        before = await self.service.repo.find_by_id(combo_id)
        result = await self.service.update(combo_id, payload)
        # Before/after for every field that really changed (ADM-08).
        changes = field_changes(before, result, [*payload.model_dump(exclude_unset=True), "slug"])
        await self.audit.log_action(current_user.id, current_user.role, "UPDATE_COMBO_OFFER", "combo_offers", combo_id, {"changes": changes})
        return success(result, "Combo offer updated successfully")

    async def delete(self, current_user: CurrentUser, combo_id: str):
        before = await self.service.repo.find_by_id(combo_id)
        await self.service.delete(combo_id)
        await self.audit.log_action(current_user.id, current_user.role, "DELETE_COMBO_OFFER", "combo_offers", combo_id, {"name": (before or {}).get("name")})
        return success(None, "Combo offer deleted successfully")
