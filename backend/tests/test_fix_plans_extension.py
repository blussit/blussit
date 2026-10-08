"""Founder rule 2026-10-07 — a SOCIETY pass's period can be extended by a
manager (own center) or an admin, at most 10 days in total per plan month,
so its remaining premium washes can still be booked. Recorded (who, role,
days, when, note), visible to staff and the resident; the daily bucket wash
and the automatic schedule do NOT run during an extension. Monthly passes
get no extension. Local Mongo only."""
import asyncio
import itertools
from datetime import datetime, timedelta, timezone

import pytest
from bson import ObjectId

from app.core.exceptions import AppException, BadRequestException, NotFoundException
from app.schemas.society_schema import PremiumBookingRequest
from app.services.society_service import SocietyService
from app.services.subscription_service import UserSubscriptionService, find_subscriptions_ended, mark_subscription_expired
from app.utils.timezone import from_stored, now_ist

from tests.factories import get_hatchback_type_id, get_star_wash_service_id, make_customer, make_service_center, make_subscription_plan
from tests.society_factories import activate_cash, auth, client, enroll, make_admin, make_center, make_resident, make_society, make_template, staff

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


@pytest.fixture
async def rig(db, cleanup):
    center = await make_center(db, cleanup, spot=5)
    other = await make_center(db, cleanup, spot=2)
    manager_id, captain_id = await staff(db, cleanup, center["id"])
    other_manager, _ = await staff(db, cleanup, other["id"])
    society = await make_society(db, cleanup, center, name=f"Extend Court {next(_seq)}")
    plan = await make_template(db, cleanup, count=4)
    admin_id = await make_admin(db, cleanup)
    return {"center": center, "other": other, "manager": manager_id, "captain": captain_id, "other_manager": other_manager,
            "society": society, "plan": plan, "admin": admin_id}


async def _ending_pass(db, cleanup, rig, *, ends_in: timedelta = timedelta(days=1)):
    resident = await make_resident(db, cleanup)
    view = await enroll(db, rig["society"], resident, rig["plan"]["id"], cars=1)
    enr = (await activate_cash(db, view["id"]))["enrollment"]
    sub_id = enr["cars"][0]["subscription"]["id"]
    end = now_ist() + ends_in
    await db.user_subscriptions.update_one({"_id": ObjectId(sub_id)}, {"$set": {"end_date": end}})
    return resident, enr, sub_id, end


async def _extend(db, rig, sub_id, days, *, role="manager", note=None):
    actor = rig["manager"] if role == "manager" else rig["admin"]
    center = rig["center"]["id"] if role == "manager" else None
    return await SocietyService(db).extend_pass(
        rig["society"]["id"], sub_id, days, note=note, actor_id=actor, actor_role=role, actor_center_id=center,
    )


def _date(dt) -> str:
    return from_stored(dt).date().isoformat()


async def test_extend_within_cap_records_history_and_fields(db, cleanup, rig):
    resident, enr, sub_id, end = await _ending_pass(db, cleanup, rig)
    view = await _extend(db, rig, sub_id, 3, note="Rain week")
    assert view["extension_days"] == 3
    assert view["extension_days_left"] == 7
    assert view["extended_until"].startswith(_date(end + timedelta(days=3)))
    view = await _extend(db, rig, sub_id, 7, role="admin")
    assert view["extension_days"] == 10 and view["extension_days_left"] == 0
    raw = await db.user_subscriptions.find_one({"_id": ObjectId(sub_id)})
    assert [h["days"] for h in raw["extensions"]] == [3, 7]
    assert [h["role"] for h in raw["extensions"]] == ["manager", "admin"]
    assert raw["extensions"][0]["by"] == rig["manager"] and raw["extensions"][0]["note"] == "Rain week"
    assert from_stored(raw["extended_until"]).date() == (end + timedelta(days=10)).date()
    # Staff see it on the residents list; the resident sees the date (not who).
    rows = await SocietyService(db).enrollments_for_society(await SocietyService(db).get_society(rig["society"]["id"]))
    car = next(c for r in rows for c in r["cars"] if (c["subscription"] or {}).get("id") == sub_id)
    assert car["subscription"]["extension_days"] == 10 and len(car["subscription"]["extensions"]) == 2
    mine = await UserSubscriptionService(db).list_my_subscriptions(str(resident["_id"]))
    row = next(s for s in mine if s["id"] == sub_id)
    assert row["extension_days"] == 10 and row["extended_until"] and "extensions" not in row
    hub = await SocietyService(db).my_hub(await SocietyService(db).get_society(rig["society"]["id"]), str(resident["_id"]))
    hub_car = hub["enrollments"][0]["cars"][0]["subscription"]
    assert hub_car["extended_until"] and "extensions" not in hub_car
    # Admin purchased-plans view carries it too.
    ov = await UserSubscriptionService(db).admin_overview(search=resident["phone"])
    assert next(r for r in ov["rows"] if r["subscription_id"] == sub_id)["extension_days"] == 10


