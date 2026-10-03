from bson import ObjectId
from motor.motor_asyncio import AsyncIOMotorDatabase
from pymongo.errors import DuplicateKeyError

from app.core.exceptions import BadRequestException, ConflictException, NotFoundException
from app.repositories.user_repository import UserRepository
from app.schemas.user_schema import AdminUserUpdateRequest, UserPublic, UserUpdateRequest
from app.utils.phone import BUSINESS_NUMBER_MESSAGE, is_business_whatsapp_number


class UserService:
    def __init__(self, db: AsyncIOMotorDatabase):
        self.db = db
        self.repo = UserRepository(db)

    async def get_by_id(self, user_id: str) -> dict:
        user = await self.repo.find_by_id(user_id)
        if not user:
            raise NotFoundException("User not found")
        return UserPublic.from_doc(user).model_dump()

    async def update_profile(self, user_id: str, payload: UserUpdateRequest) -> dict:
        data = {k: v for k, v in payload.model_dump(exclude_unset=True).items() if v is not None}
        updated = await self.repo.update_by_id(user_id, data)
        if not updated:
            raise NotFoundException("User not found")
        return UserPublic.from_doc(updated).model_dump()

    async def list_users(
        self, role: str | None, page: int, page_size: int, search: str | None,
        period: str | None = None, start: str | None = None, end: str | None = None,
    ):
        filters = {"role": role} if role else {}
        if search:
            from app.repositories.base_repository import build_search_filter
            filters.update(build_search_filter(search, ["full_name", "email", "phone"]))
        if period or (start and end):
            from app.services.kpi_service import resolve_period

            s, e, _ps, _pe = resolve_period(period, start, end)
            filters["created_at"] = {"$gte": s, "$lt": e}
        items, total = await self.repo.find_many(filters, page=page, page_size=page_size)
        return [UserPublic.from_doc(u).model_dump() for u in items], total

    async def ensure_not_last_admin(self, user_id: str) -> None:
        """Refuses to suspend / demote / delete the only active admin — the
        platform would be left with nobody able to manage it."""
        target = await self.repo.find_by_id(user_id)
        if not target or target.get("role") != "admin":
            return
        others = await self.db.users.count_documents({
            "role": "admin", "status": {"$ne": "suspended"}, "is_deleted": {"$ne": True}, "_id": {"$ne": target["_id"]},
        })
        if not others:
            raise BadRequestException("This is the only active admin account — add another admin first.")

    async def admin_update_user(self, user_id: str, payload: AdminUserUpdateRequest) -> dict:
        # exclude_unset (not `is not None`) is what lets a manager's
        # service_center_id be explicitly CLEARED back to null — dropping
        # `is not None` here used to silently ignore that, the one field on
        # this form that legitimately needs to go from "set" back to "unset".
        data = {k: v.value if hasattr(v, "value") else v for k, v in payload.model_dump(exclude_unset=True).items()}
        if "phone" in data:
            phone = data.pop("phone")
            current = await self.repo.find_by_id(user_id)
            if not current:
                raise NotFoundException("User not found")
            if phone and phone != current.get("phone"):
                if is_business_whatsapp_number(phone):
                    raise BadRequestException(BUSINESS_NUMBER_MESSAGE)
                taken = await self.repo.find_by_phone(phone)
                if taken and str(taken["_id"]) != user_id:
                    raise ConflictException("An account with this phone number already exists")
                # Set by an admin, not proven by OTP — the owner re-verifies.
                data.update({"phone": phone, "phone_verified": False, "phone_verified_at": None})
        if data.get("service_center_id"):
            center_id = data["service_center_id"]
            if not ObjectId.is_valid(center_id) or not await self.db.service_centers.find_one(
                {"_id": ObjectId(center_id), "is_deleted": {"$ne": True}}, {"_id": 1}
            ):
                raise BadRequestException("That service center doesn't exist.")
        before = await self.repo.find_by_id(user_id)
        if not before:
            raise NotFoundException("User not found")
        try:
            updated = await self.repo.update_by_id(user_id, data)
        except DuplicateKeyError:
            raise ConflictException("An account with this phone number already exists")
        if not updated:
            raise NotFoundException("User not found")
        # A change of standing (suspend / reactivate) ends every session
        # minted before it, refresh tokens included — a reactivated account
        # signs in fresh. Role / center changes need no bump: every access
        # token is re-checked against the DB row (get_current_user refuses a
        # role/center claim that no longer matches) and the refresh that
        # follows mints the new scope, so permissions are always current.
        if "status" in data and data["status"] != before.get("status"):
            # Who suspended decides who may lift it: an admin's suspension
            # can't be undone by a center manager (staff_directory_routes).
            standing = {"suspended_by_role": "admin"} if data["status"] == "suspended" else {}
            update: dict = {"$inc": {"token_version": 1}}
            if standing:
                update["$set"] = standing
            else:
                update["$unset"] = {"suspended_by_role": ""}
            await self.db.users.update_one({"_id": updated["_id"]}, update)
        return UserPublic.from_doc(updated).model_dump()

    async def deactivate_user(self, user_id: str) -> dict:
        updated = await self.repo.update_by_id(user_id, {"status": "suspended"})
        if not updated:
            raise NotFoundException("User not found")
        # Every refresh token dies now; a later reactivation needs a fresh login.
        # Admin-only route — recorded so a manager can't lift it.
        await self.db.users.update_one(
            {"_id": updated["_id"]}, {"$inc": {"token_version": 1}, "$set": {"suspended_by_role": "admin"}},
        )
        return UserPublic.from_doc(updated).model_dump()

    async def delete_user(self, user_id: str) -> bool:
        """PERMANENTLY removes the account's row from the database — a real
        delete, not the soft is_deleted flag most other records use. An
        admin clicking Delete on a person's account means gone, not merely
        hidden from lists while every field sits in Mongo forever.

        Refuses while the user is tied to live work (their bookings would
        otherwise sit orphaned in the manager queue / captain job list with
        a null name) — everything else about them (completed/cancelled
        booking history, reviews, etc.) is left as historical record
        pointing at an id that no longer resolves, exactly as it would for
        any other "the person left" scenario; only the account itself, and
        whatever it can authenticate as, actually disappears. No
        token_version bump is needed: with the row gone, both the auth
        dependency and /auth/refresh's own user lookup fail closed on
        their own (refresh explicitly raises "User no longer exists")."""
        user = await self.repo.find_by_id(user_id)
        if not user:
            return False
        active_field = "captain_id" if user.get("role") == "captain" else "customer_id"
        active = await self.db.bookings.count_documents({
            active_field: user_id,
            "status": {"$in": ["pending", "assigned", "captain_on_the_way", "service_started", "rescheduled"]},
            "is_deleted": {"$ne": True},
        })
        if active:
            raise BadRequestException(
                f"This account still has {active} active booking(s) — cancel or complete them before deleting the account."
            )
        return await self.repo.hard_delete(user_id)
