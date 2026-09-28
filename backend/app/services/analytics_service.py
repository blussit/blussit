import asyncio
from datetime import timedelta

from bson import ObjectId
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
_NOT_DELETED = {"is_deleted": {"$ne": True}}
_COMPLETED = {"$eq": ["$status", "completed"]}


def _count_if(cond) -> dict:
    return {"$sum": {"$cond": [cond, 1, 0]}}


def _sum_if(cond, value) -> dict:
    return {"$sum": {"$cond": [cond, value, 0]}}


def _not_null(expr) -> dict:
    # A missing field is NOT equal to null inside $expr — normalise first.
    return {"$ne": [{"$ifNull": [expr, None]}, None]}


def _truthy(field: str) -> dict:
    # Aggregation treats "" as true; the Python `or` these mirror doesn't.
    return {"$and": [field, {"$ne": [field, ""]}]}


# Group key reproducing `booking.vehicle_type or <its vehicle's type>`:
# quick-booking rows carry the type; legacy saved-vehicle rows are grouped
# by vehicle_id and resolved through the vehicles collection afterwards.
def _vehicle_group_key(prefix: str = "$") -> dict:
    vt, vid = f"{prefix}vehicle_type", f"{prefix}vehicle_id"
    return {"vt": {"$cond": [_truthy(vt), vt, None]}, "vid": {"$cond": [_truthy(vt), None, vid]}}


