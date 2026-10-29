"""Attacks on the coupon system, each pinned as a test (see the attack table
in docs/ARCHITECTURE.md §7.11). A test that was written FIRST and failed is
marked FIXED; the rest document a defence that already held."""
import asyncio
import random
from datetime import datetime, timedelta, timezone

import pytest
from bson import ObjectId
from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError

from app.core.config import settings
from app.core.exceptions import AppException, BadRequestException
from app.core.security import create_access_token
from app.models.enums import PaymentMethod
from app.schemas.booking_adjust_schema import BookingDiscountRequest
from app.schemas.booking_schema import BookingCancelRequest, BookingCreateRequest
from app.schemas.payment_schema import CreateOrderRequest
from app.services.booking_adjust_service import BookingAdjustService
from app.services.booking_service import BookingService
from app.services.coupon_service import CouponService
from app.utils.coupon_codes import normalise_code
from app.utils.timezone import now_ist
from tests.factories import get_hatchback_type_id, get_star_wash_service_id, make_customer, make_manager
from tests.test_coupon_bundles_redeem import _center, _customer, _req, _slot, claim, code_doc, make_bundle, user
from tests.test_coupon_rules import _book_rig, booking_discount, make_coupon, svc

pytestmark = pytest.mark.asyncio


def token(uid, role="customer", center=None):
    return {"Authorization": f"Bearer {create_access_token(uid, role, {'service_center_id': center, 'tv': 0})}"}


def client():
    from app.main import app

    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


# AT-01 --------------------------------------------------------------- services-only coupon on a plan / society / custom
async def test_at01_a_services_only_coupon_cannot_discount_a_plan(db, cleanup):
    code = await make_coupon(db, cleanup)
    uid = await user(db, cleanup)
    for use in ("plans", "society", "custom"):
        with pytest.raises(BadRequestException, match="Not Valid For"):
            await CouponService(db).validate_and_compute_discount(code, 999, uid, use=use)


# AT-02 --------------------------------------------------------------- first-booking coupon, second booking
async def test_at02_first_booking_coupon_refused_on_a_second_booking_even_via_a_new_account_with_the_same_car_or_phone_format(db, cleanup):
    center_id, pin = await _center(db, cleanup)
    service = await get_star_wash_service_id(db)
    plate = f"MP09ZZ{random.randint(1000, 9999)}"
    from tests.factories import make_address, make_customer_with_vehicle, make_vehicle

    hatch = await get_hatchback_type_id(db)
    a_id, a_veh, a_addr = await make_customer_with_vehicle(db, hatch, pincode=pin)
    await db.vehicles.update_one({"_id": ObjectId(a_veh)}, {"$set": {"registration_number": plate}})
    b_id = await make_customer(db)
    b_veh = await make_vehicle(db, b_id, hatch, registration_number=plate)
    b_addr = await make_address(db, b_id, pincode=pin)
    for uid in (a_id, b_id):
        cleanup += [("users", {"_id": ObjectId(uid)}), ("vehicles", {"owner_id": uid}), ("addresses", {"owner_id": uid}), ("bookings", {"customer_id": uid}),
                    ("coupon_usages", {"user_id": uid}), ("notifications", {"user_id": uid})]
    code = await make_coupon(db, cleanup, first_booking_only=True, stackable_with_offers=True)
    when, slot = await _slot(db, center_id)
    bs = BookingService(db)
    await bs.create_booking(a_id, BookingCreateRequest(vehicle_id=a_veh, address_id=a_addr, service_ids=[service], scheduled_date=when,
                                                       scheduled_slot=slot, payment_method=PaymentMethod.CASH))
    when2, slot2 = await _slot(db, center_id, offset=3)
    # The same customer's second booking, and a NEW account on the SAME car: both refused.
    with pytest.raises(BadRequestException, match="First Bookings Only"):
        await bs.create_booking(a_id, BookingCreateRequest(vehicle_id=a_veh, address_id=a_addr, service_ids=[service], scheduled_date=when2,
                                                           scheduled_slot=slot2, payment_method=PaymentMethod.CASH, coupon_code=code))
    with pytest.raises(BadRequestException, match="First Bookings Only"):
        await bs.create_booking(b_id, BookingCreateRequest(vehicle_id=b_veh, address_id=b_addr, service_ids=[service], scheduled_date=when2,
                                                           scheduled_slot=slot2, payment_method=PaymentMethod.CASH, coupon_code=code))
    assert (await db.coupons.find_one({"code": code}))["total_used"] == 0
    # A phone written another way is the same account — never a "new" customer.
    from app.services.auth_service import AuthService

    phone = (await db.users.find_one({"_id": ObjectId(a_id)}))["phone"]
    again = await AuthService(db).ensure_customer_by_phone(f"+91 {phone[:5]}-{phone[5:]}", "Someone")
    assert str(again["_id"]) == a_id


