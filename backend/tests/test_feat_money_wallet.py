"""MONEY feature (spec docs/FEATURE_PLAN_WALLET_EDITS_PLANS_2026-10-07.md
§1.1, 1.2, 1.5, §3): the customer wallet, the booking balance model, money
on cancel / price change, captain wallet delta settlement.

Real services on the local replica-set Mongo; Razorpay is stubbed."""
import asyncio
from datetime import datetime, timezone

import pytest
from bson import ObjectId

from app.core.exceptions import AppException, BadRequestException, NotFoundException
from app.services import booking_money as bm
from app.services.customer_wallet_service import CustomerWalletService, in_transaction
from app.services.money_service import MoneyService, backfill_booking_money, migrate_open_charges_to_wallet
from app.services.payment_service import PaymentService
from tests import test_fix_core_helpers as h
from tests import test_fix_coreb_helpers as hb

pytestmark = pytest.mark.asyncio


async def _customer(db) -> str:
    s = await hb.staffed_center(db)
    return s["cu"]["id"]


async def _bal(db, customer_id: str) -> float:
    return await CustomerWalletService(db).balance(customer_id)


async def _job(db, s: dict, *, slot: int = 0, **fields) -> dict:
    """A real booking (customer app, cash), then the money/state fields the
    test needs written onto it."""
    b = await hb.new_booking(db, s["cu"], s["when"], s["keys"][slot])
    if fields:
        await db.bookings.update_one({"_id": ObjectId(b["id"])}, {"$set": fields})
    return await hb.booking(db, b["id"])


# ---------------------------------------------------------------- pure helpers


def test_money_view_legacy_and_partial():
    legacy_paid = {"total_amount": 499, "payment_status": "paid"}
    assert bm.money_view(legacy_paid)["amount_paid"] == 499 and bm.amount_due(legacy_paid) == 0
    legacy_pending = {"total_amount": 499, "payment_status": "pending"}
    assert bm.amount_due(legacy_pending) == 499 and bm.money_view(legacy_pending)["payment_status"] == "pending"
    part = {"total_amount": 500, "amount_paid": 200, "wallet_applied": 100, "payment_status": "partially_paid"}
    v = bm.money_view(part)
    assert (v["amount_due"], v["payment_status"]) == (200, "partially_paid")
    assert bm.status_for(500, 100, 0) == "pending"          # wallet credit alone isn't "paid something"
    assert bm.status_for(500, 500, 0) == "paid"
    assert bm.amount_due({"total_amount": 300, "status": "cancelled"}) == 0
    refunded = {"total_amount": 300, "payment_status": "refunded", "amount_paid": 300}
    assert bm.money_view(refunded)["payment_status"] == "refunded" and bm.refundable(refunded) == 0
    # A carried previous balance already credited back isn't refunded again.
    carried = {"total_amount": 380, "amount_paid": 380, "wallet_due_carried": 80, "wallet_due_cleared": True, "payment_status": "paid"}
    assert bm.refundable(carried) == 300


def test_captain_wallet_target_and_legacy_posted():
    done = {"captain_id": "c1", "status": "completed", "captain_earning": 150, "total_amount": 500}
    assert bm.captain_wallet_target(done) == 150
    assert bm.captain_wallet_target({**done, "captain_cash_collected": 500}) == -350
    # Legacy cash completion: posted = earning − total, cash held = total.
    legacy_cash = {**done, "wallet_settled": True, "wallet_settled_as": "cash", "payment_status": "paid", "payment_method": "cash"}
    assert bm.captain_wallet_posted(legacy_cash) == -350 and bm.captain_wallet_target(legacy_cash) == -350
    # Legacy credit completion: posted = earning.
    legacy_credit = {**done, "wallet_settled": True, "wallet_settled_as": "credit", "payment_method": "online", "payment_status": "pending"}
    assert bm.captain_wallet_posted(legacy_credit) == 150
    # Manager-done jobs never touch a captain wallet.
    assert bm.captain_wallet_target({**done, "completed_by_role": "manager"}) == 0


# ---------------------------------------------------------------- wallet post


async def test_post_is_idempotent_and_concurrent_posts_never_double(db):
    cid = await _customer(db)
    w = CustomerWalletService(db)
    first = await w.post(cid, 100, "adjustment", key=f"t:{cid}:a")
    again = await w.post(cid, 100, "adjustment", key=f"t:{cid}:a")
    assert first["created"] and not again["created"] and await _bal(db, cid) == 100

    same = await asyncio.gather(*[w.post(cid, 50, "adjustment", key=f"t:{cid}:same") for _ in range(8)])
    assert sum(1 for r in same if r["created"]) == 1
    distinct = await asyncio.gather(*[w.post(cid, 10, "adjustment", key=f"t:{cid}:d{i}") for i in range(8)])
    assert all(r["created"] for r in distinct)
    assert await _bal(db, cid) == 100 + 50 + 80
    rows = await db.customer_wallet_ledger.find({"customer_id": cid}).sort("created_at", 1).to_list(None)
    assert len(rows) == 10
    # balance_after is a running balance — the last row is the balance.
    assert sorted(r["balance_after"] for r in rows)[-1] == 230


