"""SMALL-3 (2026-10-07): net plan revenue. A custom-plan car refunded to the
wallet (PLANS-2 refund_car: ledger kind "refund" with meta.custom_plan_id,
the refund record on the cart's car) is money given back. Every plan-money
view keeps its gross figure under the same key and shows the refunds and
the net beside it:

  - admin KPI overview + manager overview: `plan_refunds`,
    `plan_revenue_net` (plan_revenue / combined_revenue stay gross);
  - the admin explorer: totals / previous / each series bucket;
  - the manager dashboard's plan sales: `refunds` / `revenue_net` (and on
    the custom plan's own row);
  - the plan-purchase list: `refunded_amount` / `net_amount` per cart row;
  - UserSubscriptionService.center_plan_refunds next to center_plan_revenue.

Refunds are dated by the refund (cars[].refund.at), so a period only
nets the refunds made in it. Razorpay is stubbed; local Mongo only."""
from datetime import timedelta

import pytest
from bson import ObjectId

from app.services.custom_plan_service import TEMPLATE_PLAN_ID, CustomPlanService
from app.services.customer_wallet_service import KIND_LABELS, CustomerWalletService
from app.services.kpi_service import KpiService
from app.services.manager_dashboard_service import ManagerDashboardService
from app.services.subscription_service import UserSubscriptionService
from app.utils.timezone import now_ist

from tests.plans2_factories import (  # noqa: F401 — fixtures
    _tidy,
    activated_cash,
    gateway,
    mgr,
    rig,
)

pytestmark = pytest.mark.asyncio


def _window():
    """±1 h around now, kept inside today's IST day (FINAL-POLISH 2026-10-08):
    the explorer's series has one bucket per IST day stepping from `s`, so a
    window crossing midnight (a run at 00:00–01:00 IST) left the refund's
    day without a bucket. Previous window = the 2 h before `s`."""
    now = now_ist()
    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    s = max(now - timedelta(hours=1), day_start)
    e = min(now + timedelta(hours=1), day_start + timedelta(days=1))
    return s, e, s - timedelta(hours=2), s


async def test_refunds_net_out_of_every_plan_revenue_view(db, rig, gateway):
    cart = await activated_cash(db, rig)
    s, e, ps, pe = _window()
    kpi = KpiService(db)
    before = (await kpi.overview(s, e, ps, pe))["current"]

    refund = await CustomPlanService(db).refund_car(cart["id"], rig["car_a"], amount=None, reason="Moving city", **mgr(rig))
    amount = refund["refund"]["amount"]
    gross = cart["total_amount"]
    assert 0 < amount <= gross

    # The ledger row reads "Plan Refund".
    entry = await db.customer_wallet_ledger.find_one({"key": f"cp-refund:{cart['id']}:{rig['car_a']}"})
    assert entry["kind"] == "refund" and entry["meta"]["custom_plan_id"] == cart["id"]
    assert CustomerWalletService.entry_view(entry)["label"] == KIND_LABELS["refund"] == "Plan Refund"

    # Admin overview: gross unchanged, refunds + net beside it (deltas — the
    # shared test DB may hold other plan money in the same hour).
    after = (await kpi.overview(s, e, ps, pe))["current"]
    assert after["plan_revenue"] == before["plan_revenue"]
    assert after["combined_revenue"] == before["combined_revenue"]
    assert after["plan_refunds"] == pytest.approx(before["plan_refunds"] + amount)
    assert after["plan_revenue_net"] == pytest.approx(before["plan_revenue_net"] - amount)
    assert after["plan_revenue_net"] == pytest.approx(after["plan_revenue"] - after["plan_refunds"])

    # Manager overview (the rig's own center — exact).
    mo = (await kpi.manager_overview(rig["center"], s, e, ps, pe))["current"]
    assert mo["plan_revenue"] == gross and mo["plans_sold"] == 1
    assert mo["plan_refunds"] == amount and mo["plan_revenue_net"] == pytest.approx(gross - amount)
    subs = UserSubscriptionService(db)
    assert await subs.center_plan_revenue(rig["center"], s, e) == (gross, 1)  # unchanged meaning
    assert await subs.center_plan_refunds(rig["center"], s, e) == amount
    assert await subs.center_plan_refunds(rig["other"], s, e) == 0

    # Explorer, center-scoped: totals and the series.
    ex = await kpi.explorer(s, e, ps, pe, service_center_id=rig["center"])
    assert ex["totals"]["plan_revenue"] == gross
    assert ex["totals"]["plan_refunds"] == amount and ex["totals"]["plan_revenue_net"] == pytest.approx(gross - amount)
    assert sum(b["plan_refunds"] for b in ex["series"]) == pytest.approx(amount)
    assert all(b["plan_revenue_net"] == pytest.approx(b["plan_revenue"] - b["plan_refunds"]) for b in ex["series"])
    assert ex["previous"]["plan_refunds"] == 0
    # A car-type slice never had the cart's gross (its order has no type) —
    # nor its refunds.
    sliced = await kpi.explorer(s, e, ps, pe, service_center_id=rig["center"], vehicle_type=rig["hatch"])
    assert sliced["totals"]["plan_revenue"] == 0 and sliced["totals"]["plan_refunds"] == 0

    # Manager dashboard plan sales: totals and the custom plan's own row.
    sales = await ManagerDashboardService(db)._plan_sales(rig["center"], s, e)
    assert sales["revenue"] == round(gross) and sales["refunds"] == amount and sales["revenue_net"] == round(gross) - amount
    item = next(i for i in sales["items"] if i["plan_id"] == TEMPLATE_PLAN_ID)
    assert item["refunds"] == amount and item["revenue_net"] == item["revenue"] - amount

    # Plan-purchase list: the cart's row keeps what was paid, shows the refund.
    rows, total = await subs.plan_purchases(s, e, 1, 50, rig["center"])
    assert total == 1
    assert rows[0]["amount"] == gross and rows[0]["refunded_amount"] == amount
    assert rows[0]["net_amount"] == pytest.approx(gross - amount)


async def test_a_refund_counts_in_the_period_it_was_made(db, rig, gateway):
    cart = await activated_cash(db, rig)
    await CustomPlanService(db).refund_car(cart["id"], rig["car_a"], amount=None, reason="Moving city", **mgr(rig))
    # Pretend the refund happened two hours ago — before this window.
    earlier = now_ist() - timedelta(hours=2)
    await db.custom_plans.update_one({"_id": ObjectId(cart["id"]), "cars.vehicle_id": rig["car_a"]}, {"$set": {"cars.$.refund.at": earlier}})
    s, e, ps, pe = _window()
    kpi = KpiService(db)
    mo = await kpi.manager_overview(rig["center"], s, e, ps, pe)
    assert mo["current"]["plan_refunds"] == 0 and mo["current"]["plan_revenue_net"] == mo["current"]["plan_revenue"]
    assert mo["previous"]["plan_refunds"] > 0
    assert mo["previous"]["plan_revenue_net"] == pytest.approx(mo["previous"]["plan_revenue"] - mo["previous"]["plan_refunds"])
    # The purchase row shows every refund on the cart, whenever it was made.
    rows, _ = await UserSubscriptionService(db).plan_purchases(s, e, 1, 50, rig["center"])
    assert rows[0]["refunded_amount"] == mo["previous"]["plan_refunds"]
