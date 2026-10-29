"""Coupon rules (use-type + restrictions) — one central check, every flow.

Regular coupons and bundle codes carry the same rule fields
(services/coupon_rules.py); these tests pin each rule where it is enforced:
the central CouponService check, the real booking quote / create / reschedule,
plan sales, society plans, the migration of old coupons, and the WhatsApp bot.
"""
import random
from datetime import datetime, timedelta, timezone

import pytest
from bson import ObjectId

from app.core.exceptions import AppException, BadRequestException
from app.schemas.booking_schema import BookingRescheduleRequest, QuickBookingLine
from app.services.booking_service import BookingService
from app.services.coupon_service import CouponService
from app.utils.timezone import now_ist
from tests.factories import get_hatchback_type_id, get_star_wash_service_id, make_customer
from tests.test_manager_subscription_offers import gateway, rig  # noqa: F401 - fixtures
from tests.test_coupon_bundles_redeem import (
    _center, _customer, _req, _slot, code_doc, make_bundle, user,
)

pytestmark = pytest.mark.asyncio


def _code(prefix="RUL") -> str:
    return f"{prefix}{random.randint(10**6, 10**7 - 1)}"


async def make_coupon(db, cleanup, *, value=100.0, ctype="flat", **extra) -> str:
    code = _code()
    now = now_ist()
    res = await db.coupons.insert_one({
        "code": code, "coupon_type": ctype, "value": value, "min_order_value": 0, "usage_limit_per_user": 5,
        "total_usage_limit": None, "total_used": 0, "valid_from": now - timedelta(days=1), "valid_until": now + timedelta(days=30),
        "offer_kind": "standard", "is_active": True, "is_deleted": False,
        "applies_to": ["services"], "stackable_with_offers": True, "combine_with_plan": False, **extra,
    })
    cleanup.append(("coupons", {"_id": res.inserted_id}))
    cleanup.append(("coupon_usages", {"coupon_id": str(res.inserted_id)}))
    cleanup.append(("coupon_user_usage", {"coupon_id": str(res.inserted_id)}))
    return code


def svc(name="Star Wash"):
    return {"_id": ObjectId(), "name": name, "slug": name.lower().replace(" ", "-"), "is_addon": False}


async def booking_discount(db, code, uid, *, services=None, vt="vt-hatch", **kw):
    services = services or [svc()]
    return await CouponService(db).validate_and_compute_booking_discount(
        code, 500, uid, services=services, quantities={}, vehicle_type=vt, first_time_eligible=kw.pop("first_time_eligible", True),
        price_resolver=lambda *_: 0, **kw,
    )


# ------------------------------------------------------------------ use type


async def test_use_type_is_enforced_in_every_flow(db, cleanup):
    uid = await user(db, cleanup)
    services_only = await make_coupon(db, cleanup)
    plans_only = await make_coupon(db, cleanup, applies_to=["plans"])
    society_only = await make_coupon(db, cleanup, applies_to=["society"])
    everywhere = await make_coupon(db, cleanup, applies_to=["services", "plans", "society", "custom"])
    svc_ = CouponService(db)
    assert (await booking_discount(db, services_only, uid))[1] == 100
    with pytest.raises(BadRequestException, match="Not Valid For Plans"):
        await svc_.validate_and_compute_discount(services_only, 999, uid, use="plans")
    with pytest.raises(BadRequestException, match="Not Valid For Society Plans"):
        await svc_.validate_and_compute_discount(services_only, 999, uid, use="society")
    with pytest.raises(BadRequestException, match="Not Valid For Custom Plans"):
        await svc_.validate_and_compute_discount(services_only, 999, uid, use="custom")
    with pytest.raises(BadRequestException, match="Not Valid For Bookings"):
        await booking_discount(db, plans_only, uid)
    assert (await svc_.validate_and_compute_discount(plans_only, 999, uid, use="plans"))[1] == 100
    with pytest.raises(BadRequestException, match="Not Valid For Plans"):
        await svc_.validate_and_compute_discount(society_only, 999, uid, use="plans")
    for use in ("plans", "society", "custom"):
        assert (await svc_.validate_and_compute_discount(everywhere, 999, uid, use=use))[1] == 100
    assert (await booking_discount(db, everywhere, uid))[1] == 100


