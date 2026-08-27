"""
Centralized application configuration.
All environment-dependent values are read from here — never hardcode
config values anywhere else in the codebase.
"""
from functools import lru_cache
from typing import List

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # App
    APP_NAME: str = "Doorstep Vehicle Care Platform"
    APP_ENV: str = "development"
    API_V1_PREFIX: str = "/api/v1"
    DEBUG: bool = True

    # Mongo
    MONGO_URI: str = "mongodb://localhost:27017"
    MONGO_DB_NAME: str = "doorstep_vehicle_care"

    # JWT
    JWT_SECRET_KEY: str = "change-this-super-secret-key-in-production"
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60
    REFRESH_TOKEN_EXPIRE_DAYS: int = 7

    # CORS
    CORS_ORIGINS: str = "http://localhost:5173,http://localhost:3000"

    # Storage abstraction (Phase 2: Cloudinary). "local" writes uploaded
    # files to UPLOAD_DIR on disk and serves them back via the app's own
    # StaticFiles mount at /uploads — see app/core/storage.py. Never store
    # uploaded file bytes (e.g. base64) directly in a MongoDB document: it
    # was tried for captain before/after photos, ballooned each booking
    # document to ~1.4MB, and made every full-document read on the bookings
    # collection punishingly slow (count_documents/index-only queries stayed
    # fast; find() effectively hung) — see KNOWN_ISSUES.md.
    STORAGE_PROVIDER: str = "local"
    UPLOAD_DIR: str = "uploads"
    PUBLIC_BASE_URL: str = "http://localhost:8000"
    CLOUDINARY_CLOUD_NAME: str = ""
    CLOUDINARY_API_KEY: str = ""
    CLOUDINARY_API_SECRET: str = ""

    # Pagination
    DEFAULT_PAGE_SIZE: int = 20
    MAX_PAGE_SIZE: int = 100

    # WhatsApp messaging (OTPs, temp passwords, purchase/booking updates) —
    # same provider-abstraction shape as STORAGE_PROVIDER above.
    # "log" (default) never calls a real API: it writes every message to
    # the whatsapp_outbox collection instead, so OTP/notification flows are
    # fully testable with zero external dependency. Set WHATSAPP_PROVIDER
    # to "meta_cloud" and fill in the two keys below to send for real via
    # the official WhatsApp Cloud API (https://developers.facebook.com/docs/whatsapp/cloud-api) —
    # see app/services/whatsapp_service.py. Even with meta_cloud selected,
    # the factory silently falls back to "log" if either key is blank,
    # so a half-configured .env can never crash a booking/OTP flow.
    WHATSAPP_PROVIDER: str = "log"
    WHATSAPP_ACCESS_TOKEN: str = ""
    WHATSAPP_PHONE_NUMBER_ID: str = ""
    # Not used for sending (messages only need the phone number id above) —
    # kept for whenever an incoming-webhook receiver is added, which is
    # scoped to the WABA rather than one phone number.
    WHATSAPP_BUSINESS_ACCOUNT_ID: str = ""
    WHATSAPP_API_VERSION: str = "v21.0"

    @property
    def cors_origins_list(self) -> List[str]:
        return [origin.strip() for origin in self.CORS_ORIGINS.split(",") if origin.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
