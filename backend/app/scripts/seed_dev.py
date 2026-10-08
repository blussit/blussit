"""
Fill the LOCAL test database with ready-to-use accounts for every profile.

    cd backend && .venv/bin/python -m app.scripts.seed_dev      (or: scripts/dev.sh seed)

Refuses to run unless settings.dev_tools_active — i.e. backend/.env.development
with a database on this machine — so it can never write to production.
Idempotent: run it as often as you like.

Accounts (customers log in with their phone + the dev OTP, 123456):
  Admin     admin@doorstepvehiclecare.in
  Manager   manager.indore@doorstepvehiclecare.in
  Captain   captain.indore@doorstepvehiclecare.in
    (passwords: SEED_*_PASSWORD, else the local-only defaults in app/seed.py)
  Customer  9000000001  new customer, no plan, no bookings
  Customer  9000000002  has an active monthly pass
  Customer  9000000003  has a completed wash in their history
"""
import asyncio
import sys
from datetime import timedelta

from app.core.config import settings

TEST_CUSTOMERS = [
    ("9000000001", "Test Customer New"),
    ("9000000002", "Test Customer Pass"),
    ("9000000003", "Test Customer History"),
]


async def _ensure_customers(db) -> dict[str, str]:
    from app.services.auth_service import AuthService

    auth = AuthService(db)
    ids = {}
    for phone, name in TEST_CUSTOMERS:
        user = await auth.ensure_customer_by_phone(phone, name)
        ids[phone] = str(user["_id"])
    return ids


async def _ensure_pass(db, customer_id: str) -> str:
    from app.schemas.subscription_schema import SubscribeRequest
    from app.services.subscription_service import UserSubscriptionService

    if await db.user_subscriptions.find_one({"customer_id": customer_id, "status": "active"}):
        return "already has a pass"
    plan = await db.subscription_plans.find_one(
        {"is_active": True, "is_deleted": {"$ne": True}, "included_service_ids.0": {"$exists": True}}, sort=[("price", 1)]
    )
    hatchback = await db.vehicle_types.find_one({"slug": "hatchback"})
    if not plan or not hatchback:
        return "skipped (no active plan with a service, or no Hatchback type)"
    await UserSubscriptionService(db).subscribe(
        customer_id,
        SubscribeRequest(plan_id=str(plan["_id"]), vehicle_type=str(hatchback["_id"]), service_id=plan["included_service_ids"][0]),
    )
    return f"{plan.get('name')} pass added"


async def _ensure_history(db, phone: str, customer_id: str) -> str:
    from app.schemas.booking_schema import ManagerLogBookingRequest, QuickBookingLine
    from app.services.booking_service import BookingService
    from app.utils.timezone import now_ist

    if await db.bookings.find_one({"customer_id": customer_id, "status": "completed"}):
        return "already has a completed wash"
    manager = await db.users.find_one({"role": "manager", "service_center_id": {"$ne": None}, "is_deleted": {"$ne": True}})
    service = await db.services.find_one({"is_active": True, "is_addon": {"$ne": True}, "is_deleted": {"$ne": True}}, sort=[("display_order", 1)])
    hatchback = await db.vehicle_types.find_one({"slug": "hatchback"})
    if not (manager and service and hatchback):
        return "skipped (needs a manager with a center, an active service and a Hatchback type)"
    await BookingService(db).create_manager_logged_visit(
        ManagerLogBookingRequest(
            customer_name="Test Customer History", customer_phone=phone,
            lines=[QuickBookingLine(vehicle_type=str(hatchback["_id"]), quantity=1, service_ids=[str(service["_id"])])],
            scheduled_date=(now_ist() - timedelta(days=3)).strftime("%Y-%m-%d"), service_time="10:30",
            address_line="12 Test Lane, Vijay Nagar, Indore", send_whatsapp=False,
        ),
        manager_id=str(manager["_id"]), manager_center_id=manager["service_center_id"],
    )
    return f"completed {service.get('name')} added"


async def seed_dev() -> None:
    from app.core.database import close_mongo_connection, connect_to_mongo, mongodb
    from app.seed import seed

    await seed()  # base data: admin, center + manager + captain, services, plans
    await connect_to_mongo()
    db = mongodb.db
    try:
        ids = await _ensure_customers(db)
        for label, job in (
            ("9000000002", _ensure_pass(db, ids["9000000002"])),
            ("9000000003", _ensure_history(db, "9000000003", ids["9000000003"])),
        ):
            try:
                print(f"  {label}: {await job}")
            except Exception as exc:  # noqa: BLE001 — one optional extra must not stop the rest
                print(f"  {label}: skipped ({exc})")
    finally:
        await close_mongo_connection()
    print("\nLocal test accounts ready (customers: phone + OTP %s):" % settings.DEV_OTP_CODE)
    print(__doc__.split("Accounts")[1].split("\n", 1)[1])


if __name__ == "__main__":
    if not settings.dev_tools_active:
        sys.exit(
            "Refusing to seed: this only runs against the LOCAL test database "
            f"(APP_ENV={settings.APP_ENV}, database local={settings.mongo_is_local}, DEV_TOOLS_ENABLED={settings.DEV_TOOLS_ENABLED})."
        )
    asyncio.run(seed_dev())
