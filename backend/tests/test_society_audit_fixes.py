"""
Society audit fixes (2026-10-06) — one regression test (or a pair) per
verified finding, each failing on the code before its fix:

  SOC-1  a resident with two enrollments is ONE booking visit (and sits a
         society day out when any of their cars is booked that day)
  SOC-2  a society is always filed under its pin's center
  SOC-3  a stale slot / a crashing day never starves the sweep
  SOC-4  early renewal keeps room for washes already booked
  SOC-5  one plate, one live society pass — across accounts
  SOC-6  one renewal payment renews one cycle (a second is parked)
  SOC-7  two visit days of one society on one date never double-book
  SOC-8  switching the form off keeps residents' hubs open
  SOC-9  cancelling a plan tells the resident
  SOC-10 a premium wash must fall inside the plan month
  SOC-11 staff can price a resident's personal plan
  SOC-15 cancelling a request that was just paid is refused
  SOC-16 cancelled cars leave their repeat wash
  SOC-17 a paid WhatsApp link confirms once
  AUTH-8 staff "add resident" never retypes / rewrites the account's data,
         and a phone lookup shows only this society's personal plans
  AUTH-12 a captain moved to another center loses the old societies
  and: approving a change request validates the move before touching the day
"""
import asyncio
import itertools
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from bson import ObjectId

from app.core.exceptions import BadRequestException, ForbiddenException, NotFoundException
from app.schemas.booking_schema import BookingCancelRequest
from app.schemas.society_schedule_schema import (
    ChangeRequestCreate,
    ChangeRequestResolve,
    ResidentRuleRequest,
    RulePattern,
    VisitCreateRequest,
)
from app.schemas.society_schema import (
    ArriveRequest,
    PremiumBookingRequest,
    SocietyCreateRequest,
    SocietyEnrollRequest,
    SocietyUpdateRequest,
)
from app.services import payment_service
from app.services.booking_service import BookingService
from app.services.payment_service import PaymentService
from app.services.society_schedule_engine import Car, Visit, project, slot_from_key
from app.services.society_schedule_service import SocietyScheduleService
from app.services.society_service import SocietyService, bucket_window, today_ist
from app.utils.timezone import now_ist

from tests.factories import get_hatchback_type_id, get_suv_type_id, make_captain
from tests.society_factories import (
    activate_cash,
    auth,
    client,
    enroll,
    make_center,
    make_resident,
    make_society,
    make_template,
    plate,
    staff,
)

pytestmark = pytest.mark.asyncio
_seq = itertools.count(1)
SLOTS = {k: slot_from_key(k) for k in ("08:00-11:00", "11:00-14:00", "14:00-17:00", "17:00-20:00")}


@pytest.fixture
async def rig(db, cleanup):
    center = await make_center(db, cleanup, spot=4)
    society = await make_society(db, cleanup, center, name=f"Audit Fix Residency {next(_seq)}")
    manager, captain = await staff(db, cleanup, center["id"])
    captain2 = await make_captain(db, center["id"])
    cleanup.append(("users", {"_id": ObjectId(captain2)}))
    cleanup.append(("notifications", {"user_id": captain2}))
    sid = society["id"]
    for coll in ("society_visits", "society_schedule_rules", "society_schedule_requests"):
        cleanup.append((coll, {"society_id": sid}))
    cleanup.append(("slot_capacity", {"service_center_id": center["id"]}))
    cleanup.append(("society_generation_locks", {"_id": {"$regex": f"^{sid}:"}}))
    plan = await make_template(db, cleanup, count=2)
    user = SimpleNamespace(id=manager, role="manager", service_center_id=center["id"])
    return SimpleNamespace(center=center, society=society, sid=sid, manager=manager, captain=captain, captain2=captain2, plan=plan, user=user)


async def _active(db, cleanup, rig, cars: int = 1, resident: dict | None = None):
    resident = resident or await make_resident(db, cleanup)
    view = await enroll(db, rig.society, resident, rig.plan["id"], cars=cars)
    act = await activate_cash(db, view["id"])
    return resident, view["id"], [c["subscription"]["id"] for c in act["enrollment"]["cars"]]


