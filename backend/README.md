# Doorstep Vehicle Care Platform — Backend

FastAPI + MongoDB backend built with clean architecture:

```
Routes → Controllers → Services → Repositories → Database
```

## Setup

```bash
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # edit MONGO_URI / JWT_SECRET_KEY for your environment
```

Make sure MongoDB is running locally (or point `MONGO_URI` at your instance), then:

```bash
# Seed sample data: super admin, categories, services, a service center,
# subscription plans, and FAQs
python -m app.seed

# Run the API
.venv/Scripts/activate



```

API docs: `http://localhost:8000/api/docs`

Default seeded super admin: `admin@doorstepvehiclecare.in` — password from
`SEED_ADMIN_PASSWORD`, else the local-only dev default in `app/seed.py`.
Demo staff accounts are created ONLY against a local database; the seed never
creates them on Atlas/production.

## Structure

- `app/core` — config, database connection, security (JWT/hashing), RBAC dependencies, exceptions, response envelope
- `app/models` — Pydantic document models (one per MongoDB collection)
- `app/schemas` — request/response DTOs
- `app/repositories` — data access layer (generic `BaseRepository` + collection-specific repos)
- `app/services` — business logic
- `app/controllers` — thin orchestration layer between routes and services
- `app/routes/v1` — FastAPI routers, grouped by module
- `app/seed.py` — demo data seed script

## Modules implemented (Phase 1)

Auth · Users · Vehicles · Addresses · Categories · Services · Service Centers ·
Bookings (+ status history) · Subscription Plans & User Subscriptions ·
Coupons · Complaints · Reviews · Inventory · Notifications · Attendance & Leave ·
Staff Directory (captain performance/earnings) · CRM (customer 360) ·
Analytics dashboard · Content/CMS (FAQs, testimonials, settings) · Audit Logs

## Phase 2 extension points (deliberately deferred, per PRD)

Payment gateway (Razorpay), SMS/WhatsApp delivery for OTP, Google Maps live
routing, Redis caching, WebSockets, live captain tracking, automated
dispatch, and push notifications. The service layer already isolates these
concerns (e.g. `AuthService.request_otp` is a placeholder generator ready to
be swapped for a real provider; `PaymentMethod.ONLINE_PLACEHOLDER` marks
where Razorpay will plug in) so none of this requires touching the API
surface used by the frontend.



Admin: admin@doorstepvehiclecare.in (password: SEED_ADMIN_PASSWORD or the dev default in app/seed.py)
Manager: manager.indore@doorstepvehiclecare.in (SEED_MANAGER_PASSWORD or the dev default)
Captain: captain.indore@doorstepvehiclecare.in (SEED_CAPTAIN_PASSWORD or the dev default)
Customer: 9000000001 / OTP 123456
Customer: 9000000002 / OTP 123456
Customer: 9000000003 / OTP 123456