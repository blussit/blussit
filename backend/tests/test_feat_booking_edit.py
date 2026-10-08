"""Feature build 2026-10-07 — the customer edits a booking (spec 1.3):
PATCH /bookings/{id} and /bookings/group/{gid}. Locks, same-center
address, re-pricing through the create code, seats moved atomically,
money through MoneyService.on_price_change, manager flag (in-app only),
customer WhatsApp, history diff + audit, and the races."""
import asyncio
from datetime import timedelta

import pytest
from bson import ObjectId

from app.core.exceptions import BadRequestException
from app.schemas.booking_schema import BookingEditRequest, BookingRescheduleRequest
from app.services.booking_service import BookingService, PriceChangedException
from app.services.profile_service import AddressService
from app.schemas.profile_schema import AddressUpdateRequest
from app.utils.timezone import now_ist
from tests import test_feat_booking_helpers as fb
from tests import test_fix_core_helpers as h

pytestmark = pytest.mark.asyncio


async def _svc_id(db, slug: str) -> str:
    return str((await db.services.find_one({"slug": slug}))["_id"])


def edit(**kw) -> BookingEditRequest:
    return BookingEditRequest(**kw)


async def test_date_slot_edit_moves_the_seat_and_tells_everyone(db):
    s = await fb.rig(db)
    cu = s["cu"]["id"]
    b = await fb.book(db, s["cu"], s["when"], s["keys"][0])
    new_when, new_keys = await h.slot_keys(db, s["center_id"], offset=3)
    async with h.client() as c:
        r = await c.patch(f"/api/v1/bookings/{b['id']}", json={"scheduled_date": new_when, "scheduled_slot": new_keys[1]}, headers=s["cu"]["h"])
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert [x["field"] for x in data["changes"]] == ["date", "slot"]
    d = await fb.doc(db, b["id"])
    assert d["scheduled_slot"] == new_keys[1] and d["status"] == "rescheduled" and d["customer_edited_at"]
    assert set(d["customer_edited_fields"]) == {"date", "slot"}
    assert (await h.state(db, s["center_id"], s["when"], s["keys"][0]))["booked"] == 0
    new_state = await h.state(db, s["center_id"], new_when, new_keys[1])
    assert new_state["booked"] == new_state["owners"] == 1
    hist = await db.booking_status_history.find_one({"booking_id": b["id"], "changes": {"$exists": True}})
    assert hist and {c["field"] for c in hist["changes"]} == {"date", "slot"}
    assert await db.audit_logs.find_one({"action": "CUSTOMER_EDIT_BOOKING", "target_id": b["id"]})
    flag = await fb.bell(db, s["mgr"]["id"], "Customer edited a booking")
    assert flag and d["booking_number"] in flag[0]["message"] and "Date" in flag[0]["message"]
    assert not await db.whatsapp_queue.find_one({"user_id": s["mgr"]["id"], "title": "Customer edited a booking"})
    [wa] = await fb.queued(db, cu, "booking_edited")
    assert len(wa["wa_params"]) == 6 and float(wa["wa_params"][5]) == d["total_amount"] and wa["reference_id"] == b["id"]


async def test_date_change_on_an_assigned_booking_releases_the_captain(db):
    s = await fb.rig(db)
    b = await fb.book(db, s["cu"], s["when"], s["keys"][0])
    await fb.assign(db, b["id"], s)
    new_when, new_keys = await h.slot_keys(db, s["center_id"], offset=3)
    await BookingService(db).edit_booking(b["id"], edit(scheduled_date=new_when, scheduled_slot=new_keys[0]), s["cu"]["id"], "customer")
    d = await fb.doc(db, b["id"])
    assert d["captain_id"] is None and d["status"] == "rescheduled"
    assert await fb.bell(db, s["cap"]["id"], "Booking rescheduled — no longer yours")


