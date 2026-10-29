# GCP Cost Optimization — Execution Prompt (v2, 2026-10-30)

Paste everything below the line into a Claude Code session opened in this repo.
Written for: Claude Code acting as the founder's cloud/cost engineer.

---

## Role and goal

Reduce the GCP bill for the BLUSSIT platform **without making the product slower or less reliable**.
Success = lower monthly spend AND no regression in speed, timeouts or uptime for customers, captains, managers and admins.
When cost and speed conflict, speed wins: stop and ask.

## Founder decisions (already made — do not re-litigate)

1. **Stay on Google Maps.** No migration to Ola/Mappls/OSM/MapLibre. Only reduce its cost and its unnecessary API calls.
   (If you ever need context: public `tile.openstreetmap.org` and public Nominatim are not safe for production use per OSM policy; the repo's Leaflet/OSM fallback already uses them — leave it as the rare fallback, don't expand it.)
2. **Speed first.** Never trade user-visible latency for savings.
3. **Society Plans:** no inbound queries right now → add a reversible "sleep" switch (Phase 4). It saves little GCP money; do it for load/noise only.
4. Founder pushes and deploys. You prepare changes on a branch and tell them what to run.

## Facts (audited 2026-10-29; re-verify before acting)

- Project `project-daa6cf1f-8789-4cf1-b7a`, billing account `0115AD-09B351-B4C428`, region `asia-south1`. gcloud/bq are in `./google-cloud-sdk/bin` (put it on PATH for the session). Logged in as contact.blussit@gmail.com.
- Cloud Run: `blussit-api` (min 1, max 10, 1 vCPU, 1 GiB, always-on CPU, concurrency 250, timeout 3600 s) and `blussit-worker` (min 1, max 1, 1 vCPU, 1 GiB, always-on CPU, ingress internal). One image, `RUN_MODE` selects the role. Deploy only via `scripts/deploy-gcp.sh`.
- Global HTTPS load balancer + static IP 8.233.139.243 + managed cert for `api.blussit.com` fronts the API.
- BigQuery dataset `billing_export` (asia-south1) exists. Billing Export into it may or may not be saved — check for a `gcp_billing_export_v1_*` table.
- ~35 Maps/other APIs are enabled but unused. The code uses only: Routes API (server), Maps JavaScript + Places + Geocoding (browser).
- Two API keys: `Maps Platform API Key` (**no referrer restriction, allowed for ~34 APIs — leak risk**) and `API key 2` (referrer `https://blussit.com/*`). Which one the browser vs the server uses is NOT yet confirmed.
- Artifact Registry: empty `blussit` repo; `cloud-run-source-deploy` ~900 MB with ~20 stale `blussit-api` + ~7 `blussit-backend` images. Three build buckets with no lifecycle rules. No budgets; Budget API disabled.
- Mongo (Atlas), R2, MSG91, WhatsApp, Razorpay are billed outside GCP.

## Already done in the working tree (uncommitted — verify with `git diff`, run tests, then commit)

Maps call reduction, already implemented and tested (35 targeted tests passed; frontend `tsc` clean):
- `backend/app/services/route_service.py`: `road_distance_eta(..., db=, live=)` now caches in `road_distance_cache` (keys snapped to ~110 m).
  Store→customer ETA = TRAFFIC_UNAWARE, 30-day cache. Live captain→customer / dispatcher ETA = TRAFFIC_AWARE, 2-minute cache. Fallbacks cached 2 min.
- Call sites pass `db=self.db` (and `live=True` for captain legs): `booking_service.py` (3), `staff_directory_service.py` (1).
- `frontend/src/lib/googleMaps.ts`: `reverseGeocode` caches per ~11 m cell for the tab session and de-dupes concurrent lookups; failures are not cached.
- `backend/tests/test_route_eta_cache.py` (new), `docs/ARCHITECTURE.md` updated.
- First task for you: confirm the full backend suite is green on LOCAL Mongo (see Hard rules), review the diff, and commit these as one small commit on a branch.

## Hard rules

1. **Read-only first.** Every phase starts with inspection; show findings before changing anything.
2. **Approval gates.** Ask the founder before: deleting any resource, changing Cloud Run settings, touching the load balancer/DNS, changing/deleting/rotating API keys, enabling billing APIs, deploying. One question per gate, with a recommendation.
3. **Never print secret values.** Compare key strings by hash locally; never `cat`/echo `.env*` or Secret Manager payloads.
4. **Production Mongo.** `backend/.env` points at PRODUCTION. Run tests only on local docker Mongo (container `blussit-dev-mongo`, port 27099; start with `docker --context default` if the default context is down). Use an inline, quoted `MONGO_URI="mongodb://127.0.0.1:27099/?replicaSet=rs0&directConnection=true"` and confirm the host before running. Never `source` an env file. Python is `./venv/bin/python`. Known baseline failures: 3.
5. **Branches.** `main` = deployed production. Work on a branch; never push. Never use ad-hoc `gcloud run deploy --source`.
6. **Docs.** Update `docs/ARCHITECTURE.md` with every change (standing rule). UI copy in Title Case. Keep replies short.
7. **Rollback ready.** Before any Cloud Run change record current revision names and the rollback: `gcloud run services update-traffic <svc> --to-revisions=<prev>=100 --region asia-south1`.

