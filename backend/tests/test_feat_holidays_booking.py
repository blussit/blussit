"""Holiday system (2026-10-19) — enforcement. One check refuses every
booking path inside a holiday with 400 HOLIDAY (create, quick, group,
staff, hold, reschedule, edit, WhatsApp bot); available_slots marks the
slots; a manager's log of a job already done is never blocked; ending a
holiday early reopens booking at once (cache dropped)."""
from datetime import datetime, timedelta, timezone

import pytest

from app.schemas.booking_schema import BookingEditRequest, BookingRescheduleRequest, QuickAddress, QuickBookingLine, QuickBookingRequest
from app.services.booking_service import BookingService
from app.services.holiday_service import HolidayException, HolidayService
from tests import test_feat_booking_helpers as fb
from tests import test_feat_holidays_helpers as hh
from tests import test_fix_core_helpers as h
from tests.factories import get_hatchback_type_id, get_star_wash_service_id
from tests.test_feat_holidays_helpers import clean  # noqa: F401 — fixture

pytestmark = pytest.mark.asyncio

SLOTS = ["09:00-12:00", "12:00-15:00", "15:00-18:00", "18:00-19:00"]


async def _rig(db, clean) -> dict:
    cid, pin, lat, lng = await hh.center(db, clean)
    return {"center_id": cid, "pin": pin, "lat": lat, "lng": lng, "cu": await h.customer(db, pin), "mgr": await h.manager(db, cid),
            "admin": await hh.admin_headers(db)}


def _is_holiday_refusal(r) -> bool:
    return r.status_code == 400 and r.json().get("error_code") == "HOLIDAY"


async def test_full_day_holiday_blocks_every_slot_and_the_next_day_is_open(db, clean):
    s = await _rig(db, clean)
    async with h.client() as c:
        await hh.make(c, s["admin"], start_date=hh.day(1), end_date=hh.day(2), message="Family function 🎉")
    svc = BookingService(db)
    for offset in (1, 2):
        slots = await svc.available_slots(s["center_id"], hh.day(offset))
        assert [x["key"] for x in slots] == SLOTS
        for x in slots:
            assert x["status"] == "full" and x["remaining"] == 0 and x["holiday"] is True
            assert x["holiday_message"] == "Family function 🎉" and x["resume_label"] == hh.long(hh.day(3))
    open_day = await svc.available_slots(s["center_id"], hh.day(3))
    assert all(x["status"] == "available" and "holiday" not in x for x in open_day)
    # Over HTTP the same shape (the public slot endpoint).
    async with h.client() as c:
        r = await c.get(f"/api/v1/service-centers/{s['center_id']}/slots", params={"date": hh.day(1)})
        assert r.json()["data"][0]["holiday"] is True
        # Booking the first morning after the holiday works; the last slot of the holiday doesn't.
        assert _is_holiday_refusal(await h.book(c, db, s["cu"], hh.day(2), SLOTS[-1]))
        r = await h.book(c, db, s["cu"], hh.day(3), SLOTS[0])
        assert r.status_code == 200, r.text


async def test_partial_afternoon_holiday_blocks_only_overlapping_slots(db, clean):
    s = await _rig(db, clean)
    async with h.client() as c:
        made = await hh.make(c, s["admin"], start_date=hh.day(2), start_time="13:00", end_date=hh.day(2), end_time="16:00")
    assert made["holiday"]["dates_label"].endswith("1:00 PM – 4:00 PM")
    slots = {x["key"]: x for x in await BookingService(db).available_slots(s["center_id"], hh.day(2))}
    assert [k for k, x in slots.items() if x.get("holiday")] == ["12:00-15:00", "15:00-18:00"]
    assert slots["09:00-12:00"]["status"] == "available" and slots["18:00-19:00"]["status"] == "available"
    async with h.client() as c:
        r = await h.book(c, db, s["cu"], hh.day(2), "15:00-18:00")
        assert _is_holiday_refusal(r)
        assert r.json()["message"].startswith("🌴 We're taking a little holiday break on") and "You can book from" in r.json()["message"]
        assert r.json()["message"].endswith(f"{hh.long(hh.day(2))}, 4:00 PM — see you then! 😊")
        assert (await h.book(c, db, s["cu"], hh.day(2), "18:00-19:00")).status_code == 200


