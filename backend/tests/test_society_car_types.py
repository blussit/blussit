"""
Society screens show each car's TYPE next to its plate ("MP09SR5272 ·
Hatchback") and each plan's services ("Daily wash + 2 Star Wash"): the
additive serializer fields behind that — payments, the resident hub's
upcoming washes, society issues (stored + filled in for older ones), the
resident's schedule and change requests.
"""
import itertools
from datetime import timedelta
from types import SimpleNamespace

import pytest
from bson import ObjectId

from app.schemas.society_schedule_schema import ChangeRequestCreate, ResidentRuleRequest, RulePattern, VisitCreateRequest
from app.schemas.society_schema import PremiumBookingRequest
from app.services.society_schedule_service import SocietyScheduleService
from app.services.society_service import SocietyService
from app.utils.timezone import now_ist

from tests.factories import get_hatchback_type_id
from tests.society_factories import activate_cash, auth, client, enroll, make_center, make_resident, make_society, make_template, staff

pytestmark = pytest.mark.asyncio

_seq = itertools.count(1)


@pytest.fixture
async def rig(db, cleanup):
    center = await make_center(db, cleanup, spot=3)
    society = await make_society(db, cleanup, center, name=f"Maple Heights {next(_seq)}")
    manager, captain = await staff(db, cleanup, center["id"])
    sid = society["id"]
    for coll in ("society_visits", "society_schedule_rules", "society_schedule_requests", "complaints"):
        cleanup.append((coll, {"society_id": sid}))
    cleanup.append(("slot_capacity", {"service_center_id": center["id"]}))
    plan = await make_template(db, cleanup, count=2)
    hatchback = await db.vehicle_types.find_one({"_id": ObjectId(await get_hatchback_type_id(db))})
    return SimpleNamespace(center=center, society=society, sid=sid, manager=manager, captain=captain, plan=plan, type_name=hatchback["name"])


async def _active(db, cleanup, rig, cars: int = 2) -> dict:
    resident = await make_resident(db, cleanup)
    view = await enroll(db, rig.society, resident, rig.plan["id"], cars=cars)
    enrollment = (await activate_cash(db, view["id"]))["enrollment"]
    return {"resident": resident, "customer_id": str(resident["_id"]), "enrollment": enrollment}


async def test_payments_list_the_cars_with_their_type_and_the_plan_services(db, cleanup, rig):
    res = await _active(db, cleanup, rig, cars=2)
    async with client() as c:
        detail = (await c.get(f"/api/v1/societies/{rig.sid}", headers=auth(rig.manager, "manager", rig.center["id"]))).json()["data"]
    [payment] = detail["payments"]
    plates = sorted(car["registration_number"] for car in res["enrollment"]["cars"])
    assert payment["car_count"] == 2 and payment["kind"] == "activation"
    assert sorted(car["registration_number"] for car in payment["cars"]) == plates
    assert {car["vehicle_type_name"] for car in payment["cars"]} == {rig.type_name}
    assert payment["flat"] == res["enrollment"]["flat"]
    assert payment["plan_services"].startswith("Daily wash + 2 ")


async def test_hub_upcoming_wash_carries_the_car_type(db, cleanup, rig):
    res = await _active(db, cleanup, rig, cars=1)
    sub_id = res["enrollment"]["cars"][0]["subscription"]["id"]
    tomorrow = (now_ist().date() + timedelta(days=1)).isoformat()
    service = SocietyService(db)
    await service.book_premium(
        PremiumBookingRequest(subscription_ids=[sub_id], scheduled_date=tomorrow, scheduled_slot="08:00-11:00"),
        actor_id=res["customer_id"], actor_role="customer", actor_center_id=None,
    )
    hub = await service.my_hub(await db.societies.find_one({"_id": ObjectId(rig.sid)}), res["customer_id"])
    [upcoming] = hub["upcoming"]
    assert upcoming["registration_number"] == res["enrollment"]["cars"][0]["registration_number"]
    assert upcoming["vehicle_type_name"] == rig.type_name and upcoming["service_name"]
    # The resident schedule's hand-booked item names the car type too.
    mine = await SocietyScheduleService(db).my_schedule(res["customer_id"])
    booked = next(i for i in mine["items"] if i["kind"] == "booking")
    assert booked["cars"][0]["vehicle_type_name"] == rig.type_name


