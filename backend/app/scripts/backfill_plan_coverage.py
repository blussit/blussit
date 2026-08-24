"""One-off, idempotent backfill: gives existing subscription_plans documents
(created before included_service_ids/vehicle_type_prices existed as
admin-settable concepts) real values, so already-live plans behave the same
way freshly-seeded ones now do — fixed, admin-decided service coverage
instead of silently covering anything free (see
BookingService._subscription_discount).

Safe to re-run: only ever touches a plan that's still missing coverage
(empty included_service_ids), never overwrites one an admin has since set
via the UI.

Run with:  python -m app.scripts.backfill_plan_coverage
"""
import asyncio

from app.core.config import settings
from app.core.database import close_mongo_connection, connect_to_mongo, mongodb


async def backfill() -> None:
    await connect_to_mongo()
    db = mongodb.db

    print("Backfilling plan coverage in:", settings.MONGO_DB_NAME)

    foam_wash = await db.services.find_one({"slug": "foam-wash"})
    if not foam_wash:
        print("No 'foam-wash' service found — nothing to backfill against. Run app.seed first.")
        await close_mongo_connection()
        return
    foam_wash_id = str(foam_wash["_id"])

    vehicle_types = {t["slug"]: str(t["_id"]) async for t in db.vehicle_types.find({"is_deleted": {"$ne": True}})}

    plans = await db.subscription_plans.find({"is_deleted": {"$ne": True}}).to_list(length=None)
    updated = 0
    for plan in plans:
        updates = {}
        if not plan.get("included_service_ids"):
            updates["included_service_ids"] = [foam_wash_id]
        if plan.get("slug") == "monthly-shine" and not plan.get("vehicle_type_prices"):
            by_slug = {"hatchback": 399.0, "sedan": 449.0, "suv": 499.0, "xuv-5-seater": 549.0, "xuv-7-seater": 699.0}
            updates["vehicle_type_prices"] = {vehicle_types[s]: p for s, p in by_slug.items() if s in vehicle_types}
        if updates:
            await db.subscription_plans.update_one({"_id": plan["_id"]}, {"$set": updates})
            print(f"  {plan['name']!r} <- {list(updates.keys())}")
            updated += 1

    print(f"\nBackfill complete. {updated} plan(s) updated, {len(plans) - updated} already had coverage set.")
    await close_mongo_connection()


if __name__ == "__main__":
    asyncio.run(backfill())
