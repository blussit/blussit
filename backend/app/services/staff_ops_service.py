from datetime import datetime

from motor.motor_asyncio import AsyncIOMotorDatabase
from pymongo.errors import DuplicateKeyError

from app.core.authz import ensure_own_center
from app.core.exceptions import BadRequestException, ForbiddenException, NotFoundException
from app.repositories.captain_location_repository import CaptainLocationRepository
from app.repositories.staff_ops_repository import AttendanceRepository, LeaveRequestRepository
from app.repositories.user_repository import UserRepository
from app.services.notification_service import NotificationService
from app.schemas.staff_ops_schema import CheckInRequest, CheckOutRequest, LeaveRequestCreate, LeaveReviewRequest
from app.utils.serializers import serialize_doc, serialize_list
from app.utils.timezone import now_ist


class AttendanceService:
    def __init__(self, db: AsyncIOMotorDatabase):
        self.repo = AttendanceRepository(db)
        self.user_repo = UserRepository(db)
        self.location_repo = CaptainLocationRepository(db)

    async def check_in(self, captain_id: str, payload: CheckInRequest) -> dict:
        captain = await self.user_repo.find_by_id(captain_id)
        if not captain or not captain.get("service_center_id"):
            raise BadRequestException("Captain is not assigned to a service center")

        # "Today" means the Indian business day, not the server's (usually UTC)
        # local date — otherwise a captain checking in near midnight IST could
        # get the wrong attendance_date entirely.
        today_str = now_ist().date().isoformat()
        existing = await self.repo.find_today(captain_id, today_str)
        if existing:
            raise BadRequestException("You have already checked in today")

        now = now_ist()
        doc = {
            "captain_id": captain_id,
            "service_center_id": captain["service_center_id"],
            "attendance_date": today_str,
            "status": "present",
            "check_in_time": now.isoformat(),
            # Recorded wherever the captain is — deliberately no center
            # geofence (a day may start at the first booking, not the hub);
            # the manager just sees the pin.
            "check_in_location": self._loc(payload),
            "notes": payload.notes,
        }
        try:
            created = await self.repo.create(doc)
        except DuplicateKeyError:
            # Double-tap race caught by the unique (captain_id,
            # attendance_date) index — same outcome as the find_today check.
            raise BadRequestException("You have already checked in today")
        if payload.latitude is not None and payload.longitude is not None:
            await self.location_repo.record(captain_id, payload.latitude, payload.longitude, now, source="check_in")
        return serialize_doc(created)

    async def check_out(self, captain_id: str, payload: CheckOutRequest | None = None) -> dict:
        today_str = now_ist().date().isoformat()
        existing = await self.repo.find_today(captain_id, today_str)
        if not existing:
            raise NotFoundException("No check-in found for today")
        now = now_ist()
        update_data: dict = {"check_out_time": now.isoformat(), "check_out_location": self._loc(payload)}
        # Hours worked, from the stored ISO check-in string (already IST).
        check_in_time = existing.get("check_in_time")
        if check_in_time:
            try:
                worked = (now - datetime.fromisoformat(check_in_time)).total_seconds() / 60
                update_data["worked_minutes"] = max(0, round(worked))
            except ValueError:
                pass
        updated = await self.repo.update_by_id(str(existing["_id"]), update_data)
        if payload and payload.latitude is not None and payload.longitude is not None:
            await self.location_repo.record(captain_id, payload.latitude, payload.longitude, now, source="check_out")
        return serialize_doc(updated)

    @staticmethod
    def _loc(payload) -> dict | None:
        if payload is None or payload.latitude is None or payload.longitude is None:
            return None
        return {"latitude": payload.latitude, "longitude": payload.longitude}

    async def list_for_captain(self, captain_id: str, page: int, page_size: int):
        items, total = await self.repo.list_for_captain(captain_id, page, page_size)
        return serialize_list(items), total


class LeaveService:
    def __init__(self, db: AsyncIOMotorDatabase):
        self.repo = LeaveRequestRepository(db)
        self.user_repo = UserRepository(db)
        self.notifications = NotificationService(db)

    async def request_leave(self, captain_id: str, payload: LeaveRequestCreate) -> dict:
        doc = payload.model_dump()
        doc["captain_id"] = captain_id
        doc["start_date"] = doc["start_date"].isoformat()
        doc["end_date"] = doc["end_date"].isoformat()
        created = await self.repo.create(doc)
        return serialize_doc(created)

    async def list_for_captain(self, captain_id: str, page: int, page_size: int):
        items, total = await self.repo.list_for_captain(captain_id, page, page_size)
        return serialize_list(items), total

    async def list_pending_for_center(self, service_center_id: str, page: int, page_size: int):
        captains, _ = await self.user_repo.list_by_role("captain", 1, 1000, extra_filters={"service_center_id": service_center_id})
        captain_ids = [str(c["_id"]) for c in captains]
        items, total = await self.repo.list_for_center_pending(captain_ids, page, page_size)
        return serialize_list(items), total

    async def review(self, leave_id: str, payload: LeaveReviewRequest, reviewer_id: str,
                     actor_role: str = "admin", actor_center_id: str | None = None) -> dict:
        leave = await self.repo.find_by_id(leave_id)
        if not leave:
            raise NotFoundException("Leave request not found")
        # A manager may only review THEIR OWN captains' leave — resolve the
        # captain and center-scope, exactly like list_pending_for_center.
        captain = await self.user_repo.find_by_id(leave["captain_id"])
        if captain and captain.get("service_center_id"):
            ensure_own_center(actor_role, actor_center_id, captain["service_center_id"])
        # Claim the pending row atomically so two reviewers can't both win.
        data = {"status": payload.status.value, "review_note": payload.review_note, "reviewed_by": reviewer_id}
        updated = await self.repo.update_if(leave_id, {"status": "pending"}, data)
        if updated is None:
            raise BadRequestException("This leave request has already been reviewed")
        # Tell the captain — filing a request that silently changes state
        # helps no one.
        label = "approved" if payload.status.value == "approved" else "rejected"
        await self.notifications.notify(
            leave["captain_id"],
            f"Leave request {label}",
            payload.review_note or f"Your leave from {leave['start_date']} to {leave['end_date']} was {label}.",
        )
        return serialize_doc(updated)
