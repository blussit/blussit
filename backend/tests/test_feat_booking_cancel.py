"""Feature build 2026-10-07 — the customer's own cancel (spec 1.2): until
the captain heads out, before or after the slot start, charged by the
policy tier through the WALLET; plan cars forfeit the wash inside the last
hour; money settled in the cancel transaction (MoneyService)."""
import asyncio

import pytest

from app.core.exceptions import BadRequestException
from app.schemas.booking_schema import HeadingRequest
from app.services.booking_service import BookingService
from tests import test_feat_booking_helpers as fb
from tests import test_fix_core_helpers as h

pytestmark = pytest.mark.asyncio


async def test_customer_cancel_tiers_through_the_wallet(db):
    s = await fb.rig(db)
    cu = s["cu"]["id"]
    svc = BookingService(db)
    # > 4 h before: free, nothing on the wallet.
    free = await fb.book(db, s["cu"], s["when"], s["keys"][0])
    out = await svc.cancel_booking(free["id"], fb.cancel(), cu, "customer")
    assert out["status"] == "cancelled" and out["late_cancellation_charge"] is None
    assert await fb.balance(db, cu) == 0
    # 2 h before (1–4 h tier, ₹50) on an unpaid cash booking: a wallet debit.
    b = await fb.book(db, s["cu"], s["when"], s["keys"][1])
    await fb.slot_in(db, b["id"], 2 * 60)
    out = await svc.cancel_booking(b["id"], fb.cancel(), cu, "customer")
    assert out["late_cancellation_charge"]["amount"] == 50 and out["late_cancellation_charge"]["tier"] == "1_to_4h"
    assert out["wallet"]["net"] == -50 and await fb.balance(db, cu) == -50
    charge = (await fb.hb.charges_of(db, cu))[-1]
    assert charge["status"] == "settled" and charge["settled_via"] == "wallet"
    # After the slot already started (captain not out yet): ₹80, still allowed.
    late = await fb.book(db, s["cu"], s["when"], s["keys"][2])
    await fb.slot_in(db, late["id"], -20)
    out = await svc.cancel_booking(late["id"], fb.cancel(), cu, "customer")
    assert out["late_cancellation_charge"]["amount"] == 80
    assert await fb.balance(db, cu) == -130
    # Every cancel audited by the service (every channel).
    assert await db.audit_logs.count_documents({"action": "CANCEL_BOOKING", "target_id": {"$in": [free["id"], b["id"], late["id"]]}}) == 3


async def test_boundaries_exactly_4h_and_1h(db):
    s = await fb.rig(db)
    cu = s["cu"]["id"]
    svc = BookingService(db)
    four = await fb.book(db, s["cu"], s["when"], s["keys"][0])
    await fb.slot_in(db, four["id"], 4 * 60 + 1)  # a minute more than 4 h: free
    assert (await svc.cancel_booking(four["id"], fb.cancel(), cu, "customer"))["late_cancellation_charge"] is None
    one = await fb.book(db, s["cu"], s["when"], s["keys"][1])
    await fb.slot_in(db, one["id"], 61)  # just over an hour: ₹50
    assert (await svc.cancel_booking(one["id"], fb.cancel(), cu, "customer"))["late_cancellation_charge"]["amount"] == 50
    under = await fb.book(db, s["cu"], s["when"], s["keys"][2])
    await fb.slot_in(db, under["id"], 59)  # just under: ₹80
    assert (await svc.cancel_booking(under["id"], fb.cancel(), cu, "customer"))["late_cancellation_charge"]["amount"] == 80


