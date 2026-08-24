# CleanRide — System Context & Handoff

**Read this first if you're picking up this codebase.** It explains what CleanRide is,
how it's built, what's actually implemented, what's missing, and the naming/architecture
conventions the existing code follows — so new work fits in instead of fighting the
grain. Pair this with `KNOWN_ISSUES.md` in the same folder, which lists specific bugs
found/fixed and things that were never verified against a live server.

**2026-08-19 note:** a large production-hardening pass landed after this document was
first written — systemic IST-timezone fix, manager-books-for-a-customer, a real captain-
conflict scheduling bug fix, captain self-escalation, wash-duration KPI capture, a captain-
dashboard rebuild with real customer/vehicle/address data, toast notifications, map
reverse-geocoding/GPS auto-fill, and a real captain availability picker for managers. Some
claims below (especially in §8/§9) predate that pass and are now stale in places — see
`KNOWN_ISSUES.md` §0.1 for the full accounting of what changed and why.

---

## 1. What this is, in one paragraph

CleanRide is a doorstep car-wash marketplace: **customers** book a wash for their vehicle
at their address; a **manager** at the nearest **service center** assigns a **captain**
(the field worker who actually washes the car); the captain travels, verifies they're at
the right vehicle, photographs before/after with GPS proof, and completes the job; the
platform takes a cut and pays the captain from an internal wallet. Think "Swiggy/Zomato/
Blinkit, but for car washing" — that's the explicit reference model this was built
against. An **admin** controls everything: pricing, services, combos, subscription plans,
homepage merchandising, service center coverage areas, and platform-wide oversight.

---

## 2. Tech stack

- **Backend:** FastAPI (Python), MongoDB via Motor (async driver), Pydantic v2 for
  schemas/validation, JWT auth.
- **Frontend:** React 19 + TypeScript + Vite, TanStack Query for data fetching, React
  Router v6, Tailwind (via CSS custom properties / design tokens), Framer Motion for
  landing-page animation, Leaflet + OpenStreetMap for maps (no paid API key required).
- **No Redis, no Celery, no background job queue.** Deliberately — this was an explicit
  constraint from the original PRD ("keep it dependency-free"). The one background task
  (30-min-before reminders, captain-not-reached detection, stuck-captain detection) runs
  as a simple `asyncio` loop inside the FastAPI process, sweeping every 60 seconds.

---

## 3. Repository layout

```
backend/
  app/
    main.py                  — FastAPI app, router registration, the background sweep loop
    seed.py                  — creates demo accounts + sample data
    core/                    — config, database connection, JWT/dependencies, exceptions, response helpers
    models/                  — Pydantic models = MongoDB document shapes (one file per domain)
    schemas/                 — Pydantic request/response schemas (what the API actually accepts/returns)
    repositories/            — data access layer, one class per collection, all extend BaseRepository
    services/                — business logic layer — this is where the real rules live
    controllers/             — thin glue between routes and services (formats responses, logs audit trail)
    routes/v1/                — FastAPI routers, one file per domain, all mounted under /api/v1
    utils/                   — geo (haversine), timezone (IST helpers), text (slugify), serializers

frontend/
  src/
    api/                     — one file per domain, thin wrappers around the shared apiClient
    types/index.ts           — every TypeScript interface mirroring a backend model — SOURCE OF TRUTH for shapes
    components/ui/           — the design-system primitives (Button, Card, Modal, DataTable, etc.)
    components/shared/       — landing-page sections, PhotoCapture, MapPicker — reusable, not role-specific
    components/layout/       — DashboardShell (the role-agnostic authenticated shell), PublicNavbar/Footer
    pages/{admin,manager,captain,customer,auth,public,shared}/  — one folder per role/area
    App.tsx                  — all routing, nested by role under /admin, /manager, /captain, /app (customer)
    context/AuthContext.tsx  — current user + auth actions
    lib/                     — api-client (axios wrapper + error handling), date formatting, cn() class helper
```

