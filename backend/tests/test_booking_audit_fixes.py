"""
Regression tests for the 2026-10 booking/payments/scale audit findings.

Each test pins one verified bug: it reproduced (the audit's own scratch
tests) before the fix and must keep passing after it. The racing action is
either injected at the exact point between a method's read and its write
(by wrapping a helper the method awaits in between — deterministic), or
fired for real with asyncio.gather.
"""
from types import SimpleNamespace
from unittest.mock import ANY
import asyncio
import itertools
import random
from datetime import datetime, timedelta, timezone

import pytest
from bson import ObjectId

from app.core.exceptions import BadRequestException
from app.models.enums import PaymentMethod
from app.schemas.booking_schema import (
    BookingAssignCaptainRequest,
    BookingCancelRequest,
    BookingCreateRequest,
    BookingGroupCreateRequest,
    BookingRescheduleRequest,
    CaptainCancelRequest,
    GroupVehicleRequest,
    HeadingRequest,
    ManagerLogBookingRequest,
    PhotoCaptureRequest,
    QuickBookingLine,
    ReassignCaptainRequest,
    VerifyVehicleRequest,
)
from app.schemas.subscription_schema import SubscribeRequest
from app.services.booking_service import PLAN_EXTRAS_CASH_REFUSAL, BookingService, center_queue_filters
from app.services.capacity_policy_service import CapacityPolicyService
from app.services.notification_service import NotificationService
from app.services.subscription_service import UserSubscriptionService
from app.utils.timezone import now_ist

from tests.factories import (
    get_hatchback_type_id,
    get_star_wash_service_id,
    make_address,
    make_captain,
    make_customer,
    make_customer_with_vehicle,
    make_manager,
    make_service_center,
    make_subscription_plan,
    make_vehicle,
    own_upload_url,
    make_recorded_photo_url,
)

pytestmark = pytest.mark.asyncio
_PIN = itertools.count(random.randint(1000, 60000))
LAT, LNG = 22.7, 75.8


async def _photo(db, captain_id: str, name: str = "p") -> PhotoCaptureRequest:
    # A photo the captain really uploaded (recorded) — job steps take only
    # those, once each (CAP-02).
    return PhotoCaptureRequest(image_url=await make_recorded_photo_url(db, captain_id, name), latitude=LAT, longitude=LNG)


@pytest.fixture
def sent(monkeypatch):
    """Every notify() call, recorded (and still performed)."""
    calls: list[dict] = []
    real = NotificationService.notify

    async def spy(self, user_id, title, message, *args, **kwargs):
        calls.append({"user_id": user_id, "title": title, "message": message, **kwargs})
        return await real(self, user_id, title, message, *args, **kwargs)

    monkeypatch.setattr(NotificationService, "notify", spy)
    return calls


async def _center(db, cleanup, **kw) -> tuple[str, str]:
    pin = f"3{next(_PIN):05d}"
    center_id = await make_service_center(db, pincode=pin, **kw)
    for coll, q in [("slot_capacity", {"service_center_id": center_id}), ("daily_capacity", {"service_center_id": center_id}),
                    ("capacity_policy_changes", {"service_center_id": center_id}), ("service_centers", {"_id": ObjectId(center_id)})]:
        cleanup.append((coll, q))
    return center_id, pin


async def _customer(db, cleanup, pin: str) -> tuple[str, str, str]:
    hatch = await get_hatchback_type_id(db)
    customer_id, vehicle_id, address_id = await make_customer_with_vehicle(db, hatch, pincode=pin)
    for coll, q in [("users", {"_id": ObjectId(customer_id)}), ("vehicles", {"owner_id": customer_id}),
                    ("addresses", {"owner_id": customer_id}), ("bookings", {"customer_id": customer_id}),
                    ("user_subscriptions", {"customer_id": customer_id}), ("notifications", {"user_id": customer_id}),
                    ("coupon_usages", {"user_id": customer_id}), ("payment_orders", {"customer_id": customer_id})]:
        cleanup.append((coll, q))
    return customer_id, vehicle_id, address_id


async def _staff(db, cleanup, center_id: str, captains: int = 1, wallet: float = 200.0) -> tuple[list[str], str]:
    caps = [await make_captain(db, center_id, wallet_balance=wallet) for _ in range(captains)]
    manager_id = await make_manager(db, center_id)
    for cid in caps:
        cleanup += [("users", {"_id": ObjectId(cid)}), ("captain_wallets", {"captain_id": cid}),
                    ("wallet_transactions", {"captain_id": cid}), ("notifications", {"user_id": cid})]
    cleanup.append(("users", {"_id": ObjectId(manager_id)}))
    return caps, manager_id


async def _slot(db, center_id: str, offset: int = 2, index: int = 0) -> tuple[str, str]:
    when = (now_ist().date() + timedelta(days=offset)).isoformat()
    slots = await BookingService(db).available_slots(center_id, when)
    return when, [s["key"] for s in slots if s["status"] in ("available", "low")][index]


async def _seats(db, center_id: str, when: str, slot: str) -> int:
    doc = await db.slot_capacity.find_one({"service_center_id": center_id, "date": when, "slot_key": slot})
    return int((doc or {}).get("booked_count") or 0)


async def _pass(db, cleanup, customer_id: str, service_id: str, *, vehicle_id=None, vehicle_type=None, total: int = 4) -> str:
    plan_id = await make_subscription_plan(db, vehicle_types=[], included_service_ids=[service_id], total_service_count=total)
    cleanup.append(("subscription_plans", {"_id": ObjectId(plan_id)}))
    sub = await UserSubscriptionService(db).subscribe(
        customer_id, SubscribeRequest(plan_id=plan_id, vehicle_id=vehicle_id, vehicle_type=vehicle_type, service_id=service_id)
    )
    return sub["id"]


async def _remaining(db, sub_id: str) -> int:
    return (await db.user_subscriptions.find_one({"_id": ObjectId(sub_id)}))["remaining_service_count"]


async def _rig(db, cleanup, *, captains: int = 1, wallet: float = 200.0, with_pass: bool = False, method=PaymentMethod.CASH, coupon=None):
    center_id, pin = await _center(db, cleanup)
    customer_id, vehicle_id, address_id = await _customer(db, cleanup, pin)
    caps, manager_id = await _staff(db, cleanup, center_id, captains, wallet)
    service = await get_star_wash_service_id(db)
    sub_id = await _pass(db, cleanup, customer_id, service, vehicle_id=vehicle_id) if with_pass else None
    when, slot = await _slot(db, center_id)
    booking = await BookingService(db).create_booking(customer_id, BookingCreateRequest(
        vehicle_id=vehicle_id, address_id=address_id, service_ids=[service], scheduled_date=when, scheduled_slot=slot,
        payment_method=method, subscription_id=sub_id, coupon_code=coupon,
    ))
    return dict(center_id=center_id, pin=pin, customer_id=customer_id, vehicle_id=vehicle_id, address_id=address_id,
                captains=caps, captain_id=caps[0], manager_id=manager_id, service=service, sub_id=sub_id,
                when=when, slot=slot, booking=booking, booking_id=booking["id"])


async def _on_the_way(db, r: dict, *, verify: bool = True) -> None:
    bs = BookingService(db)
    await bs.assign_captain(r["booking_id"], BookingAssignCaptainRequest(captain_id=r["captain_id"]), r["manager_id"], "manager", r["center_id"])
    await db.bookings.update_one({"_id": ObjectId(r["booking_id"])}, {"$set": {"estimated_start_at": now_ist() + timedelta(minutes=20)}})
    await bs.start_heading(r["booking_id"], HeadingRequest(latitude=LAT, longitude=LNG, equipment_used=[]), r["captain_id"])
    if verify:
        raw = await db.bookings.find_one({"_id": ObjectId(r["booking_id"])})
        await bs.verify_vehicle(r["booking_id"], VerifyVehicleRequest(service_code=raw["service_code"]), r["captain_id"])