# AT-03/04 ------------------------------------------------------------- change service / car type / lines after the quote
async def test_at03_changing_the_service_or_car_after_the_quote_is_rechecked_at_create(db, cleanup):
    center_id, service, when, slot, c = await _book_rig(db, cleanup)
    wrong_car = await make_coupon(db, cleanup, applicable_vehicle_types=[str(ObjectId())])
    other_service = await make_coupon(db, cleanup, applicable_service_ids=[str(ObjectId())])
    bs = BookingService(db)
    for code, why in ((wrong_car, "Vehicle Type"), (other_service, "Selected Service")):
        with pytest.raises(BadRequestException, match=why):
            await bs.create_booking(c[0], _req(c, service, when, slot, code))
    assert await db.bookings.count_documents({"customer_id": c[0]}) == 0


# AT-05 --------------------------------------------------------------- parallel requests past the total and per-user limits
async def test_at05_parallel_redemptions_never_exceed_total_or_per_user_limits(db, cleanup):
    code = await make_coupon(db, cleanup, total_usage_limit=5, usage_limit_per_user=1)
    users = [await user(db, cleanup) for _ in range(9)]
    svc_ = CouponService(db)
    coupon = await svc_.repo.find_by_code(code)

    async def go(uid):
        try:
            await svc_.record_usage(str(coupon["_id"]), uid, str(ObjectId()))
            return uid
        except BadRequestException:
            return None

    won = [w for w in await asyncio.gather(*(go(u) for u in users + users[:3])) if w]
    assert len(won) == 5 and len(set(won)) == 5  # the total holds, nobody twice
    assert (await db.coupons.find_one({"code": code}))["total_used"] == 5


# AT-06/07 ------------------------------------------------------------ cancel / delete / restore never double-restore or go negative
async def test_at06_cancel_delete_restore_never_restore_a_use_twice(db, cleanup):
    center_id, service, when, slot, c = await _book_rig(db, cleanup)
    b, (code,) = await make_bundle(db, cleanup, value=100)
    bs = BookingService(db)
    booking = await bs.create_booking(c[0], _req(c, service, when, slot, code))
    assert (await code_doc(db, code))["used_count"] == 1
    await bs.cancel_booking(booking["id"], BookingCancelRequest(reason="Changed my plans"), c[0], "customer")
    assert (await code_doc(db, code))["used_count"] == 0
    await bs.soft_delete_booking(booking["id"], "admin-x")           # a second "give it back"
    await CouponService(db).reverse_usage(code, c[0], booking["id"])  # and a third
    assert (await code_doc(db, code))["used_count"] == 0
    # Someone else takes it; the stale reversals can't free THEIR use.
    other = await user(db, cleanup)
    await claim(db, code, other)
    await CouponService(db).reverse_usage(code, c[0], booking["id"])
    assert (await code_doc(db, code))["used_count"] == 1


# AT-08/09 ------------------------------------------------------------- cap and minimum survive a booking edit
async def test_at08_an_edit_cannot_escape_the_percent_cap_or_the_minimum_order(db, cleanup):
    code = await make_coupon(db, cleanup, value=50, ctype="percentage", max_discount_amount=100, min_order_value=300)
    bs = BookingService(db)
    car = {"coupon_code": code, "service_center_id": None}
    for subtotal, expected in ((349, 100), (3000, 100)):  # more services -> still capped
        assert await bs._held_coupon_discount(car, subtotal, [svc()], {}, "vt", False) == (float(expected), None)
    discount, why = await bs._held_coupon_discount(car, 250, [svc()], {}, "vt", False)  # removed a service below the minimum
    assert discount == 0.0 and "Minimum order" in why


