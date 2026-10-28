"""Request shapes for the festival / broadcast campaigns (admin only)."""
from datetime import date
from typing import Literal

from pydantic import BaseModel, Field

MAX_SELECTION_IDS = 20000


class AudienceFilter(BaseModel):
    search: str = Field(default="", max_length=100)
    center_id: str | None = Field(default=None, max_length=40)
    city: str | None = Field(default=None, max_length=80)
    last_booking_from: date | None = None
    last_booking_to: date | None = None
    no_booking: bool | None = None
    has_active_plan: bool | None = None
    bookings_min: int | None = Field(default=None, ge=0, le=100000)
    bookings_max: int | None = Field(default=None, ge=0, le=100000)


class Selection(BaseModel):
    """Who is picked. `all` = everyone matching `filter` except
    `excluded_ids`; `none` / `ids` = only `included_ids` (still subject to
    the eligibility rules). The server resolves it — a 100 000-person
    "Select All" is a filter and a few exclusions, never 100 000 ids."""
    mode: Literal["all", "none", "ids"] = "none"
    filter: AudienceFilter = Field(default_factory=AudienceFilter)
    excluded_ids: list[str] = Field(default_factory=list, max_length=MAX_SELECTION_IDS)
    included_ids: list[str] = Field(default_factory=list, max_length=MAX_SELECTION_IDS)


class CampaignCreate(BaseModel):
    name: str = Field(min_length=2, max_length=80)
    message: str = Field(min_length=1, max_length=2000)
    image_url: str = Field(min_length=10, max_length=500)


class CampaignUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=80)
    message: str | None = Field(default=None, min_length=1, max_length=2000)
    image_url: str | None = Field(default=None, min_length=10, max_length=500)


class SelectionBody(BaseModel):
    selection: Selection


class SendBody(BaseModel):
    selection: Selection
    # The count the admin typed in the confirmation box.
    confirm_count: int = Field(ge=0)


class LimitsUpdate(BaseModel):
    whatsapp_marketing_cost_paise: int = Field(ge=0, le=2000)
