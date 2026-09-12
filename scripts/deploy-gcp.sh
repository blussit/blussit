#!/usr/bin/env bash
set -Eeuo pipefail

# Deploy the existing backend configuration to Cloud Run.
# The source env file is local-only and is ignored by git (backend/.env).

ENV_FILE="${DEPLOY_ENV_FILE:-backend/.env}"
SERVICE="${CLOUD_RUN_SERVICE:-blussit-api}"
REGION="${CLOUD_RUN_REGION:-asia-south1}"
PORT="${CLOUD_RUN_PORT:-8080}"

fail() {
  printf 'ERROR: %s\n' "$*" >&2
  exit 1
}

command -v gcloud >/dev/null 2>&1 || fail "gcloud CLI is required"
[[ -f "$ENV_FILE" ]] || fail "Environment file not found: $ENV_FILE"
if command -v git >/dev/null 2>&1 && git ls-files --error-unmatch "$ENV_FILE" >/dev/null 2>&1; then
  fail "Refusing to use a tracked production env file: $ENV_FILE"
fi

# This is a trusted dotenv file owned by the deployer. It is never copied into
# the image; values are used only to create Secret Manager versions and deploy.
set -a
# shellcheck disable=SC1090
source "$ENV_FILE"
set +a

# Defaults mirror backend/app/core/config.py. Explicit values in ENV_FILE win.
: "${APP_NAME:=Doorstep Vehicle Care Platform}"
: "${APP_ENV:=development}"
: "${API_V1_PREFIX:=/api/v1}"
: "${DEBUG:=true}"
: "${RATE_LIMIT_ENABLED:=true}"
: "${TRUST_PROXY_HEADERS:=false}"
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

require_value() {
  local name="$1"
  [[ -n "${!name:-}" ]] || fail "Required variable is missing or empty: $name"
}

# Production deployment invariants.
[[ "$APP_ENV" == "production" ]] || fail "APP_ENV must be production for Cloud Run deployment"
[[ "$DEBUG" == "false" ]] || fail "DEBUG must be false for Cloud Run deployment"
[[ "$STORAGE_PROVIDER" == "r2" ]] || fail "STORAGE_PROVIDER must be r2 for production deployment"
[[ "$RATE_LIMIT_ENABLED" == "true" ]] || fail "RATE_LIMIT_ENABLED must be true for production deployment"
require_value MONGO_URI
require_value JWT_SECRET_KEY
[[ "$JWT_SECRET_KEY" != "change-this-super-secret-key-in-production" ]] || fail "JWT_SECRET_KEY is still the default placeholder"
require_value CORS_ORIGINS
require_value R2_ENDPOINT_URL
require_value R2_ACCESS_KEY_ID
require_value R2_SECRET_ACCESS_KEY
require_value R2_BUCKET_NAME
require_value R2_PUBLIC_BASE_URL
require_value PUBLIC_BASE_URL

[[ "$CORS_ORIGINS" == *"https://blussit.com"* ]] || fail "CORS_ORIGINS must include https://blussit.com"
[[ "$PUBLIC_BASE_URL" == "https://api.blussit.com" ]] || fail "PUBLIC_BASE_URL must be https://api.blussit.com"

if [[ "$WHATSAPP_PROVIDER" == "meta_cloud" ]]; then
  require_value WHATSAPP_ACCESS_TOKEN
  require_value WHATSAPP_PHONE_NUMBER_ID
  require_value WHATSAPP_WEBHOOK_VERIFY_TOKEN
  require_value WHATSAPP_APP_SECRET
fi

if [[ "$SMS_PROVIDER" == "fast2sms" ]]; then
  require_value FAST2SMS_API_KEY
elif [[ "$SMS_PROVIDER" == "msg91" ]]; then
  require_value MSG91_AUTH_KEY
  require_value MSG91_OTP_TEMPLATE_ID
fi

# The MSG91 widget is enabled only when all three values are present.
MSG91_WIDGET_ENABLED=false
if [[ -n "${MSG91_AUTH_KEY:-}" || -n "${MSG91_WIDGET_ID:-}" || -n "${MSG91_TOKEN_AUTH:-}" ]]; then
  require_value MSG91_AUTH_KEY
  require_value MSG91_WIDGET_ID
  require_value MSG91_TOKEN_AUTH
  MSG91_WIDGET_ENABLED=true
fi

if [[ -n "${GOOGLE_MAPS_BROWSER_KEY:-}" ]]; then
  require_value GOOGLE_MAPS_BROWSER_KEY
fi
if [[ -n "${GOOGLE_MAPS_SERVER_KEY:-}" ]]; then
  require_value GOOGLE_MAPS_SERVER_KEY
fi

# Online payments are enabled only when both active Razorpay values exist.
RAZORPAY_ENABLED=false
if [[ -n "${RAZORPAY_KEY_ID:-}" || -n "${RAZORPAY_KEY_SECRET:-}" ]]; then
  require_value RAZORPAY_KEY_ID
  require_value RAZORPAY_KEY_SECRET
  RAZORPAY_ENABLED=true
fi

PROJECT_ID="${GCP_PROJECT_ID:-$(gcloud config get-value project 2>/dev/null)}"
[[ -n "$PROJECT_ID" && "$PROJECT_ID" != "(unset)" ]] || fail "Set GCP_PROJECT_ID or configure an active gcloud project"
gcloud config set project "$PROJECT_ID" >/dev/null

