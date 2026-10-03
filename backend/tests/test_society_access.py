"""
Society plans — who can see and do what (docs/SOCIETY_PLANS.md §4), over
real HTTP: manager center scoping, captain assignment, resident isolation,
the public form token, and the captain's geo-stamped attendance.
"""
import json
from datetime import datetime, timedelta, timezone

import pytest
from bson import ObjectId

from app.services.auth_service import AuthService
from app.services.society_service import SocietyService
from app.utils.timezone import now_ist

from tests.factories import get_hatchback_type_id, make_captain
from tests.society_factories import (
    activate_cash,
    auth,
    client,
    enroll,
    make_admin,
    make_center,
    make_resident,
    make_society,
    make_template,
    plate,
    staff,
)

pytestmark = pytest.mark.asyncio


@pytest.fixture
async def two_centers(db, cleanup):
    a = await make_center(db, cleanup, spot=1)
    b = await make_center(db, cleanup, spot=2)
    manager_a, captain_a = await staff(db, cleanup, a["id"])
    manager_b, captain_b = await staff(db, cleanup, b["id"])
    society_a = await make_society(db, cleanup, a, name="Alpha Heights")
    society_b = await make_society(db, cleanup, b, name="Beta Towers")
    plan = await make_template(db, cleanup)
    return {
        "a": a, "b": b, "manager_a": manager_a, "captain_a": captain_a, "manager_b": manager_b, "captain_b": captain_b,
        "society_a": society_a, "society_b": society_b, "plan": plan,
    }


# -- managers: own center only ---------------------------------------------------


async def test_manager_sees_and_acts_on_own_center_only(db, cleanup, two_centers):
    t = two_centers
    resident = await make_resident(db, cleanup)
    enrollment_b = await enroll(db, t["society_b"], resident, t["plan"]["id"], cars=1)
    h = auth(t["manager_a"], "manager", t["a"]["id"])
    async with client() as c:
        listed = (await c.get("/api/v1/societies", headers=h)).json()["data"]
        assert {r["id"] for r in listed["rows"]} == {t["society_a"]["id"]}
        # Even an explicit center filter can't widen a manager's list.
        listed = (await c.get(f"/api/v1/societies?center_id={t['b']['id']}", headers=h)).json()["data"]
        assert {r["id"] for r in listed["rows"]} == {t["society_a"]["id"]}
        sid_b = t["society_b"]["id"]
        for method, path, body in [
            ("get", f"/api/v1/societies/{sid_b}", None),
            ("put", f"/api/v1/societies/{sid_b}", {"name": "Hijacked"}),
            ("get", f"/api/v1/societies/{sid_b}/enrollments", None),
            ("get", f"/api/v1/societies/{sid_b}/attendance", None),
            ("post", f"/api/v1/societies/{sid_b}/rotate-link", None),
            ("put", f"/api/v1/societies/{sid_b}/captain", {"captain_id": t["captain_a"]}),
            ("post", f"/api/v1/society-enrollments/{enrollment_b['id']}/activate", {"method": "cash"}),
            ("post", f"/api/v1/society-enrollments/{enrollment_b['id']}/cancel", {}),
        ]:
            resp = await getattr(c, method)(path, headers=h, **({"json": body} if body is not None else {}))
            assert resp.status_code == 403, (path, resp.status_code, resp.text)
        # Own society: fine.
        assert (await c.get(f"/api/v1/societies/{t['society_a']['id']}", headers=h)).status_code == 200
        # A captain from another center can't be made this society's captain.
        resp = await c.put(f"/api/v1/societies/{t['society_a']['id']}/captain", headers=h, json={"captain_id": t["captain_b"]})
        assert resp.status_code == 400
        # A pin served by center B can't be registered by manager A.
        resp = await c.post("/api/v1/societies", headers=h, json={
            "name": "Sneaky Society", "address_line": "1 Road", "pincode": t["b"]["pincode"],
            "latitude": t["b"]["lat"], "longitude": t["b"]["lng"],
        })
        assert resp.status_code == 400 and "not your center" in resp.json()["message"]
        # ...while a pin in their own area resolves to their center.
        resp = await c.post("/api/v1/societies", headers=h, json={
            "name": "Own Area Society", "address_line": "2 Road", "pincode": t["a"]["pincode"],
            "latitude": t["a"]["lat"] + 0.002, "longitude": t["a"]["lng"],
        })
        assert resp.status_code == 200, resp.text
        created = resp.json()["data"]
        cleanup.append(("societies", {"_id": ObjectId(created["id"])}))
        assert created["service_center_id"] == t["a"]["id"] and len(created["form_token"]) >= 22
        # Customers and captains have no business here at all.
        assert (await c.get("/api/v1/societies", headers=auth(str(resident["_id"]), "customer"))).status_code == 403
        assert (await c.get("/api/v1/societies", headers=auth(t["captain_a"], "captain", t["a"]["id"]))).status_code == 403
    assert (await db.society_enrollments.find_one({"_id": ObjectId(enrollment_b["id"])}))["status"] == "requested"


