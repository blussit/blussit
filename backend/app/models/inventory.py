from typing import Optional

from app.models.base import BusinessRecordBase
from app.models.enums import InventoryUnit


class InventoryModel(BusinessRecordBase):
    service_center_id: str
    item_name: str
    unit: InventoryUnit
    quantity_available: float
    reorder_level: float = 0
    last_restocked_at: Optional[str] = None
