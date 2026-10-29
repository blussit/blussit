"""Founder 2026-10-28: only an admin can edit anything on a booking — even a
completed one — through ONE central recompute: money stays consistent, the
customer's wallet and the captain's wallet move by new ledger rows (never a
rewritten history), payment rows are append-only, every edit is recorded
with before / after, who and why, and a stale edit is a 409."""
import pytest
from bson import ObjectId

from app.schemas.booking_adjust_schema import AdminBookingEditRequest
from app.services import booking_money as bm
from app.services.booking_admin_edit_service import BookingAdminEditService
from app.services.booking_adjust_service import booking_version
from tests import test_feat_booking_helpers as fb
from tests import test_fix_core_helpers as h

pytestmark = pytest.mark.asyncio


async def admin_actor(db):
    a = await h.admin(db)
    return {"id": a["id"], "role": "admin"}


async def edit(db, booking_id, reason="Corrected after the customer called", **fields):
    return await BookingAdminEditService(db).edit(booking_id, AdminBookingEditRequest(reason=reason, **fields), await admin_actor(db))


async def other_service(db, exclude: str) -> dict:
    rows = await fb.services(db, addon=False)
    for s in rows:
        if str(s["_id"]) != exclude and not s.get("prepaid_only") and not s.get("charges_travel"):
            return s
    raise AssertionError("no second service")


async def test_only_an_admin_may_use_the_endpoint(db):
    s = await fb.rig(db)
    other = await fb.rig(db)
    b = await fb.book(db, s["cu"], s["when"], s["keys"][0])
    adm = await h.admin(db)
    url = f"/api/v1/admin/bookings/{b['id']}"
    body = {"reason": "Fixing the notes", "admin_notes": "called twice"}
    async with h.client() as c:
        for headers in (s["mgr"]["h"], other["mgr"]["h"], s["cu"]["h"], s["cap"]["h"], {}):
            r = await c.patch(url, json=body, headers=headers)
            assert r.status_code in (401, 403), (headers, r.status_code)
        r = await c.patch(url, json=body, headers=adm["h"])
        assert r.status_code == 200, r.text
        assert r.json()["data"]["changes"][0]["field"] == "admin_notes"
        for headers in (s["mgr"]["h"], s["cu"]["h"], {}):
            assert (await c.get(url + "/history", headers=headers)).status_code in (401, 403)
        assert (await c.get(url + "/history", headers=adm["h"])).status_code == 200
    assert (await fb.doc(db, b["id"]))["admin_notes"] == "called twice"


async def test_unknown_and_money_fields_are_rejected(db):
    s = await fb.rig(db)
    b = await fb.book(db, s["cu"], s["when"], s["keys"][0])
    adm = await h.admin(db)
    url = f"/api/v1/admin/bookings/{b['id']}"
    before = await fb.doc(db, b["id"])
    async with h.client() as c:
        for extra in ({"total_amount": 1}, {"amount_paid": 999}, {"platform_earning": 5}, {"captain_earning": 9999}, {"service_center_id": "a" * 24},
                      {"center_id": "a" * 24}, {"is_deleted": True}, {"payment_status": "paid"}, {"amount_due": 0},
                      {"manager_discount": -1}, {"manager_discount": 1.5}, {"tip_amount": -5}, {"customer_id": "zzz"},
                      {"address": {"line1": "x road 5", "latitude": 95, "longitude": 10}},
                      {"address": {"line1": "x road 5", "latitude": 10, "longitude": 181}},
                      {"address": {"line1": "x road 5", "latitude": 0, "longitude": 0}},
                      {"payment_adjustments": [{"delta": 0, "method": "cash"}]},
                      {"payment_adjustments": [{"delta": 5, "method": "card"}]},
                      {"status": "teleported"}, {"scheduled_date": "2030-01-01"}):
            r = await c.patch(url, json={"reason": "Trying to break it", "admin_notes": "x", **extra}, headers=adm["h"])
            assert r.status_code == 422, (extra, r.status_code, r.text)
        assert (await c.patch(url, json={"admin_notes": "x"}, headers=adm["h"])).status_code == 422       # no reason
        assert (await c.patch(url, json={"reason": "Nothing in it"}, headers=adm["h"])).status_code == 400   # nothing to change
    after = await fb.doc(db, b["id"])
    assert after["total_amount"] == before["total_amount"] and after.get("admin_notes") is None


