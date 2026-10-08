"""Core fix round 2 — the founder's late-cancellation charge (2026-10-07):
a late, customer-requested cancellation puts a charge (₹50 / ₹80 / ₹100 by
the policy table) on the customer's account; a manager can reduce or remove
it; admins see every charge.

Updated for the WALLET model (founder 2026-10-07, feature plan §1.1/1.2):
the charge is settled through the customer wallet at the cancel (the
`customer_charges` row is the record, status "settled"); the negative
balance rides on the NEXT booking as `wallet_due_carried` (cleared when that
booking is paid); a reduction is credited back to the wallet.

Every test drives the real services / HTTP routes on the local replica-set
Mongo."""
import asyncio
from datetime import datetime, timedelta, timezone

import pytest
from bson import ObjectId

from app.core.exceptions import BadRequestException
from app.schemas.booking_schema import BookingCreateRequest, BookingGroupCreateRequest, GroupVehicleRequest
from app.schemas.subscription_schema import SubscribeRequest
from app.services.booking_service import BookingService
from app.services.customer_charge_service import CustomerChargeService, late_cancellation_quote
from app.services.payment_service import PaymentService
from app.services.subscription_service import UserSubscriptionService
from app.utils.timezone import IST, now_ist
from tests import test_fix_core_helpers as h
from tests import test_fix_coreb_helpers as hb
from tests.factories import get_hatchback_type_id, get_star_wash_service_id, make_subscription_plan

pytestmark = pytest.mark.asyncio


async def _balance(db, customer_id: str) -> float:
    from app.services.customer_wallet_service import CustomerWalletService

    return await CustomerWalletService(db).balance(customer_id)


async def _summary(db, customer_id: str) -> dict:
    from app.services.customer_wallet_service import CustomerWalletService

    return await CustomerWalletService(db).summary(customer_id)
POLICY = {"cancellation_fee_1_to_4h": 50, "cancellation_fee_under_1h": 80, "cancellation_fee_after_captain_left": 100}


# ---------------------------------------------------------------- the tier table


def _car(slot_start: datetime, status: str = "pending", offset: int = 0) -> dict:
    return {"_id": ObjectId(), "status": status, "slot_start": slot_start, "slot_end": slot_start + timedelta(hours=3),
            "group_offset_minutes": offset, "scheduled_date": datetime(2026, 10, 9), "scheduled_slot": "09:00-12:00"}


def test_tier_boundaries_single_source_of_truth():
    now = datetime(2026, 10, 9, 5, 0, tzinfo=IST)
    at = lambda minutes: now + timedelta(minutes=minutes)  # noqa: E731 — slot start this far from now
    cases = [
        (at(4 * 60 + 1), "free", 0),
        (at(4 * 60), "free", 0),             # exactly 4 h before: still free (the customer's own lock edge)
        (at(4 * 60 - 1), "1_to_4h", 50),
        (at(60), "1_to_4h", 50),             # exactly 1 h before: the ₹50 tier
        (at(59), "under_1h", 80),
        (at(-30), "under_1h", 80),           # the slot already started, captain not left
    ]
    for start, tier, amount in cases:
        q = late_cancellation_quote([_car(start)], POLICY, now=now)
        assert (q["tier"], q["amount"]) == (tier, amount), (start - now, q)
        assert q["window_hours"] == 4
    left = late_cancellation_quote([_car(at(5 * 60), "captain_on_the_way")], POLICY, now=now)
    assert left["tier"] == "after_captain_left" and left["amount"] == 100 and left["captain_left"] is True
    started = late_cancellation_quote([_car(at(-10), "service_started")], POLICY, now=now)
    assert started["tier"] == "after_captain_left" and started["amount"] == 100
    # A visit: ANY car on the way makes it the captain-left tier.
    visit = [_car(at(30)), _car(at(30), "captain_on_the_way", offset=45)]
    assert late_cancellation_quote(visit, POLICY, now=now)["tier"] == "after_captain_left"
    # Never confirmed (still waiting for its online payment): free.
    assert late_cancellation_quote([_car(at(20), "awaiting_payment")], POLICY, now=now)["amount"] == 0
    # Admin-edited amounts.
    assert late_cancellation_quote([_car(at(30))], {**POLICY, "cancellation_fee_under_1h": 120}, now=now)["amount"] == 120
    assert late_cancellation_quote([_car(at(130))], POLICY, now=now)["minutes_to_slot"] == 130


# ---------------------------------------------------------------- created at the cancel


