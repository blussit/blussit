# Doorstep Vehicle Care Platform — Phase 1

A production-ready foundation for India's premium doorstep vehicle care
platform, built per the Phase 1 PRD: FastAPI + MongoDB backend with clean
architecture, and a React + TypeScript frontend covering the public website
plus Customer, Captain, Manager, and Super Admin portals.

## Quick start

**Windows (step by step, for new teammates): see [SETUP_WINDOWS.md](SETUP_WINDOWS.md).**

**macOS / Linux:** with Docker, Python 3.12 and Node 20.19+ installed:

```bash
cp backend/.env.development.example backend/.env.development
cp frontend/.env.development.example frontend/.env.development
scripts/dev.sh up      # local Mongo (Docker) + backend + website, seeds test data on first run
```

Website http://localhost:5173 · API docs http://localhost:8000/api/docs · every
OTP is `123456` locally. Live credentials live only in `backend/.env.production`,
which the deploy script reads — local runs never touch production.

## What's included (Phase 1 scope)

- Premium public landing page (hero, how it works, services, why choose us,
  subscription plans, testimonials, stats, FAQs, contact)
- Customer auth (register/login/forgot-password with an OTP-placeholder
  flow), dashboard, vehicle & address management, a multi-step booking
  wizard, booking history with cancel/reschedule/rebook/review, subscriptions,
  support tickets, notifications, profile & security
- Captain portal: today's jobs with status-transition actions, attendance
  check-in/out, leave requests, earnings & performance summary
- Manager portal: service-center dashboard, booking queue with captain
  assignment, captain directory, inventory tracking, complaint resolution
- Super Admin portal: analytics dashboard (revenue, KPIs, booking trends,
  top performers), user management, service centers, services/categories,
  subscription plans, coupons, complaints, audit logs
- REST API with consistent response envelopes, JWT auth, role-based access
  control, pagination/filtering/search on every list endpoint, and an audit
  trail on sensitive admin/manager actions

## Deliberately deferred to Phase 2

Razorpay payments, SMS/WhatsApp delivery, Google Maps live routing/dispatch,
Redis caching, WebSockets/live tracking, push notifications, and AI
recommendations — all noted in the PRD as Phase 2 integrations. The service
layer isolates each of these behind a stable interface (e.g. a `PaymentMethod`
placeholder, an OTP generator ready to be wired to a real SMS provider) so
none of it requires reworking the API surface the frontend already consumes.
