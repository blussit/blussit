"""PLANS-2 (founder 2026-10-07): renew a custom multi-car plan.

Staff renew a paid cart: a NEW cart (`renewal_of` = old id) priced at
today's catalogue, paid by link or cash like any cart. On activation each
car whose old pass is still live gets a `scheduled` pass that starts the day
after the old pass's Last Booking Day (00:00 IST) for 30 days; the old pass
keeps its washes until then. The car's one-live-pass claim moves to the new
pass at once (it keeps the car through both periods); the scheduled pass is
promoted by the ended-pass sweep or lazily when it is first used. A car
with no live old pass (or one added in the renewal) starts today.
Razorpay is stubbed; local Mongo only."""
import asyncio
from datetime import datetime, timedelta

import pytest
from bson import ObjectId

from app.core.exceptions import BadRequestException
from app.schemas.custom_plan_schema import CustomPlanCreateRequest, CustomPlanPreviewRequest, CustomPlanRenewRequest
from app.services.custom_plan_service import CustomPlanService
from app.services.subscription_service import (
    PASS_SCHEDULED,
    UserSubscriptionService,
    find_subscriptions_ended,
    pass_blocks_new,
    pass_usable_until,
    service_price_for_type,
)
from app.utils.timezone import IST, from_stored, now_ist

from tests.plans2_factories import (  # noqa: F401 — fixtures
    _tidy,
    activated_cash,
    cars,
    gateway,
    link_order,
    mgr,
    paid,
    pass_of,
    rig,
    vehicle_id_of,
)
from tests.society_factories import auth, client

pytestmark = pytest.mark.asyncio


def _midnight_after_last_day(sub: dict) -> datetime:
    """00:00 IST on the day after the pass's Last Booking Day."""
    day = from_stored(pass_usable_until(sub)).date()
    return datetime(day.year, day.month, day.day, tzinfo=IST)


def _label(day) -> str:
    """"6 Nov 2026" — labels carry no leading zero (FINAL-POLISH 2026-10-08)."""
    return f"{day.day} {day.strftime('%b %Y')}"


async def _expected_total(db, items_by_type: list[tuple[str, list[tuple[str, int]]]]) -> float:
    total = 0.0
    for vtype, items in items_by_type:
        for sid, count in items:
            svc = await db.services.find_one({"_id": ObjectId(sid)})
            total += round(service_price_for_type(svc, vtype), 2) * count
    return round(total, 2)


