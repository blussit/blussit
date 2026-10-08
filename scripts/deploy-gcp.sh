#!/usr/bin/env bash
set -Eeuo pipefail

# Deploy the backend to Cloud Run as TWO services built from ONE image:
#
#   blussit-api     RUN_MODE=api     public HTTP API, webhooks, websockets;
#                                    scales MIN..MAX instances; no background
#                                    loops.
#   blussit-worker  RUN_MODE=worker  reminder/sweep loop, WhatsApp retry,
#                                    template sync, index builds, boot
#                                    backfills; exactly 1 instance, CPU always
#                                    on, internal ingress, not public.
#
# Either can crash, restart or be rolled back without stopping the other
# (docs/ARCHITECTURE.md, DEPLOYMENT.md). Order: build the image once ->
# deploy the WORKER (builds new indexes, runs idempotent migrations) -> wait
# until it is ready -> deploy the API -> check the API's /api/ready.
#
# The source env file is local-only and is ignored by git: LIVE credentials
# live in backend/.env.production (backend/.env.development is for local
# testing and must never be deployed).
#
# Usage (from the repository root):
#   scripts/deploy-gcp.sh                 validate, build, deploy both
#   scripts/deploy-gcp.sh --dry-run       validate only; touches nothing in GCP
#                                         (or DEPLOY_DRY_RUN=1); prints both
#                                         deploy commands
#   scripts/deploy-gcp.sh --only api      deploy just one service (also
#   scripts/deploy-gcp.sh --only worker   combinable with --dry-run)
#   DEPLOY_IMAGE=<image> scripts/...      skip the build, deploy that image
#                                         (e.g. redeploy a known-good build)
#
# The configuration rules are NOT duplicated here: the backend's own start-up
# validator (app/core/config.py: validate_settings) is run on exactly the
# values this script is about to send to Cloud Run — once per RUN_MODE — so
# a revision that would refuse to start is never deployed. This script adds
# only deploy-target policy (domains, OTP channel) and upload hygiene.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="$(cd "$SCRIPT_DIR/../backend" && pwd)"   # whose code validates the config
ENV_FILE="${DEPLOY_ENV_FILE:-backend/.env.production}"
SOURCE_DIR="${DEPLOY_SOURCE_DIR:-backend}"         # what `gcloud builds submit` uploads
API_SERVICE="${CLOUD_RUN_SERVICE:-blussit-api}"
WORKER_SERVICE="${CLOUD_RUN_WORKER_SERVICE:-blussit-worker}"
REGION="${CLOUD_RUN_REGION:-asia-south1}"
PORT="${CLOUD_RUN_PORT:-8080}"
# Artifact Registry repository the one image is pushed to (the repository
# `gcloud run deploy --source` always used, so it already exists).
ARTIFACT_REPO="${CLOUD_RUN_ARTIFACT_REPO:-cloud-run-source-deploy}"
IMAGE_NAME="${CLOUD_RUN_IMAGE_NAME:-blussit-backend}"

# ---- blussit-api -----------------------------------------------------------
# One instance is always kept warm so the first visitor after a quiet spell
# doesn't wait for Python + the database connection to boot (a cold start is
# several seconds of a blank booking screen). It costs a small always-on
# instance; set CLOUD_RUN_MIN_INSTANCES=0 to go back to scale-to-zero.
MIN_INSTANCES="${CLOUD_RUN_MIN_INSTANCES:-1}"
# Requests (incl. open WebSockets) per instance. Most requests wait on Mongo,
# so one async worker handles far more than Cloud Run's default of 80.
CONCURRENCY="${CLOUD_RUN_CONCURRENCY:-250}"
# Cloud Run closes every request, WebSockets included, at this timeout
# (default 300 s would drop every live socket every 5 minutes). 3600 is the max.
TIMEOUT="${CLOUD_RUN_TIMEOUT:-3600}"
# Each instance opens up to 30 Mongo connections (maxPoolSize in
# app/core/database.py; a maxPoolSize in MONGO_URI overrides it). Keep
# (MAX_INSTANCES + 1 worker) x 30 x 2 (old + new revision overlap during a
# deploy) below the Atlas tier's connection limit, or a traffic spike
# exhausts Atlas and every instance fails.
MAX_INSTANCES="${CLOUD_RUN_MAX_INSTANCES:-10}"
# 1Gi, not the 512Mi default: up to 250 concurrent requests, open sockets
# and spooled uploads (up to ~9 MB each) share one instance.
MEMORY="${CLOUD_RUN_MEMORY:-1Gi}"
# The API runs no loops any more, but it still does work OUTSIDE a request:
# background WhatsApp sends handed off by a request (<= 1 s wait, then the
# send finishes in the background) and the websocket relay. With request-only
# CPU those stall whenever traffic is quiet, so CPU stays allocated
# (--no-cpu-throttling) by default. Set CLOUD_RUN_CPU_THROTTLING=true to trade
# that for request-based billing (the worker's retry loop then picks up any
# send that stalled and failed).
case "$(printf '%s' "${CLOUD_RUN_CPU_THROTTLING:-false}" | tr '[:upper:]' '[:lower:]')" in
  true|1|yes|on) CPU_THROTTLING_FLAG="--cpu-throttling" ;;
  *) CPU_THROTTLING_FLAG="--no-cpu-throttling" ;;
