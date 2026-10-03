from typing import Optional

from fastapi import APIRouter, Depends, Query
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.controllers.review_controller import ReviewController
from app.core.authz import ensure_own_center
from app.core.dependencies import CurrentUser, PaginationParams, get_current_user, get_db, require_admin, require_customer, require_manager_or_admin
from app.core.exceptions import NotFoundException
from app.repositories.user_repository import UserRepository
from app.schemas.review_schema import ReviewCreateRequest, ReviewUpdateRequest

router = APIRouter(prefix="/reviews", tags=["Reviews"])


@router.get("")
async def list_public_reviews(pagination: PaginationParams = Depends(), db: AsyncIOMotorDatabase = Depends(get_db)):
    return await ReviewController(db).list_public(pagination)


@router.get("/my", dependencies=[Depends(require_customer)])
async def list_my_reviews(
    booking_ids: str | None = None, current_user: CurrentUser = Depends(get_current_user), db: AsyncIOMotorDatabase = Depends(get_db)
):
    """The customer's newest 100 reviews; `booking_ids` (comma-separated)
    narrows it to just those bookings' reviews."""
    ids = [b.strip() for b in (booking_ids or "").split(",") if b.strip()]
    return await ReviewController(db).list_my_reviews(current_user, ids or None)


@router.get("/admin/all", dependencies=[Depends(require_admin)])
async def list_all_reviews_for_admin(
    include_deleted: bool = False,
    service_center_id: Optional[str] = Query(None, pattern=r"^[0-9a-fA-F]{24}$"),
    captain_id: Optional[str] = Query(None, pattern=r"^[0-9a-fA-F]{24}$"),
    min_rating: Optional[int] = Query(None, ge=1, le=5),
    pagination: PaginationParams = Depends(),
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    """Moderation/audit visibility — includes soft-deleted reviews when
    include_deleted=true, unlike every other review listing. Center /
    captain / minimum-rating filters run server-side, paginated."""
    return await ReviewController(db).list_all_for_admin(
        pagination, include_deleted, service_center_id=service_center_id, captain_id=captain_id, min_rating=min_rating,
    )


@router.post("", dependencies=[Depends(require_customer)])
async def create_review(payload: ReviewCreateRequest, current_user: CurrentUser = Depends(get_current_user), db: AsyncIOMotorDatabase = Depends(get_db)):
    return await ReviewController(db).create(current_user, payload)


@router.put("/{review_id}", dependencies=[Depends(require_customer)])
async def update_review(
    review_id: str, payload: ReviewUpdateRequest, current_user: CurrentUser = Depends(get_current_user), db: AsyncIOMotorDatabase = Depends(get_db)
):
    return await ReviewController(db).update(current_user, review_id, payload)


@router.delete("/{review_id}", dependencies=[Depends(require_customer)])
async def delete_review(review_id: str, current_user: CurrentUser = Depends(get_current_user), db: AsyncIOMotorDatabase = Depends(get_db)):
    return await ReviewController(db).delete(current_user, review_id)


@router.get("/captain/{captain_id}/summary")
async def captain_rating_summary(captain_id: str, db: AsyncIOMotorDatabase = Depends(get_db)):
    return await ReviewController(db).captain_summary(captain_id)


@router.get("/captain/{captain_id}", dependencies=[Depends(require_manager_or_admin)])
async def list_captain_reviews(captain_id: str, current_user: CurrentUser = Depends(get_current_user), db: AsyncIOMotorDatabase = Depends(get_db)):
    """Individual reviews for one captain — what a manager sees behind the
    aggregate rating. Scoped to the manager's own team (the public summary
    endpoint above stays open; individual review text does not)."""
    captain = await UserRepository(db).find_by_id(captain_id)
    if not captain or captain.get("role") != "captain":
        raise NotFoundException("Captain not found")
    ensure_own_center(current_user.role, current_user.service_center_id, captain.get("service_center_id"))
    return await ReviewController(db).list_for_captain(captain_id)


@router.get("/booking/{booking_id}")
async def get_review_for_booking(booking_id: str, current_user: CurrentUser = Depends(get_current_user), db: AsyncIOMotorDatabase = Depends(get_db)):
    """One booking's review, if any — for inline display in a booking
    detail drawer. Returns null (not an error) when unreviewed."""
    return await ReviewController(db).get_for_booking(current_user, booking_id)


@router.get("/center/{service_center_id}", dependencies=[Depends(require_manager_or_admin)])
async def list_center_reviews(
    service_center_id: str, pagination: PaginationParams = Depends(), current_user: CurrentUser = Depends(get_current_user), db: AsyncIOMotorDatabase = Depends(get_db)
):
    """The Reviews page's manager view — own-center reviews, denormalized
    with customer/captain/service names for filtering and display."""
    return await ReviewController(db).list_for_center(current_user, service_center_id, pagination)