async def test_staff_cancel_at_customer_request_charges_policy_amount(db):
    s = await hb.staffed_center(db)
    b = await hb.new_booking(db, s["cu"], s["when"], s["keys"][0])
    await hb.slot_in(db, b["id"], 2 * 60)
    async with h.client() as c:
        r = await c.post(f"/api/v1/bookings/{b['id']}/cancel", json={"reason": "Customer called to cancel", "at_customer_request": True}, headers=s["mgr"]["h"])
    assert r.status_code == 200, r.text
    assert r.json()["data"]["late_cancellation_charge"]["amount"] == 50
    [charge] = await hb.charges_of(db, s["cu"]["id"])
    assert charge["status"] == "settled" and charge["tier"] == "1_to_4h"
    assert charge["amount"] == charge["original_amount"] == 50
    assert await _balance(db, s["cu"]["id"]) == -50
    assert charge["service_center_id"] == s["center_id"] and charge["source_booking_id"] == b["id"]
    assert charge["created_by"] == s["mgr"]["id"] and charge["created_by_role"] == "manager"
    assert [x["action"] for x in charge["history"]] == ["created"]
    note = await db.notifications.find_one({"user_id": s["cu"]["id"], "message": {"$regex": "₹50 charged to your Blussit wallet"}})
    assert note and "next booking" in note["message"]
    assert await db.audit_logs.find_one({"action": "CREATE_CUSTOMER_CHARGE", "target_id": str(charge["_id"])})


async def test_staff_business_cancel_never_charges(db):
    s = await hb.staffed_center(db)
    b = await hb.new_booking(db, s["cu"], s["when"], s["keys"][0])
    await hb.slot_in(db, b["id"], 20)  # inside the hour
    out = await BookingService(db).cancel_booking(b["id"], hb.cancel("Captain unavailable"), s["mgr"]["id"], "manager", s["center_id"])
    assert out["status"] == "cancelled" and out["late_cancellation_charge"] is None
    assert await hb.charges_of(db, s["cu"]["id"]) == []
    # A charge amount without "at the customer's request" is refused, not guessed.
    b2 = await hb.new_booking(db, s["cu"], s["when"], s["keys"][1])
    with pytest.raises(BadRequestException):
        await BookingService(db).cancel_booking(b2["id"], hb.cancel(charge_amount=50), s["mgr"]["id"], "manager", s["center_id"])
    assert (await hb.booking(db, b2["id"]))["status"] != "cancelled"


async def test_charge_amount_above_policy_refused_lower_accepted(db):
    s = await hb.staffed_center(db)
    b = await hb.new_booking(db, s["cu"], s["when"], s["keys"][0])
    await hb.slot_in(db, b["id"], 2 * 60)
    svc = BookingService(db)
    with pytest.raises(BadRequestException, match="can't be more than ₹50"):
        await svc.cancel_booking(b["id"], hb.cancel(at_customer_request=True, charge_amount=51), s["mgr"]["id"], "manager", s["center_id"])
    assert (await hb.booking(db, b["id"]))["status"] == "pending"
    assert await hb.charges_of(db, s["cu"]["id"]) == []
    await svc.cancel_booking(b["id"], hb.cancel(at_customer_request=True, charge_amount=30), s["mgr"]["id"], "manager", s["center_id"])
    [charge] = await hb.charges_of(db, s["cu"]["id"])
    assert charge["amount"] == 30 and charge["original_amount"] == 50
    # charge_amount 0 at the customer's request = no charge at all.
    b3 = await hb.new_booking(db, s["cu"], s["when"], s["keys"][1])
    await hb.slot_in(db, b3["id"], 30)
    await svc.cancel_booking(b3["id"], hb.cancel(at_customer_request=True, charge_amount=0), s["mgr"]["id"], "manager", s["center_id"])
    assert len(await hb.charges_of(db, s["cu"]["id"])) == 1


async def test_under_an_hour_and_captain_left_tiers(db):
    s = await hb.staffed_center(db)
    svc = BookingService(db)
    b1 = await hb.new_booking(db, s["cu"], s["when"], s["keys"][0])
    await hb.slot_in(db, b1["id"], 40)
    await svc.cancel_booking(b1["id"], hb.cancel(at_customer_request=True), s["mgr"]["id"], "manager", s["center_id"])
    b2 = await hb.new_booking(db, s["cu"], s["when"], s["keys"][1])
    await hb.slot_in(db, b2["id"], 6 * 60)  # far away — but the captain already left
    await hb.on_the_way(db, b2["id"], s["cap"]["id"])
    adm = await h.admin(db)
    await svc.cancel_booking(b2["id"], hb.cancel(at_customer_request=True), adm["id"], "admin", None)
    tiers = sorted((c["tier"], c["amount"]) for c in await hb.charges_of(db, s["cu"]["id"]))
    assert tiers == [("after_captain_left", 100), ("under_1h", 80)]


async def test_customer_self_cancel_more_than_4h_is_free(db):
    s = await hb.staffed_center(db)
    b = await hb.new_booking(db, s["cu"], s["when"], s["keys"][0])  # two days out
    async with h.client() as c:
        r = await c.post(f"/api/v1/bookings/{b['id']}/cancel", json={"reason": "Plans changed"}, headers=s["cu"]["h"])
        assert r.status_code == 200, r.text
        # A customer can't name a charge or waive one either — the field is ignored for them.
        b2 = await hb.new_booking(db, s["cu"], s["when"], s["keys"][1])
        r2 = await c.post(f"/api/v1/bookings/{b2['id']}/cancel", json={"reason": "Plans changed", "at_customer_request": True, "charge_amount": 999}, headers=s["cu"]["h"])
    assert r2.status_code in (200, 422), r2.text
    assert await hb.charges_of(db, s["cu"]["id"]) == []


