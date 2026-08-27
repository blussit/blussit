from typing import Optional

from pydantic import BaseModel, Field


class ReviewCreateRequest(BaseModel):
    booking_id: str
    captain_rating: int = Field(ge=1, le=5)
    captain_comment: Optional[str] = Field(default=None, max_length=1000)
    service_rating: int = Field(ge=1, le=5)
    service_comment: Optional[str] = Field(default=None, max_length=1000)


class ReviewUpdateRequest(BaseModel):
    captain_rating: Optional[int] = Field(default=None, ge=1, le=5)
    captain_comment: Optional[str] = Field(default=None, max_length=1000)
    service_rating: Optional[int] = Field(default=None, ge=1, le=5)
    service_comment: Optional[str] = Field(default=None, max_length=1000)
