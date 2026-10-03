"""
Society support (docs/SOCIETY_PLANS.md §9): resident issues as tagged
support tickets (scoping: resident own, manager own center, admin all) and
landing-page society requests (public, deduped, routed by pincode).
"""
import itertools

import pytest
from bson import ObjectId

from app.core.rate_limit import RULES

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
    staff,
)

pytestmark = pytest.mark.asyncio
_seq = itertools.count(1)


@pytest.fixture
async def rig(db, cleanup):
    a = await make_center(db, cleanup, spot=5)
    b = await make_center(db, cleanup, spot=1)
    manager_a, _ = await staff(db, cleanup, a["id"])
    manager_b, _ = await staff(db, cleanup, b["id"])
    society_a = await make_society(db, cleanup, a, name=f"Lotus Enclave {next(_seq)}")
    society_b = await make_society(db, cleanup, b, name=f"Orchid Towers {next(_seq)}")
    plan = await make_template(db, cleanup)
    admin_id = await make_admin(db, cleanup)
    cleanup.append(("complaints", {"society_id": {"$in": [society_a["id"], society_b["id"]]}}))
    cleanup.append(("notifications", {"user_id": admin_id}))
    return {"a": a, "b": b, "manager_a": manager_a, "manager_b": manager_b, "society_a": society_a, "society_b": society_b, "plan": plan, "admin": admin_id}


# -- resident issues ----------------------------------------------------------------


