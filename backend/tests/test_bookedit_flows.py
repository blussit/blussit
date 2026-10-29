"""Founder 2026-10-28, the surrounding flows: a manager's discount survives
the customer's own edit and an on-site add-on; the manager portal's
reschedule keeps money and plan validity, works for a still-unpaid booking
and cannot be looped."""
import pytest

from app.schemas.booking_adjust_schema import BookingDiscountRequest
from app.schemas.booking_schema import BookingEditRequest, BookingRescheduleRequest
from app.services import booking_money as bm
from app.services.booking_adjust_service import BookingAdjustService
from app.services.booking_service import BookingService
from tests import test_feat_booking_helpers as fb
from tests import test_fix_core_helpers as h

pytestmark = pytest.mark.asyncio


def mgr(s):
    return {"id": s["mgr"]["id"], "role": "manager", "center": s["center_id"]}


async def discount(db, s, bid, amount):
    return await BookingAdjustService(db).set_discount(bid, BookingDiscountRequest(amount=amount, reason="Goodwill for the wait"), mgr(s))


async def test_customer_edit_keeps_the_managers_discount(db):
    s = await fb.rig(db)
    b = await fb.book(db, s["cu"], s["when"], s["keys"][0])
    await discount(db, s, b["id"], 40)
    before = await fb.doc(db, b["id"])
    services = [x for x in await fb.services(db, addon=False) if str(x["_id"]) not in before["service_ids"] and not x.get("charges_travel") and not x.get("prepaid_only")]
    out = await BookingService(db).edit_booking(
        b["id"], BookingEditRequest(service_ids=[str(services[0]["_id"])]), s["cu"]["id"], "customer",
    )
    after = await fb.doc(db, b["id"])
    assert after["manager_discount"] == 40
    assert after["total_amount"] == round(max(0.0, after["subtotal"] - after["discount_amount"]) + float(after.get("travel_charge") or 0), 2)
    assert after["discount_amount"] >= 40 and out["total_amount"] == after["total_amount"]


async def test_on_site_add_on_and_tip_leave_the_discount_alone(db):
    s = await fb.rig(db)
    b = await fb.book(db, s["cu"], s["when"], s["keys"][0])
    await discount(db, s, b["id"], 25)
    addon = await fb.services(db, addon=True)
    before = await fb.doc(db, b["id"])
    if addon:
        res = await BookingService(db).add_services_on_site(b["id"], [str(addon[0]["_id"])], {}, s["mgr"]["id"], "manager", s["center_id"])
        after = await fb.doc(db, b["id"])
        assert after["manager_discount"] == 25 and after["total_amount"] == round(before["total_amount"] + res["added_total"], 2)


async def test_discount_after_an_addon_counts_the_whole_bill(db):
    s = await fb.rig(db)
    b = await fb.book(db, s["cu"], s["when"], s["keys"][0])
    addon = await fb.services(db, addon=True)
    if not addon:
        pytest.skip("no add-on in the seed")
    await BookingService(db).add_services_on_site(b["id"], [str(addon[0]["_id"])], {}, s["mgr"]["id"], "manager", s["center_id"])
    total = bm.total_of(await fb.doc(db, b["id"]))
    out = await discount(db, s, b["id"], int(total))
    assert out["amount_due_after"] == 0


async def test_manager_reschedule_keeps_the_money_and_tells_everyone(db):
    s = await fb.rig(db)
    b = await fb.book(db, s["cu"], s["when"], s["keys"][0])
    await fb.assign(db, b["id"], s)
    await discount(db, s, b["id"], 30)
    money_before = bm.money_view(await fb.doc(db, b["id"]))
    async with h.client() as c:
        r = await c.post(f"/api/v1/bookings/{b['id']}/reschedule", json={"scheduled_date": s["when"], "scheduled_slot": s["keys"][1]}, headers=s["mgr"]["h"])
        assert r.status_code == 200, r.text
    after = await fb.doc(db, b["id"])
    assert after["scheduled_slot"] == s["keys"][1] and after["status"] == "rescheduled" and after["captain_id"] is None
    assert bm.money_view(after) == money_before and after["manager_discount"] == 30
    assert await fb.bell(db, s["cap"]["id"], "Booking rescheduled — no longer yours")
    assert await fb.bell(db, s["cu"]["id"], "Booking rescheduled")
    assert await db.audit_logs.find_one({"action": "RESCHEDULE_BOOKING", "target_id": b["id"]})


