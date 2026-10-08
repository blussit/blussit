# BLUSSIT PRE-PRODUCTION AUDIT REPORT — 2026-10-07

Branch `blussit-v2` (working tree, uncommitted). First pass: **no code was
changed.** Everything below was found by tracing code and by running it —
never against production.

## How this was tested

| Method | What |
|---|---|
| Baseline test suite | 983 backend tests on a local replica-set Mongo: **983 passed, 0 failed** (7 m 47 s). Frontend `tsc` 0 errors; `npm run build` + prerender pass. |
| 7 parallel audits | payments & pricing · bookings/slots/state machine · auth/authorization/API validation · society & plans · captain/manager/admin · notifications/failures/deploy/hardcoding · frontend reliability. Each finding had to be proven by a **reproduction test** (local Mongo, Razorpay/WhatsApp/Google stubbed) or an exact code trace. ~120 reproduction tests written; they live in the session scratchpad, not the repo. |
| Independent re-run | The coordinator re-ran the most serious reproductions (BOOK-01/02/03, SLOT-01, PAY-01/02/04, PASS-1/2, CAP-02, AUTH-01, AUTH-05 and the whole security set) — every one reproduced. |
| Live end-to-end | Real backend + site on a throwaway local database, driven in Chrome at 390 px and 1366 px: guest booking with OTP → confirmation page → manager assigns → captain on a phone: start trip, wrong/right code, before photo, after photo, complete → money and messages checked in the database → customer app. Every manager (13), captain (4) and customer (12) page swept at both widths for crashes, 5xx, `undefined`/`NaN`, overflow. |

Evidence labels: **REPRO** = reproduced by a test · **E2E** = seen in the live
browser run · **RE-RUN** = reproduced again independently by the coordinator ·
**TRACE** = exact code path, not executed.

---

## 1. Overall Status

**NOT READY.**

The core of the system is solid — slot capacity under 100 concurrent buyers,
payment verification, signature checks, role isolation, and the booking state
machine all held up under real concurrency. But there are **3 P0 items** (one
code, two external) and **~25 P1 items** in payments, capacity bookkeeping,
staff accounts and the customer-facing price, and several production settings
that must be confirmed. Details and the fix order are in sections 2–5 and 25.

| | P0 | P1 | P2 | P3 |
|---|---|---|---|---|
| Count | 3 | 25 | ~45 | ~60 |

---

## 2. P0 Issues — production blockers

| ID | Issue | Evidence | Fix |
|---|---|---|---|
| **P0-1 / DEP-01** | **Customer personal data is publicly downloadable.** The public GitHub repo `blussit/blussit` (commit `111213a` on `main`) contains `devdata/blussit_dev.archive.gz` (46 phone numbers, 2 Gmail addresses, 6 bcrypt hashes, bookings, OTP requests, payment orders, WhatsApp outbox) and 7 MB of `.dev/` logs. The raw file URL returns HTTP 200. Local `main` has an unpushed commit `b04ad32` adding more logs. | REPRO (HTTP 200 on the raw URL) | Make the repo private **now**; purge with `git filter-repo` and force-push; never push `b04ad32`; treat dumped accounts as exposed. (Open since the 10-06 audit.) |
| **P0-2 / AUTH-01** | **Staff account takeover through "Forgot Password".** Password reset by OTP works for every role and sends the code to whatever phone is stored on the account — and staff phones are typed by an admin/manager and never verified. The seed script gives the admin, manager and captain the placeholder numbers **9999999999 / 9999911111 / 9999922222**. Whoever holds one of those SIMs can reset the admin password and log in as admin. Rotating the seed passwords does **not** help. | REPRO, RE-RUN (forgot-password → reset → `login as admin -> 200`) | **Production check today:** look for users with those phones or `@doorstepvehiclecare.in` seed emails; delete them or change their phone. **Code:** refuse OTP/widget password reset unless `phone_verified` is true; staff resets admin-initiated only; verify a staff phone at creation/first login. |
| **P0-3 / DEP-02** | **Razorpay mode is not enforced for production.** If production runs on Razorpay **test** keys, anyone can "pay" with test cards and bookings/plans are marked paid (signatures verify). The app only logs an error; the deploy script treats keys as optional. | TRACE (`main.py:955-960`, `deploy-gcp.sh`); `.env.production` was not read | Confirm `RAZORPAY_KEY_ID` starts with `rzp_live_`; make deploy fail and the app refuse to start in production with non-live keys. Confirm auto-capture is ON (PAY-09). |

---

## 3. P1 Issues — critical

### Money and pricing

| ID | Issue | Evidence | Fix (root cause) |
|---|---|---|---|
| PAY-01 | A customer can **buy an XUV-7 pass at the hatchback price**: create the order with a hatchback car, retype the car to XUV-7, then pay. The pass is activated for the car's *current* type (₹1,396 paid, ₹1,556 pass). | REPRO over HTTP, RE-RUN | Freeze the priced vehicle type into the order; park the payment if the car changed; block retyping a car named by an open order. |
| PAY-02 | The **payment-window expiry sweep cancels a booking that was just paid** (payment lands between its last check and the cancel). Customer paid, gets "cancelled", slot released, manual refund needed. | REPRO, RE-RUN | Expiry cancel must be guarded on `status=awaiting_payment` **and** `payment_status=pending` in the same write. |
| PAY-03 | Captain QR opened on the ₹0 **plan-covered car of a mixed visit** creates a link bound to that car: the money is parked, the paying car stays unpaid, the captain is told to collect again → customer can pay twice. | REPRO | Bind the link to the cars it charges (always store `booking_ids`). |
| PAY-04 | Captain taps Complete while the customer pays the QR online: the captain's wallet is **debited ₹309 as if he kept the cash** instead of credited ₹40. | REPRO, RE-RUN (wallet 500 → 191) | Guard the completion write on the payment fields it read; settle from the written document. |
| PASS-1 | A manager's **cash plan sale applied several times from simultaneous submits** (3 of 4, then 4 of 4 on re-run) → that many active passes and cash rows; cash counted multiple times in reports. | REPRO, RE-RUN | Insert-first claim per customer + vehicle/type + service around pass creation (also fixes PASS-4, SOC-5). |
| SOC-2 | After the admin raises the **society rate card**, existing custom combos in that society still quote and charge the old price (₹729 vs ₹1,349). | REPRO | Version auto-plans by rate card; match on version. |
| FE-03 | When the live price quote fails (429/5xx/network) the Book button **keeps the local estimate** (e.g. "Book & Pay ₹199") and stays tappable; the booking is created at the server price (₹399) and the cash confirmation page shows no amount. | TRACE (frontend) | Disable Book until the live quote arrives; label from the live quote only; show total + payment method on the confirmation page. |
| FE-04 | After a slow payment confirmation the **Pay button becomes active again** while the screen says "Confirming your payment" — a second payment is accepted and parked for refund. | TRACE | Hide Pay while confirming/under review; backend refuses a new order while a captured, unsettled one exists. |

