"""
Custom-plan enquiries — "none of the standard monthly passes fit us".

A fleet owner with nine cars, or someone who wants three washes a week at
7am, can't be served by the two-question pass flow, so instead of
dead-ending them the site captures what they need and the team follows up
by hand (founder ask: how many cars, which services, how often, when).

Same shape as CoverageLeadService deliberately: an upsert keyed on the
phone number, so somebody submitting three times is one row that says
"asked 3 times" rather than three rows to triage.
"""
from datetime import datetime, timezone

from motor.motor_asyncio import AsyncIOMotorDatabase

from app.utils.serializers import serialize_list


class PlanEnquiryService:
    def __init__(self, db: AsyncIOMotorDatabase):
        self.collection = db["plan_enquiries"]

    async def capture(
        self,
        name: str,
        phone: str,
        vehicle_count: int,
        services_wanted: str,
        washes_per_month: int | None = None,
        preferred_time: str | None = None,
        notes: str | None = None,
        customer_id: str | None = None,
    ) -> None:
        now = datetime.now(timezone.utc)
        update: dict = {
            "$set": {
                "name": name.strip(),
                "vehicle_count": vehicle_count,
                "services_wanted": services_wanted.strip(),
                "last_requested_at": now,
                "updated_at": now,
            },
            "$setOnInsert": {"phone": phone, "status": "new", "created_at": now, "is_deleted": False},
            "$inc": {"requests_count": 1},
        }
        # Optional context only overwrites when actually supplied — a later
        # bare re-submit must not blank out detail we already have.
        if washes_per_month is not None:
            update["$set"]["washes_per_month"] = washes_per_month
        if preferred_time:
            update["$set"]["preferred_time"] = preferred_time.strip()
        if notes:
            update["$set"]["notes"] = notes.strip()
        if customer_id:
            update["$set"]["customer_id"] = customer_id
        await self.collection.update_one({"phone": phone}, update, upsert=True)

    async def list_for_admin(self, page: int, page_size: int) -> tuple[list[dict], int]:
        query: dict = {"is_deleted": {"$ne": True}}
        total = await self.collection.count_documents(query)
        cursor = (
            self.collection.find(query)
            .sort("last_requested_at", -1)
            .skip(max(page - 1, 0) * page_size)
            .limit(page_size)
        )
        return serialize_list(await cursor.to_list(length=page_size)), total

    async def set_status(self, enquiry_id: str, status: str) -> bool:
        """Triage marker for the admin list — new / contacted / closed."""
        from bson import ObjectId

        if status not in {"new", "contacted", "closed"} or not ObjectId.is_valid(enquiry_id):
            return False
        result = await self.collection.update_one(
            {"_id": ObjectId(enquiry_id)},
            {"$set": {"status": status, "updated_at": datetime.now(timezone.utc)}},
        )
        return result.matched_count > 0
