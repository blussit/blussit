# BLUSSIT PRODUCTION REMEDIATION REPORT — 2026-10-07

Fixes for every issue in `docs/PRE_PRODUCTION_AUDIT_2026-10-07.md`, on branch
`blussit-v2` (uncommitted). Each code fix has a permanent regression test that
was shown to **fail on the old code** and **pass on the new code**; money,
capacity and authorization fixes have concurrency or cross-user/cross-center
tests. Nothing was run against production.

**Method.** Nine fix agents worked in parallel, each owning a disjoint set of
files (auth, payments, plans/society, booking core ×2, infra, notifications,
ops/admin, frontend ×2). The coordinator relayed cross-file requests, wired the
background jobs, fixed the gaps no agent owned, and ran the integrated suites.

Founder decisions applied (10-07): late-cancellation charge on the account,
added to the next booking, reducible/waivable by managers, visible to admin;
landing shows the cheapest buyable plan; hours 7 AM–7 PM; manager
discount/tip uncapped but counted into the total and visible to admin;
societies aren't switched off; society pass = 30-day period, manager can
extend ≤ 10 days (admin sees it).

---

## 1. Issues Fixed

Evidence for every row: the named regression test(s), failing before the fix
and passing after (some agents proved "before" by reverting only that fix and
re-running; for capacity items the audit's own repro was also re-run).

### Security & accounts

| ID | Sev | Root cause | Fix | Regression tests |
|---|---|---|---|---|
| P0-2 / AUTH-01 | P0 | OTP/widget password reset trusted any stored phone; staff phones never proven; seed staff on placeholder numbers | Code reset only for verified phones; staff only if they verified the phone themselves (`self_verified_phone`); new admin/manager staff reset `POST /auth/staff/{id}/reset-password`; atomic reset write | `test_fix_auth_reset.py` (7) |
| AUTH-05 | P1 | Bot's phone-proof path treated staff as a customer's first proof (wiped password) | `mark_phone_proven`/`ensure_customer_by_phone` refuse staff (`STAFF_ACCOUNT_PHONE`); bot routes staff numbers to CRM only | `test_fix_auth_staff_phone_proof.py`, `test_fix_notify_bot.py` |
| AZ-01, MGR-04 | P1/P3 | "Known to my center" rule only in the 360 view | Central `authz.ensure_customer_in_scope`; applied to addresses, subscriptions, 360, staff customer reset | `test_fix_auth_customer_scope.py` |
| AUTH-02 | P1 | MSG91 binding accepted the token's own payload | Only MSG91's response identifier is trusted | `test_fix_auth_msg91.py` |
| AUTH-03 | P2 | Widget tokens reusable | Single-use (`used_widget_tokens`, TTL) | `test_fix_auth_msg91.py` |
| AUTH-04 | P2 | OTP not bound to purpose | Purpose stored; per-consumer accept list | `test_fix_auth_reset.py::test_code_purpose_is_enforced` |
| AUTH-06 | P2 | Stranger could lock a customer's login OTP | Caps per (phone, requester) + per-phone ceiling | `test_fix_auth_otp_caps.py` |
| CAP-06 | P3 | "inactive" blocked nothing | Inactive/suspended refused at login, token use, refresh | `test_fix_auth_account_status.py` |
| ADM-02 | P2 | Staff creation unaudited, center unchecked | Audited `CREATE_STAFF`; center validated | `test_fix_auth_misc.py` |
| FE-13 (backend) | P2 | Suspended customer → 401 | 400 `ACCOUNT_INACTIVE` | `test_fix_auth_account_status.py` |
| SOC-10 | P3 | Car on a live pass deletable | Refused | `test_fix_auth_misc.py` |
| VAL-2 | P3 | Non-ASCII digits in phones | ASCII-only | `test_fix_auth_misc.py` |
| ENUM-1 | P3 | Login timing revealed accounts | Constant-cost check (18 ms vs 1 s → ~400 ms both). Messages unchanged (UX decision) | `test_fix_auth_misc.py` |
| CAP-01 | P1 | Moved/demoted captain kept and worked old jobs | Staff changes refused while live jobs exist; every captain step (incl. cash collect/QR) re-checks current center; assign re-reads captain in-transaction | `test_fix_ops_staff.py`, `test_fix_coreb_captain.py`, `test_fix_coord_captain_collect.py` |
| CAP-02 | P1 | No arrival-code attempt limit; reusable photos | Lock after 5 wrong codes + manager alert + unlock endpoint; no-GPS flagged; photos must be this captain's fresh upload, used once; too-fast completion flagged | `test_fix_coreb_captain.py` (7), `test_fix_ops_misc.py` |
| CAP-03/04/05/07 | P2/P3 | Inventory abuse; GPS unchecked; suspend stranded jobs; leave over jobs | Center-scoped stock, qty > 0; lat/lng ranges; one roster guard; leave refused over assigned jobs | `test_fix_coreb_captain.py`, `test_fix_ops_staff.py` |
| DATA-1 | P3 | Managers saw full Aadhaar/PAN/bank | Masked to last 4 for managers | `test_fix_ops_wallet.py` |
| VAL-1, VAL-4, VAL-6 | P3 | Unbounded address; `javascript:` attachments; 500 on garbage ids | Bounds; own-upload URLs only; 404 | `test_fix_coord_validation.py` |

### Payments & money

| ID | Sev | Root cause | Fix | Regression tests |
|---|---|---|---|---|
| P0-3 / DEP-02 | P0 | Production could run on Razorpay test keys | App refuses to start in production without `rzp_live_` key, secret and webhook secret; deploy script uses the same validator | `test_fix_infra_config.py` |
| PAY-09 | P1 | Signature trusted for authorized-not-captured payments | Verify fetches the payment; captures if authorized; settles only when captured with matching order/amount; fails closed | `test_fix_pay_b.py::test_pay09_*` |
| PAY-01 | P1 | Pass activated for the car's current type | Priced type frozen on the order; mismatch parks the payment | `test_fix_pay_a.py`, `test_fix_plans_passes.py` |
| PAY-02 | P1 | Expiry sweep cancelled a just-paid booking | `only_if_unpaid` guarded cancel (status + payment_status) | `test_fix_core_payments.py`, `test_fix_infra_loop.py` |
| PAY-03 | P1 | QR link bound to the ₹0 plan car | Links always bound to the exact cars they charge | `test_fix_pay_a.py::test_pay03_*` |
| PAY-04, PAY-05 | P1/P2 | Completion/mark-done guarded on status only | Guarded on payment fields; settle from written doc | `test_fix_core_payments.py` (race ×10) |
| PAY-06 | P2 | Cash completion left links payable | Links voided on cash completion, switch-to-cash and bot cash | `test_fix_core_payments.py`, `test_fix_notify_bot.py` |
| PAY-07, PAY-11 | P2/P3 | Per-user coupon race; missing limit field | Atomic per-user counter; `$ifNull` | `test_fix_pay_a.py` |
| PAY-08, PAY-10 | P2/P3 | Wrong plan revenue | Charged amount stamped on every activation | `test_fix_pay_a.py` |
| PAY-12, PAY-13, FE-S02 | P3/P2 | Stranded mandates; duplicate offer links | Mandate sweep covers failed verifies; offers reused | `test_fix_pay_b.py`, `test_fix_pay_c.py` |
| PAY-14 | P3 | Refunds never reflected | `refund_due` → `refunded` (admin resolve or verified `refund.processed` webhook); excluded from revenue | `test_fix_pay_c.py::test_pay14_*` |
| FE-04, FE-12 (backend) | P1/P2 | Second payable order while first confirming | Order reused or 409 `PAYMENT_CONFIRMING` | `test_fix_pay_c.py::test_fe04_*`, `test_fe12_*` |
| PASS-5, PRICE-07 | P3 | Cash sale left link payable; coupon >100 % | Links voided; validated | `test_fix_pay_*` |
| PRICE-02/04/05/06, PAY-15 | P2/P3 | Duplicate services; silent re-price; coupon/travel on ₹0 car; quote revealed first-time status | Deduped; 409 `PRICE_CHANGED` when higher; first paying car; anonymous quote neutral | `test_fix_core_pricing.py`, `test_fix_coreb_misc.py` |
| PRICE-03 | P3 | Server applied a ₹0 / not-lower first-wash price the site ignores | Same rule both sides: 0 < price < regular | `test_fix_coord_validation.py` |
| ADM-14 + founder | P3 | Plan-wash over-count; discount/tip not visible | `$ifNull`; collections show manager discounts and tips | `test_fix_ops_misc.py`, `test_fix_coord_collections.py` |

### Booking, slots & capacity

| ID | Sev | Root cause | Fix | Regression tests |
|---|---|---|---|---|
| BOOK-02/03/04/06, STATE-02 | P1/P2/P3 | Seat ownership inferred from stale reads | Each booking records `holds_seat` + `seat_key` in the reserve transaction; every release is a guarded claim; restore takes a seat or is refused; visit reschedule atomic | `test_fix_core_seats.py`, `test_fix_core_visits.py` |
| BOOK-01, BOOK-05 | P1/P3 | Timezone-aware dates stored as previous day | One IST calendar-date validator; overlap check inside the create transaction | `test_fix_core_dates.py` |
| SLOT-01 | P1 | Slot length/hours change dropped capacity | Refused while it would strand bookings; unknown slots fail closed (no 999) | `test_fix_core_slots.py`, `test_fix_ops_slots.py` |
| SLOT-02/04/05, STATE-01, MGR-05/06, E2E-03 | P2/P3 | Hold cap per IPv6 address; junk overrides; daily cap ignored; stale self-assign; future mark-done; double history | /64 key; validation; daily cap in availability; guard; refusal | `test_fix_core_slots.py`, `test_fix_coreb_misc.py` |
| Counter safety net | — | No reconciliation | `reconcile_slot_counters` (report nightly; admin repair endpoint); seat-ownership backfill at boot | `test_fix_core_*`, `test_fix_coord_loop_wiring.py` |

### Plans & society

| ID | Sev | Fix | Regression tests |
|---|---|---|---|
| PASS-1, PASS-4, SOC-5 | P1/P3 | Insert-first pass claims (`pass_claims`) | `test_fix_plans_passes.py`, `test_fix_plans_society.py` |
| SOC-2 | P1 | Auto plans versioned by rate card | `test_fix_plans_society.py` |
| PASS-2 | P2 | Upgrade keeps used washes per category; guarded write | `test_fix_plans_passes.py` |
| PASS-3 + founder rule | P3 | Washes usable only inside the pass period (quote, create, reschedule) | `test_fix_plans_passes.py`, `test_fix_coreb_misc.py` |
| Society extension (founder) | — | Manager/admin extend ≤ 10 days, audited, visible | `test_fix_plans_extension.py` |
| SOC-1, SOC-3, SOC-4, SOC-6..9, SOC-11, PASS-6, ADM-13 | P2/P3 | Society revenue in KPIs/collections; switch-off admin-only & refused with live plans; car-bound pass only for its car; lead claim; resident notified; hub link kept; residents KPI; refund trail; cross-center sales hidden; discontinued plans hidden | `test_fix_plans_*`, `test_fix_coord_plans_list.py` |

### Admin & manager safety

| ID | Sev | Fix | Regression tests |
|---|---|---|---|
| ADM-01 | P2 | `PUT /settings` refuses every key with a dedicated endpoint and unknown keys; audited | `test_fix_ops_admin.py` |
| ADM-03, ADM-05, ADM-06 | P2 | Deleting users/centers/services/vehicle types in use refused (deactivate instead) | `test_fix_ops_admin.py`, `test_fix_core_admin.py` |
| ADM-07 | P2 | Working hours validated | `test_fix_core_admin.py` |
| ADM-08, ADM-12 | P3 | Before/after audit on admin edits; inventory/FAQ/testimonials/template sync audited | `test_fix_ops_admin.py`, `test_fix_coord_template_sync_audit.py` |
| ADM-09, ADM-10, ADM-11 | P3 | Wallet adjust captain-only; withdrawal approved → paid; permanent delete never orphans wallet money (purge parks such rows) | `test_fix_ops_wallet.py`, `test_fix_coreb_misc.py`, `test_fix_coord_captain_collect.py` |
| MGR-01 | P2 | Logged job spends a pass only when explicitly asked, own-center/self-serve only | `test_fix_coreb_misc.py` |
| MGR-02/03 | P2 | **By founder decision:** no cap; counted in total; visible to admin with who/when | `test_fix_ops_misc.py`, `test_fix_coord_collections.py` |

### Founder feature — late-cancellation charge

Tier from one function: > 4 h before the slot free · 1–4 h ₹50 · < 1 h ₹80 ·
captain already left ₹100 (amounts in the booking policy, admin-editable).
Charged only when the cancellation is at the customer's request (customer
self-cancel, or staff with "at customer's request"; staff may lower it).
Stored on the account (`customer_charges`), added to the next interactive
booking inside the create transaction (shown in the quote; 409 `PRICE_CHANGED`
explains it after OTP), released if that booking is cancelled, reducible or
waivable by the center's manager or admin (audited, customer notified), visible
to admin. Captain pay never touched. Tests: `test_fix_coreb_charges.py` (30).

