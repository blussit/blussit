"""Feature build 2026-10-07 — booking money: the wallet at create (spec
1.1), on-site add-ons (1.4) and completion & collection (1.5): completion
no longer marks a cash booking paid, the captain's wallet moves by deltas,
the captain collects amount_due."""
import asyncio
from datetime import datetime, timedelta, timezone

import pytest
from bson import ObjectId

from app.core.exceptions import BadRequestException, ForbiddenException
from app.services.booking_service import BookingService
from app.services.payment_service import PaymentService
from tests import test_feat_booking_helpers as fb
from tests import test_fix_core_helpers as h

pytestmark = pytest.mark.asyncio


async def _complete(db, s: dict, booking_id: str) -> dict:
    """The captain finishes the job (after-photo) — the real completion path."""
    await db.bookings.update_one({"_id": ObjectId(booking_id)}, {"$set": {
        "captain_id": s["cap"]["id"], "status": "service_started", "vehicle_verified": True,
        "service_started_at": datetime.now(timezone.utc) - timedelta(minutes=30),
    }})
    return await BookingService(db).capture_after_photo_and_complete(booking_id, await fb.hb.snap(db, s["cap"]["id"], "after"), s["cap"]["id"])


async def _polish(db) -> dict:
    return await db.services.find_one({"slug": "exterior-polish"})


# ---------------------------------------------------------------- completion & collection


async def test_cash_completion_is_not_paid_until_the_captain_collects(db):
    s = await fb.rig(db)
    b = await fb.book(db, s["cu"], s["when"], s["keys"][0])
    before = await fb.hb.wallet(db, s["cap"]["id"])
    done = await _complete(db, s, b["id"])
    d = await fb.doc(db, b["id"])
    assert d["status"] == "completed" and d["payment_status"] == "pending"  # founder rule: collection pays it
    # The captain is owed his earning for the job (delta posting) …
    assert await fb.hb.wallet(db, s["cap"]["id"]) == pytest.approx(before + d["captain_earning"])
    assert d["captain_wallet_posted"] == pytest.approx(d["captain_earning"])
    # … a retried after-photo changes nothing …
    again = await BookingService(db).capture_after_photo_and_complete(b["id"], await fb.hb.snap(db, s["cap"]["id"], "after2"), s["cap"]["id"])
    assert again["status"] == "completed" and await fb.hb.wallet(db, s["cap"]["id"]) == pytest.approx(before + d["captain_earning"])
    # … and collecting the cash nets it to minus the platform's share.
    out = await PaymentService(db).captain_collect_cash(b["id"], s["cap"]["id"])
    assert out["amount"] == d["total_amount"]
    d2 = await fb.doc(db, b["id"])
    assert d2["payment_status"] == "paid" and d2["amount_due"] == 0
    assert await fb.hb.wallet(db, s["cap"]["id"]) == pytest.approx(before + d["captain_earning"] - d["total_amount"])
    assert done["status"] == "completed"


async def test_completion_vs_online_payment_at_once_keeps_the_money_straight(db):
    for _ in range(3):
        s = await fb.rig(db)
        b = await fb.book(db, s["cu"], s["when"], s["keys"][0])
        before = await fb.hb.wallet(db, s["cap"]["id"])
        await db.bookings.update_one({"_id": ObjectId(b["id"])}, {"$set": {
            "captain_id": s["cap"]["id"], "status": "service_started", "vehicle_verified": True,
            "service_started_at": datetime.now(timezone.utc) - timedelta(minutes=30),
        }})
        photo = await fb.hb.snap(db, s["cap"]["id"], "after")
        results = await asyncio.gather(
            BookingService(db).capture_after_photo_and_complete(b["id"], photo, s["cap"]["id"]),
            fb.pay_online(db, b["id"], b["total_amount"]),
            return_exceptions=True,
        )
        assert all(not isinstance(r, Exception) for r in results), results
        d = await fb.doc(db, b["id"])
        assert d["status"] == "completed" and d["payment_status"] == "paid"
        # Paid online: the captain holds nothing — he is just owed his earning.
        assert await fb.hb.wallet(db, s["cap"]["id"]) == pytest.approx(before + d["captain_earning"])


# ---------------------------------------------------------------- on-site add-ons