async def test_spending_requires_the_balance(db):
    cid = await _customer(db)
    w = CustomerWalletService(db)
    await w.post(cid, 60, "adjustment", key=f"t:{cid}:seed")
    with pytest.raises(BadRequestException):
        await w.post(cid, -100, "booking_payment", key=f"t:{cid}:spend", require_balance=True)
    assert await _bal(db, cid) == 60
    # A plain debit (a charge) may take it negative.
    await w.post(cid, -100, "cancellation_charge", key=f"t:{cid}:charge")
    assert await _bal(db, cid) == -40


# ---------------------------------------------------------------- create: carry / apply


def _draft(cid: str, total: float, number: str, **extra) -> dict:
    return {"_id": ObjectId(), "customer_id": cid, "booking_number": number, "total_amount": total,
            "platform_earning": total, "payment_status": "pending", "status": "pending", **extra}


async def _create(db, cid: str, cars: list[dict]) -> list[dict]:
    """A stand-in for BOOKING's create transaction: apply the wallet, insert."""
    money = MoneyService(db)

    async def _do(session):
        out = await money.apply_wallet_at_create(cid, [dict(c) for c in cars], session)
        for car in out:
            await db.bookings.insert_one(car, session=session)
        return out

    return await in_transaction(db, _do)


async def test_two_bookings_cannot_spend_one_balance(db):
    cid = await _customer(db)
    await CustomerWalletService(db).post(cid, 100, "adjustment", key=f"t:{cid}:seed")
    results = await asyncio.gather(
        _create(db, cid, [_draft(cid, 80, "T-A")]), _create(db, cid, [_draft(cid, 80, "T-B")]),
    )
    applied = [r[0]["wallet_applied"] for r in results]
    assert sum(applied) == 100 and sorted(applied) == [20, 80]
    assert await _bal(db, cid) == 0
    paid = [r[0] for r in results if r[0]["wallet_applied"] == 80][0]
    assert paid["payment_status"] == "paid" and paid["amount_due"] == 0
    await db.bookings.delete_many({"customer_id": cid})


async def _cancelled_to_wallet(db, booking_id: str, amount: float) -> None:
    """MONEY-2: a payout is now a PAYBACK for one booking that went wrong —
    this marks the booking cancelled with `amount` returned to the wallet
    (as on_booking_cancelled leaves it); the caller seeds the credit."""
    await db.bookings.update_one({"_id": ObjectId(booking_id)}, {"$set": {
        "status": "cancelled", "amount_paid": amount, "payment_status": "refunded", "refunded_to": "wallet",
        "refunded_amount": amount, "cancel_money_settled": True,
    }})


async def test_payout_vs_booking_on_the_same_balance(db):
    # MONEY-2: the free payout became a booking-linked payback — the race is
    # the same (one credit, a payback vs a booking spending it), on a
    # cancelled booking whose money went back to the wallet.
    s = await hb.staffed_center(db)
    cid = s["cu"]["id"]
    known = await hb.new_booking(db, s["cu"], s["when"], s["keys"][0])  # known to the center
    await _cancelled_to_wallet(db, known["id"], 100)
    w = CustomerWalletService(db)
    await w.post(cid, 100, "adjustment", key=f"t:{cid}:seed")
    payout = w.payback(
        cid, booking_id=known["id"], amount=100, reason="cancelled", method="upi", reference="UTR-RACE-1", note=None,
        actor={"id": s["mgr"]["id"], "role": "manager"}, center=s["center_id"],
    )
    create = _create(db, cid, [_draft(cid, 80, "T-P")])
    outcome = await asyncio.gather(payout, create, return_exceptions=True)
    balance = await _bal(db, cid)
    assert balance >= 0
    paid_out = not isinstance(outcome[0], Exception)
    spent = outcome[1][0]["wallet_applied"] if not isinstance(outcome[1], Exception) else 0
    assert (100 if paid_out else 0) + spent + balance == 100
    await db.bookings.delete_many({"customer_id": cid, "booking_number": "T-P"})


async def test_negative_balance_carried_once_and_cleared_when_paid(db):
    s = await hb.staffed_center(db)
    cid = s["cu"]["id"]
    w = CustomerWalletService(db)
    await w.post(cid, -80, "cancellation_charge", key=f"t:{cid}:charge")
    first, second = await asyncio.gather(_create(db, cid, [_draft(cid, 300, "T-C1")]), _create(db, cid, [_draft(cid, 300, "T-C2")]))
    carried = [c[0]["wallet_due_carried"] for c in (first, second)]
    assert sorted(carried) == [0, 80], "the same debt rides on one booking only"
    carrier = (first if first[0]["wallet_due_carried"] else second)[0]
    assert carrier["total_amount"] == 380 and carrier["amount_due"] == 380
    assert (await w.summary(cid))["previous_balance_due"] == 0

    # Paid in full: the debt is cleared (a credit), the reservation released.
    money = MoneyService(db)
    res = await money.apply_payment([str(carrier["_id"])], 380, method="online", key=f"t:{cid}:pay")
    assert res["excess"] == 0
    after = await hb.booking(db, str(carrier["_id"]))
    assert after["payment_status"] == "paid" and after["wallet_due_cleared"] is True
    summary = await w.summary(cid)
    assert summary["balance"] == 0 and summary["carried_due"] == 0
    await db.bookings.delete_many({"customer_id": cid})


