from motor.motor_asyncio import AsyncIOMotorDatabase

from app.core.exceptions import BadRequestException, ConflictException, ForbiddenException, NotFoundException
from app.repositories.address_repository import AddressRepository
from app.repositories.booking_repository import BookingRepository
from app.repositories.user_repository import UserRepository
from app.repositories.vehicle_repository import VehicleRepository
from app.schemas.profile_schema import (
    AddressCreateRequest,
    AddressUpdateRequest,
    VehicleCreateRequest,
    VehicleUpdateRequest,
)
from app.utils.address_match import fill_empty_fields, same_place
from app.utils.serializers import serialize_doc
from app.utils.text import normalize_plate


class VehicleService:
    def __init__(self, db: AsyncIOMotorDatabase):
        self.repo = VehicleRepository(db)
        self.booking_repo = BookingRepository(db)

    async def list_my_vehicles(self, owner_id: str) -> list[dict]:
        vehicles = await self.repo.list_by_owner(owner_id)
        return [serialize_doc(v) for v in vehicles]

    async def check_registration(self, owner_id: str, registration_number: str) -> dict:
        """Pre-check for the frontend's "already registered elsewhere —
        Continue/Cancel" confirmation, called before the actual create.
        Returns only a count, never the other account's identity — deliberately
        the same shape whether it's registered once, or not at all, on
        someone else's account, so nothing here can be used to enumerate
        which plates exist on the platform under other people's names."""
        normalized = normalize_plate(registration_number)
        other_owners = await self.repo.distinct_owners_for_registration(normalized, exclude_owner_id=owner_id)
        return {"already_registered": len(other_owners) > 0, "other_account_count": len(other_owners)}

    async def _ensure_plate_not_saved(self, owner_id: str, normalized: str, plate: str, exclude_id: str | None = None) -> None:
        """One account never holds the same car twice — "MP09 AB 1234" and
        "mp09ab1234" are the same plate (normalize_plate)."""
        if await self.repo.find_owner_plate(owner_id, normalized, exclude_id=exclude_id):
            raise ConflictException(f"{plate} is already saved on this account.")

    async def create(self, owner_id: str, payload: VehicleCreateRequest, actor_role: str = "customer") -> dict:
        normalized = normalize_plate(payload.registration_number)
        await self._ensure_plate_not_saved(owner_id, normalized, payload.registration_number)
        # Never trust the frontend's earlier check/acknowledgement alone —
        # re-count here, at the actual write, against whatever's true right
        # now. Admin can register a plate beyond the normal 2-account cap
        # (a deliberate, narrow override — see VehicleCreateRequest); nobody
        # else can, acknowledged or not.
        other_owners = await self.repo.distinct_owners_for_registration(normalized, exclude_owner_id=owner_id)
        if len(other_owners) >= 2 and actor_role != "admin":
            raise BadRequestException(
                "This vehicle registration number is already in use by the maximum allowed number of accounts."
            )
        if other_owners and not payload.acknowledge_shared_registration and actor_role != "admin":
            raise BadRequestException(
                "This vehicle is already registered with another account — confirm you want to continue adding it here."
            )
        if payload.is_default:
            await self.repo.clear_default(owner_id)
        doc = payload.model_dump(exclude={"acknowledge_shared_registration"})
        doc["owner_id"] = owner_id
        doc["registration_number_normalized"] = normalized
        created = await self.repo.create(doc)
        return serialize_doc(created)

    async def update(self, owner_id: str, vehicle_id: str, payload: VehicleUpdateRequest, actor_role: str = "customer") -> dict:
        existing = await self.repo.find_by_id(vehicle_id)
        if not existing or existing["owner_id"] != owner_id:
            raise NotFoundException("Vehicle not found")
        if payload.is_default:
            await self.repo.clear_default(owner_id)
        data = {k: v for k, v in payload.model_dump(exclude_unset=True).items() if v is not None and k != "acknowledge_shared_registration"}
        if payload.registration_number and normalize_plate(payload.registration_number) != existing.get("registration_number_normalized"):
            normalized = normalize_plate(payload.registration_number)
            await self._ensure_plate_not_saved(owner_id, normalized, payload.registration_number, exclude_id=vehicle_id)
            other_owners = await self.repo.distinct_owners_for_registration(normalized, exclude_owner_id=owner_id)
            if len(other_owners) >= 2 and actor_role != "admin":
                raise BadRequestException("This vehicle registration number is already in use by the maximum allowed number of accounts.")
            if other_owners and not payload.acknowledge_shared_registration and actor_role != "admin":
                raise BadRequestException("This vehicle is already registered with another account — confirm you want to continue.")
            data["registration_number_normalized"] = normalized
        updated = await self.repo.update_by_id(vehicle_id, data)
        return serialize_doc(updated)

    async def delete(self, owner_id: str, vehicle_id: str) -> None:
        existing = await self.repo.find_by_id(vehicle_id)
        if not existing or existing["owner_id"] != owner_id:
            raise NotFoundException("Vehicle not found")
        blocking = await self.booking_repo.exists_active_for_vehicle_or_address(vehicle_id=vehicle_id)
        if blocking:
            raise BadRequestException(
                f"This vehicle is used by booking {blocking['booking_number']}, which isn't finished yet — "
                "cancel or complete that booking before removing the vehicle."
            )
        # No longer checks for an "attached" subscription here — a
        # subscription covers a vehicle TYPE, not one specific vehicle
        # (see UserSubscriptionModel.vehicle_id's docstring), so removing
        # one vehicle never orphans a subscription; it just means one
        # fewer eligible vehicle for it, and it remains usable against any
        # other matching vehicle the customer owns.
        await self.repo.soft_delete(vehicle_id, owner_id)


