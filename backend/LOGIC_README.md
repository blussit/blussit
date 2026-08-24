# Platform Logic Reference — Wallet, Pricing, Dispatch & Transparency

This document explains the core business logic added on top of the Phase 1
foundation, in plain terms, so you can verify it matches how you want the
business to run before going live.

---

## 1. Geo-based dispatch (nearest store, not all stores)

- Every **service center** now has `location.latitude`, `location.longitude`,
  and `location.radius_km` (default 6km, editable per center by admin).
- Every **address** has optional `latitude`/`longitude` (captured by the
  browser's geolocation API on the frontend when the customer adds an
  address, or pinned on a map — no paid API required for this part).
- When a customer books, `BookingService._resolve_service_center()`:
  1. If the address has coordinates, it scans all **active** service
     centers, computes the straight-line (haversine) distance to each, and
     keeps only the ones where `distance <= that center's radius_km`. It
     picks the **nearest** one.
  2. If the address has no coordinates yet (e.g. imported data), it falls
     back to exact pincode-list matching (`location.service_pincodes`).
  3. If nothing covers the address, the booking is rejected with a clear
     "not available in your area" message — it never silently assigns a
     far-away store.
- **Admin controls area coverage** entirely through the Service Center
  screens: create a center, set its lat/lng + radius, and set which
  **manager** (a `User` with `role=manager`) runs it via
  `service_center_id`. Reassigning a manager to a different store, or
  shrinking/growing a store's radius, immediately changes which future
  bookings route there — no code changes needed.
- This is intentionally dependency-free. If you later add a Google Maps or
  Mapbox key for driving-distance/ETA instead of straight-line distance,
  only `ServiceCenterRepository.find_nearest` needs to change — every
  caller stays the same.

## 2. Captain wallet (Rapido-style, never negative)

Collection: `captain_wallets` (one per captain) + an immutable
`wallet_transactions` ledger (every credit/debit/topup/withdrawal is
recorded with a running `balance_after`, so the full history is always
auditable).

Rules enforced in `WalletService`:

- **Minimum balance** (default ₹50, editable per captain by admin) — a
  manager **cannot assign** a booking to a captain whose wallet balance is
  below this minimum (`assign_captain` / `reassign_captain` both check
  `is_eligible_for_assignment` first and reject with a clear error if not).
- **Balance can never go negative** on a normal debit — `WalletService.debit`
  raises an error if it would. The one deliberate exception is the cash-
  booking settlement (see below), which is allowed to dip the balance below
  zero because the captain is already physically holding the customer's
  cash — see the worked example below for why that's still correct.
- Every transaction is typed: `credit`, `debit`, `topup`, `withdrawal`,
  `adjustment` (manual admin correction, fully audit-logged).

## 3. Pricing / commission split — the core money logic

Admin sets two global levers under **Admin → Pricing** (stored in the
`settings` collection, key `pricing_config`):

| Setting | Meaning | Default |
|---|---|---|
| `per_km_rate` | ₹ paid to the captain per km between the store and the customer | ₹5/km |
| `default_captain_service_fee` | Flat ₹ paid to the captain for doing the job | ₹40 |

Each **Service** can optionally override `captain_fee` (e.g. premium
detailing pays the captain more than a quick exterior wash). If unset, the
global default is used.

At booking time, `PricingService.calculate_split()` computes and **stores
on the booking itself** (so it never silently changes later even if admin
tweaks the global rate afterwards):

```
captain_travel_pay  = distance_km * per_km_rate
captain_service_pay = service.captain_fee OR default_captain_service_fee
captain_earning     = captain_travel_pay + captain_service_pay   (capped at the service price)
platform_earning    = service_price - captain_earning
```

### Worked example (matches the numbers from the spec)

Water-less wash = ₹300, customer is 10km from the assigned store,
`per_km_rate` = ₹5/km, `captain_fee` for this service = ₹40:

```
captain_travel_pay  = 10 * 5   = ₹50
captain_service_pay = ₹40
captain_earning     = ₹90
platform_earning    = 300 - 90 = ₹210
```

### Settlement — this is where payment method matters

This happens exactly once, at the "after photo / complete service" step
(`BookingService.capture_after_photo_and_complete`), using the numbers
already frozen on the booking:

