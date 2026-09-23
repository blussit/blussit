"""A manager reaching a customer's 360 profile (via the KPI drill-downs'
Plans/Bookings tabs, see [[manager-sells-plans]]) must only see that
customer's activity at THEIR OWN center — get_customer_360 previously had
no center-scoping at all, so a manager who legitimately knows a customer
through their own center could see that same customer's spend/bookings/
plans at a completely different center too. Admin stays unrestricted.
"""
from datetime import datetime, timezone

import pytest
from bson import ObjectId

from app.services.crm_service import CRMService
from tests.conftest import cleanup, db  # noqa: F401 — fixtures
from tests.factories import get_hatchback_type_id, make_customer_with_vehicle, make_service_center


def _booking_doc(customer_id: str, center_id: str, number: str, amount: float) -> dict:
    now = datetime.now(timezone.utc)
    return {
        "booking_number": number, "customer_id": customer_id, "service_center_id": center_id,
        "status": "completed", "total_amount": amount, "scheduled_date": now, "created_at": now,
        "customer_phone": "9000000000", "is_deleted": False,
    }


@pytest.mark.asyncio
async def test_manager_only_sees_their_own_centers_activity_on_customer_360(db, cleanup):
    center_a = await make_service_center(db, pincode="452001")
    cleanup.append(("service_centers", {"_id": ObjectId(center_a)}))
    center_b = await make_service_center(db, pincode="452002")
    cleanup.append(("service_centers", {"_id": ObjectId(center_b)}))

    hatchback = await get_hatchback_type_id(db)
    customer_id, _vehicle_id, _address_id = await make_customer_with_vehicle(db, hatchback)
    cleanup.append(("users", {"_id": ObjectId(customer_id)}))
    cleanup.append(("vehicles", {"owner_id": customer_id}))
    cleanup.append(("addresses", {"owner_id": customer_id}))

    booking_a = await db.bookings.insert_one(_booking_doc(customer_id, center_a, "BK-SCOPE-A", 300))
    booking_b = await db.bookings.insert_one(_booking_doc(customer_id, center_b, "BK-SCOPE-B", 500))
    cleanup.append(("bookings", {"_id": {"$in": [booking_a.inserted_id, booking_b.inserted_id]}}))

    crm = CRMService(db)

    # Center A's manager: only their own booking, spend, nothing from B.
    view_a = await crm.get_customer_360(customer_id, "manager", center_a)
    numbers_a = {b["booking_number"] for b in view_a["bookings"]}
    assert numbers_a == {"BK-SCOPE-A"}
    assert view_a["lifetime_spend"] == 300

    # Center B's manager: the mirror image.
    view_b = await crm.get_customer_360(customer_id, "manager", center_b)
    numbers_b = {b["booking_number"] for b in view_b["bookings"]}
    assert numbers_b == {"BK-SCOPE-B"}
    assert view_b["lifetime_spend"] == 500

    # Admin: unrestricted, sees the customer's FULL history across both.
    view_admin = await crm.get_customer_360(customer_id, "admin", None)
    numbers_admin = {b["booking_number"] for b in view_admin["bookings"]}
    assert numbers_admin == {"BK-SCOPE-A", "BK-SCOPE-B"}
    assert view_admin["lifetime_spend"] == 800