async def test_a_plan_sale_never_goes_below_one_rupee(db, cleanup):
    huge = await make_coupon(db, cleanup, value=99999, applies_to=["plans", "society"])
    full = await make_coupon(db, cleanup, value=100, ctype="percentage", applies_to=["plans"])
    uid = await user(db, cleanup)
    svc_ = CouponService(db)
    assert (await svc_.validate_and_compute_discount(huge, 999, uid, use="plans"))[1] == 998
    assert (await svc_.validate_and_compute_discount(full, 999, uid, use="plans"))[1] == 998
    assert (await svc_.validate_and_compute_discount(huge, 999, uid, use="society"))[1] == 998


async def test_plan_sales_use_the_central_check_and_price_net_of_the_coupon(rig, cleanup):
    from app.schemas.subscription_schema import ManagerSubscriptionOfferRequest, ManagerSubscriptionPreviewRequest
    from app.services.payment_service import PaymentService

    db = rig["db"]
    services_only = await make_coupon(db, cleanup)
    other_car = await make_coupon(db, cleanup, applies_to=["plans"], applicable_vehicle_types=[str(ObjectId())])
    huge = await make_coupon(db, cleanup, value=99999, applies_to=["plans"])
    svc_ = PaymentService(db)
    preview = lambda code: svc_.manager_subscription_preview(  # noqa: E731
        ManagerSubscriptionPreviewRequest(plan_id=rig["plan_id"], vehicle_type=rig["hatchback"], service_id=rig["star"], coupon_code=code)
    )
    r = await preview(services_only)
    assert r["coupon_valid"] is False and "Not Valid For Plans" in r["coupon_error"]
    r = await preview(other_car)
    assert r["coupon_valid"] is False and "Vehicle Type" in r["coupon_error"]
    r = await preview(huge)
    assert r["coupon_valid"] is True and r["final_price"] == 1
    phone = f"9{random.randint(10**8, 10**9 - 1)}"
    cleanup.append(("users", {"phone": phone}))
    offer = lambda code: ManagerSubscriptionOfferRequest(  # noqa: E731
        customer_name="Plan Customer", customer_phone=phone, plan_id=rig["plan_id"], vehicle_type=rig["hatchback"],
        service_id=rig["star"], recurring=False, payment_method="link", coupon_code=code,
    )
    with pytest.raises(BadRequestException, match="Not Valid For Plans"):
        await svc_.manager_subscription_offer(rig["manager_id"], offer(services_only), actor_center_id=rig["center_id"])
    customer = await db.users.find_one({"phone": phone})
    if customer:
        cid = str(customer["_id"])
        cleanup += [("payment_orders", {"customer_id": cid}), ("user_subscriptions", {"customer_id": cid})]
        assert await db.payment_orders.count_documents({"customer_id": cid, "purpose": "subscription"}) == 0


async def test_society_quote_refuses_a_coupon_not_enabled_for_society(db, cleanup):
    from app.services.society_service import SocietyService

    uid = await user(db, cleanup)
    services_only = await make_coupon(db, cleanup)
    preview = await SocietyService(db).coupon_preview(services_only, 2000, uid)
    assert preview["valid"] is False and "Not Valid For Society Plans" in preview["error"]
    ok = await make_coupon(db, cleanup, applies_to=["society"], value=500)
    assert (await SocietyService(db).coupon_preview(ok, 2000, uid))["discount"] == 500


# ------------------------------------------------------------------ restrictions (central check)


