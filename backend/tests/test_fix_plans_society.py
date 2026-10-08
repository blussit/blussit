"""Remediation pass 2026-10-07 (PLANS) — society regressions: SOC-1 (society
revenue in the KPIs), SOC-2 (rate-card versioned auto plans), SOC-3 (is_active
admin-only, refused while plans are live; hub stays open), SOC-4 (a car-bound
pass never pays a type-only booking), SOC-5 (one plate, one society pass under
concurrency), SOC-6 (one society per lead), SOC-7 (resident told when staff
cancel a request), SOC-8 (hub link shown when the form is off), SOC-9 (lapsed
residents not counted), SOC-11 (refund trail when an online-paid plan is
cancelled). Local Mongo only."""
import asyncio
import itertools
from datetime import datetime, timedelta, timezone

import pytest
from bson import ObjectId

from app.core.exceptions import AppException, BadRequestException
from app.schemas.booking_schema import BookingCreateRequest
from app.schemas.society_schema import CustomCombo, RateCardRequest, SocietyEnrollRequest, SocietyQuoteRequest
from app.services.booking_service import BookingService
from app.services.society_service import SocietyService
from app.services.subscription_service import UserSubscriptionService
from app.utils.timezone import now_ist

from tests.factories import get_hatchback_type_id, get_star_wash_service_id, make_address, make_service_center
from tests.society_factories import (
    activate_cash, auth, client, enroll, make_admin, make_center, make_resident, make_society, make_template, plate, staff,
)

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
    center = await make_center(db, cleanup, spot=3)
    other = await make_center(db, cleanup, spot=4)
    manager_id, captain_id = await staff(db, cleanup, center["id"])
    other_manager, _ = await staff(db, cleanup, other["id"])
    society = await make_society(db, cleanup, center, name=f"FixPlans Court {next(_seq)}")
    plan = await make_template(db, cleanup)
    admin_id = await make_admin(db, cleanup)
    return {"center": center, "other": other, "manager": manager_id, "captain": captain_id, "other_manager": other_manager,
            "society": society, "plan": plan, "admin": admin_id}


async def _active(db, cleanup, rig, cars=1):
    resident = await make_resident(db, cleanup)
    view = await enroll(db, rig["society"], resident, rig["plan"]["id"], cars=cars)
    return resident, (await activate_cash(db, view["id"]))["enrollment"]


def _day(n: int) -> str:
    return (now_ist().date() + timedelta(days=n)).isoformat()


# ---------------------------------------------------------------------------
# SOC-5 — one plate, one society pass
# ---------------------------------------------------------------------------


async def test_soc5_same_plate_two_accounts_concurrent_activation(db, cleanup, rig):
    service = SocietyService(db)
    hatch = await get_hatchback_type_id(db)
    p = plate()
    raw = await service.societies.find_by_id(rig["society"]["id"])
    views = []
    for _ in range(2):
        resident = await make_resident(db, cleanup)
        payload = SocietyEnrollRequest(plan_id=rig["plan"]["id"], resident_name="Twin", phone=resident["phone"], flat=f"C-{next(_seq)}",
                                       cars=[{"vehicle_type": hatch, "registration_number": p}])
        views.append(await service.enroll(raw, payload, resident, source="form"))

    async def act(v):
        try:
            r = await SocietyService(db).activate(await SocietyService(db).get_enrollment(v["id"]), method="cash", actor_id=rig["manager"])
            return r["activated"]
        except AppException:
            return 0

    activated = await asyncio.gather(*[act(v) for v in views])
    vids = [v["cars"][0]["vehicle_id"] for v in views]
    live = await db.user_subscriptions.count_documents({"vehicle_id": {"$in": vids}, "status": "active", "society_id": rig["society"]["id"]})
    assert live == 1, activated
    assert sum(activated) == 1


