"""scripts/deploy-gcp.sh, exercised offline (no GCP, no network):

  P0-3 / DEP-02  refuses Razorpay test keys / missing secret / missing webhook
                 secret — by running the app's own start-up validator on the
                 exact values headed for Cloud Run (DEP-06: one rule list).
  DEP-07         refuses to run without a .gcloudignore that excludes .env*,
                 and when gcloud would upload an env file anyway.
  DEP-12         parses the env file instead of `source`-ing it: an unquoted
                 & in MONGO_URI survives intact; a MONGO_URI already exported
                 in the shell is refused.
  DEP-05         deploys with a /api/ready startup probe and checks readiness
                 after the deploy.

`gcloud` and `curl` are stubs on PATH that record their arguments; secret
values reach the stub only through stdin (never on a command line).
"""
import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts" / "deploy-gcp.sh"
BACKEND = REPO / "backend"
VENV_PY = BACKEND / ".venv" / "bin" / "python"

MONGO = "mongodb+srv://blussit:p@ss&w0rd@cluster0.example.mongodb.net/?retryWrites=true&w=majority&appName=blussit"
GOOD_ENV = {
    "APP_ENV": "production",
    "DEBUG": "false",
    "MONGO_URI": MONGO,
    "MONGO_DB_NAME": "blussit_prod",
    "JWT_SECRET_KEY": "j" * 48,
    "CORS_ORIGINS": "https://blussit.com,https://www.blussit.com",
    "PUBLIC_BASE_URL": "https://api.blussit.com",
    "STORAGE_PROVIDER": "r2",
    "R2_ENDPOINT_URL": "https://acct.r2.cloudflarestorage.com",
    "R2_ACCESS_KEY_ID": "r2-access-key",
    "R2_SECRET_ACCESS_KEY": "r2$secret&value",
    "R2_PUBLIC_BASE_URL": "https://pub-123.r2.dev",
    "WHATSAPP_PROVIDER": "meta_cloud",
    "WHATSAPP_ACCESS_TOKEN": "wa-access-token-value",
    "WHATSAPP_PHONE_NUMBER_ID": "1234567",
    "WHATSAPP_BUSINESS_ACCOUNT_ID": "7654321",
    "WHATSAPP_APP_SECRET": "wa-app-secret-value",
    "WHATSAPP_WEBHOOK_VERIFY_TOKEN": "wa-verify-token-value",
    "WHATSAPP_OTP_TEMPLATE_NAME": "blussit_otp",
    "WHATSAPP_UPDATE_TEMPLATE_NAME": "blussit_service_update",
    "WHATSAPP_TEMP_PASSWORD_TEMPLATE_NAME": "blussit_temp_password",
    # Required in production since 2026-10-07 (feature plan 1.7).
    "WHATSAPP_BUSINESS_NUMBER": "919876500000",
    "SMS_PROVIDER": "msg91",
    "MSG91_AUTH_KEY": "msg91-auth-key-value",
    "OTP_CHANNEL": "whatsapp",
    "RAZORPAY_KEY_ID": "rzp_live_Stub12345",
    "RAZORPAY_KEY_SECRET": "rzp-live-secret-value",
    "RAZORPAY_WEBHOOK_SECRET": "rzp-webhook-secret-value",
    "GCP_PROJECT_ID": "stub-project",
}
SECRET_VALUES = [GOOD_ENV[k] for k in ("MONGO_URI", "JWT_SECRET_KEY", "R2_SECRET_ACCESS_KEY", "WHATSAPP_ACCESS_TOKEN", "WHATSAPP_APP_SECRET", "RAZORPAY_KEY_SECRET", "RAZORPAY_WEBHOOK_SECRET", "MSG91_AUTH_KEY")]

