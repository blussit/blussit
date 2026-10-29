"""
Post-booking money tools (founder 2026-10-28).

  set_discount     A manager (his own center) or an admin gives, raises,
                   lowers or removes a discount on an EXISTING booking — the
                   discount button the customer never had. It is a TARGET
                   ("the manager discount on this booking is now ₹X"), so a
                   retry changes nothing, and it goes through the same money
                   model as everything else:

                     total_amount     −Δ      (never below the tip / carried
                                              previous balance — the bill)
                     discount_amount  +Δ      (coupon / offer / plan lines
                                              are not touched)
                     platform_earning −Δ      (the captain's earning is a
                                              share of the PRICE, not of the
                                              discount; the platform bears it)
                     amount_due / amount_paid / wallet_applied / payment_status
                                              by MoneyService.on_price_change:
                                              the discount first reduces what
                                              is due; only when the customer
                                              has already paid more than the
                                              new total does the surplus go
                                              BACK — to the customer's wallet,
                                              booked against this booking
                                              (ledger kind price_reduced) and
                                              capped at what the customer
                                              actually paid for it, less what a
                                              manager already paid back. A
                                              discount never mints wallet
                                              money from nothing.

                   One transaction, serialised with every other change to the
                   customer's visit (the same customer-doc write cancel /
                   reschedule / edit make), guarded on the exact money it read
                   (409 on a stale `expected_version` or a concurrent change),
                   idempotent on `idempotency_key`, with one immutable
                   `booking_adjustments` row per car (before / after, actor,
                   reason, time) and an audit-log entry.

  list_adjustments the history the manager / admin drawer shows.

The admin's full edit lives in booking_admin_edit_service.py and uses the same
helpers (version token, adjustment rows, notifications).
"""
from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from bson import ObjectId
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

from app.core.authz import ensure_own_center
from app.core.exceptions import BadRequestException, ConflictException, ForbiddenException, NotFoundException, TooManyRequestsException
from app.models.enums import BookingStatus, NotificationType
from app.services import booking_money as bm
from app.services.booking_money import EPSILON, r2
from app.services.booking_service import BookingService
from app.utils.money import round_rupees, split_whole_rupees
from app.utils.serializers import serialize_doc

logger = logging.getLogger(__name__)

# What a manager may still discount: not started yet or in progress. A
# completed booking is the admin's alone; cancelled / deleted ones are over.
MANAGER_DISCOUNT_STATUSES = frozenset({
    BookingStatus.AWAITING_PAYMENT.value, BookingStatus.PENDING.value, BookingStatus.ASSIGNED.value,
    BookingStatus.CAPTAIN_ON_THE_WAY.value, BookingStatus.SERVICE_STARTED.value, BookingStatus.RESCHEDULED.value,
})
MAX_DISCOUNT_CHANGES = 40
# The customer is WhatsApp'd about a discount at most this often per booking
# (the in-app line is always written) — a toggling manager can't spam them.
WHATSAPP_COOLDOWN_SECONDS = 600


class NothingToDiscount(BadRequestException):
    error_code = "NOTHING_TO_DISCOUNT"


class StaleBooking(ConflictException):
    error_code = "STALE_BOOKING"


class _Preview(Exception):
    """Raised at the end of a dry run's transaction to abort it."""

    def __init__(self, result: dict):
        super().__init__("dry run")
        self.result = result


