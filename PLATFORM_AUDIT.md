# BLUSSIT — Platform Audit: Priorities, Cloud Costs & Infrastructure Plan

*Written 8 Sept 2026. Prices are approximate (INR, current published rates) — re-check
before committing to a paid tier. One key fact up front: **unused features sitting in the
codebase cost ₹0 to run.** Dead code doesn't consume cloud. What actually costs money is
the server, the database, WhatsApp messages, Google Maps calls, and stored images — and
every one of those has a free or near-free configuration below.*

---

## 1. TIER 1 — MUST HAVE (the platform cannot run without these)

| Feature | Backend | Why it's non-negotiable |
|---|---|---|
| Auth: register/login, JWT, phone OTP (WhatsApp), role gates | `auth`, `user` | No accounts = no platform |
| Service catalog: services, vehicle types, per-type pricing, add-on/variant rules | `catalog`, `vehicle_type`, `pricing` | This IS the product being sold |
| Slots & capacity: admin slots, atomic reservation, cutoffs, duplicate guard | `booking_policy`, `capacity_policy`, slot repos | Prevents overbooking — a real-money failure |
| Booking lifecycle: create → assign → heading → reached → photos → complete, status machine, cancel/reschedule | `booking` | The core transaction |
| Captain workflow: jobs list, geo-tagged steps, before/after photos | `booking`, `upload` | Service can't be delivered or proven without it |
| Manager queue & captain assignment | `booking`, `staff_directory` | Bookings die unassigned otherwise |
| Service centers + zone/pincode dispatch | `service_center`, `zone` | Decides *which* team serves a booking |
| Core notifications: in-app + WhatsApp booking events (confirmed, assigned, completed) | `notification`, `whatsapp` | Customers must know what's happening |
| Photo upload & storage (8MB cap) | `upload`, `storage` | Proof-of-work photos are mandatory in the flow |
| Admin essentials: services, pricing, centers, slot capacity, users | admin pages | Someone must be able to configure the business |
| Frontend: landing, customer booking wizard + guest wizard, captain panel, manager queue | — | The doors of the shop |

**Running cost of Tier 1 alone: ~₹0–500/month** (see §4).

---

## 2. TIER 2 — IMPORTANT (the niche is stronger with these; the platform runs without them)

| Feature | Notes |
|---|---|
| Subscriptions + vehicle-type tiers + quick-book | Recurring revenue engine — highest business value in this tier |
| Combos, coupons, first-time pricing | Growth levers; all flag/data-driven, zero cost when unused |
| WhatsApp bot (book via chat) + CRM inbox + template manager | Big differentiator for the Indian market; costs only per message sent |
| Reviews (captain/service split) | Trust engine |
| Complaints/support tickets (booking-linked, manager-routed) | Retention |
| KPI dashboards (admin + manager), analytics | Management eyes; heaviest queries in the app — see §5 |
| Attendance + leave (geo-stamped) | Ops discipline |
| Captain KYC + employee IDs + customer-facing captain card | Trust & safety |
| Anti-moonlighting: arrival geofence, step-gap sweeps, breadcrumbs | Protects revenue; runs inside the existing 60s loop — no extra infra |
| Live captain map + travel ETA | ETA uses Google Routes API — the ONE feature here with a per-call price; degrades automatically to free haversine when the key/quota is absent |
| Google sign-in | Conversion helper; free |
| Audit logs, purchase confirmations, cancellation-policy page | Cheap, keep |

**Extra running cost of Tier 2: only the WhatsApp messages + (optional) Maps calls — both quantified in §4.**

---

## 3. TIER 3 — LOW PRIORITY (park or remove; not relied on)

