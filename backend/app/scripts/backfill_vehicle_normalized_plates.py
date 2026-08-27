"""
One-off, idempotent backfill: sets registration_number_normalized on every
existing vehicle so the semi-unique-registration check (see
VehicleService.create/check_registration) actually sees pre-existing
duplicates instead of treating every old row as unique just because the
field didn't exist yet when it was written.

Safe to re-run: skips any vehicle that already has the field set.

Run with:  python -m app.scripts.backfill_vehicle_normalized_plates
"""
import asyncio

from app.core.config import settings
from app.core.database import close_mongo_connection, connect_to_mongo, mongodb
from app.utils.text import normalize_plate


async def backfill() -> None:
    await connect_to_mongo()
    db = mongodb.db

    print("Backfilling vehicle registration_number_normalized in:", settings.MONGO_DB_NAME)

    candidates = await db.vehicles.find({"registration_number_normalized": {"$exists": False}}).to_list(length=None)
    updated = 0
    for vehicle in candidates:
        normalized = normalize_plate(vehicle["registration_number"])
        await db.vehicles.update_one({"_id": vehicle["_id"]}, {"$set": {"registration_number_normalized": normalized}})
        updated += 1

    print(f"Backfill complete. {updated} vehicle(s) updated.")
    await close_mongo_connection()


if __name__ == "__main__":
    asyncio.run(backfill())
