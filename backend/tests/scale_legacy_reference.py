"""
Frozen copies of the pre-scale-pass KPI / analytics / CRM / subscription
report code, kept ONLY as a parity oracle for tests/test_scale_parity.py:
the aggregation-pipeline rewrites must return the same numbers these
load-everything-into-Python versions did on the same data. Never import
this from app code.
"""
from datetime import datetime, timedelta, timezone

from bson import ObjectId
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.models.enums import SubscriptionStatus
from app.repositories.booking_repository import BookingRepository
from app.repositories.catalog_repository import ServiceRepository
from app.repositories.service_center_repository import ServiceCenterRepository
from app.repositories.subscription_repository import UserSubscriptionRepository
from app.repositories.user_repository import UserRepository
from app.repositories.vehicle_repository import VehicleRepository
from app.repositories.vehicle_type_repository import VehicleTypeRepository
from app.services.kpi_service import _DEFAULT_SETTINGS, _aware, _growth, _pct, _rupees
from app.services.subscription_service import UserSubscriptionService, _with_effective_statuses
from app.services.whatsapp_crm_service import WhatsAppCrmService, _wa_id_variants
from app.services.whatsapp_crm_service import _aware as _wa_aware
from app.services.whatsapp_crm_service import _iso
from app.utils.serializers import serialize_doc
from app.utils.timezone import now_ist

_TRAVEL_MINUTES_EXPR = {
    "$cond": [{"$and": ["$heading_at", "$vehicle_verified_at"]}, {"$divide": [{"$subtract": ["$vehicle_verified_at", "$heading_at"]}, 60000]}, None]
}
_TOTAL_MINUTES_EXPR = {"$cond": [{"$and": ["$assigned_at", "$closed_at"]}, {"$divide": [{"$subtract": ["$closed_at", "$assigned_at"]}, 60000]}, None]}


