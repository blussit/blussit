"""Core fix round 2 — the rest of the booking-side audit items (2026-10-07):
MGR-01 log-a-done-job plan use, MGR-06 future mark-done, ADM-11 permanent
delete vs captain money, FAIL-04 post-commit failures, NTF-06 stale captain
reminders, PRICE-05/06 coupon + distance charge on the paying car, inactive
staff, DUPLICATE_BOOKING, the thank-you ticket, manager discount/tip
visibility, the delete script, the WhatsApp bot quote, and the captain
re-check inside the assignment transaction."""
import itertools
import random
from datetime import datetime, timedelta, timezone

import pytest
from bson import ObjectId

from app.core.dependencies import CurrentUser
from app.core.exceptions import BadRequestException
from app.schemas.booking_schema import (
    BookingAssignCaptainRequest,
    BookingCancelRequest,
    BookingGroupCreateRequest,
    BookingQuoteRequest,
    GroupVehicleRequest,
    ManagerLogBookingRequest,
    QuickBookingLine,
)
from app.schemas.subscription_schema import SubscribeRequest
from app.services.booking_service import BookingService
from app.services.notification_service import NotificationService
from app.services.subscription_service import UserSubscriptionService
from app.utils.timezone import now_ist
from tests import test_fix_core_helpers as h
from tests import test_fix_coreb_helpers as hb
from tests.factories import get_hatchback_type_id, get_star_wash_service_id, make_subscription_plan

pytestmark = pytest.mark.asyncio
_seq = itertools.count(random.randint(1000, 90000))


@pytest.fixture
def sent(monkeypatch):
    calls: list[dict] = []
    real = NotificationService.notify

    async def spy(self, user_id, title, message, *args, **kwargs):
        calls.append({"user_id": user_id, "title": title, "message": message, **kwargs})
        return await real(self, user_id, title, message, *args, **kwargs)

    monkeypatch.setattr(NotificationService, "notify", spy)
    return calls


async def _phone(db, customer_id: str) -> str:
    return (await db.users.find_one({"_id": ObjectId(customer_id)}))["phone"]


async def _type_pass(db, customer_id: str, *, center_id: str | None = None, start_days_ago: int = 2) -> str:
    star = await get_star_wash_service_id(db)
    plan_id = await make_subscription_plan(db, vehicle_types=[], included_service_ids=[star], total_service_count=4)
    sub = await UserSubscriptionService(db).subscribe(
        customer_id, SubscribeRequest(plan_id=plan_id, vehicle_type=await get_hatchback_type_id(db), service_id=star)
    )
    await db.user_subscriptions.update_one({"_id": ObjectId(sub["id"])}, {"$set": {
        "start_date": datetime.now(timezone.utc) - timedelta(days=start_days_ago), "service_center_id": center_id,
    }})
    return sub["id"]


_minute = itertools.count(0)


async def _log(db, s: dict, *, use_subscription=None, send_whatsapp=False, discount=0, tip=0, controller=False):
    """A "Log A Done Job" for yesterday (a different minute each time — the
    same customer at the same minute is refused as a double log)."""
    line = {"vehicle_type": await get_hatchback_type_id(db), "quantity": 1, "service_ids": [await get_star_wash_service_id(db)]}
    if use_subscription is not None:
        line["use_subscription"] = use_subscription
    m = next(_minute) % 420
    payload = ManagerLogBookingRequest(
        customer_name="Logged Customer", customer_phone=await _phone(db, s["cu"]["id"]), address_line=f"Log Lane {next(_seq)}",
        lines=[QuickBookingLine(**line)], scheduled_date=(now_ist() - timedelta(days=1)).strftime("%Y-%m-%d"),
        service_time=f"{10 + m // 60:02d}:{m % 60:02d}", send_whatsapp=send_whatsapp, discount_amount=discount, tip_amount=tip,
    )
    if controller:
        from app.controllers.booking_controller import BookingController

        manager = CurrentUser(s["mgr"]["id"], "manager", service_center_id=s["center_id"])
        return (await BookingController(db).manager_log_completed(manager, payload))["data"]
    return await BookingService(db).create_manager_logged_visit(payload, manager_id=s["mgr"]["id"], manager_center_id=s["center_id"])


# ---------------------------------------------------------------- MGR-01


