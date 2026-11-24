"""The one place a captain payment is written (§7.14): the ledger row, the
Finance debit, the cash-offset wallet credit, the invoice, its settlement rows
and any invoice adjustment all land in ONE transaction, or none do.

Callers (`PayMixin.record_payment` for advance / other / petrol, and the Pay
wizard's `pay_invoice` for a salary month or a commission period) hand over a
`prepare(session)` coroutine that (re)computes what the invoice contains from
the database as the transaction sees it — so a retry after a write conflict, or
a second concurrent invoice for the same bookings, always works from fresh
numbers (a booking can never be settled twice).
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone

from bson import ObjectId
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

from app.core.exceptions import BadRequestException, ConflictException
from app.services.customer_wallet_service import in_transaction
from app.services.payroll_base import iso, rupees_text, today_ist
from app.services.payroll_commission import CommissionMixin
from app.utils.timezone import now_ist

logger = logging.getLogger(__name__)

# payment kind → (ledger kind, finance category, label)
PAYMENT_KINDS = {
    "salary": ("payout", "Salary", "Salary"),
    "commission": ("payout", "Commission", "Commission"),
    "petrol": ("payout", "Petrol & Expenses", "Petrol & expenses"),
    "advance": ("advance", "Advance", "Advance"),
    "other": ("payout", "Other", "Payment"),
}
METHOD_LABEL = {"cash": "Cash", "upi": "UPI", "bank": "Bank transfer", "other": "Other"}
MAX_BACKDATE_DAYS = 400


def check_day(day: str, name: str) -> str:
    try:
        return date.fromisoformat(day).isoformat()
    except ValueError:
        raise BadRequestException(f"{name} must be a real date like 2026-11-23.") from None


def check_paid_on(paid_on: str | None) -> str:
    day = check_day(paid_on or today_ist(), "The payment date")
    if day > today_ist():
        raise BadRequestException("A payment can't be dated in the future.")
    if day < (date.fromisoformat(today_ist()) - timedelta(days=MAX_BACKDATE_DAYS)).isoformat():
        raise BadRequestException("A payment can't be dated more than 400 days back.")
    return day


class InvoiceCore(CommissionMixin):
    # ---------- numbering ----------

    async def next_invoice_number(self, session) -> tuple[str, int]:
        """BLS-PAY-{IST year}-{seq:06d}. The counter moves INSIDE the payment
        transaction, so a payment that rolls back gives its number back (no
        gaps) and two concurrent payments queue on the counter."""
        year = now_ist().year
        counter = await self.db.counters.find_one_and_update(
            {"_id": f"payroll_invoice:{year}"}, {"$inc": {"seq": 1}}, upsert=True, return_document=ReturnDocument.AFTER, session=session,
        )
        return f"BLS-PAY-{year}-{int(counter['seq']):06d}", int(counter["seq"])

    # ---------- shapes ----------

    @staticmethod
    def invoice_view(doc: dict, *, with_lines: bool = True, for_captain: bool = False) -> dict:
        out = {
            "id": str(doc["_id"]), "number": doc.get("number"), "captain_id": doc.get("captain_id"), "captain_name": doc.get("captain_name"),
            "captain_employee_id": doc.get("captain_employee_id"), "service_center_id": doc.get("service_center_id"),
            "service_center_name": doc.get("service_center_name"), "kind": doc.get("kind"), "month": doc.get("month"),
            "period_from": doc.get("period_from"), "period_to": doc.get("period_to"), "gross_paise": int(doc.get("gross_paise") or 0),
            "previously_paid_paise": int(doc.get("previously_paid_paise") or 0), "due_paise": int(doc.get("due_paise") or 0),
            "paid_paise": int(doc.get("paid_paise") or 0), "adjustment_paise": int(doc.get("adjustment_paise") or 0),
            "adjustment_reason": doc.get("adjustment_reason"), "cash_offset_paise": int(doc.get("cash_offset_paise") or 0),
            "net_paise": int(doc.get("net_paise") or 0), "method": doc.get("method"), "paid_on": doc.get("paid_on"),
            "paid_at": iso(doc.get("paid_at")), "note": doc.get("note"), "status": doc.get("status"), "ledger_id": doc.get("ledger_id"),
            "line_count": int(doc.get("line_count") or len(doc.get("lines") or [])), "actor_name": doc.get("actor_name"),
            "actor_role": doc.get("actor_role"), "finance_impact": bool(doc.get("finance_impact")), "void": doc.get("void"),
            "created_at": iso(doc.get("created_at")),
        }
        if isinstance(out["void"], dict):
            out["void"] = {**out["void"], "at": iso(out["void"].get("at"))}
        if with_lines:
            out["lines"] = list(doc.get("lines") or [])
        if not for_captain:
            out["finance_entry_id"] = doc.get("finance_entry_id")
        return out

    # ---------- the payment ----------

    async def create_payment_invoice(
        self, captain: dict, *, kind: str, method: str, paid_on: str, note: str | None, idempotency_key: str,
        cash_offset_paise: int, amount_paise: int | None, reason: str | None, who: dict, prepare,
    ) -> tuple[dict, dict, bool]:
        """(invoice doc, ledger row, duplicate). `prepare(session)` returns the
        plan: {lines, gross_paise, due_paise, previously_paid_paise?, month?,
        period_from?, period_to?, settle: [{booking_id, total_paise, amount_paise}],
        adjust: "none" | "signed" | "bonus_only"}."""
        captain_id = str(captain["_id"])
        key = f"pay:{captain_id}:{idempotency_key}"
        existing = await self.by_key(key)
        if existing:
            return await self._existing(existing, key)
        center_id = captain.get("service_center_id")
        center = await self.center_of(center_id)
        finance_impact = await self.finance_impact_for(center_id)
        ledger_kind, category, label = PAYMENT_KINDS[kind]
        oid, invoice_oid = ObjectId(), ObjectId()

        async def _do(s):
            plan = await prepare(s)
            paid = int(amount_paise) if amount_paise is not None else int(plan["due_paise"])
            if paid <= 0:
                raise BadRequestException("Enter an amount more than ₹0.")
            diff = paid - int(plan["due_paise"])
            if diff != 0 and len((reason or "").strip()) < 3:
                raise BadRequestException(
                    f"This invoice comes to {rupees_text(plan['due_paise'])} — say why you are paying {rupees_text(paid)} instead."
                )
            if cash_offset_paise > paid:
                raise BadRequestException("The cash offset can't be more than the payment.")
            net = paid - cash_offset_paise
            if cash_offset_paise:
                owed = await self._owed_cash_paise(captain_id, s)
                if cash_offset_paise > owed:
                    raise BadRequestException(
                        f"{captain.get('full_name') or 'This captain'} holds {rupees_text(owed)} of cash — the offset can't be more than that."
                    )
            number, seq = await self.next_invoice_number(s)
            entry_id = None
            if finance_impact and net > 0:
                from app.services.finance_service import FinanceService

                entry = await FinanceService(self.db).create_system_entry(
                    title=f"{label} — {captain.get('full_name')} ({number})"[:120], type="debit", amount_paise=net, category=category,
                    source="payroll", source_id=str(oid), service_center_id=center_id, done_by=who["name"], actor_id=who["id"] or "system",
                    actor_role=who["role"], entry_date=paid_on, description=(note or "")[:400], session=s,
                )
                entry_id = entry["id"]
            if cash_offset_paise:
                # The cash he holds is handed in against this pay: his wallet goes up and his
                # bookings show it as handed over (a receipt keeps the trail, §7.15).
                await self.return_cash_in(
                    s, captain_id, cash_offset_paise, booking_id=None, kind="pay_offset", note=f"Cash handed over, set against pay ({label}, {number})",
                    who=who, center_id=center_id, key=f"off:{key}", invoice_id=str(invoice_oid), invoice_number=number, strict=False,
                )
            row = await self.post({
                "_id": oid, "captain_id": captain_id, "service_center_id": center_id, "kind": ledger_kind, "payment_kind": kind,
                "amount_paise": -paid, "method": method, "note": note, "key": key, "finance_impact": finance_impact,
                "finance_entry_id": entry_id, "cash_offset_paise": cash_offset_paise, "net_paise": net, "entry_date": paid_on,
                "invoice_id": str(invoice_oid), "invoice_number": number,
                "actor_id": who["id"], "actor_name": who["name"], "actor_role": who["role"],
            }, session=s)
            adjust = plan.get("adjust") or "none"
            if diff != 0 and (adjust == "signed" or (adjust == "bonus_only" and diff > 0)):
                await self.post({
                    "captain_id": captain_id, "service_center_id": center_id, "kind": "bonus" if diff > 0 else "deduction",
                    "amount_paise": diff, "source": "invoice_adjustment", "key": f"invadj:{invoice_oid}", "entry_date": paid_on,
                    "invoice_id": str(invoice_oid), "invoice_number": number,
                    "note": f"Invoice {number}: {'paid more' if diff > 0 else 'paid less'} than the invoice total — {reason}"[:300],
                    "actor_id": who["id"], "actor_name": who["name"], "actor_role": who["role"],
                }, session=s)
            for line in plan.get("settle") or []:
                await self._settle_in(s, invoice_oid, number, captain_id, line)
            now = datetime.now(timezone.utc)
            invoice = {
                "_id": invoice_oid, "number": number, "seq": seq, "year": now_ist().year, "captain_id": captain_id,
                "captain_name": captain.get("full_name"), "captain_employee_id": captain.get("employee_id"),
                "service_center_id": center_id, "service_center_name": (center or {}).get("name"), "kind": kind,
                "month": plan.get("month"), "period_from": plan.get("period_from"), "period_to": plan.get("period_to"),
                "lines": plan["lines"], "line_count": len(plan["lines"]), "gross_paise": int(plan["gross_paise"]),
                "previously_paid_paise": int(plan.get("previously_paid_paise") or 0), "due_paise": int(plan["due_paise"]),
                "paid_paise": paid, "adjustment_paise": diff, "adjustment_reason": (reason or "").strip() or None,
                "cash_offset_paise": cash_offset_paise, "net_paise": net, "method": method, "paid_on": paid_on, "paid_at": now,
                "note": note, "status": "paid", "ledger_id": str(oid), "key": key, "idempotency_key": idempotency_key,
                "finance_entry_id": entry_id, "finance_impact": finance_impact, "actor_id": who["id"], "actor_name": who["name"],
                "actor_role": who["role"], "void": None, "is_deleted": False, "created_at": now, "updated_at": now,
            }
            await self.db.payroll_invoices.insert_one(invoice, session=s)
            return invoice, row

        try:
            invoice, row = await in_transaction(self.db, _do, None)
        except DuplicateKeyError:
            existing = await self.by_key(key)
            if not existing:
                raise
            return await self._existing(existing, key)
        await self._after_payment(captain, invoice, row, who, label)
        return invoice, row, False

    async def _existing(self, row: dict, key: str) -> tuple[dict, dict, bool]:
        invoice = await self.db.payroll_invoices.find_one({"ledger_id": str(row["_id"])})
        return invoice or {}, row, True

    async def _after_payment(self, captain: dict, invoice: dict, row: dict, who: dict, label: str) -> None:
        captain_id = str(captain["_id"])
        center_id = captain.get("service_center_id")
        cfg = await self.settings()
        paid, offset = int(invoice["paid_paise"]), int(invoice["cash_offset_paise"])
        await self.audit(who, "PAYROLL_PAYMENT", str(row["_id"]), {
            "captain_id": captain_id, "kind": invoice["kind"], "method": invoice["method"], "amount_paise": paid, "cash_offset_paise": offset,
            "finance_impact": invoice["finance_impact"], "paid_on": invoice["paid_on"], "note": invoice.get("note"),
            "invoice_id": str(invoice["_id"]), "invoice_number": invoice["number"], "month": invoice.get("month"),
            "period_from": invoice.get("period_from"), "period_to": invoice.get("period_to"), "adjustment_paise": invoice["adjustment_paise"],
            "adjustment_reason": invoice.get("adjustment_reason"), "lines": invoice.get("line_count"),
        }, center_id)
        how = METHOD_LABEL.get(invoice["method"], invoice["method"])
        what = label
        if invoice["kind"] == "salary" and invoice.get("month"):
            what = f"{label} for {invoice['month']}"
        elif invoice["kind"] == "commission" and invoice.get("period_from"):
            what = f"{label} for {self.day_label(invoice['period_from'])} – {self.day_label(invoice['period_to'])}"
        await self.notify(
            captain_id, f"{rupees_text(paid)} paid to you",
            f"{what}: {rupees_text(paid)} by {how} on {self.day_label(invoice['paid_on'])}. Invoice {invoice['number']}."
            + (f" {rupees_text(offset)} of it was set against cash you held." if offset else ""),
            str(invoice["_id"]),
        )
        if paid > cfg["payroll_alert_paise"]:
            await self.notify_admins(
                "Large captain payment",
                f"{who['name']} paid {captain.get('full_name')} {rupees_text(paid)} ({label}, {how}). Invoice {invoice['number']}.",
                str(invoice["_id"]),
            )

    # ---------- settlement of commission bookings ----------

    async def _settle_in(self, s, invoice_oid: ObjectId, number: str, captain_id: str, line: dict) -> None:
        """Move one booking's settled amount for this captain from (total − line)
        to `total`, guarded on the old value: a concurrent invoice that already
        settled it makes this update match nothing → refused."""
        booking_id, total, amount = line["booking_id"], int(line["total_paise"]), int(line["amount_paise"])
        old = total - amount
        field = f"commission_settled_by.{captain_id}"
        guard: dict = {"_id": ObjectId(booking_id)}
        guard.update({"$or": [{field: old}, {field: {"$exists": False}}, {field: None}]} if old == 0 else {field: old})
        done = await self.bookings.update_one(guard, {"$set": {field: total}}, session=s)
        if not done.matched_count:
            raise ConflictException("A booking on this invoice was just paid or changed by someone else — open the invoice again.")
        await self.bookings.update_one({"_id": ObjectId(booking_id), "commission_captain_id": captain_id}, {"$set": {"commission_settled_paise": total}}, session=s)
        await self.db.payroll_settlements.insert_one({
            "invoice_id": str(invoice_oid), "invoice_number": number, "captain_id": captain_id, "booking_id": booking_id,
            "booking_number": line.get("booking_number"), "amount_paise": amount, "kind": "settle", "key": f"st:{invoice_oid}:{booking_id}",
            "created_at": datetime.now(timezone.utc),
        }, session=s)

    async def _unsettle_in(self, s, invoice: dict, reason: str, who: dict) -> int:
        """Void side of `_settle_in`: take each line's own amount back off the
        booking's settled value (additive, so any void order works)."""
        n = 0
        captain_id = invoice["captain_id"]
        for line in invoice.get("lines") or []:
            if not line.get("settle") or not line.get("booking_id"):
                continue
            amount = int(line.get("amount_paise") or 0)
            if amount == 0:
                continue
            bid = ObjectId(line["booking_id"])
            await self.bookings.update_one({"_id": bid}, {"$inc": {f"commission_settled_by.{captain_id}": -amount}}, session=s)
            await self.bookings.update_one({"_id": bid, "commission_captain_id": captain_id}, {"$inc": {"commission_settled_paise": -amount}}, session=s)
            await self.db.payroll_settlements.insert_one({
                "invoice_id": str(invoice["_id"]), "invoice_number": invoice["number"], "captain_id": captain_id, "booking_id": line["booking_id"],
                "booking_number": line.get("booking_number"), "amount_paise": -amount, "kind": "unsettle",
                "key": f"unst:{invoice['_id']}:{line['booking_id']}", "note": reason[:200], "created_at": datetime.now(timezone.utc),
            }, session=s)
            n += 1
        return n

    async def void_invoice_in(self, s, orig_ledger: dict, reason: str, who: dict) -> None:
        """Inside the void transaction of a payment that has an invoice."""
        invoice = await self.db.payroll_invoices.find_one({"_id": ObjectId(orig_ledger["invoice_id"])}, session=s)
        if not invoice:
            return
        done = await self.db.payroll_invoices.update_one(
            {"_id": invoice["_id"], "status": "paid"},
            {"$set": {"status": "void", "void": {"at": datetime.now(timezone.utc), "by": who["name"], "by_id": who["id"], "reason": reason[:200]},
                      "updated_at": datetime.now(timezone.utc)}},
            session=s,
        )
        if not done.matched_count:
            raise ConflictException("This invoice was just voided.")
        await self._unsettle_in(s, invoice, reason, who)
        adj = await self.ledger.find_one({"key": f"invadj:{invoice['_id']}"}, session=s)
        if adj:
            await self.post({
                "captain_id": invoice["captain_id"], "service_center_id": invoice.get("service_center_id"),
                "kind": "bonus" if int(adj["amount_paise"]) < 0 else "deduction", "amount_paise": -int(adj["amount_paise"]),
                "source": "invoice_adjustment", "key": f"invadj-void:{invoice['_id']}", "invoice_id": str(invoice["_id"]),
                "invoice_number": invoice["number"], "note": f"Invoice {invoice['number']} voided — adjustment reversed"[:300],
                "actor_id": who["id"], "actor_name": who["name"], "actor_role": who["role"],
            }, session=s)