async def test_expiry_cancel_never_charges(db, monkeypatch):
    h.install_rzp_stub(monkeypatch)
    s = await hb.staffed_center(db)
    b = await hb.new_booking(db, s["cu"], s["when"], s["keys"][0], method="online")
    assert b["status"] == "awaiting_payment"
    await hb.slot_in(db, b["id"], 30)
    out = await BookingService(db).cancel_booking(
        b["id"], hb.cancel("Payment wasn't completed in time"), "system", "admin", only_if_unpaid=True,
    )
    assert out["status"] == "cancelled"
    assert await hb.charges_of(db, s["cu"]["id"]) == []


async def test_double_tapped_cancel_creates_one_charge(db):
    for _ in range(4):
        s = await hb.staffed_center(db)
        b = await hb.new_booking(db, s["cu"], s["when"], s["keys"][0])
        await hb.slot_in(db, b["id"], 30)
        svc = BookingService(db)
        results = await asyncio.gather(*(
            svc.cancel_booking(b["id"], hb.cancel(at_customer_request=True), s["mgr"]["id"], "manager", s["center_id"]) for _ in range(3)
        ), return_exceptions=True)
        assert sum(1 for r in results if isinstance(r, dict)) == 1, results
        assert len(await hb.charges_of(db, s["cu"]["id"])) == 1


async def test_visit_cancel_charges_once_partial_cancel_never(db):
    s = await hb.staffed_center(db)
    async with h.client() as c:
        gid, ids = await h.group(c, db, s["cu"], s["when"], s["keys"][0], cars=3)
    await hb.slot_in(db, ids, 2 * 60)
    svc = BookingService(db)
    # One car of the three, the rest goes ahead: no charge — and naming one is refused.
    with pytest.raises(BadRequestException, match="whole visit"):
        await svc.cancel_booking(ids[2], hb.cancel(at_customer_request=True, charge_amount=50), s["mgr"]["id"], "manager", s["center_id"])
    await svc.cancel_booking(ids[2], hb.cancel(at_customer_request=True), s["mgr"]["id"], "manager", s["center_id"])
    assert await hb.charges_of(db, s["cu"]["id"]) == []
    # The preview agrees: this car alone is free, the whole visit isn't.
    single = await svc.cancellation_charge_preview(ids[0], s["mgr"]["id"], "manager", s["center_id"])
    whole = await svc.cancellation_charge_preview(ids[0], s["mgr"]["id"], "manager", s["center_id"], whole_visit=True)
    assert single["amount"] == 0 and single["ends_visit"] is False
    assert whole["amount"] == 50 and whole["ends_visit"] is True and whole["tier"] == "1_to_4h"
    out = await svc.cancel_booking_group(gid, hb.cancel(at_customer_request=True), s["mgr"]["id"], "manager", s["center_id"])
    assert out["cancelled_count"] == 2 and out["late_cancellation_charge"]["amount"] == 50
    [charge] = await hb.charges_of(db, s["cu"]["id"])
    assert charge["visit_key"] == gid and set(charge["source_booking_ids"]) == set(ids[:2])


async def test_last_car_of_a_visit_cancelled_alone_is_charged(db):
    s = await hb.staffed_center(db)
    async with h.client() as c:
        gid, ids = await h.group(c, db, s["cu"], s["when"], s["keys"][0], cars=2)
    await hb.slot_in(db, ids, 30)
    svc = BookingService(db)
    await svc.cancel_booking(ids[0], hb.cancel("Customer sold one car"), s["mgr"]["id"], "manager", s["center_id"])
    await svc.cancel_booking(ids[1], hb.cancel(at_customer_request=True), s["mgr"]["id"], "manager", s["center_id"])
    [charge] = await hb.charges_of(db, s["cu"]["id"])
    assert charge["tier"] == "under_1h" and charge["amount"] == 80 and charge["visit_key"] == gid


# ---------------------------------------------------------------- carried into the next booking


async def _open_charge(db, s: dict, minutes: float = 2 * 60, slot_index: int = 0) -> dict:
    """A late cancel: the ₹50 charge row (settled through the wallet)."""
    b = await hb.new_booking(db, s["cu"], s["when"], s["keys"][slot_index])
    await hb.slot_in(db, b["id"], minutes)
    await BookingService(db).cancel_booking(b["id"], hb.cancel(at_customer_request=True), s["mgr"]["id"], "manager", s["center_id"])
    return (await hb.charges_of(db, s["cu"]["id"]))[-1]