async def test_mgr01_logged_job_uses_a_pass_only_when_asked(db, sent):
    s = await hb.staffed_center(db)
    sub_id = await _type_pass(db, s["cu"]["id"])
    silent = await _log(db, s)  # the line says nothing about the plan
    assert silent["bookings"][0]["subscription_id"] is None and silent["total_amount"] > 0
    assert (await db.user_subscriptions.find_one({"_id": ObjectId(sub_id)}))["remaining_service_count"] == 4
    asked = await _log(db, s, use_subscription=True, send_whatsapp=False)
    assert asked["bookings"][0]["subscription_id"] == sub_id and asked["total_amount"] == 0
    assert (await db.user_subscriptions.find_one({"_id": ObjectId(sub_id)}))["remaining_service_count"] == 3
    # The customer is ALWAYS told a wash came off the pass — WhatsApp too, switch or not.
    done = [x for x in sent if x["user_id"] == s["cu"]["id"] and x["title"] == "Service completed"]
    assert done and done[-1]["send_whatsapp"] is True and "Booked from your" in done[-1]["message"]


async def test_mgr01_another_centers_pass_is_never_spent_by_a_log(db):
    s = await hb.staffed_center(db)
    other_center, _pin = await h.center(db)
    sub_id = await _type_pass(db, s["cu"]["id"], center_id=other_center)
    out = await _log(db, s, use_subscription=True)
    assert out["bookings"][0]["subscription_id"] is None and out["total_amount"] > 0
    assert (await db.user_subscriptions.find_one({"_id": ObjectId(sub_id)}))["remaining_service_count"] == 4
    # ...and the log quote says the same.
    from app.controllers.booking_controller import BookingController

    quote = (await BookingController(db).quote(
        CurrentUser(s["mgr"]["id"], "manager", service_center_id=s["center_id"]),
        BookingQuoteRequest(customer_phone=await _phone(db, s["cu"]["id"]), mode="log",
                            scheduled_date=(now_ist() - timedelta(days=1)).strftime("%Y-%m-%d"), service_time="10:30",
                            lines=[{"vehicle_type": await get_hatchback_type_id(db), "quantity": 1,
                                    "service_ids": [await get_star_wash_service_id(db)], "use_subscription": True}]),
    ))["data"]
    assert quote["lines"][0]["plan_covered"] == 0 and quote["total_amount"] == out["total_amount"]
    # The same pass as one this center sold, asked for: used.
    await db.user_subscriptions.update_one({"_id": ObjectId(sub_id)}, {"$set": {"service_center_id": s["center_id"]}})
    used = await _log(db, s, use_subscription=True)
    assert used["bookings"][0]["subscription_id"] == sub_id


# ---------------------------------------------------------------- MGR-06


async def test_mgr06_mark_done_refuses_a_future_booking(db):
    s = await hb.staffed_center(db)
    b = await hb.new_booking(db, s["cu"], s["when"], s["keys"][0])  # two days out
    svc = BookingService(db)
    with pytest.raises(BadRequestException, match="later date"):
        await svc.manager_mark_done(b["id"], s["mgr"]["id"], "manager", s["center_id"], send_whatsapp=False)
    assert (await hb.booking(db, b["id"]))["status"] == "pending"
    today = now_ist().strftime("%Y-%m-%d")
    await db.bookings.update_one({"_id": ObjectId(b["id"])}, {"$set": {"seat_key.date": today}})
    out = await svc.manager_mark_done(b["id"], s["mgr"]["id"], "manager", s["center_id"], send_whatsapp=False)
    assert out["completed"] == 1


# ---------------------------------------------------------------- ADM-11


async def test_adm11_permanent_delete_never_orphans_captain_money(db):
    s = await hb.staffed_center(db)
    b = await hb.new_booking(db, s["cu"], s["when"], s["keys"][0])
    await db.bookings.update_one({"_id": ObjectId(b["id"])}, {"$set": {
        "captain_id": s["cap"]["id"], "status": "service_started", "vehicle_verified": True,
        "service_started_at": datetime.now(timezone.utc) - timedelta(minutes=30),
    }})
    svc = BookingService(db)
    await svc.capture_after_photo_and_complete(b["id"], await hb.snap(db, s["cap"]["id"]), s["cap"]["id"])
    assert await db.wallet_transactions.count_documents({"booking_id": b["id"]}) == 1
    adm = await h.admin(db)
    await svc.soft_delete_booking(b["id"], adm["id"])
    for force in (False, True):
        with pytest.raises(BadRequestException, match="wallet transaction"):
            await svc.permanently_delete_booking(b["id"], force=force)
    assert await db.bookings.find_one({"_id": ObjectId(b["id"])})
    assert await db.wallet_transactions.count_documents({"booking_id": b["id"]}) == 1
    # No captain money: force still deletes.
    b2 = await hb.new_booking(db, s["cu"], s["when"], s["keys"][1])
    await svc.soft_delete_booking(b2["id"], adm["id"])
    assert (await svc.permanently_delete_booking(b2["id"], force=True))["deleted_count"] == 1


