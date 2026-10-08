"""Custom multi-car plan (manager cart) — docs/FEATURE_PLAN_WALLET_EDITS_PLANS_2026-10-07.md §1.6.

A manager builds a cart for one customer: per car, per-service counts. The
server prices it (Σ count × the car type's standard price, less a capped
whole-rupee discount), one link or cash pays the whole cart (frozen to its
revision), and activation makes one car-bound pass per car with per-service
quotas. Bookings spend those quotas per service (add-ons and services
without a quota are paid). Razorpay is stubbed; local Mongo only."""
import asyncio
import itertools
from datetime import datetime, timedelta, timezone

import pytest
from bson import ObjectId

from app.core.exceptions import AppException, BadRequestException, ConflictException, NotFoundException
from app.schemas.custom_plan_schema import CustomPlanCreateRequest, CustomPlanPreviewRequest, CustomPlanReviseRequest
from app.schemas.subscription_schema import AssignSubscriptionRequest
from app.services import payment_service
from app.services.custom_plan_service import TEMPLATE_PLAN_ID, CustomPlanService
from app.services.kpi_service import KpiService
from app.services.subscription_service import SubscriptionPlanService, UserSubscriptionService
from app.utils.timezone import now_ist

from tests.factories import (
    get_hatchback_type_id,
    get_suv_type_id,
    make_customer,
    make_manager,
    make_service_center,
    make_subscription_plan,
    make_vehicle,
)
from tests.society_factories import auth, client, make_admin

pytestmark = pytest.mark.asyncio
_seq = itertools.count(1)


class _StubLinks:
    def __init__(self):
        self.cancelled: list[str] = []
        self.created: list[dict] = []

    def create(self, payload):
        n = next(_seq)
        self.created.append(payload)
        return {"id": f"plink_cpl_{n:06d}", "short_url": f"https://rzp.io/l/cpl{n}", **payload}

    def cancel(self, link_id):
        self.cancelled.append(link_id)
        return {"id": link_id, "status": "cancelled"}


class _StubClient:
    def __init__(self):
        self.payment_link = _StubLinks()


@pytest.fixture
def gateway(monkeypatch):
    stub = _StubClient()
    monkeypatch.setattr(payment_service.settings, "RAZORPAY_KEY_ID", "rzp_test_stub")
    monkeypatch.setattr(payment_service.settings, "RAZORPAY_KEY_SECRET", "stub_secret_key")
    monkeypatch.setattr(payment_service, "_razorpay_client", lambda: stub)
    return stub


@pytest.fixture(autouse=True)
async def _tidy(db):
    since = datetime.now(timezone.utc) - timedelta(seconds=1)
    first = ObjectId.from_datetime(since)
    yield
    for name in ("user_subscriptions", "payment_orders", "custom_plans", "bookings", "notifications", "audit_logs"):
        await db[name].delete_many({"_id": {"$gte": first}})
    await db.pass_claims.delete_many({"created_at": {"$gte": since}})


@pytest.fixture
async def rig(db, cleanup):
    center = await make_service_center(db)
    other = await make_service_center(db)
    manager = await make_manager(db, center)
    other_manager = await make_manager(db, other)
    admin = await make_admin(db, cleanup)
    customer = await make_customer(db)
    hatch, suv = await get_hatchback_type_id(db), await get_suv_type_id(db)
    car_a = await make_vehicle(db, customer, hatch, registration_number=f"MP09CP{next(_seq):04d}")
    svc = {s["slug"]: str(s["_id"]) for s in await db.services.find({"slug": {"$in": ["star-wash", "deep-cleaning", "waterless-service", "exterior-polish"]}}).to_list(length=10)}
    for uid in (manager, other_manager, customer):
        cleanup.append(("users", {"_id": ObjectId(uid)}))
    cleanup.append(("vehicles", {"owner_id": customer}))
    cleanup.append(("service_centers", {"_id": {"$in": [ObjectId(center), ObjectId(other)]}}))
    return {
        "center": center, "other": other, "manager": manager, "other_manager": other_manager, "admin": admin,
        "customer": customer, "hatch": hatch, "suv": suv, "car_a": car_a, "svc": svc,
        "plate_b": f"MP09CQ{next(_seq):04d}",
    }