async def test_a_centers_holiday_leaves_other_centers_open(db, clean):
    s = await _rig(db, clean)
    t = await _rig(db, clean)
    async with h.client() as c:
        await hh.make(c, s["admin"], scope="centers", service_center_ids=[s["center_id"]])
        assert _is_holiday_refusal(await h.book(c, db, s["cu"], hh.day(1), SLOTS[0]))
        assert (await h.book(c, db, t["cu"], hh.day(1), SLOTS[0])).status_code == 200
    assert all(not x.get("holiday") for x in await BookingService(db).available_slots(t["center_id"], hh.day(1)))
    # An all-centers holiday closes both.
    async with h.client() as c:
        await hh.make(c, s["admin"], start_date=hh.day(4), end_date=hh.day(4))
        assert _is_holiday_refusal(await h.book(c, db, t["cu"], hh.day(4), SLOTS[1]))


async def test_every_booking_path_is_refused_with_holiday(db, clean, monkeypatch):
    s = await _rig(db, clean)
    hatch, star = await get_hatchback_type_id(db), await get_star_wash_service_id(db)
    # Bookings that exist BEFORE the holiday is declared (on day 3).
    b_res = await fb.book(db, s["cu"], hh.day(3), SLOTS[0])
    b_edit = await fb.book(db, s["cu"], hh.day(3), SLOTS[2])
    async with h.client() as c:
        await hh.make(c, s["admin"], start_date=hh.day(1), end_date=hh.day(2), scope="centers", service_center_ids=[s["center_id"]])
        # Customer app: one car, several cars.
        assert _is_holiday_refusal(await h.book(c, db, s["cu"], hh.day(1), SLOTS[0]))
        r = await c.post("/api/v1/bookings/group", json={
            "vehicles": [{"vehicle_type": hatch, "quantity": 2, "service_ids": [star]}], "address_id": s["cu"]["address_id"],
            "scheduled_date": hh.day(2), "scheduled_slot": SLOTS[1], "payment_method": "cash"}, headers=s["cu"]["h"])
        assert _is_holiday_refusal(r), r.text
        # Slot hold (public).
        r = await c.post("/api/v1/bookings/hold", json={"holder_key": "holiday-holder-01", "service_center_id": s["center_id"],
                                                         "date": hh.day(1), "slot_key": SLOTS[0]})
        assert _is_holiday_refusal(r), r.text
        # Anonymous website booking: refused before the OTP is even checked.
        quick = {"customer_name": "Guest Gita", "customer_phone": "9555500101",
                 "address": {"line1": "Holiday Lane 1", "pincode": s["pin"], "latitude": s["lat"], "longitude": s["lng"]},
                 "lines": [{"vehicle_type": hatch, "quantity": 1, "service_ids": [star]}],
                 "scheduled_date": hh.day(1), "scheduled_slot": SLOTS[0], "phone_otp": "000000"}
        r = await c.post("/api/v1/bookings/quick", json=quick)
        assert _is_holiday_refusal(r), r.text
        # Staff booking on a customer's behalf.
        r = await c.post("/api/v1/bookings/manager-quick", json={**quick, "phone_otp": None, "customer_phone": "9555500102"}, headers=s["mgr"]["h"])
        assert _is_holiday_refusal(r), r.text
        # Reschedule and customer edit INTO the holiday.
        r = await c.post(f"/api/v1/bookings/{b_res['id']}/reschedule", json={"scheduled_date": hh.day(1), "scheduled_slot": SLOTS[0]}, headers=s["cu"]["h"])
        assert _is_holiday_refusal(r), r.text
        r = await c.patch(f"/api/v1/bookings/{b_edit['id']}", json={"scheduled_date": hh.day(2), "scheduled_slot": SLOTS[2]}, headers=s["cu"]["h"])
        assert _is_holiday_refusal(r), r.text
        r = await c.patch(f"/api/v1/bookings/{b_edit['id']}", json={"scheduled_date": hh.day(2), "scheduled_slot": SLOTS[2], "dry_run": True}, headers=s["cu"]["h"])
        assert _is_holiday_refusal(r), r.text
    # Nothing moved, nothing left behind.
    assert (await fb.doc(db, b_res["id"]))["scheduled_slot"] == SLOTS[0]
    assert (await fb.doc(db, b_edit["id"]))["status"] == "pending"
    assert await db.slot_holds.count_documents({"service_center_id": s["center_id"]}) == 0
    assert await db.bookings.count_documents({"service_center_id": s["center_id"], "slot_start": {"$lt": datetime.now(timezone.utc) + timedelta(days=3)},
                                             "status": {"$ne": "cancelled"}, "_id": {"$nin": [fb.h.oid(b_res["id"]), fb.h.oid(b_edit["id"])]}}) == 0
    # Service calls (staff + WhatsApp sources) raise the same exception type.
    svc = BookingService(db)
    customer = await db.users.find_one({"_id": fb.h.oid(s["cu"]["id"])})
    payload = QuickBookingRequest(
        customer_name="Bot Bina", customer_phone=customer["phone"], address_id=s["cu"]["address_id"],
        lines=[QuickBookingLine(vehicle_type=hatch, quantity=1, service_ids=[star])], scheduled_date=hh.day(1), scheduled_slot=SLOTS[1],
    )
    for source in ("whatsapp", "staff"):
        with pytest.raises(HolidayException) as err:
            await svc.create_quick_booking(payload, customer=customer, source=source, allow_pinless=True, notify_background=False)
        assert err.value.error_code == "HOLIDAY" and err.value.details["resume_date"] == hh.day(3)
    with pytest.raises(HolidayException):
        await svc.reschedule_booking(b_res["id"], BookingRescheduleRequest(scheduled_date=hh.day(2), scheduled_slot=SLOTS[0]), s["mgr"]["id"], "manager", s["center_id"])
    with pytest.raises(HolidayException):
        await svc.edit_booking(b_edit["id"], BookingEditRequest(scheduled_date=hh.day(1), scheduled_slot=SLOTS[0]), s["cu"]["id"], "customer")
    # A non-date edit of a booking is untouched by the holiday.
    out = await svc.edit_booking(b_edit["id"], BookingEditRequest(customer_notes="Gate 2"), s["cu"]["id"], "customer")
    assert out


