"""Core fix round 1 — money guards (audit 2026-10-07: PAY-02, PAY-04, PAY-05,
PAY-06). Every test races the REAL settle path (PaymentService.verify_payment
with a stubbed gateway) against the real booking transition."""
import asyncio
import random

import pytest
from bson import ObjectId

from app.models.enums import PaymentMethod
from app.schemas.booking_schema import BookingCancelRequest, BookingCreateRequest, PhotoCaptureRequest
from app.schemas.payment_schema import CreateOrderRequest
from app.services.booking_service import BookingService
from app.services.payment_service import PaymentService
from app.utils.timezone import now_ist
from tests import test_fix_core_helpers as h
from tests.factories import get_hatchback_type_id, get_star_wash_service_id, make_captain, make_manager, make_recorded_photo_url

pytestmark = pytest.mark.asyncio
EXPIRY = BookingCancelRequest(reason="Payment wasn't completed in time, so the slot was released.")


async def _wallet(db, captain_id: str) -> float:
    return float((await db.captain_wallets.find_one({"captain_id": captain_id}))["balance"])


async def _new_booking(db, *, method=PaymentMethod.ONLINE, source="app") -> tuple[dict, dict, str, str]:
    cid, pin = await h.center(db)
    cu = await h.customer(db, pin)
    when, keys = await h.slot_keys(db, cid)
    booking = await BookingService(db).create_booking(
        cu["id"],
        BookingCreateRequest(
            vehicle_type=await get_hatchback_type_id(db), address_id=cu["address_id"], service_ids=[await get_star_wash_service_id(db)],
            scheduled_date=when, scheduled_slot=keys[0], payment_method=method,
        ),
        source=source,
        _allow_pinless=True,
    )
    return booking, cu, cid, when


async def _pay(db, customer_id: str, order_id: str, payment_id: str):
    return await h.pay_order(db, customer_id, order_id, payment_id)


# ---------------------------------------------------------------- PAY-02


async def test_pay02_expiry_after_late_payment_is_a_silent_no_op(db, monkeypatch):
    """main.py expire(): prepare_expiry said "safe", then the payment landed,
    then the sweep's cancel ran. The paid booking must survive untouched."""
    h.install_rzp_stub(monkeypatch)
    booking, cu, cid, when = await _new_booking(db)
    bid = booking["id"]
    order = await PaymentService(db).create_order(cu["id"], CreateOrderRequest(purpose="booking", booking_id=bid))
    await _pay(db, cu["id"], order["order_id"], "pay_fc_late")
    before = await h.seat(db, cid, when, booking["scheduled_slot"])
    result = await BookingService(db).cancel_booking(bid, EXPIRY, actor_id="system", actor_role="admin", only_if_unpaid=True)
    doc = await db.bookings.find_one({"_id": ObjectId(bid)})
    after = await h.seat(db, cid, when, booking["scheduled_slot"])
    assert result is None
    assert doc["status"] == "pending" and doc["payment_status"] == "paid"
    assert after["booked_count"] == before["booked_count"] == 1
    assert await db.payment_orders.count_documents({"kind": "refund_due", "booking_id": bid}) == 0
    assert await db.notifications.count_documents({"reference_id": bid, "title": "Booking cancelled"}) == 0


async def test_pay02_expiry_still_cancels_an_unpaid_booking(db, monkeypatch):
    h.install_rzp_stub(monkeypatch)
    booking, cu, cid, when = await _new_booking(db)
    result = await BookingService(db).cancel_booking(booking["id"], EXPIRY, actor_id="system", actor_role="admin", only_if_unpaid=True)
    assert result and result["status"] == "cancelled"
    assert (await h.seat(db, cid, when, booking["scheduled_slot"]))["booked_count"] == 0


