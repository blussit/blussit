"""P0-3 / DEP-02 / DEP-04 / DEP-06: production configuration is checked by
the APP at startup (not only by scripts/deploy-gcp.sh), every problem is
reported together, and the app refuses to start on any of them.

The checks run on a Settings object built from explicit values (no env
file), so these tests never read a real .env and never touch a database.
"""
import pytest

from app.core import config
from app.core.config import ConfigError, Settings, ensure_valid_settings, validate_production_settings, validate_settings

LIVE_ID = "rzp_live_AbCdEf123456"
TEST_ID = "rzp_test_AbCdEf123456"

# Everything a production deploy must carry — each test breaks ONE thing.
GOOD_PRODUCTION = {
    "APP_ENV": "production",
    # ARCH 2026-10-07: production must name its run mode explicitly.
    "RUN_MODE": "api",
    "DEBUG": False,
    "DEV_TOOLS_ENABLED": False,
    "RATE_LIMIT_ENABLED": True,
    "TRUST_PROXY_HEADERS": True,
    "TRUSTED_PROXY_COUNT": 1,
    "MONGO_URI": "mongodb+srv://app:pw@cluster0.example.mongodb.net/?retryWrites=true&w=majority",
    "JWT_SECRET_KEY": "x" * 48,
    "CORS_ORIGINS": "https://blussit.com,https://www.blussit.com",
    "PUBLIC_BASE_URL": "https://api.blussit.com",
    "STORAGE_PROVIDER": "r2",
    "R2_ENDPOINT_URL": "https://acct.r2.cloudflarestorage.com",
    "R2_ACCESS_KEY_ID": "ak",
    "R2_SECRET_ACCESS_KEY": "sk",
    "R2_BUCKET_NAME": "blussit-images",
    "R2_PRIVATE_BUCKET_NAME": "blussit-private-documents",
    "R2_PUBLIC_BASE_URL": "https://pub-123.r2.dev",
    "WHATSAPP_PROVIDER": "meta_cloud",
    "WHATSAPP_ACCESS_TOKEN": "tok",
    "WHATSAPP_PHONE_NUMBER_ID": "123",
    "WHATSAPP_BUSINESS_ACCOUNT_ID": "456",
    "WHATSAPP_APP_SECRET": "appsecret",
    "WHATSAPP_WEBHOOK_VERIFY_TOKEN": "verify",
    "WHATSAPP_OTP_TEMPLATE_NAME": "blussit_otp",
    "WHATSAPP_UPDATE_TEMPLATE_NAME": "blussit_service_update",
    "WHATSAPP_TEMP_PASSWORD_TEMPLATE_NAME": "blussit_temp_password",
    # Required in production since 2026-10-07 (feature plan 1.7).
    "WHATSAPP_BUSINESS_NUMBER": "919876500000",
    "SMS_PROVIDER": "msg91",
    "MSG91_AUTH_KEY": "msgkey",
    "OTP_CHANNEL": "whatsapp",
    "RAZORPAY_KEY_ID": LIVE_ID,
    "RAZORPAY_KEY_SECRET": "live_secret",
    "RAZORPAY_WEBHOOK_SECRET": "whsec",
}


def _settings(**overrides) -> Settings:
    # _env_file=None: only the values given here (plus real env vars, which
    # conftest has pinned to safe test values and the overrides replace).
    return Settings(_env_file=None, **{**GOOD_PRODUCTION, **overrides})


def _problems(**overrides) -> list[str]:
    return validate_settings(_settings(**overrides))


def _mentions(problems: list[str], needle: str) -> bool:
    return any(needle in p for p in problems)


# ---------------------------------------------------------------- Razorpay
def test_complete_production_config_passes():
    assert _problems() == []
    ensure_valid_settings(_settings())  # does not raise


def test_test_key_in_production_refuses_to_start():
    problems = _problems(RAZORPAY_KEY_ID=TEST_ID)
    assert _mentions(problems, "RAZORPAY_KEY_ID"), problems
    assert _mentions(problems, "test key")
    with pytest.raises(ConfigError) as err:
        ensure_valid_settings(_settings(RAZORPAY_KEY_ID=TEST_ID))
    assert "RAZORPAY_KEY_ID" in str(err.value)
    # The key value itself is never echoed into the error/log.
    assert TEST_ID not in str(err.value)


