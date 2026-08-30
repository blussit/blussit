"""
Coverage leads — people who tried to book from an area no service center
covers yet. The landing page captures their details instead of dead-ending
them, and admin gets a dedicated read-only view ("where is demand coming
from that we're not serving?") to guide expansion.

Deliberately NOT a per-request log: repeated interest from the same
phone+pincode upserts one document and bumps requests_count, so the admin
list reads as "N people in pincode X, this one asked 4 times" rather than
page after page of duplicates.
"""
from datetime import datetime, timezone

from motor.motor_asyncio import AsyncIOMotorDatabase

from app.utils.serializers import serialize_list


class CoverageLeadService:
    def __init__(self, db: AsyncIOMotorDatabase):
        self.collection = db["coverage_leads"]

    async def capture(self, name: str, phone: str, pincode: str, city_area: str | None = None, service_interest: str | None = None,
                      latitude: float | None = None, longitude: float | None = None) -> None:
        now = datetime.now(timezone.utc)
        update: dict = {
            "$set": {"name": name.strip(), "last_requested_at": now, "updated_at": now},
            "$setOnInsert": {"phone": phone, "pincode": pincode, "created_at": now, "is_deleted": False},
            "$inc": {"requests_count": 1},
        }
        # Optional context only overwrites when actually provided — a later
        # bare re-submit shouldn't blank out earlier detail.
        if city_area:
            update["$set"]["city_area"] = city_area.strip()
        if service_interest:
            update["$set"]["service_interest"] = service_interest.strip()
        if latitude is not None and longitude is not None:
            update["$set"]["latitude"] = latitude
            update["$set"]["longitude"] = longitude
        await self.collection.update_one({"phone": phone, "pincode": pincode}, update, upsert=True)

    async def list_for_admin(self, page: int, page_size: int) -> tuple[list[dict], int]:
        query: dict = {"is_deleted": {"$ne": True}}
        total = await self.collection.count_documents(query)
        cursor = self.collection.find(query).sort("last_requested_at", -1).skip(max(page - 1, 0) * page_size).limit(page_size)
        return serialize_list(await cursor.to_list(length=page_size)), total

    async def summary_for_admin(self) -> dict:
        """Headline numbers for the admin page: how many distinct people,
        and which pincodes have the most demand."""
        total_people = await self.collection.count_documents({"is_deleted": {"$ne": True}})
        top = await self.collection.aggregate([
            {"$match": {"is_deleted": {"$ne": True}}},
            {"$group": {"_id": "$pincode", "people": {"$sum": 1}, "requests": {"$sum": "$requests_count"}}},
            {"$sort": {"people": -1}},
            {"$limit": 5},
        ]).to_list(length=5)
        return {"total_people": total_people, "top_pincodes": [{"pincode": t["_id"], "people": t["people"], "requests": t["requests"]} for t in top]}