async def test_next_app_booking_carries_the_charge_quote_and_total_agree(db):
    s = await hb.staffed_center(db)
    plain = await hb.new_booking(db, await h.customer(db, s["pin"]), s["when"], s["keys"][1])  # same service, no charge
    charge = await _open_charge(db, s)
    hatch = await get_hatchback_type_id(db)
    star = await get_star_wash_service_id(db)
    async with h.client() as c:
        q = await c.post("/api/v1/bookings/quote", json={"lines": [{"vehicle_type": hatch, "quantity": 1, "service_ids": [star]}],
                                                       "address_id": s["cu"]["address_id"], "scheduled_date": s["when"]}, headers=s["cu"]["h"])
        assert q.status_code == 200, q.text
        quote = q.json()["data"]
        assert quote["previous_balance_due"] == 50 and quote["first_time_confirmed"] is True
        r = await h.book(c, db, s["cu"], s["when"], s["keys"][2], expected_total=quote["total_amount"])
        assert r.status_code == 200, r.text
    made = r.json()["data"]
    doc = await hb.booking(db, made["id"])
    assert doc["total_amount"] == quote["total_amount"] == pytest.approx(plain["total_amount"] + 50)
    assert doc["wallet_due_carried"] == 50
    assert doc["platform_earning"] == pytest.approx(plain["platform_earning"] + 50)
    assert doc["captain_earning"] == plain["captain_earning"]  # never the captain's money
    assert (await db.customer_charges.find_one({"_id": charge["_id"]}))["status"] == "settled"
    # Carried once: the booking after that pays the plain price.
    async with h.client() as c:
        r2 = await h.book(c, db, s["cu"], h.day(3), s["keys"][0])
    assert float((await hb.booking(db, r2.json()["data"]["id"])).get("wallet_due_carried") or 0) == 0
    assert await _balance(db, s["cu"]["id"]) == -50  # still owed until the carrier is paid


async def test_quick_booking_after_otp_reports_the_charge_then_books_it(db):
    s = await hb.staffed_center(db)
    await _open_charge(db, s)
    from app.services.auth_service import AuthService

    phone = (await db.users.find_one({"_id": ObjectId(s["cu"]["id"])}))["phone"]
    hatch = await get_hatchback_type_id(db)
    star = await get_star_wash_service_id(db)
    line = {"vehicle_type": hatch, "quantity": 1, "service_ids": [star]}
    async with h.client() as c:
        anon = (await c.post("/api/v1/bookings/quote", json={"lines": [line], "customer_phone": phone})).json()["data"]
        assert float(anon.get("previous_balance_due") or 0) == 0 and anon["first_time_confirmed"] is False  # a stranger's quote reveals nothing
        await AuthService(db).request_phone_otp(phone)
        code = (await db.otp_requests.find_one({"identifier": phone}))["otp"]
        body = {"customer_name": "Late Canceller", "customer_phone": phone, "address_id": s["cu"]["address_id"], "lines": [line],
                "scheduled_date": s["when"], "scheduled_slot": s["keys"][2], "phone_otp": code, "expected_total": anon["total_amount"]}
        r = await c.post("/api/v1/bookings/quick", json=body)
        assert r.status_code == 409, r.text
        err = r.json()
        assert err["error_code"] == "PRICE_CHANGED" and err["details"]["previous_balance_due"] == 50
        assert err["details"]["total_amount"] == pytest.approx(anon["total_amount"] + 50)
        await AuthService(db).request_phone_otp(phone)
        code = (await db.otp_requests.find_one({"identifier": phone}, sort=[("last_sent_at", -1)]))["otp"]
        r = await c.post("/api/v1/bookings/quick", json={**body, "phone_otp": code, "expected_total": err["details"]["total_amount"]})
        assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert data["total_amount"] == err["details"]["total_amount"]
    made = await db.bookings.find_one({"customer_id": s["cu"]["id"], "scheduled_slot": s["keys"][2], "status": {"$ne": "cancelled"}})
    assert made["wallet_due_carried"] == 50


async def test_manager_booking_on_behalf_carries_the_charge(db):
    s = await hb.staffed_center(db)
    await _open_charge(db, s)
    phone = (await db.users.find_one({"_id": ObjectId(s["cu"]["id"])}))["phone"]
    hatch = await get_hatchback_type_id(db)
    star = await get_star_wash_service_id(db)
    async with h.client() as c:
        r = await c.post("/api/v1/bookings/manager-quick", json={
            "customer_name": "On Behalf", "customer_phone": phone, "address_id": s["cu"]["address_id"],
            "lines": [{"vehicle_type": hatch, "quantity": 1, "service_ids": [star]}],
            "scheduled_date": s["when"], "scheduled_slot": s["keys"][2], "payment_method": "cash",
        }, headers=s["mgr"]["h"])
    assert r.status_code == 200, r.text
    made = await db.bookings.find_one({"customer_id": s["cu"]["id"], "scheduled_slot": s["keys"][2], "status": {"$ne": "cancelled"}})
    assert made["wallet_due_carried"] == 50
    assert (await _summary(db, s["cu"]["id"]))["previous_balance_due"] == 0


