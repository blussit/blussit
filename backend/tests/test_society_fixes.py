"""
Society fixes (2026-10-03): staff/resident premium bookings (the multi-car
500), JSON 500s that keep their CORS headers, the premium wash named for
what it is, and coupons on society enrollment / activation / renewal.
"""
import itertools
from datetime import datetime, timedelta, timezone

import pytest
from bson import ObjectId

from app.core.config import settings
from app.core.exceptions import BadRequestException
from app.schemas.payment_schema import CreateOrderRequest, VerifyPaymentRequest
from app.schemas.society_schema import PremiumBookingRequest, SocietyEnrollRequest, SocietyQuoteRequest
from app.services import payment_service
from app.services.booking_service import BookingService
from app.services.payment_service import PaymentService, _expected_signature
from app.services.society_service import SocietyService
from app.utils.timezone import now_ist

from tests.factories import get_hatchback_type_id, get_star_wash_service_id
from tests.society_factories import (
    activate_cash,
    auth,
    client,
    enroll,
    make_admin,
    make_center,
    make_resident,
    make_society,
    make_template,
    plate,
    staff,
)

pytestmark = pytest.mark.asyncio
_seq = itertools.count(1)


def _tomorrow() -> str:
    return (now_ist().date() + timedelta(days=1)).isoformat()


@pytest.fixture
async def rig(db, cleanup):
    center = await make_center(db, cleanup, spot=3)
    other = await make_center(db, cleanup, spot=4)
    manager_id, captain_id = await staff(db, cleanup, center["id"])
    other_manager, _ = await staff(db, cleanup, other["id"])
    society = await make_society(db, cleanup, center, name=f"Palm Court {next(_seq)}")
    plan = await make_template(db, cleanup)
    admin_id = await make_admin(db, cleanup)
    return {
        "center": center, "other": other, "manager": manager_id, "captain": captain_id, "other_manager": other_manager,
        "society": society, "plan": plan, "admin": admin_id,
    }


async def _active(db, cleanup, rig, cars: int = 2) -> tuple[dict, dict]:
    resident = await make_resident(db, cleanup)
    view = await enroll(db, rig["society"], resident, rig["plan"]["id"], cars=cars)
    return resident, (await activate_cash(db, view["id"]))["enrollment"]


# -- 1. premium bookings ------------------------------------------------------------


async def test_staff_book_premium_for_several_cars_admin_and_manager(db, cleanup, rig):
    """The modal's 'Book premium' with two cars used to 500 (BookingCreateRequest
    got payment_method=None on the group path)."""
    resident_a, enrollment_a = await _active(db, cleanup, rig)
    resident_b, enrollment_b = await _active(db, cleanup, rig)
    sid = rig["society"]["id"]
    subs_a = [c["subscription"]["id"] for c in enrollment_a["cars"]]
    subs_b = [c["subscription"]["id"] for c in enrollment_b["cars"]]
    async with client() as c:
        resp = await c.post(f"/api/v1/societies/{sid}/premium-bookings", headers=auth(rig["admin"], "admin"),
                            json={"subscription_ids": subs_a, "scheduled_date": _tomorrow(), "scheduled_slot": "08:00-11:00"})
        assert resp.status_code == 200, resp.text
        assert len(resp.json()["data"]["bookings"]) == 2
        manager_h = auth(rig["manager"], "manager", rig["center"]["id"])
        resp = await c.post(f"/api/v1/societies/{sid}/premium-bookings", headers=manager_h,
                            json={"subscription_ids": subs_b, "scheduled_date": _tomorrow(), "scheduled_slot": "11:00-14:00"})
        assert resp.status_code == 200, resp.text
        # Single car works too.
        resident_c, enrollment_c = await _active(db, cleanup, rig, cars=1)
        resp = await c.post(f"/api/v1/societies/{sid}/premium-bookings", headers=manager_h,
                            json={"subscription_ids": [enrollment_c["cars"][0]["subscription"]["id"]], "scheduled_date": _tomorrow(), "scheduled_slot": "14:00-17:00"})
        assert resp.status_code == 200, resp.text
        # Another center's manager: refused, nothing booked.
        resp = await c.post(f"/api/v1/societies/{sid}/premium-bookings", headers=auth(rig["other_manager"], "manager", rig["other"]["id"]),
                            json={"subscription_ids": subs_a, "scheduled_date": _tomorrow(), "scheduled_slot": "14:00-17:00"})
        assert resp.status_code == 403
    star = await get_star_wash_service_id(db)
    for sub_id in subs_a + subs_b:
        bookings = await db.bookings.find({"subscription_id": sub_id}).to_list(length=5)
        assert len(bookings) == 1
        b = bookings[0]
        # Plan-covered: the pass pays, only the premium service, nothing to collect.
        assert b["payment_method"] == "subscription" and b["total_amount"] == 0 and b["payment_status"] == "paid"
        assert b["service_ids"] == [star] and b["status"] == "pending"
        assert (await db.user_subscriptions.find_one({"_id": ObjectId(sub_id)}))["remaining_service_count"] == 1
    # The two cars of one resident are one visit.
    groups = {b.get("booking_group_id") for b in await db.bookings.find({"subscription_id": {"$in": subs_a}}).to_list(length=5)}
    assert len(groups) == 1 and None not in groups


