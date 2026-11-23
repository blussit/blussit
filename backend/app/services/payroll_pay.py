"""Pay terms, payments, adjustments, deposits, kits, attendance overrides and
month finalize (§7.13 D1–D3, D9–D15). Every money write is ONE transaction:
the ledger row, its Finance entry and a cash-offset wallet credit land together
or not at all; the client `idempotency_key` makes a double submit land once."""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone

from bson import ObjectId
from pymongo.errors import DuplicateKeyError

from app.core.exceptions import BadRequestException, ConflictException, NotFoundException
from app.services import payroll_terms as pt
from app.services.customer_wallet_service import in_transaction
from app.services.payroll_base import PayrollBase, rupees_text, today_ist
from app.services.payroll_commission import cap_of
from app.services.payroll_invoice_core import InvoiceCore, MAX_BACKDATE_DAYS, METHOD_LABEL as _METHOD_LABEL, PAYMENT_KINDS as _PAYMENT_KINDS, check_day as _check_day, check_paid_on

logger = logging.getLogger(__name__)

_ADJUSTMENT_KINDS = {
    "bonus": ("bonus", 1), "allowance": ("allowance", 1), "deduction": ("deduction", -1),
    "commission_extra": ("commission_adjust", 1), "commission_cut": ("commission_adjust", -1),
}
_TERMS_PAST_DAYS = 62
_TERMS_FUTURE_DAYS = 31