def test_test_key_with_parked_live_pair_says_to_swap_them():
    problems = _problems(RAZORPAY_KEY_ID=TEST_ID, RAZORPAY_LIVE_KEY_ID=LIVE_ID, RAZORPAY_LIVE_KEY_SECRET="s")
    assert _mentions(problems, "RAZORPAY_LIVE_KEY_ID"), "the hint points at the parked live pair"


def test_unknown_key_prefix_in_production_fails_closed():
    assert _mentions(_problems(RAZORPAY_KEY_ID="rzp_something_else"), "RAZORPAY_KEY_ID")
    assert _mentions(_problems(RAZORPAY_KEY_ID=""), "RAZORPAY_KEY_ID")


def test_missing_key_secret_fails():
    assert _mentions(_problems(RAZORPAY_KEY_SECRET=""), "RAZORPAY_KEY_SECRET")


def test_missing_webhook_secret_fails():
    assert _mentions(_problems(RAZORPAY_WEBHOOK_SECRET=""), "RAZORPAY_WEBHOOK_SECRET")


def test_development_keeps_test_keys_and_relaxed_rules():
    dev = Settings(
        _env_file=None,
        APP_ENV="development",
        DEBUG=True,
        RAZORPAY_KEY_ID=TEST_ID,
        RAZORPAY_KEY_SECRET="s",
        RAZORPAY_WEBHOOK_SECRET="",
        MONGO_URI="mongodb://127.0.0.1:27017",
        STORAGE_PROVIDER="local",
        WHATSAPP_PROVIDER="log",
        CORS_ORIGINS="http://localhost:5173",
    )
    assert validate_settings(dev) == []
    assert validate_production_settings(dev) == [], "production rules only apply to APP_ENV=production"


