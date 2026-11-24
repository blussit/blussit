"""PayrollService — captain pay (ARCHITECTURE.md §7.13). Composed from:

  payroll_base        settings, captain lookup + center scoping, the ledger writer
  payroll_commission  the commission engine + manager-done job commission (D4–D8, D17)
  payroll_pay         terms, payments + Finance, adjustments, deposit, kit (D1–D3, D12–D15)
  payroll_months      attendance override, month inputs, finalize (D9–D11)
  payroll_invoice_core / payroll_invoices
                      the invoice on every payment, the Pay wizard, settlements, reconcile (§7.14)
  this module         the reads: list / detail / statement / ledger / commissions /
                      summary / CSV / the captain's own view

Money is integer paise, days IST `YYYY-MM-DD`; dues are the sum of the append-only ledger."""
from __future__ import annotations

from bson import ObjectId

from app.core.authz import manager_center_or_raise
from app.core.exceptions import NotFoundException
from app.repositories.payroll_repository import DEPOSIT_KINDS, EARN_KINDS, PAID_KINDS
from app.services import payroll_terms as pt
from app.services.payroll_base import iso, ist_day, today_ist
from app.services.payroll_cash import CashMixin
from app.services.payroll_months import _utc_bounds, check_month
from app.services.payroll_salary import month_bounds
from app.utils.csv_safe_finance import build_csv

MAX_CAPTAINS = 500
LEDGER_PAGE_MAX = 100