# ---------------------------------------------------------------- FAIL-04


async def test_fail04_a_blip_after_commit_still_returns_the_booking(db, monkeypatch):
    s = await hb.staffed_center(db)

    async def blip(*a, **k):
        raise RuntimeError("connection reset by peer")

    monkeypatch.setattr(BookingService, "_record_history", blip)
    monkeypatch.setattr(BookingService, "_announce_confirmed_booking", blip)
    async with h.client() as c:
        r = await h.book(c, db, s["cu"], s["when"], s["keys"][0])
    assert r.status_code == 200, r.text
    made = r.json()["data"]
    assert (await hb.booking(db, made["id"]))["status"] == "pending"


# ---------------------------------------------------------------- NTF-06


async def _assigned_at(db, s: dict, start: datetime, slot_index: int = 0) -> str:
    b = await hb.new_booking(db, s["cu"], s["when"], s["keys"][slot_index])
    start = start.astimezone(now_ist().tzinfo)
    end = start + timedelta(hours=1)
    await db.bookings.update_one({"_id": ObjectId(b["id"])}, {"$set": {
        "status": "assigned", "captain_id": s["cap"]["id"], "reminder_sent": False,
        "scheduled_date": datetime(start.year, start.month, start.day),
        "scheduled_slot": f"{start:%H:%M}-{end:%H:%M}",
    }})
    return b["id"]


async def test_ntf06_starting_soon_is_not_sent_for_a_slot_long_started(db):
    s = await hb.staffed_center(db)
    now = now_ist()
    stale = await _assigned_at(db, s, now - timedelta(hours=2), 0)
    soon = await _assigned_at(db, s, now + timedelta(minutes=20), 1)
    just = await _assigned_at(db, s, now - timedelta(minutes=10), 2)
    due = {str(b["_id"]) for b in await BookingService(db).find_bookings_needing_reminder()}
    assert stale not in due
    assert {soon, just} <= due


# ---------------------------------------------------------------- PRICE-05 / PRICE-06


async def test_price05_06_coupon_and_distance_charge_ride_on_the_paying_car(db):
    s = await hb.staffed_center(db)
    star = await db.services.find_one({"slug": "star-wash"})
    travel_svc = {k: v for k, v in star.items() if k != "_id"}
    travel_svc.update({"name": f"Travel Wash {next(_seq)}", "slug": f"travel-wash-{next(_seq)}", "charges_travel": True, "prepaid_only": False})
    travel_id = str((await db.services.insert_one(travel_svc)).inserted_id)
    code = f"PAYCAR{next(_seq)}"
    now = datetime.now(timezone.utc)
    await db.coupons.insert_one({
        "code": code, "coupon_type": "percentage", "value": 10.0, "min_order_value": 0, "usage_limit_per_user": 5,
        "total_usage_limit": None, "total_used": 0, "valid_from": now - timedelta(days=1), "valid_until": now + timedelta(days=5),
        "offer_kind": "standard", "is_active": True, "is_deleted": False,
    })
    # The customer's address is ~11 km from the center (5 km free).
    await db.addresses.update_one({"_id": ObjectId(s["cu"]["address_id"])}, {"$set": {"latitude": 22.8, "longitude": 75.8}})
    sub_id = await _type_pass(db, s["cu"]["id"])
    hatch = await get_hatchback_type_id(db)
    cars = [GroupVehicleRequest(vehicle_type=hatch, service_ids=[str(star["_id"])], subscription_id=sub_id),
            GroupVehicleRequest(vehicle_type=hatch, service_ids=[travel_id])]
    svc = BookingService(db)
    address = await db.addresses.find_one({"_id": ObjectId(s["cu"]["address_id"])})
    quote = await svc.quote_visit(customer_id=s["cu"]["id"], phone=await _phone(db, s["cu"]["id"]), cars=cars, address=address,
                                  coupon_code=code, scheduled_date=s["when"])
    assert quote["coupon_code"] == code and quote["coupon_discount"] > 0 and quote["coupon_error"] is None
    assert quote["travel_charge"] > 0
    visit = await svc.create_booking_group(s["cu"]["id"], BookingGroupCreateRequest(
        vehicles=cars, address_id=s["cu"]["address_id"], scheduled_date=s["when"], scheduled_slot=s["keys"][0],
        payment_method="cash", coupon_code=code,
    ))
    plan_car, paying_car = sorted([await hb.booking(db, b["id"]) for b in visit["bookings"]], key=lambda c: c["group_offset_minutes"])
    assert plan_car["total_amount"] == 0 and plan_car["travel_charge"] == 0 and plan_car["coupon_code"] is None
    assert plan_car["status"] == "pending" and plan_car["payment_status"] == "paid"
    assert paying_car["coupon_code"] == code and paying_car["discount_amount"] == quote["coupon_discount"]
    assert paying_car["travel_charge"] == quote["travel_charge"]
    assert visit["total_amount"] == quote["total_amount"]
    # The captain's travel pay stays on the visit's first car (one trip).
    assert plan_car["captain_travel_pay"] >= 0 and paying_car["captain_travel_pay"] == 0
    # Every car on a plan: the coupon is refused out loud, not dropped.
    only_plan = await svc.quote_visit(customer_id=s["cu"]["id"], phone=None, cars=cars[:1], address=address, coupon_code=code,
                                      scheduled_date=s["when"])
    assert only_plan["coupon_error"] and only_plan["coupon_discount"] == 0