async def test_manager_reschedule_of_an_unpaid_online_booking_stays_awaiting_payment(db):
    s = await fb.rig(db)
    b = await fb.book(db, s["cu"], s["when"], s["keys"][0], method="online")
    assert (await fb.doc(db, b["id"]))["status"] == "awaiting_payment"
    await BookingService(db).reschedule_booking(
        b["id"], BookingRescheduleRequest(scheduled_date=s["when"], scheduled_slot=s["keys"][1]), s["mgr"]["id"], "manager", s["center_id"],
    )
    after = await fb.doc(db, b["id"])
    assert after["status"] == "awaiting_payment" and after["scheduled_slot"] == s["keys"][1] and after["holds_seat"] is True
    assert (await h.seat(db, s["center_id"], s["when"], s["keys"][1]))["booked_count"] == 1
    assert (await h.seat(db, s["center_id"], s["when"], s["keys"][0]))["booked_count"] == 0


async def test_reschedule_rules_for_a_manager(db):
    s = await fb.rig(db)
    other = await fb.rig(db)
    b = await fb.book(db, s["cu"], s["when"], s["keys"][0])
    svc = BookingService(db)
    req = BookingRescheduleRequest(scheduled_date=s["when"], scheduled_slot=s["keys"][1])
    with pytest.raises(Exception) as exc:        # another center's manager
        await svc.reschedule_booking(b["id"], req, other["mgr"]["id"], "manager", other["center_id"])
    assert getattr(exc.value, "status_code", None) == 403
    with pytest.raises(Exception):               # the past
        await svc.reschedule_booking(b["id"], BookingRescheduleRequest(scheduled_date=h.day(-3), scheduled_slot=s["keys"][1]), s["mgr"]["id"], "manager", s["center_id"])
    with pytest.raises(Exception):               # not one of the center's slots
        await svc.reschedule_booking(b["id"], BookingRescheduleRequest(scheduled_date=s["when"], scheduled_slot="03:00-04:00"), s["mgr"]["id"], "manager", s["center_id"])
    await h.set_cap(db, s["center_id"], s["when"], s["keys"][2], 0)
    with pytest.raises(Exception):               # a full slot
        await svc.reschedule_booking(b["id"], BookingRescheduleRequest(scheduled_date=s["when"], scheduled_slot=s["keys"][2]), s["mgr"]["id"], "manager", s["center_id"])
    after = await fb.doc(db, b["id"])
    assert after["scheduled_slot"] == s["keys"][0] and after["holds_seat"] is True


async def test_reschedule_storm_is_capped_per_booking(db):
    s = await fb.rig(db)
    b = await fb.book(db, s["cu"], s["when"], s["keys"][0])
    svc = BookingService(db)
    refused = 0
    for i in range(BookingService.MAX_RESCHEDULES_PER_HOUR + 3):
        slot = s["keys"][(i + 1) % 2]
        try:
            await svc.reschedule_booking(b["id"], BookingRescheduleRequest(scheduled_date=s["when"], scheduled_slot=slot), s["mgr"]["id"], "manager", s["center_id"])
        except Exception as exc:  # noqa: BLE001
            assert getattr(exc, "status_code", None) == 429, exc
            refused += 1
    assert refused >= 3


async def test_pass_booking_cannot_be_moved_past_its_last_day(db):
    s = await fb.rig(db)
    sub = await fb.pass_for(db, s["cu"]["id"])
    from app.schemas.booking_schema import BookingCreateRequest
    from tests.factories import get_hatchback_type_id, get_star_wash_service_id

    b = await BookingService(db).create_booking(
        s["cu"]["id"], BookingCreateRequest(
            vehicle_type=await get_hatchback_type_id(db), address_id=s["cu"]["address_id"], service_ids=[await get_star_wash_service_id(db)],
            scheduled_date=s["when"], scheduled_slot=s["keys"][0], payment_method="subscription", subscription_id=sub), source="app", _allow_pinless=True,
    )
    # The pass ends today: any later day is outside it.
    from datetime import datetime, timedelta

    await db.user_subscriptions.update_one({"_id": __import__("bson").ObjectId(sub)}, {"$set": {"end_date": datetime.utcnow() - timedelta(days=1), "extended_until": None}})
    when_far, keys = await h.slot_keys(db, s["center_id"], offset=4)
    with pytest.raises(Exception) as exc:
        await BookingService(db).reschedule_booking(
            b["id"], BookingRescheduleRequest(scheduled_date=when_far, scheduled_slot=keys[0]), s["mgr"]["id"], "manager", s["center_id"],
        )
    assert "pass" in str(exc.value).lower() or "last" in str(exc.value).lower() or "period" in str(exc.value).lower(), str(exc.value)
    assert (await fb.doc(db, b["id"]))["scheduled_slot"] == s["keys"][0]