async def test_service_change_keeps_the_captain_and_reprices_like_create(db):
    s = await fb.rig(db)
    b = await fb.book(db, s["cu"], s["when"], s["keys"][0])
    await fb.assign(db, b["id"], s)
    deep = await _svc_id(db, "deep-cleaning")
    svc = BookingService(db)
    out = await svc.edit_booking(b["id"], edit(service_ids=[deep]), s["cu"]["id"], "customer")
    d = await fb.doc(db, b["id"])
    deep_doc = await db.services.find_one({"slug": "deep-cleaning"})
    expected = svc._resolve_price(deep_doc, d["vehicle_type"], d["first_time_eligible"])
    assert d["service_ids"] == [deep] and d["subtotal"] == expected and d["total_amount"] == expected
    assert d["captain_id"] == s["cap"]["id"] and d["status"] == "assigned"
    assert d["duration_minutes"] == deep_doc["duration_minutes"]
    assert out["amount_due"] == expected and d["payment_status"] == "pending"
    assert await fb.bell(db, s["cap"]["id"], "Booking changed")
    assert d["platform_earning"] == pytest.approx(d["total_amount"] - d["captain_earning"])


async def test_paid_booking_price_down_credits_wallet_price_up_becomes_due(db):
    s = await fb.rig(db)
    cu = s["cu"]["id"]
    deep = await _svc_id(db, "deep-cleaning")
    star = await fb.get_star_wash_service_id(db)
    b = await fb.book(db, s["cu"], s["when"], s["keys"][0], service_ids=[deep]) if False else None
    # Book Deep Cleaning, pay it online, then move down to Star Wash.
    from app.schemas.booking_schema import BookingCreateRequest

    made = await BookingService(db).create_booking(cu, BookingCreateRequest(
        vehicle_type=await fb.get_hatchback_type_id(db), address_id=s["cu"]["address_id"], service_ids=[deep],
        scheduled_date=s["when"], scheduled_slot=s["keys"][0], payment_method="cash",
    ), _allow_pinless=True)
    await fb.pay_online(db, made["id"])
    paid_total = (await fb.doc(db, made["id"]))["total_amount"]
    out = await BookingService(db).edit_booking(made["id"], edit(service_ids=[star]), cu, "customer")
    d = await fb.doc(db, made["id"])
    assert d["total_amount"] < paid_total
    assert out["wallet_credit"] == pytest.approx(paid_total - d["total_amount"])
    assert await fb.balance(db, cu) == pytest.approx(paid_total - d["total_amount"])
    assert d["payment_status"] == "paid" and out["amount_due"] == 0
    # ... and back up: the difference is DUE (part-paid), nothing taken from the wallet.
    bal = await fb.balance(db, cu)
    out2 = await BookingService(db).edit_booking(made["id"], edit(service_ids=[deep]), cu, "customer")
    d2 = await fb.doc(db, made["id"])
    assert d2["payment_status"] == "partially_paid" and out2["amount_due"] == pytest.approx(d2["total_amount"] - d2["amount_paid"])
    assert await fb.balance(db, cu) == bal


async def test_edit_inside_the_last_hour_or_with_the_captain_out_is_refused(db):
    s = await fb.rig(db)
    b = await fb.book(db, s["cu"], s["when"], s["keys"][0])
    await fb.slot_in(db, b["id"], 59)
    with pytest.raises(BadRequestException, match="1 hour before"):
        await BookingService(db).edit_booking(b["id"], edit(customer_notes="Gate 2"), s["cu"]["id"], "customer")
    async with h.client() as c:
        r = await c.patch(f"/api/v1/bookings/{b['id']}", json={"customer_notes": "Gate 2"}, headers=s["cu"]["h"])
        assert r.status_code == 400 and "1 hour before" in r.json()["message"]
        # The older reschedule endpoint follows the same rule.
        new_when, new_keys = await h.slot_keys(db, s["center_id"], offset=3)
        r = await c.post(f"/api/v1/bookings/{b['id']}/reschedule", json={"scheduled_date": new_when, "scheduled_slot": new_keys[0]}, headers=s["cu"]["h"])
        assert r.status_code == 400 and "1 hour before" in r.json()["message"]
    b2 = await fb.book(db, s["cu"], s["when"], s["keys"][1])
    await fb.hb.on_the_way(db, b2["id"], s["cap"]["id"])
    with pytest.raises(BadRequestException, match="on the way"):
        await BookingService(db).edit_booking(b2["id"], edit(customer_notes="x"), s["cu"]["id"], "customer")
    # Someone else's booking: 404, and staff/captain can't use this endpoint.
    other = await h.customer(db, s["pin"])
    async with h.client() as c:
        assert (await c.patch(f"/api/v1/bookings/{b2['id']}", json={"customer_notes": "x"}, headers=other["h"])).status_code == 404
        assert (await c.patch(f"/api/v1/bookings/{b2['id']}", json={"customer_notes": "x"}, headers=s["mgr"]["h"])).status_code == 403


