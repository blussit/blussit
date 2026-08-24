from motor.motor_asyncio import AsyncIOMotorDatabase

from app.core.authz import ensure_own_center
from app.core.exceptions import NotFoundException
from app.repositories.booking_repository import BookingRepository
from app.repositories.review_repository import ReviewRepository
from app.repositories.user_repository import UserRepository
from app.schemas.user_schema import UserPublic
from app.services.booking_service import BookingService, _slot_start_datetime
from app.utils.timezone import from_stored


class StaffDirectoryService:
    def __init__(self, db: AsyncIOMotorDatabase):
        self.user_repo = UserRepository(db)
        self.booking_repo = BookingRepository(db)
        self.review_repo = ReviewRepository(db)

    async def list_captains_for_center(self, service_center_id: str, page: int, page_size: int):
        items, total = await self.user_repo.list_by_role("captain", page, page_size, extra_filters={"service_center_id": service_center_id})
        return [UserPublic.from_doc(u).model_dump() for u in items], total

    async def captain_performance(self, captain_id: str) -> dict:
        pipeline = [
            {"$match": {"captain_id": captain_id, "is_deleted": {"$ne": True}}},
            {"$group": {"_id": "$status", "count": {"$sum": 1}}},
        ]
        status_counts = await self.booking_repo.aggregate(pipeline)
        counts = {row["_id"]: row["count"] for row in status_counts}
        rating_summary = await self.review_repo.average_rating_for_captain(captain_id)

        earnings_pipeline = [
            {"$match": {"captain_id": captain_id, "status": "completed", "is_deleted": {"$ne": True}}},
            {"$group": {"_id": None, "total_jobs": {"$sum": 1}, "total_earnings": {"$sum": "$total_amount"}}},
        ]
        earnings = await self.booking_repo.aggregate(earnings_pipeline)
        earnings_summary = earnings[0] if earnings else {"total_jobs": 0, "total_earnings": 0}

        return {
            "booking_status_counts": counts,
            "average_rating": round(rating_summary.get("avg_rating") or 0, 2),
            "total_reviews": rating_summary.get("count", 0),
            "total_jobs_completed": earnings_summary.get("total_jobs", 0),
            "total_earnings": round(earnings_summary.get("total_earnings", 0), 2),
            "avg_heading_punctuality_minutes": await self._avg_heading_punctuality(captain_id),
        }

    async def _avg_heading_punctuality(self, captain_id: str) -> float | None:
        """Average (heading_at - scheduled slot start) in minutes across this
        captain's jobs that actually got underway — negative means early on
        average, positive means late. This is what tells a manager 'will this
        captain show up on time for the job I'm about to assign', which is
        why it's computed off heading punctuality, not daily attendance
        check-in (a separate, unrelated concept).

        Done in Python rather than a Mongo aggregation because heading_at is
        a computed timestamp that needs from_stored() applied before any
        arithmetic — see app/utils/timezone.py."""
        bookings = await self.booking_repo.find_all_no_paginate({"captain_id": captain_id, "heading_at": {"$ne": None}})
        if not bookings:
            return None
        deltas = []
        for booking in bookings:
            try:
                slot_start = _slot_start_datetime(booking["scheduled_date"], booking["scheduled_slot"])
                heading_ist = from_stored(booking["heading_at"])
            except (KeyError, ValueError, IndexError):
                continue
            deltas.append((heading_ist - slot_start).total_seconds() / 60)
        if not deltas:
            return None
        return round(sum(deltas) / len(deltas), 1)

    async def eligible_captains_for_booking(
        self, booking_id: str, db: AsyncIOMotorDatabase, actor_role: str, actor_center_id: str | None
    ) -> list[dict]:
        """For a given (still-unassigned or reassignable) booking, returns
        every captain at its service center with whether they can actually
        take it and why not if they can't — reuses the real conflict-check
        logic server-side instead of the frontend duplicating scheduling
        math."""
        booking_service = BookingService(db)
        booking = await booking_service.repo.find_by_id(booking_id)
        if not booking:
            raise NotFoundException("Booking not found")
        ensure_own_center(actor_role, actor_center_id, booking["service_center_id"])

        policy = await booking_service.policy_service.get_policy()
        # Only active captains are ever worth showing here — an
        # inactive/suspended one would just be a dead-end pick that fails
        # at assignment time anyway.
        captains, _ = await self.user_repo.list_by_role(
            "captain", 1, 200, extra_filters={"service_center_id": booking["service_center_id"], "status": "active"}
        )

        results = []
        for captain in captains:
            captain_id = str(captain["_id"])
            conflict = await booking_service._captain_conflict(
                captain_id, booking["scheduled_date"], booking["scheduled_slot"], booking.get("duration_minutes", 60), booking_id, policy
            )
            wallet_ok = await booking_service.wallet_service.is_eligible_for_assignment(captain_id, policy)
            if conflict:
                reason = f"Busy with booking {conflict['booking_number']} around this time"
            elif not wallet_ok:
                reason = "Wallet balance below the required minimum"
            else:
                reason = None
            results.append({
                "captain_id": captain_id,
                "full_name": captain.get("full_name", ""),
                "eligible": conflict is None and wallet_ok,
                "reason": reason,
            })
        return results
