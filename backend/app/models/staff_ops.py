from datetime import date

from app.models.base import BusinessRecordBase
from app.models.enums import AttendanceStatus, LeaveStatus


class AttendanceModel(BusinessRecordBase):
    captain_id: str
    service_center_id: str
    attendance_date: date
    status: AttendanceStatus = AttendanceStatus.PRESENT
    check_in_time: str | None = None
    check_out_time: str | None = None
    # Where the check-in/out physically happened ({"latitude","longitude"}).
    # Informational only — captains may legitimately start their day
    # anywhere (e.g. straight to the first booking), so there's deliberately
    # no center geofence rule here; the manager just sees the pin.
    check_in_location: dict | None = None
    check_out_location: dict | None = None
    worked_minutes: int | None = None  # computed at check-out
    notes: str | None = None


class LeaveRequestModel(BusinessRecordBase):
    captain_id: str
    start_date: date
    end_date: date
    reason: str
    status: LeaveStatus = LeaveStatus.PENDING
    reviewed_by: str | None = None
    review_note: str | None = None
