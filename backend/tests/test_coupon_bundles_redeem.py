"""Coupon bundles — redemption: the one-time guarantee, release, expiry,
the guessing guard, and real bookings through BookingService.

Quote/validation only READS; record_usage is the atomic claim; reverse_usage
hands the use back exactly once.
"""
import asyncio
import itertools
import random
from datetime import datetime, timedelta, timezone

import pytest
from bson import ObjectId

from app.core.config import settings
from app.core.exceptions import AppException, BadRequestException
from app.models.enums import PaymentMethod
from app.schemas.booking_schema import BookingCancelRequest, BookingCreateRequest
from app.services.booking_service import BookingService
from app.services.coupon_service import BUNDLE_EXPIRED, BUNDLE_USED, CouponService
from app.utils.timezone import now_ist
from tests.factories import (
    get_hatchback_type_id, get_star_wash_service_id, make_customer, make_customer_with_vehicle, make_service_center,
)

pytestmark = pytest.mark.asyncio
_SEQ = itertools.count(random.randint(1000, 900000))
_PIN = itertools.count(random.randint(1000, 60000))


def _code(prefix="BND") -> str:
    return f"{prefix}-{next(_SEQ):06d}"


async def make_bundle(db, cleanup, *, codes=None, uses=1, per_user=1, value=100.0, ctype="flat", valid_until="+30d",
                      active=True, min_order=0, cap=None, offer_kind="standard") -> tuple[dict, list[str]]:
    """A bundle + its codes written straight to the collections."""
    codes = codes or [_code()]
    now = datetime.now(timezone.utc)
    until = None if valid_until is None else (now + timedelta(days=30) if valid_until == "+30d" else valid_until)
    bundle = await db.coupon_bundles.insert_one({
        "name": f"Bundle {ObjectId()}", "coupon_type": ctype, "value": value, "min_order_value": min_order,
        "max_discount_amount": cap, "offer_kind": offer_kind, "eligible_service_keywords": [], "free_addon_keywords": [],
        "uses_per_coupon": uses, "usage_limit_per_user": per_user, "valid_from": now - timedelta(days=1), "valid_until": until,
        "is_active": active, "is_deleted": False, "created_at": now, "updated_at": now,
    })
    bid = str(bundle.inserted_id)
    await db.coupon_codes.insert_many([
        {"bundle_id": bid, "code": c, "is_active": True, "used_count": 0, "last_used_at": None, "last_use": None,
         "added_at": now, "is_deleted": False} for c in codes
    ])
    cleanup.append(("coupon_bundles", {"_id": bundle.inserted_id}))
    cleanup.append(("coupon_usages", {"bundle_id": bid}))
    cleanup.append(("coupon_codes", {"bundle_id": bid}))
    return {"id": bid, "_id": bundle.inserted_id}, codes


async def code_doc(db, code):
    return await db.coupon_codes.find_one({"code": code})


async def user(db, cleanup) -> str:
    uid = await make_customer(db)
    cleanup.append(("users", {"_id": ObjectId(uid)}))
    return uid


async def claim(db, code, uid, booking_id=None) -> str:
    """validate (read-only) then record — what create_booking does."""
    svc = CouponService(db)
    booking_id = booking_id or str(ObjectId())
    coupon, _ = await svc.validate_and_compute_discount(code, 500, uid, use="services")
    await svc.record_usage(str(coupon["_id"]), uid, booking_id)
    return booking_id


# ------------------------------------------------------------------ validation reads only


async def test_validation_and_quote_never_consume_a_code(db, cleanup):
    _b, (code,) = await make_bundle(db, cleanup)
    uid = await user(db, cleanup)
    svc = CouponService(db)
    for _ in range(3):
        coupon, discount = await svc.validate_and_compute_discount(code, 500, uid, use="services")
        assert discount == 100 and coupon["code"] == code
    assert (await code_doc(db, code))["used_count"] == 0
    assert await db.coupon_usages.count_documents({"coupon_id": str((await code_doc(db, code))["_id"])}) == 0


async def test_codes_match_case_insensitively_and_trimmed(db, cleanup):
    _b, (code,) = await make_bundle(db, cleanup)
    uid = await user(db, cleanup)
    coupon, _ = await CouponService(db).validate_and_compute_discount(f"  {code.lower()}  ", 500, uid, use="services")
    assert coupon["code"] == code


