"""A customer adds paid add-ons to their own booking — a plan wash included
(2026-11-07: the Edit sheet said "only the date, time, address and notes can
change" on a plan booking, so extras could never be added on top of a plan).
Local Mongo only."""
import pytest
from bson import ObjectId

from app.schemas.booking_schema import QuickBookingLine, QuickBookingRequest
from app.services.booking_service import BookingService
from tests import test_feat_booking_helpers as fb
from tests import test_feat_planfix_helpers as pf
from tests import test_fix_core_helpers as h

pytestmark = pytest.mark.asyncio


async def _svc(db, slug) -> dict:
    return await db.services.find_one({"slug": slug})


async def _plan_booking(db):
    """A customer with a Hatchback Star Wash pass and one booking made with it."""
    s = await fb.rig(db)
    cu = s["cu"]
    star = str((await _svc(db, "star-wash"))["_id"])
    hatch = await fb.get_hatchback_type_id(db)
    sub = await pf.std_pass(db, cu["id"], star, vehicle_type=hatch, washes=4)
    customer = await db.users.find_one({"_id": ObjectId(cu["id"])})
    out = await BookingService(db).create_quick_booking(
        QuickBookingRequest(
            customer_name="Plan Person", customer_phone=customer["phone"], address_id=cu["address_id"],
            lines=[QuickBookingLine(vehicle_type=hatch, service_ids=[star])],
            scheduled_date=s["when"], scheduled_slot=s["keys"][0], payment_method="cash",
        ),
        customer=customer, source="staff", allow_pinless=True,
    )
    return s, sub, out["bookings"][0]["id"]


async def test_customer_adds_an_addon_on_top_of_a_plan_wash(db):
    s, sub, bid = await _plan_booking(db)
    polish = await _svc(db, "exterior-polish")
    before = await fb.doc(db, bid)
    assert before["total_amount"] == 0 and before["subscription_id"] == sub
    async with h.client() as c:
        r = await c.post(f"/api/v1/bookings/{bid}/extras", json={"service_ids": [str(polish["_id"])]}, headers=s["cu"]["h"])
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert data["added_total"] == 200 and data["amount_due"] == 200
    after = await fb.doc(db, bid)
    assert after["total_amount"] == 200 and after["subscription_id"] == sub and after["payment_status"] in ("pending", "partially_paid")
    assert after["added_services"][0]["role"] == "customer" and after["captain_earning"] == before["captain_earning"]
    split = data["booking"]["plan_split"]
    assert split["extras_on_site_total"] == 200 and split["covered_amount"] == 349
    # The plan wash is untouched; the manager is told; the customer isn't sent a notice about their own action.
    assert await fb.remaining(db, sub) == 3
    assert await fb.bell(db, s["mgr"]["id"], f"Services added — {after['booking_number']}")
    assert not await db.notifications.find_one({"user_id": s["cu"]["id"], "title": "Add-On Added To Your Booking"})


async def test_customer_cannot_mark_the_extra_paid_or_add_a_main_service(db):
    s, sub, bid = await _plan_booking(db)
    polish = await _svc(db, "exterior-polish")
    deep = await _svc(db, "deep-cleaning")
    async with h.client() as c:
        r = await c.post(f"/api/v1/bookings/{bid}/extras", json={"service_ids": [str(polish["_id"])], "payment": "cash"}, headers=s["cu"]["h"])
        assert r.status_code == 200
        assert (await fb.doc(db, bid)).get("paid_cash", 0) == 0  # `payment` is a staff-only field
        r = await c.post(f"/api/v1/bookings/{bid}/extras", json={"service_ids": [str(deep["_id"])]}, headers=s["cu"]["h"])
        assert r.status_code == 400 and "Only add-ons" in r.json()["message"]


async def test_extras_authz_and_lock(db):
    s, sub, bid = await _plan_booking(db)
    polish = await _svc(db, "exterior-polish")
    stranger = await h.customer(db, "452099")
    async with h.client() as c:
        r = await c.post(f"/api/v1/bookings/{bid}/extras", json={"service_ids": [str(polish["_id"])]}, headers=stranger["h"])
        assert r.status_code == 404  # not their booking
        r = await c.post(f"/api/v1/bookings/{bid}/extras", json={"service_ids": [str(polish["_id"])]}, headers=s["cap"]["h"])
        assert r.status_code == 403  # customers only
    # Once the captain is on the way the booking is locked, like an edit.
    await fb.assign(db, bid, s)
    await fb.hb.on_the_way(db, bid, s["cap"]["id"], vehicle_verified=True)
    async with h.client() as c:
        r = await c.post(f"/api/v1/bookings/{bid}/extras", json={"service_ids": [str(polish["_id"])]}, headers=s["cu"]["h"])
    assert r.status_code == 400 and "on the way" in r.json()["message"]


async def test_a_plan_visit_with_paid_extras_is_online_only_but_a_normal_one_is_not(db):
    s, sub, bid = await _plan_booking(db)
    svc = BookingService(db)
    assert not await svc.visit_is_online_only(await db.bookings.find_one({"_id": ObjectId(bid)}))  # the plan wash alone is free
    polish = await _svc(db, "exterior-polish")
    async with h.client() as c:
        assert (await c.post(f"/api/v1/bookings/{bid}/extras", json={"service_ids": [str(polish["_id"])]}, headers=s["cu"]["h"])).status_code == 200
    assert await svc.visit_is_online_only(await db.bookings.find_one({"_id": ObjectId(bid)}))
    s2 = await fb.rig(db)
    plain = await fb.book(db, s2["cu"], s2["when"], s2["keys"][0])
    assert not await svc.visit_is_online_only(await db.bookings.find_one({"_id": ObjectId(plain["id"])}))