# AT-10/11/12/13 ------------------------------------------------------ amounts: cap, negative, > 100 %, > total, paise
async def test_at10_amounts_are_clamped_whatever_a_bad_row_says(db):
    f = CouponService._standard_discount
    assert f({"coupon_type": "percentage", "value": 50, "max_discount_amount": 100}, 100000) == 100
    assert f({"coupon_type": "flat", "value": -50}, 349) == 0
    assert f({"coupon_type": "percentage", "value": -10}, 349) == 0
    assert f({"coupon_type": "percentage", "value": 400}, 349) == 349
    assert f({"coupon_type": "flat", "value": 10**9}, 349) == 349
    assert f({"coupon_type": "flat", "value": 100}, 0.4) == 0           # a few paise: nothing off, never negative
    assert f({"coupon_type": "percentage", "value": 33}, 99.99) == 33.0  # whole rupees


async def test_at11_the_admin_form_refuses_negative_and_over_100_percent(db):
    from app.schemas.coupon_schema import CouponCreateRequest

    now = datetime.now(timezone.utc)
    base = dict(code="ATTACK1", coupon_type="percentage", value=10, valid_from=now, valid_until=now + timedelta(days=1))
    for bad in ({"value": -1}, {"value": 101}, {"max_discount_amount": -5}, {"usage_limit_per_user": 0}, {"total_usage_limit": 0}, {"min_order_value": -1}):
        with pytest.raises(ValidationError):
            CouponCreateRequest(**{**base, **bad})


# AT-14 --------------------------------------------------------------- manager discount stacks AFTER the coupon, never below zero
async def test_at14_manager_discount_is_applied_after_the_coupon_and_total_never_goes_below_zero(db, cleanup):
    center_id, service, when, slot, c = await _book_rig(db, cleanup)
    manager = await make_manager(db, center_id)
    cleanup.append(("users", {"_id": ObjectId(manager)}))
    cleanup += [("booking_adjustments", {"booking_id": {"$exists": True}}), ("audit_logs", {"actor_id": manager})]
    code = await make_coupon(db, cleanup, value=100, stackable_with_offers=True)
    bs = BookingService(db)
    booking = await bs.create_booking(c[0], _req(c, service, when, slot, code))
    bill = booking["total_amount"]
    assert bill == booking["subtotal"] - 100
    adjust = BookingAdjustService(db)
    who = {"id": manager, "role": "admin", "center": center_id}
    with pytest.raises(AppException):
        await adjust.set_discount(booking["id"], BookingDiscountRequest(amount=int(bill) + 50, reason="Trying to go below zero"), who)
    result = await adjust.set_discount(booking["id"], BookingDiscountRequest(amount=int(bill), reason="Full goodwill discount"), who)
    after = await db.bookings.find_one({"_id": ObjectId(booking["id"])})
    assert after["total_amount"] == 0 and after["coupon_code"] == code and result["changed"]
    # Cancelling still gives the coupon back.
    await bs.cancel_booking(booking["id"], BookingCancelRequest(reason="Changed my plans"), c[0], "customer")
    assert (await db.coupons.find_one({"code": code}))["total_used"] == 0


# AT-15 --------------------------------------------------------------- refund never exceeds what was paid
async def test_at15_cancelling_a_coupon_booking_credits_only_what_was_paid_and_returns_the_coupon(db, cleanup, monkeypatch):
    from tests.test_booking_audit_fixes import _StubClient
    from app.services import payment_service
    from app.services.customer_wallet_service import CustomerWalletService
    from app.schemas.payment_schema import VerifyPaymentRequest
    from app.services.payment_service import PaymentService, _expected_signature

    monkeypatch.setattr(payment_service.settings, "RAZORPAY_KEY_ID", "rzp_test_stub")
    monkeypatch.setattr(payment_service.settings, "RAZORPAY_KEY_SECRET", "stub_secret_key")
    monkeypatch.setattr(payment_service, "_razorpay_client", lambda: _StubClient())
    center_id, service, when, slot, c = await _book_rig(db, cleanup)
    code = await make_coupon(db, cleanup, value=100, stackable_with_offers=True)
    bs = BookingService(db)
    booking = await bs.create_booking(c[0], BookingCreateRequest(
        vehicle_id=c[1], address_id=c[2], service_ids=[service], scheduled_date=when, scheduled_slot=slot,
        payment_method=PaymentMethod.ONLINE, coupon_code=code))
    paid_amount = booking["total_amount"]
    payments = PaymentService(db)
    order = await payments.create_order(c[0], CreateOrderRequest(purpose="booking", booking_id=booking["id"]))
    assert order["amount"] == round(paid_amount * 100) and paid_amount == booking["subtotal"] - 100  # Razorpay is asked for the NET amount
    pay_id = f"pay_atk_{random.randint(1, 10**9)}"
    await payments.verify_payment(c[0], VerifyPaymentRequest(
        razorpay_order_id=order["order_id"], razorpay_payment_id=pay_id, razorpay_signature=_expected_signature(order["order_id"], pay_id)))
    await bs.cancel_booking(booking["id"], BookingCancelRequest(reason="Changed my mind"), c[0], "customer")
    assert await CustomerWalletService(db).balance(c[0]) == paid_amount  # never the pre-coupon subtotal
    assert (await db.coupons.find_one({"code": code}))["total_used"] == 0
    cleanup.append(("customer_wallets", {"customer_id": c[0]}))


