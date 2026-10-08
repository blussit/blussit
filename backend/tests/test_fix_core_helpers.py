"""Shared helpers for the test_fix_core_* regression suites (pre-production
audit 2026-10-07, core fix round 1). Lifted from the audit's reproduction
helpers so every regression test drives the same real paths the audit did:
the ASGI app over HTTP, the real services, the local replica-set Mongo.
No tests live here."""
import hashlib
import hmac
import itertools
import random
from datetime import datetime, timedelta
from types import SimpleNamespace

from bson import ObjectId
from httpx import ASGITransport, AsyncClient

from app.utils.timezone import now_ist
from tests.factories import (
    get_hatchback_type_id,
    get_star_wash_service_id,
    make_captain,
    make_customer_with_vehicle,
    make_manager,
    make_service_center,
)

_PIN = itertools.count(random.randint(1000, 60000))
_SEQ = itertools.count(1)
RZP_SECRET = "stub_secret_key"


def auth(user_id: str, role: str, center_id: str | None = None) -> dict:
    from app.core.security import create_access_token

    return {"Authorization": f"Bearer {create_access_token(user_id, role, {'service_center_id': center_id, 'tv': 0})}"}


def client() -> AsyncClient:
    from app.main import app

    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test", timeout=120)


async def center(db, **kw) -> tuple[str, str]:
    """A center on its own pincode. These suites don't clean their centers
    up, so it stands ~22 km north of the factory default (22.7/75.8) where
    other suites drop map pins — a leftover here can't win their
    nearest-center lookup. Customers here are matched by pincode."""
    pin = f"4{next(_PIN):05d}"
    kw.setdefault("latitude", 22.9)
    kw.setdefault("longitude", 75.8)
    cid = await make_service_center(db, pincode=pin, **kw)
    return cid, pin


async def customer(db, pin: str) -> dict:
    hatch = await get_hatchback_type_id(db)
    cid, vid, aid = await make_customer_with_vehicle(db, hatch, pincode=pin)
    return {"id": cid, "vehicle_id": vid, "address_id": aid, "h": auth(cid, "customer")}


async def admin(db) -> dict:
    a = await db.users.find_one({"role": "admin"})
    return {"id": str(a["_id"]), "h": auth(str(a["_id"]), "admin")}


async def manager(db, center_id: str) -> dict:
    mid = await make_manager(db, center_id)
    return {"id": mid, "h": auth(mid, "manager", center_id)}


async def captain(db, center_id: str, wallet: float = 200.0) -> dict:
    cid = await make_captain(db, center_id, wallet_balance=wallet)
    return {"id": cid, "h": auth(cid, "captain", center_id)}


def day(offset: int) -> str:
    return (now_ist().date() + timedelta(days=offset)).isoformat()


async def slot_keys(db, center_id: str, offset: int = 2) -> tuple[str, list[str]]:
    from app.services.booking_service import BookingService

    when = day(offset)
    slots = await BookingService(db).available_slots(center_id, when)
    return when, [s["key"] for s in slots]


async def set_cap(db, center_id: str, when: str, slot: str, cap: int) -> None:
    await db.slot_capacity.update_one(
        {"service_center_id": center_id, "date": when, "slot_key": slot},
        {"$set": {"capacity": cap, "booked_count": 0, "held_count": 0, "is_closed": False, "is_deleted": False}},
        upsert=True,
    )


async def seat(db, center_id: str, when: str, slot: str) -> dict:
    return await db.slot_capacity.find_one({"service_center_id": center_id, "date": when, "slot_key": slot}) or {}


async def live(db, center_id: str, when: str, slot: str) -> int:
    """Live (seat-holding) bookings on that day/slot, ONE per visit — a
    multi-car visit holds a single seat."""
    d = datetime.strptime(when, "%Y-%m-%d")
    rows = await db.bookings.find({
        "service_center_id": center_id, "scheduled_slot": slot,
        "scheduled_date": {"$gte": d - timedelta(hours=6), "$lt": d + timedelta(days=1)},
        "status": {"$nin": ["cancelled"]}, "is_deleted": {"$ne": True},
    }).to_list(length=None)
    visits = {r.get("booking_group_id") or str(r["_id"]) for r in rows}
    return len(visits)


async def owners(db, center_id: str, when: str, slot: str) -> int:
    """Bookings that RECORD holding that seat (the ownership flag)."""
    return await db.bookings.count_documents({
        "holds_seat": True, "seat_key.service_center_id": center_id, "seat_key.date": when, "seat_key.slot_key": slot,
    })