async def test_pay02_settle_racing_expiry_never_paid_and_cancelled(db, monkeypatch):
    h.install_rzp_stub(monkeypatch)
    outcomes = []
    for i in range(14):
        booking, cu, cid, when = await _new_booking(db)
        bid = booking["id"]
        order = await PaymentService(db).create_order(cu["id"], CreateOrderRequest(purpose="booking", booking_id=bid))

        async def settle():
            await asyncio.sleep(random.uniform(0, 0.02))
            return await _pay(db, cu["id"], order["order_id"], f"pay_fc_race_{i}")

        async def expire():
            await asyncio.sleep(random.uniform(0, 0.02))
            return await BookingService(db).cancel_booking(bid, EXPIRY, actor_id="system", actor_role="admin", only_if_unpaid=True)

        res = await asyncio.gather(settle(), expire(), return_exceptions=True)
        assert not isinstance(res[1], Exception), res[1]
        doc = await db.bookings.find_one({"_id": ObjectId(bid)})
        refunds = await db.payment_orders.count_documents({"kind": "refund_due", "booking_id": bid})
        st = await h.state(db, cid, when, booking["scheduled_slot"])
        outcomes.append((doc["status"], doc["payment_status"]))
        assert not (doc["status"] == "cancelled" and doc["payment_status"] == "paid"), (i, doc["status"], doc["payment_status"])
        assert not (refunds and doc["payment_status"] == "paid")
        assert st["booked"] == st["owners"] == (0 if doc["status"] == "cancelled" else 1), st
    print("\n[PAY-02 race]", outcomes)


async def test_pay02_group_expiry_skips_paid_car_cancels_unpaid(db, monkeypatch):
    """A visit whose first car was paid on its own order: the sweep may only
    release the car that is still unpaid."""
    h.install_rzp_stub(monkeypatch)
    cid, pin = await h.center(db)
    cu = await h.customer(db, pin)
    when, keys = await h.slot_keys(db, cid)
    async with h.client() as c:
        gid, ids = await h.group(c, db, cu, when, keys[0], cars=2, payment_method="online")
    # Car 1 settled on its own (a single-car link paid) — what the settle
    # path writes, then the confirm it runs.
    await db.bookings.update_one(
        {"_id": ObjectId(ids[0])}, {"$set": {"payment_status": "paid", "payment_method": "online", "razorpay_payment_id": "pay_fc_grp"}}
    )
    await BookingService(db).confirm_awaiting_payment_booking(ids[0], "Payment confirmed")
    out = await BookingService(db).cancel_booking_group(gid, EXPIRY, actor_id="system", actor_role="admin", only_if_unpaid=True)
    cars = {str(b["_id"]): b for b in await db.bookings.find({"booking_group_id": gid}).to_list(None)}
    assert cars[ids[0]]["status"] == "pending" and cars[ids[0]]["payment_status"] == "paid"
    assert cars[ids[1]]["status"] == "cancelled"
    assert out["cancelled_count"] == 1
    st = await h.state(db, cid, when, keys[0])
    assert st["booked"] == st["owners"] == 1


# ---------------------------------------------------------------- PAY-04


async def _started_cash_job(db) -> tuple[dict, dict, str]:
    booking, cu, cid, when = await _new_booking(db, method=PaymentMethod.CASH, source="whatsapp")
    captain_id = await make_captain(db, cid, wallet_balance=500.0)
    await db.bookings.update_one(
        {"_id": ObjectId(booking["id"])},
        {"$set": {"captain_id": captain_id, "status": "service_started", "service_started_at": now_ist(), "vehicle_verified": True}},
    )
    return booking, cu, captain_id


async def _photo(db, captain_id: str, n: str) -> PhotoCaptureRequest:
    # A photo the captain really uploaded (recorded) — CAP-02.
    return PhotoCaptureRequest(image_url=await make_recorded_photo_url(db, captain_id, n), latitude=22.7, longitude=75.8)