def _cars(rig, *, deep_a: int = 2, star_a: int = 2):
    return [
        {"vehicle_id": rig["car_a"], "items": [{"service_id": rig["svc"]["star-wash"], "count": star_a}, {"service_id": rig["svc"]["deep-cleaning"], "count": deep_a}]},
        {"registration_number": rig["plate_b"], "vehicle_type": rig["suv"],
         "items": [{"service_id": rig["svc"]["deep-cleaning"], "count": 1}, {"service_id": rig["svc"]["star-wash"], "count": 2}]},
    ]


def _mgr(rig) -> dict:
    return {"actor_id": rig["manager"], "actor_role": "manager", "actor_center_id": rig["center"]}


async def _cart(db, rig, *, discount: int = 0, cars=None) -> dict:
    payload = CustomPlanCreateRequest(customer_id=rig["customer"], cars=cars or _cars(rig), discount_amount=discount)
    # A manager reaches a customer known to their center (or sells by phone).
    await db.bookings.insert_one({"customer_id": rig["customer"], "service_center_id": rig["center"], "status": "completed", "is_deleted": False,
                                  "booking_number": f"BKCP{next(_seq):05d}", "created_at": datetime.now(timezone.utc)})
    return await CustomPlanService(db).create(payload, **_mgr(rig))


async def _link_order(db, rig, cart: dict) -> dict:
    sent = await CustomPlanService(db).send_link(cart["id"], expected_revision=cart["revision"], send_whatsapp=True, **_mgr(rig))
    order = await db.payment_orders.find_one({"_id": ObjectId(sent["order_id"])})
    return order


async def _paid(db, order: dict) -> dict:
    """What PaymentService's guarded claim leaves on a paid link order."""
    await db.payment_orders.update_one({"_id": order["_id"]}, {"$set": {"status": "paid", "paid_at": now_ist(), "razorpay_payment_id": "pay_cpl"}})
    return await db.payment_orders.find_one({"_id": order["_id"]})


async def _activated(db, rig, *, deep_a: int = 2, discount: int = 0) -> tuple[dict, list[dict]]:
    cart = await _cart(db, rig, discount=discount, cars=_cars(rig, deep_a=deep_a))
    order = await _paid(db, await _link_order(db, rig, cart))
    result = await CustomPlanService(db).activate_from_payment(order)
    assert result["ok"], result
    subs = await db.user_subscriptions.find({"custom_plan_id": cart["id"]}).to_list(length=10)
    return await CustomPlanService(db).view(await CustomPlanService(db).get(cart["id"])), subs


def _pass_for(subs: list[dict], vehicle_id: str) -> dict:
    return next(s for s in subs if s["vehicle_id"] == vehicle_id)


# ---------------------------------------------------------------- pricing