async def _coupon(db, cleanup, *, coupon_type="flat", value=100.0, total_limit=None, per_user=5) -> str:
    code = f"FIX{random.randint(100000, 999999)}"
    now = now_ist()
    res = await db.coupons.insert_one({
        "code": code, "coupon_type": coupon_type, "value": value, "min_order_value": 0, "usage_limit_per_user": per_user,
        "total_usage_limit": total_limit, "total_used": 0, "valid_from": now - timedelta(days=1), "valid_until": now + timedelta(days=30),
        "offer_kind": "standard", "is_active": True, "is_deleted": False,
    })
    cleanup.append(("coupons", {"_id": res.inserted_id}))
    return code


async def _wallet(db, captain_id: str) -> float:
    return float((await db.captain_wallets.find_one({"captain_id": captain_id}))["balance"])


# ======================================================== BOOK-1: captain photo steps


async def test_after_photo_cannot_resurrect_a_cancelled_booking(db, cleanup, sent):
    r = await _rig(db, cleanup, with_pass=True)
    await _on_the_way(db, r)
    await BookingService(db).capture_before_photo(r["booking_id"], await _photo(db, r["captain_id"]), r["captain_id"])
    pass_before = await _remaining(db, r["sub_id"])

    captain_svc = BookingService(db)
    real_geofence = captain_svc._check_geofence

    async def geofence_then_manager_cancels(booking, lat, lng):
        result = await real_geofence(booking, lat, lng)
        await BookingService(db).cancel_booking(
            r["booking_id"], BookingCancelRequest(reason="Customer called to cancel"), r["manager_id"], "manager", r["center_id"]
        )
        return result

    captain_svc._check_geofence = geofence_then_manager_cancels
    with pytest.raises(BadRequestException, match="just changed"):
        await captain_svc.capture_after_photo_and_complete(r["booking_id"], await _photo(db, r["captain_id"], "after"), r["captain_id"])

    final = await db.bookings.find_one({"_id": ObjectId(r["booking_id"])})
    assert final["status"] == "cancelled"
    assert await _remaining(db, r["sub_id"]) == pass_before + 1, "the wash is handed back once"
    assert await db.wallet_transactions.count_documents({"booking_id": r["booking_id"]}) == 0, "nobody is paid for a cancelled job"
    assert not any(c["title"] == "Service completed" for c in sent)


async def test_after_photo_racing_a_cancel_never_completes_a_cancelled_job(db, cleanup):
    for _ in range(4):
        r = await _rig(db, cleanup)
        await _on_the_way(db, r)
        await BookingService(db).capture_before_photo(r["booking_id"], await _photo(db, r["captain_id"]), r["captain_id"])
        after = await _photo(db, r["captain_id"], "after")
        results = await asyncio.gather(
            BookingService(db).capture_after_photo_and_complete(r["booking_id"], after, r["captain_id"]),
            BookingService(db).cancel_booking(r["booking_id"], BookingCancelRequest(reason="cancel now"), r["manager_id"], "manager", r["center_id"]),
            return_exceptions=True,
        )
        completed_ok, cancel_ok = (not isinstance(x, Exception) for x in results)
        final = await db.bookings.find_one({"_id": ObjectId(r["booking_id"])})
        assert completed_ok != cancel_ok, "exactly one of the two wins"
        assert final["status"] == ("completed" if completed_ok else "cancelled")
        assert await db.wallet_transactions.count_documents({"booking_id": r["booking_id"]}) == (1 if completed_ok else 0)


async def test_double_after_photo_completes_and_tells_the_customer_once(db, cleanup, sent):
    r = await _rig(db, cleanup)
    await _on_the_way(db, r)
    await BookingService(db).capture_before_photo(r["booking_id"], await _photo(db, r["captain_id"]), r["captain_id"])
    after = await _photo(db, r["captain_id"], "after")  # the same photo, double-submitted
    results = await asyncio.gather(*(
        BookingService(db).capture_after_photo_and_complete(r["booking_id"], after, r["captain_id"]) for _ in range(2)
    ))
    assert all(res["status"] == "completed" for res in results), "the duplicate is answered quietly, not refused"
    assert sum(1 for c in sent if c["title"] == "Service completed" and c["user_id"] == r["customer_id"]) == 1
    assert await db.booking_status_history.count_documents({"booking_id": r["booking_id"], "status": "completed"}) == 1
    assert await db.wallet_transactions.count_documents({"booking_id": r["booking_id"]}) == 1


async def test_before_photo_cannot_restart_a_cancelled_or_released_job(db, cleanup):
    for race in ("cancel", "release"):
        r = await _rig(db, cleanup)
        await _on_the_way(db, r)
        captain_svc = BookingService(db)
        real_geofence = captain_svc._check_geofence

        async def geofence_then_race(booking, lat, lng, r=r, race=race):
            result = await real_geofence(booking, lat, lng)
            if race == "cancel":
                await BookingService(db).cancel_booking(
                    r["booking_id"], BookingCancelRequest(reason="Customer not home"), r["manager_id"], "manager", r["center_id"]
                )
            else:
                await BookingService(db).captain_cancel(r["booking_id"], CaptainCancelRequest(reason="bike broke"), r["captain_id"])
            return result

        captain_svc._check_geofence = geofence_then_race
        with pytest.raises(BadRequestException, match="just changed"):
            await captain_svc.capture_before_photo(r["booking_id"], await _photo(db, r["captain_id"]), r["captain_id"])
        final = await db.bookings.find_one({"_id": ObjectId(r["booking_id"])})
        assert final["status"] == ("cancelled" if race == "cancel" else "pending")
        assert not final.get("before_photo") and not final.get("service_started_at")


async def test_verify_vehicle_cannot_stamp_a_cancelled_booking(db, cleanup):
    r = await _rig(db, cleanup)
    await _on_the_way(db, r, verify=False)
    raw = await db.bookings.find_one({"_id": ObjectId(r["booking_id"])})
    captain_svc = BookingService(db)
    real_geofence = captain_svc._check_geofence

    async def geofence_then_cancel(booking, lat, lng):
        result = await real_geofence(booking, lat, lng)
        await BookingService(db).cancel_booking(r["booking_id"], BookingCancelRequest(reason="no show"), r["manager_id"], "manager", r["center_id"])
        return result

    captain_svc._check_geofence = geofence_then_cancel
    with pytest.raises(BadRequestException, match="just changed"):
        await captain_svc.verify_vehicle(
            r["booking_id"], VerifyVehicleRequest(service_code=raw["service_code"], latitude=LAT, longitude=LNG), r["captain_id"]
        )
    final = await db.bookings.find_one({"_id": ObjectId(r["booking_id"])})
    assert final["status"] == "cancelled" and not final.get("vehicle_verified")


# ======================================================== PAY-6 / PAY-3: wallet settlement


async def _complete(db, r: dict) -> None:
    await db.bookings.update_one(
        {"_id": ObjectId(r["booking_id"])},
        {"$set": {"captain_id": r["captain_id"], "status": "service_started", "service_started_at": now_ist()}},
    )
    await BookingService(db).capture_after_photo_and_complete(r["booking_id"], await _photo(db, r["captain_id"], "after"), r["captain_id"])


async def test_a_coupon_on_a_cash_job_is_the_platforms_not_the_captains(db, cleanup):
    """₹349 cash job, ₹100 coupon: the customer pays ₹249 cash; the captain
    keeps his fee (₹40) and owes the platform ₹209 — not the pre-discount ₹309."""
    center_id, pin = await _center(db, cleanup)
    customer_id, vehicle_id, address_id = await _customer(db, cleanup, pin)
    (captain_id,), _ = await _staff(db, cleanup, center_id, wallet=500.0)
    code = await _coupon(db, cleanup, value=100.0)
    service = await get_star_wash_service_id(db)
    when, slot = await _slot(db, center_id)
    booking = await BookingService(db).create_booking(customer_id, BookingCreateRequest(
        vehicle_id=vehicle_id, address_id=address_id, service_ids=[service], scheduled_date=when, scheduled_slot=slot,
        payment_method=PaymentMethod.CASH, coupon_code=code,
    ))
    assert booking["discount_amount"] == 100
    assert booking["platform_earning"] == round(booking["total_amount"] - booking["captain_earning"], 2)
    await _complete(db, {"booking_id": booking["id"], "captain_id": captain_id})
    # Founder 2026-10-07: completion no longer marks cash paid — the captain
    # COLLECTS what is due (₹249), and his wallet moves by deltas.
    from app.services.payment_service import PaymentService

    await PaymentService(db).captain_collect_cash(booking["id"], captain_id)
    debited = 500.0 - await _wallet(db, captain_id)
    assert booking["total_amount"] - debited == pytest.approx(booking["captain_earning"]), "the captain nets exactly his fee"
    raw = await db.bookings.find_one({"_id": ObjectId(booking["id"])})
    assert raw["captain_cash_collected"] == booking["total_amount"] and raw["payment_status"] == "paid"


