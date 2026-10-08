"""Follow-up 2026-10-07 — a booking's address_snapshot is the source of
truth (spec 1.3, "address side door"). Editing the saved address afterwards
must not move what staff, the WhatsApp bot, the customer 360 view or the
area KPI show for that booking: every reader takes the snapshot first and
falls back to the saved address only for a booking without one."""
from datetime import datetime, timedelta, timezone

import pytest
from bson import ObjectId

from app.services.crm_service import CRMService
from app.services.kpi_service import KpiService
from app.services.staff_directory_service import StaffDirectoryService
from app.services.whatsapp_bot_service import WhatsAppBotService
from tests import test_feat_booking_helpers as fb

pytestmark = pytest.mark.asyncio

# Where the customer was when they booked, and where the saved address is
# moved to afterwards (≈ 40 km apart, so a distance read from the wrong one
# is unmistakable).
BOOKED_AT = {"latitude": 22.95, "longitude": 75.85}
MOVED_TO = {"latitude": 23.3, "longitude": 75.85}


async def _booked_then_moved(db) -> tuple[dict, dict, dict]:
    """A booking whose snapshot carries a map pin, then the customer edits
    the saved address to another street, pincode and pin (the profile edit
    writes the address doc only — the booking keeps its snapshot)."""
    s = await fb.rig(db)
    b = await fb.book(db, s["cu"], s["when"], s["keys"][0])
    booking = await fb.doc(db, b["id"])
    snap = booking["address_snapshot"]
    assert snap and snap["address_id"] == s["cu"]["address_id"]
    # The pin the customer booked with (the test address has none, and a pin
    # set before booking would route the booking by distance instead of the
    # helper center's pincode).
    await db.bookings.update_one({"_id": booking["_id"]}, {"$set": {
        "address_snapshot.latitude": BOOKED_AT["latitude"], "address_snapshot.longitude": BOOKED_AT["longitude"],
    }})
    moved_pin = f"49{int(s['pin'][-4:]):04d}"
    await db.addresses.update_one({"_id": ObjectId(s["cu"]["address_id"])}, {"$set": {
        "line1": "Moved Road 99", "city": "Dewas", "pincode": moved_pin, **MOVED_TO,
    }})
    return s, await fb.doc(db, b["id"]), {"line1": snap["line1"], "pincode": snap["pincode"], "moved_pin": moved_pin}


async def test_eligible_captains_measure_from_the_booking_snapshot(db):
    s, booking, _ = await _booked_then_moved(db)
    await db.users.update_one({"_id": ObjectId(s["cap"]["id"])}, {"$set": {"last_known_location": dict(BOOKED_AT)}})
    rows = await StaffDirectoryService(db).eligible_captains_for_booking(str(booking["_id"]), db, "manager", s["center_id"])
    mine = next(r for r in rows if r["captain_id"] == s["cap"]["id"])
    # Standing on the booked pin: 0 km — not the ~39 km to the moved address.
    assert mine["distance_km"] == 0.0


async def test_whatsapp_booking_detail_shows_the_booking_snapshot(db, cleanup):
    s, booking, snap = await _booked_then_moved(db)
    customer = await db.users.find_one({"_id": ObjectId(s["cu"]["id"])})
    phone = customer["phone"]
    cleanup.append(("whatsapp_outbox", {"phone": phone}))
    cleanup.append(("whatsapp_conversations", {"wa_id": f"91{phone}"}))
    await WhatsAppBotService(db)._show_booking_detail(f"91{phone}", phone, s["cu"]["id"], str(booking["_id"]))
    out = await db.whatsapp_outbox.find_one({"phone": phone}, sort=[("_id", -1)])
    assert out and booking["booking_number"] in out["message"]
    assert snap["line1"] in out["message"] and "Moved Road" not in out["message"]


async def test_customer_360_shows_the_booking_snapshot(db):
    s, booking, snap = await _booked_then_moved(db)
    view = await CRMService(db).get_customer_360(s["cu"]["id"], "admin", None)
    row = next(b for b in view["bookings"] if b["id"] == str(booking["_id"]))
    assert row["address_text"] == f"{snap['line1']}, Indore"
    # The saved address list itself shows the edit — that is the profile.
    assert any(a["line1"] == "Moved Road 99" for a in view["addresses"])


async def test_area_kpi_groups_by_the_booking_snapshot(db):
    s, booking, snap = await _booked_then_moved(db)
    now = datetime.now(timezone.utc)
    out = await KpiService(db).areas(now - timedelta(days=1), now + timedelta(days=1), now - timedelta(days=3), now - timedelta(days=1))
    labels = {r["area"] for r in out["areas"]}
    assert f"Indore · {snap['pincode']}" in labels
    assert f"Dewas · {snap['moved_pin']}" not in labels


async def test_a_booking_without_a_snapshot_still_falls_back_to_the_saved_address(db):
    s, booking, _ = await _booked_then_moved(db)
    await db.bookings.update_one({"_id": booking["_id"]}, {"$unset": {"address_snapshot": ""}})
    view = await CRMService(db).get_customer_360(s["cu"]["id"], "admin", None)
    row = next(b for b in view["bookings"] if b["id"] == str(booking["_id"]))
    assert row["address_text"] == "Moved Road 99, Dewas"