### Notifications & failure handling

| ID | Sev | Fix | Regression tests |
|---|---|---|---|
| FAIL-02, FAIL-01 | P2 | Durable WhatsApp queue: idempotent, leased, retried with backoff, dead-lettered; request paths wait ≤ 1 s | `test_fix_notify_outbox.py` |
| DEP-04 | P1 | Production never sends free text outside the 24 h window; template status webhooks; hourly template sync; admin delivery-health view | `test_fix_notify_outbox.py`, `test_fix_notify_bot.py` |
| NTF-07 | P2 | Confirmation "Booking confirmed — Star Wash for your SUV on 09 Oct 2026, …" (no ISO date, no repetition) | `test_fix_notify_outbox.py`, `test_manager_done_jobs.py` |
| NTF-01, NTF-02, NTF-04 | P2/P3 | One templated receipt per settlement; no "booking None"; correct refund copy | `test_fix_pay_c.py` |
| NTF-03, NTF-05, NTF-06 | P3 | "Captain arrived" message; STOP / Meta opt-out saved; no stale reminders | `test_fix_coreb_*`, `test_fix_notify_bot.py` |
| FAIL-03 | P2 | R2 outage → 503 `STORAGE_UNAVAILABLE` (retryable) | `test_fix_coord_storage_outage.py` |
| FAIL-04..07 | P3 | Post-commit side effects isolated; per-sweep budgets and timeouts below the lease; graceful shutdown | `test_fix_coreb_misc.py`, `test_fix_infra_loop.py` |

