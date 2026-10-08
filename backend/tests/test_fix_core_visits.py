"""Core fix round 1 — visit-level follow-ups (audit 2026-10-07: NTF-07 for
the NOTIFY owner, PAY-06 on the web group path, STATE-02)."""
import re

import pytest
from bson import ObjectId

from app.schemas.booking_schema import BookingGroupCreateRequest, GroupVehicleRequest
from app.schemas.subscription_schema import SubscribeRequest
from app.services.booking_service import BookingService
from app.services.notification_service import NotificationService
from app.services.payment_service import PaymentService
from app.services.subscription_service import UserSubscriptionService
from tests import test_fix_core_helpers as h
from tests.factories import get_hatchback_type_id, get_star_wash_service_id, make_subscription_plan, make_vehicle

pytestmark = pytest.mark.asyncio
ISO_DAY = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")


@pytest.fixture
def sent(monkeypatch):
    calls: list[dict] = []
    real = NotificationService.notify

    async def spy(self, user_id, title, message, *args, **kwargs):
        calls.append({"user_id": user_id, "title": title, "message": message, **kwargs})
        return await real(self, user_id, title, message, *args, **kwargs)

    monkeypatch.setattr(NotificationService, "notify", spy)
    return calls


# ---------------------------------------------------------------- NTF-07


async def test_ntf07_confirmation_reads_like_a_person_wrote_it(db, sent):
    cid, pin = await h.center(db)
    when, keys = await h.slot_keys(db, cid)
    cu = await h.customer(db, pin)
    async with h.client() as c:
        r = await h.book(c, db, cu, when, keys[0])
        assert r.status_code == 200, r.text
    mine = [x for x in sent if x["user_id"] == cu["id"] and x.get("wa_event") == "booking_confirmed"]
    assert len(mine) == 1
    note = mine[0]
    text = f"{note['title']} {note['message']}"
    star = (await db.services.find_one({"slug": "star-wash"}))["name"]
    assert note["title"] == "Booking confirmed"
    assert not ISO_DAY.search(text), text
    assert text.count(star) == 1, text
    assert "Hatchback" in note["message"]
    assert note["wa_params"][2] in note["message"]  # the human date ("09 Oct 2026")
    row = await db.notifications.find_one({"user_id": cu["id"], "title": "Booking confirmed"})
    assert row and not ISO_DAY.search(row["message"])


# ---------------------------------------------------------------- PAY-06 (group)


async def test_pay06_switching_a_visit_to_cash_voids_its_payment_link(db, monkeypatch):
    stub = h.install_rzp_stub(monkeypatch)
    cid, pin = await h.center(db)
    when, keys = await h.slot_keys(db, cid)
    cu = await h.customer(db, pin)
    async with h.client() as c:
        gid, ids = await h.group(c, db, cu, when, keys[0], cars=2, payment_method="online")
    cars = [await db.bookings.find_one({"_id": ObjectId(i)}) for i in ids]
    await PaymentService(db).create_payment_link(cars[0], contact_phone="9876500001", name="Test", cars=cars)
    link = await db.payment_orders.find_one({"kind": "link", "status": "created", "$or": [{"booking_id": {"$in": ids}}, {"booking_ids": {"$in": ids}}]})
    assert link
    result = await BookingService(db).switch_group_to_cash(gid, cu["id"])
    assert result["switched_count"] == 2
    assert (await db.payment_orders.find_one({"_id": link["_id"]}))["status"] == "voided"
    assert link["razorpay_link_id"] in stub.payment_link.cancelled


# ---------------------------------------------------------------- STATE-02


async def _visit_with_pass_car(db, method: str):
    cid, pin = await h.center(db)
    when, keys = await h.slot_keys(db, cid)
    cu = await h.customer(db, pin)
    hatch = await get_hatchback_type_id(db)
    star = await get_star_wash_service_id(db)
    v2 = await make_vehicle(db, cu["id"], hatch, is_default=False)
    plan_id = await make_subscription_plan(db, vehicle_types=[], included_service_ids=[star], total_service_count=4)
    sub = await UserSubscriptionService(db).subscribe(cu["id"], SubscribeRequest(plan_id=plan_id, vehicle_id=cu["vehicle_id"], service_id=star))
    visit = await BookingService(db).create_booking_group(cu["id"], BookingGroupCreateRequest(
        vehicles=[GroupVehicleRequest(vehicle_id=cu["vehicle_id"], service_ids=[star], subscription_id=sub["id"]),
                  GroupVehicleRequest(vehicle_id=v2, service_ids=[star])],
        address_id=cu["address_id"], scheduled_date=when, scheduled_slot=keys[0], payment_method=method,
    ))
    cars = sorted(await db.bookings.find({"booking_group_id": visit["booking_group_id"]}).to_list(5), key=lambda c: c["group_offset_minutes"])
    return cars


async def test_state02_pass_car_of_an_online_visit_is_never_briefly_confirmed(db):
    pass_car, paid_car = await _visit_with_pass_car(db, "online")
    assert pass_car["total_amount"] == 0 and paid_car["total_amount"] > 0
    assert pass_car["status"] == paid_car["status"] == "awaiting_payment"
    history = await db.booking_status_history.find({"booking_id": str(pass_car["_id"])}).to_list(None)
    assert [row["status"] for row in history] == ["awaiting_payment"], history


async def test_state02_pass_car_of_a_cash_visit_is_confirmed(db):
    pass_car, paid_car = await _visit_with_pass_car(db, "cash")
    assert pass_car["status"] == paid_car["status"] == "pending"
    assert pass_car.get("awaiting_assignment_since") is not None
