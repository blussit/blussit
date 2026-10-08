"""
Centralized application configuration.
All environment-dependent values are read from here — never hardcode
config values anywhere else in the codebase.
"""
import os
import re
from functools import lru_cache
from pathlib import Path
from typing import List
from urllib.parse import urlparse

from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parents[2]

# Which env file a LOCAL run reads:
#   1) ENV_FILE if explicitly set
#   2) backend/.env.development when present (local test DB, test creds)
#   3) backend/.env as a compatibility fallback for the same local setup
# We intentionally DO NOT fall back to backend/.env.production here: a laptop
# run must never reach the live database by accident. Cloud Run has no env file
# at all; it gets its values as real environment variables (which always win).

def _resolve_env_file() -> Path:
    env_file = os.environ.get("ENV_FILE")
    if env_file:
        return Path(env_file)

    candidates = (BACKEND_DIR / ".env.development", BACKEND_DIR / ".env")
    for candidate in candidates:
        if candidate.exists():
            return candidate

    return BACKEND_DIR / ".env.development"


ENV_FILE = _resolve_env_file()

_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", "mongo", "host.docker.internal"}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=str(ENV_FILE), extra="ignore")

    # App
    APP_NAME: str = "Doorstep Vehicle Care Platform"
    APP_ENV: str = "development"
    # What this process runs (one image, two Cloud Run services — see
    # docs/ARCHITECTURE.md "Deployment" and app/main.py):
    #   "api"    HTTP routes, webhooks, websockets (+ the cross-instance
    #            relay) and /api/health + /api/ready. NO background loops,
    #            no index builds, no boot backfills.  -> blussit-api
    #   "worker" the background loops (reminder/sweep loop under its Mongo
    #            lease, WhatsApp retry, template sync), index creation and
    #            the idempotent boot backfills, plus its own /api/health +
    #            /api/ready. Every other path answers 404.  -> blussit-worker
    #   "all"    both in one process — local development and tests (default).
    # Production must set it explicitly (validate_production_settings).
    RUN_MODE: str = "all"
    API_V1_PREFIX: str = "/api/v1"
    # Off unless a file/env turns it on (backend/.env.development does): a
    # deploy that forgets it must not publish /api/docs, skip HSTS or relax
    # the production start-up checks.
    DEBUG: bool = False
    RATE_LIMIT_ENABLED: bool = True
    # Set true when the app sits behind proxies that APPEND the address they
    # saw to X-Forwarded-For (Cloud Run's Google front end does). When false
    # (default), rate limiting keys off the socket peer address — which on
    # Cloud Run is Google's front end, i.e. ONE bucket for every customer.
    TRUST_PROXY_HEADERS: bool = False
    # How many trusted proxies append to X-Forwarded-For. The client is the
    # entry at position -TRUSTED_PROXY_COUNT; everything left of it is
    # client-supplied and spoofable. 1 = Cloud Run direct; 2 = an external
    # HTTPS load balancer in front of Cloud Run.
    TRUSTED_PROXY_COUNT: int = 1

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
    # The business's own WhatsApp number (digits, with or without 91). Meta
    # cannot deliver a message from a number to itself — it answers "(#100)
    # Invalid parameter" — so no account may use it as its phone, and any
    # send addressed to it is skipped. Blank = no check.
    WHATSAPP_BUSINESS_NUMBER: str = ""
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
    # (dev/tests), "msg91" sends for real once its auth key is set (template id optional).
    SMS_PROVIDER: str = ""
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
    # The secret typed into Razorpay Dashboard → Webhooks for
    # POST {PUBLIC_BASE_URL}/api/v1/payments/webhook. It signs every event
    # (HMAC-SHA256 of the raw body). Blank = the webhook endpoint answers
    # 404 and the reconciliation sweeps alone catch unverified payments.
    RAZORPAY_WEBHOOK_SECRET: str = ""
    # Public https base of THIS backend (e.g. "https://api.blussit.com") —
    # used as the payment-link callback target so the customer's browser
    # lands back on our verified "payment received" page. Blank (dev):
    # links are created without a callback and the reminder-loop's
    # link-status sweep alone marks them paid.
    PUBLIC_BASE_URL: str = ""
    # Meta Pixel on that "payment received" page, so a website booking paid
    # by link still reports its Purchase to Meta Ads. Public by design (the
    # same id is in frontend/index.html); blank turns it off.
    META_PIXEL_ID: str = "1153209444066340"

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

    # Local testing shortcuts — a fixed OTP for every phone and a one-click
    # "log in as" panel. Honoured ONLY when dev_tools_active (below): a
    # stray true in any non-development, non-local setup is ignored (and in
    # production refused outright — see validate_production_settings).
    DEV_TOOLS_ENABLED: bool = False
    DEV_OTP_CODE: str = "123456"

    # Logs: "" = automatic (one JSON object per line in production, which
    # Cloud Logging parses into severity/message/request_id; plain text
    # elsewhere), or force "json" / "text".
    LOG_FORMAT: str = ""
    # Error tracking (Sentry). Blank = off. Only used when the sentry-sdk
    # package is installed — see app.core.logging_setup.init_error_tracking.
    SENTRY_DSN: str = ""
    SENTRY_TRACES_SAMPLE_RATE: float = 0.0

    @property
    def mongo_is_local(self) -> bool:
        try:
            hosts = urlparse(self.MONGO_URI).netloc.rsplit("@", 1)[-1]
        except ValueError:
            return False
        names = [h.strip().rsplit(":", 1)[0].strip("[]") for h in hosts.split(",") if h.strip()]
        return bool(names) and all(n in _LOCAL_HOSTS for n in names)

    @property
    def dev_tools_active(self) -> bool:
        """All three, or nothing: the flag, APP_ENV=development, and a
        database on this machine. Production can't satisfy the last two."""
        return bool(self.DEV_TOOLS_ENABLED) and self.APP_ENV == "development" and self.mongo_is_local

    @property
    def cors_origins_list(self) -> List[str]:
        return [origin.strip() for origin in self.CORS_ORIGINS.split(",") if origin.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()


# ---------------------------------------------------------------------------
# Start-up configuration checks (P0-3 / DEP-02 / DEP-04 / DEP-06)
#
# ONE list of rules, enforced by the app itself: main.on_startup calls
# ensure_valid_settings() and refuses to start on any problem, and
# scripts/deploy-gcp.sh runs the same checks (`python -m app.core.config
# --check --require-production`) on exactly the values it is about to send
# to Cloud Run, so a deploy that would not boot never ships. Problems name
# the setting and the rule — never a value (most of these are secrets).
# Unknown values fail closed.
# ---------------------------------------------------------------------------

DEFAULT_JWT_SECRET = "change-this-super-secret-key-in-production"
KNOWN_APP_ENVS = ("development", "test", "staging", "production")
_ALLOWED_VALUES = {
    "WHATSAPP_PROVIDER": ("log", "meta_cloud"),
    "STORAGE_PROVIDER": ("local", "r2"),
    "SMS_PROVIDER": ("", "log", "msg91"),
    "OTP_CHANNEL": ("whatsapp", "sms"),
    "LOG_FORMAT": ("", "json", "text"),
    "RUN_MODE": ("api", "worker", "all"),
}
RUN_MODES = _ALLOWED_VALUES["RUN_MODE"]
# Everything that must simply be present in production. Templates (DEP-04):
# outside WhatsApp's 24-hour window only an approved template is delivered —
# without these, captain "New job", complaint/society notices, manager alerts,
# temp passwords and first-time OTPs are silently dropped.
_PRODUCTION_REQUIRED = (
    "WHATSAPP_ACCESS_TOKEN",
    "WHATSAPP_PHONE_NUMBER_ID",
    "WHATSAPP_BUSINESS_ACCOUNT_ID",
    "WHATSAPP_APP_SECRET",
    "WHATSAPP_WEBHOOK_VERIFY_TOKEN",
    "WHATSAPP_OTP_TEMPLATE_NAME",
    "WHATSAPP_UPDATE_TEMPLATE_NAME",
    "WHATSAPP_TEMP_PASSWORD_TEMPLATE_NAME",
    # The business's own number (feature plan 1.7): Meta can't deliver from a
    # number to itself, so without it a manager/customer saved with that
    # number fails every send with "(#100) Invalid parameter".
    "WHATSAPP_BUSINESS_NUMBER",
    "R2_ENDPOINT_URL",
    "R2_ACCESS_KEY_ID",
    "R2_SECRET_ACCESS_KEY",
    "R2_BUCKET_NAME",
    "R2_PRIVATE_BUCKET_NAME",
    "RAZORPAY_KEY_SECRET",
    "RAZORPAY_WEBHOOK_SECRET",
)
_LOCAL_ORIGIN_HOSTS = _LOCAL_HOSTS | {"0.0.0.0"}


class ConfigError(RuntimeError):
    """The configuration is unsafe or incomplete — the app refuses to start.
    `problems` lists every one, so a deploy is fixed in one round."""

    def __init__(self, problems: List[str]):
        self.problems = list(problems)
        super().__init__(
            "Refusing to start — fix the configuration:\n" + "\n".join(f"  - {p}" for p in self.problems)
        )


def _https_host(url: str) -> str | None:
    """The host of an https:// URL, or None when it isn't one."""
    try:
        parsed = urlparse(url.strip())
    except ValueError:
        return None
    if parsed.scheme != "https" or not parsed.hostname:
        return None
    return parsed.hostname.lower()


def _mongo_hosts(uri: str) -> list[str] | None:
    try:
        parsed = urlparse(uri)
    except ValueError:
        return None
    if parsed.scheme not in ("mongodb", "mongodb+srv"):
        return None
    hosts = parsed.netloc.rsplit("@", 1)[-1]
    names = [h.strip().rsplit(":", 1)[0].strip("[]").lower() for h in hosts.split(",") if h.strip()]
    return names or None


def validate_production_settings(s: Settings) -> List[str]:
    """The APP_ENV=production invariants. Empty list = OK (also for any
    other APP_ENV: these rules are production-only)."""
    if s.APP_ENV != "production":
        return []
    problems: List[str] = []

    if s.DEBUG:
        problems.append("DEBUG must be false in production (it publishes /api/docs and relaxes security checks).")
    if s.DEV_TOOLS_ENABLED:
        problems.append("DEV_TOOLS_ENABLED must be false in production (fixed OTP / log-in-as are local-testing tools).")
    if not s.RATE_LIMIT_ENABLED:
        problems.append("RATE_LIMIT_ENABLED must be true in production.")
    if not s.TRUST_PROXY_HEADERS:
        problems.append(
            "TRUST_PROXY_HEADERS must be true in production (behind Cloud Run every client otherwise shares one rate-limit bucket)."
        )
    if s.TRUSTED_PROXY_COUNT < 1:
        problems.append("TRUSTED_PROXY_COUNT must be 1 or more in production (1 = Cloud Run direct).")
    # The default ("all") is for a laptop. A production service must say what
    # it is, so an API revision can never quietly start the sweeps (or a
    # worker revision serve customers) because a variable went missing.
    if "RUN_MODE" not in s.model_fields_set:
        problems.append(
            "RUN_MODE must be set explicitly in production: api (blussit-api) or worker (blussit-worker); "
            "all only as an emergency single-service fallback."
        )

    # Database: never a database on this machine / a docker sidecar.
    hosts = _mongo_hosts(s.MONGO_URI) if s.MONGO_URI else None
    if not s.MONGO_URI:
        problems.append("MONGO_URI is required in production.")
    elif hosts is None:
        problems.append("MONGO_URI must be a mongodb:// or mongodb+srv:// connection string with a host.")
    elif any(h in _LOCAL_HOSTS for h in hosts):
        problems.append("MONGO_URI points at a local database — production must use the live cluster.")

    # Public URLs.
    public_host = _https_host(s.PUBLIC_BASE_URL) if s.PUBLIC_BASE_URL else None
    if not public_host or public_host in _LOCAL_ORIGIN_HOSTS:
        problems.append("PUBLIC_BASE_URL must be this API's public https:// address (e.g. https://api.blussit.com).")

    origins = s.cors_origins_list
    if not origins:
        problems.append("CORS_ORIGINS must list the site's https:// origins.")
    else:
        bad = []
        for origin in origins:
            host = _https_host(origin) if origin != "*" else None
            if host is None or host in _LOCAL_ORIGIN_HOSTS:
                bad.append(origin)
        if bad:
            problems.append(f"CORS_ORIGINS may only hold https:// site origins in production (no '*', http:// or localhost): {bad}")

    # Messaging.
    if s.WHATSAPP_PROVIDER != "meta_cloud":
        problems.append("WHATSAPP_PROVIDER must be meta_cloud in production (the log provider sends nothing).")
    if s.SMS_PROVIDER == "log":
        problems.append("SMS_PROVIDER=log only writes codes to the database — use msg91 (or leave it empty with the MSG91 widget).")
    if s.SMS_PROVIDER == "msg91" and not s.MSG91_AUTH_KEY:
        problems.append("MSG91_AUTH_KEY is required when SMS_PROVIDER=msg91.")
    widget = [bool(s.MSG91_AUTH_KEY), bool(s.MSG91_WIDGET_ID), bool(s.MSG91_TOKEN_AUTH)]
    if any(widget[1:]) and not all(widget):
        problems.append("The MSG91 widget needs all three of MSG91_AUTH_KEY, MSG91_WIDGET_ID and MSG91_TOKEN_AUTH.")
    if s.SMS_PROVIDER != "msg91" and not all(widget):
        problems.append(
            "No SMS fallback for OTPs: set SMS_PROVIDER=msg91 (with MSG91_AUTH_KEY) or the MSG91 widget trio "
            "(MSG91_AUTH_KEY, MSG91_WIDGET_ID, MSG91_TOKEN_AUTH)."
        )

    # Storage.
    if s.STORAGE_PROVIDER != "r2":
        problems.append("STORAGE_PROVIDER must be r2 in production (local disk on Cloud Run is wiped on every restart).")
    r2_host = _https_host(s.R2_PUBLIC_BASE_URL) if s.R2_PUBLIC_BASE_URL else None
    if not r2_host or r2_host.endswith("r2.cloudflarestorage.com"):
        problems.append(
            "R2_PUBLIC_BASE_URL must be the bucket's PUBLIC https:// domain (r2.dev or custom) — "
            "*.r2.cloudflarestorage.com is the private S3 endpoint and its photo URLs never load."
        )

    # Payments (P0-3): live keys only. A test key "takes" payments with test
    # cards — bookings and plans would be marked paid with no money moved.
    key_id = s.RAZORPAY_KEY_ID or ""
    if key_id.startswith("rzp_test_"):
        hint = (
            " Copy the parked RAZORPAY_LIVE_KEY_ID/RAZORPAY_LIVE_KEY_SECRET into RAZORPAY_KEY_ID/RAZORPAY_KEY_SECRET."
            if s.RAZORPAY_LIVE_KEY_ID
            else ""
        )
        problems.append("RAZORPAY_KEY_ID is a Razorpay test key (rzp_test_…) — production must use the live key (rzp_live_…)." + hint)
    elif not key_id.startswith("rzp_live_"):
        problems.append("RAZORPAY_KEY_ID must be the live Razorpay key id (starts with rzp_live_).")

    for name in _PRODUCTION_REQUIRED:
        if not str(getattr(s, name) or "").strip():
            problems.append(f"{name} is required in production.")
    if str(s.WHATSAPP_BUSINESS_NUMBER or "").strip():
        from app.utils.phone import validate_indian_mobile

        if not validate_indian_mobile(s.WHATSAPP_BUSINESS_NUMBER):
            problems.append("WHATSAPP_BUSINESS_NUMBER must be the business's Indian mobile number (10 digits, with or without 91).")
    return problems


def validate_settings(s: Settings) -> List[str]:
    """Every start-up rule for the settings' own APP_ENV."""
    problems: List[str] = []
    if s.APP_ENV not in KNOWN_APP_ENVS:
        problems.append(f"APP_ENV must be one of {', '.join(KNOWN_APP_ENVS)} (exact, lower case).")
    for name, allowed in _ALLOWED_VALUES.items():
        if getattr(s, name) not in allowed:
            shown = ", ".join(repr(v) for v in allowed)
            problems.append(f"{name} must be one of {shown}.")

    # The published default signs tokens anyone can forge: refused outside
    # development whatever DEBUG says; without DEBUG, also a short secret
    # (brute-forceable offline from any one token) and wildcard CORS (with
    # credentials, Starlette echoes back ANY caller's Origin).
    jwt_default = s.JWT_SECRET_KEY == DEFAULT_JWT_SECRET
    if jwt_default and (s.APP_ENV != "development" or not s.DEBUG):
        problems.append("JWT_SECRET_KEY is still the published default — set a long random secret.")
    elif not s.DEBUG and len(s.JWT_SECRET_KEY or "") < 32:
        problems.append("JWT_SECRET_KEY is shorter than 32 characters — set a long random secret.")
    if not s.DEBUG and "*" in s.cors_origins_list:
        problems.append("CORS_ORIGINS contains '*' — list the exact site origins.")

    for problem in validate_production_settings(s):
        if problem not in problems:
            problems.append(problem)
    if s.APP_ENV == "production":
        problems.extend(_messaging_problems(problems))
    return problems


_SETTING_NAME = re.compile(r"\b[A-Z][A-Z0-9]*_[A-Z0-9_]+\b")


def _messaging_problems(already: List[str]) -> List[str]:
    """The WhatsApp service's own production checks
    (whatsapp_service.whatsapp_config_problems — it reads the process
    settings), minus anything already reported above: kept in, so a rule
    the messaging code adds later is enforced at boot and deploy too."""
    try:
        from app.services.whatsapp_service import whatsapp_config_problems
    except ImportError:
        return []
    reported = " ".join(already)
    extra: List[str] = []
    for problem in whatsapp_config_problems():
        names = _SETTING_NAME.findall(problem)
        covered = all(n in reported for n in names) if names else "WHATSAPP_" in reported
        if not covered and problem not in extra:
            extra.append(problem)
    return extra


def ensure_valid_settings(s: Settings | None = None) -> None:
    """Raise ConfigError (refuse to start) when any rule fails."""
    problems = validate_settings(s if s is not None else settings)
    if problems:
        raise ConfigError(problems)


def main(argv: List[str] | None = None) -> int:
    """`python -m app.core.config --check [--require-production]` — the
    deploy script's pre-flight. Builds Settings from the environment it is
    given (the exact values headed for Cloud Run) and prints problems only
    (names and rules, never values). Exit 0 = the app would start."""
    import argparse
    import sys

    parser = argparse.ArgumentParser(prog="python -m app.core.config")
    parser.add_argument("--check", action="store_true", help="validate the configuration and exit")
    parser.add_argument("--require-production", action="store_true", help="also fail unless APP_ENV=production")
    args = parser.parse_args(argv)
    if not args.check:
        parser.print_help()
        return 2
    s = Settings()
    problems = validate_settings(s)
    if args.require_production and s.APP_ENV != "production":
        problems.insert(0, "APP_ENV must be production for a production deploy.")
    if problems:
        print("Configuration problems (the app would refuse to start):", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        return 1
    print(f"Configuration OK for APP_ENV={s.APP_ENV} RUN_MODE={s.RUN_MODE} (Razorpay {s.razorpay_mode}).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
