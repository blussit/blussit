# BLUSSIT Deployment Runbook

The full system description (every service, data model, flow) is
[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md). This file is the operator's
runbook: how to deploy, check, scale and roll back.

## Production shape

```
browser / PWA ──> blussit.com (Vercel: prerendered public pages + SPA)
             └──> api.blussit.com ──> Cloud Run  blussit-api     (RUN_MODE=api,    public, 1..10 instances)
                                      Cloud Run  blussit-worker  (RUN_MODE=worker, internal, exactly 1 instance)
                                         both ──> MongoDB Atlas (replica set), Cloudflare R2, Razorpay,
                                                  Meta WhatsApp Cloud API, MSG91, Google APIs
```

**One codebase, one image, two Cloud Run services.** `RUN_MODE` decides what
a process runs (`backend/app/core/config.py`, `backend/app/main.py`):

| | `blussit-api` (`RUN_MODE=api`) | `blussit-worker` (`RUN_MODE=worker`) |
|---|---|---|
| Serves | every `/api/v1/*` route, Razorpay + WhatsApp webhooks, websockets, `/uploads`, `/api/health`, `/api/ready` | only `/api/health` and `/api/ready`; **every other path is 404** (business routers are not even mounted) |
| Background work | websocket relay (change stream on `ws_events`); background WhatsApp sends handed off by requests | reminder/sweep loop (under a Mongo lease), WhatsApp retry loop, template sync loop, websocket relay (so its events reach API instances) |
| Boot work | none (never builds indexes) | index creation + idempotent backfills/migrations, then template sync |
| Instances | `--min-instances 1 --max-instances 10`, concurrency 250 | `--min-instances 1 --max-instances 1`, concurrency 10 |
| CPU | always allocated (`--no-cpu-throttling`, opt out with `CLOUD_RUN_CPU_THROTTLING=true`) | always allocated (required) |
| Ingress / auth | `--ingress all --allow-unauthenticated` (public API) | `--ingress internal --no-allow-unauthenticated` (nobody outside can reach it) |
| Probes | startup: `GET /api/ready` | startup: `GET /api/ready`; liveness: `GET /api/health` (503 if a loop died → restart) |

Either service can crash, restart, scale or be rolled back without stopping
the other:

- **Worker down:** customers keep booking and paying (all request paths live
  in the API). What pauses: reminders, unpaid-booking expiry, Razorpay
  reconciliation sweeps (verify + webhook still settle payments), WhatsApp
  retries (first attempts still go out from the API), template sync, review
  requests. Everything is picked up when the worker returns — the sweeps work
  from database state, not from memory.