# ------------------------------------------------------- DEP-06 invariants
@pytest.mark.parametrize(
    "overrides, needle",
    [
        ({"DEBUG": True}, "DEBUG"),
        ({"DEV_TOOLS_ENABLED": True}, "DEV_TOOLS_ENABLED"),
        ({"RATE_LIMIT_ENABLED": False}, "RATE_LIMIT_ENABLED"),
        ({"TRUST_PROXY_HEADERS": False}, "TRUST_PROXY_HEADERS"),
        ({"TRUSTED_PROXY_COUNT": 0}, "TRUSTED_PROXY_COUNT"),
        # WhatsApp: provider + every credential + every template (DEP-04).
        ({"WHATSAPP_PROVIDER": "log"}, "WHATSAPP_PROVIDER"),
        ({"WHATSAPP_ACCESS_TOKEN": ""}, "WHATSAPP_ACCESS_TOKEN"),
        ({"WHATSAPP_PHONE_NUMBER_ID": ""}, "WHATSAPP_PHONE_NUMBER_ID"),
        ({"WHATSAPP_APP_SECRET": ""}, "WHATSAPP_APP_SECRET"),
        ({"WHATSAPP_WEBHOOK_VERIFY_TOKEN": ""}, "WHATSAPP_WEBHOOK_VERIFY_TOKEN"),
        ({"WHATSAPP_BUSINESS_ACCOUNT_ID": ""}, "WHATSAPP_BUSINESS_ACCOUNT_ID"),
        ({"WHATSAPP_UPDATE_TEMPLATE_NAME": ""}, "WHATSAPP_UPDATE_TEMPLATE_NAME"),
        ({"WHATSAPP_TEMP_PASSWORD_TEMPLATE_NAME": ""}, "WHATSAPP_TEMP_PASSWORD_TEMPLATE_NAME"),
        ({"WHATSAPP_OTP_TEMPLATE_NAME": ""}, "WHATSAPP_OTP_TEMPLATE_NAME"),
        # Storage.
        ({"STORAGE_PROVIDER": "local"}, "STORAGE_PROVIDER"),
        ({"R2_ENDPOINT_URL": ""}, "R2_ENDPOINT_URL"),
        ({"R2_ACCESS_KEY_ID": ""}, "R2_ACCESS_KEY_ID"),
        ({"R2_SECRET_ACCESS_KEY": ""}, "R2_SECRET_ACCESS_KEY"),
        ({"R2_BUCKET_NAME": ""}, "R2_BUCKET_NAME"),
        ({"R2_PRIVATE_BUCKET_NAME": ""}, "R2_PRIVATE_BUCKET_NAME"),
        ({"R2_PUBLIC_BASE_URL": "https://acct.r2.cloudflarestorage.com/blussit-images"}, "R2_PUBLIC_BASE_URL"),
        ({"R2_PUBLIC_BASE_URL": "http://pub-123.r2.dev"}, "R2_PUBLIC_BASE_URL"),
        ({"R2_PUBLIC_BASE_URL": ""}, "R2_PUBLIC_BASE_URL"),
        # Public base URL.
        ({"PUBLIC_BASE_URL": ""}, "PUBLIC_BASE_URL"),
        ({"PUBLIC_BASE_URL": "http://api.blussit.com"}, "PUBLIC_BASE_URL"),
        ({"PUBLIC_BASE_URL": "https://localhost:8000"}, "PUBLIC_BASE_URL"),
        # Database.
        ({"MONGO_URI": "mongodb://localhost:27017"}, "MONGO_URI"),
        ({"MONGO_URI": "mongodb://127.0.0.1:27099/?replicaSet=rs0"}, "MONGO_URI"),
        ({"MONGO_URI": "mongodb://db1.example.com,localhost:27017/?replicaSet=rs0"}, "MONGO_URI"),
        ({"MONGO_URI": ""}, "MONGO_URI"),
        ({"MONGO_URI": "postgres://db.example.com"}, "MONGO_URI"),
        # JWT (already enforced before this fix — kept).
        ({"JWT_SECRET_KEY": "change-this-super-secret-key-in-production"}, "JWT_SECRET_KEY"),
        ({"JWT_SECRET_KEY": "short"}, "JWT_SECRET_KEY"),
        # CORS.
        ({"CORS_ORIGINS": "*"}, "CORS_ORIGINS"),
        ({"CORS_ORIGINS": "https://blussit.com,http://localhost:5173"}, "CORS_ORIGINS"),
        ({"CORS_ORIGINS": "https://blussit.com,http://127.0.0.1:5173"}, "CORS_ORIGINS"),
        ({"CORS_ORIGINS": "http://blussit.com"}, "CORS_ORIGINS"),
        ({"CORS_ORIGINS": ""}, "CORS_ORIGINS"),
        # OTP fallback channel (was deploy-script only).
        ({"SMS_PROVIDER": "log"}, "SMS_PROVIDER"),
        ({"SMS_PROVIDER": "", "MSG91_AUTH_KEY": ""}, "SMS"),
        ({"SMS_PROVIDER": "msg91", "MSG91_AUTH_KEY": ""}, "MSG91_AUTH_KEY"),
    ],
)
def test_each_production_invariant_is_enforced(overrides, needle):
    problems = _problems(**overrides)
    assert _mentions(problems, needle), f"{overrides} -> {problems}"
    with pytest.raises(ConfigError):
        ensure_valid_settings(_settings(**overrides))


def test_widget_trio_counts_as_the_sms_fallback():
    trio = {"SMS_PROVIDER": "", "MSG91_AUTH_KEY": "k", "MSG91_WIDGET_ID": "w", "MSG91_TOKEN_AUTH": "t"}
    assert _problems(**trio) == []
    assert _mentions(_problems(**{**trio, "MSG91_TOKEN_AUTH": ""}), "MSG91")


def test_all_problems_are_reported_together():
    problems = _problems(DEBUG=True, RAZORPAY_KEY_ID=TEST_ID, WHATSAPP_PROVIDER="log", STORAGE_PROVIDER="local", CORS_ORIGINS="*")
    for needle in ("DEBUG", "RAZORPAY_KEY_ID", "WHATSAPP_PROVIDER", "STORAGE_PROVIDER", "CORS_ORIGINS"):
        assert _mentions(problems, needle), problems
    with pytest.raises(ConfigError) as err:
        ensure_valid_settings(_settings(DEBUG=True, RAZORPAY_KEY_ID=TEST_ID, WHATSAPP_PROVIDER="log"))
    assert len(err.value.problems) >= 3


def test_secret_values_never_appear_in_problems():
    secrets = {
        "JWT_SECRET_KEY": "tiny-secret",
        "RAZORPAY_KEY_SECRET": "",
        "MONGO_URI": "mongodb://admin:Sup3rS3cret@localhost:27017",
        "WHATSAPP_ACCESS_TOKEN": "",
    }
    text = " ".join(_problems(**secrets))
    for value in ("tiny-secret", "Sup3rS3cret"):
        assert value not in text