def _in_hours() -> datetime:
    return now_ist().replace(hour=12, minute=0, second=0, microsecond=0)


def _day(offset: int) -> str:
    return (now_ist().date() + timedelta(days=offset)).isoformat()


async def _society(db, sid: str) -> dict:
    return await db.societies.find_one({"_id": ObjectId(sid)})


class _StubLinks:
    cancelled: list[str] = []

    def create(self, payload):
        n = next(_seq)
        return {"id": f"plink_fix_{n:06d}", "short_url": f"https://rzp.io/l/fix{n}", **payload}

    def cancel(self, link_id):
        self.cancelled.append(link_id)
        return {"id": link_id, "status": "cancelled"}


class _StubClient:
    payment_link = _StubLinks()


@pytest.fixture
def gateway(monkeypatch):
    monkeypatch.setattr(payment_service.settings, "RAZORPAY_KEY_ID", "rzp_test_stub")
    monkeypatch.setattr(payment_service.settings, "RAZORPAY_KEY_SECRET", "stub_secret_key")
    monkeypatch.setattr(payment_service, "_razorpay_client", lambda: _StubClient())


# ---------------------------------------------------------------------------
# SOC-1 — one resident, two enrollments
# ---------------------------------------------------------------------------


async def test_soc1_a_residents_two_enrollments_are_one_booking_visit(db, cleanup, rig):
    resident = await make_resident(db, cleanup)
    await _active(db, cleanup, rig, resident=resident)
    await _active(db, cleanup, rig, resident=resident)  # a car added mid-month = a second enrollment
    service = SocietyScheduleService(db)
    created = await service.create_visit(await _society(db, rig.sid), VisitCreateRequest(
        date=_day(1), slot_keys=["08:00-11:00", "11:00-14:00"], captain_ids=[rig.captain], washes_per_captain=6,
    ), rig.manager)
    await service.sweep(now=_in_hours())
    visit = await db.society_visits.find_one({"_id": ObjectId(created["id"])})
    assert visit["status"] == "booked", [(a["status"], a.get("error")) for a in visit["allocations"]]
    assert len(visit["allocations"]) == 1 and len(visit["allocations"][0]["sub_ids"]) == 2
    assert await db.bookings.count_documents({"society_visit_id": created["id"], "status": {"$ne": "cancelled"}}) == 2


async def test_soc1_a_booking_on_one_enrollment_sits_the_residents_other_cars_out():
    day = date(2026, 10, 10)
    booked = Car(sub_id="s1", vehicle_id="v1", enrollment_id="e1", customer_id="c1", remaining=1, cycle_start=date(2026, 10, 1),
                 end=date(2026, 11, 1), total=2, washed={day})
    other = Car(sub_id="s2", vehicle_id="v2", enrollment_id="e2", customer_id="c1", remaining=2, cycle_start=date(2026, 10, 1),
                end=date(2026, 11, 1), total=2)
    visit = Visit(id="v", kind="society", date=day, status="planned", slot_keys=["08:00-11:00"], captain_ids=["cap"], per_captain=6)
    out = project([booked, other], [visit], SLOTS, 15)["v"]
    assert out["placed"] == []
    assert {"sub_id": "s2", "reason": "Has another wash booked that day"} in out["skipped"]


# ---------------------------------------------------------------------------
# SOC-2 — the pin decides the center
# ---------------------------------------------------------------------------


async def test_soc2_admin_cannot_file_a_society_under_a_center_its_pin_is_not_in(db, cleanup):
    geo = await make_center(db, cleanup, spot=2)
    other = await make_center(db, cleanup, spot=3)
    service = SocietyService(db)

    def payload(center_id):
        return SocietyCreateRequest(
            name=f"Pin Towers {next(_seq)}", address_line="1 Road", area="X", pincode=geo["pincode"],
            latitude=geo["lat"] + 0.001, longitude=geo["lng"] + 0.001, service_center_id=center_id,
        )

    with pytest.raises(BadRequestException, match="This pin is served by"):
        await service.create_society(payload(other["id"]), "admin-test", "admin", None)
    assert await db.societies.count_documents({"service_center_id": other["id"]}) == 0
    created = await service.create_society(payload(geo["id"]), "admin-test", "admin", None)
    cleanup.append(("societies", {"_id": ObjectId(created["id"])}))
    assert created["service_center_id"] == geo["id"]


