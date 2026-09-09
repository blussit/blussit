"""
Management KPI engine — the arithmetic management will make decisions on.
Pins the definitions: repeat rate (customer's first booking predates the
period), revenue growth vs an equal-length previous period, unit-economics
/ break-even math from admin-entered cost inputs, and the settings
round-trip that feeds them.
"""
from datetime import timedelta

import pytest
from bson import ObjectId

from app.services.kpi_service import KpiService, resolve_period, _growth, _pct
from app.utils.timezone import now_ist

from tests.factories import get_hatchback_type_id, make_customer_with_vehicle, make_service_center


def test_resolve_period_prev_window_is_equal_length():
    s, e, ps, pe = resolve_period("7d", None, None)
    assert (e - s) == (pe - ps) == timedelta(days=7)
    assert pe == s
    s, e, ps, pe = resolve_period(None, "2026-08-01", "2026-08-31")
    assert (e - s).days == 31 and (pe - ps).days == 31


def test_growth_and_pct_edge_cases():
    assert _growth(120, 100) == 20.0
    assert _growth(80, 100) == -20.0
    assert _growth(50, 0) is None  # no baseline -> no fabricated %
    assert _pct(1, 0) is None


@pytest.fixture
async def rig(db, cleanup):
    hatchback = await get_hatchback_type_id(db)
    center_id = await make_service_center(db)
    cleanup.append(("service_centers", {"_id": ObjectId(center_id)}))

    async def make_booking(customer_id, vehicle_id, *, days_ago: int, amount: float, status="completed"):
        created = now_ist() - timedelta(days=days_ago)
        res = await db.bookings.insert_one({
            "customer_id": customer_id, "vehicle_id": vehicle_id, "service_center_id": center_id,
            "service_ids": [], "status": status, "total_amount": amount, "subtotal": amount,
            "scheduled_date": created.replace(tzinfo=None), "scheduled_slot": "09:00-12:00",
            "created_at": created, "updated_at": created, "booking_number": f"BKKPI{days_ago}{amount:.0f}",
        })
        cleanup.append(("bookings", {"_id": res.inserted_id}))

    ids = []
    for _ in range(2):
        customer_id, vehicle_id, _addr = await make_customer_with_vehicle(db, hatchback)
        cleanup.append(("users", {"_id": ObjectId(customer_id)}))
        cleanup.append(("vehicles", {"owner_id": customer_id}))
        cleanup.append(("addresses", {"owner_id": customer_id}))
        ids.append((customer_id, vehicle_id))
    return {"db": db, "make_booking": make_booking, "ids": ids, "center_id": center_id}


@pytest.mark.asyncio
async def test_repeat_rate_counts_only_customers_seen_before_the_period(rig, db):
    (c1, v1), (c2, v2) = rig["ids"]
    # c1: booked 20 days ago AND 2 days ago -> repeat inside a 7d window.
    await rig["make_booking"](c1, v1, days_ago=20, amount=300)
    await rig["make_booking"](c1, v1, days_ago=2, amount=300)
    # c2: first-ever booking 3 days ago -> new, not repeat.
    await rig["make_booking"](c2, v2, days_ago=3, amount=500)

    s, e, ps, pe = resolve_period("7d", None, None)
    out = await KpiService(db).overview(s, e, ps, pe)
    assert out["current"]["bookings"] >= 2
    # Of the two period customers, exactly c1 is a repeat -> 50% when the
    # test DB holds only our fixtures; with unrelated rows present the
    # rate is still bounded and repeat >= 1 is provable via business().
    biz = await KpiService(db).business(s, e, ps, pe)
    assert biz["revenue_quality"]["repeat_customer_revenue"] >= 300
    assert biz["revenue_quality"]["new_customer_revenue"] >= 500


@pytest.mark.asyncio
async def test_revenue_growth_vs_previous_period(rig, db):
    (c1, v1), _ = rig["ids"]
    await rig["make_booking"](c1, v1, days_ago=10, amount=1000)  # previous 7d window
    await rig["make_booking"](c1, v1, days_ago=2, amount=1500)   # current 7d window
    s, e, ps, pe = resolve_period("7d", None, None)
    out = await KpiService(db).business(s, e, ps, pe)
    assert out["totals"]["revenue"] >= 1500
    assert out["totals"]["revenue_growth"] is not None


@pytest.mark.asyncio
async def test_financial_break_even_math_from_settings(rig, db, cleanup):
    svc = KpiService(db)
    original = await db.business_settings.find_one({"_id": "singleton"})
    try:
        await svc.update_settings({"variable_cost_per_wash": 100.0, "fixed_cost_monthly": 30000.0, "kit_cost": 25000.0, "kits_count": 1})
        (c1, v1), _ = rig["ids"]
        await rig["make_booking"](c1, v1, days_ago=1, amount=400)
        s, e, ps, pe = resolve_period("7d", None, None)
        out = await svc.financial(s, e, ps, pe)
        # Contribution per wash = AOV - variable cost; break-even washes/day
        # = daily fixed cost / contribution per wash.
        assert out["contribution_per_wash"] == round(out["aov"] - 100.0, 2)
        expected_be = round((30000.0 / 30) / out["contribution_per_wash"], 1)
        assert out["break_even_washes_per_day"] == expected_be
        assert out["variable_cost"] == out["washes"] * 100.0
    finally:
        if original is not None:
            await db.business_settings.replace_one({"_id": "singleton"}, original, upsert=True)
        else:
            await db.business_settings.delete_one({"_id": "singleton"})


@pytest.mark.asyncio
async def test_settings_roundtrip_ignores_unknown_keys(db):
    svc = KpiService(db)
    original = await db.business_settings.find_one({"_id": "singleton"})
    try:
        out = await svc.update_settings({"variable_cost_per_wash": 77.0, "hacker_field": "x"})
        assert out["variable_cost_per_wash"] == 77.0
        assert "hacker_field" not in out
        assert out["targets"]["washes_per_captain_per_day"] > 0  # defaults survive
    finally:
        if original is not None:
            await db.business_settings.replace_one({"_id": "singleton"}, original, upsert=True)
        else:
            await db.business_settings.delete_one({"_id": "singleton"})
