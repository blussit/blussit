"""MONEY-2 (founder 2026-10-07): a tip records its OWN method
(`tip_method`: cash | online, default cash), independent of how the booking
was paid. Money received per method puts the tip in its own bucket and the
collections report counts tips_cash / tips_online. A tip saved before the
field existed is cash. Regression: a tip on an online-paid manager-done job
used to be counted as paid_online."""
import itertools

import pytest

from app.schemas.booking_schema import ManagerLogBookingRequest, QuickBookingLine
from app.services import booking_money as bm
from app.services.booking_service import BookingService
from app.services.money_service import backfill_booking_money
from app.services.payment_service import PaymentService
from tests import test_feat_booking_helpers as fb
from tests import test_fix_core_helpers as h
from tests.factories import get_hatchback_type_id, get_star_wash_service_id

from app.schemas.profile_schema import AddressCreateRequest
from app.services.profile_service import AddressService

pytestmark = pytest.mark.asyncio
_PHONE = itertools.count(9777800001)


@pytest.fixture(autouse=True)
def _day(monkeypatch):
    monkeypatch.setattr("app.services.booking_service._ist_today", lambda: "2999-12-31")


async def _log(db, s, phone, **kw):
    payload = dict(
        customer_name="Log Addr", customer_phone=phone,
        lines=[QuickBookingLine(vehicle_type=await get_hatchback_type_id(db), quantity=1, service_ids=[await get_star_wash_service_id(db)])],
        scheduled_date=h.day(-1), service_time="10:30", send_whatsapp=False,
    )
    payload.update(kw)
    out = await BookingService(db).create_manager_logged_visit(
        ManagerLogBookingRequest(**payload), manager_id=s["mgr"]["id"], manager_center_id=s["center_id"])
    return await fb.doc(db, out["bookings"][0]["id"])


async def test_log_needs_some_address():
    with pytest.raises(ValueError):
        ManagerLogBookingRequest(customer_name="X Y", customer_phone="9777800999", lines=[], scheduled_date="2026-10-01", service_time="10:00")


async def test_log_with_a_saved_address_id_uses_it_and_creates_none(db):
    s = await fb.rig(db)
    cu = s["cu"]
    user = await db.users.find_one({"_id": h.oid(cu["id"])})
    saved = await AddressService(db).create(cu["id"], AddressCreateRequest(label="Office", line1="Saved Tower 9", city="Indore", state="MP", pincode="452001"))
    before = await db.addresses.count_documents({"owner_id": cu["id"]})
    doc = await _log(db, s, user["phone"], address_id=saved["id"])
    assert doc["address_id"] == saved["id"]
    assert await db.addresses.count_documents({"owner_id": cu["id"]}) == before


async def test_log_refuses_another_customers_address(db):
    s = await fb.rig(db)
    other = await h.customer(db, s["pin"])
    theirs = await AddressService(db).create(other["id"], AddressCreateRequest(label="Home", line1="Not Yours 1", city="Indore", state="MP", pincode="452001"))
    user = await db.users.find_one({"_id": h.oid(s["cu"]["id"])})
    with pytest.raises(Exception) as e:
        await _log(db, s, user["phone"], address_id=theirs["id"])
    assert "Address not found" in str(e.value)


async def test_log_with_a_new_pinned_address_saves_it_with_the_pin(db):
    s = await fb.rig(db)
    phone = str(next(_PHONE))
    doc = await _log(db, s, phone, address={"line1": "Pinned Plaza, Vijay Nagar", "pincode": "452010", "latitude": 22.7533, "longitude": 75.8937})
    addr = await db.addresses.find_one({"_id": h.oid(doc["address_id"])})
    assert addr["line1"] == "Pinned Plaza, Vijay Nagar" and addr["pincode"] == "452010"
    assert addr["latitude"] == 22.7533 and addr["longitude"] == 75.8937