async def test_renew_copies_cars_at_todays_prices_and_shows_pay_to_renew(db, rig, gateway):
    old = await activated_cash(db, rig, discount=100)
    service = CustomPlanService(db)
    star = rig["svc"]["star-wash"]
    svc = await db.services.find_one({"_id": ObjectId(star)})
    original = {k: svc.get(k) for k in ("price", "vehicle_type_prices")}
    try:
        # Catalogue moved since the old cart was sold: the renewal uses today's.
        prices = dict(svc.get("vehicle_type_prices") or {})
        bumped = {k: float(v) + 50 for k, v in prices.items()}
        await db.services.update_one({"_id": svc["_id"]}, {"$set": {"vehicle_type_prices": bumped, "price": float(svc.get("price") or 0) + 50}})
        renewal = await service.renew(old["id"], CustomPlanRenewRequest(), **mgr(rig))
    finally:
        await db.services.update_one({"_id": svc["_id"]}, {"$set": original})
    assert renewal["status"] == "draft" and renewal["renewal_of"] == old["id"] and renewal["id"] != old["id"]
    car_b = await vehicle_id_of(db, rig, rig["plate_b"])
    assert [c["vehicle_id"] for c in renewal["cars"]] == [rig["car_a"], car_b]
    assert [[(i["service_id"], i["count"]) for i in c["items"]] for c in renewal["cars"]] == [
        [(i["service_id"], i["count"]) for i in c["items"]] for c in old["cars"]
    ]
    # Priced fresh: higher than the old subtotal, no discount unless given.
    assert renewal["subtotal"] > old["subtotal"] and renewal["discount_amount"] == 0
    assert renewal["total_amount"] == renewal["subtotal"]
    # The old cart points at its renewal; it can't be renewed twice at once.
    assert (await service.view(await service.get(old["id"])))["renewal_cart_id"] == renewal["id"]
    with pytest.raises(BadRequestException, match="already"):
        await service.renew(old["id"], CustomPlanRenewRequest(), **mgr(rig))
    # Link it: the customer sees "Pay ₹X To Renew" and when the new period starts.
    sent = await service.send_link(renewal["id"], expected_revision=1, send_whatsapp=True, **mgr(rig))
    assert sent["custom_plan"]["status"] == "awaiting_payment"
    note = await db.notifications.find_one({"user_id": rig["customer"], "reference_id": renewal["id"]})
    assert note and "renew" in note["message"].lower()
    async with client() as http:
        r = await http.get("/api/v1/subscriptions/custom-plans/my", headers=auth(rig["customer"], "customer"))
    mine = {m["id"]: m for m in r.json()["data"]}
    row = mine[renewal["id"]]
    amount = f"{renewal['total_amount']:g}"
    assert row["renewal_of"] == old["id"] and row["pay_label"] == f"Pay ₹{amount} To Renew"
    old_a = await pass_of(db, old["id"], rig["car_a"])
    expected_start = _midnight_after_last_day(old_a).date()
    assert row["renewal_starts_on"] == expected_start.isoformat()
    assert row["renewal_starts_on_label"] == _label(expected_start)
    assert row["cars"][0]["starts_on"] == expected_start.isoformat()


async def test_renew_with_edited_cars_and_discount(db, rig, gateway):
    old = await activated_cash(db, rig)
    service = CustomPlanService(db)
    edited = [
        {"vehicle_id": rig["car_a"], "items": [{"service_id": rig["svc"]["deep-cleaning"], "count": 1}]},
        {"registration_number": rig["plate_c"], "vehicle_type": rig["hatch"], "items": [{"service_id": rig["svc"]["star-wash"], "count": 3}]},
    ]
    renewal = await service.renew(old["id"], CustomPlanRenewRequest(cars=edited, discount_amount=20, note="Renewed at the door"), **mgr(rig))
    expected = await _expected_total(db, [(rig["hatch"], [(rig["svc"]["deep-cleaning"], 1)]), (rig["hatch"], [(rig["svc"]["star-wash"], 3)])])
    assert renewal["subtotal"] == expected and renewal["discount_amount"] == 20 and renewal["total_amount"] == expected - 20
    assert renewal["car_count"] == 2 and renewal["note"] == "Renewed at the door"
    await service.cancel(renewal["id"], reason=None, **mgr(rig))
    # A car on ANOTHER live plan is refused (only the renewed pass may be live).
    await activated_cash(db, rig, car_list=[edited[1]])
    with pytest.raises(BadRequestException, match="already has an active plan"):
        await service.renew(old["id"], CustomPlanRenewRequest(cars=edited), **mgr(rig))
    # The manager discount cap still applies.
    with pytest.raises(BadRequestException, match="discount"):
        await service.renew(old["id"], CustomPlanRenewRequest(discount_amount=100000), **mgr(rig))