# ---------------------------------------------------------------------------
# SOC-3 — stale slots and crashing days
# ---------------------------------------------------------------------------


async def test_soc3_engine_sends_a_resident_wash_in_a_vanished_slot_to_overflow():
    car = Car(sub_id="s1", vehicle_id="v1", enrollment_id="e1", customer_id="c1", remaining=2, cycle_start=date(2026, 10, 1), end=date(2026, 11, 1))
    visit = Visit(id="r", kind="resident", date=date(2026, 10, 10), status="planned", slot_keys=["07:00-10:00"], sub_ids=["s1"])
    out = project([car], [visit], SLOTS, 15)["r"]
    assert out["placed"] == []
    assert out["overflow"] and "slot isn't offered" in out["overflow"][0]["reason"]


async def test_soc3_stale_resident_slots_never_block_later_visit_days(db, cleanup, rig):
    service = SocietyScheduleService(db)
    society = await _society(db, rig.sid)
    tomorrow = now_ist().date() + timedelta(days=1)
    for _ in range(5):
        _, eid, subs = await _active(db, cleanup, rig)
        await service.create_rule(society, ResidentRuleRequest(
            enrollment_id=eid, subscription_ids=subs, pattern=RulePattern(kind="weekly", weekday=tomorrow.weekday()),
            slot_key="08:00-11:00", captain_id=rig.captain,
        ), "resident", rig.manager)
    # The center now opens an hour later — 08:00-11:00 no longer exists.
    await db.service_centers.update_one({"_id": ObjectId(rig.center["id"])}, {"$set": {"working_hours_start": "09:00"}})
    await _active(db, cleanup, rig)
    good = await service.create_visit(society, VisitCreateRequest(
        date=(tomorrow + timedelta(days=1)).isoformat(), slot_keys=["09:00-12:00"], captain_ids=[rig.captain], washes_per_captain=6,
    ), rig.manager)
    for _ in range(2):
        await service.sweep(now=_in_hours())
    broken = await db.society_visits.find({"society_id": rig.sid, "kind": "resident", "date": tomorrow.isoformat()}).to_list(None)
    assert len(broken) == 5 and all(v["status"] != "planned" for v in broken)
    assert all("slot isn't offered" in (v["overflow"][0]["reason"]) for v in broken)
    assert (await db.society_visits.find_one({"_id": ObjectId(good["id"])}))["status"] == "booked"


async def test_soc3_a_day_that_crashes_is_parked_failed_not_retried_forever(db, cleanup, rig, monkeypatch):
    await _active(db, cleanup, rig)
    service = SocietyScheduleService(db)
    created = await service.create_visit(await _society(db, rig.sid), VisitCreateRequest(
        date=_day(1), slot_keys=["08:00-11:00"], captain_ids=[rig.captain], washes_per_captain=6,
    ), rig.manager)

    tries: list[str] = []

    async def boom(self, visit, actor_id):
        tries.append(str(visit["_id"]))
        raise RuntimeError("database hiccup")

    monkeypatch.setattr(SocietyScheduleService, "_generate_claimed", boom)
    await service.sweep(now=_in_hours())
    visit = await db.society_visits.find_one({"_id": ObjectId(created["id"])})
    assert visit["status"] == "failed" and "database hiccup" in visit["generation_error"]
    # The sweep only books planned days — this one waits for "Book now"
    # instead of being picked first on every pass.
    await service.sweep(now=_in_hours())
    assert tries.count(created["id"]) == 1
    assert not await db.society_generation_locks.find_one({"_id": {"$regex": f"^{rig.sid}:"}})


