"""ARCH 2026-10-07 — scripts/deploy-gcp.sh deploys TWO Cloud Run services
from ONE image, offline (gcloud and curl are stubs; no GCP, no network):

  blussit-worker  RUN_MODE=worker, min = max = 1, CPU always on, internal
                  ingress, not public, startup probe /api/ready, liveness
                  /api/health — deployed FIRST
  blussit-api     RUN_MODE=api, public, scaling as before, startup probe
                  /api/ready, readiness checked with curl afterwards

The same env validation runs for both modes; --dry-run prints both deploy
commands; --only deploys one; a failed or not-ready worker stops the API
rollout; secrets never reach a command line or the output.
"""
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from tests.test_fix_infra_deploy import GOOD_ENV, SECRET_VALUES

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts" / "deploy-gcp.sh"
BACKEND = REPO / "backend"
VENV_PY = BACKEND / ".venv" / "bin" / "python"

GCLOUD_STUB = r"""#!/usr/bin/env bash
printf '%s\n' "$*" >> "$STUB_DIR/gcloud.log"
case "$1 $2" in
  "config get-value") echo stub-project ;;
  "meta list-files-for-upload") printf '%s\n' Dockerfile requirements-runtime.txt app/main.py ;;
  "secrets describe") exit 1 ;;
  "secrets create") cat > "$STUB_DIR/secret_$3" ;;
  "secrets versions") cat > "$STUB_DIR/secret_$4" ;;
  "builds submit") [[ -z "${STUB_BUILD_FAIL:-}" ]] || exit 1 ;;
  "run deploy") [[ "$3" != "${STUB_FAIL_DEPLOY:-none}" ]] || exit 1 ;;
  "run services")
    svc="$4"
    if [[ "$*" == *status.url* ]]; then echo "https://$svc.stub.example"
    elif [[ "$*" == *latestCreatedRevisionName* ]]; then echo "$svc-00002-new"
    elif [[ "$*" == *latestReadyRevisionName* ]]; then
      if [[ "$svc" == "${STUB_STALE:-none}" ]]; then echo "$svc-00001-old"; else echo "$svc-00002-new"; fi
    fi ;;
esac
exit 0
"""
CURL_STUB = r"""#!/usr/bin/env bash
printf '%s\n' "$*" >> "$STUB_DIR/curl.log"
exit 0
"""

pytestmark = pytest.mark.skipif(not VENV_PY.exists() or shutil.which("bash") is None, reason="needs bash and backend/.venv")


def _write_env(path: Path, values: dict) -> None:
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
            "DEPLOY_IMAGE_TAG": "testtag",
        }
        if dry:
            env["DEPLOY_DRY_RUN"] = "1"
        env.update(extra_env or {})
        return subprocess.run(["bash", str(SCRIPT), *args], cwd=REPO, env=env, capture_output=True, text=True, timeout=180)

    run.tmp = tmp_path
    return run


def _gcloud(rig) -> list[str]:
    log = rig.tmp / "gcloud.log"
    return log.read_text().splitlines() if log.exists() else []


def _deploys(rig) -> list[str]:
    return [line for line in _gcloud(rig) if line.startswith("run deploy")]


IMAGE = "asia-south1-docker.pkg.dev/stub-project/cloud-run-source-deploy/blussit-backend:testtag"


def test_script_parses():
    assert subprocess.run(["bash", "-n", str(SCRIPT)], capture_output=True).returncode == 0


def test_dry_run_validates_both_modes_and_prints_both_commands(rig):
    r = rig()
    assert r.returncode == 0, r.stderr
    out = r.stdout
    assert "Configuration OK for APP_ENV=production RUN_MODE=worker" in out
    assert "Configuration OK for APP_ENV=production RUN_MODE=api" in out
    worker_at, api_at = out.index("[blussit-worker] RUN_MODE=worker"), out.index("[blussit-api] RUN_MODE=api")
    assert worker_at < api_at, "worker first"
    worker, api = out[worker_at:api_at], out[api_at:]
    for flag in ("--min-instances 1", "--max-instances 1", "--no-cpu-throttling", "--no-allow-unauthenticated",
                 "--ingress internal", "--startup-probe=httpGet.path=/api/ready", "--liveness-probe=httpGet.path=/api/health",
                 "RUN_MODE=worker"):
        assert flag in worker, flag
    for flag in ("--allow-unauthenticated", "--ingress all", "--startup-probe=httpGet.path=/api/ready",
                 "--max-instances 10", "--concurrency 250", "--timeout 3600", "RUN_MODE=api"):
        assert flag in api, flag
    assert "--no-allow-unauthenticated" not in api and "--liveness-probe" not in api
    assert "Would build ONE image" in out
    assert not (rig.tmp / "gcloud.log").exists(), "a dry run never calls gcloud"
    for secret in SECRET_VALUES:
        assert secret not in r.stdout + r.stderr


