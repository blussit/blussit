# CleanRide — Full Platform Plan
### Trust & fraud prevention, correct scheduling, complete admin control — built to run at national scale

This is the plan before the code. Every gap below was confirmed by reading the actual backend (not assumed) — see the "Evidence" line under each item. Sections 10–11 are the decisions I need from you and the order I'd build in.

---

## 1. The actual business requirement, restated

This isn't "a booking form that calls an API." It's a three-sided marketplace (customer / captain / operations) where money and trust move automatically, with zero manual reconciliation and zero room for a captain, a customer, or a manager to game the system. Every screen exists to either **move a booking forward safely** or **give ops full control over what customers see and pay**. If a feature doesn't serve one of those two goals, it doesn't belong yet.

## 2. Reference model — what Swiggy/Zomato/Blinkit get right that we should copy

| Pattern | Why it matters here |
|---|---|
| Map-first address capture (pin drag + autocomplete), not free-text fields | Free-text address is the #1 cause of misrouted deliveries; same risk here for dispatch |
| One active order per rider, hard-enforced | Prevents a rider double-booking themselves — directly maps to our captain concurrency gap |
| First-order discount bound to phone **and** device/payment identity, not just account | Prevents the "new account, same person" discount abuse — we extend this to vehicle registration number, which is even stronger since a car can't be re-registered on a whim |
| Combos/bundles are merchandising decisions made in an ops panel, not hardcoded in the app | Ops changes pricing and promos hourly without an app release — that's the model for our admin panel |
| Live status ladder visible to all three parties (customer/rider/ops) | We already have this (pending → assigned → on the way → started → completed) — good foundation, just needs the gaps below closed |
| Delivery partner wallet with payout requests | We already built this — keep it |

## 3. Trust & fraud prevention (your core ask — this is Phase 1)

### 3.1 Vehicle identity verification at service time
Captain types the registration number they see on the car as an explicit validation step before starting service — not a yes/no toggle. System compares it (normalized, case/space-insensitive) against the registration number on the booking. Mismatch blocks progress and surfaces a "wrong vehicle" flag to the manager instead of silently continuing.
**Why not photo-OCR:** OCR on plates is unreliable outdoors and adds a dependency for marginal benefit over a captain physically typing what they see — a captain intentionally scamming the system will lie either way, but honest mistakes (wrong house, similar car) get caught by requiring an exact type-match against a specific string, not a glance.

### 3.2 Camera-only photo capture — reconfirmed as a locked requirement
Already built (`capture="environment"`, no gallery picker). No stored file uploads. This stays as-is; flagging it here so it's documented as a security requirement, not just a UI choice.

### 3.3 GPS-verified geofencing on before/after photos
Currently we *capture* GPS with each photo but never *check* it against the booking address. Add a server-side check: before/after photo coordinates must be within a configurable radius (default 300m — service addresses aren't pinpoint-accurate) of the booking's address coordinates. Outside that radius, the photo is still saved (so nothing is lost) but the booking gets a `location_flagged: true` marker visible to the manager, rather than silently accepted as proof of service at the right place.
**Evidence:** haversine distance calculation already exists for dispatch (`_resolve_service_center`) — this reuses the same math, doesn't add a new dependency.

### 3.4 Captain concurrency lock + travel buffer
**Evidence:** `assign_captain`/`reassign_captain` currently check wallet balance only — no time-overlap check exists.
Fix: before assigning captain X to a booking at time T for duration D, check X's other active bookings (`assigned`/`captain_on_the_way`/`service_started`) for any window overlap, where each existing booking's "occupied window" = `[start − travel_buffer, start + duration + travel_buffer]`. Reject assignment (with a clear reason) if it overlaps. Default travel buffer: 15 minutes each side — open to your input on this number.

### 3.5 First-time-offer fraud binding
**Evidence:** confirmed via code read — there is currently **no** first-time-offer logic in the codebase at all; `discounted_price` is a static, always-on price, meaning every customer gets the discount on every order, forever. This is a real revenue leak today, not a hypothetical.
Fix: a service's first-time price only applies if **neither** the vehicle's registration number **nor** the customer's phone number has ever appeared on a completed or active booking before. Registration number is checked globally across the whole platform (not just this customer's account) — this is what stops "same car, new phone number" abuse. Phone number is checked the same way, so "same phone, new car" doesn't get a second free ride either. Both must be first-time for the offer to apply.

