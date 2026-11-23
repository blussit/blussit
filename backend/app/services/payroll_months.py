"""Attendance, the month statement and finalize (§7.13 D9, D10, D11, H5, H20).
Inputs are gathered here (attendance rows, jobs done, approved leave, admin
holidays); the day-by-day maths is the pure `payroll_salary.compute_month`."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from bson import ObjectId
from pymongo.errors import DuplicateKeyError

from app.core.exceptions import BadRequestException, ConflictException
from app.services import payroll_terms as pt
from app.services.customer_wallet_service import in_transaction
from app.services.payroll_base import ist_day, rupees_text, today_ist
from app.services.payroll_pay import PayMixin
from app.services.payroll_salary import compute_month, month_bounds, month_days
from app.utils.timezone import IST

_MONTH_NAMES = ["", "January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"]


def month_label(month: str) -> str:
    return f"{_MONTH_NAMES[int(month[5:7])]} {month[:4]}"


def _utc_bounds(first: str, last: str) -> tuple[datetime, datetime]:
    """IST days (inclusive) as naive-UTC instants [from, to)."""
    lo = datetime.fromisoformat(first).replace(tzinfo=IST).astimezone(timezone.utc).replace(tzinfo=None)
    hi = (datetime.fromisoformat(last) + timedelta(days=1)).replace(tzinfo=IST).astimezone(timezone.utc).replace(tzinfo=None)
    return lo, hi


def check_month(month: str | None) -> str:
    month = month or today_ist()[:7]
    try:
        date.fromisoformat(month + "-01")
    except ValueError:
        raise BadRequestException("Pick a month like 2026-11.") from None
    return month


class MonthsMixin(PayMixin):
    # ---------- inputs ----------

    async def month_inputs(self, captain: dict, month: str) -> tuple[dict, set, set, set]:
        cid = str(captain["_id"])
        first, last = month_bounds(month)
        lo, hi = _utc_bounds(first, last)
        att_rows = await self.db.attendance.find(
            {"captain_id": cid, "attendance_date": {"$gte": first, "$lte": last}, "is_deleted": {"$ne": True}},
        ).to_list(length=40)
        attendance = {r["attendance_date"]: r for r in att_rows}
        jobs = await self.bookings.find(
            {
                "captain_id": cid, "is_deleted": {"$ne": True}, "status": {"$in": ["completed", "service_started"]},
                "$or": [{"completed_at": {"$gte": lo, "$lt": hi}}, {"service_started_at": {"$gte": lo, "$lt": hi}}],
            },
            {"completed_at": 1, "service_started_at": 1, "status": 1},
        ).to_list(length=2000)
        job_days: set[str] = set()
        for b in jobs:
            for field in ("completed_at", "service_started_at"):
                day = ist_day(b.get(field))
                if day and first <= day <= last and (field == "service_started_at" or b.get("status") == "completed"):
                    job_days.add(day)
        leaves = await self.db.leave_requests.find(
            {"captain_id": cid, "status": "approved", "start_date": {"$lte": last}, "end_date": {"$gte": first}, "is_deleted": {"$ne": True}},
        ).to_list(length=100)
        leave_days: set[str] = set()
        for lv in leaves:
            try:
                d, end = date.fromisoformat(str(lv["start_date"])[:10]), date.fromisoformat(str(lv["end_date"])[:10])
            except ValueError:
                continue
            while d <= end:
                if first <= d.isoformat() <= last:
                    leave_days.add(d.isoformat())
                d += timedelta(days=1)
        holiday_days = await self._holiday_days(captain.get("service_center_id"), month)
        return attendance, job_days, leave_days, holiday_days

    async def _holiday_days(self, center_id: str | None, month: str) -> set[str]:
        first, last = month_bounds(month)
        lo, hi = _utc_bounds(first, last)
        rows = await self.db.holidays.find(
            {"active": True, "is_deleted": {"$ne": True}, "start_at": {"$lt": hi}, "end_at": {"$gt": lo}},
        ).to_list(length=200)
        days: set[str] = set()
        for h in rows:
            if h.get("scope", "all") != "all" and str(center_id) not in [str(x) for x in h.get("service_center_ids") or []]:
                continue
            start, end = h["start_at"], h["end_at"]
            for d in month_days(month):
                day_lo, day_hi = _utc_bounds(d.isoformat(), d.isoformat())
                if start <= day_lo and end >= day_hi - timedelta(seconds=2):
                    days.add(d.isoformat())
        return days

    async def salary_month(self, captain: dict, month: str) -> dict:
        attendance, job_days, leave_days, holiday_days = await self.month_inputs(captain, month)
        result = compute_month(captain, month, today_ist(), attendance, job_days, leave_days, holiday_days)
        cid = str(captain["_id"])
        sums = await self.repo.sums_by_kind(cid, extra={"period": month})
        petrol_rows = await self.ledger.count_documents({"captain_id": cid, "period": month, "kind": "allowance", "component": "petrol"})
        accrual_rows = await self.ledger.count_documents({"captain_id": cid, "period": month, "kind": "salary_accrual"})
        finalized = bool(accrual_rows or petrol_rows)
        posted_salary = sums.get("salary_accrual", 0)
        petrol_posted = int((await self.ledger.aggregate([
            {"$match": {"captain_id": cid, "period": month, "kind": "allowance", "component": "petrol"}},
            {"$group": {"_id": None, "p": {"$sum": "$amount_paise"}}},
        ]).to_list(length=1) or [{"p": 0}])[0]["p"] or 0)
        result.update({
            "finalized": finalized, "finalized_paise": posted_salary if finalized else None, "finalized_petrol_paise": petrol_posted if finalized else None,
            "payable_paise": posted_salary if accrual_rows else result["computed_paise"],
            "basis": pt.basis_of(captain, min(month_bounds(month)[1], today_ist())),
        })
        return result

    # ---------- attendance override ----------

    async def mark_attendance(self, captain_id: str, payload, actor) -> dict:
        captain = await self.captain_or_404(captain_id, actor.role, actor.service_center_id)
        day = payload.date
        try:
            date.fromisoformat(day)
        except ValueError:
            raise BadRequestException("Pick a real date.") from None
        if day > today_ist():
            raise BadRequestException("Attendance can't be marked for a future day.")
        start = pt.start_day(captain)
        if day < start:
            raise BadRequestException(f"This captain started on {self.day_label(start)} — earlier days can't be marked.")
        end = (captain.get("pay") or {}).get("end_date")
        if end and day > end:
            raise BadRequestException(f"This captain's last day was {self.day_label(end)}.")
        if payload.status == "leave":
            message = pt.leave_on_off_day_message(captain, day)
            if message:
                raise BadRequestException(message)
        who = await self.actor_info(actor)
        now = datetime.now(timezone.utc)
        before = await self.db.attendance.find_one({"captain_id": captain_id, "attendance_date": day})
        await self.db.attendance.update_one(
            {"captain_id": captain_id, "attendance_date": day},
            {
                "$set": {
                    "status": payload.status, "paid_leave": bool(payload.paid_leave and payload.status == "leave"),
                    "override_note": payload.note, "marked_by": who["id"], "marked_by_role": who["role"], "marked_by_name": who["name"],
                    "service_center_id": captain.get("service_center_id"), "updated_at": now,
                },
                "$setOnInsert": {"captain_id": captain_id, "attendance_date": day, "created_at": now, "is_deleted": False},
            },
            upsert=True,
        )
        await self.audit(who, "PAYROLL_ATTENDANCE", captain_id, {
            "date": day, "before": (before or {}).get("status"), "after": payload.status, "paid_leave": payload.paid_leave, "note": payload.note,
        }, captain.get("service_center_id"))
        month = await self.salary_month(captain, day[:7])
        row = next(d for d in month["days"] if d["date"] == day)
        return {**row, "month": day[:7]}

    async def accrue_month(
        self, s, captain_id: str, center_id: str | None, month: str, target_salary: int, target_petrol: int,
        override_amount_paise, reason, who: dict, period_end: str,
    ) -> list[dict]:
        """Post (inside the caller's transaction) only the difference between
        what the ledger already holds for `month` and the targets — the body of
        Finalize Month, shared with the salary invoice (§7.14)."""
        posted = []
        for kind, component, target, prefix, note in (
            ("salary_accrual", None, target_salary, "sal", f"Salary for {month_label(month)}"),
            ("allowance", "petrol", target_petrol, "pet", f"Petrol for {month_label(month)}"),
        ):
            match = {"captain_id": captain_id, "period": month, "kind": kind, **({"component": component} if component else {})}
            existing = await self.ledger.aggregate(
                [{"$match": match}, {"$group": {"_id": None, "p": {"$sum": "$amount_paise"}, "n": {"$sum": 1}}}], session=s,
            ).to_list(length=1)
            have, n = (int(existing[0]["p"]), int(existing[0]["n"])) if existing else (0, 0)
            delta = target - have
            if delta == 0:
                continue
            key = f"{prefix}:{captain_id}:{month}" + (f":{n}" if n else "")
            why = (f" — payable set to {rupees_text(target)}: {reason}" if kind == "salary_accrual" and override_amount_paise is not None else "")
            row = await self.post({
                "captain_id": captain_id, "service_center_id": center_id, "kind": kind, "amount_paise": delta, "period": month,
                "component": component, "note": (note + why)[:300], "key": key, "entry_date": period_end,
                "override_amount_paise": override_amount_paise if kind == "salary_accrual" else None,
                "actor_id": who["id"], "actor_name": who["name"], "actor_role": who["role"],
            }, session=s)
            posted.append({"kind": kind, "delta": delta, "key": row["key"]})
        return posted

    # ---------- finalize ----------

    async def finalize_month(self, captain_id: str, month: str, payload, actor) -> dict:
        captain = await self.captain_or_404(captain_id, actor.role, actor.service_center_id)
        month = check_month(month)
        if month > today_ist()[:7]:
            raise BadRequestException("A month can be finalized once it has started — not before.")
        if month < pt.start_day(captain)[:7]:
            raise BadRequestException("This captain hadn't started in that month.")
        if payload.override_amount_paise is not None and not (payload.reason or "").strip():
            raise BadRequestException("Say why the salary is changed from the computed amount.")
        computed = await self.salary_month(captain, month)
        who = await self.actor_info(actor)
        target_salary = computed["computed_paise"] if payload.override_amount_paise is None else int(payload.override_amount_paise)
        target_petrol = computed["petrol_paise"]
        period_end = min(month_bounds(month)[1], today_ist())
        center_id = captain.get("service_center_id")

        async def _do(s):
            return await self.accrue_month(
                s, captain_id, center_id, month, target_salary, target_petrol, payload.override_amount_paise, payload.reason, who, period_end,
            )

        try:
            posted = await in_transaction(self.db, _do, None)
        except DuplicateKeyError:
            raise ConflictException("This month was just finalized by someone else — reload to see it.") from None
        if posted:
            await self.audit(who, "PAYROLL_FINALIZE", captain_id, {
                "month": month, "computed_paise": computed["computed_paise"], "petrol_paise": computed["petrol_paise"],
                "override_amount_paise": payload.override_amount_paise, "reason": payload.reason, "posted": posted,
            }, center_id)
        result = await self.salary_month(captain, month)
        result["posted"] = posted
        return result