| Feature | Status today | Recommendation |
|---|---|---|
| Wallet / captain earnings / withdrawals / top-ups | **Already OFF** (`wallet_gating_enabled=false`); nav hidden | Leave off. Zero runtime cost. Revisit only with a payment gateway |
| Inventory / equipment-per-job deduction | Backend works, frontend sends `[]` — a live no-op | Park. It's the future supplies-reconciliation feature we deferred — costs nothing meanwhile |
| SMS channel (MSG91 / Fast2SMS) | Disabled (`SMS_PROVIDER=""`) — WhatsApp handles OTP | Leave off. DLT SMS ≈ ₹0.15–0.25 each vs WhatsApp auth ≈ ₹0.12. Only enable if WhatsApp delivery becomes a problem |
| Cloudinary storage | Configured but `STORAGE_PROVIDER=local` | Stay local until the disk matters (uploads are 14MB after months of dev). Cloudinary free tier covers you when you switch |
| Coverage leads (out-of-area interest capture) | Light | Keep (it's ~free) but don't invest further |
| Homepage config / testimonials / content CMS | Light | Keep as-is; don't extend |
| Referral codes | Partially wired | Park — no reward mechanism exists anyway |
| Marketing WhatsApp templates (`blussit_repeat_booking` draft, disabled trio) | Local drafts | Leave disabled — marketing messages are ~₹0.78 each, 6× a utility message |
| MSG91 OTP widget | Disabled | Leave off |

> **Important nuance:** removing Tier-3 *code* saves ~nothing (it's inert). The wins the
> user actually feels come from §5 (code heaviness) and §4 (per-call services). Don't
> spend effort deleting features — spend it capping the meters.

---

## 4. WHAT ACTUALLY COSTS MONEY — the full meter list

### 4a. Fixed monthly infrastructure

| Item | Free option | Paid option (when needed) |
|---|---|---|
| **Backend server** (FastAPI + WebSockets + 60s loop → needs an always-on box; "serverless" does NOT fit our reminder loop/WS) | **Oracle Cloud Always-Free ARM VM** (4 vCPU / 24GB RAM — absurdly generous, ₹0 forever) | Hetzner CX22 ≈ ₹450/mo, DigitalOcean ≈ ₹550/mo, Railway ≈ ₹450/mo. Any ONE small VM carries us to thousands of bookings/day |
| **Frontend hosting** (static Vite build) | **Cloudflare Pages — free, commercial use allowed.** (Vercel Hobby is free but its ToS forbids commercial use → Vercel would mean Pro ≈ ₹1,700/mo) | — or serve the static files from the same VM behind Caddy: ₹0 |
| **MongoDB Atlas** | **M0 free tier** (512MB). Our booking docs ≈ 5KB → ~100K bookings fit. Breadcrumbs auto-expire in 30 days (TTL) so they can't grow unbounded | Atlas Flex ≈ ₹700–2,500/mo when M0 is outgrown (likely year 2) |
| **Domain + SSL** | SSL free (Caddy/Let's Encrypt/Cloudflare) | Domain ≈ ₹800–1,200/year |
| **CDN / DDoS front** | **Cloudflare free plan** in front of both API and site | — |
| **Uptime monitoring** | UptimeRobot free | — |

**Fixed bill at launch: ₹0–550/month** (₹0 if Oracle free VM works out; one Hetzner/DO box otherwise).

### 4b. Per-use meters (the ones that grow with business)

| Service | Unit price (India, approx.) | Our usage per booking | At 10 bookings/day | At 50/day | At 200/day |
|---|---|---|---|---|---|
| **WhatsApp Cloud API — utility templates** (confirmed, assigned, on-the-way, completed, review ask, staff alerts) | ≈ ₹0.12/msg (free if sent inside an open 24h service window — bot users often are) | ~6–8 msgs | **≈ ₹250/mo** | ≈ ₹1,300/mo | ≈ ₹5,000/mo |
| WhatsApp — authentication (OTP) | ≈ ₹0.12/msg | ~1 per new user | ≈ ₹40/mo | ≈ ₹150/mo | ≈ ₹500/mo |
| WhatsApp — marketing | ≈ ₹0.78/msg | **0 (disabled — keep it that way)** | 0 | 0 | 0 |
| **Google Routes API** (road ETA at booking + captain picker, ≤5-call budget) | 10,000 calls/mo FREE, then ≈ ₹450/1,000 | ~2–3 calls | free | free | ~18K calls → cap it (below) |
| **Google Maps JS** (map loads: pickers, live map) | 10,000 loads/mo FREE, then ≈ ₹600/1,000 | staff-side mostly | free | free | borderline → Leaflet fallback |
| Geocoding / pin resolution | 10,000/mo FREE | <1 | free | free | free |
| Image storage (before/after photos ~2 per booking + KYC) | local disk ₹0; ~1–3MB/booking uncompressed | — | ~1GB/yr | ~5GB/yr | ~20GB/yr |

**The single most important cost control: in Google Cloud Console, set per-SKU quota caps
at 10,000/month on every Maps SKU, and restrict the key by domain/IP.** Then the Google
bill is *structurally* ₹0 — when a quota is hit, our code already falls back to free
haversine estimates automatically (`route_service.py` was built for exactly this).
An unrestricted leaked key, by contrast, is the #1 way small startups get a ₹1-lakh
surprise bill.

### 4c. Realistic total monthly bill

| Stage | Infra | WhatsApp | Maps | Total |
|---|---|---|---|---|
| **Launch** (≤10 bookings/day) | ₹0–550 | ≈ ₹300 | ₹0 (capped) | **≈ ₹300–850/mo** |
| **Growth** (50/day) | ≈ ₹550 | ≈ ₹1,500 | ₹0 (capped) | **≈ ₹2,000/mo** |
| **Scale** (200/day) | ≈ ₹1,500 (bigger VM + Atlas Flex) | ≈ ₹5,500 | ≈ ₹0–2,000 | **≈ ₹7,000–9,000/mo** |

At 200 bookings/day (~₹20L+/mo revenue at ₹350 average), ₹9K of cloud is 0.15% of revenue.
**The system as built is not expensive — it only needs the meters capped.**

---

## 5. HEAVY SPOTS IN OUR OWN CODE (real optimization targets, cheap fixes)

Ranked by actual impact — these matter more than removing features:

1. **Reminder-loop sweeps do full-collection scans.** `_reminder_loop` runs 7 sweeps every
   60s, several using `find_all_no_paginate` + Python-side date filtering. Harmless under
   ~10K booking docs; at scale, move the time predicates into the Mongo queries (indexes
   on `status` already exist). *Effort: small. When: >5K active-ish bookings.*
2. **Photo uploads are raw camera files (up to 8MB).** Compress client-side before upload
   (canvas → JPEG ~200KB). Cuts storage AND captain mobile-data usage ~90%. *Effort:
   small — one change in `PhotoCapture.tsx`. Do this before launch.*
3. **KPI endpoints recompute from raw bookings on every view**, and
   `captains_performance_for_center` is a deliberate N+1 (documented, capped at 200).
   Fine for one manager refreshing occasionally; add a 5-minute cache when dashboards get
   traffic (this is the already-deferred "M2 analytics caching"). *When: dashboards feel slow.*
4. **Frontend main chunk is 726KB (198KB gzipped).** Pages are already lazy-loaded;
   the remaining weight is the shared vendor chunk. Acceptable; splitting Leaflet/maps out
   of the main chunk is the next easy win. *Effort: small. Priority: low.*
5. **Location pings** are already bounded (25s, only during an active job) and breadcrumbs
   **auto-delete after 30 days** (TTL index). No action needed — designed cheap.
6. **WhatsApp message count per booking** is the biggest *per-use* lever: every template we
   drop from the lifecycle saves ≈ ₹0.12 × bookings. Current set (~6) is reasonable; resist
   adding more, and never enable the marketing trio for blasts.

---

## 6. INFRASTRUCTURE — what to use, and what NOT to use

### Use (simple, free/cheap, fits our shape)

| Tool | Why |
|---|---|
| **Docker + docker-compose** | One `docker-compose.yml`: `backend` (uvicorn, single worker — our locks assume it) + `caddy` (auto-SSL, serves the static frontend, proxies `/api` and `/ws`). Reproducible deploys, one-command restart. **This is the right amount of DevOps for us.** |
| **Caddy** (or Nginx) | Free auto-HTTPS, static files, reverse proxy, gzip |
| **Cloudflare (free)** | DNS + CDN + DDoS shield in front of everything; caches the frontend so the VM barely serves traffic |
| **Oracle Always-Free ARM VM** (or one Hetzner/DO box) | The entire backend fits on one small VM for a long time |
| **MongoDB Atlas M0 → Flex** | Managed backups/upgrades, free to start; we already run a separate `_test` DB |
| **GitHub Actions (free tier)** | Run the 205-test suite on push; build & push the Docker image |
| **UptimeRobot / Atlas alerts (free)** | Know when it's down |

### Do NOT use (yet — these would RAISE cost and complexity for zero benefit)

| Tool | Why not |
|---|---|
| **Kubernetes** | Built for fleets of services across many machines. We have ONE service that fits on one VM. K8s would mean 3+ nodes (₹3–5K/mo minimum), plus you become a part-time cluster administrator. Docker-compose does everything we need. Revisit only past ~1,000 bookings/day across multiple cities. |
| **Kafka** | An event-streaming backbone for millions of events/sec. Our event volume is a few thousand *per day*; the in-process 60s loop + Mongo + WebSockets already handle it. Kafka would add a broker to host, monitor and pay for — solving a problem we won't have for years. |
| **Redis** | Nothing needs it yet: sessions are JWT, locks/counters/caches live in Mongo (single-flight lease, atomic capacity, coupon usage). Add later *only* for hot-path caching if the KPI cache (§5.3) outgrows Mongo. |
| **Microservices** | The monolith is our advantage: one deploy, one log, transactions across booking+capacity+subscription in one process. Splitting it multiplies servers and failure modes. |
| **Serverless (Lambda/Cloud Run) for the backend** | Our reminder loop and WebSockets need an always-on process; serverless bills per-request and kills both. (Fine for the *frontend*, which is static anyway.) |
| **Vercel Pro just for hosting static files** | ₹1,700/mo for what Cloudflare Pages does free. |

### Ready-to-use deployment shape

```
Cloudflare (free)  ──► VM (Oracle free / Hetzner ₹450)
                        ├── caddy        :443  → auto-SSL
                        │     ├── /            → frontend/dist (static)
                        │     └── /api, /ws    → uvicorn :8000
                        └── uvicorn (1 worker) → MongoDB Atlas M0 (free)
                                               → Meta WhatsApp Cloud API (per msg)
                                               → Google Maps (quota-capped: ₹0)
                                               → uploads/ on VM disk (+ nightly backup)
```

---

## 7. ACTION CHECKLIST (in order)

1. ☐ **Google Cloud Console:** restrict the Maps key (domain + IP) and set 10K/month quota
   caps on every SKU → Google bill becomes structurally ₹0.
2. ☐ **Client-side photo compression** in `PhotoCapture.tsx` (§5.2) — biggest storage win,
   one small change.
3. ☐ Pick the VM: try Oracle Always-Free first; fall back to Hetzner/DO (~₹500/mo).
4. ☐ Write the `docker-compose.yml` + `Caddyfile` (backend, single worker + static frontend).
5. ☐ Frontend to Cloudflare Pages (or same VM) — not Vercel Hobby (commercial ToS).
6. ☐ Keep OFF: wallet, SMS provider, marketing templates, Cloudinary — all already off.
7. ☐ Atlas: stay on M0; set a storage alert at 400MB; upgrade to Flex only when it fires.
8. ☐ Later, when dashboards slow down: 5-min KPI cache; move sweep time-filters into Mongo.
9. ☐ Re-verify WhatsApp per-message rates and Maps free-tier terms before go-live (both
   changed in 2025 and can change again).
```
Bottom line: Tier 1+2 as built runs for ≈ ₹300–850/month at launch, dominated by
WhatsApp messages — not by "heavy features". No Kubernetes, no Kafka, no Redis.
One VM, one compose file, capped meters.
```
