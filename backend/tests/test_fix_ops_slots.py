"""
OPS remediation — slot views outside the booking engine read the SAME slot
list and capacity resolution as the engine (BookingService.center_slots /
_slot_capacity_defaults):

- an unconfigured slot (no capacity policy names it, no center default, no
  daily cap) takes no bookings — the society planner used to count it as
  999 free seats and the manager dashboard showed it as open capacity;
- a center with no stored hours uses the platform's real hours (7:00 AM –
  7:00 PM), never a hard-coded 08:00–20:00.
"""
from datetime import timedelta

import pytest
from bson import ObjectId

from app.utils.timezone import now_ist
from tests.factories import make_service_center

pytestmark = pytest.mark.asyncio


async def _bare_center(db, cleanup, **unset_extra) -> str:
    """A center with no slot capacity configured anywhere."""
    center = await make_service_center(db)
    await db.service_centers.update_one(
        {"_id": ObjectId(center)},
        {"$unset": {"default_slot_capacity": "", "max_bookings_per_day": "", **unset_extra}},
    )
    cleanup.append(("service_centers", {"_id": ObjectId(center)}))
    cleanup.append(("slot_capacity", {"service_center_id": center}))
    cleanup.append(("capacity_policies", {"service_center_id": center}))
    return center


async def test_society_planner_counts_an_unconfigured_slot_as_no_room(db, cleanup):
    from app.services.society_schedule_service import SocietyScheduleService

    center = await _bare_center(db, cleanup)
    day = (now_ist() + timedelta(days=2)).date().isoformat()
    room = await SocietyScheduleService(db)._slot_room(center, [day])
    assert room, "the center's slots should be listed"
    assert all(v != 999 for v in room.values()), room
    assert set(room.values()) == {0}, room


async def test_society_planner_uses_configured_capacity(db, cleanup):
    from app.services.society_schedule_service import SocietyScheduleService

    center = await _bare_center(db, cleanup)
    await db.service_centers.update_one({"_id": ObjectId(center)}, {"$set": {"default_slot_capacity": 4}})
    day = (now_ist() + timedelta(days=2)).date().isoformat()
    room = await SocietyScheduleService(db)._slot_room(center, [day])
    assert set(room.values()) == {4}, room


async def test_society_planner_slots_follow_center_hours_or_the_platform_default(db, cleanup):
    from app.services.society_schedule_service import SocietyScheduleService

    center = await _bare_center(db, cleanup, working_hours_start="", working_hours_end="")
    doc = await db.service_centers.find_one({"_id": ObjectId(center)})
    keys = sorted(await SocietyScheduleService(db)._slots(doc))
    assert keys[0].startswith("07:00"), keys
    assert not any(k.startswith("19:") or k.endswith("-20:00") for k in keys), keys


async def test_manager_dashboard_never_shows_an_unconfigured_slot_as_open_seats(db, cleanup):
    from app.services.manager_dashboard_service import ManagerDashboardService

    center = await _bare_center(db, cleanup, working_hours_start="", working_hours_end="")
    doc = await db.service_centers.find_one({"_id": ObjectId(center)})
    today = await ManagerDashboardService(db)._today_schedule(center, doc)
    assert today["slots"], today
    assert today["slots"][0]["key"].startswith("07:00"), [s["key"] for s in today["slots"]]
    for slot in today["slots"]:
        assert slot["capacity"] in (0, None) and slot["capacity"] != 999, slot
        assert slot["capacity_configured"] is False, slot
    assert not today["capacity"]

    await db.service_centers.update_one({"_id": ObjectId(center)}, {"$set": {"default_slot_capacity": 3}})
    doc = await db.service_centers.find_one({"_id": ObjectId(center)})
    today = await ManagerDashboardService(db)._today_schedule(center, doc)
    assert {s["capacity"] for s in today["slots"]} == {3}
    assert all(s["capacity_configured"] for s in today["slots"])
    assert today["capacity"] == 3 * len(today["slots"])