class AddressService:
    def __init__(self, db: AsyncIOMotorDatabase):
        self.repo = AddressRepository(db)
        self.booking_repo = BookingRepository(db)
        self.user_repo = UserRepository(db)

    async def list_my_addresses(self, owner_id: str) -> list[dict]:
        addresses = await self.repo.list_by_owner(owner_id)
        return [serialize_doc(a) for a in addresses]

    async def list_for_customer(self, customer_id: str) -> list[dict]:
        """Staff booking for an existing customer picks one of their saved
        addresses instead of creating a new copy every time."""
        customer = await self.user_repo.find_by_id(customer_id)
        if not customer or customer.get("role") != "customer":
            raise NotFoundException("Customer not found")
        return await self.list_my_addresses(customer_id)

    async def find_same_place(self, owner_id: str, incoming: dict) -> tuple[dict | None, bool]:
        """Before a booking creates an address: is it a place this customer
        already has — a saved address, or one their recent bookings used?
        Same place = pins within ~60 m, else the same normalised line1 with
        no conflicting pincode (app/utils/address_match.py). The match is
        reused, gaining only the fields it was MISSING from `incoming`
        (landmark/flat line, a pin, a real pincode) — a filled field is
        never overwritten. Returns (address or None, has_saved_addresses).

        Bounded: the capped saved list + the addresses of the customer's
        newest bookings (indexed), never a scan of every address."""
        saved = await self.repo.list_by_owner(owner_id)
        seen = {str(a["_id"]) for a in saved}
        used = [i for i in await self.booking_repo.recent_address_ids(owner_id) if i not in seen]
        candidates = saved + [a for a in (await self.repo.find_by_ids(used) if used else []) if a.get("owner_id") == owner_id]

        best, best_rank = None, None
        for a in candidates:
            metres = same_place(a, incoming)
            if metres is None:
                continue
            # Nearest first; then the default; then the oldest record, so
            # repeat bookings keep converging on the original address.
            rank = (metres, not a.get("is_default"), str(a["_id"]))
            if best_rank is None or rank < best_rank:
                best, best_rank = a, rank
        if best is not None:
            updates = fill_empty_fields(best, incoming)
            if updates:
                best = await self.repo.update_by_id(str(best["_id"]), updates) or best
        return best, bool(saved)

    async def create(self, owner_id: str, payload: AddressCreateRequest) -> dict:
        if payload.is_default:
            await self.repo.clear_default(owner_id)
        doc = payload.model_dump()
        doc["owner_id"] = owner_id
        created = await self.repo.create(doc)
        return serialize_doc(created)

    async def update(self, owner_id: str, address_id: str, payload: AddressUpdateRequest) -> dict:
        existing = await self.repo.find_by_id(address_id)
        if not existing or existing["owner_id"] != owner_id:
            raise NotFoundException("Address not found")
        if payload.is_default:
            await self.repo.clear_default(owner_id)
        data = {k: v for k, v in payload.model_dump(exclude_unset=True).items() if v is not None}
        updated = await self.repo.update_by_id(address_id, data)
        return serialize_doc(updated)

    async def delete(self, owner_id: str, address_id: str) -> None:
        existing = await self.repo.find_by_id(address_id)
        if not existing or existing["owner_id"] != owner_id:
            raise NotFoundException("Address not found")
        blocking = await self.booking_repo.exists_active_for_vehicle_or_address(address_id=address_id)
        if blocking:
            raise BadRequestException(
                f"This address is used by booking {blocking['booking_number']}, which isn't finished yet — "
                "cancel or complete that booking before removing the address."
            )
        await self.repo.soft_delete(address_id, owner_id)
