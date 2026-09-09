from motor.motor_asyncio import AsyncIOMotorDatabase

from app.core.exceptions import NotFoundException
from app.models.enums import UserRole
from app.repositories.address_repository import AddressRepository
from app.repositories.booking_repository import BookingRepository
from app.repositories.complaint_repository import ComplaintRepository
from app.repositories.service_center_repository import ServiceCenterRepository
from app.repositories.subscription_repository import UserSubscriptionRepository
from app.repositories.user_repository import UserRepository
from app.repositories.vehicle_repository import VehicleRepository
from app.schemas.user_schema import UserPublic
from app.utils.serializers import serialize_list


class CRMService:
    """Builds a 360-degree customer profile view as required by the PRD's CRM module."""

    def __init__(self, db: AsyncIOMotorDatabase):
        self.user_repo = UserRepository(db)
        self.booking_repo = BookingRepository(db)
        self.subscription_repo = UserSubscriptionRepository(db)
        self.complaint_repo = ComplaintRepository(db)
        self.center_repo = ServiceCenterRepository(db)
        self.vehicle_repo = VehicleRepository(db)
        self.address_repo = AddressRepository(db)

    async def find_customer_by_phone(self, phone: str) -> dict | None:
        """Used by the manager 'book on behalf of a customer' flow. Never
        leaks a staff account via a phone search — customers only."""
        user = await self.user_repo.find_by_phone(phone)
        if not user or user.get("role") != UserRole.CUSTOMER.value:
            return None
        return UserPublic.from_doc(user).model_dump()

    async def get_customer_360(self, customer_id: str) -> dict:
        user = await self.user_repo.find_by_id(customer_id)
        # Customers only — same rule as find_customer_by_phone above. Without
        # this, passing a STAFF user id here dumped that staff member's
        # profile through a customer-CRM endpoint.
        if not user or user.get("role") != UserRole.CUSTOMER.value:
            raise NotFoundException("Customer not found")

        bookings, _ = await self.booking_repo.list_for_customer(customer_id, None, 1, 50)
        subscriptions = await self.subscription_repo.list_for_customer(customer_id)
        complaints, _ = await self.complaint_repo.list_for_customer(customer_id, 1, 50)

        completed_bookings = [b for b in bookings if b["status"] == "completed"]
        lifetime_spend = sum(b.get("total_amount", 0) for b in completed_bookings)
        last_service_date = max((b["scheduled_date"] for b in completed_bookings), default=None)

        center_counts: dict[str, int] = {}
        for b in bookings:
            center_counts[b["service_center_id"]] = center_counts.get(b["service_center_id"], 0) + 1
        preferred_center_id = max(center_counts, key=center_counts.get) if center_counts else None

        vehicles = await self.vehicle_repo.list_by_owner(customer_id)
        addresses = await self.address_repo.list_by_owner(customer_id)

        return {
            "profile": UserPublic.from_doc(user).model_dump(),
            "bookings": serialize_list(bookings),
            "subscriptions": serialize_list(subscriptions),
            "complaints": serialize_list(complaints),
            "vehicles": serialize_list(vehicles),
            "addresses": serialize_list(addresses),
            "lifetime_spend": round(lifetime_spend, 2),
            "last_service_date": last_service_date.isoformat() if last_service_date else None,
            "preferred_service_center_id": preferred_center_id,
            "total_bookings": len(bookings),
        }
