"""
Management KPI engine behind the admin dashboard's analytics tabs.

Design notes, deliberately stated:
- Every metric derived from bookings/users/reviews/complaints is computed
  from REAL stored documents (same source of truth as the rest of the
  app). Nothing operational is manually entered.
- Costs, marketing spend and targets are NOT tracked anywhere in the
  system, so they live in a single admin-editable `business_settings`
  document; every figure derived from them (contribution, break-even,
  CAC, ROAS...) is only as good as those inputs and the frontend labels
  them accordingly.
- Scale: every section is a MongoDB aggregation that $matches on an
  indexed field first and returns only aggregated rows. No section ever
  pulls a period's (let alone all-time) bookings into the 512 MiB
  container. The definitions are the original load-everything-in-Python
  ones, pinned number-for-number by tests/test_scale_parity.py against a
  frozen copy of that code (tests/scale_legacy_reference.py).
- Period comparison: every section computes the same numbers for the
  previous period of EQUAL length ending where the current one starts,
  so "vs previous" is always apples to apples.
"""
import asyncio
import math
from datetime import datetime, timedelta, timezone

from bson import ObjectId
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.core.exceptions import BadRequestException
from app.services.subscription_service import PLAN_ORDER_PURPOSES, custom_plan_refunds
from app.utils.timezone import now_ist

_ACTIVE_STATUSES = ["pending", "assigned", "on_the_way", "in_progress", "rescheduled"]

_DEFAULT_SETTINGS = {
    "variable_cost_per_wash": 90.0,
    "fixed_cost_monthly": 0.0,
    "kit_cost": 0.0,
    "kits_count": 1,
    "targets": {
        "washes_per_captain_per_day": 5.0,
        "repeat_rate_pct": 40.0,
        "capacity_utilisation_pct": 70.0,
        "avg_rating": 4.5,
        "cac": 200.0,
    },
    # [{"date": "YYYY-MM-DD", "source": "instagram|facebook|organic|referral|offline|society|other",
    #   "campaign": str, "spend": float, "leads": int, "customers": int, "revenue": float}]
    "marketing_entries": [],
}

_MARKETING_TEXT_FIELDS = ("date", "source", "campaign")
_MARKETING_NUMBER_FIELDS = ("spend", "leads", "customers", "revenue")


def _is_number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _validated_settings(payload: dict) -> dict:
    """The known keys of a settings update, each checked against the JSON
    type of its default. Values used to be stored untyped — one
    {"targets": "oops"} (or a NaN, which JSON bodies may carry) and every
    later read of the dashboard answered 500 until someone fixed the doc
    by hand. Unknown keys are dropped, as before."""
    out: dict = {}
    for key, default in _DEFAULT_SETTINGS.items():
        if key not in payload:
            continue
        value = payload[key]
        if isinstance(default, (int, float)):
            if not _is_number(value):
                raise BadRequestException(f"{key} must be a number.")
        elif isinstance(default, dict):
            if not isinstance(value, dict) or any(not _is_number(v) for k, v in value.items() if k in default):
                raise BadRequestException(f"{key} must be a set of numbers.")
            value = {k: v for k, v in value.items() if k in default}
        elif isinstance(default, list):
            if not isinstance(value, list) or not all(isinstance(entry, dict) for entry in value):
                raise BadRequestException(f"{key} must be a list of entries.")
            for entry in value:
                if any(entry.get(f) is not None and not isinstance(entry[f], str) for f in _MARKETING_TEXT_FIELDS) or any(
                    entry.get(f) is not None and not _is_number(entry[f]) for f in _MARKETING_NUMBER_FIELDS
                ):
                    raise BadRequestException("Each marketing entry needs text date/source/campaign and numeric spend/leads/customers/revenue.")
        out[key] = value
    return out


_DAY_MS = 86_400_000


def _aware(dt: datetime) -> datetime:
    """Mongo hands back naive datetimes that are really UTC — make them
    explicitly aware so they compare safely against aware IST bounds."""
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt


def _pct(part: float, whole: float) -> float | None:
    return round(part / whole * 100, 1) if whole else None


def _growth(current: float, previous: float) -> float | None:
    """% change vs the previous period; None when there is no baseline."""
    if not previous:
        return None
    return round((current - previous) / previous * 100, 1)


def _rupees(v: float) -> float:
    return round(v or 0, 2)


def resolve_period(period: str | None, start: str | None, end: str | None):
    """Returns (cur_start, cur_end, prev_start, prev_end) as aware-IST
    datetimes. Named periods are IST calendar ranges; custom start/end are
    inclusive dates."""
    now = now_ist()
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    if start and end:
        try:
            s = datetime.strptime(start, "%Y-%m-%d").replace(tzinfo=today.tzinfo)
            e = datetime.strptime(end, "%Y-%m-%d").replace(tzinfo=today.tzinfo) + timedelta(days=1)
        except ValueError:
            raise BadRequestException("Dates must be YYYY-MM-DD")
        if e <= s:
            raise BadRequestException("End date must not be before start date")
    elif period == "today" or period is None:
        s, e = today, today + timedelta(days=1)
    elif period == "yesterday":
        s, e = today - timedelta(days=1), today
    elif period == "7d":
        s, e = today - timedelta(days=6), today + timedelta(days=1)
    elif period == "30d":
        s, e = today - timedelta(days=29), today + timedelta(days=1)
    elif period == "this_month":
        s, e = today.replace(day=1), today + timedelta(days=1)
    elif period == "last_month":
        first_this = today.replace(day=1)
        s = (first_this - timedelta(days=1)).replace(day=1)
        e = first_this
    else:
        raise BadRequestException(f"Unknown period '{period}'")
    length = e - s
    return s, e, s - length, s


# ---------------------------------------------------------------- pipeline pieces
# Expression builders shared by every section. Each mirrors one Python rule
# of the original engine exactly (field truthiness included).


def _truthy(field: str) -> dict:
    # Aggregation treats "" as true; Python (the original definition) doesn't.
    return {"$and": [field, {"$ne": [field, ""]}]}


def _between(expr, s, e) -> dict:
    return {"$and": [{"$gte": [expr, s]}, {"$lt": [expr, e]}]}


def _created_in(s, e) -> dict:
    return _between("$created_at", s, e)


def _completed_in(s, e) -> dict:
    """Revenue is recognized on the COMPLETION date (AUDIT.md M3); a
    completed booking without closed_at falls back to created_at."""
    return {"$and": [{"$eq": ["$status", "completed"]}, _between({"$ifNull": ["$closed_at", "$created_at"]}, s, e)]}


def _count_if(cond) -> dict:
    return {"$sum": {"$cond": [cond, 1, 0]}}


def _sum_if(cond, value) -> dict:
    return {"$sum": {"$cond": [cond, value, 0]}}


def _not_null(field: str) -> dict:
    # {$ne: ["$f", null]} is TRUE for a missing field inside $expr — route it
    # through $ifNull so "missing" and "null" both read as absent.
    return {"$ne": [{"$ifNull": [field, None]}, None]}


def _minutes_between(start: str, end: str) -> dict:
    return {"$divide": [{"$subtract": [end, start]}, 60000]}


def _window_match(s, e, extra: dict | None = None) -> dict:
    """Bookings CREATED in the window, plus bookings COMPLETED in the window
    (so revenue lands on the completion date even when the booking was
    created earlier).

    The shared predicates are repeated inside each branch on purpose: a
    ROOTED $or is planned branch by branch (SUBPLAN), each on its best index
    (created_at_-1 / status_1_closed_at_-1, or their service_center_id
    twins). With the predicates outside, the planner enumerated a capped set
    of whole-query plans and, measured at 100k bookings, scanned every
    completed booking of a center for the second branch."""
    common: dict = {"is_deleted": {"$ne": True}, **(extra or {})}
    return {
        "$or": [
            {"created_at": {"$gte": s, "$lt": e}, **common},
            {"status": "completed", "closed_at": {"$gte": s, "$lt": e}, **common},
        ]
    }


_CHUNK = 2000


