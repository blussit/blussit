"""Founder 2026-10-28: a manager (or admin) can give a discount on a booking
that already exists — pending payment, confirmed, assigned, in progress —
raise, lower or remove it, and it flows through the SAME money model:
total, amount due, platform earning, the customer's wallet (only money the
customer actually paid comes back) and an immutable history."""
import asyncio

import pytest
from bson import ObjectId

from app.schemas.booking_adjust_schema import BookingDiscountRequest
from app.services import booking_money as bm
from app.services.booking_adjust_service import BookingAdjustService, booking_version
from app.services.customer_wallet_service import CustomerWalletService
from tests import test_feat_booking_helpers as fb
from tests import test_fix_core_helpers as h

pytestmark = pytest.mark.asyncio


def actor(s, role="manager"):
    return {"id": s["mgr"]["id"], "role": role, "center": s["center_id"]}


def req(amount=None, percent=None, reason="Customer asked for a price match", **kw):
    return BookingDiscountRequest(amount=amount, percent=percent, reason=reason, **kw)


async def give(db, s, booking_id, amount, **kw):
    return await BookingAdjustService(db).set_discount(booking_id, req(amount, **kw), actor(s))


async def test_discount_on_an_unpaid_booking_drops_total_due_and_platform_share(db):
    s = await fb.rig(db)
    b = await fb.book(db, s["cu"], s["when"], s["keys"][0])
    before = await fb.doc(db, b["id"])
    total = bm.total_of(before)
    result = await give(db, s, b["id"], 50)
    assert result["changed"] and result["discount_after"] == 50 and result["amount_due_after"] == total - 50
    after = await fb.doc(db, b["id"])
    assert after["manager_discount"] == 50 and after["total_amount"] == total - 50
    assert after["amount_due"] == total - 50 and after["payment_status"] == "pending"
    assert after["platform_earning"] == round(before["platform_earning"] - 50, 2)
    assert after["captain_earning"] == before["captain_earning"]
    assert after["discount_amount"] == round(float(before.get("discount_amount") or 0) + 50, 2)
    assert after["subtotal"] == before["subtotal"] and after.get("coupon_code") == before.get("coupon_code")
    # history + audit + customer notice
    rows = await db.booking_adjustments.find({"booking_id": b["id"]}).to_list(None)
    assert len(rows) == 1 and rows[0]["kind"] == "discount" and rows[0]["reason"].startswith("Customer asked")
    assert rows[0]["before"]["manager_discount"] == 0 and rows[0]["after"]["manager_discount"] == 50
    assert rows[0]["actor_id"] == s["mgr"]["id"] and rows[0]["actor_role"] == "manager"
    audit = await db.audit_logs.find_one({"action": "MANAGER_GIVE_DISCOUNT", "target_id": b["id"]})
    assert audit and audit["details"]["after"]["manager_discount"] == 50
    note = await fb.bell(db, s["cu"]["id"], "Discount Applied")
    assert note and f"A Discount Of ₹50 Was Applied To Your Booking {before['booking_number']}. New Amount Due: ₹{total - 50:g}" in note[0]["message"]


async def test_raise_lower_and_remove_each_leave_an_entry_and_restore_the_bill(db):
    s = await fb.rig(db)
    b = await fb.book(db, s["cu"], s["when"], s["keys"][0])
    original = await fb.doc(db, b["id"])
    for amount in (30, 80, 20, 0):
        await give(db, s, b["id"], amount)
    after = await fb.doc(db, b["id"])
    assert after["manager_discount"] == 0 and after["total_amount"] == original["total_amount"]
    assert after["platform_earning"] == original["platform_earning"] and after["discount_amount"] == original.get("discount_amount", 0)
    rows = await db.booking_adjustments.find({"booking_id": b["id"]}).sort("seq", 1).to_list(None)
    assert [r["after"]["manager_discount"] for r in rows] == [30, 80, 20, 0] and [r["seq"] for r in rows] == [1, 2, 3, 4]
    # Doing the same thing again changes nothing and writes nothing.
    again = await give(db, s, b["id"], 0)
    assert again["changed"] is False
    assert await db.booking_adjustments.count_documents({"booking_id": b["id"]}) == 4


