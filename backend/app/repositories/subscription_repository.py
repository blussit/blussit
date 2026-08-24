from app.repositories.base_repository import BaseRepository


class SubscriptionPlanRepository(BaseRepository):
    collection_name = "subscription_plans"


class UserSubscriptionRepository(BaseRepository):
    collection_name = "user_subscriptions"

    async def list_for_customer(self, customer_id: str, status: str | None = None) -> list[dict]:
        filters: dict = {"customer_id": customer_id}
        if status:
            filters["status"] = status
        return await self.find_all_no_paginate(filters)

    async def find_active_for_customer(self, customer_id: str) -> list[dict]:
        return await self.find_all_no_paginate({"customer_id": customer_id, "status": "active"})

    async def list_all(self, page: int, page_size: int):
        return await self.find_many(None, page=page, page_size=page_size)