async def test_address_must_stay_with_the_same_center(db):
    s = await fb.rig(db)
    b = await fb.book(db, s["cu"], s["when"], s["keys"][0])
    _other_center, other_pin = await h.center(db)
    far = await fb.h.customer(db, other_pin)  # an address served elsewhere
    far_addr = await db.addresses.find_one({"_id": ObjectId(far["address_id"])})
    mine = await AddressService(db).create(s["cu"]["id"], __import__("app.schemas.profile_schema", fromlist=["AddressCreateRequest"]).AddressCreateRequest(
        label="Office", line1="Elsewhere 1", city="Indore", state="MP", pincode=far_addr["pincode"],
    ))
    with pytest.raises(BadRequestException, match="different Blussit center"):
        await BookingService(db).edit_booking(b["id"], edit(address_id=mine["id"]), s["cu"]["id"], "customer")
    assert (await fb.doc(db, b["id"]))["address_id"] == s["cu"]["address_id"]
    # A second address in the same area: accepted, snapshot moves with it.
    near = await AddressService(db).create(s["cu"]["id"], __import__("app.schemas.profile_schema", fromlist=["AddressCreateRequest"]).AddressCreateRequest(
        label="Home 2", line1="Second Street 9", city="Indore", state="MP", pincode=s["pin"],
    ))
    out = await BookingService(db).edit_booking(b["id"], edit(address_id=near["id"]), s["cu"]["id"], "customer")
    d = await fb.doc(db, b["id"])
    assert d["address_id"] == near["id"] and d["address_snapshot"]["line1"] == "Second Street 9"
    assert [c["field"] for c in out["changes"]] == ["address"]


async def test_plan_booking_service_change_refused_date_allowed(db):
    s = await fb.rig(db)
    sub = await fb.pass_for(db, s["cu"]["id"])
    b = await fb.book(db, s["cu"], s["when"], s["keys"][0], subscription_id=sub)
    deep = await _svc_id(db, "deep-cleaning")
    with pytest.raises(BadRequestException, match="covered by your plan"):
        await BookingService(db).edit_booking(b["id"], edit(service_ids=[deep]), s["cu"]["id"], "customer")
    with pytest.raises(BadRequestException, match="covered by your plan"):
        await BookingService(db).edit_booking(b["id"], edit(vehicle_type=await fb.get_suv_type_id(db)), s["cu"]["id"], "customer")
    new_when, new_keys = await h.slot_keys(db, s["center_id"], offset=3)
    await BookingService(db).edit_booking(b["id"], edit(scheduled_date=new_when, scheduled_slot=new_keys[0], customer_notes="Blue gate"), s["cu"]["id"], "customer")
    d = await fb.doc(db, b["id"])
    assert d["scheduled_slot"] == new_keys[0] and d["customer_notes"] == "Blue gate" and d["total_amount"] == 0
    assert await fb.remaining(db, sub) == 3


async def test_car_type_change_reprices_and_keeps_the_line_key_unique(db):
    s = await fb.rig(db)
    b = await fb.book(db, s["cu"], s["when"], s["keys"][0])
    suv = await fb.get_suv_type_id(db)
    before = await fb.doc(db, b["id"])
    await BookingService(db).edit_booking(b["id"], edit(vehicle_type=suv), s["cu"]["id"], "customer")
    d = await fb.doc(db, b["id"])
    star = await db.services.find_one({"slug": "star-wash"})
    assert d["vehicle_type"] == suv and d["visit_line_key"] == f"{suv}#0"
    assert d["subtotal"] == BookingService._resolve_price(star, suv, d["first_time_eligible"])
    assert d["vehicle_label"] != before["vehicle_label"]


async def test_expected_total_guard(db):
    s = await fb.rig(db)
    b = await fb.book(db, s["cu"], s["when"], s["keys"][0])
    deep = await _svc_id(db, "deep-cleaning")
    with pytest.raises(PriceChangedException):
        await BookingService(db).edit_booking(b["id"], edit(service_ids=[deep], expected_total=b["total_amount"]), s["cu"]["id"], "customer")
    assert (await fb.doc(db, b["id"]))["service_ids"] == b["service_ids"]