async def test_service_vehicle_and_center_restrictions(db, cleanup):
    uid = await user(db, cleanup)
    wash, wax = svc("Star Wash"), svc("Wax Polish")
    code = await make_coupon(db, cleanup, applicable_service_ids=[str(wash["_id"])], applicable_vehicle_types=["vt-hatch"],
                             applicable_center_ids=["c1"])
    assert (await booking_discount(db, code, uid, services=[wash, wax], center_id="c1"))[1] == 100
    with pytest.raises(BadRequestException, match="Doesn't Apply To The Selected Service"):
        await booking_discount(db, code, uid, services=[wax], center_id="c1")
    with pytest.raises(BadRequestException, match="Vehicle Type"):
        await booking_discount(db, code, uid, services=[wash], vt="vt-suv", center_id="c1")
    with pytest.raises(BadRequestException, match="Service Center"):
        await booking_discount(db, code, uid, services=[wash], center_id="c2")


async def test_weekday_and_time_window(db, cleanup):
    uid = await user(db, cleanup)
    monday = datetime(2026, 11, 2)  # a Monday
    code = await make_coupon(db, cleanup, allowed_weekdays=[0, 2], time_window_start="09:00", time_window_end="12:00")
    assert (await booking_discount(db, code, uid, scheduled_date=monday, scheduled_slot="09:00-12:00"))[1] == 100
    assert (await booking_discount(db, code, uid, scheduled_date="2026-11-04", scheduled_slot="11:00-13:00"))[1] == 100
    with pytest.raises(BadRequestException, match="Only Valid On Monday, Wednesday"):
        await booking_discount(db, code, uid, scheduled_date=monday + timedelta(days=1), scheduled_slot="09:00-12:00")
    with pytest.raises(BadRequestException, match="Time Slot"):
        await booking_discount(db, code, uid, scheduled_date=monday, scheduled_slot="12:00-15:00")
    with pytest.raises(BadRequestException, match="Time Slot"):
        await booking_discount(db, code, uid, scheduled_date=monday, scheduled_slot="07:00-09:00")


async def test_a_first_wash_offer_blocks_a_non_stackable_coupon(db, cleanup):
    uid = await user(db, cleanup)
    plain = await make_coupon(db, cleanup, stackable_with_offers=False)
    stack = await make_coupon(db, cleanup, stackable_with_offers=True)
    assert (await booking_discount(db, plain, uid, offer_applied=False))[1] == 100
    with pytest.raises(BadRequestException, match="First Wash Offer"):
        await booking_discount(db, plain, uid, offer_applied=True)
    assert (await booking_discount(db, stack, uid, offer_applied=True))[1] == 100


async def test_specific_customers_only(db, cleanup):
    me = await make_customer(db)
    cleanup.append(("users", {"_id": ObjectId(me)}))
    other = await user(db, cleanup)
    phone = (await db.users.find_one({"_id": ObjectId(me)}))["phone"]
    code = await make_coupon(db, cleanup, allowed_customer_phones=[phone])
    assert (await booking_discount(db, code, me))[1] == 100
    with pytest.raises(BadRequestException, match="Isn't Available For Your Account"):
        await booking_discount(db, code, other)
    # An anonymous quote can't be told apart yet; the booking re-checks it.
    assert (await booking_discount(db, code, None))[1] == 100


async def test_first_booking_and_new_customer_rules(db, cleanup):
    uid = await user(db, cleanup)
    phone = (await db.users.find_one({"_id": ObjectId(uid)}))["phone"]
    first = await make_coupon(db, cleanup, first_booking_only=True)
    new = await make_coupon(db, cleanup, new_customers_only=True)
    assert (await booking_discount(db, first, uid, first_time_eligible=True))[1] == 100
    with pytest.raises(BadRequestException, match="First Bookings Only"):
        await booking_discount(db, first, uid, first_time_eligible=False)
    assert (await booking_discount(db, new, uid))[1] == 100
    # A CANCELLED booking still means "not new" — but does not break "first".
    await db.bookings.insert_one({"customer_id": uid, "customer_phone": phone, "status": "cancelled", "is_deleted": False})
    cleanup.append(("bookings", {"customer_id": uid}))
    with pytest.raises(BadRequestException, match="New Customers Only"):
        await booking_discount(db, new, uid)
    assert (await booking_discount(db, first, uid, first_time_eligible=None))[1] == 100
    await db.bookings.insert_one({"customer_id": uid, "customer_phone": phone, "status": "pending", "is_deleted": False})
    with pytest.raises(BadRequestException, match="First Bookings Only"):
        await booking_discount(db, first, uid, first_time_eligible=None)