async def test_renewal_activation_schedules_live_cars_and_starts_others_today(db, rig, gateway):
    old = await activated_cash(db, rig)
    service = CustomPlanService(db)
    car_b = await vehicle_id_of(db, rig, rig["plate_b"])
    # Car A's old pass is live (10 days left); car B's old pass has ended.
    await db.user_subscriptions.update_one({"custom_plan_id": old["id"], "vehicle_id": rig["car_a"]},
                                           {"$set": {"end_date": now_ist() + timedelta(days=10)}})
    await db.user_subscriptions.update_one({"custom_plan_id": old["id"], "vehicle_id": car_b},
                                           {"$set": {"end_date": now_ist() - timedelta(days=1), "status": "expired"}})
    renewal = await service.renew(old["id"], CustomPlanRenewRequest(cars=[
        *cars(rig),
        {"registration_number": rig["plate_c"], "vehicle_type": rig["hatch"], "items": [{"service_id": rig["svc"]["star-wash"], "count": 1}]},
    ]), **mgr(rig))
    order = await paid(db, await link_order(db, rig, renewal))
    before = now_ist()
    result = await service.activate_from_payment(order)
    assert result["ok"] and result["activated"] == 3, result

    old_a = await pass_of(db, old["id"], rig["car_a"])
    new_a = await pass_of(db, renewal["id"], rig["car_a"])
    start = _midnight_after_last_day(old_a)
    assert new_a["status"] == PASS_SCHEDULED
    assert from_stored(new_a["start_date"]) == start
    assert from_stored(new_a["end_date"]) == start + timedelta(days=30)
    assert new_a["renewal_of_subscription_id"] == str(old_a["_id"])
    # The old pass keeps its washes and its period; it knows its successor.
    assert old_a["status"] == "active" and old_a["remaining_service_count"] == 4
    assert old_a["renewed_by_subscription_id"] == str(new_a["_id"])
    # One claim for the car, now held by the new pass; both still block a sale.
    claim = await db.pass_claims.find_one({"_id": f"vehicle:{rig['customer']}:{rig['car_a']}"})
    assert claim["subscription_id"] == str(new_a["_id"])
    assert pass_blocks_new(old_a) and pass_blocks_new(new_a)
    # Cars without a live old pass start today.
    car_c = await vehicle_id_of(db, rig, rig["plate_c"])
    for vid in (car_b, car_c):
        p = await pass_of(db, renewal["id"], vid)
        assert p["status"] == "active" and from_stored(p["start_date"]) >= before - timedelta(seconds=5)
        assert from_stored(p["end_date"]) - from_stored(p["start_date"]) == timedelta(days=30)
    # The scheduled pass can't be booked yet; the old one still can.
    subs = UserSubscriptionService(db)
    star = await db.services.find_one({"_id": ObjectId(rig["svc"]["star-wash"])})
    with pytest.raises(BadRequestException, match=_label(start)):
        await subs.plan_consumption(str(new_a["_id"]), rig["car_a"], [star], rig["customer"])
    assert (await subs.plan_consumption(str(old_a["_id"]), rig["car_a"], [star], rig["customer"]))["by_service"]
    # A new plan for car A is refused (both periods hold the car).
    with pytest.raises(BadRequestException, match="already has an active plan"):
        await CustomPlanService(db).preview(CustomPlanPreviewRequest(
            customer_id=rig["customer"], cars=[{"vehicle_id": rig["car_a"], "items": [{"service_id": rig["svc"]["star-wash"], "count": 1}]}],
        ), actor_role="manager", actor_center_id=rig["center"])
    # The cart: active, its period from the earliest start to the latest end.
    view = await service.view(await service.get(renewal["id"]))
    assert view["status"] == "active"
    row_a = next(c for c in view["cars"] if c["vehicle_id"] == rig["car_a"])
    assert row_a["starts_on"] == start.date().isoformat() and row_a["subscription"]["status"] == PASS_SCHEDULED
    # Customer heard once (subscription_activated), with the start date.
    note = await db.notifications.find_one({"user_id": rig["customer"], "reference_id": renewal["id"], "title": {"$regex": "renewed"}})
    assert note and _label(start) in note["message"]


