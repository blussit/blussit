"""Builders shared by the society-plan tests (docs/SOCIETY_PLANS.md)."""
import itertools
from datetime import datetime, timezone

from bson import ObjectId

from app.core.security import create_access_token
from app.schemas.society_schema import SocietyCreateRequest, SocietyEnrollRequest, SocietyPlanRequest
from app.services.society_service import SocietyService

from tests.factories import get_hatchback_type_id, get_star_wash_service_id, make_captain, make_customer, make_manager

_seq = itertools.count(1)

# Each test center sits in its own far-away spot so "nearest center" is
# unambiguous whatever other suites left behind (they all use 22.7, 75.8).
_SPOTS = [(23.2599, 77.4126), (21.1458, 79.0882), (26.9124, 75.7873), (19.0760, 72.8777), (18.5204, 73.8567), (17.3850, 78.4867)]


def plate() -> str:
    return f"MP04SC{next(_seq):04d}"


async def make_center(db, cleanup, spot: int = 0) -> dict:
    lat, lng = _SPOTS[spot % len(_SPOTS)]
    n = next(_seq)
    pincode = f"46{n:04d}"
    doc = {
        "name": f"Society Test Center {n}",
        "code": f"SOCTEST{n:05d}",
        "location": {
            "address": "Test", "city": "Bhopal", "state": "MP", "pincode": pincode,
            "latitude": lat, "longitude": lng, "service_pincodes": [pincode], "radius_km": 3.0,
        },
        "working_hours_start": "08:00",
        "working_hours_end": "20:00",
        "slot_duration_minutes": 180,
        "default_slot_capacity": 10,
        "is_active": True,
        "is_deleted": False,
    }
    result = await db.service_centers.insert_one(doc)
    cleanup.append(("service_centers", {"_id": result.inserted_id}))
    return {"id": str(result.inserted_id), "lat": lat, "lng": lng, "pincode": pincode}


async def make_admin(db, cleanup) -> str:
    n = next(_seq)
    now = datetime.now(timezone.utc)
    result = await db.users.insert_one({
        "full_name": f"Society Admin {n}", "email": f"soc.admin.{n}@example.com", "phone": f"6{n:09d}",
        "role": "admin", "status": "active", "is_deleted": False, "created_at": now, "updated_at": now,
    })
    cleanup.append(("users", {"_id": result.inserted_id}))
    return str(result.inserted_id)


async def make_society(db, cleanup, center: dict, *, admin_id: str = "admin-test", name: str | None = None) -> dict:
    service = SocietyService(db)
    created = await service.create_society(
        SocietyCreateRequest(
            name=name or f"Green Acres {next(_seq)}", address_line="12 Ring Road", area="Test Nagar",
            pincode=center["pincode"], latitude=center["lat"] + 0.001, longitude=center["lng"] + 0.001,
            contact_name="Mr Secretary", contact_phone="9876500001", service_center_id=center["id"],
        ),
        admin_id, "admin", None,
    )
    cleanup.append(("societies", {"_id": ObjectId(created["id"])}))
    cleanup.append(("society_enrollments", {"society_id": created["id"]}))
    cleanup.append(("society_attendance", {"society_id": created["id"]}))
    cleanup.append(("society_payments", {"society_id": created["id"]}))
    cleanup.append(("user_subscriptions", {"society_id": created["id"]}))
    return created


async def make_template(db, cleanup, *, bucket_days: int = 25, count: int = 2, price: float = 1649, mrp: float = 2000,
                        scope: str = "template", society_ids=None, customer_phone=None, type_prices=None) -> dict:
    service = SocietyService(db)
    plan = await service.create_plan(
        SocietyPlanRequest(
            name=f"Daily wash + {count} Star Wash #{next(_seq)}", bucket_days=bucket_days,
            premium_service_id=await get_star_wash_service_id(db), premium_count=count, price=price, mrp=mrp,
            vehicle_type_prices=type_prices or {}, scope=scope, society_ids=society_ids or [], customer_phone=customer_phone,
        ),
        "admin-test",
    )
    cleanup.append(("subscription_plans", {"_id": ObjectId(plan["id"])}))
    return plan


async def make_resident(db, cleanup) -> dict:
    customer_id = await make_customer(db)
    user = await db.users.find_one({"_id": ObjectId(customer_id)})
    cleanup.append(("users", {"_id": ObjectId(customer_id)}))
    cleanup.append(("vehicles", {"owner_id": customer_id}))
    cleanup.append(("addresses", {"owner_id": customer_id}))
    cleanup.append(("bookings", {"customer_id": customer_id}))
    cleanup.append(("notifications", {"user_id": customer_id}))
    cleanup.append(("payment_orders", {"customer_id": customer_id}))
    return user


async def enroll(db, society: dict, resident: dict, plan_id: str | None = None, *, cars: int = 2, custom=None, vehicle_type=None, pay_now=False) -> dict:
    hatchback = vehicle_type or await get_hatchback_type_id(db)
    payload = SocietyEnrollRequest(
        plan_id=plan_id, custom=custom, resident_name=resident.get("full_name") or "Resident", phone=resident["phone"],
        flat=f"B-{next(_seq)}", cars=[{"vehicle_type": hatchback, "registration_number": plate()} for _ in range(cars)], pay_now=pay_now,
    )
    raw = await SocietyService(db).societies.find_by_id(society["id"])
    return await SocietyService(db).enroll(raw, payload, resident, source="form")


async def activate_cash(db, enrollment_id: str, actor_id: str = "manager-test") -> dict:
    service = SocietyService(db)
    return await service.activate(await service.get_enrollment(enrollment_id), method="cash", actor_id=actor_id)


def auth(user_id: str, role: str, center_id: str | None = None) -> dict:
    token = create_access_token(user_id, role, {"service_center_id": center_id, "tv": 0})
    return {"Authorization": f"Bearer {token}"}


async def staff(db, cleanup, center_id: str) -> tuple[str, str]:
    manager_id = await make_manager(db, center_id)
    captain_id = await make_captain(db, center_id)
    cleanup.append(("users", {"_id": ObjectId(manager_id)}))
    cleanup.append(("users", {"_id": ObjectId(captain_id)}))
    cleanup.append(("notifications", {"user_id": manager_id}))
    cleanup.append(("notifications", {"user_id": captain_id}))
    return manager_id, captain_id


def client():
    import httpx

    from app.main import app

    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")
