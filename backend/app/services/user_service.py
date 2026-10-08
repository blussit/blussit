from datetime import datetime, timedelta, timezone

from bson import ObjectId
from motor.motor_asyncio import AsyncIOMotorDatabase
from pymongo.errors import DuplicateKeyError

from app.core.authz import account_switched_off
from app.core.exceptions import BadRequestException, ConflictException, NotFoundException
from app.repositories.booking_repository import BookingRepository
from app.repositories.user_repository import UserRepository
from app.schemas.user_schema import AdminUserUpdateRequest, UserPublic, UserUpdateRequest
# Every booking state still owed work or money — what an account delete
# must never orphan (ADM-03); one definition, shared with the catalogue.
from app.services.catalog_service import LIVE_BOOKING_STATUSES as _LIVE_BOOKING_STATUSES
from app.utils.phone import BUSINESS_NUMBER_MESSAGE, is_business_whatsapp_number
from app.utils.timezone import now_ist

# The bookings a captain is holding right now (CAP-01/05): a change that
# takes him off his center's roster — another center, another role,
# suspended / inactive, deleted — would strand these with someone who can't
# (or shouldn't) work them, and his old manager could no longer see him.
CAPTAIN_LIVE_JOB_STATUSES = list(BookingRepository.CAPTAIN_ACTIVE_STATUSES)
# A payment order / link is still payable for this long (links live 7 days,
# PaymentService._LINK_VALID_FOR, and the sweeps watch them a day longer).
_OPEN_ORDER_HORIZON = timedelta(days=8)


def roster_change(user: dict, data: dict) -> str | None:
    """What `data` would do to this user that takes a CAPTAIN off his
    center's roster, as the verb phrase the refusal uses — None for any
    other edit (a rename, a phone fix) and for every non-captain."""
    if (user or {}).get("role") != "captain":
        return None
    if "role" in data and data["role"] not in (None, "captain"):
        return "change their role"
    if "service_center_id" in data and data["service_center_id"] != user.get("service_center_id"):
        return "move them to another center"
    status = data.get("status")
    if status and status != user.get("status") and account_switched_off({"status": status}):
        return "suspend them" if status == "suspended" else "set them inactive"
    return None