async def test_negative_balance_makes_a_plan_covered_booking_payable(db):
    cid = await _customer(db)
    await CustomerWalletService(db).post(cid, -80, "cancellation_charge", key=f"t:{cid}:charge")
    car = (await _create(db, cid, [_draft(cid, 0, "T-PLAN", payment_status="paid", payment_method="subscription")]))[0]
    assert car["total_amount"] == 80 and car["amount_due"] == 80 and car["payment_status"] == "pending"
    await db.bookings.delete_many({"customer_id": cid})


async def test_cancelling_an_unpaid_carrier_puts_the_debt_back(db):
    cid = await _customer(db)
    w = CustomerWalletService(db)
    await w.post(cid, -80, "cancellation_charge", key=f"t:{cid}:charge")
    car = (await _create(db, cid, [_draft(cid, 300, "T-REL")]))[0]
    await db.bookings.update_one({"_id": car["_id"]}, {"$set": {"status": "cancelled"}})
    out = await MoneyService(db).on_booking_cancelled([car], charge_amount=0)
    assert out["net"] == 0
    summary = await w.summary(cid)
    assert summary["balance"] == -80 and summary["previous_balance_due"] == 80 and summary["carried_due"] == 0
    await db.bookings.delete_many({"customer_id": cid})


async def test_positive_balance_bigger_than_the_booking_pays_it_fully(db):
    s = await hb.staffed_center(db)
    cid = s["cu"]["id"]
    await CustomerWalletService(db).post(cid, 1000, "adjustment", key=f"t:{cid}:seed")
    car = (await _create(db, cid, [_draft(cid, 300, "T-FULL", captain_id=s["cap"]["id"], status="completed")]))[0]
    assert car["wallet_applied"] == 300 and car["payment_status"] == "paid"
    assert await _bal(db, cid) == 700
    # Nothing for the captain to collect.
    assert PaymentService._unpaid([await hb.booking(db, str(car["_id"]))]) == []
    # Cancelled → the wallet credit comes back.
    await db.bookings.update_one({"_id": car["_id"]}, {"$set": {"status": "cancelled"}})
    out = await MoneyService(db).on_booking_cancelled([car], charge_amount=0)
    assert out["credited"] == 300 and await _bal(db, cid) == 1000
    await db.bookings.delete_many({"customer_id": cid})


# ---------------------------------------------------------------- cancellation


async def test_cancel_paid_unpaid_plan_and_mixed(db):
    s = await hb.staffed_center(db)
    cid = s["cu"]["id"]
    money = MoneyService(db)
    # All bookings first (a create spends any wallet credit already there).
    paid = await _job(db, s, slot=0, total_amount=300, amount_paid=300, payment_status="paid", status="cancelled")
    unpaid = await _job(db, s, slot=1, total_amount=300, status="cancelled")
    part = await _job(db, s, slot=2, total_amount=300, amount_paid=30, payment_status="partially_paid", status="cancelled")

    out = await money.on_booking_cancelled([paid], charge_amount=50)
    assert (out["credited"], out["net"]) == (300, 250) and await _bal(db, cid) == 250
    assert "₹250 added" in out["wallet_line"]
    again = await money.on_booking_cancelled([paid], charge_amount=50)
    assert again["net"] == 0 and await _bal(db, cid) == 250, "a replayed cancel moves nothing"
    doc = await hb.booking(db, str(paid["_id"]))
    assert doc["payment_status"] == "refunded" and doc["refunded_to"] == "wallet" and doc["refunded_amount"] == 300

    out = await money.on_booking_cancelled([unpaid], charge_amount=80)
    assert out["net"] == -80 and await _bal(db, cid) == 170

    # A part-paid booking whose charge is bigger than what was paid: the rest is a debit.
    out = await money.on_booking_cancelled([part], charge_amount=80)
    assert out["net"] == -50 and await _bal(db, cid) == 120


async def test_cancel_mixed_visit_plan_car_free_paid_car_tiered(db):
    s = await hb.staffed_center(db)
    cid = s["cu"]["id"]
    plan_car = await _job(db, s, slot=0, total_amount=0, payment_status="paid", payment_method="subscription", status="cancelled")
    paid_car = await _job(db, s, slot=1, total_amount=400, amount_paid=200, wallet_applied=200, payment_status="paid", status="cancelled")
    out = await MoneyService(db).on_booking_cancelled(
        [plan_car, paid_car], charge_amount=50, plan_car_ids=[str(plan_car["_id"])],
    )
    assert out["credited"] == 400 and out["net"] == 350 and await _bal(db, cid) == 350
    assert out["refunds"][str(plan_car["_id"])] == 0


async def test_payment_landing_after_cancel_goes_to_the_wallet(db):
    s = await hb.staffed_center(db)
    cid = s["cu"]["id"]
    car = await _job(db, s, total_amount=300, status="cancelled")
    res = await MoneyService(db).apply_payment([str(car["_id"])], 300, method="online", key=f"t:{cid}:late")
    assert res["applied"] == 0 and res["excess"] == 300 and await _bal(db, cid) == 300
    doc = await hb.booking(db, str(car["_id"]))
    assert doc["paid_online"] == 300 and bm.amount_due(doc) == 0