async def test_at15b_the_create_order_request_has_no_amount_to_tamper_with(db):
    assert not {"amount", "amount_paise", "discount_amount", "coupon_code"} & set(CreateOrderRequest.model_fields)


# AT-16 --------------------------------------------------------------- restore after the code expired
async def test_at16_a_restored_booking_keeps_its_coupon_even_if_the_code_expired_meanwhile(db, cleanup):
    center_id, service, when, slot, c = await _book_rig(db, cleanup)
    code = await make_coupon(db, cleanup, value=100, stackable_with_offers=True)
    bs = BookingService(db)
    booking = await bs.create_booking(c[0], _req(c, service, when, slot, code))
    await bs.soft_delete_booking(booking["id"], "admin-x")
    await db.coupons.update_one({"code": code}, {"$set": {"valid_until": now_ist() - timedelta(days=1)}})
    await bs.restore_booking(booking["id"])  # the customer already got this price; expiry is for NEW uses
    assert (await db.coupons.find_one({"code": code}))["total_used"] == 1
    with pytest.raises(BadRequestException):
        await CouponService(db).validate_and_compute_discount(code, 500, await user(db, cleanup), use="services")


# AT-17 --------------------------------------------------------------- bundle expires while a booking is pending
async def test_at17_a_booking_that_holds_a_bundle_code_survives_the_bundle_expiring(db, cleanup):
    center_id, service, when, slot, c = await _book_rig(db, cleanup)
    b, (code,) = await make_bundle(db, cleanup, value=100)
    bs = BookingService(db)
    booking = await bs.create_booking(c[0], _req(c, service, when, slot, code))
    await db.coupon_bundles.update_one({"_id": b["_id"]}, {"$set": {"valid_until": datetime.now(timezone.utc) - timedelta(minutes=1)}})
    stored = await db.bookings.find_one({"_id": ObjectId(booking["id"])})
    assert stored["coupon_code"] == code and stored["discount_amount"] == 100  # the claim was made while valid
    await bs.cancel_booking(booking["id"], BookingCancelRequest(reason="Changed my plans"), c[0], "customer")
    assert (await code_doc(db, code))["used_count"] == 0  # and it can still be given back


# AT-18 --------------------------------------------------------------- look-alikes, zero-width, unicode folding   (FIXED: ı/ſ folded into I/S)
async def test_at18_lookalike_and_unicode_codes_never_match(db, cleanup):
    code = await make_coupon(db, cleanup)
    uid = await user(db, cleanup)
    svc_ = CouponService(db)
    cyrillic = "С" + code[1:]                      # Cyrillic С
    dotless = code.replace("I", "ı") if "I" in code else code.replace("S", "ſ")
    for fake in (cyrillic, dotless, "Ｃ" + code[1:]):
        if fake != code:
            with pytest.raises(BadRequestException, match="Invalid coupon code"):
                await svc_.validate_and_compute_discount(fake, 500, uid, use="services")
    assert normalise_code("ı") != "I" and normalise_code("CO​UPON") == "COUPON"
    # Zero-width characters are invisible, so they are simply dropped.
    assert (await svc_.validate_and_compute_discount(code[:3] + "​" + code[3:], 500, uid, use="services"))[1] == 100


