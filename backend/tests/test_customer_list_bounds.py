"""
Customer-facing lists and reminder-loop finders never load an unbounded
set (2026-09-28): my passes (newest 50), my vehicles / addresses (default
first, then newest, 50), my reviews (newest 100, or just the bookings
asked about), the eight booking sweep finders (sorted, 500 max), and the
indexes the new queries rely on exist.
"""
from datetime import datetime, timedelta, timezone

import pytest
from bson import ObjectId

from app.repositories.base_repository import BaseRepository
from app.services.booking_service import BookingService
from app.services.profile_service import AddressService, VehicleService
from app.services.review_service import ReviewService
from app.services.subscription_service import UserSubscriptionService

from tests.factories import make_customer

pytestmark = pytest.mark.asyncio


@pytest.fixture
async def customer(db, cleanup):
    cid = await make_customer(db)
    for coll, flt in (
        ("users", {"_id": ObjectId(cid)}), ("user_subscriptions", {"customer_id": cid}), ("vehicles", {"owner_id": cid}),
        ("addresses", {"owner_id": cid}), ("reviews", {"customer_id": cid}),
    ):
        cleanup.append((coll, flt))
    return cid


def _at(i: int) -> datetime:
    """Row i was created i minutes after a fixed start — higher is newer."""
    return datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(minutes=i)


async def test_my_passes_are_the_newest_50(db, customer):
    await db.user_subscriptions.insert_many([
        {"customer_id": customer, "plan_id": "p", "status": "expired", "is_deleted": False, "created_at": _at(i),
         "start_date": _at(i), "end_date": _at(i) + timedelta(days=30)}
        for i in range(55)
    ])
    mine = await UserSubscriptionService(db).list_my_subscriptions(customer)
    assert len(mine) == 50
    created = [m["created_at"] for m in mine]
    assert created == sorted(created, reverse=True)
    # The five OLDEST are the ones left out.
    assert {datetime.fromisoformat(c).astimezone(timezone.utc) for c in created} == {_at(i) for i in range(5, 55)}


async def test_vehicles_and_addresses_list_the_default_first_then_newest_capped_at_50(db, customer):
    await db.vehicles.insert_many([
        {"owner_id": customer, "vehicle_type": "t", "registration_number": f"MP09LB{i:04d}", "is_default": i == 3,
         "is_deleted": False, "created_at": _at(i)}
        for i in range(55)
    ])
    await db.addresses.insert_many([
        {"owner_id": customer, "line1": f"Bound Lane {i}", "city": "Indore", "state": "MP", "pincode": "452001",
         "is_default": i == 3, "is_deleted": False, "created_at": _at(i)}
        for i in range(55)
    ])
    for rows in (await VehicleService(db).list_my_vehicles(customer), await AddressService(db).list_my_addresses(customer)):
        assert len(rows) == 50
        assert rows[0]["is_default"] is True, "the default comes first even though it's old"
        rest = [datetime.fromisoformat(r["created_at"]).astimezone(timezone.utc) for r in rows[1:]]
        assert rest == sorted(rest, reverse=True) and rest[0] == _at(54)


async def test_my_reviews_are_the_newest_100_or_just_the_bookings_asked_about(db, customer):
    await db.reviews.insert_many([
        {"customer_id": customer, "booking_id": f"bk-{customer}-{i}", "rating": 5, "is_deleted": False, "created_at": _at(i)}
        for i in range(105)
    ])
    svc = ReviewService(db)
    newest = await svc.list_my_reviews(customer)
    assert len(newest) == 100
    assert f"bk-{customer}-0" not in {r["booking_id"] for r in newest}

    picked = await svc.list_my_reviews(customer, [f"bk-{customer}-0", f"bk-{customer}-7", "someone-elses"])
    assert {r["booking_id"] for r in picked} == {f"bk-{customer}-0", f"bk-{customer}-7"}


async def test_every_booking_sweep_finder_is_sorted_and_capped(db, monkeypatch):
    seen: list[tuple] = []
    real = BaseRepository.find_all_no_paginate

    async def spy(self, filters=None, sort_by="created_at", sort_order=-1, session=None, limit=None):
        if self.collection_name == "bookings":
            seen.append((sort_by, sort_order, limit))
        return await real(self, filters, sort_by, sort_order, session, limit)

    scanned: list[str] = []
    real_scan = BookingService._sweep_scan

    async def scan_spy(self, query, is_due):
        scanned.append("scan")
        return await real_scan(self, query, is_due)

    monkeypatch.setattr(BaseRepository, "find_all_no_paginate", spy)
    monkeypatch.setattr(BookingService, "_sweep_scan", scan_spy)
    bs = BookingService(db)
    # The time-window finders read EVERY candidate, earliest slot first, and
    # cap only what's due — a cut before the "is it due" check hid due
    # bookings behind the first 500 rows of a busy day.
    for finder in (
        bs.find_bookings_needing_reminder, bs.find_bookings_captain_not_reached, bs.find_bookings_late_to_start,
        bs.find_bookings_unassigned_too_long,
    ):
        await finder()
    assert scanned == ["scan"] * 4
    assert BookingService._SWEEP_ORDER == [("scheduled_date", 1), ("scheduled_slot", 1), ("_id", 1)]
    # The status-bounded ones stay sorted and capped.
    for finder in (
        bs.find_bookings_stuck_on_the_way, bs.find_bookings_service_overrunning,
        bs.find_bookings_idle_after_arrival, bs.find_bookings_captain_left_site,
    ):
        await finder()
    assert seen == [("scheduled_date", 1, BookingService.SWEEP_MAX_ROWS)] * 4
    assert BookingService.SWEEP_MAX_ROWS == 500


async def test_the_new_queries_have_their_indexes(db):
    async def keys(collection) -> list[list]:
        return [[(k, int(v)) for k, v in ix["key"]] for ix in (await db[collection].index_information()).values()]

    assert [("role", 1), ("last_completed_at", 1)] in await keys("users")
    assert [("subscription_id", 1), ("status", 1)] in await keys("bookings")
    assert [("customer_id", 1), ("created_at", -1)] in await keys("user_subscriptions")
    assert [("owner_id", 1), ("is_default", -1), ("created_at", -1)] in await keys("vehicles")
    assert [("owner_id", 1), ("is_default", -1), ("created_at", -1)] in await keys("addresses")
    assert [("customer_id", 1), ("created_at", -1)] in await keys("reviews")
    assert [("user_id", 1), ("created_at", -1)] in await keys("notifications")