class PayrollService(CashMixin):
    # ---------- shapes ----------

    async def entry_view(self, row: dict, voided: bool | None = None) -> dict:
        if voided is None and row.get("kind") in ("payout", "advance"):
            voided = bool(await self.ledger.count_documents({"key": f"void:{row['_id']}"}, limit=1))
        return self._entry(row, bool(voided))

    @staticmethod
    def _entry(row: dict, voided: bool = False) -> dict:
        return {
            "id": str(row["_id"]), "captain_id": row.get("captain_id"), "kind": row.get("kind"), "payment_kind": row.get("payment_kind"),
            "adjustment_kind": row.get("adjustment_kind"), "amount_paise": int(row.get("amount_paise") or 0),
            "booking_id": row.get("booking_id"), "booking_number": row.get("booking_number"), "period": row.get("period"),
            "method": row.get("method"), "note": row.get("note"), "finance_impact": bool(row.get("finance_impact")),
            "finance_entry_id": row.get("finance_entry_id"), "cash_offset_paise": int(row.get("cash_offset_paise") or 0),
            "voided": voided, "voids": row.get("voids"), "actor_name": row.get("actor_name"), "actor_role": row.get("actor_role"),
            "entry_date": row.get("entry_date"), "created_at": iso(row.get("created_at")),
            "invoice_id": row.get("invoice_id"), "invoice_number": row.get("invoice_number"),
        }

    async def _entries(self, rows: list[dict]) -> list[dict]:
        ids = [str(r["_id"]) for r in rows if r.get("kind") in ("payout", "advance")]
        voided = set()
        if ids:
            voided = {r["voids"] for r in await self.ledger.find({"voids": {"$in": ids}}, {"voids": 1}).to_list(length=len(ids))}
        return [self._entry(r, str(r["_id"]) in voided) for r in rows]

    @staticmethod
    def pay_summary(user: dict, day: str | None = None) -> dict:
        pay = pt.pay_view(user, day) if user.get("pay") else pt.blank_pay("legacy")
        kit = {**pt.empty_kit(), **(pay.get("kit") or {})}
        return {
            "basis": pay.get("basis") or "legacy", "salary_monthly_paise": int(pay.get("salary_monthly_paise") or 0),
            "weekly_off_days": pt.off_days_of(pay), "paid_leaves_per_month": pay.get("paid_leaves_per_month", pt.DEFAULT_PAID_LEAVES),
            "petrol_per_day_paise": int(pay.get("petrol_per_day_paise") or 0), "extra_day_on_off": bool(pay.get("extra_day_on_off")),
            "commission": {**pt.DEFAULT_COMMISSION, **(pay.get("commission") or {})},
            "deposit_expected_paise": int(pay.get("deposit_expected_paise") or 0), "kit": kit,
            "start_date": pt.start_day(user), "end_date": pay.get("end_date"),
        }

    async def _figures(self, captain_ids: list[str], month: str) -> tuple[dict, dict, dict]:
        """(all-time per captain, month per captain, pending per-booking commissions per captain)."""
        if not captain_ids:
            return {}, {}, {}
        first, last = month_bounds(month)
        all_rows = await self.ledger.aggregate([
            {"$match": {"captain_id": {"$in": captain_ids}}},
            {"$group": {"_id": {"c": "$captain_id", "k": "$kind"}, "p": {"$sum": "$amount_paise"}}},
        ]).to_list(length=None)
        month_rows = await self.ledger.aggregate([
            {"$match": {"captain_id": {"$in": captain_ids}, "entry_date": {"$gte": first, "$lte": last}}},
            {"$group": {"_id": {"c": "$captain_id", "k": "$kind"}, "p": {"$sum": "$amount_paise"}}},
        ]).to_list(length=None)
        pending_rows = await self.bookings.aggregate([
            {"$match": {"commission_captain_id": {"$in": captain_ids}, "commission_state": "pending", "is_deleted": {"$ne": True}, "status": "completed"}},
            {"$group": {"_id": "$commission_captain_id", "n": {"$sum": 1}}},
        ]).to_list(length=None)

        def fold(rows: list[dict]) -> dict:
            out: dict[str, dict[str, int]] = {}
            for r in rows:
                out.setdefault(r["_id"]["c"], {})[r["_id"]["k"]] = int(r["p"] or 0)
            return out

        return fold(all_rows), fold(month_rows), {r["_id"]: int(r["n"]) for r in pending_rows}

    @staticmethod
    def _totals(kinds: dict[str, int]) -> dict:
        earned = sum(kinds.get(k, 0) for k in EARN_KINDS)
        paid = -sum(kinds.get(k, 0) for k in PAID_KINDS)
        deposit = sum(kinds.get(k, 0) for k in DEPOSIT_KINDS)
        return {"earned_paise": earned, "paid_paise": paid, "dues_paise": earned - paid, "deposit_held_paise": deposit}

    # ---------- the Salary page ----------

    async def list_captains(self, actor, center_id: str | None, status: str | None, basis: str | None, month: str | None) -> dict:
        month = check_month(month)
        scope = manager_center_or_raise(actor.role, actor.service_center_id)
        center = scope or (center_id or None)
        query: dict = {"role": "captain", "is_deleted": {"$ne": True}}
        if center:
            query["service_center_id"] = center
        if status and status != "all":
            query["status"] = status
        captains = await self.users.find(query).sort([("full_name", 1)]).limit(MAX_CAPTAINS).to_list(length=MAX_CAPTAINS)
        if basis:
            captains = [c for c in captains if pt.basis_of(c) == basis]
        ids = [str(c["_id"]) for c in captains]
        everything, in_month, pending = await self._figures(ids, month)
        names = await self._center_names({c.get("service_center_id") for c in captains if c.get("service_center_id")})
        impact_cache: dict = {}
        rows = []
        for c in captains:
            cid = str(c["_id"])
            total, mon = self._totals(everything.get(cid, {})), self._totals(in_month.get(cid, {}))
            center_of = c.get("service_center_id")
            if center_of not in impact_cache:
                impact_cache[center_of] = await self.finance_impact_for(center_of)
            summary = self.pay_summary(c)
            rows.append({
                "id": cid, "full_name": c.get("full_name"), "phone": c.get("phone"), "employee_id": c.get("employee_id"),
                "status": c.get("status"), "service_center_id": center_of, "service_center_name": names.get(center_of),
                "pay": summary, "basis": summary["basis"], "legacy": summary["basis"] == "legacy",
                "earned_paise": mon["earned_paise"], "paid_paise": mon["paid_paise"], "dues_paise": total["dues_paise"],
                "earned_total_paise": total["earned_paise"], "paid_total_paise": total["paid_paise"],
                "deposit_held_paise": total["deposit_held_paise"],
                "kit_provided": bool(summary["kit"].get("provided")), "kit_returned": bool(summary["kit"].get("returned_on")),
                "pending_commissions": pending.get(cid, 0), "finance_impact": impact_cache[center_of],
            })
        result = {"month": month, "rows": rows, "summary": self._summarise(rows, month)}
        if center:
            result["finance_impact"] = await self.finance_impact_for(center)
            result["center"] = await self._kit_counters(center, rows)
        return result

    @staticmethod
    def _summarise(rows: list[dict], month: str) -> dict:
        return {
            "month": month, "active_captains": sum(1 for r in rows if r["status"] == "active"),
            "legacy_captains": sum(1 for r in rows if r["legacy"]),
            "earned_paise": sum(r["earned_paise"] for r in rows), "paid_paise": sum(r["paid_paise"] for r in rows),
            "dues_paise": sum(r["dues_paise"] for r in rows), "deposit_held_paise": sum(r["deposit_held_paise"] for r in rows),
            "pending_commissions": sum(r["pending_commissions"] for r in rows),
            "kits_out": sum(1 for r in rows if r["kit_provided"] and not r["kit_returned"]),
        }

    async def _kit_counters(self, center_id: str, rows: list[dict]) -> dict:
        op = self.operation_of(await self.center_of(center_id))
        out = sum(1 for r in rows if r["kit_provided"] and not r["kit_returned"])
        counter = int(op.get("kits_purchased") or 0) if op.get("type") == "franchise" else int(op.get("kits_given") or 0)
        warning = None
        if counter and out > counter:
            warning = f"{out} kits are assigned to captains but this center only has {counter} on record."
        elif counter and out < counter:
            warning = f"{counter - out} of this center's {counter} kits aren't assigned to a captain."
        return {"kits_assigned": out, "kits_on_record": counter, "kit_warning": warning}

    async def _center_names(self, ids: set) -> dict[str, str]:
        oids = [ObjectId(i) for i in ids if i and ObjectId.is_valid(str(i))]
        if not oids:
            return {}
        rows = await self.db.service_centers.find({"_id": {"$in": oids}}, {"name": 1}).to_list(length=len(oids))
        return {str(r["_id"]): r.get("name") for r in rows}

    async def summary(self, actor, center_id: str | None, month: str | None) -> dict:
        data = await self.list_captains(actor, center_id, None, None, month)
        return {**data["summary"], **({"center": data["center"]} if data.get("center") else {}), "finance_impact": data.get("finance_impact", True)}

    async def captain_detail_payload(self, captain: dict, day: str | None = None) -> dict:
        cid = str(captain["_id"])
        month = today_ist()[:7]
        everything, in_month, pending = await self._figures([cid], month)
        total, mon = self._totals(everything.get(cid, {})), self._totals(in_month.get(cid, {}))
        names = await self._center_names({captain.get("service_center_id")})
        cfg = await self.settings()
        summary = self.pay_summary(captain, day)
        pay = captain.get("pay") or {}
        history = [
            {
                "effective_from": h.get("effective_from"), "basis": (h.get("terms") or {}).get("basis"), "terms": h.get("terms"),
                "before": h.get("before"), "after": h.get("after"), "reason": h.get("reason"), "actor_name": h.get("actor_name"),
                "at": iso(h.get("at")),
            }
            for h in pay.get("pay_history") or []
        ]
        return {
            "id": cid, "full_name": captain.get("full_name"), "phone": captain.get("phone"), "employee_id": captain.get("employee_id"),
            "status": captain.get("status"), "service_center_id": captain.get("service_center_id"),
            "service_center_name": names.get(captain.get("service_center_id")), "pay": summary, "basis": summary["basis"],
            "legacy": summary["basis"] == "legacy", "earned_paise": mon["earned_paise"], "paid_paise": mon["paid_paise"],
            "dues_paise": total["dues_paise"], "earned_total_paise": total["earned_paise"], "paid_total_paise": total["paid_paise"],
            "deposit_held_paise": total["deposit_held_paise"], "kit_provided": bool(summary["kit"].get("provided")),
            "kit_returned": bool(summary["kit"].get("returned_on")), "pending_commissions": pending.get(cid, 0),
            "finance_impact": await self.finance_impact_for(captain.get("service_center_id")), "pay_history": history,
            "cash_held_paise": await self._owed_cash_paise(cid), "max_commission_percent": cfg["max_commission_percent"],
            "alert_payment_paise": cfg["payroll_alert_paise"],
        }

    async def captain_detail(self, captain_id: str, actor) -> dict:
        captain = await self.captain_or_404(captain_id, actor.role, actor.service_center_id)
        return await self.captain_detail_payload(captain)

    # ---------- statement, ledger, commissions ----------

    async def statement(self, captain: dict, month: str | None) -> dict:
        month = check_month(month)
        cid = str(captain["_id"])
        first, last = month_bounds(month)
        everything, in_month, _ = await self._figures([cid], month)
        total, mon = self._totals(everything.get(cid, {})), self._totals(in_month.get(cid, {}))
        kinds = in_month.get(cid, {})
        rows = await self.ledger.find({"captain_id": cid, "entry_date": {"$gte": first, "$lte": last}}).sort([("created_at", 1), ("_id", 1)]).to_list(length=500)
        salary = await self.salary_month(captain, month)
        days = salary.pop("days")
        return {
            "month": month, "earned_paise": mon["earned_paise"], "paid_paise": mon["paid_paise"], "dues_paise": total["dues_paise"],
            "commission_paise": kinds.get("commission", 0), "salary_paise": kinds.get("salary_accrual", 0),
            "allowance_paise": kinds.get("allowance", 0), "adjustments_paise": kinds.get("commission_adjust", 0) + kinds.get("bonus", 0) + kinds.get("deduction", 0),
            "salary": salary, "attendance": days, "entries": await self._entries(rows), "basis": pt.basis_of(captain),
        }

    async def captain_statement(self, captain_id: str, month: str | None, actor) -> dict:
        captain = await self.captain_or_404(captain_id, actor.role, actor.service_center_id)
        return await self.statement(captain, month)

    async def ledger_page(self, captain_id: str, actor, page: int, page_size: int, kind: str | None = None) -> tuple[list[dict], int]:
        await self.captain_or_404(captain_id, actor.role, actor.service_center_id)
        page_size = min(max(page_size, 1), LEDGER_PAGE_MAX)
        flt: dict = {"captain_id": captain_id}
        if kind:
            flt["kind"] = kind
        total = await self.ledger.count_documents(flt)
        rows = await self.ledger.find(flt).sort([("created_at", -1), ("_id", -1)]).skip((max(page, 1) - 1) * page_size).limit(page_size).to_list(length=page_size)
        return await self._entries(rows), total

    async def commissions(self, captain: dict, month: str | None) -> list[dict]:
        month = check_month(month)
        cid = str(captain["_id"])
        first, last = month_bounds(month)
        lo, hi = _utc_bounds(first, last)
        bookings = await self.bookings.find(
            {"commission_captain_id": cid, "completed_at": {"$gte": lo, "$lt": hi}, "is_deleted": {"$ne": True}, "commission_state": {"$in": ["accrued", "pending"]}},
        ).sort([("completed_at", -1)]).limit(500).to_list(length=500)
        cfg = await self.settings()
        customers = {}
        cust_ids = {ObjectId(b["customer_id"]) for b in bookings if b.get("customer_id") and ObjectId.is_valid(str(b["customer_id"]))}
        if cust_ids:
            for u in await self.users.find({"_id": {"$in": list(cust_ids)}}, {"full_name": 1}).to_list(length=len(cust_ids)):
                customers[str(u["_id"])] = u.get("full_name")
        service_ids = {ObjectId(s) for b in bookings for s in (b.get("service_ids") or []) if ObjectId.is_valid(str(s))}
        services = {}
        if service_ids:
            for s in await self.db.services.find({"_id": {"$in": list(service_ids)}}, {"name": 1}).to_list(length=len(service_ids)):
                services[str(s["_id"])] = s.get("name")
        out = []
        for b in bookings:
            row = await self.booking_commission_row(b, cfg)
            row["customer_name"] = customers.get(b.get("customer_id"))
            row["service_name"] = ", ".join(services[str(s)] for s in (b.get("service_ids") or [])[:3] if str(s) in services) or None
            row["completed_day"] = ist_day(b.get("completed_at"))
            out.append(row)
        await self._attach_payment_state(cid, out)
        return out

    async def _attach_payment_state(self, captain_id: str, rows: list[dict]) -> None:
        """Each commission row's paid / unpaid state and invoice numbers (§7.14)."""
        ids = [r["booking_id"] for r in rows]
        settled: dict[str, int] = {}
        numbers: dict[str, list[str]] = {}
        invoice_ids: dict[str, list[str]] = {}
        if ids:
            for r in await self.db.payroll_settlements.aggregate([
                {"$match": {"captain_id": captain_id, "booking_id": {"$in": ids}}}, {"$group": {"_id": "$booking_id", "p": {"$sum": "$amount_paise"}}},
            ]).to_list(length=None):
                settled[r["_id"]] = int(r["p"] or 0)
            for inv in await self.db.payroll_invoices.find(
                {"captain_id": captain_id, "status": "paid", "lines.booking_id": {"$in": ids}}, {"number": 1, "lines.booking_id": 1, "paid_on": 1},
            ).sort([("created_at", 1)]).to_list(length=2000):
                for line in inv.get("lines") or []:
                    if line.get("booking_id") in settled:
                        numbers.setdefault(line["booking_id"], [])
                        invoice_ids.setdefault(line["booking_id"], [])
                        if inv["number"] not in numbers[line["booking_id"]]:
                            numbers[line["booking_id"]].append(inv["number"])
                            invoice_ids[line["booking_id"]].append(str(inv["_id"]))
        marked = await self._marked_totals(captain_id, ids) if ids else {}
        for row in rows:
            got, amount = settled.get(row["booking_id"], 0), int(row["amount_paise"])
            row["settled_paise"] = got
            row["marked_paise"] = marked.get(row["booking_id"], 0)  # settled by hand, no invoice (§7.15)
            row["unsettled_paise"] = amount - got
            row["invoice_numbers"] = numbers.get(row["booking_id"], [])
            row["invoice_ids"] = invoice_ids.get(row["booking_id"], [])
            if row.get("not_set"):
                row["payment_state"] = "not_set"
            elif amount == 0 and got == 0:
                row["payment_state"] = "none"
            elif got == amount or (amount > 0 and got > amount):  # settled more than it is now worth: a later invoice recovers it
                row["payment_state"] = "paid"
            elif got != 0:
                row["payment_state"] = "part_paid"
            else:
                row["payment_state"] = "unpaid"

    async def captain_commissions(self, captain_id: str, month: str | None, actor) -> list[dict]:
        captain = await self.captain_or_404(captain_id, actor.role, actor.service_center_id)
        return await self.commissions(captain, month)

    # ---------- CSV ----------

    async def export_csv(self, actor, center_id: str | None, status: str | None, basis: str | None, month: str | None) -> str:
        data = await self.list_captains(actor, center_id, status, basis, month)
        header = [
            "Employee ID", "Name", "Phone", "Service Center", "Status", "Basis", "Terms", f"Earned ({data['month']})", f"Paid ({data['month']})",
            "Dues (All Time)", "Deposit Held", "Kit", "Pending Commissions",
        ]
        rows = []
        for r in data["rows"]:
            pay = r["pay"]
            if pay["basis"] == "salary":
                terms = f"Salary {pay['salary_monthly_paise'] / 100:.2f}/month"
            elif pay["basis"] == "commission":
                terms = f"Commission {pay['commission'].get('percent', 0):g}%" if pay["commission"].get("mode") == "percent" else "Commission per booking"
            else:
                terms = "Per-job wallet"
            kit = "Not provided" if not r["kit_provided"] else ("Returned" if r["kit_returned"] else "With captain")
            rows.append([
                r["employee_id"], r["full_name"], r["phone"], r["service_center_name"], r["status"], r["basis"], terms,
                f"{r['earned_paise'] / 100:.2f}", f"{r['paid_paise'] / 100:.2f}", f"{r['dues_paise'] / 100:.2f}",
                f"{r['deposit_held_paise'] / 100:.2f}", kit, r["pending_commissions"],
            ])
        who = await self.actor_info(actor)
        await self.audit(who, "PAYROLL_EXPORT", None, {"rows": len(rows), "month": data["month"], "center_id": center_id}, manager_center_or_raise(actor.role, actor.service_center_id) or center_id)
        return build_csv(header, rows)

    # ---------- the captain's own view ----------

    async def _own_captain(self, actor) -> dict:
        captain = await self.users.find_one({"_id": ObjectId(actor.id), "role": "captain", "is_deleted": {"$ne": True}}) if ObjectId.is_valid(actor.id or "") else None
        if not captain:
            raise NotFoundException("Captain not found")
        return captain

    async def me(self, actor, month: str | None) -> dict:
        captain = await self._own_captain(actor)
        month = check_month(month)
        statement = await self.statement(captain, month)
        figures = await self.repo.dues(str(captain["_id"]))
        summary = self.pay_summary(captain)
        return {
            "basis": summary["basis"], "month": month, "pay": summary,
            "earned_paise": statement["earned_paise"], "paid_paise": statement["paid_paise"], "dues_paise": figures["dues_paise"],
            "deposit_held_paise": figures["deposit_held_paise"], "statement": statement,
            "commissions": await self.commissions(captain, month) if summary["basis"] == "commission" else [],
            "entries": statement["entries"], "attendance": statement["attendance"], "salary": statement["salary"],
            "invoices": (await self.my_invoices(actor, 1, 20))[0],
        }

    async def me_statement(self, actor, month: str | None) -> dict:
        return await self.statement(await self._own_captain(actor), month)


async def backfill_captain_pay(db) -> dict:
    """Boot migration (idempotent, bounded): a captain with no `pay` block gets
    basis "legacy" — the per-job wallet pay they have today — plus a start date
    (the day the account was made). Anything already set is left alone."""
    filled = 0
    cursor = db.users.find({"role": "captain", "pay": {"$exists": False}}, {"created_at": 1}).limit(2000)
    for user in await cursor.to_list(length=2000):
        pay = pt.blank_pay("legacy")
        pay.update({"start_date": pt.start_day(user), "end_date": None, "effective_from": pt.start_day(user), "pay_history": []})
        done = await db.users.update_one({"_id": user["_id"], "pay": {"$exists": False}}, {"$set": {"pay": pay}})
        filled += done.modified_count
    return {"filled": filled}