async def test_logged_done_job_never_takes_a_charge(db):
    s = await hb.staffed_center(db)
    await _open_charge(db, s)
    from app.schemas.booking_schema import ManagerLogBookingRequest, QuickBookingLine

    phone = (await db.users.find_one({"_id": ObjectId(s["cu"]["id"])}))["phone"]
    yesterday = (now_ist() - timedelta(days=1)).strftime("%Y-%m-%d")
    out = await BookingService(db).create_manager_logged_visit(ManagerLogBookingRequest(
        customer_name="Logged", customer_phone=phone, address_line="Log Lane 1",
        lines=[QuickBookingLine(vehicle_type=await get_hatchback_type_id(db), quantity=1, service_ids=[await get_star_wash_service_id(db)])],
        scheduled_date=yesterday, service_time="10:00", send_whatsapp=False,
    ), manager_id=s["mgr"]["id"], manager_center_id=s["center_id"])
    assert float(out["bookings"][0].get("wallet_due_carried") or 0) == 0
    assert (await _summary(db, s["cu"]["id"]))["previous_balance_due"] == 50


async def _pass_for(db, customer_id: str) -> str:
    star = await get_star_wash_service_id(db)
    plan_id = await make_subscription_plan(db, vehicle_types=[], included_service_ids=[star], total_service_count=4)
    sub = await UserSubscriptionService(db).subscribe(
        customer_id, SubscribeRequest(plan_id=plan_id, vehicle_type=await get_hatchback_type_id(db), service_id=star)
    )
    return sub["id"]


async def test_plan_covered_booking_becomes_payable_for_the_charge(db):
    s = await hb.staffed_center(db)
    await _open_charge(db, s)
    sub_id = await _pass_for(db, s["cu"]["id"])
    cash = await hb.new_booking(db, s["cu"], s["when"], s["keys"][2], subscription_id=sub_id)
    doc = await hb.booking(db, cash["id"])
    assert doc["payment_method"] == "subscription" and doc["subtotal"] > 0
    assert doc["total_amount"] == doc["wallet_due_carried"] == 50
    assert doc["payment_status"] == "pending" and doc["status"] == "pending"  # cash after the wash
    # The plan wash's carried balance is not a plan extra: cash stays allowed.
    assert not BookingService._plan_extras_online_only(BookingService._plan_rows([doc]))


async def test_plan_covered_booking_paying_the_charge_online_waits_for_payment(db):
    s = await hb.staffed_center(db)
    await _open_charge(db, s)
    sub_id = await _pass_for(db, s["cu"]["id"])
    online = await hb.new_booking(db, s["cu"], s["when"], s["keys"][2], subscription_id=sub_id, method="online")
    doc = await hb.booking(db, online["id"])
    assert doc["status"] == "awaiting_payment" and doc["total_amount"] == 50 and doc["payment_status"] == "pending"


async def test_visit_charge_rides_on_the_first_paying_car(db):
    s = await hb.staffed_center(db)
    await _open_charge(db, s)
    sub_id = await _pass_for(db, s["cu"]["id"])
    hatch = await get_hatchback_type_id(db)
    star = await get_star_wash_service_id(db)
    visit = await BookingService(db).create_booking_group(s["cu"]["id"], BookingGroupCreateRequest(
        vehicles=[GroupVehicleRequest(vehicle_type=hatch, service_ids=[star], subscription_id=sub_id),
                  GroupVehicleRequest(vehicle_type=hatch, service_ids=[star])],
        address_id=s["cu"]["address_id"], scheduled_date=s["when"], scheduled_slot=s["keys"][2], payment_method="cash",
    ))
    plan_car, paying_car = sorted([await hb.booking(db, b["id"]) for b in visit["bookings"]], key=lambda c: c["group_offset_minutes"])
    assert float(plan_car.get("wallet_due_carried") or 0) == 0 and plan_car["total_amount"] == 0
    assert paying_car["wallet_due_carried"] == 50


async def test_two_concurrent_bookings_apply_the_charge_once(db):
    for _ in range(3):
        s = await hb.staffed_center(db)
        await _open_charge(db, s)
        results = await asyncio.gather(
            hb.new_booking(db, s["cu"], s["when"], s["keys"][1]),
            hb.new_booking(db, s["cu"], s["when"], s["keys"][2]),
            return_exceptions=True,
        )
        made = [r for r in results if isinstance(r, dict)]
        assert len(made) == 2, results
        carried = [float((await hb.booking(db, r["id"])).get("wallet_due_carried") or 0) for r in made]
        assert sorted(carried) == [0, 50], carried
        assert (await _summary(db, s["cu"]["id"]))["carried_due"] == 50


async def test_failed_create_leaves_the_charge_open(db, monkeypatch):
    s = await hb.staffed_center(db)
    await _open_charge(db, s)
    sub_id = await _pass_for(db, s["cu"]["id"])
    svc = BookingService(db)

    async def pass_ran_out(*a, **k):
        raise BadRequestException("This pass has no washes left.")

    monkeypatch.setattr(svc.subscription_service, "commit_consumption", pass_ran_out)
    with pytest.raises(BadRequestException):
        await svc.create_booking(s["cu"]["id"], BookingCreateRequest(
            vehicle_type=await get_hatchback_type_id(db), address_id=s["cu"]["address_id"], service_ids=[await get_star_wash_service_id(db)],
            scheduled_date=s["when"], scheduled_slot=s["keys"][2], payment_method="cash", subscription_id=sub_id,
        ), _allow_pinless=True)
    summary = await _summary(db, s["cu"]["id"])
    assert summary["balance"] == -50 and summary["previous_balance_due"] == 50 and summary["carried_due"] == 0