async def test_cancel_vs_pay_race_never_loses_money(db):
    for _ in range(4):
        s = await hb.staffed_center(db)
        cid = s["cu"]["id"]
        car = await _job(db, s, total_amount=300)
        money = MoneyService(db)

        async def cancel():
            async def _do(session):
                await db.bookings.update_one({"_id": car["_id"]}, {"$set": {"status": "cancelled"}}, session=session)
                return await money.on_booking_cancelled([car], charge_amount=50, session=session)
            return await in_transaction(db, _do)

        pay = money.apply_payment([str(car["_id"])], 300, method="online", key=f"t:{cid}:race")
        await asyncio.gather(cancel(), pay)
        # Either way: the customer is left with 300 paid − 50 charge.
        assert await _bal(db, cid) == 250


# ---------------------------------------------------------------- price change


async def test_edit_down_on_a_paid_booking_credits_the_difference(db):
    s = await hb.staffed_center(db)
    cid = s["cu"]["id"]
    car = await _job(db, s, total_amount=500, amount_paid=500, payment_status="paid")
    money = MoneyService(db)

    async def _do(session):
        out = await money.on_price_change(car, {**car, "total_amount": 300}, actor={"id": cid, "role": "customer"}, reason="edit", session=session)
        await db.bookings.update_one({"_id": car["_id"]}, {"$set": {"total_amount": 300, **out["fields"]}}, session=session)
        return out

    out = await in_transaction(db, _do)
    assert out["wallet_credit"] == 200 and out["fields"]["amount_paid"] == 300 and out["fields"]["payment_status"] == "paid"
    assert await _bal(db, cid) == 200
    # Up again: the difference is due, nothing moves.
    fresh = await hb.booking(db, str(car["_id"]))

    async def _up(session):
        o = await money.on_price_change(fresh, {**fresh, "total_amount": 450}, reason="add-on", session=session)
        await db.bookings.update_one({"_id": car["_id"]}, {"$set": {"total_amount": 450, **o["fields"]}}, session=session)
        return o

    up = await in_transaction(db, _up)
    assert up["wallet_credit"] == 0 and up["amount_due"] == 150 and up["fields"]["payment_status"] == "partially_paid"
    assert await _bal(db, cid) == 200


# ---------------------------------------------------------------- collection


async def test_partial_payment_then_captain_qr_for_the_rest(db, monkeypatch):
    stub = h.install_rzp_stub(monkeypatch)
    s = await hb.staffed_center(db)
    cid, cap = s["cu"]["id"], s["cap"]["id"]
    car = await _job(db, s, total_amount=500, captain_id=cap, status="completed")
    money = MoneyService(db)
    await money.apply_payment([str(car["_id"])], 200, method="online", key=f"t:{cid}:part")
    doc = await hb.booking(db, str(car["_id"]))
    assert doc["payment_status"] == "partially_paid" and bm.amount_due(doc) == 300

    payments = PaymentService(db)
    link = await payments.captain_payment_link(str(car["_id"]), cap)
    assert link["amount"] == 300 and link["amount_paise"] == 30000  # rupees; paise named
    status = await payments.captain_check_payment(str(car["_id"]), cap)
    assert status["payment_status"] == "pending" and status["amount"] == 300
    out = await payments._apply_link_paid(link["link_id"], "pay_qr_rest")
    assert out["settled"]
    doc = await hb.booking(db, str(car["_id"]))
    assert doc["payment_status"] == "paid" and doc["amount_paid"] == 500 and doc["paid_online"] == 500
    assert await _bal(db, cid) == 0
    assert stub  # stub in place: no real gateway


async def test_online_and_cash_at_once_one_pays_the_rest_is_wallet_credit(db, monkeypatch):
    h.install_rzp_stub(monkeypatch)
    for _ in range(3):
        s = await hb.staffed_center(db)
        cid, cap = s["cu"]["id"], s["cap"]["id"]
        car = await _job(db, s, total_amount=400, captain_id=cap, status="completed", captain_earning=120)
        payments = PaymentService(db)
        link = await payments.captain_payment_link(str(car["_id"]), cap)
        results = await asyncio.gather(
            payments.captain_collect_cash(str(car["_id"]), cap),
            payments._apply_link_paid(link["link_id"], f"pay_{car['_id']}"),
            return_exceptions=True,
        )
        doc = await hb.booking(db, str(car["_id"]))
        assert doc["payment_status"] == "paid" and doc["amount_paid"] == 400
        cash_ok = not isinstance(results[0], Exception)
        received = float(doc.get("paid_cash") or 0) + float(doc.get("paid_online") or 0)
        assert received == (800 if cash_ok else 400)
        assert await _bal(db, cid) == received - 400, "every rupee beyond the price is the customer's credit"
        if cash_ok:
            assert doc["captain_cash_collected"] == 400