async def test_group_edit_changes_one_car_of_the_visit(db):
    s = await fb.rig(db)
    async with h.client() as c:
        gid, ids = await h.group(c, db, s["cu"], s["when"], s["keys"][0], cars=2)
        deep = await _svc_id(db, "deep-cleaning")
        r = await c.patch(f"/api/v1/bookings/group/{gid}", json={"cars": [{"booking_id": ids[1], "service_ids": [deep]}], "customer_notes": "Both in basement"}, headers=s["cu"]["h"])
    assert r.status_code == 200, r.text
    first, second = await fb.doc(db, ids[0]), await fb.doc(db, ids[1])
    assert second["service_ids"] == [deep] and first["service_ids"] != [deep]
    assert first["customer_notes"] == second["customer_notes"] == "Both in basement"
    # The second car starts where the first ends — unchanged first car.
    assert second["group_offset_minutes"] == first["duration_minutes"]


async def test_saved_address_edit_never_moves_a_live_booking(db):
    s = await fb.rig(db)
    b = await fb.book(db, s["cu"], s["when"], s["keys"][0])
    original = (await fb.doc(db, b["id"]))["address_snapshot"]
    assert original and original["line1"]
    # A booking from before snapshots: frozen at the address's current state by the edit.
    legacy = await fb.book(db, s["cu"], s["when"], s["keys"][1])
    await db.bookings.update_one({"_id": ObjectId(legacy["id"])}, {"$unset": {"address_snapshot": ""}})
    await AddressService(db).update(s["cu"]["id"], s["cu"]["address_id"], AddressUpdateRequest(line1="Moved Somewhere Else 77"))
    assert (await fb.doc(db, b["id"]))["address_snapshot"]["line1"] == original["line1"]
    assert (await fb.doc(db, legacy["id"]))["address_snapshot"]["line1"] == original["line1"]
    view = await BookingService(db).get_booking(b["id"])
    assert view["address_snapshot"]["line1"] == original["line1"]
    # A brand-new booking uses the edited address.
    fresh = await fb.book(db, s["cu"], s["when"], s["keys"][2])
    assert (await fb.doc(db, fresh["id"]))["address_snapshot"]["line1"] == "Moved Somewhere Else 77"


async def test_backfill_address_snapshots_is_idempotent(db):
    from app.services.booking_service import backfill_address_snapshots

    s = await fb.rig(db)
    b = await fb.book(db, s["cu"], s["when"], s["keys"][0])
    await db.bookings.update_one({"_id": ObjectId(b["id"])}, {"$unset": {"address_snapshot": ""}})
    assert await backfill_address_snapshots(db, force=True) >= 1
    snap = (await fb.doc(db, b["id"]))["address_snapshot"]
    assert snap["address_id"] == s["cu"]["address_id"]
    assert await backfill_address_snapshots(db, force=True) == 0
    assert await backfill_address_snapshots(db) == 0  # done marker


async def test_coupon_that_no_longer_applies_is_dropped_with_a_notice(db):
    s = await fb.rig(db)
    deep = await _svc_id(db, "deep-cleaning")
    star = await fb.get_star_wash_service_id(db)
    now = now_ist()
    code = f"BIG{ObjectId().__str__()[-6:]}".upper()
    await db.coupons.insert_one({
        "code": code, "description": "₹100 off over ₹600", "coupon_type": "flat", "value": 100, "min_order_value": 600,
        "max_discount_amount": None, "valid_from": now - timedelta(days=1), "valid_until": now + timedelta(days=10),
        "usage_limit_per_user": 1, "total_usage_limit": None, "total_used": 0, "is_active": True, "is_deleted": False,
        "offer_kind": "standard",
    })
    from app.schemas.booking_schema import BookingCreateRequest

    made = await BookingService(db).create_booking(s["cu"]["id"], BookingCreateRequest(
        vehicle_type=await fb.get_hatchback_type_id(db), address_id=s["cu"]["address_id"], service_ids=[deep],
        scheduled_date=s["when"], scheduled_slot=s["keys"][0], payment_method="cash", coupon_code=code,
    ), _allow_pinless=True)
    assert made["discount_amount"] == 100
    out = await BookingService(db).edit_booking(made["id"], edit(service_ids=[star]), s["cu"]["id"], "customer")
    d = await fb.doc(db, made["id"])
    assert d["coupon_code"] is None and d["discount_amount"] == 0
    assert out["notices"] and code in out["notices"][0]
    assert not await db.coupon_usages.find_one({"booking_id": made["id"]})


