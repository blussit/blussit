# Blussit Contact Card — Build Specification (V1: Indore)

**Status:** design, not built. Revised 2026-10-13: **V1 is Indore only,
delivered by our own team.** Pan-India sales with Shiprocket are V2 (§18).

**Providers:** Exotel (masked calls) is the only new one. Razorpay, Meta
WhatsApp, Google Routes and the address → center lookup are reused as they
are.

**What changes in the product:**
- the landing page and a `/contact-card` page;
- the customer app (Contact Cards);
- the manager portal;
- the admin portal;
- a public scan page.

Items marked **(confirm)** must be checked with Exotel during setup.

## Contents

1. V1 rules
2. Roles
3. User journeys
4. State machines
5. Pricing, discount, delivery charge, coverage
6. Printing — admin print designer
7. Linking, card details, number change, hand over
8. The scan page
9. Masked calling — Exotel
10. Notifications
11. Data model
12. API
13. Screens
14. Changes to existing code
15. Jobs, settings, security, tests
16. Activity, logs, reports — who sees what
17. Rollout and open decisions
18. V2 — pan-India with Shiprocket (later)

---

## 1. V1 rules

1. **A separate product.** It is not a wash, not a booking add-on, and
   captains don't handle it. Nobody is pushed to buy it.
2. **Indore only.** The delivery address must be inside our coverage (the
   same address → center lookup bookings use). Anyone outside gets "We
   Deliver In Indore For Now" and is saved as a coverage lead (existing
   `coverage_leads`).
3. **Our team delivers by hand.** The order goes to the center that covers
   the address, and its manager arranges delivery.
4. **Admin sets all prices** (Admin → Contact Cards → Settings):
   - card price;
   - discount on it (percent or flat ₹);
   - maximum cards per order;
   - delivery: base fee, free km from the center, then ₹ per km.
   The delivery settings are separate from the wash distance charge.
5. **Customer orders are online only** (Razorpay). A manager may take cash
   only on an in-person sale (Hand Over A Card).
6. **One order, any number of cards (up to the max).** No card is picked at
   purchase. The card handed over becomes the customer's when they **scan
   it in our app**.
7. **Card design comes from admin.**
   - Admin uploads a sample card image with its length × breadth, and marks
     the blank place for the QR and the place for the Card ID.
   - Every **Generate** creates brand-new cards: a unique QR and a unique
     letters-and-digits Card ID on each one.
8. **A card links once.** Only a card nobody owns can be linked.
9. **The card belongs to the number it calls.** A change of number needs an
   OTP from the new number, and moves the card to that account.
10. **The owner's number never leaves our server**, except to Exotel (to
   connect a call) and Meta (to deliver a WhatsApp).

---

## 2. Roles

No new role. A buyer is an ordinary `customer`.

