# Site Attack / QA Report — 2026-10-29

Scope: whole website (public pages, customer app, captain, manager, admin, API, WebSocket) attacked and
checked for working / responsive. Part A = local full stack (writes allowed, throw-away DB). Part B = production,
read-only (GET/HEAD only, about 1-2 requests a second, no logins).

- Tree tested: `main` working tree (with the other agents' uncommitted work), built into a scratch copy.
  API: `uvicorn` single process, `RUN_MODE=all`, local Mongo, providers = log, OTP 123456. Frontend: production
  build + prerender, served by a small Vercel emulator (same `vercel.json` rewrites, redirects and headers).
- Browser: Chrome 148 headless via Playwright (the bundled Playwright browser was missing, so system Chrome was used),
  widths 390 / 768 / 1366, axe-core 4 for accessibility.
- Artifacts (scratchpad, not in repo): `/tmp/claude-1000/-home-lap-46-Documents-doorstep-vehicle-care-platform-doorstep-platform/6ba3c706-5abd-4fa3-a28e-966b3e0281fa/scratchpad/sitecheck/`
  (`shots/` screenshots, `prod/` production captures and Lighthouse JSON, `sweep_*.json`, `audit_*.json`, `click_*.json`).
- CSVs in `docs/`: `SITE_ATTACK_QA_ROUTES_2026-10-29.csv` (every frontend route + all 447 API operations + WebSocket),
  `SITE_ATTACK_QA_BACKEND_LATENCY_2026-10-29.csv` (p50/p95/max and the status each of the 8 actors got, per route),
  `SITE_ATTACK_QA_FRONTEND_AUDIT_2026-10-29.csv` (every page load: role, width, status, load/FCP/LCP/CLS, overflow, taps, axe).

## 1. Scoreboard

| Area | Tested | Working | Broken | Slow / degraded |
|---|---|---|---|---|
| Frontend route URLs (anon 25, customer 16, captain 10, manager 20, admin 37, incl. fake-ID, 404, redirects, static files) | 108 URLs x 3 widths = **324 page loads** + about a dozen wrong-role / forced-browsing checks | 324 / 324 render (no blank screen, no 5xx, no uncaught exception, no broken image, no failed font) | **0** | 1 horizontal overflow (admin, 768 px); 12 loads with layout shift above 0.1; 99 of 108 mobile loads have controls under 40 px |
| Frontend interactive paths | anonymous booking incl. map + OTP popup + duplicate guard; OTP login (wrong + right code); customer address add, vehicle add, booking detail/edit; captain job UI + live geo refusal + manager approval; manager queue; admin booking drawer + "Edit Booking (Admin)"; holiday popup + holiday slots; coupon bundle CSV wizard (good, bad, wrong type, oversize); about 440 generic button clicks across all roles | all worked | 0 dead buttons confirmed (the generic click-bot's "dead" flags were all carousel / tab / navigation false positives, checked by hand) | see section 3 |
| Backend routes | 446 route+method pairs (447 operations + WS) x 8 actors (anonymous, customer A/B, captain 1/2, manager 1/2, admin) = **3,568 calls** | authorization held on every route (see 4.1) | **1** 5xx (dev-only, FIXED) | 0 calls over 3 s; the slowest are bcrypt logins (about 415 ms) |
| WebSocket | connect, subscribe (own / foreign / junk channels), push on assign, bad token, junk frame | all correct; push arrived 117 ms after the REST change | 0 | - |
| Worker loops | start without errors; `reminder_loop` lease held, seat-reconcile / review / wash-reminder / repeat sweeps ran, template sync ran | yes | 0 | - |
| Load test | 18 endpoints at 50 and 200 concurrent | no 5xx, no leaks | - | single instance saturates around 35-250 rps depending on endpoint (section 5) |
| Production read-only | about 35 frontend URLs, 18 API GETs, CORS, TLS, headers, 3 Lighthouse runs | all 200 and fast (TTFB 0.07-0.6 s) | 1 live 404 call from the home page | mobile Lighthouse performance 37 / 55 / 49 |

Totals: **24** findings (`F-01`..`F-24`, section 2). **1 fixed**, 23 reported.

## 2. Every problem found

Severity: High = users or money affected now; Medium = real defect or risk worth scheduling; Low = polish / hardening; Info = note.
"Role" is who hit it. Screenshots are in the scratchpad `shots/` folder named below.

| ID | Where | Role | What happened | Evidence | Severity | Status |
|---|---|---|---|---|---|---|
| F-01 | Production: home page calls `GET https://api.blussit.com/api/v1/public/contact-card/offer` | anonymous | The live frontend asks for an endpoint the live API does not have: **404 on every home visit** (console error; Lighthouse "errors in console"). Live sitemap also lists `/contact-card` and `/shipping-policy`, which are **not** in `main`'s `src/seo/pages.json` (they come from `blussit-v2`), so the live frontend is ahead of the live API and ahead of `main`. | `prod/lh_home.json`; `curl .../public/contact-card/offer` = 404 `{"detail":"Not Found"}` | Medium | REPORTED (deploy API with the frontend, or gate the call behind the feature flag) |
| F-02 | Production home, `/book`, `/services` on mobile | anonymous | Lighthouse mobile (simulated Moto G, slow 4G): performance **37 / 55 / 49**, LCP 9.6 / 4.7 / 8.1 s (simulated), TBT 1.5-1.9 s, 1.6-2.3 MB. Observed LCP parts on home: load 0.83 s + **render delay 1.24 s** because the hero image carries `animate-hero-zoom`. Heavy third parties: `gtag/js` fetched **twice** (2 x 194 KB), GTM 116 KB, `fbevents.js` 112 KB; about 480 KB unused JS; three YouTube `maxresdefault.jpg` thumbnails (115-148 KB each) on the home page. Desktop and real-network loads are fast (TTFB 0.2-0.6 s, bundle 151 KB brotli). | `prod/lh_home.json`, `lh_book.json`, `lh_services.json` | Medium | REPORTED (drop the hero zoom animation from the LCP image, defer pixel/GA until idle, use `hqdefault` thumbnails) |
| F-03 | Production tags | anonymous | gtag.js loads directly **and** GTM (`GTM-NP986HT3`) loads another GA4 `gtag/js` for the same `G-HLQLRBBC24`: possible double-counted page views. | Lighthouse network list (two `gtag/js?id=G-HLQLRBBC24`) | Medium | REPORTED (check in GA DebugView; keep one loader) |
| F-04 | `vercel.json` CSP | all | CSP is **Report-Only**, so nothing is enforced (clickjacking is covered by `X-Frame-Options: DENY`). Once enforced it would block GA: `connect-src` has `*.analytics.google.com` but the call goes to the apex `https://analytics.google.com/g/collect` (Lighthouse reported the violation). `script-src` keeps `'unsafe-inline'`. | `prod/lh_home.json` inspector-issues | Medium | REPORTED |
| F-05 | `POST /api/v1/auth/login` throttle | anonymous attacker, any staff | About 5 wrong passwords for one identifier lock it for 30 minutes **even for the right password** (`429 LOGIN_THROTTLED "try again in 27 minutes"`). Anyone who knows a manager's email can keep them out of the console from anywhere. (Observed when my load test used a wrong password for `manager2`; I cleared the counter in my own DB.) | `sweep` + `logs/load50.log` (1,692 of 1,745 requests answered 429) | Medium | REPORTED (key the lock on identifier + IP, or add a captcha / delay instead of a hard lock) |
| F-06 | Admin Vehicle Types, Services, Subscription Plans pages | admin | Icon-only controls with no accessible name (my probe found no text, `aria-label` or `title`) fire immediately with **no confirmation**. My click-through bot hit them and silently **deactivated Hatchback, Sedan, SUV, 4 car services and 3 plans** for the whole public site (the booking quote API then answered "Pick a valid vehicle type" and the booking page lost those options). One stray click = catalogue outage. I restored them through the API. | `click_admin_1366.json` (rows with `btn: ''`, 26 API calls each); DB `is_active:false` | Medium | REPORTED (label + confirm dialog + undo toast) |
| F-07 | `POST /whatsapp/crm/templates`, `/templates/bootstrap` with the log provider | admin (dev) | `AttributeError: 'LogWhatsAppProvider' object has no attribute 'create_template'` = **500** instead of a clean 400. Production uses the Cloud provider, so dev/test only. | `logs/api.log`; `sweep_write.json` | Low | **FIXED** (base `WhatsAppProvider.create_template` returns `{"error": ...}` like the Cloud provider; test `tests/test_site_attack_log_provider.py`, 2 passed) |
| F-08 | `POST /addresses`, centers, services, plans, categories, vehicle types | customer, admin | Weak validation: address pincode accepts `aaaa`, `<script>`, `999999` (only length >= 4 checked); one-character names (`a`) accepted for center, vehicle type, service, plan, category. They then show up in customer pickers ("A" in the car list, plan "A" in /app/subscriptions, address "a, a, a - aaaa"). Output is escaped, so no XSS, but junk data reaches users and may break pincode-based center lookup. | `shots/x1.png` (car list shows "A"), `/app/subscriptions` text | Low-Medium | REPORTED (6-digit pincode pattern; min name length) |
| F-09 | API: every route, including `/api/health` and `/api/ready` (also production) | monitors | `HEAD` returns **405** (CORS advertises HEAD). Any uptime monitor that uses HEAD will show the site down. | `curl -I https://api.blussit.com/api/health` = 405 | Low-Medium | REPORTED (route GET+HEAD for the two health endpoints) |
| F-10 | Manager Dashboard `/manager`, `/manager/kpi` | manager | Layout shift **CLS 0.24 (390), 0.50 (768), 0.26-0.39 (1366)**: the data section jumps about 286 px up when data arrives (skeleton height does not match). | `shots/v_mgr_cls_390.png`, `v_mgr_cls_768.png`; shift sources in `shot.py` output | Medium | REPORTED |
| F-11 | `/thank-you` | anonymous | CLS 0.44 / 0.35 / 0.25 (390 / 768 / 1366): the hero block shifts at about 720 ms. | `shots/v_thanks_cls_390.png` | Low-Medium | REPORTED |
| F-12 | Mobile (390 px), 99 of 108 page loads | all | Tap targets under 40 px: staff header "Open menu" 32x32, bell 36x36, "Find a customer" 144x34; footer social icons 36x36; floating WhatsApp 36x36; logo link 183x24; customer-app header logo 96x13; breadcrumb "Home" 36x20; "Maybe Later" 310x20 (offer popup); phone link 358x20. The 13-20 px ones also miss WCAG 2.5.8 (24 px). | `frontend_routes_audit.csv` column `small_taps` | Low-Medium | REPORTED |
| F-13 | Accessibility (axe, 1366) | all | `color-contrast` on 175 elements across 58 pages (also seen by Lighthouse on prod cards); `button-name` **critical** on `/app/addresses` (icon-only edit/delete buttons); `aria-prohibited-attr` and `scrollable-region-focusable` on `/`, `/about`, `/thank-you`; no `<main>` on `/` and `/thank-you` (Lighthouse `landmark-one-main` on prod); `aria-hidden-focus` on the floating contact block; `/manager/new-booking` and `/manager/log-job` have two `h1`; every signed-in page and `/login` keep the generic `<title>Blussit</title>`. | `frontend_routes_audit.csv` column `axe_violations` | Low | REPORTED |
| F-14 | Admin `/admin` at 768 px | admin | 23 px horizontal overflow (a "Target 40% +10%" chip); the sidebar stays open at tablet width leaving about 520 px of content; header search placeholder cut to "F" (768) and "Find custom" (390). | `shots/admin_admin_768.png`, `v_admin_390.png` | Low | REPORTED |
| F-15 | Capacity, single instance (section 5) | all | `manager-dashboard` costs about 25 ms CPU: 33 rps, p50 1.7 s at 50 concurrent, 6.2 s at 200. Slots endpoint (public, polled): about 135 rps. **No load shedding**: nothing like `SERVER_BUSY`/503 exists in the code (grep), so at 200 concurrent requests just queue (p95 up to 7.4 s, max 17 s). Cloud Run concurrency of 250 per instance will behave the same before it scales out. | `raw_results.txt`, `load200.log` | Medium | REPORTED (lower Cloud Run concurrency to about 40-80 so the autoscaler reacts earlier; cache or slim the manager dashboard) |
| F-16 | `POST /auth/otp/verify` | anonymous | A wrong code answers HTTP 200 with `"success": true, "message": "OTP verification failed", "data": {"verified": false}`. `success:true` on a failure misleads clients, logs and monitors. | `curl` | Low | REPORTED |
| F-17 | Unknown API path | anonymous | `/api/v1/nope` returns bare `{"detail":"Not Found"}`, not the app's error envelope. | `curl` | Low | REPORTED |
| F-18 | WebSocket junk frame | any signed-in | A non-JSON frame logs `ERROR WebSocket connection error` with a full traceback, then closes. No crash, but a log-noise lever. | `logs/api.log` | Low | REPORTED |
| F-19 | Cross-center manager access | manager | A manager from another center gets 403 on some routes and 404 on others for the same booking (`/bookings/{id}` 403, `/adjustments` 404): a small existence oracle. No data leaks. | `sweep_get.json` | Low | REPORTED |
| F-20 | `/index.html` and `/_pages/*.html` on production | anonymous | Duplicate copies of "/" and every prerendered page answer 200 (canonical is set, robots.txt blocks `/_pages/`, but not `/index.html`). | `prod/` fetches | Low | REPORTED |
| F-21 | `/app/addresses` | customer | City repeated in the address line: "New Palasia, Indore, Indore, Madhya Pradesh". | `shots/c_addr3.png` | Low | REPORTED |
| F-22 | Holiday popup title | anonymous | A title containing HTML renders as text (safe) but the title-case helper turns `</b>` into `</B>`. Cosmetic, only for odd titles. | `shots/hol_land.png` | Low | REPORTED |
| F-23 | Slow public edges seen once | anonymous | Production `slots` for a not-yet-requested date took TTFB 1.2 s once (next 3 calls 0.11 s): cold cache / cold instance. Local OTP "Send Code" 1.6 s. | `prod/slots_1.json` timing | Info | REPORTED |
| F-24 | Local-only noise | anonymous | Google sign-in (`GSI_LOGGER: origin not allowed`) and "Couldn't load preload assets" on `/login` at `127.0.0.1` (the client ID is authorised only for the real domain); Google Maps deprecation warnings on `/book` and add-address (`places.Autocomplete` unavailable to new customers, `addListener` on `gmp-advanced-marker`); Routes API fell back to haversine twice. | console logs | Info | REPORTED |


## 3. Frontend responsiveness per width

108 URLs per width, as the correct role. Wrong-role and anonymous forced browsing always redirected: customer/manager/captain at `/admin` -> `/`; anonymous at `/admin`, `/manager`, `/app`, `/captain/jobs`, `/app/wallet` -> `/login`; `/login?next=https://evil.example&returnTo=//evil.example` stays on `/login` (the code has no `next` handling).

| Width | Pages | Blank / 5xx / page error | Horizontal scroll | Layout shift > 0.1 | Median load / FCP / LCP (ms, per role) | Notes |
|---|---|---|---|---|---|---|
| 390 | 108 | 0 | 0 | 5 (manager dashboard, KPI, new-booking, log-job, thank-you) | load 132-236, FCP 136-288, LCP 236-776; p95 LCP up to 2.7 s (customer `/app`, manager `/manager`, measured while 5 browsers ran at once) | 99 pages with controls under 40 px (F-12); staff header search truncated |
| 768 | 108 | 0 | 1 (`/admin`, +23 px) | 3 | load 108-178, FCP 132-224, LCP 284-684 | admin sidebar never collapses (F-14) |
| 1366 | 108 | 0 | 0 | 4 | load 104-176, FCP 132-212, LCP 320-612 | `/admin/service-zones` waits 4.6 s for Google Maps tiles before the network goes idle (content is visible earlier) |

Across all widths: longest load event 953 ms, no failed font, no broken image, no hydration-mismatch message on any prerendered page, 0 uncaught exceptions. Console errors were only expected 404/403 from fake IDs or foreign-center bookings, plus the local Google items in F-24. No 24-hour time strings anywhere (all 108 routes scanned: slots read "10:00 AM – 1:00 PM"); no `undefined` / `null` / `NaN` / `Invalid Date` text.

Interactive checks (what worked):
- **Booking, anonymous (390 px):** car type -> service -> day/slot -> Continue -> Google map search + pin -> name/phone -> Confirm -> OTP popup (6 boxes, Resend in 58 s, SMS fallback timer) -> code 123456 -> booking created; booking the same time again shows "You already have booking BK0006 scheduled around this time" with a login link. Missing pin shows "Drop the pin on the service address."
- **Login:** short phone shows "Enter a valid 10-digit mobile number."; wrong code shows "Invalid or expired code."; right code logs in.
- **Customer app:** dashboard, bookings (Details / Edit / Cancel / Pay), wallet, addresses (add with map), garage (add vehicle), subscriptions, offers, support, settings, profile all work.
- **Captain (live API):** a far-away "arrived" is refused with a Title-Case reason (`Location Jumped 664.8 Km In 2s`, `You Must Be At The Customer's Location To Mark Arrived`), "ask manager" works, manager approval lets it through; admin drawer shows the flagged location verdict and a pending "Idle After Arrival" alert. The captain UI shows "Approved By Manager" on the before-photo step.
- **Holiday:** an admin holiday shows the popup on the landing page and a "Holiday Break" banner with every slot marked Holiday on `/book`.
- **Admin:** booking drawer has Pay Back / Give Discount / Edit Booking (Admin) / Add Service / Cancel / Edit Details / Delete; the admin edit modal opens with its five sections and requires a reason (5+ characters).
- **Coupon bundle wizard:** 2,000-row CSV reviewed in 2.5 s; formula/HTML codes (`=CMD|...`, `<script>`) are rejected as "Invalid Characters"; PNG renamed as CSV and a 300,000-row file ("too large - limit is 2 MB") are refused cleanly.
- **Not confirmed dead:** the click-bot's 40-odd "dead" flags (date tabs, carousels, "Log A Done Job", "New Plan") were all false positives (state-only buttons or slower lazy navigation: "Log A Done Job" took 2.2 s to navigate while the machine was busy).

## 4. Backend

### 4.1 Authorization and behaviour (all routes, all roles)

Matrices are in `SITE_ATTACK_QA_BACKEND_LATENCY_2026-10-29.csv`. Result: anonymous gets 401 on every protected route; only the intended public routes answered 2xx for anonymous (health, ready, catalogue, slots, holidays, service lookup, plans, FAQs, reviews, society info, booking quote/quick/hold, contact, coverage leads, enquiries, WhatsApp webhook); customer B never read customer A's booking, payment state, charges or reviews (403/404); captains see only their jobs; manager 1 could not read or write center 2 data (and vice versa); admin-only routes (users, finance, holidays, coupons, campaigns, CRM, audit, settings, pricing) returned 403 for all four lower roles on every method. No wrong-role 2xx found.

### 4.2 Latency (functional sweep, 3,568 calls, 8 actors, local single process)

Median route p50 **26 ms**, 95th percentile of route p95 **104 ms**, 0 calls over 3 s. Slowest (p95):

| Route | p50 ms | p95 ms | Note |
|---|---|---|---|
| POST `/auth/google` (invalid token) | 424 | 686 | calls Google |
| GET `/whatsapp/crm/templates/catalogue` | 23 | 592 | first call builds the catalogue |
| POST `/auth/login`, `/auth/change-password` | 410-414 | 412-417 | bcrypt by design |
| GET `/crm/customers/{id}` | 27 | 173 | |
| GET `/analytics/manager-summary/{center}` | 24 | 159 | |
| GET `/whatsapp/crm/delivery-health` | 33 | 157 | |
| GET `/api/ready` | 122 | 148 | database + index check |
| POST `/whatsapp/campaigns/audience/{count,selection}` | 23-32 | 139 | |
| GET `/payments/collections/admin` | 25 | 123 | |
| GET `/analytics/manager-dashboard/{center}` | 32 | 123 | grows with data (see 5) |

### 4.3 Logs and workers

Distinct errors in the API log across the whole run: (1) the F-07 `create_template` 500 (fixed); (2) `WebSocket connection error` from a junk frame (F-18). Warnings: DEV TOOLS OTP lines, two "Routes API raised - falling back to haversine". No other Traceback, no unhandled exception from any of the 3,568 calls or from about 440 UI clicks. Worker loops: `locks` shows `reminder_loop` held plus `seat_reconcile_at`, `review_request_sweep_at`, `wash_reminder_sweep_at`, `repeat_sweep_at`, and `whatsapp_template_sync` all stamped; boot tasks (customer locations, vehicle-type tombstones, booking money fields, wallet migration, address snapshots, password gates, e-mail lower-casing) completed.

### 4.4 WebSocket

`/api/v1/ws?token=`: three sockets connected in 39 ms total. Subscribe results: own booking OK; another customer's booking denied; customer on `center-bookings` denied; manager on own center OK, other center denied; public `slots:<center>:<date>` OK, `slots:bad` denied. A captain assignment produced `{"type":"changed"}` to the customer and the manager (117 ms). A garbage token is closed with 4401. Not tested: the 20-socket cap, the 64 KB frame cap (covered by existing tests per the repo notes).

## 5. Load test results

Single `uvicorn` process (RUN_MODE=all, access log on, 8-core laptop, local Mongo, client on the same machine). Raw keep-alive client (the first httpx-based run was client-bound and is not used for the numbers below). 8 s per case.

| Endpoint | 50 conc. rps | p50 / p95 / p99 ms | 200 conc. rps | p50 / p95 / p99 ms | Errors |
|---|---|---|---|---|---|
| GET `/api/health` | 844 | 47 / 152 / 163 | 720 | 239 / 380 / 402 | 0 |
| GET `/vehicle-types` | 269 | 189 / 288 / 397 | 280 | 721 / 1015 / 1102 | 0 |
| GET `/services` | 228 | 218 / 306 / 363 | 191 | 1071 / 1554 / 1624 | 0 |
| GET `/service-centers/{id}/slots` | 140 | 353 / 473 / 523 | 135 | 1604 / 1885 / 2352 | 0 |
| GET `/public/holidays` | 543 | 82 / 184 / 218 | 487 | 386 / 532 / 546 | 0 |
| GET `/homepage-config` | 353 | 135 / 232 / 258 | 315 | 648 / 910 / 953 | 0 |
| POST `/bookings/quote` | 158 | 309 / 446 / 485 | 162 | 1279 / 1604 / 3498 | 0 |
| GET `/bookings/my` (customer) | 102 | 493 / 619 / 674 | 100 | 2273 / 2748 / 3666 | 0 |
| GET `/bookings/center/{id}` (manager, 50 rows) | 74 | 628 / 1231 / 1348 | 99 | 2370 / 2777 / 3292 | 0 |
| GET `/analytics/dashboard` (admin) | 235 | 184 / 381 / 472 | 250 | 819 / 1059 / 1152 | 0 |
| GET `/analytics/manager-dashboard/{id}` | **33** | 1710 / 2451 / 2813 | **49** | 6202 / 7440 / 9986 | 0 |
| GET `/crm/customers/typeahead` | 200 | 245 / 352 / 399 | 173 | 1187 / 1750 / 1881 | 0 |
| GET `/users?search=` (admin) | 131 | 388 / 490 / 515 | 139 | 1474 / 1915 / 1990 | 0 |
| POST `/auth/login` (valid, httpx run) | 20 | 2803 / 3254 / 3330 | 29 | 11422 / 12031 / 12447 | 0 |
| POST `/auth/login` (wrong password, httpx run) | - | - | - | - | 1,692 of 1,745 answered 429 (throttle works; see F-05) |

Reading: throughput is flat from 50 to 200 concurrent users, so latency is pure queueing on one process; there is no error, 503 or crash. Resources stayed flat: file descriptors 47, Mongo connections 40 (pool 30), RSS 232 MB, 100 threads, nothing grew after the runs (no leak). Slow-client test: 150 half-open sockets and a slow request body did not slow a health call (32 ms), but uvicorn holds them forever (no header timeout; Cloud Run's front end normally closes them). Large body: 3 MB JSON gets 413 on `/contact` and `/auth/login`.

`SERVER_BUSY` was asked for: it does not exist on `main` (only `STORAGE_UNAVAILABLE`, throttling 429 and the readiness 503). See F-15.

## 6. Security header table and attacker pass

| Item | Frontend (Vercel, live and `vercel.json`) | API (live) |
|---|---|---|
| X-Frame-Options | DENY | DENY |
| X-Content-Type-Options | nosniff | nosniff |
| Referrer-Policy | strict-origin-when-cross-origin | strict-origin-when-cross-origin |
| Permissions-Policy | camera/geolocation self, payment Razorpay | all denied |
| HSTS | max-age=63072000 (no includeSubDomains / preload) | max-age=31536000; includeSubDomains |
| CSP | **Report-Only** only (F-04); `frame-ancestors 'none'` is only reported | none (JSON API) |
| Cache | `/assets/*` immutable 1 year (HIT); HTML max-age=0 must-revalidate (second hit = HIT) | catalogue `public, max-age=30, stale-while-revalidate=120` |
| CORS | static files `access-control-allow-origin: *` (Vercel default) | echoes only listed origins (`https://blussit.com`, `https://www.blussit.com` OK; `https://evil.example` preflight = 400; `Origin: null` gets no allow-origin) |
| Cookies | the app sets none (a Facebook `fr` cookie is the only one seen) | none |

Other attacker results:
- **XSS:** `"><img src=x onerror=window.__xss=1><script>...` stored in name, vehicle brand/model, complaint subject/description, contact and coverage forms; rendered on 20 customer / manager / admin pages: payload shown as text, `window.__xss` never set, no `<img src=x>` element, no dialog. `dangerouslySetInnerHTML` is used only for JSON-LD with `<` escaped.
- **Open redirects:** no `next` / `returnTo` handling in the frontend (confirmed by code search and by loading `/login?next=https://evil.example`). Payment callbacks are server side.
- **Clickjacking:** `X-Frame-Options: DENY` live; CSP only reports.
- **Source maps:** none in the build; production `/assets/index-BWk6bE0U.js.map` = 403.
- **Secrets in JS:** scanned the local build and the live bundle for Google (`AIza`), Razorpay (`rzp_`), Stripe-style, Mongo, JWT, GitHub, AWS, Meta (`EAA`), private-key and password literals: **none**.
- **Token storage:** `dvc_access_token` and `dvc_refresh_token` in `localStorage` (readable by any script; no XSS found, but a stored `httpOnly` refresh cookie would shrink the blast radius). Also `captainLanguage`, a device id, and session keys.
- **Method tampering:** DELETE/PUT/PATCH/TRACE on a GET route = 405; `X-HTTP-Method-Override: DELETE` ignored (401).
- **Path traversal:** `/uploads/../.env`, encoded variants, `/uploads//etc/passwd`, `/private_uploads/`, `/api/v1/uploads/document/../../.env` = 404 / 401.
- **NoSQL / malformed JSON:** `{"identifier":{"$ne":null}}` = 422; bad JSON = 422.
- **Forced browsing:** see section 3, all redirect.
- **Sitemap / robots:** live sitemap has 16 URLs (all 200); `robots.txt` blocks app areas and `/_pages/`; `/login`, `/admin`, `/spa.html`, 404s carry `noindex`; canonical present on every indexable page; JSON-LD on all public pages.

## 7. Production read-only results (GET / HEAD only, about 100 requests, at most 1.5 per second, plus 3 Lighthouse page loads)

Hosts: `https://blussit.com` (Vercel, Mumbai edge `bom1`), `https://api.blussit.com` (Google Cloud Run, "Google Frontend"). No 429 or 5xx at any point.

| Check | Result |
|---|---|
| TLS | frontend Let's Encrypt, valid 2026-10-25 to **2027-01-23**; API Google Trust Services, valid to **2026-12-11** |
| Redirects | `http://blussit.com` -> 308 https; `http://www` -> https://www; `https://www.blussit.com/services` -> 308 `blussit.com/services`; `/about` -> `/` |
| Pages (16 sitemap URLs + login, forgot-password, robots, 404, favicon, banner) | all 200 (404 page returns 404); TTFB 0.08-0.62 s, total 0.08-0.80 s; sizes 39-173 KB |
| Edge cache | `/`, `/book`, `/login`, assets, images: HIT; first request of prerendered pages (`/services`, policies): MISS then HIT on repeat (age 29 s) |
| Static assets | `index-BWk6bE0U.js` 151 KB brotli, immutable, HIT, TTFB 0.09 s; banner 155 KB |
| Structured data / SEO | titles and canonicals correct on every public page; 2-4 JSON-LD blocks on content pages; one `h1` each; `/book` has none (app shell) |
| API GETs (health, ready, services, vehicle-types, categories, holidays, lookup x2, booking-policy, FAQs, plans, testimonials, homepage-config, maps/google config, combo-offers, reviews) | all 200, TTFB 0.07-0.15 s (`/reviews` 0.42 s) |
| Slots next 4 days | all 200; 0.11-0.14 s (one cold 1.2 s, F-23); today shows every slot full except 4-7 PM (cut-off logic) |
| CORS preflight from `https://blussit.com` | 200 with allow-origin = the site, credentials true; foreign origin = 400 |
| `/api/docs`, `/api/openapi.json` | 404 (good) |
| HEAD on API | 405 (F-09) |
| Lighthouse (mobile, simulated) | home 37 / a11y 91 / best-practices 73 / SEO 100; `/book` 55 / 96 / 77 / 100; `/services` 49 / 96 / 77 / 100 (F-02, F-03) |
| Consistency with local build | live bundle and local bundle differ (live is built from `blussit-v2`, local from `main`); live has `/contact-card` + `/shipping-policy` and the contact-card API call that 404s (F-01). Prerendered HTML structure matches (same sections, same JSON-LD kinds). |

Nothing on production is slow by the founder's thresholds (TTFB over 1 s, total over 3 s) except the single cold slots call and mobile Lighthouse numbers (simulated throttling).

## 8. What I could not test, and why

- **Real payments:** Razorpay checkout modal, payment links, autopay (Razorpay unconfigured locally; production must not be touched).
- **Real messaging:** WhatsApp / MSG91 OTP delivery and templates, Meta webhooks, campaign sends (provider = log; no Meta account locally). CRM inbox and campaign wizard were only rendered and API-checked.
- **Google sign-in** (origin not authorised locally), **push notifications** ("Enable Alerts"), **camera / photo upload** on captain steps, **real GPS** (geolocation was simulated only through the API).
- **Real devices / Safari:** only desktop Chrome 148 with phone and tablet viewports and touch emulation.
- **Manager flows end to end through the UI** (Give Discount, Reschedule, KYC review, sell plan, custom plans, society planner): rendered, clicked through, and API-tested for authorization and validation, but not walked step by step as a human would. The same applies to society pass / schedule flows and the admin WhatsApp campaign wizard.
- **Multi-instance behaviour:** worker lease with two workers, Cloud Run concurrency / cold starts, Atlas latency and connection caps, rate limits behind the real proxy (local `RATE_LIMIT_ENABLED=false`; only the login throttle is always on).
- **Production authenticated or write paths, load, fuzzing:** forbidden by the brief; production was GET/HEAD only.
- **Vercel's own rewrite engine and CDN:** the local run used an emulator of `vercel.json`; the live site was checked with plain GETs.
- **WebSocket 20-socket and 64 KB caps** (existing unit tests cover them; not re-driven).
- **Third-party (GA, Meta pixel) event correctness** (F-03 is inferred from the network list, not from GA DebugView).

## 9. Fix made

| File | Change | Test |
|---|---|---|
| `backend/app/services/whatsapp_service.py` | Added `WhatsAppProvider.create_template()` default returning `{"error": "WhatsApp is in log-only mode ..."}` so the log provider answers a clean 400 where it used to raise `AttributeError` (500). The Cloud provider's own method is unchanged. | `backend/tests/test_site_attack_log_provider.py` (2 tests, passed on a throw-away local DB `sitecheck_test`) |

No other source files were edited. Nothing was committed, stashed, reset or pushed.