async def test_soc5_same_car_double_activation_one_pass(db, cleanup, rig):
    """One enrollment marked paid twice at once (and a second enrollment for
    the same car racing it) -> the car carries one live pass."""
    resident = await make_resident(db, cleanup)
    hatch = await get_hatchback_type_id(db)
    p = plate()
    service = SocietyService(db)
    raw = await service.societies.find_by_id(rig["society"]["id"])
    v1 = await service.enroll(raw, SocietyEnrollRequest(plan_id=rig["plan"]["id"], resident_name="Solo", phone=resident["phone"], flat="A-1",
                                                       cars=[{"vehicle_type": hatch, "registration_number": p}]), resident, source="form")
    other_society = await make_society(db, cleanup, rig["center"], name=f"Twin Court {next(_seq)}")
    raw2 = await service.societies.find_by_id(other_society["id"])
    v2 = await service.enroll(raw2, SocietyEnrollRequest(plan_id=rig["plan"]["id"], resident_name="Solo", phone=resident["phone"], flat="A-2",
                                                        cars=[{"vehicle_type": hatch, "registration_number": p}]), resident, source="form")

    async def act(v):
        try:
            r = await SocietyService(db).activate(await SocietyService(db).get_enrollment(v["id"]), method="cash", actor_id=rig["manager"])
            return r["activated"]
        except AppException:
            return 0

    await asyncio.gather(act(v1), act(v1), act(v2))
    live = await db.user_subscriptions.count_documents({"customer_id": str(resident["_id"]), "vehicle_id": v1["cars"][0]["vehicle_id"], "status": "active"})
    assert live == 1


# ---------------------------------------------------------------------------
# SOC-2 — rate card versions the auto plans
# ---------------------------------------------------------------------------


async def test_soc2_rate_card_change_prices_new_enrolments_not_existing(db, cleanup, rig):
    service = SocietyService(db)
    star = await get_star_wash_service_id(db)
    hatch = await get_hatchback_type_id(db)
    raw = await service.societies.find_by_id(rig["society"]["id"])
    saved = await db.society_settings.find_one({"_id": "rate_card"})
    card = await service.rate_card()
    combo = CustomCombo(bucket_days=card["bucket_day_options"][0], premium_service_id=star, premium_count=card["premium_count_options"][0])
    try:
        first = await make_resident(db, cleanup)
        v = await service.enroll(raw, SocietyEnrollRequest(custom=combo, resident_name="Early", phone=first["phone"], flat="D-1",
                                                          cars=[{"vehicle_type": hatch, "registration_number": plate()}]), first, source="form")
        old_price = v["cars"][0]["price"]
        old_plan_id = v["plan_id"]
        active = (await activate_cash(db, v["id"]))["enrollment"]
        await service.save_rate_card(RateCardRequest(
            bucket_day_price={"default": 100.0, "by_type": {}}, bucket_day_mrp={"default": 120.0, "by_type": {}},
            premium_discount_percent=0, bucket_day_options=card["bucket_day_options"], premium_count_options=card["premium_count_options"],
            premium_service_ids=card["premium_service_ids"], allow_customise=True), "admin")
        quoted = await service.quote(raw, SocietyQuoteRequest(custom=combo, vehicle_types=[hatch]), [hatch], None)
        assert quoted["total"] > old_price
        # A new resident choosing the same combination pays the new price.
        second = await make_resident(db, cleanup)
        v2 = await service.enroll(raw, SocietyEnrollRequest(custom=combo, resident_name="Late", phone=second["phone"], flat="D-2",
                                                           cars=[{"vehicle_type": hatch, "registration_number": plate()}]), second, source="form")
        assert v2["cars"][0]["price"] == quoted["total"]
        assert v2["plan_id"] != old_plan_id
        # Picking the OLD auto plan by id (a stale page) also gets the new price.
        third = await make_resident(db, cleanup)
        v3 = await service.enroll(raw, SocietyEnrollRequest(plan_id=old_plan_id, resident_name="Stale", phone=third["phone"], flat="D-3",
                                                           cars=[{"vehicle_type": hatch, "registration_number": plate()}]), third, source="form")
        assert v3["cars"][0]["price"] == quoted["total"]
        # The form lists one version of that combination — the current one.
        form = await service.public_form(raw, None)
        auto = [p for p in form["plans"] if p["auto_created"] and p["bucket_days"] == combo.bucket_days and p["premium_count"] == combo.premium_count]
        assert len(auto) == 1 and auto[0]["prices"][hatch]["price"] == quoted["total"]
        # The existing resident keeps renewing at the price they joined at.
        sub_id = active["cars"][0]["subscription"]["id"]
        await db.user_subscriptions.update_one({"_id": ObjectId(sub_id)}, {"$set": {"end_date": now_ist() + timedelta(days=1)}})
        renewed = await service.renew(await service.get_enrollment(active["id"]), method="cash", actor_id=rig["manager"])
        assert renewed["amount"] == old_price
    finally:
        if saved:
            await db.society_settings.replace_one({"_id": "rate_card"}, saved, upsert=True)
        else:
            await db.society_settings.delete_one({"_id": "rate_card"})


