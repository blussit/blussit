"""
The manager's ONE home screen (GET /analytics/manager-dashboard/{center}).

Everything a center manager needs on the first screen, in one round trip:
sales for a chosen period (same numbers as KpiService.manager_overview, so
the RevenueDrillModal list always agrees with the tile), what was washed —
per service and per car type — plans sold, today's schedule load per slot,
captain availability right now, and the operational alarms (needs a
captain, late starts, open issues, open complaints, low stock).

Scale rules, same as the KPI engine:
- every query is scoped to ONE service_center_id and $matches on an indexed
  prefix first ((service_center_id, status, closed_at) for the period's
  completed washes, (service_center_id, status, scheduled_date) for today);
- only aggregated rows come back — one row per (service mix, car type)
  combination, per slot, per captain — never a period's bookings;
- captains are capped (a center has a few dozen; 300 is a hard ceiling).

Center scoping is enforced in the route (ensure_own_center) AND every
query below filters on the center id passed in, so a manager can only ever
read their own center.
"""
import asyncio
from datetime import datetime, timedelta, timezone

from bson import ObjectId
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.models.enums import BookingStatus
from app.services.kpi_service import KpiService, _completed_in, _window_match
from app.utils.money import round_rupees, split_whole_rupees
from app.utils.slots import format_slot_12h, generate_slots
from app.utils.timezone import now_ist

_NOT_DELETED = {"is_deleted": {"$ne": True}}
# A real, on-the-board booking for today's load: everything except the
# never-confirmed (awaiting online payment) and the cancelled.
_ON_BOARD = [
    BookingStatus.PENDING.value,
    BookingStatus.RESCHEDULED.value,
    BookingStatus.ASSIGNED.value,
    BookingStatus.CAPTAIN_ON_THE_WAY.value,
    BookingStatus.SERVICE_STARTED.value,
    BookingStatus.COMPLETED.value,
]
_NEEDS_CAPTAIN = [BookingStatus.PENDING.value, BookingStatus.RESCHEDULED.value]
_ON_JOB = [BookingStatus.CAPTAIN_ON_THE_WAY.value, BookingStatus.SERVICE_STARTED.value]
_ACTIVE = _NEEDS_CAPTAIN + [BookingStatus.ASSIGNED.value, *_ON_JOB]
_CAPTAIN_CAP = 300
_MIX_GROUP_CAP = 2000


def _vehicle_key() -> dict:
    """booking.vehicle_type, or (legacy saved-vehicle bookings) the vehicle
    id to resolve through the vehicles collection afterwards."""
    has_type = {"$and": ["$vehicle_type", {"$ne": ["$vehicle_type", ""]}]}
    return {"vt": {"$cond": [has_type, "$vehicle_type", None]}, "vid": {"$cond": [has_type, None, "$vehicle_id"]}}


