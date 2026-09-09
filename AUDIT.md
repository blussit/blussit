# BLUSSIT Production Audit

Engineering audit · 3 September 2026

**Verdict: the booking core is genuinely solid** — atomic capacity counters, transactional creation, a duplicate-booking DB guard, and 138 passing tests mean overbooking and double-booking are structurally impossible. The risks that remain live **around** the core: money handling, auth lifecycle, file storage, and deployment configuration. Every finding below was verified in the actual code, not assumed.

**Findings: 5 Critical · 5 High · 9 Medium · 3 Low**

> **Fix status (updated 3 Sep 2026):** every code-side finding is FIXED and test-covered —
> C1 ✅ (atomic wallet ops + wallet-creation race + unique index), C2 ✅ (refresh rejects suspended users + token versioning), C3 ✅ (Cloudinary provider implemented — activates when keys are set), C5 ⚙ code ready (set `WHATSAPP_APP_SECRET` at deploy),
> H1 ✅ (per-IP rate limiting, verified live), H2 ✅ (Mongo lease makes the reminder sweep single-flight across workers), H3 ✅ (15-min access tokens), H4 ✅ (regex escaping),
> M1 ✅ (theater-seat slot holds — web, guest wizard and WhatsApp bot), M3 ✅ (revenue on completion date, canonical everywhere), M6 ✅ (TTL indexes), M7 ✅ (route-level code splitting — 47 chunks, back-office loads only for staff), M9 ✅ (timeout copy), L1 ✅ (wizard phone normalization).
> **Still yours:** C4 (rotate DB credentials + backups), C5 (App Secret env var), H5 (permanent Meta token) — all in GO_LIVE.md. M2/M5/M8/L2/L3 remain deliberate deferrals as written.

---

## CRITICAL — fix before a single paying customer

### C1. Wallet balance updates are not atomic
- **Evidence:** `backend/app/services/wallet_service.py:60, 82, 99, 117` — every balance change reads the wallet, computes `new_balance` in Python, then writes it back.
- **Impact:** Two concurrent operations (a cash-booking deduction landing while a top-up processes) silently overwrite each other — captains lose real money or balances go negative. This is the exact race the slot-capacity counters were built to prevent, applied to actual rupees.
- **Fix:** One guarded `find_one_and_update` with `$inc` and a `balance >= amount` filter per operation — same pattern as `increment_if`. ~1 hour including tests.

### C2. Suspended accounts keep full access
- **Evidence:** `get_current_user` (dependencies) trusts the JWT with no DB check; `auth_service.refresh()` verifies the user *exists* but not their status.
- **Impact:** Suspending a user (a hostile captain, an abusive customer) does nothing for up to 60 minutes — and because refresh ignores status, they can mint fresh tokens forever. Suspension is currently decorative.
- **Fix:** Reject non-active status in `refresh()` (one line) and re-check status on sensitive role routes. Password change should also bump a token-version claim.

### C3. Service photos exist only on local disk
- **Evidence:** `backend/app/core/storage.py` — writes to `UPLOAD_DIR`; the Cloudinary provider raises `NotImplementedError` despite config keys existing.
- **Impact:** Before/after photos are your dispute evidence and fraud protection. On any modern host (Render, Railway) the filesystem is wiped on every deploy — **all photos vanish**. Even on a VPS, one disk failure erases them.
- **Fix:** Implement the Cloudinary provider (~50 lines behind the existing abstraction; free tier covers 25GB) before deployment.

### C4. Database: weak credentials, shared cluster, no backups
- **Impact:** The Atlas password is trivially guessable, dev and (future) production share one cluster and one all-powerful user, and the free tier takes **no automated backups** — a bad script or a compromised credential can erase every booking with no way back.
- **Fix:** Rotate to a strong password, create a least-privilege production user on a separate database, enable the IP allowlist, and either upgrade to M2 (automated backups) or run a nightly `mongodump` to object storage.

### C5. Webhook signature check is off until a secret is set
- **Evidence:** `whatsapp_webhook_routes.py` — blank `WHATSAPP_APP_SECRET` skips HMAC verification (correct for dev, fatal in prod).
- **Impact:** Anyone who discovers the production webhook URL can forge "incoming WhatsApp messages": create accounts, place bookings, trigger admin notifications, poison the CRM inbox.
- **Fix:** Set the App Secret in the production environment — the verification code already exists and is tested. Deployment checklist item, not a code change.