class PayMixin(InvoiceCore):
    # ================= terms =================

    async def build_new_captain_pay(self, center_id: str | None, pay_in, *, start_date: str | None = None) -> dict:
        """The `pay` block of a brand-new captain: what was sent, the gaps
        filled from the center's default (`operation.default_captain_pay`)
        and the global defaults. Raises when no basis can be decided."""
        cfg = await self.settings()
        center = await self.center_of(center_id)
        default = self.operation_of(center).get("default_captain_pay") or {}
        sent = pay_in.model_dump(exclude_none=True) if pay_in is not None else {}
        basis = sent.get("basis") or (default.get("basis") if default.get("basis") in ("salary", "commission") else None)
        if basis not in ("salary", "commission"):
            raise BadRequestException("Choose how this captain is paid — salary or commission.")
        pay = pt.blank_pay(basis)
        if basis == "salary" and "salary_monthly_paise" not in sent and default.get("salary_monthly_paise"):
            sent["salary_monthly_paise"] = int(default["salary_monthly_paise"])
        if basis == "commission" and "commission" not in sent and default.get("commission_percent") is not None:
            sent["commission"] = {"mode": "percent", "percent": float(default["commission_percent"])}
        pay = pt.merged_terms(pay, {**sent, "basis": basis})
        self._validate_pay(pay, cfg)
        pay["start_date"] = start_date or today_ist()
        pay["end_date"] = None
        pay["effective_from"] = pay["start_date"]
        pay["pay_history"] = []
        pay["updated_at"] = datetime.now(timezone.utc)
        return pay

    def _validate_pay(self, pay: dict, cfg: dict) -> None:
        if pay.get("basis") not in ("salary", "commission", "legacy"):
            raise BadRequestException("Pay basis must be salary or commission.")
        c = pay.get("commission") or {}
        if float(c.get("percent") or 0) > cfg["max_commission_percent"]:
            raise BadRequestException(f"Commission can't be more than {cfg['max_commission_percent']:g}% of a booking.")
        if pay["basis"] == "commission" and c.get("mode") not in ("percent", "per_booking"):
            raise BadRequestException("Commission is either a fixed percent or an amount set per booking.")
        kit = pay.get("kit") or {}
        if len(kit.get("items") or []) > 30:
            raise BadRequestException("A kit can list at most 30 items.")

    async def update_terms(self, captain_id: str, payload, actor) -> dict:
        captain = await self.captain_or_404(captain_id, actor.role, actor.service_center_id)
        if actor.id == captain_id:
            raise BadRequestException("You can't edit your own pay terms.")
        cfg = await self.settings()
        today = today_ist()
        sent = payload.model_dump(exclude_unset=True, exclude_none=True)
        effective = _check_day(sent.pop("effective_from", None) or today, "The start date")
        reason = sent.pop("reason", None)
        start = pt.start_day(captain)
        sent_start, sent_end = sent.pop("start_date", None), sent.pop("end_date", None)
        current = pt.pay_view(captain, today) if captain.get("pay") else pt.blank_pay("legacy")
        if effective < start and not sent_start:
            raise BadRequestException(f"These terms can't start before the captain's first day ({start}).")
        if effective < (date.fromisoformat(today) - timedelta(days=_TERMS_PAST_DAYS)).isoformat():
            raise BadRequestException("Terms can be backdated by two months at most.")
        if effective > (date.fromisoformat(today) + timedelta(days=_TERMS_FUTURE_DAYS)).isoformat():
            raise BadRequestException("Terms can start at most a month ahead.")
        kit_in = sent.get("kit")
        if kit_in:
            kit_in.pop("returned_on", None)  # only the Return Kit action sets it
        merged = pt.merged_terms(current, sent)
        if sent_start:
            merged["start_date"] = _check_day(sent_start, "The first day")
        if sent_end is not None:
            merged["end_date"] = _check_day(sent_end, "The last day")
        self._validate_pay(merged, cfg)
        if merged.get("basis") == "legacy" and current.get("basis") != "legacy":
            raise BadRequestException("A captain on salary or commission can't be moved back to the old per-job wallet pay.")

        diff = pt.terms_diff(current, merged)
        for key in ("start_date", "end_date"):
            if merged.get(key) != current.get(key):
                diff[key] = {"before": current.get(key), "after": merged.get(key)}
        if not diff:
            return await self.captain_detail_payload(captain, today)
        who = await self.actor_info(actor)
        dated_changed = any(k in diff for k in pt.DATED_FIELDS)
        history = [dict(h) for h in (captain.get("pay") or {}).get("pay_history") or []]
        if dated_changed:
            if not history:
                history = [{
                    "effective_from": pt.start_day(captain), "terms": pt.dated_terms(current), "reason": "Original terms",
                    "actor_name": "System", "at": datetime.now(timezone.utc),
                }]
            history = [h for h in history if h["effective_from"] < effective]
            history.append({
                "effective_from": effective, "terms": pt.dated_terms(merged), "before": {k: v["before"] for k, v in diff.items() if k in pt.DATED_FIELDS},
                "after": {k: v["after"] for k, v in diff.items() if k in pt.DATED_FIELDS}, "reason": reason,
                "actor_id": who["id"], "actor_name": who["name"], "at": datetime.now(timezone.utc),
            })
        merged["pay_history"] = history
        merged["effective_from"] = effective if dated_changed else (captain.get("pay") or {}).get("effective_from") or start
        merged["updated_by"] = who["id"]
        merged["updated_at"] = datetime.now(timezone.utc)
        # The stored top level mirrors the segment in force today.
        stored = {**merged}
        if history:
            stored.update(pt.terms_at({**captain, "pay": {**merged, "pay_history": history}}, today))
        done = await self.users.update_one({"_id": captain["_id"], "pay": captain.get("pay")}, {"$set": {"pay": stored}})
        if not done.matched_count:
            raise ConflictException("This captain's pay was just changed by someone else — reload and try again.")
        center = captain.get("service_center_id")
        await self.audit(who, "PAYROLL_TERMS_UPDATE", captain_id, {"changes": diff, "effective_from": effective, "reason": reason}, center)
        await self._notify_terms_changed(captain, merged, effective, who, diff)
        fresh = await self.users.find_one({"_id": captain["_id"]})
        return await self.captain_detail_payload(fresh, today)

    async def _notify_terms_changed(self, captain: dict, pay: dict, effective: str, who: dict, diff: dict) -> None:
        name = captain.get("full_name") or "Captain"
        summary = self.terms_sentence(pay)
        await self.notify(
            str(captain["_id"]), "Your pay terms were updated",
            f"Your pay is now: {summary} (from {self.day_label(effective)}). Ask your manager if anything looks wrong.",
            str(captain["_id"]),
        )
        if who["role"] == "admin":
            center = await self.center_of(captain.get("service_center_id"))
            manager_id = (center or {}).get("manager_id")
            if manager_id:
                await self.notify(
                    str(manager_id), f"{name}'s pay terms changed",
                    f"An admin changed {name}'s pay: {summary} (from {self.day_label(effective)}).", str(captain["_id"]), whatsapp=False,
                )

    @staticmethod
    def day_label(day: str) -> str:
        try:
            return date.fromisoformat(day).strftime("%d %b %Y").lstrip("0")
        except ValueError:
            return day

    @staticmethod
    def terms_sentence(pay: dict) -> str:
        basis = pay.get("basis")
        if basis == "salary":
            return f"salary {rupees_text(pay.get('salary_monthly_paise') or 0)} a month"
        if basis == "commission":
            c = pay.get("commission") or {}
            return f"commission {c.get('percent', 0):g}% of each booking" if c.get("mode") == "percent" else "commission set on each booking"
        return "per-job wallet pay"

    # ================= payments =================

    async def _owed_cash_paise(self, captain_id: str, session=None) -> int:
        wallet = await self.db.captain_wallets.find_one({"captain_id": captain_id}, {"balance": 1}, session=session)
        balance = float((wallet or {}).get("balance") or 0)
        return max(0, int(round(-balance * 100)))

    async def record_payment(self, captain_id: str, payload, actor) -> dict:
        """A plain payment — advance, petrol, other (and, for the old API, a free
        salary / commission amount). It always produces an invoice (§7.14) with one
        `payment` line; salary months and commission periods go through
        `pay_invoice`, which also tracks what each invoice settles."""
        captain = await self.captain_or_404(captain_id, actor.role, actor.service_center_id)
        paid_on = check_paid_on(payload.paid_on)
        who = await self.actor_info(actor)
        _, _, label = _PAYMENT_KINDS[payload.kind]
        amount = int(payload.amount_paise)

        async def prepare(_s):
            return {
                "lines": [{"type": "payment", "label": (payload.note or label)[:120], "amount_paise": amount}],
                "gross_paise": amount, "due_paise": amount, "settle": [], "adjust": "none",
            }

        invoice, row, duplicate = await self.create_payment_invoice(
            captain, kind=payload.kind, method=payload.method, paid_on=paid_on, note=payload.note, idempotency_key=payload.idempotency_key,
            cash_offset_paise=int(payload.cash_offset_paise), amount_paise=amount, reason=None, who=who, prepare=prepare,
        )
        dues = await self.repo.dues(captain_id)
        view = await self.entry_view(row)
        return {**view, "duplicate": duplicate, "dues_paise": dues["dues_paise"], "invoice_id": str(invoice.get("_id")) if invoice else None,
                "invoice_number": (invoice or {}).get("number")}

    async def void_payment(self, ledger_id: str, reason: str, actor) -> dict:
        if not ObjectId.is_valid(ledger_id or ""):
            raise NotFoundException("Payment not found")
        orig = await self.ledger.find_one({"_id": ObjectId(ledger_id)})
        if not orig or orig.get("kind") not in ("payout", "advance"):
            raise NotFoundException("Payment not found")
        captain = await self.captain_or_404(orig["captain_id"], actor.role, actor.service_center_id)
        key = f"void:{ledger_id}"
        existing = await self.by_key(key)
        if existing:
            return {**await self.entry_view(existing), "duplicate": True}
        who = await self.actor_info(actor)
        center_id = orig.get("service_center_id")

        async def _do(s):
            if orig.get("finance_impact") and orig.get("finance_entry_id"):
                from app.services.finance_service import FinanceService

                await FinanceService(self.db).void_system_entry("payroll", ledger_id, f"Payment voided: {reason}"[:200], who["id"] or "system", who["role"], session=s)
            if orig.get("cash_offset_paise"):
                from app.services.wallet_service import WalletService

                await WalletService(self.db).post_delta(
                    orig["captain_id"], -orig["cash_offset_paise"] / 100, None, "Cash offset reversed — payment voided", session=s,
                    key=f"cashoff-void:{ledger_id}",
                )
            if orig.get("invoice_id"):
                await self.void_invoice_in(s, orig, reason, who)
            return await self.post({
                "captain_id": orig["captain_id"], "service_center_id": center_id, "kind": "payout_void", "payment_kind": orig.get("payment_kind"),
                "amount_paise": -int(orig["amount_paise"]), "voids": ledger_id, "note": reason, "key": key, "finance_impact": orig.get("finance_impact", False),
                "invoice_id": orig.get("invoice_id"), "invoice_number": orig.get("invoice_number"),
                "actor_id": who["id"], "actor_name": who["name"], "actor_role": who["role"],
            }, session=s)

        try:
            row = await in_transaction(self.db, _do, None)
        except DuplicateKeyError:
            row = await self.by_key(key)
            return {**await self.entry_view(row), "duplicate": True}
        await self.audit(who, "PAYROLL_PAYMENT_VOID", ledger_id, {"captain_id": orig["captain_id"], "amount_paise": -int(orig["amount_paise"]), "reason": reason}, center_id)
        await self.notify(
            orig["captain_id"], "A payment was cancelled",
            f"The {rupees_text(-int(orig['amount_paise']))} payment dated {self.day_label(orig.get('entry_date') or '')}"
            + (f" (invoice {orig['invoice_number']})" if orig.get("invoice_number") else "") + f" was cancelled by {who['name']}: {reason}", ledger_id,
        )
        return {**await self.entry_view(row), "duplicate": False}

    # ================= adjustments =================

    async def record_adjustment(self, captain_id: str, payload, actor) -> dict:
        captain = await self.captain_or_404(captain_id, actor.role, actor.service_center_id)
        key = f"adj:{captain_id}:{payload.idempotency_key}"
        existing = await self.by_key(key)
        if existing:
            return {**await self.entry_view(existing), "duplicate": True}
        ledger_kind, sign = _ADJUSTMENT_KINDS[payload.kind]
        booking = None
        if payload.kind.startswith("commission_"):
            if not payload.booking_id or not ObjectId.is_valid(payload.booking_id):
                raise BadRequestException("Pick the booking this commission extra / cut is for.")
        if payload.booking_id:
            if not ObjectId.is_valid(payload.booking_id):
                raise NotFoundException("Booking not found")
            booking = await self.bookings.find_one({"_id": ObjectId(payload.booking_id), "is_deleted": {"$ne": True}})
            if not booking or booking.get("service_center_id") != captain.get("service_center_id"):
                raise NotFoundException("Booking not found")
        who = await self.actor_info(actor)
        amount = sign * payload.amount_paise
        cfg = await self.settings()

        async def _do(s):
            if payload.kind.startswith("commission_"):
                fresh = await self.bookings.find_one({"_id": booking["_id"]}, session=s)
                if fresh.get("commission_captain_id") != captain_id or fresh.get("status") != "completed":
                    raise BadRequestException("This booking has no commission for this captain — set the captain's commission first.")
                from app.services import booking_money as bm

                base = bm.commission_base_paise(fresh)
                cap = cap_of(base, cfg["max_commission_percent"])
                adj = await self.ledger.aggregate([
                    {"$match": {"booking_id": payload.booking_id, "kind": "commission_adjust", "captain_id": captain_id}},
                    {"$group": {"_id": None, "paise": {"$sum": "$amount_paise"}}},
                ], session=s).to_list(length=1)
                total = int(fresh.get("commission_posted_paise") or 0) + int((adj or [{"paise": 0}])[0]["paise"] or 0) + amount
                if total > cap:
                    raise BadRequestException(
                        f"A booking's commission can't be more than {cfg['max_commission_percent']:g}% of its value ({rupees_text(cap)}). Use a bonus for the rest."
                    )
                if total < 0:
                    raise BadRequestException("That cut would take this booking's commission below ₹0.")
            return await self.post({
                "captain_id": captain_id, "service_center_id": captain.get("service_center_id"), "kind": ledger_kind, "amount_paise": amount,
                "adjustment_kind": payload.kind, "booking_id": payload.booking_id, "booking_number": (booking or {}).get("booking_number"),
                "note": payload.note, "key": key, "actor_id": who["id"], "actor_name": who["name"], "actor_role": who["role"],
            }, session=s)

        try:
            row = await in_transaction(self.db, _do, None)
        except DuplicateKeyError:
            return {**await self.entry_view(await self.by_key(key)), "duplicate": True}
        await self.audit(who, "PAYROLL_ADJUSTMENT", str(row["_id"]), {
            "captain_id": captain_id, "kind": payload.kind, "amount_paise": amount, "note": payload.note, "booking_id": payload.booking_id,
        }, captain.get("service_center_id"))
        return {**await self.entry_view(row), "duplicate": False}

    # ================= deposit & kit =================

    async def deposit_action(self, captain_id: str, payload, actor) -> dict:
        captain = await self.captain_or_404(captain_id, actor.role, actor.service_center_id)
        who = await self.actor_info(actor)
        center_id = captain.get("service_center_id")
        if payload.action == "expect":
            pay = dict(captain.get("pay") or pt.blank_pay())
            before = pay.get("deposit_expected_paise") or 0
            if before != payload.amount_paise:
                pay["deposit_expected_paise"] = int(payload.amount_paise)
                await self.users.update_one({"_id": captain["_id"]}, {"$set": {"pay": pay}})
                await self.audit(who, "PAYROLL_DEPOSIT_EXPECTED", captain_id, {"before": before, "after": payload.amount_paise}, center_id)
            return await self.captain_detail_payload(await self.users.find_one({"_id": captain["_id"]}), today_ist())
        kind = {"received": "deposit_received", "refund": "deposit_refund", "forfeit": "deposit_forfeit"}[payload.action]
        key = f"dep:{captain_id}:{payload.key}"
        if await self.by_key(key):
            return await self.captain_detail_payload(captain, today_ist())
        finance_impact = await self.finance_impact_for(center_id)
        oid = ObjectId()
        amount = payload.amount_paise if payload.action == "received" else -payload.amount_paise

        async def _do(s):
            if payload.action != "received":
                held = (await self.repo.dues(captain_id, session=s))["deposit_held_paise"]
                if payload.amount_paise > held:
                    raise BadRequestException(f"Only {rupees_text(held)} is held as deposit — that is the most that can be {'refunded' if payload.action == 'refund' else 'forfeited'}.")
            entry_id = None
            if finance_impact and payload.action in ("received", "refund"):
                from app.services.finance_service import FinanceService

                entry = await FinanceService(self.db).create_system_entry(
                    title=f"Captain deposit {'received' if payload.action == 'received' else 'refunded'} — {captain.get('full_name')}"[:120],
                    type="credit" if payload.action == "received" else "debit", amount_paise=payload.amount_paise, category="Captain Deposit",
                    source="payroll_deposit", source_id=str(oid), service_center_id=center_id, done_by=who["name"], actor_id=who["id"] or "system",
                    actor_role=who["role"], description=(payload.note or "")[:400], session=s,
                )
                entry_id = entry["id"]
            return await self.post({
                "_id": oid, "captain_id": captain_id, "service_center_id": center_id, "kind": kind, "amount_paise": amount, "method": payload.method,
                "note": payload.note, "key": key, "finance_impact": finance_impact and payload.action in ("received", "refund"),
                "finance_entry_id": entry_id, "actor_id": who["id"], "actor_name": who["name"], "actor_role": who["role"],
            }, session=s)

        try:
            await in_transaction(self.db, _do, None)
        except DuplicateKeyError:
            pass
        await self.audit(who, f"PAYROLL_DEPOSIT_{payload.action.upper()}", str(oid), {"captain_id": captain_id, "amount_paise": payload.amount_paise, "note": payload.note}, center_id)
        return await self.captain_detail_payload(captain, today_ist())

    async def update_kit(self, captain_id: str, payload, actor) -> dict:
        captain = await self.captain_or_404(captain_id, actor.role, actor.service_center_id)
        who = await self.actor_info(actor)
        pay = dict(captain.get("pay") or pt.blank_pay())
        before = dict(pay.get("kit") or pt.empty_kit())
        kit = {**pt.empty_kit(), **before, **payload.model_dump(exclude={"returned_on"})}
        if kit == before:
            return await self.captain_detail_payload(captain, today_ist())
        if kit["provided"] and not kit.get("issued_on"):
            kit["issued_on"] = today_ist()
        if kit["provided"] and not before.get("provided"):
            kit["returned_on"] = None
        pay["kit"] = kit
        await self.users.update_one({"_id": captain["_id"]}, {"$set": {"pay": pay}})
        await self.audit(who, "PAYROLL_KIT_UPDATE", captain_id, {"before": before, "after": kit}, captain.get("service_center_id"))
        return await self.captain_detail_payload(await self.users.find_one({"_id": captain["_id"]}), today_ist())

    async def return_kit(self, captain_id: str, payload, actor) -> dict:
        captain = await self.captain_or_404(captain_id, actor.role, actor.service_center_id)
        pay = dict(captain.get("pay") or pt.blank_pay())
        kit = dict(pay.get("kit") or pt.empty_kit())
        if not kit.get("provided"):
            raise BadRequestException("No kit was issued to this captain.")
        if kit.get("returned_on"):
            return await self.captain_detail_payload(captain, today_ist())
        returned = _check_day(payload.returned_on or today_ist(), "The return date")
        if returned > today_ist():
            raise BadRequestException("The return date can't be in the future.")
        kit["returned_on"] = returned
        if payload.note:
            kit["notes"] = ((kit.get("notes") or "") + f" Returned: {payload.note}").strip()[:300]
        pay["kit"] = kit
        await self.users.update_one({"_id": captain["_id"]}, {"$set": {"pay": pay}})
        who = await self.actor_info(actor)
        await self.audit(who, "PAYROLL_KIT_RETURN", captain_id, {"returned_on": returned, "note": payload.note}, captain.get("service_center_id"))
        return await self.captain_detail_payload(await self.users.find_one({"_id": captain["_id"]}), today_ist())

    async def exit_blockers(self, captain: dict, session=None) -> list[str]:
        """What stops a captain leaving (delete / exit-suspend): money owed
        either way, a deposit still held, a kit not returned (D15)."""
        cid = str(captain["_id"])
        if pt.basis_of(captain) == "legacy" and not captain.get("pay"):
            has_rows = await self.ledger.count_documents({"captain_id": cid}, limit=1, session=session)
            if not has_rows:
                return []
        out: list[str] = []
        figures = await self.repo.dues(cid, session=session)
        if figures["dues_paise"] != 0:
            kind = "owed to them" if figures["dues_paise"] > 0 else "to recover from them"
            out.append(f"pay of {rupees_text(abs(figures['dues_paise']))} {kind} — settle it on the Salary page")
        if figures["deposit_held_paise"] > 0:
            out.append(f"a deposit of {rupees_text(figures['deposit_held_paise'])} still held — refund or forfeit it")
        kit = (captain.get("pay") or {}).get("kit") or {}
        if kit.get("provided") and not kit.get("returned_on"):
            out.append("a kit that isn't marked returned")
        return out
