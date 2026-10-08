import random
import string
from typing import Optional

from motor.motor_asyncio import AsyncIOMotorClientSession

from app.repositories.base_repository import BaseRepository
from app.utils.timezone import now_ist


class BookingRepository(BaseRepository):
    collection_name = "bookings"

    async def list_for_customer(self, customer_id: str, status: str | None, page: int, page_size: int):
        filters: dict = {"customer_id": customer_id, "is_deleted": {"$ne": True}}
        if status:
            filters["status"] = status
        # _id breaks created_at ties (a multi-car visit's cars share a
        # timestamp) so "Load more" pages never repeat or skip a car. A
        # customer's own set is small; the sort stays cheap.
        total = await self.collection.count_documents(filters)
        skip = max(page - 1, 0) * page_size
        cursor = self.collection.find(filters).sort([("created_at", -1), ("_id", -1)]).skip(skip).limit(page_size)
        return await cursor.to_list(length=page_size), total

    CAPTAIN_ACTIVE_STATUSES = ("assigned", "captain_on_the_way", "service_started")
    CAPTAIN_HISTORY_STATUSES = ("completed", "cancelled")

    async def list_for_captain(
        self, captain_id: str, status: str | None, page: int, page_size: int, scope: str | None = None
    ):
        """A captain's jobs, filtered and ordered on the server.

        "active" is the work still to do, soonest first; "history" is
        finished work, newest first; "all" is everything, newest first.
        Without a scope, no status means "active": the old client fetched
        page 1 of EVERY status oldest-first and kept the active ones itself,
        so once a captain had 100 finished jobs that page held none and his
        job list (and the GPS pings gated on it) went blank."""
        if scope is None:
            if status in self.CAPTAIN_HISTORY_STATUSES:
                scope = "history"
            elif status is None or status in self.CAPTAIN_ACTIVE_STATUSES:
                scope = "active"
            else:
                scope = "all"
        allowed = {"active": self.CAPTAIN_ACTIVE_STATUSES, "history": self.CAPTAIN_HISTORY_STATUSES}.get(scope)
        filters: dict = {"captain_id": captain_id}
        if status:
            if allowed is not None and status not in allowed:
                return [], 0
            filters["status"] = status
        elif allowed is not None:
            filters["status"] = {"$in": list(allowed)}
        return await self.list_queue(filters, page, page_size, sort="scheduled_asc" if scope == "active" else "scheduled_desc")

    async def list_for_center(self, service_center_id: str, status: str | None, page: int, page_size: int):
        filters: dict = {"service_center_id": service_center_id}
        if status:
            filters["status"] = status
        return await self.find_many(filters, page=page, page_size=page_size)

    async def list_all(self, filters: dict, page: int, page_size: int):
        return await self.find_many(filters, page=page, page_size=page_size)

    # A queue page must be a stable slice: scheduled_date alone is a day
    # (dozens of ties), and skip/limit over ties can repeat or drop rows
    # between pages — _id is the final tiebreak.
    _QUEUE_SORTS = {
        "scheduled_asc": [("scheduled_date", 1), ("scheduled_slot", 1), ("_id", 1)],
        "scheduled_desc": [("scheduled_date", -1), ("scheduled_slot", -1), ("_id", -1)],
        "created_desc": [("created_at", -1), ("_id", -1)],
    }

    # The queue's "total" is a pager hint, not a report: counting a center's
    # whole history on every page load was O(N). Past this many rows the
    # total reads as exactly QUEUE_COUNT_CAP (see is_capped_total).
    QUEUE_COUNT_CAP = 5000

    @classmethod
    def is_capped_total(cls, total: int) -> bool:
        return total >= cls.QUEUE_COUNT_CAP

    async def list_queue(self, filters: dict, page: int, page_size: int, sort: str | None = None) -> tuple[list[dict], int]:
        # Each sort is served by an index ending in the same keys (see the
        # booking queue indexes in core/database.py's scale pack) — the _id
        # tiebreak included, or Mongo sorts the whole match in memory.
        spec = self._QUEUE_SORTS.get(sort or "created_desc")
        if spec is None:
            raise ValueError("Unknown sort")
        query = {**filters, "is_deleted": {"$ne": True}}
        total = await self.collection.count_documents(query, limit=self.QUEUE_COUNT_CAP)
        skip = max(page - 1, 0) * page_size
        items = await self.collection.find(query).sort(spec).skip(skip).limit(page_size).to_list(length=page_size)
        return items, total

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

    async def find_active_for_customer(
        self, customer_id: str, exclude_booking_id: str | None = None, session: Optional[AsyncIOMotorClientSession] = None
    ) -> list[dict]:
        """Same idea as find_active_for_captain, but for the customer's own
        schedule — nothing previously stopped a customer from booking (or
        rescheduling into) two overlapping slots for themselves. Pass
        `session` to read inside the create/reschedule transaction."""
        filters: dict = {
            "customer_id": customer_id,
            # awaiting_payment counts: the slot is genuinely reserved while
            # the customer finishes paying, so it can't also be double-booked.
            "status": {"$in": ["awaiting_payment", "pending", "assigned", "captain_on_the_way", "service_started", "rescheduled"]},
        }
        docs = await self.find_all_no_paginate(filters, session=session)
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

    # How far back the garage / address-reuse look into one customer's
    # bookings: newest first on the (customer_id, created_at) index, then
    # capped — a runaway account can never make either read unbounded.
    CUSTOMER_HISTORY_SCAN = 300
    OPEN_STATUSES = ("awaiting_payment", "pending", "assigned", "captain_on_the_way", "service_started", "rescheduled")

    async def recent_address_ids(self, customer_id: str, limit: int = 50) -> list[str]:
        """The distinct addresses this customer's recent bookings used,
        most recently used first."""
        pipeline = [
            {"$match": {"customer_id": customer_id, "is_deleted": {"$ne": True}}},
            {"$sort": {"created_at": -1}},
            {"$limit": self.CUSTOMER_HISTORY_SCAN},
            {"$group": {"_id": "$address_id", "last": {"$max": "$created_at"}}},
            {"$sort": {"last": -1}},
            {"$limit": limit},
        ]
        rows = await self.collection.aggregate(pipeline).to_list(length=limit)
        return [str(r["_id"]) for r in rows if r.get("_id")]

    async def garage_groups(self, customer_id: str, today, limit: int = 50) -> list[dict]:
        """One row per CAR this customer has booked, newest first — the
        booking half of the garage (see app/services/garage_service.py).
        A car is its vehicle record when the booking has one ("v:<id>"),
        else its plate ("p:<plate>"), else just its vehicle type
        ("t:<type>") — so a plateless XUV washed twice is one row.

        Per car: the newest booking, the last completed wash day, how many
        washes, the soonest open booking from `today` (naive IST midnight,
        scheduled_date's own form) and the newest finished SINGLE-car
        booking to replay for "Clean again" (completed ahead of cancelled).
        `next` and `last_wash` carry their service_ids so the garage can name
        the service without a request per car."""
        as_str = lambda field: {"$ifNull": [{"$toString": field}, ""]}  # noqa: E731
        vid, plate, vtype = as_str("$vehicle_id"), as_str("$vehicle_registration_number"), as_str("$vehicle_type")
        completed = {"$eq": ["$status", "completed"]}
        pipeline = [
            {"$match": {"customer_id": customer_id, "is_deleted": {"$ne": True}}},
            {"$sort": {"created_at": -1}},
            {"$limit": self.CUSTOMER_HISTORY_SCAN},
            {"$addFields": {"_car": {"$switch": {
                "branches": [
                    {"case": {"$gt": [{"$strLenCP": vid}, 0]}, "then": {"$concat": ["v:", vid]}},
                    {"case": {"$gt": [{"$strLenCP": plate}, 0]}, "then": {"$concat": ["p:", plate]}},
                ],
                "default": {"$concat": ["t:", vtype]},
            }}}},
            {"$group": {
                "_id": "$_car",
                "vehicle_id": {"$first": "$vehicle_id"},
                "vehicle_type": {"$first": "$vehicle_type"},
                "vehicle_label": {"$first": "$vehicle_label"},
                "plate": {"$max": "$vehicle_registration_number"},
                "latest_at": {"$max": "$created_at"},
                "last_booking_id": {"$first": "$_id"},
                "last_washed_on": {"$max": {"$cond": [completed, "$scheduled_date", None]}},
                "wash_count": {"$sum": {"$cond": [completed, 1, 0]}},
                "next": {"$min": {"$cond": [
                    {"$and": [{"$in": ["$status", list(self.OPEN_STATUSES)]}, {"$gte": ["$scheduled_date", today]}]},
                    {"d": "$scheduled_date", "s": "$scheduled_slot", "id": "$_id", "svc": "$service_ids"},
                    None,
                ]}},
                # The newest finished wash, with its services ("Last Washed 3 Oct · Star Wash").
                "last_wash": {"$max": {"$cond": [
                    completed,
                    {"d": "$scheduled_date", "s": "$scheduled_slot", "id": "$_id", "svc": "$service_ids"},
                    None,
                ]}},
                "repeat": {"$max": {"$cond": [
                    {"$and": [{"$in": ["$status", ["completed", "cancelled"]]}, {"$not": [{"$ifNull": ["$booking_group_id", False]}]}]},
                    {"done": {"$cond": [completed, 1, 0]}, "d": "$scheduled_date", "s": "$scheduled_slot", "id": "$_id"},
                    None,
                ]}},
            }},
            {"$sort": {"latest_at": -1}},
            {"$limit": limit},
        ]
        return await self.collection.aggregate(pipeline).to_list(length=limit)

    async def list_subscription_bookings_for_center(self, service_center_id: str) -> list[dict]:
        """Every booking at this center paid for via a subscription — the raw
        data behind 'who at my store has a plan and has actually used it'."""
        return await self.find_all_no_paginate(
            {"service_center_id": service_center_id, "subscription_id": {"$ne": None}},
            sort_by="created_at",
            sort_order=-1,
        )

    NUMBER_PREFIX = "BK"
    NUMBER_WIDTH = 4

    @classmethod
    def number_candidates(cls, digits: str) -> list[str]:
        """Every stored booking number typed digits can mean: "12" is BK12,
        BK012 or BK0012 — the generator below zero-pads to NUMBER_WIDTH, so
        that is every form that can exist. Exact strings, so a search by
        number is an index point lookup, not a scan of every BK... value."""
        return [f"{cls.NUMBER_PREFIX}{'0' * pad}{digits}" for pad in range(max(0, cls.NUMBER_WIDTH - len(digits)) + 1)]

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
        return f"{self.NUMBER_PREFIX}{doc['seq']:0{self.NUMBER_WIDTH}d}"


class BookingStatusHistoryRepository(BaseRepository):
    collection_name = "booking_status_history"

    async def list_for_booking(self, booking_id: str) -> list[dict]:
        return await self.find_all_no_paginate({"booking_id": booking_id}, sort_by="created_at", sort_order=1)
