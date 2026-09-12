"""
One-off migration to the monthly-pass model (founder model, 2026-09-11).

Three things, all idempotent — safe to re-run:

  1. Retire every non-MONTHLY plan. BLUSSIT sells monthly passes only now;
     quarterly/yearly plans stop being sellable but are NOT deleted, so
     subscriptions already sold under them keep renewing and reporting.
  2. Point each surviving monthly plan's `included_service_ids` at the
     services a pass may cover — the plan's own list if the admin already
     curated one, otherwise every active, non-add-on main service EXCEPT
     Deep Cleaning (which is deliberately not sold as a pass).
  3. Give plans a default `plan_discount_percent` so pass pricing (service
     price x visits, less this) has something to work with.

Run:  python -m app.scripts.migrate_monthly_passes [--discount 15] [--dry-run]
"""
import argparse
import asyncio

from app.core.database import connect_to_mongo, get_database

# Excluded from passes by name/slug — the founder's list is Jet Wash,
# Waterless and Star Wash; Deep Cleaning is a one-off detail job.
EXCLUDED_FROM_PASSES = {"deep-cleaning", "deep-clean"}


async def run(default_discount: float, dry_run: bool, reset_menu: bool) -> None:
    await connect_to_mongo(build_indexes=False)
    db = get_database()

    retired = await db.subscription_plans.count_documents(
        {"billing_cycle": {"$ne": "monthly"}, "is_active": True, "is_deleted": {"$ne": True}}
    )
    print(f"non-monthly plans still on sale: {retired}")
    if retired and not dry_run:
        await db.subscription_plans.update_many(
            {"billing_cycle": {"$ne": "monthly"}, "is_active": True, "is_deleted": {"$ne": True}},
            {"$set": {"is_active": False}},
        )
        print("  -> retired (existing subscriptions on them are untouched)")

    services = await db.services.find(
        {"is_active": True, "is_deleted": {"$ne": True}, "is_addon": {"$ne": True}}
    ).to_list(length=500)
    pass_service_ids = [
        str(s["_id"])
        for s in services
        if (s.get("slug") or "") not in EXCLUDED_FROM_PASSES
        and "deep clean" not in (s.get("name") or "").lower()
    ]
    print(f"services offerable as a pass: {len(pass_service_ids)}")
    for s in services:
        mark = "  offered" if str(s["_id"]) in pass_service_ids else "  EXCLUDED"
        print(f"{mark}: {s.get('name')}")

    plans = await db.subscription_plans.find(
        {"billing_cycle": "monthly", "is_deleted": {"$ne": True}}
    ).to_list(length=200)
    for plan in plans:
        updates: dict = {}
        # By default an admin-curated menu is left alone; --reset-menu
        # replaces it with "every pass-offerable service".
        if reset_menu or not plan.get("included_service_ids"):
            if plan.get("included_service_ids") != pass_service_ids:
                updates["included_service_ids"] = pass_service_ids
        if plan.get("plan_discount_percent") is None:
            updates["plan_discount_percent"] = default_discount
        if not updates:
            print(f"plan '{plan.get('name')}': already configured")
            continue
        print(f"plan '{plan.get('name')}': {list(updates)}")
        if not dry_run:
            await db.subscription_plans.update_one({"_id": plan["_id"]}, {"$set": updates})

    print("dry run — nothing written" if dry_run else "done")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--discount", type=float, default=15.0)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--reset-menu", action="store_true", help="Overwrite each plan's service menu, not just fill an empty one")
    args = parser.parse_args()
    asyncio.run(run(args.discount, args.dry_run, args.reset_menu))