async def test_never_below_zero_and_whole_bill_marks_it_paid(db):
    s = await fb.rig(db)
    b = await fb.book(db, s["cu"], s["when"], s["keys"][0])
    total = bm.total_of(await fb.doc(db, b["id"]))
    with pytest.raises(Exception) as exc:
        await give(db, s, b["id"], int(total) + 1)
    assert "more than the bill" in str(exc.value)
    result = await give(db, s, b["id"], int(total))
    after = await fb.doc(db, b["id"])
    assert after["total_amount"] == total - int(total) and after["amount_due"] == 0 and after["payment_status"] == "paid"
    assert result["amount_due_after"] == 0 and result["wallet_credit"] == 0


async def test_percent_becomes_whole_rupees(db):
    s = await fb.rig(db)
    b = await fb.book(db, s["cu"], s["when"], s["keys"][0])
    total = bm.total_of(await fb.doc(db, b["id"]))
    result = await BookingAdjustService(db).set_discount(b["id"], req(percent=33.3333), actor(s))
    assert result["discount_after"] == round(total * 0.333333 + 1e-9) or result["discount_after"] == int(result["discount_after"])
    assert result["discount_after"] == float(int(result["discount_after"]))


async def test_confirming_an_awaiting_payment_booking_when_nothing_is_left_to_pay(db):
    s = await fb.rig(db)
    b = await fb.book(db, s["cu"], s["when"], s["keys"][0], method="online")
    assert (await fb.doc(db, b["id"]))["status"] == "awaiting_payment"
    total = bm.total_of(await fb.doc(db, b["id"]))
    await give(db, s, b["id"], int(total))
    after = await fb.doc(db, b["id"])
    assert after["status"] == "pending" and after["amount_due"] == 0


async def test_partly_paid_due_drops_never_negative_and_surplus_returns_to_wallet(db):
    s = await fb.rig(db)
    b = await fb.book(db, s["cu"], s["when"], s["keys"][0])
    total = bm.total_of(await fb.doc(db, b["id"]))
    await fb.pay_online(db, b["id"], 100)
    await give(db, s, b["id"], 40)
    mid = await fb.doc(db, b["id"])
    assert mid["amount_paid"] == 100 and mid["amount_due"] == total - 40 - 100 and mid["payment_status"] == "partially_paid"
    assert await fb.balance(db, s["cu"]["id"]) == 0
    # Now a discount larger than what is left: due 0, the extra of what was PAID returns.
    result = await give(db, s, b["id"], int(total) - 60)
    end = await fb.doc(db, b["id"])
    assert end["amount_due"] == 0 and end["total_amount"] == 60 and end["amount_paid"] == 60
    assert result["wallet_credit"] == 40 and await fb.balance(db, s["cu"]["id"]) == 40
    ledger = await db.customer_wallet_ledger.find_one({"booking_id": b["id"], "kind": "price_reduced"})
    assert ledger and ledger["amount"] == 40 and ledger["actor_id"] == s["mgr"]["id"]


async def test_fully_paid_discount_returns_exactly_the_discount_to_the_wallet_and_notifies(db):
    s = await fb.rig(db)
    b = await fb.book(db, s["cu"], s["when"], s["keys"][0])
    total = bm.total_of(await fb.doc(db, b["id"]))
    await fb.pay_online(db, b["id"])
    result = await give(db, s, b["id"], 70)
    after = await fb.doc(db, b["id"])
    assert result["wallet_credit"] == 70 and await fb.balance(db, s["cu"]["id"]) == 70
    assert after["amount_paid"] == total - 70 and after["total_amount"] == total - 70 and after["payment_status"] == "paid"
    assert [n for n in await fb.bell(db, s["cu"]["id"]) if "Wallet Credited" in n["title"]]
    # Lowering the discount back charges the customer again (due), wallet unchanged.
    await give(db, s, b["id"], 0)
    back = await fb.doc(db, b["id"])
    assert back["total_amount"] == total and back["amount_due"] == 70 and await fb.balance(db, s["cu"]["id"]) == 70


