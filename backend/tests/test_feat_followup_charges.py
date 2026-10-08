"""Follow-up 2026-10-07 — legacy late-cancellation charges under the wallet
model. A charge still riding on a booking from the old "added to your next
booking" model ("applied") is settled through the customer wallet when that
booking goes away — never handed back to "open", which nothing claims any
more — with the same stable key the boot migration uses (`charge:{id}`), so
no replay or later migration debits it twice."""
from datetime import datetime, timezone

import pytest
from bson import ObjectId

from app.services.booking_service import BookingService
from app.services.customer_charge_service import CustomerChargeService
from app.services.money_service import migrate_open_charges_to_wallet
from tests import test_feat_booking_helpers as fb

pytestmark = pytest.mark.asyncio


async def _carrying_legacy_charge(db, s: dict, amount: float = 50.0) -> tuple[dict, ObjectId]:
    """A booking created before the wallet: it carries an old late-cancel
    charge in its total (status "applied", cancellation_charge_ids)."""
    b = await fb.book(db, s["cu"], s["when"], s["keys"][0])
    charge_id = ObjectId()
    now = datetime.now(timezone.utc)
    await db.customer_charges.insert_one({
        "_id": charge_id, "customer_id": s["cu"]["id"], "service_center_id": s["center_id"], "kind": "late_cancellation",
        "visit_key": f"legacy:{charge_id}", "source_booking_id": str(ObjectId()), "source_booking_number": "BK-OLD-1",
        "tier": "under_1h", "amount": amount, "original_amount": amount, "status": "applied",
        "applied_to_booking_id": b["id"], "applied_to_booking_number": b["booking_number"],
        "created_at": now, "updated_at": now, "history": [],
    })
    await db.bookings.update_one({"_id": ObjectId(b["id"])}, {
        "$set": {"cancellation_charge": amount, "cancellation_charge_ids": [str(charge_id)]},
        "$inc": {"total_amount": amount, "platform_earning": amount},
    })
    return await fb.doc(db, b["id"]), charge_id


async def _rows(db, charge_id) -> list[dict]:
    return await db.customer_wallet_ledger.find({"key": f"charge:{charge_id}"}).to_list(None)


async def test_unpaid_carrier_cancelled_settles_the_legacy_charge_on_the_wallet(db):
    s = await fb.rig(db)
    booking, charge_id = await _carrying_legacy_charge(db, s)
    before = await fb.balance(db, s["cu"]["id"])
    await BookingService(db).cancel_booking(str(booking["_id"]), fb.cancel(), s["cu"]["id"], "customer")

    charge = await db.customer_charges.find_one({"_id": charge_id})
    assert charge["status"] == "settled" and charge["settled_via"] == "wallet" and charge["wallet_key"] == f"charge:{charge_id}"
    assert charge["applied_to_booking_id"] is None
    assert charge["history"][-1]["action"] == "released"
    rows = await _rows(db, charge_id)
    assert len(rows) == 1 and rows[0]["amount"] == -50.0
    # Unpaid and cancelled early (free tier): the old charge is the only movement.
    assert await fb.balance(db, s["cu"]["id"]) == pytest.approx(before - 50)
    assert await db.customer_charges.count_documents({"customer_id": s["cu"]["id"], "status": "open"}) == 0

    # A replay of the release, or the boot migration, can't debit it again.
    svc = BookingService(db)
    async with await db.client.start_session() as session:
        await session.with_transaction(lambda sess: svc.charges.release_for_booking(sess, booking, reason="cancelled"))
    await migrate_open_charges_to_wallet(db)
    assert len(await _rows(db, charge_id)) == 1
    assert await fb.balance(db, s["cu"]["id"]) == pytest.approx(before - 50)


async def test_prepaid_carrier_cancelled_nets_to_paid_minus_the_old_charge(db):
    s = await fb.rig(db)
    booking, charge_id = await _carrying_legacy_charge(db, s, amount=80.0)
    await fb.pay_online(db, str(booking["_id"]))  # the total, old charge included
    paid = (await fb.doc(db, str(booking["_id"])))["amount_paid"]
    assert paid == pytest.approx(booking["total_amount"])
    before = await fb.balance(db, s["cu"]["id"])
    await BookingService(db).cancel_booking(str(booking["_id"]), fb.cancel(), s["cu"]["id"], "customer")
    # Everything paid comes back, the old charge is still owed: paid − 80.
    assert await fb.balance(db, s["cu"]["id"]) == pytest.approx(before + paid - 80)
    assert (await db.customer_charges.find_one({"_id": charge_id}))["status"] == "settled"
    assert len(await _rows(db, charge_id)) == 1


async def test_charge_audit_goes_through_the_public_method(db):
    s = await fb.rig(db)
    b = await fb.book(db, s["cu"], s["when"], s["keys"][0])
    await fb.slot_in(db, b["id"], 120)  # 1–4 h before: ₹50 tier
    out = await BookingService(db).cancel_booking(b["id"], fb.cancel(), s["cu"]["id"], "customer")
    charge = out["late_cancellation_charge"]
    assert charge and charge["amount"] == 50
    assert await db.audit_logs.find_one({"action": "CREATE_CUSTOMER_CHARGE", "target_id": charge["id"]})
    assert callable(getattr(CustomerChargeService, "audit_created", None))


async def test_announced_charge_speaks_of_the_wallet_not_the_next_booking(db):
    s = await fb.rig(db)
    b = await fb.book(db, s["cu"], s["when"], s["keys"][0])
    await fb.slot_in(db, b["id"], 120)
    out = await BookingService(db).cancel_booking(b["id"], fb.cancel(), s["cu"]["id"], "customer")
    svc = CustomerChargeService(db)
    charge = await db.customer_charges.find_one({"_id": ObjectId(out["late_cancellation_charge"]["id"])})
    await svc.announce_created(charge, await svc.actor(s["cu"]["id"], "customer"))
    note = await db.notifications.find_one({"user_id": s["cu"]["id"], "title": "Late cancellation charge"}, sort=[("_id", -1)])
    assert note and "wallet" in note["message"] and "next booking" not in note["message"]