async def test_cash_collection_refuses_a_stale_amount(db):
    s = await hb.staffed_center(db)
    cap = s["cap"]["id"]
    car = await _job(db, s, total_amount=400, captain_id=cap, status="completed")
    with pytest.raises(AppException) as exc:
        await PaymentService(db).captain_collect_cash(str(car["_id"]), cap, expected_amount=350)
    assert exc.value.error_code == "AMOUNT_DUE_CHANGED"
    done = await PaymentService(db).captain_collect_cash(str(car["_id"]), cap, expected_amount=400)
    assert done["amount"] == 400
    with pytest.raises(BadRequestException):
        await PaymentService(db).captain_collect_cash(str(car["_id"]), cap)


# ---------------------------------------------------------------- captain wallet


async def test_captain_wallet_delta_completion_addon_cash(db, monkeypatch):
    h.install_rzp_stub(monkeypatch)
    s = await hb.staffed_center(db)
    cid, cap = s["cu"]["id"], s["cap"]["id"]
    start = await hb.wallet(db, cap)
    # Prepaid online, done: completion settles the earning in.
    car = await _job(db, s, total_amount=500, amount_paid=500, payment_status="paid", payment_method="online",
                     captain_id=cap, status="completed", captain_earning=150)
    money = MoneyService(db)
    await money.settle_captain_wallet([str(car["_id"])])
    assert await hb.wallet(db, cap) == start + 150
    await money.settle_captain_wallet([str(car["_id"])])
    assert await hb.wallet(db, cap) == start + 150, "settling again posts nothing"

    # On-site add-on of ₹200 (captain earnings unchanged), collected in cash.
    fresh = await hb.booking(db, str(car["_id"]))

    async def _addon(session):
        o = await money.on_price_change(fresh, {**fresh, "total_amount": 700}, reason="add-on", session=session)
        await db.bookings.update_one({"_id": car["_id"]}, {"$set": {"total_amount": 700, **o["fields"]}}, session=session)

    await in_transaction(db, _addon)
    await PaymentService(db).captain_collect_cash(str(car["_id"]), cap)
    assert await hb.wallet(db, cap) == start + 150 - 200, "only the cash he holds is owed back"
    await money.settle_captain_wallet([str(car["_id"])])
    assert await hb.wallet(db, cap) == start - 50
    rows = await db.wallet_transactions.find({"captain_id": cap, "booking_id": str(car["_id"])}).to_list(None)
    assert len(rows) == 2 and len({r["key"] for r in rows}) == 2
    assert await _bal(db, cid) == 0


async def test_captain_wallet_concurrent_settles_post_once(db):
    s = await hb.staffed_center(db)
    cap = s["cap"]["id"]
    start = await hb.wallet(db, cap)
    car = await _job(db, s, total_amount=300, captain_id=cap, status="completed", captain_earning=90)
    await asyncio.gather(*[MoneyService(db).settle_captain_wallet([str(car["_id"])]) for _ in range(6)])
    assert await hb.wallet(db, cap) == start + 90


async def test_legacy_cash_completed_booking_is_not_resettled(db):
    s = await hb.staffed_center(db)
    cap = s["cap"]["id"]
    start = await hb.wallet(db, cap)
    car = await _job(db, s, total_amount=300, captain_id=cap, status="completed", captain_earning=90,
                     payment_status="paid", payment_method="cash", wallet_settled=True, wallet_settled_as="cash")
    await MoneyService(db).settle_captain_wallet([str(car["_id"])])
    assert await hb.wallet(db, cap) == start
    assert (await hb.booking(db, str(car["_id"])))["captain_wallet_posted"] == -210


async def test_legacy_credit_settled_booking_then_cash_debits_the_total(db):
    s = await hb.staffed_center(db)
    cap = s["cap"]["id"]
    start = await hb.wallet(db, cap)
    car = await _job(db, s, total_amount=300, captain_id=cap, status="completed", captain_earning=90,
                     payment_method="online", wallet_settled=True, wallet_settled_as="credit")
    await PaymentService(db).captain_collect_cash(str(car["_id"]), cap)
    assert await hb.wallet(db, cap) == start - 300


# ---------------------------------------------------------------- charges


async def test_open_charges_migrate_to_the_wallet_once(db):
    cid = await _customer(db)
    charge_id = ObjectId()
    await db.customer_charges.insert_one({
        "_id": charge_id, "customer_id": cid, "kind": "late_cancellation", "visit_key": f"mig:{charge_id}",
        "amount": 80.0, "status": "open", "source_booking_number": "BK-OLD", "history": [], "created_at": datetime.now(timezone.utc),
    })
    assert await migrate_open_charges_to_wallet(db) >= 1
    await migrate_open_charges_to_wallet(db)
    assert await _bal(db, cid) == -80
    assert (await db.customer_charges.find_one({"_id": charge_id}))["status"] == "settled"