async def test_plan_visit_mix_follows_the_combine_flag(db, cleanup):
    plain = await make_coupon(db, cleanup, combine_with_plan=False)
    both = await make_coupon(db, cleanup, combine_with_plan=True)
    svc_ = CouponService(db)
    await svc_.check_visit_plan_mix(plain, 0, 2)  # nothing covered
    await svc_.check_visit_plan_mix(plain, 2, 2)  # all covered: no coupon consumed anyway
    with pytest.raises(BadRequestException, match="Covered By A Plan"):
        await svc_.check_visit_plan_mix(plain, 1, 2)
    await svc_.check_visit_plan_mix(both, 1, 2)


# ------------------------------------------------------------------ rules inherited by a bundle


async def test_a_bundle_carries_the_rules_for_every_code(db, cleanup):
    uid = await user(db, cleanup)
    b, codes = await make_bundle(db, cleanup, codes=[f"BRL-{random.randint(10**5, 10**6):06d}" for _ in range(2)])
    await db.coupon_bundles.update_one({"_id": b["_id"]}, {"$set": {"applies_to": ["plans"], "first_booking_only": False, "stackable_with_offers": False, "combine_with_plan": False}})
    for code in codes:
        with pytest.raises(BadRequestException, match="Not Valid For Bookings"):
            await booking_discount(db, code, uid)
        assert (await CouponService(db).validate_and_compute_discount(code, 999, uid, use="plans"))[1] == 100
    # Editing the bundle changes all of them at once.
    await db.coupon_bundles.update_one({"_id": b["_id"]}, {"$set": {"applies_to": ["services"]}})
    for code in codes:
        assert (await booking_discount(db, code, uid))[1] == 100


# ------------------------------------------------------------------ real bookings


async def _book_rig(db, cleanup):
    center_id, pin = await _center(db, cleanup)
    service = await get_star_wash_service_id(db)
    when, slot = await _slot(db, center_id)
    customer = await _customer(db, cleanup, pin)
    return center_id, service, when, slot, customer


async def test_a_real_booking_enforces_use_type_center_and_stores_the_canonical_code(db, cleanup):
    center_id, service, when, slot, c = await _book_rig(db, cleanup)
    plans_only = await make_coupon(db, cleanup, applies_to=["plans"])
    wrong_center = await make_coupon(db, cleanup, applicable_center_ids=[str(ObjectId())])
    right_center = await make_coupon(db, cleanup, applicable_center_ids=[center_id], stackable_with_offers=True)
    bs = BookingService(db)
    with pytest.raises(BadRequestException, match="Not Valid For Bookings"):
        await bs.create_booking(c[0], _req(c, service, when, slot, plans_only))
    with pytest.raises(BadRequestException, match="Service Center"):
        await bs.create_booking(c[0], _req(c, service, when, slot, wrong_center))
    assert await db.bookings.count_documents({"customer_id": c[0]}) == 0
    booking = await bs.create_booking(c[0], _req(c, service, when, slot, f"  {right_center.lower()} "))
    assert booking["coupon_code"] == right_center and booking["discount_amount"] == 100


async def test_reschedule_keeps_the_coupon_but_not_onto_a_day_it_does_not_cover(db, cleanup):
    center_id, service, when, slot, c = await _book_rig(db, cleanup)
    day = datetime.strptime(when, "%Y-%m-%d").weekday()
    code = await make_coupon(db, cleanup, allowed_weekdays=[day], stackable_with_offers=True)
    bs = BookingService(db)
    booking = await bs.create_booking(c[0], _req(c, service, when, slot, code))
    other_when, other_slot = await _slot(db, center_id, offset=3)
    assert datetime.strptime(other_when, "%Y-%m-%d").weekday() != day
    with pytest.raises(BadRequestException, match="Only Valid On"):
        await bs.reschedule_booking(booking["id"], BookingRescheduleRequest(scheduled_date=other_when, scheduled_slot=other_slot), c[0], "customer")
    assert (await db.bookings.find_one({"_id": ObjectId(booking["id"])}))["coupon_code"] == code