async def test_server_price_preview_equals_charge_and_pass_shares(db, rig, gateway):
    service = CustomPlanService(db)
    preview = await service.preview(CustomPlanPreviewRequest(customer_id=None, cars=[
        {"vehicle_type": rig["hatch"], "items": [{"service_id": rig["svc"]["star-wash"], "count": 2}, {"service_id": rig["svc"]["deep-cleaning"], "count": 2}]},
        {"vehicle_type": rig["suv"], "items": [{"service_id": rig["svc"]["deep-cleaning"], "count": 1}, {"service_id": rig["svc"]["star-wash"], "count": 2}]},
    ], discount_amount=100), actor_role="manager", actor_center_id=rig["center"])
    star = await db.services.find_one({"_id": ObjectId(rig["svc"]["star-wash"])})
    deep = await db.services.find_one({"_id": ObjectId(rig["svc"]["deep-cleaning"])})

    def std(s, vt):  # the STANDARD per-type price (never the first-time one)
        return float((s.get("vehicle_type_prices") or {}).get(vt, s["price"]))

    a = 2 * std(star, rig["hatch"]) + 2 * std(deep, rig["hatch"])
    b = std(deep, rig["suv"]) + 2 * std(star, rig["suv"])
    assert preview["subtotal"] == a + b and preview["discount_amount"] == 100 and preview["total_amount"] == a + b - 100
    assert [c["price"] for c in preview["cars"]] == [a, b]
    assert sum(c["discount_share"] for c in preview["cars"]) == 100 and all(float(c["discount_share"]).is_integer() for c in preview["cars"])

    cart = await _cart(db, rig, discount=100)
    assert cart["total_amount"] == preview["total_amount"] and cart["revision"] == 1 and cart["status"] == "draft"
    order = await _link_order(db, rig, cart)
    assert order["amount_paise"] == round(preview["total_amount"] * 100) and order["purpose"] == "custom_plan"
    assert order["custom_plan_id"] == cart["id"] and order["custom_plan_revision"] == 1 and order["service_center_id"] == rig["center"]
    assert gateway.payment_link.created[-1]["amount"] == order["amount_paise"]
    # The customer got the link (in-app + WhatsApp through notify()).
    assert await db.notifications.count_documents({"user_id": rig["customer"], "message": {"$regex": order["short_url"]}}) >= 1

    result = await CustomPlanService(db).activate_from_payment(await _paid(db, order))
    assert result["ok"] and result["activated"] == 2 and not result["skipped"]
    subs = await db.user_subscriptions.find({"custom_plan_id": cart["id"]}).to_list(length=10)
    assert len(subs) == 2
    assert round(sum(s["amount_paid"] for s in subs), 2) == preview["total_amount"]
    car_b = await db.vehicles.find_one({"owner_id": rig["customer"], "registration_number": rig["plate_b"]})
    pa, pb = _pass_for(subs, rig["car_a"]), _pass_for(subs, str(car_b["_id"]))
    assert pa["total_by_service"] == {rig["svc"]["star-wash"]: 2, rig["svc"]["deep-cleaning"]: 2} == pa["remaining_by_service"]
    assert pa["total_service_count"] == pa["remaining_service_count"] == 4 and pb["remaining_service_count"] == 3
    assert pa["plan_id"] == TEMPLATE_PLAN_ID and pa["plan_kind"] == "custom" and pa["service_id"] is None
    assert pa["service_center_id"] == rig["center"] and pa["vehicle_type"] == rig["hatch"] and pb["vehicle_type"] == rig["suv"]
    # All cars start the same day, a 30-day period.
    assert pa["start_date"] == pb["start_date"] and pa["end_date"] - pa["start_date"] == timedelta(days=30)
    view = await CustomPlanService(db).view(await CustomPlanService(db).get(cart["id"]))
    assert view["status"] == "active" and all(c["status"] == "active" for c in view["cars"])
    # Customer told once for the whole cart.
    assert await db.notifications.count_documents({"user_id": rig["customer"], "title": "Custom plan is active"}) == 1


async def test_discount_cap_and_bad_items_refused(db, rig):
    service = CustomPlanService(db)
    cars = [{"vehicle_type": rig["hatch"], "items": [{"service_id": rig["svc"]["star-wash"], "count": 2}]}]
    with pytest.raises(BadRequestException):  # > 50 % for a manager
        await service.preview(CustomPlanPreviewRequest(cars=cars, discount_amount=400), actor_role="manager", actor_center_id=rig["center"])
    big = await service.preview(CustomPlanPreviewRequest(cars=cars, discount_amount=400), actor_role="admin", actor_center_id=None)
    assert big["discount_amount"] == 400
    with pytest.raises(BadRequestException):  # add-ons are paid at the booking, never quota
        await service.preview(CustomPlanPreviewRequest(cars=[{"vehicle_type": rig["hatch"], "items": [{"service_id": rig["svc"]["exterior-polish"], "count": 1}]}]),
                              actor_role="manager", actor_center_id=rig["center"])
    with pytest.raises(Exception):  # the same service twice on one car
        CustomPlanPreviewRequest(cars=[{"vehicle_type": rig["hatch"], "items": [{"service_id": rig["svc"]["star-wash"], "count": 1}] * 2}])
    with pytest.raises(BadRequestException):  # the same car twice
        await _cart(db, rig, cars=[_cars(rig)[0], _cars(rig)[0]])


# ---------------------------------------------------------------- activation


async def test_activation_is_idempotent_across_verify_webhook_and_sweep(db, rig, gateway):
    cart = await _cart(db, rig)
    order = await _paid(db, await _link_order(db, rig, cart))
    service = CustomPlanService(db)
    results = await asyncio.gather(*[CustomPlanService(db).activate_from_payment(order) for _ in range(3)])
    assert all(r["ok"] for r in results), results
    assert len([r for r in results if not r.get("already")]) == 1
    assert await db.user_subscriptions.count_documents({"custom_plan_id": cart["id"]}) == 2
    again = await service.activate_from_payment(order)
    assert again["ok"] and again["already"]
    assert await db.user_subscriptions.count_documents({"custom_plan_id": cart["id"]}) == 2
    assert not await service.still_payable(order)
    assert await db.notifications.count_documents({"user_id": rig["customer"], "title": "Custom plan is active"}) == 1


