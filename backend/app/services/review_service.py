from motor.motor_asyncio import AsyncIOMotorDatabase
from pymongo.errors import DuplicateKeyError

from app.core.authz import ensure_own_center
from app.core.exceptions import BadRequestException, NotFoundException
from app.models.enums import BookingStatus
from app.repositories.booking_repository import BookingRepository
from app.repositories.catalog_repository import ComboOfferRepository, ServiceRepository
from app.repositories.review_repository import ReviewRepository
from app.repositories.service_center_repository import ServiceCenterRepository
from app.repositories.user_repository import UserRepository
from app.repositories.vehicle_repository import VehicleRepository
from app.schemas.review_schema import ReviewCreateRequest, ReviewUpdateRequest
from app.utils.serializers import serialize_doc, serialize_list

_EDITABLE_FIELDS = ("captain_rating", "captain_comment", "service_rating", "service_comment")


class ReviewService:
    def __init__(self, db: AsyncIOMotorDatabase):
        self.repo = ReviewRepository(db)
        self.booking_repo = BookingRepository(db)
        self.center_repo = ServiceCenterRepository(db)
        self.user_repo = UserRepository(db)
        self.vehicle_repo = VehicleRepository(db)
        self.service_repo = ServiceRepository(db)
        self.combo_repo = ComboOfferRepository(db)

    async def create(self, customer_id: str, payload: ReviewCreateRequest) -> dict:
        booking = await self.booking_repo.find_by_id(payload.booking_id)
        if not booking or booking["customer_id"] != customer_id:
            raise NotFoundException("Booking not found")
        if booking["status"] != BookingStatus.COMPLETED.value:
            raise BadRequestException("You can only review a completed booking")
        if booking.get("is_rated"):
            raise BadRequestException("This booking has already been reviewed")

        doc = payload.model_dump()
        doc["customer_id"] = customer_id
        doc["captain_id"] = booking.get("captain_id")
        doc["service_center_id"] = booking.get("service_center_id")
        # doc is built from the REQUEST schema, not ReviewModel, so that
        # model's `is_published: bool = True` default is never actually
        # applied to the inserted document (BaseRepository.create() only
        # defaults created_at/updated_at/is_deleted) — every review ever
        # created was missing this field entirely, so list_public's
        # {"is_published": True} filter silently matched nothing. Set
        # explicitly here, same fix already applied to complaint status.
        doc["is_published"] = True
        try:
            created = await self.repo.create(doc)
        except DuplicateKeyError:
            # Double-submit race: the unique live-review-per-booking index
            # caught the second insert — same outcome as the is_rated check.
            raise BadRequestException("You've already reviewed this booking.")
        await self.booking_repo.update_by_id(payload.booking_id, {"is_rated": True})
        return serialize_doc(created)

    async def update(self, customer_id: str, review_id: str, payload: ReviewUpdateRequest) -> dict:
        """Ownership check is 404-not-403 (same convention as create's
        booking-not-found check) so this can't be used to confirm another
        customer's review even exists."""
        existing = await self.repo.find_by_id(review_id)
        if not existing or existing["customer_id"] != customer_id:
            raise NotFoundException("Review not found")

        data = {k: v for k, v in payload.model_dump(exclude_unset=True).items() if v is not None}
        if not data:
            raise BadRequestException("Nothing to update")

        update: dict = dict(data)
        # The true original is captured once, on the FIRST edit only — a
        # second edit must not overwrite it with the first edit's content.
        if existing.get("original_review") is None:
            update["original_review"] = {k: existing.get(k) for k in _EDITABLE_FIELDS}
        update["edit_count"] = existing.get("edit_count", 0) + 1

        updated = await self.repo.update_by_id(review_id, update)
        return serialize_doc(updated)

    async def delete(self, customer_id: str, review_id: str) -> None:
        """Soft delete only — never destroys the review's audit trail.
        Deliberately does NOT reset booking.is_rated, so a customer can't
        delete-then-recreate to game a "fresh" review on the same booking."""
        existing = await self.repo.find_by_id(review_id)
        if not existing or existing["customer_id"] != customer_id:
            raise NotFoundException("Review not found")
        await self.repo.soft_delete(review_id, deleted_by=customer_id)

    async def list_my_reviews(self, customer_id: str) -> list[dict]:
        reviews = await self.repo.list_for_customer(customer_id)
        return serialize_list(reviews)

    async def list_public(self, page: int, page_size: int):
        items, total = await self.repo.list_public(page, page_size)
        return serialize_list(items), total

    async def list_all_for_admin(self, page: int, page_size: int, include_deleted: bool = False):
        items, total = await self.repo.list_all_for_admin(page, page_size, include_deleted)
        return await self._enrich(items), total

    async def list_for_center(self, service_center_id: str, actor_role: str, actor_center_id: str | None, page: int, page_size: int):
        """A manager's own-center reviews (Section 12: "Manager should
        primarily see reviews relevant to their service center")."""
        ensure_own_center(actor_role, actor_center_id, service_center_id)
        items, total = await self.repo.find_many({"service_center_id": service_center_id}, page=page, page_size=page_size)
        return await self._enrich(items), total

    async def _enrich(self, reviews: list[dict]) -> list[dict]:
        """Reviews store only raw ids (customer/captain/service_center/
        booking) — denormalizes display names onto each one, the same
        "resolve once here, not N times in the frontend" approach
        BookingService._enrich_bookings uses for its own listings.

        IMPORTANT: service_names/combo_name/vehicle_snapshot are NOT stored
        on the booking document itself — BookingService._enrich_bookings
        computes them fresh, at read time, only for the specific listing
        paths that call it. A raw booking fetched here via
        booking_repo.find_by_ids (as this method necessarily does — it
        isn't going through that other enrichment path) never has those
        fields, so pulling them off `booking.get(...)` silently returns
        None for every single review, not just "some older ones" (that was
        this method's original, incorrect assumption). Fixed by resolving
        vehicle/service/combo directly here, the same way
        BookingService._enrich_bookings does it, rather than assuming
        another method already did the work."""
        if not reviews:
            return []
        booking_ids = {r["booking_id"] for r in reviews if r.get("booking_id")}
        captain_ids = {r["captain_id"] for r in reviews if r.get("captain_id")}
        center_ids = {r["service_center_id"] for r in reviews if r.get("service_center_id")}
        customer_ids = {r["customer_id"] for r in reviews if r.get("customer_id")}

        bookings = {str(b["_id"]): b for b in await self.booking_repo.find_by_ids(list(booking_ids))}
        captains = {str(c["_id"]): c for c in await self.user_repo.find_by_ids(list(captain_ids))}
        centers = {str(c["_id"]): c for c in await self.center_repo.find_by_ids(list(center_ids))}
        # customer_id lives directly on the review (always present, set at
        # create time) — resolved independently rather than only through
        # the booking's own denormalized customer_name, which some older
        # bookings never had populated.
        customers = {str(c["_id"]): c for c in await self.user_repo.find_by_ids(list(customer_ids))}

        vehicle_ids = {b["vehicle_id"] for b in bookings.values() if b.get("vehicle_id")}
        service_ids: set[str] = set()
        combo_ids: set[str] = set()
        for b in bookings.values():
            if b.get("combo_id"):
                combo_ids.add(b["combo_id"])
            else:
                service_ids.update(b.get("service_ids") or [])
        vehicles = {str(v["_id"]): v for v in await self.vehicle_repo.find_by_ids(list(vehicle_ids))}
        services = {str(s["_id"]): s for s in await self.service_repo.find_by_ids(list(service_ids))} if service_ids else {}
        combos = {str(c["_id"]): c for c in await self.combo_repo.find_by_ids(list(combo_ids))} if combo_ids else {}

        results = []
        for r in serialize_list(reviews):
            doc = dict(r)
            booking = bookings.get(r.get("booking_id") or "")
            captain = captains.get(r.get("captain_id") or "")
            center = centers.get(r.get("service_center_id") or "")
            customer = customers.get(r.get("customer_id") or "")
            vehicle = vehicles.get(booking.get("vehicle_id")) if booking else None
            doc["customer_name"] = (customer.get("full_name") if customer else None) or (booking.get("customer_name") if booking else None)
            doc["booking_number"] = booking.get("booking_number") if booking else None
            if booking and booking.get("combo_id"):
                combo = combos.get(booking["combo_id"])
                doc["combo_name"] = combo.get("name") if combo else None
                doc["service_names"] = None
            elif booking:
                doc["combo_name"] = None
                doc["service_names"] = [services[sid]["name"] for sid in (booking.get("service_ids") or []) if sid in services] or None
            else:
                doc["combo_name"] = None
                doc["service_names"] = None
            doc["vehicle_type_id"] = vehicle.get("vehicle_type") if vehicle else None
            doc["captain_name"] = captain.get("full_name") if captain else None
            doc["service_center_name"] = center.get("name") if center else None
            results.append(doc)
        return results

    async def captain_rating_summary(self, captain_id: str) -> dict:
        return await self.repo.average_rating_for_captain(captain_id)

    async def list_for_captain(self, captain_id: str) -> list[dict]:
        reviews = await self.repo.list_for_captain(captain_id)
        return serialize_list(reviews)

    async def get_for_booking(self, booking_id: str, actor_id: str, actor_role: str) -> dict | None:
        """The review for ONE booking, for inline display in a booking
        detail view (Section 13: manager/admin/captain shouldn't have to
        open a separate page to see what the customer said). None if the
        booking hasn't been reviewed yet — not an error, "No review yet"
        is a normal state. Same viewer access rule as
        BookingService.get_booking_with_history, so anyone who can already
        see the booking can see its review."""
        booking = await self.booking_repo.find_by_id(booking_id)
        if not booking:
            raise NotFoundException("Booking not found")
        if actor_role not in {"admin", "manager"} and actor_id not in {booking.get("customer_id"), booking.get("captain_id")}:
            raise NotFoundException("Booking not found")
        review = await self.repo.find_by_booking_id(booking_id)
        return serialize_doc(review) if review else None