**Pattern to follow when adding a feature:** model → schema → repository → service →
controller → route → register in `main.py` → frontend type → frontend api/ file → page/
component → route in `App.tsx` → nav item in the relevant `*Layout.tsx`. Every existing
feature in this codebase follows exactly this chain; skipping a layer is how things get
inconsistent.

---

## 4. Core entities and what they're called (don't rename these — a lot depends on exact field names)

| Entity | Backend model | Key fields worth knowing |
|---|---|---|
| User | `UserModel` | `role` (customer/captain/manager/admin), `status` (active/inactive/suspended/pending), `service_center_id` (for captains/managers) |
| Vehicle | `VehicleModel` | `registration_number` (normalized uppercase, alnum-only for comparison), `vehicle_type` (car/suv/bike/luxury — see Known Issues re: hardcoded enum) |
| Address | `AddressModel` | `latitude`/`longitude` (nullable — falls back to pincode-only dispatch if unset) |
| ServiceCenter | `ServiceCenterModel` | `location.{latitude,longitude,radius_km}` drives haversine-based auto-dispatch; falls back to pincode matching if coordinates unset |
| Category / Service | `CategoryModel` / `ServiceModel` | `Service.price`/`discounted_price` are the flat defaults; `vehicle_type_prices`/`vehicle_type_discounted_prices` are optional per-vehicle-type overrides |
| ComboOffer | `ComboOfferModel` | Bundles `service_ids` at its own price — a real sellable item, not a discount rule |
| SubscriptionPlan / UserSubscription | `SubscriptionPlanModel` / `UserSubscriptionModel` | `category_quotas: {category_id: count}` defines what's covered; `UserSubscription.remaining_by_category` tracks usage |
| Booking | `BookingModel` | The central entity — see §6 below, it has the most fields and the most business logic |
| CaptainWalletModel / WalletTransactionModel / WithdrawalRequestModel | wallet system | Captain earnings ledger — see §7 |

**Human-readable IDs:** `Booking.booking_number` is `BK` + 6-digit IST date + 5 random
digits (e.g. `BK26082012345`). No equivalent business-ID exists yet for captains,
customers, or managers — they're referenced by MongoDB ObjectId string everywhere. If a
spec needs `CAP-000123`-style IDs, that's unimplemented (see Known Issues).

---

## 5. Roles and what each one can do (current implementation — simple role checks, not granular permissions)

- **Customer:** browse services/combos/plans, book (with vehicle+address+time), track a
  booking live, rate a completed booking, manage vehicles/addresses/subscriptions.
- **Captain:** see assigned jobs, start heading out (geo-tagged, time-gated), verify the
  vehicle registration on arrival (typed, not a checkbox), capture before/after photos
  (camera-only, geo-tagged, geofence-checked), release a job back to pending if there's a
  problem, view wallet/earnings, request withdrawals, check in/out for attendance.
- **Manager:** scoped to one service center. Assign/reassign captains, view the booking
  queue with issue flags, resolve or remediate flagged bookings (reschedule, reassign),
  add captain accounts, view captain reviews, manage center inventory, view subscribers
  who've used a plan at their center, handle complaints.
- **Admin:** everything. Full CRUD on services/categories/combos/subscription
  plans/coupons/service centers/users, pricing & commission config, booking-policy config
  (operating hours, lead time, late-start grace period, geofence radius), homepage
  merchandising, platform-wide booking/subscription/wallet oversight, audit log, contact
  message inbox.

---

## 6. The booking lifecycle — this is the heart of the system

### Status flow
```
pending -> assigned -> captain_on_the_way -> service_started -> completed
                                                                (or cancelled at various points)
pending/assigned -> rescheduled -> back to pending/assigned
```

### Step by step, with the actual gates enforced