async def test_quote_shows_a_refusal_beside_the_coupon_and_never_consumes(db, cleanup):
    center_id, service, when, slot, c = await _book_rig(db, cleanup)
    hatch = await get_hatchback_type_id(db)
    plans_only = await make_coupon(db, cleanup, applies_to=["plans"])
    good = await make_coupon(db, cleanup)
    bs = BookingService(db)
    lines = [QuickBookingLine(vehicle_type=hatch, quantity=1, service_ids=[service])]
    phone = (await db.users.find_one({"_id": ObjectId(c[0])}))["phone"]
    bad = await bs.quote_visit(customer_id=c[0], phone=phone, lines=lines, coupon_code=plans_only, scheduled_date=when)
    assert bad["coupon_code"] is None and "Not Valid For Bookings" in bad["coupon_error"]
    ok = await bs.quote_visit(customer_id=c[0], phone=phone, lines=lines, coupon_code=good, scheduled_date=when)
    assert ok["coupon_code"] == good and ok["coupon_discount"] == 100 and ok["total_amount"] == ok["subtotal"] - 100
    assert (await db.coupons.find_one({"code": good}))["total_used"] == 0
    # An anonymous quote applies it the same way (nothing is revealed about the customer).
    anon = await bs.quote_visit(customer_id=None, phone=None, lines=lines, coupon_code=good, anonymous=True, scheduled_date=when)
    assert anon["coupon_discount"] == 100


# ------------------------------------------------------------------ migration of existing coupons


async def test_old_coupons_keep_behaving_and_are_migrated_to_explicit_rules(db, cleanup):
    now = now_ist()
    base = {"coupon_type": "flat", "value": 50, "min_order_value": 0, "usage_limit_per_user": 1, "total_usage_limit": None, "total_used": 0,
            "valid_from": now - timedelta(days=1), "valid_until": now + timedelta(days=9), "offer_kind": "standard", "is_active": True, "is_deleted": False}
    old, used_on_plan = _code("OLD"), _code("OLD")
    ids = []
    for code in (old, used_on_plan):
        res = await db.coupons.insert_one({**base, "code": code})
        ids.append(res.inserted_id)
        cleanup.append(("coupons", {"_id": res.inserted_id}))
    uid = await user(db, cleanup)
    # Before the migration an old coupon does what it always did: everywhere, stacking.
    svc_ = CouponService(db)
    for use in ("plans", "society"):
        assert (await svc_.validate_and_compute_discount(old, 999, uid, use=use))[1] == 50
    assert (await booking_discount(db, old, uid, offer_applied=True))[1] == 50
    sub = await db.user_subscriptions.insert_one({"customer_id": uid, "coupon_code": used_on_plan, "status": "active", "is_deleted": False})
    cleanup.append(("user_subscriptions", {"_id": sub.inserted_id}))
    await svc_.migrate_legacy_rules()
    a, b = await db.coupons.find_one({"code": old}), await db.coupons.find_one({"code": used_on_plan})
    assert a["applies_to"] == ["services"] and b["applies_to"] == ["services", "plans"]
    assert a["stackable_with_offers"] is True and a["combine_with_plan"] is True
    with pytest.raises(BadRequestException, match="Not Valid For Plans"):
        await svc_.validate_and_compute_discount(old, 999, uid, use="plans")
    assert (await svc_.validate_and_compute_discount(used_on_plan, 999, uid, use="plans"))[1] == 50
    assert await svc_.migrate_legacy_rules() == 0  # idempotent


# ------------------------------------------------------------------ the admin API