async def test_soc3_an_unexpected_booking_error_fails_only_that_resident(db, cleanup, rig, monkeypatch):
    await _active(db, cleanup, rig)
    service = SocietyScheduleService(db)
    created = await service.create_visit(await _society(db, rig.sid), VisitCreateRequest(
        date=_day(1), slot_keys=["08:00-11:00"], captain_ids=[rig.captain], washes_per_captain=6,
    ), rig.manager)

    async def broken_booking(self, payload, **kw):
        raise ValueError("validation blew up")

    monkeypatch.setattr(SocietyService, "book_premium", broken_booking)
    result = await service.generate(created["id"], retry=True)
    assert result["status"] == "failed"
    assert result["allocations"][0]["status"] == "failed" and result["allocations"][0]["error"]


# ---------------------------------------------------------------------------
# SOC-4 — early renewal + cancelling an old-cycle booking
# ---------------------------------------------------------------------------


async def test_soc4_cancelling_an_old_cycle_wash_after_early_renewal_gives_it_back(db, cleanup, rig):
    _, eid, subs = await _active(db, cleanup, rig)
    sub_id = subs[0]
    service = SocietyService(db)
    ids = []
    for off in (1, 2):
        r = await service.book_premium(
            PremiumBookingRequest(subscription_ids=[sub_id], scheduled_date=_day(off), scheduled_slot="08:00-11:00"),
            actor_id=rig.manager, actor_role="manager", actor_center_id=rig.center["id"], society_id=rig.sid,
        )
        ids.append(r["bookings"][0]["id"])
    await db.user_subscriptions.update_one({"_id": ObjectId(sub_id)}, {"$set": {"end_date": datetime.now(timezone.utc) + timedelta(days=2)}})
    out = await service.renew(await service.get_enrollment(eid), method="cash", actor_id=rig.manager)
    assert out["renewed"] == 1
    sub = await db.user_subscriptions.find_one({"_id": ObjectId(sub_id)})
    assert sub["remaining_service_count"] == 2 and sub["total_service_count"] == 4  # 2 new + room for the 2 booked
    await BookingService(db).cancel_booking(ids[0], BookingCancelRequest(reason="away"), rig.manager, "manager", rig.center["id"])
    sub = await db.user_subscriptions.find_one({"_id": ObjectId(sub_id)})
    assert sub["remaining_service_count"] == 3  # paid for 4, 1 still booked


# ---------------------------------------------------------------------------
# SOC-5 — one plate, one society pass
# ---------------------------------------------------------------------------


async def test_soc5_a_plate_on_a_live_society_pass_is_refused_on_another_account(db, cleanup, rig):
    a = await make_resident(db, cleanup)
    b = await make_resident(db, cleanup)
    hatch = await get_hatchback_type_id(db)
    shared = plate()
    raw = await _society(db, rig.sid)
    service = SocietyService(db)

    def payload(who, reg):
        return SocietyEnrollRequest(plan_id=rig.plan["id"], resident_name="Resident", phone=who["phone"], flat=f"C-{next(_seq)}",
                                    cars=[{"vehicle_type": hatch, "registration_number": reg}])

    # B requests first (nothing live yet), then A activates the plate.
    pending = await service.enroll(raw, payload(b, shared), b, source="form")
    view = await service.enroll(raw, payload(a, shared), a, source="form")
    await activate_cash(db, view["id"])
    # B's older request can't activate it now…
    with pytest.raises(BadRequestException, match="already has another active plan"):
        await activate_cash(db, pending["id"])
    # …and a fresh request for it is refused up front.
    with pytest.raises(BadRequestException, match="already has an active plan"):
        await service.enroll(raw, payload(b, shared), b, source="form")
    assert await db.user_subscriptions.count_documents({"society_id": rig.sid, "status": "active"}) == 1


# ---------------------------------------------------------------------------
# SOC-6 — one renewal payment, one cycle
# ---------------------------------------------------------------------------