async def test_cancelling_the_carrying_booking_puts_the_charge_back(db):
    s = await hb.staffed_center(db)
    await _open_charge(db, s)
    carrier = await hb.new_booking(db, s["cu"], s["when"], s["keys"][1])
    assert (await hb.booking(db, carrier["id"]))["wallet_due_carried"] == 50
    # Cancelled more than 4 h before: free for it, and the ₹50 it carried goes back.
    await BookingService(db).cancel_booking(carrier["id"], hb.cancel("Plans changed"), s["cu"]["id"], "customer")
    summary = await _summary(db, s["cu"]["id"])
    assert summary["balance"] == -50 and summary["previous_balance_due"] == 50
    nxt = await hb.new_booking(db, s["cu"], s["when"], s["keys"][2])
    assert (await hb.booking(db, nxt["id"]))["wallet_due_carried"] == 50


async def _owed(db, customer_id: str) -> float:
    """What the customer owes in all: debt not yet on a booking + debt riding
    on live bookings that aren't paid yet."""
    rows = await db.bookings.find({"customer_id": customer_id, "is_deleted": {"$ne": True}, "status": {"$ne": "cancelled"},
                                   "wallet_due_cleared": {"$ne": True}}).to_list(None)
    return (await _summary(db, customer_id))["previous_balance_due"] + sum(float(r.get("wallet_due_carried") or 0) for r in rows)


async def test_soft_delete_releases_and_restore_reclaims(db):
    # Wallet model: whatever delete/restore do with the carried balance, the
    # customer owes the ₹50 exactly once throughout.
    s = await hb.staffed_center(db)
    await _open_charge(db, s)
    carrier = await hb.new_booking(db, s["cu"], s["when"], s["keys"][1])
    adm = await h.admin(db)
    svc = BookingService(db)
    assert await _owed(db, s["cu"]["id"]) == 50
    await svc.soft_delete_booking(carrier["id"], adm["id"])
    await svc.restore_booking(carrier["id"])
    assert await _owed(db, s["cu"]["id"]) == 50
    assert await _balance(db, s["cu"]["id"]) == -50


async def test_restore_never_charges_twice(db):
    s = await hb.staffed_center(db)
    await _open_charge(db, s)
    carrier = await hb.new_booking(db, s["cu"], s["when"], s["keys"][1])
    adm = await h.admin(db)
    svc = BookingService(db)
    await svc.soft_delete_booking(carrier["id"], adm["id"])
    await hb.new_booking(db, s["cu"], s["when"], s["keys"][2])  # may carry the debt meanwhile
    await svc.restore_booking(carrier["id"])
    assert await _owed(db, s["cu"]["id"]) == 50


# ---------------------------------------------------------------- reduce / waive


async def test_adjust_reduce_and_waive_open_charge_with_authz(db):
    s = await hb.staffed_center(db)
    charge = await _open_charge(db, s)
    other_center, _pin = await h.center(db)
    stranger = await h.manager(db, other_center)
    cid = str(charge["_id"])
    async with h.client() as c:
        assert (await c.post(f"/api/v1/charges/{cid}/adjust", json={"amount": 10}, headers=stranger["h"])).status_code == 404
        assert (await c.post(f"/api/v1/charges/{cid}/adjust", json={"amount": 10}, headers=s["cu"]["h"])).status_code == 403
        up = await c.post(f"/api/v1/charges/{cid}/adjust", json={"amount": 60}, headers=s["mgr"]["h"])
        assert up.status_code == 400 and "only be reduced" in up.json()["message"]
        r = await c.post(f"/api/v1/charges/{cid}/adjust", json={"amount": 20, "note": "Regular customer"}, headers=s["mgr"]["h"])
        assert r.status_code == 200, r.text
        assert r.json()["data"]["amount"] == 20 and r.json()["data"]["status"] == "settled"
        assert await _balance(db, s["cu"]["id"]) == -20  # the ₹30 reduction credited back
        r = await c.post(f"/api/v1/charges/{cid}/adjust", json={"amount": 0}, headers=(await h.admin(db))["h"])
        assert r.status_code == 200 and r.json()["data"]["status"] == "waived"
        again = await c.post(f"/api/v1/charges/{cid}/adjust", json={"amount": 0}, headers=s["mgr"]["h"])
        assert again.status_code == 400
    assert await _balance(db, s["cu"]["id"]) == 0
    row = await db.customer_charges.find_one({"_id": charge["_id"]})
    assert [(x["action"], x["from"], x["to"]) for x in row["history"][1:]] == [("reduced", 50, 20), ("waived", 20, 0)]
    assert row["history"][1]["note"] == "Regular customer" and row["history"][1]["by"] == s["mgr"]["id"]
    audits = await db.audit_logs.find({"action": "ADJUST_CUSTOMER_CHARGE", "target_id": cid}).to_list(None)
    assert [(a["details"]["before"]["amount"], a["details"]["after"]["amount"]) for a in audits] == [(50, 20), (20, 0)]
    assert await db.notifications.count_documents({"user_id": s["cu"]["id"], "title": "Wallet Credited"}) == 2
    # A waived charge is not added to the next booking.
    nxt = await hb.new_booking(db, s["cu"], s["when"], s["keys"][2])
    assert float((await hb.booking(db, nxt["id"])).get("wallet_due_carried") or 0) == 0


