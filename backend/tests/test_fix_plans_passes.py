"""Remediation pass 2026-10-07 (PLANS) — monthly-pass regressions:
PASS-1/PASS-4 (one live pass under concurrency, insert-first claim),
PAY-01 (expected_vehicle_type / amount_paid keywords), PASS-2 (upgrade
never refills a category quota, guarded write), PASS-3 (a pass covers only
bookings dated inside its own period), PASS-6 (a manager never sees another
center's plan-sale money). Local Mongo only; Razorpay stubbed."""
import asyncio
import itertools
from datetime import datetime, timedelta, timezone

import pytest
from bson import ObjectId

from app.core.exceptions import AppException, BadRequestException
from app.schemas.booking_schema import BookingCreateRequest
from app.schemas.payment_schema import CreateOrderRequest
from app.schemas.subscription_schema import AssignSubscriptionRequest, ManagerSubscriptionOfferRequest, SubscribeRequest
from app.services import payment_service
from app.services.booking_service import BookingService
from app.services.payment_service import PaymentService
from app.services import subscription_service as subscription_module
from app.services.subscription_service import UserSubscriptionService
from app.utils.timezone import now_ist

from tests.factories import (
    get_hatchback_type_id, get_star_wash_service_id, get_suv_type_id, make_customer, make_customer_with_vehicle,
    make_manager, make_service_center, make_subscription_plan,
)

pytestmark = pytest.mark.asyncio
_seq = itertools.count(1)


@pytest.fixture(autouse=True)
async def _tidy(db):
    """Everything these tests create in the money/pass collections goes
    again at teardown (other suites assert those collections are empty)."""
    since = datetime.now(timezone.utc) - timedelta(seconds=1)
    first = ObjectId.from_datetime(since)
    yield
    for name in ("user_subscriptions", "payment_orders", "bookings", "society_payments", "coupon_usages"):
        await db[name].delete_many({"_id": {"$gte": first}})
    await db.payment_orders.delete_many({"created_at": {"$gte": since}})
    await db.pass_claims.delete_many({"created_at": {"$gte": since}})


class _Orders:
    def create(self, payload):
        return {"id": f"order_fixplans_{next(_seq):06d}", **payload}


class _Client:
    def __init__(self):
        self.order = _Orders()


@pytest.fixture
def gateway(monkeypatch):
    monkeypatch.setattr(payment_service.settings, "RAZORPAY_KEY_ID", "rzp_test_stub")
    monkeypatch.setattr(payment_service.settings, "RAZORPAY_KEY_SECRET", "stub_secret_key")
    client = _Client()
    monkeypatch.setattr(payment_service, "_razorpay_client", lambda: client)
    return client


@pytest.fixture
async def rig(db, gateway, cleanup):
    hatchback = await get_hatchback_type_id(db)
    star = await get_star_wash_service_id(db)
    center_id = await make_service_center(db)
    # On the factory default pincode/pin: left behind, it would be picked
    # for later suites' default-pincode customers.
    cleanup.append(("service_centers", {"_id": ObjectId(center_id)}))
    manager_id = await make_manager(db, center_id)
    plan_id = await make_subscription_plan(db, vehicle_types=[hatchback], included_service_ids=[star], total_service_count=2)
    return {"db": db, "hatchback": hatchback, "star": star, "center_id": center_id, "manager_id": manager_id, "plan_id": plan_id}


async def _live_passes(db, customer_id: str) -> int:
    return await db.user_subscriptions.count_documents({"customer_id": customer_id, "status": "active", "is_deleted": {"$ne": True}})


# ---------------------------------------------------------------------------
# PASS-1 / PASS-4 — insert-first claim
# ---------------------------------------------------------------------------


