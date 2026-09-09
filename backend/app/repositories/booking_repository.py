import random
import string
from typing import Optional

from motor.motor_asyncio import AsyncIOMotorClientSession

from app.repositories.base_repository import BaseRepository
from app.utils.timezone import now_ist


class BookingRepository(BaseRepository):
    collection_name = "bookings"

    async def list_for_customer(self, customer_id: str, status: str | None, page: int, page_size: int):
        filters: dict = {"customer_id": customer_id}
        if status:
            filters["status"] = status
        return await self.find_many(filters, page=page, page_size=page_size)

    async def list_for_captain(self, captain_id: str, status: str | None, page: int, page_size: int):
        filters: dict = {"captain_id": captain_id}
        if status:
            filters["status"] = status
        return await self.find_many(filters, page=page, page_size=page_size, sort_by="scheduled_date", sort_order=1)

    async def list_for_center(self, service_center_id: str, status: str | None, page: int, page_size: int):
        filters: dict = {"service_center_id": service_center_id}
        if status:
            filters["status"] = status
        return await self.find_many(filters, page=page, page_size=page_size)

    async def list_all(self, filters: dict, page: int, page_size: int):
        return await self.find_many(filters, page=page, page_size=page_size)

    async def exists_for_registration(self, registration_number: str, exclude_statuses: list[str]) -> bool:
        """Has this exact vehicle (by plate) ever had a non-cancelled booking on the platform,
        under any customer account? Used to gate the first-time-offer price — re-registering
        under a new phone number doesn't get you a second first-timer discount."""
        doc = await self.find_one(
            {"vehicle_registration_number": registration_number, "status": {"$nin": exclude_statuses}}
        )
        return doc is not None

    async def exists_for_phone(self, phone: str, exclude_statuses: list[str]) -> bool:
        doc = await self.find_one({"customer_phone": phone, "status": {"$nin": exclude_statuses}})
        return doc is not None

    async def find_active_for_captain(
        self, captain_id: str, exclude_booking_id: str | None = None, session: Optional[AsyncIOMotorClientSession] = None
    ) -> list[dict]:
        """Bookings currently occupying this captain's schedule — used for the concurrency lock.
        Pass `session` when this needs to be read+acted-on atomically within a transaction
        (see BookingService.assign_captain/reassign_captain)."""
        filters: dict = {
            "captain_id": captain_id,
            "status": {"$in": ["assigned", "captain_on_the_way", "service_started"]},
        }
        docs = await self.find_all_no_paginate(filters, session=session)
        if exclude_booking_id:
            docs = [d for d in docs if str(d["_id"]) != exclude_booking_id]
        return docs

    async def find_active_for_customer(self, customer_id: str, exclude_booking_id: str | None = None) -> list[dict]:
        """Same idea as find_active_for_captain, but for the customer's own
        schedule — nothing previously stopped a customer from booking (or
        rescheduling into) two overlapping slots for themselves."""
        filters: dict = {
            "customer_id": customer_id,
            "status": {"$in": ["pending", "assigned", "captain_on_the_way", "service_started", "rescheduled"]},
        }
        docs = await self.find_all_no_paginate(filters)
        if exclude_booking_id:
            docs = [d for d in docs if str(d["_id"]) != exclude_booking_id]
        return docs

    async def exists_active_for_vehicle_or_address(self, vehicle_id: str | None = None, address_id: str | None = None) -> dict | None:
        """Is there an upcoming/in-progress booking referencing this vehicle
        or address? Used to block deleting either out from under a booking
        that still needs it — a captain with no address to route to, or a
        vehicle-verification check with nothing to compare against."""
        field_filter: dict = {}
        if vehicle_id:
            field_filter["vehicle_id"] = vehicle_id
        elif address_id:
            field_filter["address_id"] = address_id
        else:
            return None
        return await self.find_one({**field_filter, "status": {"$nin": ["completed", "cancelled"]}})

    async def list_subscription_bookings_for_center(self, service_center_id: str) -> list[dict]:
        """Every booking at this center paid for via a subscription — the raw
        data behind 'who at my store has a plan and has actually used it'."""
        return await self.find_all_no_paginate(
            {"service_center_id": service_center_id, "subscription_id": {"$ne": None}},
            sort_by="created_at",
            sort_order=-1,
        )

    async def generate_unique_booking_number(self) -> str:
        # Sequential, human-friendly ids: BK0001, BK0002, ... (grows to
        # BK10000+ naturally). The atomic $inc on the counters doc makes
        # this safe under concurrent bookings — two simultaneous customers
        # can never draw the same number.
        from pymongo import ReturnDocument

        doc = await self.db.counters.find_one_and_update(
            {"_id": "booking_number"},
            {"$inc": {"seq": 1}},
            upsert=True,
            return_document=ReturnDocument.AFTER,
        )
        return f"BK{doc['seq']:04d}"


class BookingStatusHistoryRepository(BaseRepository):
    collection_name = "booking_status_history"

    async def list_for_booking(self, booking_id: str) -> list[dict]:
        return await self.find_all_no_paginate({"booking_id": booking_id}, sort_by="created_at", sort_order=1)