async def test_a_discount_bigger_than_the_platform_share_is_credited_to_the_captain(db, cleanup):
    center_id, pin = await _center(db, cleanup)
    customer_id, vehicle_id, address_id = await _customer(db, cleanup, pin)
    (captain_id,), _ = await _staff(db, cleanup, center_id, wallet=500.0)
    code = await _coupon(db, cleanup, coupon_type="percentage", value=95.0)
    service = await get_star_wash_service_id(db)
    when, slot = await _slot(db, center_id)
    booking = await BookingService(db).create_booking(customer_id, BookingCreateRequest(
        vehicle_id=vehicle_id, address_id=address_id, service_ids=[service], scheduled_date=when, scheduled_slot=slot,
        payment_method=PaymentMethod.CASH, coupon_code=code,
    ))
    assert booking["total_amount"] < booking["captain_earning"]
    await _complete(db, {"booking_id": booking["id"], "captain_id": captain_id})
    from app.services.payment_service import PaymentService

    await PaymentService(db).captain_collect_cash(booking["id"], captain_id)  # collection pays it (2026-10-07)
    credited = await _wallet(db, captain_id) - 500.0
    assert credited == pytest.approx(booking["captain_earning"] - booking["total_amount"])
    assert booking["total_amount"] + credited == pytest.approx(booking["captain_earning"])


async def test_an_unpaid_online_completion_is_recorded_as_credit_settled(db, cleanup):
    center_id, pin = await _center(db, cleanup)
    customer_id, vehicle_id, address_id = await _customer(db, cleanup, pin)
    (captain_id,), _ = await _staff(db, cleanup, center_id)
    service = await get_star_wash_service_id(db)
    when, slot = await _slot(db, center_id)
    booking = await BookingService(db).create_booking(customer_id, BookingCreateRequest(
        vehicle_id=vehicle_id, address_id=address_id, service_ids=[service], scheduled_date=when, scheduled_slot=slot,
        payment_method=PaymentMethod.ONLINE,
    ), source="staff", _allow_pinless=True)
    await _complete(db, {"booking_id": booking["id"], "captain_id": captain_id})
    raw = await db.bookings.find_one({"_id": ObjectId(booking["id"])})
    # Delta settlement (2026-10-07): the captain is owed his earning, he holds no cash.
    assert raw["captain_wallet_posted"] == raw["captain_earning"] and raw["captain_cash_collected"] == 0
    assert raw["payment_status"] == "pending", "an unpaid booking is never invented paid"


async def test_a_pass_booking_with_paid_extras_cannot_switch_to_cash(db, cleanup):
    center_id, pin = await _center(db, cleanup)
    customer_id = await make_customer(db)
    address_id = await make_address(db, customer_id, pincode=pin)
    cleanup += [("users", {"_id": ObjectId(customer_id)}), ("addresses", {"owner_id": customer_id}),
                ("bookings", {"customer_id": customer_id}), ("user_subscriptions", {"customer_id": customer_id})]
    hatch = await get_hatchback_type_id(db)
    star = await get_star_wash_service_id(db)
    polish = str((await db.services.find_one({"slug": "exterior-polish"}))["_id"])
    sub_id = await _pass(db, cleanup, customer_id, star, vehicle_type=hatch)
    when, slot = await _slot(db, center_id)
    bs = BookingService(db)
    booking = await bs.create_booking(customer_id, BookingCreateRequest(
        vehicle_type=hatch, address_id=address_id, service_ids=[star, polish], scheduled_date=when, scheduled_slot=slot,
        payment_method=PaymentMethod.ONLINE, subscription_id=sub_id,
    ))
    assert booking["status"] == "awaiting_payment" and booking["total_amount"] > 0
    with pytest.raises(BadRequestException) as exc:
        await bs.switch_to_cash(booking["id"], customer_id)
    assert exc.value.message == PLAN_EXTRAS_CASH_REFUSAL
    assert (await db.bookings.find_one({"_id": ObjectId(booking["id"])}))["status"] == "awaiting_payment"


# ======================================================== PAY-4: refunds owed after a cancel


class _StubOrders:
    seq = itertools.count(1)

    def create(self, payload):
        return {"id": f"order_fix_{next(self.seq):06d}", **payload}


class _StubLinks:
    def create(self, payload):
        return {"id": f"plink_fix_{random.randint(1, 10**9)}", "short_url": "https://rzp.io/l/fix", **payload}

    def cancel(self, link_id):
        return {"id": link_id, "status": "cancelled"}


class _StubClient:
    order = _StubOrders()
    payment_link = _StubLinks()
    # verify asks Razorpay what the signed payment is (PAY-09): captured,
    # for the order and amount being verified (mock.ANY).
    payment = SimpleNamespace(fetch=lambda pid: {"id": pid, "status": "captured", "order_id": ANY, "amount": ANY, "currency": "INR"})


async def _paid_online_booking(db, cleanup, monkeypatch):
    from app.schemas.payment_schema import CreateOrderRequest, VerifyPaymentRequest
    from app.services import payment_service
    from app.services.payment_service import PaymentService, _expected_signature

    monkeypatch.setattr(payment_service.settings, "RAZORPAY_KEY_ID", "rzp_test_stub")
    monkeypatch.setattr(payment_service.settings, "RAZORPAY_KEY_SECRET", "stub_secret_key")
    monkeypatch.setattr(payment_service, "_razorpay_client", lambda: _StubClient())
    center_id, pin = await _center(db, cleanup)
    customer_id, vehicle_id, address_id = await _customer(db, cleanup, pin)
    service = await get_star_wash_service_id(db)
    when, slot = await _slot(db, center_id, offset=3)
    booking = await BookingService(db).create_booking(customer_id, BookingCreateRequest(
        vehicle_id=vehicle_id, address_id=address_id, service_ids=[service], scheduled_date=when, scheduled_slot=slot,
        payment_method=PaymentMethod.ONLINE,
    ))
    payments = PaymentService(db)
    order = await payments.create_order(customer_id, CreateOrderRequest(purpose="booking", booking_id=booking["id"]))
    pay_id = f"pay_fix_{random.randint(1, 10**9)}"
    await payments.verify_payment(customer_id, VerifyPaymentRequest(
        razorpay_order_id=order["order_id"], razorpay_payment_id=pay_id, razorpay_signature=_expected_signature(order["order_id"], pay_id),
    ))
    paid = await db.bookings.find_one({"_id": ObjectId(booking["id"])})
    assert paid["payment_status"] == "paid" and paid["status"] == "pending"
    return customer_id, booking


async def test_cancelling_a_paid_online_booking_credits_the_customer_wallet(db, cleanup, monkeypatch):
    """Spec 1.2 (2026-10-07): the money of a cancelled paid booking goes to
    the customer's wallet (never lost, never parked) — it used to go on the
    admin refund list."""
    from app.services.customer_wallet_service import CustomerWalletService

    customer_id, booking = await _paid_online_booking(db, cleanup, monkeypatch)
    await BookingService(db).cancel_booking(booking["id"], BookingCancelRequest(reason="changed my mind"), customer_id, "customer")
    assert await CustomerWalletService(db).balance(customer_id) == booking["total_amount"]  # booked days out: free
    raw = await db.bookings.find_one({"_id": ObjectId(booking["id"])})
    assert raw["payment_status"] == "refunded" and raw["refunded_to"] == "wallet"
    assert not await db.payment_orders.find_one({"kind": "refund_due", "booking_id": booking["id"]})