### Booking capacity (overselling)

| ID | Issue | Evidence | Fix |
|---|---|---|---|
| SLOT-01 | **Changing slot length or working hours** (per center, or the global "Slot Length" on the Pricing page) silently drops every per-slot limit for already-booked days: 6 more sold into a full slot; new keys default to 999 seats. | REPRO, RE-RUN | Refuse the change while future bookings exist (or migrate keys + recount); unknown slot keys fail closed. |
| BOOK-03 | **Restoring a deleted booking into a full slot over-books** (restore ignores the failed seat reservation); a later cancel frees a seat it never held → slot resold. | REPRO, RE-RUN | Store seat ownership on the booking; restore takes the seat in the same transaction or is refused. |
| BOOK-01 | A booking date sent with a timezone (`2026-10-10T00:00:00+05:30`) is stored as the previous day; cancelling frees the **previous day's** seat and the booking shows on the wrong day in the manager queue. The site sends `YYYY-MM-DD`, but the API accepts both. | REPRO, RE-RUN | Accept plain dates only; normalise to IST; one-off repair + periodic counter recount. |
| BOOK-02 | Admin recycle-bin delete racing a customer cancel releases the seat twice (15/15 runs). | REPRO, RE-RUN | Same seat-ownership fix as BOOK-03. |

### Accounts and access

| ID | Issue | Evidence | Fix |
|---|---|---|---|
| AUTH-05 | **A staff member who messages the business WhatsApp from their own phone loses their password.** The bot treats it as a customer's first phone proof, wipes the "unproven" password and logs them out (manager login 200 → 401) and links the chat to the staff account as a customer. | REPRO, RE-RUN | The bot must never adopt or "prove" a staff account; route staff numbers to the CRM only. |
| AZ-01 | **Any manager can read any customer's saved addresses (with map pins)** platform-wide: find the id by phone search, then `GET /addresses/customer/{id}`. | REPRO, RE-RUN | Apply the "customer known to my center" rule used by the 360 view; same for `/subscriptions/customer/{id}`. |
| AUTH-02 | MSG91 login check accepts if **any** candidate number matches — including the unverified payload of the token itself — even when MSG91's answer names a different number. Exploitable if MSG91 validates by session rather than exact token. | REPRO (unit level) | Trust only the identifier in MSG91's response; fail on mismatch. |
| CAP-01 | A captain **moved to another center or demoted keeps his assigned jobs** and can still work them; the old center's manager can no longer suspend or track him. | REPRO | On center/role change: refuse while on a job, release assigned jobs; captain steps re-check the booking's center. |
| CAP-02 | The **4-digit arrival code has no attempt limit** (60 wrong then right → accepted), GPS is optional, and the same photo URL can be both before and after photo → a captain can "complete" a plan-covered job without going and be paid ₹40. | REPRO, RE-RUN | Lock after ~5 wrong codes + alert the manager; require GPS; bind uploads to uploader/booking; flag zero-minute jobs. |

### Customer-facing and operations

| ID | Issue | Evidence | Fix |
|---|---|---|---|
| FE-02 | A returning visitor whose saved login expired (7+ days, or logged out elsewhere) is **redirected from the homepage, /book and service pages to /login** — e.g. straight from a Meta ad. Happens once per visitor. | **E2E** (`/`, `/book`, `/services/jet-wash` → `/login`) | Silent `/auth/me` on boot: clear tokens without redirecting; redirect only on protected routes. |
| FE-05 / DEP-03 | **KYC documents cannot be opened in production** (R2 mode): the document URL needs a login header that a link/`<img>` can't send → 401. Blocks manager-verified captain KYC. | TRACE (two independent audits) | Fetch as a blob with the auth header, or short-lived signed URLs. |
| DEP-04 | **WhatsApp messages outside the 24-hour window are silently dropped** unless `WHATSAPP_UPDATE_TEMPLATE_NAME`, `WHATSAPP_BUSINESS_ACCOUNT_ID`, the temp-password template and an approved-template **Sync** are all in place. Affected: captain "New job", complaint and society notices, auto-pay stopped, manager alerts, temp passwords. | TRACE | Deploy must require them; sync templates on boot/hourly. |
| PAY-09 | Payment verify trusts an **authorized** payment; if Razorpay auto-capture is OFF, payments auto-refund after ~5 days while bookings stay "paid". | TRACE | Confirm auto-capture ON, or capture/fetch in verify. |
| DEP-08 | Unique/safety indexes can **silently be missing**: index build failures only log a warning, and this release drops the old duplicate-booking index *before* building v3. If production has legacy duplicates, the duplicate-booking and payment-uniqueness guards don't exist. | TRACE | Build v3 before dropping v2; fail readiness if critical unique indexes are missing. |

---

## 4. P2 Issues — major