1. **Customer books.** Picks service(s) or a combo, a vehicle, an address, and a direct
   time (not a slot-grid — a time input constrained to the admin's operating window,
   minimum lead time, and slot granularity, all admin-configurable via
   `booking-policy`). Server-side validation re-checks all of this — never trust the
   client. Price is resolved per vehicle type, and the "first wash" discounted price only
   applies if **neither this vehicle's registration number nor this customer's phone
   number has ever appeared on a non-cancelled booking anywhere on the platform** — this
   is the core fraud-prevention rule (checked in `BookingService.create_booking`,
   `_resolve_price` + the `exists_for_registration`/`exists_for_phone` repo checks).
   Duration is computed from the actual service(s), not hardcoded.

2. **Manager assigns a captain.** Backend checks the captain isn't already booked in an
   overlapping window (including a travel buffer on both sides — admin-configurable,
   default 15 min) — this is a hard concurrency lock, not a warning.

3. **Captain starts heading out.** Can only do this within a window around the slot:
   30 minutes before the slot start, up to (slot end + a grace period, default 30 min,
   admin-configurable). **When they start matters and affects their pay**: starting
   early or within the slot itself costs nothing; starting in the late-grace window costs
   25% of their service fee (the difference shifts to the platform, customer price is
   unaffected); starting after the grace period expires entirely costs 50%. Either late
   case flags the booking (`issue_flag = "captain_delay"`) and notifies the manager. All
   of this is computed in IST, not UTC — see Known Issues for why that distinction
   mattered.

4. **Captain verifies the vehicle.** A required step between heading-out and the
   before-photo — the captain types the registration number they actually see on the
   car. Mismatch blocks progress (they must release the job instead of forcing through).
   This is the "washed the wrong car" fraud prevention.

5. **Captain captures before-photo, service starts.** Camera-only (no gallery picker —
   enforced via `capture="environment"` on the file input), GPS-tagged. If the GPS is
   further than the admin-configured geofence radius (default 300m) from the booking
   address, the photo is still accepted but the booking gets flagged for manager review
   — never silently rejected, since addresses aren't always pinpoint-accurate.

6. **Captain captures after-photo, booking completes.** Same camera/geo rules. Captain's
   final earning and the platform's cut were computed at booking creation
   (`PricingService.calculate_split`) and only adjusted downward by the lateness penalty
   above — never recalculated from scratch at completion.

7. **If something goes wrong:** a background sweep (part of the same 60-second loop that
   sends pre-slot reminders) auto-detects two failure modes without requiring the
   customer or captain to report anything: a booking still `assigned` (captain never
   started heading out) whose entire window has expired gets flagged
   `captain_not_reached`; a booking stuck `captain_on_the_way` for 90+ minutes without a
   before-photo gets flagged `captain_delay`. Either way, the manager is notified and can
   reassign a different captain, reschedule to a different time (this clears the captain
   and resets to `rescheduled`), or just mark the issue resolved with a note if it turned
   out to be nothing.

### Money flow
`Booking.subtotal/discount_amount/total_amount` is what the customer pays.
`captain_travel_pay + captain_service_pay = captain_earning`; `platform_earning` is the
rest. These are split at creation time via `PricingService.calculate_split` (a per-km
rate x distance for travel pay, plus a flat or service-specific captain fee for service
pay), then only ever adjusted by the lateness penalty. Cash bookings **debit** the
platform's share from the captain's wallet (since the captain physically holds the cash);
online/subscription bookings **credit** the captain's fee directly. This asymmetry is
intentional — check `backend/LOGIC_README.md` if it still exists in the repo for the
original worked example.

---

## 7. Wallet system

Every captain has a `CaptainWalletModel` with a `balance` and a `minimum_balance`
(default Rs.50). A captain below minimum balance **cannot be assigned new bookings** — this
is checked in `assign_captain`/`reassign_captain` via `WalletService.is_eligible_for_assignment`.
Every wallet-affecting event creates an immutable `WalletTransactionModel` (credit or
debit, with a `balance_after` snapshot). Captains can request withdrawals
(`WithdrawalRequestModel`, admin reviews and approves/rejects/marks-paid). No real
payment gateway is wired in — this is bookkeeping only, not actual money movement (see
Known Issues).

---