### Deployment & observability

| ID | Sev | Fix | Regression tests |
|---|---|---|---|
| DEP-06 | P2 | One production validator in the app (Razorpay, WhatsApp, R2, Mongo, CORS, JWT, SMS, unknown enum values) — fails fast, lists every problem | `test_fix_infra_config.py` |
| DEP-05 | P2 | `/api/health` liveness; `/api/ready` pings Mongo + checks critical unique indexes (503 otherwise); deploy uses it | `test_fix_infra_readiness.py` |
| DEP-08 | P1 | New duplicate-booking index built before the old one is dropped; readiness fails if critical indexes are missing | `test_fix_core_*`, `test_fix_infra_readiness.py` |
| DEP-07, DEP-12 | P2/P3 | `.env.*` ignored for gcloud fallback; deploy refuses without `.gcloudignore`; env file parsed safely | `test_fix_infra_deploy.py` |
| DEP-09 | P2 | Request ids, JSON logs in production, global masking of phones/OTPs/tokens/secrets, lease errors logged; Sentry hook ready | `test_fix_infra_observability.py` |
| PERF-02, PERF-03 | P3 | Separate Razorpay poll pool; bounded subscriber/review/performance queries | `test_fix_pay_*`, `test_fix_coord_perf03.py` |

### Frontend (round 1 + round 2)

