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
    RATE_LIMIT_ENABLED: bool = True
    # Set true ONLY when a trusted reverse proxy (Caddy/nginx) fronts the
    # app AND is configured to overwrite X-Forwarded-For itself. When
    # false (default), rate limiting keys off the socket peer address —
    # honoring the header without a trusted proxy would let any client
    # spoof a fresh "IP" per request and bypass every limit.
    TRUST_PROXY_HEADERS: bool = False

    # Mongo
    MONGO_URI: str = "mongodb://localhost:27017"
    MONGO_DB_NAME: str = "doorstep_vehicle_care"

    # JWT
    JWT_SECRET_KEY: str = "change-this-super-secret-key-in-production"
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 15
    REFRESH_TOKEN_EXPIRE_DAYS: int = 7

    # CORS
    CORS_ORIGINS: str = "http://localhost:5173,http://localhost:3000"

    # Storage abstraction. "local" writes uploaded
    # files to UPLOAD_DIR on disk and serves them back via the app's own
    # StaticFiles mount at /uploads — see app/core/storage.py. "r2" uploads
    # to Cloudflare R2's S3-compatible API and returns the public object URL.
    # Never store
    # uploaded file bytes (e.g. base64) directly in a MongoDB document: it
    # was tried for captain before/after photos, ballooned each booking
    # document to ~1.4MB, and made every full-document read on the bookings
    # collection punishingly slow (count_documents/index-only queries stayed
    # fast; find() effectively hung) — see KNOWN_ISSUES.md.
    STORAGE_PROVIDER: str = "local"
    UPLOAD_DIR: str = "uploads"
    # PUBLIC_BASE_URL (this backend's own public base) is declared ONCE,
    # in the payments block below — local file storage reads the same
    # setting to build absolute photo URLs.
    R2_ENDPOINT_URL: str = ""
    R2_ACCESS_KEY_ID: str = ""
    R2_SECRET_ACCESS_KEY: str = ""
    R2_BUCKET_NAME: str = "blussit-images"
    R2_PRIVATE_BUCKET_NAME: str = "blussit-private-documents"
    R2_PUBLIC_BASE_URL: str = "https://d2208b1ea9cea36f384529411277791e.r2.cloudflarestorage.com/blussit-images"
    R2_UPLOAD_PREFIX: str = "photos"

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
    # Once you've created and gotten Meta's approval for a real
    # "authentication"-category template (its body should have exactly one
    # {{1}} placeholder for the code), set this and OTPs switch from plain
    # text to that template automatically — WhatsAppService.send_otp picks
    # it up with no other code change. Free text (the default, blank here)
    # only reaches a recipient with an open 24h session, so a brand-new
    # customer's very first OTP silently fails to send until this is set.
    WHATSAPP_OTP_TEMPLATE_NAME: str = ""
    WHATSAPP_OTP_TEMPLATE_LANGUAGE: str = "en_US"
    # Utility templates for messages that must reach a recipient WITHOUT an
    # open 24h session (same problem the OTP template solves, for the other
    # message kinds this platform sends):
    #   UPDATE:        body "{{1}}: {{2}}"  — {{1}} title, {{2}} message.
    #                  Used by every NotificationService→WhatsApp bridge
    #                  send (booking confirmations/updates to customers,
    #                  operational alerts to managers/captains).
    #   TEMP_PASSWORD: body with one {{1}} placeholder for the password.
    # Blank = free-text fallback (delivered only inside an open session).
    WHATSAPP_UPDATE_TEMPLATE_NAME: str = ""
    WHATSAPP_TEMP_PASSWORD_TEMPLATE_NAME: str = ""
    WHATSAPP_TEMPLATE_LANGUAGE: str = "en_US"
    # Incoming webhook (customers booking directly over WhatsApp chat).
    # VERIFY_TOKEN: any secret string — paste the same value into the Meta
    # App Dashboard's webhook configuration; Meta echoes it back on the
    # one-time GET verification handshake (see whatsapp_webhook_routes).
    # APP_SECRET: the Meta app's App Secret — when set, every incoming
    # POST's X-Hub-Signature-256 header is verified against it (HMAC of the
    # raw body), so nobody but Meta can inject fake "incoming messages"
    # that would drive the booking bot. Blank = signature check skipped
    # (dev only; set it in production).
    WHATSAPP_WEBHOOK_VERIFY_TOKEN: str = ""
    WHATSAPP_APP_SECRET: str = ""

    # SMS channel for OTPs/temp passwords — the bridge while WhatsApp's
    # Authentication templates are blocked on business verification (and a
    # permanent fallback after). Same provider-abstraction shape as
    # WHATSAPP_PROVIDER: "" disables SMS entirely (default — WhatsApp-only,
    # exactly today's behavior), "log" writes to the sms_outbox collection
    # (dev/tests), "fast2sms" / "msg91" send for real once their key is set.
    SMS_PROVIDER: str = ""
    FAST2SMS_API_KEY: str = ""
    MSG91_AUTH_KEY: str = ""
    MSG91_OTP_TEMPLATE_ID: str = ""
    # MSG91 OTP *widget* (verify.msg91.com) — the widget sends/verifies the
    # OTP entirely on MSG91's side (SMS/WhatsApp/email channels), and the
    # backend only validates the resulting access token via
    # /api/v5/widget/verifyAccessToken. Both values come from the widget's
    # page in the MSG91 dashboard; blank = widget disabled, classic
    # backend-generated OTP flow is used.
    MSG91_WIDGET_ID: str = ""
    MSG91_TOKEN_AUTH: str = ""

    # Razorpay Standard Checkout — KEY_ID is public by design (the browser
    # needs it to open the checkout modal; we hand it out from the
    # create-order response so the frontend needs no env of its own).
    # KEY_SECRET signs orders and verifies payment signatures and must
    # NEVER leave the backend. Blank = online payments disabled (the
    # create-order endpoint refuses with a clear message; cash keeps
    # working).
    # The ACTIVE pair — whatever is here is what customers are charged
    # against. A test key id starts "rzp_test_", a live one "rzp_live_".
    RAZORPAY_KEY_ID: str = ""
    RAZORPAY_KEY_SECRET: str = ""
    # The live pair parked out of the way while testing, so switching back
    # is a copy rather than a hunt through a password manager. NOTHING reads
    # these — they exist to be moved into the pair above.
    RAZORPAY_LIVE_KEY_ID: str = ""
    RAZORPAY_LIVE_KEY_SECRET: str = ""
    # Public https base of THIS backend (e.g. "https://api.blussit.com") —
    # used as the payment-link callback target so the customer's browser
    # lands back on our verified "payment received" page. Blank (dev):
    # links are created without a callback and the reminder-loop's
    # link-status sweep alone marks them paid.
    PUBLIC_BASE_URL: str = ""

    # Google: Maps (browser key is public-by-design, protected by key
    # restrictions in Google Cloud console; server key used for Routes
    # distance/ETA calls) + OAuth sign-in (ID-token flow — verification
    # only needs the public CLIENT_ID; the secret is kept for any future
    # code-flow use and never leaves the server).
    GOOGLE_MAPS_BROWSER_KEY: str = ""
    GOOGLE_MAPS_SERVER_KEY: str = ""
    GOOGLE_OAUTH_CLIENT_ID: str = ""
    # Which channel OTPs/temp passwords try FIRST ("whatsapp" | "sms") —
    # whichever isn't first is the automatic fallback when the first send
    # fails or isn't configured.
    OTP_CHANNEL: str = "whatsapp"

    @property
    def razorpay_mode(self) -> str:
        """"test" | "live" | "unconfigured" — read off the key id itself, so
        it can never disagree with the key actually in use. Surfaced at
        startup and in the create-order response, so nobody mistakes a real
        charge for a test one (or ships to production still in test)."""
        if not self.RAZORPAY_KEY_ID:
            return "unconfigured"
        return "live" if self.RAZORPAY_KEY_ID.startswith("rzp_live_") else "test"

    @property
    def cors_origins_list(self) -> List[str]:
        return [origin.strip() for origin in self.CORS_ORIGINS.split(",") if origin.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