RUNTIME_SA="${CLOUD_RUN_RUNTIME_SA:-blussit-api-runtime@${PROJECT_ID}.iam.gserviceaccount.com}"
RUNTIME_SA_NAME="${RUNTIME_SA%@*}"

printf 'Preparing Cloud Run deployment: service=%s region=%s\n' "$SERVICE" "$REGION"
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

gcloud projects add-iam-policy-binding "$PROJECT_ID" \
  --member="serviceAccount:$RUNTIME_SA" \
  --role="roles/secretmanager.secretAccessor" \
  --quiet >/dev/null

SECRET_NAMES=()
NORMAL_NAMES=(
  APP_NAME APP_ENV API_V1_PREFIX DEBUG RATE_LIMIT_ENABLED TRUST_PROXY_HEADERS
  MONGO_DB_NAME JWT_ALGORITHM ACCESS_TOKEN_EXPIRE_MINUTES REFRESH_TOKEN_EXPIRE_DAYS
  CORS_ORIGINS STORAGE_PROVIDER UPLOAD_DIR R2_ENDPOINT_URL R2_BUCKET_NAME
  R2_PRIVATE_BUCKET_NAME R2_PUBLIC_BASE_URL R2_UPLOAD_PREFIX DEFAULT_PAGE_SIZE MAX_PAGE_SIZE
  WHATSAPP_PROVIDER WHATSAPP_PHONE_NUMBER_ID WHATSAPP_BUSINESS_ACCOUNT_ID
  WHATSAPP_API_VERSION WHATSAPP_OTP_TEMPLATE_NAME WHATSAPP_OTP_TEMPLATE_LANGUAGE
  WHATSAPP_UPDATE_TEMPLATE_NAME WHATSAPP_TEMP_PASSWORD_TEMPLATE_NAME WHATSAPP_TEMPLATE_LANGUAGE
  SMS_PROVIDER OTP_CHANNEL PUBLIC_BASE_URL GOOGLE_MAPS_BROWSER_KEY GOOGLE_OAUTH_CLIENT_ID
  RAZORPAY_KEY_ID
)

SECRET_NAMES+=(MONGO_URI JWT_SECRET_KEY R2_ACCESS_KEY_ID R2_SECRET_ACCESS_KEY)
if [[ "$WHATSAPP_PROVIDER" == "meta_cloud" ]]; then
  SECRET_NAMES+=(WHATSAPP_ACCESS_TOKEN WHATSAPP_WEBHOOK_VERIFY_TOKEN WHATSAPP_APP_SECRET)
fi
if [[ "$SMS_PROVIDER" == "fast2sms" ]]; then
  SECRET_NAMES+=(FAST2SMS_API_KEY)
elif [[ "$SMS_PROVIDER" == "msg91" ]]; then
  SECRET_NAMES+=(MSG91_AUTH_KEY)
fi
if [[ "$MSG91_WIDGET_ENABLED" == true ]]; then
  NORMAL_NAMES+=(MSG91_WIDGET_ID)
  SECRET_NAMES+=(MSG91_AUTH_KEY MSG91_TOKEN_AUTH)
fi
if [[ "$SMS_PROVIDER" == "msg91" ]]; then
  NORMAL_NAMES+=(MSG91_OTP_TEMPLATE_ID)
fi
if [[ -n "${GOOGLE_MAPS_SERVER_KEY:-}" ]]; then
  SECRET_NAMES+=(GOOGLE_MAPS_SERVER_KEY)
fi
if [[ "$RAZORPAY_ENABLED" == true ]]; then
  SECRET_NAMES+=(RAZORPAY_KEY_SECRET)
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

for name in "${UNIQUE_SECRET_NAMES[@]}"; do
  require_value "$name"
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

# Use a custom delimiter so CORS_ORIGINS commas remain part of one value.
NORMAL_SPEC=""
for name in "${NORMAL_NAMES[@]}"; do
  value="${!name:-}"
  if [[ -n "$NORMAL_SPEC" ]]; then
    NORMAL_SPEC+="|"
  fi
  NORMAL_SPEC+="${name}=${value}"
done

SECRET_SPEC=""
for name in "${UNIQUE_SECRET_NAMES[@]}"; do
  if [[ -n "$SECRET_SPEC" ]]; then
    SECRET_SPEC+=","
  fi
  SECRET_SPEC+="${name}=${name}:latest"
done

printf 'Deploying Cloud Run service...\n'
gcloud run deploy "$SERVICE" \
  --project "$PROJECT_ID" \
  --source backend \
  --region "$REGION" \
  --service-account "$RUNTIME_SA" \
  --port "$PORT" \
  --allow-unauthenticated \
  --set-env-vars="^|^${NORMAL_SPEC}" \
  --set-secrets="$SECRET_SPEC" \
  --quiet >/dev/null

SERVICE_URL="$(gcloud run services describe "$SERVICE" \
  --project "$PROJECT_ID" \
  --region "$REGION" \
  --format='value(status.url)')"
REVISION="$(gcloud run services describe "$SERVICE" \
  --project "$PROJECT_ID" \
  --region "$REGION" \
  --format='value(status.latestReadyRevisionName)')"

printf '\nDeployment complete.\n'
printf 'Cloud Run service URL: %s\n' "$SERVICE_URL"
printf 'Ready revision: %s\n' "$REVISION"
printf 'Health check: curl -fsS %s/api/health\n' "$SERVICE_URL"
printf 'No secret values were printed.\n'