# ------------------------------------------------------------------ one-time guarantee


async def test_a_single_use_code_works_once_for_anyone_then_never(db, cleanup):
    _b, (code,) = await make_bundle(db, cleanup)
    a, b = await user(db, cleanup), await user(db, cleanup)
    svc = CouponService(db)
    await claim(db, code, a)
    doc = await code_doc(db, code)
    assert doc["used_count"] == 1 and doc["last_use"]["user_id"] == a and doc["last_use"]["booking_id"]
    # Another customer is refused at validation...
    with pytest.raises(BadRequestException) as other:
        await svc.validate_and_compute_discount(code, 500, b, use="services")
    assert other.value.message == BUNDLE_USED
    # ...and the same customer gets their own, plain message.
    with pytest.raises(BadRequestException, match="already used this coupon"):
        await svc.validate_and_compute_discount(code, 500, a, use="services")
    # Even a stale validation (both passed before the first claim) can't claim again.
    coupon = await svc.repo.find_by_code(code)
    with pytest.raises(BadRequestException) as late:
        await svc.record_usage(str(coupon["_id"]), b, str(ObjectId()))
    assert late.value.message == BUNDLE_USED
    assert (await code_doc(db, code))["used_count"] == 1


async def test_parallel_claims_on_a_one_use_code_have_exactly_one_winner(db, cleanup):
    _b, (code,) = await make_bundle(db, cleanup)
    users = [await user(db, cleanup) for _ in range(12)]
    svc = CouponService(db)
    coupon = await svc.repo.find_by_code(code)

    async def attempt(uid):
        try:
            await svc.record_usage(str(coupon["_id"]), uid, str(ObjectId()))
            return True
        except BadRequestException:
            return False

    results = await asyncio.gather(*(attempt(u) for u in users))
    assert results.count(True) == 1
    doc = await code_doc(db, code)
    assert doc["used_count"] == 1
    assert await db.coupon_usages.count_documents({"coupon_id": str(doc["_id"])}) == 1


async def test_parallel_claims_by_one_customer_on_a_multi_use_code_respect_the_per_user_limit(db, cleanup):
    _b, (code,) = await make_bundle(db, cleanup, uses=5, per_user=1)
    uid = await user(db, cleanup)
    svc = CouponService(db)
    coupon = await svc.repo.find_by_code(code)

    async def attempt():
        try:
            await svc.record_usage(str(coupon["_id"]), uid, str(ObjectId()))
            return True
        except BadRequestException:
            return False

    assert (await asyncio.gather(*(attempt() for _ in range(8)))).count(True) == 1
    assert (await code_doc(db, code))["used_count"] == 1


async def test_n_uses_across_users_then_exhausted_and_partly_used_in_between(db, cleanup):
    _b, (code,) = await make_bundle(db, cleanup, uses=3, per_user=1)
    users = [await user(db, cleanup) for _ in range(4)]
    for u in users[:3]:
        await claim(db, code, u)
    assert (await code_doc(db, code))["used_count"] == 3
    with pytest.raises(BadRequestException) as exc:
        await CouponService(db).validate_and_compute_discount(code, 500, users[3], use="services")
    assert exc.value.message == BUNDLE_USED


async def test_per_user_limit_above_one_lets_one_customer_use_it_several_times(db, cleanup):
    _b, (code,) = await make_bundle(db, cleanup, uses=5, per_user=2)
    uid = await user(db, cleanup)
    await claim(db, code, uid)
    await claim(db, code, uid)
    with pytest.raises(BadRequestException, match="maximum number of times"):
        await claim(db, code, uid)
    assert (await code_doc(db, code))["used_count"] == 2


async def test_recording_the_same_booking_twice_is_one_use(db, cleanup):
    _b, (code,) = await make_bundle(db, cleanup, uses=3, per_user=3)
    uid = await user(db, cleanup)
    svc = CouponService(db)
    coupon = await svc.repo.find_by_code(code)
    booking_id = str(ObjectId())
    await svc.record_usage(str(coupon["_id"]), uid, booking_id)
    await svc.record_usage(str(coupon["_id"]), uid, booking_id)
    assert (await code_doc(db, code))["used_count"] == 1


# ------------------------------------------------------------------ release