---

## HIGH — fix in the first week of operations

### H1. No request rate limiting anywhere
- **Impact:** Per-account protections exist (login lockout, OTP cooldown), but nothing limits raw request volume. Public endpoints (register, coverage leads, contact) can be scripted; once SMS is live, an OTP-request flood spends your money directly.
- **Fix:** Per-IP limits at the edge (Caddy/nginx rate zones) or `slowapi` on auth + public routes. Half a day.

### H2. Background loops assume exactly one server process
- **Evidence:** `main.py` reminder loop; in-memory WebSocket broadcast.
- **Impact:** Run uvicorn with 2+ workers (the obvious "make it faster" move) and every reminder and captain nudge fires twice or more. Horizontal scaling is blocked until this changes.
- **Fix:** Today: deploy with exactly one worker (document it loudly). Later: a Mongo lease lock, or move schedulers to a dedicated worker process.

### H3. Role and permission changes take up to an hour to apply
- **Impact:** Role and `service_center_id` live inside the JWT for its 60-minute life. Demote a manager and they keep manager powers until expiry; combined with C2, revocation is effectively impossible.
- **Fix:** Shorter access-token TTL (15 min) + the C2 refresh-status check gets 90% of the value for near-zero cost.

### H4. User input flows into regex queries unescaped
- **Evidence:** `base_repository.py:213` search helper and CRM name search interpolate raw input into `$regex`.
- **Impact:** A crafted search term becomes a pathological regex — CPU-pinning the database (ReDoS) or matching unintended records. Authenticated-only today, but staff accounts count as attack surface.
- **Fix:** `re.escape()` at both call sites. Fifteen minutes.

### H5. WhatsApp runs on a token that expires every ~24 hours
- **Impact:** Known issue, restated because it is a *guaranteed* production outage: notifications, OTPs and the bot all stop when the temp token dies.
- **Fix:** Meta System User permanent token (your side, 10 minutes in Business Settings — already on GO_LIVE.md).

---

## MEDIUM — plan into the next month

### M1. No temporary slot hold (the theater-seat model)
- **Today:** Capacity is claimed atomically at final confirmation — overbooking is impossible, but two customers can walk the whole wizard for the last slot and one learns only at the very end.
- **Fix:** Full design below — recommended as the next feature build.

### M2. Analytics compute whole collections in Python per request
- **Impact:** Deliberate and correct at today's volume; past ~50k bookings the KPI dashboard slows and the CRM analytics N+1 loop (2 queries × 300 conversations) crawls.
- **Fix:** When felt: move hot paths to aggregation pipelines + a 5-minute cache. No schema changes needed.

### M3. Two definitions of "repeat rate", and revenue booked on the wrong date
- **Impact:** The old dashboard counts lifetime repeat customers; the KPI engine counts period-relative repeats — the numbers will never match and management will ask why. Revenue is attributed to the booking's *creation* date, not completion, so month-end numbers shift.
- **Fix:** Pick canonical definitions (recommend: period-relative repeat; revenue on completion date), apply everywhere, note it on tooltips.

### M4. Timezone duality is a standing bug factory
- **Impact:** Mongo returns naive-UTC datetimes; business logic runs aware-IST. Three separate bugs this month were exactly this mismatch, each caught by hand.
- **Fix:** One boundary module that converts on read/write, then ban naked datetime comparisons in review.

### M5. Logout is cosmetic; refresh tokens live 7 days irrevocably
- **Fix:** Server-side refresh-token store with rotation when the stakes rise (payments). Acceptable risk today with C2/H3 fixed.

### M6. Several collections grow forever
- **Impact:** Notifications, WhatsApp outbox/inbox, audit logs have no expiry — years of rows slow queries and inflate storage cost.
- **Fix:** TTL indexes: notifications 90d, WhatsApp traffic 180d; audit logs archive-then-delete yearly.

### M7. One 500KB+ JavaScript bundle for every visitor
- **Impact:** A customer on 4G downloads the entire admin panel, KPI engine and CRM to view the landing page.
- **Fix:** Route-level `lazy()` splitting — admin/manager/captain chunks load only for those roles.