FE-01/02/11 session handling (expired login no longer bounces public pages —
confirmed live), FE-03/09 live-quote gating + `expected_total`, FE-04/12 pay
buttons hidden while confirming, FE-05 KYC documents via authenticated blob,
FE-06/07/08/10/13 and FE-16..23, FE-14/PRICE-01 "From ₹X / Month" from live
plans, FE-15/FE-S05/S06 ~60 screens with error + Try Again, FE-S01..S07,
NTF-08 confirmation token kept out of analytics, RESP-01..06 (Contact Us
scroll confirmed at 360×640), UI-01/03, pinch-zoom restored, CSP
(Report-Only), preview builds can't silently call the production API.
Round 2: staff cancel dialog with charge preview, charges pages (manager,
admin) with reduce/waive, charge line in quotes/confirmation/booking detail,
captain charge line and photo/arrival messages, policy page from live amounts,
society pass Extend, staff phone verification, staff password reset, admin
visibility of discounts/tips/charges/refunds, withdrawals payout list,
arrival-lock unlock. **(Round-2 status: see section 6.)**

---

## 2. P0 Status

| ID | Status | Detail |
|---|---|---|
| P0-1 Public customer data on GitHub | **EXTERNAL ACTION REQUIRED** | Local: `.gitignore` covers `.dev/`, `devdata/`, `*.archive.gz`, `*.log`, `*.pid`, env files; `blussit-v2` never contained the files (only `main`/`origin/main` commit `111213a`, plus unpushed `b04ad32`). **Not done (needs you):** make the repo private, purge history, force-push, ask GitHub to drop caches, reset the 6 accounts whose hashes leaked — exact steps in `docs/SECURITY_REPO_CLEANUP.md`. GitHub history has **not** been purged. |
| P0-2 Staff takeover via OTP reset | **FIXED (code) + EXTERNAL CHECK** | Code refuses it (tests above). Still check production for seed staff (`@doorstepvehiclecare.in`, phones 9999999999 / 9999911111 / 9999922222) and delete them or change their phones. |
| P0-3 Razorpay live mode | **FIXED (code) + EXTERNAL CONFIG** | The app won't start in production without live keys + secret + webhook secret; verify now fails closed. You must set `rzp_live_` keys, the webhook (incl. `refund.processed`) and confirm auto-capture. |

