# Doorstep Vehicle Care Platform — Backend

FastAPI + MongoDB backend built with clean architecture:

```
Routes → Controllers → Services → Repositories → Database
```

## Setup

Full local setup (Windows, step by step): [../SETUP_WINDOWS.md](../SETUP_WINDOWS.md).

```bash
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.development.example .env.development   # local test DB + test keys; read automatically
```

The app reads `backend/.env.development` by default (`ENV_FILE` picks another
file). `backend/.env.production` holds the live credentials and is read only by
`scripts/deploy-gcp.sh` — never use it locally.

Make sure MongoDB is running locally (or point `MONGO_URI` at your instance), then:

```bash
# Seed local test data: staff accounts, services, plans, a service center
# and three test customers (refuses to run against anything but a local DB)
python -m app.scripts.seed_dev

# Run the API
uvicorn app.main:app --reload --port 8000
```

API docs: `http://localhost:8000/api/docs`

Default seeded super admin: `admin@doorstepvehiclecare.in` / `Admin@12345`
(change this immediately in any real deployment).


Profile	Login
Admin	admin@doorstepvehiclecare.in / Admin@12345
Manager	manager.indore@doorstepvehiclecare.in / Manager@12345
Captain	captain.indore@doorstepvehiclecare.in / Captain@12345
Customer (new)	9000000001 + OTP 123456
Customer (has a plan)	9000000002 + OTP 123456
Customer (has past wash)	9000000003 + OTP 123456

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