**Payments / pricing:** PAY-05 manager mark-done racing an online payment books it as manager cash · PAY-06 cash completion leaves the customer's open payment link payable (double payment, parked) · PAY-07 "once per customer" coupon used twice by concurrent requests · PAY-08 manager-sold auto-pay passes report ₹0 paid · PRICE-01/FE-14 landing "₹999 / Month" is hard-coded (requested in commit a8cf1be) — must equal a real buyable price · FE-S03 admin Pricing page can overwrite captain fee/distance charge with defaults after a failed load · FE-S02 manager "Sell A Plan" link lost on refresh → second payable link · FE-12 pass can be re-bought while the first payment is confirming · PRICE-04 price change between quote and booking is applied silently.

**Plans / society:** PASS-2 upgrading between plans with category quotas refills them (RE-RUN: 4th wash accepted on a 2-wash plan — P1 if such plans are configured) · SOC-1 society revenue missing from admin/manager KPIs and collections · SOC-3 switching a society off strands paid residents (hub 404, captain list empty, no refund row; API-only) · SOC-4 a car-bound society pass is auto-applied to type-only bookings anywhere · MGR-01 a manager can silently spend another center's customer's plan washes via "Log A Done Job".

**Bookings:** BOOK-04 moving a multi-car visit is not atomic (racing a single-car cancel frees the old seat twice) · SLOT-05 availability ignores the daily cap (shows available, then refuses).

**Security / auth:** AUTH-03 MSG91 widget tokens reusable · AUTH-04 OTP codes not bound to purpose (a booking code resets a password) · AUTH-06 **anyone can block a customer's OTP login for 60 minutes** by requesting 5 booking codes to their number (repeatable) · CAP-03 a captain can inflate/drain any center's inventory with a negative quantity · CAP-05 suspending a captain strands his jobs; admin suspend works mid-job · ADM-01 `PUT /settings` writes any setting with no validation or audit (a bad value took booking down) · ADM-02 staff creation (incl. new admins) not audit-logged; center not validated · ADM-03 deleting a user with wallet balance / live pass / unpaid booking / center-manager role is allowed (hard delete) · ADM-05 deleting a center with live bookings allowed, no restore · ADM-06 deleting a service breaks live bookings and passes · ADM-07 invalid working hours ("9am") take that center's slot API down (500) · MGR-02 "Log A Done Job" discount uncapped (100% → ₹0) · MGR-03 tip up to ₹1,00,000 on any old job inflates revenue.

**Notifications:** NTF-07 **customer booking confirmation reads "Star Wash booked, Star Wash on 2026-10-08 at …"** — duplicated service, raw ISO date, no car type, no price (E2E; `booking_service.py:1170-1173` uses `date_str` not `wa_date`) · NTF-01 society link paid via webhook also sends "booking *None* is fully paid" · NTF-02 no payment receipt on the common (browser-callback) path · FAIL-01 manager assign/reschedule and captain steps wait on Meta (≈20 s if Meta hangs) · FAIL-02 failed WhatsApp sends are never retried · FAIL-03 an R2 storage outage blocks captains from starting/finishing jobs (500/400) · NTF-08 confirmation page link (`/thank-you?token=…`) is sent to Google Analytics and the Meta Pixel as part of the page URL — the token shows the booking and its service code (TRACE).

**Deploy / ops:** DEP-05 health check stays green with the database down · DEP-06 production invariants (WhatsApp provider, storage, keys) only checked by the deploy script, not the app · DEP-07 `backend/.gcloudignore` is untracked — a fresh checkout would upload `.env.production` to Cloud Build · DEP-09 no structured logs, request ids, error tracking or alerts; some phone numbers logged; websocket JWT in query strings · PERF-01 websocket events don't cross Cloud Run instances (captain jobs list polls every 180 s).

**Frontend:** FE-01 any token-refresh failure (network blip, 5xx, 429) logs the user out with a hard redirect — captains on mobile data · FE-06 refreshing /book while signed in loses the draft · FE-07 Reschedule offered when the backend will refuse it (assigned / inside 4 h) · FE-08 after 15 idle minutes on /app/book the quote silently becomes anonymous (first-wash price, plan not applied) · FE-09 "Price shown is final" shown before the phone number is known · FE-10 no request timeout — spinners can run for minutes · FE-11 any `/auth/me` failure logs the user out · FE-13 suspended account returns 401 → bounced to /login with no message · FE-15/FE-S05 **about 40 screens show an API failure as "nothing here"** (addresses, support, notifications, captain earnings, staff lists…) · FE-S06 five staff screens can spin forever · FE-S04 collections "This Month/Today" presets use UTC (wrong range 00:00–05:30 IST) · FE-S07 cash plan sale, center deactivate, withdrawal approve have no confirmation · FE-S01 WhatsApp inbox: Enter twice sends the reply twice · **RESP-01 Contact Us modal can't be scrolled on small phones (E2E at 360×640: title and close cut off, Send half off-screen)** · RESP-02/03/04/05/06 drawer header, customer search, keyboard covering buttons, forgot-password card, slot skeleton overflow · UI-01 old black/gold remnants visible on public pages (black browser bar `theme-color #0A0A0A`, `#FBBF24` dashboard button, `#FCD116` offer bar, black WhatsApp cards) · UI-02 7–10 px text on public/customer screens · UI-03 timestamps "7 Oct 2026, 02:35 pm".

**Business consistency:** BIZ-01 the published cancellation deductions (₹50/₹80/₹100) **cannot be applied anywhere in the system** — there is no field or flow to add a deduction to a refund or the next booking · BIZ-02 opening hours disagree: website 7 AM–7 PM, booking policy 9–19, center default 8–20 — confirm the real hours.

---

## 5. P3 Issues — minor

Payments: PAY-10 plan `purchased_price` re-priced at activation · PRICE-02 duplicate service ids not de-duplicated · PAY-11 coupon doc without a limit field unusable · PRICE-03 first-time price rules differ between site and server on edge values · PRICE-05 coupon silently dropped when the first car is plan-covered · PRICE-06 distance charge on a ₹0 plan car · PAY-12..15 autopay edge cases, several pending manager links, refunds never reflected in `payment_status`, quote reveals whether a number booked before · PRICE-07 coupon create allows >100% · PRICE-08 manager log discount cap rule unclear · PRICE-09 travel charge can change between quote and booking.