# ---------------------------------------------------------------- inactive staff


async def test_inactive_named_manager_hears_nothing(db):
    s = await hb.staffed_center(db)
    other = await h.manager(db, s["center_id"])
    await db.users.update_one({"_id": ObjectId(s["mgr"]["id"])}, {"$set": {"status": "inactive"}})
    recipients = await BookingService(db)._manager_recipients(s["center_id"], s["mgr"]["id"])
    assert recipients == [other["id"]]
    await db.users.update_one({"_id": ObjectId(other["id"])}, {"$set": {"status": "suspended"}})
    fallback = await BookingService(db)._manager_recipients(s["center_id"], s["mgr"]["id"])
    assert s["mgr"]["id"] not in fallback and other["id"] not in fallback  # admins instead


async def test_inactive_customer_gets_no_repeat_nudge(db):
    s = await hb.staffed_center(db)
    old = datetime.now(timezone.utc) - timedelta(days=3650)  # longest-lapsed first: at the front of the batch
    await db.users.update_one({"_id": ObjectId(s["cu"]["id"])}, {"$set": {"last_completed_at": old}})
    due = await BookingService(db).find_customers_due_repeat_reminder(7, limit=1000)
    assert s["cu"]["id"] in {str(u["_id"]) for u in due}  # control: active, they'd be nudged
    await db.users.update_one({"_id": ObjectId(s["cu"]["id"])}, {"$set": {"status": "inactive"}})
    due = await BookingService(db).find_customers_due_repeat_reminder(7, limit=1000)
    assert s["cu"]["id"] not in {str(u["_id"]) for u in due}


# ---------------------------------------------------------------- frontend contract


async def test_duplicate_booking_error_names_the_existing_booking(db):
    s = await hb.staffed_center(db)
    async with h.client() as c:
        first = await h.book(c, db, s["cu"], s["when"], s["keys"][0])
        again = await h.book(c, db, s["cu"], s["when"], s["keys"][0])
    assert again.status_code == 400
    err = again.json()
    assert err["error_code"] == "DUPLICATE_BOOKING"
    assert err["details"]["booking_id"] == first.json()["data"]["id"]
    assert err["details"]["booking_number"] == first.json()["data"]["booking_number"]


async def test_thank_you_ticket_carries_method_total_charge_and_car_type(db):
    s = await hb.staffed_center(db)
    async with h.client() as c:
        one = (await h.book(c, db, s["cu"], s["when"], s["keys"][0])).json()["data"]
        gid, _ids = await h.group(c, db, await h.customer(db, s["pin"]), s["when"], s["keys"][1], cars=2)
    t1 = (await db.purchase_confirmations.find_one({"token": one["confirmation_token"]}))["payload"]
    assert t1["payment_method"] == "cash" and t1["total_amount"] == one["total_amount"]
    assert t1["cancellation_charge"] == 0 and t1["vehicle_type_name"] == "Hatchback"
    t2 = (await db.purchase_confirmations.find_one({"reference_id": _ids[0]}))["payload"]
    assert t2["vehicle_type_name"] == "2 × Hatchback" and t2["payment_method"] == "cash" and "cancellation_charge" in t2


