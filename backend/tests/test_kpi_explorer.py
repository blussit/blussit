"""
Admin KPI explorer (GET /analytics/kpis-explorer) — the dashboard's
interactive charts. Pins:
- the slice's totals equal the existing section definitions (bookings on
  created_at, revenue on completion date, plans from paid payment_orders);
- day / week / month buckets always add back up to the totals;
- service revenue is split evenly across a multi-service booking;
- every drill-down list (GET /bookings with the same filters, GET
  /subscriptions/admin/plan-purchases) returns exactly the records a bar
  counted — the bar and its list never disagree;
- it's admin-only.
"""
from datetime import datetime, timedelta, timezone

import pytest
from bson import ObjectId
from httpx import ASGITransport, AsyncClient

from app.services.kpi_service import KpiService, resolve_period
from app.utils.timezone import now_ist
from tests.factories import get_hatchback_type_id, get_suv_type_id, make_manager, make_service_center


def _auth(user_id: str, role: str, center_id: str | None = None) -> dict:
    from app.core.security import create_access_token

    return {"Authorization": f"Bearer {create_access_token(user_id, role, {'service_center_id': center_id, 'tv': 0})}"}


def _client():
    from app.main import app

    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


@pytest.fixture
async def slice_rig(db, cleanup):
    center = await make_service_center(db)
    services = await db.services.find({"is_active": {"$ne": False}}, {"name": 1}).to_list(length=2)
    svc_a, svc_b = str(services[0]["_id"]), str(services[1]["_id"])
    hatch, suv = await get_hatchback_type_id(db), await get_suv_type_id(db)
    today = now_ist().replace(hour=10, minute=0, second=0, microsecond=0)
    day = lambda n: (today - timedelta(days=n)).astimezone(timezone.utc)  # noqa: E731

    def booking(n, *, created, status="pending", closed=None, amount=300.0, sids=(svc_a,), vt=hatch, source="app"):
        return {
            "booking_number": f"BK-EXPL-{n}-{ObjectId()}", "customer_id": str(ObjectId()), "service_center_id": center,
            "status": status, "total_amount": amount, "subtotal": amount, "service_ids": list(sids), "vehicle_type": vt,
            "source": source, "created_at": created, "closed_at": closed, "scheduled_date": created, "scheduled_slot": "09:00-12:00",
            "visit_line_key": f"expl-{n}-{ObjectId()}", "is_deleted": False,
        }

    docs = [
        booking(1, created=day(20), status="completed", closed=day(19), amount=400.0, sids=(svc_a, svc_b), vt=suv, source="staff"),
        booking(2, created=day(10), status="completed", closed=day(10), amount=300.0),
        booking(3, created=day(10), status="cancelled", amount=300.0, source="whatsapp"),
        booking(4, created=day(3), amount=500.0, sids=(svc_b,), vt=suv, source="staff"),
        # Created before the window, completed inside it: revenue counts, booking doesn't.
        booking(5, created=day(60), status="completed", closed=day(2), amount=250.0),
        # Soft-deleted: never counted.
        {**booking(6, created=day(5), status="completed", closed=day(5), amount=999.0), "is_deleted": True},
    ]
    ids = (await db.bookings.insert_many(docs)).inserted_ids
    plan = await db.subscription_plans.find_one({})
    orders = (await db.payment_orders.insert_many([
        {"purpose": "subscription", "status": "paid", "kind": "link", "channel": "manager", "service_center_id": center,
         "plan_id": str(plan["_id"]), "service_id": svc_a, "vehicle_type": hatch, "amount_paise": 99900, "created_at": day(4)},
        {"purpose": "subscription", "status": "created", "kind": "link", "channel": "manager", "service_center_id": center,
         "plan_id": str(plan["_id"]), "amount_paise": 50000, "created_at": day(4)},
    ])).inserted_ids
    cleanup.append(("bookings", {"_id": {"$in": ids}}))
    cleanup.append(("payment_orders", {"_id": {"$in": orders}}))
    cleanup.append(("service_centers", {"_id": ObjectId(center)}))
    return {"center": center, "svc_a": svc_a, "svc_b": svc_b, "hatch": hatch, "suv": suv, "plan_id": str(plan["_id"])}