async def test_admin_sees_every_society_and_manages_plans(db, cleanup, two_centers):
    admin_id = await make_admin(db, cleanup)
    h = auth(admin_id, "admin")
    async with client() as c:
        rows = (await c.get("/api/v1/societies", headers=h)).json()["data"]["rows"]
        assert {two_centers["society_a"]["id"], two_centers["society_b"]["id"]} <= {r["id"] for r in rows}
        plans = (await c.get("/api/v1/society-plans", headers=h)).json()["data"]
        assert two_centers["plan"]["id"] in {p["id"] for p in plans}
        manager_h = auth(two_centers["manager_a"], "manager", two_centers["a"]["id"])
        assert (await c.get("/api/v1/society-plans", headers=manager_h)).status_code == 403
        assert (await c.put("/api/v1/society-plans/rate-card", headers=manager_h, json={})).status_code == 403
        # Admin-only plan endpoints never leak into the public catalogue.
        public = (await c.get("/api/v1/subscription-plans")).json()["data"]
        assert two_centers["plan"]["id"] not in {p["id"] for p in public}
        assert (await c.get(f"/api/v1/subscription-plans/{two_centers['plan']['id']}")).status_code == 404


# -- the public form ------------------------------------------------------------------


async def test_public_form_token_never_leaks_residents(db, cleanup, two_centers):
    t = two_centers
    resident = await make_resident(db, cleanup)
    view = await enroll(db, t["society_a"], resident, t["plan"]["id"], cars=1)
    token = t["society_a"]["form_token"]
    async with client() as c:
        resp = await c.get(f"/api/v1/society-forms/{token}")
        assert resp.status_code == 200
        body = resp.text
        assert resident["phone"] not in body and view["cars"][0]["registration_number"] not in body
        assert view["flat"] not in body and "Mr Secretary" not in body and "9876500001" not in body
        data = resp.json()["data"]
        assert data["society"]["name"] == "Alpha Heights" and data["plans"]
        # Guesses, rotated and disabled links all read as not found.
        assert (await c.get("/api/v1/society-forms/" + "x" * 24)).status_code == 404
        assert (await c.get("/api/v1/society-forms/short")).status_code == 404
        manager_h = auth(t["manager_a"], "manager", t["a"]["id"])
        rotated = (await c.post(f"/api/v1/societies/{t['society_a']['id']}/rotate-link", headers=manager_h)).json()["data"]
        assert rotated["form_token"] != token
        assert (await c.get(f"/api/v1/society-forms/{token}")).status_code == 404
        await c.put(f"/api/v1/societies/{t['society_a']['id']}", headers=manager_h, json={"form_enabled": False})
        assert (await c.get(f"/api/v1/society-forms/{rotated['form_token']}")).status_code == 404


