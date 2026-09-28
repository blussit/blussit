"""
Website visitor KPI — one row per device per IST calendar day in
`site_visits`. The browser keeps a random device id and pings once per
page load; the (date, device_id) unique index makes repeat pings the same
day no-ops, so a count of rows over a date range IS "unique device-days".
"""
from datetime import datetime, timedelta

from motor.motor_asyncio import AsyncIOMotorDatabase
from pymongo.errors import DuplicateKeyError

from app.utils.timezone import now_ist


def _day(value: datetime) -> str:
    return value.strftime("%Y-%m-%d")


class SiteVisitService:
    def __init__(self, db: AsyncIOMotorDatabase):
        self.collection = db.site_visits

    async def record(self, device_id: str) -> bool:
        """True only for the device's first ping of the IST day."""
        now = now_ist()
        today = _day(now)
        try:
            result = await self.collection.update_one(
                {"date": today, "device_id": device_id},
                {"$setOnInsert": {"device_id": device_id, "date": today, "created_at": now}},
                upsert=True,
            )
        except DuplicateKeyError:
            return False  # a concurrent first ping won the insert
        return result.upserted_id is not None

    async def stats(self, s: datetime, e: datetime, ps: datetime, pe: datetime) -> dict:
        """Range bounds are resolve_period's aware-IST midnights (end
        exclusive); `date` strings compare correctly as YYYY-MM-DD."""
        rows = await self.collection.aggregate([
            {"$match": {"date": {"$gte": _day(s), "$lt": _day(e)}}},
            {"$group": {"_id": "$date", "visitors": {"$sum": 1}}},
        ]).to_list(length=None)
        per_day = {r["_id"]: r["visitors"] for r in rows}
        daily = []
        day = s
        while day < e:
            key = _day(day)
            daily.append({"date": key, "visitors": per_day.get(key, 0)})
            day += timedelta(days=1)
        return {
            "total": sum(per_day.values()),
            "today": await self.collection.count_documents({"date": _day(now_ist())}),
            "previous_total": await self.collection.count_documents({"date": {"$gte": _day(ps), "$lt": _day(pe)}}),
            "daily": daily,
        }