async def test_extend_over_the_cap_is_refused(db, cleanup, rig):
    _, _, sub_id, _ = await _ending_pass(db, cleanup, rig)
    await _extend(db, rig, sub_id, 6)
    with pytest.raises(BadRequestException) as exc:
        await _extend(db, rig, sub_id, 5)
    assert "10" in exc.value.message
    with pytest.raises(Exception):
        await _extend(db, rig, sub_id, 0)
    raw = await db.user_subscriptions.find_one({"_id": ObjectId(sub_id)})
    assert raw["extension_days"] == 6 and len(raw["extensions"]) == 1


async def test_concurrent_extends_never_pass_the_cap(db, cleanup, rig):
    _, _, sub_id, end = await _ending_pass(db, cleanup, rig)

    async def go():
        try:
            await _extend(db, rig, sub_id, 4)
            return 4
        except AppException:
            return 0

    granted = await asyncio.gather(*[go() for _ in range(5)])
    raw = await db.user_subscriptions.find_one({"_id": ObjectId(sub_id)})
    assert sum(granted) == raw["extension_days"] == 8
    assert len(raw["extensions"]) == 2
    assert from_stored(raw["extended_until"]).date() == (end + timedelta(days=8)).date()


async def test_extend_cross_center_and_wrong_society_refused(db, cleanup, rig):
    _, _, sub_id, _ = await _ending_pass(db, cleanup, rig)
    other_society = await make_society(db, cleanup, rig["other"], name=f"Other Court {next(_seq)}")
    async with client() as c:
        r = await c.post(f"/api/v1/societies/{rig['society']['id']}/passes/{sub_id}/extend", json={"days": 2},
                         headers=auth(rig["other_manager"], "manager", rig["other"]["id"]))
        assert r.status_code == 403, r.text
        r = await c.post(f"/api/v1/societies/{other_society['id']}/passes/{sub_id}/extend", json={"days": 2},
                         headers=auth(rig["other_manager"], "manager", rig["other"]["id"]))
        assert r.status_code == 404, r.text
        resident = await make_resident(db, cleanup)
        r = await c.post(f"/api/v1/societies/{rig['society']['id']}/passes/{sub_id}/extend", json={"days": 2},
                         headers=auth(str(resident["_id"]), "customer"))
        assert r.status_code == 403, r.text
        r = await c.post(f"/api/v1/societies/{rig['society']['id']}/passes/{sub_id}/extend", json={"days": 11},
                         headers=auth(rig["manager"], "manager", rig["center"]["id"]))
        assert r.status_code == 422, r.text
        r = await c.post(f"/api/v1/societies/{rig['society']['id']}/passes/{sub_id}/extend", json={"days": 2, "note": "Captain on leave"},
                         headers=auth(rig["manager"], "manager", rig["center"]["id"]))
        assert r.status_code == 200, r.text
        assert r.json()["data"]["extension_days"] == 2
    assert (await db.user_subscriptions.find_one({"_id": ObjectId(sub_id)}))["extension_days"] == 2
    audit = await db.audit_logs.find_one({"action": "EXTEND_SOCIETY_PASS", "target_id": sub_id})
    assert audit and audit["details"]["days"] == 2


async def test_only_ending_or_ended_society_passes_with_washes_left(db, cleanup, rig):
    _, _, fresh_id, _ = await _ending_pass(db, cleanup, rig, ends_in=timedelta(days=20))
    with pytest.raises(BadRequestException):
        await _extend(db, rig, fresh_id, 2)
    _, _, empty_id, _ = await _ending_pass(db, cleanup, rig)
    await db.user_subscriptions.update_one({"_id": ObjectId(empty_id)}, {"$set": {"remaining_service_count": 0}})
    with pytest.raises(BadRequestException):
        await _extend(db, rig, empty_id, 2)
    _, _, old_id, _ = await _ending_pass(db, cleanup, rig, ends_in=-timedelta(days=12))
    with pytest.raises(BadRequestException):
        await _extend(db, rig, old_id, 2)
    # A monthly (non-society) pass has no extension.
    hatch = await get_hatchback_type_id(db)
    star = await get_star_wash_service_id(db)
    from app.schemas.subscription_schema import AssignSubscriptionRequest

    plan_id = await make_subscription_plan(db, vehicle_types=[hatch], included_service_ids=[star], total_service_count=2)
    customer_id = await make_customer(db)
    monthly = await UserSubscriptionService(db).assign(AssignSubscriptionRequest(customer_id=customer_id, plan_id=plan_id, vehicle_type=hatch, service_id=star))
    with pytest.raises(NotFoundException):
        await _extend(db, rig, monthly["id"], 2, role="admin")


