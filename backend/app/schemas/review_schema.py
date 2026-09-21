from typing import Optional

from pydantic import BaseModel, Field


class ReviewCreateRequest(BaseModel):
    booking_id: str
    # Optional ONLY for a job with no captain (done by the manager) — the
    # service enforces it for every booking that had one.
    captain_rating: Optional[int] = Field(default=None, ge=1, le=5)
    captain_comment: Optional[str] = Field(default=None, max_length=1000)
    service_rating: int = Field(ge=1, le=5)
    service_comment: Optional[str] = Field(default=None, max_length=1000)


class ReviewUpdateRequest(BaseModel):
    captain_rating: Optional[int] = Field(default=None, ge=1, le=5)
    captain_comment: Optional[str] = Field(default=None, max_length=1000)
    service_rating: Optional[int] = Field(default=None, ge=1, le=5)
    service_comment: Optional[str] = Field(default=None, max_length=1000)