async def test_soc6_a_second_payment_for_the_same_renewal_is_parked_not_applied(db, cleanup, rig):
    resident, eid, subs = await _active(db, cleanup, rig)
    await db.user_subscriptions.update_one({"_id": ObjectId(subs[0])}, {"$set": {"end_date": datetime.now(timezone.utc) + timedelta(days=2)}})
    service = SocietyService(db)
    amount, _desc, ref = await service.payment_quote(str(resident["_id"]), eid, True)
    first = await service.on_order_paid({"_id": ObjectId(), "amount_paise": amount, **ref, "razorpay_order_id": "order_hub"})
    second = await service.on_order_paid({"_id": ObjectId(), "amount_paise": amount, **ref, "razorpay_order_id": "order_again"})
    assert first["ok"] and not second["ok"]
    sub = await db.user_subscriptions.find_one({"_id": ObjectId(subs[0])})
    assert sub["renewal_count"] == 1 and sub["remaining_service_count"] == 4
    start, end = bucket_window(sub, today_ist())
    assert start <= today_ist() < end
    assert await db.society_payments.count_documents({"enrollment_id": eid, "kind": "renewal"}) == 1


async def test_soc6_an_online_renewal_voids_the_unpaid_renewal_link(db, cleanup, rig, gateway):
    resident, eid, subs = await _active(db, cleanup, rig)
    cleanup.append(("payment_orders", {"society_enrollment_id": eid}))
    await db.user_subscriptions.update_one({"_id": ObjectId(subs[0])}, {"$set": {"end_date": datetime.now(timezone.utc) + timedelta(days=2)}})
    pay = PaymentService(db)
    link = await pay.create_society_link(eid, renewal=True, actor_id=rig.manager, actor_role="manager", actor_center_id=rig.center["id"], send_whatsapp=False)
    service = SocietyService(db)
    amount, _desc, ref = await service.payment_quote(str(resident["_id"]), eid, True)
    assert (await service.on_order_paid({"_id": ObjectId(), "amount_paise": amount, **ref, "razorpay_order_id": "order_hub"}))["ok"]
    doc = await db.payment_orders.find_one({"_id": ObjectId(link["order_id"])})
    assert doc["status"] == "voided"
    # Paid anyway in the same instant: parked for a human, not a second cycle.
    result = await pay._apply_link_paid(doc["razorpay_link_id"], "pay_late_1")
    assert result["status"] == "needs_attention"
    assert (await db.user_subscriptions.find_one({"_id": ObjectId(subs[0])}))["renewal_count"] == 1


# ---------------------------------------------------------------------------
# SOC-7 — two visit days, one society, one date
# ---------------------------------------------------------------------------


async def test_soc7_two_visit_days_on_one_date_generated_together_book_each_car_once(db, cleanup, rig):
    residents = [await _active(db, cleanup, rig) for _ in range(3)]
    service = SocietyScheduleService(db)
    society = await _society(db, rig.sid)
    a = await service.create_visit(society, VisitCreateRequest(date=_day(1), slot_keys=["08:00-11:00"], captain_ids=[rig.captain], washes_per_captain=6), rig.manager)
    b = await service.create_visit(society, VisitCreateRequest(date=_day(1), slot_keys=["14:00-17:00"], captain_ids=[rig.captain2], washes_per_captain=6), rig.manager)
    await asyncio.gather(service.generate(a["id"], retry=True), service.generate(b["id"], retry=True))
    # Whichever lost the race runs again later — and finds nothing left to book.
    for vid in (a["id"], b["id"]):
        await service.generate(vid, retry=True)
    subs = [s for r in residents for s in r[2]]
    assert await db.bookings.count_documents({"subscription_id": {"$in": subs}, "status": {"$ne": "cancelled"}}) == 3
    for s in subs:
        assert (await db.user_subscriptions.find_one({"_id": ObjectId(s)}))["remaining_service_count"] == 1


# ---------------------------------------------------------------------------
# SOC-8 — form switched off, hubs stay open
# ---------------------------------------------------------------------------


