# Feature Plan — Wallet, Booking Edits, On-Site Add-Ons, Custom Plans, Manager Alerts, Templates, API/Worker Split (2026-10-07)

This is the shared spec the build agents work from. Business rules are the
founder's (2026-10-07); defaults chosen by engineering are marked **(default)**
and can be changed later.

## 0. What exists today (investigation summary)

| Area | Today | Gap |
|---|---|---|
| Customer edits | Reschedule date/slot only, > 4 h before, unassigned; never re-priced | No address/service/car edit; no 1-hour rule; no manager flag. **Bug:** `PUT /addresses/{id}` silently moves live bookings (no lock, no center check, no re-price) |
| Customer cancel | Online only > 4 h and unassigned → always free | Customers can never cancel late themselves; charges only via staff |
| Late-cancel charge | `customer_charges` → added to the next booking's total | To move to the wallet model |
| Plan booking cancel | Wash always returned, any time | 1-hour rule missing |
| Customer wallet | None (captain wallet only) | New |
| Partial payments | `payment_status` pending/paid only; amounts == `total_amount` everywhere | Needed for add-ons / edits on paid bookings |
| On-site add-ons | None | New |
| Custom multi-car plan | None (one car × one service per pass) | New; reuse society enrolment + extension patterns |
| Pass extension | Society passes only; revive path lacks the car-claim re-check | Generalise; fix gap |
| Manager new-booking WhatsApp | Built; silent failure paths; no toggle | Fix + toggle |
| Templates | Many; missing review/universal/payment-failed/refunded/edited/book-now | New definitions |
| Deployment | One Cloud Run service runs API + background jobs | Split API / worker |

## 1. Business rules

### 1.1 Customer wallet
- Every customer has a wallet. The balance **can go negative**.
- **Credits**
  - Prepaid (or part-paid) booking cancelled: what was paid, minus the
    cancellation charge.
  - Paid booking edited to a lower price: the difference.
  - Overpayment (e.g. paid online while the captain also took cash): the excess.
  - A manager reducing or waiving a charge: the reduction.
  - Admin adjustments.
- **Debits**
  - Unpaid booking cancelled late: the charge (balance goes minus).
  - Wallet balance used for a booking.
  - Manual payout to the customer's source account. The customer asks the
    manager; the manager transfers the money outside the app and records it
    (amount ≤ balance, method, reference, note).
  - Admin adjustments.
- **At the next booking**
  - A negative balance is added automatically to what the customer must pay
    ("Previous Balance Due ₹X").
  - A positive balance is used automatically **(default)**.
  - Both are shown in the quote before the customer books.
- Every entry is an immutable ledger row:
  - balance-after;
  - idempotency key — no double credit from retries or races;
  - actor and booking reference.