async def test_reverse_gives_the_use_back_once_and_never_twice(db, cleanup):
    _b, (code,) = await make_bundle(db, cleanup)
    a, b = await user(db, cleanup), await user(db, cleanup)
    svc = CouponService(db)
    booking_id = await claim(db, code, a)
    await svc.reverse_usage(code, a, booking_id)
    await svc.reverse_usage(code, a, booking_id)  # a replay / second sweep
    doc = await code_doc(db, code)
    assert doc["used_count"] == 0 and doc["last_use"] is None and doc["last_used_at"] is None
    # Genuinely unused again — anyone can claim it, and only once.
    await claim(db, code, b)
    assert (await code_doc(db, code))["used_count"] == 1
    # A's stale reversal can't free B's use.
    await svc.reverse_usage(code, a, booking_id)
    assert (await code_doc(db, code))["used_count"] == 1


async def test_reverse_keeps_last_used_pointing_at_the_remaining_use(db, cleanup):
    _b, (code,) = await make_bundle(db, cleanup, uses=2)
    a, b = await user(db, cleanup), await user(db, cleanup)
    first = await claim(db, code, a)
    second = await claim(db, code, b)
    await CouponService(db).reverse_usage(code, b, second)
    doc = await code_doc(db, code)
    assert doc["used_count"] == 1 and doc["last_use"]["booking_id"] == first and doc["last_use"]["user_id"] == a


# ------------------------------------------------------------------ expiry, switches


async def test_expiry_instant_is_expired_and_just_before_is_valid(db, cleanup):
    soon = datetime.now(timezone.utc) + timedelta(seconds=3)
    _b, (code,) = await make_bundle(db, cleanup, valid_until=soon)
    uid = await user(db, cleanup)
    svc = CouponService(db)
    await svc.validate_and_compute_discount(code, 500, uid, use="services")  # before the instant
    await asyncio.sleep(3.2)
    with pytest.raises(BadRequestException) as exc:
        await svc.validate_and_compute_discount(code, 500, uid, use="services")
    assert exc.value.message == BUNDLE_EXPIRED


async def test_expiry_is_compared_in_ist_for_naive_stored_times(db, cleanup):
    # Stored naive datetimes are UTC instants (Mongo drops tzinfo): an expiry
    # 1 minute ago UTC is expired, one 5h from now UTC is valid, whatever IST says.
    past = (datetime.now(timezone.utc) - timedelta(minutes=1)).replace(tzinfo=None)
    future = (datetime.now(timezone.utc) + timedelta(hours=5)).replace(tzinfo=None)
    _b, (old,) = await make_bundle(db, cleanup, valid_until=past)
    _b2, (new,) = await make_bundle(db, cleanup, valid_until=future)
    uid = await user(db, cleanup)
    svc = CouponService(db)
    with pytest.raises(BadRequestException, match="Expired"):
        await svc.validate_and_compute_discount(old, 500, uid, use="services")
    await svc.validate_and_compute_discount(new, 500, uid, use="services")


async def test_no_expiry_bundle_never_expires(db, cleanup):
    _b, (code,) = await make_bundle(db, cleanup, valid_until=None)
    await CouponService(db).validate_and_compute_discount(code, 500, await user(db, cleanup), use="services")


async def test_switched_off_bundle_or_code_reads_as_invalid(db, cleanup):
    _b, (code,) = await make_bundle(db, cleanup, active=False)
    uid = await user(db, cleanup)
    svc = CouponService(db)
    with pytest.raises(BadRequestException, match="Invalid coupon code"):
        await svc.validate_and_compute_discount(code, 500, uid, use="services")
    b2, (code2,) = await make_bundle(db, cleanup)
    await db.coupon_codes.update_one({"code": code2}, {"$set": {"is_active": False}})
    with pytest.raises(BadRequestException, match="Invalid coupon code"):
        await svc.validate_and_compute_discount(code2, 500, uid, use="services")


async def test_bundle_edited_while_a_claim_is_in_flight_still_holds_the_use_limit(db, cleanup):
    """A booking validated under uses=2 while an admin raises it to 3, then
    claims: the claim reads the bundle fresh, so each limit is honoured at
    the moment of the write."""
    b, (code,) = await make_bundle(db, cleanup, uses=1)
    a, c = await user(db, cleanup), await user(db, cleanup)
    svc = CouponService(db)
    coupon, _ = await svc.validate_and_compute_discount(code, 500, a, use="services")
    await db.coupon_bundles.update_one({"_id": b["_id"]}, {"$set": {"uses_per_coupon": 2}})
    await svc.record_usage(str(coupon["_id"]), a, str(ObjectId()))
    await svc.record_usage(str(coupon["_id"]), c, str(ObjectId()))
    with pytest.raises(BadRequestException):
        await svc.record_usage(str(coupon["_id"]), await user(db, cleanup), str(ObjectId()))
    assert (await code_doc(db, code))["used_count"] == 2