# ------------------------------------------------------------------ races


async def test_two_edits_at_once_move_the_seat_once(db):
    for _ in range(3):
        s = await fb.rig(db)
        b = await fb.book(db, s["cu"], s["when"], s["keys"][0])
        new_when, new_keys = await h.slot_keys(db, s["center_id"], offset=3)
        results = await asyncio.gather(
            BookingService(db).edit_booking(b["id"], edit(scheduled_date=new_when, scheduled_slot=new_keys[0]), s["cu"]["id"], "customer"),
            BookingService(db).edit_booking(b["id"], edit(scheduled_date=new_when, scheduled_slot=new_keys[1]), s["cu"]["id"], "customer"),
            return_exceptions=True,
        )
        assert sum(1 for r in results if isinstance(r, dict)) == 1, results
        d = await fb.doc(db, b["id"])
        for when, key in [(s["when"], s["keys"][0]), (new_when, new_keys[0]), (new_when, new_keys[1])]:
            st = await h.state(db, s["center_id"], when, key)
            assert (st["booked"] or 0) == st["owners"] == st["live"], (when, key, st)
        assert d["scheduled_slot"] in new_keys[:2]


async def test_edit_vs_cancel_at_once(db):
    for _ in range(3):
        s = await fb.rig(db)
        b = await fb.book(db, s["cu"], s["when"], s["keys"][0])
        new_when, new_keys = await h.slot_keys(db, s["center_id"], offset=3)
        results = await asyncio.gather(
            BookingService(db).edit_booking(b["id"], edit(scheduled_date=new_when, scheduled_slot=new_keys[0]), s["cu"]["id"], "customer"),
            BookingService(db).cancel_booking(b["id"], fb.cancel(), s["cu"]["id"], "customer"),
            return_exceptions=True,
        )
        assert sum(1 for r in results if isinstance(r, dict)) >= 1, results
        for when, key in [(s["when"], s["keys"][0]), (new_when, new_keys[0])]:
            st = await h.state(db, s["center_id"], when, key)
            assert (st["booked"] or 0) == st["owners"] == st["live"], (when, key, st)


async def test_edit_vs_assign_at_once(db):
    for _ in range(3):
        s = await fb.rig(db)
        b = await fb.book(db, s["cu"], s["when"], s["keys"][0])
        new_when, new_keys = await h.slot_keys(db, s["center_id"], offset=3)
        from app.schemas.booking_schema import BookingAssignCaptainRequest

        results = await asyncio.gather(
            BookingService(db).edit_booking(b["id"], edit(scheduled_date=new_when, scheduled_slot=new_keys[0]), s["cu"]["id"], "customer"),
            BookingService(db).assign_captain(b["id"], BookingAssignCaptainRequest(captain_id=s["cap"]["id"]), s["mgr"]["id"], "manager", s["center_id"]),
            return_exceptions=True,
        )
        assert any(isinstance(r, dict) for r in results), results
        d = await fb.doc(db, b["id"])
        if d["status"] == "assigned":
            # Assigned to the slot the booking is really in now.
            start = d["estimated_start_at"]
            assert d["captain_id"] == s["cap"]["id"] and abs((start - d["slot_start"]).total_seconds()) < 1
        else:
            assert d["captain_id"] is None
        st = await h.state(db, s["center_id"], new_when, new_keys[0])
        assert (st["booked"] or 0) == st["owners"]


async def test_bot_reschedule_is_audited_and_follows_the_edit_lock(db):
    s = await fb.rig(db)
    b = await fb.book(db, s["cu"], s["when"], s["keys"][0])
    await fb.assign(db, b["id"], s)  # assigned is fine now — the captain is released
    new_when, new_keys = await h.slot_keys(db, s["center_id"], offset=3)
    await BookingService(db).reschedule_booking(b["id"], BookingRescheduleRequest(scheduled_date=new_when, scheduled_slot=new_keys[0]), s["cu"]["id"], "customer")
    row = await db.audit_logs.find_one({"action": "RESCHEDULE_BOOKING", "target_id": b["id"]})
    assert row and row["actor_role"] == "customer" and row["details"]["new_slot"] == new_keys[0]