async def test_customer_can_cancel_assigned_but_not_once_the_captain_left(db):
    s = await fb.rig(db)
    cu = s["cu"]["id"]
    svc = BookingService(db)
    b = await fb.book(db, s["cu"], s["when"], s["keys"][0])
    await fb.assign(db, b["id"], s)
    out = await svc.cancel_booking(b["id"], fb.cancel(), cu, "customer")
    assert out["status"] == "cancelled"
    assert await fb.bell(db, s["cap"]["id"], "Booking cancelled")
    b2 = await fb.book(db, s["cu"], s["when"], s["keys"][1])
    await fb.hb.on_the_way(db, b2["id"], s["cap"]["id"])
    with pytest.raises(BadRequestException, match="captain is already on the way"):
        await svc.cancel_booking(b2["id"], fb.cancel(), cu, "customer")
    assert (await fb.doc(db, b2["id"]))["status"] == "captain_on_the_way"
    # Over HTTP too (no UI-only lock).
    async with h.client() as c:
        r = await c.post(f"/api/v1/bookings/{b2['id']}/cancel", json={"reason": "Changed my mind"}, headers=s["cu"]["h"])
    assert r.status_code == 400


async def test_visit_with_one_car_on_the_way_is_locked_for_the_customer(db):
    s = await fb.rig(db)
    async with h.client() as c:
        gid, ids = await h.group(c, db, s["cu"], s["when"], s["keys"][0], cars=2)
    await fb.hb.on_the_way(db, ids[0], s["cap"]["id"])
    svc = BookingService(db)
    with pytest.raises(BadRequestException, match="captain is already on the way"):
        await svc.cancel_booking(ids[1], fb.cancel(), s["cu"]["id"], "customer")
    with pytest.raises(BadRequestException, match="captain is already on the way"):
        await svc.cancel_booking_group(gid, fb.cancel(), s["cu"]["id"], "customer")
    # Staff still can (business cancel: free).
    out = await svc.cancel_booking_group(gid, fb.cancel("Rain"), s["mgr"]["id"], "manager", s["center_id"])
    assert out["cancelled_count"] == 2 and out["late_cancellation_charge"] is None


async def test_cancel_vs_captain_heading_out_at_the_same_moment(db):
    """Exactly one wins: either the booking is cancelled and the heading-out
    refused, or the captain is on the way and the cancel refused — never a
    cancelled booking with a captain on the road."""
    for _ in range(4):
        s = await fb.rig(db)
        b = await fb.book(db, s["cu"], s["when"], s["keys"][0])
        await fb.assign(db, b["id"], s)
        await fb.slot_in(db, b["id"], 10)
        from bson import ObjectId
        from app.utils.timezone import now_ist

        await db.bookings.update_one({"_id": ObjectId(b["id"])}, {"$set": {"estimated_start_at": now_ist() + fb.timedelta(minutes=10)}})
        svc_a, svc_b = BookingService(db), BookingService(db)
        results = await asyncio.gather(
            svc_a.cancel_booking(b["id"], fb.cancel(), s["cu"]["id"], "customer"),
            svc_b.start_heading(b["id"], HeadingRequest(latitude=22.7, longitude=75.8, equipment_used=[]), s["cap"]["id"]),
            return_exceptions=True,
        )
        ok = [r for r in results if isinstance(r, dict)]
        assert len(ok) == 1, results
        final = await fb.doc(db, b["id"])
        if isinstance(results[0], dict):
            assert final["status"] == "cancelled"
        else:
            assert final["status"] == "captain_on_the_way"


async def test_prepaid_booking_cancelled_late_credits_paid_minus_charge(db):
    s = await fb.rig(db)
    cu = s["cu"]["id"]
    b = await fb.book(db, s["cu"], s["when"], s["keys"][0])
    await fb.pay_online(db, b["id"])
    paid = await fb.doc(db, b["id"])
    assert paid["payment_status"] == "paid"
    await fb.slot_in(db, b["id"], 30)
    out = await BookingService(db).cancel_booking(b["id"], fb.cancel(), cu, "customer")
    assert out["late_cancellation_charge"]["amount"] == 80
    assert out["wallet"]["credited"] == paid["total_amount"]
    assert await fb.balance(db, cu) == pytest.approx(paid["total_amount"] - 80)
    after = await fb.doc(db, b["id"])
    assert after["payment_status"] == "refunded" and after["refunded_to"] == "wallet"
    # No admin refund-queue row any more — the money is in the wallet.
    assert not await db.payment_orders.find_one({"kind": "refund_due", "booking_id": b["id"]})
    # The customer's WhatsApp: the v5 cancel template with the wallet line.
    [row] = await fb.queued(db, cu, "booking_cancelled_v5")
    assert "Cancelled by you" in row["wa_params"][4] and "wallet" in row["wa_params"][4]
    # Managers: in-app only.
    note = await fb.bell(db, s["mgr"]["id"], "Customer cancelled")
    assert note and "₹80" in note[0]["message"]
    assert not await fb.queued(db, s["mgr"]["id"], "booking_cancelled_v5")