async def test_resident_books_premium_for_two_cars(db, cleanup, rig):
    resident, enrollment = await _active(db, cleanup, rig)
    subs = [c["subscription"]["id"] for c in enrollment["cars"]]
    async with client() as c:
        resp = await c.post("/api/v1/societies/my/premium-bookings", headers=auth(str(resident["_id"]), "customer"),
                            json={"subscription_ids": subs, "scheduled_date": _tomorrow(), "scheduled_slot": "08:00-11:00"})
        assert resp.status_code == 200, resp.text
        assert {b["total_amount"] for b in resp.json()["data"]["bookings"]} == {0}
    assert await db.bookings.count_documents({"subscription_id": {"$in": subs}, "payment_method": "subscription"}) == 2


async def test_unhandled_error_is_a_json_500_with_cors_headers():
    """Starlette's catch-all answers outside the CORS middleware — the
    browser then saw a CORS failure ("Can't reach the server")."""
    from app.main import app

    async def boom():
        raise RuntimeError("kaboom")

    path = f"/api/v1/__test_boom_{next(_seq)}"
    app.add_api_route(path, boom, methods=["GET"])
    origin = settings.cors_origins_list[0]
    try:
        async with client() as c:
            resp = await c.get(path, headers={"Origin": origin})
    finally:
        app.router.routes[:] = [r for r in app.router.routes if getattr(r, "path", None) != path]
    assert resp.status_code == 500
    assert resp.json() == {"success": False, "error_code": "SERVER_ERROR", "message": "Something went wrong — please try again."}
    assert resp.headers.get("access-control-allow-origin") == origin
    assert "kaboom" not in resp.text


# -- 2. the premium wash by name -------------------------------------------------------


async def test_usage_and_bookings_name_the_premium_wash(db, cleanup, rig):
    resident, enrollment = await _active(db, cleanup, rig, cars=1)
    assert enrollment["premium_service_name"] == "Star Wash"
    assert enrollment["bucket_short_label"] == "Daily wash"
    service = SocietyService(db)
    raw = await service.societies.find_by_id(rig["society"]["id"])
    hub = await service.my_hub(raw, str(resident["_id"]))
    assert hub["enrollments"][0]["premium_service_name"] == "Star Wash"
    # Captain's checklist carries the same names.
    await service.set_captain(raw, rig["captain"], None)
    card = (await service.captain_today(rig["captain"]))["societies"][0]
    car = card["cars"][0]
    assert car["premium_service_name"] == "Star Wash" and car["bucket_label"] == "Daily wash"
    assert car["premium_remaining"] == 2 and car["premium_total"] == 2
    # A booking on the society pass reads "Star Wash · Society plan (<society>)".
    sub_id = enrollment["cars"][0]["subscription"]["id"]
    booked = await service.book_premium(
        PremiumBookingRequest(subscription_ids=[sub_id], scheduled_date=_tomorrow(), scheduled_slot="08:00-11:00"),
        actor_id=str(resident["_id"]), actor_role="customer", actor_center_id=None,
    )
    view = await BookingService(db).get_booking(booked["bookings"][0]["id"])
    assert view["service_names"] == ["Star Wash"]
    assert view["society_name"] == rig["society"]["name"]
    assert view["plan_label"] == f"Society plan ({rig['society']['name']})"
    hub = await service.my_hub(raw, str(resident["_id"]))
    assert hub["upcoming"][0]["service_name"] == "Star Wash"


# -- 3. coupons --------------------------------------------------------------------------


