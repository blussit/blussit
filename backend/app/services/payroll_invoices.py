"""Pay wizard + invoices (§7.14): what a salary month / commission period
invoice would contain (preview), paying it, listing and reading invoices, and
the reconciliation that proves the books agree.

Salary invoices are per MONTH (part payments tracked, never invoiced twice in
full); commission invoices cover any period and settle every booking exactly
once (`commission_settled_by` guard, see InvoiceCore._settle_in)."""
from __future__ import annotations

from datetime import date, timedelta

from bson import ObjectId

from app.core.authz import ensure_own_center, manager_center_or_raise
from app.core.exceptions import BadRequestException, ConflictException, ForbiddenException, NotFoundException
from app.repositories.payroll_repository import DUE_KINDS, EARN_KINDS, PAID_KINDS
from app.services import payroll_terms as pt
from app.services.payroll_base import ist_day, rupees_text, today_ist
from app.services.payroll_invoice_core import PAYMENT_KINDS, check_day, check_paid_on
from app.services.payroll_months import MonthsMixin, _utc_bounds, check_month, month_label
from app.services.payroll_salary import month_bounds, previous_month

MAX_PERIOD_DAYS = 366
MAX_COMMISSION_LINES = 1500
INVOICE_PAGE_MAX = 100


def month_status(gross: int, previously_paid: int) -> str:
    if gross <= 0:
        return "none"
    if previously_paid <= 0:
        return "unpaid"
    return "part_paid" if previously_paid < gross else "paid"