async def test_pay04_completion_after_customer_paid_online_credits_captain(db, monkeypatch):
    h.install_rzp_stub(monkeypatch)
    booking, cu, captain_id = await _started_cash_job(db)
    bid = booking["id"]
    order = await PaymentService(db).create_order(cu["id"], CreateOrderRequest(purpose="booking", booking_id=bid))
    before = await _wallet(db, captain_id)
    svc = BookingService(db)
    real = svc._check_geofence

    async def customer_pays_mid_completion(b, lat, lng):
        await _pay(db, cu["id"], order["order_id"], "pay_fc_b1")
        return await real(b, lat, lng)

    monkeypatch.setattr(svc, "_check_geofence", customer_pays_mid_completion)
    await svc.capture_after_photo_and_complete(bid, await _photo(db, captain_id, "pay04"), captain_id)
    doc = await db.bookings.find_one({"_id": ObjectId(bid)})
    after = await _wallet(db, captain_id)
    assert doc["status"] == "completed"
    assert doc["payment_method"] == "online" and doc["payment_status"] == "paid"
    # Spec 1.5 (founder 2026-10-07): completion posts the captain-wallet delta
    # (his earning; he holds no cash) — never a cash share for online money.
    assert doc["captain_wallet_posted"] == pytest.approx(doc["captain_earning"])
    assert after == pytest.approx(before + doc["captain_earning"])


async def test_pay04_completion_racing_payment_settles_from_written_doc(db, monkeypatch):
    h.install_rzp_stub(monkeypatch)
    seen = []
    for i in range(10):
        booking, cu, captain_id = await _started_cash_job(db)
        bid = booking["id"]
        order = await PaymentService(db).create_order(cu["id"], CreateOrderRequest(purpose="booking", booking_id=bid))
        before = await _wallet(db, captain_id)

        async def pay():
            await asyncio.sleep(random.uniform(0, 0.01))
            return await _pay(db, cu["id"], order["order_id"], f"pay_fc_b1r_{i}")

        photo = await _photo(db, captain_id, f"r{i}")

        async def complete():
            await asyncio.sleep(random.uniform(0, 0.01))
            return await BookingService(db).capture_after_photo_and_complete(bid, photo, captain_id)

        res = await asyncio.gather(pay(), complete(), return_exceptions=True)
        assert not isinstance(res[1], Exception), res[1]
        doc = await db.bookings.find_one({"_id": ObjectId(bid)})
        delta = round(await _wallet(db, captain_id) - before, 2)
        seen.append((doc["payment_method"], doc["payment_status"], delta))
        # Spec 1.5: completion no longer turns a cash booking into "paid in
        # cash" — the online payment always lands, whichever wins, and the
        # captain's wallet gets exactly his earning (he holds no cash).
        assert doc["payment_method"] == "online" and doc["payment_status"] == "paid"
        assert delta == pytest.approx(doc["captain_earning"])
    print("\n[PAY-04 race]", seen)


# ---------------------------------------------------------------- PAY-05


@pytest.fixture
def on_the_bookings_day(monkeypatch):
    """MGR-06: a booking is marked done on (or after) its own day, never
    before — these tests book a day or two ahead, so the manager's
    mark-done happens "on the day"."""
    monkeypatch.setattr("app.services.booking_service._ist_today", lambda: "2999-12-31")


async def test_pay05_mark_done_after_customer_paid_online_keeps_online(db, monkeypatch, on_the_bookings_day):
    h.install_rzp_stub(monkeypatch)
    booking, cu, cid, when = await _new_booking(db, method=PaymentMethod.CASH, source="whatsapp")
    bid = booking["id"]
    manager_id = await make_manager(db, cid)
    order = await PaymentService(db).create_order(cu["id"], CreateOrderRequest(purpose="booking", booking_id=bid))
    svc = BookingService(db)
    real = svc._visit_cars

    async def cars_then_customer_pays(b, session=None):
        cars = await real(b, session=session)
        if session is None and not getattr(svc, "_fc_paid", False):
            svc._fc_paid = True
            await _pay(db, cu["id"], order["order_id"], "pay_fc_b2")
        return cars

    monkeypatch.setattr(svc, "_visit_cars", cars_then_customer_pays)
    await svc.manager_mark_done(bid, manager_id, "manager", cid, send_whatsapp=False)
    doc = await db.bookings.find_one({"_id": ObjectId(bid)})
    assert doc["status"] == "completed"
    assert doc["payment_method"] == "online" and doc["payment_status"] == "paid"
    assert doc.get("cash_collected_by") is None