## Speed guardrails (a change that breaks these is rejected and rolled back)

- API `min-instances` stays ≥ 1. Never scale the API to zero.
- Keep `--cpu-boost`, the `/api/ready` startup probe, graceful-shutdown settings.
- Baseline BEFORE, compare AFTER, same endpoints/time of day: `/api/health`, slot listing, booking quote, OTP send, login, one WebSocket connect.
  Targets: p95 ≤ baseline +10%, zero new 5xx/timeouts, no new API cold-start log lines.
- One change at a time; soak ≥ 24 h (or a few hundred requests) before the next; canary with `--no-traffic --tag` where possible.

## Phase 0 — Baseline and visibility

1. Check Billing Export: `bq ls billing_export`. If no table, tell the founder exactly: Billing → Billing export → BigQuery export → project above, dataset `billing_export` → Save on **Standard AND Detailed**. Data only starts from the day it is saved.
2. Until it has data, every cost number is an "estimate"; label it so.
3. Capture Cloud Run baselines (requests, p50/p95/p99, instance count, CPU/memory utilisation, cold starts) into `docs/cost/baseline-<date>.md`.
4. With ≥ 2 days of export data, write `docs/cost/queries.sql` (cost by service / SKU / day / label) and report the real top cost drivers — especially Maps SKUs.

## Phase 1 — Zero-risk cleanup (free, no traffic impact; one approval for the list)

1. Disable unused APIs. **Keep:** run, cloudbuild, artifactregistry, secretmanager, logging, monitoring, cloudtrace, iam, iamcredentials, compute (load balancer), storage*, bigquery*, serviceusage, servicemanagement, cloudapis, and the Maps ones in use (Maps JavaScript `maps-backend`, Places, Geocoding, Routes).
   **Disable** the rest (Solar, Air Quality, Pollen, Weather, Aerial View, Street View, Roads, Elevation, Navigation, Route Optimization, Address Validation, Static Maps, Tile, Isochrones, Maps iOS/Android, Embed, Timezone, legacy Directions/Distance Matrix, Geolocation, Map Management/Datasets, Area Insights, Place Widgets, Maps Tools, unused Pub/Sub, Container Registry, Datastore, Dataform, Dataplex, Analytics Hub, AI Companion, Gemini Cloud Assist, Cloud Asset, AppHub/Optimize/Topology, Design Center, SQL component, OS Login).
   Grep the repo for each before disabling; disable in batches; watch logs 10 min for `SERVICE_DISABLED`; re-enable instantly on any hit.
2. Delete empty Artifact Registry repo `blussit`. Add a cleanup policy to `cloud-run-source-deploy`: keep last 5 versions and anything tagged `latest`; delete untagged > 14 days. Never delete an image used by a serving revision.
3. Delete the empty `run-sources-<project>` bucket; add 30-day delete lifecycle to `run-sources-...-asia-south1` and `<project>_cloudbuild`.
4. Add `google-cloud-sdk/` and `google-cloud-cli-linux-x86_64.tar.gz` to `.gitignore`.

## Phase 2 — Maps key lockdown and spend caps (highest risk reducer)

