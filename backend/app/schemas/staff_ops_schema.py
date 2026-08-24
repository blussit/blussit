from datetime import date
from typing import Optional

from pydantic import BaseModel

from app.models.enums import AttendanceStatus, LeaveStatus


class CheckInRequest(BaseModel):
    notes: Optional[str] = None


class LeaveRequestCreate(BaseModel):
    start_date: date
    end_date: date
    reason: str


class LeaveReviewRequest(BaseModel):
    status: LeaveStatus
    review_note: Optional[str] = None