async def test_society_issue_names_the_car_type_and_older_issues_get_it_filled_in(db, cleanup, rig):
    res = await _active(db, cleanup, rig, cars=1)
    car = res["enrollment"]["cars"][0]
    hr = auth(res["customer_id"], "customer")
    ha = auth(rig.manager, "manager", rig.center["id"])
    async with client() as c:
        resp = await c.post("/api/v1/society-issues", headers=hr, json={"society_id": rig.sid, "issue_type": "billing", "vehicle_id": car["vehicle_id"]})
        assert resp.status_code == 200, resp.text
        issue = resp.json()["data"]
        assert issue["registration_number"] == car["registration_number"] and issue["vehicle_type_name"] == rig.type_name
        # An issue filed before the field was stored: filled in from the car.
        await db.complaints.update_one({"_id": ObjectId(issue["id"])}, {"$unset": {"vehicle_type_name": ""}})
        listed = (await c.get(f"/api/v1/society-issues/society/{rig.sid}", headers=ha)).json()["data"]
        assert [i["vehicle_type_name"] for i in listed] == [rig.type_name]
        mine = (await c.get(f"/api/v1/society-issues/my?society_id={rig.sid}", headers=hr)).json()["data"]
        assert [i["vehicle_type_name"] for i in mine] == [rig.type_name]
        # No car picked: no type, nothing breaks.
        resp = await c.post("/api/v1/society-issues", headers=hr, json={"society_id": rig.sid, "issue_type": "captain_no_show"})
        assert resp.status_code == 200 and resp.json()["data"]["vehicle_type_name"] is None


async def test_schedule_items_and_change_requests_carry_the_car_type(db, cleanup, rig):
    res = await _active(db, cleanup, rig, cars=1)
    subs = [c["subscription"]["id"] for c in res["enrollment"]["cars"]]
    service = SocietyScheduleService(db)
    society = await db.societies.find_one({"_id": ObjectId(rig.sid)})
    day3 = now_ist().date() + timedelta(days=3)
    await service.create_rule(society, ResidentRuleRequest(
        enrollment_id=res["enrollment"]["id"], subscription_ids=subs,
        pattern=RulePattern(kind="weekly", weekday=day3.weekday()), slot_key="08:00-11:00", captain_id=rig.captain,
    ), "resident", rig.manager)
    mine = await service.my_schedule(res["customer_id"])
    first = mine["items"][0]
    assert first["cars"] == [{"sub_id": subs[0], "plate": res["enrollment"]["cars"][0]["registration_number"], "vehicle_type_name": rig.type_name}]
    req = await service.create_request(res["customer_id"], ChangeRequestCreate(visit_id=first["visit_id"], kind="skip"))
    assert req["plates"] == [res["enrollment"]["cars"][0]["registration_number"]]
    assert req["cars"] == [{"plate": res["enrollment"]["cars"][0]["registration_number"], "vehicle_type_name": rig.type_name}]


async def test_visit_day_and_captain_visit_cars_carry_type_and_service(db, cleanup, rig):
    res = await _active(db, cleanup, rig, cars=1)
    plate = res["enrollment"]["cars"][0]["registration_number"]
    service = SocietyScheduleService(db)
    society = await db.societies.find_one({"_id": ObjectId(rig.sid)})
    day3 = (now_ist().date() + timedelta(days=3)).isoformat()
    await service.create_visit(society, VisitCreateRequest(date=day3, slot_keys=["08:00-11:00"], captain_ids=[rig.captain], washes_per_captain=4), rig.manager)
    schedule = await service.society_schedule(society, None, None)
    visit = next(v for v in schedule["visits"] if v["date"] == day3)
    [car] = [c for a in visit["allocations"] for c in a["cars"]]
    assert car["plate"] == plate and car["vehicle_type_name"] == rig.type_name and car["service_name"]
    captain = await service.captain_visits(rig.captain)
    [mine] = [v for v in captain["visits"] if v["date"] == day3]
    assert [(c["plate"], c["vehicle_type_name"], bool(c["service_name"])) for c in mine["cars"]] == [(plate, rig.type_name, True)]
