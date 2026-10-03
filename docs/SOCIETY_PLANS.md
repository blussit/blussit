# Society plans — design

Housing societies with many cars buy one monthly plan per car:
**a daily bucket wash done by a captain who comes to the society**, plus a
small **quota of premium washes** (Star Wash or Deep Cleaning) the resident
books a day ahead. Example: ₹2000 MRP → **₹1649 per car per month** = daily
bucket wash (≈25 days) + 2 Star Washes.

This document is the source of truth. It reuses the existing plan machinery
(`subscription_plans`, `user_subscriptions`, `plan_consumption` /
`commit_consumption`, Razorpay orders) instead of building a second system.

---

## 1. Data model

### 1.1 Society plans live in `subscription_plans`
A society plan is an ordinary plan document with `plan_type: "society"`:

| field | meaning |
|---|---|
| `plan_type` | `"society"` (absent/`"standard"` = website plans) |
| `society_bucket_days` | bucket-wash days per plan month: 10 / 15 / 20 / 25 (25 = daily — follows the month length, see §2 "Plan month") |
| `society_premium_service_id` | Star Wash or Deep Cleaning (a real, non add-on service) |
| `society_premium_count` | premium washes per cycle (1 / 2 / 4 …) |
| `society_scope` | `template` (any society) · `society` (only `society_ids`) · `customer` (only `society_customer_id`) |
| `society_ids`, `society_customer_id` | who may see/buy a scoped plan |
| `auto_created` | true for a "customise" combination created from the rate card |
| `price` / `discounted_price` | MRP / selling price (flat) |
| `vehicle_type_prices` / `vehicle_type_discounted_prices` | per car type MRP / selling price (win over flat) |
| `included_service_ids` | `[society_premium_service_id]` — what quota pays for |
| `total_service_count` | `= society_premium_count` |

Why reuse the collection: every existing reader (customer pass list, admin
"Purchased plans", usage history, plan names, sweeps) keeps working with zero
special-casing.