class LegacyKpiService:
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

    async def _plan_revenue(self, s, e) -> float:
        """Money actually paid for a SUBSCRIPTION/PLAN in the window —
        entirely separate from booking revenue (a plan is paid for once,
        up front, never per-wash — see PaymentService.manager_subscription_offer).
        Same source of truth and same `created_at` windowing PaymentService.
        admin_collections already uses for its own subscriptions figure, so
        this dashboard number and that report never disagree."""
        rows = await self.db.payment_orders.aggregate([
            {"$match": {"purpose": "subscription", "status": "paid", "created_at": {"$gte": s, "$lt": e}}},
            {"$group": {"_id": None, "amount_paise": {"$sum": "$amount_paise"}}},
        ]).to_list(length=1)
        return _rupees((rows[0]["amount_paise"] if rows else 0) / 100)

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
        plan_rev_cur, plan_rev_prev = await self._plan_revenue(s, e), await self._plan_revenue(ps, pe)
        settings = await self.get_settings()
        return {
            "current": {**cur_b, "new_customers": new_cur, "plan_revenue": plan_rev_cur, "combined_revenue": _rupees(cur_b["revenue"] + plan_rev_cur)},
            "previous": {**prev_b, "new_customers": new_prev, "plan_revenue": plan_rev_prev, "combined_revenue": _rupees(prev_b["revenue"] + plan_rev_prev)},
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
        extra = {"service_center_id": service_center_id}
        cur, prev = await self._bookings_between(s, e, extra), await self._bookings_between(ps, pe, extra)

        def block(bookings, bs, be):
            created = [b for b in bookings if self._created_in(b, bs, be)]
            completed = [b for b in bookings if self._completed_in(b, bs, be)]
            return {"bookings": len(created), "completed": len(completed), "revenue": self._revenue(bookings, bs, be)}

        cur_b, prev_b = block(cur, s, e), block(prev, ps, pe)
        subs = LegacySubscriptions(self.db)
        cur_plan_rev, cur_plans_sold = await subs.center_plan_revenue(service_center_id, s, e)
        prev_plan_rev, prev_plans_sold = await subs.center_plan_revenue(service_center_id, ps, pe)
        return {
            "current": {**cur_b, "plans_sold": cur_plans_sold, "plan_revenue": cur_plan_rev, "combined_revenue": _rupees(cur_b["revenue"] + cur_plan_rev)},
            "previous": {**prev_b, "plans_sold": prev_plans_sold, "plan_revenue": prev_plan_rev, "combined_revenue": _rupees(prev_b["revenue"] + prev_plan_rev)},
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
            # Quick-booking model: the type is on the booking itself; the
            # vehicle join only covers older saved-vehicle bookings.
            v = vehicles.get(b.get("vehicle_id") or "")
            vt_id = b.get("vehicle_type") or (v.get("vehicle_type") if v else None)
            vt = vt_names.get(str(vt_id or ""), b.get("vehicle_label") or "Unknown")
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
        # DELIBERATE post-freeze change (2026-09-28 KPI fix): one-time revenue
        # subtracts what plan bookings CHARGED, not their pre-discount subtotal
        # (which understated it and could go negative). Mirrors kpi_service.
        sub_charged = sum(b.get("total_amount", 0) for b in completed if b.get("payment_method") == "subscription")

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
                "one_time_revenue": _rupees(max(0.0, revenue - sub_charged)),
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


class LegacyAnalyticsService:
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
                    "vehicle_type": 1,
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

        # Quick-booking model: the type is on the booking itself; the vehicle
        # join only covers older saved-vehicle bookings.
        grouped = self._group_rows_by(
            rows, lambda row: row.get("vehicle_type") or (vehicles.get(row.get("vehicle_id")) or {}).get("vehicle_type")
        )
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
        # Projected to the four fields this actually needs: review comments
        # are free text and by far the biggest part of the document, and
        # pulling them to compute an average is pure waste. NOTE (scale):
        # this still reads every review; once the reviews collection is
        # large enough for that to matter, move the join into a $lookup
        # aggregation so Mongo does it server-side.
        reviews = await self.db.reviews.find(
            {"is_deleted": {"$ne": True}}, {"booking_id": 1, "captain_rating": 1, "rating": 1}
        ).to_list(length=None)
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
            vt = booking.get("vehicle_type") or (vehicles.get(booking.get("vehicle_id")) or {}).get("vehicle_type")
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
        # Projected to the four fields this actually needs: review comments
        # are free text and by far the biggest part of the document, and
        # pulling them to compute an average is pure waste. NOTE (scale):
        # this still reads every review; once the reviews collection is
        # large enough for that to matter, move the join into a $lookup
        # aggregation so Mongo does it server-side.
        reviews = await self.db.reviews.find(
            {"is_deleted": {"$ne": True}}, {"booking_id": 1, "service_rating": 1, "rating": 1}
        ).to_list(length=None)
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


class LegacyCrm(WhatsAppCrmService):
    async def analytics(self, days: int = 30) -> dict:
        since = datetime.now(timezone.utc) - timedelta(days=days)
        convs = await self.db.whatsapp_conversations.find({"last_message_at": {"$ne": None}}).to_list(length=None)
        total = len(convs)
        open_n = sum(1 for c in convs if c.get("crm_status", "open") in ("open", None))
        pending_n = sum(1 for c in convs if c.get("crm_status") == "pending")
        resolved_n = sum(1 for c in convs if c.get("crm_status") == "resolved")
        unread_n = sum(1 for c in convs if c.get("unread_count", 0) > 0)

        sent = await self.db.whatsapp_outbox.count_documents({"created_at": {"$gte": since}})
        received = await self.db.whatsapp_inbox.count_documents({"created_at": {"$gte": since}})
        tpl_rows = await self.db.whatsapp_outbox.find(
            {"created_at": {"$gte": since}, "template_name": {"$ne": None}},
            {"delivery_status": 1, "ok": 1},
        ).to_list(length=None)
        tpl_sent = len(tpl_rows)
        tpl_delivered = sum(1 for r in tpl_rows if r.get("delivery_status") in ("delivered", "read"))
        tpl_read = sum(1 for r in tpl_rows if r.get("delivery_status") == "read")
        tpl_failed = sum(1 for r in tpl_rows if r.get("ok") is False or r.get("delivery_status") == "failed")

        # First response: first outbound after the conversation's first
        # inbound. Resolution: created -> resolved_at.
        first_resp: list[float] = []
        resolution: list[float] = []
        for c in convs[:300]:
            first_in = await self.db.whatsapp_inbox.find_one({"wa_id": c["wa_id"]}, sort=[("created_at", 1)])
            if first_in:
                first_out = await self.db.whatsapp_outbox.find_one(
                    {"phone": {"$in": _wa_id_variants(c["wa_id"])}, "created_at": {"$gte": first_in["created_at"]}}, sort=[("created_at", 1)])
                if first_out:
                    first_resp.append((_wa_aware(first_out["created_at"]) - _wa_aware(first_in["created_at"])).total_seconds() / 60)
            if c.get("resolved_at") and c.get("created_at"):
                resolution.append((_wa_aware(c["resolved_at"]) - _wa_aware(c["created_at"])).total_seconds() / 3600)

        def _pct(a, b):
            return round(a / b * 100, 1) if b else None

        return {
            "days": days,
            "conversations": {"total": total, "open": open_n, "pending": pending_n, "resolved": resolved_n, "unread": unread_n},
            "messages": {"sent": sent, "received": received},
            "templates": {
                "sent": tpl_sent,
                "delivery_rate_pct": _pct(tpl_delivered, tpl_sent),
                "read_rate_pct": _pct(tpl_read, tpl_sent),
                "failure_rate_pct": _pct(tpl_failed, tpl_sent),
            },
            "avg_first_response_minutes": round(sum(first_resp) / len(first_resp), 1) if first_resp else None,
            "avg_resolution_hours": round(sum(resolution) / len(resolution), 1) if resolution else None,
        }

    async def list_local_templates(self) -> list[dict]:
        rows = await self.db.whatsapp_templates.find({}).sort("name", 1).to_list(length=None)
        usage = {u["_id"]: u["n"] for u in await self.db.whatsapp_outbox.aggregate([
            {"$match": {"template_name": {"$ne": None}}},
            {"$group": {"_id": "$template_name", "n": {"$sum": 1}}},
        ]).to_list(length=None)}
        return [{
            "name": r["name"], "status": r.get("status", "DRAFT"), "category": r.get("category"),
            "language": r.get("language"), "body": r.get("body", ""), "param_count": r.get("param_count", 0),
            "rejected_reason": r.get("rejected_reason"), "disabled": r.get("disabled", False),
            "usage": usage.get(r["name"], 0), "synced_at": _iso(r.get("synced_at")),
        } for r in rows]


class LegacySubscriptions(UserSubscriptionService):
    async def center_overview(self, service_center_id: str, actor_role: str, actor_center_id: str | None) -> dict:
        """The manager's subscription dashboard: every subscription held by
        a customer this center has ever served (any booking dispatched
        here, not only plan redemptions — a plan-holder who hasn't
        redeemed yet is exactly who the manager wants to call), rolled up
        into KPIs (active / expiring within 14 days / expired) plus a
        per-plan breakdown, with one detail row per subscription."""
        from app.core.authz import ensure_own_center

        ensure_own_center(actor_role, actor_center_id, service_center_id)

        customer_ids = await self.repo.db.bookings.distinct(
            "customer_id", {"service_center_id": service_center_id, "is_deleted": {"$ne": True}}
        )
        # A plan a manager grants/sells directly (cash / WhatsApp link /
        # auto-pay) is tagged with their own center at creation — that
        # customer may never have a booking here at all yet, so the
        # booking-history list above alone would hide them. Union both so
        # a just-assigned plan shows up immediately, not only after the
        # customer's first visit.
        query = {"is_deleted": {"$ne": True}, "$or": [{"service_center_id": service_center_id}]}
        if customer_ids:
            query["$or"].append({"customer_id": {"$in": customer_ids}})
        subs = _with_effective_statuses(
            await self.repo.collection.find(query).sort("created_at", -1).to_list(length=2000)
        )
        if not subs:
            return {"kpis": {"total": 0, "active": 0, "expiring_soon": 0, "expired": 0}, "plan_breakdown": [], "rows": []}

        plan_ids = {ObjectId(s["plan_id"]) for s in subs if s.get("plan_id") and ObjectId.is_valid(s["plan_id"])}
        plans = {
            str(p["_id"]): p
            for p in await self.plan_repo.collection.find({"_id": {"$in": list(plan_ids)}}).to_list(length=500)
        }
        holder_ids = {ObjectId(s["customer_id"]) for s in subs if ObjectId.is_valid(s["customer_id"])}
        users = {
            str(u["_id"]): u
            for u in await self.user_repo.collection.find(
                {"_id": {"$in": list(holder_ids)}}, {"full_name": 1, "phone": 1}
            ).to_list(length=2000)
        }
        type_names = {
            str(t["_id"]): t.get("name", "")
            for t in await self.vehicle_type_repo.collection.find({}, {"name": 1}).to_list(length=200)
        }

        now = now_ist()
        rows = []
        active = expiring_soon = expired = 0
        plan_counts: dict[str, int] = {}
        for s in subs:
            plan = plans.get(s.get("plan_id") or "")
            holder = users.get(s["customer_id"], {})
            end_date = s.get("end_date")
            days_left = None
            if end_date:
                # serialize_doc already stamped end_date as a tz-aware ISO
                # string (COMPUTED_INSTANT_KEYS) — parse it back; only a
                # still-naive value carries UTC semantics.
                end = datetime.fromisoformat(end_date) if isinstance(end_date, str) else end_date
                if end.tzinfo is None:
                    end = end.replace(tzinfo=timezone.utc)
                days_left = (end - now).days
            status = s.get("effective_status")
            if status == SubscriptionStatus.ACTIVE.value:
                active += 1
                plan_name = plan.get("name") if plan else "Unknown plan"
                plan_counts[plan_name] = plan_counts.get(plan_name, 0) + 1
                if days_left is not None and days_left <= 14:
                    expiring_soon += 1
            elif status == SubscriptionStatus.EXPIRED.value:
                expired += 1
            # What the customer actually PAID — never just the plan's list
            # price. A manager-sold plan almost always differs from
            # purchased_price (a discount, a coupon), and showing the list
            # price here instead is exactly the kind of gap that makes cash
            # collected not match what the screen says was charged. Same
            # fallback as admin_overview: a genuine self-serve purchase
            # never stamps amount_paid, so purchased_price IS what they
            # paid in that one case.
            amount_paid = s.get("amount_paid")
            if amount_paid is None and not s.get("service_center_id"):
                amount_paid = s.get("purchased_price")
            rows.append({
                "subscription_id": s["id"],
                "customer_id": s["customer_id"],
                "customer_name": holder.get("full_name", "Unknown"),
                "customer_phone": holder.get("phone"),
                "plan_id": s.get("plan_id"),
                "plan_name": plan.get("name") if plan else "Unknown plan",
                "status": status,
                "vehicle_type": s.get("vehicle_type"),
                "vehicle_type_name": type_names.get(s.get("vehicle_type") or ""),
                "purchased_price": s.get("purchased_price"),
                "amount_paid": amount_paid,
                "discount_amount": s.get("discount_amount"),
                "coupon_code": s.get("coupon_code"),
                "payment_method": s.get("payment_method"),
                "remaining_service_count": s.get("remaining_service_count"),
                "total_service_count": s.get("total_service_count"),
                "start_date": s.get("start_date"),
                "end_date": s.get("end_date"),
                "days_left": days_left,
            })
        return {
            "kpis": {"total": len(rows), "active": active, "expiring_soon": expiring_soon, "expired": expired},
            "plan_breakdown": sorted(
                ({"plan_name": name, "active_count": count} for name, count in plan_counts.items()),
                key=lambda x: -x["active_count"],
            ),
            "rows": rows,
        }

    async def admin_overview(self) -> dict:
        """Every plan ever purchased or granted, platform-wide — the admin's
        answer to "who bought what, for how much". Unlike center_overview
        (one manager's own center, booking-history-derived customer list)
        this has no center scope at all: every user_subscriptions row,
        enriched with who paid what and which center (if any) sold it."""
        subs = _with_effective_statuses(
            await self.repo.collection.find({"is_deleted": {"$ne": True}}).sort("created_at", -1).to_list(length=5000)
        )
        if not subs:
            return {"kpis": {"total": 0, "active": 0, "expired": 0, "total_revenue": 0.0}, "plan_breakdown": [], "rows": []}

        plan_ids = {ObjectId(s["plan_id"]) for s in subs if s.get("plan_id") and ObjectId.is_valid(s["plan_id"])}
        plans = {
            str(p["_id"]): p
            for p in await self.plan_repo.collection.find({"_id": {"$in": list(plan_ids)}}).to_list(length=500)
        }
        holder_ids = {ObjectId(s["customer_id"]) for s in subs if ObjectId.is_valid(s["customer_id"])}
        users = {
            str(u["_id"]): u
            for u in await self.user_repo.collection.find(
                {"_id": {"$in": list(holder_ids)}}, {"full_name": 1, "phone": 1}
            ).to_list(length=5000)
        }
        center_ids = {s["service_center_id"] for s in subs if s.get("service_center_id")}
        centers = {
            str(c["_id"]): c.get("name")
            for c in await self.repo.db.service_centers.find(
                {"_id": {"$in": [ObjectId(c) for c in center_ids if ObjectId.is_valid(c)]}}, {"name": 1}
            ).to_list(length=200)
        }

        rows = []
        active = expired = 0
        total_revenue = 0.0
        plan_counts: dict[str, int] = {}
        for s in subs:
            plan = plans.get(s.get("plan_id") or "")
            holder = users.get(s["customer_id"], {})
            status = s.get("effective_status")
            if status == SubscriptionStatus.ACTIVE.value:
                active += 1
            elif status == SubscriptionStatus.EXPIRED.value:
                expired += 1
            plan_name = plan.get("name") if plan else "Unknown plan"
            plan_counts[plan_name] = plan_counts.get(plan_name, 0) + 1
            amount_paid = s.get("amount_paid")
            if amount_paid is None and not s.get("service_center_id"):
                # A self-serve purchase never stamped amount_paid (that field
                # only exists for staff-issued offers) — purchased_price is
                # the actual amount charged for it at the time.
                amount_paid = s.get("purchased_price")
            total_revenue += float(amount_paid or 0)
            rows.append({
                "subscription_id": s["id"],
                "customer_id": s["customer_id"],
                "customer_name": holder.get("full_name", "Unknown"),
                "customer_phone": holder.get("phone"),
                "plan_id": s.get("plan_id"),
                "plan_name": plan_name,
                "status": status,
                "amount_paid": amount_paid,
                "discount_amount": s.get("discount_amount"),
                "coupon_code": s.get("coupon_code"),
                "payment_method": s.get("payment_method"),
                "auto_renew": s.get("auto_renew", False),
                "service_center_id": s.get("service_center_id"),
                "service_center_name": centers.get(s.get("service_center_id") or ""),
                "remaining_service_count": s.get("remaining_service_count"),
                "total_service_count": s.get("total_service_count"),
                "start_date": s.get("start_date"),
                "end_date": s.get("end_date"),
            })
        return {
            "kpis": {"total": len(rows), "active": active, "expired": expired, "total_revenue": round(total_revenue, 2)},
            "plan_breakdown": sorted(
                ({"plan_name": name, "count": count} for name, count in plan_counts.items()),
                key=lambda x: -x["count"],
            ),
            "rows": rows,
        }

    async def _resolve_order_centers(self, orders: list[dict]) -> dict[str, str | None]:
        """Maps a payment_order's str _id -> its service_center_id. A
        manager-issued link or auto-pay order stamps this directly at
        creation (payment_service.py's _create_manager_subscription_link /
        _create_manager_autopay_mandate); a cash sale, an autopay renewal
        row, and a customer's own self-serve autopay checkout never do —
        for those the center only lives on the subscription doc the order
        settled into, so it's resolved through subscription_id instead."""
        result: dict[str, str | None] = {}
        need_lookup: dict[str, str] = {}
        for o in orders:
            oid = str(o["_id"])
            if o.get("service_center_id"):
                result[oid] = o["service_center_id"]
            elif o.get("subscription_id"):
                need_lookup[oid] = o["subscription_id"]
            else:
                result[oid] = None
        if need_lookup:
            sub_ids = [ObjectId(v) for v in set(need_lookup.values()) if ObjectId.is_valid(v)]
            subs = (
                {
                    str(s["_id"]): s.get("service_center_id")
                    for s in await self.repo.collection.find({"_id": {"$in": sub_ids}}, {"service_center_id": 1}).to_list(length=len(sub_ids))
                }
                if sub_ids
                else {}
            )
            for oid, sub_id in need_lookup.items():
                result[oid] = subs.get(sub_id)
        return result

    async def center_plan_revenue(self, service_center_id: str, s: datetime, e: datetime) -> tuple[float, int]:
        """This center's plan revenue + count of plans sold in the window —
        the manager-KPI sibling of KpiService._plan_revenue (platform-
        wide). Resolves every matching order's center first (see
        _resolve_order_centers) since payment_orders doesn't uniformly
        carry service_center_id, then sums just this center's rows."""
        match = {"purpose": "subscription", "status": "paid", "created_at": {"$gte": s, "$lt": e}}
        # Sorted so that IF a window ever has more than 5000 platform-wide
        # subscription payments (not realistic at today's volume, but this
        # cap is a real long-term scaling limit — see this method's own
        # docstring), the most RECENT ones are what gets kept, not an
        # arbitrary slice.
        orders = await self.repo.db.payment_orders.find(match).sort("created_at", -1).to_list(length=5000)
        centers = await self._resolve_order_centers(orders)
        matching = [o for o in orders if centers.get(str(o["_id"])) == service_center_id]
        revenue = round(sum(o.get("amount_paise") or 0 for o in matching) / 100, 2)
        return revenue, len(matching)

    async def plan_purchases(
        self, s: datetime, e: datetime, page: int, page_size: int, service_center_id: str | None = None,
    ) -> tuple[list[dict], int]:
        """Every individual plan PAYMENT settled in the window — literally
        the same payment_orders match KpiService._plan_revenue sums, so
        this list's total and that dashboard tile's number always agree.
        Deliberately NOT admin_overview: that lists every subscription
        ever (all-time, no period) and sums a different field
        (user_subscriptions.amount_paid, which a manager-granted FREE plan
        has but a payment_orders row never will — the two would silently
        disagree with the revenue tile if reused here).

        service_center_id (optional): scopes to one center's own sales —
        a manager's own drill-down. Not every order carries this field
        directly (see _resolve_order_centers), so a scoped call resolves
        every row in the window and filters/paginates in Python instead
        of pushing the filter into the initial Mongo query the unscoped
        (admin) path below still uses — a center's own volume is small
        enough for this to be cheap, and the admin path is untouched."""
        match = {"purpose": "subscription", "status": "paid", "created_at": {"$gte": s, "$lt": e}}
        if service_center_id is None:
            total = await self.repo.db.payment_orders.count_documents(match)
            orders = (
                await self.repo.db.payment_orders.find(match)
                .sort("created_at", -1)
                .skip((page - 1) * page_size)
                .limit(page_size)
                .to_list(length=page_size)
            )
        else:
            all_orders = await self.repo.db.payment_orders.find(match).sort("created_at", -1).to_list(length=5000)
            centers = await self._resolve_order_centers(all_orders)
            matching = [o for o in all_orders if centers.get(str(o["_id"])) == service_center_id]
            total = len(matching)
            start = (page - 1) * page_size
            orders = matching[start : start + page_size]
        if not orders:
            return [], total

        customer_ids = [ObjectId(o["customer_id"]) for o in orders if o.get("customer_id") and ObjectId.is_valid(o["customer_id"])]
        plan_ids = [ObjectId(o["plan_id"]) for o in orders if o.get("plan_id") and ObjectId.is_valid(o["plan_id"])]
        users = (
            {
                str(u["_id"]): u
                for u in await self.user_repo.collection.find(
                    {"_id": {"$in": customer_ids}}, {"full_name": 1, "phone": 1}
                ).to_list(length=len(customer_ids))
            }
            if customer_ids
            else {}
        )
        plans = (
            {str(p["_id"]): p for p in await self.plan_repo.collection.find({"_id": {"$in": plan_ids}}).to_list(length=len(plan_ids))}
            if plan_ids
            else {}
        )

        # payment_orders.kind -> a label a manager/admin actually reads on
        # screen (never "manager_cash" verbatim).
        payment_method_labels = {"cash": "Cash", "manager_cash": "Cash", "link": "Online", "autopay": "Online (auto-pay)"}
        rows = []
        for o in orders:
            holder = users.get(o.get("customer_id") or "", {})
            plan = plans.get(o.get("plan_id") or "", {})
            rows.append(
                serialize_doc(
                    {
                        "_id": o["_id"],
                        "customer_id": o.get("customer_id"),
                        "customer_name": holder.get("full_name", "Unknown"),
                        "customer_phone": holder.get("phone"),
                        "plan_id": o.get("plan_id"),
                        "plan_name": plan.get("name", "Unknown plan"),
                        "amount": round((o.get("amount_paise") or 0) / 100, 2),
                        "discount": round((o.get("discount_paise") or 0) / 100, 2) if o.get("discount_paise") else None,
                        "coupon_code": o.get("coupon_code"),
                        "payment_method": payment_method_labels.get(o.get("kind") or "", o.get("kind")),
                        "subscription_id": o.get("subscription_id"),
                        "created_at": o.get("created_at"),
                    }
                )
            )
        return rows, total
