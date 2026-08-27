from datetime import timedelta

from motor.motor_asyncio import AsyncIOMotorDatabase

from app.repositories.booking_repository import BookingRepository
from app.repositories.catalog_repository import ServiceRepository
from app.repositories.service_center_repository import ServiceCenterRepository
from app.repositories.subscription_repository import UserSubscriptionRepository
from app.repositories.user_repository import UserRepository
from app.repositories.vehicle_repository import VehicleRepository
from app.repositories.vehicle_type_repository import VehicleTypeRepository
from app.utils.timezone import now_ist

# Shared by every per-group KPI breakdown below (vehicle-type, service) —
# same travel/total-time formulas as dashboard_summary/manager_summary,
# expressible directly inside a $group accumulator without a separate
# $project stage.
_TRAVEL_MINUTES_EXPR = {
    "$cond": [{"$and": ["$heading_at", "$vehicle_verified_at"]}, {"$divide": [{"$subtract": ["$vehicle_verified_at", "$heading_at"]}, 60000]}, None]
}
_TOTAL_MINUTES_EXPR = {"$cond": [{"$and": ["$assigned_at", "$closed_at"]}, {"$divide": [{"$subtract": ["$closed_at", "$assigned_at"]}, 60000]}, None]}


class AnalyticsService:
    def __init__(self, db: AsyncIOMotorDatabase):
        self.db = db
        self.booking_repo = BookingRepository(db)
        self.user_repo = UserRepository(db)
        self.center_repo = ServiceCenterRepository(db)
        self.subscription_repo = UserSubscriptionRepository(db)
        self.service_repo = ServiceRepository(db)
        self.vehicle_type_repo = VehicleTypeRepository(db)
        self.vehicle_repo = VehicleRepository(db)

    async def dashboard_summary(self) -> dict:
        now = now_ist()
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

        total_revenue_pipeline = [
            {"$match": {"status": "completed", "is_deleted": {"$ne": True}}},
            {"$group": {"_id": None, "total": {"$sum": "$total_amount"}}},
        ]
        total_revenue = await self.booking_repo.aggregate(total_revenue_pipeline)
        total_revenue_val = total_revenue[0]["total"] if total_revenue else 0

        today_orders = await self.booking_repo.count({"created_at": {"$gte": today_start}})
        monthly_orders = await self.booking_repo.count({"created_at": {"$gte": month_start}})
        active_customers = await self.user_repo.count({"role": "customer", "status": "active"})
        cancelled = await self.booking_repo.count({"status": "cancelled"})
        completed = await self.booking_repo.count({"status": "completed"})
        total_bookings = await self.booking_repo.count({})

        subscription_revenue_pipeline = [
            {"$match": {"payment_method": "subscription", "status": "completed", "is_deleted": {"$ne": True}}},
            {"$group": {"_id": None, "total": {"$sum": "$subtotal"}}},
        ]
        subscription_revenue = await self.booking_repo.aggregate(subscription_revenue_pipeline)
        subscription_revenue_val = subscription_revenue[0]["total"] if subscription_revenue else 0

        repeat_customers_pipeline = [
            {"$match": {"is_deleted": {"$ne": True}}},
            {"$group": {"_id": "$customer_id", "count": {"$sum": 1}}},
            {"$match": {"count": {"$gt": 1}}},
        ]
        repeat_customers = await self.booking_repo.aggregate(repeat_customers_pipeline)
        total_customers_with_bookings_pipeline = [
            {"$match": {"is_deleted": {"$ne": True}}},
            {"$group": {"_id": "$customer_id"}},
        ]
        distinct_customers = await self.booking_repo.aggregate(total_customers_with_bookings_pipeline)
        repeat_rate = (len(repeat_customers) / len(distinct_customers) * 100) if distinct_customers else 0

        top_services_pipeline = [
            {"$match": {"is_deleted": {"$ne": True}}},
            {"$unwind": "$service_ids"},
            {"$group": {"_id": "$service_ids", "bookings": {"$sum": 1}}},
            {"$sort": {"bookings": -1}},
            {"$limit": 5},
        ]
        top_services = await self.booking_repo.aggregate(top_services_pipeline)

        best_captains_pipeline = [
            {"$match": {"status": "completed", "captain_id": {"$ne": None}, "is_deleted": {"$ne": True}}},
            {"$group": {"_id": "$captain_id", "jobs_completed": {"$sum": 1}, "revenue": {"$sum": "$total_amount"}}},
            {"$sort": {"jobs_completed": -1}},
            {"$limit": 5},
        ]
        best_captains = await self.booking_repo.aggregate(best_captains_pipeline)

        best_centers_pipeline = [
            {"$match": {"status": "completed", "is_deleted": {"$ne": True}}},
            {"$group": {"_id": "$service_center_id", "jobs_completed": {"$sum": 1}, "revenue": {"$sum": "$total_amount"}}},
            {"$sort": {"revenue": -1}},
            {"$limit": 5},
        ]
        best_centers = await self.booking_repo.aggregate(best_centers_pipeline)

        # Section 14's explicitly-requested operational KPIs — computed the
        # same way as everywhere else in this codebase: real stored
        # timestamps, never a second manually-tracked counter. The
        # subtraction-based averages are timezone-label-agnostic (a
        # duration between two equally-naive-but-really-UTC instants is
        # correct without from_stored() converting either side first —
        # see StaffDirectoryService.captain_performance for the identical
        # per-captain formulas this mirrors, kept consistent on purpose).
        pending_bookings = await self.booking_repo.count({"status": {"$in": ["pending", "rescheduled"]}})
        delayed_bookings = await self.booking_repo.count({"delay_minutes": {"$gt": 0}})

        duration_pipeline = [
            {"$match": {"status": "completed", "is_deleted": {"$ne": True}}},
            {
                "$project": {
                    "actual_duration_minutes": 1,
                    "travel_minutes": {
                        "$cond": [
                            {"$and": ["$heading_at", "$vehicle_verified_at"]},
                            {"$divide": [{"$subtract": ["$vehicle_verified_at", "$heading_at"]}, 60000]},
                            None,
                        ]
                    },
                    "total_minutes": {
                        "$cond": [
                            {"$and": ["$assigned_at", "$closed_at"]},
                            {"$divide": [{"$subtract": ["$closed_at", "$assigned_at"]}, 60000]},
                            None,
                        ]
                    },
                }
            },
            {
                "$group": {
                    "_id": None,
                    "avg_service_minutes": {"$avg": "$actual_duration_minutes"},
                    "avg_travel_minutes": {"$avg": "$travel_minutes"},
                    "avg_completion_minutes": {"$avg": "$total_minutes"},
                }
            },
        ]
        duration_result = await self.booking_repo.aggregate(duration_pipeline)
        durations = duration_result[0] if duration_result else {}

        rating_pipeline = [
            {"$match": {"is_deleted": {"$ne": True}}},
            {"$group": {"_id": None, "avg": {"$avg": {"$ifNull": ["$captain_rating", "$rating"]}}}},
        ]
        rating_result = await self.db.reviews.aggregate(rating_pipeline).to_list(length=1)
        avg_rating = round(rating_result[0]["avg"], 2) if rating_result and rating_result[0].get("avg") is not None else None

        today_capacity_pipeline = [
            {"$match": {"date": today_start.strftime("%Y-%m-%d")}},
            {"$group": {"_id": None, "capacity": {"$sum": "$capacity"}, "booked": {"$sum": "$booked_count"}}},
        ]
        today_capacity_result = await self.db.slot_capacity.aggregate(today_capacity_pipeline).to_list(length=1)
        today_capacity = today_capacity_result[0]["capacity"] if today_capacity_result else 0
        today_booked = today_capacity_result[0]["booked"] if today_capacity_result else 0
        utilization_pct = round((today_booked / today_capacity) * 100, 1) if today_capacity else None

        return {
            "total_revenue": round(total_revenue_val, 2),
            "todays_orders": today_orders,
            "monthly_orders": monthly_orders,
            "active_customers": active_customers,
            "repeat_customer_rate": round(repeat_rate, 2),
            "subscription_revenue": round(subscription_revenue_val, 2),
            "top_services": [{"service_id": s["_id"], "bookings": s["bookings"]} for s in top_services],
            "best_performing_captains": [{"captain_id": c["_id"], "jobs_completed": c["jobs_completed"], "revenue": round(c["revenue"], 2)} for c in best_captains],
            "best_service_centers": [{"service_center_id": c["_id"], "jobs_completed": c["jobs_completed"], "revenue": round(c["revenue"], 2)} for c in best_centers],
            "cancellation_rate": round((cancelled / total_bookings * 100), 2) if total_bookings else 0,
            "completion_rate": round((completed / total_bookings * 100), 2) if total_bookings else 0,
            "total_bookings": total_bookings,
            "completed_bookings": completed,
            "pending_bookings": pending_bookings,
            "cancelled_bookings": cancelled,
            "delayed_bookings": delayed_bookings,
            "avg_service_minutes": round(durations["avg_service_minutes"], 1) if durations.get("avg_service_minutes") is not None else None,
            "avg_travel_minutes": round(durations["avg_travel_minutes"], 1) if durations.get("avg_travel_minutes") is not None else None,
            "avg_completion_minutes": round(durations["avg_completion_minutes"], 1) if durations.get("avg_completion_minutes") is not None else None,
            "avg_rating": avg_rating,
            # Only reflects dates already touched (lazily-initialized slot_capacity
            # docs) — a day nobody has booked into or viewed the capacity admin
            # page for yet shows as 0/None here rather than the full configured
            # policy total, since that's never materialized until first touched.
            "today_capacity": today_capacity,
            "today_booked": today_booked,
            "capacity_utilization_pct": utilization_pct,
        }

    async def booking_trends(self, days: int = 30) -> list[dict]:
        start = now_ist() - timedelta(days=days)
        pipeline = [
            {"$match": {"created_at": {"$gte": start}, "is_deleted": {"$ne": True}}},
            {
                "$group": {
                    "_id": {"$dateToString": {"format": "%Y-%m-%d", "date": "$created_at", "timezone": "+05:30"}},
                    "bookings": {"$sum": 1},
                    "revenue": {"$sum": "$total_amount"},
                }
            },
            {"$sort": {"_id": 1}},
        ]
        results = await self.booking_repo.aggregate(pipeline)
        return [{"date": r["_id"], "bookings": r["bookings"], "revenue": round(r["revenue"], 2)} for r in results]

    async def service_center_summaries(self) -> list[dict]:
        """Section 10 of the ops-UX update — the admin's FIRST screen
        before drilling into any one center's bookings: one row per
        active service center with bookings/completed/pending/delayed
        counts and average rating, computed live from stored data (never
        cached/manually entered). One aggregation per center rather than
        loading every booking platform-wide up front — matches the
        existing captains_performance_for_center per-item loop style."""
        centers, _ = await self.center_repo.find_many({"is_active": True}, page=1, page_size=200)
        results = []
        for center in centers:
            center_id = str(center["_id"])
            match: dict = {"service_center_id": center_id, "is_deleted": {"$ne": True}}
            total = await self.booking_repo.count(match)
            completed = await self.booking_repo.count({**match, "status": "completed"})
            pending = await self.booking_repo.count({**match, "status": {"$in": ["pending", "rescheduled"]}})
            delayed = await self.booking_repo.count({**match, "delay_minutes": {"$gt": 0}})
            rating_pipeline = [
                {"$match": {"service_center_id": center_id, "is_deleted": {"$ne": True}}},
                {"$group": {"_id": None, "avg": {"$avg": {"$ifNull": ["$captain_rating", "$rating"]}}, "count": {"$sum": 1}}},
            ]
            rating_result = await self.db.reviews.aggregate(rating_pipeline).to_list(length=1)
            avg_rating = round(rating_result[0]["avg"], 1) if rating_result and rating_result[0].get("avg") is not None else None
            results.append({
                "service_center_id": center_id,
                "name": center.get("name"),
                "bookings": total,
                "completed": completed,
                "pending": pending,
                "delayed": delayed,
                "avg_rating": avg_rating,
                "review_count": rating_result[0]["count"] if rating_result else 0,
            })
        return results

    async def manager_summary(self, service_center_id: str) -> dict:
        """Section 20 — a manager's simplified daily operational view,
        scoped to their own center. Same real-timestamp-derived formulas
        as service_center_summaries/dashboard_summary, just narrowed to
        one center and to "today" for the booking counts (a manager cares
        about right now, not all-time totals — that's the admin view)."""
        now = now_ist()
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        today_str = today_start.strftime("%Y-%m-%d")
        base_match = {"service_center_id": service_center_id, "is_deleted": {"$ne": True}}
        today_match = {**base_match, "scheduled_date": {"$gte": today_start, "$lt": today_start + timedelta(days=1)}}

        bookings_today = await self.booking_repo.count(today_match)
        completed_today = await self.booking_repo.count({**today_match, "status": "completed"})
        pending_today = await self.booking_repo.count({**today_match, "status": {"$in": ["pending", "rescheduled"]}})
        delayed_today = await self.booking_repo.count({**today_match, "delay_minutes": {"$gt": 0}})
        unassigned_today = await self.booking_repo.count({**today_match, "status": {"$in": ["pending", "rescheduled"]}, "captain_id": None})

        duration_pipeline = [
            {"$match": {**base_match, "status": "completed"}},
            {
                "$project": {
                    "actual_duration_minutes": 1,
                    "travel_minutes": {
                        "$cond": [{"$and": ["$heading_at", "$vehicle_verified_at"]}, {"$divide": [{"$subtract": ["$vehicle_verified_at", "$heading_at"]}, 60000]}, None]
                    },
                    "total_minutes": {"$cond": [{"$and": ["$assigned_at", "$closed_at"]}, {"$divide": [{"$subtract": ["$closed_at", "$assigned_at"]}, 60000]}, None]},
                    "on_time": {"$in": ["$captain_start_stage", ["early", "on_time"]]},
                }
            },
            {
                "$group": {
                    "_id": None,
                    "avg_service_minutes": {"$avg": "$actual_duration_minutes"},
                    "avg_travel_minutes": {"$avg": "$travel_minutes"},
                    "avg_completion_minutes": {"$avg": "$total_minutes"},
                    "on_time_count": {"$sum": {"$cond": ["$on_time", 1, 0]}},
                    "total_count": {"$sum": 1},
                }
            },
        ]
        duration_result = await self.booking_repo.aggregate(duration_pipeline)
        d = duration_result[0] if duration_result else {}
        on_time_pct = round(d["on_time_count"] / d["total_count"] * 100, 1) if d.get("total_count") else None

        rating_pipeline = [
            {"$match": {"service_center_id": service_center_id, "is_deleted": {"$ne": True}}},
            {"$group": {"_id": None, "avg": {"$avg": {"$ifNull": ["$captain_rating", "$rating"]}}}},
        ]
        rating_result = await self.db.reviews.aggregate(rating_pipeline).to_list(length=1)
        avg_rating = round(rating_result[0]["avg"], 2) if rating_result and rating_result[0].get("avg") is not None else None

        # Captain utilization — how many of this center's active captains
        # currently have a job in progress right now, a simple, honest
        # proxy given there's no shift/roster system to compute "% of
        # working hours busy" against.
        total_captains = await self.user_repo.count({"role": "captain", "service_center_id": service_center_id, "status": "active"})
        busy_captains = len(await self.booking_repo.find_all_no_paginate(
            {"service_center_id": service_center_id, "status": {"$in": ["assigned", "captain_on_the_way", "service_started"]}}
        ))
        # A captain can only ever be on one job at a time (see the
        # existing captain-conflict guards), so counting active bookings
        # is equivalent to counting busy captains — but cap at
        # total_captains defensively in case of stale/duplicate data.
        busy_captains = min(busy_captains, total_captains) if total_captains else 0
        captain_utilization_pct = round(busy_captains / total_captains * 100, 1) if total_captains else None

        day_doc = await self.db.daily_capacity.find_one({"service_center_id": service_center_id, "date": today_str})
        slot_docs = await self.db.slot_capacity.find({"service_center_id": service_center_id, "date": today_str}).to_list(length=None)
        today_capacity = day_doc["capacity"] if day_doc else sum(s["capacity"] for s in slot_docs)
        today_booked = day_doc["booked_count"] if day_doc else sum(s["booked_count"] for s in slot_docs)
        capacity_utilization_pct = round(today_booked / today_capacity * 100, 1) if today_capacity else None

        return {
            "bookings_today": bookings_today,
            "completed_today": completed_today,
            "pending_today": pending_today,
            "delayed_today": delayed_today,
            "unassigned_today": unassigned_today,
            "avg_travel_minutes": round(d["avg_travel_minutes"], 1) if d.get("avg_travel_minutes") is not None else None,
            "avg_service_minutes": round(d["avg_service_minutes"], 1) if d.get("avg_service_minutes") is not None else None,
            "avg_completion_minutes": round(d["avg_completion_minutes"], 1) if d.get("avg_completion_minutes") is not None else None,
            "on_time_pct": on_time_pct,
            "captain_utilization_pct": captain_utilization_pct,
            "avg_rating": avg_rating,
            "capacity_utilization_pct": capacity_utilization_pct,
            "today_capacity": today_capacity,
            "today_booked": today_booked,
        }

    async def vehicle_type_breakdown(self, service_center_id: str | None = None) -> list[dict]:
        """Section 16 — per vehicle type (Hatchback/Sedan/SUV/...): how many
        bookings, how many completed, average service/travel/total time,
        how many ran delayed, and the average rating. This is what a click
        on a vehicle type in the admin catalog opens — "which vehicle type
        is actually driving bookings" rather than just its catalog fields.

        A booking stores only vehicle_id, never its vehicle's type directly
        (vehicle_snapshot is a virtual field BookingService._enrich_bookings
        computes at read time for specific listing endpoints — it is NOT a
        real field in the bookings collection, so it can't be $group'd on).
        The group-by-vehicle-type therefore happens in Python, after a
        batch vehicle_id -> vehicle_type join, the same pattern this
        codebase already uses everywhere a cross-collection join is needed
        (e.g. ReviewService._enrich)."""
        match: dict = {"is_deleted": {"$ne": True}}
        if service_center_id:
            match["service_center_id"] = service_center_id
        pipeline = [
            {"$match": match},
            {
                "$project": {
                    "vehicle_id": 1,
                    "status": 1,
                    "actual_duration_minutes": 1,
                    "delay_minutes": 1,
                    "travel_minutes": _TRAVEL_MINUTES_EXPR,
                    "total_minutes": _TOTAL_MINUTES_EXPR,
                }
            },
        ]
        rows = await self.booking_repo.aggregate(pipeline)

        vehicle_ids = {r["vehicle_id"] for r in rows if r.get("vehicle_id")}
        vehicles = {str(v["_id"]): v for v in await self.vehicle_repo.find_by_ids(list(vehicle_ids))}
        types = {str(t["_id"]): t.get("name", "Unknown") for t in await self.vehicle_type_repo.find_all_no_paginate()}

        grouped = self._group_rows_by(rows, lambda row: (vehicles.get(row.get("vehicle_id")) or {}).get("vehicle_type"))
        ratings_by_type = await self._ratings_grouped_by_vehicle_type(service_center_id)

        results = []
        for vt_id, g in sorted(grouped.items(), key=lambda kv: -kv[1]["total"]):
            ratings = ratings_by_type.get(vt_id, [])
            results.append({
                "vehicle_type_id": vt_id,
                "vehicle_type_name": types.get(vt_id, vt_id),
                **self._finalize_group(g),
                "avg_rating": round(sum(ratings) / len(ratings), 2) if ratings else None,
            })
        return results

    @staticmethod
    def _group_rows_by(rows: list[dict], key_fn) -> dict[str, dict]:
        """Shared grouping/summing helper for the per-booking rows both
        breakdown methods project — avoids duplicating the same
        sum/count-then-average bookkeeping twice."""
        grouped: dict[str, dict] = {}
        for row in rows:
            key = key_fn(row)
            if not key:
                continue
            g = grouped.setdefault(key, {
                "total": 0, "completed": 0, "delayed": 0,
                "service_sum": 0.0, "service_n": 0, "actual_sum": 0.0, "actual_n": 0, "expected_sum": 0.0, "expected_n": 0,
                "travel_sum": 0.0, "travel_n": 0, "total_min_sum": 0.0, "total_min_n": 0,
            })
            g["total"] += 1
            if row.get("status") == "completed":
                g["completed"] += 1
            if row.get("actual_duration_minutes") is not None:
                g["service_sum"] += row["actual_duration_minutes"]
                g["service_n"] += 1
                g["actual_sum"] += row["actual_duration_minutes"]
                g["actual_n"] += 1
            if row.get("duration_minutes") is not None:
                g["expected_sum"] += row["duration_minutes"]
                g["expected_n"] += 1
            if row.get("travel_minutes") is not None:
                g["travel_sum"] += row["travel_minutes"]
                g["travel_n"] += 1
            if row.get("total_minutes") is not None:
                g["total_min_sum"] += row["total_minutes"]
                g["total_min_n"] += 1
            if (row.get("delay_minutes") or 0) > 0:
                g["delayed"] += 1
        return grouped

    @staticmethod
    def _finalize_group(g: dict) -> dict:
        return {
            "total_bookings": g["total"],
            "completed_bookings": g["completed"],
            "avg_service_minutes": round(g["service_sum"] / g["service_n"], 1) if g["service_n"] else None,
            "avg_actual_minutes": round(g["actual_sum"] / g["actual_n"], 1) if g["actual_n"] else None,
            "avg_expected_minutes": round(g["expected_sum"] / g["expected_n"], 1) if g["expected_n"] else None,
            "avg_travel_minutes": round(g["travel_sum"] / g["travel_n"], 1) if g["travel_n"] else None,
            "avg_total_minutes": round(g["total_min_sum"] / g["total_min_n"], 1) if g["total_min_n"] else None,
            "delayed_count": g["delayed"],
        }

    async def _ratings_grouped_by_vehicle_type(self, service_center_id: str | None) -> dict[str, list[float]]:
        """Reviews don't store vehicle_type directly — joined here in Python
        against a batch booking lookup, then a second batch vehicle lookup
        (booking.vehicle_id -> vehicle.vehicle_type), matching how
        ReviewService._enrich resolves the same relationship. review.
        booking_id is a string, booking._id is an ObjectId, so a cross-type
        $lookup isn't the natural fit here either."""
        reviews = await self.db.reviews.find({"is_deleted": {"$ne": True}}).to_list(length=None)
        booking_ids = [r["booking_id"] for r in reviews if r.get("booking_id")]
        bookings = {str(b["_id"]): b for b in await self.booking_repo.find_by_ids(booking_ids)}
        vehicle_ids = {b["vehicle_id"] for b in bookings.values() if b.get("vehicle_id")}
        vehicles = {str(v["_id"]): v for v in await self.vehicle_repo.find_by_ids(list(vehicle_ids))}
        grouped: dict[str, list[float]] = {}
        for r in reviews:
            booking = bookings.get(r.get("booking_id"))
            if not booking:
                continue
            if service_center_id and booking.get("service_center_id") != service_center_id:
                continue
            vt = (vehicles.get(booking.get("vehicle_id")) or {}).get("vehicle_type")
            rating = r.get("captain_rating") if r.get("captain_rating") is not None else r.get("rating")
            if vt and rating is not None:
                grouped.setdefault(vt, []).append(rating)
        return grouped

    async def service_breakdown(self, service_center_id: str | None = None) -> list[dict]:
        """Section 17 — per service: how many times it's been booked,
        completed, actual vs expected duration, average travel/total job
        time, delayed jobs, average rating. Approximation, documented
        rather than hidden: duration_minutes/actual_duration_minutes are
        stored on the BOOKING as a whole (the sum across every service
        selected in it), not split per individual service within a
        multi-service booking — unwinding service_ids attributes a
        multi-service booking's full duration to each of its services, the
        same simplification this system's data model has no finer way
        around."""
        match: dict = {"is_deleted": {"$ne": True}}
        if service_center_id:
            match["service_center_id"] = service_center_id
        pipeline = [
            {"$match": match},
            {"$unwind": "$service_ids"},
            {
                "$group": {
                    "_id": "$service_ids",
                    "total_bookings": {"$sum": 1},
                    "completed_bookings": {"$sum": {"$cond": [{"$eq": ["$status", "completed"]}, 1, 0]}},
                    "avg_actual_minutes": {"$avg": "$actual_duration_minutes"},
                    "avg_expected_minutes": {"$avg": "$duration_minutes"},
                    "avg_travel_minutes": {"$avg": _TRAVEL_MINUTES_EXPR},
                    "avg_total_minutes": {"$avg": _TOTAL_MINUTES_EXPR},
                    "delayed_count": {"$sum": {"$cond": [{"$gt": ["$delay_minutes", 0]}, 1, 0]}},
                }
            },
            {"$sort": {"total_bookings": -1}},
        ]
        rows = await self.booking_repo.aggregate(pipeline)
        ratings_by_service = await self._ratings_grouped_by_service(service_center_id)

        services = {str(s["_id"]): s.get("name", "Unknown") for s in await self.service_repo.find_all_no_paginate()}
        results = []
        for row in rows:
            sid = row["_id"]
            ratings = ratings_by_service.get(sid, [])
            results.append({
                "service_id": sid,
                "service_name": services.get(sid, sid),
                "total_bookings": row["total_bookings"],
                "completed_bookings": row["completed_bookings"],
                "avg_actual_minutes": round(row["avg_actual_minutes"], 1) if row.get("avg_actual_minutes") is not None else None,
                "avg_expected_minutes": round(row["avg_expected_minutes"], 1) if row.get("avg_expected_minutes") is not None else None,
                "avg_travel_minutes": round(row["avg_travel_minutes"], 1) if row.get("avg_travel_minutes") is not None else None,
                "avg_total_minutes": round(row["avg_total_minutes"], 1) if row.get("avg_total_minutes") is not None else None,
                "delayed_count": row["delayed_count"],
                "avg_rating": round(sum(ratings) / len(ratings), 2) if ratings else None,
            })
        return results

    async def _ratings_grouped_by_service(self, service_center_id: str | None) -> dict[str, list[float]]:
        reviews = await self.db.reviews.find({"is_deleted": {"$ne": True}}).to_list(length=None)
        booking_ids = [r["booking_id"] for r in reviews if r.get("booking_id")]
        bookings = {str(b["_id"]): b for b in await self.booking_repo.find_by_ids(booking_ids)}
        grouped: dict[str, list[float]] = {}
        for r in reviews:
            booking = bookings.get(r.get("booking_id"))
            if not booking:
                continue
            if service_center_id and booking.get("service_center_id") != service_center_id:
                continue
            rating = r.get("service_rating") if r.get("service_rating") is not None else r.get("rating")
            if rating is None:
                continue
            for sid in booking.get("service_ids") or []:
                grouped.setdefault(sid, []).append(rating)
        return grouped
