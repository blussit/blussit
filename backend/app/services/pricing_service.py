"""
Pricing & commission engine.

Admin controls two global levers (stored in the `settings` collection under
key "pricing_config"):
  - per_km_rate: ₹ paid to the captain per km of distance between the
    service center and the customer's address (covers fuel/travel).
  - default_captain_service_fee: ₹ flat fee paid to the captain per booking
    for doing the work itself, used when a specific service doesn't
    override it.

Each Service can optionally set its own `captain_fee` (see Service model)
to override the default — e.g. a premium detailing job pays the captain
more than a quick exterior wash.

For a booking with subtotal `service_price` and `distance_km` from the
assigned service center to the customer:

    captain_travel_pay = distance_km * per_km_rate
    captain_service_pay = service.captain_fee or default_captain_service_fee
    captain_earning = captain_travel_pay + captain_service_pay
    platform_earning = service_price - captain_earning

`captain_earning` is clamped so it never exceeds the service price (a
captain can never earn more than the customer paid) and never goes below
zero.

The same config also carries the CUSTOMER distance charge, which is a
different thing entirely: a visit that includes a `charges_travel` service
pays `customer_per_km_rate` for every km beyond `customer_free_km`
(travel_quote below — the one place that number is computed). It is
platform revenue and never enters the captain split above.

Settlement then depends on how the customer paid — see WalletService for
the credit/debit logic:
  - Online payment  -> platform holds 100%, CREDITS captain_earning to the
    captain's wallet.
  - Cash payment     -> captain holds 100% in hand, so the platform DEBITS
    platform_earning from the captain's wallet (reconciling what the
    captain owes the platform since they collected the customer's cash
    directly).
"""
from decimal import ROUND_HALF_UP, Decimal

from motor.motor_asyncio import AsyncIOMotorDatabase

from app.repositories.catalog_repository import ServiceRepository
from app.repositories.content_repository import SettingRepository

DEFAULT_PRICING_CONFIG = {
    "per_km_rate": 5.0,
    "default_captain_service_fee": 40.0,
    "customer_free_km": 5.0,
    "customer_per_km_rate": 2.0,
}


def compute_travel_quote(distance_km: float | None, free_km: float, per_km_rate: float) -> dict:
    """Customer distance charge, rounded half-up to whole rupees — so the
    "3 km · ₹6" the customer reads always adds up (a ceil turned 3.01 km into
    ₹7). Rounded to paise first so float drift can't tip a half-rupee."""
    distance = max(0.0, float(distance_km or 0.0))
    paise = Decimal(str(round(max(0.0, distance - free_km) * per_km_rate, 2)))
    charge = int(paise.quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    return {"distance_km": round(distance, 2), "free_km": free_km, "per_km_rate": per_km_rate, "charge": charge}


class PricingService:
    def __init__(self, db: AsyncIOMotorDatabase):
        self.settings_repo = SettingRepository(db)
        self.service_repo = ServiceRepository(db)

    async def get_pricing_config(self) -> dict:
        setting = await self.settings_repo.get_by_key("pricing_config")
        if not setting:
            return dict(DEFAULT_PRICING_CONFIG)
        config = dict(DEFAULT_PRICING_CONFIG)
        config.update(setting.get("value", {}))
        return config

    async def set_pricing_config(
        self,
        per_km_rate: float,
        default_captain_service_fee: float,
        updated_by: str | None = None,
        customer_free_km: float | None = None,
        customer_per_km_rate: float | None = None,
    ) -> dict:
        # Omitted customer fields keep their current value, so an older
        # client that only knows the captain fields can't wipe them.
        current = await self.get_pricing_config()
        return await self.settings_repo.upsert(
            "pricing_config",
            {
                "per_km_rate": per_km_rate,
                "default_captain_service_fee": default_captain_service_fee,
                "customer_free_km": current["customer_free_km"] if customer_free_km is None else customer_free_km,
                "customer_per_km_rate": current["customer_per_km_rate"] if customer_per_km_rate is None else customer_per_km_rate,
            },
            "Captain payout + customer distance charge configuration",
            updated_by=updated_by,
        )

    async def travel_quote(self, distance_km: float | None) -> dict:
        config = await self.get_pricing_config()
        return compute_travel_quote(distance_km, float(config["customer_free_km"]), float(config["customer_per_km_rate"]))

    async def calculate_split(self, service_price: float, distance_km: float, service_doc: dict | None) -> dict:
        config = await self.get_pricing_config()
        per_km_rate = config["per_km_rate"]
        default_fee = config["default_captain_service_fee"]

        captain_fee = (service_doc or {}).get("captain_fee")
        if captain_fee is None:
            captain_fee = default_fee

        captain_travel_pay = round(distance_km * per_km_rate, 2)
        captain_service_pay = round(captain_fee, 2)
        captain_earning = round(captain_travel_pay + captain_service_pay, 2)

        # Never let the captain's cut exceed what the customer actually paid for this service.
        captain_earning = max(0.0, min(captain_earning, service_price))
        platform_earning = round(service_price - captain_earning, 2)

        return {
            "distance_km": round(distance_km, 2),
            "per_km_rate": per_km_rate,
            "captain_travel_pay": captain_travel_pay,
            "captain_service_pay": captain_service_pay,
            "captain_earning": captain_earning,
            "platform_earning": platform_earning,
        }