async def test_captain_adds_after_arrival_earning_unchanged_due_grows(db):
    s = await fb.rig(db)
    b = await fb.book(db, s["cu"], s["when"], s["keys"][0])
    polish = await _polish(db)
    svc = BookingService(db)
    await fb.assign(db, b["id"], s)
    with pytest.raises(BadRequestException, match="reached the customer"):
        await svc.add_services_on_site(b["id"], [str(polish["_id"])], {}, s["cap"]["id"], "captain")
    await fb.hb.on_the_way(db, b["id"], s["cap"]["id"])
    with pytest.raises(BadRequestException, match="Verify your arrival"):
        await svc.add_services_on_site(b["id"], [str(polish["_id"])], {}, s["cap"]["id"], "captain")
    await db.bookings.update_one({"_id": ObjectId(b["id"])}, {"$set": {"vehicle_verified": True}})
    before = await fb.doc(db, b["id"])
    async with h.client() as c:
        r = await c.post(f"/api/v1/bookings/{b['id']}/add-services", json={"service_ids": [str(polish["_id"])]}, headers=s["cap"]["h"])
    assert r.status_code == 200, r.text
    out = r.json()["data"]
    price = BookingService._resolve_price(polish, before["vehicle_type"], False)
    d = await fb.doc(db, b["id"])
    assert out["added_total"] == price and d["total_amount"] == pytest.approx(before["total_amount"] + price)
    assert d["captain_earning"] == before["captain_earning"]  # founder: unchanged
    assert d["platform_earning"] == pytest.approx(before["platform_earning"] + price)
    assert d["duration_minutes"] == before["duration_minutes"] + polish["duration_minutes"]
    [entry] = d["added_services"]
    assert entry["role"] == "captain" and entry["by"] == s["cap"]["id"] and entry["amount"] == price and entry["qty"] == 1
    assert "platform_earning" not in out["booking"]  # captain view stays redacted
    assert await fb.bell(db, s["mgr"]["id"], f"Services added — {d['booking_number']}")
    assert await db.audit_logs.find_one({"action": "ADD_SERVICES_ON_SITE", "target_id": b["id"]})
    # Not twice: Exterior Polish is a one-per-car add-on.
    with pytest.raises(BadRequestException):
        await svc.add_services_on_site(b["id"], [str(polish["_id"])], {}, s["cap"]["id"], "captain")
    # Collected with the job: the captain takes the whole new total.
    await _complete(db, s, b["id"])
    out = await PaymentService(db).captain_collect_cash(b["id"], s["cap"]["id"])
    assert out["amount"] == pytest.approx(d["total_amount"])


async def test_manager_adds_after_completion_on_prepaid_due_collected_by_qr(db):
    s = await fb.rig(db)
    b = await fb.book(db, s["cu"], s["when"], s["keys"][0])
    await fb.pay_online(db, b["id"])
    before = await fb.hb.wallet(db, s["cap"]["id"])
    await _complete(db, s, b["id"])
    done = await fb.doc(db, b["id"])
    assert done["payment_status"] == "paid"
    after_completion = await fb.hb.wallet(db, s["cap"]["id"])
    assert after_completion == pytest.approx(before + done["captain_earning"])
    # The captain can't add to a fully paid, finished visit — the manager can.
    polish = await _polish(db)
    with pytest.raises(BadRequestException, match="fully paid"):
        await BookingService(db).add_services_on_site(b["id"], [str(polish["_id"])], {}, s["cap"]["id"], "captain")
    out = await BookingService(db).add_services_on_site(b["id"], [str(polish["_id"])], {}, s["mgr"]["id"], "manager", s["center_id"])
    d = await fb.doc(db, b["id"])
    assert out["amount_due"] == out["added_total"] and d["payment_status"] == "partially_paid"
    assert d["added_services"][0]["stage"] == "completed"
    # The captain shows the QR; the customer pays it online → nothing changes on his wallet.
    await fb.pay_online(db, b["id"], out["amount_due"])
    await BookingService(db).money.settle_captain_wallet([b["id"]])
    assert (await fb.doc(db, b["id"]))["payment_status"] == "paid"
    assert await fb.hb.wallet(db, s["cap"]["id"]) == pytest.approx(after_completion)