async def test_cash_collected_by_the_captain_still_returns_surplus_as_wallet_credit(db):
    s = await fb.rig(db)
    b = await fb.book(db, s["cu"], s["when"], s["keys"][0])
    total = bm.total_of(await fb.doc(db, b["id"]))
    await fb.assign(db, b["id"], s)
    from app.services.money_service import MoneyService

    await MoneyService(db).apply_payment([b["id"]], total, method="cash", key="cash-1", captain_cash_by=s["cap"]["id"])
    await give(db, s, b["id"], 25)
    after = await fb.doc(db, b["id"])
    assert after["captain_cash_collected"] == total and after["amount_paid"] == total - 25
    assert await fb.balance(db, s["cu"]["id"]) == 25
    assert after["captain_earning"] > 0


async def test_credit_is_capped_by_what_a_manager_already_paid_back(db):
    s = await fb.rig(db)
    b = await fb.book(db, s["cu"], s["when"], s["keys"][0])
    total = bm.total_of(await fb.doc(db, b["id"]))
    await fb.pay_online(db, b["id"])
    # The manager already handed ₹100 of it back in cash (a service issue).
    await db.bookings.update_one({"_id": ObjectId(b["id"])}, {"$set": {"paid_back_total": 100.0}})
    result = await give(db, s, b["id"], int(total))
    assert result["wallet_credit"] == total - 100
    assert await fb.balance(db, s["cu"]["id"]) == total - 100


async def test_plan_covered_booking_has_nothing_to_discount(db):
    s = await fb.rig(db)
    sub = await fb.pass_for(db, s["cu"]["id"])
    from tests.factories import get_hatchback_type_id, get_star_wash_service_id
    from app.schemas.booking_schema import BookingCreateRequest
    from app.services.booking_service import BookingService

    b = await BookingService(db).create_booking(
        s["cu"]["id"], BookingCreateRequest(
            vehicle_type=await get_hatchback_type_id(db), address_id=s["cu"]["address_id"],
            service_ids=[await get_star_wash_service_id(db)], scheduled_date=s["when"], scheduled_slot=s["keys"][0],
            payment_method="subscription", subscription_id=sub), source="app", _allow_pinless=True,
    )
    doc = await fb.doc(db, b["id"])
    assert doc.get("subscription_id") and bm.total_of(doc) == 0
    with pytest.raises(Exception) as exc:
        await give(db, s, b["id"], 10)
    assert "covered by the customer's plan" in str(exc.value)
    assert await db.booking_adjustments.count_documents({"booking_id": b["id"]}) == 0


async def test_idempotency_key_replays_without_a_second_entry(db):
    s = await fb.rig(db)
    b = await fb.book(db, s["cu"], s["when"], s["keys"][0])
    first = await give(db, s, b["id"], 40, idempotency_key="retry-key-0001")
    second = await give(db, s, b["id"], 40, idempotency_key="retry-key-0001")
    assert second["already_applied"] is True and second["discount_after"] == first["discount_after"]
    assert await db.booking_adjustments.count_documents({"booking_id": b["id"]}) == 1
    assert (await fb.doc(db, b["id"]))["manager_discount"] == 40


async def test_stale_version_is_a_409_and_changes_nothing(db):
    s = await fb.rig(db)
    b = await fb.book(db, s["cu"], s["when"], s["keys"][0])
    version = booking_version(await fb.doc(db, b["id"]))
    await give(db, s, b["id"], 10)
    with pytest.raises(Exception) as exc:
        await give(db, s, b["id"], 99, expected_version=version)
    assert getattr(exc.value, "status_code", None) == 409
    assert (await fb.doc(db, b["id"]))["manager_discount"] == 10
    fresh = booking_version(await fb.doc(db, b["id"]))
    await give(db, s, b["id"], 15, expected_version=fresh)
    assert (await fb.doc(db, b["id"]))["manager_discount"] == 15


