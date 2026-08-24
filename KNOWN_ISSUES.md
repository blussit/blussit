# Known Issues & Open Items — CleanRide Platform

Written by the agent that did this build session. This is a plain accounting of what's
shaky, what's unverified, and what's deliberately deferred — so the next person (human
or agent) doesn't have to rediscover any of it the hard way.

---

## 0. UPDATE — 2026-08-19: this has now actually been run

Everything in §1 below was written when no command had ever touched a live server. That's
no longer true. Backend and frontend were both started for real against the live MongoDB
Atlas cluster in `.env`, and a full booking lifecycle was driven end-to-end over the real
HTTP API (register → login → add vehicle/address → create booking → admin assigns captain
→ captain heads out → verifies vehicle → before-photo → after-photo → completes → customer
rates). Results:

- `npm run build` — clean. Strict TypeScript (`noUnusedLocals`/`noUnusedParameters`) passes,
  and the Leaflet dependency (the doc's top named risk) causes no build issues.
- Backend boots, connects to the real Atlas cluster, serves `/api/v1` routes and
  `/api/docs`/`/api/openapi.json` normally.
- `python -m app.seed` runs clean and idempotent against the live DB.
- All 8 status transitions in one full booking lifecycle completed correctly, including
  the wallet-minimum-balance gate on captain assignment and the 30-minute heading-out
  window — both fired exactly as designed, not as bugs.
- **Two real bugs turned up doing this and are now fixed** — see the two new rows at the
  top of the table in §2 (`GET /bookings/{id}` had no auth at all; the Mongo URI including
  its password was logged in plaintext at INFO level on every boot).

This closes out the "never actually run" risk. The rest of this file (§1 as originally
written, §3, §4) is left intact below as the historical record of what was true before this
pass, plus what's still genuinely deferred.

---

## 0.1 UPDATE — 2026-08-19 (same day): Production booking-system pass

A large follow-up pass, prompted by real usage surfacing a gap between what this doc/
CONTEXT.md claimed was "fully implemented" and what was actually true. Investigated with
five parallel deep-dive audits first (timezone handling, map/location integration, admin
sidebar layout, booking-assignment/conflict logic, captain dashboard + notifications +
manager analytics), then implemented and live-verified end-to-end.

**Systemic IST/timezone fix.** Every computed timestamp (`heading_at`, `captured_at`,
`created_at`/`updated_at` everywhere, subscription dates, coupon dates, etc.) was being
returned by the API with no timezone offset — silently wrong by up to 5.5 hours once a
frontend parsed it. Root cause: MongoDB/Motor hands back computed timestamps as naive
datetimes on read (no `tz_aware` on the client), and `serialize_value`
(`app/utils/serializers.py`) called `.isoformat()` on them directly with no offset. Fixed
at the serialization boundary with a `COMPUTED_INSTANT_KEYS` name-based allowlist that runs
`from_stored()` before `.isoformat()` — the one place to extend for any future computed
timestamp, instead of the error-prone "remember to call `from_stored()` at every write
site" pattern that caused this exact bug once already (see §2 below). Also fixed
`UserPublic.from_doc()` (bypasses the serializer entirely) and the coupon service's
hand-rolled `to_ist()`-style logic (coupon dates are actually `from_stored()`-category).
Frontend: `lib/date.ts` now pins `timeZone: "Asia/Kolkata"` explicitly (was relying on the
viewer's browser timezone); added a `todayIST()` helper and fixed several
`toISOString().split("T")[0]`-based "today" computations that were UTC-calendar-date, wrong
for the first ~5.5 hours of every IST day.

**Admin/manager/captain/customer sidebar overlap.** `DashboardShell.tsx`'s `<aside>` had no
scroll container around its nav list and an absolutely-positioned logout button that didn't
reserve space — on any viewport short enough (or nav list long enough, admin has 14 items),
nav items rendered underneath/behind logout. Restructured to a proper flex column with a
`flex-1 min-h-0 overflow-y-auto` nav region and a normal-flow (`mt-auto`) footer.

**Manager can book on behalf of a customer (new or existing).** New endpoints:
`GET /crm/customers/search?phone=`, `POST /auth/customers` (temp password +
`must_change_password` flag, forced password change enforced client-side via
`ProtectedRoute`), extended `GET /crm/customers/{id}/360` to include vehicles/addresses,
`POST /bookings/manager-create` (reuses `create_booking`'s pricing/fraud/scheduling logic
end-to-end, supports inline new-vehicle/new-address). New frontend:
`ManagerNewBookingPage.tsx`, a 4-step wizard.

**Captain-conflict assignment math — real bug, fixed.** `_captain_conflict` in
`booking_service.py` was padding the travel buffer onto both sides of both the existing and
candidate booking windows independently, silently requiring 2x the configured buffer as the
effective gap between two adjacent jobs (verified: a captain finishing a 40-min wash at
1:40 PM with a 15-min buffer couldn't be rebooked until 2:10 PM instead of the intended
1:55 PM). Fixed to apply the buffer once; verified against the worked example via the exact
production helper function.

**Captain self-escalation.** New `POST /bookings/{id}/report-risk` — a captain running long
on their current job can flag an upcoming (not-yet-started) booking as at-risk, notifying
the manager through the same channel as the automatic `captain_not_reached`/`captain_delay`
sweeps.

**Wash-duration KPI capture.** New `Booking.actual_duration_minutes`, computed and stored at
completion (before-photo → after-photo elapsed time) — feeds a future analytics dashboard.
Not yet surfaced anywhere except the captain's own completed-job cards.

**Captain dashboard rebuild.** The API only ever returned raw
`customer_id`/`vehicle_id`/`address_id`/`service_ids` — a captain had no way to see a real
customer name, phone, address, vehicle, or service type anywhere, and the card UI showed a
literal "Address on file — see booking details" placeholder with no actual address or
working link. Backend: `list_for_captain`/`get_booking` now denormalize customer
name/phone, a vehicle snapshot, a flattened address, and service/combo names onto every
booking (`BookingService._enrich_bookings`, batched via a new `BaseRepository.find_by_ids`
to avoid N+1). Frontend: extracted `components/captain/JobCard.tsx` showing all of the
above at a glance, plus an `issue_flag` warning badge and the new report-risk action. The
staged heading → verify-vehicle → before-photo → after-photo flow itself was already
correct and untouched.

**Notifications — toast popups + faster polling.** No WebSocket/SSE existed (deliberate,
per the "no extra infra" constraint) and the only "real-time" signal was a passive bell dot
polled every 30s. Built a first-party `ToastContext`/`ToastContainer` (reuses the existing
`framer-motion` dependency rather than adding a toast library), dropped the poll interval to
~7s, and added timestamp-diffing against a `localStorage`-persisted "last seen" watermark so
only genuinely new notifications toast (never floods on first login). The manager's booking
queue also gained its own `refetchInterval` (had none before) and a flagged-bookings banner
pinned above the table.

**Map/location auto-fill.** The map itself (Leaflet, `MapPicker.tsx`) was already correctly
wired everywhere it's used — no rendering bug found. What was genuinely missing: reverse-
geocoding a pin back to address text, and a "use my current location" button for customers
(existed only on the admin service-center page, and even there didn't fill address text).
Added both directly to `MapPicker.tsx` (`onAddressResolved` callback +
`showUseMyLocation` prop) so all three customer/manager call sites get it without
duplicating the geolocation/reverse-geocode logic. Reverse-geocoded guesses are shown as a
dismissible "Detected: ... [Use this]" suggestion — never silently overwrite what someone
already typed.

**Manager captain picker — availability visibility.** Was a bare `<select>` of names. New
`GET /bookings/{id}/eligible-captains` (reuses the fixed `_captain_conflict` server-side, so
the manager sees *why* a captain can't take a specific booking — busy until X, wallet
balance — instead of only finding out via a failed submit) and a new
`avg_heading_punctuality_minutes` aggregation in `staff_directory_service.py` (average
minutes early/late starting heading-out vs. the scheduled slot — chosen over daily
attendance check-in, since heading punctuality is what actually predicts "will this captain
show up on time for the job I'm about to assign"). New `components/manager/CaptainPicker.tsx`
replaces the `<select>` with per-captain rating, today's queue depth, punctuality, and a
greyed-out+reasoned state when ineligible for the specific booking being assigned.

**Bonus bug found and fixed while verifying the above:** a pre-existing duplicate-key crash
on the *second* phone-only user account ever created (`email: null` written explicitly into
every such document, and MongoDB's sparse unique index on `email` treats an explicit `null`
as a real indexed value, not an absent field — so the second such account always collided).
This was directly blocking the new manager-create-customer flow. Fixed in
`AuthService._strip_absent_contact_fields` — omit the key entirely rather than writing
`None`, across `register_customer`/`create_staff_account`/`create_customer_by_staff`.

**Explicitly deferred to Phase 2, not built this pass:** the manager analytics dashboard
(peak time-of-day, day-of-week, monthly trend charts) — the current manager dashboard is
still a bare 4-tile counter page, and the `/analytics/*` endpoints are still admin-only. The
`actual_duration_minutes` field this pass added is ready for that dashboard's aggregations
whenever it's built.

---

## 1. Never actually run — this is the big one

**No command in this entire build session was ever executed against a live server.**
The sandbox this was built in has no network access — no `npm install`, no `pip install`,
no MongoDB, no `npm run build`, no `npm run dev`. Every claim of correctness in this
codebase comes from:
- `python -m py_compile` on every backend file after every change (catches syntax errors
  and import typos, nothing else)
- Manual, line-by-line comparison of every frontend API call against the actual backend
  route signature and Pydantic schema
- A repo-wide regex scan for unused imports (since `tsconfig.app.json` has
  `noUnusedLocals`/`noUnusedParameters` on — confirmed by reading the file, not assumed)

**What this cannot catch:** runtime logic errors, race conditions, CSS/layout bugs,
actual MongoDB query behavior, whether the new `leaflet` dependency installs and renders
correctly, whether FastAPI's dependency injection wires together the way the code implies,
and anything that only shows up when real data flows through the system.

**Action before trusting this in any real environment:**
```bash
cd backend && pip install -r requirements.txt && python -m app.seed && uvicorn app.main:app --reload
cd frontend && npm install && npm run build && npm run dev
```
Run both, click through a full booking lifecycle (customer books → manager assigns →
captain heads out → verifies vehicle → before-photo → after-photo → completes →
customer rates), and report back anything that breaks.

---

## 2. Real bugs found and fixed during this session (documented so nobody "fixes" them back)

| Bug | Where | Fix |
|---|---|---|
| **`GET /bookings/{id}` had no authentication at all** — anyone who knew or guessed a booking's ObjectId could read the full record (customer identity, vehicle reg number, address, captain identity, pricing breakdown, captain/platform earnings, full status history) with no login, found while running the real lifecycle test on 2026-08-19 | `booking_routes.py`, `booking_controller.py`, `booking_service.py::get_booking_with_history` | Route now requires `get_current_user`; service checks the caller is the booking's customer, its assigned captain, or a manager/admin, matching every other `/bookings/*` route. Verified live: unauthenticated request → 401, admin → 200. |
| Mongo connection string, including its password, logged in plaintext at INFO level on every backend boot | `core/database.py::connect_to_mongo` | Added `_redact_uri()`; the log line now shows `mongodb+srv://user:***@host/...` instead of the real password. |
| Booking slot times tagged as UTC instead of IST | `booking_service._slot_start_datetime` | Now tags naive scheduled_date/slot as IST via `to_ist()` — was silently off by 5.5 hours on every scheduling check |
| Captain attendance "today" used server date (usually UTC) | `staff_ops_service.py` | Now uses `now_ist().date()` |
| Coupon validity window compared in UTC | `coupon_service.py` | Now compared in IST |
| Analytics "today"/"this month" boundaries in UTC | `analytics_service.py` | Now computed from `now_ist()`; booking-trends Mongo aggregation now buckets by `+05:30` |
| `PUT /pricing-config` returned the raw MongoDB settings document (nested under `value`) while `GET` returned a flat shape | `pricing_routes.py` | Both now return the same flat `{per_km_rate, default_captain_service_fee}` shape |
| `GET /services` hardcoded `active_only=True` with no override — a deactivated service could never be seen again to reactivate it | `catalog_routes.py` | `active_only` is now a real query param, defaults to `True` |
| Manager could reassign a captain on a `captain_on_the_way` booking (I initially wrote the frontend gate wrong) | `BookingQueuePage.tsx` | Restricted to `assigned` status only, matching the backend's actual rule |
| `reschedule_booking` was customer-only, but the manager remediation flow needs it too | `booking_service.py` | Opened to managers/admins (captains explicitly blocked) |
| **Self-inflicted, caught before shipping:** I built a `to_ist()` helper that treats all naive datetimes as IST wall-clock, which is correct for user-entered fields (booking slot, coupon dates) but WRONG for computed timestamps that round-trip through MongoDB as UTC (`heading_at`, `captured_at`, etc.). I had used `to_ist(heading_at)` in the "stuck on the way" sweep before catching this. | `booking_service.py`, `utils/timezone.py` | Split into two helpers: `to_ist()` for naive user input, `from_stored()` for computed/aware-derived timestamps that Mongo has UTC-converted. Fixed the one misuse. **If you add new "how long has X been true" logic against a timestamp field, check which category it's in before picking a helper — this is an easy mistake to reintroduce.** |
| `AdminHomepageSettingsPage` called `setState` directly during render instead of in `useEffect` | frontend | Moved into `useEffect`, real risk of an infinite render loop otherwise |
| `catalog_repository.py` — a bad string edit clobbered the `ServiceRepository` class declaration while adding `ComboOfferRepository` | frontend/backend edit session | Caught by immediately re-viewing the file after the edit; fixed same turn |

---

## 3. Deliberately deferred — not bugs, but real gaps

- **Granular RBAC / permission system.** The system uses simple role checks
  (`require_admin`, `require_manager_or_admin`, `require_captain`, `require_customer`) —
  not a fine-grained permission model (e.g. `USER_VIEW`, `SERVICE_EDIT` as separate
  grantable permissions). If the eventual spec needs per-permission control rather than
  per-role, this is a real architectural gap, not a small addition.
- **Vehicle types are a hardcoded Python enum** (`car`/`suv`/`bike`/`luxury`), not an
  admin-manageable collection. Expanding this to admin CRUD (add/rename/disable vehicle
  types) touches the Service model, pricing matrices, booking forms, and vehicle
  registration everywhere — high blast radius. Not attempted this session; flagged as a
  known limitation rather than risked blind.
- **Booking status machine is simpler than a full industry state machine.** Current:
  `pending → assigned → captain_on_the_way → service_started → completed`, plus
  `cancelled`/`rescheduled`, plus the new `issue_flag` side-channel for problems. A fuller
  spec might want explicit `captain_confirmed`, `arrived`, `no_show`, `disputed` as actual
  status values rather than flags layered on top. The current approach is functionally
  complete for the lifecycle but not identical in shape to a maximal state-machine spec.
- **No idempotency keys** on booking creation or payment-confirmation endpoints. A
  double-submit (e.g. a flaky network retry) could theoretically create two bookings.
  Not implemented this session.
- **No formal Invoice entity.** Bookings carry `subtotal`/`discount_amount`/`total_amount`
  but there's no separate invoice number, PDF generation, or GST-style breakdown.
- **No service/price snapshot on the booking beyond what's already there.** A booking
  stores `service_ids` (references) plus the computed `subtotal`/`total_amount`, but not
  a line-item snapshot of each service's name/price *as it was at booking time*. If an
  admin later renames or deletes a service, historical bookings still resolve
  `service_ids` correctly (services are soft-deleted, not hard-deleted), but a
  line-item snapshot would be more robust for a real invoicing/audit requirement.
- **Maps integration uses free tooling by necessity, not by ideal choice.** Built with
  vanilla Leaflet + OpenStreetMap tiles + Nominatim search — zero cost, but Nominatim's
  usage policy caps it at roughly 1 request/second for shared/public use, which is fine
  for low volume and not fine at real scale. A paid geocoding provider (Google
  Places/Geocoding, Mapbox) would need an API key from you and a follow-up swap.
- **Contact form → backend is real, but nothing sends an actual email/SMS notification**
  to staff when a message arrives — it just saves to the database. There's an admin page
  to view submissions, but no push notification for a new one.
- **The master-prompt gap analysis (237-section spec) was started but not finished.**
  The comparison work identified several categories of gaps (business-ID formats like
  `CAP-000123`, subscription price-paid snapshots, daily-usage-limit rules on
  subscriptions, idempotency, a fuller status machine) before the conversation moved to
  the IST/timing feature request instead. That comparison should be redone or finished
  against the current codebase state — a lot has changed since it was started.

---

## 4. Known-shaky but not obviously broken

- The captain-lateness penalty logic assumes `captain_service_pay` and `captain_travel_pay`
  are both already populated on the booking (set at booking creation via
  `PricingService.calculate_split`). If a very old/seeded booking predates that field
  being populated, the penalty math will silently treat missing values as `0` rather than
  erroring — worth a defensive check if seed data or migrations are involved.
- The "captain not reached" and "stuck on the way" background sweeps run every 60 seconds
  in-process (no Redis/Celery, by design, per the original PRD's constraint). This means
  if the backend process restarts frequently or runs multiple instances without a lock,
  duplicate notifications are possible. Fine for a single-instance deployment; would need
  a proper job queue for horizontal scaling.
- `ComboOfferModel` doesn't have its own `captain_fee` field — combo bookings fall back to
  the first included service's captain fee (or the platform default) for commission
  calculation. This was a deliberate scope-limiting choice, not an oversight, but worth
  knowing if combo captain payouts look wrong.

---

## 5. What I'd verify first if I were picking this back up

1. ~~Run the actual `npm install && npm run build`...~~ **Done 2026-08-19** — clean, no
   Leaflet issues. See §0.
2. ~~Seed the database and walk one full booking lifecycle end to end...~~ **Done
   2026-08-19** — full lifecycle verified over the real API, on-time and early-start paths
   confirmed correct. **Still not verified live: the actual late-start penalty math**
   (25%/50% cuts) — the lifecycle run exercised the on-time/early path only, since forcing
   a deliberately-late captain start needs manipulating wall-clock timing or the booking
   slot deliberately. Worth doing as a targeted follow-up.
3. Re-run the master-prompt comparison against the current state — it's stale from
   several turns ago. Still open.
