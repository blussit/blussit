from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import InventoryUnit


class InventoryCreateRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    service_center_id: str = Field(pattern=r"^[0-9a-fA-F]{24}$")
    item_name: str = Field(min_length=1, max_length=100)
    unit: InventoryUnit
    quantity_available: float = Field(ge=0, le=1_000_000)
    reorder_level: float = Field(0, ge=0, le=1_000_000)


class InventoryUpdateRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    item_name: Optional[str] = Field(None, min_length=1, max_length=100)
    unit: Optional[InventoryUnit] = None
    quantity_available: Optional[float] = Field(None, ge=0, le=1_000_000)
    reorder_level: Optional[float] = Field(None, ge=0, le=1_000_000)


class InventoryAdjustRequest(BaseModel):
    delta: float = Field(ge=-1_000_000, le=1_000_000, description="Positive to add stock, negative to consume stock")
    reason: Optional[str] = Field(None, max_length=300)
