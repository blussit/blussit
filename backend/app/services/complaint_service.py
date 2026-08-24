from motor.motor_asyncio import AsyncIOMotorDatabase

from app.core.authz import ensure_own_center
from app.core.exceptions import NotFoundException
from app.models.enums import ComplaintStatus, NotificationType
from app.repositories.booking_repository import BookingRepository
from app.repositories.complaint_repository import ComplaintRepository
from app.schemas.complaint_schema import ComplaintCreateRequest, ComplaintUpdateRequest
from app.services.notification_service import NotificationService
from app.utils.serializers import serialize_doc, serialize_list


class ComplaintService:
    def __init__(self, db: AsyncIOMotorDatabase):
        self.repo = ComplaintRepository(db)
        self.booking_repo = BookingRepository(db)
        self.notifications = NotificationService(db)

    async def create(self, customer_id: str, payload: ComplaintCreateRequest) -> dict:
        service_center_id = None
        if payload.booking_id:
            booking = await self.booking_repo.find_by_id(payload.booking_id)
            if booking:
                service_center_id = booking["service_center_id"]

        doc = payload.model_dump()
        doc["customer_id"] = customer_id
        doc["service_center_id"] = service_center_id
        created = await self.repo.create(doc)
        return serialize_doc(created)

    async def list_for_customer(self, customer_id: str, page: int, page_size: int):
        items, total = await self.repo.list_for_customer(customer_id, page, page_size)
        return serialize_list(items), total

    async def list_for_center(
        self, service_center_id: str, status: str | None, page: int, page_size: int, actor_role: str, actor_center_id: str | None
    ):
        ensure_own_center(actor_role, actor_center_id, service_center_id)
        items, total = await self.repo.list_for_center(service_center_id, status, page, page_size)
        return serialize_list(items), total

    async def list_all(self, filters: dict, page: int, page_size: int):
        items, total = await self.repo.list_all(filters, page, page_size)
        return serialize_list(items), total

    async def update(self, complaint_id: str, payload: ComplaintUpdateRequest, resolved_by: str) -> dict:
        complaint = await self.repo.find_by_id(complaint_id)
        if not complaint:
            raise NotFoundException("Complaint not found")

        data = {k: v.value if hasattr(v, "value") else v for k, v in payload.model_dump(exclude_unset=True).items() if v is not None}
        if data.get("status") == ComplaintStatus.RESOLVED.value:
            data["resolved_by"] = resolved_by

        updated = await self.repo.update_by_id(complaint_id, data)
        await self.notifications.notify(
            complaint["customer_id"],
            "Complaint update",
            f"Your complaint '{complaint['subject']}' has been updated.",
            NotificationType.COMPLAINT,
            complaint_id,
        )
        return serialize_doc(updated)