async def test_pass1_simultaneous_cash_sales_create_one_pass(rig):
    db = rig["db"]
    customer_id = await make_customer(db)
    phone = (await db.users.find_one({"_id": ObjectId(customer_id)}))["phone"]
    payload = ManagerSubscriptionOfferRequest(
        customer_name="Cash Buyer", customer_phone=phone, plan_id=rig["plan_id"], vehicle_type=rig["hatchback"],
        service_id=rig["star"], recurring=False, payment_method="cash",
    )

    async def sell():
        try:
            await PaymentService(db).manager_subscription_offer(rig["manager_id"], payload, actor_center_id=rig["center_id"], actor_role="manager")
            return "ok"
        except AppException as exc:
            return exc.message

    results = await asyncio.gather(*[sell() for _ in range(4)])
    assert results.count("ok") == 1, results
    assert await _live_passes(db, customer_id) == 1
    cash_rows = await db.payment_orders.count_documents({"customer_id": customer_id, "kind": "cash", "status": "paid"})
    assert cash_rows == 1


async def test_pass1_simultaneous_assigns_create_one_pass(rig):
    db = rig["db"]
    customer_id = await make_customer(db)

    async def grant():
        try:
            await UserSubscriptionService(db).assign(
                AssignSubscriptionRequest(customer_id=customer_id, plan_id=rig["plan_id"], vehicle_type=rig["hatchback"], service_id=rig["star"]),
                actor_center_id=rig["center_id"],
            )
            return "ok"
        except BadRequestException:
            return "refused"

    results = await asyncio.gather(*[grant() for _ in range(6)])
    assert results.count("ok") == 1, results
    assert await _live_passes(db, customer_id) == 1


async def test_pass4_two_paid_orders_for_one_pass_make_one_pass(rig):
    db = rig["db"]
    customer_id = await make_customer(db)
    svc = PaymentService(db)
    req = CreateOrderRequest(purpose="subscription", plan_id=rig["plan_id"], vehicle_type=rig["hatchback"], service_id=rig["star"])
    o1 = await svc.create_order(customer_id, req)
    o2 = await svc.create_order(customer_id, req)
    d1 = await db.payment_orders.find_one({"razorpay_order_id": o1["order_id"]})
    d2 = await db.payment_orders.find_one({"razorpay_order_id": o2["order_id"]})
    outcome = await asyncio.gather(svc._apply_order_paid(d1, "pay_fix_a", via="verify"), PaymentService(db)._apply_order_paid(d2, "pay_fix_b", via="webhook"))
    assert await _live_passes(db, customer_id) == 1
    assert sorted(o["status"] for o in outcome) == ["needs_attention", "paid"]


async def test_vehicle_bound_passes_claim_the_car(rig):
    """Two simultaneous purchases naming the same saved car: one pass."""
    db = rig["db"]
    customer_id, vehicle_id, _ = await make_customer_with_vehicle(db, rig["hatchback"])
    subs = UserSubscriptionService(db)

    async def buy():
        try:
            await UserSubscriptionService(db).subscribe(customer_id, SubscribeRequest(plan_id=rig["plan_id"], vehicle_id=vehicle_id, service_id=rig["star"]))
            return "ok"
        except BadRequestException:
            return "refused"

    results = await asyncio.gather(*[buy() for _ in range(5)])
    assert results.count("ok") == 1, results
    assert await subs._active_pass_for_vehicle(customer_id, vehicle_id) is not None
    assert await _live_passes(db, customer_id) == 1


async def test_claim_is_released_when_the_pass_ends_or_is_cancelled(rig):
    db = rig["db"]
    customer_id = await make_customer(db)
    subs = UserSubscriptionService(db)
    req = AssignSubscriptionRequest(customer_id=customer_id, plan_id=rig["plan_id"], vehicle_type=rig["hatchback"], service_id=rig["star"])
    first = await subs.assign(req, actor_center_id=rig["center_id"])
    with pytest.raises(BadRequestException):
        await subs.assign(req, actor_center_id=rig["center_id"])
    # The pass lapses -> the same pass can be bought again.
    await db.user_subscriptions.update_one({"_id": ObjectId(first["id"])}, {"$set": {"end_date": now_ist() - timedelta(days=1)}})
    second = await subs.assign(req, actor_center_id=rig["center_id"])
    assert second["id"] != first["id"]
    # Cancelled -> buyable again too.
    await subs.cancel(customer_id, second["id"])
    third = await subs.assign(req, actor_center_id=rig["center_id"])
    assert third["id"] not in (first["id"], second["id"])
    # Used up (expired at 0) -> buyable again.
    await db.user_subscriptions.update_one({"_id": ObjectId(third["id"])}, {"$set": {"status": "expired", "remaining_service_count": 0}})
    fourth = await subs.assign(req, actor_center_id=rig["center_id"])
    assert fourth["status"] == "active"