# ------------------------------------------------------ fail closed overall
def test_unknown_app_env_fails_closed():
    assert _mentions(validate_settings(_settings(APP_ENV="prod")), "APP_ENV")
    assert _mentions(validate_settings(_settings(APP_ENV="Production")), "APP_ENV")


@pytest.mark.parametrize(
    "field, value",
    [("WHATSAPP_PROVIDER", "metacloud"), ("STORAGE_PROVIDER", "s3"), ("SMS_PROVIDER", "twilio"), ("OTP_CHANNEL", "email"), ("LOG_FORMAT", "xml")],
)
def test_unknown_provider_values_fail_closed_in_any_env(field, value):
    dev = Settings(_env_file=None, APP_ENV="development", DEBUG=True, **{field: value})
    assert _mentions(validate_settings(dev), field)


def test_staging_without_debug_keeps_the_existing_jwt_and_cors_rules():
    def staging(**overrides) -> Settings:
        values = dict(APP_ENV="staging", DEBUG=False, CORS_ORIGINS="https://staging.blussit.com", JWT_SECRET_KEY="y" * 40)
        return Settings(_env_file=None, **{**values, **overrides})

    assert _mentions(validate_settings(staging(JWT_SECRET_KEY="short")), "JWT_SECRET_KEY")
    assert _mentions(validate_settings(staging(JWT_SECRET_KEY="change-this-super-secret-key-in-production")), "JWT_SECRET_KEY")
    assert _mentions(validate_settings(staging(CORS_ORIGINS="*")), "CORS_ORIGINS")
    assert validate_settings(staging()) == []


# ------------------------------------------------------ startup integration
@pytest.mark.asyncio
async def test_app_startup_refuses_production_test_keys(monkeypatch):
    """The real startup hook raises before it connects to anything."""
    from app import main

    bad = _settings(RAZORPAY_KEY_ID=TEST_ID)
    connected: list[bool] = []

    async def no_connect(*_a, **_k):
        connected.append(True)

    monkeypatch.setattr(main, "settings", bad)
    monkeypatch.setattr(main, "connect_to_mongo", no_connect)
    with pytest.raises(ConfigError):
        await main.on_startup()
    assert connected == [], "refused before touching the database"


def test_cli_check_reports_and_exits_nonzero(monkeypatch, capsys):
    monkeypatch.setattr(config, "Settings", lambda **_k: _settings(RAZORPAY_KEY_ID=TEST_ID))
    assert config.main(["--check", "--require-production"]) == 1
    out = capsys.readouterr()
    assert "RAZORPAY_KEY_ID" in out.err and TEST_ID not in out.err
    monkeypatch.setattr(config, "Settings", lambda **_k: _settings())
    assert config.main(["--check", "--require-production"]) == 0
    monkeypatch.setattr(config, "Settings", lambda **_k: _settings(APP_ENV="development", DEBUG=True))
    assert config.main(["--check", "--require-production"]) == 1, "the deploy check insists on APP_ENV=production"


def test_messaging_checks_from_the_whatsapp_service_are_included(monkeypatch):
    from app.services import whatsapp_service

    monkeypatch.setattr(
        whatsapp_service,
        "whatsapp_config_problems",
        lambda: [
            "WHATSAPP_UPDATE_TEMPLATE_NAME is empty — generic updates reach only open 24-hour chats",
            "WHATSAPP_NEW_RULE_SETTING is empty — something new the messaging code needs",
        ],
    )
    # Fully configured: only the NEW rule shows (the other is not a problem
    # here, but the helper said so — it reads the process settings).
    problems = _problems()
    assert any("WHATSAPP_NEW_RULE_SETTING" in p for p in problems)
    # Already reported by our own rule: not repeated.
    missing_update = _problems(WHATSAPP_UPDATE_TEMPLATE_NAME="")
    assert sum("WHATSAPP_UPDATE_TEMPLATE_NAME" in p for p in missing_update) == 1
    # Never for non-production.
    dev = Settings(_env_file=None, APP_ENV="development", DEBUG=True)
    assert not any("WHATSAPP_NEW_RULE_SETTING" in p for p in validate_settings(dev))
