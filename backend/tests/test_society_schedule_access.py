"""
Society schedule — who can see and do what (docs/SOCIETY_PLANS.md §9.5),
over real HTTP: managers own center only (403 elsewhere), admin everywhere,
captains read-only and only the visit days they're on (404 otherwise),
residents only their own scheduled washes.
"""
from datetime import timedelta

import pytest
from bson import ObjectId

from app.schemas.society_schedule_schema import VisitCreateRequest
from app.services.society_schedule_service import SocietyScheduleService
from app.utils.timezone import now_ist

from tests.factories import make_captain
from tests.society_factories import activate_cash, auth, client, enroll, make_admin, make_center, make_resident, make_society, make_template, staff

pytestmark = pytest.mark.asyncio


@pytest.fixture
async def two(db, cleanup):
    a = await make_center(db, cleanup, spot=1)
    b = await make_center(db, cleanup, spot=2)
    manager_a, captain_a = await staff(db, cleanup, a["id"])
    manager_b, captain_b = await staff(db, cleanup, b["id"])
    society_a = await make_society(db, cleanup, a, name="Alpha Heights")
    society_b = await make_society(db, cleanup, b, name="Beta Towers")
    for s in (society_a, society_b):
        for coll in ("society_visits", "society_schedule_rules", "society_schedule_requests"):
            cleanup.append((coll, {"society_id": s["id"]}))
    plan = await make_template(db, cleanup)
    admin = await make_admin(db, cleanup)
    tomorrow = (now_ist().date() + timedelta(days=1)).isoformat()
    visit = await SocietyScheduleService(db).create_visit(
        await db.societies.find_one({"_id": ObjectId(society_a["id"])}),
        VisitCreateRequest(date=tomorrow, slot_keys=["08:00-11:00"], captain_ids=[captain_a], washes_per_captain=6),
        manager_a,
    )
    return {"a": a, "b": b, "manager_a": manager_a, "captain_a": captain_a, "manager_b": manager_b, "captain_b": captain_b,
            "society_a": society_a, "society_b": society_b, "plan": plan, "admin": admin, "visit": visit, "tomorrow": tomorrow}


async def test_manager_of_another_center_is_refused_everywhere(db, cleanup, two):
    t = two
    rule = await SocietyScheduleService(db).rules.create({
        "society_id": t["society_a"]["id"], "service_center_id": t["a"]["id"], "kind": "society",
        "pattern": {"kind": "weekly", "weekday": 5}, "slot_keys": ["08:00-11:00"], "captain_ids": [], "washes_per_captain": 6,
        "is_active": True, "materialized_until": None,
    })
    req = await db.society_schedule_requests.insert_one({
        "visit_id": t["visit"]["id"], "society_id": t["society_a"]["id"], "service_center_id": t["a"]["id"],
        "customer_id": "someone", "subscription_ids": [], "kind": "skip", "status": "pending",
    })
    cleanup.append(("society_schedule_requests", {"_id": req.inserted_id}))
    h = auth(t["manager_b"], "manager", t["b"]["id"])
    sid, vid, rid = t["society_a"]["id"], t["visit"]["id"], str(rule["_id"])
    weekly = {"pattern": {"kind": "weekly", "weekday": 5}, "slot_keys": ["08:00-11:00"], "washes_per_captain": 6}
    async with client() as c:
        for method, path, body in [
            ("get", f"/api/v1/society-schedule/societies/{sid}", None),
            ("post", f"/api/v1/society-schedule/societies/{sid}/rules", weekly),
            ("post", f"/api/v1/society-schedule/societies/{sid}/visits", {"date": t["tomorrow"], "slot_keys": ["08:00-11:00"]}),
            ("put", f"/api/v1/society-schedule/rules/{rid}", weekly),
            ("delete", f"/api/v1/society-schedule/rules/{rid}", None),
            ("patch", f"/api/v1/society-schedule/visits/{vid}", {"washes_per_captain": 4}),
            ("post", f"/api/v1/society-schedule/visits/{vid}/skip", None),
            ("post", f"/api/v1/society-schedule/visits/{vid}/exclude", {"subscription_ids": ["x"]}),
            ("post", f"/api/v1/society-schedule/visits/{vid}/book", None),
            ("post", f"/api/v1/society-schedule/requests/{req.inserted_id}/approve", {}),
            ("post", f"/api/v1/society-schedule/requests/{req.inserted_id}/decline", {}),
        ]:
            resp = await c.request(method.upper(), path, headers=h, **({"json": body} if body is not None else {}))
            assert resp.status_code == 403, (path, resp.status_code, resp.text)
        # The planner is always the manager's own center, whatever is asked.
        resp = await c.get(f"/api/v1/society-schedule/planner?center_id={t['a']['id']}", headers=h)
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["center"]["id"] == t["b"]["id"] and {s["id"] for s in data["societies"]} == {t["society_b"]["id"]}
        # Their own society: fine.
        ok = await c.get(f"/api/v1/society-schedule/societies/{t['society_b']['id']}", headers=h)
        assert ok.status_code == 200
        # Requests list is scoped to their center.
        listed = (await c.get("/api/v1/society-schedule/requests", headers=h)).json()["data"]
        assert all(r["society_id"] != sid for r in listed)
    visit = await db.society_visits.find_one({"_id": ObjectId(vid)})
    assert visit["status"] == "planned" and visit["washes_per_captain"] == 6


