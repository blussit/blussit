# BLUSSIT Backend Deployment

BLUSSIT keeps the React + Vite frontend on Vercel and runs only the FastAPI
backend as a container service. The production shape is:

`https://blussit.com` -> Vercel frontend -> `https://api.blussit.com` -> Cloud Run `blussit-api` -> MongoDB Atlas, Cloudflare R2, Razorpay, WhatsApp Cloud API, and Google APIs.

The backend image is portable. GCP-specific choices are limited to deployment
commands and Secret Manager wiring.

## Repository Structure

- `frontend/`: React + Vite frontend. Deploy this to Vercel.
- `backend/`: FastAPI backend. Build this directory as the Docker context.
- `backend/app/main.py`: FastAPI entrypoint.
- `backend/app/core/config.py`: environment-driven backend configuration.
- `backend/app/core/database.py`: Motor/MongoDB lifecycle and indexes.
- `backend/app/core/storage.py`: local/Cloudflare R2 upload abstraction.
- `backend/app/routes/v1/`: REST, webhook, and WebSocket routes.

## Local Development

Backend:

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

Frontend:

```bash
cd frontend
npm install
cp .env.example .env
npm run dev
```

For local frontend development, use:

```bash
VITE_API_BASE_URL=http://localhost:8000/api/v1
```

## Docker Build

Build only the backend:

```bash
sudo docker build -t blussit-api:local backend
```

Run locally with a non-production environment file:

```bash
sudo docker run --rm -p 8080:8080 --env-file backend/.env blussit-api:local
```

Health check:

```bash
curl http://localhost:8080/api/health
```

The container binds to `0.0.0.0` and reads Cloud Run's `PORT` variable.

## Required Environment

Normal configuration:

```bash
APP_NAME
APP_ENV
API_V1_PREFIX
DEBUG
RATE_LIMIT_ENABLED
TRUST_PROXY_HEADERS
MONGO_DB_NAME
JWT_ALGORITHM
ACCESS_TOKEN_EXPIRE_MINUTES
REFRESH_TOKEN_EXPIRE_DAYS
CORS_ORIGINS
STORAGE_PROVIDER
UPLOAD_DIR
DEFAULT_PAGE_SIZE
MAX_PAGE_SIZE
WHATSAPP_PROVIDER
WHATSAPP_API_VERSION
WHATSAPP_OTP_TEMPLATE_LANGUAGE
WHATSAPP_TEMPLATE_LANGUAGE
SMS_PROVIDER
OTP_CHANNEL
PUBLIC_BASE_URL
R2_ENDPOINT_URL
R2_BUCKET_NAME
R2_PUBLIC_BASE_URL
R2_UPLOAD_PREFIX
```

Sensitive secrets:

```bash
MONGO_URI
JWT_SECRET_KEY
R2_ACCESS_KEY_ID
R2_SECRET_ACCESS_KEY
WHATSAPP_ACCESS_TOKEN
WHATSAPP_PHONE_NUMBER_ID
WHATSAPP_BUSINESS_ACCOUNT_ID
WHATSAPP_WEBHOOK_VERIFY_TOKEN
WHATSAPP_APP_SECRET
FAST2SMS_API_KEY
MSG91_AUTH_KEY
MSG91_WIDGET_ID
MSG91_TOKEN_AUTH
MSG91_OTP_TEMPLATE_ID
GOOGLE_MAPS_BROWSER_KEY
GOOGLE_MAPS_SERVER_KEY
GOOGLE_OAUTH_CLIENT_ID
GOOGLE_OAUTH_CLIENT_SECRET
RAZORPAY_KEY_ID
RAZORPAY_KEY_SECRET
RAZORPAY_LIVE_KEY_ID
RAZORPAY_LIVE_KEY_SECRET
WHATSAPP_OTP_TEMPLATE_NAME
WHATSAPP_UPDATE_TEMPLATE_NAME
WHATSAPP_TEMP_PASSWORD_TEMPLATE_NAME
```

Production baseline:

```bash
APP_ENV=production
DEBUG=false
RATE_LIMIT_ENABLED=true
TRUST_PROXY_HEADERS=false
CORS_ORIGINS=https://blussit.com,https://www.blussit.com
STORAGE_PROVIDER=r2
R2_ENDPOINT_URL=https://d2208b1ea9cea36f384529411277791e.r2.cloudflarestorage.com
R2_BUCKET_NAME=blussit-images
R2_PUBLIC_BASE_URL=https://d2208b1ea9cea36f384529411277791e.r2.cloudflarestorage.com/blussit-images
R2_UPLOAD_PREFIX=photos
UPLOAD_DIR=/tmp/blussit-uploads
PUBLIC_BASE_URL=https://api.blussit.com
```

Do not set backend-only secrets as `VITE_*` frontend variables. Vite variables
are public to the browser.

## Google Cloud Setup

Enable these APIs:

```bash
gcloud services enable run.googleapis.com artifactregistry.googleapis.com cloudbuild.googleapis.com secretmanager.googleapis.com
```

Create secrets in Secret Manager, one per sensitive environment variable:

```bash
printf '%s' 'REDACTED_VALUE' | gcloud secrets create MONGO_URI --data-file=-
printf '%s' 'REDACTED_VALUE' | gcloud secrets create JWT_SECRET_KEY --data-file=-
```

Repeat for the sensitive variables listed above. Grant the Cloud Run runtime
service account `roles/secretmanager.secretAccessor`.

## Cloud Run Deploy

Service name: `blussit-api`

Region: `asia-south1`

From the repository root:

```bash
gcloud run deploy blussit-api \
  --source backend \
  --region asia-south1 \
  --allow-unauthenticated \
  --port 8080 \
  --set-env-vars APP_ENV=production,DEBUG=false,API_V1_PREFIX=/api/v1,CORS_ORIGINS=https://blussit.com,https://www.blussit.com,STORAGE_PROVIDER=r2,UPLOAD_DIR=/tmp/blussit-uploads,PUBLIC_BASE_URL=https://api.blussit.com,RATE_LIMIT_ENABLED=true,TRUST_PROXY_HEADERS=false,R2_ENDPOINT_URL=https://d2208b1ea9cea36f384529411277791e.r2.cloudflarestorage.com,R2_BUCKET_NAME=blussit-images,R2_PUBLIC_BASE_URL=https://d2208b1ea9cea36f384529411277791e.r2.cloudflarestorage.com/blussit-images,R2_UPLOAD_PREFIX=photos \
  --set-secrets MONGO_URI=MONGO_URI:latest,JWT_SECRET_KEY=JWT_SECRET_KEY:latest,R2_ACCESS_KEY_ID=R2_ACCESS_KEY_ID:latest,R2_SECRET_ACCESS_KEY=R2_SECRET_ACCESS_KEY:latest,RAZORPAY_KEY_ID=RAZORPAY_KEY_ID:latest,RAZORPAY_KEY_SECRET=RAZORPAY_KEY_SECRET:latest,WHATSAPP_ACCESS_TOKEN=WHATSAPP_ACCESS_TOKEN:latest,WHATSAPP_PHONE_NUMBER_ID=WHATSAPP_PHONE_NUMBER_ID:latest,WHATSAPP_WEBHOOK_VERIFY_TOKEN=WHATSAPP_WEBHOOK_VERIFY_TOKEN:latest,WHATSAPP_APP_SECRET=WHATSAPP_APP_SECRET:latest
```

Use additional `--set-secrets` mappings for MSG91, Google, live Razorpay
parking keys, and WhatsApp template names if those features are enabled.

After deploy:

```bash
gcloud run services describe blussit-api --region asia-south1
gcloud run services logs read blussit-api --region asia-south1 --limit 100
```

## Domains

- Vercel remains canonical for `https://blussit.com`.
- Keep the existing `www.blussit.com` -> `blussit.com` redirect in Vercel/DNS.
- Map `api.blussit.com` to the Cloud Run service.