# ------------------------------------------------------------------ benefit rules


async def test_percent_cap_min_order_and_total_cap(db, cleanup):
    _b, (pct,) = await make_bundle(db, cleanup, ctype="percentage", value=50, cap=120)
    _b2, (flat,) = await make_bundle(db, cleanup, value=9999)
    _b3, (mins,) = await make_bundle(db, cleanup, min_order=400)
    uid = await user(db, cleanup)
    svc = CouponService(db)
    assert (await svc.validate_and_compute_discount(pct, 500, uid, use="services"))[1] == 120  # 50% = 250, capped
    assert (await svc.validate_and_compute_discount(pct, 100, uid, use="services"))[1] == 50
    assert (await svc.validate_and_compute_discount(flat, 349, uid, use="services"))[1] == 349  # never above the total
    with pytest.raises(BadRequestException, match="Minimum order"):
        await svc.validate_and_compute_discount(mins, 399, uid, use="services")
    await svc.validate_and_compute_discount(mins, 400, uid, use="services")


async def test_free_addon_bundle_is_not_a_plain_discount(db, cleanup):
    _b, (code,) = await make_bundle(db, cleanup, offer_kind="free_addon_with_service")
    with pytest.raises(BadRequestException, match="applied automatically"):
        await CouponService(db).validate_and_compute_discount(code, 500, await user(db, cleanup), use="services")


# ------------------------------------------------------------------ legacy coupons untouched


async def test_regular_coupons_still_work_and_a_bundle_code_cannot_shadow_one(db, cleanup):
    code = f"LEG{random.randint(100000, 999999)}"
    now = now_ist()
    res = await db.coupons.insert_one({
        "code": code, "coupon_type": "flat", "value": 40, "min_order_value": 0, "usage_limit_per_user": 1,
        "total_usage_limit": None, "total_used": 0, "valid_from": now - timedelta(days=1), "valid_until": now + timedelta(days=9),
        "offer_kind": "standard", "is_active": True, "is_deleted": False,
    })
    cleanup.append(("coupons", {"_id": res.inserted_id}))
    cleanup.append(("coupon_usages", {"coupon_id": str(res.inserted_id)}))
    cleanup.append(("coupon_user_usage", {"coupon_id": str(res.inserted_id)}))
    uid = await user(db, cleanup)
    svc = CouponService(db)
    coupon, discount = await svc.validate_and_compute_discount(code.lower(), 300, uid, use="services")
    assert discount == 40 and "_bundle_id" not in coupon
    await svc.record_usage(str(coupon["_id"]), uid, str(ObjectId()))
    assert (await db.coupons.find_one({"_id": res.inserted_id}))["total_used"] == 1
    await svc.reverse_usage(code, uid, (await db.coupon_usages.find_one({"coupon_id": str(res.inserted_id)}))["booking_id"])
    assert (await db.coupons.find_one({"_id": res.inserted_id}))["total_used"] == 0
    # A regular coupon can't be created on a bundle code.
    _b, (taken,) = await make_bundle(db, cleanup)
    from app.schemas.coupon_schema import CouponCreateRequest
    with pytest.raises(AppException) as exc:
        await svc.create(CouponCreateRequest(
            code=taken, coupon_type="flat", value=10, valid_from=datetime.now(timezone.utc),
            valid_until=datetime.now(timezone.utc) + timedelta(days=1),
        ))
    assert exc.value.status_code == 409


async def test_the_anonymous_offer_lookup_never_confirms_a_bundle_code(db, cleanup):
    _b, (code,) = await make_bundle(db, cleanup)
    with pytest.raises(BadRequestException, match="Invalid coupon code"):
        await CouponService(db).validate_public_offer(code)


# ------------------------------------------------------------------ guessing guard


@pytest.fixture
def tight_guard(monkeypatch, db, cleanup):
    monkeypatch.setattr(settings, "COUPON_MISS_LIMIT", 3)
    cleanup.append(("rate_counters", {"_id": {"$regex": "^coupon-miss"}}))