async def test_dry_run_shows_the_money_and_changes_nothing(db):
    s = await fb.rig(db)
    b = await fb.book(db, s["cu"], s["when"], s["keys"][0])
    total = bm.total_of(await fb.doc(db, b["id"]))
    await fb.pay_online(db, b["id"])
    out = await edit(db, b["id"], manager_discount=40, dry_run=True)
    assert out["dry_run"] and out["money"]["wallet_credit"] == 40 and out["money"]["after"]["total"] == total - 40
    assert (await fb.doc(db, b["id"])).get("manager_discount") in (None, 0)
    assert await fb.balance(db, s["cu"]["id"]) == 0
    assert await db.booking_adjustments.count_documents({"booking_id": b["id"]}) == 0


async def test_discount_through_the_admin_edit_uses_the_same_money_model(db):
    s = await fb.rig(db)
    b = await fb.book(db, s["cu"], s["when"], s["keys"][0])
    before = await fb.doc(db, b["id"])
    total = bm.total_of(before)
    await fb.pay_online(db, b["id"])
    out = await edit(db, b["id"], manager_discount=55)
    after = await fb.doc(db, b["id"])
    assert after["total_amount"] == total - 55 and after["manager_discount"] == 55
    assert after["platform_earning"] == round(before["platform_earning"] - 55, 2) and after["captain_earning"] == before["captain_earning"]
    assert out["money"]["wallet_credit"] == 55 and await fb.balance(db, s["cu"]["id"]) == 55
    ledger = await db.customer_wallet_ledger.find_one({"booking_id": b["id"]}, sort=[("created_at", -1)])
    assert ledger["kind"] == "adjustment" and ledger["actor_role"] == "admin"
    rows = await db.booking_adjustments.find({"booking_id": b["id"]}).to_list(None)
    assert len(rows) == 1 and rows[0]["kind"] == "admin_edit" and rows[0]["reason"] and rows[0]["changes"]
    audit = await db.audit_logs.find_one({"action": "ADMIN_EDIT_BOOKING", "target_id": b["id"]})
    assert audit and audit["actor_role"] == "admin" and audit["details"]["changes"]
    assert await fb.bell(db, s["mgr"]["id"], f"Admin Edited Booking — {before['booking_number']}")


async def test_services_reprice_unpaid_and_paid(db):
    s = await fb.rig(db)
    b = await fb.book(db, s["cu"], s["when"], s["keys"][0])
    before = await fb.doc(db, b["id"])
    alt = await other_service(db, before["service_ids"][0])
    out = await edit(db, b["id"], service_ids=[str(alt["_id"])])
    after = await fb.doc(db, b["id"])
    assert after["service_ids"] == [str(alt["_id"])]
    assert after["subtotal"] != before["subtotal"]
    assert after["total_amount"] == round(before["total_amount"] - before["subtotal"] + after["subtotal"], 2)
    assert after["platform_earning"] == round(after["total_amount"] - after["captain_earning"], 2)
    assert after["amount_due"] == after["total_amount"] and after["payment_status"] == "pending"
    assert out["money"]["after"]["total"] == after["total_amount"]
    # Paid in full, then repriced down: the difference returns to the wallet.
    await fb.pay_online(db, b["id"])
    paid = bm.amount_paid_of(await fb.doc(db, b["id"]))
    await edit(db, b["id"], service_ids=before["service_ids"])
    end = await fb.doc(db, b["id"])
    assert end["total_amount"] == before["total_amount"]
    if end["total_amount"] < paid:
        assert await fb.balance(db, s["cu"]["id"]) == round(paid - end["total_amount"], 2)
    else:
        assert end["amount_due"] == round(end["total_amount"] - paid, 2)