async def _coupon(db, cleanup, *, kind: str = "percentage", value: float = 10, min_order: float = 0, per_user: int = 1,
                  total_limit: int | None = None, days: int = 5) -> str:
    code = f"SOC{next(_seq)}{ObjectId().__str__()[-4:].upper()}"
    now = datetime.now(timezone.utc)
    await db.coupons.insert_one({
        "code": code, "description": "society test", "coupon_type": kind, "value": float(value), "min_order_value": float(min_order),
        "max_discount_amount": None, "usage_limit_per_user": per_user, "total_usage_limit": total_limit, "total_used": 0,
        "valid_from": now - timedelta(days=1), "valid_until": now + timedelta(days=days), "is_active": True,
        "offer_kind": "standard", "is_deleted": False,
    })
    coupon = await db.coupons.find_one({"code": code})
    cleanup.append(("coupon_usages", {"coupon_id": str(coupon["_id"])}))
    cleanup.append(("coupons", {"code": code}))
    return code


async def _uses(db, code: str) -> tuple[int, int]:
    coupon = await db.coupons.find_one({"code": code})
    return int(coupon.get("total_used") or 0), await db.coupon_usages.count_documents({"coupon_id": str(coupon["_id"])})


async def test_quote_shows_the_coupon_discount_or_why_not(db, cleanup, rig):
    code = await _coupon(db, cleanup, value=10)
    big_min = await _coupon(db, cleanup, value=10, min_order=99999)
    hatch = await get_hatchback_type_id(db)
    token = rig["society"]["form_token"]
    async with client() as c:
        q = (await c.post(f"/api/v1/society-forms/{token}/quote", json={"plan_id": rig["plan"]["id"], "vehicle_types": [hatch, hatch], "coupon_code": code.lower()})).json()["data"]
        assert q["total"] == 3298 and q["discount"] == 330 and q["payable_total"] == 2968  # 10% of ₹3298, whole rupees
        assert q["coupon"] == {"code": code, "valid": True, "discount": 330, "error": None}
        bad = (await c.post(f"/api/v1/society-forms/{token}/quote", json={"plan_id": rig["plan"]["id"], "vehicle_types": [hatch], "coupon_code": big_min})).json()["data"]
        assert bad["coupon"]["valid"] is False and "Minimum order" in bad["coupon"]["error"] and bad["payable_total"] == 1649
        none = (await c.post(f"/api/v1/society-forms/{token}/quote", json={"plan_id": rig["plan"]["id"], "vehicle_types": [hatch], "coupon_code": "NOPE1"})).json()["data"]
        assert none["coupon"]["valid"] is False and none["discount"] == 0


async def test_coupon_is_frozen_on_request_counted_once_on_activation(db, cleanup, rig):
    code = await _coupon(db, cleanup, kind="flat", value=101)
    resident = await make_resident(db, cleanup)
    hatch = await get_hatchback_type_id(db)
    service = SocietyService(db)
    raw = await service.societies.find_by_id(rig["society"]["id"])
    payload = SocietyEnrollRequest(plan_id=rig["plan"]["id"], resident_name="Coupon Resident", phone=resident["phone"], flat="C-1",
                                   cars=[{"vehicle_type": hatch, "registration_number": plate()} for _ in range(2)], coupon_code=code)
    view = await service.enroll(raw, payload, resident, source="form")
    assert view["coupon_code"] == code and view["discount_amount"] == 101 and view["payable_amount"] == 3298 - 101
    assert await _uses(db, code) == (0, 0)  # never on request

    result = await activate_cash(db, view["id"])
    enrollment = result["enrollment"]
    assert enrollment["payment"]["amount"] == 3197 and enrollment["payment"]["discount"] == 101 and enrollment["payment"]["coupon_code"] == code
    assert await _uses(db, code) == (1, 1)
    subs = await db.user_subscriptions.find({"enrollment_id": view["id"]}).to_list(length=5)
    assert sorted(s["amount_paid"] for s in subs) == [1598.0, 1599.0]  # ₹101 split by price, whole rupees
    assert all(s["purchased_price"] == 1649.0 for s in subs)
    ledger = await db.society_payments.find_one({"enrollment_id": view["id"], "kind": "activation"})
    assert ledger["amount"] == 3197 and ledger["gross_amount"] == 3298 and ledger["discount_amount"] == 101
    # Activating again is refused — and counts nothing more.
    with pytest.raises(BadRequestException):
        await activate_cash(db, view["id"])
    assert await _uses(db, code) == (1, 1)

    # Per-resident limit (1): the same resident can't use it on another request.
    other_society = await make_society(db, cleanup, rig["center"])
    raw2 = await service.societies.find_by_id(other_society["id"])
    again = payload.model_copy(update={"cars": [payload.cars[0].model_copy(update={"registration_number": plate()})]})
    with pytest.raises(BadRequestException, match="maximum number of times"):
        await service.enroll(raw2, again, resident, source="form")