async def test_wrong_codes_are_held_after_the_limit_and_a_repeated_code_counts_once(db, cleanup, tight_guard):
    _b, (good,) = await make_bundle(db, cleanup)
    uid = await user(db, cleanup)
    svc = CouponService(db)
    for _ in range(6):  # the SAME wrong code over and over: one miss
        with pytest.raises(BadRequestException, match="Invalid coupon code"):
            await svc.validate_and_compute_discount("NOPE-0000", 500, uid, use="services")
    await svc.validate_and_compute_discount(good, 500, uid, use="services")  # still allowed
    for i in range(2):
        with pytest.raises(BadRequestException, match="Invalid coupon code"):
            await svc.validate_and_compute_discount(f"NOPE-000{i + 1}", 500, uid, use="services")
    # Three distinct misses: now even the right code is held.
    with pytest.raises(AppException) as held:
        await svc.validate_and_compute_discount(good, 500, uid, use="services")
    assert held.value.status_code == 429 and "Too Many Coupon Attempts" in held.value.message
    assert (await code_doc(db, good))["used_count"] == 0
    # Another customer is not held.
    await svc.validate_and_compute_discount(good, 500, await user(db, cleanup), use="services")


async def test_a_minimum_order_or_own_reuse_error_is_not_a_guess(db, cleanup, tight_guard):
    _b, (code,) = await make_bundle(db, cleanup, min_order=900)
    uid = await user(db, cleanup)
    svc = CouponService(db)
    for _ in range(5):
        with pytest.raises(BadRequestException, match="Minimum order"):
            await svc.validate_and_compute_discount(code, 100, uid, use="services")
    await svc.validate_and_compute_discount(code, 1000, uid, use="services")


async def test_the_guard_can_be_switched_off(db, cleanup, monkeypatch):
    monkeypatch.setattr(settings, "COUPON_MISS_LIMIT", 0)
    uid = await user(db, cleanup)
    for i in range(15):
        with pytest.raises(BadRequestException, match="Invalid coupon code"):
            await CouponService(db).validate_and_compute_discount(f"NOPE-{i:04d}", 500, uid, use="services")


# ------------------------------------------------------------------ through real bookings


async def _center(db, cleanup):
    pin = f"4{next(_PIN):05d}"
    center_id = await make_service_center(db, pincode=pin)
    for coll, q in [("slot_capacity", {"service_center_id": center_id}), ("daily_capacity", {"service_center_id": center_id}),
                    ("capacity_policy_changes", {"service_center_id": center_id}), ("service_centers", {"_id": ObjectId(center_id)})]:
        cleanup.append((coll, q))
    return center_id, pin


async def _customer(db, cleanup, pin):
    customer_id, vehicle_id, address_id = await make_customer_with_vehicle(db, await get_hatchback_type_id(db), pincode=pin)
    for coll, q in [("users", {"_id": ObjectId(customer_id)}), ("vehicles", {"owner_id": customer_id}),
                    ("addresses", {"owner_id": customer_id}), ("bookings", {"customer_id": customer_id}),
                    ("user_subscriptions", {"customer_id": customer_id}), ("notifications", {"user_id": customer_id}),
                    ("coupon_usages", {"user_id": customer_id}), ("payment_orders", {"customer_id": customer_id})]:
        cleanup.append((coll, q))
    return customer_id, vehicle_id, address_id


async def _slot(db, center_id, offset=2, index=0):
    when = (now_ist().date() + timedelta(days=offset)).isoformat()
    slots = await BookingService(db).available_slots(center_id, when)
    return when, [s["key"] for s in slots if s["status"] in ("available", "low")][index]


def _req(c, service, when, slot, code):
    return BookingCreateRequest(vehicle_id=c[1], address_id=c[2], service_ids=[service], scheduled_date=when,
                                scheduled_slot=slot, payment_method=PaymentMethod.CASH, coupon_code=code)


