"""
Homepage merchandising — what the landing page actually shows, entirely
admin-controlled instead of hardcoded in the frontend. Same settings-collection
pattern as booking_policy and pricing_config.
"""
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.repositories.content_repository import SettingRepository

DEFAULT_HOMEPAGE_CONFIG = {
    "hero_badge_text": "Professional care",
    "hero_headline": "Car wash at your doorstep",
    "hero_subtext": "We come to you. You relax. We make your car shine like new.",
    "featured_service_id": None,   # drives the hero's "first wash / regular price" cards
    "featured_combo_ids": [],      # shown as highlighted cards on the landing page
    "banner_active": False,
    "banner_text": "",
}


class HomepageConfigService:
    def __init__(self, db: AsyncIOMotorDatabase):
        self.settings_repo = SettingRepository(db)

    async def get_config(self) -> dict:
        setting = await self.settings_repo.get_by_key("homepage_config")
        config = dict(DEFAULT_HOMEPAGE_CONFIG)
        if setting:
            config.update(setting.get("value", {}))
        return config

    async def set_config(self, updates: dict) -> dict:
        current = await self.get_config()
        current.update({k: v for k, v in updates.items() if v is not None})
        await self.settings_repo.upsert("homepage_config", current, "Landing page merchandising")
        return current