# ---------------------------------------------------------------------------
# SOC-3 — switching a society off
# ---------------------------------------------------------------------------


async def test_soc3_is_active_is_admin_only_and_refused_while_plans_are_live(db, cleanup, rig):
    resident, enr = await _active(db, cleanup, rig)
    sid = rig["society"]["id"]
    async with client() as c:
        r = await c.put(f"/api/v1/societies/{sid}", json={"is_active": False}, headers=auth(rig["manager"], "manager", rig["center"]["id"]))
        assert r.status_code == 403, r.text
        r = await c.put(f"/api/v1/societies/{sid}", json={"is_active": False}, headers=auth(rig["admin"], "admin"))
        assert r.status_code == 409, r.text
        assert "live" in r.json()["message"].lower()
        # Other edits still work for the manager.
        r = await c.put(f"/api/v1/societies/{sid}", json={"notes": "Gate 2"}, headers=auth(rig["manager"], "manager", rig["center"]["id"]))
        assert r.status_code == 200, r.text
    assert (await db.societies.find_one({"_id": ObjectId(sid)}))["is_active"] is True
    # Once nobody holds a live plan, the admin can switch it off.
    await SocietyService(db).cancel(await SocietyService(db).get_enrollment(enr["id"]), vehicle_ids=None, actor_id=rig["admin"], reason="moved")
    async with client() as c:
        r = await c.put(f"/api/v1/societies/{sid}", json={"is_active": False}, headers=auth(rig["admin"], "admin"))
        assert r.status_code == 200, r.text


async def test_soc3_switched_off_society_keeps_hub_open_for_live_residents(db, cleanup, rig):
    resident, enr = await _active(db, cleanup, rig)
    # A society switched off before this rule existed (legacy data).
    await db.societies.update_one({"_id": ObjectId(rig["society"]["id"])}, {"$set": {"is_active": False}})
    token = rig["society"]["form_token"]
    async with client() as c:
        me = await c.get(f"/api/v1/society-forms/{token}/me", headers=auth(str(resident["_id"]), "customer"))
        assert me.status_code == 200, me.text
        stranger = await make_resident(db, cleanup)
        other = await c.get(f"/api/v1/society-forms/{token}/me", headers=auth(str(stranger["_id"]), "customer"))
        assert other.status_code == 404
        anon = await c.get(f"/api/v1/society-forms/{token}")
        assert anon.status_code == 404


# ---------------------------------------------------------------------------
# SOC-4 — a car-bound pass pays only for that car
# ---------------------------------------------------------------------------