# AT-19/20 ------------------------------------------------------------ operators, objects and oversized codes
async def test_at19_operator_objects_and_huge_codes_are_refused_at_the_door(db, cleanup):
    uid = await user(db, cleanup)
    async with client() as c:
        h = token(uid)
        for bad in ({"$ne": ""}, {"$gt": ""}, ["A"], 12345):
            res = await c.post("/api/v1/coupons/validate", json={"code": bad, "order_value": 500}, headers=h)
            assert res.status_code == 422, (bad, res.text)
        res = await c.post("/api/v1/coupons/validate", json={"code": "A" * 5000, "order_value": 500}, headers=h)
        assert res.status_code == 422
        res = await c.post("/api/v1/coupons/validate", json={"code": "ok-code", "order_value": -5}, headers=h)
        assert res.status_code == 422
        res = await c.get("/api/v1/coupons/public/%24ne", headers=h)  # '$ne' as a path value: just an unknown code
        assert res.status_code == 400
    with pytest.raises(ValidationError):
        BookingCreateRequest(vehicle_type="x", address_id="a", scheduled_date="2026-11-01", scheduled_slot="09:00-12:00", payment_method="cash", coupon_code="X" * 33)


# AT-21/22 ------------------------------------------------------------ brute force, rotating device ids, anonymous quote oracle
async def test_at21_rotating_device_ids_does_not_reset_the_user_counter(db, cleanup, monkeypatch):
    monkeypatch.setattr(settings, "COUPON_MISS_LIMIT", 3)
    cleanup.append(("rate_counters", {"_id": {"$regex": "^coupon-miss"}}))
    uid = await user(db, cleanup)
    async with client() as c:
        for i in range(3):
            h = {**token(uid), "X-Device-Id": f"rotating-device-{i:016d}"}
            res = await c.post("/api/v1/coupons/validate", json={"code": f"GUESS-{i}0000", "order_value": 500}, headers=h)
            assert res.status_code == 400
        h = {**token(uid), "X-Device-Id": "a-brand-new-device-0000000"}
        res = await c.post("/api/v1/coupons/validate", json={"code": "GUESS-90000", "order_value": 500}, headers=h)
        assert res.status_code == 429 and res.json()["error_code"] == "COUPON_HELD"


async def test_at22_the_anonymous_quote_cannot_be_used_to_enumerate_codes(db, cleanup, monkeypatch):
    monkeypatch.setattr(settings, "COUPON_MISS_LIMIT", 100)
    monkeypatch.setattr(settings, "COUPON_MISS_IP_LIMIT", 3)
    cleanup.append(("rate_counters", {"_id": {"$regex": "^coupon-miss"}}))
    hatch, star = await get_hatchback_type_id(db), await get_star_wash_service_id(db)
    good = await make_coupon(db, cleanup)
    body = lambda code: {"lines": [{"vehicle_type": hatch, "quantity": 1, "service_ids": [star]}], "coupon_code": code}  # noqa: E731
    async with client() as c:
        answers = []
        for i in range(5):  # a fresh device id every time, no login
            res = await c.post("/api/v1/bookings/quote", json=body(f"ENUM-{i:05d}"), headers={"X-Device-Id": f"enum-device-{i:016d}"})
            assert res.status_code == 200, res.text
            answers.append(res.json()["data"]["coupon_error"])
        assert answers[0] == "Invalid coupon code" and "Too Many Coupon Attempts" in answers[-1]
        res = await c.post("/api/v1/bookings/quote", json=body(good), headers={"X-Device-Id": "enum-device-final00000"})
        assert "Too Many Coupon Attempts" in res.json()["data"]["coupon_error"]  # even the right code waits out the hold
        assert (await db.coupons.find_one({"code": good}))["total_used"] == 0


# AT-23 --------------------------------------------------------------- IDOR / admin-only / mass assignment
async def test_at23_coupon_admin_endpoints_are_admin_only_and_the_usage_counter_is_not_assignable(db, cleanup):
    cust = await user(db, cleanup)
    center_id, _pin = await _center(db, cleanup)
    manager = await make_manager(db, center_id)
    cleanup.append(("users", {"_id": ObjectId(manager)}))
    now = datetime.now(timezone.utc)
    body = {"code": f"MASS{random.randint(10**5, 10**6)}", "coupon_type": "flat", "value": 100, "valid_from": now.isoformat(),
            "valid_until": (now + timedelta(days=3)).isoformat(), "total_used": 999, "is_deleted": True, "id": "x"}
    cleanup.append(("coupons", {"code": body["code"]}))
    async with client() as c:
        for who in (token(cust), token(manager, "manager", center_id), {}):
            assert (await c.post("/api/v1/coupons", json=body, headers=who)).status_code in (401, 403)
            assert (await c.get("/api/v1/coupons", headers=who)).status_code in (401, 403)
        admin = await db.users.find_one({"role": "admin"})
        res = await c.post("/api/v1/coupons", json=body, headers=token(str(admin["_id"]), "admin"))
        assert res.status_code == 200
        stored = await db.coupons.find_one({"code": body["code"]})
        assert stored["total_used"] == 0 and stored["is_deleted"] is False
        # a customer's own usage rows are not readable through any customer endpoint
        assert (await c.get("/api/v1/coupons/bundles", headers=token(cust))).status_code == 403


