from fastapi import APIRouter, Depends
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.controllers.review_controller import ReviewController
from app.core.dependencies import CurrentUser, PaginationParams, get_current_user, get_db, require_customer, require_manager_or_admin
from app.schemas.review_schema import ReviewCreateRequest

router = APIRouter(prefix="/reviews", tags=["Reviews"])


@router.get("")
async def list_public_reviews(pagination: PaginationParams = Depends(), db: AsyncIOMotorDatabase = Depends(get_db)):
    return await ReviewController(db).list_public(pagination)


@router.post("", dependencies=[Depends(require_customer)])
async def create_review(payload: ReviewCreateRequest, current_user: CurrentUser = Depends(get_current_user), db: AsyncIOMotorDatabase = Depends(get_db)):
    return await ReviewController(db).create(current_user, payload)


@router.get("/captain/{captain_id}/summary")
async def captain_rating_summary(captain_id: str, db: AsyncIOMotorDatabase = Depends(get_db)):
    return await ReviewController(db).captain_summary(captain_id)


@router.get("/captain/{captain_id}", dependencies=[Depends(require_manager_or_admin)])
async def list_captain_reviews(captain_id: str, db: AsyncIOMotorDatabase = Depends(get_db)):
    """Individual reviews for one captain — what a manager sees behind the aggregate rating."""
    return await ReviewController(db).list_for_captain(captain_id)