async def test_crashed_activation_is_resumed_without_duplicates(db, rig, gateway):
    cart = await _cart(db, rig)
    order = await _paid(db, await _link_order(db, rig, cart))
    service = CustomPlanService(db)
    await service.activate_from_payment(order)
    # Simulate an instance that died mid-activation long ago: claimed, one
    # car's pass already written, cart never finished.
    raw = await service.get(cart["id"])
    cars = [{**c, "status": "pending", "subscription_id": None} for c in raw["cars"]]
    first_sub = raw["cars"][0]["subscription_id"]
    await db.user_subscriptions.delete_one({"_id": ObjectId(raw["cars"][1]["subscription_id"])})
    await db.custom_plans.update_one({"_id": raw["_id"]}, {"$set": {"status": "activating", "cars": cars, "activating_at": now_ist() - timedelta(minutes=5)}})
    result = await service.activate_from_payment(order)
    assert result["ok"] and result["activated"] == 2
    subs = await db.user_subscriptions.find({"custom_plan_id": cart["id"]}).to_list(length=10)
    assert len(subs) == 2 and first_sub in {str(s["_id"]) for s in subs}


async def test_paying_an_old_revision_is_parked_and_new_link_works(db, rig, gateway):
    service = CustomPlanService(db)
    cart = await _cart(db, rig)
    old = await _link_order(db, rig, cart)
    revised = await service.revise(cart["id"], CustomPlanReviseRequest(expected_revision=1, cars=_cars(rig, deep_a=1)), **_mgr(rig))
    assert revised["revision"] == 2 and revised["status"] == "draft" and revised["total_amount"] < cart["total_amount"]
    assert old["razorpay_link_id"] in gateway.payment_link.cancelled
    assert not await service.still_payable(old)
    # A stale revise is a 409, not an overwrite.
    with pytest.raises(ConflictException):
        await service.revise(cart["id"], CustomPlanReviseRequest(expected_revision=1, cars=_cars(rig)), **_mgr(rig))
    # The old link paid anyway (lost the cancel race) -> refused, parked.
    result = await service.activate_from_payment(await _paid(db, old))
    assert not result["ok"]
    assert await db.user_subscriptions.count_documents({"custom_plan_id": cart["id"]}) == 0
    assert (await service.get(cart["id"]))["status"] == "draft"
    # Linking the stale revision is refused; the current one activates.
    with pytest.raises(ConflictException):
        await service.send_link(cart["id"], expected_revision=1, send_whatsapp=False, **_mgr(rig))
    new = await _link_order(db, rig, revised)
    assert new["custom_plan_revision"] == 2 and await service.still_payable(new)
    assert (await service.activate_from_payment(await _paid(db, new)))["ok"]
    deep = rig["svc"]["deep-cleaning"]
    sub_a = await db.user_subscriptions.find_one({"custom_plan_id": cart["id"], "vehicle_id": rig["car_a"]})
    assert sub_a["total_by_service"][deep] == 1 and sub_a["custom_plan_revision"] == 2


async def test_car_on_another_pass_at_activation_is_skipped_and_parked(db, rig, gateway):
    service = CustomPlanService(db)
    cart = await _cart(db, rig)
    order = await _paid(db, await _link_order(db, rig, cart))
    # Meanwhile car A got a monthly pass (one car carries one pass).
    plan_id = await make_subscription_plan(db, vehicle_types=[rig["hatch"]], included_service_ids=[rig["svc"]["star-wash"]], total_service_count=2)
    other = await UserSubscriptionService(db).assign(AssignSubscriptionRequest(
        customer_id=rig["customer"], plan_id=plan_id, vehicle_id=rig["car_a"], service_id=rig["svc"]["star-wash"],
    ), actor_center_id=rig["center"])
    result = await service.activate_from_payment(order)
    assert not result["ok"] and result["activated"] == 1 and len(result["skipped"]) == 1
    view = await service.view(await service.get(cart["id"]))
    assert view["status"] == "needs_review"
    car_a = next(c for c in view["cars"] if c["vehicle_id"] == rig["car_a"])
    assert car_a["status"] == "skipped" and view["review"]["refund_due"] == car_a["amount"]
    assert await db.user_subscriptions.count_documents({"custom_plan_id": cart["id"]}) == 1
    assert await db.user_subscriptions.count_documents({"vehicle_id": rig["car_a"], "status": "active"}) == 1
    assert (await db.user_subscriptions.find_one({"vehicle_id": rig["car_a"], "status": "active"}))["_id"] == ObjectId(other["id"])
    # The manager who built it hears about it (in-app).
    assert await db.notifications.count_documents({"user_id": rig["manager"], "title": "Custom plan needs review"}) == 1
    # Replays stay parked and create nothing.
    assert not (await service.activate_from_payment(order))["ok"]
    assert await db.user_subscriptions.count_documents({"custom_plan_id": cart["id"]}) == 1


