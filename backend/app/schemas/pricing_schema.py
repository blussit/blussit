from pydantic import BaseModel, Field


class PricingConfigRequest(BaseModel):
    per_km_rate: float = Field(gt=0, description="₹ paid to captain per km traveled from the store")
    default_captain_service_fee: float = Field(ge=0, description="Default flat ₹ paid to captain per booking, unless a service overrides it")