- Customers, managers (their center's customers) and admins can see the ledger.

### 1.2 Cancellation (customer and staff)

**Who can cancel**
- **Customer self-cancel** is allowed until the captain starts heading out: no
  car of the visit is `captain_on_the_way`, `service_started` or `completed`.
- **Staff cancel**: as today, with "at the customer's request" plus a charge the
  manager can reduce.

**Charge tiers** (booking policy, admin-editable)
- More than 4 h before the slot: free.
- 1–4 h before: ₹50.
- Under 1 h before, or after the slot start: ₹80.
- Captain already left: ₹100. Staff-only, since customers can't cancel then.

**Money**
- Paid (online / part-paid / wallet-applied): wallet credit of
  paid + wallet_applied − charge. If the charge exceeds that, the rest is a
  debit.
- Unpaid: wallet debit of the charge.
- Business / system cancels (expiry, no captain, weather): no charge; full
  credit of anything paid.

**Plan-covered cars**
- No money charge.
- Cancelled 1 h or more before the slot: the wash is returned.
- Cancelled within 1 h of the slot (or after it): the wash is used up
  (`consumption_forfeited`).

**Mixed visit** (plan car + paid car): plan cars follow the plan rule; the
paid part follows the tier, one charge per visit.

**Notifications**
- Customer: WhatsApp with the wallet line.
- Manager: in-app only.
- `customer_charges` stays as the charge record (tier, amount, reduce/waive
  history); settlement is through the wallet.

### 1.3 Customer edits a booking
- **Allowed** while the booking is not started/completed/cancelled and the time
  is **more than 1 hour before the slot start**. Inside 1 hour: no edits.
- **Editable fields**
  - Date/slot: capacity moves atomically.
  - Address: must resolve to the same service center. Otherwise refused with
    "cancel and book again".
  - Services / add-ons / quantities.
  - Car type / car.
  - Notes.
- **Plan-covered bookings:** date/slot, address and notes only. The service or
  car can't change ("cancel and rebook").
- **Effects**
  - Re-priced with the same pricing as quote/create; travel charge re-computed
    on an address change.
  - Date/slot change on an assigned booking: the captain is released and told.
  - Address, service or car change: the captain keeps the job and is told.
- **Money**
  - Price down on a paid booking: the difference is credited to the wallet.
  - Price up: the difference becomes **due**, collected at completion
    (captain cash/QR) or paid online by the customer.
- **Manager:** in-app flag "Customer edited BK… (what changed)" plus a
  `customer_edited` marker visible in the queue. Not WhatsApp.
- **Customer:** sees the change immediately and gets the "booking edited"
  WhatsApp.
- Every edit writes a history row with the field-by-field diff, and an audit row.
- **Address side door closed:** editing a saved address never changes a live
  booking. Bookings snapshot the address at creation and on edit.

### 1.4 On-site add-ons (captain or manager)
- **Captain:** after arrival (verified) and until the visit is fully paid, the
  assigned captain can add services/add-ons for the booking's car. The new total
  shows immediately.
- **Manager:** for own-center bookings, any time until the booking is fully paid
  and not cancelled.
- **Recorded on the booking:** `added_services[]` with service, qty, price, by,
  role and time. Manager and admin views show "Captain X added Y after the
  wash".
- **Captain earnings unchanged** for now (founder; extra pay is a future
  feature).
- Money: the added amount becomes due (see 1.5).

### 1.5 Collecting money (every booking)
- **Booking money fields**
  - `total_amount`: what the visit costs, including carried balance and added
    services.
  - `wallet_applied`.
  - `amount_paid`: received online, by QR or in cash.
  - **`amount_due` = max(0, total − wallet_applied − amount_paid)**.
  - `payment_status`: pending (nothing paid, due > 0), partially_paid (paid > 0
    and due > 0), paid (due == 0), refund_due, refunded.
- **At completion** (final stage), if due > 0 the captain app asks "Collect
  Cash or Online?":
  - Online: Razorpay QR for exactly `amount_due`.
  - Cash: recorded as cash collected by that captain.
- **Customer:** can pay `amount_due` online from the website at any time.
- **Race-safety**
  - Every settle is a guarded claim on the order/link and on the booking's
    `amount_paid`.
  - Anything paid beyond what is due goes to the customer's wallet; it is never
    lost and never "parked for refund".
- **Captain wallet: delta-based settlement.** The captain wallet always nets to
  "platform share of the cash this captain collected", posted as deltas against
  `captain_wallet_posted`. So completion, later cash collection and later
  add-ons can never double-count or miss money.

### 1.6 Custom multi-car plan (manager cart)
- A manager builds a cart for one customer:
  - per car: car (plate/type) and per-service counts, e.g.
    car A: 2 Star Wash + 2 Deep Cleaning; car B: 1 Deep Cleaning + 2 Star Wash.
- **Price** **(default)**:
  - Server-computed catalogue total: Σ count × standard price for that car type.
  - Optional manager discount on the total, capped like plan sales (≤ 50 %).
  - Frozen per car at the cart revision.
- **Payment:** payment link or cash, like Sell A Plan. No auto-pay in v1.
- **On activation:** one car-bound pass per car with per-service quotas.
  - All cars start the same day **(default)**.
  - Each car has a **30-day period**.
- **Booking:** the customer books a covered wash for that exact car (service
  must have quota left; add-ons paid). The pass is chosen explicitly, never
  auto-applied to other cars.
- **Extension:**
  - Managers only (own center) or admin.
  - At most **10 days per 30-day period**.
  - In the last 3 days or after the end, while washes remain.
  - The customer sees "ends in 30 days"; during an extension they can book the
    remaining washes (same as society passes).
  - Every extension is audited and visible to admin.

### 1.7 Manager WhatsApp
- Managers get WhatsApp **only for new bookings**; everything else is in-app.
- A per-manager toggle, **on by default**, in the manager's profile.
- Fixes:
  - a deleted or invalid `manager_id` must not block the admin fallback;
  - per-manager send errors are logged;
  - template language comes from the template row;
  - template sync pages past 100;
  - 401/403 from Meta are classified as config errors, not "rejected" silence;
  - delivery failures from the status webhook update the queue;
  - phone numbers with a leading 0 are normalised;
  - `WHATSAPP_BUSINESS_NUMBER` is required in production;
  - an admin warning if a manager's phone equals the business number.

### 1.8 Templates (submitted via Admin → WhatsApp → Templates → Submit)
New versioned definitions (rejected names are never resubmitted unchanged):
- **Review request:**
  - "Hi {{1}}, thanks for choosing Blussit for your {{2}}…";
  - URL button to the Google review page (admin setting `google_review_url`);
  - sent once after a completed and paid booking (a worker sweep, about 2 h
    later, 10 AM–7 PM).
- **Universal message:** "Hi {{1}}, {{2}} — Team Blussit". Admin/manager writes
  the message in the CRM and sends it to a customer.
- **Book Now:** marketing; URL button to `https://blussit.com/book`.
- **Payment received:** exists.
- **Payment failed:** link to retry.
- **Refund / wallet credited:** amount and new balance.
- **Wallet debited:** cancellation charge and balance.
- **Booking cancelled:** by you / by us, with the wallet line.
- **Booking edited:** what changed and the new total.
- **Plan expiring / renew:** button to `/app/subscriptions`.
- **Manager new booking v2:** button to `/manager/bookings`.
- **Pre-slot booking reminder:** the template exists; wired by a sweep.

### 1.9 Architecture: API + Worker
- **One codebase, one image, two Cloud Run services:**
  - `blussit-api`: `RUN_MODE=api`. HTTP, webhooks and websocket relay; scales
    1–N; no background loops.
  - `blussit-worker`: `RUN_MODE=worker`. Reminder/sweep loop, WhatsApp retry,
    template sync, review-request sweep; exactly 1 instance; internal ingress;
    `--no-cpu-throttling`; its own `/api/health` and `/api/ready`.
- Either can crash or restart without stopping the other. External failures
  are already isolated (WhatsApp queue, Razorpay poll pool, R2 503, readiness).
- `RUN_MODE=all` (default locally) keeps today's single-process dev setup.
- **Scale notes:**
  - The API is stateless.
  - Websocket fan-out goes through `ws_events`.
  - Lease-based single-flight sweeps stay as a safety net.
  - In-memory rate limits remain per instance (documented); OTP and money caps
    are database-backed.

## 2. Contracts between agents

### MONEY (owner: payment_service.py, new customer_wallet_service.py, new booking_money.py, customer_charge_service.py, database.py, wallet_service.py)
- `app/services/booking_money.py` (pure, no I/O):
  - `money_view(booking) -> {"total", "wallet_applied", "amount_paid", "amount_due", "payment_status"}`
  - `amount_due(booking) -> float`
  - `status_for(total, wallet_applied, amount_paid) -> str`
- `CustomerWalletService(db)`:
  - `balance(customer_id) -> float`
  - `post(customer_id, amount, kind, *, key, booking_id=None, actor_id=None, actor_role=None, note=None, session=None) -> dict`
    - Signed amount; atomic balance + ledger row; idempotent on `key`.
  - `ledger(customer_id, page, page_size)`
  - `payout(customer_id, amount, method, reference, note, actor, center)`
    - Manager (own-center customer) or admin; amount ≤ balance.
- `MoneyService(db)`, in payment_service.py or a new money_service.py:
  - `apply_wallet_at_create(customer_id, draft_cars, session) -> list[draft_cars]`
    - Inside BOOKING's create transaction.
    - Negative balance → carried into the first paying car's `total_amount` as
      `wallet_due_carried`.
    - Positive balance → `wallet_applied` (debited now).
  - `on_booking_cancelled(cars, *, charge_amount, plan_car_ids, actor, session) -> dict`
    - Wallet credit/debit per rule 1.2; returns the wallet line for messages.
  - `on_price_change(booking_before, booking_after, *, actor, reason, session) -> dict`
    - Overpaid → wallet credit; voids open links/orders; recomputes
      `payment_status`.
  - `settle_captain_wallet(booking_ids, session=None)`
    - Idempotent delta posting. BOOKING calls it at completion; MONEY calls it
      after cash/QR collection.
- Collection paths:
  - create-order, link, captain QR, verify, webhook, sweep and collect-cash all
    use **`amount_due`**;
  - partial payments are allowed;
  - overpayment → wallet;
  - `amount_paid` is updated by guarded `$inc` with order-id idempotency.
- Custom-plan payment hooks (spec from PLANS):
  - orders, links and cash rows carry `custom_plan_id` (+ revision,
    `service_center_id`);
  - activation dispatch calls `CustomPlanService(db).activate_from_payment(order)`;
  - `_target_still_payable` / `_link_is_moot` ask
    `CustomPlanService(db).still_payable(order)`.
- Migration: open `customer_charges` → wallet debit rows (idempotent, at boot).

### BOOKING (owner: booking_service.py, booking_schema.py, booking_routes.py, booking_controller.py, booking_policy_service.py, profile_service.py, models/booking.py)
- **Customer cancel** per 1.2: calls `MoneyService.on_booking_cancelled`.
- **Customer edit endpoint** `PATCH /bookings/{id}` or `/bookings/group/{gid}`
  per 1.3: calls `MoneyService.on_price_change`.
- **On-site add-ons** `POST /bookings/{id}/add-services` (captain / manager)
  per 1.4: calls `MoneyService.on_price_change`.
- **Create:** calls `MoneyService.apply_wallet_at_create` inside the create
  transaction. This replaces `customer_charges.claim_open`.
- **Completion:** calls `MoneyService.settle_captain_wallet`. Completion no
  longer marks cash bookings paid; collection does (1.5).
- **Plan rules:**
  - plan-car cancel 1-hour rule;
  - `_subscription_discount` waives every `covered_service_ids` returned by
    `plan_consumption` (custom passes);
  - `QuickBookingLine` gains optional `vehicle_id` + `subscription_id` (explicit
    car-bound pass).
- **Manager alerts:** `_manager_recipients` drops a deleted/invalid `manager_id`
  so the admin fallback works; per-manager errors are logged.
- **Address side door:** address snapshot on booking; `AddressService.update`
  never moves live bookings.
- Timestamps serialised with an offset (`manager_discount_at`,
  `tip_updated_at`, `refunded_at`, `logged_at`, charge history `at`).

### PLANS (owner: subscription_service.py, society_service.py, new custom_plan_service.py + routes + schemas, kpi_service.py, manager_dashboard_service.py, subscription_routes.py)
- Custom plan per 1.6:
  - `custom_plans` collection;
  - per-car passes with `total_by_service` / `remaining_by_service`;
  - `plan_consumption` returns `{"by_service": {...}, "covered_service_ids": [...]}`;
  - `commit_consumption`, `restore_consumption` and the clamp support
    `by_service`.
- Generalised `UserSubscriptionService.extend_pass` (+ route
  `POST /subscriptions/{id}/extend`, audit `EXTEND_PASS`):
  - the society route delegates to it;
  - car-claim re-check on revive;
  - `pass_usable_until` / `_extension_fields` no longer society-only.
- `CustomPlanService`: preview, create cart, revise, link, cash,
  `activate_from_payment`, `still_payable`, cancel.
- `/subscriptions/customer/{id}` returns `plan_name`.

### NOTIFY (owner: notification_service.py, whatsapp_service.py, whatsapp_crm_service.py, whatsapp_bot_service.py, whatsapp routes, user_schema.py/user toggle route, config.py WhatsApp settings)
- Manager toggle per 1.7: user field `whatsapp_new_booking_alerts` (default
  true), checked in `notify` for `manager_new_booking`.
- Fixes per 1.7.
- Templates per 1.8, plus events other agents call:

| Event | Params |
|---|---|
| `booking_edited` | services, ref, date, slot, vehicle, new total |
| `wallet_credited` | name, amount, reason, balance |
| `wallet_debited` | name, amount, reason, balance |
| `payment_failed` | services, ref, amount |
| `booking_cancelled_v5` | services, ref, date, slot, wallet line |
| `review_request_google` | name, services |
| `universal_message` | name, message |
| `book_now` | name |
| `plan_expiring_v2` | name, plan, date, washes |
| `manager_new_booking_v2` | name, phone, vehicles, services, when, area |

- `NotificationService.send_review_requests()` sweep for the worker.
- Admin setting `google_review_url`.

### ARCH (owner: main.py, config.py non-WhatsApp settings, deploy-gcp.sh, Dockerfile, docs/ARCHITECTURE.md)
- `RUN_MODE` api | worker | all.
- Deploy two services.
- Wire the new sweeps (review requests, pre-slot reminder) into the worker.
- Write **docs/ARCHITECTURE.md** (complete system description; kept updated
  from now on).

### FRONTEND (wave 2, after backend reports)
- **FE-CUSTOMER:**
  - wallet page and balance lines in quote / confirmation / booking detail;
  - cancel any time before the captain heads out, with the charge preview;
  - edit booking (1-hour lock);
  - pay what's due online;
  - custom-plan card and booking by car;
  - extension display.
- **FE-STAFF:**
  - captain: add services on site, new total, "Collect Cash or Online?" at
    completion;
  - manager: add services, edit flags, customer wallet + payout, custom-plan
    cart builder, extend any pass, WhatsApp toggle;
  - admin: wallet ledger, payouts, templates submit, universal-message
    composer, Google review URL setting.

### QA (wave 3)
End-to-end scenarios and edge cases across all of the above, the full backend
suite, the frontend build, and mobile widths.

## 3. Edge cases the tests must cover
- **Concurrency:**
  - cancel vs pay (online + captain cash at once → one paid, excess to wallet);
  - edit vs assign;
  - edit vs cancel;
  - two edits at once;
  - add-on vs online payment;
  - wallet used by two simultaneous bookings (no double spend);
  - payout vs booking using the same balance.
- **Customer cancel boundaries:**
  - at 4 h, 1 h, after slot start;
  - just before the captain heads out vs at the same moment.
- **Plan cars:**
  - cancel at 61 min vs 59 min; mixed visit;
  - edit of a plan booking (service change refused).
- **Wallet balance:**
  - negative balance carried into a plan-covered (₹0) booking → payable;
  - positive balance larger than the booking → wallet covers it fully (paid,
    no captain collection).
- **Edits and add-ons:**
  - address change to another center refused;
  - edit inside 1 h refused (server-side, not just UI);
  - add-on after completion on a prepaid booking → due collected by QR →
    captain wallet unchanged except the platform share of cash.
- **Custom cart:**
  - two cars, quotas per service;
  - booking a service not in the car's quota → paid;
  - extension cap 10;
  - customer can't extend;
  - quota race (two bookings for the last Deep Cleaning).
- **Worker split:**
  - API alone serves bookings while the worker is down;
  - sweeps resume when the worker returns;
  - no double sweeps with 2 workers by mistake (lease).
