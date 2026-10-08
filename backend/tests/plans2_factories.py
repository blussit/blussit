"""Shared rig for the PLANS-2 tests (custom-plan renewal + one-car refund).
Razorpay is stubbed; local Mongo only."""
import itertools
from datetime import datetime, timedelta, timezone

import pytest
from bson import ObjectId

from app.schemas.custom_plan_schema import CustomPlanCreateRequest
from app.services import payment_service
from app.services.custom_plan_service import CustomPlanService
from app.utils.timezone import now_ist

from tests.factories import get_hatchback_type_id, get_suv_type_id, make_customer, make_manager, make_service_center, make_vehicle
from tests.society_factories import make_admin

_seq = itertools.count(1)


class _StubLinks:
    def __init__(self):
        self.cancelled: list[str] = []
        self.created: list[dict] = []

    def create(self, payload):
        n = next(_seq)
        self.created.append(payload)
        return {"id": f"plink_p2_{n:06d}", "short_url": f"https://rzp.io/l/p2{n}", **payload}

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
    for name in ("user_subscriptions", "payment_orders", "custom_plans", "bookings", "notifications", "audit_logs",
                 "customer_wallet_ledger", "customer_wallets"):
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
    car_a = await make_vehicle(db, customer, hatch, registration_number=f"MP09PA{next(_seq):04d}")
    svc = {s["slug"]: str(s["_id"]) for s in await db.services.find({"slug": {"$in": ["star-wash", "deep-cleaning"]}}).to_list(length=10)}
    for uid in (manager, other_manager, customer):
        cleanup.append(("users", {"_id": ObjectId(uid)}))
    cleanup.append(("vehicles", {"owner_id": customer}))
    cleanup.append(("service_centers", {"_id": {"$in": [ObjectId(center), ObjectId(other)]}}))
    # A manager reaches a customer known to their center.
    await db.bookings.insert_one({"customer_id": customer, "service_center_id": center, "status": "completed", "is_deleted": False,
                                  "booking_number": f"BKP2{next(_seq):05d}", "created_at": datetime.now(timezone.utc)})
    cleanup.append(("bookings", {"customer_id": customer}))
    return {
        "center": center, "other": other, "manager": manager, "other_manager": other_manager, "admin": admin,
        "customer": customer, "hatch": hatch, "suv": suv, "car_a": car_a, "svc": svc,
        "plate_b": f"MP09PB{next(_seq):04d}", "plate_c": f"MP09PC{next(_seq):04d}",
    }


def cars(rig, *, star_a: int = 2, deep_a: int = 2):
    return [
        {"vehicle_id": rig["car_a"], "items": [{"service_id": rig["svc"]["star-wash"], "count": star_a}, {"service_id": rig["svc"]["deep-cleaning"], "count": deep_a}]},
        {"registration_number": rig["plate_b"], "vehicle_type": rig["suv"], "items": [{"service_id": rig["svc"]["star-wash"], "count": 2}]},
    ]


def mgr(rig) -> dict:
    return {"actor_id": rig["manager"], "actor_role": "manager", "actor_center_id": rig["center"]}


async def make_cart(db, rig, *, discount: int = 0, car_list=None) -> dict:
    payload = CustomPlanCreateRequest(customer_id=rig["customer"], cars=car_list or cars(rig), discount_amount=discount)
    return await CustomPlanService(db).create(payload, **mgr(rig))


async def link_order(db, rig, cart: dict) -> dict:
    sent = await CustomPlanService(db).send_link(cart["id"], expected_revision=cart["revision"], send_whatsapp=True, **mgr(rig))
    return await db.payment_orders.find_one({"_id": ObjectId(sent["order_id"])})


async def paid(db, order: dict) -> dict:
    """What PaymentService's guarded claim leaves on a paid link order."""
    await db.payment_orders.update_one({"_id": order["_id"]}, {"$set": {"status": "paid", "paid_at": now_ist(), "razorpay_payment_id": "pay_p2"}})
    return await db.payment_orders.find_one({"_id": order["_id"]})


async def activated_cash(db, rig, *, discount: int = 0, car_list=None) -> dict:
    """A cart paid in cash — its passes are live. Returns the fresh view."""
    cart = await make_cart(db, rig, discount=discount, car_list=car_list)
    service = CustomPlanService(db)
    await service.mark_cash_paid(cart["id"], expected_revision=1, note=None, **mgr(rig))
    return await service.view(await service.get(cart["id"]))


async def pass_of(db, cart_id: str, vehicle_id: str) -> dict:
    return await db.user_subscriptions.find_one({"custom_plan_id": cart_id, "vehicle_id": vehicle_id})


async def vehicle_id_of(db, rig, plate: str) -> str:
    v = await db.vehicles.find_one({"owner_id": rig["customer"], "registration_number": plate})
    return str(v["_id"])