async def test_whatsapp_bot_says_holiday_and_never_books(db, clean):
    from app.services.whatsapp_bot_service import WhatsAppBotService

    s = await _rig(db, clean)
    customer = await db.users.find_one({"_id": fb.h.oid(s["cu"]["id"])})
    phone, wa_id = customer["phone"], f"91{customer['phone']}"
    hatch, star = await get_hatchback_type_id(db), await get_star_wash_service_id(db)
    line = {"vehicle_type": hatch, "type_name": "Hatchback", "count": 1, "quantity": 1, "service_ids": [star],
            "service_quantities": {}, "service_name": "Star Wash", "subtotal": 199.0}
    data = {"lines": [line], "address_id": s["cu"]["address_id"], "address_label": "Home", "center_id": s["center_id"],
            "date": hh.day(1), "slot": SLOTS[0]}
    async with h.client() as c:
        await hh.make(c, s["admin"], start_date=hh.day(0), end_date=hh.day(3), scope="centers", service_center_ids=[s["center_id"]])
    bot = WhatsAppBotService(db)
    await bot._hold_and_confirm(wa_id, phone, s["cu"]["id"], dict(data))
    sent = await db.whatsapp_outbox.find({"phone": phone}).sort("_id", 1).to_list(None)
    texts = [m.get("message") or "" for m in sent]
    assert any("holiday break" in t and hh.long(hh.day(4)) in t for t in texts), texts
    # The chosen day is a holiday: the bot says so warmly (with the reopening
    # day) instead of "booked", then offers the next open days (after day 3).
    assert any(t.startswith("🌴 We're taking a little holiday break") for t in texts), texts
    offered = [r["id"] for m in sent for r in (m.get("options") or [])]
    assert offered and all(i.split(":", 1)[1].split("|")[0] >= hh.day(4) for i in offered if i.startswith("when:")), offered
    assert not any("Confirm your booking" in t for t in texts)
    # A stale Confirm tap (the bot's create path) is refused too.
    await bot._on_confirm(wa_id, phone, s["cu"]["id"], dict(data), "reply", "confirm:yes")
    assert await db.bookings.count_documents({"customer_id": s["cu"]["id"]}) == 0
    last = await db.whatsapp_outbox.find({"phone": phone}).sort("_id", -1).to_list(None)
    assert any("🌴" in (m.get("message") or "") and "holiday break" in (m.get("message") or "") for m in last)
    await db.whatsapp_outbox.delete_many({"phone": phone})
    await db.whatsapp_conversations.delete_many({"wa_id": wa_id})


