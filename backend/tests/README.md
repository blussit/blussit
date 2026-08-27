# Backend test suite

Real, reusable pytest tests against the actual service layer and a **dedicated
test database** (`doorstep_vehicle_care_test` by default) on the same MongoDB
Atlas cluster the app already uses — never the dev database.

## Run

```
cd backend
venv/bin/python -m pytest              # whole suite
venv/bin/python -m pytest tests/test_booking_slots_and_capacity.py -v
venv/bin/python -m pytest -k "capacity" # by keyword
```

No setup needed beyond `pip install -r requirements.txt` (pytest/pytest-asyncio
are listed there) and the existing `.env`'s `MONGO_URI` being reachable. The
test database is dropped and reseeded (via `app/seed.py`, the same seeding
`python -m app.seed` uses for local dev) at the start of every run, and every
document a test creates is deleted at that test's teardown (`cleanup` fixture
in `conftest.py`) — so the suite is safe to run again and again with no manual
cleanup, per-feature, without recreating fixtures each time.

## Layout

- `conftest.py` — session-scoped `db` fixture (isolated DB, seeded once) and
  a per-test `cleanup` registry fixture.
- `factories.py` — reusable builders (`make_customer_with_vehicle`,
  `make_captain`, `make_service_center`, `make_subscription_plan`, ...).
- One file per feature area — see each file's module docstring for which
  spec section(s) it covers.

## Coverage by feature

| File | Covers |
|---|---|
| `test_booking_slots_and_capacity.py` | Admin slot generation, capacity, "Available/Only N left/Fully booked" wording, concurrent last-slot race |
| `test_booking_cutoff.py` | Per-slot booking cutoff, policy-driven not hardcoded |
| `test_booking_duplicate.py` | Duplicate/double-click booking prevention, same-day multi-booking allowed |
| `test_captain_assignment.py` | Assign/reassign, shared-slot multi-captain design, concurrent assignment race, wallet gating |
| `test_captain_start_window.py` | 30-min pre-start window, late-start flagging/penalty |
| `test_subscription_vehicle_type.py` | Vehicle-type-scoped subscriptions, vehicle added after subscribing |
| `test_subscription_consumption.py` | Consumption commit/restore, concurrent last-unit race |
| `test_reviews.py` | Split ratings, ownership, edit history, soft delete |
| `test_capacity_admin.py` | Admin capacity increase/decrease/close/reopen, immediate effect |
| `test_notifications.py` | Manager/captain/customer notification routing |