async def test_failed_creation_releases_its_claim(rig, monkeypatch):
    db = rig["db"]
    customer_id = await make_customer(db)
    subs = UserSubscriptionService(db)
    req = AssignSubscriptionRequest(customer_id=customer_id, plan_id=rig["plan_id"], vehicle_type=rig["hatchback"], service_id=rig["star"])
    real_create = subs.repo.create

    async def boom(doc, session=None):
        raise RuntimeError("db blip")

    monkeypatch.setattr(subs.repo, "create", boom)
    with pytest.raises(RuntimeError):
        await subs.assign(req, actor_center_id=rig["center_id"])
    monkeypatch.setattr(subs.repo, "create", real_create)
    created = await subs.assign(req, actor_center_id=rig["center_id"])
    assert created["status"] == "active"


# ---------------------------------------------------------------------------
# PAY-01 / PAY-08 — keywords the payment paths pass
# ---------------------------------------------------------------------------


async def test_expected_vehicle_type_mismatch_creates_nothing(rig):
    db = rig["db"]
    suv = await get_suv_type_id(db)
    await db.subscription_plans.update_one({"_id": ObjectId(rig["plan_id"])}, {"$set": {"vehicle_types": []}})
    try:
        customer_id, vehicle_id, _ = await make_customer_with_vehicle(db, rig["hatchback"])
        subs = UserSubscriptionService(db)
        # Priced as an SUV, but the car is (now) a hatchback — or the other way round.
        with pytest.raises(subscription_module.PricedVehicleChanged):
            await subs.subscribe(
                customer_id, SubscribeRequest(plan_id=rig["plan_id"], vehicle_id=vehicle_id, service_id=rig["star"]),
                expected_vehicle_type=suv,
            )
        assert await _live_passes(db, customer_id) == 0
        assert isinstance(subscription_module.PricedVehicleChanged("x"), BadRequestException)
        # Same type -> created, and the refused attempt held no claim.
        sub = await subs.subscribe(
            customer_id, SubscribeRequest(plan_id=rig["plan_id"], vehicle_id=vehicle_id, service_id=rig["star"]),
            expected_vehicle_type=rig["hatchback"], amount_paid=444.0,
        )
        assert sub["vehicle_type"] == rig["hatchback"]
        raw = await db.user_subscriptions.find_one({"_id": ObjectId(sub["id"])})
        assert raw["amount_paid"] == 444.0
    finally:
        await db.subscription_plans.update_one({"_id": ObjectId(rig["plan_id"])}, {"$set": {"vehicle_types": [rig["hatchback"]]}})


async def test_amount_paid_keyword_on_type_pass_and_assign(rig):
    db = rig["db"]
    subs = UserSubscriptionService(db)
    c1 = await make_customer(db)
    sub = await subs.subscribe(
        c1, SubscribeRequest(plan_id=rig["plan_id"], vehicle_type=rig["hatchback"], service_id=rig["star"]),
        expected_vehicle_type=rig["hatchback"], amount_paid=399.0,
    )
    assert (await db.user_subscriptions.find_one({"_id": ObjectId(sub["id"])}))["amount_paid"] == 399.0
    c2 = await make_customer(db)
    granted = await subs.assign(
        AssignSubscriptionRequest(customer_id=c2, plan_id=rig["plan_id"], vehicle_type=rig["hatchback"], service_id=rig["star"]),
        actor_center_id=rig["center_id"], amount_paid=250.0,
    )
    assert (await db.user_subscriptions.find_one({"_id": ObjectId(granted["id"])}))["amount_paid"] == 250.0
    # Not given -> the field is not invented.
    c3 = await make_customer(db)
    plain = await subs.subscribe(c3, SubscribeRequest(plan_id=rig["plan_id"], vehicle_type=rig["hatchback"], service_id=rig["star"]))
    assert "amount_paid" not in await db.user_subscriptions.find_one({"_id": ObjectId(plain["id"])})


