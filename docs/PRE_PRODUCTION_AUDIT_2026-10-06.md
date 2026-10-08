# Pre-Production Audit — 2026-10-06 (branch `blussit-v2`)

Full-system audit, fix and validation pass before the v2 production deploy.
Eight read-only audits (payments, bookings/concurrency, auth/IDOR ×2, society,
API hardening/config, database/scale, notifications/integrations, frontend)
each had to prove findings with a code trace or a reproduction against a local
throwaway Mongo — never production. Every confirmed finding was then fixed at
its root cause within the existing architecture, with a regression test that
fails on the old code and passes on the new.

**Verdict: NOT READY FOR PRODUCTION — yet.** Every code-level P0 and P1 found
is fixed and tested, but four things outside the code must happen first (see
[Production gate](#production-gate)).

## Validation

| Check | Result |
|---|---|
| Backend test suite (local replica set) | **981 passed, 1 skipped** (time-of-day skip), 0 failed — was 853 before this pass |
| New regression tests | 6 files, ~140 tests: `test_account_takeover_guards`, `test_payment_audit_fixes`, `test_booking_audit_fixes`, `test_society_audit_fixes`, `test_hardening_audit_fixes`, `test_whatsapp_audit_fixes` |
| Audit reproductions re-run on fixed code | every bug-asserting repro now fails (bug gone); race loops 0/10 wrong seat counters |
| Frontend type-check (`tsc -p tsconfig.app.json`) | pass |
| Frontend production build + prerender (`npm run build`) | pass — 15 pages pre-rendered |
| `bash -n scripts/deploy-gcp.sh` + stubbed dry run | pass; refuses unsafe config |
| Visual check (Playwright, 360 px / 1366 px) | no horizontal overflow on public, customer, captain, manager, admin routes |

Change set: 165 files modified (+4,198 / −5,321 — includes ~4,500 lines of
dead code removed), all uncommitted on `blussit-v2`.

## 1. Architecture (as it actually is)

- **Backend** — FastAPI + Motor/MongoDB replica set (Atlas in prod). `routes/v1`
  → controllers → services → repositories; `core/` holds config, authz
  (`ensure_own_center`), dependencies (JWT, role guards), rate limiting,
  security, websocket manager. ~42k lines; `booking_service.py` alone ~6k.
- **Frontend** — React 19 + Vite + react-query 5 + Tailwind v4, deployed on
  Vercel; public pages now pre-rendered to HTML at build time.
- **Deploy** — Cloud Run (min 1 / max 10 instances) via
  `scripts/deploy-gcp.sh`; Vercel for the site.
- **Background work** — one in-process reminder loop, made single-flight across
  instances by a Mongo lease: payment reconciliation, unpaid-booking expiry,
  reminders, late/unassigned flags, auto-pay renewals, pass expiry, society
  auto-booking.
- **External** — Razorpay (orders, links, subscriptions, webhook), WhatsApp
  Cloud API (bot, CRM, templates), MSG91 OTP widget, Google Maps/Routes,
  Cloudflare R2, Meta Pixel, GA4.
- **Roles** — customer (phone OTP), captain, manager (one center), admin
  (password); society residents via per-society link tokens.

## 2. Critical business flows traced

Customer booking (quote → OTP → create → pay/cash → confirm → captain →
complete → review); online payment (order/link → verify/webhook/sweep →
settle → confirm); captain lifecycle (assign → heading → verify code → photos →
complete → wallet settle → cash collect); passes (quote → checkout → activate
→ redeem → renew/auto-pay → expire); society (lead → society → link → resident
enrol → pay → schedule → auto-book → renew/cancel); manager/admin operations
(queue, assign, log job, edit, recycle bin, capacity, reports).

## 3. Findings and fixes

Severity: **P0** critical (security bypass, money lost/duplicated, data
corruption, overbooking), **P1** high (broken core flow, wrong price, major
race/scale bug), **P2** medium, **P3** low. "Repro" = reproduced by a test
against local Mongo before the fix.

### 3.1 Authentication & authorization

| ID | Sev | Where | Root cause → impact | Fix |
|---|---|---|---|---|
| AUTH-1 | P0 | `msg91_widget_service.py` | Token↔phone binding compared *digits extracted* from MSG91's identifier: a genuine token for `9876543210@attacker.example` matched phone 9876543210 → reset any account's password incl. staff. Repro. | Identifier must *be* a phone (digits, optional +91/0, separators) — `_bound_phone`. 8 new tests. |
| AUTH-2 | P0 | `auth_service.register_customer` | `/auth/register` set a password on any phone with no proof; the real owner's OTP bookings/logins then adopted that account and the planted password kept working. Repro. | Registration requires OTP/widget proof; new `mark_phone_proven` — the first proof on an unverified account wipes any unproven password and revokes all sessions. Wired into OTP login, anonymous booking, society enrol, WhatsApp bot. |
| AUTH-3 | P0 | `google_auth_service.py` | Google sign-in linked to any email-matching account and minted staff tokens. Repro. | Customers only; never joins an account that has a password. |
| AUTH-4/AZ-1 | P1 | `crm_service.py` | Any manager could read any customer's phone/email/addresses/plates (360 view + phone search). Repro. | 360 requires the customer to be known to the manager's center; search returns a minimal row. |
| AZ-3 | P1 | `auth_controller` | Any manager could force-reset any customer's password platform-wide. Repro. | Scoped to customers known to the manager's center. |
| AUTH-5(soc) | P1 | `society_routes.py` | Society form skipped OTP when the signed-in phone merely *matched* — squatter path into a resident's account. Repro. | Shortcut only for a freshly *verified* phone; OTP path evicts squatters. |
| AUTH-6 | P2 | `auth_service.login` | Lockout counter was read-modify-write; a parallel burst never locked. Repro (60 guesses, no lock). | Atomic `$inc`; lock re-checked at success. |
| AUTH-7 | P2 | KYC upload/download | Captain could submit another captain's document URL and download their Aadhaar. Repro. | Upload ownership records; KYC accepts own uploads only; downloads authorized from the record. |
| AUTH-8 | P2 | society "add resident" | Manager could retype any customer's car / overwrite their address platform-wide. Repro. | Never retype; never overwrite; personal plans shown only for their society. |
| AUTH-9..14 | P2/P3 | CRM search, coupons, webhook, captain center change, public catalogue/reviews, center reads | Over-exposure (captain_fee, raw reviews with ids and pre-edit text, private coupon terms), DEBUG-dependent webhook acceptance, stale captain center access. | All fixed (projections, admin-only flags, fail-closed webhook, center re-checks). |
| — | P3 | `main.py` | Short JWT secret only warned. | Refuses to start (<32 chars / default secret outside development). |

Checked and OK: refresh-token rotation + reuse detection, token_version
revocation, role/center re-read per request, websocket channel isolation,
captain service-code redaction, dev OTP bypass impossible in production.

### 3.2 Payments & money

| ID | Sev | Where | Root cause → impact | Fix |
|---|---|---|---|---|
| PAY-6 | P0 | `booking_service` split | Platform share computed before discounts; on cash jobs the captain's wallet paid for coupons/plan waivers (₹100 coupon → captain nets −₹60). Repro. | `platform_earning = total − captain_earning`; cash settlement from the current total, credits the captain if negative. Pass+add-on cars can't switch to cash. |
| PAY-3 | P0 | wallet settlement + `captain_collect_cash` | An "online" booking settled in cash: captain credited their pay AND kept all the cash. Repro. | Settlement records `wallet_settled_as`; cash collected on a credit-settled car is debited. |
| PAY-1 | P1 | `payment_service._subscription_order` | Crafted plan-only checkout bought any pass at the plan's flat (cheapest) price; the service-less pass covered everything (₹799 vs ₹1,323). Repro. | Per-service passes require car type + service at checkout. |
| PAY-2 | P1 | `subscription_service.upgrade` | Free upgrade refilled the quota; ping-pong = unlimited washes. Repro. | Used washes stay used; a pricier plan is refused (no pay-the-difference flow exists). |
| PAY-4 | P1 | cancel paths | Paid-online booking cancelled → money vanished from every report, no refund trail. Repro. | Idempotent refund-due row in the admin attention queue on every cancel/delete path. |
| PAY-5 | P1 | auto-pay cancel | Gateway cancel was fire-and-forget; later charges never applied or flagged. Repro. | Cancelled mandates stay watched until Razorpay confirms the end; failed cancels retried; charges applied or parked. |
| EXT-2 | P1 | `prepare_expiry` | A Razorpay 5xx/HTML error released a slot the customer may have paid for. Repro. | Only a definitive "no such record" is skipped; other errors keep the booking until the hard stop. |
| PAY-7 | P2 | coupon usage | Recorded after commit; a limit race left an extra discounted booking. Repro. | Reserved before insert; rolled back on failure. |
| PAY-8 / EXT-3 | P2 | links | Links never expired and fell off the sweep after 2 days; captain cash / online payment didn't void other open links → second payment possible. Repro. | 7-day `expire_by`; sweeps cover it; links voided on cash and on online settle; bot reuses open links. |
| PAY-9 | P2 | tier guard, vehicle edit | Hatchback pass washed an XUV; pass car could be retyped. Repro. | Pass-price tier comparison; retype blocked while a pass is live. |
| PAY-10 / DB-4 | P2 | `admin_collections` | Plan revenue window 5.5 h shifted (naive IST vs UTC). Repro. | UTC-converted window. |
| SOC-6 | P2 | society renewal | Renewal paid twice applied twice, corrupting the cycle window. Repro. | Order frozen to its cycle; second payment parked or left uncaptured. |

Checked and OK: every charged amount resolved server-side; HMAC verification
(orders, reversed subscription signature, links, webhook) with
`compare_digest`; atomic paid-exactly-once claim shared by verify/webhook/sweep;
duplicate captures parked; frontend never treats checkout success as paid.

### 3.3 Bookings, slots & concurrency

| ID | Sev | Root cause → impact | Fix |
|---|---|---|---|
| BOOK-1 | P0 | Captain photo steps wrote status unconditionally: a manager cancel racing the after-photo ended **completed + captain paid** (15/15 races). Repro. | Guarded claims for verify, before-photo, after-photo; money moves only if the claim wins; duplicates quiet. |
| BOOK-2 | P0 | Capacity policy changes didn't reach dates that already had slot docs → a cut from 4 to 1 still sold 4. Repro. | Resync every non-override doc from the effective date to the next change. |
| BOOK-3 | P1 | Recycle bin refunded pass washes twice; restore never re-spent them. Repro. | Atomic `consumption_restored` claim; restore re-commits or is refused. |
| BOOK-4 | P1 | Visit seat released twice (rollback + concurrent cancels, 10/10) → oversell. Repro. | Seat only released by the car that holds it; cancels serialized in a transaction. |
| BOOK-5 | P1 | Reschedule double-tap / group with a finished car mis-moved seats. Repro. | Date in the guard; first moved car moves the seat. |
| BOOK-6 | P1 | `self_assign` resurrected cancelled bookings. Repro. | Guarded write. |
| BOOK-7 | P1 | "Log a done job" double-submit recorded two jobs. Repro. | Insert-first claim doc. |
| BOOK-8 | P1 | Soft-deleted bookings blocked rebooking; deleting an unpaid booking leaked its seat. Repro. | Index v3 with `is_deleted:false`; awaiting-payment seats released/re-reserved. |
| BOOK-9 | P1 | Mixed ₹0-pass + pay-online visit never expired and couldn't be closed. Repro. | Whole visit parked/confirmed together; expiry ignores ₹0 cars. |
| BOOK-10..13 | P2 | Stale sweep flags on moving jobs; penalties/verification surviving a captain release; reschedule bypassing the 4-hour cancel lock; wrong "already paid" message. | All fixed. |

Checked and OK (tested): 50 customers racing a 3-seat slot → exactly 3 booked;
6 concurrent reschedules into a 2-seat slot → exactly 2; same-customer triple
submit → one visit; two managers assigning → one wins.

### 3.4 Society system

SOC-1 (P1) two enrollments of one resident collided in the schedule → grouped
by customer · SOC-2 (P1) admin-chosen center ≠ pin's center → refused ·
SOC-3 (P2) a removed slot made the sweep retry forever and starve later visits
→ overflow + `failed` with reason · SOC-4 (P2) early renewal lost a refundable
wash → bounded total · SOC-5 (P2) one plate on two live society passes →
refused plate-wide · SOC-6 (P2) see 3.2 · SOC-7 (P2) concurrent same-day
generation double-booked every car → per-(society, date) lock · SOC-8 (P2)
switching the form off removed every resident's hub → hubs stay open ·
SOC-9..17 (P2/P3) missing cancel notice, booking past plan end, staff quote,
request-cancel race, orphan repeat-wash rules, double "plan active" notice,
approve-move mutating before validating — all fixed. 23 regression tests.

### 3.5 Database, scale & performance

| ID | Sev | Root cause → impact | Fix |
|---|---|---|---|
| SCALE-1 | P1 | Reminder/late/unassigned finders cut at 500 rows before the "is it due" check → at ~500 open bookings/day reminders silently stop. Repro. | Stream every candidate earliest-slot first; cap only the due list; new index. |
| DB-1 | P1 | Manager queue sort matched no index → whole-center scan per poll (99,881 docs for 20 rows). | Three queue indexes; count capped at 5,000 with `total_capped`. |
| DB-2 | P1 | No `{captain_id, status}` index → every 25 s location ping scanned the captain's lifetime history. | Index added. |
| DB-3 | P1 | Booking-number search scanned the whole index (all start "BK"). | Exact `$in` over padded forms. |
| SCALE-2 / EXT-1 | P1 | Cloud Run throttles CPU outside requests → background loop and background WhatsApp stall. | Deploy uses `--no-cpu-throttling` (opt-out env). |
| PERF-1 | P2 | Sweep notifications awaited WhatsApp inline. | Sent in background (in-app row still immediate). |
| PERF-2 | P2 | Manager summary scanned all-time history per mount. | 60 s per-center cache. |
| DB-5/DB-8 | P2/P3 | GPS trail froze after 3.5 h; one failed index build skipped all later ones. | Fixed. |
| SCALE-3 | P2 | Websocket events don't cross instances. | Short-term: live map polls every 25 s. Durable fix needs Redis/change streams (see remaining risks). |

Capacity estimate (after fixes): 1k–10k customers comfortable on current
design; 100k customers / ~1M bookings needs Atlas M30+, the remaining P2
items below (analytics bounds, subscription overview distinct), and shared
websocket fan-out.

### 3.6 API hardening, abuse & config

Fixed: request body size limit (2 MB, uploads 9 MB) enforced before parsing;
`page` bounded; NaN/inf refused in money fields; IPv6 grouped by /64 for rate
limits; staff actions no longer throttled by the public booking rule; tight
buckets for coverage-check and coupon lookups; Google Routes cache rounded to
~110 m (cost abuse); uploads staff-only with a daily cap; storage-URL
validation on photo/KYC fields; OTPs and temp passwords redacted from outboxes
and logs; temp passwords from `secrets`; webhook fail-closed outside
development; DEBUG defaults off; seed script refuses to create staff accounts
on a non-local database and docs no longer publish passwords;
`backend/.gcloudignore` keeps `.env*` out of Cloud Build; deploy script refuses
`TRUST_PROXY_HEADERS=false`, a private R2 URL, a non-Meta WhatsApp provider;
`--memory 1Gi`; Vercel security headers (frame-deny, nosniff, referrer,
permissions, HSTS).

### 3.7 Notifications & WhatsApp

Fixed: bot handoff now auto-resumes and always honours pay buttons (prepaid
bookings were expiring silently); pay-link spam / stale links; deleted
customer's number could never book again; double-tap confirm error; bot
customers got two confirmations; admin ping spam; Meta outage doubled request
latency (no generic fallback after transport failure); restore not announced
on WhatsApp; reschedule template showed raw ISO/24 h times; reassignment didn't
tell the customer; plan-expiring (Meta MARKETING) now respects opt-out; society
residents get one notice per plan, not per car.

### 3.8 Frontend reliability & theme

Fixed: booking page stuck on "Loading…" when the catalogue failed; Pay button
total including cancelled/paid cars; dead Upgrade and Edit Booking buttons;
network errors shown as "not in your area" / "no jobs" / "no passes";
Thank-You page bouncing home on a blip; society OTP double verify; socket
reconnect storm; 360 px overflow (garage, captain job notes); old gold/black
theme on the booking OTP popup, captain set-password gate, contact and custom
plan modals, Razorpay checkout, auth and policy pages (root tokens moved to
v2); Title Case and 12-hour time leaks; missing cache invalidations; ~4,500
lines of dead code removed.

## 4. Remaining risks (need external action — not fixed by code)

1. **Seed staff accounts (P0 if present).** `admin@doorstepvehiclecare.in`,
   manager and captain seed accounts were created with passwords that were
   published in this public repo. If they exist in production, rotate or
   delete them now (also the captain `9876512340`).
2. **Public repository leak.** The 2026-10-06 commit `111213a` on `main`
   (pushed to the public `blussit/blussit`) contains `.dev/` logs and
   `devdata/blussit_dev.archive.gz` with ~47 phone numbers (some likely real),
   2 Gmail addresses and bcrypt hashes. Needs `git rm`, a history rewrite and
   force-push — or making the repo private. Local `main` has another unpushed
   commit `b04ad32` adding more logs — don't push it.
3. **Production configuration to set/verify before deploying:**
   - `R2_PUBLIC_BASE_URL` must be the bucket's public `r2.dev`/custom domain
     (the deploy now refuses the private `*.r2.cloudflarestorage.com` endpoint
     that `DEPLOYMENT.md` used to document).
   - `WHATSAPP_PROVIDER=meta_cloud`, `WHATSAPP_UPDATE_TEMPLATE_NAME` pointing
     to an approved 2-parameter utility template (without it every
     out-of-session notification silently drops), OTP / temp-password
     templates set and synced (CRM → Templates → Sync).
   - `RAZORPAY_WEBHOOK_SECRET` + webhook in the Razorpay dashboard
     (recommended backstop); confirm **auto-capture is ON** in Razorpay.
   - After deploy, confirm `run.googleapis.com/cpu-throttling=false` on the
     live revision.
4. **WhatsApp template approvals** for the event templates listed in
   `whatsapp_crm_service.EVENT_TEMPLATES`.
5. **Infrastructure for scale:** shared rate-limit store (limits are per
   instance), cross-instance websocket fan-out (Redis or Mongo change
   streams), Atlas sizing (M30+ for 100k customers).
6. **Dependency upgrades (recommended, not applied — test first):**
   `fastapi`→0.120.x with `starlette`≥0.49.1, `python-multipart`≥0.0.22,
   `python-jose`≥3.4.0 (or PyJWT), `axios`≥1.20.0. The body-size middleware
   mitigates the multipart DoS meanwhile.
7. **Remaining P2/P3 not done:** payment receipts for paying an
   already-confirmed booking online (NTF-6); society revenue in platform
   reports; subscription overview `distinct()` at very large scale (PERF-3);
   unbounded admin analytics breakdowns (PERF-4); count/regex scans on admin
   lists (PERF-5); account enumeration responses (P3); pre-existing
   penalised bookings lack the stored penalty for restoration on release.

## Production gate

| Gate | Status |
|---|---|
| Core flows work | ✅ traced + tested |
| Pricing correct, server-authoritative | ✅ |
| Payment verification secure | ✅ |
| Authorization correct | ✅ (three P0 takeover chains closed) |
| Critical races handled | ✅ |
| Sensitive data protected | ⚠️ code ✅ — public repo leak (risk 2) open |
| Validation / critical APIs protected | ✅ |
| Pagination / scale risks understood | ✅ (risks 5, 7) |
| UI consistent | ✅ |
| Production configuration understood | ⚠️ risk 3 must be set |
| Tests pass / build passes | ✅ 981 passed; build + prerender pass |
| No known P0 | ⚠️ risk 1 unverified in production |

**Before going live:** (1) rotate/verify the seed staff accounts; (2) set the
production configuration in risk 3; (3) deploy this branch to a preview /
staging environment and smoke-test end to end — booking (cash + online in
Razorpay test mode), captain job with photos and cash collection, plan
purchase, society enrolment, manager queue; (4) decide on the public-repo
cleanup. Then it can ship.