async def test_soc4_car_bound_pass_never_pays_a_type_only_booking(db, cleanup, rig):
    resident, enr = await _active(db, cleanup, rig)
    sub_id = enr["cars"][0]["subscription"]["id"]
    rid = str(resident["_id"])
    hatch = await get_hatchback_type_id(db)
    star = await get_star_wash_service_id(db)
    subs = UserSubscriptionService(db)
    star_doc = await db.services.find_one({"_id": ObjectId(star)})
    with pytest.raises(BadRequestException):
        await subs.plan_consumption(sub_id, None, [star_doc], rid, vehicle_type=hatch)
    # A type-only booking elsewhere: never spends the society pass.
    other_center = await make_service_center(db)
    address_id = await make_address(db, rid)
    # A leaked center on the shared test pincode would capture later tests'
    # addresses (they'd resolve to it and fail center-access checks).
    cleanup.append(("service_centers", {"_id": ObjectId(other_center)}))
    cleanup.append(("bookings", {"service_center_id": other_center}))
    cleanup.append(("slot_capacity", {"service_center_id": other_center}))
    cleanup.append(("daily_capacity", {"service_center_id": other_center}))
    cleanup.append(("addresses", {"_id": ObjectId(address_id)}))
    bs = BookingService(db)
    when = _day(2)
    slots = await bs.available_slots(other_center, when)
    slot = next(s["key"] for s in slots if s["status"] == "available")
    try:
        b = await bs.create_self_service_booking(rid, BookingCreateRequest(vehicle_type=hatch, address_id=address_id, service_ids=[star], scheduled_date=when, scheduled_slot=slot))
        raw = await db.bookings.find_one({"_id": ObjectId(b["id"])})
        assert raw.get("subscription_id") != sub_id
    except BadRequestException:
        pass
    sub = await db.user_subscriptions.find_one({"_id": ObjectId(sub_id)})
    assert sub["remaining_service_count"] == sub["total_service_count"]
    # The named car itself still books with it.
    consumption = await subs.plan_consumption(sub_id, sub["vehicle_id"], [star_doc], rid, vehicle_type=hatch)
    assert consumption == {"flat_count": 1}


async def test_soc4_car_bound_pass_doesnt_block_a_type_pass_for_another_car(db, cleanup, rig):
    """The society pass pays only for its own car — so the resident can buy
    a normal hatchback Star Wash pass for their OTHER hatchback."""
    from app.schemas.subscription_schema import SubscribeRequest
    from tests.factories import make_subscription_plan

    resident, enr = await _active(db, cleanup, rig)
    rid = str(resident["_id"])
    hatch = await get_hatchback_type_id(db)
    star = await get_star_wash_service_id(db)
    plan_id = await make_subscription_plan(db, vehicle_types=[hatch], included_service_ids=[star], total_service_count=2)
    subs = UserSubscriptionService(db)
    assert await subs.has_active_pass(rid, hatch, star) is False
    bought = await subs.subscribe(rid, SubscribeRequest(plan_id=plan_id, vehicle_type=hatch, service_id=star))
    assert bought["status"] == "active" and not bought.get("vehicle_id")
    assert await subs.has_active_pass(rid, hatch, star) is True


# ---------------------------------------------------------------------------
# SOC-6 — one society per lead
# ---------------------------------------------------------------------------


async def test_soc6_concurrent_registration_from_one_lead(db, cleanup, rig):
    from app.schemas.society_schema import SocietyLeadRequest
    from app.services.society_support_service import SocietyLeadService

    phone = f"98{next(_seq):08d}"
    name = f"Dup Towers {next(_seq)}"
    await SocietyLeadService(db).capture(SocietyLeadRequest(society_name=name, area="Vijay Nagar", pincode=rig["center"]["pincode"], contact_name="Sec", phone=phone, approx_cars=40))
    lead = await db.society_leads.find_one({"phone": phone})
    cleanup.append(("society_leads", {"_id": lead["_id"]}))
    body = {"name": name, "address_line": "1 Road", "pincode": rig["center"]["pincode"], "latitude": rig["center"]["lat"] + 0.001,
            "longitude": rig["center"]["lng"] + 0.001, "lead_id": str(lead["_id"])}
    async with client() as c:
        h = auth(rig["manager"], "manager", rig["center"]["id"])
        rs = await asyncio.gather(*[c.post("/api/v1/societies", json=body, headers=h) for _ in range(3)])
        again = await c.post("/api/v1/societies", json=body, headers=h)
    found = await db.societies.find({"name": name}).to_list(10)
    for s in found:
        cleanup.append(("societies", {"_id": s["_id"]}))
    assert len(found) == 1, [r.status_code for r in rs]
    assert sorted(r.status_code for r in rs) == [200, 409, 409]
    assert again.status_code == 409
    lead = await db.society_leads.find_one({"_id": lead["_id"]})
    assert lead["status"] == "registered" and lead["society_id"] == str(found[0]["_id"])