**Hidden everywhere public.** `GET /subscription-plans` (website, customer
purchase sheet, admin's normal plan editor) never returns society plans.
Every general purchase path refuses them: customer checkout
(`/payments/create-order` purpose `subscription`), `/subscriptions/quote`,
manager "Sell a plan" (preview/offer), `/subscriptions/assign`. The only way
to get one is a society enrollment.

### 1.2 Rate card (customise)
`society_settings` doc `{_id: "rate_card"}`, admin-edited:
- `bucket_day_price` / `bucket_day_mrp`: `{default, by_type{vehicle_type_id: ₹}}` per bucket-wash day
- `premium_discount_percent`: off the premium service's standard per-type price
- `bucket_day_options` `[10,15,20,25]`, `premium_count_options` `[1,2,4]`
- `premium_service_ids` (default: Star Wash + Deep Cleaning by slug)
- `allow_customise` (default true)

Customise price = `round(days × day_price[type] + count × service_price[type] × (1 − discount))`,
MRP = `round(days × day_mrp[type] + count × service_price[type])` (never below price).
Choosing a combination creates (or reuses) an `auto_created`, `society`-scoped
plan for **that society** with per-type prices frozen from the rate card — so
neighbours see and can pick "the same plan" and later rate-card edits never
reprice someone mid-flight.

### 1.3 New collections
**`societies`** — `name, address_line, area, city, state, pincode, latitude,
longitude, service_center_id, contact_name, contact_phone, notes,
form_token (unique, 128-bit urlsafe), form_enabled, daily_captain_id,
substitute {captain_id, date}, is_active, created_by, attendance_alert_date`.
The center is resolved **from the pin** with the same resolver bookings use
(`_resolve_service_center`: zones → nearest center → pincode). A manager can
only register a society that resolves to their own center.

**`society_enrollments`** — one resident's request/plan in one society:
`society_id, service_center_id, customer_id, resident_name, phone, flat,
address_id, plan_id, source (form|manager), status
(requested | awaiting_payment | active | cancelled), cars[{vehicle_id,
vehicle_type, registration_number, price, mrp, subscription_id, status
(pending|active|cancelled)}], total_amount, mrp_total, payment{method, amount,
order_id, collected_by}, activated_at, cancelled_at, note`.

**`society_attendance`** — one row per society per IST day (unique
`(society_id, date)`): `captain_id, arrived_at, location{lat,lng,accuracy_m},
distance_m, far_from_society, location_missing, washed_vehicle_ids[],
service_center_id`.

**`society_payments`** — ledger: `society_id, enrollment_id, customer_id,
service_center_id, amount, method (online|cash), kind (activation|renewal),
car_count, subscription_ids, collected_by, razorpay_order_id, created_at`.
Revenue reports read this, never recomputed from plans.

### 1.4 One `user_subscriptions` doc per car (the quota)
Activation writes, per car, the same document shape a pass has:
`customer_id, plan_id, vehicle_id (the car), vehicle_type, service_id
(premium service), total/remaining_service_count = premium count,
purchased_price, amount_paid, payment_method, service_center_id, start_date,
end_date (+1 plan month)` plus society fields `society_id, enrollment_id,
bucket_days, cycle_start, prev_cycle_start`.

One car carries one live pass (existing rule) — a car can't be in two
societies or hold a society plan and a normal pass at once.

**Difference from a normal pass:** spending the last premium wash does NOT
expire it (bucket washes continue until `end_date`). `commit_consumption`
keeps it `active` at 0; the existing ended-pass sweep expires it at
`end_date`.

---

## 2. Quotas and billing
- **Premium quota** = `remaining_service_count` on the car's subscription,
  spent by a normal booking through `plan_consumption` / `commit_consumption`
  (pass path, named car). Cancel → existing `restore_consumption`.
  Add-ons on a premium visit are paid extra (existing rule).
- **Lead time:** on customer channels (`app`, `whatsapp`) a booking that
  spends a society pass must be for **tomorrow or later (IST)**. Staff
  (manager booking on the resident's behalf) are exempt.
- **Auto-apply:** like any pass, the quick-booking flow applies it when the
  car type + service match; the resident can switch "use my plan" off to pay.
- **Plan month** (`services/society_month.py`): a cycle runs from its start to
  the **same day next calendar month** (IST, clamped: 31 Jan → 28/29 Feb), so
  5 Feb → 5 Mar is 28 days and 5 Mar → 5 Apr is 31. Activation and renewal
  use `plan_month_end()`. Quotas cover the plan month, never "per day".
- **Month-length bucket rule:** a **full-month** plan (`bucket_days ≥ 25`,
  the daily wash) gets `min(bucket_days, days_in_cycle − Sundays_in_cycle)`
  bucket washes — one per working day of *that* month, capped at 25: a
  30/31-day month gives 25, a 28-day February gives 24, a 29-day month 24 or
  25. **10 / 15 / 20-day plans stay fixed** in every month. The weekly off is
  Sunday (same day the missed-attendance alert skips). Applied wherever the
  allowance is read: captain checklist (`allowance`), "used all" refusal,
  residents list / hub (`bucket_allowance`) — all via `sub_bucket_allowance()`.
- **Bucket washes** are not bookings. Allowance per car = the month-length
  rule above per cycle; the captain's checklist refuses a car that has used its allowance in
  the current cycle (`[cycle_start, end_date)`, or the previous cycle while an
  early renewal hasn't started yet).
- **Price** per car is frozen on the enrollment at request time and on the
  subscription (`purchased_price`) at activation. Total = Σ cars.
- **Payment:** (a) online — new payment purpose `society` on
  `/payments/create-order`; the server computes the amount from the
  enrollment; verify, webhook and reconciliation sweep all settle through the
  shared `_apply_order_paid` claim, which activates the enrollment; (b) cash —
  manager "Mark paid (cash)" activates immediately and records the collector.
  Razorpay off (local dev) → the form only offers "Request — pay later".
- **Renewal** (per enrollment, all its active cars): allowed from 3 days
  before `end_date` or any time after. New cycle starts at
  `max(now, old end)`, runs one plan month; premium quota for the new cycle is added
  (early renewal keeps the few unused washes until the new end — nobody
  loses what they paid for). Paid online by the resident (hub "Renew") or
  cash by the manager. Auto-pay is out of scope for v1.

---

## 3. Flows

### Manager
1. **Register society:** name, address, map pin (or pasted coordinates),
   pincode, contact person → center resolved from the pin → link generated.
2. **Share link** `https://<site>/society/<token>` — copy or WhatsApp share.
   "New link" rotates the token (old link stops working); the form can be
   switched off.
3. **Enrollments:** sees requests, payment-pending and active residents;
   can add a resident on their behalf (name, phone, flat, cars, plan), mark
   paid (cash) → active, renew (cash), cancel a car/enrollment.
4. **Daily captain:** assigns one captain of their center; can set a
   one-day substitute (captain absence).
5. **Attendance calendar:** per month — present days (arrival time, distance
   flag, cars washed), missed days.
6. **Book premium wash** for a resident (any captain can be dispatched later
   from the normal booking queue).

### Resident (public form `/society/:token`)
Name + phone → flat → number of cars → each car's type + plate → plan (a
template / society plan card, or **Customise**: days × premium type × count →
live price) → OTP (local dev 123456) → **Pay ₹X** (online) or **Request —
pay later**. After that the same link is their **society hub**: cars, plan,
premium washes left, bucket washes this cycle, valid till, **Book a premium
wash** (date ≥ tomorrow, slot), Renew / Pay. The society passes also appear
in the existing customer Plans list (plan name is suffixed with the society
name).

### Captain
"Today's societies" (societies where they are the daily captain or today's
substitute): **I've arrived** (geo-stamped; >500 m from the pin or no GPS is
recorded and flagged, never blocked) → optional car checklist (plate, type,
flat; "used 12/15 this cycle"). Lives in its own component
(`components/captain/society/*`) so the captain-app rework can re-home it.

### Admin
Societies overview (by center/city: residents, active cars, requests,
revenue this month, attendance days) → society detail (same view as the
manager). **Society plans** page: templates + custom plans (scope: all
societies / one society / one customer by phone), rate card editor.

---

## 4. Authorization
| actor | can |
|---|---|
| public (token) | read the society's name/area, plans offered to it, rate-card options, vehicle types, whether online pay is on. **Never** residents, phones, flats, counts. Unknown/rotated/disabled token = 404. |
| resident (customer) | create/replace **their own** open enrollment (phone proven by OTP, or already signed in with that phone); read only their own enrollment(s)/cars/usage; pay/renew their own; book premium washes on their own society passes. Another customer's ids → 404. |
| manager | societies whose `service_center_id` = their center (list, create, edit, link, captain, enrollments, attendance, cash activate/renew, cancel, book on behalf). Other centers → 403. Daily captain must belong to their center. |
| captain | only societies where they are today's captain (daily or substitute): read the car checklist (plate, type, flat — no phones), mark attendance, tick cars. Anything else → 403. |
| admin | everything; plan templates, custom plans, rate card. |

Rate limits (`core/rate_limit.py`): `/api/v1/society-forms` POST 30/min/IP (quote + enroll),
GET 60/min/IP. Phone OTP reuses `/bookings/verify-phone/request` (already
limited, plus the per-phone caps in AuthService).

---

## 5. Edge cases (decided)
- **Car added mid-month:** a new enrollment (or manager "add resident" again)
  — that car gets its own plan month from its activation. No proration.
- **Resubmitting the form:** one open (requested/awaiting_payment)
  enrollment per resident per society — a resubmit replaces it.
- **Car already on a live pass** (any plan, any society): refused at request,
  at order creation and again at activation; at activation a conflicting car
  is skipped and the enrollment notes it (online money for it → order parked
  for a human, like any plan that can't be activated).
- **Cancellations:** cancel a car or a whole enrollment → its subscriptions
  become `cancelled` (quota gone, captain checklist drops it). Upcoming
  premium bookings stay — cancel them from the queue. **Refunds are out of
  scope** (handled by hand).
- **Resident in two societies:** separate enrollments; each hub shows only
  its own society.
- **Captain absence:** manager sets a one-day substitute or changes the daily
  captain; days without attendance show as missed; manager gets one in-app
  alert per society per day after 11:00 AM IST (bounded sweep). No automatic
  credit — the manager decides on make-up washes.
- **Month boundaries:** quotas run per car on plan months from activation;
  admin/manager reports (attendance, revenue) are by calendar month.
- **Premium quota used up:** pass stays active (bucket washes continue);
  the booking flow simply stops covering premium washes until renewal.
- **Plan edited later:** subscriptions keep their snapshot (price, quota,
  bucket days); only new enrollments see new prices.
- **Plan deactivated:** existing residents keep it; renewals of an inactive
  plan are refused — manager moves them to another plan via a new enrollment.
- **Razorpay off:** form hides "Pay now"; requests flow to the manager.

---

## 6. API (all under `/api/v1`)
Public form: `GET /society-forms/{token}`, `POST /society-forms/{token}/quote`,
`POST /society-forms/{token}/enroll`, `GET /society-forms/{token}/me` (customer).

Resident: `POST /societies/my/premium-bookings`,
`POST /society-forms/{token}/me/enrollments/{id}/withdraw`; paying and renewing
go through `POST /payments/create-order` `{purpose: "society", society_enrollment_id, society_renewal}`.

Manager/admin: `GET|POST /societies`, `GET|PUT /societies/{id}`,
`POST /societies/{id}/rotate-link`, `PUT /societies/{id}/captain`,
`GET|POST /societies/{id}/enrollments`, `GET /societies/{id}/attendance?month=`,
`GET /societies/{id}/plans`, `POST /society-enrollments/{id}/activate`,
`POST /society-enrollments/{id}/renew`, `POST /society-enrollments/{id}/cancel`,
`POST /societies/{id}/premium-bookings`, `GET /societies/captains`.

Admin: `GET /societies` (all centers, `?center_id=` filter), `GET|POST /society-plans`,
`PUT /society-plans/{id}`, `GET|PUT /society-plans/rate-card`.

Captain: `GET /societies/captain/today`, `POST /societies/captain/{id}/arrive`,
`PUT /societies/captain/{id}/washed`.

---

## 7. Implementation map (2026-10-03)
Backend: `schemas/society_schema.py`, `repositories/society_repository.py`
(+ `ensure_society_indexes`, called from `core/database.py`),
`services/society_service.py`, `routes/v1/society_routes.py` (registered in
`main.py`; missed-attendance sweep added to `_sweep_once`). Surgical edits:
`subscription_service.py` (hide/refuse society plans, keep a society pass
active at 0, customer can't self-cancel/upgrade one, `_with_society_labels`),
`booking_service.py` (lead-time hook in `create_booking`),
`payment_service.py` + `payment_schema.py` (purpose `society`),
`rate_limit.py`, `utils/serializers.py`.

Frontend: `api/society.ts`; public `pages/society/SocietyFormPage.tsx`
(+ `components/society/SocietyOtpModal.tsx`); staff
`components/society/{SocietyListView,SocietyDetailView,SocietyFormModal,SocietyResidentModals}.tsx`
behind `pages/manager/ManagerSocieties*.tsx`, `pages/admin/AdminSocieties*.tsx`,
`pages/admin/AdminSocietyPlansPage.tsx`; captain
`components/captain/society/CaptainSocietiesToday.tsx` (self-contained, re-homeable)
behind `pages/captain/CaptainSocietiesPage.tsx`. Routes: `/society/:token`,
`/manager/societies[/:id]`, `/admin/societies[/:id]`, `/admin/society-plans`,
`/captain/societies`.

Customer plan list (owned by the customer app): `/subscriptions/my` rows for
a society pass carry `society_id`, `society_name`, `society_form_path` and a
plan name suffixed "— <society>". Suggested UI: show the society name, hide
Cancel/Upgrade/Buy again for them, and point "Book now" at
`society_form_path` (the hub) — the generic `/app/book?subscription=` path
also works (lead-time enforced server-side).

Tests: `tests/test_society_plans.py`, `tests/test_society_access.py`
(shared builders in `tests/society_factories.py`).

## 8. Not in v1 (follow-ups)
- Society revenue is reported on the Societies pages (from `society_payments`);
  it is not yet folded into the main KPI dashboard or the Collections ledger.
- Manager-sent Razorpay payment *links* for a society request (today: resident
  pays from the form link, or the manager marks cash).
- Auto-pay mandates for society plans; refunds (by hand).
- The missed-attendance alert skips Sundays; a per-society weekly-off setting
  would make that exact.

## 9. Fixes & support (2026-10-03)
- **Premium bookings are plan-covered**: `book_premium` sends
  `payment_method: subscription`, premium service only (the multi-car path
  used to send `None` → 500). Unhandled errors now answer a JSON 500 from a
  middleware *inside* CORS (`main.CatchUnhandledErrors`), so the browser
  shows the message instead of "Can't reach the server".
- **Names, not "Premium/Bucket"**: enrollment views carry
  `premium_service_name` + `bucket_short_label` ("Star Wash 1/2" = left/quota,
  "Daily wash 3/25" = done/allowance); captain cards carry the same; every
  booking list shows "Star Wash · Society plan (<society>)" (`plan_label`,
  added in `BookingService._enrich_bookings`).
- **Coupons** (existing coupons, `CouponService` rules, standard offers only):
  quote shows `coupon`/`discount`/`payable_total`; the code is frozen on the
  request (`coupon_code`, `discount_amount`, per-resident limit checked once
  the phone is known) and **counted once on activation** (`coupon_usages`
  ref `society:<enrollment>`); renewals count per renewal
  (`society-renewal:<payment>`). Staff can apply/replace/remove on "Mark paid"
  (`ActivateEnrollmentRequest.coupon_code/remove_coupon`) and on cash renewal;
  residents on the form and on online renewal (`society_coupon_code` on
  create-order). Whole rupees, split across cars by price
  (`amount_paid` per pass; ledger `gross_amount/discount_amount`). A counted
  use is given back if the request is withdrawn. Preview:
  `POST /society-enrollments/{id}/coupon-preview`,
  `POST /society-forms/{token}/me/enrollments/{id}/coupon-preview`.
- **Resident issues** = complaints tagged `category: "society"`,
  `society_id`, `issue_type`, car (`POST /society-issues`, `GET
  /society-issues/my`, staff `GET /society-issues/society/{id}`; complaints
  lists take `category=society|booking` and `society_id`). Only a resident of
  that society can file (404 otherwise); the center's managers get an in-app
  alert; open counts on the society list/detail. Same open issue on the same
  car within 12 h → 409.
- **Society requests** (landing Plans section): public `POST /society-leads`
  (6/min/IP, phone validated, same phone + society within 24 h merges),
  `GET /society-leads/info` ("from ₹X"); routed by pincode with the bookings
  resolver (unmatched → admin). Staff `GET|PUT /society-leads` (manager: own
  center); `POST /societies` with `lead_id` marks it registered.
  Code: `services/society_support_service.py`, `routes/v1/society_support_routes.py`.
  Tests: `tests/test_society_fixes.py`, `tests/test_society_support.py`.

## 10. Scheduling — premium washes (2026-10-03)
Daily bucket washes stay as they are (the society's daily captain marks
attendance). **Premium washes** (Star Wash / Deep Cleaning in a resident's
plan) are scheduled by the **manager (own center) and admin (all)**: which
days captains go to a society, how many captains, how many washes each.

### 10.1 Model
- **Repeat rules** (`society_schedule_rules`)
  - `kind: "society"` — a society's visit day: `pattern`, `slot_keys` (the
    center slots the captains work, e.g. 8–11 + 11–2), `captain_ids`,
    `washes_per_captain` (capacity = captains × washes), optional
    `start_date`/`end_date`.
  - `kind: "resident"` — one resident's own repeat wash: `enrollment_id`,
    `subscription_ids` (their cars), `pattern`, one `slot_key`, optional
    `captain_id` (empty = the society's daily captain). A car is in at most
    one active resident rule.
  - `pattern`: `weekly` (every Saturday) · `monthly_nth` (1st & 3rd Saturday)
    · `every_n_weeks` (every N weeks from `anchor_date` — what a rotation uses).
- **Visits** (`society_visits`) — the occurrences. Materialized from rules
  from tomorrow to **35 days ahead** (a full plan month; the planner can look
  up to 92 days), idempotently: `occurrence_key = rule_id:original date` is
  unique, so the sweep, the planner and a rule save can all run it. Also
  one-off visit days (`rule_id: null`). Each carries its own single-day
  changes: `date` (moved; `moved_from`), `captain_ids`, `slot_keys`,
  `washes_per_captain`, `excluded_subscription_ids` (car taken off this
  day), `status` (`planned → booked | partial | failed | empty`, `skipped`),
  `allocations` (frozen at booking time), `changes` (last 20).
  Editing/removing a rule re-makes its not-yet-booked future days (single-day
  changes on those reset); booked days stay.
- **Change requests** (`society_schedule_requests`) — a resident asks to skip
  or move one scheduled wash; one pending per (visit, resident); staff
  approve (skip = take their cars off that day / skip their repeat day; move =
  also a one-off resident visit on the new date/slot) or decline. The
  resident is told in-app either way.
- **Rotation**: `POST /society-schedule/rotation` spreads the picked
  societies, in order, over the picked weekdays — society *i* gets the
  (*i* mod *days*)-th day in week ⌊*i*/*days*⌋, repeating every
  ⌈*n*/*days*⌉ weeks (4 societies over Sat+Sun: A Sat wk1, B Sun wk1, C Sat
  wk2, D Sun wk2, then again). It is just one `every_n_weeks` rule per
  society (replacing that society's visit-day rules). `dry_run` previews.

### 10.2 Allocation (`services/society_schedule_engine.py`, pure)
Walk a society's upcoming visits in date order (a resident's own repeat
wash before the society day on the same date):
- a car is covered only if it has **premium washes left after everything
  planned before it**, the date is **inside its plan month**, it isn't
  already booked that day, and it isn't taken off the day — so the
  schedule **never plans more than the quota**;
- a resident with their own repeat wash (or any booking) that day sits the
  society day out;
- society day: cars that are *due* — more washes left than later chances
  this plan month, or not washed yet this month, or last premium wash ≥
  (plan month ÷ quota − 2) days ago — most urgent first, grouped per resident
  (one resident's cars = one booking visit, back to back, one captain), up to
  captains × washes per captain; the rest is listed as *not fitting*;
- layout: residents in flat order; each captain starts at the window start,
  next resident after the previous one + the policy travel buffer, stepping
  around the captain's other assigned jobs; every car of a multi-car resident
  must start inside one booking slot.
Projections are live (planner / schedule tab / resident view); the booking
step re-runs them and freezes the result.

### 10.3 Booking (the reminder-loop sweep)
`main._sweep_once` → `SocietyScheduleService.sweep()`: re-opens stale claims
(>10 min), materializes rules whose horizon is short (≤50 rules/pass, indexed
`(is_active, materialized_until)`), then books **planned visits dated
tomorrow … today + `generate_days_ahead`** (admin setting, default 2;
`(status, date)` index, ≤5 per pass, 8 AM–9 PM IST so confirmations go out in
the day — tomorrow's are booked whatever the hour). Per visit: claim
(`planned → generating`), project, and per resident call
**`SocietyService.book_premium`** (the manager's own premium-booking path:
`create_booking` / `create_booking_group`, plan-covered, quota spent through
`commit_consumption`, refunded on cancel), mark the bookings
`society_visit_id`, then assign the planned captain to each car at its
staggered start through the guarded `assign_captain` path (all-or-nothing per
resident). Idempotent: a car that already has a live booking that date is
**adopted, never booked twice**; a visit's own orphaned bookings (a run that
died after booking) are seen through and re-adopted. The resident gets the
normal booking confirmation; the manager gets **one** "Society visit booked"
line (the per-booking "needs a captain" alert is muted for these —
`utils/quiet_alerts.py`); each captain one "Society visit — X · N premium
washes" line. Failures (slot full, captain clash, plan ended) are kept on the
allocation and the day shows *partly booked* with **Retry booking**.
"Book now" (`POST /visits/{id}/book`) does the same at once for a day within
the booking window. Skipping / moving a booked day cancels its bookings
(quota back); new captains on a booked day reassign its bookings.

### 10.4 Conflicts shown in the planner
One captain on two society visits the same day; captain on approved leave;
a slot whose remaining capacity can't take the planned residents (each
resident = one slot seat); cars due but not fitting; premium washes with no
date before the plan month ends ("No date yet").

### 10.5 Who can do what
| actor | can |
|---|---|
| manager | everything below for societies of **their center** (other center → 403; planner always their own center) |
| admin | all centers (`?center_id=` on the planner); `PUT /society-schedule/settings` |
| captain | read-only: `GET /society-schedule/captain/visits` (today + 6 days where they are a planned captain or were given cars), `/captain/visits/{id}` (not on it → 404) |
| resident | `GET /society-schedule/my` (own cars only: scheduled + hand-booked premium washes, next wash per car, slots, own requests), `POST /society-schedule/my/requests` (a wash they're not on → 404) |

### 10.6 API (`/api/v1/society-schedule`)
`GET|PUT /settings` · `GET /planner?center_id&start&end` · `POST /rotation` ·
`GET /societies/{id}?start&end` (rules, resident rules, visits with cars,
outlook, requests, residents, warnings) · `POST /societies/{id}/rules` ·
`POST /societies/{id}/resident-rules` · `POST /societies/{id}/visits` ·
`PUT|DELETE /rules/{id}` · `PATCH /visits/{id}` (date / captains / slots /
washes per captain / note — this day only) · `POST /visits/{id}/skip|restore|exclude|book` ·
`DELETE /visits/{id}` (one-off, nothing booked) · `GET /requests` ·
`POST /requests/{id}/approve|decline` · captain + resident routes above.

### 10.7 UI
- Manager/admin: society detail → **Schedule** tab (repeat visit days,
  upcoming days with their cars and single-day controls, residents' repeat
  washes, change requests, "Premium washes left this plan month");
  **Repeat wash** on each active resident row; **Society planner**
  (`/manager/society-planner`, `/admin/society-planner`): 4-week calendar of
  every society's days, Build rotation, Add visit day, warnings, societies
  table. Code: `components/society/schedule/*`.
- Captain Today: "Society visit · Tomorrow — Sunrise Residency · 6 premium
  washes" with each car's time, plate, flat and status (booked cars open the
  normal job flow); later days as a short list
  (`components/captain/society/CaptainSocietyVisits.tsx`). Daily attendance
  card unchanged below it.
- Resident: society hub card "Next premium wash: Sat 10 Oct, 8:00 AM –
  11:00 AM", upcoming list, **Request change** (another day / skip + note);
  customer **My Plans** society card line "Next premium wash: …"
  (`ResidentScheduleCard.tsx`, `NextPremiumWash`).

### 10.8 Code + tests
Backend: `schemas/society_schedule_schema.py`,
`repositories/society_schedule_repository.py` (indexes, hooked from
`ensure_society_indexes`), `services/society_schedule_engine.py` (pure),
`services/society_schedule_service.py`, `routes/v1/society_schedule_routes.py`,
`services/society_month.py`, `utils/quiet_alerts.py`. Small hooks: `main.py`
(router + sweep), `society_service.py` (plan month + allowance),
`booking_service._notify_managers_of_new_booking` (mute check).
Tests: `tests/test_society_schedule.py`, `tests/test_society_schedule_access.py`,
`tests/test_society_month.py`.

### 10.9 Open
- Weekly off is Sunday for every society; a per-society weekly-off day would
  make the month-length rule and the missed-attendance alert exact.
- A resident's change request is answered as asked (or with a staff-picked
  date/slot via the API); the UI approves as asked.
- Allocation is greedy per society; across societies on the same date, a
  captain double-booking is warned in the planner and resolved at booking
  time (the later day steps around his booked jobs or reports "captain busy").
