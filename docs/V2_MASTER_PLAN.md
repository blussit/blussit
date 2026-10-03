# Blussit v2 — master execution plan

Status legend: ⏳ running · ✅ done · ⬜ queued

Every workstream owns a fixed set of files so parallel work never overwrites
another's. Shared files (App.tsx, the three portal layouts, main.py,
database.py) are only ever changed with small targeted edits. Everything is
tested locally (`scripts/dev.sh`, local Mongo, OTP 123456) — never against
production. Nothing is committed until the founder says so.

## Wave 1 — new user-facing flows (parallel)
| # | Workstream | Owns | Source of truth |
|---|---|---|---|
| 1 ✅ | Booking flow redesign (car type · service · date/time → slots → summary → continue), lighter than today, all pricing via `POST /bookings/quote`; also clears the type errors blocking the Vercel build | `components/booking/**`, `BookPage`, `NewBookingPage`, `SlotPicker`, `LocationPicker` | `figmav2bookingpage.jpeg` |
| 2 ✅ | Customer app: bottom tabs (Home · Bookings · Plans · Profile), Home, Bookings (Upcoming/Past), Booking detail **without live captain tracking**, My Garage, Profile menu | `pages/customer/*` (not NewBookingPage), `components/customer/*` | `usermobileviewdifferentpages.png` |
| 3 ✅ | Society plans — design (`docs/SOCIETY_PLANS.md`) then build: societies per center, shareable resident form, template + custom plans (hidden from website), daily bucket-wash attendance by captain, premium-wash quotas, admin/manager views | new society backend + frontend files | founder brief |

## Wave 2 — security & bug review of wave 1 ✅ (society + customer app: 12 fixes; booking flow → final sweep)
Read-only review (authz, input validation, payments/quotas, data leaks via the
public society form, rate limits, UI regressions), then fixes + tests.

## Wave 3 — portals (two at a time, memory-bound)
| # | Workstream | Owns |
|---|---|---|
| 4 ✅ | Captain app: simple flow per `assets/captain-flow-design.png` (Today → job → before photos → start → after photos → complete), v2 theme, remove clutter, keep society attendance | `pages/captain/*` |
| 5 ✅ | Manager panel: one Dashboard (merge Dashboard + KPIs) with per-service-type washes, fix support-ticket repeat updates, API validation, New booking / Log a done job aligned with the new booking flow, v2 theme | `pages/manager/*`, complaints stack |
| 6 ✅ | Admin panel: v2 theme, fix broken screens, admin can resolve issues, secure "view as manager" (no shared tokens, server-side center scoping, audit log), interactive KPI charts (bookings per service, per car type, filters) | `pages/admin/*`, KPI backend |
| 7 ✅ | Dropdowns: one themed `Select` used everywhere (same API) | `components/ui/Select*` |

## Final — whole-system check ✅ (34 fixes; 782 backend tests pass; tsc 0 errors; build ok; 96-page role walkthrough clean)
Security review (authn/authz, IDOR, injection, rate limits, secrets, payment
integrity), bug sweep, full backend test suite, type check, production build,
browser walkthrough of every role. Findings fixed, remaining risks reported.