_JOIN_CHUNK = 2000


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
        """All-time headline numbers. Every booking-derived figure comes out
        of ONE pass over the bookings collection ($facet), and the repeat-
        customer rate is counted on the server — this used to be ~17
        queries, two of which shipped one row per customer to Python."""
        now = now_ist()
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

        # Section 14's explicitly-requested operational KPIs — computed the
        # same way as everywhere else in this codebase: real stored
        # timestamps, never a second manually-tracked counter. The
        # subtraction-based averages are timezone-label-agnostic (a
        # duration between two equally-naive-but-really-UTC instants is
        # correct without from_stored() converting either side first —
        # see StaffDirectoryService.captain_performance for the identical
        # per-captain formulas this mirrors, kept consistent on purpose).
        facet_pipeline = [
            {"$match": _NOT_DELETED},
            {"$facet": {
                "totals": [{"$group": {
                    "_id": None,
                    "total": {"$sum": 1},
                    "completed": _count_if(_COMPLETED),
                    "cancelled": _count_if({"$eq": ["$status", "cancelled"]}),
                    "revenue": _sum_if(_COMPLETED, "$total_amount"),
                    "subscription_revenue": _sum_if({"$and": [_COMPLETED, {"$eq": ["$payment_method", "subscription"]}]}, "$subtotal"),
                    "delayed": _count_if({"$gt": [{"$ifNull": ["$delay_minutes", 0]}, 0]}),
                    "avg_service_minutes": {"$avg": {"$cond": [_COMPLETED, "$actual_duration_minutes", None]}},
                    "avg_travel_minutes": {"$avg": {"$cond": [_COMPLETED, _TRAVEL_MINUTES_EXPR, None]}},
                    "avg_completion_minutes": {"$avg": {"$cond": [_COMPLETED, _TOTAL_MINUTES_EXPR, None]}},
                }}],
                "customers": [
                    {"$group": {"_id": "$customer_id", "n": {"$sum": 1}}},
                    {"$group": {"_id": None, "distinct": {"$sum": 1}, "repeat": _count_if({"$gt": ["$n", 1]})}},
                ],
                "top_services": [
                    {"$unwind": "$service_ids"},
                    {"$group": {"_id": "$service_ids", "bookings": {"$sum": 1}}},
                    {"$sort": {"bookings": -1}},
                    {"$limit": 5},
                ],
                "best_captains": [
                    {"$match": {"status": "completed", "captain_id": {"$ne": None}}},
                    {"$group": {"_id": "$captain_id", "jobs_completed": {"$sum": 1}, "revenue": {"$sum": "$total_amount"}}},
                    {"$sort": {"jobs_completed": -1}},
                    {"$limit": 5},
                ],
                "best_centers": [
                    {"$match": {"status": "completed"}},
                    {"$group": {"_id": "$service_center_id", "jobs_completed": {"$sum": 1}, "revenue": {"$sum": "$total_amount"}}},
                    {"$sort": {"revenue": -1}},
                    {"$limit": 5},
                ],
            }},
        ]
        facet_rows, today_orders, monthly_orders, active_customers, pending_bookings, rating_result, today_capacity_result = await asyncio.gather(
            self.db.bookings.aggregate(facet_pipeline, allowDiskUse=True).to_list(length=1),
            self.booking_repo.count({"created_at": {"$gte": today_start}}),
            self.booking_repo.count({"created_at": {"$gte": month_start}}),
            self.user_repo.count({"role": "customer", "status": "active"}),
            self.booking_repo.count({"status": {"$in": ["pending", "rescheduled"]}}),
            self.db.reviews.aggregate([
                {"$match": _NOT_DELETED},
                {"$group": {"_id": None, "avg": {"$avg": {"$ifNull": ["$captain_rating", "$rating"]}}}},
            ]).to_list(length=1),
            self.db.slot_capacity.aggregate([
                {"$match": {"date": today_start.strftime("%Y-%m-%d")}},
                {"$group": {"_id": None, "capacity": {"$sum": "$capacity"}, "booked": {"$sum": "$booked_count"}}},
            ]).to_list(length=1),
        )
        facet = facet_rows[0] if facet_rows else {}
        totals = (facet.get("totals") or [{}])[0]
        customers = (facet.get("customers") or [{}])[0]
        total_bookings = totals.get("total", 0)
        completed = totals.get("completed", 0)
        cancelled = totals.get("cancelled", 0)
        distinct_customers = customers.get("distinct", 0)
        repeat_rate = (customers.get("repeat", 0) / distinct_customers * 100) if distinct_customers else 0
        avg_rating = round(rating_result[0]["avg"], 2) if rating_result and rating_result[0].get("avg") is not None else None
        today_capacity = today_capacity_result[0]["capacity"] if today_capacity_result else 0
        today_booked = today_capacity_result[0]["booked"] if today_capacity_result else 0
        utilization_pct = round((today_booked / today_capacity) * 100, 1) if today_capacity else None

        def _avg(key: str):
            return round(totals[key], 1) if totals.get(key) is not None else None

        return {
            "total_revenue": round(totals.get("revenue") or 0, 2),
            "todays_orders": today_orders,
            "monthly_orders": monthly_orders,
            "active_customers": active_customers,
            "repeat_customer_rate": round(repeat_rate, 2),
            "subscription_revenue": round(totals.get("subscription_revenue") or 0, 2),
            "top_services": [{"service_id": s["_id"], "bookings": s["bookings"]} for s in facet.get("top_services", [])],
            "best_performing_captains": [{"captain_id": c["_id"], "jobs_completed": c["jobs_completed"], "revenue": round(c["revenue"], 2)} for c in facet.get("best_captains", [])],
            "best_service_centers": [{"service_center_id": c["_id"], "jobs_completed": c["jobs_completed"], "revenue": round(c["revenue"], 2)} for c in facet.get("best_centers", [])],
            "cancellation_rate": round((cancelled / total_bookings * 100), 2) if total_bookings else 0,
            "completion_rate": round((completed / total_bookings * 100), 2) if total_bookings else 0,
            "total_bookings": total_bookings,
            "completed_bookings": completed,
            "pending_bookings": pending_bookings,
            "cancelled_bookings": cancelled,
            "delayed_bookings": totals.get("delayed", 0),
            "avg_service_minutes": _avg("avg_service_minutes"),
            "avg_travel_minutes": _avg("avg_travel_minutes"),
            "avg_completion_minutes": _avg("avg_completion_minutes"),
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
        cached/manually entered). Two grouped aggregations for all centers
        at once (bookings, reviews) instead of five queries per center."""
        centers, _ = await self.center_repo.find_many({"is_active": True}, page=1, page_size=200)
        center_ids = [str(c["_id"]) for c in centers]
        if not center_ids:
            return []
        booking_rows, rating_rows = await asyncio.gather(
            self.db.bookings.aggregate([
                {"$match": {"service_center_id": {"$in": center_ids}, **_NOT_DELETED}},
                {"$group": {
                    "_id": "$service_center_id",
                    "bookings": {"$sum": 1},
                    "completed": _count_if(_COMPLETED),
                    "pending": _count_if({"$in": ["$status", ["pending", "rescheduled"]]}),
                    "delayed": _count_if({"$gt": [{"$ifNull": ["$delay_minutes", 0]}, 0]}),
                }},
            ]).to_list(length=None),
            self.db.reviews.aggregate([
                {"$match": {"service_center_id": {"$in": center_ids}, **_NOT_DELETED}},
                {"$group": {"_id": "$service_center_id", "avg": {"$avg": {"$ifNull": ["$captain_rating", "$rating"]}}, "count": {"$sum": 1}}},
            ]).to_list(length=None),
        )
        bookings = {r["_id"]: r for r in booking_rows}
        ratings = {r["_id"]: r for r in rating_rows}
        results = []
        for center in centers:
            center_id = str(center["_id"])
            b = bookings.get(center_id, {})
            r = ratings.get(center_id)
            results.append({
                "service_center_id": center_id,
                "name": center.get("name"),
                "bookings": b.get("bookings", 0),
                "completed": b.get("completed", 0),
                "pending": b.get("pending", 0),
                "delayed": b.get("delayed", 0),
                "avg_rating": round(r["avg"], 1) if r and r.get("avg") is not None else None,
                "review_count": r["count"] if r else 0,
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
            {"$match": {**base_match, "status": "completed", "completed_by_role": {"$ne": "manager"}}},
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
        busy_captains = await self.booking_repo.count(
            {"service_center_id": service_center_id, "status": {"$in": ["assigned", "captain_on_the_way", "service_started"]}}
        )
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

        Quick-booking bookings carry vehicle_type themselves; older
        saved-vehicle bookings only carry vehicle_id. The sums are grouped
        on the server by (type, or vehicle_id when there is no type) — a
        handful of rows, never one per booking — and the legacy vehicle_id
        groups are folded into their vehicle's type here."""
        match: dict = dict(_NOT_DELETED)
        if service_center_id:
            match["service_center_id"] = service_center_id
        travel = _TRAVEL_MINUTES_EXPR
        total = _TOTAL_MINUTES_EXPR
        has_duration = _not_null("$actual_duration_minutes")
        groups, types, ratings = await asyncio.gather(
            self.db.bookings.aggregate([
                {"$match": match},
                {"$group": {
                    "_id": _vehicle_group_key(),
                    "total": {"$sum": 1},
                    "completed": _count_if(_COMPLETED),
                    "service_sum": _sum_if(has_duration, "$actual_duration_minutes"),
                    "service_n": _count_if(has_duration),
                    "travel_sum": _sum_if(_not_null(travel), travel),
                    "travel_n": _count_if(_not_null(travel)),
                    "total_min_sum": _sum_if(_not_null(total), total),
                    "total_min_n": _count_if(_not_null(total)),
                    "delayed": _count_if({"$gt": [{"$ifNull": ["$delay_minutes", 0]}, 0]}),
                }},
            ], allowDiskUse=True).to_list(length=None),
            self.vehicle_type_repo.find_all_no_paginate(),
            self._ratings_by(
                "captain_rating", service_center_id, {"vehicle_type": 1, "vehicle_id": 1},
                lambda b: [("vt", b["vehicle_type"])] if b.get("vehicle_type") else [("vid", b.get("vehicle_id"))],
            ),
        )
        vehicle_ids = {g["_id"].get("vid") for g in groups if g["_id"].get("vid")} | {key for kind, key in ratings if kind == "vid" and key}
        vehicles = {str(v["_id"]): v for v in await self.vehicle_repo.find_by_ids(list(vehicle_ids))} if vehicle_ids else {}
        type_names = {str(t["_id"]): t.get("name", "Unknown") for t in types}

        def type_of(key: dict):
            return key.get("vt") or (vehicles.get(key.get("vid")) or {}).get("vehicle_type")

        grouped: dict[str, dict] = {}
        for g in groups:
            vt = type_of(g["_id"])
            if not vt:
                continue
            acc = grouped.setdefault(vt, {k: 0 for k in ("total", "completed", "service_sum", "service_n", "travel_sum", "travel_n", "total_min_sum", "total_min_n", "delayed")})
            for k in acc:
                acc[k] += g.get(k) or 0
        rating_totals: dict[str, list[float]] = {}
        for (kind, key), (total, n) in ratings.items():
            vt = type_of({kind: key})
            if vt:
                t = rating_totals.setdefault(vt, [0.0, 0])
                t[0] += total
                t[1] += n

        results = []
        for vt_id, g in sorted(grouped.items(), key=lambda kv: -kv[1]["total"]):
            rating_sum, rating_n = rating_totals.get(vt_id, (0.0, 0))
            results.append({
                "vehicle_type_id": vt_id,
                "vehicle_type_name": type_names.get(vt_id, vt_id),
                "total_bookings": g["total"],
                "completed_bookings": g["completed"],
                "avg_service_minutes": round(g["service_sum"] / g["service_n"], 1) if g["service_n"] else None,
                "avg_actual_minutes": round(g["service_sum"] / g["service_n"], 1) if g["service_n"] else None,
                # Never populated for vehicle types (the per-booking projection
                # this replaced didn't carry duration_minutes); kept for shape.
                "avg_expected_minutes": None,
                "avg_travel_minutes": round(g["travel_sum"] / g["travel_n"], 1) if g["travel_n"] else None,
                "avg_total_minutes": round(g["total_min_sum"] / g["total_min_n"], 1) if g["total_min_n"] else None,
                "delayed_count": g["delayed"],
                "avg_rating": round(rating_sum / rating_n, 2) if rating_n else None,
            })
        return results

    async def _ratings_by(self, rating_field: str, service_center_id: str | None, projection: dict, keys_of) -> dict:
        """{key: (rating sum, rating count)} over every live review whose
        live booking (optionally at one center) yields `keys_of(booking)`.
        The rating is `rating_field`, falling back to the legacy flat
        `rating`. Reviews are streamed and joined to their bookings a chunk
        at a time (one $in query per chunk) — never all reviews or bookings
        in memory, and never a per-review $lookup (measured ~0.3 ms per row
        on the classic engine: minutes at 150k reviews)."""
        booking_filter: dict = dict(_NOT_DELETED)
        if service_center_id:
            booking_filter["service_center_id"] = service_center_id
        totals: dict = {}

        async def flush(chunk: list[tuple[str, float]]) -> None:
            bookings = {
                str(b["_id"]): b
                for b in await self.db.bookings.find(
                    {"_id": {"$in": [ObjectId(bid) for bid, _ in chunk]}, **booking_filter}, projection
                ).to_list(length=None)
            }
            for bid, rating in chunk:
                booking = bookings.get(bid)
                if not booking:
                    continue
                for key in keys_of(booking):
                    t = totals.setdefault(key, [0.0, 0])
                    t[0] += rating
                    t[1] += 1

        chunk: list[tuple[str, float]] = []
        async for r in self.db.reviews.aggregate([
            {"$match": _NOT_DELETED},
            {"$project": {"_id": 0, "booking_id": 1, "rating": {"$ifNull": [f"${rating_field}", "$rating"]}}},
            {"$match": {"rating": {"$ne": None}, "booking_id": {"$type": "string"}}},
        ]):
            if ObjectId.is_valid(r["booking_id"]):
                chunk.append((r["booking_id"], r["rating"]))
            if len(chunk) >= _JOIN_CHUNK:
                await flush(chunk)
                chunk = []
        if chunk:
            await flush(chunk)
        return {k: (t[0], t[1]) for k, t in totals.items()}

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
        rows, rating_rows = await asyncio.gather(
            self.db.bookings.aggregate(pipeline, allowDiskUse=True).to_list(length=None),
            self._ratings_by("service_rating", service_center_id, {"service_ids": 1}, lambda b: b.get("service_ids") or []),
        )
        ratings_by_service = rating_rows

        services = {str(s["_id"]): s.get("name", "Unknown") for s in await self.service_repo.find_all_no_paginate()}
        results = []
        for row in rows:
            sid = row["_id"]
            rating_sum, rating_n = ratings_by_service.get(sid, (0, 0))
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
                "avg_rating": round(rating_sum / rating_n, 2) if rating_n else None,
            })
        return results