| Who | Does |
|---|---|
| **Visitor** | sees the card on the landing page and `/contact-card`; **Get Your Card** → OTP login |
| **Customer** | buys cards; sees order status; scans a received card to activate it; edits details; pauses or retires a card; changes its number; sees its activity |
| **Scanner** (stranger at the car) | `/c/{card_id}` without login: **Call Owner** · **Send An Alert** · **Emergency** |
| **Manager** | the orders of their center: **Out For Delivery** → **Delivered**; **Hand Over A Card** (walk-in / phone order: scan + form + customer's OTP; cash or online); sees whether each card from their orders is activated (no logs, §16.5) |
| **Admin** | everything: all orders (any center, reassign center, cancel/refund), print designer + print runs, find/retire/void any card, settings, calls and cost, misuse reports |

The manager uses the same endpoints as the admin, filtered by `center_id`.
There is no card stock per center: cards are taken from the office pile.

---

## 3. User journeys

### J1 — Buy

1. Tap **Get Your Card** on the landing section, `/contact-card` or the app.
   Log in with OTP if needed (a new number gets the name step and an
   account, using the existing path).
2. Checkout `/app/contact-cards/buy`:
   - quantity;
   - delivery address (saved addresses, or **Add Address** with the map pin,
     as for bookings);
   - the bill is computed on the server:
     `Cards ₹299 × 2 = ₹598 · Discount −₹100 · Delivery (6.4 km) ₹14 = ₹512`.
   - An address outside coverage is refused, and a coverage lead is saved.
3. **Pay** with Razorpay (purpose `contact_card`) → order `paid` →
   - customer WhatsApp "Order Confirmed — we'll deliver in 1–2 days";
   - manager of that center: bell + WhatsApp (their existing new-booking
     switch);
   - admin: bell.

### J2 — Deliver (manager)

**Manager → Contact Cards → Orders**:
- the order shows the customer, phone, address (**Open In Maps**), quantity
  and how it was paid.
- **Out For Delivery** → the customer gets a WhatsApp.
- On handing it over: **Delivered**. The customer gets a WhatsApp: "Scan
  your card in the app to activate it."

### J3 — Activate (customer scans in our app)

1. **App → Contact Cards** → **Scan Your Card** (the camera opens), or **Type
   The Card ID** (printed next to the QR, e.g. `K7QM-4XPA`).
   - If they used the phone's own camera instead, `/c/{card_id}` says "New
     Card — Activate It" → login → the same flow.
2. The server checks that the card is `unassigned` and that the customer has
   a paid order with linked < quantity.
   - It links the card in one transaction.
   - The first link also marks the order `delivered`, if the manager hasn't
     yet.
3. **Card details**, pre-filled from the account: name, calling number,
   WhatsApp number if different, emergency contact, car (optional, from
   Garage or typed), consent → **Activate**.
4. WhatsApp: "Card active — calls come from {ExoPhone}, save it as Blussit
   Card." (the number in use at that moment, read from admin settings)
5. When every card on the order is linked → order `completed`.

### J4 — Hand Over A Card (manager / admin; walk-in or phone order)

1. Scan the card.
2. Enter the customer's phone; the form is pre-filled if the account exists.
3. Fill in the card details.
4. Payment: **Cash** / **Payment Link** / **Free** (with a reason).
5. **Send OTP** → the customer reads it out → **Activate**.

The server, in one transaction:
- creates the customer if new;
- creates the order (`source: hand_over`, `completed`);
- links the card.

With **Payment Link**, the card is `reserved` until the link is paid (the
existing Razorpay link flow), then activates. If the link expires, the card
returns to `unassigned`.

### J5 — Change the card's number

**Change Number** → new number → OTP → the card moves to that number's
account (created if needed) with all its details. The WhatsApp number is
cleared for the new owner to set.

The old account keeps it listed, read-only: "Moved To 98•••••12 On 13 Oct
2026". Both sides get a WhatsApp.

### J6 — Lost card / car sold

**Retire Card** → the code is dead forever. **Order A Replacement** → J1.

### J7 — Cancel

- **Customer:** **Cancel Order** while it is still `paid` (not yet out for
  delivery).
- **Admin:** may cancel at any time.

Refund: marked due, then paid in the Razorpay dashboard. The existing manual
`refund_due → refunded` tracking applies.

### J8 — Someone scans a card on a car → §8, §9

---

## 4. State machines

**Order** (`contact_card_orders.status`)

```
 awaiting_payment ─► paid ─► out_for_delivery ─► delivered ─► completed (all N linked)
        │             │            │
        ▼             ▼            ▼
    cancelled     cancelled    cancelled (admin only) → refund due
 Hand over: created as completed, or awaiting_payment → completed when the link is paid
```

- Linking is allowed on any `paid`, `out_for_delivery` or `delivered` order
  with room. The first link moves `paid`/`out_for_delivery` → `delivered`.
- Transitions are listed in `_ORDER_TRANSITIONS`. Each change is a guarded
  `find_one_and_update` on the expected status and is appended to
  `history[]`.

**Card** (`contact_cards.status`)

```
 unassigned ──(customer scan / staff hand over)──► active ⇄ paused ──► retired (final)
     │      └─(hand over by link)─► reserved ─(paid)─► active
     │                                  └─(link expired)─► unassigned
     └──(admin: misprint, damaged, print run voided)──► void (final)
```

`unassigned → active` is one guarded update (`{card_id, status: "unassigned"}`
in the filter), so two simultaneous scans can't both win. Codes are never
reused.

---

## 5. Pricing, discount, delivery charge, coverage

Everything here reuses existing code:

1. **Coverage + center:** the address → center lookup used by bookings
   (zones → nearest center → pincode). If no center covers the address →
   400 `OUT_OF_COVERAGE` + `coverage_leads` row.
2. **Distance:** `BookingService.charge_distance_km(center, address,
   straight_km)`. It returns the road distance (Google Routes, cached in
   `road_distance_cache`) with the existing fallback. Move it to a shared
   helper in `route_service.py` if calling it from the card service is
   awkward.
3. **Delivery charge:** `delivery_base_fee` +
   `pricing_service.compute_travel_quote(distance_km, delivery_free_km,
   delivery_per_km_rate)["charge"]`, all from `settings.contact_card`. This
   is the same rounding as the wash charge, but separate rates.
4. **Card price and discount** (admin settings):
   - `price` = the price per card before discount (whole ₹);
   - `discount_type` = `none` / `percent` / `flat`;
   - `discount_value` = the % (0–90) or ₹ per card (less than `price`);
   - `unit_price` = `price` − discount, rounded half-up to whole rupees,
     never below ₹1.
   The landing page and app show `price` struck through next to `unit_price`
   when a discount is on ("₹299 ₹199 · 33% Off").
5. **Bill:**
   - `cards_total = price × quantity`;
   - `discount = (price − unit_price) × quantity`;
   - `total = cards_total − discount + delivery_charge`.

   Then:
   - Quantity must be 1 to `max_per_order`.
   - `POST /contact-cards/orders/quote` and order create run the same
     function.
   - Create takes `expected_total`; a mismatch gives 409 `PRICE_CHANGED`
     (admin changed a price while the customer was paying).
   - Price, discount, distance, delivery charge and center are **frozen on
     the order**, so later setting changes never alter a placed order.
6. **Admin settings changes** are audited (`CONTACT_CARD_SETTINGS`, before
   → after) and take effect for new quotes immediately.

---

## 6. Printing — admin print designer

### 6.1 What each card carries

The printed card has only **two things on it besides the design**: the QR
and the Card ID.

| Field | Example | Where | Rules |
|---|---|---|---|
| `card_id` | `K7QM-4XPA` | **printed** in the ID box; **inside the QR** as `HTTPS://BLUSSIT.COM/C/K7QM4XPA` | 8 characters from `23456789ABCDEFGHJKMNPQRSTVWXYZ` (no 0/O, 1/I/L, U — nothing to misread). Always mixes letters and digits (a draw with only one kind is redrawn). Random from `secrets` (~39 bits, ~800 billion possibilities), **unique index**. Shown grouped `XXXX-XXXX`; the dash and case are ignored on input |
| `dial_code` | `48217730` | **not printed**; only inside the scan page's **Call Owner** link | 8 random digits, first digit 1–8 (9 is the emergency prefix), unique. Needed because a phone keypad can't type letters for Exotel |
| `print_run_id`, `position` | run 12, card 37 | internal | for "which sheet was this card on" |

- **Unique every time.** Every **Generate** mints brand-new cards. A Card
  ID is never reused, not even after a card is voided or retired. The
  unique index on `card_id` makes a repeat impossible; on the rare clash the
  server simply redraws.
- **Fallback when the QR is damaged.** `blussit.com/c` has an **Enter Card
  ID** box that opens the same scan page. The customer's **Type The Card
  ID** does the same when activating.
- **QR:** encoded in upper case (alphanumeric mode, a sparser code that
  scans better through glass), error correction H, drawn as vector, at
  least 20 mm wide (the editor warns below that).

### 6.2 Step 1 — Card Template (Admin → Contact Cards → Templates; set once, reuse)

```
┌──────────────────────── New Card Template ─────────────────────────┐
│ Name [Windscreen Card 2026]                                        │
│ Sample Card  [Upload Image]   (PNG/JPG, the full card design with  │
│                                blank spaces left for QR and ID)    │
│ Card Size    Length [86] mm   Breadth [54] mm                      │
│                                                                    │
│   ┌────────────────────────────────────────┐                       │
│   │  BLUSSIT        ┌─────────┐            │   drag / resize:      │
│   │  Scan to reach  │   QR    │            │   ▣ QR box (square)   │
│   │  the owner      │  here   │            │   ▭ ID box            │
│   │                 └─────────┘            │                       │
│   │                 [ ID here ]            │                       │
│   └────────────────────────────────────────┘                       │
│ ID Text   Size [9] pt  Colour [#000000]  Align [Centre ▾]  Bold [✓]│
│ Preview with a sample ID: K7QM-4XPA            [Save Template]      │
└────────────────────────────────────────────────────────────────────┘
```

- The admin uploads the **example card** (the finished design with the
  QR and ID areas left blank) and enters its **length × breadth in mm**.
  The image is stretched to exactly that size.
- The admin draws the **QR box** (kept square) and the **ID box** on the
  image by dragging and resizing. Positions are stored in mm from the card's
  top-left, so they print exactly where they were placed. The QR box can
  also have a white background if the artwork is dark there.
- **ID text style:** size, colour, alignment, bold.
- **Quality check:** the editor warns if the image is below ~300 DPI at that
  card size (e.g. an 86 mm card needs ≥ 1,016 px wide), if the QR box is
  below 20 mm, or if a box runs off the card.
- Templates are saved and reusable. The image goes to the R2 public bucket.
  A template already used by a print run can't be edited, only copied (so
  an old run can always be reprinted the same).

### 6.3 Step 2 — Generate Cards (Admin → Contact Cards → Print → Generate)

The admin answers three questions, then sees a live sheet window:

```
┌──────────────────────────── Generate Cards ─────────────────────────────┐
│ Template   [Windscreen Card 2026 ▾]   (86 × 54 mm)                      │
│ Number Of Cards  [200]                                                  │
│ Page Size  [A4 ▾]  210 × 297 mm   (A4 · A3 · 12×18 in · 13×19 in · Custom)│
│ Cards Per Page   Across [2]  Down [5]   [Fit Max]   Rotate Cards [ ]     │
│ Margins  Top [10] Left [10] mm    Gap  Across [4] Down [4] mm           │
│ Cut Marks [✓]   Centre On Page [✓]                                      │
├──────────────────────────── Sheet Preview ──────────────────────────────┤
│   ┌──────┐ ┌──────┐                                                    │
│   │ ▣ ID │ │ ▣ ID │     page 1 of 20 · 10 cards per page               │
│   └──────┘ └──────┘     click a slot to skip it (half-used sheets)     │
│   ┌──────┐ ┌──────┐                                                    │
│   │ ▣ ID │ │ ╳    │  ← skipped slot                                     │
│   └──────┘ └──────┘                                                    │
│   ...                                                                   │
├─────────────────────────────────────────────────────────────────────────┤
│ 200 cards → 21 pages (last page: 2 cards)        [Generate & Download PDF]│
└─────────────────────────────────────────────────────────────────────────┘
```

- **Number of cards**, **page size** (presets or custom mm), and **cards
  across × down**. **Fit Max** computes the largest grid that fits.
  **Rotate Cards** turns cards 90° when that fits more on the page.
- **Live preview** of the real sheet, drawn to scale with the template
  image and placeholder QR/ID. Every margin, gap or count change redraws it
  instantly. A red warning shows if the grid doesn't fit the page.