async def test_withdrawn_request_never_keeps_a_coupon_use(db, cleanup, rig):
    code = await _coupon(db, cleanup, value=5)
    resident = await make_resident(db, cleanup)
    hatch = await get_hatchback_type_id(db)
    service = SocietyService(db)
    raw = await service.societies.find_by_id(rig["society"]["id"])
    view = await service.enroll(raw, SocietyEnrollRequest(
        plan_id=rig["plan"]["id"], resident_name="W Resident", phone=resident["phone"], flat="W-1",
        cars=[{"vehicle_type": hatch, "registration_number": plate()}], coupon_code=code), resident, source="form")
    # A use counted against this request (e.g. an activation that couldn't
    # place any car fell back to an open request) is given back on withdraw.
    coupon = await db.coupons.find_one({"code": code})
    await service._record_coupon(code, str(resident["_id"]), f"society:{view['id']}")
    await db.society_enrollments.update_one({"_id": ObjectId(view["id"])}, {"$set": {"coupon_usage_recorded": True}})
    assert await _uses(db, code) == (1, 1)
    token = rig["society"]["form_token"]
    async with client() as c:
        resp = await c.post(f"/api/v1/society-forms/{token}/me/enrollments/{view['id']}/withdraw", headers=auth(str(resident["_id"]), "customer"))
        assert resp.status_code == 200, resp.text
    assert await _uses(db, code) == (0, 0)
    assert coupon is not None


async def test_staff_mark_paid_and_renew_with_a_coupon(db, cleanup, rig):
    code = await _coupon(db, cleanup, value=10, per_user=5)
    resident = await make_resident(db, cleanup)
    view = await enroll(db, rig["society"], resident, rig["plan"]["id"], cars=1)
    h = auth(rig["manager"], "manager", rig["center"]["id"])
    async with client() as c:
        preview = (await c.post(f"/api/v1/society-enrollments/{view['id']}/coupon-preview", headers=h, json={"coupon_code": code})).json()["data"]
        assert preview == {"subtotal": 1649, "discount": 165, "payable": 1484, "coupon": {"code": code, "valid": True, "discount": 165, "error": None}}
        # Other center's manager can't even preview.
        assert (await c.post(f"/api/v1/society-enrollments/{view['id']}/coupon-preview", headers=auth(rig["other_manager"], "manager", rig["other"]["id"]),
                             json={"coupon_code": code})).status_code == 403
        bad = await c.post(f"/api/v1/society-enrollments/{view['id']}/activate", headers=h,
                           json={"method": "cash", "expected_revision": view["revision"], "coupon_code": "NOPE77"})
        assert bad.status_code == 400 and "NOPE77" in bad.json()["message"]
        assert (await db.society_enrollments.find_one({"_id": ObjectId(view["id"])}))["status"] == "requested"
        ok = await c.post(f"/api/v1/society-enrollments/{view['id']}/activate", headers=h,
                          json={"method": "cash", "expected_revision": view["revision"], "coupon_code": code})
        assert ok.status_code == 200, ok.text
        assert ok.json()["data"]["payment"]["amount"] == 1484
    assert await _uses(db, code) == (1, 1)

    # Renewal with a coupon (inside the window).
    sub = await db.user_subscriptions.find_one({"enrollment_id": view["id"]})
    await db.user_subscriptions.update_one({"_id": sub["_id"]}, {"$set": {"end_date": now_ist() + timedelta(days=2)}})
    async with client() as c:
        preview = (await c.post(f"/api/v1/society-enrollments/{view['id']}/coupon-preview", headers=h, json={"coupon_code": code, "renewal": True})).json()["data"]
        assert preview["subtotal"] == 1649 and preview["payable"] == 1484
        resp = await c.post(f"/api/v1/society-enrollments/{view['id']}/renew", headers=h, json={"method": "cash", "coupon_code": code})
        assert resp.status_code == 200, resp.text
    renewal = await db.society_payments.find_one({"enrollment_id": view["id"], "kind": "renewal"})
    assert renewal["amount"] == 1484 and renewal["discount_amount"] == 165 and renewal["coupon_code"] == code
    assert await _uses(db, code) == (2, 2)
    assert (await db.user_subscriptions.find_one({"_id": sub["_id"]}))["amount_paid"] == 1484.0


