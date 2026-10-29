from app.core.ws_manager import manager as ws_manager
from app.repositories.base_repository import BaseRepository


class SubscriptionPlanRepository(BaseRepository):
    collection_name = "subscription_plans"


class UserSubscriptionRepository(BaseRepository):
    collection_name = "user_subscriptions"

    async def _ping(self, doc: dict | None, session) -> None:
        """A plan was bought, used, renewed, extended or ended: the owner's
        plans / dashboard and that center's manager see it at once. One
        funnel — every service write goes through create / update_by_id /
        update_if. A write inside the caller's transaction is not announced
        (it isn't committed yet; the caller's own booking / payment ping
        covers it)."""
        if session is None and doc and doc.get("customer_id"):
            await ws_manager.notify_changed("plan", users=[doc["customer_id"]], center_id=doc.get("service_center_id"))

    async def create(self, data: dict, session=None) -> dict:
        created = await super().create(data, session=session)
        await self._ping(created, session)
        return created

    async def update_by_id(self, id: str, data: dict, session=None) -> dict | None:
        updated = await super().update_by_id(id, data, session=session)
        await self._ping(updated, session)
        return updated

    async def update_if(self, id: str, guard_filter: dict, data: dict, session=None) -> dict | None:
        updated = await super().update_if(id, guard_filter, data, session=session)
        await self._ping(updated, session)
        return updated

    async def list_for_customer(self, customer_id: str, status: str | None = None, limit: int | None = None) -> list[dict]:
        """Newest first (customer_id + created_at index)."""
        filters: dict = {"customer_id": customer_id}
        if status:
            filters["status"] = status
        return await self.find_all_no_paginate(filters, limit=limit)

    async def find_active_for_customer(self, customer_id: str) -> list[dict]:
        return await self.find_all_no_paginate({"customer_id": customer_id, "status": "active"})
