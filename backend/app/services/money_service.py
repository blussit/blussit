"""
Booking money (spec docs/FEATURE_PLAN_WALLET_EDITS_PLANS_2026-10-07.md
§1.2, §1.5, §2 MONEY): every movement of a booking's money against the
customer wallet and the captain wallet goes through here.

  apply_wallet_at_create  inside the create transaction — a negative balance
                          rides on the first paying car (wallet_due_carried,
                          reserved on the wallet), a positive one is spent
                          (wallet_applied, debited now, guarded on balance).
  apply_payment           the ONE way money received (online order, payment
                          link, captain QR, cash) lands on bookings: split
                          over the cars' amount_due, idempotent on its key,
                          one transaction; anything beyond what is due is a
                          wallet credit (overpayment), never parked.
  on_booking_cancelled    inside the cancel transaction — credit what was
                          paid + wallet credit spent, minus the charge (a
                          debit when the charge is bigger).
  on_price_change         inside an edit / add-on transaction — recompute
                          due and status; an overpaid booking credits the
                          wallet; returns the fields to write.
  settle_captain_wallet   posts only the delta between what a booking should
                          leave on its captain's wallet (earning once done −
                          cash he holds) and what was already posted.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from bson import ObjectId
from pymongo import ReturnDocument

from app.core.exceptions import BadRequestException
from app.models.enums import CustomerWalletEntryKind as Kind
from app.services import booking_money as bm
from app.services.booking_money import EPSILON, r2
from app.services.customer_wallet_service import CustomerWalletService, in_transaction

logger = logging.getLogger(__name__)

CASH = "cash"
ONLINE = "online"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _oid(value) -> ObjectId | None:
    value = str(value or "")
    return ObjectId(value) if ObjectId.is_valid(value) else None


def received_by_method(booking: dict) -> tuple[float, float]:
    """(cash, online) received for this booking — for collections. Legacy
    bookings: the whole total under their payment method when paid."""
    booking = booking or {}
    if booking.get("paid_cash") is not None or booking.get("paid_online") is not None:
        return r2(booking.get("paid_cash")), r2(booking.get("paid_online"))
    if booking.get("amount_paid") is None and booking.get("payment_status") in ("paid", "refund_due", "refunded"):
        total = bm.amount_paid_of(booking)
        if booking.get("payment_method") == CASH:
            return total, 0.0
        if booking.get("payment_method") in (ONLINE, "online_placeholder", "subscription"):
            return 0.0, total
    return 0.0, 0.0


class MoneyService:
    def __init__(self, db):
        self.db = db
        self.bookings = db.bookings
        self.wallet = CustomerWalletService(db)

    # ------------------------------------------------------------------
    # Create
    # ------------------------------------------------------------------

    async def apply_wallet_at_create(self, customer_id: str, draft_cars: list[dict], session, *, actor: dict | None = None) -> list[dict]:
        """Inside BOOKING's create transaction (`session`), on the booking
        docs about to be inserted (each with its `_id`). Returns the same
        docs with money fields set:
          - every car: wallet_applied, amount_paid (0), wallet_due_carried,
            amount_due;
          - balance < 0: the debt not already riding on another unpaid
            booking is added to the first car with something to pay (else
            the first car) — total_amount and platform_earning grow by it,
            `wallet_due_carried` records it, and it is reserved on the wallet
            (released if this booking is cancelled before it is paid;
            cleared — a credit — when it is paid);
          - balance > 0: spent on the cars in order (`wallet_applied`,
            debited now, guarded on the balance) — a car it covers fully is
            payment_status "paid".
        The caller recomputes awaiting/pending from the returned
        total_amount / amount_due (a carried debt makes a ₹0 plan car payable)."""
        cars = list(draft_cars or [])
        for car in cars:
            if car.get("_id") is None:
                raise BadRequestException("Internal error: a booking needs its id before the wallet is applied.")
            car.setdefault("wallet_applied", 0.0)
            car.setdefault("amount_paid", 0.0)
            car.setdefault("wallet_due_carried", 0.0)
        if not customer_id or not cars:
            return cars
        summary = await self.wallet.summary(customer_id, session=session)
        actor = actor or {}
        touched: set[int] = set()
        if summary["previous_balance_due"] > EPSILON:
            carry = summary["previous_balance_due"]
            index = next((i for i, c in enumerate(cars) if bm.total_of(c) > 0), 0)
            car = cars[index]
            car["wallet_due_carried"] = r2(float(car.get("wallet_due_carried") or 0) + carry)
            car["total_amount"] = r2(bm.total_of(car) + carry)
            car["platform_earning"] = r2(float(car.get("platform_earning") or 0) + carry)
            await self.wallet.reserve_carry(customer_id, carry, session)
            touched.add(index)
        elif summary["credit_available"] > EPSILON:
            available = summary["credit_available"]
            for index, car in enumerate(cars):
                due = max(0.0, r2(bm.total_of(car) - bm.wallet_applied_of(car) - float(car.get("amount_paid") or 0)))
                use = r2(min(available, due))
                if use <= EPSILON:
                    continue
                await self.wallet.post(
                    customer_id, -use, Kind.BOOKING_PAYMENT.value, key=f"apply:{car['_id']}",
                    booking_id=str(car["_id"]), booking_number=car.get("booking_number"),
                    actor_id=actor.get("id"), actor_role=actor.get("role"), require_balance=True, session=session,
                    note=f"Used for {car.get('booking_number') or 'a booking'}",
                )
                car["wallet_applied"] = r2(bm.wallet_applied_of(car) + use)
                available = r2(available - use)
                touched.add(index)
                if available <= EPSILON:
                    break
        for index, car in enumerate(cars):
            car["amount_due"] = max(0.0, r2(bm.total_of(car) - bm.wallet_applied_of(car) - float(car.get("amount_paid") or 0)))
            if index in touched:
                car["payment_status"] = bm.status_for(bm.total_of(car), bm.wallet_applied_of(car), car.get("amount_paid"))
        return cars

    async def quote_lines(self, customer_id: str | None) -> dict:
        """For the quote (BOOKING): {"previous_balance_due", "wallet_credit"}."""
        if not customer_id:
            return {"previous_balance_due": 0.0, "wallet_credit": 0.0, "balance": 0.0}
        s = await self.wallet.summary(customer_id)
        return {"previous_balance_due": s["previous_balance_due"], "wallet_credit": s["credit_available"], "balance": s["balance"]}

    # ------------------------------------------------------------------
    # Payments
    # ------------------------------------------------------------------

    async def apply_payment(
        self,
        booking_ids: list[str],
        amount: float,
        *,
        method: str,
        key: str,
        session=None,
        actor_id: str | None = None,
        actor_role: str | None = None,
        captain_cash_by: str | None = None,
        collected_by: str | None = None,
        set_fields: dict | None = None,
        note: str | None = None,
    ) -> dict:
        """Money received (`amount` rupees, `method` online|cash) for these
        cars, split over what each still owes (visit order). One
        transaction; idempotent on `key` (a payment already on a car is a
        no-op). Cancelled / deleted / already-paid cars take nothing — what
        no car owes is credited to the customer's wallet (overpayment).
        `captain_cash_by`: the captain who took this cash at the door (the
        captain wallet then owes it — see settle_captain_wallet).

        Returns {"already", "applied", "excess", "newly_paid" (ids that are
        now fully paid), "cars" (docs after), "customer_id", "wallet_entry"}."""
        amount = r2(amount)
        oids = [o for o in (_oid(i) for i in booking_ids) if o is not None]
        if not oids:
            raise BadRequestException("No booking to apply the payment to.")

        async def _do(s):
            cars = await self.bookings.find({"_id": {"$in": oids}}, session=s).to_list(length=50)
            if not cars:
                raise BadRequestException("Booking not found.")
            cars.sort(key=lambda c: (int(c.get("group_offset_minutes") or 0), str(c["_id"])))
            if any(key in (c.get("payment_keys") or []) for c in cars):
                return {"already": True, "applied": 0.0, "excess": 0.0, "newly_paid": [], "cars": cars,
                        "customer_id": cars[0].get("customer_id"), "wallet_entry": None}
            remaining = amount
            newly_paid: list[str] = []
            after_docs = []
            customer_id = cars[0].get("customer_id")
            now = _now()
            for car in cars:
                due = bm.amount_due(car)
                take = r2(min(due, remaining)) if remaining > EPSILON else 0.0
                remaining = r2(remaining - take)
                after_docs.append(await self._write_payment(
                    car, take, method=method, key=key, now=now, session=s, actor_id=actor_id, actor_role=actor_role,
                    captain_cash_by=captain_cash_by, collected_by=collected_by, set_fields=set_fields,
                ))
                if take > EPSILON and bm.is_paid(after_docs[-1]):
                    newly_paid.append(str(car["_id"]))
            entry = None
            if remaining > EPSILON:
                lead = cars[0]
                # Money received beyond what was due is still money received
                # by that method — and cash beyond it is still in the
                # captain's hand (his wallet owes it to the platform).
                extra: dict = {"paid_cash" if method == CASH else "paid_online": remaining}
                if method == CASH and captain_cash_by:
                    extra["captain_cash_collected"] = remaining
                await self.bookings.update_one({"_id": lead["_id"]}, {"$inc": extra}, session=s)
                after_docs[0] = await self.bookings.find_one({"_id": lead["_id"]}, session=s)
                entry = await self.wallet.post(
                    customer_id, remaining, Kind.OVERPAYMENT.value, key=f"over:{key}", booking_id=str(lead["_id"]),
                    booking_number=lead.get("booking_number"), actor_id=actor_id, actor_role=actor_role, session=s,
                    note=note or f"Paid ₹{amount:g} ({method}) when ₹{r2(amount - remaining):g} was due",
                    meta={"method": method, "payment_key": key},
                )
            return {"already": False, "applied": r2(amount - remaining), "excess": remaining, "newly_paid": newly_paid,
                    "cars": after_docs, "customer_id": customer_id, "wallet_entry": entry}

        result = await in_transaction(self.db, _do, session)
        if session is None:
            await self.after_payment(result)
        return result

    async def _write_payment(
        self, car: dict, take: float, *, method: str, key: str, now, session, actor_id, actor_role,
        captain_cash_by: str | None, collected_by: str | None, set_fields: dict | None,
    ) -> dict:
        paid_before = bm.amount_paid_of(car)
        paid_after = r2(paid_before + take)
        cash, online = received_by_method(car)
        fields: dict = {
            "amount_paid": paid_after,
            # The captain-wallet bookkeeping is pinned BEFORE the cash moves
            # (legacy bookings infer it from how they settled).
            "captain_wallet_posted": bm.captain_wallet_posted(car),
            "captain_cash_collected": bm.captain_cash_held(car),
            "paid_cash": cash,
            "paid_online": online,
            "wallet_applied": bm.wallet_applied_of(car),
            "updated_at": now,
        }
        if take > EPSILON:
            if method == CASH:
                fields["paid_cash"] = r2(cash + take)
                if captain_cash_by:
                    fields["captain_cash_collected"] = r2(fields["captain_cash_collected"] + take)
                    fields["cash_collected_by"] = captain_cash_by
                    fields["cash_collected_at"] = now
                elif collected_by:
                    fields["cash_collected_by"] = collected_by
                    fields["cash_collected_at"] = now
            else:
                fields["paid_online"] = r2(online + take)
            fields["payment_method"] = method
            fields.update(set_fields or {})
        projected = {**car, **fields}
        status = bm.status_for(bm.total_of(projected), bm.wallet_applied_of(projected), paid_after)
        if car.get("payment_status") in ("refund_due", "refunded") or car.get("status") == "cancelled":
            status = car.get("payment_status")
        fields["payment_status"] = status
        fields["amount_due"] = bm.amount_due({**projected, "payment_status": status})
        # A previous negative balance carried by this booking is settled once
        # this booking's money covers it (debt first).
        carried = r2(car.get("wallet_due_carried"))
        clear = carried > EPSILON and not car.get("wallet_due_cleared") and paid_after + EPSILON >= carried
        if clear:
            fields["wallet_due_cleared"] = True
            fields["wallet_due_cleared_at"] = now
        update = {
            "$set": fields,
            "$push": {
                "payment_keys": key,
                "payments": {"key": key, "amount": take, "method": method, "at": now, "by": actor_id, "role": actor_role},
            },
        }
        updated = await self.bookings.find_one_and_update(
            {"_id": car["_id"], "payment_keys": {"$ne": key}}, update, session=session, return_document=ReturnDocument.AFTER,
        )
        if updated is None:
            raise BadRequestException("This payment was just applied — refresh.")
        if clear and car.get("customer_id"):
            await self.wallet.release_carry(car["customer_id"], carried, session)
            await self.wallet.post(
                car["customer_id"], carried, Kind.PREVIOUS_BALANCE_PAID.value, key=f"carry_paid:{car['_id']}",
                booking_id=str(car["_id"]), booking_number=car.get("booking_number"), session=session,
                note=f"Previous balance paid with {car.get('booking_number') or 'a booking'}",
            )
        return updated

    async def after_payment(self, result: dict, *, confirm_note: str | None = None) -> None:
        """Post-commit side effects of apply_payment: tell the customer about
        an overpayment credit; confirm cars parked waiting for payment."""
        if not result or result.get("already"):
            return
        entry = result.get("wallet_entry")
        if entry and entry.get("created"):
            await self.wallet.announce(result.get("customer_id"), entry)
        if confirm_note and result.get("newly_paid"):
            from app.services.booking_service import BookingService

            service = BookingService(self.db)
            for booking_id in result["newly_paid"]:
                try:
                    await service.confirm_awaiting_payment_booking(booking_id, confirm_note)
                except Exception:  # noqa: BLE001 — the money stands; the queue shows it
                    logger.exception("Could not confirm %s after its payment", booking_id)

    # ------------------------------------------------------------------
    # Cancellation
    # ------------------------------------------------------------------

    async def on_booking_cancelled(
        self,
        cars: list,
        *,
        charge_amount: float = 0.0,
        plan_car_ids: list[str] | None = None,
        actor: dict | None = None,
        session=None,
        charge_id: str | None = None,
        reason: str | None = None,
    ) -> dict:
        """Inside the cancel transaction (`session`), AFTER the cars were
        written cancelled (or in the same transaction anyway). `cars`: the
        cancelled bookings (docs or ids) — re-read here, in the session, so
        a payment that committed a moment before is included and one after
        sees "cancelled" (it then goes to the wallet as an overpayment).

        Money (spec 1.2): what was paid + wallet credit spent − the charge
        is credited (a debit when the charge is bigger; an unpaid booking
        cancelled late is a debit of the charge). A previous balance carried
        by an unpaid car goes back onto the wallet's open debt. Plan cars
        carry no charge (BOOKING passes charge_amount already decided for
        the visit); their paid extras are returned like any payment.
        Business/system cancels pass charge_amount 0. Idempotent: a car
        already settled is skipped, the ledger key covers the visit.

        Returns {"credited", "charge", "net", "balance", "wallet_line",
        "refunds": {booking_id: amount}, "entry"}."""
        actor = actor or {"id": "system", "role": "system"}
        ids = [str(c.get("_id") or c.get("id")) if isinstance(c, dict) else str(c) for c in cars or []]
        oids = [o for o in (_oid(i) for i in ids) if o is not None]
        charge = max(0.0, r2(charge_amount))

        async def _do(s):
            fresh = await self.bookings.find({"_id": {"$in": oids}}, session=s).to_list(length=50)
            fresh.sort(key=lambda c: (int(c.get("group_offset_minutes") or 0), str(c["_id"])))
            pending = [c for c in fresh if not c.get("cancel_money_settled")]
            if not pending:
                return {"credited": 0.0, "charge": 0.0, "net": 0.0, "balance": None, "wallet_line": "", "refunds": {}, "entry": None}
            customer_id = pending[0].get("customer_id")
            refunds: dict[str, float] = {}
            now = _now()
            for car in pending:
                back = bm.refundable(car)
                refunds[str(car["_id"])] = back
                carried = r2(car.get("wallet_due_carried"))
                if carried > EPSILON and not car.get("wallet_due_cleared") and customer_id:
                    await self.wallet.release_carry(customer_id, carried, s)
                fields: dict = {"cancel_money_settled": True, "cancel_money_settled_at": now, "amount_due": 0.0, "updated_at": now}
                if back > EPSILON:
                    fields.update({
                        "payment_status": bm.REFUNDED, "refunded_to": "wallet", "refunded_amount": back, "refunded_at": now,
                    })
                await self.bookings.update_one({"_id": car["_id"], "cancel_money_settled": {"$ne": True}}, {"$set": fields}, session=s)
            credited = r2(sum(refunds.values()))
            net = r2(credited - charge)
            entry = None
            cancel_key = "cancel:" + ",".join(sorted(str(c["_id"]) for c in pending))
            if customer_id and abs(net) > EPSILON:
                numbers = " + ".join(str(c.get("booking_number") or "") for c in pending)
                parts = []
                if credited > EPSILON:
                    parts.append(f"₹{credited:g} paid back")
                if charge > EPSILON:
                    parts.append(f"₹{charge:g} cancellation charge")
                entry = await self.wallet.post(
                    customer_id, net, Kind.CANCELLATION.value if credited > EPSILON else Kind.CANCELLATION_CHARGE.value,
                    key=cancel_key, booking_id=str(pending[0]["_id"]), booking_number=numbers,
                    actor_id=actor.get("id"), actor_role=actor.get("role"), actor_name=actor.get("name"), session=s,
                    note=f"{numbers} cancelled — " + ", ".join(parts) + (f" ({reason})" if reason else ""),
                    meta={"charge": charge, "credited": credited, "charge_id": charge_id, "plan_car_ids": plan_car_ids or []},
                )
            if charge_id and _oid(charge_id) is not None:
                await self.db.customer_charges.update_one(
                    {"_id": _oid(charge_id)},
                    {"$set": {"status": "settled", "settled_via": "wallet", "wallet_key": cancel_key if entry else None,
                              "updated_at": now}},
                    session=s,
                )
            balance = (entry or {}).get("balance")
            if balance is None and customer_id:
                balance = await self.wallet.balance(customer_id, session=s)
            return {
                "credited": credited, "charge": charge, "net": net, "balance": balance,
                "wallet_line": CustomerWalletService.wallet_line(net, balance or 0.0) if abs(net) > EPSILON else "",
                "refunds": refunds, "entry": entry, "customer_id": customer_id,
            }

        return await in_transaction(self.db, _do, session)

    # ------------------------------------------------------------------
    # Price changes (customer edit, on-site add-on, manager discount / tip)
    # ------------------------------------------------------------------

    async def on_price_change(self, booking_before: dict, booking_after: dict, *, actor: dict | None = None, reason: str = "", session=None) -> dict:
        """Inside BOOKING's edit / add-on transaction. `booking_after` carries
        the NEW total_amount (which must still include wallet_due_carried and
        any legacy cancellation_charge). Re-reads the booking in `session`
        for its money, then:
          - paid + wallet credit spent > new total → the difference is
            credited to the wallet (wallet credit spent is returned first);
          - otherwise the difference becomes due.
        Returns {"fields": {...to $set on the booking...}, "wallet_credit",
        "amount_due", "due_delta", "entry", "booking_id"}. BOOKING writes
        `fields` in the same transaction, guarded on its own before-state,
        then calls after_price_change() once committed."""
        actor = actor or {"id": "system", "role": "system"}
        booking_id = str(booking_before.get("_id") or booking_before.get("id"))

        async def _do(s):
            fresh = await self.bookings.find_one({"_id": _oid(booking_id)}, session=s) or booking_before
            if fresh.get("status") == "cancelled" or fresh.get("is_deleted"):
                raise BadRequestException("This booking was cancelled — it can't be changed.")
            old = bm.money_view(fresh)
            new_total = bm.total_of(booking_after)
            applied = old["wallet_applied"]
            paid = old["amount_paid"]
            excess = r2(applied + paid - new_total)
            entry = None
            if excess > EPSILON:
                give_back = r2(min(applied, excess))
                applied = r2(applied - give_back)
                paid = r2(paid - (excess - give_back))
                rev = int(fresh.get("money_rev") or 0)
                entry = await self.wallet.post(
                    fresh["customer_id"], excess, Kind.PRICE_REDUCED.value, key=f"price:{booking_id}:{rev}",
                    booking_id=booking_id, booking_number=fresh.get("booking_number"),
                    actor_id=actor.get("id"), actor_role=actor.get("role"), actor_name=actor.get("name"), session=s,
                    note=f"{fresh.get('booking_number')}: ₹{old['total']:g} → ₹{new_total:g}" + (f" ({reason})" if reason else ""),
                )
            else:
                excess = 0.0
            status = bm.status_for(new_total, applied, paid)
            due = max(0.0, r2(new_total - applied - paid))
            fields = {
                "wallet_applied": applied,
                "amount_paid": paid,
                "amount_due": due,
                "payment_status": status,
                "money_rev": int(fresh.get("money_rev") or 0) + 1,
                # Pinned before anything else moves (legacy inference).
                "captain_wallet_posted": bm.captain_wallet_posted(fresh),
                "captain_cash_collected": bm.captain_cash_held(fresh),
            }
            if fresh.get("paid_cash") is None and fresh.get("paid_online") is None:
                fields["paid_cash"], fields["paid_online"] = received_by_method(fresh)
            return {
                "booking_id": booking_id, "fields": fields, "wallet_credit": excess, "amount_due": due,
                "due_delta": r2(due - old["amount_due"]), "entry": entry, "customer_id": fresh.get("customer_id"),
            }

        return await in_transaction(self.db, _do, session)

    async def after_price_change(self, results: list[dict] | dict, reason: str = "Booking price changed") -> None:
        """Post-commit for on_price_change: void the open payment links made
        for the old amount, tell the customer about a wallet credit, and
        confirm a booking left waiting for a payment that is no longer due."""
        results = [results] if isinstance(results, dict) else list(results or [])
        ids = [r["booking_id"] for r in results if r and r.get("booking_id")]
        if not ids:
            return
        try:
            from app.services.payment_service import PaymentService

            await PaymentService(self.db).void_open_links(ids, reason)
        except Exception:  # noqa: BLE001 — never raises by contract
            logger.exception("Could not void links after a price change on %s", ids)
        for r in results:
            entry = r.get("entry")
            if entry and entry.get("created"):
                await self.wallet.announce(r.get("customer_id"), entry)
            if r.get("amount_due", 1) <= EPSILON:
                try:
                    from app.services.booking_service import BookingService

                    await BookingService(self.db).confirm_awaiting_payment_booking(r["booking_id"], "Confirmed — nothing left to pay")
                except Exception:  # noqa: BLE001
                    logger.exception("Could not confirm %s after a price change", r.get("booking_id"))

    # ------------------------------------------------------------------
    # Captain wallet: delta settlement
    # ------------------------------------------------------------------

    async def settle_captain_wallet(self, booking_ids: list[str], session=None) -> list[dict]:
        """For each booking, post to its captain's wallet only the difference
        between where it should leave him (captain_earning once completed −
        cash he took from the customer for it) and what was already posted
        (`captain_wallet_posted`). Idempotent (a second call posts nothing)
        and race-safe (guarded on the posted amount, inside a transaction;
        the ledger row carries a unique key). Manager-done / logged jobs and
        bookings with no captain are skipped. Late penalties already sit in
        captain_earning. Returns [{booking_id, captain_id, delta, posted}]."""
        from app.services.wallet_service import WalletService

        wallets = WalletService(self.db)
        out: list[dict] = []
        for booking_id in booking_ids:
            oid = _oid(booking_id)
            if oid is None:
                continue

            async def _do(s, oid=oid):
                b = await self.bookings.find_one({"_id": oid}, session=s)
                if not b or bm.captain_wallet_exempt(b):
                    return None
                posted = bm.captain_wallet_posted(b)
                target = bm.captain_wallet_target(b)
                delta = r2(target - posted)
                if abs(delta) <= EPSILON:
                    if b.get("captain_wallet_posted") is None and (b.get("wallet_settled") or posted):
                        await self.bookings.update_one(
                            {"_id": oid, "captain_wallet_posted": None}, {"$set": {"captain_wallet_posted": posted}}, session=s,
                        )
                    return None
                rev = int(b.get("captain_wallet_rev") or 0) + 1
                guard = {"_id": oid, "captain_wallet_rev": b.get("captain_wallet_rev")}
                done = await self.bookings.update_one(
                    guard,
                    {"$set": {"captain_wallet_posted": target, "captain_wallet_rev": rev, "captain_wallet_captain_id": b["captain_id"],
                              "captain_cash_collected": bm.captain_cash_held(b), "updated_at": _now()}},
                    session=s,
                )
                if not done.modified_count:
                    raise BadRequestException("The booking just changed — try again.")
                number = b.get("booking_number")
                if delta > 0:
                    description = f"Payout for booking {number}"
                else:
                    description = f"Platform share of cash for booking {number}"
                await wallets.post_delta(b["captain_id"], delta, str(oid), description, session=s, key=f"cw:{oid}:{rev}")
                return {"booking_id": str(oid), "captain_id": b["captain_id"], "delta": delta, "posted": target}

            try:
                row = await in_transaction(self.db, _do, session)
            except BadRequestException:
                if session is not None:
                    raise
                # A concurrent settle moved it first: the re-run sees its result.
                row = await in_transaction(self.db, _do, None)
            if row:
                out.append(row)
        return out

    # ------------------------------------------------------------------
    # Reports
    # ------------------------------------------------------------------

    async def wallet_totals(self) -> dict:
        """Admin lines: credit held for customers, and debt they owe."""
        rows = await self.db.customer_wallets.aggregate([
            {"$group": {
                "_id": None,
                "credits_held": {"$sum": {"$cond": [{"$gt": ["$balance", 0]}, "$balance", 0]}},
                "credit_customers": {"$sum": {"$cond": [{"$gt": ["$balance", 0]}, 1, 0]}},
                "dues_outstanding": {"$sum": {"$cond": [{"$lt": ["$balance", 0]}, {"$multiply": [-1, "$balance"]}, 0]}},
                "due_customers": {"$sum": {"$cond": [{"$lt": ["$balance", 0]}, 1, 0]}},
            }},
        ]).to_list(length=1)
        row = rows[0] if rows else {}
        payouts = await self.db.customer_wallet_ledger.aggregate([
            {"$match": {"kind": Kind.PAYOUT.value}},
            {"$group": {"_id": None, "amount": {"$sum": "$amount"}, "count": {"$sum": 1}}},
        ]).to_list(length=1)
        p = payouts[0] if payouts else {}
        return {
            "credits_held": r2(row.get("credits_held")),
            "credit_customers": int(row.get("credit_customers") or 0),
            "dues_outstanding": r2(row.get("dues_outstanding")),
            "due_customers": int(row.get("due_customers") or 0),
            "payouts_amount": r2(-(p.get("amount") or 0)),
            "payouts_count": int(p.get("count") or 0),
        }


# ----------------------------------------------------------------------
# Boot-time migrations (idempotent — safe on every boot)
# ----------------------------------------------------------------------


async def migrate_open_charges_to_wallet(db) -> int:
    """Open late-cancellation charges (the old "added to your next booking"
    model) become wallet debits: each charge is settled (status open →
    settled, guarded) and debited on the wallet with key `charge:{id}` in
    one transaction. Charges already carried by a booking ("applied") are
    left on it. Returns how many moved."""
    wallet = CustomerWalletService(db)
    moved = 0
    rows = await db.customer_charges.find({"status": "open"}, {"_id": 1}).to_list(length=None)
    for row in rows:
        async def _do(s, cid=row["_id"]):
            charge = await db.customer_charges.find_one_and_update(
                {"_id": cid, "status": "open"},
                {"$set": {"status": "settled", "settled_via": "wallet", "wallet_key": f"charge:{cid}", "updated_at": _now()},
                 "$push": {"history": {"at": _now(), "action": "moved_to_wallet", "by": "system", "by_name": "System",
                                       "role": "system", "from": None, "to": None, "note": "Charged to the customer's wallet"}}},
                session=s,
            )
            if charge is None:
                return False
            amount = r2(charge.get("amount"))
            if amount > EPSILON:
                await wallet.post(
                    charge["customer_id"], -amount, Kind.CHARGE_MIGRATED.value, key=f"charge:{cid}",
                    booking_id=charge.get("source_booking_id"), booking_number=charge.get("source_booking_number"),
                    actor_id="system", actor_role="system", session=s,
                    note=f"Late cancellation of {charge.get('source_booking_number')}",
                )
            return True

        try:
            if await in_transaction(db, _do):
                moved += 1
        except Exception:  # noqa: BLE001 — one bad row must not stop the boot
            logger.exception("Could not move charge %s to the wallet", row["_id"])
    return moved


async def migrate_legacy_tip_method(db) -> int:
    """MONEY-2: a tip saved before `tip_method` existed is a CASH tip. The
    old code counted it in the job's payment-method bucket, so on a
    manager-done job paid online it sits in paid_online: it moves to
    paid_cash (amount_paid unchanged). One server-side update per doc,
    idempotent — only tips with no tip_method are touched."""
    move = {"$and": [
        {"$eq": ["$completed_by_role", "manager"]},
        {"$ne": [{"$ifNull": ["$amount_paid", None]}, None]},
        {"$ne": ["$payment_method", CASH]},
        {"$gte": [{"$ifNull": ["$paid_online", -1]}, "$tip_amount"]},
    ]}
    result = await db.bookings.update_many(
        {"tip_amount": {"$gt": 0}, "tip_method": {"$exists": False}},
        [
            {"$set": {"_tip_move": move}},
            {"$set": {
                "paid_online": {"$cond": ["$_tip_move", {"$round": [{"$subtract": ["$paid_online", "$tip_amount"]}, 2]}, "$paid_online"]},
                "paid_cash": {"$cond": ["$_tip_move", {"$round": [{"$add": [{"$ifNull": ["$paid_cash", 0]}, "$tip_amount"]}, 2]}, "$paid_cash"]},
                "tip_method": CASH,
            }},
            {"$unset": "_tip_move"},
        ],
    )
    return result.modified_count


async def backfill_booking_money(db) -> int:
    """Materialise amount_paid / wallet_applied / amount_due on bookings
    written before them (one server-side update; idempotent — only docs
    missing amount_paid). Also gives legacy tips their method (cash) —
    migrate_legacy_tip_method — since this already runs at every boot."""
    received = {"$in": ["$payment_status", ["paid", "refund_due", "refunded"]]}
    total = {"$ifNull": ["$total_amount", 0]}
    result = await db.bookings.update_many(
        {"amount_paid": {"$exists": False}},
        [{"$set": {
            "wallet_applied": {"$ifNull": ["$wallet_applied", 0]},
            "amount_paid": {"$cond": [received, total, 0]},
            "amount_due": {"$cond": [
                {"$or": [received, {"$eq": ["$status", "cancelled"]}]}, 0, {"$max": [0, total]},
            ]},
        }}],
    )
    try:
        await migrate_legacy_tip_method(db)
    except Exception:  # noqa: BLE001 — never blocks the money backfill
        logger.exception("Could not give legacy tips their method")
    return result.modified_count