- **Slots:** click any slot to skip it (e.g. reusing a half-printed sticker
  sheet). Skipped slots apply to every page.
- **Pages** = number of cards ÷ active slots per page, rounded up. The last
  page is filled from the top-left.
- **Generate & Download PDF:**
  1. The browser sends `{template_id, count, sheet: {page, across, down,
     margins, gaps, rotate, skipped_slots, cut_marks}}` to `POST
     /admin/contact-cards/print-runs`.
  2. The server mints `count` new cards (`unassigned`, unique `card_id` +
     `dial_code`, `position`), assigns the next `run_no`, stores the print
     run with a copy of the template and the sheet settings, and returns
     the Card IDs in order. This is one bulk insert; any unique clash is
     redrawn and retried.
  3. The browser builds the PDF:
     - the template image at exact mm size;
     - a vector QR in the QR box (the existing `qrcode` package);
     - the Card ID text in the ID box;
     - cut marks if chosen.
     It uses `pdf-lib`, so the preview and the PDF use the same drawing
     code. A footer (outside the cut area) prints `Run 12 · Page 3/21`.
  4. The file `blussit-cards-run-12.pdf` downloads. Print at **100% / Actual
     Size** (the page says so; "Fit to page" would shrink the cards).

### 6.4 Print runs (Admin → Contact Cards → Print)

| Column | |
|---|---|
| Run, date, by, template, cards, pages | |
| Status counts | unassigned / active / void |
| **Download Same File** | rebuilds the identical PDF from the stored IDs and settings, for when a printout went wrong. It never mints new cards; new cards always come from **Generate** |
| **Card List (CSV)** | `card_id, url` for a print shop that prints variable data itself |
| **Void Run** | voids every still-unassigned card of the run (lost or misprinted sheets); linked cards are untouched |

- Limit: 2,000 cards per run.
- Every generate, download and void is audited
  (`CONTACT_CARD_PRINT_RUN_*`). The files contain live codes, so they are
  admin-only.
- Before the first big run: print one page, cut it, stick a card on a car
  and scan it in sunlight, at night and through tinted glass.

---

## 7. Linking, card details, number change, hand over

### 7.1 Card details

| Field | Required | Rules |
|---|---|---|
| Owner name | yes | from the account; used in messages and staff screens; **never shown on the scan page** |
| Calling number | yes | always the owning account's phone; changed only by **Change Number** |
| WhatsApp number | no | if different from the calling number, an OTP to it is needed |
| Emergency contacts | no, max 2 | name + phone; each gets a WhatsApp saying they were added, with a remove link |
| Car | no | from Garage or typed: name, colour, plate. Scan page shows "Hyundai i20 · White · MP09 ••••12" |
| Calls / alerts / quiet hours | settings | **owner-managed** per card. Quiet hours default **10:00 PM – 7:00 AM**; the owner can change the times or turn them off (§9.2). Calls on/off and alerts on/off are owner switches too |

### 7.2 Link checks (`POST /contact-cards/link`)

1. Card by `card_id` (dash and case ignored). Unknown → "Not A Valid Card". Not
   `unassigned` → "This Card Is Already Linked" (the same message for
   every state).
2. The customer's oldest linkable order (§4). None → "We Couldn't Find An
   Order For This Card".
3. In one transaction:
   - the card becomes `active` with owner, order, details, `consent_at`,
     `linked_by: self`;
   - the order gets `linked_count + 1` and the card in `card_ids`, plus
     `delivered`/`completed` when due;
   - a history row is written.
4. "Card Active" WhatsApp.

At most 10 link attempts per customer per hour, so nobody can guess codes.

### 7.3 Change number

1. `POST /contact-cards/{id}/change-number` `{phone}` → OTP (purpose
   `contact_card`).
2. `{phone, otp, name?}` → in one transaction:
   - `ensure_customer_by_phone`;
   - set `owner_id` and `call_phone`;
   - clear `whatsapp_phone`;
   - push to `transfers[]`.
3. Audit + WhatsApp to both sides.

A staff number is refused.

### 7.4 Hand over

`POST /staff/contact-cards/hand-over/otp` then `/hand-over`. The order is
created with `center_id` = the manager's own center (admin picks a center or
none). A manager can't hand over on behalf of another center.

---

## 8. The scan page — `/c/:cardId`

Public, no login, `noindex`, a small lazy chunk.
`GET /api/v1/public/cards/{card_id}` returns only the car hint, the enabled
actions and `{exophone, dial_code}` (the dial code only builds the Call link; it is not printed). It never returns a name, phone number,
address or full plate.

| Card state | Page shows |
|---|---|
| unknown | "Card Not Found" + **Get Your Own Card** |
| `unassigned` / `reserved` | "New Card — Activate It" → login → J3 |
| `active` | car hint · **Call Owner** · **Send An Alert** · **Emergency** · footer **Get Your Own Card** · **Book A Wash** |
| `active`, quiet hours | Call hidden ("Owner Has Calls Off Until 7:00 AM") |
| `paused` | "Owner Is Not Taking Contact Right Now" · Emergency only |
| `retired` / `void` | "This Card Is No Longer Active" |
| the owner, logged in | **Manage This Card** |

- **Alert reasons** are a fixed list with no free text: Lights On · Blocking
  My Car · Window Or Sunroof Open · Being Towed · Alarm Ringing · Tyre Flat ·
  Something Else Is Wrong.
- **Share My Location** is optional and asked only when tapped.
- Every visit and action → `contact_card_events`. A logged-in owner opening
  their own card is not counted as a scan.

---

## 9. Masked calling — Exotel

### 9.1 Call flow

**Setup:** an Exotel account + KYC + an ExoPhone. The same call flow is attached
to whichever ExoPhone is in use (§9.3):

```
 Greeting "Blussit Contact Card."
  └ Gather up to 9 digits, finish on # (sent automatically by the scan page's Call link)
     └ Connect: number(s) from URL  GET /api/v1/telephony/exotel/dial-target?CallSid&From&digits&token
               max 180 s · caller ID = ExoPhone · whisper "Call about your car via Blussit" (confirm)
               status callback → POST /api/v1/telephony/exotel/status
     └ not connected → "The owner could not be reached. Scan the card to send an alert."
```

- **Call Owner** = `tel:+91XXXXXXXXXX,,48217730`: the phone keys in the
  card's hidden dial code after a pause. If no digits arrive (a phone that
  ignores the pause, or someone calling the number directly), the IVR says
  "Please scan the card and tap Call Owner" and hangs up. Callers always
  start from the scan page, so the card only needs the QR + Card ID.
- **Emergency** = `9` + the dial code (9 digits). Both links end with `#`
  (written `%23` in the `tel:` link) so Gather stops at once and can tell 8
  digits (normal) from 9 (emergency) **(confirm Gather's finish-on-key with
  Exotel)**. Dial codes are drawn so a normal one never starts with `9`.

**`dial-target`** must answer within ~2 s, using indexed reads only.

1. It first inserts a call row keyed by `CallSid` (unique), so retries are
   harmless.
2. Checks, in order:
   - URL token;
   - card `active`;
   - caller not blocked;
   - caps (§9.2);
   - calls on and not in quiet hours.
3. If all pass, it returns `call_phone`. Each refusal plays the refuse prompt
   and the reason is stored.

**Emergency** skips quiet hours and returns the owner, then the emergency
contacts, each of whom also gets the emergency WhatsApp.

**Status callback:** stores the outcome, duration and price (idempotent).
On no answer, the owner gets the missed-call WhatsApp.

**Exotel off or down:** the admin switch `calls_enabled` hides Call Owner;
alerts still work.

**(confirm)** with Exotel: Connect's dynamic-number URL, the digits
parameter, whisper, and sequential numbers for Emergency.

### 9.2 Quiet hours and call limits