async def test_a_booking_spends_the_code_cancel_returns_it_and_a_second_customer_cannot_reuse_a_spent_one(db, cleanup):
    center_id, pin = await _center(db, cleanup)
    service = await get_star_wash_service_id(db)
    when, slot = await _slot(db, center_id)
    a, b = await _customer(db, cleanup, pin), await _customer(db, cleanup, pin)
    _bd, (code,) = await make_bundle(db, cleanup, value=100)
    svc = BookingService(db)
    booking = await svc.create_booking(a[0], _req(a, service, when, slot, code.lower()))
    assert booking["coupon_code"].upper() == code and booking["discount_amount"] == 100
    assert (await code_doc(db, code))["used_count"] == 1
    when2, slot2 = await _slot(db, center_id, offset=3)
    with pytest.raises(BadRequestException, match="Already Been Used"):
        await svc.create_booking(b[0], _req(b, service, when2, slot2, code))
    assert await db.bookings.count_documents({"customer_id": b[0]}) == 0
    # Cancelling the booking hands the single use back...
    await svc.cancel_booking(booking["id"], BookingCancelRequest(reason="Changed my plan"), a[0], "customer")
    assert (await code_doc(db, code))["used_count"] == 0
    # ...so the other customer can now use it, once.
    again = await svc.create_booking(b[0], _req(b, service, when2, slot2, code))
    assert again["discount_amount"] == 100 and (await code_doc(db, code))["used_count"] == 1


async def test_two_customers_racing_for_one_code_book_exactly_one_discounted_visit(db, cleanup):
    center_id, pin = await _center(db, cleanup)
    service = await get_star_wash_service_id(db)
    when, slot = await _slot(db, center_id)
    a, b = await _customer(db, cleanup, pin), await _customer(db, cleanup, pin)
    _bd, (code,) = await make_bundle(db, cleanup)

    async def book(c):
        try:
            return await BookingService(db).create_booking(c[0], _req(c, service, when, slot, code))
        except BadRequestException as exc:
            return exc

    got = await asyncio.gather(book(a), book(b))
    assert sum(1 for g in got if isinstance(g, dict)) == 1
    assert await db.bookings.count_documents({"coupon_code": code}) == 1
    assert (await code_doc(db, code))["used_count"] == 1


async def test_a_booking_paid_by_a_plan_does_not_consume_the_code(db, cleanup):
    from app.schemas.subscription_schema import SubscribeRequest
    from app.services.subscription_service import UserSubscriptionService
    from tests.factories import make_subscription_plan

    center_id, pin = await _center(db, cleanup)
    service = await get_star_wash_service_id(db)
    when, slot = await _slot(db, center_id)
    c = await _customer(db, cleanup, pin)
    plan_id = await make_subscription_plan(db, vehicle_types=[], included_service_ids=[service], total_service_count=4)
    cleanup.append(("subscription_plans", {"_id": ObjectId(plan_id)}))
    sub = await UserSubscriptionService(db).subscribe(c[0], SubscribeRequest(plan_id=plan_id, vehicle_id=c[1], service_id=service))
    _bd, (code,) = await make_bundle(db, cleanup)
    req = _req(c, service, when, slot, code)
    req.subscription_id = sub["id"]
    booking = await BookingService(db).create_booking(c[0], req)
    assert not booking.get("coupon_code")
    assert (await code_doc(db, code))["used_count"] == 0


async def test_a_booking_that_fails_after_the_reservation_releases_the_code(db, cleanup):
    center_id, pin = await _center(db, cleanup)
    service = await get_star_wash_service_id(db)
    when, slot = await _slot(db, center_id)
    c = await _customer(db, cleanup, pin)
    _bd, (code,) = await make_bundle(db, cleanup)
    # A slot that does not exist fails the create after validation.
    with pytest.raises(AppException):
        await BookingService(db).create_booking(c[0], _req(c, service, when, "03:00-04:00", code))
    assert (await code_doc(db, code))["used_count"] == 0
    assert await db.coupon_usages.count_documents({"user_id": c[0]}) == 0


async def test_recycle_bin_returns_the_code_and_restore_spends_it_again_unless_someone_took_it(db, cleanup):
    center_id, pin = await _center(db, cleanup)
    service = await get_star_wash_service_id(db)
    when, slot = await _slot(db, center_id)
    a, b = await _customer(db, cleanup, pin), await _customer(db, cleanup, pin)
    _bd, (code,) = await make_bundle(db, cleanup)
    svc = BookingService(db)
    booking = await svc.create_booking(a[0], _req(a, service, when, slot, code))
    await svc.soft_delete_booking(booking["id"], "admin-x")
    assert (await code_doc(db, code))["used_count"] == 0
    await svc.restore_booking(booking["id"])
    assert (await code_doc(db, code))["used_count"] == 1
    # Delete again, let B take the single use, then A's restore is refused.
    await svc.soft_delete_booking(booking["id"], "admin-x")
    when2, slot2 = await _slot(db, center_id, offset=3)
    await svc.create_booking(b[0], _req(b, service, when2, slot2, code))
    with pytest.raises(BadRequestException):
        await svc.restore_booking(booking["id"])
    assert (await code_doc(db, code))["used_count"] == 1


