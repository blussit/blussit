"""SMALL-1 (2026-10-07): the PLANS-2 names become real enum members, and a
scheduled pass is bucketed the least surprising way everywhere a status is
validated or grouped.

  - CustomerWalletEntryKind.REFUND = "refund" reads "Plan Refund";
  - SubscriptionStatus.SCHEDULED = "scheduled" is PASS_SCHEDULED;
  - the manager / admin subscription overviews show renewals paid ahead as
    their OWN group: KPI `scheduled`, filter `status=scheduled`, row status
    "scheduled" — never inside "active" (they can't be booked yet). Once a
    scheduled pass's start has come it reads "active" (KPI, filter, row)
    even before the promotion sweep stores it;
  - a car / customer / center with only a scheduled pass still counts as
    holding a live pass (car retype / removal guards).
Razorpay is stubbed; local Mongo only."""
from datetime import timedelta

import pytest

from app.core.exceptions import BadRequestException
from app.models.enums import CustomerWalletEntryKind, SubscriptionStatus
from app.models.subscription import UserSubscriptionModel
from app.schemas.custom_plan_schema import CustomPlanRenewRequest
from app.services.custom_plan_service import CustomPlanService
from app.services.customer_wallet_service import KIND_LABELS
from app.services.subscription_service import PASS_SCHEDULED, LIVE_PASS_STATUSES, UserSubscriptionService
from app.utils.timezone import now_ist

from tests.plans2_factories import (  # noqa: F401 — fixtures
    _tidy,
    activated_cash,
    gateway,
    mgr,
    pass_of,
    rig,
    vehicle_id_of,
)

pytestmark = pytest.mark.asyncio


def test_enum_members_and_labels():
    assert CustomerWalletEntryKind.REFUND.value == "refund"
    assert KIND_LABELS[CustomerWalletEntryKind.REFUND.value] == "Plan Refund"
    assert SubscriptionStatus.SCHEDULED.value == "scheduled" == PASS_SCHEDULED
    assert PASS_SCHEDULED in LIVE_PASS_STATUSES
    # The model accepts a scheduled pass (no validation error).
    assert UserSubscriptionModel.model_fields["status"].annotation is SubscriptionStatus
    assert SubscriptionStatus("scheduled") is SubscriptionStatus.SCHEDULED


async def _scheduled_renewal(db, rig) -> tuple[dict, list[dict], list[dict]]:
    """A paid cart (two live passes) renewed and paid: two scheduled passes."""
    old = await activated_cash(db, rig)
    service = CustomPlanService(db)
    renewal = await service.renew(old["id"], CustomPlanRenewRequest(), **mgr(rig))
    await service.mark_cash_paid(renewal["id"], expected_revision=1, note=None, **mgr(rig))
    car_b = await vehicle_id_of(db, rig, rig["plate_b"])
    olds = [await pass_of(db, old["id"], v) for v in (rig["car_a"], car_b)]
    news = [await pass_of(db, renewal["id"], v) for v in (rig["car_a"], car_b)]
    assert all(p["status"] == PASS_SCHEDULED for p in news)
    return renewal, olds, news


async def _all_rows(call, status=None) -> list[dict]:
    out = await call(status, 1, 200)
    return out["rows"]


async def test_overviews_group_scheduled_passes_on_their_own(db, rig, gateway):
    _renewal, olds, news = await _scheduled_renewal(db, rig)
    subs = UserSubscriptionService(db)
    old_ids, new_ids = {str(p["_id"]) for p in olds}, {str(p["_id"]) for p in news}

    async def center(status=None, page=1, size=200):
        return await subs.center_overview(rig["center"], "manager", rig["center"], page=page, page_size=size, status=status)

    async def admin(status=None, page=1, size=200):
        return await subs.admin_overview(page=page, page_size=size, status=status)

    ov = await center()
    assert ov["kpis"]["scheduled"] == 2 and ov["kpis"]["active"] == 2 and ov["kpis"]["total"] == 4
    rows = {r["subscription_id"]: r for r in ov["rows"]}
    assert {rows[i]["status"] for i in new_ids} == {PASS_SCHEDULED}
    assert {rows[i]["status"] for i in old_ids} == {"active"}
    assert {r["subscription_id"] for r in await _all_rows(center, "scheduled")} == new_ids
    assert {r["subscription_id"] for r in await _all_rows(center, "active")} == old_ids
    # Not "expired" either.
    assert not {r["subscription_id"] for r in await _all_rows(center, "expired")} & new_ids

    adm = await admin()
    assert adm["kpis"]["scheduled"] >= 2
    assert new_ids <= {r["subscription_id"] for r in await _all_rows(admin, "scheduled")}
    assert not new_ids & {r["subscription_id"] for r in await _all_rows(admin, "active")}

    # The start of one has come; the promotion sweep hasn't stored it yet.
    started = news[0]
    await db.user_subscriptions.update_one(
        {"_id": started["_id"]}, {"$set": {"start_date": now_ist() - timedelta(hours=1), "end_date": now_ist() + timedelta(days=29)}},
    )
    ov = await center()
    assert ov["kpis"]["scheduled"] == 1 and ov["kpis"]["active"] == 3
    assert next(r for r in ov["rows"] if r["subscription_id"] == str(started["_id"]))["status"] == "active"
    assert str(started["_id"]) in {r["subscription_id"] for r in await _all_rows(center, "active")}
    assert {r["subscription_id"] for r in await _all_rows(center, "scheduled")} == {str(news[1]["_id"])}


async def test_a_car_held_only_by_a_scheduled_pass_is_still_on_a_plan(db, rig, gateway):
    from app.schemas.profile_schema import VehicleUpdateRequest
    from app.services.profile_service import VehicleService

    _renewal, olds, news = await _scheduled_renewal(db, rig)
    # The old period is over (ended + expired); only the renewal holds car A.
    await db.user_subscriptions.update_one({"_id": olds[0]["_id"]}, {"$set": {"status": "expired", "end_date": now_ist() - timedelta(days=1)}})
    vehicles = VehicleService(db)
    with pytest.raises(BadRequestException, match="active plan"):
        await vehicles.update(rig["customer"], rig["car_a"], VehicleUpdateRequest(vehicle_type=rig["suv"]))
    with pytest.raises(BadRequestException, match="active plan"):
        await vehicles.delete(rig["customer"], rig["car_a"])