## 8. What's fully implemented (high confidence, code-reviewed thoroughly)

- Full auth (JWT), role-based routing, all four role dashboards
- Complete booking lifecycle as described in §6, including the fraud/trust core: camera-
  only photo capture, GPS geofencing, vehicle verification, captain concurrency locking,
  first-time-offer fraud binding, IST-correct time validation
- Full admin CRUD: services (with vehicle-type pricing + first-time pricing), categories,
  combo offers, subscription plans (category-quota based), coupons, service centers (with
  map-based coordinate picking), users, pricing/commission config, booking-policy config,
  homepage merchandising config, contact message inbox
- Manager: captain management (add/suspend/reactivate, view reviews), booking queue with
  issue-flag remediation, inventory (full CRUD, not just create), subscriber visibility
- Customer: booking flow with direct time input + map-based address pinning, vehicle/
  address management, subscriptions (browse/subscribe/cancel/upgrade), booking history
  with full transparency trail visible (before/after photos, GPS, timing, issue flags)
- Captain: job list with the full staged workflow, wallet/earnings with withdrawal
  requests, attendance check-in/out
- Landing page matching the target Figma design, fully wired to real backend data (no
  hardcoded prices/content — all admin-controlled)

## 9. What's missing or deferred (see `KNOWN_ISSUES.md` for full detail on each)

- Granular RBAC (currently role-based, not permission-based)
- Admin-configurable vehicle types (currently a hardcoded enum)
- A fuller industry-standard booking status machine (`no_show`, `disputed`,
  `captain_confirmed` as real statuses rather than the current issue-flag side-channel)
- Idempotency keys on booking creation
- Formal invoice entity / line-item price snapshots
- Real payment gateway integration (Razorpay/Stripe/etc. — currently bookkeeping only)
- A paid geocoding provider (currently free Leaflet/OSM/Nominatim, rate-limited at scale)
- The master-prompt (237-section spec) gap comparison was started but not finished — redo
  it against current state if that's still a live requirement

## 10. How to actually run this

```bash
# Backend
cd backend
pip install -r requirements.txt
cp .env.example .env   # fill in MONGO_URI, JWT secret, etc.
python -m app.seed     # creates demo accounts -- see console output for credentials
uvicorn app.main:app --reload

# Frontend
cd frontend
npm install            # will install leaflet -- this is the one unverified new dependency
cp .env.example .env   # point VITE_API_URL at the backend
npm run dev
```

Demo accounts created by `seed.py` (check the script for current values, but as of this
session): `admin@doorstepvehiclecare.in` / `Admin@12345`,
`manager.indore@doorstepvehiclecare.in` / `Manager@12345`,
`captain.indore@doorstepvehiclecare.in` / `Captain@12345`.

---

## 11. A note on how this was built, for whoever picks it up next

The original build session happened in a sandbox with **no network access** — no live
server, no database, no package installation. Every piece of "verification" in that
session was static: reading the actual route/schema/model code and cross-checking field
names, tracing logic by hand, and running `py_compile` / unused-import scans. That caught
real bugs (see `KNOWN_ISSUES.md`), but it wasn't the same as watching the system run.

**That gap has since been closed (2026-08-19).** Backend and frontend were both actually
started against the live MongoDB Atlas cluster from `.env`, `npm run build` was run for
real, and a full booking lifecycle (customer books → manager assigns captain → captain
heads out → verifies vehicle → before/after photos → completes → customer rates) was
driven end-to-end over the real HTTP API and passed. Two real bugs turned up doing this —
an unauthenticated booking-details endpoint and a plaintext password in the connection-log
line — both fixed; see the top of the table in `KNOWN_ISSUES.md` §2 for details. The one
thing that pass didn't cover is the late-start captain penalty math firing under real
wall-clock timing (it only exercised the on-time/early path) — see `KNOWN_ISSUES.md` §5.
Treat the codebase as now proven correct by execution for the core lifecycle, not just by
code review — but still worth that one targeted late-start test before this goes further.