Plans/society: PASS-3 pass usable for dates after it ends · PASS-4 two paid checkouts for one pass → two passes · PASS-5 cash sale doesn't void a pending plan link · PASS-6 manager sees other centers' plan-sale amounts · SOC-5 one plate on two society passes under concurrency · SOC-6 duplicate societies from one lead · SOC-7 rejecting a society request doesn't tell the resident · SOC-8 app hides the society hub link when the form is off · SOC-9 residents KPI counts lapsed residents · SOC-10 resident can delete a car on a live society pass · SOC-11 cancelled online-paid society enrolment leaves no refund row.

Bookings: BOOK-05 same customer, two tabs, two car types → two overlapping bookings · BOOK-06 mark-done then delete never frees the seat · SLOT-02 slot-hold cap per full IPv6 address · SLOT-03 availability can be up to 60 s stale (backend always re-checks) · SLOT-04 slot-capacity override accepts junk dates/keys · STATE-01 self-assign racing a reschedule anchors start time to the old slot · STATE-02 millisecond window where an unpaid visit stops expiring · E2E-03 status history logs "captain on the way" twice.

Security/validation: ENUM-1 account existence revealed (forgot-password/OTP 404 vs 200; login 18 ms unknown vs 1,085 ms known) · CAP-06 status "inactive" blocks nothing · CAP-04 GPS not range-checked · VAL-1 address fields unbounded (1.5 MB accepted) · VAL-2 phone numbers with non-English digits accepted → look-alike duplicate accounts · VAL-3 heading with a bad inventory id → 500 and the job left half-updated · VAL-4 complaint attachments accept `javascript:` URLs (React 19 neutralises them on render) · VAL-5 quote accepts another user's address id (reveals only distance/center) · VAL-6 two admin routes 500 on garbage ids; `DELETE /users/not-an-id` says "deleted" · RL-1 rate limits per instance; rotating IPv6 /64s escapes them · bcrypt truncates passwords at 72 bytes · DATA-1 managers see full Aadhaar/PAN and bank account numbers (needed for KYC review, but could be masked).

Admin/ops: CAP-07 leave approval ignores assigned jobs · CAP-08 KYC not checked before assignment (rule unclear) · MGR-04 `/subscriptions/customer/{id}` skips the known-to-center check · MGR-05 slot capacity GET writes docs for junk dates · MGR-06 mark-done allowed on future bookings · ADM-08 update audits have no before/after · ADM-09 wallet adjust accepts any id · ADM-10 withdrawal can't reach "paid" after "approved" · ADM-11 force-delete leaves orphan wallet transactions · ADM-12 more unaudited mutations (inventory, FAQ, testimonials, template sync) · ADM-13 public plans list shows discontinued plans · ADM-14 `plan_washes_count` over-counts legacy rows.

Notifications/infra: NTF-03 no "captain arrived" or pre-slot reminder to customers; unused templates · NTF-04 wrong refund-due wording · NTF-05 WhatsApp "stop" and Meta opt-out not saved · NTF-06 stale "starting soon" reminders after downtime · FAIL-04 a DB blip after commit hides a successful booking · FAIL-05 slow Meta starves later sweeps · FAIL-06 background messages lost on shutdown · FAIL-07 brief lease overlap can double-send · PERF-02 shared Razorpay thread pool · PERF-03 unbounded subscriber/review/captain-history queries · PERF-04 rate limits in memory; verify `api.blussit.com` is not behind Cloudflare proxy · DEP-10 dependency upgrades still pending (starlette 0.38.6, python-multipart 0.0.9, python-jose 3.3.0); transitive deps unpinned · DEP-11 no CSP; HSTS without includeSubDomains; preview builds call the production API · DEP-12 deploy script `source`s the env file.

Frontend: FE-16..24 stale caches after reschedule, retry after a lost response shows a bare "already booked", captain cash double-tap, address text duplication on edit, upgrade dialog wording, raw "Request failed with status code 502", multi-tab identity mismatch, slot holds when storage is blocked, society back-button/quote staleness · E2E-02 confirmation page has no price/payment method/address and drops the car type when opened on another device · UI dead code (Vite template assets, ~55 MB unused originals in `public/`, unused exports, gold CSS tokens behind remap shims) · RESP-07..19 small-screen layout issues · `maximum-scale=1` blocks pinch zoom.

---

## 6. Broken Flows

| Flow | What breaks | IDs |
|---|---|---|
| Online booking, last-second payment | Paid booking cancelled by the expiry sweep | PAY-02 |
| Captain QR on a mixed plan + paid visit | Money parked, car unpaid, captain asks again | PAY-03 |
| Captain completes while customer pays QR | Captain wallet debited instead of credited | PAY-04 |
| Admin changes slot length / hours | Capacity limits disappear for booked days | SLOT-01 |
| Admin restores a deleted booking | Over-books; later resold | BOOK-03 |
| Staff member messages the business WhatsApp | Staff password wiped, logged out | AUTH-05 |
| Manager KYC review (production storage) | Documents won't open | FE-05 |
| Returning visitor from an ad | Sent to /login | FE-02 |
| Quote fails on the booking page | Booked at a price not shown | FE-03 |
| Customer cancellation with deduction | No way to apply the published deduction | BIZ-01 |
| Society switched off | Paid residents stranded | SOC-3 |
| Contact Us on small phones | Form can't be submitted | RESP-01 |

Verified working end to end (E2E): guest booking → OTP → confirmation page (also after refresh and on another device) → manager queue shows ₹369 → assign (double-click: one assignment) → captain start window message → start trip → wrong code refused with a clear message → right code → before photo → start wash → after photo (double-click: one completion) → booking completed, cash marked paid, captain earns ₹40 and the ₹329 platform share is debited from his wallet → customer messages in order (confirmed, assigned, on the way, started, completed) → customer app shows "Wash Complete".

## 7. Missing Flows