async def test_own_manager_and_admin_can_plan(db, cleanup, two):
    t = two
    async with client() as c:
        h = auth(t["manager_a"], "manager", t["a"]["id"])
        resp = await c.patch(f"/api/v1/society-schedule/visits/{t['visit']['id']}", headers=h, json={"washes_per_captain": 4})
        assert resp.status_code == 200, resp.text
        # A captain from another center can't be put on this society's day.
        resp = await c.patch(f"/api/v1/society-schedule/visits/{t['visit']['id']}", headers=h, json={"captain_ids": [t["captain_b"]]})
        assert resp.status_code == 400
        # Tomorrow-or-later only: residents get a day's notice.
        today = now_ist().date().isoformat()
        resp = await c.post(f"/api/v1/society-schedule/societies/{t['society_a']['id']}/visits", headers=h, json={"date": today, "slot_keys": ["08:00-11:00"]})
        assert resp.status_code == 400
        ha = auth(t["admin"], "admin")
        assert (await c.get("/api/v1/society-schedule/planner", headers=ha)).status_code == 400  # pick a center
        resp = await c.get(f"/api/v1/society-schedule/planner?center_id={t['b']['id']}", headers=ha)
        assert resp.status_code == 200 and {s["id"] for s in resp.json()["data"]["societies"]} == {t["society_b"]["id"]}
        resp = await c.post(f"/api/v1/society-schedule/visits/{t['visit']['id']}/skip", headers=ha)
        assert resp.status_code == 200
        # Only admin changes the global settings.
        assert (await c.put("/api/v1/society-schedule/settings", headers=h, json={"generate_days_ahead": 3, "default_washes_per_captain": 6})).status_code == 403


async def test_captain_sees_only_the_days_they_are_on(db, cleanup, two):
    t = two
    other_captain = await make_captain(db, t["a"]["id"])
    cleanup.append(("users", {"_id": ObjectId(other_captain)}))
    vid = t["visit"]["id"]
    async with client() as c:
        mine = auth(t["captain_a"], "captain", t["a"]["id"])
        resp = await c.get("/api/v1/society-schedule/captain/visits", headers=mine)
        assert resp.status_code == 200
        assert [v["id"] for v in resp.json()["data"]["visits"]] == [vid]
        assert (await c.get(f"/api/v1/society-schedule/captain/visits/{vid}", headers=mine)).status_code == 200
        for who, center in ((other_captain, t["a"]["id"]), (t["captain_b"], t["b"]["id"])):
            h = auth(who, "captain", center)
            assert (await c.get(f"/api/v1/society-schedule/captain/visits/{vid}", headers=h)).status_code == 404
            assert (await c.get("/api/v1/society-schedule/captain/visits", headers=h)).json()["data"]["visits"] == []
        # Read-only: no planning routes for a captain.
        assert (await c.post(f"/api/v1/society-schedule/visits/{vid}/skip", headers=mine)).status_code == 403
        assert (await c.get(f"/api/v1/society-schedule/societies/{t['society_a']['id']}", headers=mine)).status_code == 403


async def test_residents_see_and_change_only_their_own(db, cleanup, two):
    t = two
    a = await make_resident(db, cleanup)
    b = await make_resident(db, cleanup)
    for r in (a, b):
        view = await enroll(db, t["society_a"], r, t["plan"]["id"], cars=1)
        await activate_cash(db, view["id"])
    async with client() as c:
        ha = auth(str(a["_id"]), "customer")
        data = (await c.get("/api/v1/society-schedule/my", headers=ha)).json()["data"]
        assert data["items"] and data["items"][0]["visit_id"] == t["visit"]["id"]
        mine = {car["sub_id"] for item in data["items"] for car in item["cars"]}
        own = {str(s["_id"]) for s in await db.user_subscriptions.find({"customer_id": str(a["_id"])}).to_list(None)}
        assert mine and mine <= own
        assert data["items"][0]["slot_label"] == "8:00 AM – 11:00 AM"
        # Another resident's view never shows a's cars.
        hb = auth(str(b["_id"]), "customer")
        other = (await c.get("/api/v1/society-schedule/my", headers=hb)).json()["data"]
        assert not ({car["sub_id"] for item in other["items"] for car in item["cars"]} & own)
        # Asking to change a wash they aren't on -> 404.
        outsider = await make_resident(db, cleanup)
        ho = auth(str(outsider["_id"]), "customer")
        resp = await c.post("/api/v1/society-schedule/my/requests", headers=ho, json={"visit_id": t["visit"]["id"], "kind": "skip"})
        assert resp.status_code == 404
        resp = await c.post("/api/v1/society-schedule/my/requests", headers=ha, json={"visit_id": t["visit"]["id"], "kind": "skip", "note": "Out of town"})
        assert resp.status_code == 200, resp.text
        # Staff-only routes stay closed to residents.
        assert (await c.get("/api/v1/society-schedule/planner", headers=ha)).status_code == 403
