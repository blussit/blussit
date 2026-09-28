from typing import Optional

from pydantic import BaseModel, Field


class PricingConfigRequest(BaseModel):
    per_km_rate: float = Field(gt=0, description="₹ paid to captain per km traveled from the store")
    default_captain_service_fee: float = Field(ge=0, description="Default flat ₹ paid to captain per booking, unless a service overrides it")
    # Customer distance charge (charges_travel services only). Optional so
    # an older client that omits them keeps the stored values.
    customer_free_km: Optional[float] = Field(default=None, ge=0, description="Km included free before the distance charge starts")
    customer_per_km_rate: Optional[float] = Field(default=None, ge=0, description="₹ per km beyond customer_free_km")