async def test_adjust_applied_unpaid_booking_lowers_total_and_voids_links(db, monkeypatch):
    # Wallet model: the debt rides on the unpaid booking (wallet_due_carried);
    # reducing the charge lowers THAT booking's price in the same
    # transaction and voids the link made for the old amount.
    stub = h.install_rzp_stub(monkeypatch)
    s = await hb.staffed_center(db)
    charge = await _open_charge(db, s)
    carrier = await hb.new_booking(db, s["cu"], s["when"], s["keys"][1], source="whatsapp", apply_charges=True)
    raw = await hb.booking(db, carrier["id"])
    assert raw["wallet_due_carried"] == 50
    await PaymentService(db).create_payment_link(raw, contact_phone="9876500011", name="Test")
    link = await db.payment_orders.find_one({"kind": "link", "booking_id": carrier["id"], "status": "created"})
    out = await CustomerChargeService(db).adjust(
        str(charge["_id"]), 20, "Goodwill", actor_id=s["mgr"]["id"], actor_role="manager", actor_center_id=s["center_id"],
    )
    assert out["amount"] == 20 and out["status"] == "settled"
    doc = await hb.booking(db, carrier["id"])
    assert doc["wallet_due_carried"] == 20
    assert doc["total_amount"] == pytest.approx(raw["total_amount"] - 30)
    assert doc["platform_earning"] == pytest.approx(raw["platform_earning"] - 30)
    assert doc["captain_earning"] == raw["captain_earning"]
    assert doc["amount_due"] == pytest.approx(raw["total_amount"] - 30)
    summary = await _summary(db, s["cu"]["id"])
    assert summary["balance"] == -20 and summary["carried_due"] == 20 and summary["previous_balance_due"] == 0
    assert (await db.payment_orders.find_one({"_id": link["_id"]}))["status"] == "voided"
    assert link["razorpay_link_id"] in stub.payment_link.cancelled


async def test_adjust_on_a_paid_booking_points_to_a_refund(db):
    # Wallet model: a reduction after the carrier was paid is simply wallet
    # credit — no refund case any more.
    from app.services.money_service import MoneyService

    s = await hb.staffed_center(db)
    charge = await _open_charge(db, s)
    carrier = await hb.new_booking(db, s["cu"], s["when"], s["keys"][1])
    raw = await hb.booking(db, carrier["id"])
    await MoneyService(db).apply_payment([carrier["id"]], raw["total_amount"], method="online", key=f"t:{carrier['id']}")
    assert await _balance(db, s["cu"]["id"]) == 0
    await CustomerChargeService(db).adjust(
        str(charge["_id"]), 0, None, actor_id=s["mgr"]["id"], actor_role="manager", actor_center_id=s["center_id"],
    )
    assert await _balance(db, s["cu"]["id"]) == 50
    assert (await db.customer_charges.find_one({"_id": charge["_id"]}))["status"] == "waived"


async def test_waiving_the_only_cost_of_an_unpaid_plan_booking_confirms_it(db):
    s = await hb.staffed_center(db)
    charge = await _open_charge(db, s)
    sub_id = await _pass_for(db, s["cu"]["id"])
    online = await hb.new_booking(db, s["cu"], s["when"], s["keys"][2], subscription_id=sub_id, method="online")
    assert (await hb.booking(db, online["id"]))["status"] == "awaiting_payment"
    await CustomerChargeService(db).adjust(str(charge["_id"]), 0, None, actor_id=s["mgr"]["id"], actor_role="manager", actor_center_id=s["center_id"])
    doc = await hb.booking(db, online["id"])
    assert doc["total_amount"] == 0 and doc["payment_status"] == "paid" and doc["status"] == "pending"
    summary = await _summary(db, s["cu"]["id"])
    assert summary["balance"] == 0 and summary["carried_due"] == 0  # no debt, no stray credit


# ---------------------------------------------------------------- the captain's money


async def test_captain_completion_charge_goes_to_the_platform(db):
    s = await hb.staffed_center(db)
    plain = await hb.new_booking(db, await h.customer(db, s["pin"]), s["when"], s["keys"][2], source="whatsapp")
    await _open_charge(db, s)
    carrier = await hb.new_booking(db, s["cu"], s["when"], s["keys"][1], source="whatsapp", apply_charges=True)
    assert (await hb.booking(db, carrier["id"]))["wallet_due_carried"] == 50
    started = datetime.now(timezone.utc) - timedelta(minutes=30)
    await db.bookings.update_one({"_id": ObjectId(carrier["id"])}, {"$set": {
        "captain_id": s["cap"]["id"], "status": "service_started", "service_started_at": started, "vehicle_verified": True,
    }})
    before = await hb.wallet(db, s["cap"]["id"])
    await BookingService(db).capture_after_photo_and_complete(carrier["id"], await hb.snap(db, s["cap"]["id"], "after"), s["cap"]["id"])
    # Spec 1.5: completion credits his earning; collecting the cash debits it all.
    await PaymentService(db).captain_collect_cash(carrier["id"], s["cap"]["id"])
    doc = await hb.booking(db, carrier["id"])
    assert doc["captain_earning"] == plain["captain_earning"]  # unchanged by the charge
    # Cash: the captain holds the whole total, the platform's share — charge included — comes out of his wallet.
    assert await hb.wallet(db, s["cap"]["id"]) == pytest.approx(before - (doc["total_amount"] - doc["captain_earning"]))
    assert doc["total_amount"] - doc["captain_earning"] == pytest.approx(plain["total_amount"] - plain["captain_earning"] + 50)
    assert await _balance(db, s["cu"]["id"]) == 0  # the carried debt was paid with it


