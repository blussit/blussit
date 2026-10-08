"""Follow-up 2026-10-07 — an on-site add-on makes the captain's job longer
(spec 1.4). It is never blocked for that, but when the new end runs into
the captain's NEXT assigned job, the center's managers get an in-app
heads-up and the response carries `next_job_conflict`."""
from datetime import timedelta

import pytest
from bson import ObjectId

from app.schemas.booking_schema import BookingAssignCaptainRequest
from app.services.booking_service import BookingService
from app.utils.timezone import from_stored
from tests import test_feat_booking_helpers as fb
from tests import test_fix_core_helpers as h

pytestmark = pytest.mark.asyncio


async def _polish(db) -> dict:
    return await db.services.find_one({"slug": "exterior-polish"})


async def _two_jobs(db, gap_after_buffer_minutes: int) -> tuple[dict, dict, dict]:
    """Captain has job A, then job B (another customer) starting exactly
    the travel buffer + `gap_after_buffer_minutes` after A ends."""
    s = await fb.rig(db)
    a = await fb.book(db, s["cu"], s["when"], s["keys"][0])
    await fb.assign(db, a["id"], s)
    a_doc = await fb.doc(db, a["id"])
    a_end = from_stored(a_doc["estimated_start_at"]) + timedelta(minutes=int(a_doc["duration_minutes"]))
    policy = await BookingService(db).policy_service.get_policy()
    b_start = a_end + timedelta(minutes=int(policy["captain_travel_buffer_minutes"]) + gap_after_buffer_minutes)
    other = await h.customer(db, s["pin"])
    b = await fb.book(db, other, s["when"], s["keys"][0])
    await BookingService(db).assign_captain(
        b["id"], BookingAssignCaptainRequest(captain_id=s["cap"]["id"], estimated_start_at=b_start),
        s["mgr"]["id"], "manager", s["center_id"],
    )
    assert (await fb.doc(db, b["id"]))["captain_id"] == s["cap"]["id"]
    return s, a_doc, await fb.doc(db, b["id"])


async def test_add_on_that_runs_into_the_next_job_warns_the_manager_but_is_not_blocked(db):
    s, a, b = await _two_jobs(db, gap_after_buffer_minutes=0)
    polish = await _polish(db)
    out = await BookingService(db).add_services_on_site(
        str(a["_id"]), [str(polish["_id"])], {}, s["mgr"]["id"], "manager", s["center_id"],
    )
    grew = int(polish.get("duration_minutes", 30))
    assert out["added_total"] > 0  # the add-on stands
    conflict = out["next_job_conflict"]
    assert conflict and conflict["booking_id"] == str(b["_id"]) and conflict["booking_number"] == b["booking_number"]
    assert conflict["grew_by_minutes"] == grew and conflict["late_by_minutes"] == grew
    bell = await fb.bell(db, s["mgr"]["id"], f"Next job may start late — {b['booking_number']}")
    assert bell, "manager not told"
    assert "Captain" in bell[0]["message"] and f"grew by {grew} min" in bell[0]["message"]
    assert bell[0]["reference_id"] == str(b["_id"])
    # In-app only.
    assert not await db.whatsapp_queue.find_one({"user_id": s["mgr"]["id"], "title": bell[0]["title"]})


async def test_add_on_with_room_before_the_next_job_says_nothing(db):
    s, a, b = await _two_jobs(db, gap_after_buffer_minutes=120)
    polish = await _polish(db)
    out = await BookingService(db).add_services_on_site(
        str(a["_id"]), [str(polish["_id"])], {}, s["mgr"]["id"], "manager", s["center_id"],
    )
    assert out["next_job_conflict"] is None
    assert not await fb.bell(db, s["mgr"]["id"], f"Next job may start late — {b['booking_number']}")


async def test_captain_add_on_over_http_carries_the_conflict(db):
    s, a, b = await _two_jobs(db, gap_after_buffer_minutes=0)
    await fb.hb.on_the_way(db, str(a["_id"]), s["cap"]["id"], vehicle_verified=True)
    polish = await _polish(db)
    async with h.client() as c:
        r = await c.post(f"/api/v1/bookings/{a['_id']}/add-services", json={"service_ids": [str(polish["_id"])]}, headers=s["cap"]["h"])
    assert r.status_code == 200, r.text
    conflict = r.json()["data"]["next_job_conflict"]
    assert conflict and conflict["booking_number"] == b["booking_number"]


async def test_unassigned_booking_has_no_captain_conflict(db):
    s = await fb.rig(db)
    a = await fb.book(db, s["cu"], s["when"], s["keys"][0])
    polish = await _polish(db)
    out = await BookingService(db).add_services_on_site(a["id"], [str(polish["_id"])], {}, s["mgr"]["id"], "manager", s["center_id"])
    assert out["next_job_conflict"] is None
    assert (await db.bookings.find_one({"_id": ObjectId(a["id"])}))["added_services"]