def test_full_deploy_builds_once_and_deploys_worker_then_api_from_the_same_image(rig):
    r = rig(dry=False)
    assert r.returncode == 0, r.stderr + r.stdout
    log = _gcloud(rig)
    builds = [line for line in log if line.startswith("builds submit")]
    assert len(builds) == 1 and f"--tag {IMAGE}" in builds[0]
    deploys = _deploys(rig)
    assert [d.split()[2] for d in deploys] == ["blussit-worker", "blussit-api"]
    assert log.index(builds[0]) < log.index(deploys[0])
    worker, api = deploys
    for d in deploys:
        assert f"--image {IMAGE}" in d and "--source" not in d
        assert "RAZORPAY_KEY_ID=rzp_live_Stub12345" in d, "same config on both"
        assert "WHATSAPP_BUSINESS_NUMBER=919876500000" in d
        assert "RAZORPAY_WEBHOOK_SECRET=RAZORPAY_WEBHOOK_SECRET:latest" in d
        assert "GOOGLE_CLOUD_PROJECT=stub-project" in d
        assert "--startup-probe=httpGet.path=/api/ready" in d
    assert "|RUN_MODE=worker" in worker and "--ingress internal" in worker and "--no-allow-unauthenticated" in worker
    assert "--min-instances 1 --max-instances 1" in worker and "--no-cpu-throttling" in worker
    assert "|RUN_MODE=api" in api and "--allow-unauthenticated" in api and "--ingress all" in api
    curl = (rig.tmp / "curl.log").read_text()
    assert "https://blussit-api.stub.example/api/ready" in curl
    assert "blussit-worker" not in curl, "the worker is internal — its startup probe is the readiness check"
    for secret in SECRET_VALUES:
        assert secret not in "\n".join(log)
        assert secret not in r.stdout + r.stderr
    assert "blussit-worker: revision blussit-worker-00002-new" in r.stdout


def test_a_failed_worker_deploy_stops_before_the_api(rig):
    r = rig(dry=False, extra_env={"STUB_FAIL_DEPLOY": "blussit-worker"})
    assert r.returncode != 0 and "Deploying blussit-worker failed" in r.stderr
    assert [d.split()[2] for d in _deploys(rig)] == ["blussit-worker"]


def test_a_worker_revision_that_never_got_ready_stops_before_the_api(rig):
    r = rig(dry=False, extra_env={"STUB_STALE": "blussit-worker"})
    assert r.returncode != 0 and "not ready" in r.stderr
    assert [d.split()[2] for d in _deploys(rig)] == ["blussit-worker"]


def test_a_failed_build_deploys_nothing(rig):
    r = rig(dry=False, extra_env={"STUB_BUILD_FAIL": "1"})
    assert r.returncode != 0 and "Image build failed" in r.stderr
    assert _deploys(rig) == []


@pytest.mark.parametrize("only, expected", [("api", ["blussit-api"]), ("worker", ["blussit-worker"])])
def test_only_one_service(rig, only, expected):
    r = rig("--only", only, dry=False)
    assert r.returncode == 0, r.stderr
    assert [d.split()[2] for d in _deploys(rig)] == expected
    assert f"RUN_MODE={only}" in r.stdout


def test_existing_image_skips_the_build(rig):
    image = "asia-south1-docker.pkg.dev/stub-project/cloud-run-source-deploy/blussit-backend:known-good"
    r = rig(dry=False, extra_env={"DEPLOY_IMAGE": image})
    assert r.returncode == 0, r.stderr
    assert not [line for line in _gcloud(rig) if line.startswith("builds submit")]
    assert all(f"--image {image}" in d for d in _deploys(rig)) and len(_deploys(rig)) == 2


def test_run_mode_in_the_env_file_is_ignored(rig):
    r = rig(dry=False, env_values={**GOOD_ENV, "RUN_MODE": "all"})
    assert r.returncode == 0, r.stderr
    assert "RUN_MODE in" in r.stderr and "ignored" in r.stderr
    worker, api = _deploys(rig)
    assert "RUN_MODE=worker" in worker and "RUN_MODE=all" not in worker
    assert "RUN_MODE=api" in api and "RUN_MODE=all" not in api


def test_run_mode_exported_in_the_shell_is_refused(rig):
    r = rig(extra_env={"RUN_MODE": "all"})
    assert r.returncode != 0 and "RUN_MODE" in r.stderr and "unset" in r.stderr


def test_missing_business_number_is_refused_for_both_services(rig):
    values = {k: v for k, v in GOOD_ENV.items() if k != "WHATSAPP_BUSINESS_NUMBER"}
    r = rig(env_values=values)
    assert r.returncode != 0 and "WHATSAPP_BUSINESS_NUMBER" in r.stderr
    assert not (rig.tmp / "gcloud.log").exists()


def test_bad_only_value_is_a_usage_error(rig):
    r = rig("--only", "both")
    assert r.returncode == 2 and "Usage" in r.stderr
