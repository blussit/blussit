"""
Spec Sections 18-20: split captain/service ratings, only for
completed/eligible bookings, edit preserves original + audit history,
delete is soft (never destructive), and no cross-user access.
"""
import pytest
from bson import ObjectId

from app.core.exceptions import BadRequestException, NotFoundException
from app.schemas.review_schema import ReviewCreateRequest, ReviewUpdateRequest
from app.services.review_service import ReviewService

from tests.factories import get_star_wash_service_id, get_hatchback_type_id, make_captain, make_customer_with_vehicle, make_service_center


@pytest.fixture
async def completed_booking(db, cleanup):
    """A booking already in the terminal COMPLETED state, inserted
    directly (this file is testing review rules, not the booking state
    machine — already covered elsewhere) — real shape lifted from
    BookingModel's completed fields."""
    hatchback = await get_hatchback_type_id(db)
    foam = await get_star_wash_service_id(db)
    center_id = await make_service_center(db)
    cleanup.append(("service_centers", {"_id": ObjectId(center_id)}))
    customer_id, vehicle_id, address_id = await make_customer_with_vehicle(db, hatchback)
    cleanup.append(("users", {"_id": ObjectId(customer_id)}))
    cleanup.append(("vehicles", {"owner_id": customer_id}))
    cleanup.append(("addresses", {"owner_id": customer_id}))
    captain_id = await make_captain(db, center_id)
    cleanup.append(("users", {"_id": ObjectId(captain_id)}))
    cleanup.append(("captain_wallets", {"captain_id": captain_id}))

    result = await db.bookings.insert_one({
        "booking_number": "BK-TEST-REVIEW",
        "customer_id": customer_id,
        "vehicle_id": vehicle_id,
        "address_id": address_id,
        "service_center_id": center_id,
        "captain_id": captain_id,
        "service_ids": [foam],
        "status": "completed",
        "is_rated": False,
        "is_deleted": False,
    })
    booking_id = str(result.inserted_id)
    cleanup.append(("bookings", {"_id": ObjectId(booking_id)}))
    cleanup.append(("reviews", {"booking_id": booking_id}))
    return {"db": db, "booking_id": booking_id, "customer_id": customer_id, "captain_id": captain_id}


@pytest.mark.asyncio
async def test_create_review_requires_both_ratings_and_marks_booking_rated(completed_booking):
    service = ReviewService(completed_booking["db"])
    review = await service.create(
        completed_booking["customer_id"],
        ReviewCreateRequest(booking_id=completed_booking["booking_id"], captain_rating=5, captain_comment="Great!", service_rating=4, service_comment="Good wash"),
    )
    assert review["captain_rating"] == 5 and review["service_rating"] == 4
    assert review["captain_id"] == completed_booking["captain_id"]

    booking = await completed_booking["db"].bookings.find_one({"_id": ObjectId(completed_booking["booking_id"])})
    assert booking["is_rated"] is True


@pytest.mark.asyncio
async def test_cannot_review_non_completed_booking(db, cleanup):
    hatchback = await get_hatchback_type_id(db)
    customer_id, vehicle_id, address_id = await make_customer_with_vehicle(db, hatchback)
    cleanup.append(("users", {"_id": ObjectId(customer_id)}))
    cleanup.append(("vehicles", {"owner_id": customer_id}))
    cleanup.append(("addresses", {"owner_id": customer_id}))
    result = await db.bookings.insert_one({
        "booking_number": "BK-TEST-PENDING", "customer_id": customer_id, "vehicle_id": vehicle_id, "address_id": address_id,
        "service_center_id": "x", "status": "pending", "is_rated": False, "is_deleted": False,
    })
    booking_id = str(result.inserted_id)
    cleanup.append(("bookings", {"_id": ObjectId(booking_id)}))

    service = ReviewService(db)
    with pytest.raises(BadRequestException):
        await service.create(customer_id, ReviewCreateRequest(booking_id=booking_id, captain_rating=5, service_rating=5))