async def test_reducing_a_settled_charge_credits_the_wallet_once(db):
    from app.services.customer_charge_service import CustomerChargeService

    s = await hb.staffed_center(db)
    cid = s["cu"]["id"]
    charge_id = ObjectId()
    await db.customer_charges.insert_one({
        "_id": charge_id, "customer_id": cid, "service_center_id": s["center_id"], "kind": "late_cancellation",
        "visit_key": f"adj:{charge_id}", "amount": 80.0, "status": "settled", "settled_via": "wallet",
        "source_booking_number": "BK-X", "history": [], "created_at": datetime.now(timezone.utc),
    })
    await CustomerWalletService(db).post(cid, -80, "cancellation_charge", key=f"t:{cid}:c")
    svc = CustomerChargeService(db)
    results = await asyncio.gather(*[
        svc.adjust(str(charge_id), 30, "goodwill", actor_id=s["mgr"]["id"], actor_role="manager", actor_center_id=s["center_id"])
        for _ in range(3)
    ], return_exceptions=True)
    assert sum(1 for r in results if not isinstance(r, Exception)) >= 1
    assert await _bal(db, cid) == -30
    await svc.adjust(str(charge_id), 0, "waive", actor_id=s["mgr"]["id"], actor_role="manager", actor_center_id=s["center_id"])
    assert await _bal(db, cid) == 0
    assert (await db.customer_charges.find_one({"_id": charge_id}))["status"] == "waived"


# ---------------------------------------------------------------- HTTP


async def test_wallet_routes_scope_payout_and_adjust(db):
    s = await hb.staffed_center(db)
    cid = s["cu"]["id"]
    known = await hb.new_booking(db, s["cu"], s["when"], s["keys"][0])
    # MONEY-2: a payout is a payback for one booking that went wrong.
    await _cancelled_to_wallet(db, known["id"], 300)
    other = await hb.staffed_center(db)
    admin = await h.admin(db)
    await CustomerWalletService(db).post(cid, 300, "adjustment", key=f"t:{cid}:seed")
    async with h.client() as c:
        me = await c.get("/api/v1/wallet/me", headers=s["cu"]["h"])
        assert me.status_code == 200 and me.json()["data"]["balance"] == 300 and me.json()["data"]["items"]
        assert (await c.get(f"/api/v1/customers/{cid}/wallet", headers=s["mgr"]["h"])).status_code == 200
        assert (await c.get(f"/api/v1/customers/{cid}/wallet", headers=other["mgr"]["h"])).status_code == 404
        assert (await c.get(f"/api/v1/customers/{cid}/wallet", headers=s["cu"]["h"])).status_code == 403
        body = {"booking_id": known["id"], "reason": "cancelled", "amount": 400, "method": "upi", "reference": "UTR123456"}
        # Above what was paid for that booking (MONEY-2 cap).
        too_much = await c.post(f"/api/v1/customers/{cid}/wallet/payout", json=body, headers=s["mgr"]["h"])
        assert too_much.status_code == 400 and too_much.json().get("error_code") == "PAYBACK_TOO_MUCH"
        ok = await c.post(f"/api/v1/customers/{cid}/wallet/payout", json={**body, "amount": 250}, headers=s["mgr"]["h"])
        assert ok.status_code == 200, ok.text
        dup = await c.post(f"/api/v1/customers/{cid}/wallet/payout", json={**body, "amount": 250}, headers=s["mgr"]["h"])
        assert dup.status_code == 200 and dup.json()["data"]["created"] is False
        assert (await c.post(f"/api/v1/customers/{cid}/wallet/payout", json={**body, "amount": 10}, headers=other["mgr"]["h"])).status_code == 404
        assert (await c.post(f"/api/v1/customers/{cid}/wallet/adjust", json={"amount": 5, "note": "fix"}, headers=s["mgr"]["h"])).status_code == 403
        adj = await c.post(f"/api/v1/customers/{cid}/wallet/adjust", json={"amount": -20, "note": "correction", "idempotency_key": "adj-001"}, headers=admin["h"])
        assert adj.status_code == 200
        again = await c.post(f"/api/v1/customers/{cid}/wallet/adjust", json={"amount": -20, "note": "correction", "idempotency_key": "adj-001"}, headers=admin["h"])
        assert again.json()["data"]["created"] is False
        payouts = await c.get("/api/v1/wallet/payouts", headers=admin["h"])
        assert payouts.status_code == 200 and any(p["customer_id"] == cid for p in payouts.json()["data"])
    assert await _bal(db, cid) == 30
    audit = await db.audit_logs.find_one({"target_id": cid, "action": "CUSTOMER_PAYBACK"})
    assert audit is not None


# ---------------------------------------------------------------- reports / migration


async def test_collections_count_money_received_not_totals(db):
    s = await hb.staffed_center(db)
    cid, cap = s["cu"]["id"], s["cap"]["id"]
    car = await _job(db, s, total_amount=500, captain_id=cap, status="completed")
    await MoneyService(db).apply_payment([str(car["_id"])], 200, method="online", key=f"t:{cid}:part")
    rep = await PaymentService(db).center_collections(s["center_id"], "admin", None, None, s["when"])
    row = next(r for r in rep["rows"] if r["captain_id"] == cap)
    assert row["online_amount"] == 200 and row["uncollected_amount"] == 300 and row["billed_amount"] == 500
    admin = await PaymentService(db).admin_collections(None, None)
    assert "wallet" in admin and "credits_held" in admin["wallet"]


async def test_backfill_materialises_legacy_money_fields(db):
    s = await hb.staffed_center(db)
    car = await _job(db, s, total_amount=300, payment_status="paid")
    await db.bookings.update_one({"_id": car["_id"]}, {"$unset": {"amount_paid": "", "amount_due": "", "wallet_applied": ""}})
    await backfill_booking_money(db)
    doc = await hb.booking(db, str(car["_id"]))
    assert doc["amount_paid"] == 300 and doc["amount_due"] == 0 and doc["wallet_applied"] == 0