async def test_add_services_authz(db):
    s = await fb.rig(db)
    b = await fb.book(db, s["cu"], s["when"], s["keys"][0])
    polish = await _polish(db)
    other_center, _pin = await h.center(db)
    stranger = await h.manager(db, other_center)
    async with h.client() as c:
        r = await c.post(f"/api/v1/bookings/{b['id']}/add-services", json={"service_ids": [str(polish["_id"])]}, headers=stranger["h"])
        assert r.status_code == 403
        r = await c.post(f"/api/v1/bookings/{b['id']}/add-services", json={"service_ids": [str(polish["_id"])]}, headers=s["cu"]["h"])
        assert r.status_code == 403
        other_cap = await h.captain(db, s["center_id"])
        await fb.hb.on_the_way(db, b["id"], s["cap"]["id"], vehicle_verified=True)
        r = await c.post(f"/api/v1/bookings/{b['id']}/add-services", json={"service_ids": [str(polish["_id"])]}, headers=other_cap["h"])
        assert r.status_code == 403
    await BookingService(db).cancel_booking(b["id"], fb.cancel("Rain"), s["mgr"]["id"], "manager", s["center_id"])
    with pytest.raises(BadRequestException, match="cancelled"):
        await BookingService(db).add_services_on_site(b["id"], [str(polish["_id"])], {}, s["mgr"]["id"], "manager", s["center_id"])


async def test_add_on_vs_online_payment_at_once(db):
    for _ in range(3):
        s = await fb.rig(db)
        b = await fb.book(db, s["cu"], s["when"], s["keys"][0])
        polish = await _polish(db)
        await fb.hb.on_the_way(db, b["id"], s["cap"]["id"], vehicle_verified=True)
        results = await asyncio.gather(
            BookingService(db).add_services_on_site(b["id"], [str(polish["_id"])], {}, s["cap"]["id"], "captain"),
            fb.pay_online(db, b["id"], b["total_amount"]),
            return_exceptions=True,
        )
        assert all(not isinstance(r, Exception) for r in results), results
        d = await fb.doc(db, b["id"])
        price = BookingService._resolve_price(polish, d["vehicle_type"], False)
        assert d["total_amount"] == pytest.approx(b["total_amount"] + price)
        assert d["amount_paid"] == pytest.approx(b["total_amount"])
        assert d["amount_due"] == pytest.approx(price) and d["payment_status"] == "partially_paid"
        assert await fb.balance(db, s["cu"]["id"]) == 0


# ---------------------------------------------------------------- the wallet at create


async def test_negative_balance_rides_on_the_next_booking_quote_agrees(db):
    s = await fb.rig(db)
    cu = s["cu"]["id"]
    await fb.credit(db, cu, -50)
    hatch = await fb.get_hatchback_type_id(db)
    star = await fb.get_star_wash_service_id(db)
    async with h.client() as c:
        q = (await c.post("/api/v1/bookings/quote", json={"lines": [{"vehicle_type": hatch, "quantity": 1, "service_ids": [star]}],
                                                          "address_id": s["cu"]["address_id"], "scheduled_date": s["when"]}, headers=s["cu"]["h"])).json()["data"]
        assert q["previous_balance_due"] == 50 and q["amount_payable"] == q["total_amount"]
        r = await h.book(c, db, s["cu"], s["when"], s["keys"][0], expected_total=q["total_amount"] - 50)
        assert r.status_code == 409 and r.json()["details"]["previous_balance_due"] == 50
        r = await h.book(c, db, s["cu"], s["when"], s["keys"][0], expected_total=q["total_amount"])
        assert r.status_code == 200, r.text
    d = await fb.doc(db, r.json()["data"]["id"])
    assert d["wallet_due_carried"] == 50 and d["total_amount"] == q["total_amount"]
    # Carried, not yet paid: the next booking doesn't carry it again.
    nxt = await fb.book(db, s["cu"], s["when"], s["keys"][1])
    assert (await fb.doc(db, nxt["id"]))["wallet_due_carried"] == 0
    # Paid with the booking: the debt is cleared.
    await fb.pay_online(db, d["_id"].__str__())
    assert await fb.balance(db, cu) == 0


async def test_negative_balance_makes_a_plan_booking_payable(db):
    s = await fb.rig(db)
    cu = s["cu"]["id"]
    await fb.credit(db, cu, -80)
    sub = await fb.pass_for(db, cu)
    b = await fb.book(db, s["cu"], s["when"], s["keys"][0], subscription_id=sub)
    d = await fb.doc(db, b["id"])
    assert d["total_amount"] == 80 and d["wallet_due_carried"] == 80
    assert d["payment_status"] == "pending" and d["status"] == "pending" and d["amount_due"] == 80


