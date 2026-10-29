"""
Admin full edit of a booking (founder 2026-10-28): PATCH /admin/bookings/{id}.

Only an admin may change a booking in ANY state — completed and cancelled
included — and every edit:

  - names a reason (kept in the immutable history),
  - is planned first (all validation and catalogue reads), then applied in ONE
    Mongo transaction, serialised with every other change to the customer's
    visit and guarded on the booking's version (409 when it moved on),
  - recomputes the money ONCE, incrementally, from the components that
    changed (price lines, travel, discount, add-ons, tip) — so a booking whose
    stored total carries history (first-wash price, plan extras, a carried
    balance) never drifts to a formula's value — and sends the difference
    through MoneyService.on_price_change: paid beyond the new total goes back
    to the customer's wallet (ledger kind `adjustment`, capped at what they
    actually paid for this booking), a higher total becomes due,
  - never rewrites a payment: the `payments[]` / `payment_keys[]` rows
    (Razorpay-verified or cash) are append-only; a correction is a signed
    entry in `payment_adjustments[]` and in `booking_adjustments`, linked to
    the payment it corrects,
  - moves the captain's wallet by a delta ledger entry (settle_captain_wallet)
    — a completed job's earning appears, disappears or moves to another
    captain as new, reversible rows, never as an edited history,
  - leaves one `booking_adjustments` row (field-level before / after, actor,
    reason, money before / after) and one audit-log entry,
  - supports `dry_run` (the real transaction, aborted: the exact impact) and
    `idempotency_key` (a retry replays the first answer).

What it will not do (it says so, with the reason): re-open a cancelled booking,
reprice a plan-covered wash, change a cancelled booking's money (that was
settled to the wallet when it was cancelled — use the wallet adjustment), or
edit a booking in the recycle bin (restore it first).
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from bson import ObjectId
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

from app.core.exceptions import BadRequestException, ForbiddenException, NotFoundException
from app.models.enums import BookingStatus, CustomerWalletEntryKind as Kind, NotificationType
from app.schemas.booking_schema import BookingRescheduleRequest
from app.services import booking_money as bm
from app.services.booking_adjust_service import (
    EPSILON, BookingAdjustService, StaleBooking, booking_version, discountable_rupees, money_snapshot, payback_cap,
)
from app.services.booking_money import r2
from app.services.booking_service import (
    _ALLOWED_TRANSITIONS, _resolve_slot_window, _slot_cutoff_passed, _ensure_within_advance_window,
)
from app.services.money_service import received_by_method
from app.utils.serializers import serialize_doc
from app.utils.timezone import now_ist, to_ist

logger = logging.getLogger(__name__)

_OPEN = frozenset({
    BookingStatus.AWAITING_PAYMENT.value, BookingStatus.PENDING.value, BookingStatus.ASSIGNED.value, BookingStatus.RESCHEDULED.value,
})
_ON_SITE = frozenset({BookingStatus.CAPTAIN_ON_THE_WAY.value, BookingStatus.SERVICE_STARTED.value})
_STATUS_LABELS = {
    "awaiting_payment": "Awaiting Payment", "pending": "Pending", "assigned": "Assigned", "rescheduled": "Rescheduled",
    "captain_on_the_way": "Captain On The Way", "service_started": "Service Started", "completed": "Completed", "cancelled": "Cancelled",
}
MAX_ADMIN_EDITS = 60


class _Preview(Exception):
    def __init__(self, result: dict):
        super().__init__("dry run")
        self.result = result


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _label(status: str | None) -> str:
    return _STATUS_LABELS.get(status or "", (status or "").replace("_", " ").title())


def _change(car: dict, field: str, label: str, old, new) -> dict:
    return {"booking_id": str(car["_id"]), "booking_number": car.get("booking_number"), "field": field, "label": label, "from": old, "to": new}


class BookingAdminEditService:
    def __init__(self, db):
        self.db = db
        self.adj = BookingAdjustService(db)
        self.svc = self.adj.svc
        self.repo = self.svc.repo
        self.money = self.svc.money
        self.adjustments = db.booking_adjustments

    # ------------------------------------------------------------------
    # Entry
    # ------------------------------------------------------------------

    async def edit(self, booking_id: str, payload, actor: dict) -> dict:
        if actor.get("role") != "admin":
            raise ForbiddenException("Only an admin can edit a booking this way.")
        booking = await self.repo.find_by_id(booking_id, include_deleted=True) if ObjectId.is_valid(str(booking_id or "")) else None
        if not booking:
            raise NotFoundException("Booking not found")
        if booking.get("is_deleted"):
            raise BadRequestException("This booking is in the recycle bin — restore it before editing it.")
        key = f"aedit:{booking_id}:{payload.idempotency_key}" if payload.idempotency_key else None
        if key:
            done = await self.adjustments.find_one({"key": key})
            if done is not None:
                return {**(done.get("result") or {}), "already_applied": True}
        fields_asked = payload.changed_fields()
        if not fields_asked:
            raise BadRequestException("Nothing to change — pick at least one field.")
        version0 = booking_version(booking)
        if payload.expected_version and payload.expected_version != version0:
            raise StaleBooking("This booking changed while you were editing it — refresh and try again.")
        if not payload.dry_run:
            await self.adj.throttle(actor["id"], 120)
        if await self.adjustments.count_documents({"booking_id": booking_id, "kind": "admin_edit"}) >= MAX_ADMIN_EDITS:
            raise BadRequestException("This booking has been edited too many times — review its history first.")
        visit = await self.svc._visit_cars(booking) if booking.get("status") != BookingStatus.CANCELLED.value else [booking]
        if not any(str(c["_id"]) == booking_id for c in visit):
            visit = [booking]
        plan = await self._plan(booking, visit, payload)
        who = await self.svc.charges.actor(actor["id"], "admin")
        when = _now()
        await self._prepare_address(plan, booking, payload)

        async def _do(session):
            plan.reset()
            await self.svc._touch_customer(booking["customer_id"], session)
            if plan.new_customer:
                await self.svc._touch_customer(plan.new_customer["id"], session)
            fresh = {}
            for car in visit:
                row = await self.repo.find_by_id(str(car["_id"]), session=session)
                if not row:
                    raise StaleBooking("This booking changed while you were editing it — refresh and try again.")
                fresh[str(car["_id"])] = row
            lead = fresh[booking_id]
            if booking_version(lead) != version0:
                raise StaleBooking("This booking changed while you were editing it — refresh and try again.")
            result = await self._apply(plan, payload, fresh, lead, who, when, session)
            if payload.dry_run:
                raise _Preview(result)
            row = {
                "booking_id": booking_id, "booking_number": lead.get("booking_number"), "customer_id": lead["customer_id"],
                "service_center_id": lead.get("service_center_id"), "kind": "admin_edit",
                "seq": await self.adjustments.count_documents({"booking_id": booking_id}, session=session) + 1,
                "key": key or f"aedit:{booking_id}:r{int(lead.get('admin_edit_rev') or 0) + 1}",
                "actor_id": actor["id"], "actor_role": "admin", "actor_name": who.get("name"), "reason": payload.reason,
                "fields": fields_asked, "changes": result["changes"], "before": result["money"]["before"],
                "after": result["money"]["after"], "wallet_credit": result["money"]["wallet_credit"],
                "payment_adjustments": plan.payment_rows, "created_at": when, "result": result,
            }
            try:
                await self.adjustments.insert_one(row, session=session)
            except DuplicateKeyError:
                raise StaleBooking("This edit was just applied — refresh and try again.") from None
            return result

        try:
            async with await self.db.client.start_session() as session:
                result = await session.with_transaction(_do)
        except _Preview as preview:
            return {**preview.result, "dry_run": True}
        except DuplicateKeyError:
            raise BadRequestException("That change would clash with another booking of the same customer (same car and slot).") from None
        await self._after(booking, visit, plan, payload, result, actor, who)
        return result

    # ------------------------------------------------------------------
    # Planning (reads and validation; nothing is written)
    # ------------------------------------------------------------------

    async def _plan(self, booking: dict, visit: list[dict], p) -> "_Plan":
        plan = _Plan(booking, visit)
        status = booking.get("status")
        closed = status in (BookingStatus.COMPLETED.value, BookingStatus.CANCELLED.value)
        cancelled = status == BookingStatus.CANCELLED.value
        money_asked = any([
            p.service_ids is not None, p.vehicle_type, p.remove_coupon, p.remove_added_services, p.manager_discount is not None,
            p.tip_amount is not None, p.payment_adjustments, p.recompute_travel,
        ])
        if cancelled and money_asked:
            raise BadRequestException(
                "This booking was cancelled and its money already went back to the customer's wallet — "
                "use the customer's wallet adjustment for money corrections."
            )
        if cancelled and p.status and p.status != "cancelled":
            raise BadRequestException("A cancelled booking can't be re-opened — make a new booking (or restore it from the recycle bin if it was deleted).")
        center = await self.svc.center_repo.find_by_id(booking["service_center_id"])
        if not center:
            raise NotFoundException("Service center not found")
        plan.center = center
        customer_id = booking["customer_id"]

        # -- notes
        if p.customer_notes is not None:
            for car in visit:
                if car.get("status") != BookingStatus.COMPLETED.value or str(car["_id"]) == str(booking["_id"]):
                    plan.visit(car, "customer_notes", "Customer Notes", p.customer_notes)
        if p.alternate_contact_name is not None:
            plan.visit_all("alternate_contact_name", "Alternate Contact Name", p.alternate_contact_name)
        if p.alternate_contact_phone is not None:
            plan.visit_all("alternate_contact_phone", "Alternate Contact Phone", p.alternate_contact_phone)
        if p.admin_notes is not None:
            plan.target("admin_notes", "Admin Notes", p.admin_notes)

        # -- customer
        if p.customer_id and p.customer_id != customer_id:
            new = await self.db.users.find_one({"_id": ObjectId(p.customer_id), "role": "customer", "is_deleted": {"$ne": True}})
            if not new or new.get("status") in ("suspended", "inactive"):
                raise NotFoundException("That customer doesn't exist (or isn't active).")
            for car in visit:
                if car.get("subscription_id") or bm.wallet_applied_of(car) > EPSILON or r2(car.get("wallet_due_carried")) > EPSILON or r2(car.get("paid_back_total")) > EPSILON:
                    raise BadRequestException(
                        f"{car.get('booking_number')} is tied to the current customer's plan or wallet — refund or settle that first, then move it."
                    )
            plan.new_customer = {"id": str(new["_id"]), "phone": new.get("phone"), "name": new.get("full_name")}
            old = await self.db.users.find_one({"_id": ObjectId(customer_id)}, {"full_name": 1, "phone": 1}) or {}
            for car in visit:
                plan.visit(car, "customer_id", "Customer", plan.new_customer["id"], display=(old.get("full_name"), new.get("full_name")))
                plan.visit(car, "customer_phone", "Customer Phone", new.get("phone"))
            if any(bm.amount_paid_of(c) > EPSILON for c in visit):
                plan.warnings.append("The payments already made stay with this booking, but were made by the previous customer.")
            customer_id = plan.new_customer["id"]

        # -- schedule
        if p.scheduled_date is not None:
            await self._plan_schedule(plan, booking, visit, p, center, closed)

        # -- address / center
        if p.address is not None or p.recompute_center or p.recompute_travel:
            await self._plan_address(plan, booking, visit, p, center, customer_id, status)

        # -- services & prices
        if p.service_ids is not None or p.vehicle_type or p.remove_coupon or p.remove_added_services:
            await self._plan_services(plan, booking, p, customer_id)

        # -- captain
        if p.captain_id or p.clear_captain:
            await self._plan_captain(plan, booking, visit, p, status)

        # -- money inputs
        if p.manager_discount is not None:
            plan.md_target = int(p.manager_discount)
        if p.tip_amount is not None:
            plan.tip_target = int(p.tip_amount)
            plan.tip_method = p.tip_method
        elif p.tip_method:
            plan.tip_method = p.tip_method
        plan.adjustments = list(p.payment_adjustments or [])
        for a in plan.adjustments:
            if a.payment_key and a.payment_key not in (booking.get("payment_keys") or []) and not str(a.payment_key).startswith("adj:"):
                raise BadRequestException(f"There is no payment '{a.payment_key}' on this booking.")

        # -- status
        if p.status and p.status != status:
            self._plan_status(plan, booking, p, status)
        if p.completed_at is not None and (p.status == "completed" or status == BookingStatus.COMPLETED.value):
            if p.completed_at.replace(tzinfo=None) > datetime.utcnow() + timedelta(days=1):
                raise BadRequestException("The completion time can't be in the future.")
            plan.completed_at = p.completed_at
        elif p.completed_at is not None:
            raise BadRequestException("A completion time only applies to a completed booking.")
        return plan

    async def _plan_schedule(self, plan, booking, visit, p, center, closed: bool) -> None:
        policy = await self.svc.policy_service.get_policy()
        new_day = to_ist(p.scheduled_date).strftime("%Y-%m-%d")
        old_day = self.svc._seat_key_of(booking)["date"]
        slot = p.scheduled_slot
        if new_day == old_day and slot == booking["scheduled_slot"]:
            return
        try:
            start, end = _resolve_slot_window(center, p.scheduled_date, slot, policy)
        except Exception as exc:  # noqa: BLE001 — a slot that isn't one of the center's
            raise BadRequestException("That isn't one of this center's time slots.") from exc
        status = booking.get("status")
        if status in _ON_SITE:
            raise BadRequestException("The captain is already on the way or working — complete or cancel the booking before moving it.")
        movers = [c for c in visit if c.get("status") not in (BookingStatus.COMPLETED.value, BookingStatus.CANCELLED.value)]
        if status == BookingStatus.COMPLETED.value or status == BookingStatus.CANCELLED.value or not movers:
            plan.visit_all("scheduled_date", "Date", p.scheduled_date, display=(old_day, new_day), only_ids=[str(booking["_id"])])
            plan.visit_all("scheduled_slot", "Time Slot", slot, display=(booking["scheduled_slot"], slot), only_ids=[str(booking["_id"])])
            plan.visit_all("slot_start", "slot_start", start, silent=True, only_ids=[str(booking["_id"])])
            plan.visit_all("slot_end", "slot_end", end, silent=True, only_ids=[str(booking["_id"])])
            return
        _ensure_within_advance_window(p.scheduled_date, policy)
        if _slot_cutoff_passed(end, policy):
            raise BadRequestException("This slot is no longer available to book — please pick another slot.")
        from app.services.subscription_service import ensure_subscription_covers_date

        for car in movers:
            await ensure_subscription_covers_date(self.db, car.get("subscription_id"), p.scheduled_date)
        plan.move = {"date": new_day, "old_date": old_day, "slot": slot, "start": start, "end": end, "ids": [str(c["_id"]) for c in movers],
                     "req": BookingRescheduleRequest(scheduled_date=p.scheduled_date, scheduled_slot=slot)}
        plan.changes_text.append(_change(booking, "scheduled_date", "Date", old_day, new_day))
        plan.changes_text.append(_change(booking, "scheduled_slot", "Time Slot", booking["scheduled_slot"], slot))

    async def _plan_address(self, plan, booking, visit, p, center, customer_id, status) -> None:
        old = await self.svc._address_of(booking) or {}
        probe = None
        new_address = None
        distance_km = None
        if p.address is not None:
            a = p.address
            probe = {"latitude": a.latitude, "longitude": a.longitude, "pincode": a.pincode or old.get("pincode") or ""}
            plan.address_in = a
        if p.recompute_center or p.recompute_travel:
            probe = probe or {k: old.get(k) for k in ("latitude", "longitude", "pincode")}
            try:
                served, distance_km = await self.svc._resolve_service_center(probe, allow_pinless=True)
            except BadRequestException as exc:
                raise BadRequestException(f"The address can't be served: {exc.message}") from exc
            if p.recompute_center and str(served["_id"]) != booking["service_center_id"]:
                if status not in _OPEN and status != BookingStatus.CANCELLED.value:
                    raise BadRequestException("Only a booking that hasn't started can move to another center.")
                if p.scheduled_date is not None:
                    raise BadRequestException("Change the center and the time in two separate edits.")
                plan.new_center = served
        if p.address is not None:
            a = p.address
            new_address = {
                "label": old.get("label") or "Home", "line1": a.line1, "line2": None, "landmark": a.landmark,
                "city": a.city or old.get("city") or "—", "state": a.state or old.get("state") or "—",
                "pincode": a.pincode or old.get("pincode") or "", "latitude": a.latitude, "longitude": a.longitude,
            }
            plan.new_address = new_address
            plan.changes_text.append(_change(booking, "address", "Address", old.get("line1"), a.line1))
            if (old.get("latitude"), old.get("longitude")) != (a.latitude, a.longitude):
                plan.changes_text.append(_change(booking, "map_pin", "Map Pin",
                                                 f"{old.get('latitude')}, {old.get('longitude')}", f"{a.latitude}, {a.longitude}"))
        if p.recompute_travel:
            holder = next((c for c in visit if r2(c.get("travel_charge")) > EPSILON), None)
            if holder is not None and str(holder["_id"]) != str(booking["_id"]):
                raise BadRequestException(f"The travel charge sits on {holder.get('booking_number')} — recompute it there.")
            ids = list(booking.get("service_ids") or [])
            services = [s for s in [await self.svc.service_repo.find_by_id(i) for i in ids] if s]
            terms = self.svc._car_terms(services, bool(booking.get("subscription_id")))
            target = {**old, **(new_address or {})}
            charge_km, source = await self.svc.charge_distance_km(plan.new_center or center, target, float(distance_km or booking.get("distance_km") or 0))
            quote = await self.svc.pricing_service.travel_quote(charge_km)
            new_travel = float(quote["charge"]) if terms["charges_travel"] else 0.0
            plan.travel = {"charge": new_travel, "km": quote["distance_km"], "source": source, "distance_km": float(distance_km or 0)}
            plan.changes_text.append(_change(booking, "travel_charge", "Travel Charge", r2(booking.get("travel_charge")), new_travel))

    async def _plan_services(self, plan, booking, p, customer_id) -> None:
        svc = self.svc
        if booking.get("subscription_id") and (p.service_ids is not None or p.vehicle_type):
            raise BadRequestException("This wash is covered by the customer's plan, so its service or car can't be repriced. Cancel it and book again.")
        car = booking
        fields: dict = {}
        subtotal = r2(car.get("subtotal"))
        coupon_discount = max(0.0, r2(car.get("discount_amount")) - r2(car.get("manager_discount")))
        earning = r2(car.get("captain_earning"))
        if p.service_ids is not None or p.vehicle_type:
            edit = SimpleNamespace(vehicle_id=None, vehicle_type=p.vehicle_type, service_ids=p.service_ids, service_quantities=p.service_quantities)
            cplan = await svc._edit_car_plan(car, edit, customer_id)
            first_time = bool(car.get("first_time_eligible")) if car.get("first_time_eligible") is not None else await svc._first_time_of(car, {str(car["_id"])})
            priced = await svc._price_services(
                customer_id=customer_id, services=cplan["services"], quantities=cplan["quantities"], combo=cplan["combo"],
                vehicle_type=cplan["vehicle_type"], vehicle_id=cplan["vehicle_id"], first_time_eligible=first_time,
                subscription_id=None, coupon_code=None, scheduled_date=car.get("scheduled_date"),
            )
            subtotal, coupon_discount = float(priced["subtotal"]), 0.0
            if car.get("coupon_code") and not p.remove_coupon:
                coupon_discount, why = await svc._held_coupon_discount(
                    car, subtotal, cplan["services"], cplan["quantities"], cplan["vehicle_type"], first_time,
                )
                if why:
                    plan.warnings.append(f"Coupon {car['coupon_code']} no longer applies ({why}) — it was removed.")
                    fields["coupon_code"] = None
                    plan.release_coupon = car["coupon_code"]
            fields.update({
                "vehicle_id": cplan["vehicle_id"], "vehicle_type": cplan["vehicle_type"], "vehicle_label": cplan["vehicle_label"],
                "vehicle_registration_number": cplan["vehicle_registration_number"], "visit_line_key": cplan["visit_line_key"],
                "service_ids": cplan["service_ids"], "combo_id": car.get("combo_id") if cplan["combo"] else None,
                "service_quantities": {sid: q for sid, q in cplan["quantities"].items() if q > 1},
                "subtotal": round(subtotal, 2), "duration_minutes": cplan["duration_minutes"],
            })
            split = await svc.pricing_service.calculate_split(
                subtotal, float(car.get("distance_km") or 0), cplan["services"][0] if cplan["services"] else None,
            )
            fields.update({
                "captain_travel_pay": split["captain_travel_pay"], "captain_service_pay": split["captain_service_pay"],
                "captain_earning": split["captain_earning"],
            })
            earning = float(split["captain_earning"] or 0)
            names = [s.get("name") for s in cplan["services"]]
            old_names = [s.get("name") for s in [await svc.service_repo.find_by_id(i) for i in (car.get("service_ids") or [])] if s]
            plan.changes_text.append(_change(car, "services", "Services", ", ".join(map(str, old_names)), ", ".join(map(str, names))))
            if cplan["vehicle_type"] != car.get("vehicle_type"):
                plan.changes_text.append(_change(car, "vehicle_type", "Car Type", car.get("vehicle_label"), cplan["vehicle_label"]))
        if p.remove_coupon and car.get("coupon_code"):
            coupon_discount = 0.0
            fields["coupon_code"] = None
            plan.release_coupon = car["coupon_code"]
            plan.changes_text.append(_change(car, "coupon_code", "Coupon", car.get("coupon_code"), None))
        if p.remove_added_services:
            added = list(car.get("added_services") or [])
            idx = sorted(set(p.remove_added_services))
            if any(i < 0 or i >= len(added) for i in idx):
                raise BadRequestException("There's no such added service on this booking.")
            gone = [added[i] for i in idx]
            kept = [e for i, e in enumerate(added) if i not in idx]
            removed_total = r2(sum(float(e.get("amount") or 0) for e in gone))
            fields["added_services"] = kept
            fields["added_services_total"] = max(0.0, r2(r2(car.get("added_services_total")) - removed_total))
            fields["duration_minutes"] = max(30, int(fields.get("duration_minutes") or car.get("duration_minutes") or 60)
                                             - sum(int(e.get("duration_minutes") or 0) for e in gone))
            plan.changes_text.append(_change(car, "added_services", "Added Services", ", ".join(str(e.get("name")) for e in gone), None))
        plan.service_fields = fields
        plan.subtotal_new = subtotal
        plan.coupon_discount_new = min(coupon_discount, subtotal)
        plan.earning_new = earning

    async def _plan_captain(self, plan, booking, visit, p, status) -> None:
        if status in _ON_SITE and not p.force_status:
            raise BadRequestException("The captain is already on the way or working — tick 'Override The Status Rules' to change who is on the job.")
        if p.captain_id:
            captain = await self.db.users.find_one({"_id": ObjectId(p.captain_id), "role": "captain", "is_deleted": {"$ne": True}})
            if not captain or captain.get("status") != "active":
                raise BadRequestException("That captain doesn't exist or isn't active.")
            if captain.get("service_center_id") != (plan.new_center and str(plan.new_center["_id"]) or booking["service_center_id"]):
                raise BadRequestException("That captain belongs to a different service center than the booking.")
            plan.captain = {"id": p.captain_id, "name": captain.get("full_name")}
        else:
            plan.captain = {"id": None, "name": None}

    def _plan_status(self, plan, booking, p, status) -> None:
        new = p.status
        if new == BookingStatus.CANCELLED.value:
            if status == BookingStatus.COMPLETED.value:
                raise BadRequestException("A completed booking can't be cancelled — refund the customer through their wallet instead.")
            plan.cancel = True
            plan.changes_text.append(_change(booking, "status", "Status", _label(status), _label(new)))
            return
        if not p.force_status and new not in _ALLOWED_TRANSITIONS.get(status, set()) and not (new == BookingStatus.PENDING.value and status == BookingStatus.RESCHEDULED.value):
            raise BadRequestException(
                f"{_label(status)} → {_label(new)} isn't a normal step. Tick 'Override The Status Rules' if you really mean it."
            )
        plan.status = new
        plan.changes_text.append(_change(booking, "status", "Status", _label(status), _label(new)))

    async def _prepare_address(self, plan: "_Plan", booking: dict, p) -> None:
        """The customer's saved address for the new place — found or created
        BEFORE the transaction: AddressService writes the customer's user
        document, which the transaction already holds (writing it from inside
        would wait on itself). A preview never saves one."""
        if plan.new_address is None:
            return
        owner = plan.new_customer["id"] if plan.new_customer else booking["customer_id"]
        if p.dry_run:
            plan.saved_address = {"_id": ObjectId(), "owner_id": owner, **plan.new_address}
            return
        from app.schemas.profile_schema import AddressCreateRequest
        from app.services.profile_service import AddressService

        addresses = AddressService(self.db)
        match, has_saved = await addresses.find_same_place(owner, plan.new_address)
        if match:
            plan.saved_address = match
            return
        made = await addresses.create(owner, AddressCreateRequest(
            label="Home", line1=plan.new_address["line1"], landmark=plan.new_address.get("landmark"), city=plan.new_address["city"],
            state=plan.new_address["state"], pincode=plan.new_address["pincode"] or "000000",
            latitude=plan.new_address["latitude"], longitude=plan.new_address["longitude"], is_default=not has_saved,
        ))
        plan.saved_address = await self.svc.address_repo.find_by_id(made["id"])

    # ------------------------------------------------------------------
    # Applying (inside the transaction)
    # ------------------------------------------------------------------

    async def _apply(self, plan: "_Plan", p, fresh: dict, lead: dict, who: dict, when: datetime, session) -> dict:
        svc = self.svc
        booking_id = str(lead["_id"])
        status_before = lead.get("status")
        before = money_snapshot(lead)
        changes = list(plan.changes_text)
        captain_before = lead.get("captain_id")
        visit_ids = list(fresh)
        center_id = str(plan.new_center["_id"]) if plan.new_center else lead["service_center_id"]
        sets: dict[str, dict] = {cid: {} for cid in visit_ids}
        for cid, fields in plan.visit_fields.items():
            sets[cid].update(fields)
        changes.extend(plan.visit_changes)
        target_set = sets[booking_id]
        target_set.update(plan.target_fields)
        target_set.update(plan.service_fields)

        # -- address (saved for the customer beforehand; frozen on every live car)
        if plan.saved_address is not None:
            from app.services.profile_service import address_snapshot

            snap = address_snapshot(plan.saved_address)
            for cid, row in fresh.items():
                if row.get("status") != BookingStatus.CANCELLED.value:
                    sets[cid].update({"address_id": str(plan.saved_address["_id"]), "address_snapshot": snap})

        # -- travel
        if plan.travel is not None:
            target_set.update({
                "travel_charge": plan.travel["charge"], "travel_charge_km": plan.travel["km"] if plan.travel["charge"] else None,
                "travel_charge_source": plan.travel["source"] if plan.travel["charge"] else None,
            })

        # -- seat moves: schedule, then center
        rows = [fresh[cid] for cid in visit_ids]
        move_ctx = None
        moved_ids: set[str] = set()
        if plan.move:
            mv = plan.move
            await svc._raise_if_customer_conflict(
                (plan.new_customer or {}).get("id") or lead["customer_id"], mv["start"], mv["end"], booking_id, lead.get("booking_group_id"), session=session,
            )
            await svc._backfill_seats(rows, session)
            move_ctx = await svc._seat_move_begin(session, rows, mv["ids"], plan.center, mv["date"], mv["slot"])
            for cid in mv["ids"]:
                car = fresh[cid]
                rf = svc._reschedule_fields(car, mv["req"], mv["start"], mv["end"])
                if car.get("status") == BookingStatus.AWAITING_PAYMENT.value:
                    rf.update({"status": BookingStatus.AWAITING_PAYMENT.value, "awaiting_assignment_since": None})
                sets[cid].update(rf)
                moved_ids.add(cid)
        if plan.new_center is not None:
            changes.append(_change(lead, "service_center_id", "Service Center", plan.center.get("name"), plan.new_center.get("name")))
            if status_before in _OPEN:
                for cid, car in fresh.items():
                    if car.get("status") == BookingStatus.CANCELLED.value:
                        continue
                    sets[cid].update({"service_center_id": center_id})
                    if car.get("status") in (BookingStatus.ASSIGNED.value,):
                        sets[cid].update({"captain_id": None, "status": BookingStatus.PENDING.value, "assigned_at": None, "estimated_start_at": None})
            else:
                for cid in visit_ids:
                    sets[cid].update({"service_center_id": center_id})

        # -- captain
        wallet_move = None
        if plan.captain is not None:
            new_captain = plan.captain["id"]
            if new_captain:
                await svc._ensure_captain_assignable(new_captain, center_id, session)
            for cid, car in fresh.items():
                if car.get("status") in (BookingStatus.CANCELLED.value, BookingStatus.COMPLETED.value) and cid != booking_id:
                    continue
                s = sets[cid]
                s["captain_id"] = new_captain
                if new_captain and car.get("status") in (BookingStatus.PENDING.value, BookingStatus.RESCHEDULED.value) and not plan.status:
                    s.update({"status": BookingStatus.ASSIGNED.value, "assigned_at": now_ist()})
                if not new_captain and car.get("status") == BookingStatus.ASSIGNED.value and not plan.status:
                    s.update({"status": BookingStatus.PENDING.value, "assigned_at": None, "estimated_start_at": None})
            if new_captain != captain_before:
                changes.append(_change(lead, "captain_id", "Captain", captain_before, new_captain))
                if bm.captain_wallet_posted(lead) != 0 and captain_before:
                    wallet_move = captain_before

        # -- status
        if plan.status:
            target_set["status"] = plan.status
            if plan.status == BookingStatus.COMPLETED.value:
                done_at = plan.completed_at or when
                target_set.update({"completed_at": done_at, "closed_at": done_at, "completed_by_role": lead.get("completed_by_role") or "admin"})
            elif status_before == BookingStatus.COMPLETED.value:
                target_set.update({"completed_at": None, "closed_at": None})
            if plan.status in (BookingStatus.CAPTAIN_ON_THE_WAY.value, BookingStatus.SERVICE_STARTED.value, BookingStatus.ASSIGNED.value):
                effective_captain = (plan.captain or {}).get("id", lead.get("captain_id")) if plan.captain is not None else lead.get("captain_id")
                if not effective_captain:
                    raise BadRequestException("Pick a captain for that status.")
        elif plan.completed_at is not None:
            target_set.update({"completed_at": plan.completed_at, "closed_at": plan.completed_at})
            changes.append(_change(lead, "completed_at", "Completed At", str(lead.get("completed_at")), str(plan.completed_at)))

        # -- money (only for a booking that is still live)
        money_result = None
        merged = {**lead, **target_set}
        if lead.get("status") != BookingStatus.CANCELLED.value:
            money_result = await self._money(plan, p, lead, merged, target_set, who, when, session, changes)
        # -- carry the money fields of other cars (none) and write every car
        written: dict[str, dict] = {}
        for cid, row in fresh.items():
            fields = dict(sets[cid])
            if cid == booking_id:
                fields["admin_edited_at"] = when
                fields["admin_edited_by"] = who.get("id")
                fields["updated_at"] = when
            elif fields:
                fields["updated_at"] = when
            if not fields:
                written[cid] = row
                continue
            guard = {"_id": row["_id"], "is_deleted": {"$ne": True}, "status": row.get("status"), "scheduled_slot": row.get("scheduled_slot")}
            update: dict = {"$set": fields}
            if cid == booking_id:
                guard.update({"total_amount": row.get("total_amount"), "money_rev": row.get("money_rev"), "amount_paid": row.get("amount_paid")})
                update["$inc"] = {"admin_edit_rev": 1}
                if plan.payment_rows:
                    update["$push"] = {"payment_adjustments": {"$each": plan.payment_rows}}
            done = await self.repo.collection.find_one_and_update(guard, update, session=session, return_document=ReturnDocument.AFTER)
            if done is None:
                raise StaleBooking("This booking changed while you were editing it — refresh and try again.")
            written[cid] = done
        if move_ctx is not None:
            if await svc._seat_move_finish(session, move_ctx, plan.move["ids"], moved_ids, center_id, plan.move["date"], plan.move["slot"]):
                written[plan.move["ids"][0]] = await self.repo.find_by_id(plan.move["ids"][0], session=session)
        if plan.new_center is not None and status_before in _OPEN:
            # Free the old seat, take one at the new center on the same day and slot.
            old_lead = fresh[booking_id]
            holder = next((c for c in rows if c.get("holds_seat") is True), None)
            if holder is not None:
                await svc._release_seat(await self.repo.find_by_id(str(holder["_id"]), session=session) or holder, session)
            if not plan.move:
                key = svc._seat_key_of(old_lead)
                await svc._take_seat(session, plan.new_center, booking_id, key["date"], old_lead["scheduled_slot"])
        # -- captain wallet: reverse the old captain's posting, then settle as the booking now stands
        if wallet_move:
            posted = bm.captain_wallet_posted(lead)
            from app.services.wallet_service import WalletService

            await WalletService(self.db).post_delta(
                wallet_move, -posted, booking_id, f"Reversed — {lead.get('booking_number')} edited by an admin",
                session=session, key=f"cwmove:{booking_id}:r{int(lead.get('admin_edit_rev') or 0) + 1}",
            )
            await self.repo.collection.update_one({"_id": lead["_id"]}, {"$set": {"captain_wallet_posted": 0.0}}, session=session)
        captain_delta = 0.0
        after_doc = await self.repo.find_by_id(booking_id, session=session)
        if lead.get("status") != BookingStatus.CANCELLED.value and not plan.cancel:
            target = bm.captain_wallet_target(after_doc)
            posted_now = bm.captain_wallet_posted(after_doc)
            captain_delta = r2(target - posted_now) if not wallet_move else r2(target)
            if p.dry_run:
                pass
            else:
                await self.money.settle_captain_wallet([booking_id], session=session)
        after_doc = await self.repo.find_by_id(booking_id, session=session)
        after = money_snapshot(after_doc)
        credit = r2((money_result or {}).get("wallet_credit"))
        due_up = r2(max(0.0, after["amount_due"] - before["amount_due"]))
        if plan.cancel:
            refund = bm.refundable(after_doc)
            warnings = [*plan.warnings, f"It will be cancelled (no charge) and ₹{refund:g} returned to the customer's wallet."]
        else:
            refund = 0.0
            warnings = list(plan.warnings)
        result = {
            "booking_id": booking_id, "booking_number": lead.get("booking_number"), "changed": True, "dry_run": bool(p.dry_run),
            "changes": changes, "warnings": warnings,
            "money": {
                "before": before, "after": after, "customer_owes": due_up, "wallet_credit": credit,
                "wallet_refund_on_cancel": refund, "captain_wallet_delta": captain_delta,
            },
            "version": booking_version(after_doc), "already_applied": False,
            "message": self._summary(before, after, credit, due_up, plan.cancel),
        }
        plan.money_result = money_result
        plan.after_doc = after_doc
        plan.written = written
        return result

    async def _money(self, plan, p, lead, merged, target_set, who, when, session, changes) -> dict | None:
        old_md = r2(lead.get("manager_discount"))
        old_tip = r2(lead.get("tip_amount"))
        subtotal = plan.subtotal_new if plan.subtotal_new is not None else r2(lead.get("subtotal"))
        total_disc_old = r2(lead.get("discount_amount"))
        if plan.service_fields or plan.md_target is not None or plan.coupon_discount_new is not None:
            coupon_part = plan.coupon_discount_new if plan.coupon_discount_new is not None else max(0.0, total_disc_old - old_md)
            md = float(plan.md_target) if plan.md_target is not None else old_md
            if plan.md_target is None:
                md = min(old_md, max(0.0, subtotal - coupon_part))
            else:
                limit = max(0.0, subtotal - coupon_part)
                if md > limit + EPSILON:
                    raise BadRequestException(f"The discount can't be more than the price (₹{int(limit):g}).")
            discount_total = round(coupon_part + md, 2)
        else:
            md = old_md
            discount_total = total_disc_old
        old_base = max(0.0, r2(lead.get("subtotal")) - total_disc_old)
        new_base = max(0.0, subtotal - discount_total)
        travel_new = r2(target_set.get("travel_charge", lead.get("travel_charge")))
        d_travel = r2(travel_new - r2(lead.get("travel_charge")))
        d_added = r2(r2(target_set.get("added_services_total", lead.get("added_services_total"))) - r2(lead.get("added_services_total")))
        # a tip belongs to a job that is done
        tip_new = old_tip if plan.tip_target is None else float(plan.tip_target)
        will_be_done = (plan.status or lead.get("status")) == BookingStatus.COMPLETED.value
        if plan.tip_target is not None and tip_new > 0 and not will_be_done:
            raise BadRequestException("A tip can only be recorded on a completed booking.")
        d_tip = r2(tip_new - old_tip)
        earning_old = r2(lead.get("captain_earning"))
        earning_new = plan.earning_new if plan.earning_new is not None else earning_old
        total_old = bm.total_of(lead)
        total_new = r2(total_old + (new_base - old_base) + d_travel + d_added + d_tip)
        if total_new < -EPSILON:
            raise BadRequestException("That would make the total negative.")
        total_new = max(0.0, total_new)
        if plan.md_target is not None or abs(md - old_md) > EPSILON:
            target_set.update({
                "manager_discount": md, "manager_discount_by": who.get("id"), "manager_discount_at": when,
                "manager_discount_reason": p.reason,
            })
            changes.append(_change(lead, "manager_discount", "Discount", old_md, md))
        if plan.service_fields or plan.md_target is not None or plan.coupon_discount_new is not None:
            target_set["discount_amount"] = discount_total
            if plan.coupon_discount_new is not None and abs(plan.coupon_discount_new - max(0.0, total_disc_old - old_md)) > EPSILON:
                changes.append(_change(lead, "coupon_discount", "Coupon Discount", max(0.0, total_disc_old - old_md), plan.coupon_discount_new))
        if plan.tip_target is not None and abs(d_tip) > EPSILON:
            target_set.update({"tip_amount": tip_new, "tip_updated_by": who.get("id"), "tip_updated_at": when})
            changes.append(_change(lead, "tip_amount", "Tip", old_tip, tip_new))
        method = plan.tip_method or lead.get("tip_method") or "cash"
        if plan.tip_method and tip_new > 0:
            target_set["tip_method"] = method
        if tip_new <= 0 and plan.tip_target is not None:
            target_set["tip_method"] = None
        target_set.update({
            "total_amount": total_new,
            "platform_earning": r2(r2(lead.get("platform_earning")) + (total_new - total_old) - (earning_new - earning_old)),
        })
        if abs(total_new - total_old) > EPSILON:
            changes.append(_change(lead, "total_amount", "Total", total_old, total_new))
        # money received: the tip handed over with a manager's bill, then the admin's corrections
        cash, online = received_by_method(lead)
        manager_done = lead.get("completed_by_role") == "manager"
        paid_adjust = 0.0
        if manager_done and abs(d_tip) > EPSILON:
            old_bucket = "paid_online" if (lead.get("tip_method") == "online") else "paid_cash"
            if lead.get("tip_method") is None:
                old_bucket = "paid_cash" if lead.get("payment_method") == "cash" else "paid_online"
            buckets = {"paid_cash": cash, "paid_online": online}
            buckets[old_bucket] = r2(buckets[old_bucket] - old_tip)
            buckets["paid_online" if method == "online" else "paid_cash"] = r2(buckets["paid_online" if method == "online" else "paid_cash"] + tip_new)
            cash, online = buckets["paid_cash"], buckets["paid_online"]
            paid_adjust += d_tip
        rows = []
        cap_cash = r2(lead.get("captain_cash_collected")) if lead.get("captain_cash_collected") is not None else bm.captain_cash_held(lead)
        rev = int(lead.get("admin_edit_rev") or 0) + 1
        for i, a in enumerate(plan.adjustments):
            paid_adjust += a.delta
            if a.method == "cash":
                cash = r2(cash + a.delta)
            else:
                online = r2(online + a.delta)
            if a.affects_captain_cash:
                cap_cash = r2(cap_cash + a.delta)
            rows.append({
                "key": f"adj:{lead['_id']}:{rev}:{i}", "amount": a.delta, "method": a.method, "adjusts": a.payment_key,
                "note": a.note, "reason": p.reason, "at": when, "by": who.get("id"), "role": "admin",
            })
            changes.append(_change(lead, "payment", "Payment Correction", a.payment_key or "Received", f"{'+' if a.delta > 0 else '−'}₹{abs(a.delta):g} ({a.method})"))
        plan.payment_rows = rows
        if cash < -EPSILON or online < -EPSILON:
            raise BadRequestException("That would take more out of the cash or online payments than was ever received.")
        cap = max(0.0, r2(bm.paid_by_customer(lead) + paid_adjust - r2(lead.get("paid_back_total"))))
        money = await self.money.on_price_change(
            lead, {**merged, "total_amount": total_new}, actor=who, reason="Edited by an admin", session=session,
            paid_adjust=paid_adjust, credit_cap=cap, kind=Kind.ADJUSTMENT,
        )
        target_set.update(money["fields"])
        target_set.update({"paid_cash": max(0.0, cash), "paid_online": max(0.0, online)})
        if plan.adjustments and any(a.affects_captain_cash for a in plan.adjustments):
            target_set["captain_cash_collected"] = max(0.0, cap_cash)
        if abs(money["fields"]["amount_paid"] - bm.amount_paid_of(lead)) > EPSILON:
            changes.append(_change(lead, "amount_paid", "Amount Paid", bm.amount_paid_of(lead), money["fields"]["amount_paid"]))
        if plan.status == BookingStatus.AWAITING_PAYMENT.value and money["amount_due"] <= EPSILON:
            raise BadRequestException("Nothing is due on this booking, so it can't be 'Awaiting Payment'.")
        return money

    @staticmethod
    def _summary(before: dict, after: dict, credit: float, due_up: float, cancel: bool) -> str:
        bits = [f"Total ₹{before['total']:g} → ₹{after['total']:g}"]
        if after["amount_due"] != before["amount_due"]:
            bits.append(f"amount due ₹{before['amount_due']:g} → ₹{after['amount_due']:g}")
        if credit > EPSILON:
            bits.append(f"₹{credit:g} goes back to the customer's wallet")
        if due_up > EPSILON:
            bits.append(f"the customer owes ₹{due_up:g} more")
        if cancel:
            bits.append("the booking will be cancelled")
        return ". ".join(bits) + "."

    # ------------------------------------------------------------------
    # After the commit
    # ------------------------------------------------------------------

    async def _after(self, booking, visit, plan, payload, result, actor, who) -> None:
        svc = self.svc
        post = svc._post_commit
        lead = plan.after_doc or booking
        bid = str(booking["_id"])
        if plan.money_result:
            await post("price-change money", lead, self.money.after_price_change([plan.money_result], "Edited by an admin"))
        if plan.release_coupon:
            await post("coupon release", lead, svc.coupon_service.reverse_usage(plan.release_coupon, booking["customer_id"], bid))
        if plan.status == BookingStatus.COMPLETED.value and booking.get("status") != BookingStatus.COMPLETED.value:
            await post("completion effects", lead, svc._after_visit_completed(lead["customer_id"], [lead], lead.get("completed_at") or _now(), send_whatsapp=False))
        if plan.cancel:
            from app.schemas.booking_schema import BookingCancelRequest

            try:
                await svc.cancel_booking(
                    bid, BookingCancelRequest(reason=f"Cancelled by an admin: {payload.reason}"[:300]), actor["id"], "admin", None,
                )
            except BadRequestException as exc:
                result.setdefault("warnings", []).append(f"The other edits were saved, but the cancel was refused: {exc.message}")
        from app.services.audit_service import AuditService

        await post("admin edit audit", lead, AuditService(self.db).log_action(
            actor["id"], "admin", "ADMIN_EDIT_BOOKING", "bookings", bid,
            {"reason": payload.reason, "fields": payload.changed_fields(), "changes": result["changes"],
             "before": result["money"]["before"], "after": result["money"]["after"], "wallet_credit": result["money"]["wallet_credit"],
             "idempotency_key": payload.idempotency_key},
            service_center_id=lead.get("service_center_id"),
        ))
        await post("history row", lead, svc._record_history(
            bid, BookingStatus(lead["status"]), actor["id"], f"Edited by admin {who.get('name') or ''} — {payload.reason}".strip(),
            changes=[{k: c[k] for k in ("booking_id", "booking_number", "field", "from", "to") if k in c} for c in result["changes"]][:30],
        ))
        await post("admin edit notices", lead, self._tell(booking, lead, plan, result, who))
        for doc in (plan.written or {}).values():
            await svc._broadcast_booking_changed(doc)
        if plan.move:
            await svc._broadcast_slots_changed(booking["service_center_id"], plan.move["old_date"])
            await svc._broadcast_slots_changed(lead["service_center_id"], plan.move["date"])

    async def _tell(self, booking, lead, plan, result, who) -> None:
        svc = self.svc
        number = booking.get("booking_number")
        center = await svc.center_repo.find_by_id(booking["service_center_id"])
        fields = ", ".join(sorted({c["label"] for c in result["changes"]})) or "details"
        for manager_id in await svc._manager_recipients(booking.get("service_center_id"), (center or {}).get("manager_id")):
            await svc.notifications.notify(
                manager_id, f"Admin Edited Booking — {number}", f"{who.get('name') or 'An admin'} changed {fields} on {number}.",
                NotificationType.BOOKING, str(booking["_id"]), send_whatsapp=False, background=True,
            )
        money = result["money"]
        disc = next((c for c in result["changes"] if c["field"] == "manager_discount"), None)
        if disc is not None and lead.get("status") != BookingStatus.CANCELLED.value:
            new_d, old_d = float(disc["to"] or 0), float(disc["from"] or 0)
            due = money["after"]["amount_due"]
            if new_d <= 0:
                line = f"The Discount On Your Booking {number} Was Removed. New Amount Due: ₹{due:g}"
            elif new_d > old_d:
                line = f"A Discount Of ₹{new_d:g} Was Applied To Your Booking {number}. New Amount Due: ₹{due:g}"
            else:
                line = f"Your Booking {number} Discount Is Now ₹{new_d:g}. New Amount Due: ₹{due:g}"
            if money["wallet_credit"] > EPSILON:
                line += f". ₹{money['wallet_credit']:g} Was Added To Your Wallet"
            await svc.notifications.notify(
                lead["customer_id"], "Discount Applied" if new_d > 0 else "Discount Removed", line, NotificationType.BOOKING,
                str(booking["_id"]), send_whatsapp=True, background=True,
            )
        customer_facing = {"Date", "Time Slot", "Address", "Services", "Car Type", "Total", "Status", "Tip", "Payment Correction"}
        labels = {c["label"] for c in result["changes"]} - ({"Total"} if disc is not None else set())
        if customer_facing & labels and lead.get("status") != BookingStatus.CANCELLED.value:
            line = f"Your Booking {number} Was Updated By Blussit."
            if abs(money["after"]["total"] - money["before"]["total"]) > EPSILON:
                line += f" New Total: ₹{money['after']['total']:g}."
            if money["after"]["amount_due"] > EPSILON:
                line += f" Amount Due: ₹{money['after']['amount_due']:g}."
            if money["wallet_credit"] > EPSILON:
                line += f" ₹{money['wallet_credit']:g} Was Added To Your Wallet."
            await svc.notifications.notify(
                lead["customer_id"], "Booking Updated", line, NotificationType.BOOKING, str(booking["_id"]), send_whatsapp=False, background=True,
            )
        old_captain = booking.get("captain_id")
        if old_captain and old_captain != lead.get("captain_id"):
            await svc.notifications.notify(
                old_captain, "Job Changed", f"{number} is no longer yours — an admin changed it.", NotificationType.BOOKING,
                str(booking["_id"]), send_whatsapp=False, background=True,
            )
        if lead.get("captain_id") and lead.get("captain_id") != old_captain:
            await svc.notifications.notify(
                lead["captain_id"], "New Job", f"{number} was assigned to you by an admin.", NotificationType.BOOKING,
                str(booking["_id"]), send_whatsapp=False, background=True,
            )

    # ------------------------------------------------------------------
    # History
    # ------------------------------------------------------------------

    async def history(self, booking_id: str, limit: int = 100) -> list[dict]:
        rows = await self.adjustments.find({"booking_id": booking_id}).sort([("created_at", -1), ("_id", -1)]).limit(limit).to_list(length=limit)
        return [BookingAdjustService.adjustment_view(r) for r in rows]


class _Plan:
    """What an admin edit will do, decided before the transaction."""

    def __init__(self, booking: dict, visit: list[dict]):
        self.booking = booking
        self.visit_ids = [str(c["_id"]) for c in visit]
        self.visit_fields: dict[str, dict] = {}
        self.visit_changes: list[dict] = []
        self.target_fields: dict = {}
        self.service_fields: dict = {}
        self.changes_text: list[dict] = []
        self.warnings: list[str] = []
        self.center: dict | None = None
        self.new_center: dict | None = None
        self.new_customer: dict | None = None
        self.new_address: dict | None = None
        self.saved_address: dict | None = None
        self.address_in = None
        self.travel: dict | None = None
        self.move: dict | None = None
        self.captain: dict | None = None
        self.status: str | None = None
        self.cancel = False
        self.completed_at = None
        self.md_target: int | None = None
        self.tip_target: int | None = None
        self.tip_method: str | None = None
        self.adjustments: list = []
        self.payment_rows: list[dict] = []
        self.subtotal_new: float | None = None
        self.coupon_discount_new: float | None = None
        self.earning_new: float | None = None
        self.release_coupon: str | None = None
        self.money_result: dict | None = None
        self.after_doc: dict | None = None
        self.written: dict | None = None
        self._car_by_id = {str(c["_id"]): c for c in visit}

    def reset(self) -> None:
        """A retried transaction starts clean (the plan itself is read-only)."""
        self.payment_rows = []
        self.money_result = None
        self.after_doc = None
        self.written = None

    def target(self, field: str, label: str, value) -> None:
        old = self.booking.get(field)
        if old != value:
            self.target_fields[field] = value
            self.changes_text.append(_change(self.booking, field, label, old, value))

    def visit(self, car: dict, field: str, label: str, value, *, display=None, silent: bool = False) -> None:
        old = car.get(field)
        if old == value:
            return
        self.visit_fields.setdefault(str(car["_id"]), {})[field] = value
        if not silent:
            a, b = display if display else (old, value)
            self.visit_changes.append(_change(car, field, label, a, b))

    def visit_all(self, field: str, label: str, value, *, display=None, silent: bool = False, only_ids: list[str] | None = None) -> None:
        first = True
        for cid in only_ids or self.visit_ids:
            car = self._car_by_id[cid]
            if car.get("status") == BookingStatus.CANCELLED.value:
                continue
            self.visit(car, field, label, value, display=display, silent=silent or not first)
            first = False