@pytest.mark.asyncio
async def test_explorer_slice_totals_buckets_and_breakdowns(db, slice_rig):
    s, e, ps, pe = resolve_period("30d", None, None)
    kpi = KpiService(db)
    data = await kpi.explorer(s, e, ps, pe, service_center_id=slice_rig["center"])
    t = data["totals"]
    assert (t["bookings"], t["completed"], t["cancelled"]) == (4, 3, 1)
    assert t["revenue"] == 950.0  # 400 + 300 + 250 (completed in window), deleted 999 excluded
    assert (t["plans_sold"], t["plan_revenue"]) == (1, 999.0)
    assert data["range"]["granularity"] == "day" and len(data["series"]) == 30

    # Same numbers as the existing per-window engine for that center.
    totals = await kpi._window_totals(s, e, {"service_center_id": slice_rig["center"]})
    assert (totals["bookings"], totals["completed"], round(totals["revenue"], 2)) == (t["bookings"], t["completed"], t["revenue"])

    # Buckets add back up, at every size.
    for gran in ("day", "week", "month"):
        g = await kpi.explorer(s, e, ps, pe, service_center_id=slice_rig["center"], granularity=gran)
        assert sum(b["bookings"] for b in g["series"]) == 4, gran
        assert round(sum(b["revenue"] for b in g["series"]), 2) == 950.0, gran
        assert sum(b["plans_sold"] for b in g["series"]) == 1, gran
        assert g["series"][0]["start"] == s.date().isoformat()
        assert g["series"][-1]["end"] == (e - timedelta(days=1)).date().isoformat()

    by_service = {r["id"]: r for r in data["by_service"]}
    assert by_service[slice_rig["svc_a"]]["bookings"] == 3  # 1, 2, 3 (5 was created before the window)
    assert by_service[slice_rig["svc_b"]]["bookings"] == 2  # 1, 4
    # Booking 1's ₹400 is split ₹200/₹200; svc_a also has 300 + 250.
    assert by_service[slice_rig["svc_a"]]["revenue"] == 750.0
    assert by_service[slice_rig["svc_b"]]["revenue"] == 200.0
    by_type = {r["id"]: r for r in data["by_vehicle_type"]}
    assert by_type[slice_rig["suv"]]["bookings"] == 2 and by_type[slice_rig["hatch"]]["bookings"] == 2
    by_source = {r["key"]: r["bookings"] for r in data["by_source"]}
    assert by_source == {"staff": 2, "app": 1, "whatsapp": 1}

    # Filters narrow every number consistently.
    suv_only = await kpi.explorer(s, e, ps, pe, service_center_id=slice_rig["center"], vehicle_type=slice_rig["suv"])
    assert (suv_only["totals"]["bookings"], suv_only["totals"]["revenue"]) == (2, 400.0)
    assert suv_only["totals"]["plans_sold"] == 0  # the plan was sold for a hatchback
    staff_only = await kpi.explorer(s, e, ps, pe, service_center_id=slice_rig["center"], source="staff")
    assert staff_only["totals"]["bookings"] == 2


@pytest.mark.asyncio
async def test_explorer_drill_down_lists_match_the_bars(db, slice_rig):
    admin = await db.users.find_one({"role": "admin"})
    headers = _auth(str(admin["_id"]), "admin")
    c = slice_rig["center"]
    async with _client() as client:
        res = await client.get(f"/api/v1/analytics/kpis-explorer?period=30d&service_center_id={c}", headers=headers)
        assert res.status_code == 200, res.text
        data = res.json()["data"]
        by_service = {r["id"]: r for r in data["by_service"]}

        async def count(params: str) -> int:
            r = await client.get(f"/api/v1/bookings?period=30d&service_center_id={c}&page_size=100&{params}", headers=headers)
            assert r.status_code == 200, r.text
            return r.json()["meta"]["total"]

        assert await count("") == data["totals"]["bookings"]
        assert await count("date_field=completed") == data["totals"]["completed"]
        assert await count(f"service_id={slice_rig['svc_a']}") == by_service[slice_rig["svc_a"]]["bookings"]
        assert await count(f"vehicle_type={slice_rig['suv']}") == 2
        assert await count("source=whatsapp") == 1
        # One bucket (a single day) drills to exactly its bookings.
        busiest = max(data["series"], key=lambda b: b["bookings"])
        assert await count(f"start={busiest['start']}&end={busiest['end']}") == busiest["bookings"]

        plans = await client.get(
            f"/api/v1/subscriptions/admin/plan-purchases?period=30d&service_center_id={c}&plan_id={slice_rig['plan_id']}", headers=headers,
        )
        assert plans.status_code == 200 and plans.json()["meta"]["total"] == data["totals"]["plans_sold"] == 1


@pytest.mark.asyncio
async def test_explorer_is_admin_only_and_validates_input(db, slice_rig, cleanup):
    manager = await make_manager(db, slice_rig["center"])
    cleanup.append(("users", {"_id": ObjectId(manager)}))
    admin = await db.users.find_one({"role": "admin"})
    async with _client() as client:
        res = await client.get("/api/v1/analytics/kpis-explorer?period=30d", headers=_auth(manager, "manager", slice_rig["center"]))
        assert res.status_code == 403
        bad = await client.get("/api/v1/analytics/kpis-explorer?service_center_id=not-an-id", headers=_auth(str(admin["_id"]), "admin"))
        assert bad.status_code == 422
        too_long = await client.get("/api/v1/analytics/kpis-explorer?start=2020-01-01&end=2026-01-01", headers=_auth(str(admin["_id"]), "admin"))
        assert too_long.status_code == 400