async def test_resident_raises_an_issue_and_only_the_right_staff_see_it(db, cleanup, rig):
    resident = await make_resident(db, cleanup)
    view = await enroll(db, rig["society_a"], resident, rig["plan"]["id"], cars=2)
    enrollment = (await activate_cash(db, view["id"]))["enrollment"]
    car = enrollment["cars"][1]
    stranger = await make_resident(db, cleanup)
    neighbour = await make_resident(db, cleanup)
    await enroll(db, rig["society_a"], neighbour, rig["plan"]["id"], cars=1)
    sid = rig["society_a"]["id"]
    hr = auth(str(resident["_id"]), "customer")
    ha = auth(rig["manager_a"], "manager", rig["a"]["id"])
    hb = auth(rig["manager_b"], "manager", rig["b"]["id"])
    async with client() as c:
        resp = await c.post("/api/v1/society-issues", headers=hr, json={
            "society_id": sid, "issue_type": "daily_wash_missed", "vehicle_id": car["vehicle_id"], "note": "Not washed today",
        })
        assert resp.status_code == 200, resp.text
        issue = resp.json()["data"]
        assert issue["category"] == "society" and issue["society_id"] == sid and issue["issue_type"] == "daily_wash_missed"
        assert issue["registration_number"] == car["registration_number"] and issue["service_center_id"] == rig["a"]["id"]
        assert issue["subject"].startswith("Daily wash missed") and issue["priority"] == "high" and issue["status"] == "open"
        # Same issue, same car, still open: not filed twice.
        again = await c.post("/api/v1/society-issues", headers=hr, json={"society_id": sid, "issue_type": "daily_wash_missed", "vehicle_id": car["vehicle_id"]})
        assert again.status_code == 409
        # Not a resident there / not their car: refused, nothing filed.
        assert (await c.post("/api/v1/society-issues", headers=auth(str(stranger["_id"]), "customer"),
                             json={"society_id": sid, "issue_type": "billing"})).status_code == 404
        assert (await c.post("/api/v1/society-issues", headers=hr, json={"society_id": rig["society_b"]["id"], "issue_type": "billing"})).status_code == 404
        assert (await c.post("/api/v1/society-issues", headers=auth(str(neighbour["_id"]), "customer"),
                             json={"society_id": sid, "issue_type": "billing", "vehicle_id": car["vehicle_id"]})).status_code == 400
        # "Other" needs a few words.
        assert (await c.post("/api/v1/society-issues", headers=hr, json={"society_id": sid, "issue_type": "other"})).status_code == 422
        # Staff can't file as a resident.
        assert (await c.post("/api/v1/society-issues", headers=ha, json={"society_id": sid, "issue_type": "billing"})).status_code == 403

        # Resident: their own, in their support list too.
        mine = (await c.get(f"/api/v1/society-issues/my?society_id={sid}", headers=hr)).json()["data"]
        assert [i["id"] for i in mine] == [issue["id"]]
        support = (await c.get("/api/v1/complaints/my", headers=hr)).json()["data"]
        assert issue["id"] in {i["id"] for i in support}
        assert (await c.get(f"/api/v1/society-issues/my?society_id={sid}", headers=auth(str(neighbour["_id"]), "customer"))).json()["data"] == []
        assert (await c.get(f"/api/v1/complaints/{issue['id']}", headers=auth(str(neighbour["_id"]), "customer"))).status_code == 404

        # Manager A: on the society page and under "Society issues".
        listed = (await c.get(f"/api/v1/society-issues/society/{sid}?status=open", headers=ha)).json()["data"]
        assert [i["id"] for i in listed] == [issue["id"]]
        filtered = (await c.get(f"/api/v1/complaints/center/{rig['a']['id']}?category=society", headers=ha)).json()["data"]
        assert [i["id"] for i in filtered] == [issue["id"]]
        by_society = (await c.get(f"/api/v1/complaints/center/{rig['a']['id']}?society_id={sid}", headers=ha)).json()["data"]
        assert [i["id"] for i in by_society] == [issue["id"]]
        assert issue["id"] not in {i["id"] for i in (await c.get(f"/api/v1/complaints/center/{rig['a']['id']}?category=booking", headers=ha)).json()["data"]}
        detail = (await c.get(f"/api/v1/societies/{sid}", headers=ha)).json()["data"]
        assert detail["open_issues"] == 1
        rows = (await c.get("/api/v1/societies", headers=ha)).json()["data"]
        assert rows["kpis"]["open_issues"] == 1 and rows["rows"][0]["open_issues"] == 1
        # Manager A replies + resolves through the normal ticket thread.
        resp = await c.post(f"/api/v1/complaints/{issue['id']}/reply", headers=ha, json={"message": "Captain will redo it at 4 PM", "status": "resolved"})
        assert resp.status_code == 200
        assert (await c.get(f"/api/v1/societies/{sid}", headers=ha)).json()["data"]["open_issues"] == 0

        # Manager B (other center): nothing.
        assert (await c.get(f"/api/v1/society-issues/society/{sid}", headers=hb)).status_code == 403
        assert (await c.get(f"/api/v1/complaints/center/{rig['a']['id']}?category=society", headers=hb)).status_code == 403
        assert (await c.get(f"/api/v1/complaints/{issue['id']}", headers=hb)).status_code == 403
        assert (await c.get(f"/api/v1/complaints/center/{rig['b']['id']}?category=society", headers=hb)).json()["data"] == []

        # Admin: everything.
        admin = (await c.get(f"/api/v1/complaints?category=society&society_id={sid}", headers=auth(rig["admin"], "admin"))).json()["data"]
        assert [i["id"] for i in admin] == [issue["id"]]
    # The center's manager heard about it in-app.
    note = await db.notifications.find_one({"user_id": rig["manager_a"], "title": "Society issue"})
    assert note and car["registration_number"] in note["message"]
    assert not await db.notifications.find_one({"user_id": rig["manager_b"], "title": "Society issue"})


# -- society requests (landing page) ------------------------------------------------


def _lead(pincode: str, **over) -> dict:
    n = next(_seq)
    return {"society_name": f"Silver Oaks {n}", "area": "Scheme 54", "pincode": pincode, "contact_name": "Mr Jain",
            "phone": f"98{n:08d}", "approx_cars": 60, "note": "Two towers", **over}