@pytest.mark.asyncio
async def test_cannot_review_same_booking_twice(completed_booking):
    service = ReviewService(completed_booking["db"])
    await service.create(completed_booking["customer_id"], ReviewCreateRequest(booking_id=completed_booking["booking_id"], captain_rating=5, service_rating=5))
    with pytest.raises(BadRequestException):
        await service.create(completed_booking["customer_id"], ReviewCreateRequest(booking_id=completed_booking["booking_id"], captain_rating=1, service_rating=1))


@pytest.mark.asyncio
async def test_cannot_review_someone_elses_booking(completed_booking, db, cleanup):
    other_customer_id = (await make_customer_with_vehicle(db, await get_hatchback_type_id(db)))[0]
    cleanup.append(("users", {"_id": ObjectId(other_customer_id)}))
    cleanup.append(("vehicles", {"owner_id": other_customer_id}))
    cleanup.append(("addresses", {"owner_id": other_customer_id}))

    service = ReviewService(completed_booking["db"])
    with pytest.raises(NotFoundException):
        await service.create(other_customer_id, ReviewCreateRequest(booking_id=completed_booking["booking_id"], captain_rating=5, service_rating=5))


@pytest.mark.asyncio
async def test_edit_preserves_original_on_first_edit_only(completed_booking):
    service = ReviewService(completed_booking["db"])
    review = await service.create(completed_booking["customer_id"], ReviewCreateRequest(booking_id=completed_booking["booking_id"], captain_rating=3, captain_comment="ok", service_rating=3, service_comment="ok"))

    first_edit = await service.update(completed_booking["customer_id"], review["id"], ReviewUpdateRequest(captain_rating=5, captain_comment="actually great"))
    assert first_edit["original_review"]["captain_rating"] == 3
    assert first_edit["captain_rating"] == 5
    assert first_edit["edit_count"] == 1

    second_edit = await service.update(completed_booking["customer_id"], review["id"], ReviewUpdateRequest(captain_rating=4))
    # original_review must NOT be overwritten by the second edit's prior state
    assert second_edit["original_review"]["captain_rating"] == 3
    assert second_edit["edit_count"] == 2


@pytest.mark.asyncio
async def test_cannot_edit_someone_elses_review(completed_booking, db, cleanup):
    service = ReviewService(completed_booking["db"])
    review = await service.create(completed_booking["customer_id"], ReviewCreateRequest(booking_id=completed_booking["booking_id"], captain_rating=5, service_rating=5))

    other_customer_id = (await make_customer_with_vehicle(db, await get_hatchback_type_id(db)))[0]
    cleanup.append(("users", {"_id": ObjectId(other_customer_id)}))
    cleanup.append(("vehicles", {"owner_id": other_customer_id}))
    cleanup.append(("addresses", {"owner_id": other_customer_id}))

    with pytest.raises(NotFoundException):
        await service.update(other_customer_id, review["id"], ReviewUpdateRequest(captain_rating=1))


@pytest.mark.asyncio
async def test_delete_is_soft_and_does_not_reset_is_rated(completed_booking):
    """Deliberate design: delete never resets booking.is_rated — prevents
    a delete-then-recreate abuse loop."""
    service = ReviewService(completed_booking["db"])
    review = await service.create(completed_booking["customer_id"], ReviewCreateRequest(booking_id=completed_booking["booking_id"], captain_rating=5, service_rating=5))

    await service.delete(completed_booking["customer_id"], review["id"])

    raw = await completed_booking["db"].reviews.find_one({"_id": ObjectId(review["id"])})
    assert raw["is_deleted"] is True
    assert raw["captain_rating"] == 5  # content preserved, not destroyed

    booking = await completed_booking["db"].bookings.find_one({"_id": ObjectId(completed_booking["booking_id"])})
    assert booking["is_rated"] is True  # NOT reset


@pytest.mark.asyncio
async def test_cannot_delete_someone_elses_review(completed_booking, db, cleanup):
    service = ReviewService(completed_booking["db"])
    review = await service.create(completed_booking["customer_id"], ReviewCreateRequest(booking_id=completed_booking["booking_id"], captain_rating=5, service_rating=5))

    other_customer_id = (await make_customer_with_vehicle(db, await get_hatchback_type_id(db)))[0]
    cleanup.append(("users", {"_id": ObjectId(other_customer_id)}))
    cleanup.append(("vehicles", {"owner_id": other_customer_id}))
    cleanup.append(("addresses", {"owner_id": other_customer_id}))

    with pytest.raises(NotFoundException):
        await service.delete(other_customer_id, review["id"])