async def test_deleting_a_paid_online_booking_puts_it_on_the_refund_list(db, cleanup, monkeypatch):
    _customer_id, booking = await _paid_online_booking(db, cleanup, monkeypatch)
    await BookingService(db).soft_delete_booking(booking["id"], "admin-x")
    assert await db.payment_orders.count_documents({"kind": "refund_due", "booking_id": booking["id"]}) == 1


# ======================================================== PAY-7: coupon's last use


async def test_a_coupon_race_loser_gets_no_discounted_booking(db, cleanup):
    center_id, pin = await _center(db, cleanup)
    code = await _coupon(db, cleanup, total_limit=1, per_user=1)
    service = await get_star_wash_service_id(db)
    when, slot = await _slot(db, center_id)
    a = await _customer(db, cleanup, pin)
    b = await _customer(db, cleanup, pin)

    def request(c):
        return BookingCreateRequest(vehicle_id=c[1], address_id=c[2], service_ids=[service], scheduled_date=when,
                                    scheduled_slot=slot, payment_method=PaymentMethod.CASH, coupon_code=code)

    loser = BookingService(db)
    real_record = loser.coupon_service.record_usage

    async def winner_books_first(*args, **kwargs):
        # B validated the coupon (still unused) — A books it in between.
        await BookingService(db).create_booking(a[0], request(a))
        return await real_record(*args, **kwargs)

    loser.coupon_service.record_usage = winner_books_first
    with pytest.raises(BadRequestException, match="usage limit"):
        await loser.create_booking(b[0], request(b))
    assert await db.bookings.count_documents({"coupon_code": code}) == 1
    assert await db.bookings.count_documents({"customer_id": b[0]}) == 0
    assert await _seats(db, center_id, when, slot) == 1, "the loser never held a seat"
    assert (await db.coupons.find_one({"code": code}))["total_used"] == 1


# ======================================================== BOOK-2: capacity policy resync


async def _touch_day(db, center_id: str, date_str: str) -> None:
    """Give a day its counter docs the way its first reservation does. (These
    tests used the manager's capacity GET for that — it no longer writes,
    audit MGR-05.)"""
    bs = BookingService(db)
    center = await db.service_centers.find_one({"_id": ObjectId(center_id)})
    for key in await CapacityPolicyService(db)._resolve_slots(center):
        await bs.slot_capacity_repo.get_or_init(
            {"service_center_id": center_id, "date": date_str, "slot_key": key},
            {"capacity": await bs._default_slot_capacity(center, date_str, key), "booked_count": 0, "is_closed": False},
        )


async def test_a_capacity_cut_reaches_days_that_already_have_bookings(db, cleanup):
    center_id, pin = await _center(db, cleanup, slot_duration_minutes=180, default_slot_capacity=20)
    cps = CapacityPolicyService(db)
    today = now_ist().date().isoformat()
    day = lambda n: (now_ist().date() + timedelta(days=n)).isoformat()  # noqa: E731
    keys = await cps._resolve_slots(await db.service_centers.find_one({"_id": ObjectId(center_id)}))
    await cps.schedule_change(center_id, today, 4 * len(keys), None, "admin", "admin", None)
    # A later change already scheduled for day 3 (3 per slot).
    await cps.schedule_change(center_id, day(3), 3 * len(keys), None, "admin", "admin", None)
    service = await get_star_wash_service_id(db)
    slot = keys[1]
    first = await _customer(db, cleanup, pin)
    await BookingService(db).create_booking(first[0], BookingCreateRequest(
        vehicle_id=first[1], address_id=first[2], service_ids=[service], scheduled_date=day(1), scheduled_slot=slot))
    # Day 2's docs already exist; one slot is a manual override.
    await _touch_day(db, center_id, day(2))
    await BookingService(db).set_slot_capacity(center_id, day(2), keys[0], 7, None)
    await _touch_day(db, center_id, day(3))

    await cps.schedule_change(center_id, today, 1 * len(keys), None, "admin", "admin", None)

    async def cap(d, k):
        return (await db.slot_capacity.find_one({"service_center_id": center_id, "date": d, "slot_key": k}))["capacity"]

    assert await cap(day(1), slot) == 1, "tomorrow's already-touched slot follows the cut"
    assert await _seats(db, center_id, day(1), slot) == 1, "booked counters are never touched"
    assert await cap(day(2), keys[1]) == 1
    assert await cap(day(2), keys[0]) == 7, "a manual override still wins"
    assert await cap(day(3), keys[1]) == 3, "the next scheduled change governs its own days"
    second = await _customer(db, cleanup, pin)
    with pytest.raises(BadRequestException, match="fully booked"):
        await BookingService(db).create_booking(second[0], BookingCreateRequest(
            vehicle_id=second[1], address_id=second[2], service_ids=[service], scheduled_date=day(1), scheduled_slot=slot))


async def test_cancelling_a_scheduled_change_reverts_every_day_it_touched(db, cleanup):
    center_id, _pin = await _center(db, cleanup, slot_duration_minutes=180, default_slot_capacity=20)
    cps = CapacityPolicyService(db)
    day = lambda n: (now_ist().date() + timedelta(days=n)).isoformat()  # noqa: E731
    keys = await cps._resolve_slots(await db.service_centers.find_one({"_id": ObjectId(center_id)}))
    await cps.schedule_change(center_id, now_ist().date().isoformat(), 4 * len(keys), None, "admin", "admin", None)
    change = await cps.schedule_change(center_id, day(1), 2 * len(keys), None, "admin", "admin", None)
    for n in (1, 2):
        await _touch_day(db, center_id, day(n))
    await cps.cancel_scheduled_change(center_id, change["id"], "admin", "admin", None)
    for n in (1, 2):
        doc = await db.slot_capacity.find_one({"service_center_id": center_id, "date": day(n), "slot_key": keys[0]})
        assert doc["capacity"] == 4


# ======================================================== BOOK-3 / BOOK-8: recycle bin


async def test_deleting_a_cancelled_pass_booking_does_not_refund_it_twice(db, cleanup):
    r = await _rig(db, cleanup, with_pass=True)
    bs = BookingService(db)
    await bs.cancel_booking(r["booking_id"], BookingCancelRequest(reason="not needed"), r["customer_id"], "customer")
    after_cancel = await _remaining(db, r["sub_id"])
    await bs.soft_delete_booking(r["booking_id"], "admin-x")
    assert await _remaining(db, r["sub_id"]) == after_cancel


async def test_restoring_a_deleted_pass_booking_spends_the_wash_again_or_refuses(db, cleanup):
    r = await _rig(db, cleanup, with_pass=True)
    bs = BookingService(db)
    booked = await _remaining(db, r["sub_id"])
    await bs.soft_delete_booking(r["booking_id"], "admin-x")
    assert await _remaining(db, r["sub_id"]) == booked + 1
    await bs.restore_booking(r["booking_id"])
    assert await _remaining(db, r["sub_id"]) == booked, "a restored booking is not a free wash"
    await bs.cancel_booking(r["booking_id"], BookingCancelRequest(reason="after restore"), r["customer_id"], "customer")
    assert await _remaining(db, r["sub_id"]) == booked + 1, "and its cancel hands the wash back again"

    # Delete again, then drain the pass: the restore is refused, nothing changes.
    r2 = await _rig(db, cleanup, with_pass=True)
    await bs.soft_delete_booking(r2["booking_id"], "admin-x")
    await db.user_subscriptions.update_one({"_id": ObjectId(r2["sub_id"])}, {"$set": {"remaining_service_count": 0}})
    with pytest.raises(BadRequestException, match="no wash left"):
        await bs.restore_booking(r2["booking_id"])
    raw = await db.bookings.find_one({"_id": ObjectId(r2["booking_id"])})
    assert raw["is_deleted"] is True and await _remaining(db, r2["sub_id"]) == 0