GCLOUD_STUB = r"""#!/usr/bin/env bash
printf '%s\n' "$*" >> "$STUB_DIR/gcloud.log"
case "$1 $2" in
  "config get-value") echo stub-project ;;
  "meta list-files-for-upload") printf '%s\n' Dockerfile requirements-runtime.txt app/main.py ${STUB_UPLOAD_EXTRA:-} ;;
  "secrets describe") exit 1 ;;
  "secrets create") cat > "$STUB_DIR/secret_$3" ;;
  "secrets versions") cat > "$STUB_DIR/secret_$4" ;;
  "run services") if [[ "$*" == *status.url* ]]; then echo https://stub-run.example; else echo blussit-api-00042-abc; fi ;;
esac
exit 0
"""
CURL_STUB = r"""#!/usr/bin/env bash
printf '%s\n' "$*" >> "$STUB_DIR/curl.log"
[[ -z "${STUB_CURL_FAIL:-}" ]] || exit 22
exit 0
"""

pytestmark = pytest.mark.skipif(not VENV_PY.exists() or shutil.which("bash") is None, reason="needs bash and backend/.venv")


def _write_env(path: Path, values: dict) -> None:
    # Unquoted on purpose: `source` mangled & and $ in values like these.
    path.write_text("".join(f"{k}={v}\n" for k, v in values.items()))


@pytest.fixture
def rig(tmp_path):
    stub = tmp_path / "bin"
    stub.mkdir()
    for name, body in (("gcloud", GCLOUD_STUB), ("curl", CURL_STUB)):
        (stub / name).write_text(body)
        (stub / name).chmod(0o755)
    source = tmp_path / "src"
    source.mkdir()
    shutil.copy(BACKEND / ".gcloudignore", source / ".gcloudignore")
    env_file = tmp_path / ".env.production"
    _write_env(env_file, GOOD_ENV)

    def run(*args, extra_env: dict | None = None, env_values: dict | None = None, dry: bool = True):
        if env_values is not None:
            _write_env(env_file, env_values)
        env = {
            "PATH": f"{stub}:{os.environ.get('PATH', '/usr/bin:/bin')}",
            "HOME": str(tmp_path),
            "STUB_DIR": str(tmp_path),
            "DEPLOY_ENV_FILE": str(env_file),
            "DEPLOY_SOURCE_DIR": str(source),
            "DEPLOY_PYTHON": str(VENV_PY),
            "DEPLOY_READY_DELAY": "0",
            "DEPLOY_READY_RETRIES": "2",
        }
        if dry:
            env["DEPLOY_DRY_RUN"] = "1"
        env.update(extra_env or {})
        return subprocess.run(["bash", str(SCRIPT), *args], cwd=REPO, env=env, capture_output=True, text=True, timeout=120)

    run.tmp = tmp_path
    run.source = source
    return run


def test_script_parses():
    assert subprocess.run(["bash", "-n", str(SCRIPT)], capture_output=True).returncode == 0


def test_dry_run_passes_a_complete_production_file(rig):
    r = rig()
    assert r.returncode == 0, r.stderr
    assert "Dry run OK" in r.stdout and "Configuration OK" in r.stdout
    assert not (rig.tmp / "gcloud.log").exists(), "a dry run never calls gcloud"
    for secret in SECRET_VALUES:
        assert secret not in r.stdout + r.stderr


def test_dry_run_flag_works_too(rig):
    r = rig("--dry-run", extra_env={"DEPLOY_DRY_RUN": ""})
    assert r.returncode == 0, r.stderr
    assert "Dry run OK" in r.stdout


