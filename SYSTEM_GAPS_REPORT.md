# BLUSSIT — Complete System Gaps Report


---

# ✅ FIX STATUS — updated 9 Sept 2026 (same day, full remediation pass)

**FIXED — Security (all of §A + §B):** A1 subscription ownership · A2 webhook fail-closed
outside DEBUG · A3 captain self-top-up endpoint REMOVED (cash-in now goes through the
audited admin adjustment) · A4 totals clamped ≥0 · A5 notification mark-read owner-scoped
atomic · A6 rate-limit IP no longer spoofable (TRUST_PROXY_HEADERS opt-in) + OTP/reset
rules added · B1 OTP single-use (consumed on verify) · B2 forgot-password bumps
token_version · B3 real `POST /auth/logout` (kills all refresh tokens; frontend calls it)
· B4 boot refuses default JWT secret when DEBUG=false · B5 penalty conserves money
(captain loses exactly what platform gains, floored at 0) · B6 cancel/reschedule writes
guarded (no double capacity release) · B7 hold endpoint validates date+slot against real
generated slots · B8 all seven cross-center holes closed (complaint update, leave review,
captain performance, inventory CRUD, wallet read + bank masking for managers, CRM 360
customers-only, assign/reassign same-center rule) · B9 withdrawal review atomic claim +
Literal status · B10 streaming upload cap + magic-byte sniffing + nosniff headers ·
B11 delete-user blocks on active bookings + kills sessions.

**FIXED — Races & integrity (§C):** C1 unique live-review-per-booking index + duplicate
handling · C2 unique attendance-per-day index + duplicate handling · C3 KYC submit/review
guarded against lost updates · C4 start_heading idempotent (atomic claim) + guarded
inventory decrement · C5 one pending withdrawal at a time · C6 Google sign-in omits null
email · C7 WhatsApp failures now logged loudly · C8 soft-deleted docs are write-protected
· C9 hold sweep drains in batches · C13 captain earnings = captain_earning (gross exposed
separately) · C14 punctuality respects the date filter.

**FIXED — Workflows (§D/§E):** manager leave-approval panel (approve/reject + captain
notified) AND approved leave now blocks assignment for those dates · vehicle EDIT +
address EDIT · role-aware login/Google/guest redirects + deep-link (state.from) honored ·
staff booking deep-links land on the queue (highlighted), not the customer page ·
MandatoryGates has a "Log out instead" escape · guest accounts get "Set your password
(OTP)" instead of a dead login · global mutation-error toast (every silent mutation now
surfaces failures) + errors shown inside the cancel/reschedule modals · confirmations on
cancel-plan / delete-review / logout · customers can REPLY on support threads (two-way
now; manager notified) · homepage settings actually drive the hero (headline/subtext/
banner wired; dead "featured" fields removed from the form) · privacy-policy + terms pages
created and footer legal links wired; dead footer rows/social icons removed (WhatsApp icon
live) · guest wizard: coupon field, honest first-wash pricing (struck-through regular
price), OTP resend + change-number escape · manager booking flow gained a coupon input ·
landmark edits no longer silently dropped · dead `line2` removed · manager-without-center
sees a real message instead of an infinite KPI spinner · category rename/delete UI ·
hardcoded fake testimonials REMOVED (section hides until real ones exist) ·
`VITE_WHATSAPP_NUMBER` documented in .env.example.

**FIXED — Performance & scale (§G):** index pack (bookings plate/phone/vehicle/address,
pincode-dispatch multikey, coupon_usages, leave_requests, reviews, complaints.booking_id,
notifications bell compound, audit compounds, otp_requests + TTL) · whatsapp outbox/inbox
365-day TTLs · zombie sweeps time-bounded in the query · left-site sweep batched ·
available_slots one-query · policy 10s read-cache (busted on write) · startup index
builds + backfill moved off the boot path · subscription expiry sweep (status drift +
dead auto_renew resolved) · WS sockets close at token expiry · entry bundle 728KB→427KB
(Leaflet + all authed pages lazy; react vendor note in vite.config).