For Cloud Run domain mapping:

```bash
gcloud run domain-mappings create --service blussit-api --domain api.blussit.com --region asia-south1
```

## Vercel Frontend

Set:

```bash
VITE_API_BASE_URL=https://api.blussit.com/api/v1
VITE_WHATSAPP_NUMBER=<public_whatsapp_number>
```

Do not point the frontend at the temporary Cloud Run URL in source code.

## Webhooks

Configured from discovered routes:

- WhatsApp verification and event webhook: `https://api.blussit.com/api/v1/whatsapp/webhook`
- Razorpay payment-link callback: `https://api.blussit.com/api/v1/payments/link-callback`

No dedicated Razorpay server-to-server webhook route was found. Payments are
settled through signed checkout verification, signed payment-link callback, and
the existing payment-link polling sweep.

## MongoDB Atlas

Do not run MongoDB inside the container. Set `MONGO_URI` to the Atlas URI and
`MONGO_DB_NAME` to the production database name. Allow Cloud Run egress from
Atlas networking rules, or use the chosen private connectivity pattern.

Indexes are created from `backend/app/core/database.py` after startup. Critical
capacity and duplicate guards use MongoDB indexes and atomic updates, including
slot capacity, daily capacity, active customer booking duplication, payment
orders/links, WhatsApp message deduplication, and subscription/coupon counters.

## Storage

Cloud Run filesystems are ephemeral. Production must use:

```bash
STORAGE_PROVIDER=r2
R2_ENDPOINT_URL=https://d2208b1ea9cea36f384529411277791e.r2.cloudflarestorage.com
R2_BUCKET_NAME=blussit-images
R2_PUBLIC_BASE_URL=https://d2208b1ea9cea36f384529411277791e.r2.cloudflarestorage.com/blussit-images
R2_UPLOAD_PREFIX=photos
UPLOAD_DIR=/tmp/blussit-uploads
```

Local storage is only for development. Uploaded media must use Cloudflare R2
for production.

R2 access is server-side only through `POST /api/v1/uploads/photo`. The
frontend receives the returned public URL and stores that URL on bookings,
profiles, KYC records, or other business records. There is no browser-direct R2
upload path and no storage delete route in the current backend.

If `R2_PUBLIC_BASE_URL` uses a Cloudflare custom domain or public bucket URL,
keep `R2_ENDPOINT_URL` pointed at the S3-compatible account endpoint and set
`R2_PUBLIC_BASE_URL` to the URL browsers should load from.

## Realtime

The WebSocket endpoint is:

```text
/api/v1/ws?token=<access_jwt>
```

It authenticates the JWT and authorizes channel subscriptions. The registry is
in-process, so it is suitable for best-effort UI refresh messages on one
instance but not guaranteed cross-instance fanout. For multi-instance Cloud Run,
REST remains authoritative; realtime events may be missed by clients connected
to another instance. Add an external pub/sub broker later if cross-instance
realtime delivery becomes required.

## Logs and Rollback

Application logs go to stdout/stderr and appear in Cloud Run logs.

Rollback:

```bash
gcloud run revisions list --service blussit-api --region asia-south1
gcloud run services update-traffic blussit-api --region asia-south1 --to-revisions REVISION_NAME=100
```

## Troubleshooting

- Startup fails in production if `JWT_SECRET_KEY` is still the default.
- WhatsApp incoming POSTs fail closed outside debug unless `WHATSAPP_APP_SECRET` is set.
- Online payments are disabled if Razorpay keys are not set.
- Photo uploads fail in production if `STORAGE_PROVIDER=r2` is set without Cloudflare R2 credentials.
- Health endpoint is `GET /api/health`.

## Oracle Migration Notes

The backend uses standard container environment variables and external managed
services. To move later, deploy the same image to Oracle Container Instances,
OKE, or another container platform, keep Vercel for the frontend, keep MongoDB
Atlas/R2/Razorpay/WhatsApp configuration external, and replace only the
platform deployment commands and secret injection mechanism.
