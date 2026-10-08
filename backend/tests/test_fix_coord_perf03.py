"""PERF-03 — bounded list queries keep their output:
  - the center's subscribers list is one grouped query + batched lookups,
    same rows as before (one per customer, newest visit first, visit count,
    plan name/status/remaining from the customer's latest plan booking);
  - a captain's review list and performance summary are bounded.
"""
from datetime import datetime, timedelta, timezone

import pytest
from bson import ObjectId

from app.repositories.review_repository import ReviewRepository
from app.services import staff_directory_service
from app.services.booking_service import BookingService
from tests.factories import make_customer, make_service_center


@pytest.mark.asyncio
async def test_subscribers_list_rows_match_the_old_semantics(db, cleanup):
    center = await make_service_center(db)
    cleanup.append(("service_centers", {"_id": ObjectId(center)}))
    plan = await db.subscription_plans.insert_one({"name": "Audit Monthly", "is_active": True})
    cleanup.append(("subscription_plans", {"_id": plan.inserted_id}))
    a, b = await make_customer(db, name="Asha"), await make_customer(db, name="Bilal")
    cleanup.append(("users", {"_id": {"$in": [ObjectId(a), ObjectId(b)]}}))
    sub_a = await db.user_subscriptions.insert_one({"customer_id": a, "plan_id": str(plan.inserted_id), "status": "active", "remaining_service_count": 2})
    sub_b = await db.user_subscriptions.insert_one({"customer_id": b, "plan_id": str(plan.inserted_id), "status": "expired", "remaining_service_count": 0})
    cleanup.append(("user_subscriptions", {"_id": {"$in": [sub_a.inserted_id, sub_b.inserted_id]}}))
    now = datetime.now(timezone.utc)
    rows = [
        {"customer_id": a, "subscription_id": str(sub_a.inserted_id), "created_at": now - timedelta(days=3)},
        {"customer_id": a, "subscription_id": str(sub_a.inserted_id), "created_at": now - timedelta(days=1)},
        {"customer_id": b, "subscription_id": str(sub_b.inserted_id), "created_at": now - timedelta(days=2)},
        {"customer_id": b, "subscription_id": None, "created_at": now},  # not a plan booking
        {"customer_id": a, "subscription_id": str(sub_a.inserted_id), "created_at": now, "is_deleted": True},  # deleted
    ]
    await db.bookings.insert_many([{**r, "service_center_id": center, "booking_number": f"PERF-{ObjectId()}", "status": "completed"} for r in rows])
    cleanup.append(("bookings", {"service_center_id": center}))

    out = await BookingService(db).list_subscribers_for_center(center, "admin", None)
    assert [r["customer_id"] for r in out] == [a, b], "newest visit first"
    first, second = out
    assert first["visits"] == 2 and first["plan_name"] == "Audit Monthly" and first["subscription_status"] == "active"
    assert first["remaining_service_count"] == 2 and first["customer_name"] == "Asha"
    assert second["visits"] == 1 and second["subscription_status"] == "expired"


@pytest.mark.asyncio
async def test_captain_reviews_and_performance_are_bounded(db, cleanup, monkeypatch):
    captain = f"cap-{ObjectId()}"
    await db.reviews.insert_many([{"captain_id": captain, "booking_id": str(ObjectId()), "rating": 5, "is_deleted": False, "created_at": datetime.now(timezone.utc)} for _ in range(5)])
    cleanup.append(("reviews", {"captain_id": captain}))
    monkeypatch.setattr(ReviewRepository, "CAPTAIN_REVIEWS_LIMIT", 3)
    assert len(await ReviewRepository(db).list_for_captain(captain)) == 3
    assert staff_directory_service._PERFORMANCE_MAX_JOBS >= 1000
