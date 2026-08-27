"""
One-off, idempotent backfill: sets slot_start/slot_end (and, where a
captain is already assigned, estimated_start_at) on existing bookings that
predate those fields, using the exact same derivation the legacy code path
already used (_slot_start_datetime(scheduled_slot) + duration_minutes) —
so old rows get a real, directly-usable value instead of every new code
path needing a defensive fallback forever. Only touches bookings still in
an active/relevant state (skips already-completed/cancelled ones, which
have nothing further riding on these fields).

Safe to re-run: skips any booking that already has slot_start set.

Run with:  python -m app.scripts.backfill_booking_slots
"""
import asyncio
from datetime import timedelta

from app.core.config import settings
from app.core.database import close_mongo_connection, connect_to_mongo, mongodb
from app.services.booking_service import _slot_start_datetime


async def backfill() -> None:
    await connect_to_mongo()
    db = mongodb.db

    print("Backfilling booking slot_start/slot_end in:", settings.MONGO_DB_NAME)

    candidates = await db.bookings.find(
        {
            "slot_start": {"$exists": False},
            "status": {"$in": ["pending", "assigned", "captain_on_the_way", "service_started", "rescheduled"]},
        }
    ).to_list(length=None)

    updated = 0
    skipped = 0
    for booking in candidates:
        try:
            slot_start = _slot_start_datetime(booking["scheduled_date"], booking["scheduled_slot"])
        except (ValueError, IndexError, KeyError):
            skipped += 1
            continue
        duration = booking.get("duration_minutes", 60)
        slot_end = slot_start + timedelta(minutes=duration)
        update = {"slot_start": slot_start, "slot_end": slot_end}
        if booking.get("captain_id"):
            update["estimated_start_at"] = slot_start
        await db.bookings.update_one({"_id": booking["_id"]}, {"$set": update})
        updated += 1

    print(f"Backfill complete. {updated} booking(s) updated, {skipped} skipped (unparseable slot).")
    await close_mongo_connection()


if __name__ == "__main__":
    asyncio.run(backfill())