async def test_an_admin_deleted_booking_frees_the_slot_for_rebooking(db, cleanup):
    center_id, pin = await _center(db, cleanup)
    customer_id, _vehicle_id, address_id = await _customer(db, cleanup, pin)
    hatch = await get_hatchback_type_id(db)
    service = await get_star_wash_service_id(db)
    when, slot = await _slot(db, center_id)
    bs = BookingService(db)
    request = BookingCreateRequest(vehicle_type=hatch, address_id=address_id, service_ids=[service], scheduled_date=when, scheduled_slot=slot)
    first = await bs.create_booking(customer_id, request)
    await bs.soft_delete_booking(first["id"], "admin-x")
    again = await bs.create_booking(customer_id, request)
    assert again["status"] == "pending"
    info = await db.bookings.index_information()
    assert info["uniq_active_customer_slot_v3"]["partialFilterExpression"]["is_deleted"] is False
    # And the restore of the old one is refused rather than doubling the slot.
    with pytest.raises(BadRequestException, match="can't be restored"):
        await bs.restore_booking(first["id"])


async def test_deleting_an_unpaid_booking_hands_its_seat_back(db, cleanup):
    center_id, pin = await _center(db, cleanup)
    customer_id, _vehicle_id, address_id = await _customer(db, cleanup, pin)
    hatch = await get_hatchback_type_id(db)
    service = await get_star_wash_service_id(db)
    when, slot = await _slot(db, center_id)
    bs = BookingService(db)
    booking = await bs.create_booking(customer_id, BookingCreateRequest(
        vehicle_type=hatch, address_id=address_id, service_ids=[service], scheduled_date=when, scheduled_slot=slot, payment_method="online"))
    assert booking["status"] == "awaiting_payment" and await _seats(db, center_id, when, slot) == 1
    await bs.soft_delete_booking(booking["id"], "admin-x")
    assert await _seats(db, center_id, when, slot) == 0


# ======================================================== BOOK-4: visit seat bookkeeping


async def test_a_visit_car_losing_its_pass_race_keeps_a_strangers_seat_and_stays_quiet(db, cleanup, sent):
    center_id, pin = await _center(db, cleanup)
    customer_id, vehicle_id, address_id = await _customer(db, cleanup, pin)
    hatch = await get_hatchback_type_id(db)
    v2 = await make_vehicle(db, customer_id, hatch)
    service = await get_star_wash_service_id(db)
    sub_id = await _pass(db, cleanup, customer_id, service, vehicle_id=v2)
    when, slot = await _slot(db, center_id)
    stranger = await _customer(db, cleanup, pin)
    bs = BookingService(db)
    await bs.create_booking(stranger[0], BookingCreateRequest(
        vehicle_id=stranger[1], address_id=stranger[2], service_ids=[service], scheduled_date=when, scheduled_slot=slot))

    async def lost_race(*args, **kwargs):
        raise BadRequestException("This subscription's remaining services just ran out — someone else may have just booked with it.")

    bs.subscription_service.commit_consumption = lost_race
    with pytest.raises(BadRequestException, match="ran out"):
        await bs.create_booking_group(customer_id, BookingGroupCreateRequest(
            vehicles=[GroupVehicleRequest(vehicle_id=vehicle_id, service_ids=[service]),
                      GroupVehicleRequest(vehicle_id=v2, service_ids=[service], subscription_id=sub_id)],
            address_id=address_id, scheduled_date=when, scheduled_slot=slot, payment_method="cash"))
    assert await _seats(db, center_id, when, slot) == 1, "the stranger's seat is untouched"
    assert not any(c["user_id"] == customer_id and "cancelled" in c["title"].lower() for c in sent)


async def test_concurrent_cancels_of_a_visits_cars_release_its_seat_once(db, cleanup):
    for _ in range(4):
        center_id, pin = await _center(db, cleanup)
        customer_id, vehicle_id, address_id = await _customer(db, cleanup, pin)
        hatch = await get_hatchback_type_id(db)
        v2 = await make_vehicle(db, customer_id, hatch)
        service = await get_star_wash_service_id(db)
        when, slot = await _slot(db, center_id)
        stranger = await _customer(db, cleanup, pin)
        bs = BookingService(db)
        await bs.create_booking(stranger[0], BookingCreateRequest(
            vehicle_id=stranger[1], address_id=stranger[2], service_ids=[service], scheduled_date=when, scheduled_slot=slot))
        visit = await bs.create_booking_group(customer_id, BookingGroupCreateRequest(
            vehicles=[GroupVehicleRequest(vehicle_id=vehicle_id, service_ids=[service]), GroupVehicleRequest(vehicle_id=v2, service_ids=[service])],
            address_id=address_id, scheduled_date=when, scheduled_slot=slot, payment_method="cash"))
        assert await _seats(db, center_id, when, slot) == 2
        results = await asyncio.gather(*(
            BookingService(db).cancel_booking(b["id"], BookingCancelRequest(reason="cancel car"), "admin", "admin") for b in visit["bookings"]
        ), return_exceptions=True)
        assert not any(isinstance(x, Exception) for x in results)
        assert await _seats(db, center_id, when, slot) == 1, "only the visit's own seat came back"


# ======================================================== BOOK-5: reschedule


async def test_a_double_submitted_move_to_a_new_day_keeps_the_counters_right(db, cleanup):
    center_id, pin = await _center(db, cleanup)
    customer_id, _v, address_id = await _customer(db, cleanup, pin)
    hatch = await get_hatchback_type_id(db)
    service = await get_star_wash_service_id(db)
    d1, slot = await _slot(db, center_id, offset=1)
    d2, d3 = [(now_ist().date() + timedelta(days=n)).isoformat() for n in (2, 3)]
    bs = BookingService(db)
    booking = await bs.create_booking(customer_id, BookingCreateRequest(
        vehicle_type=hatch, address_id=address_id, service_ids=[service], scheduled_date=d1, scheduled_slot=slot))
    await bs.reschedule_booking(booking["id"], BookingRescheduleRequest(scheduled_date=d2, scheduled_slot=slot), customer_id, "customer")
    results = await asyncio.gather(*(
        BookingService(db).reschedule_booking(booking["id"], BookingRescheduleRequest(scheduled_date=d3, scheduled_slot=slot), customer_id, "customer")
        for _ in range(2)
    ), return_exceptions=True)
    assert sum(1 for x in results if not isinstance(x, Exception)) == 1
    assert [await _seats(db, center_id, d, slot) for d in (d1, d2, d3)] == [0, 0, 1]


async def test_a_cancel_racing_a_reschedule_releases_the_right_seat(db, cleanup):
    for _ in range(5):
        center_id, pin = await _center(db, cleanup)
        customer_id, _v, address_id = await _customer(db, cleanup, pin)
        hatch = await get_hatchback_type_id(db)
        service = await get_star_wash_service_id(db)
        d1, slot = await _slot(db, center_id, offset=1)
        d2, d3 = [(now_ist().date() + timedelta(days=n)).isoformat() for n in (2, 3)]
        bs = BookingService(db)
        booking = await bs.create_booking(customer_id, BookingCreateRequest(
            vehicle_type=hatch, address_id=address_id, service_ids=[service], scheduled_date=d1, scheduled_slot=slot))
        await bs.reschedule_booking(booking["id"], BookingRescheduleRequest(scheduled_date=d2, scheduled_slot=slot), customer_id, "customer")
        await asyncio.gather(
            BookingService(db).cancel_booking(booking["id"], BookingCancelRequest(reason="ops cancel"), "admin", "admin"),
            BookingService(db).reschedule_booking(booking["id"], BookingRescheduleRequest(scheduled_date=d3, scheduled_slot=slot), customer_id, "customer"),
            return_exceptions=True,
        )
        final = await db.bookings.find_one({"_id": ObjectId(booking["id"])})
        live = final["status"] != "cancelled"
        on_d3 = final["scheduled_date"].strftime("%Y-%m-%d") == d3
        expected = [0, int(live and not on_d3), int(live and on_d3)]
        assert [await _seats(db, center_id, d, slot) for d in (d1, d2, d3)] == expected