## 3. P1 Status

| ID | Status |
|---|---|
| PAY-01, PAY-02, PAY-03, PAY-04 | FIXED |
| PASS-1, SOC-2 | FIXED |
| FE-03, FE-04 | FIXED |
| SLOT-01, BOOK-01, BOOK-02, BOOK-03 | FIXED |
| AUTH-05, AZ-01, AUTH-02, CAP-01, CAP-02 | FIXED |
| FE-02 | FIXED (confirmed live) |
| FE-05 | FIXED (code path; R2 can't run locally — verify on staging) |
| DEP-04 | FIXED in code; templates must be approved + synced (external) |
| PAY-09 | FIXED (fails closed); confirm auto-capture ON (external) |
| DEP-08 | FIXED; readiness shows any index production can't build |

No P1 is open in code.

## 4. P2 Status

All P2 items are **FIXED** except:

| ID | Status |
|---|---|
| PERF-01 websocket events don't cross Cloud Run instances | **NOT FIXED** — needs a shared bus (Redis or Mongo change streams). Mitigated by polling (UI refreshes; backend always re-validates). |
| PERF-04 / RL-1 rate limits per instance, in memory | **NOT FIXED** — needs a shared store. Keep `api.blussit.com` DNS-only (not Cloudflare-proxied). |
| DEP-11 CSP | **PARTIAL** — shipped Report-Only; switch to enforcing after checking reports on the live site. |
| UI-02 tiny text | **PARTIAL** — 7–9 px fixed; some 10 px badges kept. |
| MGR-02/03 | **BY DESIGN** (founder): uncapped, counted, visible to admin. |

## 5. P3 Status

FIXED: PAY-10..15, PRICE-02/03/05/06/07, PASS-3..6, SOC-5..11, BOOK-05/06,
SLOT-02/04, STATE-01/02, E2E-02/03, CAP-04/06/07, VAL-1/2/3/4/6, DATA-1,
MGR-04..06, ADM-08..14, NTF-03..06, FAIL-04..07, PERF-02/03, DEP-12, FE-16..23.

Not fixed (low risk, reasons):

| ID | Status |
|---|---|
| VAL-5 | **NOT A BUG** on re-check — the quote ignores an address that isn't the caller's. |
| PRICE-08 | **BY DESIGN** (founder: no cap on manager discount). |
| PRICE-09 | NOT FIXED — travel charge can change between quote and booking if Google Routes recovers mid-flow; now caught by `PRICE_CHANGED` when it rises. |
| SLOT-03 | NOT FIXED — on-screen availability can be ≤ 60 s stale; the backend always re-checks (clean refusal, never an oversell). |
| ENUM-1 (messages) | PARTIAL — timing fixed; "no account found" wording kept (UX decision). |
| bcrypt 72-byte limit | NOT FIXED — passwords are bounded to 72 chars by the UI. |
| CAP-08 KYC before assignment | **BUSINESS DECISION REQUIRED** (section 9). |
| Society `activate()` atomicity, plan revenue by paid date | NOT FIXED — duplicates are now prevented by claims; revenue window is consistent across KPIs and collections (by order date). |
| Pre-slot customer reminder | NOT BUILT (template exists). |
| FE-24 back button, RESP-07..19, unused `public/` originals | see section 6 |

## 6. Tests

*(Numbers filled in from the final runs.)*

| Suite | Result |
|---|---|
| Backend tests before this pass | 983 |
| New regression test functions | 387 in 47 files (more cases with parametrisation) |
| Full backend suite (final) | _pending_ |
| Concurrency (in suite) | 100 customers → last 5 seats; 20 → 3; cancel ×4; verify vs expiry ×14; payment vs captain completion ×10; payment vs manager completion ×10; 4 plan sales; 3 society renewals; coupon redemptions; assign vs cancel; reschedule vs cancel; delete vs cancel ×15; restore vs new booking ×8 — all preserve money, capacity, state |
| Security (in suite) | cross-customer / cross-center / cross-captain negatives; expired, forged, `alg=none`, wrong-role tokens; OTP replay; webhook replay; duplicate payments parked; price / user / booking id tampering; wallet manipulation; negative inventory; unauthorised settings |
| Frontend | `tsc` 0 errors; `npm run build` + prerender pass; Playwright at 360/390/412/768/1366 — _round 2 pending_ |
| Dependency upgrade check (DEP-10) | _pending_ |

## 7. Production Configuration Checklist

| Item | Status |
|---|---|
| Razorpay live keys, secret, webhook secret | NEEDS MANUAL VERIFICATION (app now refuses to start without them) |
| Razorpay auto-capture ON; webhook events incl. `refund.processed` | NEEDS MANUAL VERIFICATION |
| WhatsApp: provider meta_cloud, token, phone id, app secret, verify token, business account id, OTP/update/temp-password templates | NEEDS MANUAL VERIFICATION (app refuses to start without them) |
| WhatsApp templates approved + CRM Sync; Meta webhook fields `message_template_status_update`, `template_category_update`, `user_preferences` | NEEDS MANUAL ACTION |
| R2 public bucket URL + private bucket | NEEDS MANUAL VERIFICATION (validated at startup) |
| MongoDB: unique indexes build (readiness shows missing ones); ≥ 300 connections | NEEDS MANUAL VERIFICATION after deploy (`/api/ready`) |
| Seat-counter backfill + reconcile report, then admin repair if drift | AUTOMATIC at boot + nightly; admin reviews drift |
| Cloud Run: `--no-cpu-throttling`, startup probe on `/api/ready` | PASS in deploy script; verify on the live revision |
| DNS: `api.blussit.com` DNS-only | NEEDS MANUAL VERIFICATION |
| CORS: blussit.com origins only | PASS (validated) |
| Environment variables | PASS (validator lists every missing one) |
| Monitoring / alerts | NEEDS SETUP — JSON logs with severity + request id are ready for Cloud Logging alerts |
| Error tracking | NEEDS SETUP — add `sentry-sdk` + `SENTRY_DSN` (hook ready) |
| Backups | NEEDS MANUAL VERIFICATION (Atlas backups) |
| `backend/.gcloudignore` committed | NEEDS COMMIT |
| Vercel Preview `VITE_API_BASE_URL` → staging API | NEEDS SETUP (preview builds now fail without it) |
| GitHub repo private + history purged | NEEDS MANUAL ACTION (P0-1) |
| Production center hours / capacity | PASS — Indore hub already 07:00–19:00, 12 per slot, 40 per day (public API, read-only check) |

## 8. Remaining Risks

- Production state not inspected (by rule): seed staff existence, legacy
  duplicate bookings that could block the unique index, historic seat-counter
  drift — the boot backfill, `/api/ready` and the nightly reconcile report
  surface these after deploy.
- Third parties not exercised live: Razorpay checkout/capture, MSG91 token
  semantics, Meta template delivery, R2 private documents.
- Multi-instance behaviour simulated with concurrent requests on one replica
  set, not 10 Cloud Run instances; websocket fan-out and rate limits are
  per instance (PERF-01, PERF-04).
- Load beyond a few hundred concurrent requests not tested.

## 9. Founder Decisions Required

1. **Captain KYC before assignment (CAP-08):** today a captain with KYC not
   yet verified can be assigned jobs. Should assignment require verified KYC?

## 10. FINAL VERDICT

*(See the final section after all runs.)*
