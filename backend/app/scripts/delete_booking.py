"""
Permanently deletes ONE booking (and the rows that only exist because of it)
from the database in backend/.env. Built for cleaning up a test / mistaken
entry such as a "Log a done job" record.

Safe by default:
  * With no --delete flag it only SHOWS what it found and what it would remove.
  * --phone is required and must match the booking's customer, so a typo in
    the booking number can never delete somebody else's booking.
  * It refuses if there isn't exactly one match, if the booking is part of a
    multi-car visit, or if a subscription pass was spent on it (that needs the
    pass value handed back, not just a delete).
  * Before deleting it writes everything it is about to remove to a JSON
    backup file next to where you ran it, so it can be restored by hand.

Removed together with the booking: its status history, its in-app
notifications and its review, if any. Kept on purpose: audit-log entries (the
record that it existed) and WhatsApp send records; the customer account is
never touched.

Run from the backend/ folder:

  python -m app.scripts.delete_booking BK0015 --phone 7879858805          # look only
  python -m app.scripts.delete_booking BK0015 --phone 7879858805 --delete # really delete
"""
import argparse
import asyncio
import re
from datetime import datetime
from pathlib import Path

from bson import json_util

from app.core.config import settings
from app.core.database import close_mongo_connection, connect_to_mongo, mongodb


def _digits(value: str) -> str:
    digits = re.sub(r"\D", "", value or "")
    return digits[-10:]


def _summary(booking: dict) -> str:
    keys = (
        "booking_number", "status", "customer_phone", "total_amount", "payment_method", "payment_status",
        "scheduled_date", "scheduled_slot", "completed_by_role", "source", "service_code",
    )
    return "\n".join(f"    {key:18s} {booking.get(key)}" for key in keys)


async def run(number: str, phone: str, do_delete: bool) -> int:
    await connect_to_mongo(build_indexes=False)
    db = mongodb.db
    print(f"Database: {settings.MONGO_DB_NAME}")

    candidates = await db.bookings.find({"booking_number": number}).to_list(length=10)
    matches = [b for b in candidates if _digits(b.get("customer_phone") or "") == _digits(phone)]
    if len(matches) != 1:
        print(f"Found {len(candidates)} booking(s) numbered {number}, {len(matches)} of them for phone {phone}. Expected exactly 1 — nothing done.")
        await close_mongo_connection()
        return 1
    booking = matches[0]
    booking_id = str(booking["_id"])
    print("Found:\n" + _summary(booking))

    if booking.get("booking_group_id"):
        siblings = await db.bookings.count_documents({"booking_group_id": booking["booking_group_id"], "_id": {"$ne": booking["_id"]}})
        if siblings:
            print(f"This booking is one car of a {siblings + 1}-car visit — refusing to delete just one of them.")
            await close_mongo_connection()
            return 1
    if booking.get("subscription_id") and booking.get("subscription_consumption"):
        print("A subscription pass was spent on this booking — its value must be handed back first. Refusing.")
        await close_mongo_connection()
        return 1

    children = {
        "booking_status_history": {"booking_id": booking_id},
        "notifications": {"reference_id": booking_id},
        "reviews": {"booking_id": booking_id},
    }
    found = {name: await db[name].find(flt).to_list(length=500) for name, flt in children.items()}
    kept_audit = await db.audit_logs.count_documents({"target_id": booking_id})
    print("Related rows: " + ", ".join(f"{name}={len(rows)}" for name, rows in found.items()))
    print(f"Kept (not deleted): audit_logs={kept_audit}")

    if not do_delete:
        print("\nDry run — nothing was changed. Add --delete to remove the booking and the related rows above.")
        await close_mongo_connection()
        return 0

    backup = Path(f"deleted-{number}-{datetime.now().strftime('%Y%m%d-%H%M%S')}.json")
    backup.write_text(json_util.dumps({"booking": booking, **found}, indent=2))
    print(f"\nBackup written: {backup.resolve()}")

    for name, flt in children.items():
        result = await db[name].delete_many(flt)
        print(f"  deleted from {name}: {result.deleted_count}")
    result = await db.bookings.delete_one({"_id": booking["_id"]})
    print(f"  deleted from bookings: {result.deleted_count}")
    print("Still there afterwards:", await db.bookings.count_documents({"_id": booking["_id"]}), "(should be 0)")
    await close_mongo_connection()
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Permanently delete one booking (dry run unless --delete).")
    parser.add_argument("booking_number", help="e.g. BK0015")
    parser.add_argument("--phone", required=True, help="the customer's phone — must match the booking")
    parser.add_argument("--delete", action="store_true", help="actually delete (default is a dry run)")
    args = parser.parse_args()
    raise SystemExit(asyncio.run(run(args.booking_number.strip().upper(), args.phone, args.delete)))


if __name__ == "__main__":
    main()
