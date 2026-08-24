from typing import Optional

from pydantic import BaseModel


class VehicleTypeCreateRequest(BaseModel):
    name: str
    display_order: int = 0
    is_active: bool = True


class VehicleTypeUpdateRequest(BaseModel):
    name: Optional[str] = None
    display_order: Optional[int] = None
    is_active: Optional[bool] = None