- Cancellation deduction: no field, flow or report to apply the ₹50/₹80/₹100 deductions (BIZ-01).
- Payment receipt on the common callback path; "captain arrived" and pre-slot customer reminders (NTF-02/03).
- Retry for failed WhatsApp sends (FAIL-02); readiness probe with a DB ping (DEP-05); error tracking and alerts (DEP-09).
- Society lifecycle: no defined "end society" flow, no delete endpoint, no mid-cycle plan change (SOC-3).
- Refunds: `refunded` status never written; refund webhooks ignored (PAY-14).
- Restore for deleted centers, services, zones (ADM-05/06).
- Counter reconciliation job for `booked_count` (safety net for BOOK-01..06).

## 8. Security Issues

P0-2 AUTH-01 · P1 AUTH-05, AZ-01, AUTH-02, CAP-01, CAP-02 · P2 AUTH-03, AUTH-04, AUTH-06, CAP-03, ADM-01, NTF-08, DEP-07, DEP-11 · P3 ENUM-1, VAL-1..6, RL-1, CAP-04/06.

Checked and holding: no route reachable without login except the public ones (107-route sweep); 170-request cross-user/cross-center matrix (customer, captain, manager A vs B) — all refused except AZ-01/MGR-04; forged/`alg=none`/expired/wrong-secret tokens refused; refresh rotation with reuse detection; logout revokes access and refresh; registration needs phone proof; staff can't use Google sign-in; OTP brute force capped at 5 attempts even with 40 parallel guesses; OTP replay and expiry refused; login lockout atomic under 20 parallel tries; JSON operator injection (`{"$ne": null}`) rejected with 422; mass assignment of role/price/status/owner/wallet ignored on users, vehicles, addresses, bookings, reviews; spoofed `X-Forwarded-For` doesn't bypass rate limits; no internal fields (captain fee, platform earning, service code) in public or captain responses; websocket channel authorisation; dev OTP shortcut impossible in production.

## 9. Payment Issues

P0-3 DEP-02 · P1 PAY-01..04, PAY-09, FE-04 · P2 PAY-05..08, FE-12, FE-S02 · P3 PAY-10..15.

Checked and holding: verify + webhook + sweep + second verify racing → settled exactly once; forged signature, someone else's order, replayed signature on another order, bad webhook signature, forged link callback — all refused, nothing marked paid; second capture on one order parked; two tabs, two orders both paid → one parked; coupon returned when an unpaid booking expires; every charged amount computed from the stored booking/plan/enrolment; quote equals create for a 3-car visit with prepaid service, travel and coupon (₹1,040 = ₹1,040); frontend never treats checkout success as paid; online booking sends no confirmation until paid, then exactly one.

## 10. Booking & Slot Issues

P1 SLOT-01, BOOK-01, BOOK-02, BOOK-03 · P2 BOOK-04, SLOT-05 · P3 BOOK-05/06, SLOT-02..04, STATE-01/02.

Checked and holding: 10 users → last seat: 1 booked; 100 users → last 5 seats: 5 booked, 95 clean refusals; `/bookings/quick` 20 → 3 seats: 3; identical triple tap → one visit; holds vs racers; cancel ×4 double-click → released once; 6 concurrent reschedules into 2 seats → exactly 2; cancel vs reschedule ×10; assign vs cancel; after-photo vs cancel (never cancelled-and-paid); capacity cut 4 → 1 mid-flight; expiry sweep releases once; invented/past/out-of-hours/other-center slots refused; center always derived from the address.

**Actual booking state machine** — statuses: `awaiting_payment, pending, assigned, captain_on_the_way, service_started, completed, cancelled, rescheduled` (+ `is_deleted` flag).

| From | To | Who | Guarded |
|---|---|---|---|
| — | pending / awaiting_payment | create (all channels) | seat reserved in the same transaction |
| — | completed | manager "Log A Done Job" | no seat taken |
| awaiting_payment | pending | payment verify/webhook/sweep, switch to cash | yes |
| awaiting_payment, pending, assigned, on the way, started, rescheduled | cancelled | customer (pending/rescheduled/awaiting only, > 4 h), staff, expiry sweep | yes (transaction) — expiry needs the payment guard (PAY-02) |
| pending, rescheduled | assigned | assign / auto-assign | yes (transaction + captain lock) |
| pending, assigned | assigned | reassign | yes |
| assigned, on the way | pending | captain release | yes |
| assigned | captain_on_the_way | start trip (-30 min … +4 h) | yes |
| captain_on_the_way | (verified) | arrival code | yes — no attempt limit (CAP-02) |
| captain_on_the_way | service_started | before photo | yes |
| service_started | completed | after photo | yes — payment fields not guarded (PAY-04) |
| pending, rescheduled, assigned | completed | manager mark-done | yes — payment fields not guarded (PAY-05) |
| pending, assigned, rescheduled | rescheduled | reschedule (customer pending/rescheduled only, > 4 h) | yes |
| any | is_deleted | admin delete | stale seat decision (BOOK-02) |
| deleted | live | admin restore | seat best-effort (BOOK-03) |

`completed` and `cancelled` are terminal: 13 endpoints tried against each — all refused. Awaiting-payment, deleted, on-the-way and started bookings refuse every impossible transition tested.

## 11. Pricing Issues

P1 PAY-01, SOC-2, FE-03 · P2 PRICE-01 (₹999), PRICE-04, FE-S03, PASS-2 · P3 PRICE-02..09.

Single source of truth: **the backend** (`BookingService._price_services` shared by quote and create; `resolve_pass_price` for plans; society `payment_quote`). Every frontend price is either the server's number or a placeholder replaced by `/bookings/quote` — **except** the landing "₹999 / Month" (hard-coded) and the booking button when the quote fails (FE-03). WhatsApp bot uses the same server pricing. Seen in E2E: SUV Star Wash ₹369 identical on the booking page, thank-you page, manager queue, captain job screen and database.

## 12. Society Issues