async def test_address_and_map_pin_are_updated_on_the_visit(db):
    s = await fb.rig(db)
    b = await fb.book(db, s["cu"], s["when"], s["keys"][0])
    import asyncio

    # Regression (found by the live attack run): the saved address used to be
    # created INSIDE the transaction, whose write on the customer's user
    # document blocked AddressService's own write to it — the request hung
    # until the lock timed out. It must finish at once.
    out = await asyncio.wait_for(edit(db, b["id"], address={"line1": "Plot 9, Vijay Nagar", "landmark": "Near the temple", "city": "Indore", "state": "Madhya Pradesh",
                                           "pincode": s["pin"], "latitude": 22.75, "longitude": 75.9}), 20)
    after = await fb.doc(db, b["id"])
    assert after["address_snapshot"]["line1"] == "Plot 9, Vijay Nagar" and after["address_snapshot"]["latitude"] == 22.75
    assert any(c["field"] == "address" for c in out["changes"]) and any(c["field"] == "map_pin" for c in out["changes"])
    saved = await db.addresses.find_one({"_id": ObjectId(after["address_id"])})
    assert saved["owner_id"] == s["cu"]["id"]


async def test_reschedule_an_open_booking_moves_the_seat_and_releases_the_captain(db):
    s = await fb.rig(db)
    b = await fb.book(db, s["cu"], s["when"], s["keys"][0])
    await fb.assign(db, b["id"], s)
    old = await h.seat(db, s["center_id"], s["when"], s["keys"][0])
    await edit(db, b["id"], scheduled_date=s["when"], scheduled_slot=s["keys"][1])
    after = await fb.doc(db, b["id"])
    assert after["scheduled_slot"] == s["keys"][1] and after["status"] == "rescheduled" and after["captain_id"] is None
    new = await h.seat(db, s["center_id"], s["when"], s["keys"][1])
    assert new["booked_count"] == 1
    assert (await h.seat(db, s["center_id"], s["when"], s["keys"][0]))["booked_count"] == old["booked_count"] - 1
    assert after["holds_seat"] is True and after["seat_key"]["slot_key"] == s["keys"][1]


async def test_reschedule_into_a_full_slot_is_refused_and_nothing_moves(db):
    s = await fb.rig(db)
    b = await fb.book(db, s["cu"], s["when"], s["keys"][0])
    await h.set_cap(db, s["center_id"], s["when"], s["keys"][1], 0)
    with pytest.raises(Exception) as exc:
        await edit(db, b["id"], scheduled_date=s["when"], scheduled_slot=s["keys"][1])
    assert "fully booked" in str(exc.value).lower() or "closed" in str(exc.value).lower() or "not" in str(exc.value).lower()
    after = await fb.doc(db, b["id"])
    assert after["scheduled_slot"] == s["keys"][0] and after["holds_seat"] is True


async def test_payment_rows_are_append_only_and_corrections_are_adjustments(db):
    s = await fb.rig(db)
    b = await fb.book(db, s["cu"], s["when"], s["keys"][0])
    total = bm.total_of(await fb.doc(db, b["id"]))
    await fb.pay_online(db, b["id"], 100)
    doc = await fb.doc(db, b["id"])
    rows_before = [dict(p) for p in doc["payments"]]
    key = doc["payment_keys"][0]
    # The admin finds ₹30 of cash that was never entered, and ₹20 of the online money was refunded outside the app.
    await edit(db, b["id"], payment_adjustments=[
        {"delta": 30, "method": "cash", "note": "Cash at the door"},
        {"delta": -20, "method": "online", "payment_key": key, "note": "Refunded in Razorpay dashboard"},
    ])
    after = await fb.doc(db, b["id"])
    assert after["payments"] == rows_before and after["payment_keys"] == [key]
    assert len(after["payment_adjustments"]) == 2 and after["payment_adjustments"][1]["adjusts"] == key
    assert after["amount_paid"] == 110 and after["paid_cash"] == 30 and after["paid_online"] == 80
    assert after["amount_due"] == round(total - 110, 2)
    # Taking out more than was received is refused.
    with pytest.raises(Exception):
        await edit(db, b["id"], payment_adjustments=[{"delta": -500, "method": "online"}])
    with pytest.raises(Exception):
        await edit(db, b["id"], payment_adjustments=[{"delta": 5, "method": "online", "payment_key": "po:not-a-real-payment"}])
    assert (await fb.doc(db, b["id"]))["amount_paid"] == 110