# ---------------------------------------------------------------- custom plans


async def test_custom_plan_link_dispatches_activation(db, monkeypatch):
    h.install_rzp_stub(monkeypatch)
    calls: list = []

    class FakeCustomPlans:
        def __init__(self, _db):
            pass

        async def activate_from_payment(self, order):
            calls.append(order["custom_plan_id"])
            return {"ok": True}

        async def still_payable(self, order):
            return True

    monkeypatch.setattr(PaymentService, "_custom_plans", staticmethod(lambda: FakeCustomPlans))
    cid = await _customer(db)
    payments = PaymentService(db)
    plan_id = str(ObjectId())
    link = await payments.create_custom_plan_link(
        custom_plan_id=plan_id, revision=1, customer_id=cid, amount=1999, description="Custom plan · 2 cars",
        service_center_id=None, actor_id="mgr", send_whatsapp=False,
    )
    again = await payments.create_custom_plan_link(
        custom_plan_id=plan_id, revision=1, customer_id=cid, amount=1999, description="Custom plan · 2 cars",
        service_center_id=None, actor_id="mgr", send_whatsapp=False,
    )
    assert again["reused"] and again["link_id"] == link["link_id"]
    out = await asyncio.gather(*[payments._apply_link_paid(link["link_id"], "pay_cp_1") for _ in range(3)])
    assert calls == [plan_id] and any(o.get("purpose") == "custom_plan" for o in out)
    cash = await payments.record_custom_plan_cash(custom_plan_id=plan_id, revision=2, customer_id=cid, amount=1500,
                                                  service_center_id=None, actor_id="mgr")
    dup = await payments.record_custom_plan_cash(custom_plan_id=plan_id, revision=2, customer_id=cid, amount=1500,
                                                 service_center_id=None, actor_id="mgr")
    assert cash["created"] and not dup["created"]


async def test_custom_plan_payment_without_the_service_is_parked(db, monkeypatch):
    h.install_rzp_stub(monkeypatch)

    def missing():
        raise RuntimeError("Custom plans are not available")

    monkeypatch.setattr(PaymentService, "_custom_plans", staticmethod(missing))
    cid = await _customer(db)
    payments = PaymentService(db)
    link = await payments.create_custom_plan_link(
        custom_plan_id=str(ObjectId()), revision=1, customer_id=cid, amount=999, description="Custom plan",
        service_center_id=None, actor_id="mgr", send_whatsapp=False,
    )
    out = await payments._apply_link_paid(link["link_id"], "pay_cp_x")
    assert out["status"] == "needs_attention"
    row = await db.payment_orders.find_one({"razorpay_link_id": link["link_id"]})
    assert row["status"] == "paid_attention"


async def test_unknown_customer_wallet_is_404(db):
    with pytest.raises(NotFoundException):
        await CustomerWalletService(db).staff_view(str(ObjectId()), "admin", None, 1, 10)


async def test_custom_plan_link_activates_once_across_callback_webhook_and_sweep(db, monkeypatch):
    """PLANS contract: a paid custom-plan link (never the booking branch) is
    activated through CustomPlanService.activate_from_payment exactly once,
    whichever of the browser callback, the webhook and the sweep lands —
    and the customer gets no booking receipt for it."""
    import hashlib
    import hmac
    import json

    from app.services import payment_service

    stub = h.install_rzp_stub(monkeypatch)
    monkeypatch.setattr(payment_service.settings, "RAZORPAY_WEBHOOK_SECRET", "whsec_money")
    calls: list = []

    class FakeCustomPlans:
        def __init__(self, _db):
            pass

        async def activate_from_payment(self, order):
            calls.append(str(order["_id"]))
            return {"ok": True, "custom_plan_id": order["custom_plan_id"], "activated": 2, "skipped": 0}

        async def still_payable(self, order):
            return True

    monkeypatch.setattr(PaymentService, "_custom_plans", staticmethod(lambda: FakeCustomPlans))
    cid = await _customer(db)
    payments = PaymentService(db)
    made = await payments.create_link_order(
        purpose="custom_plan", customer_id=cid, amount_paise=249900, description="Custom plan — 2 cars",
        reference={"custom_plan_id": str(ObjectId()), "custom_plan_revision": 1, "car_count": 2},
        issued_by="mgr", service_center_id=None, reference_prefix="cpl-test",
    )
    link_id = made["link_id"]
    stub.payment_link.fetch = lambda lid: {"id": lid, "status": "paid", "payments": [{"payment_id": "pay_cp_multi"}]}

    # Browser callback (signed), webhook (signed), the sweep — together.
    reference_id = (await db.payment_orders.find_one({"razorpay_link_id": link_id}))["reference_id"]
    message = f"{link_id}|{reference_id}|paid|pay_cp_multi"
    callback = {
        "razorpay_payment_link_id": link_id, "razorpay_payment_link_reference_id": reference_id,
        "razorpay_payment_link_status": "paid", "razorpay_payment_id": "pay_cp_multi",
        "razorpay_signature": hmac.new(h.RZP_SECRET.encode(), message.encode(), hashlib.sha256).hexdigest(),
    }
    body = json.dumps({"event": "payment_link.paid", "payload": {
        "payment_link": {"entity": {"id": link_id}}, "payment": {"entity": {"id": "pay_cp_multi"}},
    }}).encode()
    signature = hmac.new(b"whsec_money", body, hashlib.sha256).hexdigest()
    await asyncio.gather(
        payments.verify_link_callback(callback),
        payments.handle_webhook(body, signature, f"evt_{link_id}"),
        payments.sync_pending_links(),
    )
    await payments.sync_pending_links()
    assert len(calls) == 1
    row = await db.payment_orders.find_one({"razorpay_link_id": link_id})
    assert row["status"] == "paid" and not row.get("settling")
    assert await db.notifications.count_documents({"user_id": cid, "title": "Payment Received"}) == 0
    # Counted as plan money in the admin roll-up.
    report = await payments.admin_collections(None, None)
    assert report["custom_plans"]["online_amount"] >= 2499


