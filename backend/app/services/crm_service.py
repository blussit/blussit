from motor.motor_asyncio import AsyncIOMotorDatabase

from app.core.exceptions import NotFoundException
from app.models.enums import UserRole
from app.repositories.address_repository import AddressRepository
from app.repositories.booking_repository import BookingRepository
from app.repositories.catalog_repository import ServiceRepository
from app.repositories.complaint_repository import ComplaintRepository
from app.repositories.service_center_repository import ServiceCenterRepository
from app.repositories.subscription_repository import SubscriptionPlanRepository, UserSubscriptionRepository
from app.repositories.user_repository import UserRepository
from app.repositories.vehicle_repository import VehicleRepository
from app.repositories.vehicle_type_repository import VehicleTypeRepository
from app.schemas.user_schema import UserPublic
from app.utils.serializers import serialize_list


class CRMService:
    """Builds a 360-degree customer profile view as required by the PRD's CRM module."""

    def __init__(self, db: AsyncIOMotorDatabase):
        self.user_repo = UserRepository(db)
        self.booking_repo = BookingRepository(db)
        self.subscription_repo = UserSubscriptionRepository(db)
        self.plan_repo = SubscriptionPlanRepository(db)
        self.complaint_repo = ComplaintRepository(db)
        self.center_repo = ServiceCenterRepository(db)
        self.vehicle_repo = VehicleRepository(db)
        self.address_repo = AddressRepository(db)
        self.service_repo = ServiceRepository(db)
        self.vehicle_type_repo = VehicleTypeRepository(db)

    async def find_customer_by_phone(self, phone: str) -> dict | None:
        """Used by the manager 'book on behalf of a customer' flow. Never
        leaks a staff account via a phone search — customers only."""
        user = await self.user_repo.find_by_phone(phone)
        if not user or user.get("role") != UserRole.CUSTOMER.value:
            return None
        return UserPublic.from_doc(user).model_dump()

    async def search_customers(self, q: str, limit: int = 8) -> list[dict]:
        """Typeahead for the manager's 'add a booking' / 'sell a plan' forms
        — matching by name OR phone as they type, so an existing customer
        is picked directly instead of accidentally re-created. Customers
        only, same rule as find_customer_by_phone above."""
        q = (q or "").strip()
        if len(q) < 2:
            return []
        items, _total = await self.user_repo.list_by_role(
            UserRole.CUSTOMER.value, page=1, page_size=limit, search=q
        )
        return [UserPublic.from_doc(u).model_dump() for u in items]

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

        # Display names for the admin/manager detail view below — resolved
        # once here rather than making the frontend chase
        # service_ids/address_id/vehicle_type/plan_id itself. Deliberately
        # NOT reusing BookingService._enrich_bookings (a private helper
        # scoped to that service) — same "resolve locally" call as
        # ReviewService._enrich makes, for the same reason.
        service_ids = {sid for b in bookings for sid in (b.get("service_ids") or [])}
        booking_address_ids = {b.get("address_id") for b in bookings if b.get("address_id")}
        vehicle_type_ids = {b.get("vehicle_type") for b in bookings if b.get("vehicle_type")}
        vehicle_type_ids.update(v.get("vehicle_type") for v in vehicles if v.get("vehicle_type"))
        center_ids = {b.get("service_center_id") for b in bookings if b.get("service_center_id")}
        plan_ids = {s.get("plan_id") for s in subscriptions if s.get("plan_id")}

        services = {str(s["_id"]): s.get("name") for s in await self.service_repo.find_by_ids(list(service_ids))} if service_ids else {}
        booking_addresses = {str(a["_id"]): a for a in await self.address_repo.find_by_ids(list(booking_address_ids))} if booking_address_ids else {}
        vehicle_type_names = {str(v["_id"]): v.get("name") for v in await self.vehicle_type_repo.find_by_ids(list(vehicle_type_ids))} if vehicle_type_ids else {}
        centers = {str(c["_id"]): c.get("name") for c in await self.center_repo.find_by_ids(list(center_ids))} if center_ids else {}
        plans = {str(p["_id"]): p.get("name") for p in await self.plan_repo.find_by_ids(list(plan_ids))} if plan_ids else {}

        enriched_bookings = []
        for b in bookings:
            addr = booking_addresses.get(b.get("address_id") or "")
            enriched_bookings.append({
                **b,
                "vehicle_label": b.get("vehicle_label") or vehicle_type_names.get(b.get("vehicle_type") or "") or "Vehicle",
                "service_names": [services.get(sid, "Service") for sid in (b.get("service_ids") or [])],
                "address_text": f"{addr.get('line1')}, {addr.get('city')}" if addr else None,
                "service_center_name": centers.get(b.get("service_center_id") or ""),
            })
        enriched_vehicles = [{**v, "vehicle_type_name": vehicle_type_names.get(v.get("vehicle_type") or "")} for v in vehicles]
        enriched_subscriptions = [{**s, "plan_name": plans.get(s.get("plan_id") or "", "Unknown plan")} for s in subscriptions]

        return {
            "profile": UserPublic.from_doc(user).model_dump(),
            "bookings": serialize_list(enriched_bookings),
            "subscriptions": serialize_list(enriched_subscriptions),
            "complaints": serialize_list(complaints),
            "vehicles": serialize_list(enriched_vehicles),
            "addresses": serialize_list(addresses),
            "lifetime_spend": round(lifetime_spend, 2),
            "last_service_date": last_service_date.isoformat() if last_service_date else None,
            "preferred_service_center_id": preferred_center_id,
            "preferred_service_center_name": centers.get(preferred_center_id or ""),
            "total_bookings": len(bookings),
        }
