from datetime import date
from typing import Optional

from pydantic import BaseModel

from app.models.enums import AttendanceStatus, LeaveStatus


class CheckInRequest(BaseModel):
    notes: Optional[str] = None
    # Where the captain physically is at check-in — recorded and shown to
    # the manager, deliberately NOT geofenced (a captain may start their
    # day heading straight to the first booking).
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    accuracy_m: Optional[float] = None


class CheckOutRequest(BaseModel):
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    accuracy_m: Optional[float] = None


class LeaveRequestCreate(BaseModel):
    start_date: date
    end_date: date
    reason: str


class LeaveReviewRequest(BaseModel):
    status: LeaveStatus
    review_note: Optional[str] = None