def _numbers(rows: list[dict], limit: int = 8) -> str:
    names = [r.get("booking_number") or str(r["_id"]) for r in rows[:limit]]
    more = len(rows) - limit
    return ", ".join(names) + (f" and {more} more" if more > 0 else "")


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
        # An inactive admin can't sign in any more than a suspended one
        # (authz.account_switched_off) — neither counts as "another admin".
        others = await self.db.users.count_documents({
            "role": "admin", "status": {"$nin": ["suspended", "inactive"]}, "is_deleted": {"$ne": True}, "_id": {"$ne": target["_id"]},
        })
        if not others:
            raise BadRequestException("This is the only active admin account — add another admin first.")

    async def _captain_live_work(self, captain_id: str, session=None) -> tuple[list[dict], list[dict]]:
        """(live bookings he holds, societies he is the standing captain of)."""
        jobs = await self.db.bookings.find(
            {"captain_id": captain_id, "status": {"$in": CAPTAIN_LIVE_JOB_STATUSES}, "is_deleted": {"$ne": True}},
            {"booking_number": 1}, session=session,
        ).sort("scheduled_date", 1).to_list(length=50)
        today = now_ist().date().isoformat()
        societies = await self.db.societies.find(
            {
                "is_deleted": {"$ne": True}, "is_active": {"$ne": False},
                "$or": [{"daily_captain_id": captain_id}, {"substitute.captain_id": captain_id, "substitute.date": {"$gte": today}}],
            },
            {"name": 1}, session=session,
        ).to_list(length=20)
        return jobs, societies

    async def ensure_captain_can_leave_roster(self, user: dict, data: dict, *, action: str | None = None, session=None) -> None:
        """THE one check every path that changes a captain's center, role or
        standing runs (admin edit, admin suspend, the manager's captain
        status switch, account delete): refused while he holds live jobs or
        is a society's standing captain — the error names them, so the
        manager reassigns first. `action` overrides the verb phrase (delete).
        No-op for non-captains and harmless edits."""
        action = action or roster_change(user, data)
        if not action or (user or {}).get("role") != "captain":
            return
        jobs, societies = await self._captain_live_work(str(user["_id"]), session=session)
        problems = []
        if jobs:
            problems.append(f"{len(jobs)} live job(s) — {_numbers(jobs)}")
        if societies:
            problems.append("is the society captain for " + ", ".join(s.get("name") or str(s["_id"]) for s in societies))
        if problems:
            raise BadRequestException(
                f"This captain still has {'; and '.join(problems)}. Reassign them to another captain before you {action}."
            )

    async def _write_guarded(self, user: dict, data: dict, extra_update: dict | None = None) -> dict | None:
        """Writes `data` (and an optional raw update — the token bump) to the
        user. When the edit takes a captain off his roster, the live-work
        check runs in the SAME transaction as the write, after it: the
        write to the captain's row conflicts with a concurrent assignment
        (assign_captain writes that row inside its own transaction), so one
        of the two retries and sees the other — a job can't slip onto him
        between the check and the change."""
        user_id = str(user["_id"])
        if not roster_change(user, data):
            updated = await self.repo.update_by_id(user_id, data)
            if updated is not None and extra_update:
                await self.db.users.update_one({"_id": user["_id"]}, extra_update)
            return updated

        async def _do(session):
            updated = await self.repo.update_by_id(user_id, data, session=session)
            if updated is not None and extra_update:
                await self.db.users.update_one({"_id": user["_id"]}, extra_update, session=session)
            await self.ensure_captain_can_leave_roster(user, data, session=session)
            return updated

        async with await self.db.client.start_session() as session:
            return await session.with_transaction(_do)

    async def set_captain_status(self, captain: dict, status: str, actor_role: str) -> dict | None:
        """The manager's (or admin's) captain on/off switch. Suspending ends
        every session and records who suspended — an admin's suspension is
        the admin's to lift (staff_directory_routes)."""
        if status == "suspended":
            standing = {"$inc": {"token_version": 1}, "$set": {"suspended_by_role": actor_role}}
        else:
            standing = {"$unset": {"suspended_by_role": ""}}
        return await self._write_guarded(captain, {"status": status}, standing)

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
        # A change of standing (suspend / reactivate) ends every session
        # minted before it, refresh tokens included — a reactivated account
        # signs in fresh. Role / center changes need no bump: every access
        # token is re-checked against the DB row (get_current_user refuses a
        # role/center claim that no longer matches) and the refresh that
        # follows mints the new scope, so permissions are always current.
        standing_update = None
        if "status" in data and data["status"] != before.get("status"):
            # Who suspended decides who may lift it: an admin's suspension
            # can't be undone by a center manager (staff_directory_routes).
            standing_update = {"$inc": {"token_version": 1}}
            if data["status"] == "suspended":
                standing_update["$set"] = {"suspended_by_role": "admin"}
            else:
                standing_update["$unset"] = {"suspended_by_role": ""}
        try:
            # A captain's center / role / standing change is refused while he
            # holds live jobs (CAP-01/05) — see _write_guarded.
            updated = await self._write_guarded(before, data, standing_update)
        except DuplicateKeyError:
            raise ConflictException("An account with this phone number already exists")
        if not updated:
            raise NotFoundException("User not found")
        return UserPublic.from_doc(updated).model_dump()

    async def deactivate_user(self, user_id: str) -> dict:
        user = await self.repo.find_by_id(user_id)
        if not user:
            raise NotFoundException("User not found")
        # Every refresh token dies now; a later reactivation needs a fresh login.
        # Admin-only route — recorded so a manager can't lift it. A captain
        # mid-job (or merely assigned) is refused until his jobs are
        # reassigned (CAP-05).
        updated = await self._write_guarded(
            user, {"status": "suspended"}, {"$inc": {"token_version": 1}, "$set": {"suspended_by_role": "admin"}},
        )
        if not updated:
            raise NotFoundException("User not found")
        return UserPublic.from_doc(updated).model_dump()

    async def _delete_blockers(self, user: dict, session=None) -> list[str]:
        """Everything that makes a hard delete unsafe (ADM-03): money the
        account holds or owes, and live obligations other records point at.
        Deleting through any of these orphaned a wallet balance, a paid
        pass, an unpaid booking or a center's manager slot for good."""
        uid = str(user["_id"])
        role = user.get("role")
        reasons: list[str] = []
        live = await self.db.bookings.find(
            {"$or": [{"customer_id": uid}, {"captain_id": uid}], "status": {"$in": _LIVE_BOOKING_STATUSES}, "is_deleted": {"$ne": True}},
            {"booking_number": 1}, session=session,
        ).to_list(length=50)
        if live:
            reasons.append(f"{len(live)} active booking(s) ({_numbers(live)}) — cancel or complete them")
        if role == "captain":
            wallet = await self.db.captain_wallets.find_one({"captain_id": uid}, {"balance": 1}, session=session)
            balance = round(float((wallet or {}).get("balance") or 0), 2)
            if balance:
                reasons.append(f"a wallet balance of ₹{balance:g} — settle it to ₹0")
            if await self.db.withdrawal_requests.count_documents(
                {"captain_id": uid, "status": {"$in": ["pending", "approved"]}, "is_deleted": {"$ne": True}}, session=session,
            ):
                reasons.append("a withdrawal request that isn't paid out or rejected yet")
            _jobs, societies = await self._captain_live_work(uid, session=session)
            if societies:
                reasons.append("the society captain slot for " + ", ".join(s.get("name") or str(s["_id"]) for s in societies))
        now = datetime.now(timezone.utc)
        passes = await self.db.user_subscriptions.count_documents(
            {
                "customer_id": uid, "status": {"$in": ["active", "paused", "scheduled"]}, "is_deleted": {"$ne": True},
                "$or": [{"end_date": None}, {"end_date": {"$gt": now}}, {"extended_until": {"$gt": now}}],
            },
            session=session,
        )
        if passes:
            reasons.append(f"{passes} active plan(s)")
        if await self.db.payment_orders.count_documents(
            {
                "customer_id": uid,
                "$or": [
                    {"status": {"$in": ["created", "failed"]}, "created_at": {"$gte": now - _OPEN_ORDER_HORIZON}},
                    {"settling": True},
                    {"status": "paid_attention", "resolved_at": None},
                ],
            },
            session=session,
        ):
            reasons.append("a payment still open or under review")
        centers = await self.db.service_centers.find(
            {"manager_id": uid, "is_deleted": {"$ne": True}}, {"name": 1}, session=session,
        ).to_list(length=10)
        if centers:
            reasons.append("the manager slot of " + ", ".join(c.get("name") or str(c["_id"]) for c in centers) + " — link another manager there")
        return reasons

    async def delete_user(self, user_id: str) -> bool:
        """PERMANENTLY removes the account's row from the database — a real
        delete, not the soft is_deleted flag most other records use. An
        admin clicking Delete on a person's account means gone, not merely
        hidden from lists while every field sits in Mongo forever.

        Refused (ADM-03) while the account holds money or live obligations
        — active/unpaid bookings, a captain's wallet balance or open
        withdrawal, an active plan, a payment still open, a center's
        manager slot, a society's captain slot (see _delete_blockers). The
        refusal recommends deactivating instead (status Inactive): that
        signs the person out everywhere and keeps every record intact.
        Completed/cancelled history, reviews etc. are left as historical
        record pointing at an id that no longer resolves. No token_version
        bump is needed: with the row gone, both the auth dependency and
        /auth/refresh's own user lookup fail closed on their own (refresh
        explicitly raises "User no longer exists").

        The checks run in the same transaction as the delete, after it — a
        concurrent captain assignment writes the captain's row inside its
        own transaction, so the two conflict and one retries."""
        user = await self.repo.find_by_id(user_id)
        if not user:
            raise NotFoundException("User not found")

        async def _do(session):
            result = await self.db.users.delete_one({"_id": user["_id"]}, session=session)
            reasons = await self._delete_blockers(user, session=session)
            if reasons:
                raise BadRequestException(
                    "This account can't be deleted — it still has " + "; ".join(reasons)
                    + ". Deactivate it instead (set its status to Inactive): that signs them out everywhere and keeps the records."
                )
            return result.deleted_count > 0

        async with await self.db.client.start_session() as session:
            deleted = await session.with_transaction(_do)
        if deleted:
            # The WhatsApp bot links a number to its account on the
            # conversation doc; a dead link there would stop that number
            # from ever booking on WhatsApp again (it re-links on the next
            # message instead — see WhatsAppBotService._handle_message).
            await self.db.whatsapp_conversations.update_many({"customer_id": user_id}, {"$unset": {"customer_id": ""}})
        return deleted