1. Identify which key the browser uses (`mapsApi.config()` → `browser_key`, `frontend/src/lib/googleMaps.ts`) and which the server uses (`GOOGLE_MAPS_SERVER_KEY` secret — compare hashes, don't print).
2. Browser key → HTTP referrers `https://blussit.com/*`, `https://www.blussit.com/*` (+ any staging host) and APIs: Maps JavaScript, Places, Geocoding only.
3. Server key → restrict to the Routes API only; keep it only in Secret Manager.
4. Delete any key that is unused after step 1 (ask first). Rotate a key that was ever unrestricted if the founder agrees; update Secret Manager and redeploy through the deploy script.
5. Set per-API daily quotas (the only true spend cap) at ~3× the observed peak daily usage.
6. Enable the Billing Budget API (ask) and create a monthly budget with 50/90/100% email alerts (ask the amount). State plainly: budgets alert, quotas cap.

## Phase 3 — Remaining Maps call reduction (Google stays)

Already done: Routes ETA caching, reverse-geocode caching (see above). Still to do — report each with expected saving, then implement the approved ones, each with tests:
- **Map loads are billed per `new google.maps.Map()`.** Audit `LocationPicker.tsx`, `MapPicker.tsx`, `LiveCaptainMap.tsx`, `AdminServiceZonesPage.tsx`: remount/re-open creates a new billed load. Options: keep the picker mounted but hidden within a booking, or reuse one Map instance per page session. Only do it if it causes no layout/regression risk; measure first.
- **Places Autocomplete:** keep `fields: ["geometry"]`, `componentRestrictions: in`; add an Indore `bounds` bias (not strict) so users pick sooner; confirm the widget's session behaviour (autocomplete requests + the selection are billed as one session only if a place is selected).
- **Pin-first UX:** "Use my location" + drag pin needs no Autocomplete. Check the flow doesn't fire Autocomplete or geocode when the user only taps GPS.
- **`QuickBookFlow.tsx:425` preload** loads the Maps script 800 ms after the booking page opens. Script load is not billed; leave unless it hurts mobile load time.
- **Per-SKU usage counters:** add lightweight per-day counts of Routes calls (cache hit vs miss) to logs or a tiny collection so cost can be attributed and anomalies alerted.
- **Distance charge** (`charge_road_km`) is already optimised (traffic-unaware, 30-day cache, snapped keys) — leave it.

## Phase 4 — Society Plans "sleep" switch (reversible; not deletion)

Tell the founder up front: society code runs inside the same two Cloud Run services, so this saves little GCP money; the value is less load and noise.
1. Add `society_plans_enabled` (admin-editable runtime setting, default **true**, cached — not read per request from a cold query).
2. When off: skip society sweeps in the worker (`alert_missing_attendance`, `_society_schedule_sweep` in `backend/app/main.py`), hide society entry points for customers/managers/admins, make `/society/:token` show a polite "Paused" page, and make society mutation endpoints return a clear 403/503 — **no data is deleted or altered**.
3. One-click reversible from the admin UI with an audit-log entry. Tests for both states. Update `docs/ARCHITECTURE.md` and `docs/SOCIETY_PLANS.md`.
4. Refuse to sleep while any active society pass or pending society payment exists unless the founder confirms.

## Phase 5 — Codebase waste audit (report first, fix approved items)

Review and report; fix only approved items, one small commit each with tests:
- Background loops in `backend/app/main.py` (~L1195–1550): poll intervals, unindexed queries, full scans, anything running on the API that should run only in `RUN_MODE=worker`.
- Mongo: N+1 patterns, unbounded `find()`, missing projections, missing/duplicate indexes, per-request queries that could be cached. Confirm the Atlas cluster is in GCP Mumbai (cross-region = egress + latency).
- Outbound HTTP: repeated WhatsApp/MSG91/Razorpay/Google calls; `shared_client` reuse; every call has a timeout.
- WebSockets (`ws_routes.py`): idle connections, ping/timeouts, frontend reconnect storms.
- Frontend polling: `refetchInterval`/`setInterval` (e.g. 45 s travel-status poll in `BookingDetailDrawer.tsx`) — pause when hidden, stop when not needed, avoid duplicate fetches.
- Logging: level, per-request INFO lines, large payloads; keep `--no-access-log`; add exclusion filters for health-check noise if volume is non-trivial.
- Asset weight: big images served through the API instead of R2/CDN; confirm gzip/br.
- Failure risks: unhandled exceptions in loops, retries without backoff, timeouts shorter than a dependency's p99.
- Run the full backend suite on local Mongo before and after.

## Phase 6 — Cloud Run right-sizing (data-driven, one change per soak)

- **API CPU allocation:** test request-based CPU (`--cpu-throttling`, min 1 kept; the deploy script supports `CLOUD_RUN_CPU_THROTTLING=true`). Big potential saving; keep only if WebSockets, webhooks and post-response background work still behave and p95 holds. Canary a tagged revision first; revert on any stall.
- **Worker:** stays always-on, max 1. Try 0.5 vCPU / 512 MiB only if utilisation stays < 40% for a week.
- **Memory:** lower the API below 1 GiB only if p99 peak memory < 60% of the new limit.
- Keep maxScale 10, concurrency 250 and the 3600 s timeout (WebSockets) unless metrics show waste.
- Report each change with before/after cost estimate and latency numbers.

## Phase 7 — Load balancer (decision only; no change without a tested plan)

The HTTPS LB (~$18+/month + data) fronts one service for `api.blussit.com`. Compare in writing, with risk to the live domain:
(a) keep as is; (b) Cloud Run domain mapping (check asia-south1 support, TLS, latency); (c) Cloudflare in front of the Cloud Run URL.
Also decide whether `blussit-api` should accept only the LB (`ingress: internal-and-cloud-load-balancing`). Do not change DNS/ingress without a rollback and explicit approval.

## Phase 8 — Verify and report

1. Re-run the Phase 0 baseline; compare. Anything over the speed guardrails → roll back that step.
2. After each deploy, smoke test on the live URL: anonymous booking + OTP, login, slot picker, map pin + address fill, quote, payment link, captain job card, manager dashboard + live captain map, booking drawer ETA, WebSocket updates.
3. Write `docs/cost/REPORT-<date>.md`: what changed, before/after monthly estimate (actuals from Billing Export when available), latency before/after, what was deliberately NOT changed and why, rollback commands, open decisions.
4. Update `docs/ARCHITECTURE.md` and deploy docs; save durable lessons to memory.

## Order of work

0 baseline + billing export → commit the already-done Maps work (tests green) → 1 free cleanup → 2 key lockdown + quotas + budget → 3 remaining Maps reduction →
4 society switch → 5 waste audit → 6 Cloud Run tuning → 7 LB decision → 8 verify + report.
Stop and ask at every gate in "Hard rules".
