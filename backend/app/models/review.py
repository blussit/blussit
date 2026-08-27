from typing import Optional

from app.models.base import BusinessRecordBase


class ReviewModel(BusinessRecordBase):
    booking_id: str
    customer_id: str
    captain_id: Optional[str] = None
    service_center_id: Optional[str] = None
    # Legacy single rating/comment — kept for reviews written before the
    # captain/service split existed; never written to by new reviews.
    # Every read path prefers captain_rating/service_rating, falling back
    # to these for old documents (see ReviewRepository.average_rating_for_captain).
    rating: Optional[int] = None
    comment: Optional[str] = None
    captain_rating: Optional[int] = None
    captain_comment: Optional[str] = None
    service_rating: Optional[int] = None
    service_comment: Optional[str] = None
    # Snapshot of the review's content the FIRST time it's ever edited —
    # never overwritten again on subsequent edits, so the true original is
    # always recoverable for moderation/audit even after several edits.
    original_review: Optional[dict] = None
    edit_count: int = 0
    is_published: bool = True
