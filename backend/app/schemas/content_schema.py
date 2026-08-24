from typing import Optional

from pydantic import BaseModel, EmailStr, Field


class FaqCreateRequest(BaseModel):
    question: str
    answer: str
    display_order: int = 0
    is_active: bool = True


class FaqUpdateRequest(BaseModel):
    question: Optional[str] = None
    answer: Optional[str] = None
    display_order: Optional[int] = None
    is_active: Optional[bool] = None


class TestimonialCreateRequest(BaseModel):
    customer_name: str
    customer_image: Optional[str] = None
    rating: int = Field(default=5, ge=1, le=5)
    comment: str
    is_featured: bool = True
    display_order: int = 0


class TestimonialUpdateRequest(BaseModel):
    customer_name: Optional[str] = None
    customer_image: Optional[str] = None
    rating: Optional[int] = None
    comment: Optional[str] = None
    is_featured: Optional[bool] = None
    display_order: Optional[int] = None


class SettingUpsertRequest(BaseModel):
    key: str
    value: dict
    description: Optional[str] = None


class ContactMessageCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    phone: str = Field(min_length=6, max_length=20)
    email: EmailStr
    message: str = Field(min_length=1, max_length=2000)


class BookingPolicyUpdateRequest(BaseModel):
    operating_start: Optional[str] = None
    operating_end: Optional[str] = None
    min_lead_minutes: Optional[int] = Field(default=None, ge=0)
    slot_granularity_minutes: Optional[int] = Field(default=None, ge=5)
    captain_travel_buffer_minutes: Optional[int] = Field(default=None, ge=0)
    photo_geofence_radius_m: Optional[int] = Field(default=None, ge=10)
    late_start_grace_minutes: Optional[int] = Field(default=None, ge=0)
    wallet_gating_enabled: Optional[bool] = None


class HomepageConfigUpdateRequest(BaseModel):
    hero_badge_text: Optional[str] = None
    hero_headline: Optional[str] = None
    hero_subtext: Optional[str] = None
    featured_service_id: Optional[str] = None
    featured_combo_ids: Optional[list[str]] = None
    banner_active: Optional[bool] = None
    banner_text: Optional[str] = None
