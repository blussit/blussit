"""Commission engine (§7.13 D4–D8, D17). A completed booking earns its captain
a cut of `commission_base_paise` — a fixed percent (frozen on the booking at
first completion) or an amount set per booking, plus a hand-set override.
Posting is delta-keyed by a guarded revision on the booking (`cm:{booking}:{rev}`,
the same pattern as MoneyService.settle_captain_wallet), so completion, a later
edit, a refund, a delete/restore or a reassignment each post only the
difference — a reversal after a payout simply makes dues negative."""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from bson import ObjectId

from app.core.authz import ensure_own_center
from app.core.exceptions import BadRequestException, NotFoundException
from app.services import booking_money as bm
from app.services import payroll_terms as pt
from app.services.customer_wallet_service import in_transaction
from app.services.payroll_base import PayrollBase, ist_day, rupees_text, split_by_weight, today_ist

logger = logging.getLogger(__name__)


def pct_of(base_paise: int, percent: float) -> int:
    """percent of base, half-up to a whole paisa."""
    if base_paise <= 0 or percent <= 0:
        return 0
    return int(base_paise * percent / 100 + 0.5)


def cap_of(base_paise: int, max_percent: float) -> int:
    return int(base_paise * max_percent / 100 + 1e-9)


class CommissionMixin(PayrollBase):
    # ---------- the sync (called from every money / status path) ----------

    async def sync_commission(self, booking, session=None) -> dict | None:
        """Bring one booking's commission posting in step with its state.
        `booking` is a doc or an id. Idempotent; returns
        {booking_id, captain_id, target_paise, posted_paise, rows} when it
        changed anything, else None. Inside the caller's transaction when a
        session is given."""
        oid = booking.get("_id") if isinstance(booking, dict) else (ObjectId(str(booking)) if ObjectId.is_valid(str(booking or "")) else None)
        if oid is None:
            return None
        cfg = await self.settings(session)

        async def _do(s):
            return await self._sync_in(s, oid, cfg)

        try:
            return await in_transaction(self.db, _do, session)
        except BadRequestException:
            if session is not None:
                raise
            return await in_transaction(self.db, _do, None)  # a concurrent sync moved it first

    async def _sync_in(self, s, oid: ObjectId, cfg: dict) -> dict | None:
        b = await self.bookings.find_one({"_id": oid}, session=s)
        if not b:
            return None
        old_cid = b.get("commission_captain_id")
        posted = int(b.get("commission_posted_paise") or 0)
        completed = b.get("status") == "completed"
        if not completed and not old_cid:
            return None
        # Who earns it: the booking's captain; a manager-done job earns only for the
        # captain the manager named (D17).
        cid = old_cid if b.get("completed_by_role") == "manager" else b.get("captain_id")
        alive = completed and not b.get("is_deleted") and b.get("payment_status") not in ("refunded", "refund_due")
        captain = None
        eligible = False
        committed = False
        if cid and alive and ObjectId.is_valid(str(cid)):
            captain = await self.users.find_one(
                {"_id": ObjectId(cid), "role": "captain"}, {"pay": 1, "service_center_id": 1, "full_name": 1, "created_at": 1}, session=s,
            )
            if captain:
                committed = old_cid == cid and b.get("commission_mode_snapshot") in ("percent", "per_booking")
                legacy_stamp = b.get("captain_pay_mode") == "legacy" and b.get("captain_pay_mode_for") == cid
                eligible = (committed or pt.basis_of(captain) == "commission") and not (legacy_stamp and not committed)

        if not eligible and not old_cid:
            return None  # never earned commission, nothing to post or reverse
        base = bm.commission_base_paise(b) if eligible else 0
        target = 0
        state = "none"
        mode, rate = b.get("commission_mode_snapshot"), b.get("commission_rate_snapshot")
        set_fields: dict = {}
        unset_fields: dict = {}
        if eligible:
            if not committed:
                mode, rate = pt.commission_percent_at(captain, ist_day(b.get("completed_at")) or today_ist())
                set_fields.update({"commission_mode_snapshot": mode, "commission_rate_snapshot": rate})
                if old_cid and old_cid != cid:  # reassigned: the old captain's override doesn't carry over
                    unset_fields.update({"commission_override_paise": "", "commission_override_percent": "", "commission_override_reason": ""})
                    b = {**b, "commission_override_paise": None, "commission_override_percent": None}
            override_pct = b.get("commission_override_percent")
            override_paise = b.get("commission_override_paise")
            if override_pct is not None:
                target = pct_of(base, float(override_pct))
                if target != override_paise:
                    set_fields["commission_override_paise"] = target
            elif override_paise is not None:
                target = int(override_paise)
            elif mode == "per_booking":
                target = 0
            else:
                target = pct_of(base, float(rate or 0))
            target = min(target, cap_of(base, cfg["max_commission_percent"]))
            state = "pending" if (mode == "per_booking" and override_pct is None and override_paise is None) else "accrued"

        ops: list[tuple[str, int, str]] = []  # (captain, delta, note)
        number = b.get("booking_number")
        if old_cid and posted != 0 and (not eligible or old_cid != cid):
            ops.append((old_cid, -posted, f"Commission reversed — booking {number}"))
            posted = 0
        if eligible and target - posted != 0:
            ops.append((cid, target - posted, f"Commission for booking {number}" if target - posted > 0 else f"Commission changed — booking {number}"))
            posted = target

        # Extra / cut rows set by hand on this booking go with it when it stops earning.
        adj_ops = await self._adjustment_reversals(s, b, cid if eligible else None)

        fields = {
            "commission_base_paise": base, "commission_paid_target_paise": target, "commission_posted_paise": posted,
            "commission_state": state,
        }
        if eligible:
            fields["commission_captain_id"] = cid
        changed = {k: v for k, v in fields.items() if b.get(k) != v}
        changed.update({k: v for k, v in set_fields.items() if b.get(k) != v})
        if not ops and not adj_ops and not changed and not unset_fields:
            return None
        rev0 = int(b.get("commission_rev") or 0)
        new_rev = rev0 + len(ops) + (1 if adj_ops else 0)
        update: dict = {"$set": {**changed, "commission_rev": new_rev, "updated_at": datetime.now(timezone.utc)}}
        if unset_fields:
            update["$unset"] = unset_fields
        done = await self.bookings.update_one({"_id": oid, "commission_rev": b.get("commission_rev")}, update, session=s)
        if not done.modified_count:
            raise BadRequestException("The booking just changed — try again.")
        rows = []
        for i, (cap_id, delta, note) in enumerate(ops, start=1):
            center = await self._center_of_captain(cap_id, captain if cap_id == cid else None, s)
            row = await self.post({
                "captain_id": cap_id, "service_center_id": center, "kind": "commission", "amount_paise": delta, "booking_id": str(oid),
                "booking_number": number, "key": f"cm:{oid}:{rev0 + i}", "note": note, "actor_id": None, "actor_name": "System", "actor_role": "system",
            }, session=s)
            rows.append(row)
        for cap_id, delta in adj_ops:
            center = await self._center_of_captain(cap_id, None, s)
            await self.post({
                "captain_id": cap_id, "service_center_id": center, "kind": "commission_adjust", "amount_paise": delta, "booking_id": str(oid),
                "booking_number": number, "key": f"cmadj:{oid}:{cap_id}:{new_rev}", "note": f"Extra / cut reversed — booking {number}",
                "actor_id": None, "actor_name": "System", "actor_role": "system",
            }, session=s)
        return {"booking_id": str(oid), "captain_id": cid, "target_paise": target, "posted_paise": posted, "rows": len(rows) + len(adj_ops)}

    async def _center_of_captain(self, captain_id: str, captain: dict | None, session) -> str | None:
        if captain is None and ObjectId.is_valid(str(captain_id or "")):
            captain = await self.users.find_one({"_id": ObjectId(captain_id)}, {"service_center_id": 1}, session=session)
        return (captain or {}).get("service_center_id")

    async def _adjustment_reversals(self, s, b: dict, keep_captain: str | None) -> list[tuple[str, int]]:
        """Net commission_extra / commission_cut rows on this booking for every
        captain that should no longer carry them."""
        rows = await self.ledger.aggregate([
            {"$match": {"booking_id": str(b["_id"]), "kind": "commission_adjust"}},
            {"$group": {"_id": "$captain_id", "paise": {"$sum": "$amount_paise"}}},
        ], session=s).to_list(length=10)
        return [(r["_id"], -int(r["paise"])) for r in rows if int(r["paise"] or 0) != 0 and r["_id"] != keep_captain]

    # ---------- reading one booking's commission ----------

    async def booking_commission_row(self, b: dict, cfg: dict | None = None) -> dict:
        cfg = cfg or await self.settings()
        base = int(b.get("commission_base_paise") if b.get("commission_base_paise") is not None else bm.commission_base_paise(b))
        posted = int(b.get("commission_posted_paise") or 0)
        adj = 0
        rows = await self.ledger.aggregate([
            {"$match": {"booking_id": str(b["_id"]), "kind": "commission_adjust", "captain_id": b.get("commission_captain_id")}},
            {"$group": {"_id": None, "paise": {"$sum": "$amount_paise"}}},
        ]).to_list(length=1)
        if rows:
            adj = int(rows[0]["paise"] or 0)
        mode = b.get("commission_mode_snapshot")
        return {
            "id": str(b["_id"]), "booking_id": str(b["_id"]), "booking_number": b.get("booking_number"),
            "completed_at": b.get("completed_at").isoformat() if hasattr(b.get("completed_at"), "isoformat") else None,
            "customer_name": b.get("customer_name"), "vehicle_type": b.get("vehicle_type"), "service_name": b.get("service_name"),
            "base_paise": base, "rate_percent": b.get("commission_rate_snapshot"), "mode": mode,
            "amount_paise": posted + adj, "auto_paise": posted, "adjustments_paise": adj,
            "edited": b.get("commission_override_paise") is not None or b.get("commission_override_percent") is not None,
            "not_set": b.get("commission_state") == "pending",
            "max_paise": cap_of(base, cfg["max_commission_percent"]), "max_percent": cfg["max_commission_percent"],
            "reason": b.get("commission_override_reason"),
            "captain_id": b.get("commission_captain_id"),
        }

    # ---------- set one booking's commission by hand ----------

    async def set_booking_commission(self, booking_id: str, amount_paise: int | None, percent: float | None, reason: str, actor) -> dict:
        if not ObjectId.is_valid(booking_id or ""):
            raise NotFoundException("Booking not found")
        b = await self.bookings.find_one({"_id": ObjectId(booking_id), "is_deleted": {"$ne": True}})
        if not b:
            raise NotFoundException("Booking not found")
        ensure_own_center(actor.role, actor.service_center_id, b.get("service_center_id"))
        if b.get("status") != "completed":
            raise BadRequestException("Commission is set once the job is completed.")
        cid = b.get("commission_captain_id") if b.get("completed_by_role") == "manager" else b.get("captain_id")
        captain = await self.users.find_one({"_id": ObjectId(cid), "role": "captain"}) if cid and ObjectId.is_valid(str(cid)) else None
        if not captain:
            raise BadRequestException("This job has no captain to pay a commission to.")
        if pt.basis_of(captain) != "commission" and not (b.get("commission_captain_id") == cid and b.get("commission_mode_snapshot")):
            raise BadRequestException(f"{captain.get('full_name') or 'This captain'} isn't on commission — set their pay terms to commission first.")
        cfg = await self.settings()
        base = bm.commission_base_paise(b)
        cap = cap_of(base, cfg["max_commission_percent"])
        target = int(amount_paise) if amount_paise is not None else pct_of(base, float(percent))
        adj = int((await self.ledger.aggregate([
            {"$match": {"booking_id": booking_id, "kind": "commission_adjust", "captain_id": cid}},
            {"$group": {"_id": None, "paise": {"$sum": "$amount_paise"}}},
        ]).to_list(length=1) or [{"paise": 0}])[0]["paise"] or 0)
        if target + adj > cap:
            raise BadRequestException(self.cap_message(cfg["max_commission_percent"], cap, base, "booking"))
        who = await self.actor_info(actor)
        before = {"posted_paise": int(b.get("commission_posted_paise") or 0), "override_paise": b.get("commission_override_paise")}

        async def _do(s):
            fresh = await self.bookings.find_one({"_id": b["_id"]}, session=s)
            if fresh.get("status") != "completed" or fresh.get("is_deleted"):
                raise BadRequestException("This booking just changed — refresh and try again.")
            sets = {
                "commission_override_paise": target, "commission_override_reason": reason, "commission_override_by": who["id"],
                "commission_override_at": datetime.now(timezone.utc),
            }
            update: dict = {"$set": sets}
            if percent is not None:
                sets["commission_override_percent"] = float(percent)
            else:
                update["$unset"] = {"commission_override_percent": ""}
            if fresh.get("commission_captain_id") != cid:
                sets["commission_captain_id"] = cid
            await self.bookings.update_one({"_id": b["_id"]}, update, session=s)
            return await self._sync_in(s, b["_id"], cfg)

        result = await in_transaction(self.db, _do, None)
        after = await self.bookings.find_one({"_id": b["_id"]})
        center = captain.get("service_center_id")
        await self.audit(who, "PAYROLL_COMMISSION_SET", booking_id, {
            "captain_id": cid, "reason": reason, "base_paise": base, "before": before,
            "after": {"posted_paise": int(after.get("commission_posted_paise") or 0), "override_paise": target, "percent": percent},
        }, center)
        if base > 0 and target * 100 / base > cfg["commission_alert_percent"]:
            await self.notify_admins(
                "High commission set",
                f"{captain.get('full_name')} — booking {b.get('booking_number')}: {rupees_text(target)} is {target * 100 / base:.0f}% of {rupees_text(base)} "
                f"(set by {who['name']}). Reason: {reason}",
                booking_id,
            )
        return {**await self.booking_commission_row(after, cfg), "synced": bool(result)}

    # ---------- manager-done jobs (D17) ----------

    async def validate_done_job_commission(
        self, center_id: str, captain_id: str | None, amount_paise: int | None, default_captain_id: str | None = None,
    ) -> dict | None:
        """Checks a manager's "who washed it, and what do they earn" before the
        job is written (founder 2026-11-23: a RUPEE amount, ≤ the cap of the job's
        value, self-operated centers only). Returns {captain_id, captain,
        amount_paise, cfg} or None when nothing accrues (no captain, or a
        franchise center where the partner pays captains)."""
        center = await self.center_of(center_id)
        franchise = (self.operation_of(center).get("type") or "self_operated") == "franchise"
        if franchise:
            if amount_paise is not None:
                raise BadRequestException("Commission is managed by the franchise partner at this center.")
            return None
        cid = captain_id or (default_captain_id if amount_paise is not None else None)
        if not cid:
            if amount_paise is not None:
                raise BadRequestException("Pick the captain who did this job to record their commission.")
            return None
        captain = await self.users.find_one({"_id": ObjectId(cid), "role": "captain", "is_deleted": {"$ne": True}}) if ObjectId.is_valid(str(cid)) else None
        if not captain or captain.get("service_center_id") != center_id:
            raise BadRequestException("That captain isn't part of this service center.")
        cfg = await self.settings()
        if amount_paise is not None and pt.basis_of(captain) != "commission":
            raise BadRequestException(f"{captain.get('full_name')} isn't paid by commission — leave the commission empty.")
        return {"captain_id": str(cid), "captain": captain, "amount_paise": amount_paise, "cfg": cfg}

    @staticmethod
    def cap_message(max_percent: float, cap_paise: int, base_paise: int, what: str = "job") -> str:
        return f"Commission can't be more than {max_percent:g}% ({rupees_text(cap_paise)}) of this {rupees_text(base_paise)} {what}."

    def done_job_fields(self, spec: dict, cars: list[dict]) -> dict[str, dict]:
        """{car id: booking fields} recording the named captain and the commission
        amount the manager typed (an override), split over the cars by value."""
        out: dict[str, dict] = {}
        bases = [bm.commission_base_paise(c) for c in cars]
        amounts: list[int] = [0] * len(cars)
        if spec["amount_paise"] is not None:
            max_percent = spec["cfg"]["max_commission_percent"]
            caps = [cap_of(base, max_percent) for base in bases]
            if int(spec["amount_paise"]) > sum(caps):
                raise BadRequestException(self.cap_message(max_percent, sum(caps), sum(bases)))
            amounts = split_by_weight(int(spec["amount_paise"]), bases, caps)
        for i, car in enumerate(cars):
            fields: dict = {"commission_captain_id": spec["captain_id"]}
            if spec["amount_paise"] is not None:
                fields["commission_override_paise"] = int(amounts[i] or 0)
                fields["commission_override_reason"] = "Set when the manager recorded the job"
            out[str(car["_id"])] = fields
        return out

    async def apply_done_job_commission(self, spec: dict, booking_ids: list[str]) -> None:
        """A manager-LOGGED job: stamp the captain + override on the finished
        cars and sync their commission, one transaction per car."""
        cars = [c for c in [await self.bookings.find_one({"_id": ObjectId(i)}) for i in booking_ids] if c]
        fields_by_id = self.done_job_fields(spec, cars)
        cfg = spec["cfg"]
        for car in cars:
            cid = str(car["_id"])

            async def _do(s, cid=cid, car=car):
                await self.bookings.update_one({"_id": car["_id"]}, {"$set": fields_by_id[cid]}, session=s)
                return await self._sync_in(s, car["_id"], cfg)

            await in_transaction(self.db, _do, None)