async def test_manager_log_of_a_done_job_is_never_blocked(db, clean):
    s = await _rig(db, clean)
    now = datetime.now(timezone.utc)
    await hh.insert_raw(db, start=now - timedelta(days=2), end=now + timedelta(days=2), scope="centers", centers=[s["center_id"]])
    hatch, star = await get_hatchback_type_id(db), await get_star_wash_service_id(db)
    body = {
        "customer_name": "Logged Lata", "customer_phone": "9555500111",
        "lines": [{"vehicle_type": hatch, "quantity": 1, "service_ids": [star]}],
        "scheduled_date": hh.day(-1), "service_time": "10:30",
        "address_line": "Logged Lane 4", "payment_method": "cash", "send_whatsapp": False,
    }
    async with h.client() as c:
        r = await c.post("/api/v1/bookings/manager-log-completed", json=body, headers=s["mgr"]["h"])
        assert r.status_code == 200, r.text
        assert r.json()["data"]["bookings"][0]["status"] == "completed"
        # …while a new booking in the same holiday is refused.
        assert _is_holiday_refusal(await h.book(c, db, s["cu"], hh.day(1), SLOTS[0]))


async def test_end_now_reopens_booking_at_once(db, clean):
    s = await _rig(db, clean)
    now = datetime.now(timezone.utc)
    running = await hh.insert_raw(db, start=now - timedelta(hours=1), end=now + timedelta(days=3), scope="centers", centers=[s["center_id"]])
    svc = BookingService(db)
    assert all(x.get("holiday") for x in await svc.available_slots(s["center_id"], hh.day(1)))  # cache warmed
    async with h.client() as c:
        upcoming = (await hh.make(c, s["admin"], start_date=hh.day(5), end_date=hh.day(5)))["holiday"]["id"]
        assert _is_holiday_refusal(await h.book(c, db, s["cu"], hh.day(1), SLOTS[0]))
        assert _is_holiday_refusal(await h.book(c, db, s["cu"], hh.day(5), SLOTS[0]))
        assert (await c.post(f"/api/v1/admin/holidays/{running}/end", headers=s["admin"])).status_code == 200
        assert not any(x.get("holiday") for x in await svc.available_slots(s["center_id"], hh.day(1)))
        assert (await h.book(c, db, s["cu"], hh.day(1), SLOTS[0])).status_code == 200
        # Cancelling the upcoming one reopens its day too.
        assert (await c.post(f"/api/v1/admin/holidays/{upcoming}/end", headers=s["admin"])).status_code == 200
        r = await h.book(c, db, s["cu"], hh.day(5), SLOTS[0])
        assert r.status_code == 200, r.text
    assert await HolidayService(db).public_list(s["center_id"]) == []


async def test_existing_bookings_are_kept_and_restore_is_refused_inside_a_holiday(db, clean):
    # RT-01 (2026-10-28): the restore used to skip the holiday check; it now
    # refuses a date that became a holiday after the delete (see
    # test_redteam2_holiday_restore.py). Existing live bookings stay untouched.
    s = await _rig(db, clean)
    b = await fb.book(db, s["cu"], hh.day(1), SLOTS[0])
    async with h.client() as c:
        assert (await c.post(f"/api/v1/bookings/{b['id']}/delete", headers=s["admin"])).status_code == 200
        assert (await fb.doc(db, b["id"]))["is_deleted"] is True
        made = await hh.make(c, s["admin"], scope="centers", service_center_ids=[s["center_id"]])
        assert made["affected_bookings"]["count"] == 0  # in the recycle bin
        r = await c.post(f"/api/v1/bookings/{b['id']}/restore", headers=s["admin"])
        assert r.status_code == 400 and "holiday" in r.json()["message"].lower(), r.text
    assert (await fb.doc(db, b["id"]))["is_deleted"] is True