async def test_renewal_after_an_extension_starts_after_the_extension(db, rig, gateway):
    old = await activated_cash(db, rig)
    service = CustomPlanService(db)
    old_a = await pass_of(db, old["id"], rig["car_a"])
    await db.user_subscriptions.update_one({"_id": old_a["_id"]}, {"$set": {"end_date": now_ist() + timedelta(days=1)}})
    await UserSubscriptionService(db).extend_pass(str(old_a["_id"]), 5, note=None, actor_id=rig["manager"], actor_role="manager", actor_center_id=rig["center"])
    renewal = await service.renew(old["id"], CustomPlanRenewRequest(), **mgr(rig))
    await service.mark_cash_paid(renewal["id"], expected_revision=1, note=None, **mgr(rig))
    old_a = await db.user_subscriptions.find_one({"_id": old_a["_id"]})
    new_a = await pass_of(db, renewal["id"], rig["car_a"])
    assert from_stored(new_a["start_date"]) == _midnight_after_last_day(old_a)
    assert from_stored(new_a["start_date"]).date() == from_stored(old_a["extended_until"]).date()
    # Once a renewal is lined up, the old pass can't be stretched over it.
    with pytest.raises(BadRequestException, match="renewal"):
        await UserSubscriptionService(db).extend_pass(str(old_a["_id"]), 1, note=None, actor_id=rig["manager"], actor_role="manager", actor_center_id=rig["center"])


async def test_scheduled_pass_promoted_by_sweep_and_lazily(db, rig, gateway):
    old = await activated_cash(db, rig)
    service = CustomPlanService(db)
    renewal = await service.renew(old["id"], CustomPlanRenewRequest(), **mgr(rig))
    await service.mark_cash_paid(renewal["id"], expected_revision=1, note=None, **mgr(rig))
    car_b = await vehicle_id_of(db, rig, rig["plate_b"])
    new_a, new_b = await pass_of(db, renewal["id"], rig["car_a"]), await pass_of(db, renewal["id"], car_b)
    assert new_a["status"] == new_b["status"] == PASS_SCHEDULED
    # The old period ends: car A's old pass is over and the new start is past.
    past = now_ist() - timedelta(hours=1)
    for p in (new_a, new_b):
        await db.user_subscriptions.update_one({"_id": p["_id"]}, {"$set": {"start_date": past, "end_date": past + timedelta(days=30)}})
    # Lazily: the first booking promotes it (no sweep needed at night).
    star = await db.services.find_one({"_id": ObjectId(rig["svc"]["star-wash"])})
    plan = await UserSubscriptionService(db).plan_consumption(str(new_a["_id"]), rig["car_a"], [star], rig["customer"])
    assert plan["by_service"]
    assert (await db.user_subscriptions.find_one({"_id": new_a["_id"]}))["status"] == "active"
    # The sweep promotes the other one; running it again changes nothing.
    await find_subscriptions_ended(db)
    await find_subscriptions_ended(db)
    assert (await db.user_subscriptions.find_one({"_id": new_b["_id"]}))["status"] == "active"
    # Views read it as active even before the stored flip.
    await db.user_subscriptions.update_one({"_id": new_b["_id"]}, {"$set": {"status": PASS_SCHEDULED}})
    mine = await UserSubscriptionService(db).list_my_subscriptions(rig["customer"])
    row = next(s for s in mine if s["id"] == str(new_b["_id"]))
    assert row["effective_status"] == "active"


async def test_renewal_activation_racing_verify_webhook_and_sweep(db, rig, gateway):
    old = await activated_cash(db, rig)
    service = CustomPlanService(db)
    renewal = await service.renew(old["id"], CustomPlanRenewRequest(), **mgr(rig))
    order = await paid(db, await link_order(db, rig, renewal))
    results = await asyncio.gather(*[CustomPlanService(db).activate_from_payment(order) for _ in range(4)])
    assert all(r["ok"] for r in results), results
    assert len([r for r in results if not r.get("already")]) == 1
    assert await db.user_subscriptions.count_documents({"custom_plan_id": renewal["id"]}) == 2
    new_a = await pass_of(db, renewal["id"], rig["car_a"])
    claim = await db.pass_claims.find_one({"_id": f"vehicle:{rig['customer']}:{rig['car_a']}"})
    assert claim["subscription_id"] == str(new_a["_id"])
    # And cash racing a late webhook on the same renewal changes nothing.
    again = await service.activate_from_payment(order)
    assert again["ok"] and again["already"]
    assert await db.user_subscriptions.count_documents({"custom_plan_id": renewal["id"]}) == 2


