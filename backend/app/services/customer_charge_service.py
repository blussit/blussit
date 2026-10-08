"""
Customer account charges — the late-cancellation charge (founder,
2026-10-07).

A customer who cancels LATE owes a charge from the booking policy table:

    more than CUSTOMER_CANCEL_LOCK_HOURS (4 h) before the slot    free
    1–4 hours before                                             ₹50
    less than an hour before (or after the slot started)         ₹80
    after the captain left for the address                       ₹100

(amounts: booking policy cancellation_fee_* keys, admin-editable). Only a
cancellation the customer asked for is charged: business/operational
cancels, payment-window expiry, captain release, system and
society-schedule cancels never are.

WALLET MODEL (founder 2026-10-07, spec §1.1/1.2): `customer_charges` stays
the RECORD of the charge (tier, amount, reduce/waive history); the MONEY is
settled through the customer wallet. The cancel transaction creates the
charge (status "settled") and MoneyService.on_booking_cancelled nets it
against what was paid (a credit of paid − charge, or a debit). A negative
wallet balance is then carried into the next booking by
MoneyService.apply_wallet_at_create.

Lifecycle of one charge (one per cancelled VISIT — `visit_key`):
  settled  — created in the same transaction as the cancel that caused it,
             charged to the wallet there;
  reduced / waived — a manager (own center) or admin lowers it, never
             raises it: the reduction is CREDITED to the wallet, in one
             transaction with the charge's guarded write.
Legacy states (the old "added to your next booking" model): "open" charges
are moved onto the wallet at boot (money_service.migrate_open_charges_to_wallet);
"applied" ones stay on the booking that carries them — reduced on it as
before, or charged to the wallet if that booking is cancelled unpaid.
Every step is a history row on the charge; creation and adjustments are
audit-logged with before/after.
"""
import logging
from datetime import datetime, timedelta, timezone

from bson import ObjectId
from pymongo import ReturnDocument

from app.core.authz import manager_center_or_raise
from app.core.exceptions import BadRequestException, ForbiddenException, NotFoundException
from app.models.enums import (
    BookingStatus,
    CancellationChargeTier,
    CustomerChargeStatus,
    NotificationType,
    PaymentMethod,
    PaymentStatus,
)
from app.utils.money import round_rupees
from app.utils.serializers import serialize_doc
from app.utils.timezone import now_ist

logger = logging.getLogger(__name__)

KIND_LATE_CANCELLATION = "late_cancellation"

# Tier -> the booking-policy key holding its amount, and that key's default.
TIER_POLICY_KEYS = {
    CancellationChargeTier.ONE_TO_FOUR_HOURS.value: ("cancellation_fee_1_to_4h", 50),
    CancellationChargeTier.UNDER_ONE_HOUR.value: ("cancellation_fee_under_1h", 80),
    CancellationChargeTier.AFTER_CAPTAIN_LEFT.value: ("cancellation_fee_after_captain_left", 100),
}
TIER_LABELS = {
    CancellationChargeTier.FREE.value: "more than the free-cancellation window before the slot",
    CancellationChargeTier.ONE_TO_FOUR_HOURS.value: "1–4 hours before the slot",
    CancellationChargeTier.UNDER_ONE_HOUR.value: "less than an hour before the slot",
    CancellationChargeTier.AFTER_CAPTAIN_LEFT.value: "after the captain had left for the address",
}
_CAPTAIN_LEFT = frozenset({BookingStatus.CAPTAIN_ON_THE_WAY.value, BookingStatus.SERVICE_STARTED.value})
# A charge waits on its carrying booking only while that booking could
# still be paid — a booking DONE and PAID keeps its charge for good.
_CLOSED = (BookingStatus.COMPLETED.value, BookingStatus.CANCELLED.value)