def _tz(dt: datetime) -> str:
    offset = dt.utcoffset() or timedelta(0)
    minutes = int(offset.total_seconds() // 60)
    sign = "+" if minutes >= 0 else "-"
    return f"{sign}{abs(minutes) // 60:02d}:{abs(minutes) % 60:02d}"


class KpiService:
    def __init__(self, db: AsyncIOMotorDatabase):
        self.db = db

    # ---------------------------------------------------------------- helpers

    async def _agg_one(self, collection, pipeline: list[dict]) -> dict:
        rows = await collection.aggregate(pipeline, allowDiskUse=True).to_list(length=1)
        return rows[0] if rows else {}

    async def _window_totals(self, s, e, extra: dict | None = None) -> dict:
        """Every per-window booking count/sum the sections use, in one pass
        over the window's bookings. `bookings` counts by created_at,
        revenue/completed by completion date."""
        created, completed = _created_in(s, e), _completed_in(s, e)
        has_travel = {"$and": [completed, "$heading_at", "$vehicle_verified_at"]}
        has_service = {"$and": [completed, "$actual_duration_minutes"]}
        row = await self._agg_one(self.db.bookings, [
            {"$match": _window_match(s, e, extra)},
            {"$group": {
                "_id": None,
                "bookings": _count_if(created),
                "completed": _count_if(completed),
                "revenue": _sum_if(completed, "$total_amount"),
                "cancelled": _count_if({"$and": [created, {"$eq": ["$status", "cancelled"]}]}),
                "captain_cancelled": _count_if({"$and": [created, {"$eq": ["$cancelled_by_role", "captain"]}]}),
                "started": _count_if({"$and": [created, _truthy("$captain_start_stage")]}),
                "on_time": _count_if({"$and": [created, {"$in": ["$captain_start_stage", ["early", "on_time"]]}]}),
                "subscription_revenue": _sum_if({"$and": [completed, {"$eq": ["$payment_method", "subscription"]}]}, "$subtotal"),
                "subscription_charged": _sum_if({"$and": [completed, {"$eq": ["$payment_method", "subscription"]}]}, "$total_amount"),
                "travel_sum": _sum_if(has_travel, _minutes_between("$heading_at", "$vehicle_verified_at")),
                "travel_n": _count_if(has_travel),
                "service_sum": _sum_if(has_service, "$actual_duration_minutes"),
                "service_n": _count_if(has_service),
                # Founder: a manager's discount and a tip both count in the
                # job's total (so in revenue) and go to nobody's wallet —
                # shown as their own lines so the admin sees them.
                "manager_discounts": _sum_if(completed, {"$ifNull": ["$manager_discount", 0]}),
                "tips": _sum_if(completed, {"$ifNull": ["$tip_amount", 0]}),
            }},
        ])
        keys = ("bookings", "completed", "revenue", "cancelled", "captain_cancelled", "started", "on_time",
                "subscription_revenue", "subscription_charged", "travel_sum", "travel_n", "service_sum", "service_n",
                "manager_discounts", "tips")
        return {k: row.get(k) or 0 for k in keys}

    async def _customers_seen_before(self, customer_ids, before: datetime) -> set:
        """Which of these customers have ANY live booking created before
        `before` — i.e. their first booking predates it. Checked in chunks
        with one $in query each on customer_id_1_created_at_-1 (a $lookup per
        customer measured ~0.8 ms each at 100k bookings)."""
        ids = [c for c in customer_ids if c is not None]
        found: set = set()
        for i in range(0, len(ids), _CHUNK):
            rows = await self.db.bookings.aggregate([
                {"$match": {"customer_id": {"$in": ids[i:i + _CHUNK]}, "created_at": {"$lt": before}, "is_deleted": {"$ne": True}}},
                {"$group": {"_id": "$customer_id"}},
            ]).to_list(length=None)
            found.update(r["_id"] for r in rows)
        return found

    async def _customer_split(self, s, e) -> dict:
        """customers: distinct customers with a booking CREATED in [s, e);
        repeat: those of them with a live booking created before s;
        new_revenue: completed-in-window revenue from customers with none.
        One row per customer active in the window (not per booking)."""
        created, completed = _created_in(s, e), _completed_in(s, e)
        rows = await self.db.bookings.aggregate([
            {"$match": _window_match(s, e)},
            {"$group": {"_id": "$customer_id", "created": _count_if(created), "revenue": _sum_if(completed, "$total_amount")}},
        ], allowDiskUse=True).to_list(length=None)
        prior = await self._customers_seen_before([r["_id"] for r in rows], s)
        created_rows = [r for r in rows if r["created"]]
        return {
            "customers": len(created_rows),
            "repeat": sum(1 for r in created_rows if r["_id"] in prior),
            "new_revenue": sum(r["revenue"] or 0 for r in rows if r["_id"] not in prior),
        }

    async def _ratings(self, s, e) -> tuple[int, dict]:
        """(reviews in window, {rating value: count}) — ratings are the
        captain rating, falling back to the legacy flat one; a 0/absent
        rating counts as a review but not as a rating."""
        rows = await self.db.reviews.aggregate([
            {"$match": {"created_at": {"$gte": s, "$lt": e}, "is_deleted": {"$ne": True}}},
            {"$group": {"_id": {"$cond": ["$captain_rating", "$captain_rating", "$rating"]}, "n": {"$sum": 1}}},
        ]).to_list(length=None)
        total = sum(r["n"] for r in rows)
        return total, {r["_id"]: r["n"] for r in rows if r["_id"]}

    async def _plan_revenue(self, s, e) -> float:
        """Money actually paid for a SUBSCRIPTION/PLAN in the window —
        entirely separate from booking revenue (a plan is paid for once,
        up front, never per-wash — see PaymentService.manager_subscription_offer).
        Same source of truth and same `created_at` windowing PaymentService.
        admin_collections already uses for its own subscriptions figure, so
        this dashboard number and that report never disagree."""
        rows = await self.db.payment_orders.aggregate([
            {"$match": {"purpose": {"$in": PLAN_ORDER_PURPOSES}, "status": "paid", "created_at": {"$gte": s, "$lt": e}}},
            {"$group": {"_id": None, "amount_paise": {"$sum": "$amount_paise"}}},
        ]).to_list(length=1)
        return _rupees((rows[0]["amount_paise"] if rows else 0) / 100)

    async def _plan_refunds(self, s, e, service_center_id: str | None = None) -> float:
        """Custom-plan cars refunded to the wallet in the window (PLANS-2) —
        shown beside the gross plan revenue as `plan_refunds`, with
        `plan_revenue_net` = plan_revenue − plan_refunds."""
        return _rupees((await custom_plan_refunds(self.db, s, e, service_center_id))["amount"])

    async def _society_revenue(self, s, e, service_center_id: str | None = None) -> float:
        """Society plan money in the window — its own line next to booking
        and monthly-pass revenue (SOC-1): online society payments (paid
        orders) + society cash (society_payments). See society_revenue."""
        from app.services.society_service import society_revenue

        return _rupees((await society_revenue(self.db, s, e, service_center_id))["amount"])

    async def get_settings(self) -> dict:
        doc = await self.db.business_settings.find_one({"_id": "singleton"})
        merged = {**_DEFAULT_SETTINGS, **(doc or {})}
        merged["targets"] = {**_DEFAULT_SETTINGS["targets"], **(merged.get("targets") or {})}
        merged.pop("_id", None)
        return merged

    async def update_settings(self, payload: dict) -> dict:
        allowed = _validated_settings(payload)
        await self.db.business_settings.update_one(
            {"_id": "singleton"}, {"$set": allowed}, upsert=True
        )
        return await self.get_settings()

    def _marketing_entries_between(self, settings: dict, s, e) -> list[dict]:
        out = []
        for entry in settings.get("marketing_entries", []):
            try:
                d = datetime.strptime(str(entry.get("date", "")), "%Y-%m-%d").replace(tzinfo=s.tzinfo)
            except ValueError:
                continue
            if s <= d < e:
                out.append(entry)
        return out

    # ---------------------------------------------------------------- sections

    async def _overview_block(self, s, e) -> dict:
        totals, split = await asyncio.gather(self._window_totals(s, e), self._customer_split(s, e))
        return {
            "bookings": totals["bookings"],
            "completed": totals["completed"],
            "revenue": _rupees(totals["revenue"]),
            "completion_rate": _pct(totals["completed"], totals["bookings"]),
            "repeat_customer_rate": _pct(split["repeat"], split["customers"]),
            # Already inside revenue — listed so they're visible.
            "manager_discounts": _rupees(totals["manager_discounts"]),
            "tips": _rupees(totals["tips"]),
        }

    async def overview(self, s, e, ps, pe) -> dict:
        (
            cur_b, prev_b, new_cur, new_prev, plan_rev_cur, plan_rev_prev, soc_cur, soc_prev, settings, ref_cur, ref_prev,
        ) = await asyncio.gather(
            self._overview_block(s, e),
            self._overview_block(ps, pe),
            self.db.users.count_documents({"role": "customer", "created_at": {"$gte": s, "$lt": e}}),
            self.db.users.count_documents({"role": "customer", "created_at": {"$gte": ps, "$lt": pe}}),
            self._plan_revenue(s, e),
            self._plan_revenue(ps, pe),
            self._society_revenue(s, e),
            self._society_revenue(ps, pe),
            self.get_settings(),
            self._plan_refunds(s, e),
            self._plan_refunds(ps, pe),
        )

        def money(block: dict, plan_rev: float, soc_rev: float, refunds: float) -> dict:
            # plan_revenue / combined_revenue stay gross; refunds + net beside.
            return {
                "plan_revenue": plan_rev, "plan_refunds": refunds, "plan_revenue_net": _rupees(plan_rev - refunds),
                "society_revenue": soc_rev, "combined_revenue": _rupees(block["revenue"] + plan_rev + soc_rev),
            }

        return {
            "current": {**cur_b, "new_customers": new_cur, **money(cur_b, plan_rev_cur, soc_cur, ref_cur)},
            "previous": {**prev_b, "new_customers": new_prev, **money(prev_b, plan_rev_prev, soc_prev, ref_prev)},
            "targets": settings["targets"],
            "alerts": await self._alerts(s, e, ps, pe, cur_b, prev_b, settings),
        }

    async def manager_overview(self, service_center_id: str, s, e, ps, pe) -> dict:
        """Center-scoped sibling of overview() — this manager's own
        bookings + plan revenue for their center, current vs previous-
        equal-length period. Booking side is a plain service_center_id
        filter (bookings already carry it); plan side goes through
        UserSubscriptionService.center_plan_revenue since payment_orders
        doesn't uniformly carry the field itself (see that method's own
        docstring for which kinds do and don't)."""
        from app.services.subscription_service import UserSubscriptionService

        extra = {"service_center_id": service_center_id}
        subs = UserSubscriptionService(self.db)
        (
            cur, prev, (cur_plan_rev, cur_plans_sold), (prev_plan_rev, prev_plans_sold), cur_soc, prev_soc, cur_ref, prev_ref,
        ) = await asyncio.gather(
            self._window_totals(s, e, extra),
            self._window_totals(ps, pe, extra),
            subs.center_plan_revenue(service_center_id, s, e),
            subs.center_plan_revenue(service_center_id, ps, pe),
            self._society_revenue(s, e, service_center_id),
            self._society_revenue(ps, pe, service_center_id),
            self._plan_refunds(s, e, service_center_id),
            self._plan_refunds(ps, pe, service_center_id),
        )

        def block(t):
            return {
                "bookings": t["bookings"], "completed": t["completed"], "revenue": _rupees(t["revenue"]),
                "manager_discounts": _rupees(t["manager_discounts"]), "tips": _rupees(t["tips"]),
            }

        def money(b: dict, sold: int, plan_rev: float, soc_rev: float, refunds: float) -> dict:
            return {
                "plans_sold": sold, "plan_revenue": plan_rev, "plan_refunds": refunds, "plan_revenue_net": _rupees(plan_rev - refunds),
                "society_revenue": soc_rev, "combined_revenue": _rupees(b["revenue"] + plan_rev + soc_rev),
            }

        cur_b, prev_b = block(cur), block(prev)
        return {
            "current": {**cur_b, **money(cur_b, cur_plans_sold, cur_plan_rev, cur_soc, cur_ref)},
            "previous": {**prev_b, **money(prev_b, prev_plans_sold, prev_plan_rev, prev_soc, prev_ref)},
        }

    async def _alerts(self, s, e, ps, pe, cur_b, prev_b, settings) -> list[dict]:
        """Only meaningful exceptions — an empty list is the good outcome."""
        alerts: list[dict] = []
        cr, pr = cur_b.get("repeat_customer_rate"), prev_b.get("repeat_customer_rate")
        if cr is not None and pr is not None and pr - cr >= 5:
            alerts.append({"severity": "warn", "text": f"Repeat customer rate dropped {round(pr - cr, 1)} points vs previous period"})
        tomorrow = (now_ist() + timedelta(days=1)).date().isoformat()
        unresolved, capacity, (_reviews, ratings) = await asyncio.gather(
            self.db.complaints.count_documents({"status": {"$in": ["open", "in_progress"]}, "is_deleted": {"$ne": True}}),
            self._agg_one(self.db.slot_capacity, [
                {"$match": {"date": tomorrow}},
                {"$group": {"_id": None, "cap": {"$sum": "$capacity"}, "booked": {"$sum": "$booked_count"}}},
            ]),
            self._ratings(s, e),
        )
        if unresolved:
            alerts.append({"severity": "warn", "text": f"{unresolved} customer complaint(s) unresolved"})
        # Tomorrow's booked share of capacity — early demand warning.
        cap, booked = capacity.get("cap") or 0, capacity.get("booked") or 0
        if cap and booked / cap < 0.4:
            alerts.append({"severity": "info", "text": f"Tomorrow only {_pct(booked, cap)}% of capacity is booked"})
        if cur_b.get("completion_rate") is not None and cur_b["completion_rate"] < 85 and cur_b["bookings"] >= 5:
            alerts.append({"severity": "warn", "text": f"Completion rate at {cur_b['completion_rate']}% this period"})
        n = sum(ratings.values())
        if n:
            avg = sum(v * c for v, c in ratings.items()) / n
            if avg < 4.0:
                alerts.append({"severity": "warn", "text": f"Average rating this period is {round(avg, 1)}★"})
        return alerts

    async def business(self, s, e, ps, pe) -> dict:
        window = _window_match(s, e)
        created, completed = _created_in(s, e), _completed_in(s, e)
        tz = _tz(s)
        cur, prev, split, trend, mix_rows, vt_rows, services, vehicle_types = await asyncio.gather(
            self._window_totals(s, e),
            self._window_totals(ps, pe),
            self._customer_split(s, e),
            self._agg_one(self.db.bookings, [
                {"$match": window},
                {"$facet": {
                    "created": [
                        {"$match": {"$expr": created}},
                        {"$group": {"_id": {"$dateToString": {"format": "%Y-%m-%d", "date": "$created_at", "timezone": tz}}, "n": {"$sum": 1}}},
                    ],
                    "revenue": [
                        {"$match": {"$expr": completed}},
                        {"$group": {
                            "_id": {"$dateToString": {"format": "%Y-%m-%d", "date": {"$ifNull": ["$closed_at", "$created_at"]}, "timezone": tz}},
                            "amount": {"$sum": "$total_amount"},
                        }},
                    ],
                }},
            ]),
            # Service mix — a multi-service booking's amount is split evenly
            # across its services (never double-counted).
            self.db.bookings.aggregate([
                {"$match": _window_match(s, e, {"service_ids.0": {"$exists": True}})},
                {"$project": {
                    "service_ids": 1,
                    "c": created,
                    "d": completed,
                    "x": {"$eq": ["$status", "cancelled"]},
                    "share": {"$divide": ["$total_amount", {"$size": "$service_ids"}]},
                }},
                {"$unwind": "$service_ids"},
                {"$group": {
                    "_id": "$service_ids",
                    "bookings": _count_if("$c"),
                    "cancelled": _count_if({"$and": ["$c", "$x"]}),
                    "revenue": _sum_if("$d", "$share"),
                }},
            ], allowDiskUse=True).to_list(length=None),
            # Vehicle-type mix. Quick-booking bookings carry the type
            # themselves; older saved-vehicle ones are grouped by vehicle_id
            # and resolved through the vehicles collection below.
            self.db.bookings.aggregate([
                {"$match": window},
                {"$group": {
                    "_id": {
                        "vt": {"$cond": [_truthy("$vehicle_type"), "$vehicle_type", None]},
                        "vid": {"$cond": [_truthy("$vehicle_type"), None, "$vehicle_id"]},
                        "label": "$vehicle_label",
                    },
                    "bookings": _count_if(created),
                    "revenue": _sum_if(completed, "$total_amount"),
                }},
            ], allowDiskUse=True).to_list(length=None),
            self.db.services.find({}, {"name": 1}).to_list(length=None),
            self.db.vehicle_types.find({}, {"name": 1}).to_list(length=None),
        )
        revenue, prev_revenue = _rupees(cur["revenue"]), _rupees(prev["revenue"])

        # Daily trend series across the period (booking counts + revenue).
        series: dict[str, dict] = {}
        day = s
        while day < e:
            series[day.date().isoformat()] = {"date": day.date().isoformat(), "bookings": 0, "revenue": 0.0}
            day += timedelta(days=1)
        for r in trend.get("created", []):
            if r["_id"] in series:
                series[r["_id"]]["bookings"] = r["n"]
        for r in trend.get("revenue", []):
            if r["_id"] in series:
                series[r["_id"]]["revenue"] = _rupees(r["amount"])

        svc_names = {str(x["_id"]): x.get("name", "?") for x in services}
        service_mix = [
            {
                "name": svc_names.get(r["_id"], "Unknown"),
                "bookings": r["bookings"],
                "revenue": _rupees(r["revenue"]),
                "cancelled": r["cancelled"],
            }
            for r in mix_rows
        ]
        service_mix.sort(key=lambda r: (-r["revenue"], r["name"]))
        for row in service_mix:
            row["aov"] = _rupees(row["revenue"] / row["bookings"]) if row["bookings"] else 0
            row["cancellation_rate"] = _pct(row["cancelled"], row["bookings"])

        v_ids = {g["_id"]["vid"] for g in vt_rows if isinstance(g["_id"].get("vid"), str) and ObjectId.is_valid(g["_id"]["vid"])}
        vehicles = (
            {str(v["_id"]): v for v in await self.db.vehicles.find({"_id": {"$in": [ObjectId(i) for i in v_ids]}}, {"vehicle_type": 1}).to_list(length=None)}
            if v_ids else {}
        )
        vt_names = {str(x["_id"]): x.get("name", "?") for x in vehicle_types}
        vt_mix: dict[str, dict] = {}
        for g in vt_rows:
            key = g["_id"]
            v = vehicles.get(key.get("vid") or "")
            vt_id = key.get("vt") or (v.get("vehicle_type") if v else None)
            vt = vt_names.get(str(vt_id or ""), key.get("label") or "Unknown")
            row = vt_mix.setdefault(vt, {"name": vt, "bookings": 0, "revenue": 0.0})
            row["bookings"] += g["bookings"]
            row["revenue"] += g["revenue"] or 0
        vehicle_mix = sorted(vt_mix.values(), key=lambda r: (-r["bookings"], r["name"]))
        for row in vehicle_mix:
            row["revenue"] = _rupees(row["revenue"])
            row["aov"] = _rupees(row["revenue"] / row["bookings"]) if row["bookings"] else 0

        # Revenue quality: new-customer vs repeat vs subscription-covered.
        new_rev = _rupees(split["new_revenue"])
        repeat_rev = _rupees(revenue - new_rev)
        sub_rev = _rupees(cur["subscription_revenue"])

        return {
            "totals": {
                "bookings": cur["bookings"],
                "completed": cur["completed"],
                "cancelled": cur["cancelled"],
                "completion_rate": _pct(cur["completed"], cur["bookings"]),
                "aov": _rupees(revenue / cur["completed"]) if cur["completed"] else 0,
                "revenue": revenue,
                "revenue_growth": _growth(revenue, prev_revenue),
                "booking_growth": _growth(cur["bookings"], prev["bookings"]),
            },
            "trend": list(series.values()),
            "service_mix": service_mix,
            "vehicle_mix": vehicle_mix,
            "revenue_quality": {
                "total": revenue,
                "new_customer_revenue": new_rev,
                "repeat_customer_revenue": repeat_rev,
                "repeat_revenue_pct": _pct(repeat_rev, revenue),
                "subscription_revenue": sub_rev,
                # Plan bookings add only what they charged (paid add-ons) to
                # revenue, so that — not their pre-discount subtotal — is what
                # comes back out.
                "one_time_revenue": _rupees(max(0.0, revenue - cur["subscription_charged"])),
            },
        }

    async def _lifetime_customer_stats(self, now: datetime) -> dict:
        """All-time per-customer booking history reduced to one row on the
        server: $group per customer (count, first, second, last booking),
        then one more $group into the retention/churn/CLV counters.

        The original Python definitions these reproduce:
          eligible(N)  = (now - first).days >= N      <=> first <= now - N days
          again(N)     = some later booking with (b - first).days <= N
                         <=> second - first < (N + 1) days
          churned      = (now - last).days > 60       <=> last <= now - 61 days
          avg gap      = sum of consecutive gaps / count = sum(last - first) / sum(n - 1)
        """
        has_second = {"$gte": ["$n", 2]}

        def eligible(days: int) -> dict:
            return {"$lte": ["$first", now - timedelta(days=days)]}

        def again(days: int) -> dict:
            return {"$and": [eligible(days), has_second, {"$lt": ["$second_gap", (days + 1) * _DAY_MS]}]}

        counters: dict = {}
        for d in (30, 60, 90):
            counters[f"eligible_{d}"] = _count_if(eligible(d))
            counters[f"again_{d}"] = _count_if(again(d))
        row = await self._agg_one(self.db.bookings, [
            {"$match": {"is_deleted": {"$ne": True}}},
            {"$group": {
                "_id": "$customer_id",
                "n": {"$sum": 1},
                "first": {"$min": "$created_at"},
                "last": {"$max": "$created_at"},
                "first_two": {"$minN": {"input": "$created_at", "n": 2}},
                "revenue": _sum_if({"$eq": ["$status", "completed"]}, "$total_amount"),
            }},
            {"$project": {
                "n": 1, "first": 1, "last": 1, "revenue": 1,
                "second_gap": {"$subtract": [{"$max": "$first_two"}, "$first"]},
            }},
            {"$group": {
                "_id": None,
                "customers": {"$sum": 1},
                "bookings": {"$sum": "$n"},
                "repeat": _count_if({"$gt": ["$n", 1]}),
                "gap_ms": {"$sum": {"$subtract": ["$last", "$first"]}},
                "gap_n": {"$sum": {"$subtract": ["$n", 1]}},
                "revenue": {"$sum": "$revenue"},
                "matured_repeat": _count_if({"$and": [eligible(30), {"$gt": ["$n", 1]}]}),
                "churned": _count_if({"$lte": ["$last", now - timedelta(days=61)]}),
                **counters,
            }},
        ])
        return row

    async def customers(self, s, e, ps, pe) -> dict:
        now = now_ist()
        stats, split, cur, total_customers, new_customers, prev_new = await asyncio.gather(
            self._lifetime_customer_stats(now),
            self._customer_split(s, e),
            self._window_totals(s, e),
            self.db.users.count_documents({"role": "customer"}),
            self.db.users.count_documents({"role": "customer", "created_at": {"$gte": s, "$lt": e}}),
            self.db.users.count_documents({"role": "customer", "created_at": {"$gte": ps, "$lt": pe}}),
        )
        n_customers = stats.get("customers", 0)
        repeat_customers = stats.get("repeat", 0)
        gap_n = stats.get("gap_n", 0)
        new_rev = _rupees(split["new_revenue"])
        total_rev = _rupees(cur["revenue"])

        return {
            "total_customers": total_customers,
            "new_customers": new_customers,
            "new_customers_growth": _growth(new_customers, prev_new),
            "repeat_customers": repeat_customers,
            "repeat_rate": _pct(repeat_customers, n_customers),
            # Second-wash rate over a matured cohort (first booking 30+ days
            # ago) — an un-matured cohort would understate it misleadingly.
            "second_wash_rate": _pct(stats.get("matured_repeat", 0), stats.get("eligible_30", 0)),
            # N-day repeat: of customers whose FIRST booking is at least N days
            # old, how many booked again within N days of that first booking.
            "retention": {f"d{d}": _pct(stats.get(f"again_{d}", 0), stats.get(f"eligible_{d}", 0)) for d in (30, 60, 90)},
            "avg_washes_per_customer": round(stats.get("bookings", 0) / n_customers, 1) if n_customers else 0,
            "avg_days_between_washes": round(stats["gap_ms"] / _DAY_MS / gap_n, 1) if gap_n else None,
            "churn_rate": _pct(stats.get("churned", 0), n_customers),
            "clv": _rupees((stats.get("revenue") or 0) / n_customers) if n_customers else 0,
            "new_vs_repeat_revenue": {
                "new": new_rev,
                "repeat": _rupees(total_rev - new_rev),
                "repeat_pct": _pct(total_rev - new_rev, total_rev),
            },
        }

    async def _captain_performance(self, captain_ids: list[str], date_from: str, date_to: str) -> dict[str, dict]:
        """The subset of StaffDirectoryService.captain_performance the
        captains tab shows, for every captain in three grouped queries
        instead of that method's ~5 queries per captain. Same window:
        scheduled_date (naive IST wall-clock digits), date_to inclusive,
        exactly as that method bounds it."""
        if not captain_ids:
            return {}
        match = {
            "captain_id": {"$in": captain_ids},
            "is_deleted": {"$ne": True},
            "scheduled_date": {
                "$gte": datetime.strptime(date_from, "%Y-%m-%d"),
                "$lt": datetime.strptime(date_to, "%Y-%m-%d") + timedelta(days=1),
            },
        }
        completed = {"$eq": ["$status", "completed"]}
        has_travel = {"$and": [completed, "$heading_at", "$vehicle_verified_at"]}
        has_service = {"$and": [completed, _not_null("$actual_duration_minutes")]}
        booking_rows, rating_rows, complaint_rows = await asyncio.gather(
            self.db.bookings.aggregate([
                {"$match": match},
                {"$group": {
                    "_id": "$captain_id",
                    "jobs": _count_if(completed),
                    "cancelled": _count_if({"$eq": ["$status", "cancelled"]}),
                    # The captain's own earnings, not the customer-paid gross.
                    "earnings": _sum_if(completed, {"$ifNull": ["$captain_earning", 0]}),
                    "started": _count_if(_truthy("$captain_start_stage")),
                    "on_time": _count_if({"$in": ["$captain_start_stage", ["early", "on_time"]]}),
                    "service_sum": _sum_if(has_service, "$actual_duration_minutes"),
                    "service_n": _count_if(has_service),
                    "travel_sum": _sum_if(has_travel, {"$max": [0, _minutes_between("$heading_at", "$vehicle_verified_at")]}),
                    "travel_n": _count_if(has_travel),
                }},
            ]).to_list(length=None),
            # All-time average, exactly as ReviewRepository.average_rating_for_captain.
            self.db.reviews.aggregate([
                {"$match": {"captain_id": {"$in": captain_ids}, "is_deleted": {"$ne": True}}},
                {"$group": {"_id": "$captain_id", "avg": {"$avg": {"$ifNull": ["$captain_rating", "$rating"]}}}},
            ]).to_list(length=None),
            self._complaints_per_captain(match),
        )
        out: dict[str, dict] = {cid: {} for cid in captain_ids}
        for r in booking_rows:
            out[r["_id"]] = {
                "total_jobs_completed": r["jobs"],
                "cancelled_bookings": r["cancelled"],
                "total_earnings": round(r["earnings"] or 0, 2),
                "on_time_start_pct": round(r["on_time"] / r["started"] * 100, 1) if r["started"] else None,
                "avg_service_minutes": round(r["service_sum"] / r["service_n"], 1) if r["service_n"] else None,
                "avg_travel_minutes": round(r["travel_sum"] / r["travel_n"], 1) if r["travel_n"] else None,
            }
        for r in rating_rows:
            out.setdefault(r["_id"], {})["average_rating"] = round(r["avg"] or 0, 2)
        for captain_id, n in complaint_rows.items():
            out.setdefault(captain_id, {})["repeat_complaints"] = n
        return out

    async def _complaints_per_captain(self, booking_match: dict) -> dict[str, int]:
        """Complaints about any booking matching `booking_match`, counted
        per that booking's captain. Driven from complaints (far fewer rows
        than bookings), joined to their bookings a chunk at a time."""
        counts: dict[str, int] = {}

        async def flush(chunk: list[str]) -> None:
            oids = [ObjectId(b) for b in chunk]
            captain_of = {
                str(b["_id"]): b.get("captain_id")
                for b in await self.db.bookings.find({"_id": {"$in": oids}, **booking_match}, {"captain_id": 1}).to_list(length=None)
            }
            for b in chunk:
                if b in captain_of:
                    counts[captain_of[b]] = counts.get(captain_of[b], 0) + 1

        chunk: list[str] = []
        async for c in self.db.complaints.find({"is_deleted": {"$ne": True}, "booking_id": {"$type": "string"}}, {"booking_id": 1, "_id": 0}):
            if ObjectId.is_valid(c["booking_id"]):
                chunk.append(c["booking_id"])
            if len(chunk) >= _CHUNK:
                await flush(chunk)
                chunk = []
        if chunk:
            await flush(chunk)
        return counts

    async def captains(self, s, e, ps, pe) -> dict:
        settings = await self.get_settings()
        target_per_day = settings["targets"].get("washes_per_captain_per_day") or 5.0
        days = max((e - s).days, 1)
        date_from, date_to = s.date().isoformat(), (e - timedelta(days=1)).date().isoformat()

        captains = await self.db.users.find({"role": "captain", "is_deleted": {"$ne": True}}, {"full_name": 1}).to_list(length=None)
        perf_by_captain = await self._captain_performance([str(c["_id"]) for c in captains], date_from, date_to)
        rows = []
        for c in captains:
            cid = str(c["_id"])
            perf = perf_by_captain.get(cid, {})
            jobs = perf.get("total_jobs_completed", 0)
            per_day = round(jobs / days, 2)
            rows.append({
                "captain_id": cid,
                "name": c.get("full_name", "Captain"),
                "jobs": jobs,
                "washes_per_day": per_day,
                "revenue": perf.get("total_earnings", 0),
                "avg_job_minutes": perf.get("avg_service_minutes"),
                "avg_travel_minutes": perf.get("avg_travel_minutes"),
                "on_time_pct": perf.get("on_time_start_pct"),
                "rating": perf.get("average_rating", 0),
                "complaints": perf.get("repeat_complaints", 0),
                "cancellations": perf.get("cancelled_bookings", 0),
                "utilisation_pct": _pct(per_day, target_per_day),
            })
        rows.sort(key=lambda r: -r["jobs"])
        total_jobs = sum(r["jobs"] for r in rows)
        return {
            "days_in_period": days,
            "target_washes_per_captain_per_day": target_per_day,
            "fleet_washes_per_captain_per_day": round(total_jobs / days / len(rows), 2) if rows else 0,
            "captains": rows,
        }

    async def financial(self, s, e, ps, pe) -> dict:
        settings, cur, captains_count = await asyncio.gather(
            self.get_settings(),
            self._window_totals(s, e),
            self.db.users.count_documents({"role": "captain", "is_deleted": {"$ne": True}}),
        )
        washes = cur["completed"]
        revenue = _rupees(cur["revenue"])
        days = max((e - s).days, 1)

        vc = settings["variable_cost_per_wash"]
        variable_cost = _rupees(washes * vc)
        contribution = _rupees(revenue - variable_cost)
        fixed_prorated = _rupees(settings["fixed_cost_monthly"] * days / 30)
        marketing_spend = _rupees(sum(x.get("spend", 0) for x in self._marketing_entries_between(settings, s, e)))
        net_profit = _rupees(contribution - fixed_prorated - marketing_spend)

        aov = _rupees(revenue / washes) if washes else 0
        cpw = _rupees(aov - vc)  # contribution per wash at current AOV
        daily_fixed = settings["fixed_cost_monthly"] / 30
        break_even_washes_per_day = round(daily_fixed / cpw, 1) if cpw > 0 else None

        monthly_contribution_per_kit = (contribution / days * 30 / settings["kits_count"]) if settings["kits_count"] else 0
        kit_payback_months = round(settings["kit_cost"] / monthly_contribution_per_kit, 1) if monthly_contribution_per_kit > 0 and settings["kit_cost"] else None

        return {
            "inputs": {k: settings[k] for k in ("variable_cost_per_wash", "fixed_cost_monthly", "kit_cost", "kits_count")},
            "gross_revenue": revenue,
            # Inside gross_revenue already: what managers knocked off jobs
            # they logged, and the tips customers added on top.
            "manager_discounts": _rupees(cur["manager_discounts"]),
            "tips_included": _rupees(cur["tips"]),
            "washes": washes,
            "aov": aov,
            "variable_cost": variable_cost,
            "contribution": contribution,
            "contribution_per_wash": cpw,
            "contribution_margin_pct": _pct(contribution, revenue),
            "fixed_cost_period": fixed_prorated,
            "marketing_spend": marketing_spend,
            "net_profit": net_profit,
            "profit_margin_pct": _pct(net_profit, revenue),
            "break_even_washes_per_day": break_even_washes_per_day,
            "actual_washes_per_day": round(washes / days, 1),
            "break_even_revenue_per_day": _rupees(break_even_washes_per_day * aov) if break_even_washes_per_day and aov else None,
            "revenue_per_captain": _rupees(revenue / captains_count) if captains_count else 0,
            "kit_payback_months": kit_payback_months,
            "cost_per_booking": _rupees((variable_cost + fixed_prorated + marketing_spend) / cur["bookings"]) if cur["bookings"] else 0,
        }

    async def marketing(self, s, e, ps, pe) -> dict:
        settings = await self.get_settings()
        entries = self._marketing_entries_between(settings, s, e)
        spend = _rupees(sum(x.get("spend", 0) for x in entries))
        manual_leads = sum(int(x.get("leads", 0) or 0) for x in entries)
        attributed_customers = sum(int(x.get("customers", 0) or 0) for x in entries)
        attributed_revenue = _rupees(sum(x.get("revenue", 0) for x in entries))

        coverage_leads, new_customers, referral_customers, cur, split = await asyncio.gather(
            self.db.coverage_leads.count_documents({"created_at": {"$gte": s, "$lt": e}}),
            self.db.users.count_documents({"role": "customer", "created_at": {"$gte": s, "$lt": e}}),
            self.db.users.count_documents(
                {"role": "customer", "referred_by": {"$nin": [None, ""]}, "created_at": {"$gte": s, "$lt": e}}),
            self._window_totals(s, e),
            self._customer_split(s, e),
        )
        leads = manual_leads + coverage_leads
        organic = max(new_customers - attributed_customers - referral_customers, 0)

        by_source: dict[str, dict] = {}
        campaigns = []
        for x in entries:
            src = x.get("source", "other")
            row = by_source.setdefault(src, {"source": src, "spend": 0.0, "leads": 0, "customers": 0, "revenue": 0.0})
            row["spend"] = _rupees(row["spend"] + (x.get("spend", 0) or 0))
            row["leads"] += int(x.get("leads", 0) or 0)
            row["customers"] += int(x.get("customers", 0) or 0)
            row["revenue"] = _rupees(row["revenue"] + (x.get("revenue", 0) or 0))
            campaigns.append({
                "date": x.get("date"), "source": src, "campaign": x.get("campaign") or src,
                "spend": _rupees(x.get("spend", 0) or 0), "leads": int(x.get("leads", 0) or 0),
                "customers": int(x.get("customers", 0) or 0), "revenue": _rupees(x.get("revenue", 0) or 0),
                "cac": _rupees((x.get("spend", 0) or 0) / x["customers"]) if x.get("customers") else None,
                "roas": round((x.get("revenue", 0) or 0) / x["spend"], 2) if x.get("spend") else None,
            })

        return {
            "spend": spend,
            "leads": leads,
            "coverage_leads": coverage_leads,
            "cost_per_lead": _rupees(spend / leads) if leads else None,
            "new_customers": new_customers,
            "ad_attributed_customers": attributed_customers,
            "referral_customers": referral_customers,
            "organic_customers": organic,
            "referral_rate": _pct(referral_customers, new_customers),
            "cac": _rupees(spend / attributed_customers) if attributed_customers else (_rupees(spend / new_customers) if new_customers and spend else None),
            "ad_attributed_revenue": attributed_revenue,
            "roas": round(attributed_revenue / spend, 2) if spend else None,
            "booking_conversion_pct": _pct(cur["bookings"], leads),
            "funnel": {
                "leads": leads,
                "bookings": cur["bookings"],
                "completed": cur["completed"],
                "repeat_customers": split["repeat"],
            },
            "by_source": sorted(by_source.values(), key=lambda r: -r["spend"]),
            "campaigns": campaigns,
            "target_cac": settings["targets"].get("cac"),
        }

    async def operations(self, s, e, ps, pe) -> dict:
        dates = []
        day = s
        while day < e:
            dates.append(day.date().isoformat())
            day += timedelta(days=1)

        slot_rows, cur, (review_count, ratings), complaints = await asyncio.gather(
            self.db.slot_capacity.aggregate([
                {"$match": {"date": {"$in": dates}}},
                {"$group": {"_id": {"$ifNull": ["$slot_key", "?"]}, "capacity": {"$sum": "$capacity"}, "booked": {"$sum": "$booked_count"}}},
            ]).to_list(length=None),
            self._window_totals(s, e),
            self._ratings(s, e),
            # Complaints per period are a small set; only the four fields
            # the section reads are fetched.
            self.db.complaints.find(
                {"created_at": {"$gte": s, "$lt": e}, "is_deleted": {"$ne": True}},
                {"status": 1, "category": 1, "created_at": 1, "updated_at": 1},
            ).to_list(length=None),
        )
        capacity = sum(x["capacity"] for x in slot_rows)
        booked = sum(x["booked"] for x in slot_rows)
        by_slot = {x["_id"]: x["booked"] for x in sorted(slot_rows, key=lambda x: x["_id"])}
        peak = max(by_slot.items(), key=lambda kv: kv[1])[0] if by_slot else None
        lowest = min(by_slot.items(), key=lambda kv: kv[1])[0] if by_slot else None

        completed = cur["completed"]
        avg_travel = round(cur["travel_sum"] / cur["travel_n"], 1) if cur["travel_n"] else None
        avg_service = round(cur["service_sum"] / max(cur["service_n"], 1), 1) if completed else None

        n_ratings = sum(ratings.values())
        distribution = {str(star): sum(c for v, c in ratings.items() if round(v) == star) for star in range(5, 0, -1)}

        resolved = [c for c in complaints if c.get("status") in ("resolved", "closed") and c.get("updated_at")]
        avg_resolution_hours = round(
            sum((_aware(c["updated_at"]) - _aware(c["created_at"])).total_seconds() / 3600 for c in resolved) / len(resolved), 1
        ) if resolved else None
        by_category: dict[str, int] = {}
        for c in complaints:
            key = c.get("category") or "other"
            by_category[key] = by_category.get(key, 0) + 1

        return {
            "capacity": {
                "total": capacity,
                "booked": booked,
                "utilisation_pct": _pct(booked, capacity),
                "available": max(capacity - booked, 0),
                "peak_slot": peak,
                "lowest_slot": lowest,
            },
            "on_time_arrival_pct": _pct(cur["on_time"], cur["started"]),
            "avg_travel_minutes": avg_travel,
            "avg_service_minutes": avg_service,
            "cancellation_rate": _pct(cur["cancelled"], cur["bookings"]),
            "captain_cancellations": cur["captain_cancelled"],
            "experience": {
                "avg_rating": round(sum(v * c for v, c in ratings.items()) / n_ratings, 2) if n_ratings else None,
                "five_star_pct": _pct(distribution.get("5", 0), n_ratings),
                "rating_distribution": distribution,
                "review_collection_pct": _pct(review_count, completed),
                "complaints": len(complaints),
                "complaint_rate_pct": _pct(len(complaints), completed),
                "unresolved_complaints": sum(1 for c in complaints if c.get("status") in ("open", "in_progress")),
                "avg_resolution_hours": avg_resolution_hours,
                "complaint_categories": sorted(
                    [{"category": k, "count": v} for k, v in by_category.items()], key=lambda r: -r["count"]),
            },
        }

    async def areas(self, s, e, ps, pe) -> dict:
        """Bookings grouped by the delivery address's pincode+city — the
        closest thing to 'area' the data actually records. Grouped on the
        server by (address, customer) — one row per customer-address in the
        window, never per booking — then addresses are resolved in batches."""
        created, completed = _created_in(s, e), _completed_in(s, e)
        rows = await self.db.bookings.aggregate([
            {"$match": _window_match(s, e)},
            {"$group": {
                # The booking's own snapshot area (spec 1.3) when it has one —
                # a later edit of the saved address doesn't move old bookings.
                "_id": {
                    "a": "$address_id", "c": "$customer_id",
                    "sc": "$address_snapshot.city", "sp": "$address_snapshot.pincode",
                },
                "bookings": _count_if(created),
                "revenue": _sum_if(completed, "$total_amount"),
            }},
        ], allowDiskUse=True).to_list(length=None)
        def _snap(r: dict) -> dict | None:
            key = r["_id"]
            if key.get("sc") is None and key.get("sp") is None:
                return None
            return {k: v for k, v in (("city", key.get("sc")), ("pincode", key.get("sp"))) if v is not None}

        addr_ids = sorted({
            r["_id"].get("a") for r in rows
            if _snap(r) is None and isinstance(r["_id"].get("a"), str) and ObjectId.is_valid(r["_id"]["a"])
        })
        addresses: dict[str, dict] = {}
        for i in range(0, len(addr_ids), _CHUNK):
            for a in await self.db.addresses.find(
                {"_id": {"$in": [ObjectId(x) for x in addr_ids[i:i + _CHUNK]]}}, {"city": 1, "pincode": 1}
            ).to_list(length=None):
                addresses[str(a["_id"])] = a
        prior = await self._customers_seen_before({r["_id"].get("c") for r in rows if r["bookings"]}, s)

        by_area: dict[str, dict] = {}
        for r in rows:
            a = _snap(r) or addresses.get(r["_id"].get("a") or "")
            label = f"{a.get('city', '?')} · {a.get('pincode', '?')}" if a else "Unknown"
            row = by_area.setdefault(label, {"area": label, "bookings": 0, "revenue": 0.0, "customers": set(), "repeat_customers": set()})
            row["bookings"] += r["bookings"]
            row["revenue"] += r["revenue"] or 0
            if r["bookings"]:
                customer = r["_id"].get("c")
                row["customers"].add(customer)
                if customer in prior:
                    row["repeat_customers"].add(customer)
        out = [
            {
                "area": row["area"],
                "bookings": row["bookings"],
                "revenue": _rupees(row["revenue"]),
                "customers": len(row["customers"]),
                "repeat_rate": _pct(len(row["repeat_customers"]), len(row["customers"])),
            }
            for row in by_area.values()
        ]
        return {"areas": sorted(out, key=lambda r: (-r["bookings"], r["area"]))}

    # ---------------------------------------------------------------- explorer
    # The admin dashboard's interactive charts: one filterable slice of the
    # business (period + center + service + car type + channel), bucketed
    # by day/week/month, broken down by service, car type, center, channel
    # and plan. Same definitions as every section above — bookings count on
    # created_at, revenue on completion (_completed_in), a multi-service
    # booking's amount split evenly across its services (business()'s
    # service mix), plan revenue from paid subscription payment_orders
    # (_plan_revenue) — so a number here matches the tile it sits under.
    # Bounded: every pipeline starts on the indexed window match and returns
    # aggregated rows only (≤ one per day / service / type / center / plan).

    EXPLORER_MAX_DAYS = 731
    SOURCE_LABELS = {"app": "Website / app", "whatsapp": "WhatsApp", "staff": "Staff (phone / walk-in)"}

    @staticmethod
    def explorer_granularity(s: datetime, e: datetime, requested: str | None) -> str:
        if requested in ("day", "week", "month"):
            return requested
        days = (e - s).days
        return "day" if days <= 31 else "week" if days <= 183 else "month"

    @staticmethod
    def _bucket_start(day: datetime, granularity: str) -> datetime:
        if granularity == "week":  # ISO week, Monday first
            return day - timedelta(days=day.weekday())
        if granularity == "month":
            return day.replace(day=1)
        return day

    @staticmethod
    def _next_bucket(start: datetime, granularity: str) -> datetime:
        if granularity == "week":
            return start + timedelta(days=7)
        if granularity == "month":
            return (start.replace(day=28) + timedelta(days=4)).replace(day=1)
        return start + timedelta(days=1)

    async def explorer(
        self,
        s: datetime,
        e: datetime,
        ps: datetime,
        pe: datetime,
        *,
        service_center_id: str | None = None,
        service_id: str | None = None,
        vehicle_type: str | None = None,
        source: str | None = None,
        granularity: str | None = None,
    ) -> dict:
        if (e - s).days > self.EXPLORER_MAX_DAYS:
            raise BadRequestException("Pick a range of two years or less.")
        gran = self.explorer_granularity(s, e, granularity)
        tz = _tz(s)

        booking_filter: dict = {}
        if service_center_id:
            booking_filter["service_center_id"] = service_center_id
        if service_id:
            booking_filter["service_ids"] = service_id
        if vehicle_type:
            booking_filter["vehicle_type"] = vehicle_type
        if source:
            booking_filter["source"] = source

        # Plans follow every filter that means something for a plan sale:
        # center (resolved per order like the manager view), the plan's
        # service tier and its car type. Channel is a booking concept.
        plan_match: dict = {"purpose": {"$in": PLAN_ORDER_PURPOSES}, "status": "paid", "created_at": {"$gte": s, "$lt": e}}
        prev_plan_match: dict = {"purpose": {"$in": PLAN_ORDER_PURPOSES}, "status": "paid", "created_at": {"$gte": ps, "$lt": pe}}
        for m in (plan_match, prev_plan_match):
            if service_id:
                m["service_id"] = service_id
            if vehicle_type:
                m["vehicle_type"] = vehicle_type

        def plan_pipeline(match: dict) -> list[dict]:
            if service_center_id:
                from app.services.subscription_service import UserSubscriptionService

                return UserSubscriptionService._center_orders(match, service_center_id)
            return [{"$match": match}]

        created, completed = _created_in(s, e), _completed_in(s, e)
        day_of = lambda expr: {"$dateToString": {"format": "%Y-%m-%d", "date": expr, "timezone": tz}}  # noqa: E731

        async def refunds(rs, re_, *, by_day: bool = False) -> dict:
            # Custom-plan car refunds (PLANS-2). A custom cart's order
            # carries no service / car type, so the gross above excludes it
            # under those filters — and so do its refunds.
            if service_id or vehicle_type:
                return {"amount": 0.0, "count": 0, "by_day": {}}
            return await custom_plan_refunds(self.db, rs, re_, service_center_id, by_day_tz=tz if by_day else None)

        (
            cur, prev, daily, svc_rows, vt_rows, center_rows, source_rows,
            plan_daily, plan_rows, prev_plans, services, vehicle_types, centers, plans, cur_refunds, prev_refunds,
        ) = await asyncio.gather(
            self._window_totals(s, e, booking_filter),
            self._window_totals(ps, pe, booking_filter),
            self._agg_one(self.db.bookings, [
                {"$match": _window_match(s, e, booking_filter)},
                {"$facet": {
                    "created": [
                        {"$match": {"$expr": created}},
                        {"$group": {
                            "_id": day_of("$created_at"),
                            "n": {"$sum": 1},
                            "cancelled": _count_if({"$eq": ["$status", "cancelled"]}),
                        }},
                    ],
                    "completed": [
                        {"$match": {"$expr": completed}},
                        {"$group": {
                            "_id": day_of({"$ifNull": ["$closed_at", "$created_at"]}),
                            "n": {"$sum": 1},
                            "amount": {"$sum": "$total_amount"},
                        }},
                    ],
                }},
            ]),
            self.db.bookings.aggregate([
                {"$match": _window_match(s, e, {**booking_filter, "service_ids.0": {"$exists": True}})},
                {"$project": {
                    "service_ids": 1, "c": created, "d": completed,
                    "share": {"$divide": ["$total_amount", {"$size": "$service_ids"}]},
                }},
                {"$unwind": "$service_ids"},
                *([{"$match": {"service_ids": service_id}}] if service_id else []),
                {"$group": {"_id": "$service_ids", "bookings": _count_if("$c"), "completed": _count_if("$d"), "revenue": _sum_if("$d", "$share")}},
            ], allowDiskUse=True).to_list(length=None),
            self.db.bookings.aggregate([
                {"$match": _window_match(s, e, booking_filter)},
                {"$group": {
                    "_id": {
                        "vt": {"$cond": [_truthy("$vehicle_type"), "$vehicle_type", None]},
                        "vid": {"$cond": [_truthy("$vehicle_type"), None, "$vehicle_id"]},
                    },
                    "bookings": _count_if(created),
                    "revenue": _sum_if(completed, "$total_amount"),
                }},
            ], allowDiskUse=True).to_list(length=None),
            self.db.bookings.aggregate([
                {"$match": _window_match(s, e, booking_filter)},
                {"$group": {"_id": "$service_center_id", "bookings": _count_if(created), "revenue": _sum_if(completed, "$total_amount")}},
            ], allowDiskUse=True).to_list(length=None),
            self.db.bookings.aggregate([
                {"$match": _window_match(s, e, booking_filter)},
                {"$group": {"_id": {"$ifNull": ["$source", "app"]}, "bookings": _count_if(created), "revenue": _sum_if(completed, "$total_amount")}},
            ], allowDiskUse=True).to_list(length=None),
            self.db.payment_orders.aggregate([
                *plan_pipeline(plan_match),
                {"$group": {"_id": day_of("$created_at"), "n": {"$sum": 1}, "paise": {"$sum": {"$ifNull": ["$amount_paise", 0]}}}},
            ], allowDiskUse=True).to_list(length=None),
            self.db.payment_orders.aggregate([
                *plan_pipeline(plan_match),
                {"$group": {"_id": "$plan_id", "n": {"$sum": 1}, "paise": {"$sum": {"$ifNull": ["$amount_paise", 0]}}}},
            ], allowDiskUse=True).to_list(length=None),
            self._agg_one(self.db.payment_orders, [
                *plan_pipeline(prev_plan_match),
                {"$group": {"_id": None, "n": {"$sum": 1}, "paise": {"$sum": {"$ifNull": ["$amount_paise", 0]}}}},
            ]),
            self.db.services.find({}, {"name": 1}).to_list(length=500),
            self.db.vehicle_types.find({}, {"name": 1, "display_order": 1}).to_list(length=200),
            self.db.service_centers.find({}, {"name": 1}).to_list(length=500),
            self.db.subscription_plans.find({}, {"name": 1}).to_list(length=500),
            refunds(s, e, by_day=True),
            refunds(ps, pe),
        )

        # ---- time series, rolled up from daily rows to the bucket size.
        buckets: dict[str, dict] = {}
        order: list[str] = []
        day = s
        while day < e:
            b = self._bucket_start(day, gran)
            key = b.date().isoformat()
            if key not in buckets:
                end_b = min(self._next_bucket(b, gran), e)
                buckets[key] = {
                    "key": key,
                    # Inclusive IST dates the bucket covers inside the range —
                    # exactly what a drill-down passes back as start/end.
                    "start": max(b, s).date().isoformat(),
                    "end": (end_b - timedelta(days=1)).date().isoformat(),
                    "bookings": 0, "completed": 0, "cancelled": 0, "revenue": 0.0,
                    "plans_sold": 0, "plan_revenue": 0.0, "plan_refunds": 0.0,
                }
                order.append(key)
            day += timedelta(days=1)

        def bucket_for(iso_day: str) -> dict | None:
            try:
                d = datetime.strptime(iso_day, "%Y-%m-%d").replace(tzinfo=s.tzinfo)
            except (TypeError, ValueError):
                return None
            return buckets.get(self._bucket_start(d, gran).date().isoformat())

        for r in daily.get("created", []):
            if (b := bucket_for(r["_id"])) is not None:
                b["bookings"] += r["n"]
                b["cancelled"] += r["cancelled"]
        for r in daily.get("completed", []):
            if (b := bucket_for(r["_id"])) is not None:
                b["completed"] += r["n"]
                b["revenue"] += r["amount"] or 0
        for r in plan_daily:
            if (b := bucket_for(r["_id"])) is not None:
                b["plans_sold"] += r["n"]
                b["plan_revenue"] += (r["paise"] or 0) / 100
        for iso_day, amount in (cur_refunds.get("by_day") or {}).items():
            if (b := bucket_for(iso_day)) is not None:
                b["plan_refunds"] += amount
        series = []
        for key in order:
            b = buckets[key]
            b["revenue"] = _rupees(b["revenue"])
            b["plan_revenue"] = _rupees(b["plan_revenue"])
            b["plan_refunds"] = _rupees(b["plan_refunds"])
            b["plan_revenue_net"] = _rupees(b["plan_revenue"] - b["plan_refunds"])
            series.append(b)

        # ---- breakdowns
        svc_names = {str(x["_id"]): x.get("name", "?") for x in services}
        by_service = sorted(
            (
                {"id": r["_id"], "name": svc_names.get(r["_id"], "Unknown service"), "bookings": r["bookings"],
                 "completed": r["completed"], "revenue": _rupees(r["revenue"])}
                for r in svc_rows if r["bookings"] or r["revenue"]
            ),
            key=lambda r: (-r["bookings"], -r["revenue"], r["name"]),
        )

        v_ids = {g["_id"]["vid"] for g in vt_rows if isinstance(g["_id"].get("vid"), str) and ObjectId.is_valid(g["_id"]["vid"])}
        vehicles = (
            {str(v["_id"]): v for v in await self.db.vehicles.find(
                {"_id": {"$in": [ObjectId(i) for i in v_ids]}}, {"vehicle_type": 1}).to_list(length=len(v_ids))}
            if v_ids else {}
        )
        vt_names = {str(x["_id"]): x.get("name", "?") for x in vehicle_types}
        vt_acc: dict[str, dict] = {}
        for g in vt_rows:
            v = vehicles.get(g["_id"].get("vid") or "")
            vt_id = str(g["_id"].get("vt") or (v.get("vehicle_type") if v else "") or "")
            row = vt_acc.setdefault(vt_id or "unknown", {
                "id": vt_id or None, "name": vt_names.get(vt_id, "Unknown type"), "bookings": 0, "revenue": 0.0})
            row["bookings"] += g["bookings"]
            row["revenue"] += g["revenue"] or 0
        by_vehicle_type = sorted(
            ({**r, "revenue": _rupees(r["revenue"])} for r in vt_acc.values() if r["bookings"] or r["revenue"]),
            key=lambda r: (-r["bookings"], -r["revenue"], r["name"]),
        )

        center_names = {str(x["_id"]): x.get("name", "?") for x in centers}
        by_center = sorted(
            (
                {"id": r["_id"], "name": center_names.get(str(r["_id"] or ""), "No center"), "bookings": r["bookings"], "revenue": _rupees(r["revenue"])}
                for r in center_rows if r["bookings"] or r["revenue"]
            ),
            key=lambda r: (-r["bookings"], -r["revenue"], r["name"]),
        )
        by_source = sorted(
            (
                {"key": r["_id"], "name": self.SOURCE_LABELS.get(r["_id"], str(r["_id"]).replace("_", " ").capitalize()),
                 "bookings": r["bookings"], "revenue": _rupees(r["revenue"])}
                for r in source_rows if r["bookings"] or r["revenue"]
            ),
            key=lambda r: (-r["bookings"], r["name"]),
        )
        plan_names = {str(x["_id"]): x.get("name", "?") for x in plans}
        by_plan = sorted(
            ({"id": r["_id"], "name": plan_names.get(str(r["_id"] or ""), "Unknown plan"), "sold": r["n"], "revenue": _rupees((r["paise"] or 0) / 100)}
             for r in plan_rows),
            key=lambda r: (-r["sold"], -r["revenue"], r["name"]),
        )

        plans_sold = sum(r["sold"] for r in by_plan)
        plan_revenue = _rupees(sum(r["revenue"] for r in by_plan))

        def totals(t: dict, sold: int, plan_rev: float, plan_refunds: float) -> dict:
            rev = _rupees(t["revenue"])
            return {
                "bookings": t["bookings"], "completed": t["completed"], "cancelled": t["cancelled"],
                "revenue": rev, "aov": _rupees(rev / t["completed"]) if t["completed"] else 0,
                "completion_rate": _pct(t["completed"], t["bookings"]),
                "plans_sold": sold, "plan_revenue": plan_rev, "combined_revenue": _rupees(rev + plan_rev),
                # Gross above; custom-plan refunds and the net beside it.
                "plan_refunds": _rupees(plan_refunds), "plan_revenue_net": _rupees(plan_rev - plan_refunds),
            }

        return {
            "range": {"start": s.date().isoformat(), "end": (e - timedelta(days=1)).date().isoformat(), "granularity": gran},
            "filters": {"service_center_id": service_center_id, "service_id": service_id, "vehicle_type": vehicle_type, "source": source},
            "totals": totals(cur, plans_sold, plan_revenue, cur_refunds["amount"]),
            "previous": totals(prev, prev_plans.get("n") or 0, _rupees((prev_plans.get("paise") or 0) / 100), prev_refunds["amount"]),
            "series": series,
            "by_service": by_service,
            "by_vehicle_type": by_vehicle_type,
            "by_center": by_center,
            "by_source": by_source,
            "by_plan": by_plan,
            "options": {
                "centers": sorted(({"id": str(c["_id"]), "name": c.get("name", "?")} for c in centers), key=lambda r: r["name"]),
                "services": sorted(({"id": str(c["_id"]), "name": c.get("name", "?")} for c in services), key=lambda r: r["name"]),
                "vehicle_types": [
                    {"id": str(c["_id"]), "name": c.get("name", "?")}
                    for c in sorted(vehicle_types, key=lambda c: (c.get("display_order") or 0, c.get("name") or ""))
                ],
                "sources": [{"key": k, "name": v} for k, v in self.SOURCE_LABELS.items()],
            },
        }
