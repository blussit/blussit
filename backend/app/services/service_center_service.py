from motor.motor_asyncio import AsyncIOMotorDatabase

from app.core.exceptions import NotFoundException
from app.repositories.service_center_repository import ServiceCenterRepository
from app.schemas.service_center_schema import ServiceCenterCreateRequest, ServiceCenterUpdateRequest
from app.utils.serializers import serialize_doc, serialize_list


class ServiceCenterService:
    def __init__(self, db: AsyncIOMotorDatabase):
        self.repo = ServiceCenterRepository(db)

    async def list_centers(self, page: int, page_size: int, search: str | None, active_only: bool = False):
        items, total = await self.repo.search(search, page, page_size, active_only)
        return serialize_list(items), total

    async def get(self, center_id: str) -> dict:
        center = await self.repo.find_by_id(center_id)
        if not center:
            raise NotFoundException("Service center not found")
        return serialize_doc(center)

    async def find_for_pincode(self, pincode: str) -> list[dict]:
        centers = await self.repo.find_by_pincode(pincode)
        return serialize_list(centers)

    async def create(self, payload: ServiceCenterCreateRequest) -> dict:
        doc = payload.model_dump()
        doc["code"] = self.repo.generate_code(payload.location.city)
        created = await self.repo.create(doc)
        return serialize_doc(created)

    async def update(self, center_id: str, payload: ServiceCenterUpdateRequest) -> dict:
        data = {k: v for k, v in payload.model_dump(exclude_unset=True).items() if v is not None}
        updated = await self.repo.update_by_id(center_id, data)
        if not updated:
            raise NotFoundException("Service center not found")
        return serialize_doc(updated)

    async def delete(self, center_id: str) -> None:
        deleted = await self.repo.soft_delete(center_id)
        if not deleted:
            raise NotFoundException("Service center not found")