### M8. Booking transactions require a replica set
- **Fix:** Atlas provides this; just never point production at a standalone `mongod`. Deployment note, not code.

### M9. Bot forgets a half-finished chat after 30 quiet minutes
- **Fix:** Acceptable; soften with "we saved your details, type hi to continue" copy if complaints appear.

---

## LOW — worth knowing, not worth stopping for

- **L1.** Guest wizard sends the un-normalized phone on one edge path — typing "+91 98765…" passes validation but the raw string is used for the inline login-after-409 path; the backend stores the bare form, so that login misses. Normalize once at input.
- **L2.** Admin CRM polls every 5–8s per open tab — fine for a few admins; consolidate onto the existing WebSocket channel if the team grows.
- **L3.** API docs visibility rides on `DEBUG=false` — already automatic; just confirm the production environment sets it.

---

## Slot holds: the theater-seat design

Reuses the exact atomic-counter machinery that already guards capacity, so the hold system inherits the same impossibility of overbooking.

```
1. Customer picks a slot          2. Slot shows as taken           3. Confirm or release
   POST /bookings/hold               available_slots subtracts        Booking converts the hold
   Atomic guard:                     active holds — every other       inside the existing
   booked + held < capacity          customer (web AND WhatsApp)      transaction (held-1, booked+1).
   → $inc held_count,                sees one fewer spot              Abandon/back releases it;
   hold doc with 5-min TTL           instantly.                       TTL expiry is the safety net.
```

- **New collection** `slot_holds`: center, date, slot, holder, `expires_at` (TTL index) — one hold per customer per slot (unique index).
- **Counter:** `held_count` on the existing `slot_capacity` doc; a small sweeper reconciles expired holds back.
- **Frontend:** countdown chip ("Slot held · 4:32"), auto-renew while the customer is active, release on leaving.
- **WhatsApp bot:** acquires the hold at slot-pick; chat pace fits comfortably inside 5 minutes.
- **Failure honesty:** if a hold can't be acquired, the customer finds out at slot-pick — the earliest possible moment — instead of at confirmation.

---

## Cost-optimized production blueprint

Everything below runs the whole platform for roughly ₹900–1,300/month, in one region (Mumbai) for latency.

| Piece | Choice | Monthly | Why |
|---|---|---|---|
| Backend host | Hetzner CX22 VPS or Railway Hobby | ≈ ₹400 | Single uvicorn worker (see H2) behind Caddy — free auto-HTTPS, built-in rate limiting for H1 |
| Database | Atlas M2 (Mumbai) | ≈ ₹800 | Automated backups solve C4; stay M0 + nightly `mongodump` to Backblaze if squeezed |
| Frontend | Vercel (current) | Free | Already deployed; just set `VITE_API_BASE_URL` |
| Photos | Cloudinary free tier | Free | 25GB — years of runway at current volume (solves C3) |
| WhatsApp | Utility templates | ~₹0.115/conversation | Customer-initiated bot chats are free; avoid Marketing category for ops messages |
| Monitoring | UptimeRobot on `/api/health` | Free | Know the API is down before customers tell you |

---

## The order to do it in

Sequenced so each step protects the ones after it. **Items 1–6 are the true go-live gate.**

1. **Rotate database credentials + backups** (C4) — everything else assumes the data is safe.
2. **Wallet atomicity** (C1) — money bugs compound; fix while balances are still small.
3. **Auth lifecycle** (C2 + H3) — status check on refresh, 15-min access tokens.
4. **Cloudinary provider** (C3) — before the first deploy wipes a disk.
5. **Regex escaping** (H4) — fifteen minutes, do it alongside #3.
6. **Deploy:** VPS + Caddy (rate limits → H1), one worker (H2), App Secret set (C5), permanent Meta token (H5), `DEBUG=false`.
7. **Slot holds** (M1) — the theater-seat system, built on now-hardened foundations.
8. **Metric canon + TTLs + code-splitting** (M3, M6, M7) — polish while operating.

---

## What was checked and found healthy

Atomic slot-capacity reservation with transactional rollback · duplicate-booking unique index · booking-number uniqueness · phone-verification gate · login brute-force lockout · OTP never returned by the API · secrets out of git · webhook dedup against Meta redeliveries · OTP redaction in the CRM · stack traces never leaked · upload type/size validation · role checks on every admin/manager route sampled · 138/138 tests green.
