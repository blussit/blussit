from motor.motor_asyncio import AsyncIOMotorDatabase

from app.core.exceptions import BadRequestException, ForbiddenException, NotFoundException
from app.repositories.staff_ops_repository import AttendanceRepository, LeaveRequestRepository
from app.repositories.user_repository import UserRepository
from app.schemas.staff_ops_schema import CheckInRequest, LeaveRequestCreate, LeaveReviewRequest
from app.utils.serializers import serialize_doc, serialize_list
from app.utils.timezone import now_ist


class AttendanceService:
    def __init__(self, db: AsyncIOMotorDatabase):
        self.repo = AttendanceRepository(db)
        self.user_repo = UserRepository(db)

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

        doc = {
            "captain_id": captain_id,
            "service_center_id": captain["service_center_id"],
            "attendance_date": today_str,
            "status": "present",
            "check_in_time": now_ist().isoformat(),
            "notes": payload.notes,
        }
        created = await self.repo.create(doc)
        return serialize_doc(created)

    async def check_out(self, captain_id: str) -> dict:
        today_str = now_ist().date().isoformat()
        existing = await self.repo.find_today(captain_id, today_str)
        if not existing:
            raise NotFoundException("No check-in found for today")
        updated = await self.repo.update_by_id(str(existing["_id"]), {"check_out_time": now_ist().isoformat()})
        return serialize_doc(updated)

    async def list_for_captain(self, captain_id: str, page: int, page_size: int):
        items, total = await self.repo.list_for_captain(captain_id, page, page_size)
        return serialize_list(items), total


class LeaveService:
    def __init__(self, db: AsyncIOMotorDatabase):
        self.repo = LeaveRequestRepository(db)
        self.user_repo = UserRepository(db)

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

    async def review(self, leave_id: str, payload: LeaveReviewRequest, reviewer_id: str) -> dict:
        leave = await self.repo.find_by_id(leave_id)
        if not leave:
            raise NotFoundException("Leave request not found")
        data = {"status": payload.status.value, "review_note": payload.review_note, "reviewed_by": reviewer_id}
        updated = await self.repo.update_by_id(leave_id, data)
        return serialize_doc(updated)