class ManagerDashboardService:
    def __init__(self, db: AsyncIOMotorDatabase):
        self.db = db

    async def dashboard(self, service_center_id: str, s: datetime, e: datetime, ps: datetime, pe: datetime) -> dict:
        center = await self.db.service_centers.find_one(
            {"_id": ObjectId(service_center_id)} if ObjectId.is_valid(service_center_id) else {"_id": None},
            {"name": 1, "working_hours_start": 1, "working_hours_end": 1, "slot_duration_minutes": 1, "default_slot_capacity": 1},
        )
        sales, mix, plans, today, captains, ops = await asyncio.gather(
            KpiService(self.db).manager_overview(service_center_id, s, e, ps, pe),
            self._wash_mix(service_center_id, s, e),
            self._plan_sales(service_center_id, s, e),
            self._today_schedule(service_center_id, center),
            self._captain_availability(service_center_id),
            self._ops(service_center_id),
        )
        return {
            "center": {"id": service_center_id, "name": (center or {}).get("name")},
            "period": {"start": s.isoformat(), "end": e.isoformat()},
            "sales": sales,
            "services": mix["services"],
            "vehicle_types": mix["vehicle_types"],
            "washes": mix["totals"],
            "plans": plans,
            "today": today,
            "captains": captains,
            "ops": ops,
        }

    # ------------------------------------------------------------ washes

    async def _wash_mix(self, center_id: str, s: datetime, e: datetime) -> dict:
        """Completed washes in the period, per service and per car type.

        Same population and the same revenue recognition as the Sales tile
        (completed in the window by closed_at, falling back to created_at),
        so the per-service revenue adds up to the booking revenue shown
        above it. A booking stores ONE total for its whole service mix
        (base wash + add-ons); that total is split across its services in
        proportion to each service's list price for the booking's car
        type, in whole rupees (split_whole_rupees), so nothing is lost or
        double counted. Plan washes count as washes but carry ₹0 here —
        the plan's money is counted once, when the plan was sold."""
        rows = await self.db.bookings.aggregate([
            {"$match": _window_match(s, e, {"service_center_id": center_id})},
            {"$match": {"$expr": _completed_in(s, e)}},
            {"$group": {
                "_id": {"sids": "$service_ids", "q": "$service_quantities", **_vehicle_key()},
                "n": {"$sum": 1},
                "revenue": {"$sum": {"$ifNull": ["$total_amount", 0]}},
                "plan_washes": {"$sum": {"$cond": [{"$eq": ["$payment_method", "subscription"]}, 1, 0]}},
                "label": {"$first": "$vehicle_label"},
            }},
            {"$limit": _MIX_GROUP_CAP},
        ], allowDiskUse=True).to_list(length=_MIX_GROUP_CAP)

        service_ids = {sid for r in rows for sid in (r["_id"].get("sids") or []) if isinstance(sid, str)}
        vehicle_ids = {r["_id"].get("vid") for r in rows if r["_id"].get("vid")}
        services, types, vehicles = await asyncio.gather(
            self._docs_by_id(
                self.db.services, service_ids,
                {"name": 1, "is_addon": 1, "price": 1, "vehicle_type_prices": 1},
            ),
            self.db.vehicle_types.find({}, {"name": 1}).to_list(length=200),
            self._docs_by_id(self.db.vehicles, vehicle_ids, {"vehicle_type": 1}),
        )
        type_names = {str(t["_id"]): t.get("name") or "Other" for t in types}

        per_service: dict[str, dict] = {}
        per_type: dict[str, dict] = {}
        total_washes = total_revenue = total_plan = 0
        for r in rows:
            key = r["_id"]
            vt = key.get("vt") or (vehicles.get(key.get("vid")) or {}).get("vehicle_type") or ""
            n = int(r["n"] or 0)
            revenue = round_rupees(r["revenue"])
            total_washes += n
            total_revenue += revenue
            total_plan += int(r["plan_washes"] or 0)

            t = per_type.setdefault(vt or "_other", {
                "vehicle_type_id": vt or None,
                "name": type_names.get(vt) or r.get("label") or "Other",
                "washes": 0, "revenue": 0,
            })
            t["washes"] += n
            t["revenue"] += revenue

            sids = [sid for sid in (key.get("sids") or []) if isinstance(sid, str)]
            if not sids:
                continue
            qty = key.get("q") or {}
            units = [max(1, int(qty.get(sid, 1) or 1)) for sid in sids]
            weights = []
            for sid, u in zip(sids, units):
                doc = services.get(sid) or {}
                price = (doc.get("vehicle_type_prices") or {}).get(vt, doc.get("price")) or 0
                weights.append(float(price) * u)
            shares = split_whole_rupees(revenue, weights) if revenue > 0 else [0] * len(sids)
            for sid, u, share in zip(sids, units, shares):
                doc = services.get(sid) or {}
                row = per_service.setdefault(sid, {
                    "service_id": sid,
                    "name": doc.get("name") or "Retired service",
                    "is_addon": bool(doc.get("is_addon")),
                    "washes": 0, "bookings": 0, "revenue": 0,
                })
                row["washes"] += u * n
                row["bookings"] += n
                row["revenue"] += share

        services_out = sorted(per_service.values(), key=lambda x: (x["is_addon"], -x["washes"], -x["revenue"]))
        types_out = sorted(per_type.values(), key=lambda x: (-x["washes"], -x["revenue"]))
        return {
            "services": services_out,
            "vehicle_types": types_out,
            "totals": {"washes": total_washes, "revenue": total_revenue, "plan_washes": total_plan},
        }

    async def _docs_by_id(self, collection, ids: set, projection: dict) -> dict[str, dict]:
        oids = [ObjectId(i) for i in ids if isinstance(i, str) and ObjectId.is_valid(i)]
        if not oids:
            return {}
        docs = await collection.find({"_id": {"$in": oids}}, projection).to_list(length=len(oids))
        return {str(d["_id"]): d for d in docs}

    # ------------------------------------------------------------ plans

    async def _plan_sales(self, center_id: str, s: datetime, e: datetime) -> dict:
        """Plans sold by THIS center in the period, per plan — the same
        payment_orders population (and center resolution) as the Sales
        tile's plan revenue, so the rows add up to it."""
        from app.services.subscription_service import UserSubscriptionService

        match = {"purpose": "subscription", "status": "paid", "created_at": {"$gte": s, "$lt": e}}
        rows = await self.db.payment_orders.aggregate([
            *UserSubscriptionService._center_orders(match, center_id),
            {"$group": {"_id": "$plan_id", "n": {"$sum": 1}, "paise": {"$sum": {"$ifNull": ["$amount_paise", 0]}}}},
            {"$sort": {"n": -1}},
            {"$limit": 100},
        ]).to_list(length=100)
        plans = await self._docs_by_id(self.db.subscription_plans, {r["_id"] for r in rows}, {"name": 1})
        items = [
            {
                "plan_id": r["_id"],
                "name": (plans.get(r["_id"]) or {}).get("name") or "Plan",
                "sold": int(r["n"] or 0),
                "revenue": round_rupees((r["paise"] or 0) / 100),
            }
            for r in rows
        ]
        return {
            "items": items,
            "sold": sum(i["sold"] for i in items),
            "revenue": sum(i["revenue"] for i in items),
        }

    # ------------------------------------------------------------ today

    async def _today_schedule(self, center_id: str, center: dict | None) -> dict:
        """Today's board per slot: cars booked, visits (seats — a multi-car
        visit is one seat), how many still need a captain, how many are
        done, against the slot's capacity."""
        now = now_ist()
        today_str = now.strftime("%Y-%m-%d")
        # scheduled_date holds the IST calendar day as naive midnight digits.
        day = datetime.strptime(today_str, "%Y-%m-%d")
        rows, cap_docs = await asyncio.gather(
            self.db.bookings.aggregate([
                {"$match": {
                    "service_center_id": center_id,
                    "status": {"$in": _ON_BOARD},
                    "scheduled_date": {"$gte": day, "$lt": day + timedelta(days=1)},
                    **_NOT_DELETED,
                }},
                {"$group": {
                    "_id": "$scheduled_slot",
                    "cars": {"$sum": 1},
                    "visits": {"$addToSet": {"$ifNull": ["$booking_group_id", {"$toString": "$_id"}]}},
                    "unassigned": {"$sum": {"$cond": [{"$in": ["$status", _NEEDS_CAPTAIN]}, 1, 0]}},
                    "in_progress": {"$sum": {"$cond": [{"$in": ["$status", _ON_JOB]}, 1, 0]}},
                    "completed": {"$sum": {"$cond": [{"$eq": ["$status", BookingStatus.COMPLETED.value]}, 1, 0]}},
                }},
            ]).to_list(length=200),
            self.db.slot_capacity.find(
                {"service_center_id": center_id, "date": today_str}, {"slot_key": 1, "capacity": 1, "booked_count": 1, "is_closed": 1}
            ).to_list(length=200),
        )
        by_slot = {r["_id"]: r for r in rows}
        caps = {d["slot_key"]: d for d in cap_docs}

        slot_keys: list[str] = []
        if center:
            from app.services.booking_policy_service import BookingPolicyService

            policy = await BookingPolicyService(self.db).get_policy()
            duration = center.get("slot_duration_minutes") or policy.get("slot_duration_minutes") or 180
            slot_keys = [sl["key"] for sl in generate_slots(
                center.get("working_hours_start", "08:00"), center.get("working_hours_end", "20:00"), duration
            )]
        # Bookings filed under a key that is no longer generated (hours
        # changed, a logged walk-in) still belong on the board.
        extra = sorted(k for k in by_slot if k and k not in slot_keys)
        default_cap = (center or {}).get("default_slot_capacity")

        missing_policy = [k for k in slot_keys if k not in caps]
        distribution: dict = {}
        if missing_policy:
            from app.services.capacity_policy_service import CapacityPolicyService

            try:
                distribution = (await CapacityPolicyService(self.db).get_effective_policy(center_id, today_str)).get("slot_distribution") or {}
            except Exception:  # noqa: BLE001 — capacity is informative here, never fatal
                distribution = {}

        slots = []
        for key in slot_keys + extra:
            r = by_slot.get(key) or {}
            cap_doc = caps.get(key)
            capacity = cap_doc["capacity"] if cap_doc else distribution.get(key, default_cap)
            visits = len(r.get("visits") or [])
            slots.append({
                "key": key,
                "label": format_slot_12h(key),
                "cars": int(r.get("cars") or 0),
                "visits": visits,
                "capacity": int(capacity) if isinstance(capacity, (int, float)) and capacity < 999 else None,
                "unassigned": int(r.get("unassigned") or 0),
                "in_progress": int(r.get("in_progress") or 0),
                "completed": int(r.get("completed") or 0),
                "is_closed": bool(cap_doc and cap_doc.get("is_closed")),
            })
        total_capacity = sum(sl["capacity"] or 0 for sl in slots if not sl["is_closed"])
        total_visits = sum(sl["visits"] for sl in slots)
        return {
            "date": today_str,
            "slots": slots,
            "cars": sum(sl["cars"] for sl in slots),
            "visits": total_visits,
            "capacity": total_capacity or None,
            "load_pct": round(total_visits / total_capacity * 100) if total_capacity else None,
            "unassigned": sum(sl["unassigned"] for sl in slots),
            "in_progress": sum(sl["in_progress"] for sl in slots),
            "completed": sum(sl["completed"] for sl in slots),
        }

    # ------------------------------------------------------------ captains

    async def _captain_availability(self, center_id: str) -> dict:
        """Who can take a job right now. One status per active captain:
        on_job (on the way / washing), available (checked in, not on a
        job), on_leave (approved leave covering today), checked_out, or
        not_checked_in — plus today's job counts."""
        today_str = now_ist().strftime("%Y-%m-%d")
        day = datetime.strptime(today_str, "%Y-%m-%d")
        captains = await self.db.users.find(
            {"role": "captain", "service_center_id": center_id, "status": "active", **_NOT_DELETED},
            {"full_name": 1, "phone": 1, "profile_image": 1},
        ).sort("full_name", 1).limit(_CAPTAIN_CAP).to_list(length=_CAPTAIN_CAP)
        ids = [str(c["_id"]) for c in captains]
        if not ids:
            return {"items": [], "counts": {"total": 0, "available": 0, "on_job": 0, "on_leave": 0, "off_duty": 0}}

        attendance, leaves, jobs = await asyncio.gather(
            self.db.attendance.find(
                {"captain_id": {"$in": ids}, "attendance_date": today_str},
                {"captain_id": 1, "check_in_time": 1, "check_out_time": 1},
            ).to_list(length=len(ids)),
            self.db.leave_requests.find(
                {"captain_id": {"$in": ids}, "status": "approved", "start_date": {"$lte": today_str}, "end_date": {"$gte": today_str}, **_NOT_DELETED},
                {"captain_id": 1},
            ).to_list(length=len(ids) * 2),
            self.db.bookings.aggregate([
                {"$match": {
                    "service_center_id": center_id,
                    "status": {"$in": [*_ACTIVE, BookingStatus.COMPLETED.value]},
                    "scheduled_date": {"$gte": day, "$lt": day + timedelta(days=1)},
                    "captain_id": {"$in": ids},
                    **_NOT_DELETED,
                }},
                {"$group": {
                    "_id": "$captain_id",
                    "jobs": {"$sum": 1},
                    "done": {"$sum": {"$cond": [{"$eq": ["$status", BookingStatus.COMPLETED.value]}, 1, 0]}},
                    "current": {"$max": {"$cond": [{"$in": ["$status", _ON_JOB]}, "$booking_number", None]}},
                }},
            ]).to_list(length=len(ids)),
        )
        att = {a["captain_id"]: a for a in attendance}
        on_leave = {lv["captain_id"] for lv in leaves}
        job_rows = {j["_id"]: j for j in jobs}

        items = []
        counts = {"total": len(captains), "available": 0, "on_job": 0, "on_leave": 0, "off_duty": 0}
        for c in captains:
            cid = str(c["_id"])
            a = att.get(cid) or {}
            j = job_rows.get(cid) or {}
            if j.get("current"):
                state = "on_job"
            elif cid in on_leave:
                state = "on_leave"
            elif a.get("check_in_time") and not a.get("check_out_time"):
                state = "available"
            elif a.get("check_out_time"):
                state = "checked_out"
            else:
                state = "not_checked_in"
            bucket = state if state in ("available", "on_job", "on_leave") else "off_duty"
            counts[bucket] += 1
            items.append({
                "id": cid,
                "name": c.get("full_name") or "Captain",
                "phone": c.get("phone"),
                "photo_url": c.get("profile_image"),
                "state": state,
                "current_booking": j.get("current"),
                "jobs_today": int(j.get("jobs") or 0),
                "done_today": int(j.get("done") or 0),
                "checked_in_at": a.get("check_in_time"),
            })
        order = {"on_job": 0, "available": 1, "not_checked_in": 2, "checked_out": 3, "on_leave": 4}
        items.sort(key=lambda x: (order.get(x["state"], 9), x["name"].lower()))
        return {"items": items, "counts": counts}

    # ------------------------------------------------------------ alarms

    async def _ops(self, center_id: str) -> dict:
        """The things that need a decision now — the same definitions the
        booking queue's scopes use, so each number matches the list it
        links to."""
        now_utc = datetime.now(timezone.utc)
        base = {"service_center_id": center_id, **_NOT_DELETED}
        needs_captain, late_now, open_issues, open_complaints, low_stock, issue_rows = await asyncio.gather(
            self.db.bookings.count_documents({**base, "status": {"$in": _NEEDS_CAPTAIN}}),
            # Assigned, the captain's start time has passed, and he hasn't
            # left yet — the "late start" the reminder sweep chases.
            self.db.bookings.count_documents({
                **base,
                "status": BookingStatus.ASSIGNED.value,
                "$expr": {"$lt": [{"$ifNull": ["$estimated_start_at", "$slot_start"]}, now_utc]},
            }),
            self.db.bookings.count_documents({
                **base,
                "status": {"$in": [BookingStatus.AWAITING_PAYMENT.value, *_ACTIVE]},
                "issue_flag": {"$nin": [None, ""]},
                "issue_resolved": {"$ne": True},
            }),
            self.db.complaints.count_documents({**base, "status": {"$in": ["open", "in_progress"]}}),
            self.db.inventory.count_documents({
                **base, "$expr": {"$lte": ["$quantity_available", {"$ifNull": ["$reorder_level", 0]}]},
            }),
            self.db.bookings.find(
                {
                    **base,
                    "status": {"$in": [BookingStatus.AWAITING_PAYMENT.value, *_ACTIVE]},
                    "issue_flag": {"$nin": [None, ""]},
                    "issue_resolved": {"$ne": True},
                },
                {
                    "booking_number": 1, "issue_flag": 1, "scheduled_date": 1, "scheduled_slot": 1, "customer_id": 1,
                    "vehicle_type": 1, "vehicle_label": 1, "service_ids": 1, "combo_id": 1,
                },
            ).sort("scheduled_date", 1).limit(5).to_list(length=5),
        )
        # Car type + wash on each flagged row ("BK0038 · Sedan · Star Wash")
        # — three batched lookups over at most five rows, never one per row.
        types, services, combos = await asyncio.gather(
            self._docs_by_id(self.db.vehicle_types, {b.get("vehicle_type") for b in issue_rows}, {"name": 1}),
            self._docs_by_id(self.db.services, {sid for b in issue_rows for sid in (b.get("service_ids") or [])}, {"name": 1}),
            self._docs_by_id(self.db.combo_offers, {b.get("combo_id") for b in issue_rows}, {"name": 1}),
        )

        def _service_names(b: dict) -> list[str]:
            combo = combos.get(b.get("combo_id") or "")
            if combo:
                return [combo.get("name") or "Combo"]
            return [services[sid].get("name") or "" for sid in (b.get("service_ids") or []) if sid in services]

        return {
            "needs_captain": needs_captain,
            "late_starts": late_now,
            "open_issues": open_issues,
            "open_complaints": open_complaints,
            "low_stock": low_stock,
            "issues": [
                {
                    "id": str(b["_id"]),
                    "booking_number": b.get("booking_number"),
                    "issue_flag": b.get("issue_flag"),
                    "scheduled_date": b["scheduled_date"].strftime("%Y-%m-%d") if isinstance(b.get("scheduled_date"), datetime) else None,
                    "scheduled_slot": b.get("scheduled_slot"),
                    "slot_label": format_slot_12h(b.get("scheduled_slot")),
                    "vehicle_type_name": (types.get(b.get("vehicle_type") or "") or {}).get("name") or b.get("vehicle_label"),
                    "service_names": _service_names(b),
                }
                for b in issue_rows
            ],
        }