async def test_soc8_switching_the_form_off_keeps_residents_hubs_open(db, cleanup, rig):
    resident, eid, _subs = await _active(db, cleanup, rig)
    stranger = await make_resident(db, cleanup)
    society = await SocietyService(db).update_society(await _society(db, rig.sid), SocietyUpdateRequest(form_enabled=False))
    token = society["form_token"]
    me = auth(str(resident["_id"]), "customer")
    async with client() as c:
        hub = await c.get(f"/api/v1/society-forms/{token}/me", headers=me)
        assert hub.status_code == 200 and hub.json()["data"]["enrollments"][0]["id"] == eid
        form = await c.get(f"/api/v1/society-forms/{token}", headers=me)
        assert form.status_code == 200 and form.json()["data"]["form_enabled"] is False
        preview = await c.post(f"/api/v1/society-forms/{token}/me/enrollments/{eid}/coupon-preview", json={"renewal": False}, headers=me)
        assert preview.status_code == 200
        # New sign-ups stay closed.
        assert (await c.get(f"/api/v1/society-forms/{token}")).status_code == 404
        assert (await c.get(f"/api/v1/society-forms/{token}/me", headers=auth(str(stranger["_id"]), "customer"))).status_code == 404
        assert (await c.post(f"/api/v1/society-forms/{token}/quote", json={"plan_id": rig.plan["id"], "vehicle_types": [await get_hatchback_type_id(db)]})).status_code == 404


# ---------------------------------------------------------------------------
# SOC-9 / SOC-15 / SOC-16 — cancellation
# ---------------------------------------------------------------------------


async def test_soc9_cancelling_an_active_plan_tells_the_resident(db, cleanup, rig):
    resident, eid, _subs = await _active(db, cleanup, rig)
    service = SocietyService(db)
    await service.cancel(await service.get_enrollment(eid), vehicle_ids=None, actor_id=rig.manager, reason="Moved out")
    note = await db.notifications.find_one({"user_id": str(resident["_id"]), "title": "Society plan cancelled"})
    assert note and "Moved out" in note["message"]


async def test_soc15_cancelling_a_request_that_was_just_paid_is_refused(db, cleanup, rig):
    resident = await make_resident(db, cleanup)
    view = await enroll(db, rig.society, resident, rig.plan["id"], cars=1)
    service = SocietyService(db)
    stale = await service.get_enrollment(view["id"])  # what the cancel button was looking at
    await activate_cash(db, view["id"])  # the payment won
    with pytest.raises(BadRequestException, match="just paid"):
        await service.cancel(stale, vehicle_ids=None, actor_id=str(resident["_id"]), reason="Withdrawn by resident")
    assert (await service.get_enrollment(view["id"]))["status"] == "active"


async def test_soc16_cancelled_cars_leave_their_repeat_wash(db, cleanup, rig):
    _, eid, subs = await _active(db, cleanup, rig)
    schedule = SocietyScheduleService(db)
    day3 = now_ist().date() + timedelta(days=3)
    rule = await schedule.create_rule(await _society(db, rig.sid), ResidentRuleRequest(
        enrollment_id=eid, subscription_ids=subs, pattern=RulePattern(kind="weekly", weekday=day3.weekday()),
        slot_key="08:00-11:00", captain_id=rig.captain,
    ), "resident", rig.manager)
    assert await db.society_visits.count_documents({"rule_id": rule["id"], "status": "planned"}) >= 1
    service = SocietyService(db)
    await service.cancel(await service.get_enrollment(eid), vehicle_ids=None, actor_id=rig.manager)
    raw = await db.society_schedule_rules.find_one({"_id": ObjectId(rule["id"])})
    assert raw["is_active"] is False and raw["subscription_ids"] == []
    assert await db.society_visits.count_documents({"rule_id": rule["id"], "status": "planned"}) == 0
    # Nothing re-materializes it.
    await schedule.sweep(now=_in_hours())
    assert await db.society_visits.count_documents({"rule_id": rule["id"], "status": "planned"}) == 0


# ---------------------------------------------------------------------------
# SOC-10 — inside the plan month
# ---------------------------------------------------------------------------


async def test_soc10_a_premium_wash_after_the_plan_month_is_refused(db, cleanup, rig):
    resident, _eid, subs = await _active(db, cleanup, rig)
    await db.user_subscriptions.update_one({"_id": ObjectId(subs[0])}, {"$set": {"end_date": datetime.now(timezone.utc) + timedelta(hours=40)}})
    with pytest.raises(BadRequestException, match="plan month ends"):
        await SocietyService(db).book_premium(
            PremiumBookingRequest(subscription_ids=subs, scheduled_date=_day(4), scheduled_slot="08:00-11:00"),
            actor_id=str(resident["_id"]), actor_role="customer", actor_center_id=None,
        )
    assert await db.bookings.count_documents({"subscription_id": subs[0]}) == 0