@pytest.mark.asyncio
async def test_created_review_is_published(completed_booking):
    """ReviewModel defaults is_published=True, but create() builds the doc
    from the request schema (not the model) — this field must be set
    explicitly or list_public's {"is_published": True} filter silently
    matches nothing (a real bug this test caught: every review ever
    created was missing the field entirely)."""
    service = ReviewService(completed_booking["db"])
    review = await service.create(completed_booking["customer_id"], ReviewCreateRequest(booking_id=completed_booking["booking_id"], captain_rating=5, service_rating=5))
    raw = await completed_booking["db"].reviews.find_one({"_id": ObjectId(review["id"])})
    assert raw["is_published"] is True


@pytest.mark.asyncio
async def test_get_for_booking_returns_none_when_unreviewed(completed_booking):
    service = ReviewService(completed_booking["db"])
    result = await service.get_for_booking(completed_booking["booking_id"], completed_booking["customer_id"], "customer")
    assert result is None


@pytest.mark.asyncio
async def test_get_for_booking_visible_to_manager_and_owner(completed_booking):
    service = ReviewService(completed_booking["db"])
    await service.create(completed_booking["customer_id"], ReviewCreateRequest(booking_id=completed_booking["booking_id"], captain_rating=4, service_rating=5))

    as_customer = await service.get_for_booking(completed_booking["booking_id"], completed_booking["customer_id"], "customer")
    assert as_customer is not None and as_customer["captain_rating"] == 4

    as_manager = await service.get_for_booking(completed_booking["booking_id"], "some-manager-id", "manager")
    assert as_manager is not None and as_manager["service_rating"] == 5

    as_assigned_captain = await service.get_for_booking(completed_booking["booking_id"], completed_booking["captain_id"], "captain")
    assert as_assigned_captain is not None


@pytest.mark.asyncio
async def test_get_for_booking_hidden_from_unrelated_customer(completed_booking, db, cleanup):
    service = ReviewService(completed_booking["db"])
    await service.create(completed_booking["customer_id"], ReviewCreateRequest(booking_id=completed_booking["booking_id"], captain_rating=4, service_rating=5))

    other_customer_id = (await make_customer_with_vehicle(db, await get_hatchback_type_id(db)))[0]
    cleanup.append(("users", {"_id": ObjectId(other_customer_id)}))
    cleanup.append(("vehicles", {"owner_id": other_customer_id}))
    cleanup.append(("addresses", {"owner_id": other_customer_id}))

    with pytest.raises(NotFoundException):
        await service.get_for_booking(completed_booking["booking_id"], other_customer_id, "customer")


@pytest.mark.asyncio
async def test_list_for_center_enriches_with_names_and_scopes_to_own_center(completed_booking):
    """Spec Section 12: manager sees own-center reviews with the display
    fields already resolved (customer/captain/service names), not raw ids."""
    service = ReviewService(completed_booking["db"])
    await service.create(completed_booking["customer_id"], ReviewCreateRequest(booking_id=completed_booking["booking_id"], captain_rating=5, service_rating=4, captain_comment="Great captain"))

    booking = await completed_booking["db"].bookings.find_one({"_id": ObjectId(completed_booking["booking_id"])})
    center_id = booking["service_center_id"]

    items, total = await service.list_for_center(center_id, "manager", center_id, 1, 20)
    assert total == 1
    assert items[0]["captain_name"]  # resolved, not just captain_id
    assert items[0]["service_center_name"]
    assert items[0]["captain_comment"] == "Great captain"


@pytest.mark.asyncio
async def test_manager_cannot_list_reviews_for_another_center(completed_booking):
    from app.core.exceptions import ForbiddenException

    service = ReviewService(completed_booking["db"])
    booking = await completed_booking["db"].bookings.find_one({"_id": ObjectId(completed_booking["booking_id"])})
    with pytest.raises(ForbiddenException):
        await service.list_for_center(booking["service_center_id"], "manager", "some-other-center-id", 1, 20)
