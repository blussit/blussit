"""Admin input for holidays. Dates and times are IST wall-clock values as
the admin types them; HolidayService turns them into UTC instants."""
from typing import Literal

from pydantic import BaseModel, Field, field_validator

DATE_PATTERN = r"^\d{4}-\d{2}-\d{2}$"
TIME_PATTERN = r"^([01]\d|2[0-3]):[0-5]\d$"


class _HolidayFields(BaseModel):
    @field_validator("message", "title", check_fields=False)
    @classmethod
    def _strip(cls, value):
        return value.strip() if isinstance(value, str) else value

    @field_validator("service_center_ids", check_fields=False)
    @classmethod
    def _unique_ids(cls, value):
        if value is None:
            return value
        seen: list[str] = []
        for item in value:
            item = str(item).strip()
            if item and item not in seen:
                seen.append(item)
        return seen


class HolidayCreateRequest(_HolidayFields):
    title: str | None = Field(default=None, max_length=80)
    message: str = Field(min_length=1, max_length=300)
    scope: Literal["all", "centers"] = "all"
    service_center_ids: list[str] = Field(default_factory=list, max_length=50)
    start_date: str = Field(pattern=DATE_PATTERN)
    # Optional: the holiday starts at 00:00 IST unless a time is given.
    start_time: str | None = Field(default=None, pattern=TIME_PATTERN)
    end_date: str = Field(pattern=DATE_PATTERN)
    # Optional: the holiday runs to the end of end_date unless a time is given.
    end_time: str | None = Field(default=None, pattern=TIME_PATTERN)
    # When the website popup starts showing: this many days before the start.
    notice_days_before: int = Field(default=2, ge=0, le=30)

    @field_validator("message")
    @classmethod
    def _message_not_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Write a short message for customers.")
        return value


class HolidayUpdateRequest(_HolidayFields):
    """Every field optional — what is sent replaces the stored value
    (start_time / end_time / title may be sent as null to clear them)."""

    title: str | None = Field(default=None, max_length=80)
    message: str | None = Field(default=None, min_length=1, max_length=300)
    scope: Literal["all", "centers"] | None = None
    service_center_ids: list[str] | None = Field(default=None, max_length=50)
    start_date: str | None = Field(default=None, pattern=DATE_PATTERN)
    start_time: str | None = Field(default=None, pattern=TIME_PATTERN)
    end_date: str | None = Field(default=None, pattern=DATE_PATTERN)
    end_time: str | None = Field(default=None, pattern=TIME_PATTERN)
    notice_days_before: int | None = Field(default=None, ge=0, le=30)
