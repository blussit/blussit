"""Backfill users.home_* / bookings_count for every customer.

    cd backend && ENV_FILE=.env.development .venv/bin/python -m app.scripts.backfill_customer_location [--all]

Bounded batches (500) with a per-pass time budget; prints the cursor so an
interrupted --all run resumes with `--after <last_id>`. Safe to re-run."""
import argparse
import asyncio

from app.core.database import close_mongo_connection, connect_to_mongo, get_database
from app.services.customer_location_service import backfill_customer_locations


async def main(all_customers: bool, after: str | None, budget: float) -> None:
    await connect_to_mongo()
    try:
        db = get_database()
        total, cursor = 0, after
        while True:
            r = await backfill_customer_locations(db, budget_seconds=budget, all_customers=all_customers, after_id=cursor)
            total += r["processed"]
            cursor = r["last_id"]
            print(f"processed {total} (cursor {cursor})")
            if r["done"]:
                break
        print("done")
    finally:
        await close_mongo_connection()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--all", action="store_true", help="re-derive every customer, not just unstamped ones")
    ap.add_argument("--after", default=None)
    ap.add_argument("--budget", type=float, default=30.0)
    a = ap.parse_args()
    asyncio.run(main(a.all, a.after, a.budget))