async def test_admin_api_saves_and_validates_rules(db, cleanup):
    from httpx import ASGITransport, AsyncClient

    from app.core.security import create_access_token
    from app.main import app

    admin = await db.users.find_one({"role": "admin"})
    head = {"Authorization": f"Bearer {create_access_token(str(admin['_id']), 'admin', {'service_center_id': None, 'tv': admin.get('token_version', 0)})}"}
    now = datetime.now(timezone.utc)
    body = {"code": _code("API"), "coupon_type": "percentage", "value": 20, "max_discount_amount": 150, "valid_from": now.isoformat(),
            "valid_until": (now + timedelta(days=5)).isoformat(), "applies_to": ["services", "plans"], "first_booking_only": True,
            "applicable_vehicle_types": ["vt1"], "allowed_customer_phones": ["+91 98765 43210"], "allowed_weekdays": [5, 6],
            "time_window_start": "09:00", "time_window_end": "12:00", "stackable_with_offers": True}
    cleanup.append(("coupons", {"code": body["code"]}))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        res = await client.post("/api/v1/coupons", json=body, headers=head)
        assert res.status_code == 200, res.text
        data = res.json()["data"]
        assert data["applies_to"] == ["services", "plans"] and data["allowed_customer_phones"] == ["9876543210"]
        assert data["combine_with_plan"] is False and data["allowed_weekdays"] == [5, 6]
        # defaults: a coupon created with no rules is bookings-only
        plain = {**body, "code": _code("API"), "applies_to": None}
        del plain["applies_to"]
        cleanup.append(("coupons", {"code": plain["code"]}))
        assert (await client.post("/api/v1/coupons", json=plain, headers=head)).json()["data"]["applies_to"] == ["services"]
        # refusals
        for bad in ({"applies_to": []}, {"applies_to": ["everything"]}, {"allowed_customer_phones": ["12345"]}, {"allowed_weekdays": [9]},
                    {"time_window_start": "12:00", "time_window_end": "09:00"}, {"time_window_start": "09:00", "time_window_end": None},
                    {"value": -5}, {"max_discount_amount": -1}, {"value": 150}):
            res = await client.post("/api/v1/coupons", json={**body, "code": _code("API"), **bad}, headers=head)
            assert res.status_code in (400, 422), (bad, res.text)
        # editing rules, clearing the window
        cid = data["id"]
        res = await client.put(f"/api/v1/coupons/{cid}", json={"applies_to": ["plans"], "time_window_start": None, "time_window_end": None, "allowed_weekdays": []}, headers=head)
        assert res.status_code == 200 and res.json()["data"]["applies_to"] == ["plans"] and res.json()["data"]["time_window_start"] is None


async def test_first_wash_price_and_a_non_stackable_coupon_on_a_real_booking(db, cleanup):
    center_id, service, when, slot, c = await _book_rig(db, cleanup)
    base = await db.services.find_one({"_id": ObjectId(service)})
    clone = {k: v for k, v in base.items() if k != "_id"}
    tag = ObjectId()
    clone.update(name=f"First Wash Test {tag}", slug=f"first-wash-test-{tag}", discounted_price=float(base["price"]) - 50,
                 vehicle_type_discounted_prices={})
    inserted = await db.services.insert_one(clone)
    cleanup.append(("services", {"_id": inserted.inserted_id}))
    service = str(inserted.inserted_id)
    plain = await make_coupon(db, cleanup, stackable_with_offers=False)
    stack = await make_coupon(db, cleanup, stackable_with_offers=True)
    bs = BookingService(db)
    with pytest.raises(BadRequestException, match="First Wash Offer"):
        await bs.create_booking(c[0], _req(c, service, when, slot, plain))
    assert await db.bookings.count_documents({"customer_id": c[0]}) == 0
    booking = await bs.create_booking(c[0], _req(c, service, when, slot, stack))
    assert booking["coupon_code"] == stack
    # The customer's NEXT booking is no longer a first wash: the plain coupon works.
    when2, slot2 = await _slot(db, center_id, offset=3)
    second = await bs.create_booking(c[0], _req(c, service, when2, slot2, plain))
    assert second["coupon_code"] == plain