### 3.6 No status can be faked by anyone but the captain
Manager/admin can reassign or cancel, but cannot mark a job "heading/before-photo/after-photo/completed" themselves — those transitions stay captain-only, geo+photo gated, exactly as built. Documenting this as an explicit locked invariant so it doesn't get "convenienced away" later.

## 4. Booking & scheduling correctness

### 4.1 Per-service duration drives the slot, not a fixed 60 minutes
**Evidence:** `Service.duration_minutes` exists on the model today but is never read anywhere in booking creation — it's dead data.
Fix: booking's occupied window = `service.duration_minutes` (sum, if multiple services in one booking) — this is what feeds the captain concurrency check in §3.4 too. If admin sets Wax = 30 minutes, that booking occupies a 30-minute window, not 60.

### 4.2 Time selection — direct time input, not a slot grid
Per your spec: customer picks any time within the admin-configured daily window (default 9:00 AM–7:00 PM, admin-editable), snapped to the granularity admin sets (you said "1-hour window" for now), with a minimum lead time of 10 minutes from the current moment. No slot-grid picker — a time input constrained to that range.

### 4.3 IST timezone handling, explicit
**Evidence:** no timezone normalization currently — `scheduled_date`/`scheduled_slot` are stored as-given. Fix: all booking-time validation (past-date, lead-time, operating-window) computed in IST server-side, regardless of the customer device's local timezone, since this is an India-only service for now.

### 4.4 No past-date / past-time / immediate-slot bookings
**Evidence:** confirmed via code read — `create_booking` has zero date/time validation today. Fix: reject at the API layer (not just hide it in the UI) if the requested time is in the past, or inside the minimum lead time window.

### 4.5 Overlap handling — shipping your called scope for v1
Per your explicit note: **manager-facing warning, not a hard scheduler, for now.** Two different customers can technically request overlapping windows at the same center; we surface this to the manager at assignment time as a warning banner ("3 other bookings in this window") rather than blocking it outright, since the manager is the human in the loop who actually knows capacity. Documented here as an accepted v1 limitation, not an oversight — revisit with a real capacity/calendar engine post-launch if volume demands it.

## 5. Pricing engine overhaul

### 5.1 Vehicle-type-based pricing per service
**Evidence:** today `Service.price` is one flat number regardless of hatchback vs. SUV vs. luxury. Fix: admin sets a price (and optional discounted/first-time price) per vehicle type, per service — not a multiplier guess, an explicit number ops controls directly (a hatchback wash and a luxury SUV wash are priced completely differently in the real world, and ops needs exact control, not a formula).

### 5.2 Combo / bundle offers
Admin creates a combo as its own sellable entity (e.g. "Exterior + Interior + Foam Wax — ₹899") with its own price, not just a discount rule stacked on individual services. Shows on the homepage as a distinct card.

### 5.3 First-time-user offers, done right
Admin sets both the strike-through price and the offer price per service (e.g. ₹499 → ₹199). Eligibility gated by §3.5's fraud check, computed server-side at checkout — never trust a client-sent "is this my first order" flag.

### 5.4 Homepage merchandising, admin-controlled
Which services are "featured," which combo/offer banners show, what the homepage hero pricing card displays — all admin-toggleable, not hardcoded in the frontend (today the Hero component reads the first catalog service, which is a placeholder, not a real merchandising system).

### 5.5 Single source of truth
Every rupee anywhere in the frontend traces back to an admin-set value. No price constants left in frontend code once this phase ships.

## 6. Subscription lifecycle — currently a real gap, not just missing polish

**Evidence:** subscriptions today are "buy N generic service credits" — there's no concept of *which* services a plan covers.