@pytest.mark.parametrize(
    "change, needle",
    [
        ({"RAZORPAY_KEY_ID": "rzp_test_Stub12345"}, "RAZORPAY_KEY_ID"),
        ({"RAZORPAY_KEY_SECRET": ""}, "RAZORPAY_KEY_SECRET"),
        ({"RAZORPAY_WEBHOOK_SECRET": ""}, "RAZORPAY_WEBHOOK_SECRET"),
        ({"WHATSAPP_UPDATE_TEMPLATE_NAME": ""}, "WHATSAPP_UPDATE_TEMPLATE_NAME"),
        ({"WHATSAPP_OTP_TEMPLATE_NAME": ""}, "WHATSAPP_OTP_TEMPLATE_NAME"),
        ({"R2_PUBLIC_BASE_URL": "https://acct.r2.cloudflarestorage.com/blussit-images"}, "R2_PUBLIC_BASE_URL"),
        ({"MONGO_URI": "mongodb://localhost:27017"}, "MONGO_URI"),
        ({"DEBUG": "true"}, "DEBUG"),
        ({"WHATSAPP_PROVIDER": "log"}, "WHATSAPP_PROVIDER"),
        ({"APP_ENV": "development"}, "APP_ENV"),
    ],
)
def test_deploy_refuses_what_the_app_would_refuse(rig, change, needle):
    r = rig(env_values={**GOOD_ENV, **change})
    assert r.returncode != 0
    assert needle in r.stderr, r.stderr
    assert "rzp_test_Stub12345" not in r.stdout + r.stderr, "values are never printed"
    assert not (rig.tmp / "gcloud.log").exists()


def test_exported_mongo_uri_is_refused(rig):
    r = rig(extra_env={"MONGO_URI": "mongodb://dev-shell-value"})
    assert r.returncode != 0 and "MONGO_URI is already set" in r.stderr


def test_any_exported_app_setting_is_refused(rig):
    r = rig(extra_env={"RAZORPAY_KEY_ID": "rzp_test_from_shell"})
    assert r.returncode != 0 and "RAZORPAY_KEY_ID" in r.stderr and "unset" in r.stderr


def test_missing_gcloudignore_is_refused(rig):
    (rig.source / ".gcloudignore").unlink()
    r = rig()
    assert r.returncode != 0 and ".gcloudignore is missing" in r.stderr


def test_gcloudignore_without_env_exclusion_is_refused(rig):
    (rig.source / ".gcloudignore").write_text(".git\n__pycache__/\n")
    r = rig()
    assert r.returncode != 0 and "must exclude every env file" in r.stderr


def test_gcloudignore_reincluding_an_env_file_is_refused(rig):
    (rig.source / ".gcloudignore").write_text(".env\n.env.*\n!.env.example\n!.env.production\n")
    r = rig()
    assert r.returncode != 0 and "re-includes an env file" in r.stderr


def test_full_deploy_with_stubbed_gcloud(rig):
    r = rig(dry=False)
    assert r.returncode == 0, r.stderr + r.stdout
    log = (rig.tmp / "gcloud.log").read_text()
    deploy = next(line for line in log.splitlines() if line.startswith("run deploy"))
    assert "--startup-probe=httpGet.path=/api/ready" in deploy
    assert "RAZORPAY_KEY_ID=rzp_live_Stub12345" in deploy
    assert "RAZORPAY_WEBHOOK_SECRET=RAZORPAY_WEBHOOK_SECRET:latest" in deploy
    assert "GOOGLE_CLOUD_PROJECT=stub-project" in deploy
    for secret in SECRET_VALUES:
        assert secret not in log, "secret values never appear on a gcloud command line"
        assert secret not in r.stdout + r.stderr
    # DEP-12: the unquoted & and $ survived intact (source used to cut them).
    assert (rig.tmp / "secret_MONGO_URI").read_text() == MONGO
    assert (rig.tmp / "secret_R2_SECRET_ACCESS_KEY").read_text() == GOOD_ENV["R2_SECRET_ACCESS_KEY"]
    assert "https://stub-run.example/api/ready" in (rig.tmp / "curl.log").read_text()


def test_not_ready_after_deploy_fails_the_script(rig):
    r = rig(dry=False, extra_env={"STUB_CURL_FAIL": "1"})
    assert r.returncode != 0 and "NOT READY" in r.stderr


def test_gcloud_upload_list_with_an_env_file_is_refused(rig):
    r = rig(dry=False, extra_env={"STUB_UPLOAD_EXTRA": ".env.production"})
    assert r.returncode != 0 and "would upload an env file" in r.stderr
    assert "run deploy" not in (rig.tmp / "gcloud.log").read_text()
