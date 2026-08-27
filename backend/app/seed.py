"""
Seeds the database with a super admin account, service categories &
services, a sample service center, subscription plans, and landing-page
content (FAQs, testimonials) so the app is demo-ready immediately.

Run with:  python -m app.seed
"""
import asyncio

from bson import ObjectId

from app.core.config import settings
from app.core.database import close_mongo_connection, connect_to_mongo, mongodb
from app.core.security import hash_password


async def seed() -> None:
    await connect_to_mongo()
    db = mongodb.db

    print("Seeding database:", settings.MONGO_DB_NAME)

    # --- Super Admin -------------------------------------------------
    admin_email = "admin@doorstepvehiclecare.in"
    existing_admin = await db.users.find_one({"email": admin_email})
    if not existing_admin:
        await db.users.insert_one(
            {
                "full_name": "Platform Super Admin",
                "email": admin_email,
                "phone": "9999999999",
                "password_hash": hash_password("Admin@12345"),
                "role": "admin",
                "status": "active",
                "is_deleted": False,
            }
        )
        print(f"Created super admin -> {admin_email} / Admin@12345")
    else:
        print("Super admin already exists, skipping.")

    # --- Vehicle Types -------------------------------------------------
    # Admin-managed, not a fixed enum (see app/models/vehicle_type.py) — seed
    # a starting set so services/plans below have real ids to reference.
    vehicle_type_seeds = [
        ("Hatchback", "hatchback"), ("Sedan", "sedan"), ("SUV", "suv"),
        ("XUV 5-Seater", "xuv-5-seater"), ("XUV 7-Seater", "xuv-7-seater"), ("Bike", "bike"),
    ]
    vehicle_type_ids: dict[str, str] = {}
    for i, (name, slug) in enumerate(vehicle_type_seeds):
        existing = await db.vehicle_types.find_one({"slug": slug})
        if existing:
            vehicle_type_ids[slug] = str(existing["_id"])
            continue
        result = await db.vehicle_types.insert_one({"name": name, "slug": slug, "display_order": i, "is_active": True, "is_deleted": False})
        vehicle_type_ids[slug] = str(result.inserted_id)
    # "car" as shorthand for "every car-shaped type" (used below to keep the
    # service/plan definitions readable instead of spelling out all 5 ids
    # every time).
    car_type_ids = [vehicle_type_ids[s] for s in ("hatchback", "sedan", "suv", "xuv-5-seater", "xuv-7-seater")]
    print("Vehicle types ready:", vehicle_type_ids)

    # --- Categories & Services ----------------------------------------
    categories = [
        {"name": "Car Care", "slug": "car-care", "description": "Complete exterior & interior care for cars and SUVs.", "display_order": 1},
        {"name": "Bike Care", "slug": "bike-care", "description": "Doorstep wash and cleaning for two-wheelers.", "display_order": 2},
        {"name": "Premium Detailing", "slug": "premium-detailing", "description": "Subscription-based premium plans.", "display_order": 3},
    ]
    category_ids = {}
    for cat in categories:
        existing = await db.categories.find_one({"slug": cat["slug"]})
        if existing:
            category_ids[cat["slug"]] = str(existing["_id"])
            continue
        cat.update({"icon": None, "is_active": True, "is_deleted": False})
        result = await db.categories.insert_one(cat)
        category_ids[cat["slug"]] = str(result.inserted_id)
    print("Categories ready:", category_ids)

    services = [
        {"category": "car-care", "name": "Exterior Wash", "price": 249, "duration_minutes": 30, "vehicle_types": car_type_ids},
        {"category": "car-care", "name": "Foam Wash", "price": 349, "duration_minutes": 40, "vehicle_types": car_type_ids},
        {"category": "car-care", "name": "Interior Cleaning", "price": 499, "duration_minutes": 60, "vehicle_types": car_type_ids},
        {"category": "car-care", "name": "Vacuum Cleaning", "price": 199, "duration_minutes": 20, "vehicle_types": car_type_ids},
        {"category": "car-care", "name": "Wax Polish", "price": 899, "duration_minutes": 90, "vehicle_types": car_type_ids},
        {"category": "car-care", "name": "Dashboard Polish", "price": 299, "duration_minutes": 30, "vehicle_types": car_type_ids},
        {"category": "bike-care", "name": "Bike Foam Wash", "price": 149, "duration_minutes": 20, "vehicle_types": [vehicle_type_ids["bike"]]},
        {"category": "bike-care", "name": "Bike Water Wash", "price": 99, "duration_minutes": 15, "vehicle_types": [vehicle_type_ids["bike"]]},
        {"category": "bike-care", "name": "Chain Cleaning", "price": 129, "duration_minutes": 20, "vehicle_types": [vehicle_type_ids["bike"]]},
    ]
    service_ids: dict[str, str] = {}
    for svc in services:
        slug = svc["name"].lower().replace(" ", "-")
        existing = await db.services.find_one({"slug": slug})
        if existing:
            service_ids[slug] = str(existing["_id"])
            continue
        result = await db.services.insert_one(
            {
                "category_id": category_ids[svc["category"]],
                "name": svc["name"],
                "slug": slug,
                "description": f"Professional {svc['name'].lower()} at your doorstep.",
                "vehicle_types": svc["vehicle_types"],
                "price": svc["price"],
                "discounted_price": None,
                "duration_minutes": svc["duration_minutes"],
                "image": None,
                "is_active": True,
                "is_featured": svc["name"] in {"Foam Wash", "Interior Cleaning", "Bike Foam Wash"},
                "display_order": 0,
                "is_deleted": False,
            }
        )
        service_ids[slug] = str(result.inserted_id)
    print("Services seeded.")

    # --- Service Center -------------------------------------------------
    existing_center = await db.service_centers.find_one({"code": "IND-0001"})
    if not existing_center:
        result = await db.service_centers.insert_one(
            {
                "name": "Indore Central Service Hub",
                "code": "IND-0001",
                "location": {
                    "address": "AB Road, Indore",
                    "city": "Indore",
                    "state": "Madhya Pradesh",
                    "pincode": "452001",
                    "latitude": 22.7196,
                    "longitude": 75.8577,
                    "service_pincodes": ["452001", "452002", "452003", "452010"],
                    "radius_km": 6.0,
                },
                "manager_id": None,
                "contact_phone": "9999900000",
                "contact_email": "indore@doorstepvehiclecare.in",
                "working_hours_start": "08:00",
                "working_hours_end": "20:00",
                # 3-hour admin slots (08-11, 11-14, 14-17, 17-20), 50/day
                # split evenly across them by default — see
                # app/utils/slots.py / BookingService._reserve_slot_capacity.
                "slot_duration_minutes": 180,
                "default_slot_capacity": 13,
                "max_bookings_per_day": 50,
                "is_active": True,
                "is_deleted": False,
            }
        )
        center_id = str(result.inserted_id)

        manager_email = "manager.indore@doorstepvehiclecare.in"
        manager_result = await db.users.insert_one(
            {
                "full_name": "Indore Hub Manager",
                "email": manager_email,
                "phone": "9999911111",
                "password_hash": hash_password("Manager@12345"),
                "role": "manager",
                "status": "active",
                "service_center_id": center_id,
                "is_deleted": False,
            }
        )
        await db.service_centers.update_one({"_id": result.inserted_id}, {"$set": {"manager_id": str(manager_result.inserted_id)}})
        print(f"Created service center + manager -> {manager_email} / Manager@12345")

        captain_email = "captain.indore@doorstepvehiclecare.in"
        captain_result = await db.users.insert_one(
            {
                "full_name": "Rahul Verma",
                "email": captain_email,
                "phone": "9999922222",
                "password_hash": hash_password("Captain@12345"),
                "role": "captain",
                "status": "active",
                "service_center_id": center_id,
                "is_deleted": False,
            }
        )
        captain_id = str(captain_result.inserted_id)
        await db.captain_wallets.insert_one(
            {"captain_id": captain_id, "balance": 200.0, "minimum_balance": 50.0, "is_deleted": False}
        )
        print(f"Created captain -> {captain_email} / Captain@12345 (wallet seeded with ₹200)")

    # --- Pricing configuration --------------------------------------------
    existing_pricing = await db.settings.find_one({"key": "pricing_config"})
    if not existing_pricing:
        await db.settings.insert_one(
            {
                "key": "pricing_config",
                "value": {"per_km_rate": 5.0, "default_captain_service_fee": 40.0},
                "description": "Global captain payout configuration",
                "is_deleted": False,
            }
        )
        print("Pricing configuration seeded (₹5/km + ₹40 default captain fee).")

    # --- Subscription Plans ----------------------------------------------
    # Monthly -> Quarterly -> Yearly is a natural upgrade ladder — wired up
    # via upgrade_to_plan_ids below once all three exist, to demo the
    # admin-controlled-upgrade-path feature out of the box.
    #
    # Each plan names EXACTLY which service(s) it covers via
    # included_service_ids — "fixed type of service, decided by admin, not
    # the customer at booking time" (see BookingService._subscription_discount
    # for what happens if a customer books something else on it: a same-visit
    # "swap", charged the price difference, never free-for-anything).
    foam_wash_id = service_ids.get("foam-wash")
    plans = [
        {"name": "Monthly Shine", "billing_cycle": "monthly", "price": 799, "total_service_count": 4},
        {"name": "Quarterly Shine", "billing_cycle": "quarterly", "price": 2199, "total_service_count": 12},
        {"name": "Yearly Shine", "billing_cycle": "yearly", "price": 7999, "total_service_count": 48},
    ]
    plan_ids: dict[str, str] = {}
    for plan in plans:
        slug = plan["name"].lower().replace(" ", "-")
        existing = await db.subscription_plans.find_one({"slug": slug})
        if existing:
            plan_ids[plan["name"]] = str(existing["_id"])
            continue
        doc = {
            "name": plan["name"],
            "slug": slug,
            "description": f"{plan['total_service_count']}x Foam Wash included.",
            "billing_cycle": plan["billing_cycle"],
            "price": plan["price"],
            "discounted_price": None,
            "vehicle_type_prices": {},
            "vehicle_type_discounted_prices": {},
            "included_service_ids": [foam_wash_id] if foam_wash_id else [],
            "total_service_count": plan["total_service_count"],
            "vehicle_types": [],  # unrestricted — every vehicle type eligible
            "upgrade_to_plan_ids": [],
            "is_active": True,
            "is_popular": plan["name"] == "Quarterly Shine",
            "display_order": 0,
            "is_deleted": False,
        }
        # Demo of admin-set per-vehicle-type pricing (Monthly Shine only —
        # the other two keep a flat price so both cases are visible):
        # same plan, different cost by vehicle type, no fixed spread.
        if plan["name"] == "Monthly Shine":
            by_slug = {"hatchback": 399.0, "sedan": 449.0, "suv": 499.0, "xuv-5-seater": 549.0, "xuv-7-seater": 699.0}
            doc["vehicle_type_prices"] = {vehicle_type_ids[slug]: price for slug, price in by_slug.items() if slug in vehicle_type_ids}
        result = await db.subscription_plans.insert_one(doc)
        plan_ids[plan["name"]] = str(result.inserted_id)

    if "Monthly Shine" in plan_ids and "Quarterly Shine" in plan_ids:
        await db.subscription_plans.update_one({"_id": ObjectId(plan_ids["Monthly Shine"])}, {"$set": {"upgrade_to_plan_ids": [plan_ids["Quarterly Shine"]]}})
    if "Quarterly Shine" in plan_ids and "Yearly Shine" in plan_ids:
        await db.subscription_plans.update_one({"_id": ObjectId(plan_ids["Quarterly Shine"])}, {"$set": {"upgrade_to_plan_ids": [plan_ids["Yearly Shine"]]}})
    print("Subscription plans seeded.")

    # --- FAQs -------------------------------------------------------------
    faqs = [
        ("How does doorstep vehicle cleaning work?", "Book a service, and our verified captain arrives at your location with all equipment to clean your vehicle on the spot."),
        ("Is my vehicle safe with your captains?", "All captains are background-verified and trained professionals who follow strict quality and safety checklists."),
        ("What if I need to cancel or reschedule?", "You can cancel or reschedule any booking up to the point a captain starts the service, directly from your dashboard."),
        ("Do you offer subscription plans?", "Yes — monthly, quarterly, and yearly plans are available with a fixed number of included services."),
    ]
    for i, (q, a) in enumerate(faqs):
        existing = await db.faqs.find_one({"question": q})
        if existing:
            continue
        await db.faqs.insert_one({"question": q, "answer": a, "display_order": i, "is_active": True, "is_deleted": False})
    print("FAQs seeded.")

    print("\nSeeding complete.")
    await close_mongo_connection()


if __name__ == "__main__":
    asyncio.run(seed())