async def test_bookings_inside_the_extension_only(db, cleanup, rig):
    resident, enr, sub_id, end = await _ending_pass(db, cleanup, rig, ends_in=timedelta(days=1, hours=2))
    service = SocietyService(db)
    rid = str(resident["_id"])
    after_end = (from_stored(end).date() + timedelta(days=1)).isoformat()
    # Before the extension: a day after the end is refused for everyone.
    with pytest.raises(BadRequestException):
        await service.book_premium(PremiumBookingRequest(subscription_ids=[sub_id], scheduled_date=after_end, scheduled_slot="08:00-11:00"),
                                   actor_id=rid, actor_role="customer", actor_center_id=None)
    await _extend(db, rig, sub_id, 3)
    # Resident inside the extension.
    booked = await service.book_premium(PremiumBookingRequest(subscription_ids=[sub_id], scheduled_date=after_end, scheduled_slot="08:00-11:00"),
                                        actor_id=rid, actor_role="customer", actor_center_id=None)
    assert booked["bookings"]
    # Manager inside the extension (another day).
    second = (from_stored(end).date() + timedelta(days=2)).isoformat()
    booked2 = await service.book_premium(PremiumBookingRequest(subscription_ids=[sub_id], scheduled_date=second, scheduled_slot="11:00-14:00"),
                                         actor_id=rig["manager"], actor_role="manager", actor_center_id=rig["center"]["id"], society_id=rig["society"]["id"])
    assert booked2["bookings"]
    # On/after extended_until: nobody.
    past = (from_stored(end).date() + timedelta(days=3)).isoformat()
    for kwargs in ({"actor_id": rid, "actor_role": "customer", "actor_center_id": None},
                   {"actor_id": rig["manager"], "actor_role": "manager", "actor_center_id": rig["center"]["id"], "society_id": rig["society"]["id"]}):
        with pytest.raises(BadRequestException):
            await service.book_premium(PremiumBookingRequest(subscription_ids=[sub_id], scheduled_date=past, scheduled_slot="14:00-17:00"), **kwargs)
    # The automatic schedule never books into the extension.
    with pytest.raises(BadRequestException):
        await service.book_premium(PremiumBookingRequest(subscription_ids=[sub_id], scheduled_date=(from_stored(end).date() + timedelta(days=2)).isoformat(), scheduled_slot="14:00-17:00"),
                                   actor_id=rig["admin"], actor_role="admin", actor_center_id=None, society_id=rig["society"]["id"], allow_extension=False)


async def test_ended_pass_extended_after_the_sweep_and_daily_wash_stays_off(db, cleanup, rig):
    resident, enr, sub_id, end = await _ending_pass(db, cleanup, rig, ends_in=-timedelta(hours=3))
    # The ended-pass sweep already flipped it.
    await mark_subscription_expired(db, sub_id)
    assert (await db.user_subscriptions.find_one({"_id": ObjectId(sub_id)}))["status"] == "expired"
    view = await _extend(db, rig, sub_id, 4)
    assert view["status"] == "active"
    raw = await db.user_subscriptions.find_one({"_id": ObjectId(sub_id)})
    assert raw["status"] == "active"
    # The sweep leaves it alone while the extension runs …
    ended = await find_subscriptions_ended(db, limit=500)
    assert sub_id not in {str(s["_id"]) for s in ended}
    await mark_subscription_expired(db, sub_id)
    assert (await db.user_subscriptions.find_one({"_id": ObjectId(sub_id)}))["status"] == "active"
    # … the customer's plan list shows it bookable …
    mine = await UserSubscriptionService(db).list_my_subscriptions(str(resident["_id"]))
    assert next(s for s in mine if s["id"] == sub_id)["effective_status"] == "active"
    # … but the captain's daily checklist doesn't include it.
    service = SocietyService(db)
    assert sub_id not in {str(s["_id"]) for s in await service._live_society_subs(rig["society"]["id"])}
    # Once the extension is over, the sweep expires it.
    await db.user_subscriptions.update_one({"_id": ObjectId(sub_id)}, {"$set": {"extended_until": now_ist() - timedelta(minutes=1)}})
    ended = await find_subscriptions_ended(db, limit=500)
    assert sub_id in {str(s["_id"]) for s in ended}


async def test_renewal_starts_a_new_period_without_the_old_extension(db, cleanup, rig):
    resident, enr, sub_id, end = await _ending_pass(db, cleanup, rig)
    await _extend(db, rig, sub_id, 5)
    service = SocietyService(db)
    await service.renew(await service.get_enrollment(enr["id"]), method="cash", actor_id=rig["manager"])
    raw = await db.user_subscriptions.find_one({"_id": ObjectId(sub_id)})
    assert raw["extension_days"] == 0 and raw.get("extended_until") is None
    assert len(raw["extensions"]) == 1  # history kept
    # A fresh 10 days are available in the new period once it is ending.
    await db.user_subscriptions.update_one({"_id": ObjectId(sub_id)}, {"$set": {"end_date": now_ist() + timedelta(days=1)}})
    view = await _extend(db, rig, sub_id, 10)
    assert view["extension_days"] == 10
