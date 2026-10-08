"""
Customer wallet (founder, 2026-10-07 — spec §1.1).

Every customer has one balance (`customer_wallets`, one doc per customer),
which CAN go negative, and an immutable ledger (`customer_wallet_ledger`):
one row per movement — signed amount, kind, balance after, actor, booking,
note and an idempotency `key` (unique index). A row and its balance change
are written in ONE transaction, and a key already on the ledger makes the
post a no-op: a retried request, a replayed webhook or two racing paths can
never credit or debit twice.

Credits: a cancelled paid booking (net of its charge), a booking edited down,
an overpayment, a reduced/waived charge, admin adjustments. Debits: an unpaid
booking cancelled late (the charge), balance spent on a booking, a manager
PAYBACK for a booking that went wrong (below), admin adjustments.

Manager paybacks (founder 2026-10-07, MONEY-2). A manager never adds money
to a wallet at will and never pays out "the balance": he pays money BACK for
one of the customer's bookings that was cancelled, delayed or had an issue,
from the center's side (cash / UPI / bank) — `payback()`. Wallet credit is
debited first (kind `payout`, "Paid By Manager <name>"); what the wallet
can't cover is a goodwill payback recorded on the booking only
(`manager_paybacks[]`, no wallet movement) — never on a cancelled booking,
whose money already went back to the wallet. Every payback is one row in
`customer_paybacks` (unique idempotency key) and, per booking, the total
(`paid_back_total`) never exceeds what the customer paid for it. The only
free movement left is the admin's audited `adjust()`.

A NEGATIVE balance is carried into the customer's next booking (its first
paying car's total, `wallet_due_carried`) and only cleared — a credit —
when that booking's money actually comes in. While it rides on an unpaid
booking it is RESERVED on the wallet (`carried_due`), so two bookings can
never both carry the same debt; it is released if that booking is cancelled
before it was paid. A POSITIVE balance is spent at creation
(`wallet_applied`), guarded on the balance itself (no double spend), and
returned if that booking is cancelled.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from bson import ObjectId
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

from app.core.exceptions import BadRequestException, NotFoundException
from app.models.enums import CustomerWalletEntryKind as Kind, NotificationType
from app.services import booking_money as bm
from app.services.booking_money import EPSILON, paise, r2
from app.utils.serializers import serialize_doc

logger = logging.getLogger(__name__)

PAYOUT_METHODS = ("upi", "bank_transfer", "cash", "other")
PAYBACK_REASONS = ("cancelled", "delayed", "complaint", "service_issue", "other")
REASON_LABELS = {
    "cancelled": "Booking Cancelled",
    "delayed": "Service Delayed",
    "complaint": "Complaint",
    "service_issue": "Service Issue",
    "other": "Other",
}
# Issue flags that mean the job ran late (anything else is a service issue).
_DELAY_FLAG_WORDS = ("late", "delay", "overrun", "missed", "not_started", "not_reached")
OTHER_CENTER = "Other Center"

# The customer-facing reason for each kind (ledger list, WhatsApp line).
KIND_LABELS = {
    Kind.CANCELLATION.value: "Booking cancelled",
    Kind.CANCELLATION_CHARGE.value: "Late cancellation charge",
    Kind.CHARGE_MIGRATED.value: "Late cancellation charge",
    Kind.CHARGE_REDUCED.value: "Cancellation charge reduced",
    Kind.PRICE_REDUCED.value: "Booking price reduced",
    Kind.OVERPAYMENT.value: "Extra payment kept for you",
    Kind.BOOKING_PAYMENT.value: "Used for a booking",
    Kind.PREVIOUS_BALANCE_PAID.value: "Previous balance paid",
    Kind.PAYOUT.value: "Paid back to you",
    Kind.ADJUSTMENT.value: "Adjustment",
    Kind.REFUND.value: "Plan Refund",
}


class InsufficientWalletBalance(BadRequestException):
    error_code = "WALLET_BALANCE_TOO_LOW"


class PaybackNotAllowed(BadRequestException):
    error_code = "PAYBACK_NOT_ALLOWED"


class PaybackTooMuch(BadRequestException):
    error_code = "PAYBACK_TOO_MUCH"


def payback_key(booking_id: str, method: str, reference: str | None, amount: float, idempotency_key: str | None, now: datetime) -> str:
    """The idempotency key of a payback: (booking, method, reference) for a
    transfer; for cash the caller's idempotency_key, else (booking, amount,
    minute) — a double tap records once."""
    if idempotency_key:
        return f"payback:{booking_id}:key:{idempotency_key}"
    if method != "cash":
        return f"payback:{booking_id}:{method}:{' '.join((reference or '').split()).lower()}"
    return f"payback:{booking_id}:cash:{paise(amount)}:{now.strftime('%Y%m%d%H%M')}"


def _utc_days(date_from: str | None, date_to: str | None) -> dict | None:
    """IST days (YYYY-MM-DD, inclusive) as the UTC instants created_at is
    stored in. None when neither bound is given."""
    from datetime import timedelta

    from app.utils.timezone import IST

    if not date_from and not date_to:
        return None
    out: dict = {}
    try:
        if date_from:
            out["$gte"] = datetime.strptime(date_from, "%Y-%m-%d").replace(tzinfo=IST).astimezone(timezone.utc).replace(tzinfo=None)
        if date_to:
            end = datetime.strptime(date_to, "%Y-%m-%d") + timedelta(days=1)
            out["$lt"] = end.replace(tzinfo=IST).astimezone(timezone.utc).replace(tzinfo=None)
    except ValueError:
        raise BadRequestException("Dates must be real days (YYYY-MM-DD).") from None
    return out


async def in_transaction(db, fn, session=None):
    """Run `fn(session)` inside the caller's transaction, or in a new one."""
    if session is not None:
        return await fn(session)
    async with await db.client.start_session() as own:
        return await own.with_transaction(fn)


