from typing import Optional

from pydantic import BaseModel, Field

from app.models.enums import InventoryUnit


class InventoryCreateRequest(BaseModel):
    service_center_id: str
    item_name: str
    unit: InventoryUnit
    quantity_available: float = Field(ge=0)
    reorder_level: float = 0


class InventoryUpdateRequest(BaseModel):
    item_name: Optional[str] = None
    unit: Optional[InventoryUnit] = None
    quantity_available: Optional[float] = None
    reorder_level: Optional[float] = None


class InventoryAdjustRequest(BaseModel):
    delta: float = Field(description="Positive to add stock, negative to consume stock")
    reason: Optional[str] = None