async def test_manager_remove_coupon_and_resident_coupon_rechecked(db, cleanup, rig):
    code = await _coupon(db, cleanup, value=10, days=5)
    resident = await make_resident(db, cleanup)
    hatch = await get_hatchback_type_id(db)
    service = SocietyService(db)
    raw = await service.societies.find_by_id(rig["society"]["id"])
    view = await service.enroll(raw, SocietyEnrollRequest(
        plan_id=rig["plan"]["id"], resident_name="R Resident", phone=resident["phone"], flat="R-1",
        cars=[{"vehicle_type": hatch, "registration_number": plate()}], coupon_code=code), resident, source="form")
    # The coupon expires before the manager collects: refused with the reason…
    await db.coupons.update_one({"code": code}, {"$set": {"valid_until": datetime.now(timezone.utc) - timedelta(minutes=1)}})
    with pytest.raises(BadRequestException, match="expired"):
        await service.activate(await service.get_enrollment(view["id"]), method="cash", actor_id=rig["manager"], expected_revision=view["revision"])
    # …and the manager can drop it and collect the full price.
    result = await service.activate(await service.get_enrollment(view["id"]), method="cash", actor_id=rig["manager"],
                                    expected_revision=view["revision"], remove_coupon=True)
    assert result["enrollment"]["payment"]["amount"] == 1649 and result["enrollment"]["payment"]["discount"] == 0
    assert await _uses(db, code) == (0, 0)


async def test_online_payment_charges_the_discounted_total(db, cleanup, rig, monkeypatch):
    class _Orders:
        def create(self, payload):
            return {"id": f"order_socfix_{next(_seq):05d}", **payload}

    class _Client:
        order = _Orders()

    monkeypatch.setattr(payment_service.settings, "RAZORPAY_KEY_ID", "rzp_test_stub")
    monkeypatch.setattr(payment_service.settings, "RAZORPAY_KEY_SECRET", "stub_secret_key")
    monkeypatch.setattr(payment_service, "_razorpay_client", lambda: _Client())
    code = await _coupon(db, cleanup, kind="flat", value=200, per_user=3)
    resident = await make_resident(db, cleanup)
    customer_id = str(resident["_id"])
    hatch = await get_hatchback_type_id(db)
    service = SocietyService(db)
    raw = await service.societies.find_by_id(rig["society"]["id"])
    view = await service.enroll(raw, SocietyEnrollRequest(
        plan_id=rig["plan"]["id"], resident_name="O Resident", phone=resident["phone"], flat="O-1", pay_now=True,
        cars=[{"vehicle_type": hatch, "registration_number": plate()}], coupon_code=code), resident, source="form")
    payments = PaymentService(db)
    order = await payments.create_order(customer_id, CreateOrderRequest(purpose="society", society_enrollment_id=view["id"]))
    assert order["amount"] == (1649 - 200) * 100
    assert await _uses(db, code) == (0, 0)
    result = await payments.verify_payment(customer_id, VerifyPaymentRequest(
        razorpay_order_id=order["order_id"], razorpay_payment_id="pay_socfix_1",
        razorpay_signature=_expected_signature(order["order_id"], "pay_socfix_1")))
    assert result["status"] == "paid"
    enrollment = await db.society_enrollments.find_one({"_id": ObjectId(view["id"])})
    assert enrollment["status"] == "active" and enrollment["payment"]["amount"] == 1449
    assert await _uses(db, code) == (1, 1)

    # Online renewal with a coupon: the order is priced with it.
    await db.user_subscriptions.update_one({"enrollment_id": view["id"]}, {"$set": {"end_date": now_ist() + timedelta(days=1)}})
    renewal = await payments.create_order(customer_id, CreateOrderRequest(
        purpose="society", society_enrollment_id=view["id"], society_renewal=True, society_coupon_code=code.lower()))
    assert renewal["amount"] == (1649 - 200) * 100
    result = await payments.verify_payment(customer_id, VerifyPaymentRequest(
        razorpay_order_id=renewal["order_id"], razorpay_payment_id="pay_socfix_2",
        razorpay_signature=_expected_signature(renewal["order_id"], "pay_socfix_2")))
    assert result["status"] == "paid", result
    ledger = await db.society_payments.find_one({"enrollment_id": view["id"], "kind": "renewal"})
    assert ledger["amount"] == 1449 and ledger["discount_amount"] == 200
    assert await _uses(db, code) == (2, 2)


async def test_free_addon_offers_are_refused_for_society_plans(db, cleanup, rig):
    code = await _coupon(db, cleanup)
    await db.coupons.update_one({"code": code}, {"$set": {"offer_kind": "free_addon_with_service"}})
    hatch = await get_hatchback_type_id(db)
    quote = await SocietyService(db).quote(
        await SocietyService(db).societies.find_by_id(rig["society"]["id"]),
        SocietyQuoteRequest(plan_id=rig["plan"]["id"], vehicle_types=[hatch], coupon_code=code), [hatch], None,
    )
    assert quote["coupon"]["valid"] is False and "bookings only" in quote["coupon"]["error"]
