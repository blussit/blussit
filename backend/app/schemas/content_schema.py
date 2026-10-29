from typing import Annotated, Optional

from pydantic import BaseModel, EmailStr, Field, StringConstraints, field_validator


# FAQ + testimonial limits. They are what the landing page can show without
# breaking its layout, and they refuse empty / whitespace-only text (a blank
# row would render as an empty accordion or an empty quote).
FAQ_QUESTION_MAX = 200
FAQ_ANSWER_MAX = 1500
REVIEW_NAME_MAX = 80
REVIEW_COMMENT_MAX = 600
ORDER_MAX = 10_000

_Question = Annotated[str, StringConstraints(strip_whitespace=True, min_length=5, max_length=FAQ_QUESTION_MAX)]
_Answer = Annotated[str, StringConstraints(strip_whitespace=True, min_length=5, max_length=FAQ_ANSWER_MAX)]
_Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=2, max_length=REVIEW_NAME_MAX)]
_Comment = Annotated[str, StringConstraints(strip_whitespace=True, min_length=5, max_length=REVIEW_COMMENT_MAX)]
_Order = Annotated[int, Field(ge=0, le=ORDER_MAX)]


def _photo_url(value: Optional[str]) -> Optional[str]:
    """A customer photo is a link to an uploaded picture (our own /uploads
    path or an https URL) — never a script/data URI. Blank clears it."""
    if value is None:
        return None
    value = value.strip()
    if not value:
        return None
    if len(value) > 500:
        raise ValueError("The photo link is too long")
    if not (value.startswith("https://") or value.startswith("http://") or value.startswith("/uploads/")):
        raise ValueError("The photo must be an uploaded picture or an https link")
    return value


class FaqCreateRequest(BaseModel):
    question: _Question
    answer: _Answer
    display_order: _Order = 0
    is_active: bool = True


class FaqUpdateRequest(BaseModel):
    question: Optional[_Question] = None
    answer: Optional[_Answer] = None
    display_order: Optional[_Order] = None
    is_active: Optional[bool] = None


class TestimonialCreateRequest(BaseModel):
    customer_name: _Name
    customer_image: Optional[str] = None
    rating: int = Field(default=5, ge=1, le=5)
    comment: _Comment
    is_featured: bool = True
    display_order: _Order = 0

    _photo = field_validator("customer_image")(_photo_url)


class TestimonialUpdateRequest(BaseModel):
    customer_name: Optional[_Name] = None
    # "" / null clears the photo; leave the field out to keep it.
    customer_image: Optional[str] = None
    rating: Optional[int] = Field(default=None, ge=1, le=5)
    comment: Optional[_Comment] = None
    is_featured: Optional[bool] = None
    display_order: Optional[_Order] = None

    _photo = field_validator("customer_image")(_photo_url)


class SettingUpsertRequest(BaseModel):
    """Generic setting write — only whitelisted keys, each value validated
    against its own schema (SettingService.validated_value, ADM-01)."""
    key: str = Field(min_length=1, max_length=64, pattern=r"^[a-z][a-z0-9_]*$")
    value: dict
    description: Optional[str] = Field(None, max_length=300)


class ContactMessageCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    phone: str = Field(min_length=6, max_length=20)
    email: EmailStr
    message: str = Field(min_length=1, max_length=2000)


class BookingPolicyUpdateRequest(BaseModel):
    operating_start: Optional[str] = None
    operating_end: Optional[str] = None
    min_lead_minutes: Optional[int] = Field(default=None, ge=0)
    slot_duration_minutes: Optional[int] = Field(default=None, ge=5)
    slot_booking_cutoff_minutes: Optional[int] = Field(default=None, ge=0)
    max_advance_days: Optional[int] = Field(default=None, ge=1, le=60)
    delay_tolerance_minutes: Optional[int] = Field(default=None, ge=0)
    captain_travel_buffer_minutes: Optional[int] = Field(default=None, ge=0)
    photo_geofence_radius_m: Optional[int] = Field(default=None, ge=10)
    arrival_to_start_tolerance_minutes: Optional[int] = Field(default=None, ge=1)
    # Captain GPS integrity (see geo_integrity_service)
    geo_accuracy_cap_m: Optional[int] = Field(default=None, ge=10, le=1000)
    geo_max_fix_age_s: Optional[int] = Field(default=None, ge=10, le=900)
    geo_future_tolerance_s: Optional[int] = Field(default=None, ge=5, le=600)
    geo_ping_max_age_s: Optional[int] = Field(default=None, ge=60, le=3600)
    geo_trail_grace_s: Optional[int] = Field(default=None, ge=0, le=1800)
    geo_min_trail_pings: Optional[int] = Field(default=None, ge=0, le=20)
    geo_max_speed_kmh: Optional[int] = Field(default=None, ge=60, le=400)
    geo_min_service_seconds: Optional[int] = Field(default=None, ge=0, le=3600)
    geo_override_valid_minutes: Optional[int] = Field(default=None, ge=5, le=240)
    late_start_grace_minutes: Optional[int] = Field(default=None, ge=0)
    late_assignment_grace_minutes: Optional[int] = Field(default=None, ge=0, le=120)
    captain_start_lockout_hours: Optional[int] = Field(default=None, ge=1)
    payment_window_minutes: Optional[int] = Field(default=None, ge=5, le=240)
    payment_reminder_minutes_before: Optional[int] = Field(default=None, ge=0, le=120)
    max_vehicles_per_booking: Optional[int] = Field(default=None, ge=1, le=10)
    repeat_reminder_enabled: Optional[bool] = None
    repeat_reminder_days: Optional[int] = Field(default=None, ge=3, le=365)
    wallet_gating_enabled: Optional[bool] = None


class HomepageConfigUpdateRequest(BaseModel):
    hero_badge_text: Optional[str] = None
    hero_headline: Optional[str] = None
    hero_subtext: Optional[str] = None
    featured_service_id: Optional[str] = None
    featured_combo_ids: Optional[list[str]] = None
    banner_active: Optional[bool] = None
    banner_text: Optional[str] = None