async def test_positive_balance_covers_the_booking_no_collection(db):
    s = await fb.rig(db)
    cu = s["cu"]["id"]
    await fb.credit(db, cu, 1000)
    b = await fb.book(db, s["cu"], s["when"], s["keys"][0])
    d = await fb.doc(db, b["id"])
    assert d["wallet_applied"] == d["total_amount"] and d["payment_status"] == "paid" and d["amount_due"] == 0
    assert await fb.balance(db, cu) == pytest.approx(1000 - d["total_amount"])
    before = await fb.hb.wallet(db, s["cap"]["id"])
    await _complete(db, s, b["id"])
    with pytest.raises(BadRequestException, match="nothing to collect"):
        await PaymentService(db).captain_collect_cash(b["id"], s["cap"]["id"])
    assert await fb.hb.wallet(db, s["cap"]["id"]) == pytest.approx(before + d["captain_earning"])
    # A cancelled wallet-paid booking gives the credit back.
    b2 = await fb.book(db, s["cu"], s["when"], s["keys"][1])
    used = (await fb.doc(db, b2["id"]))["wallet_applied"]
    bal = await fb.balance(db, cu)
    await BookingService(db).cancel_booking(b2["id"], fb.cancel(), cu, "customer")
    assert await fb.balance(db, cu) == pytest.approx(bal + used)


async def test_online_booking_part_covered_by_wallet_waits_for_the_rest(db, monkeypatch):
    h.install_rzp_stub(monkeypatch)
    s = await fb.rig(db)
    cu = s["cu"]["id"]
    await fb.credit(db, cu, 100)
    b = await fb.book(db, s["cu"], s["when"], s["keys"][0], method="online")
    d = await fb.doc(db, b["id"])
    assert d["wallet_applied"] == 100 and d["status"] == "awaiting_payment" and d["amount_due"] == pytest.approx(d["total_amount"] - 100)
    # Expiry sweep: unpaid → released, the credit comes back.
    await BookingService(db).cancel_booking(b["id"], fb.cancel("Payment wasn't completed in time"), "system", "admin", only_if_unpaid=True)
    assert await fb.balance(db, cu) == 100


async def test_two_bookings_cannot_spend_the_same_credit(db):
    for _ in range(3):
        s = await fb.rig(db)
        cu = s["cu"]["id"]
        await fb.credit(db, cu, 400)
        results = await asyncio.gather(
            fb.book(db, s["cu"], s["when"], s["keys"][0]),
            fb.book(db, s["cu"], s["when"], s["keys"][1]),
            return_exceptions=True,
        )
        made = [r for r in results if isinstance(r, dict)]
        assert len(made) == 2, results
        spent = sum([(await fb.doc(db, r["id"]))["wallet_applied"] for r in made])
        assert spent == pytest.approx(400) and await fb.balance(db, cu) == pytest.approx(0)


async def test_soft_delete_returns_wallet_money_and_restore_leaves_it_due(db):
    s = await fb.rig(db)
    cu = s["cu"]["id"]
    await fb.credit(db, cu, 100)
    b = await fb.book(db, s["cu"], s["when"], s["keys"][0])
    d = await fb.doc(db, b["id"])
    assert d["wallet_applied"] == 100 and await fb.balance(db, cu) == 0
    adm = await h.admin(db)
    svc = BookingService(db)
    await svc.soft_delete_booking(b["id"], adm["id"])
    assert await fb.balance(db, cu) == 100  # the credit is back
    await svc.restore_booking(b["id"])
    back = await fb.doc(db, b["id"])
    assert back["wallet_applied"] == 0 and back["amount_due"] == back["total_amount"] and back["payment_status"] == "pending"
    assert await fb.balance(db, cu) == 100  # not spent twice
    # A carried debt: released on delete, not carried twice after restore.
    s2 = await fb.rig(db)
    await fb.credit(db, s2["cu"]["id"], -50)
    b2 = await fb.book(db, s2["cu"], s2["when"], s2["keys"][0])
    total = (await fb.doc(db, b2["id"]))["total_amount"]
    await svc.soft_delete_booking(b2["id"], adm["id"])
    from app.services.customer_wallet_service import CustomerWalletService

    assert (await CustomerWalletService(db).summary(s2["cu"]["id"]))["previous_balance_due"] == 50
    await svc.restore_booking(b2["id"])
    back2 = await fb.doc(db, b2["id"])
    assert back2["total_amount"] == total - 50 and back2["wallet_due_carried"] == 0
    assert (await CustomerWalletService(db).summary(s2["cu"]["id"]))["previous_balance_due"] == 50