class InvoiceMixin(MonthsMixin):
    # ================= salary =================

    async def _month_posted(self, s, captain_id: str, month: str, kind: str, component: str | None = None) -> tuple[int, int]:
        match = {"captain_id": captain_id, "period": month, "kind": kind, **({"component": component} if component else {})}
        rows = await self.ledger.aggregate(
            [{"$match": match}, {"$group": {"_id": None, "p": {"$sum": "$amount_paise"}, "n": {"$sum": 1}}}], session=s,
        ).to_list(length=1)
        return (int(rows[0]["p"]), int(rows[0]["n"])) if rows else (0, 0)

    async def _salary_plan(self, captain: dict, month: str, *, session=None, accrue_who: dict | None = None) -> dict:
        """The salary invoice of one month as the database stands. With
        `accrue_who` (inside the pay transaction) an un-finalized month is
        finalized to its computed amount first, so dues = accrued − paid holds."""
        cid = str(captain["_id"])
        center_id = captain.get("service_center_id")
        first, last = month_bounds(month)
        sm = await self.salary_month(captain, month)
        sal, sal_n = await self._month_posted(session, cid, month, "salary_accrual")
        pet, pet_n = await self._month_posted(session, cid, month, "allowance", "petrol")
        finalized = bool(sal_n or pet_n)
        if not finalized and accrue_who is not None:
            await self.accrue_month(
                session, cid, center_id, month, sm["computed_paise"], sm["petrol_paise"], None, None, accrue_who, min(last, today_ist()),
            )
            sal, sal_n = await self._month_posted(session, cid, month, "salary_accrual")
            pet, pet_n = await self._month_posted(session, cid, month, "allowance", "petrol")
            finalized = True
        salary_amt = sal if finalized else sm["computed_paise"]
        petrol_amt = pet if finalized else sm["petrol_paise"]
        paid_leave = sum(1 for d in sm["days"] if d["status"] == "leave" and d.get("paid_leave"))
        lines: list[dict] = [{
            "type": "salary", "label": f"Salary — {month_label(month)}", "month": month, "expected_working_days": sm["expected_working_days"],
            "present_days": sm["present_days"], "half_days": sm["half_days"], "leave_days": sm["leave_days"], "paid_leave_days": paid_leave,
            "unpaid_days": sm["absent_days"] + max(0, sm["leave_days"] - paid_leave), "holidays": sm["holidays"], "paid_days": sm["paid_days"],
            "amount_paise": int(salary_amt), "computed_paise": int(sm["computed_paise"]),
        }]
        if petrol_amt or sm["petrol_paise"]:
            lines.append({"type": "petrol", "label": f"Petrol — {month_label(month)}", "amount_paise": int(petrol_amt)})
        others = await self.ledger.find({
            "captain_id": cid, "kind": {"$in": ["bonus", "deduction", "allowance"]}, "entry_date": {"$gte": first, "$lte": last},
            "source": {"$ne": "invoice_adjustment"}, "component": {"$ne": "petrol"},
        }, session=session).sort([("created_at", 1), ("_id", 1)]).to_list(length=200)
        for row in others:
            lines.append({
                "type": row["kind"], "label": (row.get("note") or row["kind"].title())[:120], "amount_paise": int(row["amount_paise"]),
                "date": row.get("entry_date"), "ledger_id": str(row["_id"]),
            })
        gross = sum(int(l["amount_paise"]) for l in lines)
        prev_rows = await self.db.payroll_invoices.find(
            {"captain_id": cid, "kind": "salary", "month": month, "status": "paid"}, {"paid_paise": 1, "number": 1}, session=session,
        ).to_list(length=100)
        previously_paid = sum(int(r.get("paid_paise") or 0) for r in prev_rows)
        today = today_ist()
        return {
            "lines": lines, "gross_paise": gross, "previously_paid_paise": previously_paid, "due_paise": gross - previously_paid,
            "month": month, "period_from": first, "period_to": last, "settle": [], "adjust": "bonus_only",
            "meta": {
                "finalized": finalized, "computed_paise": int(sm["computed_paise"]), "computed_petrol_paise": int(sm["petrol_paise"]),
                "stale": bool(finalized and (sal != sm["computed_paise"] or pet != sm["petrol_paise"])),
                "month_open": last >= today, "status": month_status(gross, previously_paid), "invoices": [r.get("number") for r in prev_rows],
            },
        }

    def _salary_guard(self, captain: dict, month: str) -> None:
        if pt.basis_of(captain) == "legacy" and not captain.get("pay"):
            raise BadRequestException("Set this captain's pay terms (salary or commission) before paying them.")
        if month > today_ist()[:7]:
            raise BadRequestException("A month can be paid once it has started — not before.")
        if month < pt.start_day(captain)[:7]:
            raise BadRequestException("This captain hadn't started in that month.")

    async def salary_months(self, captain_id: str, actor) -> dict:
        captain = await self.captain_or_404(captain_id, actor.role, actor.service_center_id)
        applicable = pt.basis_of(captain) == "salary" or bool(await self.ledger.count_documents({"captain_id": captain_id, "kind": "salary_accrual"}, limit=1))
        today_month = today_ist()[:7]
        start_month = pt.start_day(captain)[:7]
        months, cursor = [], today_month
        for _ in range(6):
            if cursor < start_month:
                break
            if applicable:
                plan = await self._salary_plan(captain, cursor)
                months.append({
                    "month": cursor, "label": month_label(cursor), "status": plan["meta"]["status"], "payable_paise": plan["gross_paise"],
                    "paid_paise": plan["previously_paid_paise"], "balance_paise": plan["due_paise"], "finalized": plan["meta"]["finalized"],
                    "computed_paise": plan["meta"]["computed_paise"], "month_open": plan["meta"]["month_open"],
                })
            cursor = previous_month(cursor)
        default = next((m["month"] for m in months if m["month"] != today_month and m["balance_paise"] > 0), today_month)
        return {"applicable": applicable, "months": months, "default_month": default, "current_month": today_month}

    # ================= commission =================

    async def _service_names(self, bookings: list[dict]) -> dict[str, str]:
        ids = {ObjectId(s) for b in bookings for s in (b.get("service_ids") or []) if ObjectId.is_valid(str(s))}
        if not ids:
            return {}
        rows = await self.db.services.find({"_id": {"$in": list(ids)}}, {"name": 1}).to_list(length=len(ids))
        return {str(r["_id"]): r.get("name") for r in rows}

    async def _commission_plan(
        self, captain: dict, first: str, last: str, booking_ids: list[str] | None = None, *, session=None, with_candidates: bool = False,
    ) -> dict:
        cid = str(captain["_id"])
        totals = {
            r["_id"]: int(r["p"] or 0) for r in await self.ledger.aggregate([
                {"$match": {"captain_id": cid, "kind": {"$in": ["commission", "commission_adjust"]}, "booking_id": {"$type": "string"}}},
                {"$group": {"_id": "$booking_id", "p": {"$sum": "$amount_paise"}}},
            ], session=session).to_list(length=None)
        }
        row_settled = {
            r["_id"]: int(r["p"] or 0) for r in await self.db.payroll_settlements.aggregate([
                {"$match": {"captain_id": cid}}, {"$group": {"_id": "$booking_id", "p": {"$sum": "$amount_paise"}}},
            ], session=session).to_list(length=None)
        }
        lo, hi = _utc_bounds(first, last)
        period_docs = await self.bookings.find(
            {"commission_captain_id": cid, "completed_at": {"$gte": lo, "$lt": hi}, "is_deleted": {"$ne": True}, "status": "completed"},
            session=session,
        ).sort([("completed_at", 1)]).limit(MAX_COMMISSION_LINES).to_list(length=MAX_COMMISSION_LINES)
        in_period = {str(b["_id"]) for b in period_docs}
        # Settled earlier, total changed since (an edit, a refund, a reassignment): any date.
        revised_ids = [
            b for b in set(totals) | set(row_settled)
            if b not in in_period and row_settled.get(b, 0) != 0 and totals.get(b, 0) != row_settled.get(b, 0) and ObjectId.is_valid(b)
        ][:MAX_COMMISSION_LINES]
        revised_docs = (
            await self.bookings.find({"_id": {"$in": [ObjectId(b) for b in revised_ids]}}, session=session).to_list(length=len(revised_ids))
            if revised_ids else []
        )
        wanted = set(booking_ids) if booking_ids else None
        docs = [(b, False) for b in period_docs] + [(b, True) for b in revised_docs]
        names = await self._service_names([b for b, _ in docs])
        lines, candidates, not_set = [], [], []
        for b, revised in docs:
            bid = str(b["_id"])
            total = totals.get(bid, 0)
            settled = int(((b.get("commission_settled_by") or {}).get(cid)) or 0)
            amount = total - settled
            row = {
                "type": "commission", "booking_id": bid, "booking_number": b.get("booking_number"), "settle": True,
                "date": ist_day(b.get("completed_at")), "vehicle_type": b.get("vehicle_label") or b.get("vehicle_type"),
                "service_name": ", ".join(names[str(x)] for x in (b.get("service_ids") or [])[:3] if str(x) in names) or None,
                "base_paise": int(b.get("commission_base_paise") or 0), "amount_paise": amount, "total_paise": total, "settled_paise": settled,
                "kind": "revised" if settled != 0 else "new",
                "label": f"Booking {b.get('booking_number')}",
            }
            if b.get("commission_state") == "pending" and not revised and total == 0 and settled == 0:
                not_set.append({**row, "not_set": True, "plan_covered": bm_plan_covered(b), "selectable": False})
                continue
            if amount == 0:
                continue
            selected = wanted is None or bid in wanted
            candidates.append({**row, "selected": selected, "selectable": True})
            if selected:
                lines.append(row)
        gross = sum(l["amount_paise"] for l in lines)
        plan = {
            "lines": [{k: v for k, v in l.items() if k not in ("total_paise", "settled_paise")} for l in lines],
            "gross_paise": gross, "due_paise": gross, "period_from": first, "period_to": last, "month": None,
            "settle": [
                {"booking_id": l["booking_id"], "booking_number": l["booking_number"], "total_paise": l["total_paise"], "amount_paise": l["amount_paise"]}
                for l in lines
            ],
            "adjust": "signed",
        }
        if with_candidates:
            plan["candidates"] = candidates
            plan["not_set"] = not_set
            plan["truncated"] = len(period_docs) >= MAX_COMMISSION_LINES
        return plan

    @staticmethod
    def _commission_period(from_date: str | None, to_date: str | None) -> tuple[str, str]:
        to_day = check_day(to_date or today_ist(), "The end date")
        from_day = check_day(from_date or to_day, "The start date")
        if from_day > to_day:
            raise BadRequestException("The start date can't be after the end date.")
        if to_day > today_ist():
            raise BadRequestException("The period can't end in the future.")
        if (date.fromisoformat(to_day) - date.fromisoformat(from_day)).days >= MAX_PERIOD_DAYS:
            raise BadRequestException("A commission invoice can cover a year at most.")
        return from_day, to_day

    # ================= preview =================

    async def pay_preview(self, captain_id: str, kind: str, month: str | None, from_date: str | None, to_date: str | None, actor) -> dict:
        captain = await self.captain_or_404(captain_id, actor.role, actor.service_center_id)
        base = {"captain_id": captain_id, "captain_name": captain.get("full_name"), "kind": kind, "basis": pt.basis_of(captain)}
        if kind == "salary":
            month = check_month(month)
            self._salary_guard(captain, month)
            plan = await self._salary_plan(captain, month)
            return {
                **base, "month": month, "month_label": month_label(month), "lines": plan["lines"], "gross_paise": plan["gross_paise"],
                "previously_paid_paise": plan["previously_paid_paise"], "due_paise": plan["due_paise"], "can_pay": plan["due_paise"] > 0,
                "default_amount_paise": max(0, plan["due_paise"]), **plan["meta"],
                "warnings": self._salary_warnings(plan),
            }
        if kind == "commission":
            first, last = self._commission_period(from_date, to_date)
            plan = await self._commission_plan(captain, first, last, None, with_candidates=True)
            warnings = []
            if plan["not_set"]:
                warnings.append(f"{len(plan['not_set'])} booking(s) have no commission amount yet — set it in the Commissions tab to include them.")
            if any(c["kind"] == "revised" for c in plan["candidates"]):
                warnings.append("Some bookings were paid before and their commission has changed since — the difference is on this invoice.")
            if plan["truncated"]:
                warnings.append(f"Only the first {MAX_COMMISSION_LINES} bookings of this period are shown — pick a shorter period.")
            return {
                **base, "period_from": first, "period_to": last, "lines": plan["lines"], "candidates": plan["candidates"], "not_set": plan["not_set"],
                "gross_paise": plan["gross_paise"], "due_paise": plan["due_paise"], "can_pay": plan["gross_paise"] > 0,
                "default_amount_paise": max(0, plan["gross_paise"]), "warnings": warnings,
            }
        raise BadRequestException("Preview is for a salary month or a commission period.")

    @staticmethod
    def _salary_warnings(plan: dict) -> list[str]:
        meta, out = plan["meta"], []
        if meta["month_open"]:
            out.append("This month is still running — only the days so far are counted. A later invoice picks up the rest.")
        if meta["stale"]:
            out.append("The finalized amount differs from today's attendance maths — run Finalize Month first if the attendance was edited.")
        if plan["due_paise"] <= 0 and plan["gross_paise"] > 0:
            out.append("This month is already paid in full.")
        return out

    # ================= pay =================

    async def pay_invoice(self, captain_id: str, payload, actor) -> dict:
        captain = await self.captain_or_404(captain_id, actor.role, actor.service_center_id)
        paid_on = check_paid_on(payload.paid_on)
        who = await self.actor_info(actor)
        kind = payload.kind
        month = None
        first = last = None
        if kind == "salary":
            month = check_month(payload.month)
            self._salary_guard(captain, month)

            async def prepare(s):
                plan = await self._salary_plan(captain, month, session=s, accrue_who=who)
                if plan["due_paise"] <= 0:
                    raise BadRequestException(
                        f"{month_label(month)} is already paid in full." if plan["gross_paise"] > 0 else f"There is no salary to pay for {month_label(month)}."
                    )
                if payload.expected_gross_paise is not None and int(payload.expected_gross_paise) != plan["due_paise"]:
                    raise ConflictException("This month's balance changed while you were paying — open the invoice again.")
                return plan
        elif kind == "commission":
            first, last = self._commission_period(payload.from_date, payload.to_date)

            async def prepare(s):
                plan = await self._commission_plan(captain, first, last, payload.booking_ids, session=s)
                if not plan["lines"]:
                    raise BadRequestException("There is no unpaid commission in this period (or those bookings were just paid).")
                if plan["gross_paise"] <= 0:
                    raise BadRequestException("Recoveries on these bookings come to more than their commission — add more bookings, or leave it for the next invoice.")
                if payload.expected_gross_paise is not None and int(payload.expected_gross_paise) != plan["gross_paise"]:
                    raise ConflictException("These commissions changed while you were paying — open the invoice again.")
                return plan
        else:
            if payload.amount_paise is None:
                raise BadRequestException("Enter the amount.")
            _, _, label = PAYMENT_KINDS[kind]
            amount = int(payload.amount_paise)

            async def prepare(_s):
                return {
                    "lines": [{"type": "payment", "label": (payload.note or label)[:120], "amount_paise": amount}], "gross_paise": amount,
                    "due_paise": amount, "settle": [], "adjust": "none",
                }

        invoice, _row, duplicate = await self.create_payment_invoice(
            captain, kind=kind, method=payload.method, paid_on=paid_on, note=payload.note, idempotency_key=payload.idempotency_key,
            cash_offset_paise=int(payload.cash_offset_paise), amount_paise=payload.amount_paise, reason=payload.reason, who=who, prepare=prepare,
        )
        dues = await self.repo.dues(captain_id)
        return {**self.invoice_view(invoice), "duplicate": duplicate, "dues_paise": dues["dues_paise"]}

    # ================= reads =================

    async def list_invoices(
        self, actor, *, center_id: str | None = None, captain_id: str | None = None, kind: str | None = None, status: str | None = None,
        date_from: str | None = None, date_to: str | None = None, page: int = 1, page_size: int = 30,
    ) -> tuple[list[dict], int, dict]:
        scope = manager_center_or_raise(actor.role, actor.service_center_id)
        flt: dict = {}
        if scope or center_id:
            flt["service_center_id"] = scope or center_id
        if captain_id:
            flt["captain_id"] = captain_id
        if kind:
            flt["kind"] = kind
        if status:
            flt["status"] = status
        if date_from or date_to:
            flt["paid_on"] = {**({"$gte": check_day(date_from, "The from date")} if date_from else {}), **({"$lte": check_day(date_to, "The to date")} if date_to else {})}
        return await self._invoice_page(flt, page, page_size)

    async def _invoice_page(self, flt: dict, page: int, page_size: int) -> tuple[list[dict], int, dict]:
        page_size = min(max(page_size, 1), INVOICE_PAGE_MAX)
        total = await self.db.payroll_invoices.count_documents(flt)
        rows = await self.db.payroll_invoices.find(flt, {"lines": 0}).sort([("created_at", -1), ("_id", -1)]).skip((max(page, 1) - 1) * page_size).limit(page_size).to_list(length=page_size)
        sums = await self.db.payroll_invoices.aggregate([
            {"$match": {**flt, "status": "paid"}}, {"$group": {"_id": None, "paid": {"$sum": "$paid_paise"}, "n": {"$sum": 1}}},
        ]).to_list(length=1)
        totals = {"paid_paise": int(sums[0]["paid"]) if sums else 0, "count": int(sums[0]["n"]) if sums else 0}
        return [self.invoice_view(r, with_lines=False) for r in rows], total, totals

    async def get_invoice(self, actor, invoice_id: str) -> dict:
        if not ObjectId.is_valid(invoice_id or ""):
            raise NotFoundException("Invoice not found")
        doc = await self.db.payroll_invoices.find_one({"_id": ObjectId(invoice_id)})
        if not doc:
            raise NotFoundException("Invoice not found")
        if actor.role == "captain":
            if doc.get("captain_id") != actor.id:
                raise NotFoundException("Invoice not found")
            return self.invoice_view(doc, for_captain=True)
        if actor.role == "manager":
            ensure_own_center(actor.role, actor.service_center_id, doc.get("service_center_id"))
        elif actor.role != "admin":
            raise ForbiddenException("Not allowed")
        return self.invoice_view(doc)

    async def void_invoice(self, invoice_id: str, reason: str, actor) -> dict:
        view = await self.get_invoice(actor, invoice_id)
        if actor.role == "captain":
            raise ForbiddenException("Only a manager or admin can cancel an invoice.")
        result = await self.void_payment(view["ledger_id"], reason, actor)
        fresh = await self.db.payroll_invoices.find_one({"_id": ObjectId(invoice_id)})
        return {**self.invoice_view(fresh), "duplicate": bool(result.get("duplicate"))}

    async def my_invoices(self, actor, page: int = 1, page_size: int = 30) -> tuple[list[dict], int, dict]:
        rows, total, totals = await self._invoice_page({"captain_id": actor.id}, page, page_size)
        for r in rows:
            r.pop("finance_entry_id", None)
        return rows, total, totals

    # ================= reconcile =================

    async def reconcile(self, center_id: str | None = None, limit: int = 500) -> dict:
        """Proves the books agree (§7.14). Returns the mismatches — there should be none."""
        limit = min(max(limit, 1), 2000)
        flt = {"service_center_id": center_id} if center_id else {}
        ids = set(await self.ledger.distinct("captain_id", flt)) | set(await self.db.payroll_invoices.distinct("captain_id", flt))
        captain_ids = sorted(ids)[:limit]
        mismatches: list[dict] = []

        def bad(captain_id, check, expected, actual, detail=""):
            mismatches.append({"captain_id": captain_id, "check": check, "expected_paise": expected, "actual_paise": actual, "detail": detail})

        by_kind: dict[str, dict[str, int]] = {}
        for r in await self.ledger.aggregate([
            {"$match": {"captain_id": {"$in": captain_ids}}}, {"$group": {"_id": {"c": "$captain_id", "k": "$kind"}, "p": {"$sum": "$amount_paise"}}},
        ]).to_list(length=None):
            by_kind.setdefault(r["_id"]["c"], {})[r["_id"]["k"]] = int(r["p"] or 0)
        invoices = await self.db.payroll_invoices.find({"captain_id": {"$in": captain_ids}}).to_list(length=None)
        by_inv: dict[str, list[dict]] = {}
        for inv in invoices:
            by_inv.setdefault(inv["captain_id"], []).append(inv)
        settle_rows: dict[str, int] = {}
        for r in await self.db.payroll_settlements.aggregate([
            {"$match": {"captain_id": {"$in": captain_ids}}}, {"$group": {"_id": "$captain_id", "p": {"$sum": "$amount_paise"}}},
        ]).to_list(length=None):
            settle_rows[r["_id"]] = int(r["p"] or 0)
        booking_settled: dict[str, int] = {}
        for r in await self.bookings.aggregate([
            {"$match": {"commission_settled_by": {"$exists": True}}}, {"$project": {"s": {"$objectToArray": "$commission_settled_by"}}},
            {"$unwind": "$s"}, {"$match": {"s.k": {"$in": captain_ids}}}, {"$group": {"_id": "$s.k", "p": {"$sum": "$s.v"}}},
        ]).to_list(length=None):
            booking_settled[r["_id"]] = int(r["p"] or 0)
        payout_rows = await self.ledger.find(
            {"captain_id": {"$in": captain_ids}, "kind": {"$in": ["payout", "advance"]}, "invoice_id": {"$type": "string"}},
        ).to_list(length=None)
        voided_ledger = {r["voids"] for r in await self.ledger.find({"captain_id": {"$in": captain_ids}, "kind": "payout_void"}, {"voids": 1}).to_list(length=None)}
        paid_invoiced: dict[str, int] = {}
        for row in payout_rows:
            if str(row["_id"]) not in voided_ledger:
                paid_invoiced[row["captain_id"]] = paid_invoiced.get(row["captain_id"], 0) - int(row["amount_paise"])
        for cid in captain_ids:
            kinds = by_kind.get(cid, {})
            accrued = sum(kinds.get(k, 0) for k in EARN_KINDS)
            paid = -sum(kinds.get(k, 0) for k in PAID_KINDS)
            signed = sum(kinds.get(k, 0) for k in DUE_KINDS)
            dues = (await self.repo.dues(cid))["dues_paise"]
            if dues != accrued - paid or dues != signed:
                bad(cid, "dues = accrued − paid", accrued - paid, dues, f"signed ledger sum {signed}")
            mine = by_inv.get(cid, [])
            live = [i for i in mine if i.get("status") == "paid"]
            inv_paid = sum(int(i.get("paid_paise") or 0) for i in live)
            if inv_paid != paid_invoiced.get(cid, 0):
                bad(cid, "paid invoices = invoiced ledger payments", paid_invoiced.get(cid, 0), inv_paid)
            for i in mine:
                gone = i.get("ledger_id") in voided_ledger
                if (i.get("status") == "void") != gone:
                    bad(cid, "invoice status follows its payment's void", 0, int(i.get("paid_paise") or 0), f"invoice {i.get('number')}")
            lines_sum = sum(int(l.get("amount_paise") or 0) for i in live for l in i.get("lines") or [] if l.get("settle"))
            if lines_sum != settle_rows.get(cid, 0) or lines_sum != booking_settled.get(cid, 0):
                bad(cid, "settled commission", lines_sum, booking_settled.get(cid, 0), f"settlement rows {settle_rows.get(cid, 0)}")
        # Finance ↔ invoices, per center (franchise-managed centers write no Finance row).
        fin_flt = {"source": "payroll", "is_deleted": {"$ne": True}, **({"service_center_id": center_id} if center_id else {})}
        finance: dict = {}
        for r in await self.db.finance_entries.aggregate([
            {"$match": fin_flt}, {"$group": {"_id": "$service_center_id", "p": {"$sum": "$amount_paise"}}},
        ]).to_list(length=None):
            finance[r["_id"]] = int(r["p"] or 0)
        inv_net: dict = {}
        for r in await self.db.payroll_invoices.aggregate([
            {"$match": {"status": "paid", "finance_impact": True, **flt}}, {"$group": {"_id": "$service_center_id", "p": {"$sum": "$net_paise"}}},
        ]).to_list(length=None):
            inv_net[r["_id"]] = int(r["p"] or 0)
        for c in set(finance) | set(inv_net):
            if finance.get(c, 0) != inv_net.get(c, 0):
                bad(None, "Finance debits = paid invoices (net of cash offset)", inv_net.get(c, 0), finance.get(c, 0), f"center {c}")
        return {
            "ok": not mismatches, "captains_checked": len(captain_ids), "invoices_checked": len(invoices), "mismatches": mismatches,
            "truncated": len(ids) > len(captain_ids),
        }


def bm_plan_covered(booking: dict) -> bool:
    from app.services import booking_money as bm

    return bm.plan_covered_base_paise(booking) > 0