async def test_public_society_request_reaches_the_right_manager(db, cleanup, rig):
    cleanup.append(("society_leads", {"pincode": {"$in": [rig["a"]["pincode"], "999111"]}}))
    body = _lead(rig["a"]["pincode"])
    ha = auth(rig["manager_a"], "manager", rig["a"]["id"])
    hb = auth(rig["manager_b"], "manager", rig["b"]["id"])
    async with client() as c:
        resp = await c.post("/api/v1/society-leads", json=body)
        assert resp.status_code == 200 and resp.json()["data"] is None  # nothing about anyone comes back
        # Same phone + same society within 24 h: merged, not a second request.
        again = await c.post("/api/v1/society-leads", json={**body, "society_name": body["society_name"].upper() + " ", "approx_cars": 80})
        assert again.status_code == 200
        assert await db.society_leads.count_documents({"phone": body["phone"]}) == 1
        lead = await db.society_leads.find_one({"phone": body["phone"]})
        assert lead["requests_count"] == 2 and lead["approx_cars"] == 80 and lead["service_center_id"] == rig["a"]["id"]
        # Bad phone / pincode: refused.
        assert (await c.post("/api/v1/society-leads", json={**body, "phone": "12345"})).status_code == 422
        assert (await c.post("/api/v1/society-leads", json={**body, "pincode": "45"})).status_code == 422
        # A pincode no center serves: admin's.
        orphan = _lead("999111")
        assert (await c.post("/api/v1/society-leads", json=orphan)).status_code == 200

        mine = (await c.get("/api/v1/society-leads", headers=ha)).json()["data"]
        assert [r["id"] for r in mine["rows"]] == [str(lead["_id"])] and mine["counts"]["new"] == 1
        assert mine["rows"][0]["phone"] == body["phone"] and mine["rows"][0]["status"] == "new"
        # A manager filter can't widen the list.
        assert (await c.get(f"/api/v1/society-leads?center_id={rig['b']['id']}", headers=ha)).json()["data"]["rows"][0]["id"] == str(lead["_id"])
        assert (await c.get("/api/v1/society-leads", headers=hb)).json()["data"]["rows"] == []
        admin_h = auth(rig["admin"], "admin")
        unassigned = (await c.get("/api/v1/society-leads?center_id=unassigned", headers=admin_h)).json()["data"]["rows"]
        assert orphan["phone"] in {r["phone"] for r in unassigned}
        # Public / customers can't list.
        assert (await c.get("/api/v1/society-leads")).status_code in (401, 403)

        # Status: own center only.
        assert (await c.put(f"/api/v1/society-leads/{lead['_id']}", headers=hb, json={"status": "contacted"})).status_code == 403
        resp = await c.put(f"/api/v1/society-leads/{lead['_id']}", headers=ha, json={"status": "contacted", "staff_note": "Visit Sat 11 AM"})
        assert resp.status_code == 200 and resp.json()["data"]["status"] == "contacted"
        assert (await c.put(f"/api/v1/society-leads/{lead['_id']}", headers=ha, json={"status": "bogus"})).status_code == 422

        # "Register this society" links the request to the new society.
        resp = await c.post("/api/v1/societies", headers=ha, json={
            "name": body["society_name"], "address_line": "Scheme 54, AB Road", "pincode": rig["a"]["pincode"],
            "latitude": rig["a"]["lat"] + 0.002, "longitude": rig["a"]["lng"], "lead_id": str(lead["_id"]),
        })
        assert resp.status_code == 200, resp.text
        society_id = resp.json()["data"]["id"]
        cleanup.append(("societies", {"_id": ObjectId(society_id)}))
        fresh = await db.society_leads.find_one({"_id": lead["_id"]})
        assert fresh["status"] == "registered" and fresh["society_id"] == society_id
        # Manager B can't use A's request to register anything.
        orphan_doc = await db.society_leads.find_one({"phone": orphan["phone"]})
        resp = await c.post("/api/v1/societies", headers=hb, json={
            "name": "Sneaky", "address_line": "1 Road", "pincode": rig["b"]["pincode"], "latitude": rig["b"]["lat"] + 0.001,
            "longitude": rig["b"]["lng"], "lead_id": str(orphan_doc["_id"]),
        })
        assert resp.status_code == 403
    # Managers of the center were told in-app; admins for the unmatched one.
    assert await db.notifications.count_documents({"user_id": rig["manager_a"], "title": "New society request"}) == 1
    assert not await db.notifications.find_one({"user_id": rig["manager_b"], "title": "New society request"})
    assert await db.notifications.find_one({"user_id": rig["admin"], "title": "New society request"})


async def test_society_request_is_rate_limited_and_pitch_price_is_public(db, cleanup, rig):
    assert ("/api/v1/society-leads", 6, 60, "POST") in RULES
    # More specific than the catch-all, so it's the bucket that applies.
    prefixes = [r[0] for r in RULES]
    assert prefixes.index("/api/v1/society-leads") < prefixes.index("/api/v1/")
    async with client() as c:
        info = (await c.get("/api/v1/society-leads/info")).json()["data"]
    assert info["from_price"] is not None and info["from_price"] <= 1649