async def test_parallel_identical_discounts_apply_once(db):
    s = await fb.rig(db)
    b = await fb.book(db, s["cu"], s["when"], s["keys"][0])
    total = bm.total_of(await fb.doc(db, b["id"]))
    out = await asyncio.gather(*[give(db, s, b["id"], 60) for _ in range(6)], return_exceptions=True)
    ok = [r for r in out if isinstance(r, dict)]
    assert ok, out
    after = await fb.doc(db, b["id"])
    assert after["manager_discount"] == 60 and after["total_amount"] == total - 60
    assert await db.booking_adjustments.count_documents({"booking_id": b["id"]}) == 1


async def test_parallel_discount_and_payment_never_double_collect(db):
    s = await fb.rig(db)
    b = await fb.book(db, s["cu"], s["when"], s["keys"][0])
    total = bm.total_of(await fb.doc(db, b["id"]))
    await asyncio.gather(give(db, s, b["id"], 100), fb.pay_online(db, b["id"], total), return_exceptions=True)
    end = await fb.doc(db, b["id"])
    # Whatever the order, nothing is lost: received − (total) = wallet credit, due is 0.
    assert end["amount_due"] == 0 and end["manager_discount"] == 100
    held = await fb.balance(db, s["cu"]["id"])
    assert round(end["amount_paid"] + end["wallet_applied"] + held, 2) == total
    assert held == 100


async def test_late_payment_of_the_old_amount_lands_in_the_wallet(db):
    """A checkout opened for the full bill and paid after the discount took it
    to ₹0: the existing overpayment rule credits the wallet — no double collect."""
    s = await fb.rig(db)
    b = await fb.book(db, s["cu"], s["when"], s["keys"][0], method="online")
    total = bm.total_of(await fb.doc(db, b["id"]))
    await give(db, s, b["id"], int(total))
    assert (await fb.doc(db, b["id"]))["amount_due"] == 0
    await fb.pay_online(db, b["id"], total)
    end = await fb.doc(db, b["id"])
    assert end["amount_due"] == 0 and end["amount_paid"] == 0
    assert await fb.balance(db, s["cu"]["id"]) == total


async def test_cancel_after_discount_refunds_only_what_was_paid(db):
    s = await fb.rig(db)
    b = await fb.book(db, s["cu"], s["when"], s["keys"][0])
    total = bm.total_of(await fb.doc(db, b["id"]))
    await fb.pay_online(db, b["id"])
    await give(db, s, b["id"], 70)
    assert await fb.balance(db, s["cu"]["id"]) == 70
    from app.services.booking_service import BookingService

    await BookingService(db).cancel_booking(b["id"], fb.cancel(), s["mgr"]["id"], "manager", s["center_id"])
    # 70 came back for the discount, the rest (total − 70) on cancel: exactly what was paid.
    assert await fb.balance(db, s["cu"]["id"]) == total


async def test_permissions_and_scopes(db):
    s = await fb.rig(db)
    other = await fb.rig(db)
    b = await fb.book(db, s["cu"], s["when"], s["keys"][0])
    async with h.client() as c:
        url = f"/api/v1/bookings/{b['id']}/discount"
        body = {"amount": 10, "reason": "Loyal customer goodwill"}
        # another center's manager: 404 (existence is not confirmed)
        assert (await c.post(url, json=body, headers=other["mgr"]["h"])).status_code == 404
        assert (await c.post(url, json=body, headers=s["cu"]["h"])).status_code == 403
        assert (await c.post(url, json=body, headers=s["cap"]["h"])).status_code == 403
        assert (await c.post(url, json=body)).status_code in (401, 403)
        assert (await c.post(url, json=body, headers=s["mgr"]["h"])).status_code == 200
        # history: the same scope rules
        assert (await c.get(f"/api/v1/bookings/{b['id']}/adjustments", headers=other["mgr"]["h"])).status_code == 404
        assert (await c.get(f"/api/v1/bookings/{b['id']}/adjustments", headers=s["cu"]["h"])).status_code == 403
        r = await c.get(f"/api/v1/bookings/{b['id']}/adjustments", headers=s["mgr"]["h"])
        assert r.status_code == 200 and len(r.json()["data"]) == 1
        adm = await h.admin(db)
        assert (await c.get(f"/api/v1/bookings/{b['id']}/adjustments", headers=adm["h"])).status_code == 200