def _now() -> datetime:
    return datetime.now(timezone.utc)


class CustomerWalletService:
    def __init__(self, db):
        self.db = db
        self.wallets = db.customer_wallets
        self.ledger_rows = db.customer_wallet_ledger

    # -- reads --------------------------------------------------------------

    async def wallet(self, customer_id: str, session=None) -> dict:
        doc = await self.wallets.find_one({"customer_id": customer_id}, session=session)
        return doc or {"customer_id": customer_id, "balance": 0.0, "carried_due": 0.0}

    async def balance(self, customer_id: str, session=None) -> float:
        return r2((await self.wallet(customer_id, session=session)).get("balance"))

    async def summary(self, customer_id: str, session=None) -> dict:
        """balance; credit usable now; debt not yet carried by a booking."""
        doc = await self.wallet(customer_id, session=session)
        balance = r2(doc.get("balance"))
        carried = r2(doc.get("carried_due"))
        return {
            "customer_id": customer_id,
            "balance": balance,
            "credit_available": max(0.0, balance),
            # Shown in the quote as "Previous Balance Due ₹X": what the next
            # booking will carry.
            "previous_balance_due": max(0.0, r2(-balance - carried)),
            # Debt already riding on an unpaid booking (cleared when it is paid).
            "carried_due": carried,
        }

    async def ledger(self, customer_id: str, page: int = 1, page_size: int = 20) -> tuple[list[dict], int]:
        rows, total = await self._ledger_rows(customer_id, page, page_size)
        return [self.entry_view(r) for r in rows], total

    async def _ledger_rows(self, customer_id: str, page: int, page_size: int) -> tuple[list[dict], int]:
        query = {"customer_id": customer_id}
        total = await self.ledger_rows.count_documents(query)
        rows = await self.ledger_rows.find(query).sort([("created_at", -1), ("_id", -1)]).skip(
            max(page - 1, 0) * page_size
        ).limit(page_size).to_list(length=page_size)
        return rows, total

    @staticmethod
    def entry_view(row: dict) -> dict:
        doc = serialize_doc(dict(row))
        amount = r2(row.get("amount"))
        return {
            "id": doc.get("id"),
            "amount": amount,
            "direction": "credit" if amount >= 0 else "debit",
            "kind": row.get("kind"),
            "label": KIND_LABELS.get(row.get("kind"), "Wallet"),
            "balance_after": r2(row.get("balance_after")),
            "booking_id": row.get("booking_id"),
            "booking_number": row.get("booking_number"),
            "note": row.get("note"),
            "actor_role": row.get("actor_role"),
            "actor_name": row.get("actor_name"),
            "method": (row.get("meta") or {}).get("method"),
            "reference": (row.get("meta") or {}).get("reference"),
            # A manager payback's reason (cancelled / delayed / …).
            "reason": (row.get("meta") or {}).get("reason"),
            "reason_label": REASON_LABELS.get((row.get("meta") or {}).get("reason") or ""),
            "created_at": doc.get("created_at"),
        }

    # -- the one write ------------------------------------------------------

    async def post(
        self,
        customer_id: str,
        amount: float,
        kind: str,
        *,
        key: str,
        booking_id: str | None = None,
        actor_id: str | None = None,
        actor_role: str | None = None,
        note: str | None = None,
        session=None,
        require_balance: bool = False,
        booking_number: str | None = None,
        actor_name: str | None = None,
        meta: dict | None = None,
    ) -> dict:
        """Move `amount` (signed: + credit, − debit) on the customer's wallet
        and append its ledger row, atomically; idempotent on `key` — a key
        already on the ledger returns that row (`created` False) and changes
        nothing. `require_balance`: a debit only while the balance covers it
        (spending credit, a payout) — refused with WALLET_BALANCE_TOO_LOW
        otherwise. Runs inside `session`'s transaction, or its own."""
        if not customer_id or not key:
            raise BadRequestException("A wallet entry needs a customer and a key.")
        amount = r2(amount)
        kind = getattr(kind, "value", kind)

        async def _do(s):
            existing = await self.ledger_rows.find_one({"key": key}, session=s)
            if existing is not None:
                return {**self.entry_view(existing), "created": False, "balance": r2(existing.get("balance_after"))}
            if abs(amount) < 0.005:
                return {"created": False, "amount": 0.0, "balance": await self.balance(customer_id, session=s)}
            query: dict = {"customer_id": customer_id}
            if require_balance and amount < 0:
                query["balance"] = {"$gte": r2(-amount) - EPSILON}
            # The wallet doc is written BEFORE the ledger row: two racing
            # posts of one key conflict here (a transient error the
            # transaction retries), and the retry then finds the key above.
            wallet = await self.wallets.find_one_and_update(
                query,
                {"$inc": {"balance": amount}, "$set": {"updated_at": _now()},
                 "$setOnInsert": {"customer_id": customer_id, "carried_due": 0.0, "created_at": _now()}},
                upsert=not (require_balance and amount < 0),
                return_document=ReturnDocument.AFTER,
                session=s,
            )
            if wallet is None:
                raise InsufficientWalletBalance(
                    f"The wallet balance is ₹{await self.balance(customer_id, session=s):g} — not enough for ₹{-amount:g}."
                )
            # Float drift guard: keep the stored balance on whole paise.
            balance = r2(wallet["balance"])
            if balance != wallet["balance"]:
                await self.wallets.update_one({"_id": wallet["_id"]}, {"$set": {"balance": balance}}, session=s)
            number = booking_number
            if booking_id and not number and ObjectId.is_valid(str(booking_id)):
                b = await self.db.bookings.find_one({"_id": ObjectId(str(booking_id))}, {"booking_number": 1}, session=s)
                number = (b or {}).get("booking_number")
            row = {
                "customer_id": customer_id,
                "amount": amount,
                "kind": kind,
                "balance_after": balance,
                "key": key,
                "booking_id": str(booking_id) if booking_id else None,
                "booking_number": number,
                "actor_id": actor_id,
                "actor_role": actor_role,
                "actor_name": actor_name,
                "note": note,
                "meta": meta or None,
                "created_at": _now(),
            }
            inserted = await self.ledger_rows.insert_one(row, session=s)
            row["_id"] = inserted.inserted_id
            return {**self.entry_view(row), "created": True, "balance": balance}

        if session is not None:
            return await _do(session)
        try:
            return await in_transaction(self.db, _do)
        except DuplicateKeyError:
            # Lost a same-key race outside any caller transaction: the
            # winner's row is the answer.
            existing = await self.ledger_rows.find_one({"key": key})
            if existing is None:
                raise
            return {**self.entry_view(existing), "created": False, "balance": r2(existing.get("balance_after"))}

    # -- a negative balance riding on a booking ------------------------------

    async def reserve_carry(self, customer_id: str, amount: float, session) -> None:
        """Mark `amount` of the debt as carried by an unpaid booking —
        guarded so the reserved total never exceeds the debt."""
        amount = r2(amount)
        if amount <= 0:
            return
        done = await self.wallets.find_one_and_update(
            {"customer_id": customer_id, "$expr": {"$gte": [
                {"$subtract": [{"$multiply": [-1, "$balance"]}, {"$ifNull": ["$carried_due", 0]}]}, amount - EPSILON,
            ]}},
            {"$inc": {"carried_due": amount}, "$set": {"updated_at": _now()}},
            session=session,
        )
        if done is None:
            raise BadRequestException("Your wallet balance just changed — please try again.")

    async def release_carry(self, customer_id: str, amount: float, session) -> None:
        amount = r2(amount)
        if amount <= 0:
            return
        await self.wallets.update_one(
            {"customer_id": customer_id}, {"$inc": {"carried_due": -amount}, "$set": {"updated_at": _now()}}, session=session,
        )
        # Never below zero (a legacy row, a manual fix).
        await self.wallets.update_one({"customer_id": customer_id, "carried_due": {"$lt": 0}}, {"$set": {"carried_due": 0.0}}, session=session)

    # -- staff: payout / adjust ----------------------------------------------

    async def payback_signals(self, booking: dict, session=None) -> list[str]:
        """Why money may go back for this booking: "cancelled"; "delayed"
        (it ran late: delay_minutes, a late-start penalty, a late/overrun
        issue flag); "issue" (any other issue flag, open or resolved);
        "complaint" (a complaint raised on it). Empty: nothing went wrong."""
        signals: list[str] = []
        if booking.get("status") == "cancelled":
            signals.append("cancelled")
        flags = [f for f in (booking.get("issue_flag"), booking.get("resolved_issue_flag")) if f]
        late_flag = any(any(w in str(f) for w in _DELAY_FLAG_WORDS) for f in flags)
        if (
            float(booking.get("delay_minutes") or 0) > 0
            or float(booking.get("late_penalty_amount") or 0) > 0
            or float(booking.get("late_penalty_pct") or 0) > 0
            or late_flag
        ):
            signals.append("delayed")
        if flags:
            signals.append("issue")
        if await self.db.complaints.find_one(
            {"booking_id": str(booking["_id"]), "is_deleted": {"$ne": True}}, {"_id": 1}, session=session,
        ):
            signals.append("complaint")
        return signals

    @staticmethod
    def _reason_allowed(reason: str, signals: list[str]) -> bool:
        if reason == "cancelled":
            return "cancelled" in signals
        if reason == "complaint":
            return "complaint" in signals
        if reason == "delayed":
            return "delayed" in signals or "cancelled" in signals
        if reason == "service_issue":
            return bool({"issue", "complaint", "delayed"} & set(signals))
        return bool(signals)

    async def payback(
        self,
        customer_id: str,
        *,
        booking_id: str,
        amount: float,
        reason: str,
        method: str,
        reference: str | None,
        note: str | None,
        actor: dict,
        center: str | None,
        idempotency_key: str | None = None,
    ) -> dict:
        """A manager (the booking's center) or an admin paid money BACK to
        the customer for one of their bookings that went wrong — cancelled,
        delayed, a complaint, a service issue — directly (cash) or online
        (UPI / bank), from the center's side, and records it here.

        Wallet credit is debited first (≤ the positive balance; kind
        `payout`, "Paid By Manager <name>"); the rest is a goodwill payback
        on the booking alone (no wallet movement) — refused on a cancelled
        booking (its money already went back to the wallet). Per booking,
        all paybacks together never exceed what the customer paid for it.
        One transaction; idempotent on payback_key(). Audited
        (CUSTOMER_PAYBACK); the customer is told."""
        role = actor.get("role")
        if role not in ("manager", "admin"):
            raise NotFoundException("Customer not found")
        await self._customer_or_404(customer_id)
        if not ObjectId.is_valid(str(booking_id or "")):
            raise NotFoundException("Booking not found")
        booking = await self.db.bookings.find_one(
            {"_id": ObjectId(str(booking_id)), "customer_id": customer_id, "is_deleted": {"$ne": True}},
        )
        # Another center's booking (or another customer's) is "not found"
        # — never confirm it exists.
        if not booking or (role == "manager" and (not center or booking.get("service_center_id") != center)):
            raise NotFoundException("Booking not found")
        amount = r2(amount)
        if amount <= 0:
            raise BadRequestException("Enter the amount you paid back.")
        if method not in PAYOUT_METHODS:
            raise BadRequestException("Pick how the money was paid back.")
        if reason not in PAYBACK_REASONS:
            raise BadRequestException("Pick why the money is paid back.")
        reference = " ".join((reference or "").split()) or None
        if method != "cash" and len(reference or "") < 3:
            raise BadRequestException("Enter the transfer reference (UPI / bank reference).")
        note = " ".join((note or "").split()) or None
        if reason == "other" and len(note or "") < 10:
            raise BadRequestException("Say what went wrong (at least 10 characters).")
        now = _now()
        key = payback_key(str(booking["_id"]), method, reference, amount, idempotency_key, now)
        done = await self.db.customer_paybacks.find_one({"key": key})
        if done is not None:
            return self.payback_view(done, created=False)
        signals = await self.payback_signals(booking)
        if not signals or not self._reason_allowed(reason, signals):
            raise PaybackNotAllowed("Pay back is only for a booking that was cancelled, delayed or had an issue.")

        name = await self._name(actor.get("id"))
        who = f"Paid By {'Admin' if role == 'admin' else 'Manager'}" + (f" {name}" if name else "")
        number = booking.get("booking_number")
        booking_center = booking.get("service_center_id")

        async def _do(s):
            existing = await self.db.customer_paybacks.find_one({"key": key}, session=s)
            if existing is not None:
                return existing, None, False
            b = await self.db.bookings.find_one({"_id": booking["_id"]}, session=s)
            already = r2((b or {}).get("paid_back_total"))
            cap = r2(bm.paid_by_customer(b) - already)
            if amount > cap + EPSILON:
                raise PaybackTooMuch(
                    f"Everything paid for {number} was already paid back." if cap <= EPSILON
                    else f"At most ₹{cap:g} can still be paid back for {number} (what the customer paid for it)."
                )
            balance = await self.balance(customer_id, session=s)
            wallet_part = r2(min(amount, max(0.0, balance)))
            goodwill = r2(amount - wallet_part)
            if goodwill > EPSILON and b.get("status") == "cancelled":
                raise InsufficientWalletBalance(
                    f"The wallet holds ₹{max(0.0, balance):g} — this booking's money already went back to the wallet, "
                    "so only what is still there can be paid back."
                )
            payback_id = ObjectId()
            entry = None
            if wallet_part > EPSILON:
                entry = await self.post(
                    customer_id, -wallet_part, Kind.PAYOUT.value, key=f"{key}:wallet", booking_id=str(b["_id"]),
                    booking_number=number, actor_id=actor.get("id"), actor_role=role, actor_name=name,
                    note=f"{who} · {REASON_LABELS[reason]}" + (f" — {note}" if note else ""),
                    require_balance=True, session=s,
                    meta={"method": method, "reference": reference, "reason": reason, "service_center_id": booking_center,
                          "payback_id": str(payback_id), "source": "manager_payback"},
                )
            record = {
                "_id": payback_id,
                "key": key,
                "customer_id": customer_id,
                "booking_id": str(b["_id"]),
                "booking_number": number,
                "service_center_id": booking_center,
                "amount": amount,
                "wallet_amount": wallet_part,
                "goodwill_amount": goodwill,
                "method": method,
                "reference": reference,
                "reason": reason,
                "note": note,
                "signals": signals,
                "paid_by_id": actor.get("id"),
                "paid_by_name": name,
                "paid_by_role": role,
                "ledger_entry_id": (entry or {}).get("id"),
                "balance_after": (entry or {}).get("balance", balance),
                "created_at": now,
            }
            await self.db.customer_paybacks.insert_one(record, session=s)
            moved = await self.db.bookings.update_one(
                {"_id": b["_id"], "paid_back_total": b.get("paid_back_total")},
                {"$set": {"paid_back_total": r2(already + amount), "updated_at": now},
                 "$push": {"manager_paybacks": {
                     "id": str(payback_id), "amount": amount, "wallet_amount": wallet_part, "goodwill_amount": goodwill,
                     "method": method, "reference": reference, "reason": reason, "reason_label": REASON_LABELS.get(reason or ""),
                     "note": note, "by": actor.get("id"), "by_name": name, "by_role": role, "at": now,
                 }}},
                session=s,
            )
            if not moved.modified_count:
                raise BadRequestException("This booking's paybacks just changed — refresh and try again.")
            return record, entry, True

        try:
            record, entry, created = await in_transaction(self.db, _do)
        except DuplicateKeyError:
            existing = await self.db.customer_paybacks.find_one({"key": key})
            if existing is None:
                raise
            return self.payback_view(existing, created=False)
        if not created:
            return self.payback_view(record, created=False)
        await self._audit(actor, "CUSTOMER_PAYBACK", customer_id, {
            "booking_id": record["booking_id"], "booking_number": number, "amount": amount,
            "wallet_amount": record["wallet_amount"], "goodwill_amount": record["goodwill_amount"],
            "method": method, "reference": reference, "reason": reason, "note": note, "signals": signals,
            "balance_after": record["balance_after"],
        }, booking_center)
        if entry and entry.get("created"):
            await self.announce(customer_id, entry, reason=f"Paid back for {REASON_LABELS[reason].lower()}")
        if record["goodwill_amount"] > EPSILON:
            await self._announce_goodwill(customer_id, record)
        return self.payback_view(record, created=True)

    @staticmethod
    def payback_view(record: dict, *, created: bool | None = None) -> dict:
        doc = serialize_doc(dict(record))
        wallet_part, goodwill = r2(record.get("wallet_amount")), r2(record.get("goodwill_amount"))
        out = {
            "id": doc.get("id"),
            "customer_id": record.get("customer_id"),
            "booking_id": record.get("booking_id"),
            "booking_number": record.get("booking_number"),
            "service_center_id": record.get("service_center_id"),
            "amount": r2(record.get("amount")),
            "wallet_amount": wallet_part,
            "goodwill_amount": goodwill,
            "mode": "mixed" if wallet_part > EPSILON and goodwill > EPSILON else ("wallet" if wallet_part > EPSILON else "goodwill"),
            "method": record.get("method"),
            "reference": record.get("reference"),
            "reason": record.get("reason"),
            "reason_label": REASON_LABELS.get(record.get("reason") or ""),
            "note": record.get("note"),
            "paid_by_id": record.get("paid_by_id"),
            "paid_by_name": record.get("paid_by_name"),
            "paid_by_role": record.get("paid_by_role"),
            "balance": r2(record.get("balance_after")),
            "source": record.get("source") or "manager_payback",
            "created_at": doc.get("created_at"),
        }
        if created is not None:
            out["created"] = created
        return out

    async def _announce_goodwill(self, customer_id: str, record: dict) -> None:
        """In-app line for money handed back outside the wallet."""
        try:
            from app.services.notification_service import NotificationService

            number = record.get("booking_number")
            await NotificationService(self.db).notify(
                customer_id, "Money Paid Back",
                f"₹{r2(record.get('goodwill_amount')):g} paid back to you"
                + (f" for {number}" if number else "") + f" — {REASON_LABELS.get(record.get('reason') or '', 'Paid back')}.",
                NotificationType.SYSTEM, record.get("booking_id"), send_whatsapp=False, background=True,
            )
        except Exception:  # noqa: BLE001
            logger.exception("Could not announce payback for customer %s", customer_id)

    async def adjust(self, customer_id: str, amount: float, note: str, actor: dict, *, idempotency_key: str | None = None) -> dict:
        """Admin correction, either direction. Audited; the customer is told."""
        if actor.get("role") != "admin":
            raise NotFoundException("Customer not found")
        await self._customer_or_404(customer_id)
        amount = r2(amount)
        if abs(amount) < 0.01:
            raise BadRequestException("Enter a non-zero amount.")
        if not (note or "").strip():
            raise BadRequestException("Add a note saying why.")
        key = f"adjust:{customer_id}:{idempotency_key}" if idempotency_key else f"adjust:{customer_id}:{ObjectId()}"
        entry = await self.post(
            customer_id, amount, Kind.ADJUSTMENT.value, key=key,
            actor_id=actor.get("id"), actor_role="admin", actor_name=await self._name(actor.get("id")), note=note.strip(),
        )
        if entry.get("created"):
            await self._audit(actor, "CUSTOMER_WALLET_ADJUST", customer_id, {"amount": amount, "note": note, "balance_after": entry["balance"]}, None)
            await self.announce(customer_id, entry)
        return entry

    async def payouts(
        self, page: int, page_size: int, center: str | None = None, date_from: str | None = None, date_to: str | None = None,
    ) -> tuple[list[dict], int]:
        """Admin: every payback — manager paybacks (wallet and goodwill,
        `customer_paybacks`) plus the older wallet payouts that have no
        payback record (the pre-MONEY-2 free payout, a Razorpay refund taken
        from the wallet) — newest first, by center and/or IST day range."""
        window = _utc_days(date_from, date_to)
        match: dict = {}
        legacy: dict = {"kind": Kind.PAYOUT.value, "meta.payback_id": {"$exists": False}}
        if center:
            match["service_center_id"] = center
            legacy["meta.service_center_id"] = center
        if window:
            match["created_at"] = window
            legacy["created_at"] = window
        legacy_shape = {
            "_id": 1, "customer_id": 1, "booking_id": 1, "booking_number": 1, "created_at": 1,
            "service_center_id": "$meta.service_center_id",
            "amount": {"$multiply": [-1, "$amount"]},
            "wallet_amount": {"$multiply": [-1, "$amount"]},
            "goodwill_amount": {"$literal": 0},
            "method": "$meta.method", "reference": "$meta.reference", "reason": "$meta.reason", "note": 1,
            "paid_by_id": "$actor_id", "paid_by_name": "$actor_name", "paid_by_role": "$actor_role",
            "balance_after": 1,
            "source": {"$cond": [{"$eq": ["$meta.method", "razorpay_refund"]}, "razorpay_refund", "wallet_payout"]},
        }
        pipeline = [
            {"$match": match},
            {"$unionWith": {"coll": "customer_wallet_ledger", "pipeline": [{"$match": legacy}, {"$project": legacy_shape}]}},
            {"$sort": {"created_at": -1, "_id": -1}},
            {"$facet": {
                "rows": [{"$skip": max(page - 1, 0) * page_size}, {"$limit": page_size}],
                "total": [{"$count": "n"}],
            }},
        ]
        out = (await self.db.customer_paybacks.aggregate(pipeline).to_list(length=1)) or [{}]
        rows = out[0].get("rows") or []
        total = ((out[0].get("total") or [{}])[0]).get("n") or 0

        def oids(values) -> list[ObjectId]:
            return [ObjectId(v) for v in {str(v) for v in values if v} if ObjectId.is_valid(v)]

        user_ids = oids([r.get("customer_id") for r in rows] + [r.get("paid_by_id") for r in rows])
        users = {
            str(u["_id"]): u
            for u in await self.db.users.find({"_id": {"$in": user_ids}}, {"full_name": 1, "phone": 1}).to_list(length=len(user_ids))
        } if user_ids else {}
        center_ids = oids([r.get("service_center_id") for r in rows])
        centers = {
            str(c["_id"]): c.get("name")
            for c in await self.db.service_centers.find({"_id": {"$in": center_ids}}, {"name": 1}).to_list(length=len(center_ids))
        } if center_ids else {}
        items = []
        for r in rows:
            view = self.payback_view(r)
            customer = users.get(r.get("customer_id") or "") or {}
            staff = users.get(str(r.get("paid_by_id") or "")) or {}
            items.append({
                **view,
                "paid_by_name": view.get("paid_by_name") or staff.get("full_name"),
                "customer_name": customer.get("full_name"), "customer_phone": customer.get("phone"),
                "center_name": centers.get(r.get("service_center_id") or ""),
            })
        return items, total

    async def paid_back_totals(self, window: dict | None, service_center_id: str | None = None) -> dict[str | None, dict]:
        """Manager paybacks per center in a UTC created_at window — the
        collections line `paid_back_by_managers` (money the center paid out
        to customers; it reduces what the center collected)."""
        match: dict = {}
        if window:
            match["created_at"] = window
        if service_center_id is not None:
            match["service_center_id"] = service_center_id
        rows = await self.db.customer_paybacks.aggregate([
            {"$match": match},
            {"$group": {
                "_id": "$service_center_id",
                "amount": {"$sum": "$amount"},
                "count": {"$sum": 1},
                "wallet_amount": {"$sum": "$wallet_amount"},
                "goodwill_amount": {"$sum": "$goodwill_amount"},
                "cash_amount": {"$sum": {"$cond": [{"$eq": ["$method", "cash"]}, "$amount", 0]}},
            }},
        ]).to_list(length=1000)
        return {
            r["_id"]: {
                "amount": r2(r.get("amount")), "count": int(r.get("count") or 0),
                "wallet_amount": r2(r.get("wallet_amount")), "goodwill_amount": r2(r.get("goodwill_amount")),
                "cash_amount": r2(r.get("cash_amount")), "online_amount": r2(float(r.get("amount") or 0) - float(r.get("cash_amount") or 0)),
            }
            for r in rows
        }

    async def staff_view(self, customer_id: str, actor_role: str, actor_center_id: str | None, page: int, page_size: int) -> dict:
        """A customer's wallet for staff. An admin sees every entry in full.
        A manager (customer known to their center) sees the whole ledger —
        amount, kind, date, balance after — but an entry tied to ANOTHER
        center's booking shows "Other Center" instead of that booking's
        number, link, note, reason, reference and who did it. Every entry
        carries `own_center`."""
        from app.core.authz import ensure_customer_in_scope

        await self._customer_or_404(customer_id)
        await ensure_customer_in_scope(self.db, actor_role, actor_center_id, customer_id)
        rows, total = await self._ledger_rows(customer_id, page, page_size)
        centers: dict[str, str | None] = {}
        if actor_role != "admin":
            ids = [ObjectId(b) for b in {r.get("booking_id") for r in rows if r.get("booking_id")} if ObjectId.is_valid(b)]
            if ids:
                centers = {
                    str(b["_id"]): b.get("service_center_id")
                    for b in await self.db.bookings.find({"_id": {"$in": ids}}, {"service_center_id": 1}).to_list(length=len(ids))
                }
        items = []
        for row in rows:
            view = self.entry_view(row)
            if actor_role == "admin":
                own = True
            else:
                where = centers.get(row.get("booking_id") or "") if row.get("booking_id") else (row.get("meta") or {}).get("service_center_id")
                own = not where or where == actor_center_id
            view["own_center"] = own
            view["booking_label"] = view.get("booking_number") if own else OTHER_CENTER
            if not own:
                for field in ("booking_id", "booking_number", "note", "reference", "reason", "reason_label", "actor_name"):
                    view[field] = None
            items.append(view)
        return {**await self.summary(customer_id), "items": items, "total": total, "page": page, "page_size": page_size}

    # -- messages -----------------------------------------------------------

    async def announce(self, customer_id: str, entry: dict, reason: str | None = None) -> None:
        """In-app + WhatsApp (wallet_credited / wallet_debited: name, amount,
        reason, balance). After the money moved; best effort."""
        try:
            amount = r2(entry.get("amount"))
            if abs(amount) < 0.005:
                return
            from app.services.notification_service import NotificationService

            name = ((await self._customer(customer_id)) or {}).get("full_name") or "there"
            first = name.split(" ")[0]
            reason = reason or entry.get("label") or KIND_LABELS.get(entry.get("kind"), "Wallet")
            if entry.get("booking_number"):
                reason = f"{reason} ({entry['booking_number']})"
            balance = r2(entry.get("balance"))
            credit = amount > 0
            title = "Wallet Credited" if credit else "Wallet Debited"
            message = (
                f"₹{abs(amount):g} {'added to' if credit else 'taken from'} your Blussit wallet — {reason}. "
                f"Balance ₹{balance:g}."
            )
            await NotificationService(self.db).notify(
                customer_id, title, message, NotificationType.SYSTEM, entry.get("booking_id"),
                wa_event="wallet_credited" if credit else "wallet_debited",
                # NOTIFY's template: [first name, amount (plain number),
                # reason, balance with ₹ ("₹250" / "-₹50")].
                wa_params=[first, f"{abs(amount):g}", reason, f"₹{balance:g}" if balance >= 0 else f"-₹{-balance:g}"],
                background=True,
            )
        except Exception:  # noqa: BLE001
            logger.exception("Could not announce wallet entry for customer %s", customer_id)

    @staticmethod
    def wallet_line(amount: float, balance: float) -> str:
        """The line booking messages carry ("₹250 added to your wallet…")."""
        amount, balance = r2(amount), r2(balance)
        shown = f"₹{balance:g}" if balance >= 0 else f"−₹{-balance:g}, added to your next booking"
        if amount > 0:
            return f"₹{amount:g} added to your Blussit wallet (balance {shown})."
        if amount < 0:
            return f"₹{-amount:g} charged to your Blussit wallet (balance {shown})."
        return ""

    # -- helpers ------------------------------------------------------------

    async def _customer(self, customer_id: str) -> dict | None:
        if not ObjectId.is_valid(customer_id or ""):
            return None
        return await self.db.users.find_one({"_id": ObjectId(customer_id)}, {"full_name": 1, "phone": 1, "role": 1, "is_deleted": 1})

    async def _customer_or_404(self, customer_id: str) -> dict:
        user = await self._customer(customer_id)
        if not user or user.get("role") != "customer":
            raise NotFoundException("Customer not found")
        return user

    async def _name(self, user_id: str | None) -> str | None:
        if not user_id or not ObjectId.is_valid(user_id):
            return None
        u = await self.db.users.find_one({"_id": ObjectId(user_id)}, {"full_name": 1})
        return (u or {}).get("full_name")

    async def _audit(self, actor: dict, action: str, customer_id: str, details: dict, center: str | None) -> None:
        try:
            from app.services.audit_service import AuditService

            await AuditService(self.db).log_action(
                actor.get("id") or "system", actor.get("role") or "system", action, "customer_wallets", customer_id,
                {"customer_id": customer_id, **details}, service_center_id=center,
            )
        except Exception:  # noqa: BLE001
            logger.exception("Could not audit wallet action %s for %s", action, customer_id)