async def test_soc6_failed_registration_gives_the_lead_back(db, cleanup, rig):
    from app.schemas.society_schema import SocietyLeadRequest
    from app.services.society_support_service import SocietyLeadService

    phone = f"98{next(_seq):08d}"
    await SocietyLeadService(db).capture(SocietyLeadRequest(society_name="Far Towers", area="Nowhere", pincode=rig["center"]["pincode"], contact_name="Sec", phone=phone, approx_cars=10))
    lead = await db.society_leads.find_one({"phone": phone})
    cleanup.append(("society_leads", {"_id": lead["_id"]}))
    # A pin served by another center -> refused, lead untouched.
    body = {"name": "Far Towers", "address_line": "1 Road", "pincode": rig["other"]["pincode"], "latitude": rig["other"]["lat"] + 0.001,
            "longitude": rig["other"]["lng"] + 0.001, "lead_id": str(lead["_id"])}
    async with client() as c:
        r = await c.post("/api/v1/societies", json=body, headers=auth(rig["manager"], "manager", rig["center"]["id"]))
    assert r.status_code == 400
    assert (await db.society_leads.find_one({"_id": lead["_id"]}))["status"] == "new"


# ---------------------------------------------------------------------------
# SOC-7 / SOC-8 / SOC-9
# ---------------------------------------------------------------------------


async def test_soc7_resident_is_told_when_staff_cancel_their_request(db, cleanup, rig):
    resident = await make_resident(db, cleanup)
    view = await enroll(db, rig["society"], resident, rig["plan"]["id"], cars=1)
    before = await db.notifications.count_documents({"user_id": str(resident["_id"])})
    async with client() as c:
        r = await c.post(f"/api/v1/society-enrollments/{view['id']}/cancel", json={"reason": "not serviceable"}, headers=auth(rig["manager"], "manager", rig["center"]["id"]))
    assert r.status_code == 200, r.text
    rows = await db.notifications.find({"user_id": str(resident["_id"])}).sort("created_at", -1).to_list(10)
    assert len(rows) == before + 1
    assert "not serviceable" in rows[0]["message"]


async def test_soc7_resident_withdrawing_is_not_told_twice(db, cleanup, rig):
    resident = await make_resident(db, cleanup)
    view = await enroll(db, rig["society"], resident, rig["plan"]["id"], cars=1)
    before = await db.notifications.count_documents({"user_id": str(resident["_id"])})
    async with client() as c:
        r = await c.post(f"/api/v1/society-forms/{rig['society']['form_token']}/me/enrollments/{view['id']}/withdraw", headers=auth(str(resident["_id"]), "customer"))
    assert r.status_code == 200, r.text
    assert await db.notifications.count_documents({"user_id": str(resident["_id"])}) == before