async def state(db, center_id: str, when: str, slot: str) -> dict:
    d = await seat(db, center_id, when, slot)
    return {
        "booked": d.get("booked_count"), "cap": d.get("capacity"),
        "live": await live(db, center_id, when, slot), "owners": await owners(db, center_id, when, slot),
    }


async def body(db, cust: dict, when: str, slot: str, **extra) -> dict:
    hatch = await get_hatchback_type_id(db)
    svc = await get_star_wash_service_id(db)
    b = {"vehicle_type": hatch, "address_id": cust["address_id"], "service_ids": [svc],
         "scheduled_date": when, "scheduled_slot": slot, "payment_method": "cash"}
    b.update(extra)
    return {k: v for k, v in b.items() if v is not None}


async def book(c: AsyncClient, db, cust: dict, when: str, slot: str, **extra):
    return await c.post("/api/v1/bookings", json=await body(db, cust, when, slot, **extra), headers=cust["h"])


async def group(c: AsyncClient, db, cust: dict, when: str, slot: str, cars: int = 2, **extra):
    hatch = await get_hatchback_type_id(db)
    svc = await get_star_wash_service_id(db)
    b = {"vehicles": [{"vehicle_type": hatch, "quantity": cars, "service_ids": [svc]}], "address_id": cust["address_id"],
         "scheduled_date": when, "scheduled_slot": slot, "payment_method": "cash"}
    b.update(extra)
    r = await c.post("/api/v1/bookings/group", json=b, headers=cust["h"])
    assert r.status_code == 200, r.text
    d = r.json()["data"]
    return d["booking_group_id"], [x["id"] for x in d["bookings"]]


def oid(x: str) -> ObjectId:
    return ObjectId(x)


# -- Razorpay stub (never a real call) ------------------------------------


def rzp_sig(order_id: str, payment_id: str) -> str:
    return hmac.new(RZP_SECRET.encode(), f"{order_id}|{payment_id}".encode(), hashlib.sha256).hexdigest()


class _Orders:
    def create(self, payload):
        return {"id": f"order_fc_{next(_SEQ):06d}", **payload}

    def payments(self, order_id):
        return {"items": []}


class _Links:
    def __init__(self):
        self.cancelled: list[str] = []

    def create(self, payload):
        return {"id": f"plink_fc_{next(_SEQ):06d}", "short_url": "https://rzp.io/l/fc", **payload}

    def fetch(self, link_id):
        return {"id": link_id, "status": "created", "payments": []}

    def cancel(self, link_id):
        self.cancelled.append(link_id)
        return {"id": link_id, "status": "cancelled"}


class _Payments:
    """Verify asks the gateway for the payment (PAY-09) — a test registers
    the captured payment first (register_captured)."""

    def __init__(self):
        self.store: dict[str, dict] = {}

    def fetch(self, payment_id):
        if payment_id not in self.store:
            raise RuntimeError(f"The id provided does not exist: {payment_id}")
        return dict(self.store[payment_id])

    def capture(self, payment_id, amount, data):
        self.store[payment_id]["status"] = "captured"
        return dict(self.store[payment_id])


_CURRENT: dict = {}


def install_rzp_stub(monkeypatch):
    from app.services import payment_service

    stub = SimpleNamespace(order=_Orders(), payment_link=_Links(), payment=_Payments())
    monkeypatch.setattr(payment_service.settings, "RAZORPAY_KEY_ID", "rzp_test_stub")
    monkeypatch.setattr(payment_service.settings, "RAZORPAY_KEY_SECRET", RZP_SECRET)
    monkeypatch.setattr(payment_service, "_razorpay_client", lambda: stub)
    _CURRENT["stub"] = stub
    return stub


async def pay_order(db, customer_id: str, order_id: str, payment_id: str):
    """The real verify path for a captured gateway payment of that order."""
    from app.schemas.payment_schema import VerifyPaymentRequest
    from app.services.payment_service import PaymentService

    order = await db.payment_orders.find_one({"razorpay_order_id": order_id})
    _CURRENT["stub"].payment.store[payment_id] = {
        "id": payment_id, "order_id": order_id, "amount": order["amount_paise"], "currency": "INR", "status": "captured",
    }
    return await PaymentService(db).verify_payment(customer_id, VerifyPaymentRequest(
        razorpay_order_id=order_id, razorpay_payment_id=payment_id, razorpay_signature=rzp_sig(order_id, payment_id),
    ))