- **Online payment** → the platform already holds the full ₹300 (captured
  by whatever payment gateway is wired in — Razorpay in Phase 2, or however
  you're currently taking payment). The platform then **CREDITS**
  `captain_earning` (₹90) into the captain's wallet. Platform keeps ₹210.

- **Cash payment** → the captain collects the full ₹300 in cash directly
  from the customer. The platform never touched that money, but the
  captain owes the platform its ₹210 share. So the platform **DEBITS**
  `platform_earning` (₹210) from the captain's wallet. The captain's ₹90
  stays with them as cash in hand (it's never added to the wallet, because
  they already physically have it). This debit is the one case allowed to
  push the wallet below zero/below minimum — that's expected and correct;
  it's exactly why the minimum-balance-to-get-assigned rule exists: a
  captain who keeps taking cash jobs without topping up will eventually be
  blocked from new assignments until they top up or withdraw less.

Every settlement is logged in `wallet_transactions` with the booking
number in the description, and the booking record itself stores
`captain_earning`, `platform_earning`, `distance_km`, and `wallet_settled`
— so admin, manager, and the captain can all see the exact same numbers
for any booking. Nothing is computed differently for different roles.

### Withdrawals

A captain requests a withdrawal (`POST /wallet/withdrawals`); it's created
as `pending`. Admin reviews it (`PUT /wallet/withdrawals/{id}/review`) —
approving or marking it `paid` debits the wallet at that point (kept
simple for Phase 1; a real bank-transfer integration would instead debit
only after the transfer succeeds).

## 4. Timing gate — captains can't start early

`BookingService.start_heading()` enforces that a captain can only mark
"heading to customer" starting **30 minutes before** the booked slot's
start time (`START_WINDOW_MINUTES`). If they try earlier, they get a clear
message with how many minutes remain. This uses the booking's own
`scheduled_date` + `scheduled_slot` — no external calendar/maps dependency.

The same 30-minute window drives the **captain reminder notification**: a
lightweight in-process background loop (`app/main.py`, `_reminder_loop`,
polling every 60 seconds — deliberately not Celery/Redis, which are Phase 2
per the original PRD) finds assigned bookings entering that window and
sends the captain a "get ready" notification exactly once
(`reminder_sent` flag prevents duplicates).

## 5. Full doorstep-visit transparency trail

Every stage of an on-site visit is captured on the booking with a
timestamp and, where relevant, GPS coordinates — visible identically to
the customer, the manager, and admin:

1. **Assigned** — manager (or admin) assigns a captain; blocked if the
   captain's wallet is below minimum.
2. **Heading out** (`POST /bookings/{id}/heading`) — captain selects which
   equipment they're taking from their store's inventory (quantities are
   deducted live), and their current GPS location + timestamp is recorded
   as `heading_at` / `heading_location`. Gated by the 30-minute window above.
3. **Before photo / service started** (`POST /bookings/{id}/before-photo`)
   — captain must submit a photo. The frontend enforces this comes from a
   **live camera capture only** (`capture="environment"` on the file
   input — there is no "choose from gallery" option), plus the device's
   GPS coordinates at that moment. Stored as `before_photo` with
   `image_url`, `latitude`, `longitude`, `captured_at`.
4. **After photo / completed** (`POST /bookings/{id}/after-photo`) — same
   camera-only + GPS requirement, stored as `after_photo`. This is also
   the moment the wallet settlement above happens.
5. Every status change is additionally appended to
   `booking_status_history` with who changed it and an optional note, so
   the full chronological trail (who left the store with what equipment,
   when they started, before/after photos, when it was paid) is always
   retrievable via `GET /bookings/{id}` (`status_history` field).

## 6. Cancellation & smooth reassignment

- **Customer cancels** — allowed any time before completion
  (`POST /bookings/{id}/cancel`), notifies both the customer and the
  assigned captain if any.
- **Captain cancels / becomes unavailable**
  (`POST /bookings/{id}/captain-cancel`) — the booking drops back to
  `pending`, the captain is recorded in `previous_captain_ids` (so you can
  see reassignment history), and the store's manager is immediately
  notified to reassign. No wallet impact, since nothing was settled yet.
- **Manager reassigns** (`POST /bookings/{id}/reassign-captain`) — same
  wallet-eligibility check as a fresh assignment.

## 7. Visibility — who sees what

- **Admin**: everything, everywhere — every booking, every captain's
  wallet and transaction history, every store's earnings, pending
  withdrawal requests, the global pricing config, and the full audit log
  of every sensitive action taken by any manager or admin.
- **Manager**: their own store's booking queue, their captains and their
  wallet balances (read-only — they can see a captain is below minimum
  and therefore can't be assigned, but only admin approves withdrawals or
  makes manual wallet adjustments), inventory, and complaints.
- **Captain**: their own jobs, wallet balance + full transaction ledger,
  withdrawal requests, and their own performance/earnings summary.
- **Customer**: their own bookings with the full transparency trail
  described above (captain name, every timestamp, before/after photos),
  plus the ability to leave a review once a booking is completed.

## 8. What still needs a frontend to match

The backend endpoints above are all live and tested (see `app/main.py` —
127 routes, 94 documented API paths at `/api/docs`), but the existing
frontend (built in the previous phase) still calls the **old** generic
`PATCH /bookings/{id}/status` endpoint, which has been replaced by the
explicit `heading` / `before-photo` / `after-photo` / `captain-cancel` /
`reassign-captain` endpoints described above. The frontend needs a
matching rebuild — new CleanRide-style theme, a direct booking modal flow,
dashboard-first routing after login, camera-capture components with
geolocation, wallet screens, and admin area/pricing management screens —
which is a substantial follow-up piece of work in its own right.