esac
# DEP-05: a new revision takes traffic only once /api/ready answers 200
# (database reachable + critical unique indexes present). 18 x 10 s leaves
# first-boot index builds three minutes. CLOUD_RUN_STARTUP_PROBE=default
# keeps Cloud Run's plain TCP probe instead.
STARTUP_PROBE="${CLOUD_RUN_STARTUP_PROBE:-httpGet.path=/api/ready,httpGet.port=${PORT},initialDelaySeconds=0,timeoutSeconds=4,periodSeconds=10,failureThreshold=18}"
READY_RETRIES="${DEPLOY_READY_RETRIES:-10}"
READY_DELAY="${DEPLOY_READY_DELAY:-6}"

# ---- blussit-worker --------------------------------------------------------
# Exactly one instance (min = max = 1): the sweeps are single-flight under a
# Mongo lease anyway, so a second instance (e.g. old + new revision during a
# deploy) would only idle — never double-send. CPU is always allocated: the
# loops run outside any request. Not public: internal ingress + no
# unauthenticated invocations; it serves only /api/health and /api/ready
# (probes) and 404s everything else.
WORKER_CONCURRENCY="${CLOUD_RUN_WORKER_CONCURRENCY:-10}"
WORKER_MEMORY="${CLOUD_RUN_WORKER_MEMORY:-1Gi}"
WORKER_CPU="${CLOUD_RUN_WORKER_CPU:-1}"
WORKER_TIMEOUT="${CLOUD_RUN_WORKER_TIMEOUT:-300}"
# The worker builds the indexes its own /api/ready checks — same 3 minutes.
WORKER_STARTUP_PROBE="${CLOUD_RUN_WORKER_STARTUP_PROBE:-$STARTUP_PROBE}"
# /api/health answers 503 when a background loop has died (a bug — they
# catch every error): 3 failures 60 s apart restart the container.
# CLOUD_RUN_WORKER_LIVENESS_PROBE=default sends no liveness probe.
WORKER_LIVENESS_PROBE="${CLOUD_RUN_WORKER_LIVENESS_PROBE:-httpGet.path=/api/health,httpGet.port=${PORT},timeoutSeconds=4,periodSeconds=60,failureThreshold=3}"

usage() {
  printf 'Usage: %s [--dry-run] [--only api|worker]\n' "$0" >&2
  exit 2
}
DRY_RUN=false
ONLY=""
while (( $# > 0 )); do
  case "$1" in
    --dry-run|--check) DRY_RUN=true ;;
    --only)
      [[ $# -ge 2 ]] || usage
      ONLY="$2"
      shift
      ;;
    --only=*) ONLY="${1#--only=}" ;;
    *) usage ;;
  esac
  shift
done
case "$ONLY" in
  "") TARGETS=(worker api) ;;   # worker FIRST: indexes + migrations before the new API takes traffic
  api|worker) TARGETS=("$ONLY") ;;
  *) usage ;;
esac
case "$(printf '%s' "${DEPLOY_DRY_RUN:-}" | tr '[:upper:]' '[:lower:]')" in
  1|true|yes|on) DRY_RUN=true ;;
esac

fail() {
  printf 'ERROR: %s\n' "$*" >&2
  exit 1
}

[[ -f "$ENV_FILE" ]] || fail "Environment file not found: $ENV_FILE"
if command -v git >/dev/null 2>&1 && git ls-files --error-unmatch "$ENV_FILE" >/dev/null 2>&1; then
  fail "Refusing to use a tracked production env file: $ENV_FILE"
fi

# DEP-07: `gcloud run deploy --source` uploads the whole source directory to
# a Cloud Build bucket. Only .gcloudignore keeps the live .env.production out
# of that upload (.dockerignore filters the later docker build, not the
# upload) — so it must exist and exclude every env file.
check_gcloudignore() {
  local file="$SOURCE_DIR/.gcloudignore" lines
  [[ -f "$file" ]] || fail "$file is missing — without it gcloud would upload $SOURCE_DIR/.env.production to Cloud Build. Restore it (and commit it)."
  lines="$(sed -e 's/[[:space:]]*$//' -e 's/^[[:space:]]*//' "$file")"
  if ! grep -qxF '.env*' <<<"$lines" && ! { grep -qxF '.env' <<<"$lines" && grep -qxF '.env.*' <<<"$lines"; }; then
    fail "$file must exclude every env file (a '.env*' line, or both '.env' and '.env.*')"
  fi
  local reincluded
  reincluded="$(grep -E '^!.*\.env' <<<"$lines" | grep -vxE '!(.*/)?\.env(\.development)?\.example' || true)"
  [[ -z "$reincluded" ]] || fail "$file re-includes an env file with a '!' rule — remove it"
  if command -v git >/dev/null 2>&1 && git rev-parse --is-inside-work-tree >/dev/null 2>&1 \
    && ! git ls-files --error-unmatch "$file" >/dev/null 2>&1; then
    printf 'WARNING: %s is not committed — a fresh checkout would not have it. Commit it.\n' "$file" >&2
  fi
}
check_gcloudignore