# ---------------------------------------------------------------------------
# PASS-2 — upgrade keeps used washes used, per category
# ---------------------------------------------------------------------------


async def _two_way_upgrade_plans(db, rig, quota: int = 2):
    star = await db.services.find_one({"_id": ObjectId(rig["star"])})
    cat = star["category_id"]
    a = await make_subscription_plan(db, vehicle_types=[rig["hatchback"]], included_service_ids=[rig["star"]], total_service_count=quota)
    b = await make_subscription_plan(db, vehicle_types=[rig["hatchback"]], included_service_ids=[rig["star"]], total_service_count=quota)
    for p, other in ((a, b), (b, a)):
        await db.subscription_plans.update_one({"_id": ObjectId(p)}, {"$set": {"category_quotas": {cat: quota}, "upgrade_to_plan_ids": [other]}})
    return star, cat, a, b


async def test_pass2_upgrade_never_refills_a_category_quota(rig):
    db = rig["db"]
    star, cat, a, b = await _two_way_upgrade_plans(db, rig)
    customer_id = await make_customer(db)
    subs = UserSubscriptionService(db)
    sub = await subs.assign(AssignSubscriptionRequest(customer_id=customer_id, plan_id=a, vehicle_type=rig["hatchback"], service_id=rig["star"]), actor_center_id=rig["center_id"])
    sid = sub["id"]
    for _ in range(2):
        c = await subs.plan_consumption(sid, None, [star], customer_id, vehicle_type=rig["hatchback"])
        await subs.commit_consumption(sid, c)
        cur = await db.user_subscriptions.find_one({"_id": ObjectId(sid)})
        if cur["status"] != "active":
            break
        target = b if cur["plan_id"] == a else a
        await subs.upgrade(customer_id, sid, target)
    cur = await db.user_subscriptions.find_one({"_id": ObjectId(sid)})
    assert cur["remaining_by_category"].get(cat, 0) == 0
    assert cur["remaining_service_count"] == 0
    with pytest.raises(BadRequestException):
        await subs.plan_consumption(sid, None, [star], customer_id, vehicle_type=rig["hatchback"])


async def test_pass2_upgrade_racing_a_booking_never_loses_the_wash(rig, monkeypatch):
    """A wash is committed between the upgrade's read and its write: the
    guarded write re-reads, so that wash stays spent."""
    db = rig["db"]
    star, cat, a, b = await _two_way_upgrade_plans(db, rig)
    customer_id = await make_customer(db)
    subs = UserSubscriptionService(db)
    sub = await subs.assign(AssignSubscriptionRequest(customer_id=customer_id, plan_id=a, vehicle_type=rig["hatchback"], service_id=rig["star"]), actor_center_id=rig["center_id"])
    sid = sub["id"]
    booking_side = UserSubscriptionService(db)
    consumption = await booking_side.plan_consumption(sid, None, [star], customer_id, vehicle_type=rig["hatchback"])
    real_find = subs.plan_repo.find_by_id
    fired = {"done": False}

    async def find_and_race(plan_id, *args, **kwargs):
        doc = await real_find(plan_id, *args, **kwargs)
        if plan_id == b and not fired["done"]:
            fired["done"] = True
            await booking_side.commit_consumption(sid, consumption)
        return doc

    monkeypatch.setattr(subs.plan_repo, "find_by_id", find_and_race)
    await subs.upgrade(customer_id, sid, b)
    assert fired["done"]
    cur = await db.user_subscriptions.find_one({"_id": ObjectId(sid)})
    assert cur["plan_id"] == b
    assert cur["remaining_by_category"][cat] == 1, cur["remaining_by_category"]
    assert cur["remaining_service_count"] == 1