# ---------------------------------------------------------------- charge reduced while carried


async def _carrier_with_charge(db, s: dict, charge: float = 50.0) -> tuple[dict, ObjectId]:
    """A settled ₹50 charge on the wallet, the debt riding on a new booking."""
    cid = s["cu"]["id"]
    charge_id = ObjectId()
    await db.customer_charges.insert_one({
        "_id": charge_id, "customer_id": cid, "service_center_id": s["center_id"], "kind": "late_cancellation",
        "visit_key": f"carry:{charge_id}", "amount": charge, "original_amount": charge, "status": "settled",
        "settled_via": "wallet", "source_booking_number": "BK-SRC", "history": [], "created_at": datetime.now(timezone.utc),
    })
    await CustomerWalletService(db).post(cid, -charge, "cancellation_charge", key=f"t:{charge_id}")
    b = await hb.new_booking(db, s["cu"], s["when"], s["keys"][1])
    car = await hb.booking(db, b["id"])
    assert car["wallet_due_carried"] == charge
    return car, charge_id


async def _adjust(db, s, charge_id, amount):
    from app.services.customer_charge_service import CustomerChargeService

    return await CustomerChargeService(db).adjust(
        str(charge_id), amount, None, actor_id=s["mgr"]["id"], actor_role="manager", actor_center_id=s["center_id"],
    )


async def test_waive_while_carried_lowers_the_booking_and_gives_no_credit(db):
    s = await hb.staffed_center(db)
    car, charge_id = await _carrier_with_charge(db, s)
    await _adjust(db, s, charge_id, 0)
    doc = await hb.booking(db, str(car["_id"]))
    assert doc["total_amount"] == car["total_amount"] - 50 and doc["wallet_due_carried"] == 0
    assert doc["amount_due"] == doc["total_amount"] and doc["payment_status"] == "pending"
    summary = await CustomerWalletService(db).summary(s["cu"]["id"])
    assert (summary["balance"], summary["carried_due"], summary["previous_balance_due"]) == (0, 0, 0)
    # Paying the lower price later leaves nothing behind.
    await MoneyService(db).apply_payment([str(car["_id"])], doc["total_amount"], method="online", key=f"t:{car['_id']}")
    assert await _bal(db, s["cu"]["id"]) == 0


async def test_reduce_half_while_carried_lowers_half(db):
    s = await hb.staffed_center(db)
    car, charge_id = await _carrier_with_charge(db, s)
    await _adjust(db, s, charge_id, 25)
    doc = await hb.booking(db, str(car["_id"]))
    assert doc["total_amount"] == car["total_amount"] - 25 and doc["wallet_due_carried"] == 25
    summary = await CustomerWalletService(db).summary(s["cu"]["id"])
    assert (summary["balance"], summary["carried_due"], summary["previous_balance_due"]) == (-25, 25, 0)
    await MoneyService(db).apply_payment([str(car["_id"])], doc["total_amount"], method="online", key=f"t:{car['_id']}")
    assert await _bal(db, s["cu"]["id"]) == 0


async def test_reduce_racing_a_payment_has_one_outcome_and_no_double_benefit(db):
    for _ in range(4):
        s = await hb.staffed_center(db)
        cid = s["cu"]["id"]
        car, charge_id = await _carrier_with_charge(db, s)
        full = car["total_amount"]
        results = await asyncio.gather(
            _adjust(db, s, charge_id, 0),
            MoneyService(db).apply_payment([str(car["_id"])], full, method="online", key=f"t:race:{car['_id']}"),
            return_exceptions=True,
        )
        assert not any(isinstance(r, Exception) for r in results), results
        doc = await hb.booking(db, str(car["_id"]))
        balance = await _bal(db, cid)
        summary = await CustomerWalletService(db).summary(cid)
        assert summary["carried_due"] == 0 and doc["payment_status"] == "paid"
        # Customer paid `full`; the waived ₹50 comes back exactly once —
        # either as a lower price (overpaid → credit) or as wallet credit.
        assert balance == 50, (doc["total_amount"], doc.get("amount_paid"), balance)
        assert float(doc.get("paid_online") or 0) == full