async def test_soc8_hub_link_kept_when_the_form_is_off(db, cleanup, rig):
    resident, enr = await _active(db, cleanup, rig)
    async with client() as c:
        r = await c.put(f"/api/v1/societies/{rig['society']['id']}", json={"form_enabled": False}, headers=auth(rig["manager"], "manager", rig["center"]["id"]))
        assert r.status_code == 200
        me = await c.get(f"/api/v1/society-forms/{rig['society']['form_token']}/me", headers=auth(str(resident["_id"]), "customer"))
        mine = await c.get("/api/v1/subscriptions/my", headers=auth(str(resident["_id"]), "customer"))
    assert me.status_code == 200
    paths = [s.get("society_form_path") for s in mine.json()["data"] if s.get("society_id")]
    assert paths and all(p == f"/society/{rig['society']['form_token']}" for p in paths)


async def test_soc9_lapsed_residents_are_not_counted(db, cleanup, rig):
    resident, enr = await _active(db, cleanup, rig, cars=1)
    live_resident, _ = await _active(db, cleanup, rig, cars=2)
    sub_id = enr["cars"][0]["subscription"]["id"]
    await db.user_subscriptions.update_one({"_id": ObjectId(sub_id)}, {"$set": {"end_date": now_ist() - timedelta(days=40), "status": "expired"}})
    lst = await SocietyService(db).list_societies(center_id=rig["center"]["id"])
    row = [r for r in lst["rows"] if r["id"] == rig["society"]["id"]][0]
    assert row["residents"] == 1
    assert row["active_cars"] == 2
    detail = await SocietyService(db).society_detail(await SocietyService(db).get_society(rig["society"]["id"]))
    assert detail["residents"] == 1


# ---------------------------------------------------------------------------
# SOC-11 — refund trail when an online-paid plan is cancelled
# ---------------------------------------------------------------------------


async def test_soc11_cancelling_an_online_paid_plan_flags_a_refund(db, cleanup, rig):
    resident, enr = await _active(db, cleanup, rig, cars=2)
    rid = str(resident["_id"])
    sub_ids = [c["subscription"]["id"] for c in enr["cars"]]
    # As if it had been paid online through a Razorpay order.
    order_id = (await db.payment_orders.insert_one({
        "purpose": "society", "kind": "order", "status": "paid", "customer_id": rid, "razorpay_order_id": f"order_soc11_{next(_seq)}",
        "razorpay_payment_id": f"pay_soc11_{next(_seq)}", "amount_paise": 329800, "society_enrollment_id": enr["id"], "created_at": now_ist(),
    })).inserted_id
    order = await db.payment_orders.find_one({"_id": order_id})
    await db.user_subscriptions.update_many({"_id": {"$in": [ObjectId(s) for s in sub_ids]}}, {"$set": {"payment_method": "online"}})
    await db.society_enrollments.update_one({"_id": ObjectId(enr["id"])}, {"$set": {"payment.method": "online", "payment.order_id": str(order_id)}})
    await db.society_payments.update_many({"enrollment_id": enr["id"]}, {"$set": {"method": "online", "razorpay_order_id": order["razorpay_order_id"]}})
    async with client() as c:
        r = await c.post(f"/api/v1/society-enrollments/{enr['id']}/cancel", json={"vehicle_ids": [enr["cars"][0]["vehicle_id"]], "reason": "moved out"},
                         headers=auth(rig["manager"], "manager", rig["center"]["id"]))
        assert r.status_code == 200, r.text
        # A retried cancel of the rest + the same car never doubles a row.
        r = await c.post(f"/api/v1/society-enrollments/{enr['id']}/cancel", json={"reason": "moved out"}, headers=auth(rig["manager"], "manager", rig["center"]["id"]))
        assert r.status_code == 200, r.text
    rows = await db.payment_orders.find({"customer_id": rid, "kind": "refund_due"}).to_list(10)
    assert len(rows) == 2
    paid = {s["_id"]: s for s in await db.user_subscriptions.find({"_id": {"$in": [ObjectId(s) for s in sub_ids]}}).to_list(5)}
    assert sorted(r["amount_paise"] for r in rows) == sorted(int(round(s["amount_paid"] * 100)) for s in paid.values())
    assert all(r["razorpay_payment_id"] == order["razorpay_payment_id"] for r in rows)
    assert all(r["status"] == "paid_attention" for r in rows)


