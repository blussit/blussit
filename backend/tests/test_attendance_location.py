"""
Attendance with location: check-in/out record WHERE they happened (no
geofence rule — a captain may start the day anywhere, the manager just
sees the pin), check-out computes worked_minutes, and both leave a
captain_locations breadcrumb.
"""
from datetime import timedelta

import pytest
from bson import ObjectId

from app.schemas.staff_ops_schema import CheckInRequest, CheckOutRequest
from app.services.staff_ops_service import AttendanceService
from app.utils.timezone import now_ist

from tests.factories import make_captain, make_service_center


@pytest.fixture
async def rig(db, cleanup):
    center_id = await make_service_center(db)
    cleanup.append(("service_centers", {"_id": ObjectId(center_id)}))
    captain_id = await make_captain(db, center_id)
    cleanup.append(("users", {"_id": ObjectId(captain_id)}))
    cleanup.append(("captain_wallets", {"captain_id": captain_id}))
    cleanup.append(("attendance", {"captain_id": captain_id}))
    cleanup.append(("captain_locations", {"captain_id": captain_id}))
    return {"db": db, "captain_id": captain_id}


@pytest.mark.asyncio
async def test_check_in_records_location_and_breadcrumb(rig, db):
    svc = AttendanceService(db)
    row = await svc.check_in(rig["captain_id"], CheckInRequest(latitude=22.71, longitude=75.85))
    assert row["check_in_location"] == {"latitude": 22.71, "longitude": 75.85}
    crumb = await db.captain_locations.find_one({"captain_id": rig["captain_id"], "source": "check_in"})
    assert crumb is not None


@pytest.mark.asyncio
async def test_check_out_computes_worked_minutes(rig, db):
    svc = AttendanceService(db)
    created = await svc.check_in(rig["captain_id"], CheckInRequest(latitude=22.71, longitude=75.85))
    # Backdate the check-in ~95 minutes so the hours math has something real.
    backdated = (now_ist() - timedelta(minutes=95)).isoformat()
    await db.attendance.update_one({"_id": ObjectId(created["id"])}, {"$set": {"check_in_time": backdated}})

    row = await svc.check_out(rig["captain_id"], CheckOutRequest(latitude=22.72, longitude=75.86))
    assert row["check_out_location"] == {"latitude": 22.72, "longitude": 75.86}
    assert 93 <= row["worked_minutes"] <= 97
    crumb = await db.captain_locations.find_one({"captain_id": rig["captain_id"], "source": "check_out"})
    assert crumb is not None


@pytest.mark.asyncio
async def test_check_in_without_gps_is_accepted(rig, db):
    """GPS denied/broken must never block a work day — it just records nothing."""
    svc = AttendanceService(db)
    row = await svc.check_in(rig["captain_id"], CheckInRequest())
    assert row["check_in_location"] is None
