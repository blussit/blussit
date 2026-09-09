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
- At BLUSSIT's current scale (a handful of captains, hundreds of
  bookings) fetching a period's bookings once and computing in Python is
  both faster to get right and plenty fast to run; revisit with
  aggregation pipelines only if volume ever makes it slow.
- Period comparison: every section computes the same numbers for the
  previous period of EQUAL length ending where the current one starts,
  so "vs previous" is always apples to apples.
"""
from datetime import datetime, timedelta, timezone

from motor.motor_asyncio import AsyncIOMotorDatabase

from app.core.exceptions import BadRequestException
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


class KpiService:
    def __init__(self, db: AsyncIOMotorDatabase):
        self.db = db

    # ---------------------------------------------------------------- helpers

    async def _bookings_between(self, s, e, extra: dict | None = None) -> list[dict]:
        """Bookings CREATED in the window, plus bookings COMPLETED in the
        window (so revenue can be recognized on the completion date — the
        canonical attribution, see AUDIT.md M3 — even when the booking was
        created earlier). Callers count bookings by created_at and revenue
        by _revenue(), which filters on completion."""
        q = {
            "$or": [
                {"created_at": {"$gte": s, "$lt": e}},
                {"status": "completed", "closed_at": {"$gte": s, "$lt": e}},
            ],
            "is_deleted": {"$ne": True},
        }
        if extra:
            q.update(extra)
        return await self.db.bookings.find(q).to_list(length=None)

    @staticmethod
    def _created_in(b: dict, s, e) -> bool:
        dt = b.get("created_at")
        return dt is not None and s <= _aware(dt) < e

    @staticmethod
    def _completed_in(b: dict, s, e) -> bool:
        if b.get("status") != "completed":
            return False
        dt = b.get("closed_at") or b.get("created_at")
        return dt is not None and s <= _aware(dt) < e

    async def _first_booking_at_by_customer(self) -> dict[str, datetime]:
        rows = await self.db.bookings.aggregate([
            {"$match": {"is_deleted": {"$ne": True}}},
            {"$group": {"_id": "$customer_id", "first_at": {"$min": "$created_at"}}},
        ]).to_list(length=None)
        return {r["_id"]: _aware(r["first_at"]) for r in rows}

    def _revenue(self, bookings: list[dict], s, e) -> float:
        """Revenue recognized on the COMPLETION date (M3 canon)."""
        return _rupees(sum(b.get("total_amount", 0) for b in bookings if self._completed_in(b, s, e)))

    async def get_settings(self) -> dict:
        doc = await self.db.business_settings.find_one({"_id": "singleton"})
        merged = {**_DEFAULT_SETTINGS, **(doc or {})}
        merged["targets"] = {**_DEFAULT_SETTINGS["targets"], **(merged.get("targets") or {})}
        merged.pop("_id", None)
        return merged

    async def update_settings(self, payload: dict) -> dict:
        allowed = {k: payload[k] for k in _DEFAULT_SETTINGS if k in payload}
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

    async def overview(self, s, e, ps, pe) -> dict:
        cur, prev = await self._bookings_between(s, e), await self._bookings_between(ps, pe)
        first_at = await self._first_booking_at_by_customer()

        def block(bookings, bs, be):
            created = [b for b in bookings if self._created_in(b, bs, be)]
            completed = [b for b in bookings if self._completed_in(b, bs, be)]
            customers = {b["customer_id"] for b in created}
            repeat = {c for c in customers if first_at.get(c) and first_at[c] < _aware(bs)}
            return {
                "bookings": len(created),
                "completed": len(completed),
                "revenue": self._revenue(bookings, bs, be),
                "completion_rate": _pct(len(completed), len(created)),
                "repeat_customer_rate": _pct(len(repeat), len(customers)),
            }

        cur_b, prev_b = block(cur, s, e), block(prev, ps, pe)
        new_cur = await self.db.users.count_documents({"role": "customer", "created_at": {"$gte": s, "$lt": e}})
        new_prev = await self.db.users.count_documents({"role": "customer", "created_at": {"$gte": ps, "$lt": pe}})
        settings = await self.get_settings()
        return {
            "current": {**cur_b, "new_customers": new_cur},
            "previous": {**prev_b, "new_customers": new_prev},
            "targets": settings["targets"],
            "alerts": await self._alerts(s, e, ps, pe, cur_b, prev_b, settings),
        }

    async def _alerts(self, s, e, ps, pe, cur_b, prev_b, settings) -> list[dict]:
        """Only meaningful exceptions — an empty list is the good outcome."""
        alerts: list[dict] = []
        cr, pr = cur_b.get("repeat_customer_rate"), prev_b.get("repeat_customer_rate")
        if cr is not None and pr is not None and pr - cr >= 5:
            alerts.append({"severity": "warn", "text": f"Repeat customer rate dropped {round(pr - cr, 1)} points vs previous period"})
        unresolved = await self.db.complaints.count_documents(
            {"status": {"$in": ["open", "in_progress"]}, "is_deleted": {"$ne": True}}
        )
        if unresolved:
            alerts.append({"severity": "warn", "text": f"{unresolved} customer complaint(s) unresolved"})
        # Tomorrow's booked share of capacity — early demand warning.
        tomorrow = (now_ist() + timedelta(days=1)).date().isoformat()
        slots = await self.db.slot_capacity.find({"date": tomorrow}).to_list(length=None)
        cap = sum(x.get("capacity", 0) for x in slots)
        booked = sum(x.get("booked_count", 0) for x in slots)
        if cap and booked / cap < 0.4:
            alerts.append({"severity": "info", "text": f"Tomorrow only {_pct(booked, cap)}% of capacity is booked"})
        if cur_b.get("completion_rate") is not None and cur_b["completion_rate"] < 85 and cur_b["bookings"] >= 5:
            alerts.append({"severity": "warn", "text": f"Completion rate at {cur_b['completion_rate']}% this period"})
        ratings = [r.get("captain_rating") or r.get("rating") for r in await self.db.reviews.find(
            {"created_at": {"$gte": s, "$lt": e}, "is_deleted": {"$ne": True}}).to_list(length=None)]
        ratings = [r for r in ratings if r]
        if ratings and sum(ratings) / len(ratings) < 4.0:
            alerts.append({"severity": "warn", "text": f"Average rating this period is {round(sum(ratings) / len(ratings), 1)}★"})
        return alerts

    async def business(self, s, e, ps, pe) -> dict:
        cur, prev = await self._bookings_between(s, e), await self._bookings_between(ps, pe)
        created = [b for b in cur if self._created_in(b, s, e)]
        completed = [b for b in cur if self._completed_in(b, s, e)]
        cancelled = [b for b in created if b.get("status") == "cancelled"]
        revenue, prev_revenue = self._revenue(cur, s, e), self._revenue(prev, ps, pe)

        # Daily trend series across the period (booking counts + revenue).
        series: dict[str, dict] = {}
        day = s
        while day < e:
            series[day.date().isoformat()] = {"date": day.date().isoformat(), "bookings": 0, "revenue": 0.0}
            day += timedelta(days=1)
        for b in cur:
            if self._created_in(b, s, e):
                key = _aware(b["created_at"]).astimezone(s.tzinfo).date().isoformat()
                if key in series:
                    series[key]["bookings"] += 1
            if self._completed_in(b, s, e):
                rkey = _aware(b.get("closed_at") or b["created_at"]).astimezone(s.tzinfo).date().isoformat()
                if rkey in series:
                    series[rkey]["revenue"] = _rupees(series[rkey]["revenue"] + b.get("total_amount", 0))

        # Service mix — a multi-service booking's amount is split evenly
        # across its services (never double-counted).
        svc_names = {str(x["_id"]): x.get("name", "?") for x in await self.db.services.find({}).to_list(length=None)}
        mix: dict[str, dict] = {}
        for b in cur:
            ids = b.get("service_ids") or []
            for sid in ids:
                row = mix.setdefault(sid, {"name": svc_names.get(sid, "Unknown"), "bookings": 0, "revenue": 0.0, "cancelled": 0})
                if self._created_in(b, s, e):
                    row["bookings"] += 1
                    if b.get("status") == "cancelled":
                        row["cancelled"] += 1
                if self._completed_in(b, s, e):
                    row["revenue"] = _rupees(row["revenue"] + b.get("total_amount", 0) / len(ids))
        service_mix = sorted(mix.values(), key=lambda r: -r["revenue"])
        for row in service_mix:
            row["aov"] = _rupees(row["revenue"] / row["bookings"]) if row["bookings"] else 0
            row["cancellation_rate"] = _pct(row["cancelled"], row["bookings"])

        # Vehicle-type mix — joined through vehicles in Python (vehicle
        # info is never denormalized onto bookings; see BookingService).
        from bson import ObjectId
        v_ids = [ObjectId(b["vehicle_id"]) for b in cur if b.get("vehicle_id") and ObjectId.is_valid(b["vehicle_id"])]
        vehicles = {str(v["_id"]): v for v in await self.db.vehicles.find({"_id": {"$in": v_ids}}).to_list(length=None)} if v_ids else {}
        vt_names = {str(x["_id"]): x.get("name", "?") for x in await self.db.vehicle_types.find({}).to_list(length=None)}
        vt_mix: dict[str, dict] = {}
        for b in cur:
            v = vehicles.get(b.get("vehicle_id", ""))
            vt = vt_names.get(str(v.get("vehicle_type", "")), "Unknown") if v else "Unknown"
            row = vt_mix.setdefault(vt, {"name": vt, "bookings": 0, "revenue": 0.0})
            if self._created_in(b, s, e):
                row["bookings"] += 1
            if self._completed_in(b, s, e):
                row["revenue"] = _rupees(row["revenue"] + b.get("total_amount", 0))
        vehicle_mix = sorted(vt_mix.values(), key=lambda r: -r["bookings"])
        for row in vehicle_mix:
            row["aov"] = _rupees(row["revenue"] / row["bookings"]) if row["bookings"] else 0

        # Revenue quality: new-customer vs repeat vs subscription-covered.
        first_at = await self._first_booking_at_by_customer()
        new_rev = _rupees(sum(b.get("total_amount", 0) for b in completed if first_at.get(b["customer_id"], s) >= s))
        repeat_rev = _rupees(revenue - new_rev)
        sub_rev = _rupees(sum(b.get("subtotal", 0) for b in completed if b.get("payment_method") == "subscription"))

        return {
            "totals": {
                "bookings": len(created),
                "completed": len(completed),
                "cancelled": len(cancelled),
                "completion_rate": _pct(len(completed), len(created)),
                "aov": _rupees(revenue / len(completed)) if completed else 0,
                "revenue": revenue,
                "revenue_growth": _growth(revenue, prev_revenue),
                "booking_growth": _growth(len(created), len([b for b in prev if self._created_in(b, ps, pe)])),
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
                "one_time_revenue": _rupees(revenue - sub_rev),
            },
        }

    async def customers(self, s, e, ps, pe) -> dict:
        all_rows = await self.db.bookings.find(
            {"is_deleted": {"$ne": True}},
            {"customer_id": 1, "created_at": 1, "status": 1, "total_amount": 1},
        ).to_list(length=None)
        by_customer: dict[str, list] = {}
        for b in sorted(all_rows, key=lambda x: x["created_at"]):
            by_customer.setdefault(b["customer_id"], []).append(b)

        now = now_ist()

        def tz(dt):
            return _aware(dt)

        total_customers = await self.db.users.count_documents({"role": "customer"})
        new_customers = await self.db.users.count_documents({"role": "customer", "created_at": {"$gte": s, "$lt": e}})
        prev_new = await self.db.users.count_documents({"role": "customer", "created_at": {"$gte": ps, "$lt": pe}})

        washes_counts = [len(v) for v in by_customer.values()]
        repeat_customers = sum(1 for c in washes_counts if c > 1)
        gaps: list[float] = []
        for rows in by_customer.values():
            for a, b in zip(rows, rows[1:]):
                gaps.append((tz(b["created_at"]) - tz(a["created_at"])).total_seconds() / 86400)

        # N-day repeat: of customers whose FIRST booking is at least N days
        # old, how many booked again within N days of that first booking.
        def n_day_repeat(days: int):
            eligible = again = 0
            for rows in by_customer.values():
                first = tz(rows[0]["created_at"])
                if (now - first).days < days:
                    continue
                eligible += 1
                if any((tz(b["created_at"]) - first).days <= days for b in rows[1:]):
                    again += 1
            return _pct(again, eligible)

        # Second-wash rate over a matured cohort (first booking 30+ days
        # ago) — an un-matured cohort would understate it misleadingly.
        matured = [rows for rows in by_customer.values() if (now - tz(rows[0]["created_at"])).days >= 30]
        second_wash_rate = _pct(sum(1 for rows in matured if len(rows) > 1), len(matured))

        churned = sum(1 for rows in by_customer.values() if (now - tz(rows[-1]["created_at"])).days > 60)
        lifetime_revenue = sum(b.get("total_amount", 0) for b in all_rows if b.get("status") == "completed")

        cur = await self._bookings_between(s, e)
        completed = [b for b in cur if self._completed_in(b, s, e)]
        first_at = {c: _aware(rows[0]["created_at"]) for c, rows in by_customer.items()}
        new_rev = _rupees(sum(b.get("total_amount", 0) for b in completed if tz(first_at.get(b["customer_id"], s)) >= s))
        total_rev = self._revenue(cur, s, e)

        return {
            "total_customers": total_customers,
            "new_customers": new_customers,
            "new_customers_growth": _growth(new_customers, prev_new),
            "repeat_customers": repeat_customers,
            "repeat_rate": _pct(repeat_customers, len(by_customer)),
            "second_wash_rate": second_wash_rate,
            "retention": {"d30": n_day_repeat(30), "d60": n_day_repeat(60), "d90": n_day_repeat(90)},
            "avg_washes_per_customer": round(sum(washes_counts) / len(washes_counts), 1) if washes_counts else 0,
            "avg_days_between_washes": round(sum(gaps) / len(gaps), 1) if gaps else None,
            "churn_rate": _pct(churned, len(by_customer)),
            "clv": _rupees(lifetime_revenue / len(by_customer)) if by_customer else 0,
            "new_vs_repeat_revenue": {
                "new": new_rev,
                "repeat": _rupees(total_rev - new_rev),
                "repeat_pct": _pct(total_rev - new_rev, total_rev),
            },
        }

    async def captains(self, s, e, ps, pe) -> dict:
        from app.services.staff_directory_service import StaffDirectoryService

        staff = StaffDirectoryService(self.db)
        settings = await self.get_settings()
        target_per_day = settings["targets"].get("washes_per_captain_per_day") or 5.0
        days = max((e - s).days, 1)
        date_from, date_to = s.date().isoformat(), (e - timedelta(days=1)).date().isoformat()

        captains = await self.db.users.find({"role": "captain", "is_deleted": {"$ne": True}}).to_list(length=None)
        rows = []
        for c in captains:
            cid = str(c["_id"])
            perf = await staff.captain_performance(cid, date_from=date_from, date_to=date_to)
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
                "rating": perf.get("average_rating"),
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
        settings = await self.get_settings()
        cur = await self._bookings_between(s, e)
        completed = [b for b in cur if self._completed_in(b, s, e)]
        washes = len(completed)
        revenue = self._revenue(cur, s, e)
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
        captains_count = await self.db.users.count_documents({"role": "captain", "is_deleted": {"$ne": True}})

        monthly_contribution_per_kit = (contribution / days * 30 / settings["kits_count"]) if settings["kits_count"] else 0
        kit_payback_months = round(settings["kit_cost"] / monthly_contribution_per_kit, 1) if monthly_contribution_per_kit > 0 and settings["kit_cost"] else None

        return {
            "inputs": {k: settings[k] for k in ("variable_cost_per_wash", "fixed_cost_monthly", "kit_cost", "kits_count")},
            "gross_revenue": revenue,
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
            "cost_per_booking": _rupees((variable_cost + fixed_prorated + marketing_spend) / len([b for b in cur if self._created_in(b, s, e)])) if any(self._created_in(b, s, e) for b in cur) else 0,
        }

    async def marketing(self, s, e, ps, pe) -> dict:
        settings = await self.get_settings()
        entries = self._marketing_entries_between(settings, s, e)
        spend = _rupees(sum(x.get("spend", 0) for x in entries))
        manual_leads = sum(int(x.get("leads", 0) or 0) for x in entries)
        attributed_customers = sum(int(x.get("customers", 0) or 0) for x in entries)
        attributed_revenue = _rupees(sum(x.get("revenue", 0) for x in entries))

        coverage_leads = await self.db.coverage_leads.count_documents({"created_at": {"$gte": s, "$lt": e}})
        leads = manual_leads + coverage_leads
        new_customers = await self.db.users.count_documents({"role": "customer", "created_at": {"$gte": s, "$lt": e}})
        referral_customers = await self.db.users.count_documents(
            {"role": "customer", "referred_by": {"$nin": [None, ""]}, "created_at": {"$gte": s, "$lt": e}})
        organic = max(new_customers - attributed_customers - referral_customers, 0)

        cur = await self._bookings_between(s, e)
        created = [b for b in cur if self._created_in(b, s, e)]
        completed = [b for b in cur if self._completed_in(b, s, e)]
        first_at = await self._first_booking_at_by_customer()
        repeat_customers = len({b["customer_id"] for b in created if first_at.get(b["customer_id"], s) < s})

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
            "booking_conversion_pct": _pct(len(created), leads),
            "funnel": {
                "leads": leads,
                "bookings": len(created),
                "completed": len(completed),
                "repeat_customers": repeat_customers,
            },
            "by_source": sorted(by_source.values(), key=lambda r: -r["spend"]),
            "campaigns": campaigns,
            "target_cac": (await self.get_settings())["targets"].get("cac"),
        }

    async def operations(self, s, e, ps, pe) -> dict:
        dates = []
        day = s
        while day < e:
            dates.append(day.date().isoformat())
            day += timedelta(days=1)

        slots = await self.db.slot_capacity.find({"date": {"$in": dates}}).to_list(length=None)
        capacity = sum(x.get("capacity", 0) for x in slots)
        booked = sum(x.get("booked_count", 0) for x in slots)
        by_slot: dict[str, int] = {}
        for x in slots:
            by_slot[x.get("slot_key", "?")] = by_slot.get(x.get("slot_key", "?"), 0) + x.get("booked_count", 0)
        peak = max(by_slot.items(), key=lambda kv: kv[1])[0] if by_slot else None
        lowest = min(by_slot.items(), key=lambda kv: kv[1])[0] if by_slot else None

        cur = await self._bookings_between(s, e)
        created = [b for b in cur if self._created_in(b, s, e)]
        completed = [b for b in cur if self._completed_in(b, s, e)]
        cancelled = [b for b in created if b.get("status") == "cancelled"]
        captain_cancel = sum(1 for b in created if b.get("cancelled_by_role") == "captain")
        started = [b for b in created if b.get("captain_start_stage")]
        on_time = sum(1 for b in started if b["captain_start_stage"] in ("early", "on_time"))

        def avg_minutes(pairs):
            vals = [((_aware(b[k2]) - _aware(b[k1])).total_seconds() / 60) for b, k1, k2 in pairs if b.get(k1) and b.get(k2)]
            return round(sum(vals) / len(vals), 1) if vals else None

        avg_travel = avg_minutes([(b, "heading_at", "vehicle_verified_at") for b in completed])
        avg_service = round(
            sum(b.get("actual_duration_minutes", 0) for b in completed if b.get("actual_duration_minutes")) /
            max(sum(1 for b in completed if b.get("actual_duration_minutes")), 1), 1) if completed else None

        reviews = await self.db.reviews.find({"created_at": {"$gte": s, "$lt": e}, "is_deleted": {"$ne": True}}).to_list(length=None)
        ratings = [r.get("captain_rating") or r.get("rating") for r in reviews]
        ratings = [r for r in ratings if r]
        distribution = {str(star): sum(1 for r in ratings if round(r) == star) for star in range(5, 0, -1)}

        complaints = await self.db.complaints.find({"created_at": {"$gte": s, "$lt": e}, "is_deleted": {"$ne": True}}).to_list(length=None)
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
            "on_time_arrival_pct": _pct(on_time, len(started)),
            "avg_travel_minutes": avg_travel,
            "avg_service_minutes": avg_service,
            "cancellation_rate": _pct(len(cancelled), len(created)),
            "captain_cancellations": captain_cancel,
            "experience": {
                "avg_rating": round(sum(ratings) / len(ratings), 2) if ratings else None,
                "five_star_pct": _pct(distribution.get("5", 0), len(ratings)),
                "rating_distribution": distribution,
                "review_collection_pct": _pct(len(reviews), len(completed)),
                "complaints": len(complaints),
                "complaint_rate_pct": _pct(len(complaints), len(completed)),
                "unresolved_complaints": sum(1 for c in complaints if c.get("status") in ("open", "in_progress")),
                "avg_resolution_hours": avg_resolution_hours,
                "complaint_categories": sorted(
                    [{"category": k, "count": v} for k, v in by_category.items()], key=lambda r: -r["count"]),
            },
        }

    async def areas(self, s, e, ps, pe) -> dict:
        """Bookings grouped by the delivery address's pincode+city — the
        closest thing to 'area' the data actually records."""
        from bson import ObjectId

        cur = await self._bookings_between(s, e)
        addr_ids = [ObjectId(b["address_id"]) for b in cur if b.get("address_id") and ObjectId.is_valid(b["address_id"])]
        addresses = {str(a["_id"]): a for a in await self.db.addresses.find({"_id": {"$in": addr_ids}}).to_list(length=None)} if addr_ids else {}
        first_at = await self._first_booking_at_by_customer()

        rows: dict[str, dict] = {}
        for b in cur:
            a = addresses.get(b.get("address_id", ""))
            label = f"{a.get('city', '?')} · {a.get('pincode', '?')}" if a else "Unknown"
            row = rows.setdefault(label, {"area": label, "bookings": 0, "revenue": 0.0, "customers": set(), "repeat_customers": set()})
            if self._created_in(b, s, e):
                row["bookings"] += 1
                row["customers"].add(b["customer_id"])
                if first_at.get(b["customer_id"], s) < s:
                    row["repeat_customers"].add(b["customer_id"])
            if self._completed_in(b, s, e):
                row["revenue"] = _rupees(row["revenue"] + b.get("total_amount", 0))
        out = []
        for row in rows.values():
            out.append({
                "area": row["area"],
                "bookings": row["bookings"],
                "revenue": row["revenue"],
                "customers": len(row["customers"]),
                "repeat_rate": _pct(len(row["repeat_customers"]), len(row["customers"])),
            })
        return {"areas": sorted(out, key=lambda r: -r["bookings"])}