- **Plan contents by category**: admin defines a plan as a set of quotas per service category (e.g. "4 normal washes + 1 deep clean + 1 foaming per month"), not one flat counter.
- **Redemption at booking time**: customer chooses "use my subscription" vs. pay per booking; system checks the right category quota, not a generic counter.
- **Visibility**: customer sees remaining quota broken down by category, not just "3 of 10 left." Manager/admin see who's subscribed at their center and usage-to-date. Admin sees platform-wide subscriber list.
- **Upgrade path**: mid-cycle upgrade to a higher plan with the unused portion credited — not currently possible at all (can only cancel and rebuy at full price today).
- **Renewal/expiry reminders**: notification on approaching expiry, matching the existing notification system already in place.

## 7. Location & maps — currently manual text entry

**Evidence:** address lat/lng are plain number inputs today; nothing geocodes an address or shows a map.

- Real address autocomplete + pin-drop needs a maps provider. This is the one item in this whole plan that needs a decision and (likely) a paid API key from you — see §10.
- Once wired, this feeds directly into the dispatch haversine math that already exists — no backend rework needed, just accurate input instead of hand-typed coordinates.

## 8. Manager panel — expand

- Captain CRUD: create exists (built last round); this phase adds edit and a real deactivate (currently only suspend/reactivate via the shared user-status field).
- **Captain review visibility**: per-captain aggregate rating + list of individual reviews — currently reviews exist in the data model but there's no screen showing them grouped by captain.
- Inventory: currently create + quantity-adjust + list only. Add edit (rename, change reorder level) and delete — right now a mis-entered item can't be fixed or removed.
- **Store-level subscriber list**: "who at this center has an active plan, what have they used" — currently invisible to managers entirely.

## 9. Admin panel — expand to genuinely complete control

- Vehicle-type price matrix per service (§5.1)
- Combo offers CRUD (§5.2)
- First-time offer configuration (§5.3)
- Subscription plans with per-category quotas (§6)
- Homepage merchandising controls (§5.4)
- Platform-wide "who's subscribed to what, usage-to-date" report — currently subscriptions exist per-customer but there's no admin rollup view
- Everything already built and confirmed working: services, categories, service centers (with map radius), coupons, users, pricing/commission config, wallet withdrawal review, audit log

## 10. Decisions I need from you before touching code

1. **Maps provider.** Google Places/Geocoding is the strongest fit for Indian addresses but costs money past a free tier and needs an API key from your Google Cloud account. Mapbox is a cheaper alternative with weaker India coverage. OSM/Nominatim is free but noticeably weaker address matching in India. Which do you want, and do you have (or can you generate) an API key? I can't wire this up without one.
2. **Payment gateway.** The platform currently tracks `payment_status` but there's no real payment collection — is this cash-only for launch, or do you have Razorpay/Stripe/other keys to wire in for real online payment + captain payouts?
3. **Travel buffer minutes** for the captain concurrency lock (§3.4) — I'll default to 15 minutes each side unless you specify otherwise.
4. **Geofence radius** for photo-location validation (§3.3) — defaulting to 300m unless you want tighter/looser.
5. Confirm the operating window default (9:00 AM–7:00 PM IST) and 10-minute minimum lead time — these become admin-editable settings, just confirming the shipped defaults match what you described.

## 11. Build order I'm proposing

**Phase 1 — fraud/trust core (highest risk if left open):** captain concurrency lock + travel buffer (§3.4), registration-number verification step in captain flow (§3.1), booking time validation — past/lead-time/operating-window (§4.2–4.4), first-time-offer fraud binding (§3.5), photo geofencing (§3.3).

**Phase 2 — pricing correctness:** per-service duration driving slots (§4.1), vehicle-type price matrix (§5.1), combo offers admin CRUD (§5.2), first-time-offer admin config (§5.3).

**Phase 3 — subscriptions done right:** category-based quotas, redemption logic, manager/admin visibility, upgrade path (§6).

**Phase 4 — maps + merchandising:** address autocomplete/pin-drop (pending your API key decision), homepage merchandising controls (§5.4).

**Phase 5 — remaining panel completeness:** manager captain-review visibility, inventory edit/delete, store-level subscriber reports (§8–9).

I'd start Phase 1 the moment you confirm the numbers in §10 (or tell me to just use my defaults) — everything in Phase 1 needs no external API keys, so it's not blocked on anything but your go-ahead.
