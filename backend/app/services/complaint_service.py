from datetime import datetime, timezone

from motor.motor_asyncio import AsyncIOMotorDatabase

from app.core.authz import ensure_own_center
from app.core.exceptions import BadRequestException, ForbiddenException, NotFoundException
from app.models.enums import ComplaintStatus, NotificationType
from app.repositories.booking_repository import BookingRepository
from app.repositories.complaint_repository import ComplaintRepository
from app.repositories.service_center_repository import ServiceCenterRepository
from app.repositories.user_repository import UserRepository
from app.schemas.complaint_schema import ComplaintCreateRequest, ComplaintUpdateRequest
from app.services.notification_service import NotificationService
from app.utils.serializers import serialize_doc, serialize_list


class ComplaintService:
    def __init__(self, db: AsyncIOMotorDatabase):
        self.repo = ComplaintRepository(db)
        self.booking_repo = BookingRepository(db)
        self.center_repo = ServiceCenterRepository(db)
        self.user_repo = UserRepository(db)
        self.notifications = NotificationService(db)

    async def create(self, customer_id: str, payload: ComplaintCreateRequest) -> dict:
        # A complaint must be pinned to a real booking the customer actually
        # owns — never a free-floating complaint the customer typed against
        # nothing. This is also what routes it to the RIGHT manager: the
        # complaint inherits its service_center_id straight from the
        # booking, so there is no separate "pick a center" step to get
        # wrong.
        booking = await self.booking_repo.find_by_id(payload.booking_id)
        if not booking:
            raise NotFoundException("Booking not found")
        if booking["customer_id"] != customer_id:
            raise ForbiddenException("You can only raise a complaint about your own booking")

        doc = payload.model_dump()
        doc["customer_id"] = customer_id
        doc["service_center_id"] = booking["service_center_id"]
        # BaseRepository.create() only defaults created_at/updated_at/is_deleted
        # — it never runs this dict through ComplaintModel, so that model's
        # `status: ComplaintStatus = ComplaintStatus.OPEN` default was never
        # actually applied. Every complaint ever created was missing `status`
        # entirely, which crashed StatusBadge (`undefined.replace(...)`) and
        # white-screened the whole admin/manager complaints page the moment a
        # real complaint existed. Set explicitly here, same pattern already
        # used for every other status-bearing model in this codebase (e.g.
        # BookingModel via booking_service.py).
        doc["status"] = ComplaintStatus.OPEN.value
        doc["replies"] = []
        created = await self.repo.create(doc)

        # The service center's manager(s) should hear about this immediately
        # — same notification-on-creation convention already used for a new
        # booking (BookingService.create_booking notifying the center).
        managers = await self.user_repo.find_many({"role": "manager", "service_center_id": booking["service_center_id"]}, page=1, page_size=50)
        for manager in managers[0]:
            await self.notifications.notify(
                str(manager["_id"]), "New complaint", f"A new complaint was raised: '{payload.subject}'.", NotificationType.COMPLAINT, str(created["_id"])
            )
        return serialize_doc(created)

    async def list_for_customer(self, customer_id: str, page: int, page_size: int):
        items, total = await self.repo.list_for_customer(customer_id, page, page_size)
        return await self._enrich(items), total

    async def list_for_center(
        self, service_center_id: str, status: str | None, page: int, page_size: int, actor_role: str, actor_center_id: str | None
    ):
        ensure_own_center(actor_role, actor_center_id, service_center_id)
        items, total = await self.repo.list_for_center(service_center_id, status, page, page_size)
        return await self._enrich(items), total

    async def list_all(self, filters: dict, page: int, page_size: int):
        items, total = await self.repo.list_all(filters, page, page_size)
        return await self._enrich(items), total

    async def _enrich(self, complaints: list[dict]) -> list[dict]:
        """Denormalizes the fields the admin/manager complaint list actually
        needs to be useful at a glance — which customer, which booking,
        which service center — same enrichment pattern already used for
        reviews (ReviewService._enrich)."""
        booking_ids = {c["booking_id"] for c in complaints if c.get("booking_id")}
        customer_ids = {c["customer_id"] for c in complaints if c.get("customer_id")}
        center_ids = {c["service_center_id"] for c in complaints if c.get("service_center_id")}

        bookings = {str(b["_id"]): b for b in await self.booking_repo.find_by_ids(list(booking_ids))}
        customers = {str(u["_id"]): u for u in await self.user_repo.find_by_ids(list(customer_ids))}
        centers = {str(c["_id"]): c for c in await self.center_repo.find_by_ids(list(center_ids))}

        results = []
        for complaint in complaints:
            doc = serialize_doc(complaint)
            booking = bookings.get(complaint.get("booking_id"))
            customer = customers.get(complaint.get("customer_id"))
            center = centers.get(complaint.get("service_center_id"))
            doc["booking_number"] = booking.get("booking_number") if booking else None
            doc["customer_name"] = (customer.get("full_name") if customer else None) or (booking.get("customer_name") if booking else None)
            doc["service_center_name"] = center.get("name") if center else None
            results.append(doc)
        return results

    async def update(self, complaint_id: str, payload: ComplaintUpdateRequest, resolved_by: str,
                     actor_role: str = "admin", actor_center_id: str | None = None) -> dict:
        complaint = await self.repo.find_by_id(complaint_id)
        if not complaint:
            raise NotFoundException("Complaint not found")
        # Same center-scoping as add_reply below — a manager may only touch
        # complaints routed to THEIR center (this method used to skip it,
        # letting any manager resolve any center's complaints).
        if complaint.get("service_center_id"):
            ensure_own_center(actor_role, actor_center_id, complaint["service_center_id"])

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

    async def add_reply(
        self, complaint_id: str, actor_id: str, actor_role: str, actor_center_id: str | None, message: str, status: str | None = None
    ) -> dict:
        """A manager/admin's running insight on a complaint — "what I found,
        what I'm doing, what got resolved" — appended to an ongoing thread
        rather than overwriting a single resolution_note field, so the full
        history of what happened stays visible to everyone (including the
        customer) rather than only ever showing the latest note."""
        complaint = await self.repo.find_by_id(complaint_id)
        if not complaint:
            raise NotFoundException("Complaint not found")
        if actor_role == "customer":
            # Support is a CONVERSATION now — the customer can answer their
            # own thread (only their own; 404-not-403 so a guessed id
            # confirms nothing), but never change its status.
            if complaint.get("customer_id") != actor_id:
                raise NotFoundException("Complaint not found")
            status = None
        elif complaint.get("service_center_id"):
            ensure_own_center(actor_role, actor_center_id, complaint["service_center_id"])

        reply = {"author_id": actor_id, "author_role": actor_role, "message": message, "created_at": datetime.now(timezone.utc)}
        updated = await self.repo.push_to_array(complaint_id, "replies", reply)

        update_data: dict = {}
        if status and status != complaint.get("status"):
            update_data["status"] = status
            if status == ComplaintStatus.RESOLVED.value:
                update_data["resolved_by"] = actor_id
        if update_data:
            updated = await self.repo.update_by_id(complaint_id, update_data)

        if actor_role == "customer":
            # Tell the serving center's manager, not the customer themselves.
            center = await self.center_repo.find_by_id(complaint["service_center_id"]) if complaint.get("service_center_id") else None
            if center and center.get("manager_id"):
                await self.notifications.notify(
                    center["manager_id"],
                    "Customer replied on a complaint",
                    f"'{complaint['subject']}': {message[:120]}",
                    NotificationType.COMPLAINT,
                    complaint_id,
                )
        else:
            await self.notifications.notify(
                complaint["customer_id"],
                "Complaint update",
                f"Your complaint '{complaint['subject']}' has a new update.",
                NotificationType.COMPLAINT,
                complaint_id,
            )
        return serialize_doc(updated)