async def test_input_attacks_are_422_and_change_nothing(db):
    s = await fb.rig(db)
    b = await fb.book(db, s["cu"], s["when"], s["keys"][0])
    before = await fb.doc(db, b["id"])
    url = f"/api/v1/bookings/{b['id']}/discount"
    bad = [
        {"amount": -5}, {"amount": 10.5}, {"amount": "abc"}, {"amount": "٣٠"}, {"amount": "1e3"}, {"amount": " 12 x"}, {"amount": "nan"}, {"amount": True}, {"amount": 10**15},
        {"percent": -1}, {"percent": 100.01}, {"percent": "x"}, {"amount": 10, "percent": 10}, {},
        {"amount": 10, "total_amount": 1}, {"amount": 10, "amount_paid": 999}, {"amount": 10, "platform_earning": 9999},
        {"amount": 10, "captain_earning": 9999}, {"amount": 10, "status": "completed"}, {"amount": 10, "customer_id": "a" * 24},
        {"amount": 10, "service_center_id": "a" * 24}, {"amount": 10, "idempotency_key": "x"},
    ]
    async with h.client() as c:
        for extra in bad:
            body = {"reason": "A fair reason here", **extra}
            r = await c.post(url, json=body, headers=s["mgr"]["h"])
            assert r.status_code in (400, 422), (extra, r.status_code, r.text)
        r = await c.post(url, json={"amount": 10, "reason": "no"}, headers=s["mgr"]["h"])
        assert r.status_code == 422
        for raw in (b'{"amount": NaN, "reason": "A fair reason"}', b'{"percent": Infinity, "reason": "A fair reason"}',
                    b'{"percent": 1e999, "reason": "A fair reason"}'):
            r = await c.post(url, content=raw, headers={**s["mgr"]["h"], "Content-Type": "application/json"})
            assert r.status_code in (400, 422), (raw, r.status_code)
    after = await fb.doc(db, b["id"])
    assert after["total_amount"] == before["total_amount"] and not after.get("manager_discount")
    assert await db.booking_adjustments.count_documents({"booking_id": b["id"]}) == 0


async def test_completed_booking_only_an_admin_may_discount(db, monkeypatch):
    monkeypatch.setattr("app.services.booking_service._ist_today", lambda: "2999-12-31")
    s = await fb.rig(db)
    b = await fb.book(db, s["cu"], s["when"], s["keys"][0])
    from app.services.booking_service import BookingService

    await BookingService(db).manager_mark_done(b["id"], s["mgr"]["id"], "manager", s["center_id"], send_whatsapp=False)
    with pytest.raises(Exception) as exc:
        await give(db, s, b["id"], 10)
    assert getattr(exc.value, "status_code", None) == 403
    adm = await h.admin(db)
    result = await BookingAdjustService(db).set_discount(b["id"], req(10), {"id": adm["id"], "role": "admin", "center": None})
    assert result["changed"]
    assert await db.audit_logs.find_one({"action": "ADMIN_GIVE_DISCOUNT", "target_id": b["id"]})
    # the manager of the center was told
    assert await fb.bell(db, s["mgr"]["id"], f"Admin Changed A Discount — {(await fb.doc(db, b['id']))['booking_number']}")