# ---------------------------------------------------------------- discount + tip visibility


async def test_manager_discount_and_tip_are_visible_audited_and_never_captain_money(db):
    s = await hb.staffed_center(db)
    out = await _log(db, s, discount=50, tip=20, controller=True)
    bid = out["bookings"][0]["id"]
    doc = await hb.booking(db, bid)
    assert doc["manager_discount"] == 50 and doc["manager_discount_by"] == s["mgr"]["id"] and doc.get("manager_discount_at")
    assert doc["tip_amount"] == 20 and doc["tip_updated_by"] == s["mgr"]["id"]
    assert doc["captain_earning"] == 0 and doc["platform_earning"] == doc["total_amount"]
    adm = await h.admin(db)
    async with h.client() as c:
        row = next(b for b in (await c.get(f"/api/v1/bookings?service_center_id={s['center_id']}", headers=adm["h"])).json()["data"] if b["id"] == bid)
        assert (row["discount_amount"], row["manager_discount"], row["tip_amount"]) == (doc["discount_amount"], 50, 20)
        assert row["manager_discount_by_name"] and row["tip_updated_by_name"]
        r = await c.patch(f"/api/v1/bookings/{bid}/tip", json={"tip_amount": 35}, headers=s["mgr"]["h"])
        assert r.status_code == 200, r.text
    logged = await db.audit_logs.find_one({"action": "MANAGER_LOG_COMPLETED", "target_id": bid})
    assert logged["details"]["before"]["total_amount"] == doc["total_amount"] + 50 - 20
    assert logged["details"]["after"] == {"total_amount": doc["total_amount"], "manager_discount": 50, "tip_amount": 20}
    tip = await db.audit_logs.find_one({"action": "SET_BOOKING_TIP", "target_id": bid})
    assert tip["details"]["before"]["tip_amount"] == 20 and tip["details"]["after"]["tip_amount"] == 35
    assert tip["details"]["after"]["total_amount"] == doc["total_amount"] + 15
    assert (await hb.booking(db, bid))["captain_earning"] == 0


# ---------------------------------------------------------------- delete script


async def test_delete_script_hands_back_only_the_recorded_seat_and_charges(db, monkeypatch, tmp_path):
    from app.scripts import delete_booking

    monkeypatch.chdir(tmp_path)  # the script writes its backup file where it runs

    async def keep_the_test_connection(*a, **k):
        return None

    # The script opens/closes the app's global connection; here it must use
    # (and leave open) the test session's.
    monkeypatch.setattr(delete_booking, "connect_to_mongo", keep_the_test_connection)
    monkeypatch.setattr(delete_booking, "close_mongo_connection", keep_the_test_connection)
    s = await hb.staffed_center(db)
    b = await hb.new_booking(db, s["cu"], s["when"], s["keys"][2])
    await hb.slot_in(db, b["id"], 30)
    await BookingService(db).cancel_booking(b["id"], hb.cancel(at_customer_request=True), s["mgr"]["id"], "manager", s["center_id"])
    async with h.client() as c:
        gid, ids = await h.group(c, db, s["cu"], s["when"], s["keys"][0], cars=2)
    cars = [await hb.booking(db, i) for i in ids]
    # Wallet model (2026-10-07): the ₹80 late-cancel charge is a wallet debt
    # the next visit carries as "previous balance due" on its paying car.
    assert sum(float(c.get("wallet_due_carried") or 0) for c in cars) == 80
    before = (await h.seat(db, s["center_id"], s["when"], s["keys"][0]))["booked_count"]
    numbers = [c["booking_number"] for c in cars]
    assert await delete_booking.run(numbers, None, True) == 0
    assert await db.bookings.count_documents({"_id": {"$in": [ObjectId(i) for i in ids]}}) == 0
    assert (await h.seat(db, s["center_id"], s["when"], s["keys"][0]))["booked_count"] == before - 1
    # The deleted visit no longer carries the debt: it is open on the wallet
    # again, for the customer's next booking.
    from app.services.customer_wallet_service import CustomerWalletService

    wallet = await CustomerWalletService(db).summary(s["cu"]["id"])
    assert wallet["balance"] == -80 and wallet["carried_due"] == 0 and wallet["previous_balance_due"] == 80
    [charge] = await hb.charges_of(db, s["cu"]["id"])
    assert charge["status"] == "settled" and charge["settled_via"] == "wallet"


