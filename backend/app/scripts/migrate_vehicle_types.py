"""
One-off migration: introduces the new admin-managed `vehicle_types` collection
(replacing the old fixed car/suv/bike/luxury enum) and remaps every existing
document that referenced the old values.

Safe to re-run: seeding is idempotent (matches on slug), and remapping only
ever touches documents still holding one of the four legacy string values —
once remapped, re-running is a no-op for them.

Run from backend/: python -m app.scripts.migrate_vehicle_types
"""
import asyncio

from app.core.config import settings
from motor.motor_asyncio import AsyncIOMotorClient

# name -> slug, in seed/display order. Six total: the 5 the user asked for,
# plus Bike kept as a distinct still-needed category (existing bike services/
# pricing already depend on it — nothing in the request suggested dropping
# two-wheeler support, and the whole point of this system is that admins can
# freely add/edit/remove from here afterward).
SEED_TYPES = [
    ("Hatchback", "hatchback"),
    ("Sedan", "sedan"),
    ("SUV", "suv"),
    ("XUV 5-Seater", "xuv-5-seater"),
    ("XUV 7-Seater", "xuv-7-seater"),
    ("Bike", "bike"),
]

# Legacy enum value -> new slug(s). A single new slug means "map directly";
# a list means "this legacy bucket was broad, expand it to every matching new
# type" — used for both list-membership fields (vehicle_types) and dict-key
# fields (vehicle_type_prices), so old "car" pricing/eligibility still applies
# to every car-shaped type post-migration instead of silently vanishing.
LEGACY_TO_NEW = {
    "car": ["hatchback", "sedan", "suv", "xuv-5-seater", "xuv-7-seater"],
    "suv": ["suv"],
    "bike": ["bike"],
    "luxury": ["hatchback", "sedan", "suv", "xuv-5-seater", "xuv-7-seater"],
}
# For single-value fields (Vehicle.vehicle_type) we need exactly one target,
# not a fan-out — best-effort default, logged so it's easy to spot-check.
LEGACY_TO_SINGLE_NEW = {"car": "sedan", "suv": "suv", "bike": "bike", "luxury": "sedan"}


async def main() -> None:
    client = AsyncIOMotorClient(settings.MONGO_URI)
    db = client[settings.MONGO_DB_NAME]

    print("== Seeding vehicle types ==")
    slug_to_id: dict[str, str] = {}
    for i, (name, slug) in enumerate(SEED_TYPES):
        existing = await db.vehicle_types.find_one({"slug": slug})
        if existing:
            slug_to_id[slug] = str(existing["_id"])
            print(f"  already exists: {name} ({slug})")
            continue
        doc = {"name": name, "slug": slug, "display_order": i, "is_active": True, "is_deleted": False}
        res = await db.vehicle_types.insert_one(doc)
        slug_to_id[slug] = str(res.inserted_id)
        print(f"  created: {name} ({slug}) -> {res.inserted_id}")

    print("\n== Remapping vehicles.vehicle_type ==")
    async for v in db.vehicles.find({"vehicle_type": {"$in": list(LEGACY_TO_SINGLE_NEW.keys())}}):
        old = v["vehicle_type"]
        new_slug = LEGACY_TO_SINGLE_NEW[old]
        new_id = slug_to_id[new_slug]
        await db.vehicles.update_one({"_id": v["_id"]}, {"$set": {"vehicle_type": new_id}})
        print(f"  vehicle {v['_id']}: {old} -> {new_slug} ({new_id}) — spot-check this one, it was a best-effort default")

    print("\n== Remapping services.vehicle_types / vehicle_type_prices / vehicle_type_discounted_prices ==")
    async for s in db.services.find({}):
        changed = False
        update: dict = {}

        types = s.get("vehicle_types") or []
        if any(t in LEGACY_TO_NEW for t in types):
            new_types: list[str] = []
            for t in types:
                new_types.extend(slug_to_id[slug] for slug in LEGACY_TO_NEW.get(t, [t]) if slug in slug_to_id)
            update["vehicle_types"] = sorted(set(new_types))
            changed = True

        for field in ("vehicle_type_prices", "vehicle_type_discounted_prices"):
            prices = s.get(field) or {}
            if any(k in LEGACY_TO_NEW for k in prices):
                new_prices: dict[str, float] = {}
                for k, v in prices.items():
                    for slug in LEGACY_TO_NEW.get(k, [k]):
                        if slug in slug_to_id:
                            new_prices.setdefault(slug_to_id[slug], v)
                update[field] = new_prices
                changed = True

        if changed:
            await db.services.update_one({"_id": s["_id"]}, {"$set": update})
            print(f"  service {s['_id']} ({s.get('name')}): remapped")

    print("\n== Remapping combo_offers (same fields as services) ==")
    async for c in db.combo_offers.find({}):
        changed = False
        update: dict = {}
        types = c.get("vehicle_types") or []
        if any(t in LEGACY_TO_NEW for t in types):
            new_types = []
            for t in types:
                new_types.extend(slug_to_id[slug] for slug in LEGACY_TO_NEW.get(t, [t]) if slug in slug_to_id)
            update["vehicle_types"] = sorted(set(new_types))
            changed = True
        for field in ("vehicle_type_prices", "vehicle_type_discounted_prices"):
            prices = c.get(field) or {}
            if any(k in LEGACY_TO_NEW for k in prices):
                new_prices = {}
                for k, v in prices.items():
                    for slug in LEGACY_TO_NEW.get(k, [k]):
                        if slug in slug_to_id:
                            new_prices.setdefault(slug_to_id[slug], v)
                update[field] = new_prices
                changed = True
        if changed:
            await db.combo_offers.update_one({"_id": c["_id"]}, {"$set": update})
            print(f"  combo {c['_id']} ({c.get('name')}): remapped")

    print("\n== Remapping subscription_plans.vehicle_types ==")
    async for p in db.subscription_plans.find({}):
        types = p.get("vehicle_types") or []
        if any(t in LEGACY_TO_NEW for t in types):
            new_types = []
            for t in types:
                new_types.extend(slug_to_id[slug] for slug in LEGACY_TO_NEW.get(t, [t]) if slug in slug_to_id)
            await db.subscription_plans.update_one({"_id": p["_id"]}, {"$set": {"vehicle_types": sorted(set(new_types))}})
            print(f"  plan {p['_id']} ({p.get('name')}): remapped")

    print("\n== Backfilling missing vehicle_id on existing active subscriptions ==")
    # vehicle_id just became required going forward; a handful of
    # pre-existing subscriptions may predate that and have none. Best-effort:
    # attach each to the customer's own default (or first) vehicle so they
    # keep working, logged clearly since this is a real assumption, not a
    # certainty — worth a manual spot-check.
    async for sub in db.user_subscriptions.find({"status": "active", "vehicle_id": None}):
        vehicle = await db.vehicles.find_one({"owner_id": sub["customer_id"], "is_deleted": {"$ne": True}}, sort=[("is_default", -1)])
        if not vehicle:
            print(f"  subscription {sub['_id']}: customer {sub['customer_id']} has NO vehicles — left as-is, needs manual attention")
            continue
        await db.user_subscriptions.update_one({"_id": sub["_id"]}, {"$set": {"vehicle_id": str(vehicle["_id"])}})
        print(f"  subscription {sub['_id']}: attached to vehicle {vehicle['_id']} (customer {sub['customer_id']}'s default/first vehicle)")

    print("\nDone.")


if __name__ == "__main__":
    asyncio.run(main())