# ---------------------------------------------------------------------------
# SOC-11 + AUTH-8 — staff acting for a typed phone
# ---------------------------------------------------------------------------


async def test_soc11_staff_quote_prices_the_residents_personal_plan(db, cleanup, rig):
    resident = await make_resident(db, cleanup)
    personal = await make_template(db, cleanup, scope="customer", customer_phone=resident["phone"], society_ids=[rig.sid], price=999, mrp=1500)
    body = {"plan_id": personal["id"], "vehicle_types": [await get_hatchback_type_id(db)]}
    h = auth(rig.manager, "manager", rig.center["id"])
    async with client() as c:
        priced = await c.post(f"/api/v1/societies/{rig.sid}/quote", json={**body, "phone": resident["phone"]}, headers=h)
        assert priced.status_code == 200, priced.text
        assert priced.json()["data"]["total"] == 999
        assert (await c.post(f"/api/v1/societies/{rig.sid}/quote", json=body, headers=h)).status_code == 404


async def test_auth8_staff_phone_lookup_shows_only_this_societys_personal_plans(db, cleanup, rig):
    resident = await make_resident(db, cleanup)
    anywhere = await make_template(db, cleanup, scope="customer", customer_phone=resident["phone"], society_ids=[], price=901)
    here = await make_template(db, cleanup, scope="customer", customer_phone=resident["phone"], society_ids=[rig.sid], price=902)
    h = auth(rig.manager, "manager", rig.center["id"])
    async with client() as c:
        offered = await c.get(f"/api/v1/societies/{rig.sid}/plans", params={"phone": resident["phone"]}, headers=h)
        ids = {p["id"] for p in offered.json()["data"]["plans"]}
        assert here["id"] in ids and anywhere["id"] not in ids
        # The resident themself still sees their any-society plan on the form.
        mine = await c.get(f"/api/v1/society-forms/{rig.society['form_token']}", headers=auth(str(resident["_id"]), "customer"))
        assert anywhere["id"] in {p["id"] for p in mine.json()["data"]["plans"]}


async def test_auth8_staff_add_resident_never_retypes_a_car_or_rewrites_an_address(db, cleanup, rig):
    resident = await make_resident(db, cleanup)
    own = await enroll(db, rig.society, resident, rig.plan["id"], cars=1)  # their own request: saved car + address
    car = own["cars"][0]
    address = await db.addresses.find_one({"owner_id": str(resident["_id"]), "society_id": rig.sid})
    h = auth(rig.manager, "manager", rig.center["id"])
    body = {"plan_id": rig.plan["id"], "resident_name": "Someone Else", "phone": resident["phone"], "flat": "Z-99"}
    async with client() as c:
        wrong = await c.post(f"/api/v1/societies/{rig.sid}/enrollments", headers=h, json={
            **body, "cars": [{"vehicle_type": await get_suv_type_id(db), "registration_number": car["registration_number"]}],
        })
        assert wrong.status_code == 400 and "saved on this customer's account" in wrong.json()["message"]
        assert (await db.vehicles.find_one({"_id": ObjectId(car["vehicle_id"])}))["vehicle_type"] == car["vehicle_type"]
        ok = await c.post(f"/api/v1/societies/{rig.sid}/enrollments", headers=h, json={
            **body, "cars": [{"vehicle_type": await get_hatchback_type_id(db), "registration_number": plate()}],
        })
        assert ok.status_code == 200, ok.text
    kept = await db.addresses.find_one({"_id": address["_id"]})
    assert kept["line1"] == address["line1"]
    assert await db.addresses.count_documents({"owner_id": str(resident["_id"]), "society_id": rig.sid, "line1": {"$regex": "^Z-99"}}) == 1


