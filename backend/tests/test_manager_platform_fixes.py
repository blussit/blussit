"""
Regression tests for the manager-platform hardening pass:
  - the 7-day advance-booking window (policy `max_advance_days`) at every
    scheduling entry point (availability, guest holds),
  - captain accounts requiring a phone number,
  - the center subscription overview (KPIs the manager Subscribers page
    renders).
"""
from datetime import timedelta

import pytest
from bson import ObjectId
from pydantic import ValidationError

from app.core.exceptions import BadRequestException
from app.models.enums import UserRole
from app.schemas.subscription_schema import SubscribeRequest
from app.schemas.user_schema import StaffCreateRequest
from app.services.booking_service import BookingService
from app.services.subscription_service import UserSubscriptionService
from app.utils.timezone import now_ist

from tests.factories import get_hatchback_type_id, make_customer, make_service_center, make_subscription_plan

pytestmark = pytest.mark.asyncio


async def test_slots_and_holds_reject_dates_beyond_advance_window(db, cleanup):
    center_id = await make_service_center(db)
    cleanup.append(("service_centers", {"_id": ObjectId(center_id)}))
    svc = BookingService(db)

    far = (now_ist().date() + timedelta(days=8)).isoformat()
    near = (now_ist().date() + timedelta(days=1)).isoformat()

    with pytest.raises(BadRequestException, match="days in advance"):
        await svc.available_slots(center_id, far)
    with pytest.raises(BadRequestException, match="days in advance"):
        await svc.hold_slot("holderkey12345", center_id, far, "09:00-12:00")
    with pytest.raises(BadRequestException, match="already passed"):
        await svc.available_slots(center_id, (now_ist().date() - timedelta(days=1)).isoformat())

    # Tomorrow is inside the window — availability renders normally.
    slots = await svc.available_slots(center_id, near)
    assert slots and all("status" in s for s in slots)


async def test_late_assignment_gets_grace_before_late_flag(db, cleanup):
    """A captain handed a booking AFTER its slot began (last-minute booking)
    is not 'late' the moment he's assigned — the late clock starts
    late_assignment_grace_minutes (15) after assignment. Only once that
    grace passes does the late-to-start sweep pick the booking up."""
    from app.services.booking_service import BookingService, _effective_start_anchor
    from app.services.booking_policy_service import DEFAULT_BOOKING_POLICY

    now = now_ist()
    slot_start = now - timedelta(hours=2)  # slot began long ago

    # Pure anchor math: late assignment shifts the deadline; early doesn't.
    # (aware datetimes — same shape from_stored yields for real rows)
    late_assigned = {"assigned_at": now - timedelta(minutes=5)}
    early_assigned = {"assigned_at": now - timedelta(days=1)}
    policy = dict(DEFAULT_BOOKING_POLICY)
    assert _effective_start_anchor(late_assigned, slot_start, policy) > now  # still inside his 15-min grace
    assert _effective_start_anchor(early_assigned, slot_start, policy) == slot_start

    # Sweep behavior on real rows: freshly-assigned stays out, stale gets
    # flagged. Aware datetimes → pymongo stores true UTC instants, exactly
    # what assign_captain writes.
    base = {
        "status": "assigned", "captain_id": "cap-x", "customer_id": "cust-x", "service_center_id": "ctr-x",
        "scheduled_date": now.replace(hour=0, minute=0, second=0, microsecond=0, tzinfo=None),
        "scheduled_slot": "09:00-12:00",
        "estimated_start_at": now - timedelta(hours=2),
        "is_deleted": False,
    }
    fresh = await db.bookings.insert_one({**base, "customer_id": "cust-grace-fresh", "booking_number": "BK-GRACE-FRESH", "assigned_at": now - timedelta(minutes=5)})
    stale = await db.bookings.insert_one({**base, "customer_id": "cust-grace-stale", "booking_number": "BK-GRACE-STALE", "assigned_at": now - timedelta(minutes=40)})
    cleanup.append(("bookings", {"_id": {"$in": [fresh.inserted_id, stale.inserted_id]}}))

    due_ids = {str(b["_id"]) for b in await BookingService(db).find_bookings_late_to_start()}
    assert str(fresh.inserted_id) not in due_ids, "5 minutes after a late assignment is NOT late"
    assert str(stale.inserted_id) in due_ids, "40 minutes after assignment (grace long past) IS late"


