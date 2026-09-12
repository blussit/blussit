# BLUSSIT — production readiness

Status of each area before public launch. Classifications:

- **PASS** — inspected, already correct, evidence noted.
- **FIXED** — a defect was found and fixed in this repo; tests noted.
- **NEEDS MANUAL CONFIG** — correct in code, but someone has to set it in a
  dashboard/host we cannot reach from the repo.
- **PARTIAL** — improved, with a known limit written down rather than hidden.
- **NOT AUDITED** — honestly out of scope of the last pass; say so, don't guess.

---

## Security

| Area | Status | Notes |
|---|---|---|
| Secrets in source control | PASS | `backend/.env` is git-ignored (root + per-package rules); only `.env.example` placeholders are tracked. Repo-wide scan for live keys/tokens found none in source. |
| Frontend env exposure | PASS | Only `VITE_API_BASE_URL` and `VITE_WHATSAPP_NUMBER` reach the bundle — both public by nature. The Razorpay key id arrives per-request from the backend; the secret never leaves it. |
| Password hashing / lockout / OTP limits | PASS | Existing `auth_service` + per-IP buckets on every OTP/login/reset path. |
| Security headers | FIXED | `nosniff`, `Referrer-Policy`, `X-Frame-Options: DENY`, `Permissions-Policy` on every response; HSTS outside DEBUG only (pinning `localhost` to HTTPS would break every dev machine). Uploads additionally get `default-src 'none'; sandbox`. |
| Rate limiting | FIXED | Rules are now method-aware. Added POST-only buckets for booking creation (20/60), payment order creation (20/60), reviews (15/60) and the public plan-enquiry form (6/60). Reads are untouched, so browsing is unaffected. |
| API docs exposure | PASS | `/api/docs`, `/redoc`, `openapi.json` are `None` unless `DEBUG`. |
| Error leakage | PASS | The catch-all handler logs server-side and returns a generic message; no stack traces reach clients. |
| Object-level authz (IDOR) | PASS | Spot-checked and covered by tests on the paths that matter: a pass can't be spent by another customer, another customer's auto-pay mandate can't be claimed, manager actions go through `ensure_own_center`. |
| CSP for the SPA | NEEDS MANUAL CONFIG | The SPA is served by its own host — its page-level CSP belongs in that host's config, not in FastAPI. |

## Booking, capacity and concurrency

| Area | Status | Notes |
|---|---|---|
| Slot oversell under load | PASS | `tests/test_concurrency_capacity.py`: 9 real concurrent bookings against a 3-seat slot ⇒ exactly 3 succeed, counter lands on 3, every loser gets a clean 400. |
| Pass entitlement race | FIXED | Two concurrent bookings racing a pass's last wash used to leave the LOSER holding a live, free, dispatchable booking: the row and its slot reservation were already committed when `commit_consumption` failed. Now rolled back (`_rollback_uncommitted_booking`) — safe because nothing has been announced at that point. Covered by test. |
| Double settlement | PASS | Online payment racing the captain's cash tap: one applies, the other is parked `paid_attention` for a human. Covered by test. |
| Booking state machine | PASS | `_ALLOWED_TRANSITIONS` is asserted inside every transition method. |
| Online payment before confirmation | FIXED (earlier this session) | `awaiting_payment` state: slot held, nothing dispatched, nobody notified until payment verifies or the customer switches to cash. Expired holds are swept back. |

## Payments

| Area | Status | Notes |
|---|---|---|
| Server-side amount | PASS | Client names the thing; the server prices it. Pass prices come from `resolve_pass_price`, the same function `quote_pass` shows the buyer. |
| Signature verification | PASS | Orders `HMAC(order_id\|payment_id)`, mandates `HMAC(payment_id\|subscription_id)`; the client's echoed id is never trusted — the id we opened checkout with is what gets verified. |
| Idempotency | PASS | `payment_orders` atomic `created→paid` claim; replays return the same success. |
| Auto-pay renewals | PASS | Poll-based (`paid_count` ledger), each cycle claimed atomically, throttled per mandate. |
| Refunds | NOT AUDITED | No refund flow exists; parked payments are resolved by hand today. |

## Data

| Area | Status | Notes |
|---|---|---|
| Indexes | FIXED | Added `(customer_id, vehicle_id, status)` and `(customer_id, plan_id, status)` on subscriptions (behind the one-pass-per-car guard), unique `phone` on plan enquiries, and rebuilt the duplicate-booking partial index to include `awaiting_payment`. |
| Pagination caps | PASS | `PaginationParams` clamps `page_size` to `MAX_PAGE_SIZE`. |
| Unbounded reads | PARTIAL | Two KPI rating roll-ups still read every review; now projected to the three fields they use. **Known limit**: move the join into a `$lookup` aggregation before the reviews collection gets large. |
| `PUBLIC_BASE_URL` shadowing | FIXED (earlier this session) | Declared twice in `config.py`; Pydantic kept the last, so locally-stored photo URLs came out host-less. One field now, with an explicit local fallback. |

## SEO and launch completeness

| Area | Status | Notes |
|---|---|---|
| robots.txt / sitemap.xml | FIXED | Both added; signed-in areas disallowed. |
| Canonical / OG / Twitter / JSON-LD | FIXED | Canonical is the bare `https://blussit.com`. JSON-LD is deliberately factual — no ratings or certifications we can't substantiate. |
| Legal pages | PASS/FIXED | Terms, Privacy and Cancellation exist and are linked from the footer. The Terms "Monthly passes" section was rewritten to state the real rules (one car one pass, online-only, add-ons never covered, auto-pay, no carry-over). |
| Footer credit | FIXED | "Developed by Kalakartechcrew" with `rel="noopener noreferrer"`. |
| Debug leftovers | PASS | No `console.log` in `src/`; the only `localhost` reference is a dev fallback that now refuses to apply in production builds. |
| Domain redirects (www → apex, http → https) | NEEDS MANUAL CONFIG | DNS/host settings — cannot be done from the repo. Canonical metadata already assumes the apex. |

## Not audited in this pass

Stated plainly rather than marked PASS: accessibility beyond the components
touched here, the full admin/manager/captain UI sweep across breakpoints,
WebSocket authorization, Cloudinary lifecycle/orphans, backup and restore
strategy, and load testing beyond the concurrency tests listed above.