async def test_pass2_upgrade_and_bookings_concurrently_stay_consistent(rig):
    db = rig["db"]
    star, cat, a, b = await _two_way_upgrade_plans(db, rig, quota=4)
    customer_id = await make_customer(db)
    subs = UserSubscriptionService(db)
    sub = await subs.assign(AssignSubscriptionRequest(customer_id=customer_id, plan_id=a, vehicle_type=rig["hatchback"], service_id=rig["star"]), actor_center_id=rig["center_id"])
    sid = sub["id"]

    async def book():
        s = UserSubscriptionService(db)
        try:
            c = await s.plan_consumption(sid, None, [star], customer_id, vehicle_type=rig["hatchback"])
            await s.commit_consumption(sid, c)
            return 1
        except BadRequestException:
            return 0

    async def flip(target):
        try:
            await UserSubscriptionService(db).upgrade(customer_id, sid, target)
        except AppException:
            pass  # e.g. already on that plan (a racing flip landed first)
        return 0

    spent = sum(await asyncio.gather(book(), flip(b), book(), flip(a), book()))
    cur = await db.user_subscriptions.find_one({"_id": ObjectId(sid)})
    assert cur["remaining_by_category"][cat] == 4 - spent
    assert cur["remaining_service_count"] == 4 - spent


async def test_pass2_upgrade_refuses_a_plan_that_doesnt_cover_the_pass(rig):
    db = rig["db"]
    suv = await get_suv_type_id(db)
    other_service = await db.services.find_one({"_id": {"$ne": ObjectId(rig["star"])}, "is_addon": {"$ne": True}, "is_deleted": {"$ne": True}})
    a = await make_subscription_plan(db, vehicle_types=[rig["hatchback"]], included_service_ids=[rig["star"]], total_service_count=2)
    wrong_menu = await make_subscription_plan(db, vehicle_types=[rig["hatchback"]], included_service_ids=[str(other_service["_id"])], total_service_count=2)
    wrong_type = await make_subscription_plan(db, vehicle_types=[suv], included_service_ids=[rig["star"]], total_service_count=2)
    await db.subscription_plans.update_one({"_id": ObjectId(a)}, {"$set": {"upgrade_to_plan_ids": [wrong_menu, wrong_type]}})
    customer_id = await make_customer(db)
    subs = UserSubscriptionService(db)
    sub = await subs.assign(AssignSubscriptionRequest(customer_id=customer_id, plan_id=a, vehicle_type=rig["hatchback"], service_id=rig["star"]), actor_center_id=rig["center_id"])
    for target in (wrong_menu, wrong_type):
        with pytest.raises(BadRequestException):
            await subs.upgrade(customer_id, sub["id"], target)
    assert (await db.user_subscriptions.find_one({"_id": ObjectId(sub["id"])}))["plan_id"] == a


# ---------------------------------------------------------------------------
# PASS-3 — a pass covers bookings dated inside its own period
# ---------------------------------------------------------------------------


async def _slot(bs: BookingService, center_id: str, when: str) -> str:
    slots = await bs.available_slots(center_id, when)
    return next(s["key"] for s in slots if s["status"] == "available")


