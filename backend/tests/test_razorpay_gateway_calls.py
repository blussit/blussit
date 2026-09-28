"""
Razorpay SDK calls are blocking `requests` — they must never run on the
event loop, must be bounded by a timeout, and the reminder-loop sweeps must
reach every pending record over successive passes, however many there are.

Stubbed client only (the suite's standing rule): nothing here ever reaches
Razorpay. The one test that exercises the real SDK client uses fake keys,
an unresolvable host, and intercepts the transport before any socket opens.
"""
import asyncio
import itertools
import threading
import time
from datetime import timedelta

import pytest
import requests
from bson import ObjectId

from app.core.exceptions import BadRequestException
from app.schemas.payment_schema import CreateOrderRequest
from app.services import payment_service
from app.services.payment_service import PaymentService
from app.utils.timezone import now_ist

from tests.factories import make_customer

pytestmark = pytest.mark.asyncio

_seq = itertools.count(1)


@pytest.fixture
def gateway_keys(monkeypatch):
    monkeypatch.setattr(payment_service.settings, "RAZORPAY_KEY_ID", "rzp_test_stub")
    monkeypatch.setattr(payment_service.settings, "RAZORPAY_KEY_SECRET", "stub_secret_key")
    monkeypatch.setattr(payment_service, "_sweeps_paused_until", 0.0)


async def _seed_booking(db, customer_id) -> str:
    res = await db.bookings.insert_one({
        "booking_number": f"BK-RZPT-{next(_seq):05d}",
        "customer_id": customer_id, "service_center_id": "ctr-rzp", "status": "pending",
        "payment_method": "online", "payment_status": "pending", "total_amount": 349.0,
        "scheduled_date": now_ist().replace(tzinfo=None), "scheduled_slot": "09:00-12:00",
        "is_deleted": False, "created_at": now_ist(),
    })
    return str(res.inserted_id)


async def _customer_with_booking(db, cleanup) -> tuple[str, str]:
    customer_id = await make_customer(db)
    cleanup.append(("users", {"_id": ObjectId(customer_id)}))
    booking_id = await _seed_booking(db, customer_id)
    cleanup.append(("bookings", {"_id": ObjectId(booking_id)}))
    cleanup.append(("payment_orders", {"customer_id": customer_id}))
    return customer_id, booking_id


async def test_sdk_call_runs_off_the_event_loop(db, cleanup, gateway_keys, monkeypatch):
    customer_id, booking_id = await _customer_with_booking(db, cleanup)
    loop_thread = threading.get_ident()
    seen = {}

    class _SlowOrders:
        def create(self, payload):
            seen["thread"] = threading.get_ident()
            time.sleep(0.4)  # a slow Razorpay
            return {"id": f"order_slow_{next(_seq):06d}", **payload}

    class _Client:
        order = _SlowOrders()

    monkeypatch.setattr(payment_service, "_razorpay_client", lambda: _Client())

    ticks = 0

    async def ticker():
        nonlocal ticks
        while True:
            await asyncio.sleep(0.02)
            ticks += 1

    ticking = asyncio.create_task(ticker())
    try:
        order = await PaymentService(db).create_order(customer_id, CreateOrderRequest(purpose="booking", booking_id=booking_id))
    finally:
        ticking.cancel()
    assert order["amount"] == 34900
    assert seen["thread"] != loop_thread
    # The loop kept serving other coroutines for the whole 0.4 s call.
    assert ticks >= 10


async def test_create_order_timeout_is_the_friendly_error(db, cleanup, gateway_keys, monkeypatch):
    customer_id, booking_id = await _customer_with_booking(db, cleanup)

    class _HungOrders:
        def create(self, payload):
            raise requests.exceptions.ReadTimeout("Read timed out. (read timeout=15)")

    class _Client:
        order = _HungOrders()

    monkeypatch.setattr(payment_service, "_razorpay_client", lambda: _Client())
    with pytest.raises(BadRequestException, match="Couldn't start the payment — please try again"):
        await PaymentService(db).create_order(customer_id, CreateOrderRequest(purpose="booking", booking_id=booking_id))
    assert await db.payment_orders.count_documents({"customer_id": customer_id}) == 0


async def test_real_client_is_reused_and_every_request_gets_the_default_timeout(gateway_keys, monkeypatch):
    from requests.adapters import HTTPAdapter

    monkeypatch.setattr(payment_service, "_RZP_CLIENT", None)
    client = payment_service._razorpay_client()
    assert payment_service._razorpay_client() is client
    client.base_url = "https://razorpay.invalid"  # never resolvable, belt and braces

    class _Sent(Exception):
        pass

    def _intercept(self, request, **kwargs):
        raise _Sent(kwargs.get("timeout"))

    monkeypatch.setattr(HTTPAdapter, "send", _intercept)
    with pytest.raises(_Sent) as sent:
        await payment_service._rzp(client.payment_link.fetch, "plink_never_sent")
    assert sent.value.args[0] == payment_service._RZP_TIMEOUT == (5, 15)