# ---------------------------------------------------------------- lists, preview, policy


async def test_charge_lists_are_scoped(db):
    s = await hb.staffed_center(db)
    charge = await _open_charge(db, s)
    other_center, _pin = await h.center(db)
    stranger = await h.manager(db, other_center)
    adm = await h.admin(db)
    async with h.client() as c:
        mine = (await c.get("/api/v1/charges", headers=s["mgr"]["h"])).json()
        theirs = (await c.get("/api/v1/charges", headers=stranger["h"])).json()
        everyone = (await c.get(f"/api/v1/charges?customer_id={s['cu']['id']}", headers=adm["h"])).json()
        cust = await c.get("/api/v1/charges", headers=s["cu"]["h"])
        my = (await c.get("/api/v1/charges/my", headers=s["cu"]["h"])).json()["data"]
        settled = (await c.get("/api/v1/charges?status=settled", headers=s["mgr"]["h"])).json()
    row = next(x for x in mine["data"] if x["id"] == str(charge["_id"]))
    assert row["customer_name"] and row["customer_phone"] and row["created_by_name"] and row["history"]
    assert row["applied_booking_paid"] is False and row["source_booking_number"]
    assert all(x["service_center_id"] == s["center_id"] for x in mine["data"]) and mine["meta"]["total"] >= 1
    assert not any(x["id"] == str(charge["_id"]) for x in theirs["data"])
    assert [x["id"] for x in everyone["data"]] == [str(charge["_id"])]
    assert any(x["id"] == str(charge["_id"]) for x in settled["data"])
    assert cust.status_code == 403
    assert my["items"][0]["amount"] == 50 and my["items"][0]["status"] == "settled"


async def test_preview_endpoint_authz_and_amount(db):
    s = await hb.staffed_center(db)
    b = await hb.new_booking(db, s["cu"], s["when"], s["keys"][0])
    await hb.slot_in(db, b["id"], 45)
    other_center, other_pin = await h.center(db)
    stranger = await h.manager(db, other_center)
    someone = await h.customer(db, other_pin)
    async with h.client() as c:
        own = await c.get(f"/api/v1/bookings/{b['id']}/cancellation-charge-preview", headers=s["cu"]["h"])
        mgr = await c.get(f"/api/v1/bookings/{b['id']}/cancellation-charge-preview", headers=s["mgr"]["h"])
        other_mgr = await c.get(f"/api/v1/bookings/{b['id']}/cancellation-charge-preview", headers=stranger["h"])
        other_cust = await c.get(f"/api/v1/bookings/{b['id']}/cancellation-charge-preview", headers=someone["h"])
    assert own.status_code == mgr.status_code == 200
    data = mgr.json()["data"]
    assert data["tier"] == "under_1h" and data["amount"] == 80 and data["captain_left"] is False
    assert 40 <= data["minutes_to_slot"] <= 45 and data["window_hours"] == 4 and data["ends_visit"] is True
    assert other_mgr.status_code == 403 and other_cust.status_code == 404


async def test_policy_endpoint_exposes_and_validates_the_amounts(db):
    adm = await h.admin(db)
    async with h.client() as c:
        policy = (await c.get("/api/v1/booking-policy")).json()["data"]
        assert (policy["cancellation_fee_1_to_4h"], policy["cancellation_fee_under_1h"], policy["cancellation_fee_after_captain_left"]) == (50, 80, 100)
        assert (await c.put("/api/v1/booking-policy", json={"cancellation_fee_under_1h": 2001}, headers=adm["h"])).status_code == 422
        assert (await c.put("/api/v1/booking-policy", json={"cancellation_fee_under_1h": -1}, headers=adm["h"])).status_code == 422
        r = await c.put("/api/v1/booking-policy", json={"cancellation_fee_under_1h": 90}, headers=adm["h"])
        assert r.status_code == 200 and r.json()["data"]["cancellation_fee_under_1h"] == 90
        audit = await db.audit_logs.find_one({"action": "UPDATE_BOOKING_POLICY", "details.after.cancellation_fee_under_1h": 90})
        assert audit and audit["details"]["before"]["cancellation_fee_under_1h"] == 80
        await c.put("/api/v1/booking-policy", json={"cancellation_fee_under_1h": 80}, headers=adm["h"])