async def test_rescheduling_a_visit_whose_first_car_is_done_moves_a_seat_and_tells_the_customer(db, cleanup, sent):
    center_id, pin = await _center(db, cleanup)
    customer_id, vehicle_id, address_id = await _customer(db, cleanup, pin)
    hatch = await get_hatchback_type_id(db)
    v2 = await make_vehicle(db, customer_id, hatch)
    (captain_id,), manager_id = await _staff(db, cleanup, center_id)
    service = await get_star_wash_service_id(db)
    when, slot = await _slot(db, center_id)
    bs = BookingService(db)
    visit = await bs.create_booking_group(customer_id, BookingGroupCreateRequest(
        vehicles=[GroupVehicleRequest(vehicle_id=vehicle_id, service_ids=[service]), GroupVehicleRequest(vehicle_id=v2, service_ids=[service])],
        address_id=address_id, scheduled_date=when, scheduled_slot=slot, payment_method="cash"))
    ids = [b["id"] for b in sorted(visit["bookings"], key=lambda b: b.get("group_offset_minutes", 0))]
    await bs.assign_captain_to_group(visit["booking_group_id"], BookingAssignCaptainRequest(captain_id=captain_id), manager_id, "manager", center_id)
    await db.bookings.update_many({"booking_group_id": visit["booking_group_id"]}, {"$set": {"estimated_start_at": now_ist() + timedelta(minutes=20)}})
    await bs.start_heading(ids[0], HeadingRequest(latitude=LAT, longitude=LNG, equipment_used=[]), captain_id)
    raw = await db.bookings.find_one({"_id": ObjectId(ids[0])})
    await bs.verify_vehicle(ids[0], VerifyVehicleRequest(service_code=raw["service_code"]), captain_id)
    await bs.capture_before_photo(ids[0], await _photo(db, captain_id), captain_id)
    await bs.capture_after_photo_and_complete(ids[0], await _photo(db, captain_id, "after"), captain_id)
    await bs.captain_cancel(ids[1], CaptainCancelRequest(reason="ran out of time"), captain_id)
    new_when, new_slot = await _slot(db, center_id, offset=3)
    await bs.reschedule_booking(ids[1], BookingRescheduleRequest(scheduled_date=new_when, scheduled_slot=new_slot), manager_id, "manager", center_id)
    car2 = await db.bookings.find_one({"_id": ObjectId(ids[1])})
    assert car2["status"] == "rescheduled" and car2["scheduled_slot"] == new_slot
    assert await _seats(db, center_id, new_when, new_slot) == 1, "the moved car holds a seat in its new slot"
    assert await _seats(db, center_id, when, slot) == 1, "the done car keeps the seat it used"
    assert sum(1 for c in sent if c["user_id"] == customer_id and c["title"] == "Booking rescheduled") == 1


async def test_customers_cannot_reschedule_inside_the_edit_lock(db, cleanup):
    """2026-10-07: a reschedule is a date/slot EDIT — refused within 1 hour
    of the slot (and once the captain is on the way); an assigned booking
    may be moved (the captain is released)."""
    r = await _rig(db, cleanup)
    bs = BookingService(db)
    later_when, later_slot = await _slot(db, r["center_id"], offset=4)
    move = BookingRescheduleRequest(scheduled_date=later_when, scheduled_slot=later_slot)
    soon = now_ist() + timedelta(minutes=50)
    await db.bookings.update_one({"_id": ObjectId(r["booking_id"])}, {"$set": {"slot_start": soon, "slot_end": soon + timedelta(hours=3)}})
    with pytest.raises(BadRequestException, match="1 hour before"):
        await bs.reschedule_booking(r["booking_id"], move, r["customer_id"], "customer")
    far = now_ist() + timedelta(days=2)
    await db.bookings.update_one({"_id": ObjectId(r["booking_id"])}, {"$set": {"slot_start": far, "slot_end": far + timedelta(hours=3)}})
    await bs.assign_captain(r["booking_id"], BookingAssignCaptainRequest(captain_id=r["captain_id"]), r["manager_id"], "manager", r["center_id"])
    await db.bookings.update_one({"_id": ObjectId(r["booking_id"])}, {"$set": {"status": "captain_on_the_way"}})
    with pytest.raises(BadRequestException, match="on the way"):
        await bs.reschedule_booking(r["booking_id"], move, r["customer_id"], "customer")
    await db.bookings.update_one({"_id": ObjectId(r["booking_id"])}, {"$set": {"status": "assigned"}})
    moved = await bs.reschedule_booking(r["booking_id"], move, r["customer_id"], "customer")
    assert moved["status"] == "rescheduled" and moved["captain_id"] is None


# ======================================================== BOOK-6: self-assign


async def test_self_assign_cannot_resurrect_a_cancel_or_yank_a_captain_on_the_road(db, cleanup):
    r = await _rig(db, cleanup)
    manager_svc = BookingService(db)
    real_policy = manager_svc.policy_service.get_policy
    fired = {}

    async def policy_then_customer_cancels():
        result = await real_policy()
        if not fired:
            fired["x"] = await BookingService(db).cancel_booking(r["booking_id"], BookingCancelRequest(reason="changed mind"), r["customer_id"], "customer")
        return result

    manager_svc.policy_service.get_policy = policy_then_customer_cancels
    with pytest.raises(BadRequestException, match="can't be self-assigned"):
        await manager_svc.self_assign(r["booking_id"], r["manager_id"], "manager", r["center_id"])
    assert (await db.bookings.find_one({"_id": ObjectId(r["booking_id"])}))["status"] == "cancelled"

    r2 = await _rig(db, cleanup)
    bs = BookingService(db)
    await bs.assign_captain(r2["booking_id"], BookingAssignCaptainRequest(captain_id=r2["captain_id"]), r2["manager_id"], "manager", r2["center_id"])
    await db.bookings.update_one({"_id": ObjectId(r2["booking_id"])}, {"$set": {"estimated_start_at": now_ist() + timedelta(minutes=20)}})
    manager_svc = BookingService(db)
    real_policy = manager_svc.policy_service.get_policy
    fired2 = {}

    async def policy_then_captain_heads_out():
        result = await real_policy()
        if not fired2:
            fired2["x"] = await BookingService(db).start_heading(r2["booking_id"], HeadingRequest(latitude=LAT, longitude=LNG, equipment_used=[]), r2["captain_id"])
        return result

    manager_svc.policy_service.get_policy = policy_then_captain_heads_out
    with pytest.raises(BadRequestException):
        await manager_svc.self_assign(r2["booking_id"], r2["manager_id"], "manager", r2["center_id"])
    final = await db.bookings.find_one({"_id": ObjectId(r2["booking_id"])})
    assert final["status"] == "captain_on_the_way" and final["captain_id"] == r2["captain_id"]


# ======================================================== BOOK-7: manager "log a done job"


async def test_a_double_submitted_logged_job_is_logged_once(db, cleanup):
    center_id, _pin = await _center(db, cleanup)
    manager_id = await make_manager(db, center_id)
    cleanup.append(("users", {"_id": ObjectId(manager_id)}))
    hatch = await get_hatchback_type_id(db)
    service = await get_star_wash_service_id(db)
    phone = f"97{random.randint(10000000, 99999999)}"
    cleanup.append(("users", {"phone": phone}))
    when = now_ist() - timedelta(hours=1)
    center = await db.service_centers.find_one({"_id": ObjectId(center_id)})
    hhmm = when.strftime("%H:%M")
    if not (center["working_hours_start"] <= hhmm < center["working_hours_end"]):
        when, hhmm = (when - timedelta(days=1)).replace(hour=11, minute=0), "11:00"
    payload = ManagerLogBookingRequest(
        customer_name="Walk In", customer_phone=phone,
        lines=[QuickBookingLine(vehicle_type=hatch, service_ids=[service])],
        scheduled_date=when.strftime("%Y-%m-%d"), service_time=hhmm, address_line="12 MG Road", send_whatsapp=False,
    )
    results = await asyncio.gather(*(
        BookingService(db).create_manager_logged_visit(payload, manager_id=manager_id, manager_center_id=center_id) for _ in range(2)
    ), return_exceptions=True)
    user = await db.users.find_one({"phone": phone})
    cleanup += [("bookings", {"customer_id": str(user["_id"])}), ("addresses", {"owner_id": str(user["_id"])})]
    assert sum(1 for x in results if not isinstance(x, Exception)) == 1
    assert await db.bookings.count_documents({"customer_id": str(user["_id"]), "completed_by_role": "manager"}) == 1
    assert await db.booking_locks.count_documents({"_id": {"$regex": f"^manager_log:{user['_id']}"}}) == 0, "the claim is let go"


