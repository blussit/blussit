from typing import Optional

from pydantic import BaseModel, Field


class VehicleTypeCreateRequest(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    display_order: int = 0
    is_active: bool = True


class VehicleTypeUpdateRequest(BaseModel):
    name: Optional[str] = None
    display_order: Optional[int] = None
    is_active: Optional[bool] = None
