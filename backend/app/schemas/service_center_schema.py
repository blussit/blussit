import re
from typing import Optional

from pydantic import BaseModel, Field, field_validator, model_validator

from app.models.service_center import DEFAULT_WORKING_HOURS_END, DEFAULT_WORKING_HOURS_START

# 24-hour HH:MM, zero-padded ("07:00", "19:30") — the only form the slot
# generator understands. "9am" was accepted and then took the center's whole
# slot API down with a 500 (audit ADM-07).
_HHMM = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


def _hhmm(value: Optional[str]) -> Optional[str]:
    if value is None:
        return value
    value = value.strip()
    if not _HHMM.match(value):
        raise ValueError("Use 24-hour HH:MM, e.g. 07:00 or 19:00")
    return value


def ensure_opens_before_closes(start: Optional[str], end: Optional[str]) -> None:
    """Shared by the schemas and ServiceCenterService.update (which checks a
    one-sided edit against the stored other half)."""
    if start and end and start >= end:
        raise ValueError("The center must open before it closes (e.g. 07:00 – 19:00).")


class ServiceCenterLocationSchema(BaseModel):
    address: str
    city: str
    state: str
    pincode: str
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    service_pincodes: list[str] = []
    radius_km: float = 6.0


class ServiceCenterCreateRequest(BaseModel):
    name: str
    location: ServiceCenterLocationSchema
    manager_id: Optional[str] = None
    contact_phone: Optional[str] = None
    contact_email: Optional[str] = None
    working_hours_start: str = DEFAULT_WORKING_HOURS_START
    working_hours_end: str = DEFAULT_WORKING_HOURS_END
    slot_duration_minutes: Optional[int] = Field(default=None, ge=5, le=720)
    max_bookings_per_day: Optional[int] = Field(default=None, ge=0)
    default_slot_capacity: Optional[int] = Field(default=None, ge=0)

    _hours = field_validator("working_hours_start", "working_hours_end")(_hhmm)

    @model_validator(mode="after")
    def _opens_before_closes(self) -> "ServiceCenterCreateRequest":
        ensure_opens_before_closes(self.working_hours_start, self.working_hours_end)
        return self


class ServiceCenterUpdateRequest(BaseModel):
    name: Optional[str] = None
    location: Optional[ServiceCenterLocationSchema] = None
    manager_id: Optional[str] = None
    contact_phone: Optional[str] = None
    contact_email: Optional[str] = None
    working_hours_start: Optional[str] = None
    working_hours_end: Optional[str] = None
    slot_duration_minutes: Optional[int] = Field(default=None, ge=5, le=720)
    max_bookings_per_day: Optional[int] = Field(default=None, ge=0)
    default_slot_capacity: Optional[int] = Field(default=None, ge=0)
    is_active: Optional[bool] = None

    _hours = field_validator("working_hours_start", "working_hours_end")(_hhmm)

    @model_validator(mode="after")
    def _opens_before_closes(self) -> "ServiceCenterUpdateRequest":
        ensure_opens_before_closes(self.working_hours_start, self.working_hours_end)
        return self
