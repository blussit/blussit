from datetime import datetime

from app.repositories.base_repository import BaseRepository


class CaptainLocationRepository(BaseRepository):
    collection_name = "captain_locations"

    async def record(self, captain_id: str, latitude: float, longitude: float, at: datetime,
                     source: str = "ping", booking_id: str | None = None, accuracy_m: float | None = None) -> None:
        """Fire-and-forget breadcrumb append — never let a trail write break
        the workflow action it rides on."""
        try:
            await self.collection.insert_one({
                "captain_id": captain_id,
                "booking_id": booking_id,
                "latitude": latitude,
                "longitude": longitude,
                "accuracy_m": accuracy_m,
                "source": source,
                "at": at,
            })
        except Exception:
            pass

    async def list_for_captain(self, captain_id: str, since: datetime | None = None, limit: int = 500) -> list[dict]:
        query: dict = {"captain_id": captain_id}
        if since is not None:
            query["at"] = {"$gte": since}
        cursor = self.collection.find(query).sort("at", 1).limit(limit)
        return await cursor.to_list(length=limit)
