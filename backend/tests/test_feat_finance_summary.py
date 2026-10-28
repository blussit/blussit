"""Finance ledger — summary math (with seeded paid bookings), voided entries
ignored, IST date boundaries, concurrency, and that adding entries changes
nothing in the booking collections / KPIs."""
import asyncio
from datetime import datetime

import pytest
from bson import ObjectId

from app.services import finance_service
from tests import test_feat_finance_helpers as fh
from tests import test_fix_core_helpers as h
from tests.factories import make_service_center
from tests.test_feat_finance_helpers import BASE, clean  # noqa: F401 — fixture

pytestmark = pytest.mark.asyncio

# A window no other suite books into: the bookings seeded here are the only
# ones in it, so bookings_earning for it is exact.
WIN_FROM, WIN_TO = "2019-03-01", "2019-03-31"


async def _seed_bookings(db, cleanup):
    center = await make_service_center(db)
    cleanup.append(("service_centers", {"_id": ObjectId(center)}))
    cleanup.append(("bookings", {"service_center_id": center}))
    base = {"service_center_id": center, "is_deleted": False, "captain_earning": 0.0}
    d = lambda day: datetime(2019, 3, day)  # noqa: E731
    await db.bookings.insert_many([
        {**base, "booking_number": f"FIN1{ObjectId()}", "status": "completed", "payment_status": "paid", "payment_method": "cash", "scheduled_date": d(5), "total_amount": 500, "paid_cash": 500, "paid_online": 0},
        {**base, "booking_number": f"FIN2{ObjectId()}", "status": "completed", "payment_status": "paid", "payment_method": "online", "scheduled_date": d(6), "total_amount": 300, "paid_cash": 0, "paid_online": 300},
        # billed but never paid -> not earning
        {**base, "booking_number": f"FIN3{ObjectId()}", "status": "completed", "payment_status": "pending", "payment_method": "cash", "scheduled_date": d(7), "total_amount": 999, "paid_cash": 0, "paid_online": 0},
        # cancelled -> excluded
        {**base, "booking_number": f"FIN4{ObjectId()}", "status": "cancelled", "payment_status": "paid", "payment_method": "cash", "scheduled_date": d(8), "total_amount": 700, "paid_cash": 700, "paid_online": 0},
        # outside the window
        {**base, "booking_number": f"FIN5{ObjectId()}", "status": "completed", "payment_status": "paid", "payment_method": "cash", "scheduled_date": datetime(2019, 4, 2), "total_amount": 111, "paid_cash": 111, "paid_online": 0},
    ])


async def test_summary_math(db, clean, cleanup):
    admin = await fh.admin_h(db)
    await _seed_bookings(db, cleanup)
    from app.services.payment_service import PaymentService

    async with h.client() as c:
        before = (await c.get(f"{BASE}/summary?from={WIN_FROM}&to={WIN_TO}", headers=admin)).json()["data"]
        assert before["bookings_earning_paise"] == 80000 and before["bookings_earning"] == 800.0
        assert before["credits_paise"] == 0 and before["total_balance_paise"] == before["all_time"]["bookings_earning_paise"]
        base_balance = before["total_balance_paise"]

        # Same number as the Collections roll-up it reuses.
        roll = await PaymentService(db).admin_collections(WIN_FROM, WIN_TO)
        assert round(roll["totals"]["net_collected"] * 100) == before["bookings_earning_paise"]

        # entries dated inside the 2019 window can't be created via the API
        # (5-year rule) - insert them raw, plus current ones through the API.
        now = datetime.utcnow()
        raw = lambda **k: {"is_deleted": False, "created_at": now, "updated_at": now, "created_by": "t", "done_by": "t", "description": "", **k}  # noqa: E731
        await db.finance_entries.insert_many([
            raw(title="Sale", type="credit", amount_paise=250000, entry_date="2019-03-10", category="Product Sale"),
            raw(title="Salary", type="debit", amount_paise=100000, entry_date="2019-03-31", category="Salary"),
            raw(title="Ads", type="debit", amount_paise=40000, entry_date="2019-03-01", category=None),
            raw(title="Before", type="debit", amount_paise=77700, entry_date="2019-02-28", category="Salary"),
            raw(title="After", type="credit", amount_paise=55500, entry_date="2019-04-01", category="Product Sale"),
        ])
        finance_service.clear_cache()
        s = (await c.get(f"{BASE}/summary?from={WIN_FROM}&to={WIN_TO}", headers=admin)).json()["data"]
        # inclusive boundaries: 03-01 and 03-31 in, 02-28 and 04-01 out
        assert s["credits_paise"] == 250000 and s["debits_paise"] == 140000
        assert s["net_other_paise"] == 110000 and s["net_period_paise"] == 80000 + 110000
        assert s["credits"] == 2500.0 and s["debits"] == 1400.0 and s["net_other"] == 1100.0
        assert s["credit_count"] == 1 and s["debit_count"] == 2
        assert s["total_balance_paise"] == base_balance + 250000 + 55500 - 140000 - 77700
        cats = {x["category"]: x for x in s["by_category"]}
        assert cats["Product Sale"]["credit_paise"] == 250000 and cats["Salary"]["debit"] == 1000.0 and cats["Uncategorised"]["debit_paise"] == 40000
        assert s["series_unit"] == "day" and [x["key"] for x in s["series"]] == ["2019-03-01", "2019-03-10", "2019-03-31"]
        assert s["period"] == {"from": WIN_FROM, "to": WIN_TO} and "received" in s["bookings_earning_definition"]

        # a long range groups by month
        m = (await c.get(f"{BASE}/summary?from=2019-01-01&to=2019-12-31", headers=admin)).json()["data"]
        assert m["series_unit"] == "month" and [x["key"] for x in m["series"]] == ["2019-02", "2019-03", "2019-04"]

        # voiding takes an entry out of every total
        listed = (await c.get(f"{BASE}/entries?from=2019-03-10&to=2019-03-10", headers=admin)).json()["data"]
        r = await c.post(f"{BASE}/entries/{listed[0]['id']}/void", json={"reason": "wrong amount"}, headers=admin)
        assert r.status_code == 200
        s2 = (await c.get(f"{BASE}/summary?from={WIN_FROM}&to={WIN_TO}", headers=admin)).json()["data"]
        assert s2["credits_paise"] == 0 and s2["total_balance_paise"] == s["total_balance_paise"] - 250000

        # default period = this month (IST); bad ranges refused
        d = (await c.get(f"{BASE}/summary", headers=admin)).json()["data"]
        assert d["period"]["from"] == fh.today()[:8] + "01" and d["period"]["to"] == fh.today()
        assert (await c.get(f"{BASE}/summary?from=2026-02-01&to=2026-01-01", headers=admin)).status_code == 400
        assert (await c.get(f"{BASE}/summary?from=2000-01-01&to=2026-01-01", headers=admin)).status_code == 400