async def test_captain_account_requires_phone():
    with pytest.raises(ValidationError, match="phone"):
        StaffCreateRequest(full_name="No Phone", password="Password@1", role=UserRole.CAPTAIN)
    # Managers stay phone-optional; captains with a valid phone pass.
    StaffCreateRequest(full_name="Mgr", password="Password@1", role=UserRole.MANAGER)
    ok = StaffCreateRequest(full_name="Cap", phone="98765 43210", password="Password@1", role=UserRole.CAPTAIN)
    assert ok.phone == "9876543210"


async def test_center_subscription_overview_kpis(db, cleanup):
    center_id = await make_service_center(db)
    cleanup.append(("service_centers", {"_id": ObjectId(center_id)}))
    customer_id = await make_customer(db)
    cleanup.append(("users", {"_id": ObjectId(customer_id)}))
    cleanup.append(("user_subscriptions", {"customer_id": customer_id}))
    plan_id = await make_subscription_plan(db, vehicle_types=[])
    cleanup.append(("subscription_plans", {"_id": ObjectId(plan_id)}))

    # A booking at this center makes the customer part of the center's book.
    booking = {"customer_id": customer_id, "service_center_id": center_id, "status": "completed", "is_deleted": False}
    res = await db.bookings.insert_one(booking)
    cleanup.append(("bookings", {"_id": res.inserted_id}))

    svc = UserSubscriptionService(db)
    hatchback = await get_hatchback_type_id(db)
    sub = await svc.subscribe(customer_id, SubscribeRequest(plan_id=plan_id, vehicle_type=hatchback))

    overview = await svc.center_overview(center_id, "manager", center_id)
    assert overview["kpis"]["total"] >= 1
    assert overview["kpis"]["active"] >= 1
    row = next(r for r in overview["rows"] if r["subscription_id"] == sub["id"])
    assert row["customer_id"] == customer_id
    assert row["status"] == "active"
    assert row["days_left"] is not None and row["days_left"] > 14
    assert overview["plan_breakdown"] and overview["plan_breakdown"][0]["active_count"] >= 1

    # Force the sub to expire in 3 days — it must show up in expiring_soon.
    await db.user_subscriptions.update_one(
        {"_id": ObjectId(sub["id"])}, {"$set": {"end_date": now_ist().replace(tzinfo=None) + timedelta(days=3)}}
    )
    overview = await svc.center_overview(center_id, "manager", center_id)
    assert overview["kpis"]["expiring_soon"] >= 1

    # A manager from another center is refused.
    from app.core.exceptions import ForbiddenException

    with pytest.raises(ForbiddenException):
        await svc.center_overview(center_id, "manager", str(ObjectId()))


async def test_customer_email_domain_allow_list():
    """Founder call: customer sign-ups accept only mainstream consumer mail
    providers. Staff accounts are deliberately NOT restricted (company
    domains are legitimate there)."""
    from app.schemas.user_schema import ManagerCreateCustomerRequest, RegisterRequest

    # Allowed, and normalised to lower case.
    assert RegisterRequest(full_name="A B", email="Ravi@Gmail.com", password="Password@1").email == "ravi@gmail.com"
    for good in ("a@yahoo.com", "a@yahoo.co.in", "a@outlook.com", "a@hotmail.com", "a@icloud.com", "a@rediffmail.com"):
        assert RegisterRequest(full_name="A B", email=good, password="Password@1").email == good

    for bad in ("a@yourfirm.co.in", "a@test.xyz", "a@mailinator.com"):
        with pytest.raises(ValidationError, match="Gmail, Yahoo"):
            RegisterRequest(full_name="A B", email=bad, password="Password@1")

    # Staff-created customers go through the same gate...
    with pytest.raises(ValidationError, match="Gmail, Yahoo"):
        ManagerCreateCustomerRequest(full_name="A B", email="a@test.xyz", phone="9876543210", temp_password="Password@1")
    ok = ManagerCreateCustomerRequest(full_name="A B", email="a@gmail.com", phone="9876543210", temp_password="Password@1")
    assert ok.email == "a@gmail.com"

    # ...but STAFF accounts keep full email freedom (company domains).
    StaffCreateRequest(full_name="Mgr", email="ops@blussit.in", password="Password@1", role=UserRole.MANAGER)

    # Phone-only sign-up is still fine — email stays optional.
    assert RegisterRequest(full_name="A B", phone="9876543210", password="Password@1").email is None