def late_cancellation_quote(cars: list[dict], policy: dict, now: datetime | None = None) -> dict:
    """THE single source of truth for what cancelling these cars (one
    booking, or every live car of a visit) costs right now — the preview,
    the customer's own cancel, the bot's and the staff cancel all read it.

    The tier is the visit's: its slot start (all cars share it) and whether
    the captain has left (ANY car on the way or in service). A visit that
    was never confirmed (every car still awaiting its online payment) is
    free — no job was ever booked for it. The free window is the same lock
    the customer's own online cancel uses (CUSTOMER_CANCEL_LOCK_HOURS): at
    or before slot − 4 h is free, at or before slot − 1 h is the 1–4 h tier,
    later (including after the slot started) is the under-1 h tier.

    Returns {tier, amount, captain_left, minutes_to_slot, window_hours}."""
    from app.services.booking_service import CUSTOMER_CANCEL_LOCK_HOURS, _booking_window

    now = now or now_ist()
    live = [c for c in cars if c] or [{}]
    lead = min(live, key=lambda c: int(c.get("group_offset_minutes") or 0))
    slot_start, _slot_end = _booking_window(lead)
    captain_left = any(c.get("status") in _CAPTAIN_LEFT for c in live)
    confirmed = any(c.get("status") != BookingStatus.AWAITING_PAYMENT.value for c in live)
    if captain_left:
        tier = CancellationChargeTier.AFTER_CAPTAIN_LEFT.value
    elif not confirmed or now <= slot_start - timedelta(hours=CUSTOMER_CANCEL_LOCK_HOURS):
        tier = CancellationChargeTier.FREE.value
    elif now <= slot_start - timedelta(hours=1):
        tier = CancellationChargeTier.ONE_TO_FOUR_HOURS.value
    else:
        tier = CancellationChargeTier.UNDER_ONE_HOUR.value
    amount = 0
    if tier in TIER_POLICY_KEYS:
        key, default = TIER_POLICY_KEYS[tier]
        amount = max(0, round_rupees(policy.get(key, default)))
    return {
        "tier": tier,
        "amount": float(amount),
        "captain_left": captain_left,
        "minutes_to_slot": int((slot_start - now).total_seconds() // 60),
        "window_hours": CUSTOMER_CANCEL_LOCK_HOURS,
    }


def _history(action: str, actor: dict, *, frm: float | None = None, to: float | None = None, note: str | None = None) -> dict:
    return {
        "at": datetime.now(timezone.utc),
        "action": action,
        "by": actor.get("id"),
        "by_name": actor.get("name"),
        "role": actor.get("role"),
        "from": frm,
        "to": to,
        "note": note,
    }


class CustomerChargeService:
    def __init__(self, db):
        self.db = db
        self.collection = db.customer_charges

    # -- who did it ------------------------------------------------------

    async def actor(self, actor_id: str | None, actor_role: str) -> dict:
        """{id, role, name} for history rows — the name looked up once."""
        name = "System" if actor_role == "system" or actor_id == "system" else None
        if name is None and actor_id and ObjectId.is_valid(actor_id):
            user = await self.db.users.find_one({"_id": ObjectId(actor_id)}, {"full_name": 1})
            name = (user or {}).get("full_name")
        return {"id": actor_id, "role": actor_role, "name": name or actor_role.title()}

    # -- reads ------------------------------------------------------------

    async def open_charges(self, customer_id: str | None, session=None) -> list[dict]:
        if not customer_id:
            return []
        return await self.collection.find(
            {"customer_id": customer_id, "status": CustomerChargeStatus.OPEN.value}, session=session
        ).sort("created_at", 1).to_list(length=50)

    async def open_total(self, customer_id: str | None) -> float:
        """What the customer's next booking will carry — the quote's
        cancellation_charge."""
        return round(sum(float(c.get("amount") or 0) for c in await self.open_charges(customer_id)), 2)

    # -- created by a late cancel (inside the cancel's transaction) -------

    async def record_late_cancellation(
        self, session, *, cars: list[dict], visit_key: str, tier: str, amount: float, original_amount: float, actor: dict,
    ) -> dict | None:
        """One charge per cancelled visit, written in the cancel's own
        transaction. Upserted on the visit key, so a replayed/double cancel
        can never create a second one. Returns the new charge, or None when
        the visit already had one."""
        lead = min(cars, key=lambda c: int(c.get("group_offset_minutes") or 0))
        numbers = " + ".join(str(c.get("booking_number") or "") for c in sorted(cars, key=lambda c: int(c.get("group_offset_minutes") or 0)))
        now = datetime.now(timezone.utc)
        charge_id = ObjectId()
        amount = float(round_rupees(amount))
        doc = {
            "_id": charge_id,
            "customer_id": lead["customer_id"],
            "service_center_id": lead.get("service_center_id"),
            "kind": KIND_LATE_CANCELLATION,
            "visit_key": visit_key,
            "source_booking_id": str(lead["_id"]),
            "source_booking_number": numbers,
            "source_booking_ids": [str(c["_id"]) for c in cars],
            "tier": tier,
            "amount": amount,
            "original_amount": float(round_rupees(original_amount)),
            # Charged to the wallet by MoneyService.on_booking_cancelled in
            # this same transaction (the caller passes this amount).
            "status": CustomerChargeStatus.SETTLED.value,
            "settled_via": "wallet",
            "applied_to_booking_id": None,
            "applied_to_booking_number": None,
            "created_at": now,
            "updated_at": now,
            "created_by": actor.get("id"),
            "created_by_role": actor.get("role"),
            "created_by_name": actor.get("name"),
            "history": [_history("created", actor, to=amount, note=f"Late cancellation of {numbers} ({TIER_LABELS.get(tier, tier)})")],
        }
        result = await self.collection.update_one(
            {"visit_key": visit_key, "kind": KIND_LATE_CANCELLATION}, {"$setOnInsert": doc}, upsert=True, session=session,
        )
        return doc if result.upserted_id == charge_id else None

    # -- applied at the next booking (inside its create transaction) ------

    async def claim_open(self, session, *, customer_id: str, booking_id: str, booking_number: str) -> list[dict]:
        """Every open charge of this customer moves onto this booking. Each
        is claimed with a guarded write (status still "open") inside the
        create transaction: a concurrent booking of the same customer that
        took it first makes this one conflict and retry, then see it gone."""
        claimed: list[dict] = []
        system = {"id": "system", "role": "system", "name": "System"}
        for row in await self.open_charges(customer_id, session=session):
            done = await self.collection.find_one_and_update(
                {"_id": row["_id"], "status": CustomerChargeStatus.OPEN.value},
                {
                    "$set": {
                        "status": CustomerChargeStatus.APPLIED.value,
                        "applied_to_booking_id": booking_id,
                        "applied_to_booking_number": booking_number,
                        "updated_at": datetime.now(timezone.utc),
                    },
                    "$push": {"history": _history("applied", system, frm=row.get("amount"), to=row.get("amount"), note=f"Added to {booking_number}")},
                },
                return_document=ReturnDocument.AFTER,
                session=session,
            )
            if done is not None:
                claimed.append(done)
        return claimed

    # -- released when its booking goes away (inside that transaction) ----

    @staticmethod
    def keeps_charges(booking: dict) -> bool:
        """A booking that was done AND paid keeps what it carried — the
        money was collected; releasing it would charge the customer twice."""
        return booking.get("status") == BookingStatus.COMPLETED.value and booking.get("payment_status") == PaymentStatus.PAID.value

    async def release_for_booking(self, session, booking: dict, *, actor: dict | None = None, reason: str = "cancelled") -> list[str]:
        """Legacy charges this booking carried (the old "added to your next
        booking" model, status "applied") leave it — it was cancelled or
        deleted before it was paid — and are settled through the customer
        wallet: status applied → settled (guarded) plus a wallet debit with
        the stable key `charge:{id}` (the same key
        migrate_open_charges_to_wallet uses), so a replay or a later
        migration can never debit twice. Nothing goes back to "open" — no
        booking claims open charges any more. Call inside the cancel/delete
        transaction."""
        if not float(booking.get("cancellation_charge") or 0) and not booking.get("cancellation_charge_ids"):
            return []
        if self.keeps_charges(booking):
            return []
        booking_id = str(booking.get("_id") or booking.get("id"))
        actor = actor or {"id": "system", "role": "system", "name": "System"}
        rows = await self.collection.find(
            {"applied_to_booking_id": booking_id, "status": CustomerChargeStatus.APPLIED.value},
            {"_id": 1, "amount": 1, "customer_id": 1, "source_booking_id": 1, "source_booking_number": 1}, session=session,
        ).to_list(length=50)
        released: list[str] = []
        from app.services.customer_wallet_service import CustomerWalletService

        wallet = CustomerWalletService(self.db)
        for row in rows:
            # Wallet model: the charge leaves the booking and is charged to
            # the customer's wallet (it is carried into the next booking
            # from there), in this same transaction.
            done = await self.collection.update_one(
                {"_id": row["_id"], "status": CustomerChargeStatus.APPLIED.value, "applied_to_booking_id": booking_id},
                {
                    "$set": {"status": CustomerChargeStatus.SETTLED.value, "settled_via": "wallet", "wallet_key": f"charge:{row['_id']}",
                             "applied_to_booking_id": None, "applied_to_booking_number": None, "updated_at": datetime.now(timezone.utc)},
                    "$push": {"history": _history(
                        "released", actor, frm=row.get("amount"), to=row.get("amount"),
                        note=f"{booking.get('booking_number')} {reason} — charged to the wallet",
                    )},
                },
                session=session,
            )
            if done.modified_count:
                released.append(str(row["_id"]))
                if float(row.get("amount") or 0) > 0:
                    await wallet.post(
                        row["customer_id"], -float(row["amount"]), "cancellation_charge", key=f"charge:{row['_id']}",
                        booking_id=row.get("source_booking_id"), booking_number=row.get("source_booking_number"),
                        actor_id=actor.get("id"), actor_role=actor.get("role"), session=session,
                        note=f"Late cancellation of {row.get('source_booking_number')} (was on {booking.get('booking_number')})",
                    )
        return released

    async def reclaim_for_booking(self, session, booking: dict) -> tuple[float, list[str]]:
        """A restored booking takes back the charges it carried — each only
        if it is still open (a charge since applied elsewhere or waived is
        not taken again). Returns (the amount it now carries, the ids).
        Call inside the restore transaction."""
        booking_id = str(booking["_id"])
        number = booking.get("booking_number")
        system = {"id": "system", "role": "system", "name": "System"}
        total = 0.0
        ids: list[str] = []
        for charge_id in booking.get("cancellation_charge_ids") or []:
            if not ObjectId.is_valid(charge_id):
                continue
            done = await self.collection.find_one_and_update(
                {"_id": ObjectId(charge_id), "status": CustomerChargeStatus.OPEN.value, "customer_id": booking.get("customer_id")},
                {
                    "$set": {"status": CustomerChargeStatus.APPLIED.value, "applied_to_booking_id": booking_id,
                             "applied_to_booking_number": number, "updated_at": datetime.now(timezone.utc)},
                    "$push": {"history": _history("applied", system, note=f"{number} restored — added back to it")},
                },
                return_document=ReturnDocument.AFTER,
                session=session,
            )
            if done is not None:
                total += float(done.get("amount") or 0)
                ids.append(charge_id)
        return round(total, 2), ids

    # -- staff: reduce / waive --------------------------------------------

    async def _load_for_staff(self, charge_id: str, actor_role: str, actor_center_id: str | None) -> dict:
        if actor_role not in ("manager", "admin"):
            raise ForbiddenException("Only a manager or admin can change a charge.")
        charge = await self.collection.find_one({"_id": ObjectId(charge_id)}) if ObjectId.is_valid(charge_id or "") else None
        # A manager sees only their own center's charges — anything else is
        # "not found", never "forbidden" (an id must not confirm it exists).
        if not charge or (actor_role == "manager" and (not actor_center_id or charge.get("service_center_id") != actor_center_id)):
            raise NotFoundException("Charge not found")
        return charge

    async def adjust(
        self, charge_id: str, amount: float, note: str | None, *, actor_id: str, actor_role: str, actor_center_id: str | None,
    ) -> dict:
        """Reduce (or, at 0, waive) a charge. Never raises it. On a charge
        settled through the wallet, the reduction is credited back to the
        wallet. On a (legacy) charge already added to an unpaid, still-open booking, that booking's
        cancellation_charge / total / platform earning drop with it in ONE
        transaction, then its open pay links are voided (they were for the
        old total). Refused once the booking that carries it is paid or done
        — that money is a refund matter."""
        charge = await self._load_for_staff(charge_id, actor_role, actor_center_id)
        amount = float(round_rupees(amount))
        current = float(charge.get("amount") or 0)
        if charge.get("status") == CustomerChargeStatus.WAIVED.value:
            raise BadRequestException("This charge was already removed.")
        if amount > current + 0.004:
            raise BadRequestException(f"A charge can only be reduced — it is ₹{current:g} now.")
        if abs(amount - current) < 0.005:
            return await self.view(charge)
        actor = await self.actor(actor_id, actor_role)
        new_status = CustomerChargeStatus.WAIVED.value if amount <= 0 else charge["status"]
        action = "waived" if amount <= 0 else "reduced"
        delta = round(current - amount, 2)
        charge_update = {
            "$set": {"amount": amount, "status": new_status, "updated_at": datetime.now(timezone.utc)},
            "$push": {"history": _history(action, actor, frm=current, to=amount, note=note)},
        }
        charge_guard = {"_id": charge["_id"], "status": charge["status"], "amount": charge.get("amount")}
        booking_after: dict | None = None
        wallet_entry: dict | None = None

        if charge.get("status") == CustomerChargeStatus.SETTLED.value:
            # Already charged to the wallet: the reduction goes back to it,
            # in one transaction with the guarded charge write (a double
            # click or two managers reduce it once).
            from app.services.customer_wallet_service import CustomerWalletService

            wallet = CustomerWalletService(self.db)
            step = len(charge.get("history") or [])
            lowered: list[dict] = []
            # What went back to the wallet, kept on the charge itself
            # (view → wallet_credited) — same write as the history row.
            charge_update["$inc"] = {"wallet_credited": delta}

            async def _settled(session):
                nonlocal wallet_entry
                lowered.clear()
                done = await self.collection.find_one_and_update(charge_guard, charge_update, return_document=ReturnDocument.AFTER, session=session)
                if done is None:
                    raise BadRequestException("This charge just changed — refresh and try again.")
                # The debt may be riding on a booking not paid yet
                # (wallet_due_carried): that booking's price drops by the
                # reduction (up to what it carries) and its reservation is
                # released — the customer sees the lower price there instead
                # of paying it and getting credit back later.
                await self._lower_carriers(session, charge["customer_id"], delta, lowered)
                wallet_entry = await wallet.post(
                    charge["customer_id"], delta, "charge_reduced", key=f"charge_adjust:{charge['_id']}:{step}",
                    booking_id=charge.get("source_booking_id"), booking_number=charge.get("source_booking_number"),
                    actor_id=actor.get("id"), actor_role=actor.get("role"), actor_name=actor.get("name"), session=session,
                    note=f"Cancellation charge for {charge.get('source_booking_number')} {action}: ₹{current:g} → ₹{amount:g}"
                    + (f" — {note}" if note else ""),
                )
                return done

            async with await self.db.client.start_session() as session:
                updated = await session.with_transaction(_settled)
        elif charge.get("status") == CustomerChargeStatus.APPLIED.value and charge.get("applied_to_booking_id"):
            booking_id = charge["applied_to_booking_id"]
            booking = await self.db.bookings.find_one({"_id": ObjectId(booking_id)}) if ObjectId.is_valid(booking_id) else None
            if booking and (booking.get("payment_status") == PaymentStatus.PAID.value or booking.get("status") == BookingStatus.COMPLETED.value):
                raise BadRequestException(
                    f"This charge was already paid with {booking.get('booking_number')} — refund it from Payments instead."
                )

            async def _do(session):
                nonlocal booking_after
                fresh = await self.db.bookings.find_one({"_id": ObjectId(booking_id)}, session=session) if booking else None
                if fresh is not None:
                    if fresh.get("payment_status") == PaymentStatus.PAID.value or fresh.get("status") == BookingStatus.COMPLETED.value:
                        raise BadRequestException(
                            f"This charge was already paid with {fresh.get('booking_number')} — refund it from Payments instead."
                        )
                    if fresh.get("status") != BookingStatus.CANCELLED.value and not fresh.get("is_deleted"):
                        carried = round(float(fresh.get("cancellation_charge") or 0) - delta, 2)
                        total = round(float(fresh.get("total_amount") or 0) - delta, 2)
                        fields = {
                            "cancellation_charge": max(0.0, carried),
                            "total_amount": max(0.0, total),
                            "platform_earning": round(float(fresh.get("platform_earning") or 0) - delta, 2),
                            "updated_at": datetime.now(timezone.utc),
                        }
                        # A plan wash that now owes nothing is paid by its plan
                        # again, exactly as at creation.
                        if fields["total_amount"] <= 0 and fresh.get("payment_method") == PaymentMethod.SUBSCRIPTION.value:
                            fields["payment_status"] = PaymentStatus.PAID.value
                        booking_after = await self.db.bookings.find_one_and_update(
                            {"_id": fresh["_id"], "total_amount": fresh.get("total_amount"),
                             "payment_status": fresh.get("payment_status"), "status": fresh.get("status")},
                            {"$set": fields},
                            return_document=ReturnDocument.AFTER,
                            session=session,
                        )
                        if booking_after is None:
                            raise BadRequestException("That booking just changed — refresh and try again.")
                done = await self.collection.find_one_and_update(charge_guard, charge_update, return_document=ReturnDocument.AFTER, session=session)
                if done is None:
                    raise BadRequestException("This charge just changed — refresh and try again.")
                return done

            async with await self.db.client.start_session() as session:
                updated = await session.with_transaction(_do)
            if booking_after is not None:
                await self._after_booking_reduced(booking_after, action)
        else:
            updated = await self.collection.find_one_and_update(charge_guard, charge_update, return_document=ReturnDocument.AFTER)
            if updated is None:
                raise BadRequestException("This charge just changed — refresh and try again.")

        await self._audit(actor, "ADJUST_CUSTOMER_CHARGE", updated, before=charge, note=note)
        if wallet_entry is not None and lowered:
            # Post-commit, like MoneyService.after_price_change: the links
            # for the old amount are voided, a booking left with nothing to
            # pay is confirmed; the customer hears the new price.
            from app.services.money_service import MoneyService

            await MoneyService(self.db).after_price_change(
                [{"booking_id": r["booking_id"], "amount_due": r["amount_due"]} for r in lowered], f"Cancellation charge {action}",
            )
            for r in lowered:
                await self._broadcast(r["doc"])
            await self._notify_lowered(updated, current, lowered)
        elif wallet_entry is not None:
            from app.services.customer_wallet_service import CustomerWalletService

            await CustomerWalletService(self.db).announce(charge["customer_id"], wallet_entry)
        else:
            await self._notify_adjusted(updated, current)
        return await self.view(updated)

    async def _lower_carriers(self, session, customer_id: str, reduction: float, lowered: list[dict]) -> None:
        """Inside the adjust transaction: take `reduction` off the previous
        balance carried by this customer's unpaid (or part-paid) live
        bookings — oldest first, never more than each carries — and release
        that much of the wallet's reservation. A carrier paid a moment
        before (its debt cleared) is no longer one: the reduction then stays
        plain wallet credit. Writes are guarded on the booking's money
        revision, so a payment racing this conflicts and the transaction
        retries against the paid booking."""
        from app.services import booking_money as bm
        from app.services.customer_wallet_service import CustomerWalletService

        remaining = round(float(reduction or 0), 2)
        if remaining <= 0:
            return
        wallet = CustomerWalletService(self.db)
        carriers = await self.db.bookings.find(
            {"customer_id": customer_id, "wallet_due_carried": {"$gt": 0}, "wallet_due_cleared": {"$ne": True},
             "status": {"$ne": BookingStatus.CANCELLED.value}, "is_deleted": {"$ne": True}},
            session=session,
        ).sort("created_at", 1).to_list(length=20)
        now = datetime.now(timezone.utc)
        for car in carriers:
            if remaining <= 0:
                break
            if car.get("cancel_money_settled") or bm.money_view(car)["payment_status"] in ("refund_due", "refunded"):
                continue
            carried = bm.r2(car.get("wallet_due_carried"))
            shift = bm.r2(min(remaining, carried))
            if shift <= 0:
                continue
            new_carried = bm.r2(carried - shift)
            new_total = bm.r2(bm.total_of(car) - shift)
            applied = bm.wallet_applied_of(car)
            paid = bm.amount_paid_of(car)
            fields: dict = {
                "wallet_due_carried": new_carried,
                "total_amount": new_total,
                "platform_earning": bm.r2(float(car.get("platform_earning") or 0) - shift),
                "money_rev": int(car.get("money_rev") or 0) + 1,
                "updated_at": now,
            }
            await wallet.release_carry(customer_id, shift, session)
            excess = bm.r2(applied + paid - new_total)
            if excess > bm.EPSILON:
                # Part-paid beyond the new price: the extra is wallet credit.
                paid = bm.r2(paid - excess)
                fields["amount_paid"] = paid
                await wallet.post(
                    customer_id, excess, "overpayment", key=f"carry_cut_over:{car['_id']}:{fields['money_rev']}",
                    booking_id=str(car["_id"]), booking_number=car.get("booking_number"), session=session,
                    note=f"{car.get('booking_number')} costs less after the charge was reduced",
                )
            if new_carried > 0 and paid + bm.EPSILON >= new_carried:
                # What it already received now covers the smaller carried
                # balance: settle that, as a payment would have.
                await wallet.release_carry(customer_id, new_carried, session)
                await wallet.post(
                    customer_id, new_carried, "previous_balance_paid", key=f"carry_paid:{car['_id']}",
                    booking_id=str(car["_id"]), booking_number=car.get("booking_number"), session=session,
                    note=f"Previous balance paid with {car.get('booking_number')}",
                )
                fields.update({"wallet_due_cleared": True, "wallet_due_cleared_at": now})
            status = bm.status_for(new_total, applied, paid)
            fields["payment_status"] = status
            fields["amount_due"] = max(0.0, bm.r2(new_total - applied - paid))
            done = await self.db.bookings.find_one_and_update(
                {"_id": car["_id"], "money_rev": car.get("money_rev"), "wallet_due_cleared": {"$ne": True},
                 "status": {"$ne": BookingStatus.CANCELLED.value}},
                {"$set": fields}, return_document=ReturnDocument.AFTER, session=session,
            )
            if done is None:
                raise BadRequestException("That booking just changed — refresh and try again.")
            lowered.append({"booking_id": str(car["_id"]), "amount_due": fields["amount_due"], "shift": shift, "doc": done})
            remaining = bm.r2(remaining - shift)

    async def _notify_lowered(self, charge: dict, before_amount: float, lowered: list[dict]) -> None:
        amount = float(charge.get("amount") or 0)
        ref = charge.get("source_booking_number")
        cut = round(sum(r["shift"] for r in lowered), 2)
        numbers = ", ".join(str(r["doc"].get("booking_number")) for r in lowered)
        head = (f"Good news — the late-cancellation charge for {ref} has been removed."
                if amount <= 0 else f"The late-cancellation charge for {ref} is now ₹{amount:g} (was ₹{before_amount:g}).")
        try:
            from app.services.notification_service import NotificationService

            await NotificationService(self.db).notify(
                charge["customer_id"], "Cancellation charge updated",
                f"{head} {numbers} now costs ₹{cut:g} less.", NotificationType.BOOKING, lowered[0]["booking_id"], background=True,
            )
        except Exception:  # noqa: BLE001
            logger.exception("Could not tell the customer about charge %s", charge.get("_id"))

    async def _after_booking_reduced(self, booking: dict, action: str) -> None:
        """The booking's old total is gone: void the pay links made for it
        and, if nothing is left to pay on an unpaid booking, confirm it."""
        booking_id = str(booking["_id"])
        try:
            from app.services.payment_service import PaymentService

            await PaymentService(self.db).void_open_links([booking_id], f"Cancellation charge {action}")
        except Exception:  # noqa: BLE001 — never raises by contract; belt and braces
            logger.exception("Could not void pay links for %s", booking.get("booking_number"))
        if booking.get("status") != BookingStatus.AWAITING_PAYMENT.value or float(booking.get("total_amount") or 0) > 0:
            await self._broadcast(booking)
            return
        try:
            from app.services.booking_service import BookingService

            service = BookingService(self.db)
            if booking.get("booking_group_id"):
                await service._confirm_parked_cars_of(booking)
            else:
                await service.confirm_awaiting_payment_booking(booking_id, "Confirmed — nothing left to pay after the cancellation charge was removed")
        except Exception:  # noqa: BLE001 — the reduction stands; the queue picks it up
            logger.exception("Could not confirm %s after its charge was removed", booking.get("booking_number"))

    async def _broadcast(self, booking: dict) -> None:
        try:
            from app.services.booking_service import BookingService

            await BookingService(self.db)._broadcast_booking_changed(booking)
        except Exception:  # noqa: BLE001
            logger.exception("Could not broadcast booking %s", booking.get("booking_number"))

    # -- audit + messages (after commit, best effort) ----------------------

    async def _audit(self, actor: dict, action: str, after: dict, *, before: dict | None = None, note: str | None = None) -> None:
        def money(c: dict | None) -> dict | None:
            if not c:
                return None
            return {k: c.get(k) for k in ("amount", "status", "tier", "applied_to_booking_number")}

        try:
            from app.services.audit_service import AuditService

            await AuditService(self.db).log_action(
                actor.get("id") or "system", actor.get("role") or "system", action, "customer_charges", str(after["_id"]),
                {
                    "customer_id": after.get("customer_id"),
                    "source_booking_number": after.get("source_booking_number"),
                    "before": money(before),
                    "after": money(after),
                    "note": note,
                },
                service_center_id=after.get("service_center_id"),
            )
        except Exception:  # noqa: BLE001 — the change stands
            logger.exception("Could not audit-log charge %s", after.get("_id"))

    async def audit_created(self, charge: dict, actor: dict) -> None:
        """After the cancel committed: the CREATE_CUSTOMER_CHARGE audit row
        (best effort). The customer hears about the charge in the cancel
        message's wallet line, so this sends nothing."""
        await self._audit(actor, "CREATE_CUSTOMER_CHARGE", charge)

    async def announce_created(self, charge: dict, actor: dict) -> None:
        """After the cancel committed: audit row + an in-app note to the
        customer. Wallet model: the charge was netted against what was paid
        or debited on the wallet in the cancel itself — only a NEGATIVE
        balance is added to the next booking, so the note says "charged to
        your wallet", not "added to your next booking"."""
        await self.audit_created(charge, actor)
        try:
            from app.services.notification_service import NotificationService

            await NotificationService(self.db).notify(
                charge["customer_id"],
                "Late cancellation charge",
                f"A late-cancellation charge of ₹{float(charge['amount']):g} was charged to your Blussit wallet "
                f"({charge.get('source_booking_number')} was cancelled {TIER_LABELS.get(charge.get('tier'), '')}).",
                NotificationType.BOOKING,
                # Opens the booking that was cancelled late.
                charge.get("source_booking_id"),
                background=True,
            )
        except Exception:  # noqa: BLE001
            logger.exception("Could not tell the customer about charge %s", charge.get("_id"))

    async def _notify_adjusted(self, charge: dict, before_amount: float) -> None:
        amount = float(charge.get("amount") or 0)
        ref = charge.get("source_booking_number")
        message = (
            f"Good news — the late-cancellation charge for {ref} has been removed."
            if amount <= 0 else
            f"The late-cancellation charge for {ref} is now ₹{amount:g} (was ₹{before_amount:g})."
        )
        try:
            from app.services.notification_service import NotificationService

            await NotificationService(self.db).notify(
                charge["customer_id"], "Cancellation charge updated", message, NotificationType.BOOKING,
                # The booking it now sits on, else the one cancelled late.
                charge.get("applied_to_booking_id") or charge.get("source_booking_id"), background=True,
            )
        except Exception:  # noqa: BLE001
            logger.exception("Could not tell the customer about charge %s", charge.get("_id"))

    # -- lists --------------------------------------------------------------

    async def view(self, charge: dict, *, users: dict | None = None, paid: dict | None = None) -> dict:
        doc = serialize_doc(charge)
        customer = (users or {}).get(charge.get("customer_id") or "")
        if users is None and charge.get("customer_id") and ObjectId.is_valid(charge["customer_id"]):
            customer = await self.db.users.find_one({"_id": ObjectId(charge["customer_id"])}, {"full_name": 1, "phone": 1})
        applied = charge.get("applied_to_booking_id")
        if paid is None and applied and ObjectId.is_valid(applied):
            b = await self.db.bookings.find_one({"_id": ObjectId(applied)}, {"payment_status": 1})
            paid = {applied: (b or {}).get("payment_status") == PaymentStatus.PAID.value}
        return {
            "id": doc["id"],
            "customer_id": charge.get("customer_id"),
            "customer_name": (customer or {}).get("full_name"),
            "customer_phone": (customer or {}).get("phone"),
            "service_center_id": charge.get("service_center_id"),
            "kind": charge.get("kind"),
            "source_booking_id": charge.get("source_booking_id"),
            "source_booking_number": charge.get("source_booking_number"),
            "tier": charge.get("tier"),
            "amount": float(charge.get("amount") or 0),
            "original_amount": float(charge.get("original_amount") or 0),
            "status": charge.get("status"),
            "applied_to_booking_id": applied,
            "applied_to_booking_number": charge.get("applied_to_booking_number"),
            "applied_booking_paid": bool((paid or {}).get(applied or "")),
            "created_at": doc.get("created_at"),
            "created_by_name": charge.get("created_by_name"),
            "created_by_role": charge.get("created_by_role"),
            "history": doc.get("history") or [],
            # Where the money went: "wallet" (charged to / credited back on
            # the customer wallet), "booking" (legacy: carried in a
            # booking's total), None (not charged anywhere — an old open
            # charge, or one waived before it was settled).
            "settlement": self.settlement_of(charge),
            "wallet_credited": self.wallet_credited_of(charge),
        }

    @staticmethod
    def settlement_of(charge: dict) -> str | None:
        if charge.get("settled_via") == "wallet":
            return "wallet"
        if charge.get("applied_to_booking_id"):
            return "booking"
        return None

    @staticmethod
    def wallet_credited_of(charge: dict) -> float:
        """₹ credited back to the wallet by reduce/waive. Stored since
        2026-10-07 (`wallet_credited`); for a row adjusted before that, the
        reductions made after it reached the wallet, read from history."""
        if charge.get("wallet_credited") is not None:
            return round(float(charge["wallet_credited"]), 2)
        if charge.get("settled_via") != "wallet":
            return 0.0
        history = charge.get("history") or []
        start = 0
        for index, row in enumerate(history):
            if row.get("action") in ("released", "moved_to_wallet"):
                start = index + 1
        credited = 0.0
        for row in history[start:]:
            if row.get("action") in ("reduced", "waived") and row.get("from") is not None and row.get("to") is not None:
                credited += float(row["from"]) - float(row["to"])
        return round(credited, 2)

    async def list_for_staff(
        self, *, actor_role: str, actor_center_id: str | None, status: str | None, customer_id: str | None, page: int, page_size: int,
    ) -> tuple[list[dict], int]:
        if actor_role not in ("manager", "admin"):
            raise ForbiddenException("Only a manager or admin can see charges.")
        query: dict = {}
        center = manager_center_or_raise(actor_role, actor_center_id)
        if center:
            query["service_center_id"] = center
        if status:
            query["status"] = status
        if customer_id:
            query["customer_id"] = customer_id
        total = await self.collection.count_documents(query)
        rows = await self.collection.find(query).sort("created_at", -1).skip(max(page - 1, 0) * page_size).limit(page_size).to_list(length=page_size)
        user_ids = [ObjectId(r["customer_id"]) for r in rows if ObjectId.is_valid(r.get("customer_id") or "")]
        users = {
            str(u["_id"]): u
            for u in await self.db.users.find({"_id": {"$in": user_ids}}, {"full_name": 1, "phone": 1}).to_list(length=len(user_ids))
        } if user_ids else {}
        applied = [ObjectId(r["applied_to_booking_id"]) for r in rows if ObjectId.is_valid(r.get("applied_to_booking_id") or "")]
        paid = {
            str(b["_id"]): b.get("payment_status") == PaymentStatus.PAID.value
            for b in await self.db.bookings.find({"_id": {"$in": applied}}, {"payment_status": 1}).to_list(length=len(applied))
        } if applied else {}
        return [await self.view(r, users=users, paid=paid) for r in rows], total

    async def my_charges(self, customer_id: str) -> dict:
        """The customer's charge records. `open_total` (legacy: what the next
        booking carried) is 0 once charges are settled through the wallet —
        the wallet balance (GET /wallet/me) is what the next booking carries."""
        rows = await self.collection.find({"customer_id": customer_id}).sort("created_at", -1).to_list(length=50)
        return {
            "open_total": round(sum(float(r.get("amount") or 0) for r in rows if r.get("status") == CustomerChargeStatus.OPEN.value), 2),
            "items": [
                {
                    "id": str(r["_id"]),
                    "amount": float(r.get("amount") or 0),
                    "tier": r.get("tier"),
                    "source_booking_number": r.get("source_booking_number"),
                    "created_at": serialize_doc({"created_at": r.get("created_at")})["created_at"],
                    "status": r.get("status"),
                    "applied_to_booking_number": r.get("applied_to_booking_number"),
                    "settlement": self.settlement_of(r),
                    "wallet_credited": self.wallet_credited_of(r),
                }
                for r in rows
            ],
        }