async def test_summary_today_uses_ist_day(db, clean):
    admin = await fh.admin_h(db)
    async with h.client() as c:
        await fh.make(c, admin, type="credit", amount_paise=1000)  # default date
        s = (await c.get(f"{BASE}/summary?from={fh.today()}&to={fh.today()}", headers=admin)).json()["data"]
        assert s["credits_paise"] == 1000 and s["series"][0]["key"] == fh.today()
        s = (await c.get(f"{BASE}/summary?from={fh.day(-1)}&to={fh.day(-1)}", headers=admin)).json()["data"]
        assert s["credits_paise"] == 0


async def test_bookings_earning_is_cached_60s(db, clean, monkeypatch):
    admin = await fh.admin_h(db)
    from app.services.payment_service import PaymentService

    calls = []
    orig = PaymentService.admin_collections

    async def counting(self, a, b):
        calls.append((a, b))
        return await orig(self, a, b)

    monkeypatch.setattr(PaymentService, "admin_collections", counting)
    finance_service.clear_cache()
    async with h.client() as c:
        for _ in range(3):
            assert (await c.get(f"{BASE}/summary?from=2019-03-01&to=2019-03-31", headers=admin)).status_code == 200
    assert len(calls) == 2  # the period + the all-time window, once each


async def test_fifty_parallel_creates_all_land(db, clean):
    admin = await fh.admin_h(db)
    async with h.client() as c:
        rs = await asyncio.gather(*[
            c.post(f"{BASE}/entries", json=fh.body(title=f"Parallel {i}", amount_paise=100 + i), headers=admin) for i in range(50)
        ])
        assert all(r.status_code == 200 for r in rs)
        s = (await c.get(f"{BASE}/summary?from={fh.today()}&to={fh.today()}", headers=admin)).json()["data"]
        assert s["debit_count"] == 50 and s["debits_paise"] == sum(100 + i for i in range(50))
    assert await db.finance_entries.count_documents({}) == 50
    assert await db.audit_logs.count_documents({"module": "finance", "action": "FINANCE_ENTRY_CREATE"}) == 50


async def test_entries_never_touch_booking_numbers(db, clean, cleanup):
    admin = await fh.admin_h(db)
    await _seed_bookings(db, cleanup)
    from app.services.payment_service import PaymentService

    async def snapshot():
        roll = await PaymentService(db).admin_collections(WIN_FROM, WIN_TO)
        return roll["totals"], roll["subscriptions"], roll["wallet"], await db.bookings.count_documents({}), await db.users.count_documents({})

    before = await snapshot()
    async with h.client() as c:
        for i in range(5):
            e = await fh.make(c, admin, type="credit" if i % 2 else "debit", amount_paise=123456, entry_date=fh.today())
        await c.post(f"{BASE}/entries/{e['id']}/void", json={"reason": "test void"}, headers=admin)
        hist = (await c.get("/api/v1/payments/collections/admin", params={"date_from": WIN_FROM, "date_to": WIN_TO}, headers=admin))
        assert hist.status_code == 200
    assert await snapshot() == before