async def test_more_payment_than_the_total_goes_to_the_wallet(db):
    s = await fb.rig(db)
    b = await fb.book(db, s["cu"], s["when"], s["keys"][0])
    total = bm.total_of(await fb.doc(db, b["id"]))
    out = await edit(db, b["id"], payment_adjustments=[{"delta": total + 25, "method": "cash", "note": "Customer overpaid in cash"}])
    after = await fb.doc(db, b["id"])
    assert after["amount_due"] == 0 and after["payment_status"] == "paid" and after["amount_paid"] == total
    assert out["money"]["wallet_credit"] == 25 and await fb.balance(db, s["cu"]["id"]) == 25


async def test_stale_version_and_idempotency(db):
    s = await fb.rig(db)
    b = await fb.book(db, s["cu"], s["when"], s["keys"][0])
    v0 = booking_version(await fb.doc(db, b["id"]))
    await edit(db, b["id"], admin_notes="one", idempotency_key="admin-key-0001")
    again = await edit(db, b["id"], admin_notes="one", idempotency_key="admin-key-0001")
    assert again["already_applied"] is True
    assert await db.booking_adjustments.count_documents({"booking_id": b["id"]}) == 1
    with pytest.raises(Exception) as exc:
        await edit(db, b["id"], admin_notes="two", expected_version=v0)
    assert getattr(exc.value, "status_code", None) == 409
    assert (await fb.doc(db, b["id"]))["admin_notes"] == "one"
    v1 = booking_version(await fb.doc(db, b["id"]))
    await edit(db, b["id"], admin_notes="two", expected_version=v1)
    assert (await fb.doc(db, b["id"]))["admin_notes"] == "two"


async def test_status_rules_override_and_no_reopening_a_cancelled_booking(db):
    s = await fb.rig(db)
    b = await fb.book(db, s["cu"], s["when"], s["keys"][0])
    with pytest.raises(Exception) as exc:
        await edit(db, b["id"], status="service_started")
    assert "Override" in str(exc.value)
    await fb.assign(db, b["id"], s)
    await edit(db, b["id"], status="captain_on_the_way", force_status=True)
    assert (await fb.doc(db, b["id"]))["status"] == "captain_on_the_way"
    await edit(db, b["id"], status="pending", force_status=True, clear_captain=True)
    assert (await fb.doc(db, b["id"]))["captain_id"] is None
    # cancel through the real cancel flow: paid money returns to the wallet
    await fb.pay_online(db, b["id"])
    total = bm.total_of(await fb.doc(db, b["id"]))
    out = await edit(db, b["id"], status="cancelled")
    gone = await fb.doc(db, b["id"])
    assert gone["status"] == "cancelled" and await fb.balance(db, s["cu"]["id"]) == total
    assert out["money"]["wallet_refund_on_cancel"] == total
    with pytest.raises(Exception) as exc:
        await edit(db, b["id"], status="pending", force_status=True)
    assert "re-opened" in str(exc.value)
    with pytest.raises(Exception) as exc:
        await edit(db, b["id"], manager_discount=10)
    assert "cancelled" in str(exc.value)
    # but a cancelled booking's notes can still be fixed
    await edit(db, b["id"], admin_notes="cleared up with the customer")


