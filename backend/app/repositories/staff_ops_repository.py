from app.repositories.base_repository import BaseRepository


class AttendanceRepository(BaseRepository):
    collection_name = "attendance"

    async def find_today(self, captain_id: str, today_str: str):
        return await self.find_one({"captain_id": captain_id, "attendance_date": today_str})

    async def list_for_captain(self, captain_id: str, page: int, page_size: int):
        return await self.find_many({"captain_id": captain_id}, page=page, page_size=page_size, sort_by="attendance_date", sort_order=-1)


class LeaveRequestRepository(BaseRepository):
    collection_name = "leave_requests"

    async def list_for_captain(self, captain_id: str, page: int, page_size: int):
        return await self.find_many({"captain_id": captain_id}, page=page, page_size=page_size)

    async def list_for_center_pending(self, captain_ids: list[str], page: int, page_size: int):
        return await self.find_many({"captain_id": {"$in": captain_ids}, "status": "pending"}, page=page, page_size=page_size)