async def test_public_enroll_needs_the_otp_and_signs_the_resident_in(db, cleanup, two_centers):
    t = two_centers
    phone = "9733300001"
    cleanup.append(("users", {"phone": phone}))
    cleanup.append(("otp_requests", {"identifier": phone}))
    cleanup.append(("otp_requests", {"_id": f"otp-send-cap:{phone}"}))
    cleanup.append(("whatsapp_outbox", {"phone": phone}))
    hatchback = await get_hatchback_type_id(db)
    body = {
        "plan_id": t["plan"]["id"], "resident_name": "Neha Verma", "phone": phone, "flat": "C-402",
        "cars": [{"vehicle_type": hatchback, "registration_number": plate()}, {"vehicle_type": hatchback, "registration_number": plate()}],
    }
    token = t["society_a"]["form_token"]
    async with client() as c:
        resp = await c.post(f"/api/v1/society-forms/{token}/enroll", json=body)
        assert resp.status_code == 400 and await db.users.find_one({"phone": phone}) is None
        await AuthService(db).request_phone_otp(phone)
        code = (await db.otp_requests.find_one({"identifier": phone}))["otp"]
        resp = await c.post(f"/api/v1/society-forms/{token}/enroll", json={**body, "phone_otp": "000000" if code != "000000" else "111111"})
        assert resp.status_code == 400
        resp = await c.post(f"/api/v1/society-forms/{token}/enroll", json={**body, "phone_otp": code})
        assert resp.status_code == 200, resp.text
        data = resp.json()["data"]
        user = await db.users.find_one({"phone": phone})
        cleanup.append(("vehicles", {"owner_id": str(user["_id"])}))
        cleanup.append(("addresses", {"owner_id": str(user["_id"])}))
        assert data["enrollment"]["status"] == "requested" and len(data["enrollment"]["cars"]) == 2
        assert data["auth"]["access_token"] and data["auth"]["user"]["phone"] == phone
        me = await c.get(f"/api/v1/society-forms/{token}/me", headers={"Authorization": f"Bearer {data['auth']['access_token']}"})
        assert me.status_code == 200 and len(me.json()["data"]["enrollments"]) == 1


# -- residents: their own data only -----------------------------------------------


async def test_resident_reads_and_spends_only_their_own(db, cleanup, two_centers):
    t = two_centers
    alice = await make_resident(db, cleanup)
    bob = await make_resident(db, cleanup)
    a_view = await enroll(db, t["society_a"], alice, t["plan"]["id"], cars=1)
    b_view = await enroll(db, t["society_a"], bob, t["plan"]["id"], cars=1)
    b_enrollment = (await activate_cash(db, b_view["id"]))["enrollment"]
    b_sub = b_enrollment["cars"][0]["subscription"]["id"]
    token = t["society_a"]["form_token"]
    ha = auth(str(alice["_id"]), "customer")
    async with client() as c:
        me = await c.get(f"/api/v1/society-forms/{token}/me", headers=ha)
        assert me.status_code == 200
        assert [e["id"] for e in me.json()["data"]["enrollments"]] == [a_view["id"]]
        assert bob["phone"] not in me.text and b_view["cars"][0]["registration_number"] not in me.text
        tomorrow = (now_ist().date() + timedelta(days=1)).isoformat()
        resp = await c.post("/api/v1/societies/my/premium-bookings", headers=ha,
                            json={"subscription_ids": [b_sub], "scheduled_date": tomorrow, "scheduled_slot": "08:00-11:00"})
        assert resp.status_code == 404
        resp = await c.post(f"/api/v1/society-forms/{token}/me/enrollments/{b_view['id']}/withdraw", headers=ha)
        assert resp.status_code == 404
        # Their own open request can be withdrawn; an active plan can't be.
        resp = await c.post(f"/api/v1/society-forms/{token}/me/enrollments/{a_view['id']}/withdraw", headers=ha)
        assert resp.status_code == 200 and resp.json()["data"]["status"] == "cancelled"
        hb = auth(str(bob["_id"]), "customer")
        resp = await c.post(f"/api/v1/society-forms/{token}/me/enrollments/{b_view['id']}/withdraw", headers=hb)
        assert resp.status_code == 400
        # Staff routes are closed to residents.
        assert (await c.post(f"/api/v1/society-enrollments/{b_view['id']}/activate", headers=ha, json={"method": "cash"})).status_code == 403
    assert await db.bookings.count_documents({"subscription_id": b_sub}) == 0


# -- captains: attendance ------------------------------------------------------------