async def test_cash_activates_records_one_row_and_refuses_a_car_on_a_live_pass(db, rig, gateway):
    service = CustomPlanService(db)
    cart = await _cart(db, rig, discount=50)
    link = await _link_order(db, rig, cart)
    # A car that picked up a pass meanwhile: cash is refused BEFORE money is recorded.
    plan_id = await make_subscription_plan(db, vehicle_types=[rig["hatch"]], included_service_ids=[rig["svc"]["star-wash"]], total_service_count=2)
    other = await UserSubscriptionService(db).assign(AssignSubscriptionRequest(
        customer_id=rig["customer"], plan_id=plan_id, vehicle_id=rig["car_a"], service_id=rig["svc"]["star-wash"],
    ), actor_center_id=rig["center"])
    with pytest.raises(BadRequestException):
        await service.mark_cash_paid(cart["id"], expected_revision=1, note=None, **_mgr(rig))
    assert await db.payment_orders.count_documents({"custom_plan_id": cart["id"], "kind": "cash"}) == 0
    await db.user_subscriptions.update_one({"_id": ObjectId(other["id"])}, {"$set": {"status": "cancelled"}})
    await db.pass_claims.delete_many({"subscription_id": other["id"]})

    with pytest.raises(ConflictException):  # stale revision
        await service.mark_cash_paid(cart["id"], expected_revision=2, note=None, **_mgr(rig))
    result = await service.mark_cash_paid(cart["id"], expected_revision=1, note="Paid at the door", **_mgr(rig))
    assert result["activated"] == 2 and not result["skipped"] and result["custom_plan"]["status"] == "active"
    rows = await db.payment_orders.find({"custom_plan_id": cart["id"], "kind": "cash"}).to_list(length=5)
    assert len(rows) == 1 and rows[0]["purpose"] == "custom_plan" and rows[0]["amount_paise"] == round(cart["total_amount"] * 100)
    assert rows[0]["service_center_id"] == rig["center"] and rows[0]["status"] == "paid"
    subs = await db.user_subscriptions.find({"custom_plan_id": cart["id"]}).to_list(length=10)
    assert {s["payment_method"] for s in subs} == {"cash"} and round(sum(s["amount_paid"] for s in subs), 2) == cart["total_amount"]
    # The link for this cart can no longer take money.
    assert link["razorpay_link_id"] in gateway.payment_link.cancelled
    assert not await service.still_payable(link)
    # Paid: can't be revised, cancelled or paid again.
    for call in (
        service.cancel(cart["id"], reason=None, **_mgr(rig)),
        service.mark_cash_paid(cart["id"], expected_revision=1, note=None, **_mgr(rig)),
        service.revise(cart["id"], CustomPlanReviseRequest(expected_revision=1, cars=_cars(rig)), **_mgr(rig)),
    ):
        with pytest.raises(BadRequestException):
            await call


async def test_cash_and_online_racing_activate_once(db, rig, gateway):
    service = CustomPlanService(db)
    cart = await _cart(db, rig)
    order = await _paid(db, await _link_order(db, rig, cart))

    async def cash():
        try:
            return (await CustomPlanService(db).mark_cash_paid(cart["id"], expected_revision=1, note=None, **_mgr(rig)))["activated"] > 0
        except AppException:
            return False

    async def online():
        return (await CustomPlanService(db).activate_from_payment(order))["ok"]

    outcomes = await asyncio.gather(cash(), online())
    assert outcomes.count(True) == 1, outcomes
    assert await db.user_subscriptions.count_documents({"custom_plan_id": cart["id"]}) == 2
    final = await service.get(cart["id"])
    assert final["status"] == "active"
    if outcomes[0]:
        assert final["payment"]["method"] == "cash"
    else:
        assert final["payment"]["order_id"] == str(order["_id"])
        assert await db.payment_orders.count_documents({"custom_plan_id": cart["id"], "kind": "cash"}) == 0