# ======================================================== BOOK-9: one visit, one payment state


async def _mixed_visit(db, cleanup):
    center_id, pin = await _center(db, cleanup)
    customer_id, vehicle_id, address_id = await _customer(db, cleanup, pin)
    hatch = await get_hatchback_type_id(db)
    v2 = await make_vehicle(db, customer_id, hatch)
    service = await get_star_wash_service_id(db)
    sub_id = await _pass(db, cleanup, customer_id, service, vehicle_id=vehicle_id)
    when, slot = await _slot(db, center_id)
    visit = await BookingService(db).create_booking_group(customer_id, BookingGroupCreateRequest(
        vehicles=[GroupVehicleRequest(vehicle_id=vehicle_id, service_ids=[service], subscription_id=sub_id),
                  GroupVehicleRequest(vehicle_id=v2, service_ids=[service])],
        address_id=address_id, scheduled_date=when, scheduled_slot=slot, payment_method="online"))
    cars = sorted(await db.bookings.find({"booking_group_id": visit["booking_group_id"]}).to_list(5), key=lambda c: c["group_offset_minutes"])
    return customer_id, visit, cars


async def test_a_pass_car_waits_with_its_visits_online_payment_and_confirms_with_it(db, cleanup, sent):
    customer_id, _visit, cars = await _mixed_visit(db, cleanup)
    pass_car, paid_car = cars
    assert pass_car["total_amount"] == 0 and paid_car["total_amount"] > 0
    assert [c["status"] for c in cars] == ["awaiting_payment", "awaiting_payment"], "nothing is dispatchable before the visit is paid"
    assert pass_car["payment_status"] == "paid" and pass_car["payment_method"] == "subscription"

    await BookingService(db).confirm_awaiting_payment_booking(str(paid_car["_id"]), "Paid online")
    after = {str(c["_id"]): c for c in await db.bookings.find({"_id": {"$in": [c["_id"] for c in cars]}}).to_list(5)}
    assert {c["status"] for c in after.values()} == {"pending"}
    assert after[str(pass_car["_id"])]["payment_method"] == "subscription"
    # (Title is "Booking confirmed" since NTF-07 — it was "<service> booked".)
    assert sum(1 for c in sent if c["user_id"] == customer_id and c["title"] == "Booking confirmed") == 1, "announced once"


async def test_switching_a_mixed_visit_to_cash_confirms_the_pass_car_too(db, cleanup):
    customer_id, visit, cars = await _mixed_visit(db, cleanup)
    result = await BookingService(db).switch_group_to_cash(visit["booking_group_id"], customer_id)
    assert result["switched_count"] == 1
    after = {str(c["_id"]): c for c in await db.bookings.find({"booking_group_id": visit["booking_group_id"]}).to_list(5)}
    assert {c["status"] for c in after.values()} == {"pending"}
    assert after[str(cars[0]["_id"])]["payment_method"] == "subscription" and after[str(cars[1]["_id"])]["payment_method"] == "cash"


async def test_cancelling_the_paying_car_confirms_the_pass_car_left_waiting(db, cleanup):
    customer_id, _visit, cars = await _mixed_visit(db, cleanup)
    await BookingService(db).cancel_booking(str(cars[1]["_id"]), BookingCancelRequest(reason="only the pass car"), "admin", "admin")
    assert (await db.bookings.find_one({"_id": cars[0]["_id"]}))["status"] == "pending"


# ======================================================== BOOK-10: stale sweep flag


async def test_a_stale_late_start_sweep_does_not_flag_a_captain_already_on_the_way(db, cleanup):
    r = await _rig(db, cleanup)
    bs = BookingService(db)
    await bs.assign_captain(r["booking_id"], BookingAssignCaptainRequest(captain_id=r["captain_id"]), r["manager_id"], "manager", r["center_id"])
    past = now_ist() - timedelta(minutes=10)
    today = datetime.strptime(now_ist().date().isoformat(), "%Y-%m-%d")
    await db.bookings.update_one({"_id": ObjectId(r["booking_id"])}, {"$set": {
        "estimated_start_at": past, "assigned_at": past - timedelta(hours=5), "scheduled_date": today}})
    due = [b for b in await bs.find_bookings_late_to_start() if str(b["_id"]) == r["booking_id"]]
    assert due, "the sweep picks the late booking"
    await BookingService(db).start_heading(r["booking_id"], HeadingRequest(latitude=LAT, longitude=LNG, equipment_used=[]), r["captain_id"])
    await bs.flag_late_to_start(due[0])
    raw = await db.bookings.find_one({"_id": ObjectId(r["booking_id"])})
    assert raw["status"] == "captain_on_the_way" and raw.get("issue_flag") != "captain_not_started"
    verified = await BookingService(db).verify_vehicle(r["booking_id"], VerifyVehicleRequest(service_code=raw["service_code"]), r["captain_id"])
    assert verified["vehicle_verified"] is True
    # The generic sweep flagger is state-guarded too.
    assert await bs.flag_issue(r["booking_id"], "captain_not_reached", "stale") is False


# ======================================================== BOOK-11: captain release


async def test_a_released_job_starts_clean_for_the_next_captain(db, cleanup):
    r = await _rig(db, cleanup, captains=2)
    cap1, cap2 = r["captains"]
    bs = BookingService(db)
    original = await db.bookings.find_one({"_id": ObjectId(r["booking_id"])})
    await bs.assign_captain(r["booking_id"], BookingAssignCaptainRequest(captain_id=cap1), "admin", "admin", None)
    late = now_ist() - timedelta(minutes=original["duration_minutes"] + 5)
    await db.bookings.update_one({"_id": ObjectId(r["booking_id"])}, {"$set": {"estimated_start_at": late, "assigned_at": now_ist() - timedelta(hours=5)}})
    started = await bs.start_heading(r["booking_id"], HeadingRequest(latitude=LAT, longitude=LNG, equipment_used=[]), cap1)
    assert started["captain_start_stage"] in ("late", "severely_late") and started["captain_earning"] < original["captain_earning"]
    raw = await db.bookings.find_one({"_id": ObjectId(r["booking_id"])})
    await bs.verify_vehicle(r["booking_id"], VerifyVehicleRequest(service_code=raw["service_code"]), cap1)
    await bs.captain_cancel(r["booking_id"], CaptainCancelRequest(reason="emergency"), cap1)

    released = await db.bookings.find_one({"_id": ObjectId(r["booking_id"])})
    assert released["vehicle_verified"] is False and released["captain_start_stage"] is None and not released.get("issue_flag")
    for field in ("captain_earning", "captain_service_pay", "platform_earning"):
        assert released[field] == original[field], field

    await bs.assign_captain(r["booking_id"], BookingAssignCaptainRequest(captain_id=cap2), "admin", "admin", None)
    await db.bookings.update_one({"_id": ObjectId(r["booking_id"])}, {"$set": {"estimated_start_at": now_ist() + timedelta(minutes=10)}})
    await bs.start_heading(r["booking_id"], HeadingRequest(latitude=LAT, longitude=LNG, equipment_used=[]), cap2)
    with pytest.raises(BadRequestException, match="Verify"):
        await bs.capture_before_photo(r["booking_id"], await _photo(db, cap2), cap2)
    fresh = await db.bookings.find_one({"_id": ObjectId(r["booking_id"])})
    assert fresh["captain_earning"] == original["captain_earning"], "the next captain is paid in full"


# ======================================================== BOOK-13: switch to cash vs expiry


async def test_switch_to_cash_losing_to_expiry_says_released_not_paid(db, cleanup):
    r = await _rig(db, cleanup, method=PaymentMethod.ONLINE)
    assert r["booking"]["status"] == "awaiting_payment"
    svc = BookingService(db)
    real = svc.visit_is_prepaid

    async def prepaid_then_expiry(booking):
        result = await real(booking)
        await BookingService(db).cancel_booking(
            r["booking_id"], BookingCancelRequest(reason="Payment wasn't completed in time, so the slot was released."), "system", "admin"
        )
        return result

    svc.visit_is_prepaid = prepaid_then_expiry
    with pytest.raises(BadRequestException) as exc:
        await svc.switch_to_cash(r["booking_id"], r["customer_id"])
    assert "released" in exc.value.message and "paid" not in exc.value.message