# AT-24 --------------------------------------------------------------- client-sent money is never read
async def test_at24_a_booking_request_has_no_discount_or_total_field_to_trust(db):
    req = BookingCreateRequest(vehicle_type="x", address_id="a", scheduled_date="2026-11-01", scheduled_slot="09:00-12:00",
                               payment_method="cash", **{"discount_amount": 999, "total_amount": 1, "amount_due": 0})
    assert not any(hasattr(req, k) for k in ("discount_amount", "total_amount", "amount_due"))


# AT-25 --------------------------------------------------------------- CSV import abuse beyond the bundle suite
async def test_at25_a_zip_or_binary_renamed_csv_is_refused(db):
    admin = await db.users.find_one({"role": "admin"})
    h = token(str(admin["_id"]), "admin")
    async with client() as c:
        zip_bytes = b"PK\x03\x04" + bytes(range(256)) * 4
        res = await c.post("/api/v1/coupons/bundles/preview", files={"file": ("codes.csv", zip_bytes, "text/csv")}, headers=h)
        assert res.status_code == 400
        res = await c.post("/api/v1/coupons/bundles/preview", files={"file": ("codes.zip", b"Code\nABCD1\n", "application/zip")}, headers=h)
        assert res.status_code == 400


# AT-26 --------------------------------------------------------------- the plan-visit message is not a code oracle  (FIXED: it was uncounted)
async def test_at26_the_plan_visit_refusal_counts_as_a_guess(db, cleanup, monkeypatch):
    monkeypatch.setattr(settings, "COUPON_MISS_LIMIT", 2)
    cleanup.append(("rate_counters", {"_id": {"$regex": "^coupon-miss"}}))
    codes = [await make_coupon(db, cleanup, combine_with_plan=False) for _ in range(3)]
    svc_ = CouponService(db)
    # Unknown code: silent (nothing to learn); real codes: the refusal is counted per device/IP source.
    await svc_.check_visit_plan_mix("NOPE-0000", 1, 2)
    from app.core import rate_limit

    token_ = rate_limit._request_device.set("probe-device-0000000000")
    try:
        for code in codes[:2]:
            with pytest.raises(BadRequestException):
                await svc_.check_visit_plan_mix(code, 1, 2)
        with pytest.raises(AppException) as held:
            await svc_.check_visit_plan_mix(codes[2], 1, 2)
        assert held.value.status_code == 429
    finally:
        rate_limit._request_device.reset(token_)


# AT-27 --------------------------------------------------------------- an edit that drops the restricted service drops the coupon
async def test_at27_an_edit_that_removes_the_qualifying_service_drops_the_coupon(db, cleanup):
    wash, other = svc("Star Wash"), svc("Wax Polish")
    code = await make_coupon(db, cleanup, applicable_service_ids=[str(wash["_id"])])
    bs = BookingService(db)
    car = {"coupon_code": code}
    assert await bs._held_coupon_discount(car, 500, [wash, other], {}, "vt", False) == (100.0, None)
    discount, why = await bs._held_coupon_discount(car, 500, [other], {}, "vt", False)
    assert discount == 0.0 and "Selected Service" in why


# AT-28 --------------------------------------------------------------- the custom-plan use-type cannot be switched on yet
async def test_at28_custom_plan_use_type_is_refused_until_a_cart_flow_calls_it(db):
    from app.schemas.coupon_schema import CouponCreateRequest

    now = datetime.now(timezone.utc)
    with pytest.raises(ValidationError, match="Custom plan carts"):
        CouponCreateRequest(code="CUSTOM1", coupon_type="flat", value=10, valid_from=now, valid_until=now + timedelta(days=1), applies_to=["custom"])