**REMOVED (dead weight):** captain top-up (endpoint+UI) · rebook endpoint (the detail
page's Rebook button covers it) · 8 zero-import components · dead API client functions ·
`GOOGLE_OAUTH_CLIENT_SECRET` config field · gender/date_of_birth off the public schema.

**CORRECTIONS to this report:** BookingQueuePage already sorts by priority first
(`byPriorityThenScheduled`) — the "priority is decorative" finding was wrong.

**DEFERRED (explicit, not forgotten):** payment gateway (prereq for wallet gating) ·
refresh-token rotation w/ jti store (logout + token_version covers the practical risk) ·
full pagination retrofit across ~30 list views (backend caps at 100; worst pages noted) ·
admin feature pages for FAQ/testimonial CRUD, admin wallet adjust UI, cross-center
admin lists, captain inventory view, settings KV UI, template bootstrap button (backend
endpoints all remain, guarded) · attendance date-string→date migration · WS ticket auth ·
multi-worker rate-limit/WS (single-worker deployment stands, documented) · supplies
tracking · cancellation charge tiers (business decision).


*Generated 9 Sept 2026 from a full four-track audit: API wiring matrix (235 backend
endpoints × ~180 frontend calls), frontend dead-end sweep, adversarial security/bug hunt,
and architecture/scalability review. Every finding carries a file reference. The findings below are the ORIGINAL audit; see FIX STATUS above for what has since been remediated.*

**Totals: 6 critical security bugs · 11 high · ~25 medium · 13 built-but-unreachable
features · 20 dead API client functions · ~15 broken/half workflows · ~30 scalability
items · ~2,400 lines of dead frontend code.**

---

## A. CRITICAL — fix before anything else (security / money)

| # | Finding | Where | Failure |
|---|---|---|---|
| A1 | **Subscription theft (IDOR):** booking accepts any `subscription_id` — ownership is never checked against the caller | `booking_service.py:532` → `subscription_service.plan_consumption` | Customer A books free washes using customer B's plan and drains B's remaining count |
| A2 | **WhatsApp webhook fails OPEN:** empty `WHATSAPP_APP_SECRET` (current state) skips signature verification; webhook is also exempt from rate limiting | `whatsapp_webhook_routes.py:51`, `.env` | Anyone who finds the URL can impersonate any customer by phone number: list/cancel/reschedule their bookings, create bookings, and flip `phone_verified=True` |
| A3 | **Captain self-funds wallet:** `POST /wallet/top-up` credits any amount — there is **no payment gateway anywhere in the codebase** | `wallet_service.py:114`, `wallet_schema.py:6` | Captain tops up ₹1,00,000 (no money moves), requests withdrawal, admin sees a "funded" wallet and pays out real cash. (Wallet gating is OFF today, which contains this — but the endpoint is live) |
| A4 | **Negative totals auto-marked PAID:** combo + legacy subscription discount can exceed the combo price; no `max(0, …)` clamp | `booking_service.py:547,587` | ₹499 combo + unrestricted plan → `total_amount = -299`, `payment_status="paid"`; poisons revenue KPIs, captain still paid off full subtotal |
| A5 | **Notification IDOR:** `mark_read` ignores its `user_id` argument and returns the full document | `notification_service.py:75-77` | Any user reads any other user's notifications (booking numbers, complaint content, urgent alerts) by ID and silently marks them read |
| A6 | **Rate limiting fully bypassable:** `X-Forwarded-For` trusted blindly; and `/auth/otp/request`, `/auth/otp/verify`, `/auth/reset-password` aren't in the rules table at all | `rate_limit.py:47,26-41` | Unlimited OTP sends (real money per WhatsApp msg + spam to real people), unlimited OTP brute force, login-lockout DoS of the admin account |

## B. HIGH — security & money correctness

| # | Finding | Where |
|---|---|---|
| B1 | Verified OTP is never consumed — replayable for its full 10 min across reset-password → otp-login → verify-phone | `auth_service.py:271-286` |
| B2 | Forgot-password reset does NOT invalidate existing sessions (no `token_version` bump, unlike change-password) | `auth_service.py:288-295` |
| B3 | No refresh-token rotation/revocation store, **no logout endpoint** — stolen refresh token works 7 days | `auth_service.py:190-211` |
| B4 | Default JWT secret ships in code with no startup guard; `DEBUG=True` default exposes /api/docs | `config.py:27`, `main.py` |
| B5 | Late-start penalty can RAISE captain payout (recomputed from unclamped travel+service parts) — penalized captain earns ₹90 on a ₹49 job | `booking_service.py:1705-1713` |
| B6 | Concurrent cancel double-releases slot capacity → oversell; same read-then-write race on reschedule | `booking_service.py:1950-1988, 2016-2116` |
| B7 | Unauthenticated slot-hold endpoint accepts invented slot keys → freeze a center's whole calendar + unbounded junk `slot_capacity` docs | `booking_routes.py:204`, `booking_service.py:1051` |
| B8 | Manager cross-center holes: complaint update (no center check), leave review (any center), captain performance (filter optional), inventory create/update/adjust/delete (no scoping at all), captain bank details readable by any manager, CRM 360 returns any user's full PII, assign_captain never checks captain's center | `complaint_service.py:104`, `staff_ops_service.py:105`, `staff_directory_routes.py:120`, `inventory_service.py:33-53`, `wallet_routes.py:24`, `crm_service.py:37`, `booking_service.py:1412` |
| B9 | Withdrawal approval is check-then-act — double-click debits twice; `status` string unvalidated ("Approved" ≠ "approved" skips the debit) | `wallet_service.py:140-151`, `wallet_schema.py:15` |
| B10 | Upload reads entire body into RAM before the 8MB check (2GB POST = OOM = kills the single worker + reminder loop); content-type trusted, no magic-byte sniff, no `X-Content-Type-Options` | `core/storage.py:53-61` |
| B11 | Deleting a user: no cascade (orphaned bookings/vehicles/capacity), access token stays valid ~15 min, no token_version bump | `user_service.py:46` |

## C. MEDIUM — races & data integrity

| # | Finding | Where |
|---|---|---|
| C1 | Double-submit creates two reviews per booking (no unique index on `reviews.booking_id`) | `review_service.py:34-50` |
| C2 | Double check-in creates two attendance rows (no index on `attendance` at all) | `staff_ops_service.py:31-45` |
| C3 | KYC review vs resubmit = whole-subdoc lost update ("verified" can attach to documents nobody reviewed) | `staff_kyc_service.py:65-110` |
| C4 | `start_heading` not idempotent: retry double-deducts inventory, rewrites penalty | `booking_service.py:1725-1730` |
| C5 | Captain can stack N pending withdrawals each individually ≤ balance | no partial-unique index |
| C6 | Google sign-in can insert `email: None` → duplicate-key 500 on the second no-email account | `google_auth_service.py:89-100` |
| C7 | `notify()` failures swallowed twice — a Meta outage silently stops ALL customer messages with zero logging | `notification_service.py:60-67` |
| C8 | Soft-deleted docs still writable (`update_by_id` doesn't filter `is_deleted`) | `base_repository.py:91-110` |
| C9 | `_sweep_holds` caps at 200/pass with no drain loop — hold burst permanently over-reserves slots | `booking_service.py:1040` |
| C10 | Attendance timestamps stored as ISO strings (only place in codebase); malformed value silently drops `worked_minutes` | `staff_ops_service.py:41,62` |
| C11 | 5 computed timestamps missing from `COMPUTED_INSTANT_KEYS` (phone_verified_at, submitted_at…) — the classic 5.5-hour display bug class | `utils/serializers.py:38-79` |
| C12 | Staff who message the business WhatsApp number get dropped into the CUSTOMER bot FSM under their staff account | `whatsapp_bot_service.py:204` |
| C13 | Captain `total_earnings` KPI sums `total_amount` (customer gross), not `captain_earning` — earnings page shows the wrong number | `staff_directory_service.py:101` |
| C14 | `_avg_heading_punctuality` ignores the date-range filter (all-time always) | `staff_directory_service.py:192` |

---

## D. BUILT BUT UNREACHABLE — backend features with no UI (13)

| Feature | Backend | Impact |
|---|---|---|
| **Leave approval** | `staff_ops_routes.py:48,58` | **Broken two-sided workflow: captains file leave nobody can ever approve.** Also: approved leave has zero effect — captains on leave are still assignable |
| **Rebook / "book again"** | `booking_routes.py:183` | Full repeat-booking flow exists; no button on MyBookings/BookingDetail |
| Vehicle EDIT + Address EDIT | `profile_routes.py:44,77` | Customers must delete & re-add to fix a typo; can't change default |
| FAQ CRUD | `content_routes.py:29-41` | Landing shows hardcoded fallback FAQs forever |
| Testimonial CRUD | `content_routes.py:52-62` | Landing permanently shows 3 hardcoded fake reviews; the code even references an admin route that doesn't exist |
| Admin wallet view + manual adjust | `wallet_routes.py:24,64` | Admin approves withdrawals blind |
| Inventory adjust-with-reason | `inventory_routes.py:45` | Managers overwrite raw quantities instead — no audit trail |
| Category edit/delete | `catalog_routes.py:32,37` | Categories are create-only |
| Captain inventory view | `inventory_routes.py:11` | No captain stock page |
| Admin cross-center bookings + all-subscriptions list | `booking_routes.py:82`, `subscription_routes.py:79` | Admin oversight forced center-by-center |
| Settings key/value store | `content_routes.py:68-78` | No UI |
| WhatsApp template bootstrap | `whatsapp_crm_routes.py:220` | No button |
| Plan detail endpoint / user detail drill-down / review `include_deleted` moderation view | various | No consumers |

Plus **20 dead API client functions** in `frontend/src/api/*` (each maps to one of the above
or superseded flows) — full list in the wiring matrix (agent output archived in this report's
source session).

---

## E. FRONTEND DEAD-ENDS & HALF-BUILT UI

### E1. The big one: Admin Homepage Settings mostly does nothing
`AdminHomepageSettingsPage` saves 7 fields; **only `hero_badge_text` reaches a rendered
page.** Headline/subtext/banner/featured-service/featured-combos feed components
(`Hero.tsx`, `FeatureStrip.tsx`) that are imported nowhere. Admin edits → Save → success
toast → zero visible change. The real headline is hardcoded in `LandingSections.tsx:507`.

### E2. Dead links (public site)
- Footer: Careers, Contact, FAQ, Terms, Privacy Policy all `href="#"` (`PublicFooter.tsx:81-91`); all 4 social icons `href="#"`; Services/Plans columns all point at the same bare pages; "Corporate Plans" doesn't exist.
- "Remember me" checkbox on login is decorative (`LoginPage.tsx:18`).
- No privacy-policy page exists at all (also a Meta App-Live requirement — see §H).

### E3. Dead code (~2,400 lines)
- `LandingSections.tsx` (3,043 lines): only `LandingHero` used; 13 dead exported sections.
- 8 more zero-import components (`Hero`, `FeatureStrip`, `PlansSection`, `WhyChooseUs`, `AboutFaqContact`, `TestimonialsAndStats`, `AreaAutocomplete`, `MapAddressPicker`).

### E4. Silent failures — mutations with NO onError (user clicks, nothing happens)
Subscription cancel; notifications mark-read; KPI settings save; admin user
suspend/delete; coupon toggle/delete; service toggle/delete; inventory delete; queue
resolve-issue & priority; captain mark-urgent; WhatsApp template sync. Plus: booking
cancel/reschedule errors render BEHIND the open modal (`BookingDetailPage.tsx:351`);
several pages share one error string across different modals without clearing it.

### E5. Broken flows / stuck states
- **`GuestOnlyRoute` sends every logged-in user to `/app`** regardless of role → staff clicking Login triple-bounce to the landing page (`ProtectedRoute.tsx:37`); Google sign-in hardcodes `/app` too (`GoogleSignInButton.tsx:51`); deep-link `state.from` captured then ignored (`LoginPage.tsx:29`).
- `/manager/bookings/:id` and `/admin/bookings/:id` render the CUSTOMER detail page (read-only for staff); a stale comment routes around a route that actually exists.
- **MandatoryGates modal has no escape** — if set-password/OTP keeps failing, the logout button is underneath the z-50 modal; only localStorage clearing escapes.
- **Guest-checkout accounts can never set a password from the portal** (random password + change-password demands the current one; no forgot-password link inside the app); ThankYouPage tells guests to "Return to login" against a 16-char random password.
- Manager with no `service_center_id`: infinite spinner on KPI page; empty-state CTAs that post `service_center_id: ""` and fail.
- Guest wizard: **no coupon field** (admin coupons unusable by guests), no notes, no alternate contact; shows full price while promising "first-wash offer applied automatically" (charged ≠ shown). Manager flow also has no coupon input.
- Customer support threads are one-way — customer can read staff replies but never respond (`SupportPage.tsx:131`).
- Landmark typed on a saved address is silently dropped (`NewBookingPage.tsx:727`); `line2` posted always-empty (`AddressesPage.tsx:13`).
- No confirmation dialog on: Cancel plan, Delete review, Log out (useConfirm exists, unused here).
- Pagination exists in only 4 of ~35 list views — everything else caps at 30–100 rows and client-side filters silently miss the rest.
- Coverage-leads and contact-messages admin pages are read-only (no "contacted" status, no tel: links).
- `VITE_WHATSAPP_NUMBER` read but absent from `.env.example` — misconfigured deploy silently sends WhatsApp CTAs to a hardcoded fallback number.

---

## F. DEAD PLUMBING (config / fields / enums)

| Item | Reality |
|---|---|
| `GOOGLE_OAUTH_CLIENT_SECRET` | Live secret in `.env` for a setting **no code reads** |
| `APP_ENV` | Gates nothing — only `DEBUG` matters; setting `APP_ENV=production` alone still exposes /api/docs |
| `referral_code` / `referred_by` | Generated on every signup, **read by nothing** (no lookup, no reward, no form field) — the referral-rate KPI is structurally always 0 |
| `auto_renew` on subscriptions | Accepted, stored, **nothing renews anything**; no expiry sweep either — untouched subs stay "active" past end_date until lazily read |
| `equipment_used` | Frontend hardcodes `[]` — inventory-per-job deduction is a live no-op (known deferral) |
| `gender`, `date_of_birth` | Accepted via API, no UI, never read — PII collected for nothing |
| `Complaint.attachments`, `Testimonial.customer_image`, `Review.edit_count/original_review`, `CaptainLocation.accuracy_m`, `created_by/updated_by` | Written (or declared) and never read |
| Enums never produced | `CaptainAvailability` (whole enum), `AttendanceStatus.ABSENT/HALF_DAY/ON_LEAVE`, `SubscriptionStatus.PAUSED` (frontend types it!), `NotificationType.PROMOTION/SUBSCRIPTION`, `PaymentStatus.FAILED/REFUNDED`, `PaymentMethod.ONLINE_PLACEHOLDER` |
| `BookingPriority` | Settable, displayed — but **never used in any sort/query/assignment logic**; a label with no consequence |
| Attendance/leave module | A record-keeping island: eligible-captains never consults leave or attendance |
| MSG91 widget | **PLATFORM_AUDIT said "disabled" — it is actually ENABLED in the current .env** (all 3 keys set): the OTP flow runs through verify.msg91.com incl. a 3rd-party script on the public wizard. Decide deliberately: keep or turn off |
| Model/storage drift | attendance & leave dates stored as ISO strings while models declare `date` |

---

## G. SCALABILITY & ARCHITECTURE

### G1. Missing indexes on HOT paths (full collection scans today)
- `bookings.vehicle_registration_number` and `bookings.customer_phone` — scanned on **every booking price check** (first-time-offer fraud check) (`booking_repository.py:39-45`)
- `bookings.vehicle_id` / `bookings.address_id` (delete guards); `service_centers.location.service_pincodes` (the pincode dispatch! — while a dead index sits on `location.pincode`)
- `attendance` (no indexes), `leave_requests` (none), `coupon_usages` (none — hit on every coupon apply), `otp_requests.identifier` (none — hit on every OTP, and **no TTL: grows forever**), `reviews.customer_id`, `complaints.booking_id`
- ~8 compound-sort gaps, worst: `notifications.(user_id, is_read, created_at)` — **polled every 30s by every logged-in user**

### G2. Unbounded queries
- `kpi_service.py:323` loads the **entire bookings collection**; `analytics_service` scans **all reviews twice per dashboard load**; captain performance with no date filter loads a captain's whole history; center-subscribers endpoint loads every subscription booking ever + 3-query N+1 per customer
- **`whatsapp_outbox`/`whatsapp_inbox` grow forever** (row per message both providers) and the CRM analytics aggregates the whole collection + a 600-query N+1
- Zombie bookings: unassigned/not-reached sweeps have **no upper time bound** — abandoned pending bookings are re-fetched and re-parsed every 60s forever

### G3. N+1s (top offenders)
1. KPI captains section: 4 queries × ALL captains (uncapped — the "200 cap" is a different path)
2. left-site sweep: 2 lookups × in-service bookings **every 60s** (batch with `find_by_ids`)
3. `available_slots`: one query per slot, **polled every 60s per open SlotPicker, public**
4. `get_policy()` uncached — read per call, 5×/sweep-cycle + every slot request

### G4. Single-worker ceiling (documented, but inventory of what breaks at `--workers 4`)
- Rate limiter is per-process (limits multiply by N) → move to proxy zones or Mongo
- WebSocket registry is per-process → partial broadcasts, frozen live maps → Redis pub/sub or change streams when scaling
- `create_indexes()` races itself (TTL drop-and-recreate window with NO index); employee-id backfill burns counter values across workers
- Transactions require a **replica set** — standalone mongod breaks booking creation entirely

### G5. Startup blocks boot
74 sequential index round-trips (~7s on Atlas RTT; genuine index BUILDS on first big boot
→ health-probe kills), TTL rebuild, employee-id backfill — all before first request.
Move behind the existing Mongo lease as a background task.

### G6. WebSocket auth gaps
- JWT in the query string (lands in proxy logs/history) — use a short-lived ticket
- Token checked once at handshake — socket outlives expiry/role change/suspension indefinitely
- No rate limit on subscribe frames (each `booking:` subscribe = a DB read)

### G7. Frontend bundle — one-line bug
**`BookingQueuePage` is the only eagerly-imported manager page (`App.tsx:42`)** and it
drags **Leaflet (~145KB + CSS) into the entry chunk — every anonymous landing-page
visitor downloads a manager-only map library.** Fix = make it `lazy()` like its siblings.
Then: manualChunks vendor split, lazy the 10 customer pages (all statically imported today).

---

## H. DEPLOYMENT / GO-LIVE STILL OPEN (from GO_LIVE.md, verified against code)

- No `Dockerfile` / `docker-compose.yml` / `Caddyfile` exist yet
- `.env` still dev: `DEBUG=true` (docs public), localhost CORS, localhost `PUBLIC_BASE_URL`, empty `WHATSAPP_APP_SECRET` (= A2!), temp 24h Meta token
- **`WHATSAPP_OTP_TEMPLATE_NAME` + `WHATSAPP_TEMP_PASSWORD_TEMPLATE_NAME` are EMPTY → a brand-new customer's first OTP does not deliver** (awaits Meta business verification)
- No privacy-policy/terms pages (Meta App-Live requirement); testimonials unseeded
- Maps key still IP-restricted to the dev machine; no quota caps set (PLATFORM_AUDIT #1)
- No client-side photo compression yet (PLATFORM_AUDIT #2)
- Real service zones not drawn (test zones in dev DB)

## I. DELIBERATE DEFERRALS (documented, not bugs)
Cancellation charge tiers (policy page only, unenforced — user decision) · supplies/
consumables reconciliation · attendance selfie · shift/roster · payment gateway (see A3
— its absence is now a security issue via wallet top-up, not just a missing feature) ·
M2 KPI caching (now graduated into §G2) · distance-based pricing decision.

---

## J. PRIORITIZED FIX PLAN (proposed next steps)

**P0 — Security lockdown (do first, ~1 day):**
A1 subscription ownership check · A5 notification ownership · A2 set `WHATSAPP_APP_SECRET`
+ fail-closed when empty in production · A6 fix rate-limit key + add missing OTP rules ·
B1 consume OTP on use · B2 bump token_version on reset · A3 disable/guard wallet top-up
until a gateway exists · A4 clamp totals ≥ 0 · B4 refuse boot on default JWT secret when
DEBUG=false · B8 add `ensure_own_center` to the seven unscoped manager surfaces.

**P1 — Broken workflows users hit (~2 days):**
Manager leave-approval UI (+ leave affects assignability) · vehicle/address edit ·
role-aware login/Google redirects + honor `state.from` · guest coupon field + honest
first-wash price · staff booking-detail routes → proper staff view · MandatoryGates
escape hatch + guest password recovery path · onError on all silent mutations ·
confirmation dialogs · customer reply on support threads · homepage-settings: wire or
remove the 6 dead fields · footer legal pages (privacy/terms — also Meta requirement).

**P2 — Money-path & race hardening (~1–2 days):**
B5 penalty clamp · B6 guarded cancel/reschedule (update_if) · B9 withdrawal atomic
review + enum status · C1/C2 unique indexes · C3 KYC versioned update · B7 validate hold
slot keys · C9 drain holds loop · B10 streaming upload size check · C7 log notify failures.

**P3 — Performance & scale (~2 days):**
G1 index pack (one migration in `create_indexes`) · G7 lazy BookingQueuePage (one line)
+ vendor split · G3 batch the sweep/N+1 hotspots + cache `get_policy` · G2 time-bound the
zombie sweeps + TTL on `whatsapp_outbox/inbox` + `otp_requests` · G5 background startup ·
subscription expiry sweep (fixes `auto_renew`/status drift too).

**P4 — Cleanup (~1 day):**
Delete 20 dead API functions + ~2,400 dead frontend lines + dead config/enums/fields ·
fix C13 earnings figure + C14 date filter · decide MSG91 widget on/off · admin gap pages
worth keeping (wallet adjust, category edit, cross-center lists) · drop the rest.

*Estimated total: ~7–8 working days to a genuinely hardened, scalable v1.*
