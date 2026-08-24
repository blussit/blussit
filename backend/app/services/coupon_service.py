from motor.motor_asyncio import AsyncIOMotorDatabase

from app.core.exceptions import BadRequestException, ConflictException, NotFoundException
from app.models.enums import CouponType
from app.repositories.coupon_repository import CouponRepository, CouponUsageRepository
from app.schemas.coupon_schema import CouponCreateRequest, CouponUpdateRequest
from app.utils.serializers import serialize_doc, serialize_list
from app.utils.timezone import from_stored, now_ist


class CouponService:
    def __init__(self, db: AsyncIOMotorDatabase):
        self.repo = CouponRepository(db)
        self.usage_repo = CouponUsageRepository(db)

    async def list_all(self, page: int, page_size: int, active_only: bool = False):
        filters = {"is_active": True} if active_only else {}
        items, total = await self.repo.find_many(filters, page=page, page_size=page_size)
        return serialize_list(items), total

    async def create(self, payload: CouponCreateRequest) -> dict:
        if await self.repo.find_by_code(payload.code):
            raise ConflictException("A coupon with this code already exists")
        doc = payload.model_dump()
        doc["code"] = payload.code.upper()
        created = await self.repo.create(doc)
        return serialize_doc(created)

    async def update(self, coupon_id: str, payload: CouponUpdateRequest) -> dict:
        data = {k: v for k, v in payload.model_dump(exclude_unset=True).items() if v is not None}
        updated = await self.repo.update_by_id(coupon_id, data)
        if not updated:
            raise NotFoundException("Coupon not found")
        return serialize_doc(updated)

    async def delete(self, coupon_id: str) -> None:
        if not await self.repo.soft_delete(coupon_id):
            raise NotFoundException("Coupon not found")

    async def validate_and_compute_discount(self, code: str, order_value: float, user_id: str) -> tuple[dict, float]:
        coupon = await self.repo.find_by_code(code)
        if not coupon or not coupon.get("is_active"):
            raise BadRequestException("Invalid coupon code")

        now = now_ist()
        # valid_from/valid_until are aware-UTC-semantic (frontend sends real
        # "Z" timestamps), not naive user-entered IST digits — from_stored()
        # is the correct helper here, not to_ist(). See timezone.py.
        valid_from = from_stored(coupon["valid_from"])
        valid_until = from_stored(coupon["valid_until"])
        if now < valid_from or now > valid_until:
            raise BadRequestException("This coupon has expired or is not yet active")

        if order_value < coupon.get("min_order_value", 0):
            raise BadRequestException(f"Minimum order value of ₹{coupon['min_order_value']} required for this coupon")

        if coupon.get("total_usage_limit") and coupon.get("total_used", 0) >= coupon["total_usage_limit"]:
            raise BadRequestException("This coupon has reached its usage limit")

        user_usage_count = await self.usage_repo.count_for_user(str(coupon["_id"]), user_id)
        if user_usage_count >= coupon.get("usage_limit_per_user", 1):
            raise BadRequestException("You have already used this coupon the maximum number of times")

        if coupon["coupon_type"] == CouponType.PERCENTAGE.value:
            discount = order_value * (coupon["value"] / 100)
            if coupon.get("max_discount_amount"):
                discount = min(discount, coupon["max_discount_amount"])
        else:
            discount = coupon["value"]

        discount = min(discount, order_value)
        return coupon, round(discount, 2)

    async def record_usage(self, coupon_id: str, user_id: str, booking_id: str) -> None:
        """The counter bump is guarded by an atomic $expr condition on the
        SAME update — two concurrent bookings racing for a coupon's last
        remaining slot can no longer both read "still under the limit" and
        both win (a real check-then-act race the old plain $inc had). Order
        matters: bump the coupon counter first and only insert the usage
        record if that succeeds, so a losing race never leaves an orphaned
        usage doc behind. (The per-user limit isn't given the same atomic
        treatment — usage_limit_per_user can be >1, so there's no single
        counter field to guard the way total_usage_limit has; the race
        window there is a single customer double-submitting against their
        own limit, much narrower than many different customers racing a
        shared global cap.)"""
        result = await self.repo.collection.update_one(
            {
                "_id": self.repo._oid(coupon_id),
                "$expr": {"$or": [{"$eq": ["$total_usage_limit", None]}, {"$lt": ["$total_used", "$total_usage_limit"]}]},
            },
            {"$inc": {"total_used": 1}},
        )
        if result.matched_count == 0:
            raise BadRequestException("This coupon has just reached its usage limit — please try a different code.")
        await self.usage_repo.create({"coupon_id": coupon_id, "user_id": user_id, "booking_id": booking_id})

    async def reverse_usage(self, coupon_code: str, user_id: str, booking_id: str) -> None:
        """Undoes record_usage — called when a coupon-paid booking is
        cancelled, so the coupon's capacity and the customer's own
        per-user allowance aren't permanently burned by a booking that
        never actually happened. Safe to call even if no usage row exists
        (e.g. the booking predates this fix, or usage was never recorded)."""
        coupon = await self.repo.find_by_code(coupon_code)
        if not coupon:
            return
        deleted = await self.usage_repo.collection.delete_one({"coupon_id": str(coupon["_id"]), "user_id": user_id, "booking_id": booking_id})
        if deleted.deleted_count:
            await self.repo.collection.update_one({"_id": coupon["_id"]}, {"$inc": {"total_used": -1}})