async def test_cancel_before_payment(db, rig, gateway):
    service = CustomPlanService(db)
    cart = await _cart(db, rig)
    link = await _link_order(db, rig, cart)
    cancelled = await service.cancel(cart["id"], reason="Customer changed mind", **_mgr(rig))
    assert cancelled["status"] == "cancelled" and link["razorpay_link_id"] in gateway.payment_link.cancelled
    assert not await service.still_payable(link)
    assert not (await service.activate_from_payment(await _paid(db, link)))["ok"]
    assert await db.user_subscriptions.count_documents({"custom_plan_id": cart["id"]}) == 0
    for call in (
        service.send_link(cart["id"], expected_revision=1, send_whatsapp=False, **_mgr(rig)),
        service.mark_cash_paid(cart["id"], expected_revision=1, note=None, **_mgr(rig)),
        service.cancel(cart["id"], reason=None, **_mgr(rig)),
    ):
        with pytest.raises(BadRequestException):
            await call


async def test_access_rules_over_http(db, rig, gateway):
    cart = await _cart(db, rig)
    cid = cart["id"]
    other = auth(rig["other_manager"], "manager", rig["other"])
    async with client() as c:
        for method, path, body in (
            ("get", f"/api/v1/subscriptions/custom-plans/{cid}", None),
            ("put", f"/api/v1/subscriptions/custom-plans/{cid}", {"expected_revision": 1, "cars": _cars(rig)}),
            ("post", f"/api/v1/subscriptions/custom-plans/{cid}/link", {"expected_revision": 1}),
            ("post", f"/api/v1/subscriptions/custom-plans/{cid}/cash", {"expected_revision": 1}),
            ("post", f"/api/v1/subscriptions/custom-plans/{cid}/cancel", {}),
        ):
            r = await c.request(method.upper(), path, json=body, headers=other)
            assert r.status_code == 403, (path, r.text)
            r = await c.request(method.upper(), path, json=body, headers=auth(rig["customer"], "customer"))
            assert r.status_code == 403, (path, r.text)
        # Another center's manager can't reach this customer by id either.
        r = await c.post("/api/v1/subscriptions/custom-plans", json={"customer_id": rig["customer"], "cars": _cars(rig)}, headers=other)
        assert r.status_code == 404, r.text
        # The other manager's list never shows it; the own manager's does.
        r = await c.get("/api/v1/subscriptions/custom-plans", headers=other)
        assert cid not in {x["id"] for x in r.json()["data"]}
        r = await c.get(f"/api/v1/subscriptions/custom-plans?service_center_id={rig['center']}", headers=other)
        assert cid not in {x["id"] for x in r.json()["data"]}
        r = await c.get("/api/v1/subscriptions/custom-plans", headers=auth(rig["manager"], "manager", rig["center"]))
        assert cid in {x["id"] for x in r.json()["data"]}
        # A client total is never read: the server price stands.
        r = await c.post("/api/v1/subscriptions/custom-plans/preview", json={"cars": _cars(rig)[1:], "total_amount": 1},
                         headers=auth(rig["manager"], "manager", rig["center"]))
        assert r.status_code == 200 and r.json()["data"]["total_amount"] > 1
        # Paid by cash through the route -> audited; the customer sees it.
        r = await c.post(f"/api/v1/subscriptions/custom-plans/{cid}/cash", json={"expected_revision": 1},
                         headers=auth(rig["manager"], "manager", rig["center"]))
        assert r.status_code == 200, r.text
        assert await db.audit_logs.find_one({"action": "CUSTOM_PLAN_CASH", "target_id": cid})
        r = await c.get("/api/v1/subscriptions/custom-plans/my", headers=auth(rig["customer"], "customer"))
        mine = r.json()["data"]
        assert [m["id"] for m in mine] == [cid] and mine[0]["cars"][0]["subscription"]["remaining"] == 4
        assert "review" not in mine[0] and "created_by" not in mine[0]
        stranger = await make_customer(db)
        r = await c.get("/api/v1/subscriptions/custom-plans/my", headers=auth(stranger, "customer"))
        assert r.json()["data"] == []
        await db.users.delete_one({"_id": ObjectId(stranger)})