async def test_bundle_api_saves_rules_and_one_edit_changes_every_code(db, cleanup):
    from httpx import ASGITransport, AsyncClient

    from app.core.security import create_access_token
    from app.main import app

    admin = await db.users.find_one({"role": "admin"})
    head = {"Authorization": f"Bearer {create_access_token(str(admin['_id']), 'admin', {'service_center_id': None, 'tv': admin.get('token_version', 0)})}"}
    uid = await user(db, cleanup)
    codes = [f"RULB-{random.randint(10**5, 10**6)}" for _ in range(3)]
    body = {"name": f"Rules Bundle {ObjectId()}", "coupon_type": "flat", "value": 80, "uses_per_coupon": 1, "codes": codes,
            "valid_until": (datetime.now(timezone.utc) + timedelta(days=9)).isoformat(), "applies_to": ["plans"], "new_customers_only": True,
            "allowed_weekdays": [1], "stackable_with_offers": False}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        res = await client.post("/api/v1/coupons/bundles", json=body, headers=head)
        assert res.status_code == 200, res.text
        bundle = res.json()["data"]["bundle"]
        bid = bundle["id"]
        cleanup += [("coupon_codes", {"bundle_id": bid}), ("coupon_bundles", {"_id": ObjectId(bid)})]
        assert bundle["applies_to"] == ["plans"] and bundle["new_customers_only"] is True and bundle["allowed_weekdays"] == [1]
        # Rule-less create = bookings only, like a regular coupon.
        plain = await client.post("/api/v1/coupons/bundles", json={**body, "name": f"Plain {ObjectId()}", "codes": [f"RULP-{random.randint(10**5, 10**6)}"],
                                                                     "applies_to": None, "new_customers_only": False, "allowed_weekdays": []}, headers=head)
        assert plain.status_code in (200, 422)
        for code in codes:
            with pytest.raises(BadRequestException, match="Not Valid For Bookings"):
                await booking_discount(db, code, uid)
        res = await client.put(f"/api/v1/coupons/bundles/{bid}", json={"applies_to": ["services"], "new_customers_only": False, "allowed_weekdays": []}, headers=head)
        assert res.status_code == 200 and res.json()["data"]["applies_to"] == ["services"]
        for code in codes:
            assert (await booking_discount(db, code, uid))[1] == 80
        assert (await client.put(f"/api/v1/coupons/bundles/{bid}", json={"applies_to": ["custom"]}, headers=head)).status_code == 422


async def test_quick_booking_applies_the_coupon_and_the_expected_total_guards_the_price(db, cleanup):
    """The no-login / OTP path (create_quick_booking): the coupon is quoted,
    the shown total is checked, and the booking commits the code once."""
    from app.schemas.booking_schema import QuickBookingRequest

    center_id, service, when, slot, c = await _book_rig(db, cleanup)
    hatch = await get_hatchback_type_id(db)
    code = await make_coupon(db, cleanup, value=100, stackable_with_offers=True)
    customer = await db.users.find_one({"_id": ObjectId(c[0])})
    bs = BookingService(db)
    lines = [QuickBookingLine(vehicle_type=hatch, quantity=1, service_ids=[service])]
    quote = await bs.quote_visit(customer_id=c[0], phone=customer["phone"], lines=lines, coupon_code=code, scheduled_date=when,
                                 address=await db.addresses.find_one({"_id": ObjectId(c[2])}))
    assert quote["coupon_discount"] == 100

    def request(expected):
        return QuickBookingRequest(customer_name="Quick Coupon", customer_phone=customer["phone"], address_id=c[2], lines=lines,
                                   scheduled_date=when, scheduled_slot=slot, coupon_code=f" {code.lower()} ", expected_total=expected)

    with pytest.raises(AppException) as stale:  # the customer was shown a lower price than the server would charge
        await bs.create_quick_booking(request(quote["total_amount"] - 1), customer=customer, source="app")
    assert stale.value.status_code == 409
    assert (await db.coupons.find_one({"code": code}))["total_used"] == 0
    result = await bs.create_quick_booking(request(quote["total_amount"]), customer=customer, source="app")
    assert result["total_amount"] == quote["total_amount"]
    assert (await db.coupons.find_one({"code": code}))["total_used"] == 1
