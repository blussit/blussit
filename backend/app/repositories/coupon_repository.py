from app.repositories.base_repository import BaseRepository


class CouponRepository(BaseRepository):
    collection_name = "coupons"

    async def find_by_code(self, code: str) -> dict | None:
        return await self.find_one({"code": code.upper()})


class CouponUsageRepository(BaseRepository):
    collection_name = "coupon_usages"

    async def count_for_user(self, coupon_id: str, user_id: str) -> int:
        return await self.collection.count_documents({"coupon_id": coupon_id, "user_id": user_id})