async def test_pass3_booking_dated_after_the_pass_ends_is_refused(rig, cleanup):
    db = rig["db"]
    customer_id, vehicle_id, address_id = await make_customer_with_vehicle(db, rig["hatchback"])
    cleanup.append(("bookings", {"customer_id": customer_id}))
    plan_id = await make_subscription_plan(db, vehicle_types=[rig["hatchback"]], included_service_ids=[rig["star"]], total_service_count=4)
    subs = UserSubscriptionService(db)
    sub = await subs.assign(AssignSubscriptionRequest(customer_id=customer_id, plan_id=plan_id, vehicle_type=rig["hatchback"], service_id=rig["star"]), actor_center_id=rig["center_id"])
    sid = sub["id"]
    # Ends in 3 days: day+4 is after its end.
    await db.user_subscriptions.update_one({"_id": ObjectId(sid)}, {"$set": {"end_date": now_ist() + timedelta(days=3)}})
    bs = BookingService(db)
    late = (now_ist().date() + timedelta(days=4)).isoformat()
    with pytest.raises(BadRequestException) as exc:
        await bs.create_booking(customer_id, BookingCreateRequest(
            vehicle_type=rig["hatchback"], address_id=address_id, service_ids=[rig["star"]], scheduled_date=late,
            scheduled_slot=await _slot(bs, rig["center_id"], late), subscription_id=sid,
        ))
    assert "pass" in exc.value.message.lower()
    fresh = await db.user_subscriptions.find_one({"_id": ObjectId(sid)})
    assert fresh["remaining_service_count"] == 4
    # The planning call refuses the same date on its own.
    star = await db.services.find_one({"_id": ObjectId(rig["star"])})
    with pytest.raises(BadRequestException):
        await subs.plan_consumption(sid, None, [star], customer_id, vehicle_type=rig["hatchback"], scheduled_date=late)
    # The day the pass ends is already outside it; the day before is inside.
    end_day = (now_ist() + timedelta(days=3)).date()
    with pytest.raises(BadRequestException):
        await subs.plan_consumption(sid, None, [star], customer_id, vehicle_type=rig["hatchback"], scheduled_date=end_day.isoformat())
    inside = (end_day - timedelta(days=1)).isoformat()
    assert await subs.plan_consumption(sid, None, [star], customer_id, vehicle_type=rig["hatchback"], scheduled_date=inside)
    # And a real booking inside the period still works.
    booked = await bs.create_booking(customer_id, BookingCreateRequest(
        vehicle_type=rig["hatchback"], address_id=address_id, service_ids=[rig["star"]], scheduled_date=inside,
        scheduled_slot=await _slot(bs, rig["center_id"], inside), subscription_id=sid,
    ))
    assert booked["id"]


# ---------------------------------------------------------------------------
# PASS-6 — a manager never sees another center's plan-sale money
# ---------------------------------------------------------------------------


async def test_pass6_center_overview_hides_other_centers_sales(rig, cleanup):
    db = rig["db"]
    a = rig["center_id"]
    b = await make_service_center(db, pincode="452098")
    cleanup.append(("service_centers", {"_id": ObjectId(b)}))
    mgr_a = rig["manager_id"]
    customer_id, _vehicle_id, _address_id = await make_customer_with_vehicle(db, rig["hatchback"])
    phone = (await db.users.find_one({"_id": ObjectId(customer_id)}))["phone"]
    await PaymentService(db).manager_subscription_offer(mgr_a, ManagerSubscriptionOfferRequest(
        customer_name="Xavier", customer_phone=phone, plan_id=rig["plan_id"], vehicle_type=rig["hatchback"], service_id=rig["star"],
        payment_method="cash", discount_amount=40,
    ), actor_center_id=a, actor_role="manager")
    # The customer once booked at center B.
    await db.bookings.insert_one({"customer_id": customer_id, "service_center_id": b, "is_deleted": False, "status": "completed", "booking_number": f"FIXP{next(_seq)}"})
    subs = UserSubscriptionService(db)
    listed = await subs.list_for_customer(customer_id, "manager", b)
    ov_b = await subs.center_overview(b, "manager", b, search=phone)
    rows_b = [r for r in ov_b["rows"] if r["customer_id"] == customer_id]
    assert listed == [] and rows_b == []
    # Center A's manager still sees their own sale, with the money.
    ov_a = await subs.center_overview(a, "manager", a, search=phone)
    rows_a = [r for r in ov_a["rows"] if r["customer_id"] == customer_id]
    assert len(rows_a) == 1 and rows_a[0]["amount_paid"] is not None
    await db.bookings.delete_many({"customer_id": customer_id})
