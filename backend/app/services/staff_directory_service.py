from datetime import datetime, timedelta

from motor.motor_asyncio import AsyncIOMotorDatabase

from app.core.authz import ensure_own_center
from app.core.exceptions import NotFoundException
from app.repositories.booking_repository import BookingRepository
from app.repositories.review_repository import ReviewRepository
from app.repositories.user_repository import UserRepository
from app.schemas.user_schema import UserPublic
from app.services.booking_service import BookingService, _resolve_estimated_start, _slot_start_datetime
from app.utils.geo import haversine_km
from app.utils.timezone import from_stored


class StaffDirectoryService:
    def __init__(self, db: AsyncIOMotorDatabase):
        self.db = db
        self.user_repo = UserRepository(db)
        self.booking_repo = BookingRepository(db)
        self.review_repo = ReviewRepository(db)

    async def list_captains_for_center(self, service_center_id: str, page: int, page_size: int):
        """Deliberately does NOT add location fields to the shared
        UserPublic projection (used broadly — auth/me, general user CRUD,
        CRM) since that would expose a captain's location through every
        call site that happens to touch a captain doc, not just this
        specifically manager/admin-gated, own-center-scoped one (Section 12:
        "without exposing unnecessary personal data to unauthorized
        users"). Merged in here instead, the same pattern
        eligible_captains_for_booking already uses for the same reason."""
        items, total = await self.user_repo.list_by_role("captain", page, page_size, extra_filters={"service_center_id": service_center_id})
        results = []
        for u in items:
            base = UserPublic.from_doc(u).model_dump()
            location = u.get("last_known_location")
            base["latitude"] = location["latitude"] if location else None
            base["longitude"] = location["longitude"] if location else None
            # Bypasses serialize_doc entirely (built as a plain dict, like
            # UserPublic itself) — last_location_at is a computed
            # aware-at-write-time instant (now_ist(), see
            # BookingService.update_captain_location) read back naive, so
            # it needs its own from_stored() call here or this silently
            # reproduces the exact bug COMPUTED_INSTANT_KEYS exists to
            # prevent everywhere else.
            raw_last_location_at = u.get("last_location_at")
            base["last_location_at"] = from_stored(raw_last_location_at).isoformat() if raw_last_location_at else None
            # Verification state for the manager's team list — status only,
            # never the numbers themselves (those live behind the dedicated
            # review endpoint).
            base["kyc_status"] = (u.get("captain_kyc") or {}).get("status", "pending")
            # Roster photo — same precedence the customer's captain card
            # uses (KYC photo first, account profile image as fallback).
            base["photo_url"] = (u.get("captain_kyc") or {}).get("photo_url") or u.get("profile_image")
            results.append(base)
        return results, total

    async def captain_attendance(self, captain_id: str, actor_role: str, actor_center_id: str | None, page: int, page_size: int):
        """A manager's own captain's check-in/check-out history — reuses
        AttendanceService's existing per-captain list (previously
        captain-self-only via GET /attendance/my) rather than duplicating
        the query, just adding the center-ownership check a manager-facing
        call needs. Deliberately imported locally to avoid a circular
        import (staff_ops_service doesn't import this module)."""
        from app.services.staff_ops_service import AttendanceService

        captain = await self.user_repo.find_by_id(captain_id)
        if not captain or captain.get("role") != "captain":
            raise NotFoundException("Captain not found")
        ensure_own_center(actor_role, actor_center_id, captain.get("service_center_id") or "")
        items, total = await AttendanceService(self.db).list_for_captain(captain_id, page, page_size)
        return items, total

    async def captain_performance(
        self,
        captain_id: str,
        date_from: str | None = None,
        date_to: str | None = None,
        service_center_id: str | None = None,
    ) -> dict:
        """All figures computed from real stored timestamps/fields on the
        booking documents — never a manually entered number. date_from/
        date_to filter on scheduled_date (inclusive, "YYYY-MM-DD");
        service_center_id further scopes to one center."""
        match: dict = {"captain_id": captain_id, "is_deleted": {"$ne": True}}
        if date_from or date_to:
            date_match: dict = {}
            if date_from:
                date_match["$gte"] = datetime.strptime(date_from, "%Y-%m-%d")
            if date_to:
                date_match["$lte"] = datetime.strptime(date_to, "%Y-%m-%d") + timedelta(days=1)
            match["scheduled_date"] = date_match
        if service_center_id:
            match["service_center_id"] = service_center_id

        pipeline = [{"$match": match}, {"$group": {"_id": "$status", "count": {"$sum": 1}}}]
        status_counts = await self.booking_repo.aggregate(pipeline)
        counts = {row["_id"]: row["count"] for row in status_counts}
        rating_summary = await self.review_repo.average_rating_for_captain(captain_id)

        bookings = await self.booking_repo.find_all_no_paginate(match)
        completed = [b for b in bookings if b.get("status") == "completed"]
        total_jobs = len(completed)
        # The CAPTAIN's earnings, not the customer-paid gross — summing
        # total_amount here showed a captain "earnings" of the full booking
        # revenue on their own earnings page. Gross is exposed separately.
        total_earnings = round(sum(b.get("captain_earning") or 0 for b in completed), 2)
        total_booking_value = round(sum(b.get("total_amount", 0) for b in completed), 2)

        reassigned_away = sum(1 for b in bookings if captain_id in (b.get("previous_captain_ids") or []))

        # On-time-start % and delay stats come straight from captain_start_stage
        # (set once, at start_heading, never recomputed) and delay_minutes
        # (set once, at completion — see BookingService.capture_after_photo_and_complete).
        started = [b for b in bookings if b.get("captain_start_stage")]
        on_time_starts = sum(1 for b in started if b["captain_start_stage"] in ("early", "on_time"))
        on_time_start_pct = round(on_time_starts / len(started) * 100, 1) if started else None

        delayed = [b for b in completed if b.get("delay_minutes")]
        on_time_completion_pct = round((total_jobs - len(delayed)) / total_jobs * 100, 1) if total_jobs else None
        avg_delay = round(sum(b["delay_minutes"] for b in delayed) / len(delayed), 1) if delayed else None

        # Travel/waiting/service/total durations — all derived from the same
        # real timeline fields, never a second, redundant stored copy.
        def _minutes(a: dict, start_key: str, end_key: str) -> float | None:
            start, end = a.get(start_key), a.get(end_key)
            if not start or not end:
                return None
            return max(0.0, (from_stored(end) - from_stored(start)).total_seconds() / 60)

        travel_times = [m for b in completed if (m := _minutes(b, "heading_at", "vehicle_verified_at")) is not None]
        waiting_times = [m for b in completed if (m := _minutes(b, "vehicle_verified_at", "service_started_at")) is not None]
        service_times = [b["actual_duration_minutes"] for b in completed if b.get("actual_duration_minutes") is not None]
        total_times = [m for b in completed if (m := _minutes(b, "assigned_at", "closed_at")) is not None]

        def _avg(values: list[float]) -> float | None:
            return round(sum(values) / len(values), 1) if values else None

        # Repeat complaints — complaints whose booking belongs to this
        # captain (Complaint has no direct captain_id, only booking_id).
        booking_ids = [str(b["_id"]) for b in bookings]
        repeat_complaints = await self.db.complaints.count_documents({"booking_id": {"$in": booking_ids}, "is_deleted": {"$ne": True}}) if booking_ids else 0

        distinct_days = len({b["scheduled_date"].strftime("%Y-%m-%d") for b in bookings if b.get("scheduled_date")})
        distinct_slots = len({b.get("scheduled_slot") for b in bookings if b.get("scheduled_slot")})

        # How often each anomaly signal hit this captain in the range — the
        # repeat-offender view. Open issue flags (idle_after_arrival,
        # left_site_during_service, captain_delay, service_overrun, ...) plus
        # the location-mismatch flags, which persist on the booking even
        # after a manager resolves the issue itself.
        flags_by_type: dict[str, int] = {}
        for b in bookings:
            if b.get("issue_flag"):
                flags_by_type[b["issue_flag"]] = flags_by_type.get(b["issue_flag"], 0) + 1
            for key, label in (
                ("arrival_flagged", "arrival_location"),
                ("before_photo_flagged", "before_photo_location"),
                ("after_photo_flagged", "after_photo_location"),
            ):
                if b.get(key):
                    flags_by_type[label] = flags_by_type.get(label, 0) + 1

        return {
            "flags_by_type": flags_by_type,
            "booking_status_counts": counts,
            "total_bookings": len(bookings),
            "cancelled_bookings": counts.get("cancelled", 0),
            "reassigned_away_count": reassigned_away,
            "average_rating": round(rating_summary.get("avg_rating") or 0, 2),
            "total_reviews": rating_summary.get("count", 0),
            "repeat_complaints": repeat_complaints,
            "total_jobs_completed": total_jobs,
            "total_earnings": total_earnings,
            "total_booking_value": total_booking_value,
            # Same date window as everything else in this payload — this
            # helper used to ignore the filter and always report all-time.
            "avg_heading_punctuality_minutes": await self._avg_heading_punctuality(captain_id, {k: v for k, v in match.items() if k == "scheduled_date"}),
            "on_time_start_pct": on_time_start_pct,
            "on_time_completion_pct": on_time_completion_pct,
            "delayed_jobs": len(delayed),
            "avg_delay_minutes": avg_delay,
            "avg_travel_minutes": _avg(travel_times),
            "avg_waiting_minutes": _avg(waiting_times),
            "avg_service_minutes": _avg(service_times),
            "avg_total_job_minutes": _avg(total_times),
            "jobs_per_day": round(total_jobs / distinct_days, 2) if distinct_days else None,
            "jobs_per_slot": round(total_jobs / distinct_slots, 2) if distinct_slots else None,
        }

    async def _avg_heading_punctuality(self, captain_id: str, extra_match: dict | None = None) -> float | None:
        """Average (heading_at - scheduled slot start) in minutes across this
        captain's jobs that actually got underway — negative means early on
        average, positive means late. This is what tells a manager 'will this
        captain show up on time for the job I'm about to assign', which is
        why it's computed off heading punctuality, not daily attendance
        check-in (a separate, unrelated concept).

        Done in Python rather than a Mongo aggregation because heading_at is
        a computed timestamp that needs from_stored() applied before any
        arithmetic — see app/utils/timezone.py."""
        bookings = await self.booking_repo.find_all_no_paginate({**(extra_match or {}), "captain_id": captain_id, "heading_at": {"$ne": None}})
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
        math. Also surfaces enough location/availability context (Section 12)
        for a manager to assign intelligently: each captain's distance from
        THIS booking's address (from their last known GPS position — see
        UserModel.last_known_location, now refreshed continuously via a
        periodic ping while a captain has an active job — see
        BookingService.update_captain_location/has_active_job), and
        whether they're currently mid-job."""
        booking_service = BookingService(db)
        booking = await booking_service.repo.find_by_id(booking_id)
        if not booking:
            raise NotFoundException("Booking not found")
        ensure_own_center(actor_role, actor_center_id, booking["service_center_id"])

        address = await booking_service.address_repo.find_by_id(booking["address_id"])
        policy = await booking_service.policy_service.get_policy()
        # Only active captains are ever worth showing here — an
        # inactive/suspended one would just be a dead-end pick that fails
        # at assignment time anyway.
        captains, _ = await self.user_repo.list_by_role(
            "captain", 1, 200, extra_filters={"service_center_id": booking["service_center_id"], "status": "active"}
        )

        # Trial start = the same default assign_captain itself would use if
        # the manager doesn't override it (the booking's own slot_start) —
        # keeps this advisory "who's free" preview accurate for the common
        # case; the real, authoritative check re-runs atomically inside
        # assign_captain's transaction regardless of what's shown here.
        trial_start = _resolve_estimated_start(booking, None)
        trial_end = trial_start + timedelta(minutes=booking.get("duration_minutes", 60))

        results = []
        for captain in captains:
            captain_id = str(captain["_id"])
            conflict = await booking_service._captain_conflict(captain_id, trial_start, trial_end, booking_id, policy)
            wallet_ok = await booking_service.wallet_service.is_eligible_for_assignment(captain_id, policy)
            if conflict:
                reason = f"Busy with booking {conflict['booking_number']} around this time"
            elif not wallet_ok:
                reason = "Wallet balance below the required minimum"
            else:
                reason = None

            distance_km = None
            last_location = captain.get("last_known_location")
            if last_location and address and address.get("latitude") is not None and address.get("longitude") is not None:
                distance_km = round(
                    haversine_km(last_location["latitude"], last_location["longitude"], address["latitude"], address["longitude"]), 1
                )
            active_now = await self.booking_repo.find_active_for_captain(captain_id, None)

            results.append({
                "captain_id": captain_id,
                "full_name": captain.get("full_name", ""),
                "eligible": conflict is None and wallet_ok,
                "reason": reason,
                "distance_km": distance_km,
                # Raw coordinates (not just the computed distance) so the
                # frontend can seed a live map for an on-job captain —
                # updated in place afterward over the "captain-location:{id}"
                # WebSocket channel, this is just the starting position.
                "latitude": last_location["latitude"] if last_location else None,
                "longitude": last_location["longitude"] if last_location else None,
                # Bypasses serialize_doc (built as a plain dict) — this was
                # returning the raw naive-holding-UTC value with no
                # from_stored() conversion, the exact bug class
                # COMPUTED_INSTANT_KEYS exists to prevent elsewhere. Fixed
                # here alongside the same fix in list_captains_for_center.
                "last_location_at": from_stored(captain["last_location_at"]).isoformat() if captain.get("last_location_at") else None,
                "is_on_job": len(active_now) > 0,
                "current_job_count": len(active_now),
            })
        # Road ETA (traffic-aware) for the located captains — the number a
        # dispatcher actually thinks in. Haversine distance_km above stays
        # as the always-available fallback; capped to keep Routes calls
        # bounded however many captains a center ever has.
        from app.services.route_service import road_distance_eta

        if address and address.get("latitude") is not None:
            budget = 5
            for row in results:
                if budget <= 0:
                    break
                if row["latitude"] is None:
                    continue
                leg = await road_distance_eta(row["latitude"], row["longitude"], address["latitude"], address["longitude"])
                budget -= 1
                if leg:
                    row["road_km"] = leg["km"]
                    row["eta_minutes"] = leg["minutes"]
                    row["eta_source"] = leg["source"]
        return results