class _RecordingLinks:
    def __init__(self):
        self.fetched: list[str] = []
        self.fail_with: Exception | None = None

    def fetch(self, link_id):
        self.fetched.append(link_id)
        if self.fail_with:
            raise self.fail_with
        return {"id": link_id, "status": "created", "payments": []}


async def _seed_links(db, cleanup, tag: str, count: int) -> list[str]:
    # Older than anything else pending in the test db, so these are first in line.
    base = now_ist() - timedelta(hours=47)
    ids = [f"plink_{tag}_{i}" for i in range(count)]
    await db.payment_orders.insert_many([
        {
            "kind": "link", "razorpay_link_id": link_id, "status": "created", "purpose": "booking",
            "customer_id": f"cust-{tag}", "booking_id": str(ObjectId()), "amount_paise": 10000,
            "created_at": base + timedelta(minutes=i),
        }
        for i, link_id in enumerate(ids)
    ])
    cleanup.append(("payment_orders", {"customer_id": f"cust-{tag}"}))
    return ids


async def _no_notify(order_doc):
    raise AssertionError("nothing was paid — nobody should be notified")


async def test_link_sweep_reaches_links_beyond_the_cap_across_passes(db, cleanup, gateway_keys, monkeypatch):
    links = _RecordingLinks()

    class _Client:
        payment_link = links

    monkeypatch.setattr(payment_service, "_razorpay_client", lambda: _Client())
    monkeypatch.setattr(payment_service, "_SWEEP_MAX_CALLS", 3)
    ids = await _seed_links(db, cleanup, f"fair{next(_seq)}", 5)
    svc = PaymentService(db)

    await svc.sync_pending_links(_no_notify)
    assert links.fetched == ids[:3]  # capped, oldest first

    links.fetched.clear()
    await svc.sync_pending_links(_no_notify)
    assert len(links.fetched) <= 3
    assert links.fetched[:2] == ids[3:]  # the never-checked two go first

    stamped = await db.payment_orders.count_documents({"razorpay_link_id": {"$in": ids}, "last_checked_at": {"$exists": True}})
    assert stamped == 5


async def test_sweep_stops_on_a_timeout_and_resumes_next_pass(db, cleanup, gateway_keys, monkeypatch):
    links = _RecordingLinks()
    links.fail_with = requests.exceptions.ConnectTimeout("connect timeout=5")

    class _Client:
        payment_link = links

    monkeypatch.setattr(payment_service, "_razorpay_client", lambda: _Client())
    ids = await _seed_links(db, cleanup, f"down{next(_seq)}", 3)
    svc = PaymentService(db)

    # Razorpay unreachable: one lookup, then the pass ends — no queue of timeouts.
    await svc.sync_pending_links(_no_notify)
    assert links.fetched == ids[:1]
    assert await db.payment_orders.count_documents({"razorpay_link_id": {"$in": ids}, "last_checked_at": {"$exists": True}}) == 0

    # The other sweeps in the same pass skip the gateway entirely.
    links.fetched.clear()
    await svc.sync_pending_links(_no_notify)
    assert links.fetched == []

    # Next pass, gateway back: picks up where it stopped.
    monkeypatch.setattr(payment_service, "_sweeps_paused_until", 0.0)
    links.fail_with = None
    await svc.sync_pending_links(_no_notify)
    assert links.fetched[:3] == ids


async def test_a_bad_record_is_skipped_not_fatal(db, cleanup, gateway_keys, monkeypatch):
    class _Links(_RecordingLinks):
        def fetch(self, link_id):
            self.fetched.append(link_id)
            if link_id.endswith("_0"):
                raise RuntimeError("The id provided does not exist")
            return {"id": link_id, "status": "created", "payments": []}

    links = _Links()

    class _Client:
        payment_link = links

    monkeypatch.setattr(payment_service, "_razorpay_client", lambda: _Client())
    ids = await _seed_links(db, cleanup, f"bad{next(_seq)}", 3)

    await PaymentService(db).sync_pending_links(_no_notify)
    assert links.fetched[:3] == ids
    # Stamped too, so it goes to the back of the line instead of hogging the front.
    assert await db.payment_orders.count_documents({"razorpay_link_id": {"$in": ids}, "last_checked_at": {"$exists": True}}) == 3