def _now() -> datetime:
    """UTC now at millisecond precision — what Mongo stores — so a value
    written and later matched by equality (notified_at marking) agrees."""
    now = datetime.now(timezone.utc)
    return now.replace(microsecond=now.microsecond // 1000 * 1000)


def booking_version(doc: dict | None) -> str:
    """A short token that changes whenever anything an editor decides on
    changes — money, status, schedule, captain, tip, discount. The client
    echoes it back as `expected_version`; a mismatch is a 409."""
    d = doc or {}
    parts = [
        str(d.get("updated_at")), str(d.get("status")), str(d.get("total_amount")), str(d.get("amount_paid")),
        str(d.get("wallet_applied")), str(d.get("money_rev")), str(d.get("manager_discount")), str(d.get("tip_amount")),
        str(d.get("scheduled_date")), str(d.get("scheduled_slot")), str(d.get("captain_id")), str(d.get("customer_id")),
        str(d.get("service_center_id")), str(len(d.get("payment_keys") or [])),
        str(len(d.get("payment_adjustments") or [])), str(d.get("admin_edit_rev")),
    ]
    return hashlib.sha1("|".join(parts).encode()).hexdigest()[:16]


def discountable_rupees(car: dict) -> int:
    """The part of this car's bill a manager discount may take away, in whole
    rupees: the bill so far (with any discount already given added back)
    less the tip (the customer's gift) and any previous wallet balance this
    booking carries (a debt, not a price). A plan-covered wash with no extras
    has none."""
    total = bm.total_of(car)
    md = r2(car.get("manager_discount"))
    tip = r2(car.get("tip_amount"))
    carried = r2(car.get("wallet_due_carried"))
    return max(0, int(r2(total + md - tip - carried) + 1e-9))


def money_snapshot(car: dict) -> dict:
    view = bm.money_view(car)
    return {
        "manager_discount": r2(car.get("manager_discount")), "total": view["total"], "amount_paid": view["amount_paid"],
        "wallet_applied": view["wallet_applied"], "amount_due": view["amount_due"], "payment_status": view["payment_status"],
        "platform_earning": r2(car.get("platform_earning")), "tip_amount": r2(car.get("tip_amount")),
    }


def payback_cap(car: dict) -> float:
    """The most that can go back to the customer's wallet for this booking:
    what they actually paid for it, less what a manager already paid back."""
    return max(0.0, r2(bm.paid_by_customer(car) - r2(car.get("paid_back_total"))))


class BookingAdjustService:
    def __init__(self, db):
        self.db = db
        self.svc = BookingService(db)
        self.repo = self.svc.repo
        self.money = self.svc.money
        self.adjustments = db.booking_adjustments

    # ------------------------------------------------------------------
    # Access
    # ------------------------------------------------------------------

    async def _load(self, booking_id: str, actor: dict) -> dict:
        """The booking, or 404 — also for a manager outside its center: an
        id from another center must not confirm that it exists."""
        booking = await self.repo.find_by_id(booking_id) if ObjectId.is_valid(str(booking_id or "")) else None
        if not booking:
            raise NotFoundException("Booking not found")
        if actor["role"] == "manager":
            try:
                ensure_own_center("manager", actor.get("center"), booking.get("service_center_id"))
            except ForbiddenException:
                raise NotFoundException("Booking not found") from None
        elif actor["role"] != "admin":
            raise ForbiddenException("You do not have permission to perform this action")
        return booking

    async def throttle(self, actor_id: str, limit: int, window_seconds: int = 300) -> None:
        """A staff account can change money on at most `limit` bookings per
        window — a stolen or careless session can't sweep the whole queue."""
        since = _now() - timedelta(seconds=window_seconds)
        if await self.adjustments.count_documents({"actor_id": actor_id, "created_at": {"$gte": since}}) >= limit:
            raise TooManyRequestsException("Too many price changes in a short time — wait a few minutes and try again.")

    # ------------------------------------------------------------------
    # Discount
    # ------------------------------------------------------------------

    @staticmethod
    def _targets(cars: list[dict], payload) -> tuple[int, list[int]]:
        """(total discount wanted, per-car shares) in whole rupees."""
        bases = [discountable_rupees(c) for c in cars]
        bill = sum(bases)
        if payload.amount is not None:
            target = int(payload.amount)
        else:
            target = int(round_rupees(Decimal(bill) * Decimal(str(payload.percent)) / Decimal(100)))
        if target > bill:
            if bill <= 0:
                if any(c.get("subscription_id") for c in cars):
                    raise NothingToDiscount("This wash is covered by the customer's plan, so there is nothing to discount.")
                raise NothingToDiscount("There is nothing left on this booking to discount.")
            raise BadRequestException(f"The discount can't be more than the bill (₹{bill:g}).")
        shares = split_whole_rupees(target, [float(b) for b in bases], caps=[float(b) for b in bases])
        return target, shares

    async def set_discount(self, booking_id: str, payload, actor: dict) -> dict:
        """`actor`: {"id", "role", "center"}. Returns the result (see
        `_result`); `dry_run` runs the real transaction and aborts it."""
        booking = await self._load(booking_id, actor)
        role = actor["role"]
        lead_key = f"disc:{booking_id}:{payload.idempotency_key}" if payload.idempotency_key else None
        if lead_key:
            done = await self.adjustments.find_one({"key": lead_key})
            if done is not None:
                return {**(done.get("result") or {}), "already_applied": True}
        if booking.get("status") == BookingStatus.CANCELLED.value:
            raise BadRequestException("This booking was cancelled — there is nothing to discount.")
        if booking.get("status") == BookingStatus.COMPLETED.value and role != "admin":
            raise ForbiddenException("A completed booking's discount can only be changed by an admin.")
        if booking.get("status") not in MANAGER_DISCOUNT_STATUSES and role != "admin":
            raise BadRequestException("This booking can't be discounted in its current state.")
        if await self.adjustments.count_documents({"booking_id": booking_id, "kind": "discount"}) >= MAX_DISCOUNT_CHANGES:
            raise BadRequestException("This booking's discount has been changed too many times — ask an admin to review it.")
        if not payload.dry_run:
            await self.throttle(actor["id"], 60)
        visit = await self.svc._visit_cars(booking) if payload.scope == "visit" else [booking]
        if not any(str(c["_id"]) == booking_id for c in visit):
            visit = [booking]
        car_ids = [str(c["_id"]) for c in visit]
        customer_id = booking["customer_id"]
        who = await self.svc.charges.actor(actor["id"], role)
        when = _now()
        reason = payload.reason

        async def _do(session):
            await self.svc._touch_customer(customer_id, session)
            fresh = []
            for cid in car_ids:
                car = await self.repo.find_by_id(cid, session=session)
                if not car:
                    raise NotFoundException("Booking not found")
                fresh.append(car)
            lead = next(c for c in fresh if str(c["_id"]) == booking_id)
            if payload.expected_version and booking_version(lead) != payload.expected_version:
                raise StaleBooking("This booking changed while you were looking at it — refresh and try again.")
            for car in fresh:
                if car.get("status") == BookingStatus.CANCELLED.value or car.get("is_deleted"):
                    raise BadRequestException("This booking was cancelled — there is nothing to discount.")
                if role != "admin" and car.get("status") not in MANAGER_DISCOUNT_STATUSES:
                    if car.get("status") == BookingStatus.COMPLETED.value:
                        raise ForbiddenException("A completed booking's discount can only be changed by an admin.")
                    raise BadRequestException("This booking can't be discounted in its current state.")
            target, shares = self._targets(fresh, payload)
            if all(abs(r2(c.get("manager_discount")) - s) < EPSILON for c, s in zip(fresh, shares)):
                # Already exactly this: a retry, a double click — nothing to do.
                return None, self._result(payload, fresh, fresh, [], target, changed=False, who=who), []
            rows: list[dict] = []
            money_results: list[dict] = []
            after_docs: list[dict] = []
            for index, (car, share) in enumerate(zip(fresh, shares)):
                old_md = r2(car.get("manager_discount"))
                delta = r2(share - old_md)
                if abs(delta) < EPSILON:
                    after_docs.append(car)
                    continue
                fields = {
                    "manager_discount": float(share),
                    "manager_discount_by": actor["id"],
                    "manager_discount_at": when,
                    "manager_discount_reason": reason,
                    "discount_amount": max(0.0, r2(r2(car.get("discount_amount")) + delta)),
                    "total_amount": r2(bm.total_of(car) - delta),
                    "platform_earning": r2(r2(car.get("platform_earning")) - delta),
                    "updated_at": when,
                }
                money = await self.money.on_price_change(
                    car, {**car, **fields}, actor=who, reason="Discount" if share else "Discount removed", session=session,
                    credit_cap=payback_cap(car),
                )
                fields.update(money["fields"])
                guard = {
                    "total_amount": car.get("total_amount"), "manager_discount": car.get("manager_discount"),
                    "money_rev": car.get("money_rev"), "status": car.get("status"), "amount_paid": car.get("amount_paid"),
                    "wallet_applied": car.get("wallet_applied"), "is_deleted": {"$ne": True},
                }
                done = await self.repo.collection.find_one_and_update(
                    {"_id": car["_id"], **guard}, {"$set": fields}, session=session, return_document=ReturnDocument.AFTER,
                )
                if done is None:
                    raise StaleBooking("This booking's amount just changed — refresh and try again.")
                money_results.append(money)
                after_docs.append(done)
                cid = str(car["_id"])
                seq = await self.adjustments.count_documents({"booking_id": cid}, session=session) + 1
                rows.append({
                    "booking_id": cid, "booking_number": car.get("booking_number"), "customer_id": customer_id,
                    "service_center_id": car.get("service_center_id"), "kind": "discount", "seq": seq,
                    "key": lead_key if (lead_key and cid == booking_id) else (
                        f"disc:{cid}:{payload.idempotency_key}:v" if payload.idempotency_key else f"disc:{cid}:r{int(car.get('money_rev') or 0)}"
                    ),
                    "visit_scope": payload.scope, "actor_id": actor["id"], "actor_role": role, "actor_name": who.get("name"),
                    "reason": reason, "before": money_snapshot(car), "after": money_snapshot(done),
                    "wallet_credit": money["wallet_credit"], "created_at": when,
                })
            result = self._result(payload, fresh, after_docs, money_results, target, changed=True, who=who)
            for row in rows:
                row["result"] = result if row["booking_id"] == booking_id else None
            if rows and not any(r["booking_id"] == booking_id for r in rows):
                rows[0]["result"] = result
            if payload.dry_run:
                raise _Preview(result)
            if rows:
                try:
                    await self.adjustments.insert_many(rows, session=session)
                except DuplicateKeyError:
                    raise StaleBooking("This discount was just applied — refresh and try again.") from None
            return after_docs, result, money_results

        try:
            async with await self.db.client.start_session() as session:
                after_docs, result, money_results = await session.with_transaction(_do)
        except _Preview as preview:
            return {**preview.result, "dry_run": True}
        if not result.get("changed"):
            return result
        await self._after_discount(booking, visit, after_docs, money_results, result, actor, who, payload, when)
        return result

    def _result(self, payload, before: list[dict], after: list[dict], money_results: list[dict], target: int, *, changed: bool, who: dict) -> dict:
        cars = []
        by_after = {str(c["_id"]): c for c in after}
        credits = {r["booking_id"]: r["wallet_credit"] for r in money_results}
        for old in before:
            cid = str(old["_id"])
            new = by_after.get(cid, old)
            cars.append({
                "booking_id": cid, "booking_number": old.get("booking_number"),
                "discount_before": r2(old.get("manager_discount")), "discount_after": r2(new.get("manager_discount")),
                "total_before": bm.total_of(old), "total_after": bm.total_of(new),
                "amount_due_before": bm.amount_due(old), "amount_due_after": bm.amount_due(new),
                "amount_paid_after": bm.amount_paid_of(new), "payment_status": bm.money_view(new)["payment_status"],
                "wallet_credit": r2(credits.get(cid)),
            })
        credit = r2(sum(c["wallet_credit"] for c in cars))
        total_due = r2(sum(c["amount_due_after"] for c in cars))
        total_after = r2(sum(c["total_after"] for c in cars))
        lead_id = str(before[0]["_id"])
        lead = next((c for c in cars if c["booking_id"] == lead_id), cars[0])
        discount_after = r2(sum(c["discount_after"] for c in cars))
        discount_before = r2(sum(c["discount_before"] for c in cars))
        if not changed:
            message = f"The discount is already ₹{discount_after:g}."
        elif discount_after <= EPSILON:
            message = f"Discount removed. New total ₹{total_after:g}, amount due ₹{total_due:g}."
        else:
            message = f"Discount set to ₹{discount_after:g}. New total ₹{total_after:g}, amount due ₹{total_due:g}."
        if credit > EPSILON:
            message += f" ₹{credit:g} goes back to the customer's wallet."
        return {
            "booking_id": lead_id, "booking_number": lead["booking_number"], "scope": payload.scope, "changed": changed,
            "dry_run": bool(payload.dry_run), "discount_before": discount_before, "discount_after": discount_after,
            "target": float(target), "total_after": total_after, "amount_due_after": total_due, "wallet_credit": credit,
            "cars": cars, "message": message, "version": booking_version(by_after.get(lead_id, before[0])),
            "already_applied": False,
        }

    async def _after_discount(self, booking, visit, after_docs, money_results, result, actor, who, payload, when) -> None:
        post = self.svc._post_commit
        role = actor["role"]
        lead = next((c for c in after_docs if str(c["_id"]) == str(booking["_id"])), after_docs[0])
        await post("price-change money", lead, self.money.after_price_change(money_results, "Discount applied"))
        from app.services.audit_service import AuditService

        action = "ADMIN_GIVE_DISCOUNT" if role == "admin" else "MANAGER_GIVE_DISCOUNT"
        await post("discount audit", lead, AuditService(self.db).log_action(
            actor["id"], role, action, "bookings", str(booking["_id"]),
            {"scope": payload.scope, "reason": payload.reason, "idempotency_key": payload.idempotency_key,
             "before": {"manager_discount": result["discount_before"]},
             "after": {"manager_discount": result["discount_after"], "total": result["total_after"],
                       "amount_due": result["amount_due_after"]},
             "wallet_credit": result["wallet_credit"], "cars": result["cars"]},
            service_center_id=booking.get("service_center_id"),
        ))
        for car in result["cars"]:
            note = (
                f"{who.get('name') or role.title()} {'removed the discount' if car['discount_after'] <= EPSILON else 'set the discount to ₹%g' % car['discount_after']}"
                f" — {payload.reason}"
            )
            changes = [
                {"booking_id": car["booking_id"], "booking_number": car["booking_number"], "field": "discount", "from": car["discount_before"], "to": car["discount_after"]},
                {"booking_id": car["booking_id"], "booking_number": car["booking_number"], "field": "total", "from": car["total_before"], "to": car["total_after"]},
            ]
            doc = next((c for c in after_docs if str(c["_id"]) == car["booking_id"]), lead)
            if abs(car["discount_after"] - car["discount_before"]) > EPSILON:
                await post("history row", doc, self.svc._record_history(
                    car["booking_id"], BookingStatus(doc["status"]), actor["id"], note, changes=changes,
                ))
        await post("discount notices", lead, self._tell(booking, lead, result, role, who, payload, when))
        for doc in after_docs:
            await self.svc._broadcast_booking_changed(doc)

    async def _tell(self, booking: dict, lead: dict, result: dict, role: str, who: dict, payload, when) -> None:
        customer_id = booking["customer_id"]
        number = result["booking_number"]
        due = result["amount_due_after"]
        discount = result["discount_after"]
        raised = discount > result["discount_before"] + EPSILON
        if discount <= EPSILON:
            line = f"The Discount On Your Booking {number} Was Removed. New Amount Due: ₹{due:g}"
        elif raised:
            line = f"A Discount Of ₹{discount:g} Was Applied To Your Booking {number}. New Amount Due: ₹{due:g}"
        else:
            line = f"Your Booking {number} Discount Is Now ₹{discount:g}. New Amount Due: ₹{due:g}"
        if result["wallet_credit"] > EPSILON:
            line += f". ₹{result['wallet_credit']:g} Was Added To Your Wallet"
        recent = await self.adjustments.find_one({
            "booking_id": result["booking_id"], "kind": "discount",
            "notified_at": {"$gte": _now() - timedelta(seconds=WHATSAPP_COOLDOWN_SECONDS)},
        })
        if recent is None:
            await self.adjustments.update_many(
                {"booking_id": result["booking_id"], "kind": "discount", "created_at": when}, {"$set": {"notified_at": _now()}},
            )
        await self.svc.notifications.notify(
            customer_id, "Discount Applied" if discount > EPSILON else "Discount Removed", line, NotificationType.BOOKING,
            result["booking_id"], send_whatsapp=recent is None, background=True,
        )
        if role == "admin":
            center = await self.svc.center_repo.find_by_id(booking["service_center_id"])
            for manager_id in await self.svc._manager_recipients(booking.get("service_center_id"), (center or {}).get("manager_id")):
                await self.svc.notifications.notify(
                    manager_id, f"Admin Changed A Discount — {number}",
                    f"{who.get('name') or 'An admin'} set the discount on {number} to ₹{discount:g}. Amount due ₹{due:g}.",
                    NotificationType.BOOKING, result["booking_id"], send_whatsapp=False, background=True,
                )

    # ------------------------------------------------------------------
    # History
    # ------------------------------------------------------------------

    async def list_adjustments(self, booking_id: str, actor: dict, limit: int = 100) -> list[dict]:
        booking = await self._load(booking_id, actor)
        rows = await self.adjustments.find({"booking_id": str(booking["_id"])}).sort([("created_at", -1), ("_id", -1)]).limit(limit).to_list(length=limit)
        return [self.adjustment_view(r) for r in rows]

    @staticmethod
    def adjustment_view(row: dict) -> dict:
        doc = serialize_doc(dict(row))
        for hidden in ("result", "key", "notified_at"):
            doc.pop(hidden, None)
        return doc