async def test_business_cancel_never_charges_and_returns_everything(db):
    s = await fb.rig(db)
    cu = s["cu"]["id"]
    b = await fb.book(db, s["cu"], s["when"], s["keys"][0])
    await fb.pay_online(db, b["id"])
    await fb.slot_in(db, b["id"], 20)
    total = (await fb.doc(db, b["id"]))["total_amount"]
    out = await BookingService(db).cancel_booking(b["id"], fb.cancel("No captain available"), s["mgr"]["id"], "manager", s["center_id"])
    assert out["late_cancellation_charge"] is None and out["wallet"]["credited"] == total
    assert await fb.balance(db, cu) == total
    [row] = await fb.queued(db, cu, "booking_cancelled_v5")
    assert "Cancelled by us" in row["wa_params"][4]


async def test_plan_car_61_vs_59_minutes(db):
    s = await fb.rig(db)
    cu = s["cu"]["id"]
    sub = await fb.pass_for(db, cu)
    svc = BookingService(db)
    early = await fb.book(db, s["cu"], s["when"], s["keys"][0], subscription_id=sub)
    assert await fb.remaining(db, sub) == 3
    await fb.slot_in(db, early["id"], 61)
    out = await svc.cancel_booking(early["id"], fb.cancel(), cu, "customer")
    assert out["late_cancellation_charge"] is None and out["plan_wash_forfeited"] is False
    assert await fb.remaining(db, sub) == 4  # returned
    late = await fb.book(db, s["cu"], s["when"], s["keys"][1], subscription_id=sub)
    assert await fb.remaining(db, sub) == 3
    await fb.slot_in(db, late["id"], 59)
    out = await svc.cancel_booking(late["id"], fb.cancel(), cu, "customer")
    assert out["late_cancellation_charge"] is None and out["plan_wash_forfeited"] is True
    assert await fb.remaining(db, sub) == 3  # used up
    d = await fb.doc(db, late["id"])
    assert d["consumption_forfeited"] is True and d.get("consumption_restored") is not True
    assert await fb.balance(db, cu) == 0  # never a money charge on a plan car
    # The recycle bin can't hand a forfeited wash back either.
    adm = await h.admin(db)
    await svc.soft_delete_booking(late["id"], adm["id"])
    assert await fb.remaining(db, sub) == 3


async def test_staff_may_return_a_forfeited_plan_wash(db):
    s = await fb.rig(db)
    sub = await fb.pass_for(db, s["cu"]["id"])
    b = await fb.book(db, s["cu"], s["when"], s["keys"][0], subscription_id=sub)
    await fb.slot_in(db, b["id"], 30)
    await BookingService(db).cancel_booking(
        b["id"], fb.cancel(at_customer_request=True, return_plan_wash=True), s["mgr"]["id"], "manager", s["center_id"],
    )
    assert await fb.remaining(db, sub) == 4