P1 SOC-2 · P2 SOC-1, SOC-3, SOC-4 · P3 SOC-5..11 (+ change-request double approval, revenue dated by link creation, renewal undercount, non-atomic activation, form retyping a saved car, autopay quota at old price — TRACE).

Checked and holding: cross-center managers refused on 9 society endpoints (403); another society's link/pass → 404 or empty; concurrent cash renewal ×3 → one renewal; plan switched off blocks renewal (documented rule).

Flow (as built): public lead → manager/admin → register society (center from pin) → configure plans/rate card/schedule → residents enrol by link (OTP) → pay (online/link/cash, revision + amount checked) → passes per car → daily schedule sweep creates bookings → captain arrive/washed → renew → cancel. There is **no admin approve/reject state** — a lead is simply closed — and no deactivation lifecycle.

## 13. Captain Issues

P1 CAP-01, CAP-02, PAY-04 · P2 CAP-03, CAP-05, FAIL-03 · P3 CAP-04, CAP-06, CAP-07, CAP-08, FE-18.

Checked and holding: another captain (same or other center) refused on 13 job endpoints; skipping steps refused; replays safe; cash collect on online-paid or cancelled jobs refused; suspended captain's old token refused; captain never sees the service code or platform earning. E2E on a phone screen: clear start-window message, clear wrong-code message, photo capture, double-tap protection.

## 14. Manager Issues

P1 AZ-01 · P2 MGR-01, MGR-02, MGR-03, PASS-1, FE-S02, FE-S07 · P3 MGR-04..06, PASS-6.

Checked and holding: manager A refused on 40 center-B endpoints (bookings, queue, capacity, captains, KYC, attendance, wallet, leave, inventory, CRM 360, dashboards, complaints, password reset) and on 12 society endpoints; booking into another center's address refused; manager can't create an admin. All 13 manager pages render without errors or overflow at 390 px and 1366 px (E2E). Dashboards agree with each other and the database on a seeded set (bookings 7, completed 5, revenue ₹1,347) except ADM-14.

## 15. Admin Issues

P2 ADM-01, ADM-02, ADM-03, ADM-05, ADM-06, ADM-07, CAP-05, FE-S03 · P3 ADM-08..14.

| Action | Validation | Audit-logged | Reversible | Can corrupt live data |
|---|---|---|---|---|
| Create staff (incl. admin) | center not checked | **No** | — | yes (ADM-02) |
| Change user role/center/status | partial | yes | yes | **yes** (CAP-01/05) |
| Delete user | live bookings only | yes | **no** | **yes** (ADM-03) |
| Delete center | none | yes | **no** | **yes** (ADM-05) |
| Center hours / slot length | none | yes, no diff | yes | **yes** (ADM-07, SLOT-01) |
| Delete service | none | yes, no diff | soft only | **yes** (ADM-06) |
| `PUT /settings` | **none** | **no** | — | **yes** (ADM-01) |
| Plans, coupons, pricing config, booking policy | yes | yes | yes | no (except via `/settings`) |
| Wallet adjust, withdrawals | partial | yes | counter-adjust | ADM-09/10 |
| Booking delete / restore / permanent | partial | yes | soft only | **yes** (BOOK-02/03, ADM-11) |

## 16. Frontend/UI Issues

P1 FE-02 (E2E), FE-03, FE-04, FE-05 · P2 FE-01, FE-06..13, FE-15, FE-S01..S07, UI-01..03 · P3 FE-16..24, dead code.

Checked and holding: `tsc` 0 errors; all 257 source files reachable; 338/338 frontend API calls match a backend route; no TODO/FIXME/console.log/empty handlers; double-submit protected on booking, OTP, cancel, reschedule, captain steps, assign (server also refuses the duplicate); confirmation page survives refresh and opens on another device; no raw 24-hour times reach customers.

## 17. Mobile/Responsive Issues

E2E sweep at 390 px: **no horizontal overflow** on any public, customer, captain or manager page tested; booking flow, OTP popup, captain job screen and confirmation page lay out correctly. Broken: RESP-01 Contact Us at 360×640 (E2E). From code: RESP-02..06 (drawer header, customer search, keyboard covering buttons, forgot-password card, slot skeleton) and RESP-07..19 at ≤360 px.

## 18. Performance/Scalability Issues

P1 DEP-08 · P2 PERF-01, FAIL-01, FAIL-05 · P3 PERF-02..04, FE-S06.

Capacity estimate is unchanged from 10-06: comfortable for 1k–10k customers on the current design; 100k customers needs Atlas M30+, bounded admin analytics, shared websocket fan-out and a shared rate-limit store.

## 19. Database Issues

- Safety-net unique indexes can be silently absent (DEP-08); the duplicate-booking index is dropped before its replacement is built.
- Seat counters (`booked_count`) have no ownership record on the booking and no reconciliation job — the root of BOOK-01..06.
- `scheduled_date` accepts timezone-aware values (BOOK-01).
- Live passes have no uniqueness guard (PASS-1/4, SOC-5); societies none per lead (SOC-6); coupon per-user usage none (PAY-07).
- Hard deletes of users/centers leave orphans (ADM-03/05/11).
- Rules that exist only in code and should also be guarded in the database: one live pass per car, one seat per visit, one society per lead, per-user coupon limit, unique society plate.

## 20. Notification/WhatsApp Issues

P1 DEP-04 · P2 NTF-07 (E2E), NTF-01, NTF-02, NTF-08, FAIL-01, FAIL-02, AUTH-05 · P3 NTF-03..06, FAIL-04..07.

Checked and holding: every template call passes the right number of variables; sends happen after the database write; an online booking gets no confirmation until verified payment, then exactly one; bot confirmations are not duplicated; marketing templates respect opt-out; managers get WhatsApp only for new bookings. E2E message sequence for one booking: confirmed → captain assigned → on the way → service started → completed (+ manager "New booking" and captain "New job").

## 21. Deployment Issues

Must set or verify **before** deploying:

| Item | Status |
|---|---|
| Repo private + history purged (P0-1) | MUST DO |
| Seed staff accounts removed / phones changed in production (P0-2) | MUST DO |
| `RAZORPAY_KEY_ID` = `rzp_live_…` + secret; auto-capture ON; `RAZORPAY_WEBHOOK_SECRET` + dashboard webhook (P0-3, PAY-09) | MUST VERIFY |
| `WHATSAPP_UPDATE_TEMPLATE_NAME`, `WHATSAPP_TEMP_PASSWORD_TEMPLATE_NAME`, OTP template, `WHATSAPP_BUSINESS_ACCOUNT_ID`; templates approved; CRM → Templates → Sync (DEP-04) | MUST SET |
| `R2_PUBLIC_BASE_URL` = public bucket domain (deploy refuses the private endpoint) | SET |
| Commit `backend/.gcloudignore` (DEP-07) | MUST FIX |
| Cloud Run `cpu-throttling=false` on the live revision | VERIFY after deploy |
| Atlas allows ≥300 connections (10 instances × 30) | VERIFY |
| `api.blussit.com` DNS-only (not Cloudflare-proxied) for correct client IPs | VERIFY |
| Readiness probe with DB ping; error tracking; alerts (DEP-05/09) | MISSING |
| Unique indexes present after first boot (DEP-08) | VERIFY |
| Google Search Console after deploy | founder |

Already enforced: JWT secret length/default, `*` CORS, WhatsApp provider = Meta, storage = R2, SMS provider, `--no-cpu-throttling`, non-root Docker image without `.env`, DEBUG off, dev tools impossible outside development.

## 22. Hardcoded/Temporary Code

No TODO/FIXME, bare `except:`, `print`/`console.log`, mock APIs, fake success paths, empty handlers or "coming soon" screens.

| Item | Classification |
|---|---|
| Landing "₹999 / Month" (`PlansShowcase.tsx:96`) | **Blocker unless** it is a real buyable price — derive it or confirm it |
| Homepage stats "4.9/5 · 140+ · 2000+ L" | OK — founder confirmed true (10-07) |
| `PUT /settings` generic upsert | Remove / lock (ADM-01) |
| `seed.py` overwrites catalogue prices with no production guard on catalogue writes | Risk — never run against production |
| Silent excepts that change money: `_usable_passes` returns `[]` on a DB error → full price charged | Risk |
| Price fallbacks used in real charges: society ₹38/₹52, travel ₹2/km | Risk — must match admin settings |
| Fixed rules admins can't change: penalties 25%/50%, manager plan discount ≤50%, geofence 500 m vs 300 m | Safe if intended |
| Unused endpoints (`POST /auth/otp/verify`, FAQ/testimonial CRUD), ~22 unused API wrappers | Safe to remove |
| Real-looking phone numbers in `scripts/delete_booking.py:9`, `whatsapp_bot_service.py:236` | Check and remove |
| Dev tools (fixed OTP, local badge) | Safe — gated by env + local DB |

## 23. Test Coverage Gaps

The 983 existing tests pass but did not catch: seat bookkeeping on delete/restore/timezone dates (BOOK-01..03), slot-config changes (SLOT-01), payment-vs-completion races (PAY-02/04/05), captain QR binding on mixed visits (PAY-03), pass checkout with a retyped car (PAY-01), concurrent plan sales (PASS-1), staff phone/WhatsApp interactions (AUTH-01/05), manager address access (AZ-01), arrival-code limits (CAP-02), frontend auth interceptor and error states (no frontend test suite exists). Each finding above has a reproduction test that should become a permanent regression test when it is fixed.

## 24. Root Causes

1. **Seat ownership is inferred, not recorded.** Releases decide from stale reads and sibling positions → BOOK-02/03/04/06; plus raw date input (BOOK-01) and slot keys tied to current config (SLOT-01).
2. **Guards check status but not money.** Completion, mark-done and expiry writes guard on `status` only, not on `payment_status`/`payment_method` → PAY-02/04/05.
3. **Check-then-insert without a claim.** Pass creation, society creation, coupon per-user, plate rule → PASS-1/4, SOC-5/6, PAY-07.
4. **Phone ownership assumed for staff.** Staff phones are never proven, yet OTP reset, MSG91 and the WhatsApp bot treat a phone as identity → AUTH-01, AUTH-05.
5. **Ownership/center checks applied per endpoint, not centrally.** The 360 view got the "known to my center" rule; addresses and subscriptions didn't → AZ-01, MGR-04; captain steps check `captain_id` but not center → CAP-01.
6. **Values frozen too late.** Pass orders don't freeze vehicle type; links don't freeze which cars they pay → PAY-01, PAY-03.
7. **Frontend treats every failure the same.** Interceptor logs out on any refresh failure; queries without `isError` branches show "empty" → FE-01/02/11/15.
8. **Production invariants live in a shell script,** not the app → DEP-02/04/06.

## 25. Fixes Applied

**None — first pass, by instruction.** Proposed order for the fix pass (each fix ships with its reproduction test as a regression test, then full suite + build + E2E re-run):

| Step | Fixes | Root cause |
|---|---|---|
| 0 (founder, external) | P0-1 repo private/purge · P0-2 check & remove seed staff in production · P0-3 Razorpay live keys + auto-capture · WhatsApp templates/WABA/sync · decisions below | — |
| 1 | AUTH-01 (reset only to verified phones; staff reset admin-only), AUTH-05 (bot never adopts staff), AZ-01 + MGR-04, AUTH-02, AUTH-03, AUTH-04, AUTH-06 | 4, 5 |
| 2 | PAY-02, PAY-04, PAY-05 guards on payment fields; PAY-03 link binding; PAY-06 void links on cash; PAY-01 freeze vehicle type; FE-04/FE-12 backend refusal of a second order while one is captured | 2, 6 |
| 3 | Seat ownership flag + transactional release/restore (BOOK-02/03/04/06), date normalisation (BOOK-01/05), slot-config guard (SLOT-01), nightly counter recount | 1 |
| 4 | Insert-first claims: PASS-1/4, SOC-5, SOC-6, PAY-07; SOC-2 rate-card versioning; PASS-2 | 3 |
| 5 | CAP-01/05 release jobs on move/demote/suspend + center check on captain steps; CAP-02 code lockout + GPS + photo binding; CAP-03 quantity validation | 5 |
| 6 | ADM-01 lock `/settings`; ADM-03/05/06 reference checks; ADM-07 hours validation; ADM-02 audit staff creation; MGR-01/02/03 | — |
| 7 | Frontend: FE-01/02/11 interceptor; FE-03 live-quote gating + confirmation total; FE-04/12 pay gating; FE-05 KYC blob; RESP-01; FE-15/S05 error states; FE-S01/S03; UI-01 colours | 7 |
| 8 | NTF-07 confirmation copy; NTF-01/02; FAIL-01 background sends; FAIL-02 retry outbox; NTF-08 strip token from analytics URL | — |
| 9 | DEP-05 readiness probe; DEP-06 production config validator; DEP-07 commit `.gcloudignore`; DEP-08 index order + verification; DEP-09 logging/error tracking; dependency upgrades | 8 |