- **API down / redeploying:** the worker keeps sweeping; nothing customer-facing
  is served (Vercel's static pages still load).
- **Two workers by mistake** (or old + new revision during a deploy): the
  Mongo lease (`locks._id = "reminder_loop"`) lets exactly one sweep; the
  other idles. The WhatsApp retry and template sync claim their own rows /
  run at most hourly across instances.
- `RUN_MODE=all` (the default) runs everything in one process — local
  development and tests, or an emergency single-service fallback.

## Deploy (normal)

From the repository root, with the live values in `backend/.env.production`
(git-ignored; never `source` it):

```bash
scripts/deploy-gcp.sh --dry-run      # validate for BOTH modes; prints both gcloud commands; touches nothing
scripts/deploy-gcp.sh                # build one image, deploy worker, then API
```

What the script does, in order:

1. Refuses a tracked env file, a missing/weak `backend/.gcloudignore`, and any
   app setting already exported in the shell (e.g. `MONGO_URI`, `RUN_MODE`).
2. Parses the env file with python-dotenv (never `source`). A `RUN_MODE` line
   in it is ignored (each service gets its own).
3. Runs the app's own start-up validator (`python -m app.core.config --check
   --require-production`) on exactly the values Cloud Run will get — once with
   `RUN_MODE=worker`, once with `RUN_MODE=api`. Anything the app would refuse
   to start with stops the deploy here.
4. Ensures APIs, the runtime service account (`blussit-api-runtime@…`, shared
   by both services) and Secret Manager entries (new version per secret).
5. Builds **one** image with `gcloud builds submit backend --tag
   asia-south1-docker.pkg.dev/<project>/cloud-run-source-deploy/blussit-backend:<utc-time>-<git-sha>`
   (upload filtered by `.gcloudignore`; the script also asks gcloud for the
   upload list and refuses any env file).
6. Deploys **`blussit-worker` first** and waits until its new revision is
   Ready (its startup probe is `/api/ready`, so this means "database reachable
   + critical indexes present"). The worker builds new indexes and runs the
   boot migrations before the new API code takes traffic.
7. Deploys **`blussit-api`** from the same image, then polls
   `https://<api-url>/api/ready` until it answers 200.

A failed build, a failed worker deploy or a worker revision that never gets
ready stops the script **before** the API is touched (the previous revisions
keep serving).

Options:

| Need | Command |
|---|---|
| Deploy only one service (hotfix) | `scripts/deploy-gcp.sh --only api` / `--only worker` |
| Redeploy a known image (no build) | `DEPLOY_IMAGE=<full image ref> scripts/deploy-gcp.sh` |
| Validate only | `scripts/deploy-gcp.sh --dry-run` (or `DEPLOY_DRY_RUN=1`) |
| Different env file | `DEPLOY_ENV_FILE=path scripts/deploy-gcp.sh` |

Tunables (environment of the shell running the script): API —
`CLOUD_RUN_MIN_INSTANCES` (1), `CLOUD_RUN_MAX_INSTANCES` (10),
`CLOUD_RUN_CONCURRENCY` (250), `CLOUD_RUN_TIMEOUT` (3600 — websockets),
`CLOUD_RUN_MEMORY` (1Gi), `CLOUD_RUN_CPU_THROTTLING` (false),
`CLOUD_RUN_STARTUP_PROBE`; worker — `CLOUD_RUN_WORKER_CONCURRENCY` (10),
`CLOUD_RUN_WORKER_MEMORY` (1Gi), `CLOUD_RUN_WORKER_CPU` (1),
`CLOUD_RUN_WORKER_TIMEOUT` (300), `CLOUD_RUN_WORKER_STARTUP_PROBE`,
`CLOUD_RUN_WORKER_LIVENESS_PROBE` (`default` = none); shared —
`CLOUD_RUN_SERVICE` (blussit-api), `CLOUD_RUN_WORKER_SERVICE`
(blussit-worker), `CLOUD_RUN_REGION` (asia-south1), `CLOUD_RUN_ARTIFACT_REPO`
(cloud-run-source-deploy), `CLOUD_RUN_RUNTIME_SA`, `GCP_PROJECT_ID`.

## First deploy of the split (one-time, from the single-service setup)

Before 2026-10-07 `blussit-api` ran the API **and** the loops. The first
split deploy is just `scripts/deploy-gcp.sh`:

1. `blussit-worker` is created (new service, internal) and boots; it builds
   indexes and runs the migrations. While the old `blussit-api` revision
   (no `RUN_MODE` → `all`) still runs, the old process may keep holding the
   sweep lease — that is fine (exactly one sweeps).
2. `blussit-api` gets the new revision with `RUN_MODE=api`; old instances stop
   and release the lease; the worker takes over the sweeps within ~30 s.
3. Check (below): API ready, worker ready, lease holder is the worker.

Nothing else changes: the domain mapping `api.blussit.com → blussit-api`,
the webhooks and Vercel stay as they are.

## Checks after a deploy

```bash
# API readiness (public)
curl -s https://api.blussit.com/api/ready     # {"success":true,"checks":{"database":"ok","indexes":"ok"},"mode":"api"}
curl -s https://api.blussit.com/api/health    # liveness, "mode":"api"

# Worker (internal — check Cloud Run's view, not curl)
gcloud run services describe blussit-worker --region asia-south1 \
  --format='value(status.latestReadyRevisionName,status.latestCreatedRevisionName)'   # the two must match
gcloud run services logs read blussit-worker --region asia-south1 --limit 50          # "RUN_MODE=worker — running background loops…"
```

In the worker's logs, look for: `RUN_MODE=worker`, `Boot task … done`,
`Seat counter drift` (should be absent), `Reminder pass stopped early`
(occasional is fine), `hit its …s timeout` (investigate if frequent).
In MongoDB, `db.locks.findOne({_id: "reminder_loop"})` shows the lease holder
and when each paced sweep last ran (`repeat_sweep_at`, `seat_reconcile_at`,
`review_request_sweep_at`, …).

`/api/ready` answers 503 with `checks` (and `missing_indexes`) when the
database is unreachable or a critical unique index is missing. On a
**brand-new database** the API stays not-ready until the worker's first index
build finishes — deploy the worker first (the script does).

## Rollback

Each service rolls back independently:

```bash
gcloud run revisions list --service blussit-api    --region asia-south1
gcloud run revisions list --service blussit-worker --region asia-south1
gcloud run services update-traffic blussit-api    --region asia-south1 --to-revisions=<REVISION>=100
gcloud run services update-traffic blussit-worker --region asia-south1 --to-revisions=<REVISION>=100
```

Or redeploy a known-good image to both: `DEPLOY_IMAGE=<image> scripts/deploy-gcp.sh`.

- Rolling the API back to a **pre-split** revision (no `RUN_MODE` → `all`)
  is safe: that revision runs the loops too, and the lease keeps the sweeps
  single-flight with the worker.
- Index changes and boot migrations are additive and idempotent; a rollback
  never needs a database restore.

**Emergency single service** (worker can't start at all): run everything in
the API until it is fixed —

```bash
gcloud run services update blussit-api --region asia-south1 --update-env-vars RUN_MODE=all
```

(then set it back to `api` with the next normal deploy).

## Logs and monitoring

```bash
gcloud run services logs read blussit-api    --region asia-south1 --limit 100
gcloud run services logs read blussit-worker --region asia-south1 --limit 100
```

Production logs are JSON lines (severity, message, `request_id`, trace link
via `GOOGLE_CLOUD_PROJECT`); phones, OTPs, tokens and secrets are masked at
the logger. Every response carries `X-Request-ID`. Suggested Cloud Logging
alerts: `severity>=ERROR` on either service; the worker's
`textPayload:"Seat counter drift"`; `LOOP_DEAD`; `NOT_READY`. Error tracking:
set `SENTRY_DSN` (the hook is ready; add `sentry-sdk` to the image).
WhatsApp delivery health is in Admin → WhatsApp (queue, dead letters).

## Scaling

- **API:** raise `CLOUD_RUN_MAX_INSTANCES`. Every instance is stateless
  (sessions are JWTs; websocket fan-out goes through `ws_events`).
- **MongoDB connections:** each instance opens up to **30** (`maxPoolSize` in
  `backend/app/core/database.py`; a `maxPoolSize=` in `MONGO_URI` overrides
  it). Budget: `(API max instances + 1 worker) × 30 × 2` (old + new revisions
  overlap during a deploy) must stay under the Atlas tier's connection limit —
  10 API instances → 660 at peak. Atlas M10 allows 1,500; M0/M2/M5 allow 500
  (lower the pool or `MAX_INSTANCES` there).
- **Worker:** stays at exactly 1. Throughput is bounded by design: each pass
  runs every 60 s with a 70 s pass budget, 15 s per sweep, a 60 s hard cut per
  sweep; whatever a pass doesn't reach is picked up by the next one.
- **Rate limits are per API instance** (in memory): with N instances a client
  can get up to N× a limit. OTP, login and money caps are additionally
  enforced in the database. Keep `api.blussit.com` DNS-only (not
  Cloudflare-proxied) so client IPs are real. A shared store (Redis /
  Memorystore) is the step that makes limits global if abuse appears.

## Configuration

`backend/app/core/config.py` is the single list of settings and rules; the
app refuses to start (and the deploy refuses to ship) on any problem, listing
all of them. Names only here — values live in `backend/.env.production`
(local, git-ignored) and Secret Manager.

**Set by the deploy per service:** `RUN_MODE` (`api` | `worker`; required
explicitly in production), `GOOGLE_CLOUD_PROJECT`.

**Required in production (plain env vars):** `APP_ENV=production`,
`CORS_ORIGINS` (must include `https://blussit.com` and
`https://www.blussit.com`), `PUBLIC_BASE_URL=https://api.blussit.com`,
`TRUST_PROXY_HEADERS=true`, `TRUSTED_PROXY_COUNT` (1), `STORAGE_PROVIDER=r2`,
`R2_ENDPOINT_URL`, `R2_BUCKET_NAME`, `R2_PRIVATE_BUCKET_NAME`,
`R2_PUBLIC_BASE_URL` (public r2.dev/custom domain, never
`*.r2.cloudflarestorage.com`), `WHATSAPP_PROVIDER=meta_cloud`,
`WHATSAPP_PHONE_NUMBER_ID`, `WHATSAPP_BUSINESS_ACCOUNT_ID`,
`WHATSAPP_BUSINESS_NUMBER` (the business's own number — required since
2026-10-07), `WHATSAPP_OTP_TEMPLATE_NAME`, `WHATSAPP_UPDATE_TEMPLATE_NAME`,
`WHATSAPP_TEMP_PASSWORD_TEMPLATE_NAME`, `RAZORPAY_KEY_ID` (`rzp_live_…`),
`OTP_CHANNEL=whatsapp`, and an SMS fallback (`SMS_PROVIDER=msg91` or the
MSG91 widget trio).

**Secrets (Secret Manager):** `MONGO_URI`, `JWT_SECRET_KEY` (≥ 32 chars),
`R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`, `WHATSAPP_ACCESS_TOKEN`,
`WHATSAPP_WEBHOOK_VERIFY_TOKEN`, `WHATSAPP_APP_SECRET`, `RAZORPAY_KEY_SECRET`,
`RAZORPAY_WEBHOOK_SECRET`, `MSG91_AUTH_KEY` / `MSG91_TOKEN_AUTH` (when used),
`GOOGLE_MAPS_SERVER_KEY` (when set), `SENTRY_DSN` (when set).

**Optional:** `MONGO_DB_NAME`, `DEBUG` (must be false), `RATE_LIMIT_ENABLED`
(must be true), `ACCESS_TOKEN_EXPIRE_MINUTES` (15),
`REFRESH_TOKEN_EXPIRE_DAYS` (7), `WHATSAPP_API_VERSION`,
`WHATSAPP_*_LANGUAGE`, `MSG91_WIDGET_ID`, `MSG91_OTP_TEMPLATE_ID`,
`GOOGLE_MAPS_BROWSER_KEY`, `GOOGLE_OAUTH_CLIENT_ID`, `LOG_FORMAT`,
`SENTRY_TRACES_SAMPLE_RATE`, `META_PIXEL_ID`.

Never put a backend secret in a `VITE_*` variable — those are public.

## External setup (one-time / on change)

- **Domain:** `gcloud run domain-mappings create --service blussit-api --domain api.blussit.com --region asia-south1`.
  The worker has no domain. Vercel serves `blussit.com` (`www` redirects).
- **Razorpay:** live keys; auto-capture ON; webhook
  `https://api.blussit.com/api/v1/payments/webhook` (secret =
  `RAZORPAY_WEBHOOK_SECRET`) with events `payment.captured`, `order.paid`,
  `payment.failed`, `payment_link.paid`, `refund.processed` and
  `subscription.*` (activated, charged, pending, halted, cancelled,
  completed). Payment-link callback:
  `https://api.blussit.com/api/v1/payments/link-callback`.
- **Meta WhatsApp:** webhook `https://api.blussit.com/api/v1/whatsapp/webhook`
  (verify token = `WHATSAPP_WEBHOOK_VERIFY_TOKEN`, signature =
  `WHATSAPP_APP_SECRET`), fields `messages`,
  `message_template_status_update`, `template_category_update`,
  `user_preferences`. Templates are submitted from Admin → WhatsApp →
  Templates and synced hourly by the worker.
- **MongoDB Atlas:** replica set (transactions + change streams are
  required); allow Cloud Run egress; backups on.
- **Cloudflare R2:** public bucket (photos) + private bucket (KYC documents).
- **Vercel:** `VITE_API_BASE_URL=https://api.blussit.com/api/v1` for
  Production; Preview builds need their own (staging) API URL — they refuse to
  build against production.

## Local development

```bash
scripts/dev.sh up        # local Mongo replica set (docker, port 27099) + backend (RUN_MODE=all) + website
scripts/dev.sh test      # backend suite on a throwaway local database
```

Uses `backend/.env.development` only (fixed OTP 123456 on a local database).
To try the split locally, run two processes against the same local database:

```bash
cd backend
RUN_MODE=worker .venv/bin/uvicorn app.main:app --port 8001     # loops + indexes
RUN_MODE=api    .venv/bin/uvicorn app.main:app --port 8000     # what the website calls
```

Docker: `docker build -t blussit-backend:local backend`, then
`docker run --rm -p 8080:8080 --env-file backend/.env.development -e RUN_MODE=api blussit-backend:local`.

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| Deploy stops at "would refuse to start" | a production rule (listed above the error) — fix the env file |
| Worker revision never ready | its `/api/ready`: database unreachable from Cloud Run, or a unique index can't be built (legacy duplicates) — read the worker logs |
| API ready but no reminders / expiries | worker down or not holding the lease — check its revision status, logs and `locks.reminder_loop` |
| `LOOP_DEAD` in worker logs | a background loop crashed (bug); the liveness probe restarts it — find the stack trace above it |
| Websocket UIs don't refresh live | relay down on that instance (logs: "WebSocket relay stream failed"); UIs still refresh by polling |
| Inbound WhatsApp rejected | `WHATSAPP_APP_SECRET` mismatch |

## Moving off GCP

The image is portable: any container platform that can run two services from
one image with `RUN_MODE=api` / `RUN_MODE=worker`, inject the env vars and
secrets, and probe `/api/ready` works. Keep Atlas, R2, Razorpay, Meta and
Vercel as they are.