# ---------------------------------------------------------------------------
# SOC-17 — a paid WhatsApp link confirms once
# ---------------------------------------------------------------------------


async def test_soc17_a_paid_link_activation_sends_one_confirmation(db, cleanup, rig, gateway):
    resident = await make_resident(db, cleanup)
    view = await enroll(db, rig.society, resident, rig.plan["id"], cars=1)
    cleanup.append(("payment_orders", {"society_enrollment_id": view["id"]}))
    pay = PaymentService(db)
    link = await pay.create_society_link(view["id"], renewal=False, actor_id=rig.manager, actor_role="manager",
                                         actor_center_id=rig.center["id"], send_whatsapp=False)
    doc = await db.payment_orders.find_one({"_id": ObjectId(link["order_id"])})
    result = await pay._apply_link_paid(doc["razorpay_link_id"], "pay_link_fix_1")
    assert result["settled"]
    assert await db.notifications.count_documents({"user_id": str(resident["_id"]), "title": "Society plan active"}) == 1


# ---------------------------------------------------------------------------
# AUTH-12 — a captain's society access follows their current center
# ---------------------------------------------------------------------------


async def test_auth12_a_captain_moved_to_another_center_loses_the_old_societies(db, cleanup, rig):
    service = SocietyService(db)
    await service.set_captain(await _society(db, rig.sid), rig.captain, None)
    schedule = SocietyScheduleService(db)
    visit = await schedule.create_visit(await _society(db, rig.sid), VisitCreateRequest(
        date=_day(1), slot_keys=["08:00-11:00"], captain_ids=[rig.captain], washes_per_captain=6,
    ), rig.manager)
    assert [s["id"] for s in (await service.captain_today(rig.captain))["societies"]] == [rig.sid]
    assert [v["id"] for v in (await schedule.captain_visits(rig.captain))["visits"]] == [visit["id"]]

    elsewhere = await make_center(db, cleanup, spot=1)
    await db.users.update_one({"_id": ObjectId(rig.captain)}, {"$set": {"service_center_id": elsewhere["id"]}})
    assert (await service.captain_today(rig.captain))["societies"] == []
    with pytest.raises(ForbiddenException):
        await service.arrive(rig.captain, rig.sid, ArriveRequest())
    assert (await schedule.captain_visits(rig.captain))["visits"] == []
    with pytest.raises(NotFoundException):
        await schedule.captain_visit(rig.captain, visit["id"])


# ---------------------------------------------------------------------------
# Change requests — validate the move first
# ---------------------------------------------------------------------------


async def test_approving_a_move_with_a_foreign_captain_changes_nothing(db, cleanup, rig):
    resident, eid, subs = await _active(db, cleanup, rig)
    service = SocietyScheduleService(db)
    day3 = now_ist().date() + timedelta(days=3)
    await service.create_rule(await _society(db, rig.sid), ResidentRuleRequest(
        enrollment_id=eid, subscription_ids=subs, pattern=RulePattern(kind="weekly", weekday=day3.weekday()),
        slot_key="08:00-11:00", captain_id=rig.captain,
    ), "resident", rig.manager)
    first = (await service.my_schedule(str(resident["_id"])))["items"][0]
    req = await service.create_request(str(resident["_id"]), ChangeRequestCreate(
        visit_id=first["visit_id"], kind="move", preferred_date=(day3 + timedelta(days=1)).isoformat(),
    ))
    other = await make_center(db, cleanup, spot=0)
    foreign = await make_captain(db, other["id"])
    cleanup.append(("users", {"_id": ObjectId(foreign)}))
    raw = await db.society_schedule_requests.find_one({"_id": ObjectId(req["id"])})
    with pytest.raises(BadRequestException, match="captains from this society"):
        await service.approve_request(raw, ChangeRequestResolve(captain_id=foreign), rig.user)
    visit = await db.society_visits.find_one({"_id": ObjectId(first["visit_id"])})
    assert visit["status"] == "planned" and not visit.get("excluded_subscription_ids")
    assert (await db.society_schedule_requests.find_one({"_id": ObjectId(req["id"])}))["status"] == "pending"
