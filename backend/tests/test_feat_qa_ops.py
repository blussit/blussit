"""QA 2026-10-07 — ops fixes:

- /payments/collect/cash: `expected_amount` stays optional (an old cached
  captain app keeps working through a deploy) but a call without it logs a
  warning.
- WhatsApp delivery health: cached 15 s, and `?fresh=1` (the admin card's
  Refresh) bypasses the cache.
- Add-on applicability: on-site add-ons and booking edits accept only what
  the booking page offers that vehicle type — services listed for the type,
  never the "add a bike" line (Extra Bike Wash is the bike counter on a bike
  booking, not a pick on a car). What a car already carries may stay.
Local Mongo only."""
import logging
from datetime import datetime, timezone

import pytest
from bson import ObjectId

from app.core.exceptions import BadRequestException
from app.schemas.booking_schema import BookingCreateRequest, BookingEditRequest
from app.services.booking_service import BookingService
from app.services.payment_service import PaymentService
from tests import test_fix_coreb_helpers as hb
from tests.factories import get_hatchback_type_id
from tests.society_factories import auth, client, make_admin

pytestmark = pytest.mark.asyncio


async def _svc(db, name: str) -> str:
    doc = await db.services.find_one({"name": name, "is_deleted": {"$ne": True}})
    assert doc, f"seed() should have created {name}"
    return str(doc["_id"])


# ---------------------------------------------------------------- collect cash


async def _completed_cash_job(db, s: dict) -> str:
    b = await hb.new_booking(db, s["cu"], s["when"], s["keys"][0])
    await db.bookings.update_one({"_id": ObjectId(b["id"])}, {"$set": {
        "captain_id": s["cap"]["id"], "status": "completed", "payment_status": "pending", "payment_method": "cash",
    }})
    return b["id"]


async def test_collect_cash_without_expected_amount_warns_but_works(db, caplog):
    s = await hb.staffed_center(db)
    jid = await _completed_cash_job(db, s)
    with caplog.at_level(logging.WARNING, logger="app.services.payment_service"):
        await PaymentService(db).captain_collect_cash(jid, s["cap"]["id"])
    assert (await hb.booking(db, jid))["payment_status"] == "paid"
    warned = [r for r in caplog.records if r.levelno == logging.WARNING and "expected_amount" in r.getMessage()]
    assert warned and jid in warned[0].getMessage()


async def test_collect_cash_with_expected_amount_does_not_warn(db, caplog):
    s = await hb.staffed_center(db)
    jid = await _completed_cash_job(db, s)
    due = float((await hb.booking(db, jid))["total_amount"])
    with caplog.at_level(logging.WARNING, logger="app.services.payment_service"):
        await PaymentService(db).captain_collect_cash(jid, s["cap"]["id"], due)
    assert not [r for r in caplog.records if "expected_amount" in r.getMessage()]


# ------------------------------------------------------------- delivery health


async def test_delivery_health_fresh_bypasses_the_cache(db, cleanup):
    admin = await make_admin(db, cleanup)
    url = "/api/v1/whatsapp/crm/delivery-health"
    params = {"days": 89}
    async with client() as http:
        first = await http.get(url, headers=auth(admin, "admin"), params=params)
        assert first.status_code == 200, first.text
        before = first.json()["data"]["queue"].get("failed", 0)
        now = datetime.now(timezone.utc)
        row = await db.whatsapp_queue.insert_one({
            "_id": f"qa-health-{ObjectId()}",  # queue keys are strings (the de-dup key)
            "status": "failed", "failure": "rejected", "phone": "9999999999", "title": "QA", "attempts": 1,
            "created_at": now, "updated_at": now,
        })
        cleanup.append(("whatsapp_queue", {"_id": row.inserted_id}))
        cached = await http.get(url, headers=auth(admin, "admin"), params=params)
        assert cached.json()["data"]["queue"].get("failed", 0) == before  # still the cached answer
        fresh = await http.get(url, headers=auth(admin, "admin"), params={**params, "fresh": 1})
        assert fresh.status_code == 200, fresh.text
        assert fresh.json()["data"]["queue"].get("failed", 0) == before + 1


# ---------------------------------------------------------- add-on applicability


async def test_on_site_add_refuses_what_the_booking_page_never_offers_a_car(db):
    s = await hb.staffed_center(db)
    b = await hb.new_booking(db, s["cu"], s["when"], s["keys"][0])
    svc = BookingService(db)
    mgr = {"actor_id": s["mgr"]["id"], "actor_role": "manager", "actor_center_id": s["center_id"]}
    extra_bike, bike_polish = await _svc(db, "Extra Bike Wash"), await _svc(db, "Bike Polish")
    with pytest.raises(BadRequestException, match="isn't offered"):
        await svc.add_services_on_site(b["id"], [extra_bike], {}, **mgr)
    with pytest.raises(BadRequestException, match="isn't offered"):
        await svc.add_services_on_site(b["id"], [extra_bike, bike_polish], {}, **mgr)
    assert not (await hb.booking(db, b["id"])).get("added_services")
    # A real car add-on still goes on.
    out = await svc.add_services_on_site(b["id"], [await _svc(db, "Exterior Polish")], {}, **mgr)
    assert [a["name"] for a in out["added"]] == ["Exterior Polish"]


async def test_edit_refuses_a_new_unoffered_add_on_but_keeps_what_the_car_has(db):
    s = await hb.staffed_center(db)
    star, extra_bike, polish = await _svc(db, "Star Wash"), await _svc(db, "Extra Bike Wash"), await _svc(db, "Exterior Polish")
    svc = BookingService(db)
    plain = await hb.new_booking(db, s["cu"], s["when"], s["keys"][0])
    with pytest.raises(BadRequestException, match="isn't offered"):
        await svc.edit_booking(plain["id"], BookingEditRequest(service_ids=[star, extra_bike]), s["cu"]["id"], "customer")
    # A car booked with the add-a-bike combo (other channels may) keeps it
    # through an edit that adds a real car add-on.
    combo = await svc.create_booking(
        s["cu"]["id"],
        BookingCreateRequest(vehicle_type=await get_hatchback_type_id(db), address_id=s["cu"]["address_id"], service_ids=[star, extra_bike],
                             scheduled_date=s["when"], scheduled_slot=s["keys"][1], payment_method="cash"),
        source="app", _allow_pinless=True,
    )
    out = await svc.edit_booking(combo["id"], BookingEditRequest(service_ids=[star, extra_bike, polish]), s["cu"]["id"], "customer")
    assert set((await hb.booking(db, combo["id"]))["service_ids"]) == {star, extra_bike, polish}
    assert out
