from pymongo.errors import DuplicateKeyError

from app.repositories.base_repository import BaseRepository


class CouponRepository(BaseRepository):
    collection_name = "coupons"

    async def find_by_code(self, code: str) -> dict | None:
        return await self.find_one({"code": code.upper()})


class CouponUsageRepository(BaseRepository):
    collection_name = "coupon_usages"

    async def count_for_user(self, coupon_id: str, user_id: str) -> int:
        return await self.collection.count_documents({"coupon_id": coupon_id, "user_id": user_id})


class CouponUserCounterRepository(BaseRepository):
    """One counter doc per (coupon, customer) — `used` is how many of the
    customer's own allowance (usage_limit_per_user) is spent. Keyed on _id,
    so the claim below is atomic without any extra index: two concurrent
    redemptions can't both read "still under the limit" and both win, which
    counting coupon_usages rows and then inserting one could."""

    collection_name = "coupon_user_usage"

    @staticmethod
    def _key(coupon_id: str, user_id: str) -> str:
        return f"{coupon_id}:{user_id}"

    async def claim(self, coupon_id: str, user_id: str, limit: int, recorded_uses) -> bool:
        """Spend one of this customer's uses; False when none is left.
        `recorded_uses()` seeds a missing counter from the uses recorded
        before counters existed (only called the first time)."""
        key = self._key(coupon_id, user_id)
        if not await self.collection.find_one({"_id": key}, {"_id": 1}):
            try:
                await self.collection.insert_one({"_id": key, "coupon_id": coupon_id, "user_id": user_id, "used": await recorded_uses()})
            except DuplicateKeyError:
                pass  # a concurrent first use created it — the guarded $inc below decides
        result = await self.collection.update_one({"_id": key, "used": {"$lt": limit}}, {"$inc": {"used": 1}})
        return result.modified_count == 1

    async def release(self, coupon_id: str, user_id: str) -> None:
        await self.collection.update_one({"_id": self._key(coupon_id, user_id), "used": {"$gt": 0}}, {"$inc": {"used": -1}})