async def test_pay05_mark_done_racing_payment(db, monkeypatch, on_the_bookings_day):
    h.install_rzp_stub(monkeypatch)
    seen = []
    for i in range(10):
        booking, cu, cid, when = await _new_booking(db, method=PaymentMethod.CASH, source="whatsapp")
        bid = booking["id"]
        manager_id = await make_manager(db, cid)
        order = await PaymentService(db).create_order(cu["id"], CreateOrderRequest(purpose="booking", booking_id=bid))

        async def pay():
            await asyncio.sleep(random.uniform(0, 0.01))
            return await _pay(db, cu["id"], order["order_id"], f"pay_fc_b2r_{i}")

        async def done():
            await asyncio.sleep(random.uniform(0, 0.01))
            return await BookingService(db).manager_mark_done(bid, manager_id, "manager", cid, send_whatsapp=False)

        res = await asyncio.gather(pay(), done(), return_exceptions=True)
        assert not isinstance(res[1], Exception), res[1]
        doc = await db.bookings.find_one({"_id": ObjectId(bid)})
        seen.append((doc["payment_method"], doc.get("cash_collected_by") is not None))
        if doc.get("razorpay_payment_id"):
            assert doc["payment_method"] == "online" and doc.get("cash_collected_by") is None
        else:
            assert doc["payment_method"] == "cash" and doc.get("cash_collected_by") == manager_id
    print("\n[PAY-05 race]", seen)


# ---------------------------------------------------------------- PAY-06


async def _open_link(db, booking_id: str) -> dict:
    raw = await db.bookings.find_one({"_id": ObjectId(booking_id)})
    await PaymentService(db).create_payment_link(raw, contact_phone="9876500000", name="Test")
    return await db.payment_orders.find_one({"kind": "link", "booking_id": booking_id, "status": "created"})


async def test_pay06_cash_completion_voids_the_open_payment_link(db, monkeypatch):
    # Spec 1.5: completion only closes the job (the customer may still pay
    # online); the captain's "Collect Cash" is what takes the money — and
    # that is what voids the open link.
    stub = h.install_rzp_stub(monkeypatch)
    booking, cu, captain_id = await _started_cash_job(db)
    link = await _open_link(db, booking["id"])
    assert link
    await BookingService(db).capture_after_photo_and_complete(booking["id"], await _photo(db, captain_id, "pay06"), captain_id)
    assert (await db.payment_orders.find_one({"_id": link["_id"]}))["status"] == "created"
    await PaymentService(db).captain_collect_cash(booking["id"], captain_id)
    after = await db.payment_orders.find_one({"_id": link["_id"]})
    assert after["status"] == "voided"
    assert link["razorpay_link_id"] in stub.payment_link.cancelled


async def test_pay06_switch_to_cash_voids_the_open_payment_link(db, monkeypatch):
    stub = h.install_rzp_stub(monkeypatch)
    booking, cu, cid, when = await _new_booking(db)
    assert booking["status"] == "awaiting_payment"
    link = await _open_link(db, booking["id"])
    await BookingService(db).switch_to_cash(booking["id"], cu["id"])
    after = await db.payment_orders.find_one({"_id": link["_id"]})
    assert after["status"] == "voided"
    assert link["razorpay_link_id"] in stub.payment_link.cancelled