async def test_mixed_visit_one_charge_plan_car_follows_plan_rule(db):
    s = await fb.rig(db)
    cu = s["cu"]["id"]
    sub = await fb.pass_for(db, cu)
    hatch = await fb.get_hatchback_type_id(db)
    star = await fb.get_star_wash_service_id(db)
    from app.schemas.booking_schema import BookingGroupCreateRequest, GroupVehicleRequest

    visit = await BookingService(db).create_booking_group(cu, BookingGroupCreateRequest(
        vehicles=[GroupVehicleRequest(vehicle_type=hatch, service_ids=[star], subscription_id=sub),
                  GroupVehicleRequest(vehicle_type=hatch, service_ids=[star])],
        address_id=s["cu"]["address_id"], scheduled_date=s["when"], scheduled_slot=s["keys"][0], payment_method="cash",
    ), apply_charges=True)
    ids = [b["id"] for b in visit["bookings"]]
    await fb.slot_in(db, ids, 30)
    out = await BookingService(db).cancel_booking_group(visit["booking_group_id"], fb.cancel(), cu, "customer")
    assert out["cancelled_count"] == 2
    assert out["late_cancellation_charge"]["amount"] == 80  # one charge for the visit's paid part
    assert out["plan_wash_forfeited"] == 1
    assert await fb.remaining(db, sub) == 3
    assert await fb.balance(db, cu) == -80
    assert len(await fb.hb.charges_of(db, cu)) == 1


async def test_all_plan_visit_carries_no_money_charge(db):
    s = await fb.rig(db)
    cu = s["cu"]["id"]
    sub = await fb.pass_for(db, cu)
    b = await fb.book(db, s["cu"], s["when"], s["keys"][0], subscription_id=sub)
    await fb.slot_in(db, b["id"], 2 * 60)
    out = await BookingService(db).cancel_booking(b["id"], fb.cancel(), cu, "customer")
    assert out["late_cancellation_charge"] is None and await fb.balance(db, cu) == 0
    assert await fb.remaining(db, sub) == 4  # more than an hour before: returned


async def test_preview_shows_the_customer_what_cancelling_costs(db):
    s = await fb.rig(db)
    b = await fb.book(db, s["cu"], s["when"], s["keys"][0])
    await fb.pay_online(db, b["id"])
    await fb.slot_in(db, b["id"], 45)
    total = (await fb.doc(db, b["id"]))["total_amount"]
    async with h.client() as c:
        r = await c.get(f"/api/v1/bookings/{b['id']}/cancellation-charge-preview", headers=s["cu"]["h"])
    assert r.status_code == 200, r.text
    p = r.json()["data"]
    assert p["can_cancel"] is True and p["tier"] == "under_1h" and p["amount"] == 80
    assert p["wallet_credit"] == total and p["net"] == pytest.approx(total - 80) and p["wallet_balance_after"] == pytest.approx(total - 80)
    await fb.hb.on_the_way(db, b["id"], s["cap"]["id"])
    async with h.client() as c:
        p2 = (await c.get(f"/api/v1/bookings/{b['id']}/cancellation-charge-preview", headers=s["cu"]["h"])).json()["data"]
    assert p2["can_cancel"] is False and "on the way" in p2["refusal"]
    # Plan car inside the hour: no money, the wash is forfeited.
    sub = await fb.pass_for(db, s["cu"]["id"])
    pb = await fb.book(db, s["cu"], s["when"], s["keys"][1], subscription_id=sub)
    await fb.slot_in(db, pb["id"], 30)
    async with h.client() as c:
        p3 = (await c.get(f"/api/v1/bookings/{pb['id']}/cancellation-charge-preview", headers=s["cu"]["h"])).json()["data"]
    assert p3["amount"] == 0 and p3["plan_covered"] is True and p3["plan_wash_forfeited_count"] == 1 and p3["plan_wash_returned"] is False


async def test_whatsapp_bot_cancel_is_audited(db):
    """The bot calls the same service (actor customer) — it now leaves an
    audit row like every other channel."""
    s = await fb.rig(db)
    b = await fb.book(db, s["cu"], s["when"], s["keys"][0])
    await BookingService(db).cancel_booking(b["id"], fb.cancel("Cancelled by customer via WhatsApp"), s["cu"]["id"], "customer")
    row = await db.audit_logs.find_one({"action": "CANCEL_BOOKING", "target_id": b["id"]})
    assert row and row["actor_role"] == "customer"