**Decisions needed from the founder** (business rules — not invented here):
1. Cancellation deductions (BIZ-01): should the system actually apply ₹50/₹80/₹100 (to the refund / next booking), or should the policy wording change?
2. Is ₹999/month a real price someone can buy today? If not, derive the "From" price from live plans.
3. Real opening hours: 7–19 (website), 9–19 (booking policy) or 8–20 (center default)?
4. Should a manager's "Log A Done Job" discount and tips be capped? At what?
5. Ending a society: refund rule and what happens to scheduled washes.
6. May a car-bound society pass be used outside the society (SOC-4) or after its end date (PASS-3)?

## 26. Remaining Risks (could not be verified)

- Production database: whether seed staff exist (P0-2), whether legacy duplicates block unique indexes (DEP-08), whether existing `booked_count` counters already drifted (BOOK-01..06) — needs a read-only production check.
- Production configuration (`.env.production` deliberately not read): Razorpay mode, WhatsApp templates, R2, Cloud Run flags.
- Live third parties not exercised: Razorpay test/live checkout in a browser (no keys locally), MSG91 token semantics (AUTH-02), Meta template delivery, R2 KYC access (FE-05).
- Multi-instance behaviour (Cloud Run up to 10) was simulated by concurrent requests against one replica set, not by running 10 instances.
- Load beyond a few hundred concurrent requests was not tested.
- Frontend findings marked TRACE (FE-01, FE-03, FE-04, FE-05) are from code; FE-02 and RESP-01 were confirmed live.

## 27. FINAL PRODUCTION VERDICT

# 🔴 NOT READY FOR PRODUCTION

Unresolved: 3 P0 (public personal-data exposure; staff takeover via unverified phones; unenforced Razorpay live mode) and P1s in payment integrity (PAY-01..04), booking capacity (SLOT-01, BOOK-01..03) and authorization (AUTH-05, AZ-01, CAP-01/02).

### Production gate — 30 questions

| # | Question | Answer |
|---|---|---|
| 1 | Major customer flows working? | Yes in the happy path (E2E); FE-02/03/04 break edge paths |
| 2 | Captain flows working? | Yes (E2E); CAP-01/02, PAY-03/04 |
| 3 | Manager flows working? | Yes (E2E, 13 pages); MGR-01..03, PASS-1 |
| 4 | Admin flows working? | Mostly; ADM-01/03/05/06/07 can corrupt live data |
| 5 | Society flows working? | Yes; SOC-1..4 |
| 6 | Pricing consistent everywhere? | Yes except ₹999 (PRICE-01), FE-03, SOC-2, PAY-01 |
| 7 | Payment securely verified? | Yes (signatures, settle-once) — if live keys + auto-capture (P0-3, PAY-09) |
| 8 | Can payment be manipulated? | Partly — PAY-01 (vehicle type), test-key risk (P0-3) |
| 9 | Can bookings exceed capacity? | **Yes** — SLOT-01, BOOK-01..03 (not under plain concurrency) |
| 10 | Race conditions handled? | Core ones yes; PAY-02/04/05, PASS-1, PAY-07, BOOK-02/04 no |
| 11 | Users access other users' data? | Managers: customers' addresses (AZ-01) |
| 12 | Managers access unauthorized centers? | No (40 endpoints) — except AZ-01/MGR-01/MGR-04 data paths |
| 13 | Important APIs validated? | Mostly; ADM-01/07, VAL-1..6, SLOT-04 |
| 14 | Database constraints correct? | Partly — DEP-08, section 19 |
| 15 | Loading/empty/error states? | No — ~40 screens show errors as empty (FE-15/S05) |
| 16 | Mobile layouts safe? | Yes on all swept pages; RESP-01 and small-screen items |
| 17 | Overflow/broken cards/modals? | RESP-01..06 |
| 18 | Old UI accidentally used? | Visible remnants on public pages (UI-01) |
| 19 | WhatsApp/notification flows complete? | Only with templates configured (DEP-04); NTF-01/02/07 |
| 20 | Fake/mock production behaviour? | None found |
| 21 | Hardcoded business values? | ₹999; society/travel fallbacks; fixed penalty rules |
| 22 | Production environment variables correct? | Not verifiable here (section 21) |
| 23 | External integrations failure-safe? | Partly — FAIL-01..03, DEP-05 |
| 24 | Pagination where required? | Mostly; PERF-03 |
| 25 | Expensive queries optimized? | Hot paths indexed (10-06); PERF-03 remains |
| 26 | Rate limits/abuse protection? | Yes per instance; AUTH-06, RL-1, CAP-02 |
| 27 | Authentication/authorization secure? | **No** — P0-2, AUTH-05, AZ-01, AUTH-02 |
| 28 | Duplicate requests/idempotency? | Bookings/payments yes; plan sales (PASS-1), coupons (PAY-07) no |
| 29 | Logs sufficient to debug production? | No — DEP-09 |
| 30 | Unresolved P0/P1? | **Yes** |
