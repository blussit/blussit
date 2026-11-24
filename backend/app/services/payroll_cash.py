"""Two simple things a manager needs next to captain pay (§7.15):

* **Cash received from a captain** — the captain took cash from customers at
  the door; when he hands it over, the manager records it here. The captain's
  wallet goes up by the amount, each booking remembers how much of its cash was
  handed in (`captain_cash_returned`), and a receipt keeps the trail. A receipt
  can be undone. A cash offset against pay (`cash_offset_paise`) uses the same
  bookkeeping, so the two never disagree.
* **Mark commission paid / unpaid** — for bookings that were paid some other
  way (an advance, a plain payment, cash in hand) so they must not be paid
  again. It moves no money; it only settles the booking, exactly as an invoice
  line does, and can be undone.

Everything is guarded on the booking's own values inside one transaction, so a
double tap or two managers at once can't count anything twice.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from bson import ObjectId
from pymongo.errors import DuplicateKeyError

from app.core.authz import ensure_own_center
from app.core.exceptions import BadRequestException, ConflictException, NotFoundException
from app.services import booking_money as bm
from app.services import payroll_terms as pt
from app.services.customer_wallet_service import in_transaction
from app.services.payroll_base import iso, ist_day, rupees_text, today_ist
from app.services.payroll_invoices import InvoiceMixin
from app.services.payroll_months import _utc_bounds, check_month
from app.services.payroll_salary import month_bounds

MAX_BOOKINGS = 1000


def _paise(rupees) -> int:
    return bm.paise(rupees)


class CashMixin(InvoiceMixin):
    # ================= mark commission paid / unpaid =================

    async def _commission_totals(self, cid: str, booking_ids: list[str], session=None) -> dict[str, int]:
        if not booking_ids:
            return {}
        rows = await self.ledger.aggregate([
            {"$match": {"captain_id": cid, "kind": {"$in": ["commission", "commission_adjust"]}, "booking_id": {"$in": booking_ids}}},
            {"$group": {"_id": "$booking_id", "p": {"$sum": "$amount_paise"}}},
        ], session=session).to_list(length=None)
        return {r["_id"]: int(r["p"] or 0) for r in rows}

    async def _marked_totals(self, cid: str, booking_ids: list[str], session=None) -> dict[str, int]:
        if not booking_ids:
            return {}
        rows = await self.db.payroll_settlements.aggregate([
            {"$match": {"captain_id": cid, "booking_id": {"$in": booking_ids}, "kind": {"$in": ["mark", "unmark"]}}},
            {"$group": {"_id": "$booking_id", "p": {"$sum": "$amount_paise"}}},
        ], session=session).to_list(length=None)
        return {r["_id"]: int(r["p"] or 0) for r in rows}

    async def mark_commissions_paid(self, captain_id: str, booking_ids: list[str] | None, month: str | None, reason: str | None, actor) -> dict:
        """Settle bookings that were already paid outside an invoice. No money moves."""
        captain = await self.captain_or_404(captain_id, actor.role, actor.service_center_id)
        cid = str(captain["_id"])
        who = await self.actor_info(actor)
        flt: dict = {"commission_captain_id": cid, "status": "completed", "is_deleted": {"$ne": True}}
        if booking_ids:
            ids = [ObjectId(b) for b in booking_ids if ObjectId.is_valid(str(b))]
            if len(ids) != len(set(booking_ids)):
                raise NotFoundException("Booking not found")
            flt["_id"] = {"$in": ids}
        else:
            first, last = month_bounds(check_month(month))
            lo, hi = _utc_bounds(first, last)
            flt["completed_at"] = {"$gte": lo, "$lt": hi}

        async def _do(s):
            docs = await self.bookings.find(flt, session=s).limit(MAX_BOOKINGS).to_list(length=MAX_BOOKINGS)
            totals = await self._commission_totals(cid, [str(d["_id"]) for d in docs], s)
            done, amount = [], 0
            for b in docs:
                bid = str(b["_id"])
                total = totals.get(bid, 0)
                settled = int(((b.get("commission_settled_by") or {}).get(cid)) or 0)
                diff = total - settled
                if diff <= 0:
                    continue  # nothing owing (or a recovery) — leave it to an invoice
                field = f"commission_settled_by.{cid}"
                guard: dict = {"_id": b["_id"]}
                guard.update({"$or": [{field: 0}, {field: {"$exists": False}}, {field: None}]} if settled == 0 else {field: settled})
                hit = await self.bookings.update_one(guard, {"$set": {field: total}}, session=s)
                if not hit.matched_count:
                    raise ConflictException("A booking was just paid or changed by someone else — try again.")
                await self.bookings.update_one({"_id": b["_id"], "commission_captain_id": cid}, {"$set": {"commission_settled_paise": total}}, session=s)
                await self.db.payroll_settlements.insert_one({
                    "invoice_id": None, "invoice_number": None, "captain_id": cid, "booking_id": bid, "booking_number": b.get("booking_number"),
                    "amount_paise": diff, "kind": "mark", "key": f"mk:{bid}:{uuid.uuid4().hex}", "note": (reason or "")[:200],
                    "actor_id": who["id"], "actor_name": who["name"], "created_at": datetime.now(timezone.utc),
                }, session=s)
                done.append(b.get("booking_number"))
                amount += diff
            return done, amount

        done, amount = await in_transaction(self.db, _do, None)
        if done:
            await self.audit(who, "PAYROLL_MARK_PAID", cid, {"captain_id": cid, "bookings": done[:50], "count": len(done), "amount_paise": amount, "reason": reason}, captain.get("service_center_id"))
        return {"count": len(done), "amount_paise": amount, "bookings": done,
                "message": f"{len(done)} {'booking' if len(done) == 1 else 'bookings'} marked paid ({rupees_text(amount)})." if done else "Nothing was left to mark — they are already paid."}

    async def mark_commissions_unpaid(self, captain_id: str, booking_ids: list[str], reason: str, actor) -> dict:
        """Undo a "mark paid" (only that part — money paid by an invoice stays paid)."""
        captain = await self.captain_or_404(captain_id, actor.role, actor.service_center_id)
        cid = str(captain["_id"])
        who = await self.actor_info(actor)
        if not booking_ids:
            raise BadRequestException("Pick the bookings to mark unpaid.")

        async def _do(s):
            marked = await self._marked_totals(cid, booking_ids, s)
            done, amount = [], 0
            for bid in booking_ids:
                if not ObjectId.is_valid(str(bid)):
                    raise NotFoundException("Booking not found")
                b = await self.bookings.find_one({"_id": ObjectId(bid)}, session=s)
                if not b:
                    raise NotFoundException("Booking not found")
                ensure_own_center(actor.role, actor.service_center_id, b.get("service_center_id"))
                settled = int(((b.get("commission_settled_by") or {}).get(cid)) or 0)
                take = min(marked.get(bid, 0), settled)
                if take <= 0:
                    continue
                field = f"commission_settled_by.{cid}"
                hit = await self.bookings.update_one({"_id": b["_id"], field: settled}, {"$inc": {field: -take}}, session=s)
                if not hit.matched_count:
                    raise ConflictException("A booking was just changed by someone else — try again.")
                await self.bookings.update_one({"_id": b["_id"], "commission_captain_id": cid}, {"$inc": {"commission_settled_paise": -take}}, session=s)
                await self.db.payroll_settlements.insert_one({
                    "invoice_id": None, "invoice_number": None, "captain_id": cid, "booking_id": bid, "booking_number": b.get("booking_number"),
                    "amount_paise": -take, "kind": "unmark", "key": f"unmk:{bid}:{uuid.uuid4().hex}", "note": reason[:200],
                    "actor_id": who["id"], "actor_name": who["name"], "created_at": datetime.now(timezone.utc),
                }, session=s)
                done.append(b.get("booking_number"))
                amount += take
            return done, amount

        done, amount = await in_transaction(self.db, _do, None)
        if done:
            await self.audit(who, "PAYROLL_MARK_UNPAID", cid, {"captain_id": cid, "bookings": done[:50], "count": len(done), "amount_paise": amount, "reason": reason}, captain.get("service_center_id"))
        return {"count": len(done), "amount_paise": amount, "bookings": done,
                "message": f"{len(done)} {'booking' if len(done) == 1 else 'bookings'} marked unpaid." if done else "Nothing to undo — those were paid by an invoice, not marked by hand."}

    # ================= cash held by a captain =================

    @staticmethod
    def booking_cash(b: dict) -> dict:
        """What the captain took in cash for this booking, what he handed in, what he still holds (paise)."""
        collected = _paise(bm.captain_cash_held(b)) if not bm.captain_wallet_exempt(b) else 0
        returned = min(collected, _paise(b.get("captain_cash_returned")))
        return {"collected_paise": collected, "returned_paise": returned, "held_paise": collected - returned}

    async def _cash_bookings(self, cid: str, session=None, booking_id: str | None = None) -> list[dict]:
        flt: dict = {
            "$or": [{"captain_id": cid}, {"captain_wallet_captain_id": cid}], "status": "completed", "is_deleted": {"$ne": True},
            "completed_by_role": {"$ne": "manager"},
        }
        if booking_id:
            flt["_id"] = ObjectId(booking_id)
        docs = await self.bookings.find(flt, session=session).sort([("completed_at", 1), ("_id", 1)]).limit(MAX_BOOKINGS).to_list(length=MAX_BOOKINGS)
        return [d for d in docs if self.booking_cash(d)["collected_paise"] > 0]

    async def cash_position(self, captain_id: str, actor) -> dict:
        captain = await self.captain_or_404(captain_id, actor.role, actor.service_center_id)
        cid = str(captain["_id"])
        rows = []
        for b in await self._cash_bookings(cid):
            c = self.booking_cash(b)
            if c["held_paise"] > 0:
                rows.append({"booking_id": str(b["_id"]), "booking_number": b.get("booking_number"), "completed_at": iso(b.get("completed_at")),
                             "day": ist_day(b.get("completed_at")), **c})
        receipts = await self.db.captain_cash_receipts.find({"captain_id": cid}).sort([("created_at", -1)]).limit(30).to_list(length=30)
        return {
            "captain_id": cid, "captain_name": captain.get("full_name"), "held_paise": sum(r["held_paise"] for r in rows),
            "bookings": rows[:200], "receipts": [self.receipt_view(r) for r in receipts],
        }

    @staticmethod
    def receipt_view(r: dict) -> dict:
        return {
            "id": str(r["_id"]), "captain_id": r.get("captain_id"), "amount_paise": int(r.get("amount_paise") or 0), "kind": r.get("kind"),
            "status": r.get("status"), "note": r.get("note"), "allocations": r.get("allocations") or [], "actor_name": r.get("actor_name"),
            "invoice_number": r.get("invoice_number"), "created_at": iso(r.get("created_at")), "void": (
                {**r["void"], "at": iso(r["void"].get("at"))} if isinstance(r.get("void"), dict) else None
            ),
        }

    async def return_cash_in(
        self, s, cid: str, amount_paise: int, *, booking_id: str | None, kind: str, note: str | None, who: dict, center_id: str | None,
        key: str, invoice_id: str | None = None, invoice_number: str | None = None, strict: bool = True, post_wallet: bool = True,
    ) -> dict:
        """Inside a transaction: take `amount_paise` of the captain's held cash
        off his bookings (oldest first, or just `booking_id`), lift his wallet
        by the same, and write the receipt. `strict` refuses more than he holds;
        otherwise whatever the bookings don't cover only moves the wallet."""
        docs = await self._cash_bookings(cid, s, booking_id)
        left, allocations = amount_paise, []
        for b in docs:
            if left <= 0:
                break
            take = min(left, self.booking_cash(b)["held_paise"])
            if take <= 0:
                continue
            guard = {"_id": b["_id"], "captain_cash_returned": b.get("captain_cash_returned")} if b.get("captain_cash_returned") is not None else {
                "_id": b["_id"], "$or": [{"captain_cash_returned": {"$exists": False}}, {"captain_cash_returned": None}]
            }
            hit = await self.bookings.update_one(guard, {"$set": {"captain_cash_returned": bm.r2(float(b.get("captain_cash_returned") or 0) + take / 100)}}, session=s)
            if not hit.matched_count:
                raise ConflictException("A booking was just changed by someone else — try again.")
            allocations.append({"booking_id": str(b["_id"]), "booking_number": b.get("booking_number"), "amount_paise": take})
            left -= take
        if left > 0 and (strict or booking_id):
            held = amount_paise - left
            raise BadRequestException(
                f"This captain holds only {rupees_text(held)} of cash" + (" for this booking" if booking_id else "") + f" — you can't receive {rupees_text(amount_paise)}."
            )
        if post_wallet:
            from app.services.wallet_service import WalletService

            await WalletService(self.db).post_delta(cid, amount_paise / 100, booking_id, note or "Cash handed over to the business", session=s, key=f"cashrx:{key}")
        receipt = {
            "_id": ObjectId(), "captain_id": cid, "service_center_id": center_id, "amount_paise": amount_paise, "kind": kind, "status": "active",
            "note": note, "allocations": allocations, "unallocated_paise": left, "key": key, "invoice_id": invoice_id, "invoice_number": invoice_number,
            "actor_id": who["id"], "actor_name": who["name"], "actor_role": who["role"], "void": None, "created_at": datetime.now(timezone.utc),
        }
        await self.db.captain_cash_receipts.insert_one(receipt, session=s)
        return receipt

    async def receive_cash(self, captain_id: str, amount_paise: int, booking_id: str | None, note: str | None, idempotency_key: str, actor) -> dict:
        captain = await self.captain_or_404(captain_id, actor.role, actor.service_center_id)
        cid = str(captain["_id"])
        who = await self.actor_info(actor)
        key = f"rx:{cid}:{idempotency_key}"
        existing = await self.db.captain_cash_receipts.find_one({"key": key})
        if existing:
            return {**self.receipt_view(existing), "duplicate": True}
        if booking_id:
            if not ObjectId.is_valid(booking_id):
                raise NotFoundException("Booking not found")
            b = await self.bookings.find_one({"_id": ObjectId(booking_id), "is_deleted": {"$ne": True}}, {"captain_id": 1, "captain_wallet_captain_id": 1})
            if not b or cid not in (b.get("captain_id"), b.get("captain_wallet_captain_id")):
                raise NotFoundException("Booking not found")

        async def _do(s):
            return await self.return_cash_in(
                s, cid, amount_paise, booking_id=booking_id, kind="received", note=note, who=who, center_id=captain.get("service_center_id"), key=key,
            )

        try:
            receipt = await in_transaction(self.db, _do, None)
        except (DuplicateKeyError, BadRequestException, ConflictException):
            # The same tap twice at once: the first landed, so this is its duplicate, not an error.
            existing = await self.db.captain_cash_receipts.find_one({"key": key})
            if not existing:
                raise
            return {**self.receipt_view(existing), "duplicate": True}
        await self.audit(who, "CAPTAIN_CASH_RECEIVED", str(receipt["_id"]), {
            "captain_id": cid, "amount_paise": amount_paise, "bookings": [a["booking_number"] for a in receipt["allocations"]][:50], "note": note,
        }, captain.get("service_center_id"))
        await self.notify(
            cid, f"{rupees_text(amount_paise)} cash received from you",
            f"{who['name']} recorded {rupees_text(amount_paise)} of cash you handed over" + (f" ({', '.join(a['booking_number'] for a in receipt['allocations'][:3])})" if receipt["allocations"] else "") + ".",
            str(receipt["_id"]), whatsapp=False,
        )
        return {**self.receipt_view(receipt), "duplicate": False}

    async def undo_receipt_in(self, s, receipt: dict, reason: str, who: dict, *, post_wallet: bool = True) -> None:
        """Inside a transaction: put a receipt's cash back on the captain's bookings and wallet."""
        done = await self.db.captain_cash_receipts.update_one(
            {"_id": receipt["_id"], "status": "active"},
            {"$set": {"status": "void", "void": {"at": datetime.now(timezone.utc), "by": who["name"], "by_id": who["id"], "reason": reason[:200]}}}, session=s,
        )
        if not done.matched_count:
            raise ConflictException("This receipt was just undone.")
        for a in receipt.get("allocations") or []:
            await self.bookings.update_one({"_id": ObjectId(a["booking_id"])}, {"$inc": {"captain_cash_returned": -a["amount_paise"] / 100}}, session=s)
        if post_wallet:
            from app.services.wallet_service import WalletService

            await WalletService(self.db).post_delta(
                receipt["captain_id"], -int(receipt["amount_paise"]) / 100, None, "Cash receipt undone", session=s, key=f"cashrx-void:{receipt['key']}",
            )

    async def void_receipt(self, receipt_id: str, reason: str, actor) -> dict:
        if not ObjectId.is_valid(receipt_id or ""):
            raise NotFoundException("Receipt not found")
        receipt = await self.db.captain_cash_receipts.find_one({"_id": ObjectId(receipt_id)})
        if not receipt:
            raise NotFoundException("Receipt not found")
        captain = await self.captain_or_404(receipt["captain_id"], actor.role, actor.service_center_id)
        if receipt.get("kind") != "received":
            raise BadRequestException("This cash was set against a payment — cancel that payment's invoice instead.")
        if receipt.get("status") != "active":
            return {**self.receipt_view(receipt), "duplicate": True}
        who = await self.actor_info(actor)

        async def _do(s):
            await self.undo_receipt_in(s, receipt, reason, who)

        await in_transaction(self.db, _do, None)
        await self.audit(who, "CAPTAIN_CASH_RECEIPT_UNDONE", receipt_id, {"captain_id": receipt["captain_id"], "amount_paise": receipt["amount_paise"], "reason": reason}, captain.get("service_center_id"))
        return {**self.receipt_view(await self.db.captain_cash_receipts.find_one({"_id": receipt["_id"]})), "duplicate": False}

    # ================= one booking's captain money =================

    async def booking_captain_money(self, booking_id: str, actor) -> dict:
        if not ObjectId.is_valid(booking_id or ""):
            raise NotFoundException("Booking not found")
        b = await self.bookings.find_one({"_id": ObjectId(booking_id), "is_deleted": {"$ne": True}})
        if not b:
            raise NotFoundException("Booking not found")
        ensure_own_center(actor.role, actor.service_center_id, b.get("service_center_id"))
        commission_cid = b.get("commission_captain_id")
        cash_cid = b.get("captain_wallet_captain_id") or b.get("captain_id")
        cid = commission_cid or b.get("captain_id")
        captain = await self.users.find_one({"_id": ObjectId(cid), "role": "captain"}) if cid and ObjectId.is_valid(str(cid)) else None
        out = {
            "booking_id": str(b["_id"]), "booking_number": b.get("booking_number"), "status": b.get("status"), "captain_id": cid,
            "captain_name": (captain or {}).get("full_name"), "basis": pt.basis_of(captain) if captain else None,
            "finance_impact": await self.finance_impact_for(b.get("service_center_id")), "commission": None, "cash": None, "adjustments": [],
        }
        if not captain:
            return out
        if b.get("status") == "completed" and commission_cid:
            row = await self.booking_commission_row(b)
            await self._attach_payment_state(commission_cid, [row])
            marked = (await self._marked_totals(commission_cid, [str(b["_id"])])).get(str(b["_id"]), 0)
            row["marked_paise"] = marked
            out["commission"] = row
        cash = self.booking_cash(b) if cash_cid else None
        if cash and cash["collected_paise"] > 0:
            receipts = await self.db.captain_cash_receipts.find({"allocations.booking_id": str(b["_id"]), "status": "active"}).sort([("created_at", -1)]).limit(10).to_list(length=10)
            out["cash"] = {**cash, "captain_id": cash_cid, "receipts": [self.receipt_view(r) for r in receipts]}
        adj = await self.ledger.find({"booking_id": str(b["_id"]), "kind": {"$in": ["bonus", "deduction", "commission_adjust"]}}).sort([("created_at", -1)]).limit(30).to_list(length=30)
        out["adjustments"] = [self._entry(r) for r in adj]
        out["today"] = today_ist()
        return out