# ---------------------------------------------------------------- quotas


async def test_quotas_per_service_add_ons_and_unlisted_services_paid(db, rig, gateway):
    _view, subs = await _activated(db, rig, deep_a=1)
    sub = _pass_for(subs, rig["car_a"])
    sid = str(sub["_id"])
    docs = await db.services.find({"_id": {"$in": [ObjectId(rig["svc"][k]) for k in ("star-wash", "deep-cleaning", "waterless-service", "exterior-polish")]}}).to_list(length=4)
    by_slug = {s["slug"]: s for s in docs}
    star, deep, waterless, polish = by_slug["star-wash"], by_slug["deep-cleaning"], by_slug["waterless-service"], by_slug["exterior-polish"]
    subs_svc = UserSubscriptionService(db)
    cid = rig["customer"]

    plan = await subs_svc.plan_consumption(sid, rig["car_a"], [star, polish], cid)
    assert plan == {"by_service": {str(star["_id"]): 1}, "covered_service_ids": [str(star["_id"])]}
    # A main service the pass has no quota for rides along PAID.
    plan = await subs_svc.plan_consumption(sid, rig["car_a"], [star, waterless], cid)
    assert plan["covered_service_ids"] == [str(star["_id"])]
    two = await subs_svc.plan_consumption(sid, rig["car_a"], [star, deep], cid)
    assert two["by_service"] == {str(star["_id"]): 1, str(deep["_id"]): 1}
    # Nothing on the booking is covered -> not a plan booking.
    with pytest.raises(BadRequestException):
        await subs_svc.plan_consumption(sid, rig["car_a"], [waterless, polish], cid)
    # Only its own car: never another car, never a type-only booking.
    car_b = await db.vehicles.find_one({"owner_id": cid, "registration_number": rig["plate_b"]})
    for vid in (str(car_b["_id"]), None):
        with pytest.raises(BadRequestException):
            await subs_svc.plan_consumption(sid, vid, [star], cid, vehicle_type=rig["hatch"])
    # Somebody else's customer id -> 404.
    with pytest.raises(NotFoundException):
        await subs_svc.plan_consumption(sid, rig["car_a"], [star], rig["admin"])

    await subs_svc.commit_consumption(sid, two)
    raw = await db.user_subscriptions.find_one({"_id": sub["_id"]})
    assert raw["remaining_by_service"] == {str(star["_id"]): 1, str(deep["_id"]): 0} and raw["remaining_service_count"] == 1
    with pytest.raises(BadRequestException):  # the deep clean is used up
        await subs_svc.plan_consumption(sid, rig["car_a"], [deep], cid)
    # Cancel gives it back, never past what was sold.
    await subs_svc.restore_consumption(sid, {"by_service": {str(deep["_id"]): 1}})
    await subs_svc.restore_consumption(sid, {"by_service": {str(deep["_id"]): 1}})
    raw = await db.user_subscriptions.find_one({"_id": sub["_id"]})
    assert raw["remaining_by_service"][str(deep["_id"])] == 1 and raw["remaining_service_count"] == 2
    # Spending the very last wash expires it; a cancel brings it back.
    await subs_svc.commit_consumption(sid, {"by_service": {str(deep["_id"]): 1, str(star["_id"]): 1}})
    raw = await db.user_subscriptions.find_one({"_id": sub["_id"]})
    assert raw["remaining_service_count"] == 0 and raw["status"] == "expired"
    await subs_svc.restore_consumption(sid, {"by_service": {str(star["_id"]): 1}})
    assert (await db.user_subscriptions.find_one({"_id": sub["_id"]}))["status"] == "active"


async def test_two_bookings_racing_for_the_last_deep_cleaning(db, rig, gateway):
    _view, subs = await _activated(db, rig, deep_a=1)
    sub = _pass_for(subs, rig["car_a"])
    sid = str(sub["_id"])
    deep = await db.services.find_one({"_id": ObjectId(rig["svc"]["deep-cleaning"])})
    subs_svc = UserSubscriptionService(db)
    # Both bookings price it while one is still left …
    plans = [await subs_svc.plan_consumption(sid, rig["car_a"], [deep], rig["customer"]) for _ in range(2)]

    async def commit(plan):
        try:
            await UserSubscriptionService(db).commit_consumption(sid, plan)
            return True
        except BadRequestException:
            return False

    # … only one can spend it.
    outcomes = await asyncio.gather(*[commit(p) for p in plans])
    assert outcomes.count(True) == 1, outcomes
    raw = await db.user_subscriptions.find_one({"_id": sub["_id"]})
    assert raw["remaining_by_service"][str(deep["_id"])] == 0
    assert raw["remaining_service_count"] == raw["total_service_count"] - 1


