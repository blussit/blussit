"""
One-off: mark which services are WATERLESS washes.

The booking flow tells the customer what to prepare — water and power for a
water-based wash, a shaded parking spot for a waterless one — so this has to
be a real flag, not a guess from the name at render time. This script sets it
once from the name; admin owns it from then on (Services page).

Idempotent: re-running only fixes services that don't already carry the flag.

Run:  python -m app.scripts.backfill_waterless_services [--dry-run]
"""
import argparse
import asyncio
import re

from app.core.database import connect_to_mongo, get_database

WATERLESS = re.compile(r"water\s*-?\s*less|waterless|dry\s+wash", re.I)


async def run(dry_run: bool) -> None:
    await connect_to_mongo(build_indexes=False)
    db = get_database()
    services = await db.services.find({"is_deleted": {"$ne": True}}).to_list(length=500)
    changes = []
    for svc in services:
        should_be = bool(WATERLESS.search(svc.get("name") or ""))
        if svc.get("is_waterless") == should_be:
            continue
        changes.append((svc["_id"], svc.get("name"), should_be))

    for _id, name, value in changes:
        print(f"  {name}: is_waterless -> {value}")
        if not dry_run:
            await db.services.update_one({"_id": _id}, {"$set": {"is_waterless": value}})
    print(f"{len(changes)} service(s) {'would change' if dry_run else 'updated'}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    asyncio.run(run(parser.parse_args().dry_run))