async def test_two_renewal_carts_never_double_schedule_a_car(db, rig, gateway):
    """A second renewal (made after the first was cancelled, or forced in)
    can never put two passes on the car's next period."""
    old = await activated_cash(db, rig)
    service = CustomPlanService(db)
    first = await service.renew(old["id"], CustomPlanRenewRequest(), **mgr(rig))
    await service.mark_cash_paid(first["id"], expected_revision=1, note=None, **mgr(rig))
    # Forge a second renewal of the same old cart (bypassing the one-open rule).
    raw = await service.get(first["id"])
    raw.pop("_id")
    clone = {**raw, "status": "draft", "payment": None, "cars": [{**c, "status": "pending", "subscription_id": None} for c in raw["cars"]],
             "link_order_ids": []}
    clone.pop("period_start", None)
    clone.pop("period_end", None)
    ins = await db.custom_plans.insert_one(clone)
    with pytest.raises(BadRequestException, match="already renewed"):
        await service.mark_cash_paid(str(ins.inserted_id), expected_revision=1, note=None, **mgr(rig))
    # Even a payment that lands on it anyway (no pre-check): every car is
    # skipped at activation — the old pass already names its successor.
    order = {"_id": ObjectId(), "kind": "link", "custom_plan_id": str(ins.inserted_id), "custom_plan_revision": 1,
             "amount_paise": int(round(clone["total_amount"] * 100))}
    result = await service.activate_from_payment(order)
    assert not result["ok"] and result["activated"] == 0 and len(result["skipped"]) == 2
    assert (await service.get(str(ins.inserted_id)))["status"] == "needs_review"
    assert await db.user_subscriptions.count_documents({"vehicle_id": rig["car_a"], "status": {"$in": ["active", PASS_SCHEDULED]}}) == 2


async def test_cancelling_a_renewal_frees_the_old_cart(db, rig, gateway):
    old = await activated_cash(db, rig)
    service = CustomPlanService(db)
    renewal = await service.renew(old["id"], CustomPlanRenewRequest(), **mgr(rig))
    await service.cancel(renewal["id"], reason="customer changed mind", **mgr(rig))
    assert (await service.view(await service.get(old["id"])))["renewal_cart_id"] is None
    again = await service.renew(old["id"], CustomPlanRenewRequest(), **mgr(rig))
    assert again["renewal_of"] == old["id"]


async def test_renew_access_and_states_over_http(db, rig, gateway):
    service = CustomPlanService(db)
    unpaid = await service.create(CustomPlanCreateRequest(customer_id=rig["customer"], cars=cars(rig)), **mgr(rig))
    async with client() as http:
        r = await http.post(f"/api/v1/subscriptions/custom-plans/{unpaid['id']}/renew", json={}, headers=auth(rig["manager"], "manager", rig["center"]))
        assert r.status_code == 400, r.text  # only a paid plan renews
    await service.cancel(unpaid["id"], reason=None, **mgr(rig))
    old = await activated_cash(db, rig)
    path = f"/api/v1/subscriptions/custom-plans/{old['id']}/renew"
    async with client() as http:
        r = await http.post(path, json={}, headers=auth(rig["customer"], "customer"))
        assert r.status_code == 403, r.text
        r = await http.post(path, json={}, headers=auth(rig["other_manager"], "manager", rig["other"]))
        assert r.status_code == 403, r.text
        r = await http.post(path, json={}, headers=auth(rig["admin"], "admin"))
        assert r.status_code == 200, r.text
        body = r.json()["data"]
        assert body["renewal_of"] == old["id"] and body["status"] == "draft"
    assert await db.audit_logs.find_one({"action": "RENEW_CUSTOM_PLAN", "target_id": body["id"]})