# The backend's Python (pydantic-settings + python-dotenv) parses the env file
# and runs the validator — the same code and parser the app itself uses.
if [[ -n "${DEPLOY_PYTHON:-}" ]]; then
  PY="$DEPLOY_PYTHON"
elif [[ -x "$APP_DIR/.venv/bin/python" ]]; then
  PY="$APP_DIR/.venv/bin/python"
else
  PY="$(command -v python3 || true)"
fi
[[ -n "$PY" ]] || fail "No Python found — create backend/.venv (pip install -r backend/requirements.txt) or set DEPLOY_PYTHON"

# Runs the backend's Python with a clean environment and no env file, so
# nothing from this shell can leak into what is checked.
py_clean() {
  env -i PATH="$PATH" HOME="${HOME:-/tmp}" LANG=C.UTF-8 ENV_FILE=/dev/null \
    PYTHONPATH="$APP_DIR" PYTHONDONTWRITEBYTECODE=1 "$PY" "$@"
}
py_clean -c 'import dotenv, pydantic_settings, app.core.config' 2>/dev/null \
  || fail "$PY can't import the backend (python-dotenv, pydantic-settings) — run: pip install -r backend/requirements.txt"

# Deploy-script settings an env file may also carry (read below, after load).
DEPLOY_KEYS=(GCP_PROJECT_ID CLOUD_RUN_RUNTIME_SA)
mapfile -t SETTINGS_NAMES < <(py_clean -c 'from app.core.config import Settings; print("\n".join(sorted(Settings.model_fields)))')
(( ${#SETTINGS_NAMES[@]} > 20 )) || fail "Could not read the backend's setting names"

# DEP-12: the env file is the ONLY source of app settings. A value already
# exported in this shell (e.g. a MONGO_URI from a dev session) would silently
# stand in for anything the file doesn't set — with `source`, an unquoted &
# in the file's MONGO_URI even made the FILE's value vanish and the shell's
# win. Refuse instead.
if [[ -n "${MONGO_URI+set}" ]]; then
  fail "MONGO_URI is already set in this shell — run 'unset MONGO_URI' (the value must come from $ENV_FILE only)"
fi
LEAKED=()
for name in "${SETTINGS_NAMES[@]}"; do
  if [[ -n "${!name+set}" ]]; then
    LEAKED+=("$name")
  fi
done
(( ${#LEAKED[@]} == 0 )) || fail "These settings are already set in this shell and would leak into the deploy — unset them: ${LEAKED[*]}"

# DEP-12: parse the env file with python-dotenv (the parser the app uses),
# never `source` it: values are data, not shell code (an unquoted & or $ in a
# connection string or secret broke the old `source`). Pairs arrive
# NUL-separated and are assigned with printf -v; nothing is evaluated.
ENV_PAIRS=()
mapfile -d '' -t ENV_PAIRS < <(py_clean -c '
import re, sys
from dotenv import dotenv_values
from app.core.config import Settings
allowed = set(Settings.model_fields) | set(sys.argv[2:])
ignored = []
out = sys.stdout.buffer
for key, value in dotenv_values(sys.argv[1]).items():
    if value is None:
        continue
    if key not in allowed or not re.fullmatch(r"[A-Z][A-Z0-9_]*", key):
        ignored.append(key)
        continue
    if "\0" in value:
        sys.exit("NUL byte in the value of " + key)
    out.write(key.encode() + b"\0" + value.encode() + b"\0")
if ignored:
    print("Ignoring unknown key(s) in the env file (not app settings): " + ", ".join(sorted(ignored)), file=sys.stderr)
out.write(b"__END__\0ok\0")
' "$ENV_FILE" "${DEPLOY_KEYS[@]}")
(( ${#ENV_PAIRS[@]} >= 2 )) && [[ "${ENV_PAIRS[-2]}" == "__END__" ]] || fail "Could not parse $ENV_FILE"
for (( i = 0; i < ${#ENV_PAIRS[@]} - 2; i += 2 )); do
  printf -v "${ENV_PAIRS[i]}" '%s' "${ENV_PAIRS[i+1]}"
done
unset ENV_PAIRS
# RUN_MODE is per SERVICE (api / worker), set below — never from the shared file.
if [[ -n "${RUN_MODE+set}" ]]; then
  printf 'NOTE: RUN_MODE in %s is ignored — this script sets RUN_MODE=api on %s and RUN_MODE=worker on %s.\n' "$ENV_FILE" "$API_SERVICE" "$WORKER_SERVICE" >&2
  unset RUN_MODE
fi

# Production only: refuse anything that looks like the local test setup.
[[ "${APP_ENV:-}" == "production" ]] || fail "$ENV_FILE must set APP_ENV=production (is this the development file?)"
# Any spelling pydantic would read as true (True/1/yes/on), not just "true".
case "$(printf '%s' "${DEV_TOOLS_ENABLED:-false}" | tr '[:upper:]' '[:lower:]')" in
  true|1|yes|on|y|t) fail "DEV_TOOLS_ENABLED is for local testing only — remove it from $ENV_FILE" ;;
esac
# Behind Cloud Run every request's socket peer is Google's front end: without
# trusting X-Forwarded-For, ALL customers share one rate-limit bucket (one
# busy minute locks everyone out of OTP/login). Unset means true here.
case "$(printf '%s' "${TRUST_PROXY_HEADERS:-true}" | tr '[:upper:]' '[:lower:]')" in
  true|1|yes|on|y|t) TRUST_PROXY_HEADERS=true ;;
  *) fail "TRUST_PROXY_HEADERS must be true on Cloud Run (every client otherwise shares one rate-limit bucket) — fix $ENV_FILE" ;;
esac

# Defaults mirror backend/app/core/config.py (except TRUST_PROXY_HEADERS,
# forced true above for Cloud Run). Explicit values in ENV_FILE win. Whatever
# these produce is exactly what the validator below checks.
: "${APP_NAME:=Doorstep Vehicle Care Platform}"
: "${API_V1_PREFIX:=/api/v1}"
: "${DEBUG:=false}"
: "${RATE_LIMIT_ENABLED:=true}"
# 1 = Cloud Run direct / domain mapping: Google's front end APPENDS the real
# client address as the LAST X-Forwarded-For entry (everything left of it is
# client-supplied). Use 2 only behind an external HTTPS load balancer.
: "${TRUSTED_PROXY_COUNT:=1}"
: "${MONGO_DB_NAME:=doorstep_vehicle_care}"
: "${JWT_ALGORITHM:=HS256}"
: "${ACCESS_TOKEN_EXPIRE_MINUTES:=15}"
: "${REFRESH_TOKEN_EXPIRE_DAYS:=7}"
: "${DEFAULT_PAGE_SIZE:=20}"
: "${MAX_PAGE_SIZE:=100}"
: "${WHATSAPP_PROVIDER:=log}"
: "${WHATSAPP_API_VERSION:=v21.0}"
: "${WHATSAPP_OTP_TEMPLATE_LANGUAGE:=en_US}"
: "${WHATSAPP_TEMPLATE_LANGUAGE:=en_US}"
: "${SMS_PROVIDER:=}"
: "${OTP_CHANNEL:=whatsapp}"
: "${STORAGE_PROVIDER:=local}"
: "${UPLOAD_DIR:=uploads}"
: "${R2_BUCKET_NAME:=blussit-images}"
: "${R2_PRIVATE_BUCKET_NAME:=blussit-private-documents}"
: "${R2_UPLOAD_PREFIX:=photos}"
: "${PUBLIC_BASE_URL:=}"
: "${LOG_FORMAT:=}"

require_value() {
  local name="$1"
  [[ -n "${!name:-}" ]] || fail "Required variable is missing or empty: $name"
}

# Deploy-target policy (the app's generic rules run below).
[[ "${CORS_ORIGINS:-}" == *"https://blussit.com"* ]] || fail "CORS_ORIGINS must include https://blussit.com"
[[ "${CORS_ORIGINS:-}" == *"https://www.blussit.com"* ]] || fail "CORS_ORIGINS must include https://www.blussit.com"
[[ "$PUBLIC_BASE_URL" == "https://api.blussit.com" ]] || fail "PUBLIC_BASE_URL must be https://api.blussit.com"
# OTP delivery (login + the confirm-booking OTP): WhatsApp first, SMS as
# the fallback (the app requires the SMS path itself).
[[ "$OTP_CHANNEL" == "whatsapp" ]] || fail "OTP_CHANNEL must be whatsapp (WhatsApp first, SMS fallback)"

# The MSG91 widget is enabled only when all three values are present (the
# validator refuses a partial trio).
MSG91_WIDGET_ENABLED=false
if [[ -n "${MSG91_AUTH_KEY:-}" && -n "${MSG91_WIDGET_ID:-}" && -n "${MSG91_TOKEN_AUTH:-}" ]]; then
  MSG91_WIDGET_ENABLED=true
fi

SECRET_NAMES=()
NORMAL_NAMES=(
  APP_NAME APP_ENV API_V1_PREFIX DEBUG RATE_LIMIT_ENABLED TRUST_PROXY_HEADERS TRUSTED_PROXY_COUNT
  MONGO_DB_NAME JWT_ALGORITHM ACCESS_TOKEN_EXPIRE_MINUTES REFRESH_TOKEN_EXPIRE_DAYS
  CORS_ORIGINS STORAGE_PROVIDER UPLOAD_DIR R2_ENDPOINT_URL R2_BUCKET_NAME
  R2_PRIVATE_BUCKET_NAME R2_PUBLIC_BASE_URL R2_UPLOAD_PREFIX DEFAULT_PAGE_SIZE MAX_PAGE_SIZE
  WHATSAPP_PROVIDER WHATSAPP_PHONE_NUMBER_ID WHATSAPP_BUSINESS_ACCOUNT_ID WHATSAPP_BUSINESS_NUMBER
  WHATSAPP_API_VERSION WHATSAPP_OTP_TEMPLATE_NAME WHATSAPP_OTP_TEMPLATE_LANGUAGE
  WHATSAPP_UPDATE_TEMPLATE_NAME WHATSAPP_TEMP_PASSWORD_TEMPLATE_NAME WHATSAPP_TEMPLATE_LANGUAGE
  SMS_PROVIDER OTP_CHANNEL PUBLIC_BASE_URL GOOGLE_MAPS_BROWSER_KEY GOOGLE_OAUTH_CLIENT_ID
  RAZORPAY_KEY_ID LOG_FORMAT
)

# Production always runs Meta WhatsApp, R2 and live Razorpay (the validator
# enforces it), so their secrets always go to Secret Manager.
SECRET_NAMES+=(MONGO_URI JWT_SECRET_KEY R2_ACCESS_KEY_ID R2_SECRET_ACCESS_KEY)
SECRET_NAMES+=(WHATSAPP_ACCESS_TOKEN WHATSAPP_WEBHOOK_VERIFY_TOKEN WHATSAPP_APP_SECRET)
SECRET_NAMES+=(RAZORPAY_KEY_SECRET RAZORPAY_WEBHOOK_SECRET)
if [[ "$SMS_PROVIDER" == "msg91" ]]; then
  SECRET_NAMES+=(MSG91_AUTH_KEY)
fi
if [[ "$MSG91_WIDGET_ENABLED" == true ]]; then
  NORMAL_NAMES+=(MSG91_WIDGET_ID)
  SECRET_NAMES+=(MSG91_AUTH_KEY MSG91_TOKEN_AUTH)
fi
if [[ "$SMS_PROVIDER" == "msg91" && -n "${MSG91_OTP_TEMPLATE_ID:-}" ]]; then
  NORMAL_NAMES+=(MSG91_OTP_TEMPLATE_ID)
fi
if [[ -n "${GOOGLE_MAPS_SERVER_KEY:-}" ]]; then
  SECRET_NAMES+=(GOOGLE_MAPS_SERVER_KEY)
fi
# Optional error tracking (active only if the image has sentry-sdk).
if [[ -n "${SENTRY_DSN:-}" ]]; then
  SECRET_NAMES+=(SENTRY_DSN)
  if [[ -n "${SENTRY_TRACES_SAMPLE_RATE:-}" ]]; then
    NORMAL_NAMES+=(SENTRY_TRACES_SAMPLE_RATE)
  fi
fi

# Remove duplicate secret names while preserving order.
UNIQUE_SECRET_NAMES=()
declare -A SEEN_SECRET=()
for name in "${SECRET_NAMES[@]}"; do
  if [[ -z "${SEEN_SECRET[$name]:-}" ]]; then
    UNIQUE_SECRET_NAMES+=("$name")
    SEEN_SECRET[$name]=1
  fi
done

# P0-3 / DEP-02 / DEP-04 / DEP-06: run the app's own start-up validator on
# EXACTLY the environment Cloud Run will get (the names below and nothing
# else; no env file) — once per service, with that service's RUN_MODE.
# Secrets travel by environment, never on a command line.
if [[ "${RAZORPAY_KEY_ID:-}" == rzp_test_* && -n "${RAZORPAY_LIVE_KEY_ID:-}" ]]; then
  printf 'HINT: the live Razorpay pair is parked in RAZORPAY_LIVE_KEY_ID/RAZORPAY_LIVE_KEY_SECRET — move it into RAZORPAY_KEY_ID/RAZORPAY_KEY_SECRET in %s.\n' "$ENV_FILE" >&2
fi
for target in "${TARGETS[@]}"; do
  printf 'Validating the configuration with the backend start-up checks (RUN_MODE=%s)...\n' "$target"
  if ! (
    for name in "${NORMAL_NAMES[@]}" "${UNIQUE_SECRET_NAMES[@]}"; do
      export "$name"="${!name:-}"
    done
    export RUN_MODE="$target" ENV_FILE=/dev/null PYTHONPATH="$APP_DIR" PYTHONDONTWRITEBYTECODE=1
    "$PY" -m app.core.config --check --require-production || exit 1
    # The image must at least IMPORT: a code error (a missing import) would
    # otherwise only show as a crash-looping Cloud Run revision.
    "$PY" -c "import app.main" || { echo "ERROR: the backend code does not import (traceback above)." >&2; exit 1; }
  ); then
    fail "The backend would refuse to start with $ENV_FILE as RUN_MODE=$target (problems listed above) — nothing was deployed"
  fi
done
# Every Secret Manager entry needs a value (also the optional ones enabled above).
for name in "${UNIQUE_SECRET_NAMES[@]}"; do
  require_value "$name"
done

service_name() {
  if [[ "$1" == worker ]]; then printf '%s' "$WORKER_SERVICE"; else printf '%s' "$API_SERVICE"; fi
}

# Fills DEPLOY_ARGS with the `gcloud run deploy` arguments for one service.
# Shared: image, region, runtime account, env + secrets (identical config
# for both; only RUN_MODE differs). Per service: scaling, CPU, ingress, probes.
build_deploy_args() {
  local target="$1" env_spec="$2"
  DEPLOY_ARGS=(
    run deploy "$(service_name "$target")"
    --project "$PROJECT_ID"
    --image "$IMAGE"
    --region "$REGION"
    --service-account "$RUNTIME_SA"
    --port "$PORT"
  )
  if [[ "$target" == worker ]]; then
    DEPLOY_ARGS+=(
      --min-instances 1
      --max-instances 1
      --concurrency "$WORKER_CONCURRENCY"
      --timeout "$WORKER_TIMEOUT"
      --memory "$WORKER_MEMORY"
      --cpu "$WORKER_CPU"
      --no-cpu-throttling
      --cpu-boost
    )
    [[ "$WORKER_STARTUP_PROBE" == "default" ]] || DEPLOY_ARGS+=(--startup-probe="$WORKER_STARTUP_PROBE")
    [[ "$WORKER_LIVENESS_PROBE" == "default" ]] || DEPLOY_ARGS+=(--liveness-probe="$WORKER_LIVENESS_PROBE")
    DEPLOY_ARGS+=(--no-allow-unauthenticated --ingress internal)
  else
    DEPLOY_ARGS+=(
      --min-instances "$MIN_INSTANCES"
      --max-instances "$MAX_INSTANCES"
      --concurrency "$CONCURRENCY"
      --timeout "$TIMEOUT"
      --memory "$MEMORY"
      "$CPU_THROTTLING_FLAG"
      --cpu-boost
    )
    [[ "$STARTUP_PROBE" == "default" ]] || DEPLOY_ARGS+=(--startup-probe="$STARTUP_PROBE")
    DEPLOY_ARGS+=(--allow-unauthenticated --ingress all)
  fi
  DEPLOY_ARGS+=(--set-env-vars="$env_spec" --set-secrets="$SECRET_SPEC" --quiet)
}

# Sets ENV_SPEC for one service. A custom delimiter (^|^) keeps the commas
# in CORS_ORIGINS inside one value.
build_env_spec() {
  local target="$1" spec="" name value
  for name in "${NORMAL_NAMES[@]}"; do
    value="${!name:-}"
    [[ "$value" != *"|"* ]] || fail "$name contains '|', which the env-var spec uses as its delimiter"
    spec+="${spec:+|}${name}=${value}"
  done
  ENV_SPEC="^|^${spec}|RUN_MODE=${target}"
}

SECRET_SPEC=""
for name in "${UNIQUE_SECRET_NAMES[@]}"; do
  SECRET_SPEC+="${SECRET_SPEC:+,}${name}=${name}:latest"
done

GIT_SHA="$(git -C "$APP_DIR" rev-parse --short HEAD 2>/dev/null || echo nogit)"
IMAGE_TAG="${DEPLOY_IMAGE_TAG:-$(date -u +%Y%m%d-%H%M%S)-${GIT_SHA}}"

if [[ "$DRY_RUN" == true ]]; then
  PROJECT_ID="${GCP_PROJECT_ID:-<gcloud-project>}"
  RUNTIME_SA="${CLOUD_RUN_RUNTIME_SA:-blussit-api-runtime@${PROJECT_ID}.iam.gserviceaccount.com}"
  IMAGE="${DEPLOY_IMAGE:-${REGION}-docker.pkg.dev/${PROJECT_ID}/${ARTIFACT_REPO}/${IMAGE_NAME}:${IMAGE_TAG}}"
  printf '\nDry run OK — nothing was deployed and nothing in GCP was touched.\n'
  if [[ -n "${DEPLOY_IMAGE:-}" ]]; then
    printf 'Would deploy the existing image %s (no build).\n' "$IMAGE"
  else
    printf 'Would build ONE image from %s: %s\n' "$SOURCE_DIR" "$IMAGE"
  fi
  printf 'Deploy order: %s\n' "${TARGETS[*]}"
  printf 'Env vars (%d + RUN_MODE): %s\n' "${#NORMAL_NAMES[@]}" "${NORMAL_NAMES[*]}"
  printf 'Secrets (%d): %s\n' "${#UNIQUE_SECRET_NAMES[@]}" "${UNIQUE_SECRET_NAMES[*]}"
  printf 'Startup probe: %s\n' "$STARTUP_PROBE"
  for target in "${TARGETS[@]}"; do
    build_deploy_args "$target" "<${#NORMAL_NAMES[@]} env vars above>|RUN_MODE=$target"
    printf '\n[%s] RUN_MODE=%s\n  gcloud' "$(service_name "$target")" "$target"
    printf ' %q' "${DEPLOY_ARGS[@]}"
    printf '\n'
  done
  printf '\nNo secret values were printed.\n'
  exit 0
fi

command -v gcloud >/dev/null 2>&1 || fail "gcloud CLI is required"
command -v curl >/dev/null 2>&1 || fail "curl is required for the post-deploy readiness check"

PROJECT_ID="${GCP_PROJECT_ID:-$(gcloud config get-value project 2>/dev/null)}"
[[ -n "$PROJECT_ID" && "$PROJECT_ID" != "(unset)" ]] || fail "Set GCP_PROJECT_ID or configure an active gcloud project"
gcloud config set project "$PROJECT_ID" >/dev/null

if [[ -z "${DEPLOY_IMAGE:-}" ]]; then
  # DEP-07, second line of defence: ask gcloud itself which files it would
  # upload (it applies .gcloudignore) and refuse if any env file is among them.
  if UPLOAD_LIST="$(gcloud meta list-files-for-upload "$SOURCE_DIR" 2>/dev/null)"; then
    ENV_UPLOADS="$(grep -E '(^|/)\.env($|\.)' <<<"$UPLOAD_LIST" | grep -vE '\.example$' || true)"
    [[ -z "$ENV_UPLOADS" ]] || fail "gcloud would upload an env file from $SOURCE_DIR — fix $SOURCE_DIR/.gcloudignore"
  else
    printf 'WARNING: could not list the files gcloud will upload; relying on the .gcloudignore check.\n' >&2
  fi
fi

RUNTIME_SA="${CLOUD_RUN_RUNTIME_SA:-blussit-api-runtime@${PROJECT_ID}.iam.gserviceaccount.com}"
RUNTIME_SA_NAME="${RUNTIME_SA%@*}"
IMAGE="${DEPLOY_IMAGE:-${REGION}-docker.pkg.dev/${PROJECT_ID}/${ARTIFACT_REPO}/${IMAGE_NAME}:${IMAGE_TAG}}"

printf 'Preparing Cloud Run deployment: services=%s region=%s\n' "${TARGETS[*]}" "$REGION"
printf 'Using env file: %s (values are not printed)\n' "$ENV_FILE"

printf 'Enabling required Google APIs...\n'
gcloud services enable \
  run.googleapis.com \
  artifactregistry.googleapis.com \
  cloudbuild.googleapis.com \
  secretmanager.googleapis.com \
  --project "$PROJECT_ID" >/dev/null

if ! gcloud iam service-accounts describe "$RUNTIME_SA" --project "$PROJECT_ID" >/dev/null 2>&1; then
  printf 'Creating runtime service account: %s\n' "$RUNTIME_SA"
  gcloud iam service-accounts create "$RUNTIME_SA_NAME" \
    --project "$PROJECT_ID" \
    --display-name="BLUSSIT API Cloud Run runtime" >/dev/null
fi

# IAM is eventually consistent: a newly-created service account can be
# returned by create before projects.add-iam-policy-binding can resolve it.
for attempt in {1..12}; do
  if gcloud iam service-accounts describe "$RUNTIME_SA" --project "$PROJECT_ID" >/dev/null 2>&1; then
    break
  fi
  if [[ "$attempt" == 12 ]]; then
    fail "Runtime service account is not visible to IAM yet: $RUNTIME_SA"
  fi
  sleep 2
done

gcloud projects add-iam-policy-binding "$PROJECT_ID" \
  --member="serviceAccount:$RUNTIME_SA" \
  --role="roles/secretmanager.secretAccessor" \
  --quiet >/dev/null

for name in "${UNIQUE_SECRET_NAMES[@]}"; do
  if gcloud secrets describe "$name" --project "$PROJECT_ID" >/dev/null 2>&1; then
    printf 'Adding Secret Manager version: %s\n' "$name"
    printf '%s' "${!name}" | gcloud secrets versions add "$name" \
      --project "$PROJECT_ID" --data-file=- >/dev/null
  else
    printf 'Creating Secret Manager secret: %s\n' "$name"
    printf '%s' "${!name}" | gcloud secrets create "$name" \
      --project "$PROJECT_ID" \
      --replication-policy=automatic \
      --data-file=- >/dev/null
  fi
done

# Lets the JSON logs link to the request's trace in Cloud Logging.
GOOGLE_CLOUD_PROJECT="$PROJECT_ID"
NORMAL_NAMES+=(GOOGLE_CLOUD_PROJECT)

# ONE image for both services: the same code can never differ between the
# API and the worker. `gcloud builds submit` uploads SOURCE_DIR (filtered by
# .gcloudignore, checked above) and builds backend/Dockerfile.
if [[ -z "${DEPLOY_IMAGE:-}" ]]; then
  if ! gcloud artifacts repositories describe "$ARTIFACT_REPO" --location "$REGION" --project "$PROJECT_ID" >/dev/null 2>&1; then
    printf 'Creating Artifact Registry repository: %s\n' "$ARTIFACT_REPO"
    gcloud artifacts repositories create "$ARTIFACT_REPO" \
      --repository-format=docker --location "$REGION" --project "$PROJECT_ID" >/dev/null
  fi
  printf 'Building image %s ...\n' "$IMAGE"
  gcloud builds submit "$SOURCE_DIR" --tag "$IMAGE" --project "$PROJECT_ID" --quiet >/dev/null \
    || fail "Image build failed — nothing was deployed"
else
  printf 'Deploying the existing image %s (no build)\n' "$IMAGE"
fi

describe() {
  gcloud run services describe "$1" --project "$PROJECT_ID" --region "$REGION" --format="value($2)"
}

for target in "${TARGETS[@]}"; do
  svc="$(service_name "$target")"
  build_env_spec "$target"
  build_deploy_args "$target" "$ENV_SPEC"
  printf 'Deploying %s (RUN_MODE=%s)...\n' "$svc" "$target"
  # `gcloud run deploy` waits for the new revision's startup probe
  # (/api/ready) and fails if it never passes — traffic stays on the old one.
  gcloud "${DEPLOY_ARGS[@]}" >/dev/null || fail "Deploying $svc failed — its previous revision keeps serving (see DEPLOYMENT.md: rollback)"
  READY_REV="$(describe "$svc" status.latestReadyRevisionName)"
  CREATED_REV="$(describe "$svc" status.latestCreatedRevisionName)"
  [[ -n "$READY_REV" && "$READY_REV" == "$CREATED_REV" ]] \
    || fail "$svc: the new revision ${CREATED_REV:-?} is not ready (latest ready: ${READY_REV:-none}) — check its logs; nothing after it was deployed"
  if [[ "$target" == worker ]]; then
    # Internal ingress: not reachable from here. Its startup probe IS
    # /api/ready (database + critical indexes), so "ready" above means it.
    printf '%s ready: revision %s (internal; probes on /api/ready and /api/health)\n' "$svc" "$READY_REV"
    WORKER_REVISION="$READY_REV"
  else
    SERVICE_URL="$(describe "$svc" status.url)"
    # DEP-05: liveness (/api/health) says only that the process is up;
    # READINESS says it can serve — the database answers and the critical
    # indexes exist.
    printf 'Checking readiness: %s/api/ready\n' "$SERVICE_URL"
    READY=false
    for (( attempt = 1; attempt <= READY_RETRIES; attempt++ )); do
      if curl -fsS --max-time 10 "$SERVICE_URL/api/ready" >/dev/null 2>&1; then
        READY=true
        break
      fi
      (( attempt == READY_RETRIES )) || sleep "$READY_DELAY"
    done
    [[ "$READY" == true ]] || fail "Revision $READY_REV is deployed but NOT READY (GET $SERVICE_URL/api/ready != 200) — check the Cloud Run logs and roll back if needed"
    API_REVISION="$READY_REV"
  fi
done

printf '\nDeployment complete (image %s).\n' "$IMAGE"
if [[ -n "${API_REVISION:-}" ]]; then
  printf '%s: revision %s — %s (readiness %s/api/ready, liveness %s/api/health)\n' "$API_SERVICE" "$API_REVISION" "$SERVICE_URL" "$SERVICE_URL" "$SERVICE_URL"
fi
if [[ -n "${WORKER_REVISION:-}" ]]; then
  printf '%s: revision %s (internal only)\n' "$WORKER_SERVICE" "$WORKER_REVISION"
fi
printf 'Rollback: gcloud run services update-traffic <service> --region %s --to-revisions=<previous-revision>=100\n' "$REGION"
printf 'No secret values were printed.\n'