# ---------------------------------------------------------------- views / reports


async def test_customer_and_staff_views(db, rig, gateway):
    view, subs = await _activated(db, rig)
    mine = await UserSubscriptionService(db).list_my_subscriptions(rig["customer"])
    custom = [s for s in mine if s.get("custom_plan_id") == view["id"]]
    assert len(custom) == 2
    row = next(s for s in custom if s["vehicle_id"] == rig["car_a"])
    assert row["plan_name"] == "Custom plan" and row["registration_number"]
    assert {r["service_name"] for r in row["services"]} == {"Star Wash", "Deep Cleaning"}
    assert all(r["remaining"] == r["total"] == 2 for r in row["services"])
    assert "can_extend" not in row and "extensions" not in row
    # Staff picker: plan_name now present (it was missing), per-service rows too.
    staff = await UserSubscriptionService(db).list_for_customer(rig["customer"], "manager", rig["center"])
    assert all(s.get("plan_name") for s in staff) and any(s.get("services") for s in staff)
    async with client() as c:
        r = await c.get(f"/api/v1/subscriptions/customer/{rig['customer']}", headers=auth(rig["admin"], "admin"))
        assert r.status_code == 200 and all(s["plan_name"] for s in r.json()["data"])
    # Overview rows carry the car and the per-service quotas.
    ov = await UserSubscriptionService(db).center_overview(rig["center"], "manager", rig["center"])
    rows = [r for r in ov["rows"] if r.get("custom_plan_id") == view["id"]]
    assert len(rows) == 2 and all(r["registration_number"] and r["remaining_by_service"] and r["vehicle_id"] for r in rows)
    adm = await UserSubscriptionService(db).admin_overview(search=None, page_size=200)
    shares = [r["amount_paid"] for r in adm["rows"] if r.get("custom_plan_id") == view["id"]]
    assert round(sum(shares), 2) == view["total_amount"] and len(shares) == 2


async def test_hidden_template_is_never_public_or_customer_cancellable(db, rig, gateway):
    _view, subs = await _activated(db, rig)
    plans = await SubscriptionPlanService(db).list_all(active_only=False)
    assert TEMPLATE_PLAN_ID not in {p["id"] for p in plans}
    with pytest.raises(NotFoundException):
        await SubscriptionPlanService(db).get(TEMPLATE_PLAN_ID)
    sid = str(subs[0]["_id"])
    with pytest.raises(BadRequestException):
        await UserSubscriptionService(db).cancel(rig["customer"], sid)
    with pytest.raises(BadRequestException):
        await UserSubscriptionService(db).upgrade(rig["customer"], sid, await make_subscription_plan(db, vehicle_types=[]))


async def test_plan_revenue_counts_the_cart_once(db, rig, gateway):
    view, _subs = await _activated(db, rig, discount=100)
    s, e = now_ist() - timedelta(hours=1), now_ist() + timedelta(hours=1)
    subs_svc = UserSubscriptionService(db)
    revenue, sold = await subs_svc.center_plan_revenue(rig["center"], s, e)
    assert sold == 1 and revenue == view["total_amount"]
    rows, total = await subs_svc.plan_purchases(s, e, 1, 50, rig["center"])
    assert total == 1 and rows[0]["plan_name"] == "Custom plan" and rows[0]["car_count"] == 2 and rows[0]["custom_plan_id"] == view["id"]
    assert rows[0]["amount"] == view["total_amount"]
    platform = await KpiService(db)._plan_revenue(s, e)
    assert platform >= view["total_amount"]
    from app.services.manager_dashboard_service import ManagerDashboardService

    sales = await ManagerDashboardService(db)._plan_sales(rig["center"], s, e)
    item = next(i for i in sales["items"] if i["plan_id"] == TEMPLATE_PLAN_ID)
    assert item["name"] == "Custom plan" and item["sold"] == 1
