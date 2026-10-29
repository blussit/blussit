from bson import ObjectId
from pymongo.errors import DuplicateKeyError

from app.repositories.base_repository import BaseRepository
from app.services.coupon_rules import RULE_FIELDS
from app.utils.coupon_codes import normalise_code


class CouponRepository(BaseRepository):
    collection_name = "coupons"

    async def find_by_code(self, code: str) -> dict | None:
        """The coupon behind a code: a regular coupon first, else a bundle
        code (see effective_bundle_coupon). Callers get one shape either
        way, `_id` being whatever record_usage must be handed back."""
        normal = normalise_code(code)
        found = await self.find_one({"code": normal})
        if found or not normal:
            return found
        return await CouponCodeRepository(self.db).find_effective(normal)


class CouponBundleRepository(BaseRepository):
    """One document per imported bundle: the benefit, expiry and per-code
    allowance every code in it shares. Editing a bundle is one write here —
    its codes only point back (coupon_codes.bundle_id)."""

    collection_name = "coupon_bundles"


def effective_bundle_coupon(bundle: dict, code_doc: dict) -> dict:
    """A bundle code as the coupon dict CouponService already understands:
    the bundle's benefit + the code's own used count. The bundle's expiry is
    `valid_until` (None = never); a switched-off or deleted bundle or code
    reads as inactive."""
    return {
        "_id": code_doc["_id"],
        "code": code_doc["code"],
        "_bundle_id": str(bundle["_id"]),
        "bundle_name": bundle.get("name"),
        "description": bundle.get("description"),
        "coupon_type": bundle.get("coupon_type"),
        "value": bundle.get("value", 0),
        "min_order_value": bundle.get("min_order_value", 0) or 0,
        "max_discount_amount": bundle.get("max_discount_amount"),
        "offer_kind": bundle.get("offer_kind", "standard"),
        "eligible_service_keywords": bundle.get("eligible_service_keywords") or [],
        "free_addon_keywords": bundle.get("free_addon_keywords") or [],
        "usage_limit_per_user": bundle.get("usage_limit_per_user", 1),
        "total_usage_limit": int(bundle.get("uses_per_coupon") or 1),
        "total_used": int(code_doc.get("used_count") or 0),
        "valid_from": bundle.get("valid_from"),
        "valid_until": bundle.get("valid_until"),
        "is_active": bool(bundle.get("is_active", True)) and not bundle.get("is_deleted") and bool(code_doc.get("is_active", True)),
        # The rules every code of the bundle shares (services/coupon_rules.py).
        **{k: bundle[k] for k in RULE_FIELDS if k in bundle},
    }


class CouponCodeRepository(BaseRepository):
    """The individual single-use codes of bundles. `code` is globally unique
    (unique index) and stored normalised; `used_count` is the only thing a
    redemption touches (a guarded $inc — see CouponService.record_usage)."""

    collection_name = "coupon_codes"

    async def find_effective(self, code: str) -> dict | None:
        code_doc = await self.collection.find_one({"code": code})
        if not code_doc:
            return None
        bundle = await self.db["coupon_bundles"].find_one({"_id": ObjectId(code_doc["bundle_id"])})
        if not bundle:
            return None
        return effective_bundle_coupon(bundle, code_doc)

    async def find_effective_by_id(self, code_id: str) -> dict | None:
        code_doc = await self.find_by_id(code_id)
        if not code_doc:
            return None
        bundle = await self.db["coupon_bundles"].find_one({"_id": ObjectId(code_doc["bundle_id"])})
        return effective_bundle_coupon(bundle, code_doc) if bundle else None


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