async def test_completed_booking_edit_moves_customer_and_captain_wallets_by_new_rows(db):
    s = await fb.rig(db)
    b = await fb.book(db, s["cu"], s["when"], s["keys"][0])
    await fb.assign(db, b["id"], s)
    await fb.pay_online(db, b["id"])
    await edit(db, b["id"], status="completed", force_status=True)
    done = await fb.doc(db, b["id"])
    assert done["status"] == "completed" and done["completed_at"]
    earning = done["captain_earning"]
    assert earning > 0
    cap_before = await db.captain_wallets.find_one({"captain_id": s["cap"]["id"]})
    rows_before = await db.wallet_transactions.count_documents({"booking_id": b["id"]})
    assert rows_before >= 1
    # A discount after completion: the customer's money returns, the captain is untouched.
    await edit(db, b["id"], manager_discount=60)
    mid = await db.captain_wallets.find_one({"captain_id": s["cap"]["id"]})
    assert mid["balance"] == cap_before["balance"]
    assert await fb.balance(db, s["cu"]["id"]) == 60
    # A tip on a completed job: part of the total, platform money, due.
    out = await edit(db, b["id"], tip_amount=30, tip_method="cash")
    tipped = await fb.doc(db, b["id"])
    assert tipped["tip_amount"] == 30 and tipped["amount_due"] == 30 and tipped["captain_earning"] == earning
    assert out["money"]["customer_owes"] == 30
    # Re-open the job: the captain's earning comes back out as a NEW ledger row.
    await edit(db, b["id"], status="assigned", force_status=True)
    wallet = await db.captain_wallets.find_one({"captain_id": s["cap"]["id"]})
    assert round(wallet["balance"], 2) == round(cap_before["balance"] - earning, 2)
    assert await db.wallet_transactions.count_documents({"booking_id": b["id"]}) == rows_before + 1
    # Completed again, then a different captain: the first is reversed, the second credited.
    await edit(db, b["id"], status="completed", force_status=True)
    other = await h.captain(db, s["center_id"], wallet=500.0)
    await edit(db, b["id"], captain_id=other["id"])
    first = await db.captain_wallets.find_one({"captain_id": s["cap"]["id"]})
    second = await db.captain_wallets.find_one({"captain_id": other["id"]})
    assert round(first["balance"], 2) == round(cap_before["balance"] - earning, 2)
    assert round(second["balance"], 2) == round(500.0 + earning, 2)


async def test_tip_needs_a_completed_booking(db):
    s = await fb.rig(db)
    b = await fb.book(db, s["cu"], s["when"], s["keys"][0])
    with pytest.raises(Exception) as exc:
        await edit(db, b["id"], tip_amount=20)
    assert "completed" in str(exc.value)


async def test_customer_reassignment_rules(db):
    s = await fb.rig(db)
    other_cu = await h.customer(db, s["pin"])
    b = await fb.book(db, s["cu"], s["when"], s["keys"][0])
    await edit(db, b["id"], customer_id=other_cu["id"])
    moved = await fb.doc(db, b["id"])
    assert moved["customer_id"] == other_cu["id"]
    # A customer that doesn't exist, or a staff account, is refused.
    with pytest.raises(Exception):
        await edit(db, b["id"], customer_id="a" * 24)
    with pytest.raises(Exception):
        await edit(db, b["id"], customer_id=s["mgr"]["id"])
    # A booking that spent the customer's wallet credit stays with them.
    await fb.credit(db, s["cu"]["id"], 100)
    spent = await fb.book(db, s["cu"], s["when"], s["keys"][1])
    assert bm.wallet_applied_of(await fb.doc(db, spent["id"])) > 0
    with pytest.raises(Exception) as exc:
        await edit(db, spent["id"], customer_id=other_cu["id"])
    assert "wallet" in str(exc.value).lower()


async def test_remove_coupon_and_added_service_lines(db):
    s = await fb.rig(db)
    b = await fb.book(db, s["cu"], s["when"], s["keys"][0])
    addon = (await fb.services(db, addon=True))
    from app.services.booking_service import BookingService

    if addon:
        added = await BookingService(db).add_services_on_site(b["id"], [str(addon[0]["_id"])], {}, s["mgr"]["id"], "manager", s["center_id"])
        with_addon = await fb.doc(db, b["id"])
        assert with_addon["added_services_total"] > 0
        await edit(db, b["id"], remove_added_services=[0])
        gone = await fb.doc(db, b["id"])
        assert gone["added_services_total"] == 0 and gone["total_amount"] == round(with_addon["total_amount"] - added["added_total"], 2)
        assert gone["added_services"] == []
        with pytest.raises(Exception):
            await edit(db, b["id"], remove_added_services=[3])