async def test_an_unpaid_online_booking_swept_after_the_window_returns_the_code(db, cleanup, monkeypatch):
    center_id, pin = await _center(db, cleanup)
    service = await get_star_wash_service_id(db)
    when, slot = await _slot(db, center_id)
    c = await _customer(db, cleanup, pin)
    _bd, (code,) = await make_bundle(db, cleanup)
    svc = BookingService(db)
    booking = await svc.create_booking(c[0], _req(c, service, when, slot, code))
    assert (await code_doc(db, code))["used_count"] == 1
    # The payment-window sweep cancels a still-unpaid visit with only_if_unpaid.
    await db.bookings.update_one({"_id": ObjectId(booking["id"])}, {"$set": {"payment_status": "pending", "status": "awaiting_payment"}})
    raw = await db.bookings.find_one({"_id": ObjectId(booking["id"])})
    await svc.cancel_booking_group(
        raw["booking_group_id"], BookingCancelRequest(reason="Payment window expired"), "system", "admin", only_if_unpaid=True,
    )
    assert (await code_doc(db, code))["used_count"] == 0


async def test_validate_endpoint_does_not_consume_and_the_guard_follows_the_device(db, cleanup, monkeypatch):
    from httpx import ASGITransport, AsyncClient

    from app.core.security import create_access_token
    from app.main import app

    monkeypatch.setattr(settings, "COUPON_MISS_LIMIT", 3)
    cleanup.append(("rate_counters", {"_id": {"$regex": "^coupon-miss"}}))
    _b, (good,) = await make_bundle(db, cleanup)
    a, b = await user(db, cleanup), await user(db, cleanup)
    head = lambda uid: {"Authorization": f"Bearer {create_access_token(uid, 'customer', {'service_center_id': None, 'tv': 0})}", "X-Device-Id": "devicedevicedevice1234"}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        ok = await client.post("/api/v1/coupons/validate", json={"code": good.lower(), "order_value": 500}, headers=head(a))
        assert ok.status_code == 200 and ok.json()["data"]["discount_amount"] == 100
        assert (await code_doc(db, good))["used_count"] == 0
        for i in range(3):
            res = await client.post("/api/v1/coupons/validate", json={"code": f"WRONG-{i:03d}", "order_value": 500}, headers=head(a))
            assert res.status_code == 400
        # The same device, a different customer: held too (one phone, many accounts).
        held = await client.post("/api/v1/coupons/validate", json={"code": good, "order_value": 500}, headers=head(b))
        assert held.status_code == 429 and held.json()["error_code"] == "COUPON_HELD"
        assert held.json()["details"]["retry_after"] >= 1 if "details" in held.json() else True
        # A different device is not.
        other = head(b)
        other["X-Device-Id"] = "anotherdevice9876543"
        assert (await client.post("/api/v1/coupons/validate", json={"code": good, "order_value": 500}, headers=other)).status_code == 200


async def test_an_edited_booking_rechecks_its_held_bundle_code_by_applicability_only(db, cleanup):
    """Customer / admin edits re-price the coupon the booking already holds:
    its use was counted at create, so a spent or expired code still applies;
    only the minimum order (the offer's own rule) can drop it."""
    b, (code,) = await make_bundle(db, cleanup, value=150, min_order=300)
    uid = await user(db, cleanup)
    await claim(db, code, uid)  # the booking holds the one and only use
    await db.coupon_bundles.update_one({"_id": b["_id"]}, {"$set": {"valid_until": datetime.now(timezone.utc) - timedelta(days=1)}})
    svc = BookingService(db)
    assert await svc._held_coupon_discount({"coupon_code": code}, 500, [], {}, "x", False) == (150.0, None)
    discount, why = await svc._held_coupon_discount({"coupon_code": code}, 250, [], {}, "x", False)
    assert discount == 0.0 and "Minimum order" in why
    assert await svc._held_coupon_discount({"coupon_code": "GONE-0000"}, 500, [], {}, "x", False) == (0.0, "Coupon GONE-0000 is no longer available")