**Quiet hours (owner-managed).**
- Every card starts with **10:00 PM – 7:00 AM**: the default comes from
  admin settings (`default_quiet_hours`) and is copied onto the card when it
  is activated.
- The owner changes it on **App → Contact Cards → card → Quiet Hours**:
  start and end time (12-hour pickers; stored as 24-hour IST `"22:00"`), or
  **Off**.
- Overnight ranges (start later than end) wrap past midnight.
- Changing the admin default only affects cards activated afterwards; it
  never overwrites a time an owner chose.
- During quiet hours: **Call Owner** is hidden on the scan page ("Owner Has
  Calls Off Until 7:00 AM") and `dial-target` refuses normal calls.
  **Alerts** and **Emergency** still go through.

**Call limits (mandatory, admin-managed).** Every call checks all of them,
and the owner cannot switch them off.

| Limit | Default | Admin can set | Why |
|---|---|---|---|
| Calls per card per hour | 3 | 1–10 | stops one prankster ringing repeatedly |
| Calls per card per day | 8 | 1–30 | caps the worst day for an owner |
| Calls per caller number per day (all cards) | 5 | 1–20 | stops one person ringing many cars |
| Call length | 180 s | 60–600 s | ends long nuisance calls; caps Exotel cost |
| Alerts per card per hour | 6 | 1–20 | same for WhatsApp alerts (+ 10-min de-dup per reason) |

- Emergency calls count toward the same caps but get their own extra
  allowance (2 per card per hour), so a real emergency is never blocked
  by earlier prank calls.
- When a limit is hit:
  - the scan page shows "Too Many Calls To This Car Right Now — Send An
    Alert Instead";
  - `dial-target` refuses with the reason stored (`refused_cap_card_hour`,
    `refused_cap_caller_day`, …);
  - the owner sees it in the activity log ("3 calls blocked by the limit").
- Admin sees refusals by reason on **Calls & Cost**. A caller hitting the
  caller limit on 3 days in a row is flagged for a **global block**.
- Counters: one `$inc` document per `{limit}:{card or caller hash}:{window}`
  in `contact_card_rate`, with a TTL. They are checked before the owner's
  number is returned, and only connected or attempted calls count.
- Limit changes are audited (`CONTACT_CARD_SETTINGS`) and apply to the
  next call.

### 9.3 The ExoPhone is an admin setting

The Exotel number can change: Exotel may reassign it, or we may switch to a
better one. So it is **never** hard-coded, never in an env var and never
printed on the card.

- **Admin → Contact Cards → Settings → Calling Number**:
  - the number in use (e.g. `+91 731 XXX XXXX`);
  - **Change Number** (validated as an Indian number, confirm dialog);
  - the history of past numbers with dates and who changed them (audited
    `CONTACT_CARD_EXOPHONE`).
- **Everything reads it live.** The scan page's **Call Owner** link
  (`tel:{number},,{dial_code}`) and the "save this number" line in WhatsApp
  messages are built from the setting at that moment. A change takes effect
  on the very next scan, and no card needs reprinting.
- **Old number keeps working while Exotel still routes it:** `dial-target`
  accepts a call on any number, because Exotel sends the flow request no
  matter which ExoPhone was dialled. Calls in the log record which number
  they came in on (`exophone`).
- **On change, admin may tick "Tell Card Owners"** → the
  `card_number_changed` WhatsApp ("Calls about your car now come from
  {{1}} — save it as Blussit Card.") goes to every active card owner,
  queued and rate-paced through `whatsapp_queue`.
- **Checklist shown beside the field:** "Attach the Blussit call flow to the
  new number in Exotel, then make a test call from **Test Call** (calls the
  admin's phone through the flow) before saving."
- If no number is set, or **Calls Off** is set, Call Owner is hidden
  everywhere and alerts still work.

---

## 10. Notifications

WhatsApp utility templates (short), sent via
`NotificationService.notify(wa_event=…)`. They are added to `EVENT_TEMPLATES`
and the local template definitions in `whatsapp_crm_service.py`.

| Event | Template | To | Draft |
|---|---|---|---|
| `card_order_paid` | `blussit_card_order_paid_v1` | buyer | "Card order {{1}} confirmed — {{2}} card(s). We'll deliver in 1–2 days." |
| `card_out_for_delivery` | `blussit_card_out_for_delivery_v1` | buyer | "Your Contact Card is on the way today." |
| `card_delivered` | `blussit_card_delivered_v1` | buyer | "Delivered. Scan your card in the app to activate it." [Activate] |
| `card_link_reminder` | `blussit_card_link_reminder_v1` | buyer | "Your card isn't active yet — scan it in the app." (once, 2 days after delivery) |
| `card_active` | `blussit_card_active_v1` | owner | "Card active on {{1}}. Calls come from {{2}} — save it as Blussit Card." |
| `card_alert` | `blussit_card_alert_v1` | owner | "Someone at your {{1}} says: {{2}}. {{3}}." |
| `card_missed_call` | `blussit_card_missed_call_v1` | owner | "Missed call about your {{1}} at {{2}}." |
| `card_emergency` | `blussit_card_emergency_v1` | owner + emergency contacts | "Emergency reported at {{1}}, {{2}}. Location: {{3}}" |
| `card_moved` | `blussit_card_moved_v1` | old + new owner | "Contact Card {{1}} is now on {{2}}." |
| `card_emergency_added` | `blussit_card_emergency_added_v1` | emergency contact | "{{1}} added you as their car's emergency contact. Remove: {{2}}" |
| `card_order_cancelled` | `blussit_card_order_cancelled_v1` | buyer | "Card order {{1}} cancelled. {{2}}" |
| `card_number_changed` | `blussit_card_number_changed_v1` | active card owners (only when admin ticks Tell Card Owners) | "Calls about your car now come from {{1}} — save it as Blussit Card." |
| `manager_card_order` | `blussit_manager_card_order_v1` | center manager | "New card order {{1}}: {{2}} card(s), {{3}}." [Open] |

- The in-app bell gets the same events.
- Admin's bell also gets: new order, order not delivered after 2 days, and
  misuse reports.
- Alert de-dup: one WhatsApp per card per reason per 10 minutes (the queue's
  existing key).

---

## 11. Data model

Indexes go in `create_indexes()`. The unique indexes marked ★ go in
`_CRITICAL_INDEXES`.

| Collection | Key fields | Indexes |
|---|---|---|
| `contact_card_templates` | name, `image_url` (R2), `image_px` {w, h}, card {length_mm, breadth_mm}, `qr_box` {x, y, size} mm, `id_box` {x, y, w, h} mm, `id_style` {size_pt, colour, align, bold}, `qr_white_bg`, `locked` (used by a run → copy only), created_by | name |
| `contact_card_print_runs` | `run_no`, `template_id` + template snapshot, `sheet` {page {w, h}, across, down, margins, gaps, rotate, skipped_slots[], cut_marks}, `count`, `pages`, created_by, `downloads[]` {by, at, kind}, `voided_at` | ★ `run_no` |
| `contact_cards` | `card_id`, `dial_code`, `print_run_id`, `position`, `status`, `owner_id`, `order_id`, `reserved_order_id`, `owner_name`, `call_phone`, `whatsapp_phone`, `emergency_contacts[]`, `car` {vehicle_id?, label, colour?, plate?}, `settings` {calls, alerts, quiet_hours}, `linked_at`, `linked_by`, `consent_at`, `transfers[]` {from_owner, to_owner, masked_to, at}, `retired_reason/at`, `stats` {scans, alerts, calls, connected, minutes, notifications, last_scan_at, last_alert_at, last_call_at, last_used_at} (§16.2) | ★ `card_id`, ★ `dial_code`; (`owner_id`, status); `transfers.from_owner`; (`print_run_id`, position); `stats.last_used_at` |
| `contact_card_orders` | `order_number` (CC-10001), `customer_id`, `center_id`, `quantity`, `linked_count`, `card_ids[]`, money {unit_price, cards_total, delivery_km, delivery_charge, total}, `source` (web / app / hand_over), `payment_method` (online / link / cash / free), `payment_order_id`, `free_reason`, `address_id`, `address_snapshot`, `status`, `history[]` {status, at, by}, `delivered_by`, `cancel`, `refund` | ★ `order_number`; (`center_id`, status, created_at); (`customer_id`, created_at) |
| `contact_card_events` | `card_id`, `owner_id`, `type` (scan / alert / call_tap / emergency / report / link_attempt), `reason`, `ip_hash`, `device` (phone / desktop), `location`, `at` | (`card_id`, at); (`owner_id`, at); TTL 180 d |
| `contact_card_calls` | `provider_call_id`, `card_id`, `owner_id`, `exophone`, `caller_hash`, `kind`, `outcome`, `refusal_reason`, `started_at`, `answered_at`, `ended_at`, `duration_s`, `price`, `emergency_tried[]` | ★ `provider_call_id`; (`card_id`, started_at); (`owner_id`, started_at); (`caller_hash`, started_at); TTL 365 d |
| `contact_card_notifications` | `card_id`, `owner_id`, `event`, `recipient_kind` (owner / whatsapp / emergency), `recipient_masked`, `queue_key`, `at` | (`card_id`, at); (`owner_id`, at); TTL 365 d |
| `contact_card_daily` | `_id` = `{card_id}:{YYYY-MM-DD}` (IST), `card_id`, `owner_id`, `date`, counts {scans, alerts, calls, connected, missed, refused, refused_by {reason: n}, minutes, emergency, notifications, notifications_failed}, `cost` | (`date`); (`owner_id`, date) — kept forever |
| `contact_card_blocks` | `owner_id` or null (global), `caller_hash` | unique (`owner_id`, `caller_hash`) |
| `contact_card_rate` | `_id` = `{kind}:{key}:{window}`, `n`, `expire_at` | TTL |

Changes to existing data:
- `payment_orders`: purpose `contact_card` + `contact_card_order_id`.
- `counters`: `contact_card_run`, `contact_card_order`.
- `settings.contact_card` (own endpoint; added to the keys `PUT /settings`
  refuses):
  - `sales_enabled`, `calls_enabled`;
  - `price`, `discount_type` (none / percent / flat), `discount_value`,
    `max_per_order`;
  - `delivery_base_fee`, `delivery_free_km`, `delivery_per_km_rate`;
  - `call_limits` {per_card_hour, per_card_day, per_caller_day,
    max_call_seconds, alerts_per_card_hour} (§9.2);
  - `default_quiet_hours` {start: "22:00", end: "07:00"};
  - alert reasons;
  - `exophone` {number, active_since, history[] {number, from, to, by}}
    (§9.3).
- Audit actions:
  - `CONTACT_CARD_PRINT_RUN_*`;
  - `_HAND_OVER`, `_TRANSFER`, `_RETIRE`, `_VOID`;
  - `_ORDER_STATUS`, `_ORDER_CANCEL`, `_ORDER_MOVE`;
  - `_SETTINGS`, `_GLOBAL_BLOCK`.

Card money is separate from wash money. It never touches the customer
wallet, booking money, captain wallets or wash revenue KPIs.

---

## 12. API (`/api/v1`)

**Public** (no login, rate-limited)

| | Path | Does |
|---|---|---|
| GET | `/public/contact-card/offer` | price, discount, unit price, max per order, `sales_enabled`, delivery base fee + free km + per-km rate |
| GET | `/public/cards/{card_id}` | scan-page data; records a scan |
| POST | `/public/cards/{card_id}/alert` | `{reason, location?}` |
| POST | `/public/cards/{card_id}/event` | call / emergency taps |
| POST | `/public/contact-card/emergency-contact/remove` | `{token}` from the WhatsApp link → that person is removed from the card; the owner is told |

**Exotel** (URL token)

| | Path | Does |
|---|---|---|
| GET | `/telephony/exotel/dial-target` | §9 |
| POST | `/telephony/exotel/status` | §9 |

**Customer**

| | Path | Does |
|---|---|---|
| POST | `/contact-cards/orders/quote` | `{quantity, address_id}` → bill (cards + delivery km/charge) or `OUT_OF_COVERAGE` |
| POST | `/contact-cards/orders` | `{quantity, address_id, expected_total}` → `awaiting_payment` → `POST /payments/create-order` {purpose: "contact_card", contact_card_order_id} |
| GET | `/contact-cards/orders` · `/orders/{id}` | list / detail with history |
| POST | `/contact-cards/orders/{id}/cancel` | while `paid` |
| GET | `/contact-cards` · `/{id}` | my cards (+ moved-away, read-only) / detail |
| POST | `/contact-cards/link` | §7.2 |
| PATCH | `/contact-cards/{id}` | details, settings, pause/resume (a WhatsApp number change needs `otp`) |
| POST | `/contact-cards/{id}/otp` | OTP for a WhatsApp number change |
| POST | `/contact-cards/{id}/change-number` | §7.3 |
| POST | `/contact-cards/{id}/retire` | |
| GET | `/contact-cards/{id}/activity?type=&before=` | owner view (§16.3): calls with per-card caller labels, alerts, daily scan counts, month summary — no numbers, IPs, dates of purchase or cost |
| GET / DELETE | `/contact-cards/{id}/blocks` · `/{block_id}` | blocked callers list / unblock |
| POST | `/contact-cards/{id}/calls/{call_id}/block` · `/report` | |

**Staff** (`require_manager_or_admin`; a manager is filtered to their center)

| | Path | Does |
|---|---|---|
| GET | `/staff/contact-cards/orders?status=&q=` | list |
| GET | `/staff/contact-cards/orders/{id}` | detail |
| POST | `/staff/contact-cards/orders/{id}/out-for-delivery` · `/delivered` | delivery steps |
| POST | `/staff/contact-cards/hand-over/otp` · `/hand-over` | J4 |
| GET | `/staff/contact-cards/lookup?q=` | Card ID / phone / order no |

**Admin** (`require_admin`)

| | Path | Does |
|---|---|---|
| POST | `/admin/contact-cards/orders/{id}/move` · `/cancel` · `/refunded` | reassign center, cancel (refund due), mark refunded |
| CRUD | `/admin/contact-cards/templates` (+ `/{id}/copy`, image upload via the existing upload route) | card templates (§6.2) |
| POST / GET | `/admin/contact-cards/print-runs` · `/{id}` (IDs in order, for **Download Same File**) · `/{id}/void` · `/{id}/cards.csv` | §6.3–6.4 |
| GET | `/admin/contact-cards/cards?status=&q=` | search |
| POST | `/admin/contact-cards/cards/{id}/retire` · `/void` | |
| GET / PUT | `/admin/contact-cards/settings` | all of `settings.contact_card`; changing `exophone` takes `notify_owners: bool` |
| POST | `/admin/contact-cards/exophone/test-call` | rings the admin's phone through the Exotel flow (§9.3) |
| GET | `/admin/contact-cards/overview?from=&to=` | tiles, daily chart, refusals by reason, top cards, flagged callers (§16.4) |
| GET | `/admin/contact-cards/cards?status=&filter=&sort=&from=&to=` | Cards & Users table with stats, purchased / activated / last used; `…/cards.csv` export (audited) |
| GET | `/admin/contact-cards/cards/{id}/activity` · `/daily` · `/notifications` | per-card logs (audited `CONTACT_CARD_LOG_VIEW`) |
| GET | `/admin/contact-cards/callers?flagged=` | abuse view by admin caller label |
| GET | `/admin/contact-cards/notifications?from=&to=&status=` | sent / delivered / failed by day and event |
| GET | `/admin/contact-cards/calls` · `/reports` | call log + cost; misuse reports |
| POST / DELETE | `/admin/contact-cards/blocks` | global blocks |

New error codes: `CARD_NOT_FOUND`, `CARD_ALREADY_LINKED`,
`NO_ORDER_FOR_CARD`, `OUT_OF_COVERAGE`, `ORDER_NOT_CANCELLABLE`,
`PRICE_CHANGED`.

---

## 13. Screens

**Landing page + public**
- **Landing:** new `components/public/landing/ContactCardShowcase.tsx`
  (monochrome style like the other sections):
  - card image;
  - "Your Number Stays Hidden";
  - 3 steps: Order → We Deliver → Scan To Activate;
  - price (with the struck-through price and "% Off" when a discount is on);
  - **Get Your Card**.
  It hides itself when `sales_enabled` is off.
- **`/contact-card`** (prerendered, `src/seo/pages.json`): how it works,
  privacy promise, FAQ, price, "Delivered In Indore", **Get Your Card**.
- **`/c/:cardId`** (`noindex`): scan page (§8).
- **`/c`** (`noindex`): **Enter Card ID** box for a damaged QR → `/c/:cardId`.

**Customer app** — new nav item **Contact Cards**
(`components/customer/CustomerShell.tsx`)
- **`/app/contact-cards`:**
  - **My Cards** (active / paused; moved-away read-only);
  - **Orders** (status chips);
  - big **Scan Your Card** button when an order is waiting for cards;
  - **Get A Card**.
- **`/app/contact-cards/buy`:** quantity (1 to max) · address · bill (cards,
  discount, delivery km/charge) · **Pay**.
- **`/app/contact-cards/orders/:id`:** status steps (Ordered → Paid → Out
  For Delivery → Delivered → Activated 1 of 2), **Scan Your Card**, **Cancel
  Order** (while paid).
- **`/app/contact-cards/:id`:** details form, settings, activity log,
  **Block**, **Report**, **Change Number**, **Pause**, **Retire Card**,
  **Order A Replacement**.
- **Home** (`DashboardHomePage`): a Contact Card tile (buy, or "2 Cards
  Active").
- **Garage:** "Contact Card Active" badge on cars named on a card.

**Manager** — `/manager/contact-cards`, nav in `ManagerLayout.tsx`
- **Orders:** To Deliver / Out For Delivery / Delivered, with customer,
  phone, address (**Open In Maps**), quantity, paid. Buttons **Out For
  Delivery** and **Delivered**.
- **Hand Over A Card.**
- **Find A Card** (own center).
- Dashboard tile: "3 Card Orders To Deliver".

**Admin** — `/admin/contact-cards`, nav in `AdminLayout.tsx`
- **Orders:** all centers, filters, **Move To Center**, **Cancel**, **Mark
  Refunded**.
- **Hand Over A Card**, **Find A Card** (retire/void).
- **Templates** (upload the sample card, set length × breadth, mark the
  QR and ID places — §6.2).
- **Print**: **Generate Cards** (sheet window, §6.3) and past print runs
  (§6.4).
- **Overview** (§16.4 tiles + sales: orders, cards sold, revenue, refunds,
  delivery charge collected, % activated), **Cards & Users**, **Callers**,
  **Notifications** (§16.4).
- **Calls & Cost** (full call log + Exotel cost), **Misuse Reports**.
- **Settings:**
  - **Card Price**, **Discount** (None / % / ₹), **Maximum Cards Per
    Order**;
  - **Delivery**: Base Fee, Free Km, ₹ Per Km after that;
  - a live example bill ("2 cards, 8 km → ₹512");
  - **Calling Number (ExoPhone)** with its change history (§9.3);
  - **Call Limits** and **Default Quiet Hours** (§9.2);
  - Sales On/Off, Calls On/Off, alert reasons.
- Customer drawer: a **Contact Cards** section.

**Shared pieces:**
- `src/api/contactCards.ts`, types in `src/types/index.ts`;
- `components/shared/QrScanner.tsx` (BarcodeDetector + fallback decoder +
  **Type The Card ID**);
- `components/admin/contactCards/TemplateEditor.tsx` (sample card + QR/ID
  boxes) and `SheetDesigner.tsx` (sheet window + PDF build), sharing one
  `drawCard()` used by the preview and the PDF;
- `pdf-lib`.

House rules: Title Case, 12-hour IST times, brand portal white theme,
console components on staff pages.

---

## 14. Changes to existing code

**Backend**

| File | Change |
|---|---|
| `app/core/config.py` | Exotel + hash settings (§15) and validator rules |
| `app/core/database.py` | indexes for §11; ★ into `_CRITICAL_INDEXES` |
| `app/main.py` | mount the card, staff, admin and telephony routers in `include_business_routers()`; add §15 sweeps to `_sweep_once` |
| `app/core/http_client.py` | `exotel` client (read 10 s) |
| `app/core/rate_limit.py` | per-IP limits on `/public/cards/*` |
| `app/services/auth_service.py` | OTP purpose `contact_card`; `OTP_ACCEPTS["contact_card"]` |
| `app/schemas/payment_schema.py` | `CreateOrderRequest.purpose` += `"contact_card"`, `contact_card_order_id` |
| `app/services/payment_service.py` | `create_order` branch → `ContactCardService.payment_quote`; `_apply_order_paid` branch → `ContactCardService.on_order_paid` (park with `_flag_order_attention` on failure); payment-link settle for hand over by link |
| `app/services/booking_service.py` / `route_service.py` | reuse `charge_distance_km` (move to a shared helper if needed) |
| `app/services/pricing_service.py` | reuse `compute_travel_quote` as is |
| `app/services/whatsapp_crm_service.py` | §10 templates into `EVENT_TEMPLATES` + local definitions |
| `app/services/user_service.py` | `delete_user` refused while the customer owns active cards or open card orders |
| `app/services/profile_service.py` | vehicle delete clears `car.vehicle_id` on cards (the label stays) |
| `app/services/coverage_lead_service.py` | `source: "contact_card"` on out-of-coverage leads |
| `app/models/enums.py` | `ContactCardStatus`, `CardOrderStatus`, `NotificationType.CONTACT_CARD` |
| **new** | `models/contact_card.py`, `schemas/contact_card_schema.py`, `repositories/contact_card_repository.py`, `services/contact_card_service.py` (orders, quote, link, details, transfer, hand over), `services/contact_card_print_service.py` (minting, print runs, CSV), `services/telephony_service.py` (Exotel), `routes/v1/contact_card_routes.py`, `contact_card_staff_routes.py`, `contact_card_admin_routes.py`, `telephony_routes.py` |
| `seed.py` | default `settings.contact_card`, one demo template |

**Frontend**

| File | Change |
|---|---|
| `src/App.tsx` | routes `/contact-card`, `/c`, `/c/:cardId`, `/app/contact-cards/*`, `/manager/contact-cards`, `/admin/contact-cards/*` |
| `vercel.json` | `/contact-card` → prerendered page; `/c` and `/c/:cardId` → `/spa.html` + `X-Robots-Tag: noindex` |
| `src/seo/pages.json` | `/contact-card` + sitemap |
| `pages/public/LandingPage.tsx` | add `ContactCardShowcase` |
| `components/customer/CustomerShell.tsx` | **Contact Cards** nav |
| `pages/customer/DashboardHomePage.tsx`, `GaragePage.tsx` | tile, badge |
| `pages/manager/ManagerLayout.tsx`, `pages/admin/AdminLayout.tsx` | nav |
| admin customer drawer | Contact Cards section |
| policy pages (`PolicyKit`) | Terms, Cancellation & Refund and Privacy each gain a Contact Card section |
| `lib/metaPixel.ts` + gtag | Purchase on card payment (once, the same rule as today) |
| `package.json` | `pdf-lib` |

**Docs:** [ARCHITECTURE.md](ARCHITECTURE.md) (collections, flow, routes,
sweeps, settings, Exotel in external services, change log) and
DEPLOYMENT.md (Exotel secrets + flow URL), per the standing rule.

---

## 15. Jobs, settings, security, tests

**Sweeps** (worker reminder loop):

| Sweep | What | Cadence |
|---|---|---|
| undelivered orders | `paid` > 2 days → manager + admin reminder | every 6 h |
| link reminder | `delivered` 2 days ago, not fully linked → `card_link_reminder` once (claim-first) | hourly, 10 AM–7 PM |
| reserved cards | hand-over link expired → card back to `unassigned` | every pass |
| Exotel cost backfill | calls missing a price | hourly |

**Settings** (`core/config.py`):
- `TELEPHONY_PROVIDER` (`log` / `exotel`);
- `EXOTEL_ACCOUNT_SID`, `EXOTEL_API_KEY`, `EXOTEL_API_TOKEN`,
  `EXOTEL_API_HOST` (confirm). The ExoPhone number is **not** an env
  setting; admin manages it in settings (§9.3);
- `EXOTEL_WEBHOOK_TOKEN` (≥ 32);
- `CONTACT_CARD_HASH_KEY` (≥ 32);
- `CONTACT_CARD_PUBLIC_BASE` (`https://blussit.com/c/`).

Locally, the `log` provider plus a dev-only "simulate call" page.

**Limits** (admin-editable, always on; Mongo counters in `contact_card_rate`,
valid across instances; call limits explained in §9.2):

| Limit | Default |
|---|---|
| Calls per card | 3/hour, 8/day |
| Calls per caller number | 5/day |
| Alerts per card | 6/hour + 10-min de-dup per reason |
| Scan-page actions per IP | 20/hour |
| Link attempts per customer | 10/hour |
| Call length | 180 s |

**Privacy:**
- The scan page never returns a name, phone number or full plate.
- Caller numbers and IPs are stored only as HMAC hashes.
- Location only if shared.
- Events expire after 180 days.
- No call recording.
- Linking stores `consent_at`.
- Emergency contacts are told and can remove themselves.
- Print-run downloads are admin-only and audited.

**Tests** (`backend/tests/test_feat_card_*.py`, local replica-set Mongo):
- **Card IDs:** 8 characters from the allowed set, always letters + digits,
  unique across runs, forced-clash redraw; lookup ignores dash/case; a
  print-run download never mints.
- **Orders:** quote = create (discount percent/flat rounding, never below
  ₹1; max per order; base fee + `compute_travel_quote`); settings changes
  don't alter placed orders;
  `OUT_OF_COVERAGE` + lead; `PRICE_CHANGED`; settle once; cancel only while
  paid; manager sees only their center; status transitions.
- **Linking:** two concurrent links → one wins; already-linked refused with
  the same message; needs an order with room; the first link marks
  delivered; the last completes; rate limit.
- **Hand over, transfer, scan page:** hand over needs the OTP and the link
  reservation is released on expiry; transfer needs the OTP, the old owner
  sees it read-only and a staff number is refused; the scan page never leaks
  personal data.
- **Activity + privacy:** every event updates raw row + daily roll-up +
  card stats; owner labels differ per card and admin labels don't; no
  response to an owner or manager contains a caller hash, IP, purchased or
  last-used date; managers get 403 on logs; admin log views are audited;
  roll-ups survive the raw TTL.
- **Quiet hours + limits:** default 10 PM–7 AM copied on activation and
  owner-editable (incl. Off, overnight wrap); admin default change leaves
  existing cards alone; each cap refuses with its reason; emergency
  allowance; limits can't be switched off by an owner.
- **ExoPhone:** the Call link and WhatsApp use the current setting; a change
  applies on the next scan; calls on an old number still route; change is
  audited.
- **Calls and printing:** `dial-target` decisions and latency; status
  callback idempotent; a print-run download never mints.

Frontend: `tsc -b`, build + prerender, Playwright walkthroughs:
- buy;
- manager deliver;
- scan to activate;
- change number;
- hand over;
- print designer → PDF.

---

## 16. Activity, logs, reports — who sees what

### 16.1 Privacy rules (apply everywhere)

1. **The caller is anonymous to everyone in our system.**
   - The caller's phone number and name are never shown to the owner, a
     manager or an admin.
   - We store only an HMAC hash of the number (`CONTACT_CARD_HASH_KEY`).
   - The real number exists only inside Exotel's own call records. Police or
     legal requests go to Exotel, with the `CallSid` from our log.
2. **The owner is anonymous to the caller.**
   - The caller sees only the ExoPhone and the car hint (e.g. "Hyundai i20 ·
     White · MP09 ••••12").
   - No owner name, number, address or full plate ever reaches the scan page
     or the call.
3. **Two caller labels, built so they can't be linked:**
   - **Owner sees "Caller K7Q2":** 4 characters from HMAC(card_id +
     caller_hash). It is stable on that card, so the owner can recognise and
     block a repeat caller. It differs on every other card, so owners can't
     compare notes to track one person.
   - **Admin sees "#3F9A2C":** the first 6 characters of `caller_hash`. It is
     the same across all cards, so admin can spot one person pranking many
     cars.
4. **Scanner (no call):** we store a hashed IP and a device type (phone /
   desktop), plus a location only if they tap **Share My Location**. No
   cookies or tracking IDs on the scan page.
5. **Managers never see call, alert or scan logs.** They only see delivery
   and activation for their own orders.
6. **Admin viewing a customer's logs is audited** (`CONTACT_CARD_LOG_VIEW`:
   who, whose card, when).

### 16.2 What is recorded

| Record | Fields | Kept |
|---|---|---|
| **Call** (`contact_card_calls`) | `CallSid`, card, owner, ExoPhone used, `caller_hash`, `kind` (normal / emergency), started, answered, ended, `duration_s`, `outcome` (connected / missed / busy / failed / refused), `refusal_reason` (quiet_hours / cap_card_hour / cap_card_day / cap_caller_day / blocked / card_paused / calls_off), `price` | 1 year |
| **Alert** (`contact_card_events`, type alert) | card, reason, time, ip_hash, device type, location (if shared), notification ref | 180 days |
| **Scan** (`contact_card_events`, type scan) | card, time, ip_hash, device type | 180 days |
| **Emergency** | as call + alert; which emergency contacts were tried | 1 year |
| **Notification** (`contact_card_notifications`) | card, owner, event (`card_alert`, `card_missed_call`, …), recipient kind (owner / WhatsApp number / emergency contact), masked recipient, time, queue key, `wamid` → delivery status (queued / sent / delivered / read / failed). The `wamid` is copied onto this row when the queue sends; the status comes from `whatsapp_outbox` (updated by Meta's status webhook). **Check during build** that the queue exposes the `wamid` at send time; if not, add a small hook there | 1 year |
| **Card history** | purchased (order paid), delivered, activated (by self / staff), number changes (masked), pauses, retire / void, consent time | forever |
| **Daily roll-up** (`contact_card_daily`) | per card per IST day: scans, alerts, calls attempted / connected / missed / refused, call minutes, emergency count, notifications sent / failed, Exotel cost | forever (small; powers every report) |
| **Card stats** (on `contact_cards.stats`) | totals: scans, alerts, calls, connected, minutes, notifications; `last_scan_at`, `last_alert_at`, `last_call_at`, `last_used_at` (latest of the three) | live |

- Every event does three writes:
  - append the raw row;
  - `$inc` today's `contact_card_daily` document (upsert on `card_id + date`);
  - `$inc` / `$max` the card's `stats`.
- Reports read the roll-ups and stats, never scan raw events. They stay fast
  after the raw rows expire.

### 16.3 What the owner sees (customer app → card → **Activity**)

```
 This Month   4 Calls · 3 Connected · 2 Alerts · 11 Scans
 ─────────────────────────────────────────────────────────
 Today
  📞 Caller K7Q2   4:12 PM   1 min 20 s   Connected        [Block] [Report]
  📞 Caller K7Q2   4:05 PM   —            Missed           [Block] [Report]
  🔔 Alert: Lights On          3:58 PM    📍 View Location
  👁 Card Scanned 3 Times
 Yesterday
  📞 Caller M2X9   11:40 PM  —            Held (Quiet Hours)
  📞 Caller M2X9   11:41 PM  —            Blocked By Limit
  🚨 Emergency      9:15 AM   Owner + Emergency Contact Called
```

- Filters: All / Calls / Alerts / Emergency. History goes back 90 days.
- **Block** / **Report** on any caller. **Blocked Callers** list with
  **Unblock**.
- Scans are grouped per day ("Scanned 3 Times"), never itemised with
  technical data.
- Never shown to the owner: caller numbers or names, IPs, the purchase date,
  the last-used date or any cost.

### 16.4 What the admin sees (Admin → Contact Cards)

**Overview** (date range; default today and the last 30 days):
- **Tiles:**
  - active cards;
  - cards activated today;
  - calls today (connected / missed / refused);
  - alerts today;
  - scans today;
  - emergency events;
  - WhatsApp sent / failed today;
  - Exotel cost today and this month.
- **Daily chart:** calls (connected / missed / refused), alerts, scans.
- **Refusals by reason** (quiet hours, each limit, blocked) — shows whether
  limits are set right.
- **Top cards by calls** this week, and **flagged callers** (hit a limit on 3
  days in a row, or reported).

**Cards & Users** (table; sort, filter, search, CSV export):

| Column | Source |
|---|---|
| Card ID, status | card |
| Owner (name, masked phone → opens customer drawer), car | card |
| **Purchased** date, order no, amount | order (admin only) |
| Delivered, **Activated** date (self / staff) | order / card |
| **Last Used** (latest scan, alert or call) | `stats.last_used_at` (admin only) |
| Calls today / 7 days / 30 days / total, connected % | daily roll-up + stats |
| Alerts, scans (same periods) | daily roll-up + stats |
| Notifications sent / failed | daily roll-up |
| Refused by limit, blocked callers | daily roll-up / blocks |
| Exotel cost (period) | daily roll-up |

Filters:
- status;
- **Never Used** (activated but no scan in 30 days);
- **Not Activated** (delivered more than 7 days ago);
- heavy use (above N calls/day);
- has reports.

**Per-user / per-card detail** (customer drawer → Contact Cards, or the
table row):
- the card's history timeline: purchased → delivered → activated → number
  changes → pauses;
- a **daily table** (calls, alerts, scans, notifications per day);
- the **call log** (time, admin caller label `#3F9A2C`, duration, outcome,
  refusal reason, ExoPhone, cost);
- the **alert log**;
- the **notification log** (event, recipient kind, masked recipient, sent /
  delivered / read / failed, time);
- consent time, blocks, misuse reports.

**Callers** (abuse view):
- caller labels `#3F9A2C` with cards called, calls, refusals and reports
  over a period;
- **Block Everywhere** / **Unblock**, audited.

**Notifications:**
- WhatsApp sent / delivered / failed per day and per event type;
- the failed list with the reason, so admin can see an owner whose WhatsApp
  isn't receiving.

### 16.5 What the manager sees

Only order and delivery information for their center:
- customer name and phone (needed to deliver);
- address, quantity, order status;
- whether each delivered card has been **Activated** (yes / no).

No call, alert, scan or notification data. No purchased / last-used reports.

### 16.6 Security summary

| Threat | Control |
|---|---|
| Owner's number leaking | never in any public response or call; only Exotel and Meta get it, to connect and deliver |
| Caller's number leaking | hash only in our database; owner and admin see labels only |
| Linking callers across owners | per-card owner labels (§16.1) |
| Prank / harassment calls | mandatory call limits, quiet hours, per-owner and global blocks, reports, flagged callers |
| Guessing Card IDs | ~39-bit random IDs; link attempts 10/hour per customer; scan-page actions 20/hour per IP |
| Someone else claiming a card | links only an `unassigned` card, only with a paid order; first link wins (guarded update) |
| Card moved to a stranger's number | needs an OTP from the new number; audited; both sides told |
| Fake Exotel / webhook calls | URL token + idempotent `CallSid` |
| Staff snooping | managers get no logs; admin log views audited; exports audited |
| Data kept too long | TTLs: events 180 days, calls / notifications 1 year; daily roll-ups hold counts only |

---

## 17. Rollout and open decisions

| Step | Scope | Rough effort |
|---|---|---|
| **V1a** | settings, templates + sheet designer, landing section + `/contact-card`, buy + Razorpay + delivery charge, manager delivery, scan to activate, card details, change number, hand over, scan page with **alerts**, notifications, activity logs + daily roll-ups, admin orders / Overview / Cards & Users | ~2.5–3 weeks |
| **V1b** | Exotel calling, call limits, quiet hours, block/report, missed-call + emergency, ExoPhone setting + test call, Callers + Calls & Cost | ~1–1.5 weeks |

Suggested split for two developers in V1a:
- **Backend:** models, orders + payment, linking, transfer, hand over,
  events + roll-ups, notifications, sweeps, tests.
- **Frontend:** landing + product page, customer app screens, scan page,
  manager + admin screens, template editor + sheet designer.

Agree the API shapes in §12 on day one so both sides can work in parallel.

Cards sold in V1a start taking calls the day V1b ships, with no reprint (the
Call link is built from data each card already has).

**Start now:**
- Exotel KYC + ExoPhone;
- submit the §10 templates to Meta;
- card artwork (the sample card with blank QR and ID spaces);
- print 10 test cards and scan them in sunlight, at night and through
  tinted glass.

**Decided (founder, 2026-10-13):** price, discount, max per order and all
delivery prices are admin settings; customer orders are online only and
managers may take cash on in-person sales; card design and size come from
the admin's uploaded sample card with marked QR and ID places; every
Generate makes new cards with a unique QR and a letters-and-digits Card ID.

Also decided: quiet hours are managed by each owner (default 10:00 PM –
7:00 AM); call limits are mandatory and admin-managed; the ExoPhone number is
an admin setting that can change at any time.

Nothing open for V1. For the Exotel account: ask whether the ExoPhone can stay
fixed (most accounts keep it), and how a change is announced, so admin can
update the setting the same day.

---

## 18. V2 — pan-India with Shiprocket (later, not in V1)

Kept here so V1 doesn't block it. Nothing in V1 needs undoing.

- **Coverage:** accept any Indian pincode. `center_id` = null for orders
  outside every center (admin only).
- **Delivery:**
  - `delivery.method` = `hand` (V1 behaviour) or `shiprocket`.
  - A new `shipments` collection: Shiprocket order/shipment ids, AWB,
    courier, label, status, events, freight.
- **Shiprocket calls:** auth token, pincode serviceability (shown at
  checkout), create order, assign AWB, label, pickup, cancel, track.
  Admin gets bulk **Create Shipments / Download Labels / Request Pickup**.
- **Tracking:** a webhook (`/webhooks/shipping-updates`, `x-api-key`, a path
  without the word "shiprocket") plus a backup polling sweep. The order adds
  `packed → shipped → out_for_delivery` and the exception states
  `delivery_failed / returning / returned`. The customer sees one timeline
  combining our steps and the courier's scans.
- **Money:** a flat shipping fee instead of per km, GST/HSN on the order,
  Shiprocket invoice. A Shipping Policy page is required by Razorpay for
  physical goods.
- **Flows unchanged:** linking, card details, number change, the scan page
  and calling are the same in V2.