# ---------------------------------------------------------------- WhatsApp bot


async def test_bot_quote_uses_the_chosen_day_and_shows_the_charge(db):
    from app.services.whatsapp_bot_service import WhatsAppBotService

    s = await hb.staffed_center(db)
    b = await hb.new_booking(db, s["cu"], s["when"], s["keys"][2])
    await hb.slot_in(db, b["id"], 2 * 60)
    await BookingService(db).cancel_booking(b["id"], hb.cancel(at_customer_request=True), s["mgr"]["id"], "manager", s["center_id"])
    sub_id = await _type_pass(db, s["cu"]["id"])
    # The pass ends tomorrow: a wash booked for later isn't on it.
    await db.user_subscriptions.update_one({"_id": ObjectId(sub_id)}, {"$set": {"end_date": datetime.now(timezone.utc) + timedelta(days=1)}})
    line = {"vehicle_type": await get_hatchback_type_id(db), "quantity": 1, "service_ids": [await get_star_wash_service_id(db)]}
    bot = WhatsAppBotService(db)
    later = await bot._visit_quote(s["cu"]["id"], {"lines": [dict(line)], "address_id": s["cu"]["address_id"], "date": h.day(4)})
    # The late-cancel charge now sits on the wallet (spec 1.1): the next
    # booking carries it as "Previous Balance Due".
    assert later["lines"][0]["plan_covered"] == 0 and later["previous_balance_due"] == 50
    assert "Previous Balance Due: ₹50" in bot._bill_extras(later)
    assert bot._quote_total(later["lines"], later) == later["amount_payable"]
    # Credit instead of debt: shown as a wallet line, taken off what's payable.
    credit = {"wallet_applied": 30.0, "previous_balance_due": 0.0, "total_amount": 299.0, "amount_payable": 269.0}
    assert "Wallet Credit: −₹30" in bot._bill_extras(credit) and "Previous Balance" not in bot._bill_extras(credit)
    assert bot._quote_total([], credit) == 269.0
    today = await bot._visit_quote(s["cu"]["id"], {"lines": [dict(line)], "address_id": s["cu"]["address_id"], "date": h.day(0)})
    assert today["lines"][0]["plan_covered"] == 1


# ---------------------------------------------------------------- assignment re-check


async def test_assign_rechecks_the_captain_inside_the_transaction(db, monkeypatch):
    s = await hb.staffed_center(db)
    b = await hb.new_booking(db, s["cu"], s["when"], s["keys"][0])
    svc = BookingService(db)
    real = svc._ensure_captain_not_on_leave

    async def suspended_meanwhile(captain_id, booking):
        await real(captain_id, booking)
        await db.users.update_one({"_id": ObjectId(captain_id)}, {"$set": {"status": "suspended"}})

    monkeypatch.setattr(svc, "_ensure_captain_not_on_leave", suspended_meanwhile)
    with pytest.raises(BadRequestException, match="isn't active"):
        await svc.assign_captain(b["id"], BookingAssignCaptainRequest(captain_id=s["cap"]["id"]), s["mgr"]["id"], "manager", s["center_id"])
    doc = await hb.booking(db, b["id"])
    assert doc["status"] == "pending" and not doc.get("captain_id")


async def test_lost_before_photo_step_hands_the_photo_back(db, monkeypatch):
    s = await hb.staffed_center(db)
    b = await hb.new_booking(db, s["cu"], s["when"], s["keys"][0])
    await hb.on_the_way(db, b["id"], s["cap"]["id"], vehicle_verified=True)
    svc = BookingService(db)
    real = svc._check_geofence

    async def cancelled_meanwhile(booking, lat, lng):
        result = await real(booking, lat, lng)
        await BookingService(db).cancel_booking(b["id"], BookingCancelRequest(reason="Customer not home"), s["mgr"]["id"], "manager", s["center_id"])
        return result

    monkeypatch.setattr(svc, "_check_geofence", cancelled_meanwhile)
    photo = await hb.snap(db, s["cap"]["id"])
    with pytest.raises(BadRequestException, match="just changed"):
        await svc.capture_before_photo(b["id"], photo, s["cap"]["id"])
    from app.core.storage import photo_key_for_url

    assert (await db.uploaded_photos.find_one({"_id": photo_key_for_url(photo.image_url)}))["used_by"] is None