async def test_only_todays_captain_marks_attendance_and_ticks_cars(db, cleanup, two_centers):
    t = two_centers
    service = SocietyService(db)
    society = await service.societies.find_by_id(t["society_a"]["id"])
    await service.set_captain(society, t["captain_a"], None)
    resident = await make_resident(db, cleanup)
    view = await enroll(db, t["society_a"], resident, t["plan"]["id"], cars=2)
    enrollment = (await activate_cash(db, view["id"]))["enrollment"]
    vehicles = [c["vehicle_id"] for c in enrollment["cars"]]
    other_captain = await make_captain(db, t["a"]["id"])
    cleanup.append(("users", {"_id": ObjectId(other_captain)}))
    sid = t["society_a"]["id"]
    near = {"latitude": t["a"]["lat"] + 0.001, "longitude": t["a"]["lng"] + 0.001, "accuracy_m": 12}
    async with client() as c:
        h_other = auth(other_captain, "captain", t["a"]["id"])
        assert (await c.post(f"/api/v1/societies/captain/{sid}/arrive", headers=h_other, json=near)).status_code == 403
        assert (await c.get("/api/v1/societies/captain/today", headers=h_other)).json()["data"]["societies"] == []
        h = auth(t["captain_a"], "captain", t["a"]["id"])
        # Ticking before arriving is refused.
        assert (await c.put(f"/api/v1/societies/captain/{sid}/washed", headers=h, json={"vehicle_ids": vehicles})).status_code == 400
        resp = await c.post(f"/api/v1/societies/captain/{sid}/arrive", headers=h, json=near)
        assert resp.status_code == 200, resp.text
        card = resp.json()["data"]
        assert card["attendance"]["far_from_society"] is False and card["attendance"]["distance_m"] < 50
        assert {x["vehicle_id"] for x in card["cars"]} == set(vehicles)
        assert "phone" not in json.dumps(card["cars"])
        resp = await c.put(f"/api/v1/societies/captain/{sid}/washed", headers=h, json={"vehicle_ids": vehicles})
        assert resp.status_code == 200 and resp.json()["data"]["attendance"]["washed_count"] == 2
        # A car that isn't on a live plan here can't be ticked.
        resp = await c.put(f"/api/v1/societies/captain/{sid}/washed", headers=h, json={"vehicle_ids": [str(ObjectId())]})
        assert resp.status_code == 400
        today = (await c.get("/api/v1/societies/captain/today", headers=h)).json()["data"]
        assert [s["id"] for s in today["societies"]] == [sid]
        # The manager sees today's attendance on the calendar.
        hm = auth(t["manager_a"], "manager", t["a"]["id"])
        month = (await c.get(f"/api/v1/societies/{sid}/attendance", headers=hm)).json()["data"]
        today_row = next(d for d in month["days"] if d["date"] == now_ist().date().isoformat())
        assert today_row["present"] and today_row["washed_count"] == 2 and month["present_days"] == 1


async def test_substitute_takes_over_for_the_day_and_far_arrivals_are_flagged(db, cleanup, two_centers):
    t = two_centers
    service = SocietyService(db)
    sid = t["society_a"]["id"]
    society = await service.societies.find_by_id(sid)
    await service.set_captain(society, t["captain_a"], None)
    sub_captain = await make_captain(db, t["a"]["id"])
    cleanup.append(("users", {"_id": ObjectId(sub_captain)}))
    society = await service.societies.find_by_id(sid)
    await service.set_captain(society, sub_captain, now_ist().date().isoformat())
    far = {"latitude": t["a"]["lat"] + 0.05, "longitude": t["a"]["lng"]}
    async with client() as c:
        assert (await c.post(f"/api/v1/societies/captain/{sid}/arrive", headers=auth(t["captain_a"], "captain", t["a"]["id"]), json=far)).status_code == 403
        resp = await c.post(f"/api/v1/societies/captain/{sid}/arrive", headers=auth(sub_captain, "captain", t["a"]["id"]), json=far)
        assert resp.status_code == 200
        assert resp.json()["data"]["attendance"]["far_from_society"] is True
    assert await db.notifications.count_documents({"user_id": t["manager_a"], "title": "Society arrival flagged"}) == 1