async def test_soc11_cash_paid_cancellation_flags_nothing(db, cleanup, rig):
    resident, enr = await _active(db, cleanup, rig, cars=1)
    await SocietyService(db).cancel(await SocietyService(db).get_enrollment(enr["id"]), vehicle_ids=None, actor_id=rig["manager"], reason="moved")
    assert await db.payment_orders.count_documents({"customer_id": str(resident["_id"]), "kind": "refund_due"}) == 0


# ---------------------------------------------------------------------------
# SOC-1 — society revenue in the KPIs
# ---------------------------------------------------------------------------


async def test_soc1_society_revenue_is_its_own_line(db, cleanup, rig):
    from app.services.kpi_service import KpiService
    from app.services.manager_dashboard_service import ManagerDashboardService

    now = now_ist()
    s, e = now - timedelta(hours=2), now + timedelta(hours=2)
    ps, pe = s - timedelta(days=1), s
    kpi = KpiService(db)
    before = (await kpi.overview(s, e, ps, pe))["current"]
    resident, enr = await _active(db, cleanup, rig, cars=1)
    cash = enr["payment"]["amount"]
    rid = str(resident["_id"])
    # An online society payment for this center's society (a resident's
    # checkout applied through the shared claim: on_order_paid -> activate).
    online_resident = await make_resident(db, cleanup)
    online_view = await enroll(db, rig["society"], online_resident, rig["plan"]["id"], cars=1)
    paid = await SocietyService(db).activate(
        await SocietyService(db).get_enrollment(online_view["id"]), method="online", actor_id=None,
        order={"_id": ObjectId(), "razorpay_order_id": f"order_soc1_{next(_seq)}"},
    )
    online = paid["enrollment"]["payment"]["amount"]
    # A paid online order that could NOT be applied is parked, not revenue.
    await db.payment_orders.insert_one({
        "purpose": "society", "kind": "order", "status": "paid_attention", "customer_id": rid, "amount_paise": 50000,
        "society_enrollment_id": enr["id"], "created_at": now, "paid_at": now, "razorpay_order_id": f"order_soc1_{next(_seq)}",
    })
    # Another center's society cash must not leak into this center.
    other_society = await make_society(db, cleanup, rig["other"], name=f"Other Court {next(_seq)}")
    other_resident = await make_resident(db, cleanup)
    other_view = await enroll(db, other_society, other_resident, rig["plan"]["id"], cars=1)
    other_cash = (await activate_cash(db, other_view["id"]))["enrollment"]["payment"]["amount"]

    after = (await kpi.overview(s, e, ps, pe))["current"]
    assert after["society_revenue"] - before["society_revenue"] == pytest.approx(cash + online + other_cash)
    assert after["combined_revenue"] - before["combined_revenue"] == pytest.approx(cash + online + other_cash)
    assert after["plan_revenue"] == before["plan_revenue"]

    mgr = (await kpi.manager_overview(rig["center"]["id"], s, e, ps, pe))["current"]
    assert mgr["society_revenue"] == pytest.approx(cash + online)
    assert mgr["combined_revenue"] == pytest.approx(mgr["revenue"] + mgr["plan_revenue"] + cash + online)
    dash = await ManagerDashboardService(db).dashboard(rig["center"]["id"], s, e, ps, pe)
    assert dash["plans"]["society"]["revenue"] == pytest.approx(cash + online)
    assert dash["plans"]["society"]["online_amount"] == pytest.approx(online)
    assert dash["plans"]["society"]["cash_amount"] == pytest.approx(cash)
    other_mgr = (await kpi.manager_overview(rig["other"]["id"], s, e, ps, pe))["current"]
    assert other_mgr["society_revenue"] == pytest.approx(other_cash)
    await db.payment_orders.delete_many({"customer_id": rid})
