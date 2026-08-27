from app.repositories.base_repository import BaseRepository


class CapacityPolicyRepository(BaseRepository):
    """One document per (service_center_id, effective_date) — see
    CapacityPolicyChangeModel for the full resolution/edit/history model
    this backs."""
    collection_name = "capacity_policy_changes"

    async def find_latest_effective(self, service_center_id: str, date_str: str) -> dict | None:
        """The policy currently in effect for this date — the most recent
        change whose effective_date is <= date_str."""
        return await self.collection.find_one(
            {"service_center_id": service_center_id, "effective_date": {"$lte": date_str}, "is_deleted": {"$ne": True}},
            sort=[("effective_date", -1)],
        )

    async def find_next_scheduled(self, service_center_id: str, date_str: str) -> dict | None:
        """The next FUTURE change for this center, if any — effective_date
        strictly after date_str, i.e. not yet active."""
        return await self.collection.find_one(
            {"service_center_id": service_center_id, "effective_date": {"$gt": date_str}, "is_deleted": {"$ne": True}},
            sort=[("effective_date", 1)],
        )

    async def find_for_date(self, service_center_id: str, effective_date: str) -> dict | None:
        return await self.find_one({"service_center_id": service_center_id, "effective_date": effective_date})

    async def list_history(self, service_center_id: str, limit: int = 50) -> list[dict]:
        cursor = self.collection.find({"service_center_id": service_center_id, "is_deleted": {"$ne": True}}).sort("effective_date", -1).limit(limit)
        return await cursor.to_list(length=limit)