async def test_admin_discount_edit_tells_the_customer_the_discount_line(db):
    s = await fb.rig(db)
    b = await fb.book(db, s["cu"], s["when"], s["keys"][0])
    number = (await fb.doc(db, b["id"]))["booking_number"]
    total = bm.total_of(await fb.doc(db, b["id"]))
    await edit(db, b["id"], manager_discount=60)
    notes = await fb.bell(db, s["cu"]["id"], "Discount Applied")
    assert notes and f"A Discount Of ₹60 Was Applied To Your Booking {number}. New Amount Due: ₹{total - 60:g}" in notes[0]["message"]
    assert not await fb.bell(db, s["cu"]["id"], "Booking Updated")


async def test_coupon_is_released_when_the_admin_removes_it(db):
    from app.models.base import utcnow  # noqa: F401

    s = await fb.rig(db)
    b = await fb.book(db, s["cu"], s["when"], s["keys"][0])
    doc = await fb.doc(db, b["id"])
    # A coupon the booking holds (as a customer's coupon booking would): ₹30 off, one use counted.
    cid = (await db.coupons.insert_one({
        "code": "ADMINRM30", "discount_type": "flat", "discount_value": 30, "is_active": True, "is_deleted": False, "total_used": 1,
        "usage_limit": 5, "created_at": doc["created_at"], "updated_at": doc["created_at"],
    })).inserted_id
    await db.coupon_usages.insert_one({"coupon_id": str(cid), "user_id": s["cu"]["id"], "booking_id": b["id"], "created_at": doc["created_at"]})
    total = bm.total_of(doc)
    await db.bookings.update_one({"_id": doc["_id"]}, {"$set": {"coupon_code": "ADMINRM30", "discount_amount": 30.0, "total_amount": total - 30,
                                                                "platform_earning": round(doc["platform_earning"] - 30, 2), "amount_due": total - 30}})
    out = await edit(db, b["id"], remove_coupon=True)
    after = await fb.doc(db, b["id"])
    assert after["coupon_code"] is None and after["total_amount"] == total and after["amount_due"] == total
    assert any(c["field"] == "coupon_code" for c in out["changes"])
    assert (await db.coupons.find_one({"_id": cid}))["total_used"] == 0
    assert await db.coupon_usages.count_documents({"booking_id": b["id"]}) == 0


async def test_visit_level_fields_apply_to_every_car_money_to_the_one(db):
    s = await fb.rig(db)
    async with h.client() as c:
        _gid, ids = await h.group(c, db, s["cu"], s["when"], s["keys"][0], cars=2)
    before = [await fb.doc(db, i) for i in ids]
    await edit(db, ids[0], customer_notes="Gate code 4455", scheduled_date=s["when"], scheduled_slot=s["keys"][1], manager_discount=20)
    after = [await fb.doc(db, i) for i in ids]
    assert all(a["customer_notes"] == "Gate code 4455" and a["scheduled_slot"] == s["keys"][1] for a in after)
    assert after[0]["manager_discount"] == 20 and not after[1].get("manager_discount")
    assert after[1]["total_amount"] == before[1]["total_amount"]
    # one seat for the visit, on the new slot
    assert (await h.seat(db, s["center_id"], s["when"], s["keys"][1]))["booked_count"] == 1
    assert (await h.seat(db, s["center_id"], s["when"], s["keys"][0]))["booked_count"] == 0


async def test_captain_on_the_way_cannot_be_swapped_or_moved_without_override(db):
    s = await fb.rig(db)
    b = await fb.book(db, s["cu"], s["when"], s["keys"][0])
    await fb.assign(db, b["id"], s)
    await db.bookings.update_one({"_id": ObjectId(b["id"])}, {"$set": {"status": "captain_on_the_way"}})
    other = await h.captain(db, s["center_id"], wallet=500.0)
    with pytest.raises(Exception) as exc:
        await edit(db, b["id"], captain_id=other["id"])
    assert "Override" in str(exc.value)
    with pytest.raises(Exception) as exc:
        await edit(db, b["id"], scheduled_date=s["when"], scheduled_slot=s["keys"][1])
    assert "on the way" in str(exc.value)
    await edit(db, b["id"], captain_id=other["id"], force_status=True)
    assert (await fb.doc(db, b["id"]))["captain_id"] == other["id"]
