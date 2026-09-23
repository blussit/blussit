"""
Diagnoses and fixes subscriptions granted by a manager BEFORE this fix:
`user_subscriptions` never recorded which center's manager granted/sold the
plan, so a customer with no booking yet at that center was invisible on the
manager's Subscriptions page (center_overview only ever looked at booking
history). New grants now stamp `service_center_id` at creation time (and
the backend now REFUSES a grant from a manager with no center at all — see
app.core.authz.resolve_grant_center_id); this script is for the ones that
already existed before that fix.

  python -m app.scripts.backfill_subscription_center                      # trace + look only
  python -m app.scripts.backfill_subscription_center --apply              # trace + write it
  python -m app.scripts.backfill_subscription_center --list-centers       # id/name of every center
  python -m app.scripts.backfill_subscription_center --list-managers      # every manager + their center (or "NONE LINKED")
  python -m app.scripts.backfill_subscription_center --phone 98XXXXXXXX --center <center_id>            # look: what this customer holds, untagged or not
  python -m app.scripts.backfill_subscription_center --phone 98XXXXXXXX --center <center_id> --apply     # directly tag THAT customer's untagged subscription(s) with a center you choose by hand — use when the trace below can't find one (most often: the manager who granted it has no service_center_id on their own account, so there's nothing to trace TO — fix that account first with --list-managers, or just set the plan's center directly here)

Run from backend/. Safe by default: no flag ever writes without --apply.
"""
import argparse
import asyncio

from bson import ObjectId

from app.core.config import settings
from app.core.database import close_mongo_connection, connect_to_mongo, mongodb


async def list_centers(db) -> None:
    centers = await db.service_centers.find({"is_deleted": {"$ne": True}}, {"name": 1, "is_active": 1}).to_list(length=500)
    if not centers:
        print("No service centers found.")
        return
    for c in centers:
        print(f"  {c['_id']}  {c.get('name')}" + ("" if c.get("is_active", True) else "  (inactive)"))


async def list_managers(db) -> None:
    managers = await db.users.find(
        {"role": "manager", "is_deleted": {"$ne": True}}, {"full_name": 1, "phone": 1, "service_center_id": 1}
    ).to_list(length=500)
    if not managers:
        print("No manager accounts found.")
        return
    centers = {str(c["_id"]): c.get("name") for c in await db.service_centers.find({}, {"name": 1}).to_list(length=500)}
    for m in managers:
        center_id = m.get("service_center_id")
        where = f"{center_id}  ({centers.get(center_id, 'UNKNOWN CENTER ID')})" if center_id else "NONE LINKED — this manager's grants have nowhere to attribute to; an admin must link a center on their account (Admin -> Users -> edit this manager)"
        print(f"  {m.get('full_name')}  ·  {m.get('phone')}  ->  {where}")


async def fix_one_customer(db, phone: str, center_id: str, do_apply: bool) -> int:
    from app.repositories.base_repository import build_search_filter

    center = await db.service_centers.find_one({"_id": ObjectId(center_id)}) if ObjectId.is_valid(center_id) else None
    if not center:
        print(f"'{center_id}' is not a real service center id. Run --list-centers to see valid ones.")
        return 1
    user = await db.users.find_one(build_search_filter(phone, ["phone"]))
    if not user:
        print(f"No customer with phone {phone}.")
        return 1
    subs = await db.user_subscriptions.find({"customer_id": str(user["_id"]), "is_deleted": {"$ne": True}}).to_list(length=100)
    if not subs:
        print(f"{user.get('full_name')} ({phone}) holds no subscriptions at all.")
        return 1
    plans = {str(p["_id"]): p.get("name") for p in await db.subscription_plans.find({}, {"name": 1}).to_list(length=500)}
    print(f"{user.get('full_name')} ({phone}) — {len(subs)} subscription(s):")
    to_fix = []
    for s in subs:
        current = s.get("service_center_id")
        print(f"  {s['_id']}  plan={plans.get(s.get('plan_id'), s.get('plan_id'))}  status={s.get('status')}  service_center_id={current!r}")
        if not current:
            to_fix.append(s["_id"])
    if not to_fix:
        print("Every subscription already has a service_center_id — nothing to fix here.")
        return 0
    print(f"\nWould set service_center_id={center_id} ({center.get('name')}) on {len(to_fix)} untagged subscription(s) above.")
    if not do_apply:
        print("Dry run — nothing was changed. Add --apply to write it.")
        return 0
    result = await db.user_subscriptions.update_many(
        {"_id": {"$in": to_fix}, "service_center_id": {"$in": [None]}}, {"$set": {"service_center_id": center_id}}
    )
    print(f"Updated {result.modified_count} of {len(to_fix)}.")
    return 0


