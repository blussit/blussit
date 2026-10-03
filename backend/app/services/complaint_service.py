from datetime import datetime, timezone

from bson import ObjectId
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
        self, service_center_id: str, status: str | None, page: int, page_size: int, actor_role: str, actor_center_id: str | None,
        search: str | None = None, extra: dict | None = None,
    ):
        ensure_own_center(actor_role, actor_center_id, service_center_id)
        items, total = await self.repo.list_for_center(
            service_center_id, status, page, page_size, {**(extra or {}), **await self.search_filter(search)},
        )
        return await self._enrich(items), total

    async def list_all(self, filters: dict, page: int, page_size: int, search: str | None = None):
        items, total = await self.repo.list_all({**filters, **await self.search_filter(search)}, page, page_size)
        return await self._enrich(items), total

    async def search_filter(self, search: str | None) -> dict:
        """Subject text, a booking number (BK...), or the customer's name /
        phone — the three things staff actually know when chasing a
        complaint. Customers are resolved through users first; complaints
        store only ids."""
        from app.repositories.base_repository import build_search_filter

        text = (search or "").strip()
        if not text:
            return {}
        ors: list[dict] = list(build_search_filter(text, ["subject"])["$or"])
        if text.upper().startswith("BK"):
            booking = await self.booking_repo.collection.find_one({"booking_number": text.upper()}, {"_id": 1})
            if booking:
                ors.append({"booking_id": str(booking["_id"])})
        digits = "".join(ch for ch in text if ch.isdigit())
        who = digits[2:] if len(digits) == 12 and digits.startswith("91") else text
        customers = await self.user_repo.collection.find(
            {"role": "customer", **build_search_filter(who, ["full_name", "phone"])}, {"_id": 1}
        ).limit(200).to_list(length=200)
        if customers:
            ors.append({"customer_id": {"$in": [str(u["_id"]) for u in customers]}})
        return {"$or": ors}

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
        self._ensure_staff_scope(complaint, actor_role, actor_center_id)

        data = {k: v.value if hasattr(v, "value") else v for k, v in payload.model_dump(exclude_unset=True).items() if v is not None}
        if data.get("status") == ComplaintStatus.RESOLVED.value:
            data["resolved_by"] = resolved_by
            data["resolved_at"] = datetime.now(timezone.utc)

        if not data:
            raise BadRequestException("Nothing to update — change the status, priority or note.")
        updated = await self.repo.update_by_id(complaint_id, data)
        # Re-prioritising is internal triage — only a change the customer
        # can see (status, resolution note) is worth a message to them.
        if set(data) - {"priority", "updated_at"}:
            await self.notifications.notify(
                complaint["customer_id"],
                "Complaint update",
                f"Your complaint '{complaint['subject']}' has been updated.",
                NotificationType.COMPLAINT,
                complaint_id,
            )
        return (await self._enrich([updated]))[0]

    _STATUS_LABELS = {
        ComplaintStatus.OPEN.value: "Open",
        ComplaintStatus.IN_PROGRESS.value: "In progress",
        ComplaintStatus.RESOLVED.value: "Resolved",
        ComplaintStatus.CLOSED.value: "Closed",
    }

    async def _ticket_handlers(self, complaint: dict) -> list[str]:
        """Who hears about a customer's reply: the ticket's center's
        managers (the center's own manager_id first), else the admins."""
        ids: list[str] = []
        center_id = complaint.get("service_center_id")
        if center_id:
            center = await self.center_repo.find_by_id(center_id)
            if center and center.get("manager_id"):
                ids.append(str(center["manager_id"]))
            managers, _ = await self.user_repo.find_many(
                {"role": "manager", "service_center_id": center_id, "status": {"$ne": "suspended"}}, page=1, page_size=20,
            )
            ids.extend(str(m["_id"]) for m in managers)
        if not ids:
            admins, _ = await self.user_repo.find_many({"role": "admin", "status": {"$ne": "suspended"}}, page=1, page_size=5)
            ids.extend(str(a["_id"]) for a in admins)
        return list(dict.fromkeys(ids))

    @staticmethod
    def _ensure_staff_scope(complaint: dict, actor_role: str, actor_center_id: str | None) -> None:
        """Admin: any ticket. Manager: only tickets routed to their own
        center — a legacy ticket with no center is admin-only, never
        "anyone's"."""
        if actor_role == "admin":
            return
        if actor_role != "manager" or not complaint.get("service_center_id"):
            raise ForbiddenException("You don't have access to this complaint")
        ensure_own_center(actor_role, actor_center_id, complaint["service_center_id"])

    async def get_one(self, complaint_id: str, actor_id: str, actor_role: str, actor_center_id: str | None) -> dict:
        """One ticket, fresh from the database, for whoever may see it: the
        customer who raised it (404 for anyone else's — a guessed id
        confirms nothing), a manager of its center, or an admin. The staff
        drawer reads the thread from HERE after every post instead of
        trusting a copy of a list row, so a second, third... update always
        shows up."""
        complaint = await self.repo.find_by_id(complaint_id)
        if not complaint:
            raise NotFoundException("Complaint not found")
        if actor_role == "customer":
            if complaint.get("customer_id") != actor_id:
                raise NotFoundException("Complaint not found")
        else:
            self._ensure_staff_scope(complaint, actor_role, actor_center_id)
        return (await self._enrich([complaint]))[0]

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
        else:
            self._ensure_staff_scope(complaint, actor_role, actor_center_id)

        message = (message or "").strip()
        status_changes = bool(status) and status != complaint.get("status")
        if not message and actor_role != "customer" and status_changes:
            # Staff may just move the status — the thread still records it,
            # in words the customer understands.
            message = f"Status changed to {self._STATUS_LABELS.get(status, status)}."
        if not message:
            raise BadRequestException("Write the update before posting it.")
        now = datetime.now(timezone.utc)
        author = await self.user_repo.collection.find_one({"_id": ObjectId(actor_id)}, {"full_name": 1}) if ObjectId.is_valid(actor_id) else None
        reply = {
            "author_id": actor_id,
            "author_role": actor_role,
            "author_name": (author or {}).get("full_name"),
            "message": message,
            "created_at": now,
        }

        set_data: dict = {"updated_at": now}
        if status and status != complaint.get("status"):
            set_data["status"] = status
            if status == ComplaintStatus.RESOLVED.value:
                set_data["resolved_by"] = actor_id
                set_data["resolved_at"] = now
        # A legacy ticket can carry `replies: null` (or no field at all) —
        # $push onto null is a write error, which would make every update
        # on that ticket fail. Normalise it first, then append the reply
        # and apply the status change in ONE atomic write.
        if not isinstance(complaint.get("replies"), list):
            await self.repo.collection.update_one(
                {"_id": ObjectId(complaint_id), "replies": {"$not": {"$type": "array"}}}, {"$set": {"replies": []}}
            )
        await self.repo.collection.update_one(
            {"_id": ObjectId(complaint_id), "is_deleted": {"$ne": True}},
            {"$push": {"replies": reply}, "$set": set_data},
        )
        updated = await self.repo.find_by_id(complaint_id)
        if not updated:
            raise NotFoundException("Complaint not found")

        if actor_role == "customer":
            # Tell the staff who handle it, not the customer themselves: the
            # serving center's managers — or, for a ticket with no center
            # (or a center with no manager), the admins, so a customer's
            # reply never lands in nobody's inbox.
            for staff_id in await self._ticket_handlers(complaint):
                await self.notifications.notify(
                    staff_id,
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
        return (await self._enrich([updated]))[0]