async def test_bucket_allowance_is_enforced_per_cycle(db, cleanup, two_centers):
    t = two_centers
    service = SocietyService(db)
    sid = t["society_a"]["id"]
    await service.set_captain(await service.societies.find_by_id(sid), t["captain_a"], None)
    plan = await make_template(db, cleanup, bucket_days=1, count=1, price=499, mrp=599)
    resident = await make_resident(db, cleanup)
    view = await enroll(db, t["society_a"], resident, plan["id"], cars=1)
    enrollment = (await activate_cash(db, view["id"]))["enrollment"]
    vehicle = enrollment["cars"][0]["vehicle_id"]
    # The cycle started three days ago and the car was washed two days ago.
    started = datetime.now(timezone.utc) - timedelta(days=3)
    await db.user_subscriptions.update_one({"vehicle_id": vehicle, "society_id": sid}, {"$set": {"cycle_start": started, "start_date": started}})
    two_days_ago = (now_ist().date() - timedelta(days=2)).isoformat()
    await db.society_attendance.insert_one({"society_id": sid, "date": two_days_ago, "captain_id": t["captain_a"], "washed_vehicle_ids": [vehicle]})
    h = auth(t["captain_a"], "captain", t["a"]["id"])
    async with client() as c:
        await c.post(f"/api/v1/societies/captain/{sid}/arrive", headers=h, json={"latitude": t["a"]["lat"], "longitude": t["a"]["lng"]})
        card = (await c.get("/api/v1/societies/captain/today", headers=h)).json()["data"]["societies"][0]
        car = next(x for x in card["cars"] if x["vehicle_id"] == vehicle)
        assert car["used"] == 1 and car["allowance_left"] == 0
        resp = await c.put(f"/api/v1/societies/captain/{sid}/washed", headers=h, json={"vehicle_ids": [vehicle]})
        assert resp.status_code == 400 and "used all 1" in resp.json()["message"]


async def test_missing_attendance_alert_is_bounded_and_once_a_day(db, cleanup, two_centers, monkeypatch):
    t = two_centers
    service = SocietyService(db)
    await service.set_captain(await service.societies.find_by_id(t["society_a"]["id"]), t["captain_a"], None)
    from app.services import society_service as module

    noon = now_ist().replace(hour=12, minute=0)
    if noon.weekday() == 6:
        noon = noon + timedelta(days=1)
    monkeypatch.setattr(module, "now_ist", lambda: noon)
    monkeypatch.setattr(module, "today_ist", lambda: noon.date())
    first = await service.alert_missing_attendance()
    second = await service.alert_missing_attendance()
    assert first >= 1 and second == 0
    assert await db.notifications.count_documents({"user_id": t["manager_a"], "title": "Society attendance missing"}) == 1
    # Before the alert hour nothing is even read.
    monkeypatch.setattr(module, "now_ist", lambda: noon.replace(hour=9))
    assert await service.alert_missing_attendance() == 0


async def test_society_edit_ignores_nulls_that_would_erase_the_pin(db, cleanup):
    from tests.society_factories import make_center, make_society
    from app.schemas.society_schema import SocietyUpdateRequest
    from app.services.society_service import SocietyService

    center = await make_center(db, cleanup, spot=3)
    society = await make_society(db, cleanup, center)
    service = SocietyService(db)
    raw = await service.societies.find_by_id(society["id"])
    await service.update_society(raw, SocietyUpdateRequest(latitude=None, longitude=None, is_active=None, form_enabled=None, notes=None))
    after = await db.societies.find_one({"_id": raw["_id"]})
    assert after["latitude"] == raw["latitude"] and after["longitude"] == raw["longitude"]
    assert after["is_active"] is True and after.get("form_enabled") == raw.get("form_enabled")
    assert after.get("notes") is None  # a clearable field may still be blanked


async def test_far_future_month_is_a_400_not_a_500(db, cleanup):
    from tests.society_factories import auth, client, make_center, make_society, staff

    center = await make_center(db, cleanup, spot=4)
    society = await make_society(db, cleanup, center)
    manager_id, _ = await staff(db, cleanup, center["id"])
    h = auth(manager_id, "manager", center["id"])
    async with client() as c:
        assert (await c.get("/api/v1/societies?month=9999-12", headers=h)).status_code == 400
        assert (await c.get(f"/api/v1/societies/{society['id']}/attendance?month=9999-12", headers=h)).status_code == 400