async def trace_backfill(db, do_apply: bool) -> int:
    subs = await db.user_subscriptions.find(
        {"service_center_id": {"$in": [None]}, "is_deleted": {"$ne": True}}
    ).to_list(length=5000)
    # Mongo's $in:[None] only matches docs where the field is literally null
    # OR absent — both count as "not yet backfilled".
    if not subs:
        print("Nothing to do — every subscription already has a service_center_id (or none exist).")
        return 0
    print(f"{len(subs)} subscription(s) with no service_center_id.\n")

    sub_ids = [str(s["_id"]) for s in subs]
    orders = await db.payment_orders.find(
        {"purpose": "subscription", "subscription_id": {"$in": sub_ids}, "issued_by": {"$ne": None}}
    ).to_list(length=5000)
    issued_by_sub: dict[str, str] = {o["subscription_id"]: o["issued_by"] for o in orders if o.get("subscription_id")}

    logs = await db.audit_logs.find(
        {"action": "ASSIGN_SUBSCRIPTION", "target_id": {"$in": sub_ids}}
    ).to_list(length=5000)
    for log in logs:
        issued_by_sub.setdefault(log["target_id"], log["actor_id"])

    manager_ids = {v for v in issued_by_sub.values() if v}
    managers = {
        str(u["_id"]): u
        for u in await db.users.find(
            {"_id": {"$in": [ObjectId(m) for m in manager_ids if ObjectId.is_valid(m)]}},
            {"service_center_id": 1, "full_name": 1, "role": 1, "phone": 1},
        ).to_list(length=2000)
    }

    fixable = {}   # sub_id -> (plan_name, center_id, manager_name)
    no_grantor = []      # never traced to anyone at all — almost certainly a genuine self-serve purchase
    grantor_no_center = []  # traced to a manager, but THAT manager has no service_center_id — the real gap
    plans_by_id = {
        str(p["_id"]): p.get("name")
        for p in await db.subscription_plans.find({}, {"name": 1}).to_list(length=500)
    }
    for s in subs:
        sid = str(s["_id"])
        manager_id = issued_by_sub.get(sid)
        if not manager_id:
            no_grantor.append(sid)
            continue
        manager = managers.get(manager_id)
        center_id = (manager or {}).get("service_center_id")
        if not center_id:
            grantor_no_center.append((sid, s.get("customer_id"), plans_by_id.get(s.get("plan_id"), s.get("plan_id")), manager))
            continue
        fixable[sid] = (plans_by_id.get(s.get("plan_id"), s.get("plan_id")), center_id, manager.get("full_name"))

    print(f"Traceable to a manager WITH a center (will be fixed): {len(fixable)}")
    for sid, (plan_name, center_id, manager_name) in fixable.items():
        print(f"  {sid}  plan={plan_name}  ->  center={center_id}  (granted by {manager_name})")

    if grantor_no_center:
        print(f"\nTraced to a manager, but THAT MANAGER HAS NO CENTER LINKED — cannot fix automatically ({len(grantor_no_center)}):")
        for sid, customer_id, plan_name, manager in grantor_no_center:
            who = f"{manager.get('full_name')} ({manager.get('phone')})" if manager else "unknown manager"
            print(f"  {sid}  customer={customer_id}  plan={plan_name}  granted by {who} — link a center to their account, or run --phone <this customer's phone> --center <id> to set it directly")

    if no_grantor:
        print(f"\nNo grantor found at all (likely a genuine self-serve purchase — left alone, correctly): {len(no_grantor)}")

    if not fixable:
        return 0
    if not do_apply:
        print("\nDry run — nothing was changed. Add --apply to write the fixable ones above.")
        return 0

    updated = 0
    for sid, (_plan_name, center_id, _manager_name) in fixable.items():
        result = await db.user_subscriptions.update_one(
            {"_id": ObjectId(sid), "service_center_id": {"$in": [None]}}, {"$set": {"service_center_id": center_id}}
        )
        updated += result.modified_count
    print(f"\nUpdated {updated} of {len(fixable)} subscription(s).")
    return 0


async def run(args) -> int:
    await connect_to_mongo(build_indexes=False)
    db = mongodb.db
    print(f"Database: {settings.MONGO_DB_NAME}\n")
    try:
        if args.list_centers:
            return await list_centers(db) or 0
        if args.list_managers:
            return await list_managers(db) or 0
        if args.phone:
            if not args.center:
                print("--phone needs --center <id> too (see --list-centers).")
                return 1
            return await fix_one_customer(db, args.phone, args.center, args.apply)
        return await trace_backfill(db, args.apply)
    finally:
        await close_mongo_connection()


def main() -> None:
    parser = argparse.ArgumentParser(description="Diagnose/fix subscriptions with no service_center_id (dry run unless --apply).")
    parser.add_argument("--apply", action="store_true", help="actually write the update (default is a dry run)")
    parser.add_argument("--list-centers", action="store_true", help="print every service center's id and name")
    parser.add_argument("--list-managers", action="store_true", help="print every manager and which center (if any) they're linked to")
    parser.add_argument("--phone", help="look up (and, with --center, directly fix) one customer's subscriptions by phone")
    parser.add_argument("--center", help="the service center id to set — used with --phone")
    raise SystemExit(asyncio.run(run(parser.parse_args())))


if __name__ == "__main__":
    main()