async def test_cancelled_booking_cannot_be_discounted(db):
    s = await fb.rig(db)
    b = await fb.book(db, s["cu"], s["when"], s["keys"][0])
    from app.services.booking_service import BookingService

    await BookingService(db).cancel_booking(b["id"], fb.cancel(), s["mgr"]["id"], "manager", s["center_id"])
    with pytest.raises(Exception) as exc:
        await give(db, s, b["id"], 10)
    assert getattr(exc.value, "status_code", None) == 400


async def test_dry_run_changes_nothing_but_returns_the_numbers(db):
    s = await fb.rig(db)
    b = await fb.book(db, s["cu"], s["when"], s["keys"][0])
    total = bm.total_of(await fb.doc(db, b["id"]))
    await fb.pay_online(db, b["id"])
    out = await give(db, s, b["id"], 30, dry_run=True)
    assert out["dry_run"] and out["wallet_credit"] == 30 and out["total_after"] == total - 30
    after = await fb.doc(db, b["id"])
    assert not after.get("manager_discount") and await fb.balance(db, s["cu"]["id"]) == 0
    assert await db.booking_adjustments.count_documents({"booking_id": b["id"]}) == 0


async def test_visit_scope_spreads_whole_rupees_over_the_cars(db):
    s = await fb.rig(db)
    async with h.client() as c:
        _gid, ids = await h.group(c, db, s["cu"], s["when"], s["keys"][0], cars=2)
    docs = [await fb.doc(db, i) for i in ids]
    bill = sum(bm.total_of(d) for d in docs)
    out = await BookingAdjustService(db).set_discount(ids[0], req(51, scope="visit"), actor(s))
    after = [await fb.doc(db, i) for i in ids]
    assert sum(a["manager_discount"] for a in after) == 51 and all(a["manager_discount"] == int(a["manager_discount"]) for a in after)
    assert round(sum(bm.total_of(a) for a in after), 2) == round(bill - 51, 2)
    assert out["discount_after"] == 51 and len(out["cars"]) == 2
    assert await db.booking_adjustments.count_documents({"booking_id": {"$in": ids}}) == 2


async def test_customer_and_captain_views_do_not_expose_the_staff_trail(db):
    s = await fb.rig(db)
    b = await fb.book(db, s["cu"], s["when"], s["keys"][0])
    await fb.assign(db, b["id"], s)
    await give(db, s, b["id"], 20, reason="Secret internal reason")
    async with h.client() as c:
        mine = (await c.get(f"/api/v1/bookings/{b['id']}", headers=s["cu"]["h"])).json()["data"]
        job = (await c.get(f"/api/v1/bookings/{b['id']}", headers=s["cap"]["h"])).json()["data"]
        staff = (await c.get(f"/api/v1/bookings/{b['id']}", headers=s["mgr"]["h"])).json()["data"]
    for view in (mine, job):
        assert "manager_discount_reason" not in view and "manager_discount_by" not in view and "platform_earning" not in view
        assert "payment_adjustments" not in view
    assert mine["manager_discount"] == 20 and "manager_discount" not in job
    assert staff["manager_discount_reason"] == "Secret internal reason" and staff["version"]


async def test_customer_whatsapp_is_throttled_but_every_change_has_an_in_app_line(db):
    s = await fb.rig(db)
    b = await fb.book(db, s["cu"], s["when"], s["keys"][0])
    for amount in (10, 20, 30):
        await give(db, s, b["id"], amount)
    await asyncio.sleep(0.5)       # the send runs in the background
    in_app = await fb.bell(db, s["cu"]["id"], "Discount Applied")
    assert len(in_app) == 3
    queued = await db.whatsapp_queue.count_documents({"user_id": s["cu"]["id"], "title": "Discount Applied"})
    assert queued == 1, queued     # one WhatsApp for the burst (the cooldown), not three
    rows = await db.booking_adjustments.find({"booking_id": b["id"]}).to_list(None)
    assert sum(1 for r in rows if r.get("notified_at")) == 1