# ======================================================== notifications


async def test_a_restored_booking_is_announced_on_whatsapp(db, cleanup, sent):
    r = await _rig(db, cleanup)
    bs = BookingService(db)
    await bs.soft_delete_booking(r["booking_id"], "admin-x")
    await bs.restore_booking(r["booking_id"])
    restored = [c for c in sent if c["user_id"] == r["customer_id"] and c["title"].endswith("restored")]
    assert len(restored) == 1 and restored[0]["wa_event"] == "booking_confirmed" and restored[0].get("send_whatsapp", True) is True


async def test_a_staff_reschedule_message_reads_like_every_other_message(db, cleanup, sent):
    r = await _rig(db, cleanup)
    new_when, new_slot = await _slot(db, r["center_id"], offset=3)
    await BookingService(db).reschedule_booking(
        r["booking_id"], BookingRescheduleRequest(scheduled_date=new_when, scheduled_slot=new_slot), r["manager_id"], "manager", r["center_id"]
    )
    msg = next(c for c in sent if c.get("wa_event") == "reschedule_confirmation")
    wa_date, wa_slot = msg["wa_params"][2], msg["wa_params"][3]
    assert wa_date == datetime.strptime(new_when, "%Y-%m-%d").strftime("%d %b %Y")
    assert ("AM" in wa_slot or "PM" in wa_slot) and wa_slot != new_slot


async def test_a_reassignment_tells_the_customer_who_is_coming_now(db, cleanup, sent):
    r = await _rig(db, cleanup, captains=2)
    cap1, cap2 = r["captains"]
    bs = BookingService(db)
    await bs.assign_captain(r["booking_id"], BookingAssignCaptainRequest(captain_id=cap1), "admin", "admin", None)
    sent.clear()
    await bs.reassign_captain(r["booking_id"], ReassignCaptainRequest(captain_id=cap2), "admin", "admin", None)
    told = [c for c in sent if c["user_id"] == r["customer_id"] and c.get("wa_event") == "captain_assigned"]
    name = (await db.users.find_one({"_id": ObjectId(cap2)}))["full_name"]
    assert len(told) == 1 and told[0]["wa_params"][0] == name


# ======================================================== P3: payment method from the client


async def test_plan_payment_needs_a_plan_and_the_legacy_online_value_is_online(db, cleanup):
    center_id, pin = await _center(db, cleanup)
    customer_id, vehicle_id, address_id = await _customer(db, cleanup, pin)
    service = await get_star_wash_service_id(db)
    when, slot = await _slot(db, center_id)
    bs = BookingService(db)

    def request(method):
        return BookingCreateRequest(vehicle_id=vehicle_id, address_id=address_id, service_ids=[service],
                                    scheduled_date=when, scheduled_slot=slot, payment_method=method)

    with pytest.raises(BadRequestException, match="plan"):
        await bs.create_booking(customer_id, request(PaymentMethod.SUBSCRIPTION))
    booking = await bs.create_booking(customer_id, request(PaymentMethod.ONLINE_PLACEHOLDER))
    assert booking["status"] == "awaiting_payment" and booking["payment_method"] == "online"


# ======================================================== SCALE / DB


async def test_the_late_start_sweep_finds_a_due_booking_behind_500_others(db, cleanup):
    marker = f"scale-{ObjectId()}"
    cleanup.append(("bookings", {"scale_marker": marker}))
    today = BookingService._sweep_day(0)
    later = now_ist() + timedelta(hours=3)
    rows = [{
        "scale_marker": marker, "booking_number": f"SC{marker[-6:]}{i}", "customer_id": f"{marker}-{i}", "service_center_id": marker,
        "status": "assigned", "scheduled_date": today, "scheduled_slot": "21:00-23:00", "estimated_start_at": later,
        "duration_minutes": 45, "is_deleted": False, "self_assigned": False,
    } for i in range(600)]
    await db.bookings.insert_many(rows)
    due = {
        "scale_marker": marker, "booking_number": f"SC{marker[-6:]}due", "customer_id": f"{marker}-due", "service_center_id": marker,
        "status": "assigned", "scheduled_date": today, "scheduled_slot": "06:00-09:00", "estimated_start_at": now_ist() - timedelta(minutes=10),
        "duration_minutes": 45, "is_deleted": False, "self_assigned": False,
    }
    due_id = (await db.bookings.insert_one(due)).inserted_id
    found = await BookingService(db).find_bookings_late_to_start()
    assert any(b["_id"] == due_id for b in found)


async def test_searching_by_number_is_an_exact_lookup_that_still_finds_the_booking(db, cleanup):
    r = await _rig(db, cleanup)
    number = r["booking"]["booking_number"]
    digits = number[2:].lstrip("0") or "0"
    clauses = await center_queue_filters(db, search=digits)
    assert any(
        isinstance(c.get("booking_number"), dict) and number in c["booking_number"].get("$in", [])
        for c in clauses[0]["$or"]
    )
    hits = await db.bookings.find({"$and": [{"service_center_id": r["center_id"]}, *clauses]}).to_list(5)
    assert [h["booking_number"] for h in hits] == [number]


async def test_the_captain_trail_returns_the_latest_points_in_order(db, cleanup):
    from app.repositories.captain_location_repository import CaptainLocationRepository

    captain_id = f"trail-{ObjectId()}"
    cleanup.append(("captain_locations", {"captain_id": captain_id}))
    start = datetime.now(timezone.utc) - timedelta(hours=6)
    await db.captain_locations.insert_many([
        {"captain_id": captain_id, "latitude": LAT, "longitude": LNG, "at": start + timedelta(seconds=25 * i), "n": i} for i in range(600)
    ])
    rows = await CaptainLocationRepository(db).list_for_captain(captain_id, start - timedelta(minutes=1), limit=500)
    assert len(rows) == 500 and rows[0]["n"] == 100 and rows[-1]["n"] == 599
    assert [row["n"] for row in rows] == sorted(row["n"] for row in rows)


async def test_queue_and_captain_indexes_exist_and_the_queue_count_is_capped(db, cleanup, monkeypatch):
    from app.repositories.booking_repository import BookingRepository

    keys = [list(spec["key"]) for spec in (await db.bookings.index_information()).values()]
    for wanted in (
        [("service_center_id", 1), ("created_at", -1), ("_id", -1)],
        [("service_center_id", 1), ("scheduled_date", 1), ("scheduled_slot", 1), ("_id", 1)],
        [("service_center_id", 1), ("captain_start_stage", 1), ("scheduled_date", 1), ("scheduled_slot", 1), ("_id", 1)],
        [("captain_id", 1), ("status", 1), ("scheduled_date", 1), ("scheduled_slot", 1), ("_id", 1)],
        [("status", 1), ("scheduled_date", 1), ("scheduled_slot", 1), ("_id", 1)],
    ):
        assert [tuple(k) for k in wanted] in [[tuple(k) for k in key] for key in keys], wanted

    marker = f"queue-{ObjectId()}"
    cleanup.append(("bookings", {"service_center_id": marker}))
    await db.bookings.insert_many([
        {"service_center_id": marker, "booking_number": f"Q{marker[-6:]}{i}", "status": "completed", "is_deleted": False,
         "created_at": datetime.now(timezone.utc)} for i in range(4)
    ])
    monkeypatch.setattr(BookingRepository, "QUEUE_COUNT_CAP", 3)
    items, total = await BookingRepository(db).list_queue({"service_center_id": marker}, 1, 10)
    assert len(items) == 4 and total == 3 and BookingRepository.is_capped_total(total)


async def test_one_failing_index_build_does_not_stop_the_rest(db):
    from app.core.database import _IndexBuildDb

    name = f"index_probe_{ObjectId()}"
    try:
        await db[name].insert_many([{"k": 1}, {"k": 1}])
        tolerant = _IndexBuildDb(db)
        assert await tolerant[name].create_index("k", unique=True) is None  # legacy duplicates — skipped, not raised
        await getattr(tolerant, name).create_index("other")
        assert "other_1" in await db[name].index_information()
    finally:
        await db.drop_collection(name)
