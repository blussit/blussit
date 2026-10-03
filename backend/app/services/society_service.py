"""
Society plans — see docs/SOCIETY_PLANS.md (the source of truth).

A housing society buys one monthly plan per car: a daily bucket wash done
by the society's captain (tracked as attendance, never as bookings) plus a
small quota of premium washes (Star Wash / Deep Cleaning) booked a day
ahead. The quota IS an ordinary `user_subscriptions` pass per car, so the
existing booking machinery (plan_consumption / commit_consumption /
restore_consumption) spends and refunds it — this module only adds the
society around it: societies, the shareable resident form, enrollments,
activation (online or cash), renewal, captain attendance and reports.
"""
import logging
import secrets
from datetime import date, datetime, timedelta, timezone

from bson import ObjectId
from motor.motor_asyncio import AsyncIOMotorDatabase
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

from app.core.authz import ensure_own_center
from app.core.config import settings
from app.core.exceptions import BadRequestException, ConflictException, ForbiddenException, NotFoundException
from app.models.enums import SubscriptionStatus
from app.repositories.society_repository import (
    SocietyAttendanceRepository,
    SocietyEnrollmentRepository,
    SocietyPaymentRepository,
    SocietyRepository,
)
from app.repositories.subscription_repository import SubscriptionPlanRepository, UserSubscriptionRepository
from app.utils.geo import haversine_km
from app.utils.money import round_rupees, split_whole_rupees
from app.utils.slots import format_slot_12h
from app.utils.text import normalize_plate, slugify
from app.utils.timezone import from_stored, now_ist, to_ist
# Plan month = same day next month; full-month bucket allowance follows it.
from app.services.society_month import plan_month_end, sub_bucket_allowance  # noqa: E402

logger = logging.getLogger(__name__)

PLAN_TYPE = "society"
CYCLE_DAYS = 30
# Renewal opens this long before a cycle ends (and stays open after it).
RENEW_WINDOW = timedelta(days=3)
# A captain's arrival further than this from the society pin is flagged.
ARRIVAL_RADIUS_M = 500
# After this IST hour, a society with no attendance yet alerts its manager.
ATTENDANCE_ALERT_HOUR = 11
OPEN_STATUSES = ("requested", "awaiting_payment")
LIVE_BOOKING_STATUSES = ["awaiting_payment", "pending", "assigned", "captain_on_the_way", "service_started", "rescheduled"]
MAX_ROWS = 500

DEFAULT_RATE_CARD = {
    # ₹ per bucket-wash day (selling / MRP), per car type when set.
    "bucket_day_price": {"default": 38.0, "by_type": {}},
    "bucket_day_mrp": {"default": 52.0, "by_type": {}},
    # Off the premium service's standard per-type price.
    "premium_discount_percent": 0.0,
    "bucket_day_options": [10, 15, 20, 25],
    "premium_count_options": [1, 2, 4],
    # Empty = Star Wash + Deep Cleaning (by slug).
    "premium_service_ids": [],
    "allow_customise": True,
}
DEFAULT_PREMIUM_SLUGS = ("star-wash", "deep-cleaning")


# -- pure helpers --------------------------------------------------------------


def bucket_label(days: int) -> str:
    days = int(days or 0)
    if days >= 25:
        return "Daily wash"
    if days == 15:
        return "Alternate-day wash"
    return f"{days} washes a month"


def bucket_short_label(days: int) -> str:
    """The bucket wash's name on a usage counter ("Daily wash 3/25") —
    never the internal word "bucket"."""
    days = int(days or 0)
    if days >= 25:
        return "Daily wash"
    if days == 15:
        return "Alternate-day wash"
    return "Society wash"


def society_plan_label(society_name: str | None) -> str:
    """How a booking made on a society pass reads next to its service:
    "Star Wash · Society plan (Green Acres)"."""
    return f"Society plan ({society_name})" if society_name else "Society plan"


async def society_names_for_subscriptions(db, subscription_ids) -> dict[str, dict]:
    """subscription_id -> {society_id, society_name} for the society passes
    among these ids (two indexed reads; non-society ids are just absent)."""
    oids = [ObjectId(str(i)) for i in set(subscription_ids or []) if i and ObjectId.is_valid(str(i))]
    if not oids:
        return {}
    subs = await db.user_subscriptions.find(
        {"_id": {"$in": oids}, "society_id": {"$nin": [None, ""]}}, {"society_id": 1}
    ).to_list(length=len(oids))
    if not subs:
        return {}
    sids = {s["society_id"] for s in subs if ObjectId.is_valid(str(s.get("society_id")))}
    rows = await db.societies.find({"_id": {"$in": [ObjectId(s) for s in sids]}}, {"name": 1}).to_list(length=len(sids) or 1)
    names = {str(r["_id"]): r.get("name") for r in rows}
    return {str(s["_id"]): {"society_id": s["society_id"], "society_name": names.get(s["society_id"])} for s in subs}


def combo_name(days: int, count: int, service_name: str) -> str:
    return f"{bucket_label(days)} + {count} {service_name}"


def _rate(table: dict | None, vehicle_type: str) -> float:
    table = table or {}
    by_type = table.get("by_type") or {}
    if vehicle_type in by_type and by_type[vehicle_type] is not None:
        return float(by_type[vehicle_type])
    return float(table.get("default") or 0.0)


def service_offered_for(service: dict, vehicle_type: str) -> bool:
    types = service.get("vehicle_types") or []
    return not types or vehicle_type in types


def custom_price(rate_card: dict, service: dict, vehicle_type: str, days: int, count: int) -> tuple[int, int]:
    """(selling, MRP) for a customised combination, whole rupees."""
    from app.services.subscription_service import service_price_for_type

    unit = service_price_for_type(service, vehicle_type)
    discount = float(rate_card.get("premium_discount_percent") or 0.0)
    day_price = _rate(rate_card.get("bucket_day_price"), vehicle_type)
    day_mrp = _rate(rate_card.get("bucket_day_mrp"), vehicle_type) or day_price
    price = round_rupees(days * day_price + count * unit * (100.0 - discount) / 100.0)
    mrp = round_rupees(days * day_mrp + count * unit)
    return price, max(price, mrp)


def plan_price_for(plan: dict, vehicle_type: str) -> tuple[int, int] | None:
    """(selling, MRP) of a society plan for one car type; None when the plan
    isn't sold for that type. Per-type prices win over the flat ones."""
    allowed = plan.get("vehicle_types") or []
    if allowed and vehicle_type not in allowed:
        return None
    sell = (plan.get("vehicle_type_discounted_prices") or {}).get(vehicle_type)
    if sell is None:
        sell = plan.get("discounted_price") if plan.get("discounted_price") is not None else plan.get("price")
    if sell is None:
        return None
    mrp = (plan.get("vehicle_type_prices") or {}).get(vehicle_type) or plan.get("price") or sell
    sell_r = round_rupees(sell)
    return sell_r, max(sell_r, round_rupees(mrp))


def plan_visible_to(plan: dict, society_id: str, customer_id: str | None) -> bool:
    """Who may see/buy a society plan (docs §1.1)."""
    if plan.get("plan_type") != PLAN_TYPE or not plan.get("is_active") or plan.get("is_deleted"):
        return False
    scope = plan.get("society_scope") or "template"
    society_ids = plan.get("society_ids") or []
    if scope == "template":
        return True
    if scope == "society":
        return society_id in society_ids
    if scope == "customer":
        return bool(customer_id) and plan.get("society_customer_id") == customer_id and (not society_ids or society_id in society_ids)
    return False


def _aware(dt: datetime | None) -> datetime | None:
    """A stored computed instant (naive = UTC) as an aware datetime."""
    if dt is None:
        return None
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt


def _iso(dt: datetime | None) -> str | None:
    return from_stored(dt).isoformat() if dt else None


def today_ist() -> date:
    return now_ist().date()


def bucket_window(sub: dict, on: date) -> tuple[date, date]:
    """The cycle a bucket wash on `on` counts against: [start, end). While
    an early renewal hasn't started yet, that's the previous cycle."""
    cycle_start = from_stored(sub.get("cycle_start") or sub["start_date"]).date()
    end = from_stored(sub["end_date"]).date()
    prev = sub.get("prev_cycle_start")
    if on < cycle_start and prev:
        return from_stored(prev).date(), cycle_start
    return cycle_start, end


def sub_is_live(sub: dict, now: datetime | None = None) -> bool:
    now = now or now_ist()
    end = _aware(sub.get("end_date"))
    return sub.get("status") == SubscriptionStatus.ACTIVE.value and (end is None or end > now)


def online_payment_available() -> bool:
    return bool(settings.RAZORPAY_KEY_ID and settings.RAZORPAY_KEY_SECRET)


async def ensure_society_lead_time(db, subscription_id: str | None, scheduled_date, source: str) -> None:
    """Called by BookingService.create_booking: a customer-channel booking
    that spends a SOCIETY pass must be for tomorrow or later (IST). Staff
    booking on a resident's behalf are exempt."""
    if source not in ("app", "whatsapp") or not subscription_id or not ObjectId.is_valid(str(subscription_id)):
        return
    sub = await db.user_subscriptions.find_one({"_id": ObjectId(subscription_id)}, {"society_id": 1})
    if not sub or not sub.get("society_id"):
        return
    if isinstance(scheduled_date, datetime):
        day = to_ist(scheduled_date).date()
    else:
        day = datetime.strptime(str(scheduled_date)[:10], "%Y-%m-%d").date()
    if day <= today_ist():
        raise BadRequestException(
            "Society plan washes are booked a day ahead — pick tomorrow or later, or switch off your plan to book a paid wash today."
        )


class SocietyService:
    def __init__(self, db: AsyncIOMotorDatabase):
        self.db = db
        self.societies = SocietyRepository(db)
        self.enrollments = SocietyEnrollmentRepository(db)
        self.attendance = SocietyAttendanceRepository(db)
        self.payments = SocietyPaymentRepository(db)
        self.plans = SubscriptionPlanRepository(db)
        self.subs = UserSubscriptionRepository(db)

    # ------------------------------------------------------------------
    # Lookups
    # ------------------------------------------------------------------

    async def _type_names(self) -> dict[str, str]:
        rows = await self.db.vehicle_types.find({}, {"name": 1}).to_list(length=100)
        return {str(r["_id"]): r.get("name", "") for r in rows}

    async def _service(self, service_id: str) -> dict | None:
        if not ObjectId.is_valid(service_id or ""):
            return None
        return await self.db.services.find_one({"_id": ObjectId(service_id), "is_deleted": {"$ne": True}})

    async def _services_by_id(self, ids) -> dict[str, dict]:
        oids = [ObjectId(i) for i in set(ids) if i and ObjectId.is_valid(i)]
        if not oids:
            return {}
        return {str(s["_id"]): s for s in await self.db.services.find({"_id": {"$in": oids}}).to_list(length=len(oids))}

    async def _service_names(self, ids) -> dict[str, str]:
        return {sid: s.get("name") or "" for sid, s in (await self._services_by_id([i for i in ids if i])).items()}

    async def _users_by_id(self, ids) -> dict[str, dict]:
        oids = [ObjectId(i) for i in set(ids) if i and ObjectId.is_valid(str(i))]
        if not oids:
            return {}
        rows = await self.db.users.find({"_id": {"$in": oids}}, {"full_name": 1, "phone": 1, "role": 1, "service_center_id": 1}).to_list(length=len(oids))
        return {str(u["_id"]): u for u in rows}

    async def _center_names(self, ids) -> dict[str, str]:
        oids = [ObjectId(i) for i in set(ids) if i and ObjectId.is_valid(str(i))]
        if not oids:
            return {}
        rows = await self.db.service_centers.find({"_id": {"$in": oids}}, {"name": 1, "location.city": 1}).to_list(length=len(oids))
        return {str(c["_id"]): c.get("name", "") for c in rows}

    async def get_society(self, society_id: str) -> dict:
        society = await self.societies.find_by_id(society_id)
        if not society:
            raise NotFoundException("Society not found")
        return society

    async def society_for_actor(self, society_id: str, actor_role: str, actor_center_id: str | None) -> dict:
        society = await self.get_society(society_id)
        if actor_role not in ("admin", "manager"):
            raise ForbiddenException("You don't have access to this society")
        ensure_own_center(actor_role, actor_center_id, society.get("service_center_id"))
        return society

    async def society_by_token(self, token: str) -> dict:
        """The public form's only key. Unknown, rotated or switched-off links
        all read as 'not found' — nothing confirms a guess."""
        token = (token or "").strip()
        if len(token) < 16 or len(token) > 64:
            raise NotFoundException("This society link isn't valid any more")
        society = await self.societies.find_one({"form_token": token})
        if not society or not society.get("is_active", True) or not society.get("form_enabled", True):
            raise NotFoundException("This society link isn't valid any more")
        return society

    # ------------------------------------------------------------------
    # Rate card + plans
    # ------------------------------------------------------------------

    async def rate_card(self) -> dict:
        doc = await self.db.society_settings.find_one({"_id": "rate_card"}) or {}
        card = {**DEFAULT_RATE_CARD, **{k: v for k, v in doc.items() if k in DEFAULT_RATE_CARD}}
        if not card.get("premium_service_ids"):
            rows = await self.db.services.find(
                {"slug": {"$in": list(DEFAULT_PREMIUM_SLUGS)}, "is_deleted": {"$ne": True}}, {"_id": 1, "slug": 1}
            ).to_list(length=10)
            order = {slug: i for i, slug in enumerate(DEFAULT_PREMIUM_SLUGS)}
            card["premium_service_ids"] = [str(r["_id"]) for r in sorted(rows, key=lambda r: order.get(r.get("slug"), 9))]
        return card

    async def save_rate_card(self, payload, actor_id: str) -> dict:
        data = payload.model_dump()
        services = await self._services_by_id(data["premium_service_ids"])
        for sid in data["premium_service_ids"]:
            svc = services.get(sid)
            if not svc or svc.get("is_addon"):
                raise BadRequestException("Premium services must be real, non add-on services.")
        await self.db.society_settings.update_one(
            {"_id": "rate_card"}, {"$set": {**data, "updated_by": actor_id, "updated_at": now_ist()}}, upsert=True
        )
        return await self.rate_card_view()

    async def rate_card_view(self) -> dict:
        card = await self.rate_card()
        services = await self._services_by_id(card["premium_service_ids"])
        return {
            **{k: card[k] for k in DEFAULT_RATE_CARD},
            "premium_services": [{"id": sid, "name": services[sid].get("name")} for sid in card["premium_service_ids"] if sid in services],
        }

    async def _car_types(self, premium_service_ids: list[str]) -> list[dict]:
        """Car types a society plan can be sold for: active types any premium
        service is offered for (bikes drop out naturally)."""
        services = await self._services_by_id(premium_service_ids)
        types = await self.db.vehicle_types.find({"is_active": True, "is_deleted": {"$ne": True}}).sort("display_order", 1).to_list(length=50)
        out = []
        for t in types:
            tid = str(t["_id"])
            if any(service_offered_for(s, tid) for s in services.values()):
                out.append({"id": tid, "name": t.get("name"), "slug": t.get("slug")})
        return out

    async def plan_view(self, plan: dict, car_types: list[dict], services: dict[str, dict]) -> dict:
        service = services.get(plan.get("society_premium_service_id") or "") or {}
        prices = {}
        for t in car_types:
            if not service_offered_for(service, t["id"]):
                continue
            priced = plan_price_for(plan, t["id"])
            if priced:
                prices[t["id"]] = {"price": priced[0], "mrp": priced[1]}
        return {
            "id": str(plan["_id"]),
            "name": plan.get("name"),
            "description": plan.get("description"),
            "bucket_days": plan.get("society_bucket_days"),
            "bucket_label": bucket_label(plan.get("society_bucket_days") or 0),
            "premium_service_id": plan.get("society_premium_service_id"),
            "premium_service_name": service.get("name"),
            "premium_count": plan.get("society_premium_count"),
            "scope": plan.get("society_scope") or "template",
            "auto_created": bool(plan.get("auto_created")),
            "prices": prices,
        }

    async def plans_for(self, society: dict, customer_id: str | None) -> list[dict]:
        """Plans offered in this society (to this customer, if known)."""
        sid = str(society["_id"])
        query: dict = {
            "plan_type": PLAN_TYPE, "is_active": True, "is_deleted": {"$ne": True},
            "$or": [
                {"society_scope": {"$in": ["template", None]}},
                {"society_scope": "society", "society_ids": sid},
            ],
        }
        if customer_id:
            query["$or"].append({"society_scope": "customer", "society_customer_id": customer_id})
        plans = await self.plans.collection.find(query).sort([("display_order", 1), ("created_at", 1)]).to_list(length=100)
        plans = [p for p in plans if plan_visible_to(p, sid, customer_id)]
        card = await self.rate_card()
        car_types = await self._car_types(card["premium_service_ids"] + [p.get("society_premium_service_id") for p in plans])
        services = await self._services_by_id([p.get("society_premium_service_id") for p in plans])
        views = [await self.plan_view(p, car_types, services) for p in plans]
        return [v for v in views if v["prices"]]

    async def _premium_service_checked(self, service_id: str, allowed_ids: list[str] | None = None) -> dict:
        service = await self._service(service_id)
        if not service or not service.get("is_active", True) or service.get("is_addon"):
            raise BadRequestException("Pick Star Wash or Deep Cleaning as the premium wash.")
        if allowed_ids is not None and service_id not in allowed_ids:
            raise BadRequestException("That premium wash isn't offered for society plans.")
        return service

    async def resolve_choice(self, society: dict, choice, customer_id: str | None, *, create: bool) -> tuple[dict, dict]:
        """(plan, premium service) for a PlanChoice. A customised combination
        finds — or, with create=True, makes — the society's own auto plan
        for it, priced from the rate card at this moment."""
        sid = str(society["_id"])
        if choice.plan_id:
            plan = await self.plans.find_by_id(choice.plan_id) if ObjectId.is_valid(choice.plan_id) else None
            if not plan or not plan_visible_to(plan, sid, customer_id):
                raise NotFoundException("That plan isn't available in this society.")
            service = await self._premium_service_checked(plan.get("society_premium_service_id") or "")
            return plan, service

        combo = choice.custom
        card = await self.rate_card()
        if not card.get("allow_customise"):
            raise BadRequestException("Custom plans aren't offered right now — pick one of the plans.")
        if combo.bucket_days not in card["bucket_day_options"]:
            raise BadRequestException("Pick one of the offered bucket-wash options.")
        if combo.premium_count not in card["premium_count_options"]:
            raise BadRequestException("Pick one of the offered premium-wash counts.")
        service = await self._premium_service_checked(combo.premium_service_id, card["premium_service_ids"])
        match = {
            "plan_type": PLAN_TYPE, "auto_created": True, "is_active": True, "is_deleted": {"$ne": True},
            "society_ids": sid, "society_bucket_days": combo.bucket_days,
            "society_premium_service_id": combo.premium_service_id, "society_premium_count": combo.premium_count,
        }
        existing = await self.plans.collection.find_one(match)
        if existing:
            return existing, service
        car_types = await self._car_types([combo.premium_service_id])
        selling, mrps = {}, {}
        for t in car_types:
            if service_offered_for(service, t["id"]):
                selling[t["id"]], mrps[t["id"]] = custom_price(card, service, t["id"], combo.bucket_days, combo.premium_count)
        if not selling:
            raise BadRequestException("That premium wash isn't offered for cars.")
        draft = self._plan_doc(
            name=combo_name(combo.bucket_days, combo.premium_count, service.get("name") or "premium wash"),
            description="Custom plan for this society.",
            bucket_days=combo.bucket_days, service_id=combo.premium_service_id, count=combo.premium_count,
            price=float(min(selling.values())), mrp=float(min(mrps.values())),
            type_prices=selling, type_mrps=mrps, vehicle_types=list(selling), scope="society",
            society_ids=[sid], customer_id=None, is_active=True, display_order=100, auto_created=True,
        )
        if not create:
            draft["_id"] = None
            return draft, service
        created = await self.plans.create(draft)
        return created, service

    @staticmethod
    def _plan_doc(*, name, description, bucket_days, service_id, count, price, mrp, type_prices, type_mrps, vehicle_types,
                  scope, society_ids, customer_id, is_active, display_order, auto_created, created_by=None) -> dict:
        return {
            "name": name,
            "slug": f"society-{slugify(name)}-{secrets.token_hex(3)}",
            "description": description,
            "billing_cycle": "monthly",
            "plan_type": PLAN_TYPE,
            # MRP / selling, flat and per car type (selling wins — see plan_price_for).
            "price": float(max(mrp or price, price)),
            "discounted_price": float(price),
            "vehicle_type_prices": {k: float(v) for k, v in (type_mrps or {}).items()},
            "vehicle_type_discounted_prices": {k: float(v) for k, v in (type_prices or {}).items()},
            "included_service_ids": [service_id],
            "plan_discount_percent": 0.0,
            "service_pass_prices": {},
            "category_quotas": {},
            "total_service_count": int(count),
            "vehicle_types": list(vehicle_types or []),
            "upgrade_to_plan_ids": [],
            "is_active": bool(is_active),
            "is_popular": False,
            "display_order": int(display_order or 0),
            "society_bucket_days": int(bucket_days),
            "society_premium_service_id": service_id,
            "society_premium_count": int(count),
            "society_scope": scope,
            "society_ids": list(society_ids or []),
            "society_customer_id": customer_id,
            "auto_created": bool(auto_created),
            "created_by": created_by,
        }

    async def create_plan(self, payload, actor_id: str) -> dict:
        service = await self._premium_service_checked(payload.premium_service_id)
        customer_id = None
        if payload.scope == "customer":
            from app.services.auth_service import AuthService

            customer = await AuthService(self.db).ensure_customer_by_phone(payload.customer_phone, "")
            customer_id = str(customer["_id"])
        society_ids = await self._valid_society_ids(payload.society_ids)
        if payload.scope == "society" and not society_ids:
            raise BadRequestException("Pick the society this plan is for.")
        await self._valid_types(list(payload.vehicle_type_prices) + list(payload.vehicle_type_mrps) + payload.vehicle_types)
        doc = self._plan_doc(
            name=payload.name, description=payload.description, bucket_days=payload.bucket_days,
            service_id=str(service["_id"]), count=payload.premium_count, price=payload.price, mrp=payload.mrp,
            type_prices=payload.vehicle_type_prices, type_mrps=payload.vehicle_type_mrps, vehicle_types=payload.vehicle_types,
            scope=payload.scope, society_ids=society_ids, customer_id=customer_id, is_active=payload.is_active,
            display_order=payload.display_order, auto_created=False, created_by=actor_id,
        )
        created = await self.plans.create(doc)
        return (await self.list_plans(plan_ids=[str(created["_id"])]))[0]

    async def update_plan(self, plan_id: str, payload) -> dict:
        plan = await self.plans.find_by_id(plan_id)
        if not plan or plan.get("plan_type") != PLAN_TYPE:
            raise NotFoundException("Society plan not found")
        data = payload.model_dump(exclude_unset=True)
        update: dict = {}
        if "name" in data and data["name"]:
            update["name"] = data["name"]
        if "description" in data:
            update["description"] = data["description"]
        price = data.get("price")
        mrp = data.get("mrp")
        if price is not None:
            update["discounted_price"] = float(price)
        if price is not None or mrp is not None:
            sell = float(price if price is not None else plan.get("discounted_price") or plan.get("price"))
            update["price"] = float(max(mrp or plan.get("price") or sell, sell))
        if data.get("vehicle_type_prices") is not None:
            await self._valid_types(list(data["vehicle_type_prices"]))
            update["vehicle_type_discounted_prices"] = {k: float(v) for k, v in data["vehicle_type_prices"].items() if v}
        if data.get("vehicle_type_mrps") is not None:
            await self._valid_types(list(data["vehicle_type_mrps"]))
            update["vehicle_type_prices"] = {k: float(v) for k, v in data["vehicle_type_mrps"].items() if v}
        if data.get("vehicle_types") is not None:
            await self._valid_types(data["vehicle_types"])
            update["vehicle_types"] = data["vehicle_types"]
        if data.get("society_ids") is not None:
            update["society_ids"] = await self._valid_society_ids(data["society_ids"])
        for key in ("is_active", "display_order"):
            if data.get(key) is not None:
                update[key] = data[key]
        if update:
            await self.plans.update_by_id(plan_id, update)
        return (await self.list_plans(plan_ids=[plan_id]))[0]

    async def _valid_society_ids(self, ids: list[str]) -> list[str]:
        oids = [ObjectId(i) for i in set(ids or []) if ObjectId.is_valid(i)]
        if not oids:
            return []
        rows = await self.db.societies.find({"_id": {"$in": oids}, "is_deleted": {"$ne": True}}, {"_id": 1}).to_list(length=len(oids))
        return [str(r["_id"]) for r in rows]

    async def _valid_types(self, ids: list[str]) -> None:
        names = await self._type_names()
        bad = [i for i in set(ids) if i not in names]
        if bad:
            raise BadRequestException("Unknown vehicle type in the prices.")

    async def list_plans(self, plan_ids: list[str] | None = None) -> list[dict]:
        """Admin: every society plan (templates, society and personal ones)."""
        query: dict = {"plan_type": PLAN_TYPE, "is_deleted": {"$ne": True}}
        if plan_ids:
            query["_id"] = {"$in": [ObjectId(p) for p in plan_ids if ObjectId.is_valid(p)]}
        plans = await self.plans.collection.find(query).sort([("is_active", -1), ("display_order", 1), ("created_at", -1)]).to_list(length=300)
        card = await self.rate_card()
        car_types = await self._car_types(card["premium_service_ids"] + [p.get("society_premium_service_id") for p in plans])
        services = await self._services_by_id([p.get("society_premium_service_id") for p in plans])
        society_names = {}
        all_society_ids = {s for p in plans for s in (p.get("society_ids") or [])}
        if all_society_ids:
            rows = await self.db.societies.find({"_id": {"$in": [ObjectId(s) for s in all_society_ids if ObjectId.is_valid(s)]}}, {"name": 1}).to_list(length=500)
            society_names = {str(r["_id"]): r.get("name") for r in rows}
        customers = await self._users_by_id([p.get("society_customer_id") for p in plans if p.get("society_customer_id")])
        counts = {}
        if plans:
            now = now_ist()
            agg = await self.db.user_subscriptions.aggregate([
                {"$match": {"plan_id": {"$in": [str(p["_id"]) for p in plans]}, "status": "active", "end_date": {"$gt": now}, "is_deleted": {"$ne": True}}},
                {"$group": {"_id": "$plan_id", "n": {"$sum": 1}}},
            ]).to_list(length=None)
            counts = {r["_id"]: r["n"] for r in agg}
        out = []
        for p in plans:
            view = await self.plan_view(p, car_types, services)
            customer = customers.get(p.get("society_customer_id") or "") or {}
            view.update({
                "is_active": bool(p.get("is_active")),
                "display_order": p.get("display_order", 0),
                "flat_price": p.get("discounted_price") if p.get("discounted_price") is not None else p.get("price"),
                "flat_mrp": p.get("price"),
                "vehicle_type_prices": p.get("vehicle_type_discounted_prices") or {},
                "vehicle_type_mrps": p.get("vehicle_type_prices") or {},
                "vehicle_types": p.get("vehicle_types") or [],
                "society_ids": p.get("society_ids") or [],
                "society_names": [society_names.get(s, "—") for s in p.get("society_ids") or []],
                "customer_name": customer.get("full_name"),
                "customer_phone": customer.get("phone"),
                "active_cars": counts.get(str(p["_id"]), 0),
            })
            out.append(view)
        return out

    # ------------------------------------------------------------------
    # Societies (manager / admin)
    # ------------------------------------------------------------------

    async def _resolve_center(self, payload_lat: float, payload_lng: float, pincode: str) -> dict:
        from app.services.booking_service import BookingService

        try:
            center, _ = await BookingService(self.db)._resolve_service_center(
                {"latitude": payload_lat, "longitude": payload_lng, "pincode": pincode}
            )
        except BadRequestException as exc:
            raise BadRequestException("This location isn't in a served area yet — check the pin and pincode.") from exc
        return center

    async def create_society(self, payload, actor_id: str, actor_role: str, actor_center_id: str | None) -> dict:
        if actor_role == "admin" and payload.service_center_id:
            center = await self.db.service_centers.find_one({"_id": ObjectId(payload.service_center_id)}) if ObjectId.is_valid(payload.service_center_id) else None
            if not center:
                raise BadRequestException("Pick a valid service center.")
        else:
            center = await self._resolve_center(payload.latitude, payload.longitude, payload.pincode)
        center_id = str(center["_id"])
        if actor_role == "manager":
            if not actor_center_id:
                raise BadRequestException("Your account isn't linked to a service center yet.")
            if center_id != actor_center_id:
                raise BadRequestException(f"This location is served by {center.get('name') or 'another center'}, not your center.")
        lead_id = getattr(payload, "lead_id", None)
        if lead_id:
            from app.services.society_support_service import SocietyLeadService

            # Checked before anything is written: only a request this actor
            # may see can be marked registered.
            await SocietyLeadService(self.db)._lead_for(lead_id, actor_role, actor_center_id)
        doc = payload.model_dump(exclude={"service_center_id", "lead_id"})
        doc.update({
            "service_center_id": center_id,
            "form_token": secrets.token_urlsafe(18),
            "form_enabled": True,
            "daily_captain_id": None,
            "substitute": None,
            "is_active": True,
            "created_by": actor_id,
            "attendance_alert_date": None,
        })
        created = await self.societies.create(doc)
        detail = await self.society_detail(created)
        if lead_id:
            from app.services.society_support_service import SocietyLeadService

            await SocietyLeadService(self.db).mark_registered(lead_id, detail, actor_id, actor_role, actor_center_id)
        return detail

    # The only fields an edit may blank out. Everything else ignores an
    # explicit null — a null pin used to erase the society's coordinates,
    # which silently switched off the captain's far-from-society check.
    _CLEARABLE_SOCIETY_FIELDS = frozenset({"area", "state", "contact_name", "contact_phone", "notes"})

    async def update_society(self, society: dict, payload) -> dict:
        data = {
            k: v for k, v in payload.model_dump(exclude_unset=True).items()
            if v is not None or k in self._CLEARABLE_SOCIETY_FIELDS
        }
        if "latitude" in data and data.get("latitude") is not None:
            center = await self._resolve_center(data["latitude"], data["longitude"], data.get("pincode") or society.get("pincode"))
            if str(center["_id"]) != society.get("service_center_id"):
                raise BadRequestException("That pin is served by another center — register it there instead.")
        for key in ("name", "address_line", "city", "pincode"):
            if key in data and not data[key]:
                data.pop(key)
        updated = await self.societies.update_by_id(str(society["_id"]), data)
        return await self.society_detail(updated)

    async def rotate_link(self, society: dict) -> dict:
        updated = await self.societies.update_by_id(str(society["_id"]), {"form_token": secrets.token_urlsafe(18), "form_enabled": True})
        return await self.society_detail(updated)

    async def captains_for_center(self, center_id: str) -> list[dict]:
        rows = await self.db.users.find(
            {"role": "captain", "service_center_id": center_id, "is_deleted": {"$ne": True}, "status": {"$ne": "suspended"}},
            {"full_name": 1, "phone": 1, "employee_id": 1},
        ).sort("full_name", 1).to_list(length=200)
        return [{"id": str(r["_id"]), "name": r.get("full_name"), "phone": r.get("phone"), "employee_id": r.get("employee_id")} for r in rows]

    async def set_captain(self, society: dict, captain_id: str | None, on_date: str | None) -> dict:
        captain = None
        if captain_id:
            captain = await self.db.users.find_one({"_id": ObjectId(captain_id)}) if ObjectId.is_valid(captain_id) else None
            if (
                not captain or captain.get("role") != "captain" or captain.get("is_deleted")
                or captain.get("service_center_id") != society.get("service_center_id")
            ):
                raise BadRequestException("Pick a captain from this society's service center.")
        if on_date:
            try:
                day = datetime.strptime(on_date, "%Y-%m-%d").date()
            except ValueError as exc:
                raise BadRequestException("Pick a valid date.") from exc
            if day < today_ist():
                raise BadRequestException("A substitute can only be set for today or later.")
            update = {"substitute": {"captain_id": captain_id, "date": on_date} if captain_id else None}
        else:
            update = {"daily_captain_id": captain_id}
        updated = await self.societies.update_by_id(str(society["_id"]), update)
        if captain:
            from app.services.notification_service import NotificationService

            when = f"on {datetime.strptime(on_date, '%Y-%m-%d').strftime('%d %b')}" if on_date else "every day"
            await NotificationService(self.db).notify(
                captain_id, "Society assigned",
                f"You're the bucket-wash captain for {society.get('name')} {when}. Mark your arrival in the app.",
                send_whatsapp=False,
            )
        return await self.society_detail(updated)

    @staticmethod
    def today_captain_id(society: dict, on: str | None = None) -> str | None:
        on = on or today_ist().isoformat()
        sub = society.get("substitute") or {}
        if sub.get("date") == on and sub.get("captain_id"):
            return sub["captain_id"]
        return society.get("daily_captain_id")

    async def _counts(self, society_ids: list[str]) -> dict[str, dict]:
        if not society_ids:
            return {}
        now = now_ist()
        enroll = await self.db.society_enrollments.aggregate([
            {"$match": {"society_id": {"$in": society_ids}, "is_deleted": {"$ne": True}}},
            {"$group": {
                "_id": "$society_id",
                "residents": {"$sum": {"$cond": [{"$eq": ["$status", "active"]}, 1, 0]}},
                "requests": {"$sum": {"$cond": [{"$in": ["$status", list(OPEN_STATUSES)]}, 1, 0]}},
            }},
        ]).to_list(length=None)
        cars = await self.db.user_subscriptions.aggregate([
            {"$match": {"society_id": {"$in": society_ids}, "status": "active", "end_date": {"$gt": now}, "is_deleted": {"$ne": True}}},
            {"$group": {"_id": "$society_id", "cars": {"$sum": 1}}},
        ]).to_list(length=None)
        from app.services.society_support_service import open_issue_counts

        issues = await open_issue_counts(self.db, society_ids)
        out: dict[str, dict] = {sid: {"residents": 0, "requests": 0, "active_cars": 0, "open_issues": issues.get(sid, 0)} for sid in society_ids}
        for r in enroll:
            out[r["_id"]].update(residents=r["residents"], requests=r["requests"])
        for r in cars:
            out[r["_id"]]["active_cars"] = r["cars"]
        return out

    async def society_detail(self, society: dict) -> dict:
        sid = str(society["_id"])
        counts = (await self._counts([sid]))[sid]
        today = today_ist().isoformat()
        att = await self.db.society_attendance.find_one({"society_id": sid, "date": today})
        captain_ids = [society.get("daily_captain_id"), (society.get("substitute") or {}).get("captain_id"), (att or {}).get("captain_id")]
        users = await self._users_by_id([c for c in captain_ids if c])
        centers = await self._center_names([society.get("service_center_id")])
        sub = society.get("substitute") or None

        def person(uid):
            u = users.get(uid or "")
            return {"id": uid, "name": u.get("full_name"), "phone": u.get("phone")} if u else None

        return {
            "id": sid,
            "name": society.get("name"),
            "address_line": society.get("address_line"),
            "area": society.get("area"),
            "city": society.get("city"),
            "state": society.get("state"),
            "pincode": society.get("pincode"),
            "latitude": society.get("latitude"),
            "longitude": society.get("longitude"),
            "contact_name": society.get("contact_name"),
            "contact_phone": society.get("contact_phone"),
            "notes": society.get("notes"),
            "service_center_id": society.get("service_center_id"),
            "service_center_name": centers.get(society.get("service_center_id") or ""),
            "form_token": society.get("form_token"),
            "form_path": f"/society/{society.get('form_token')}",
            "form_enabled": society.get("form_enabled", True),
            "is_active": society.get("is_active", True),
            "daily_captain": person(society.get("daily_captain_id")),
            "substitute": {"date": sub.get("date"), "captain": person(sub.get("captain_id"))} if sub and sub.get("date") and sub.get("date") >= today else None,
            "today_captain": person(self.today_captain_id(society, today)),
            "today_attendance": {
                "arrived_at": _iso(att.get("arrived_at")),
                "captain": person(att.get("captain_id")),
                "washed_count": len(att.get("washed_vehicle_ids") or []),
                "far_from_society": bool(att.get("far_from_society")),
                "location_missing": bool(att.get("location_missing")),
            } if att else None,
            **counts,
            "created_at": _iso(society.get("created_at")),
        }

    async def list_societies(self, *, center_id: str | None, search: str | None = None, month: str | None = None) -> dict:
        """Manager (their center) or admin (all / one center): KPIs + rows."""
        query: dict = {"is_deleted": {"$ne": True}}
        if center_id:
            query["service_center_id"] = center_id
        text = (search or "").strip()
        if text:
            import re

            pattern = {"$regex": re.escape(text), "$options": "i"}
            query["$or"] = [{"name": pattern}, {"area": pattern}, {"city": pattern}, {"pincode": pattern}]
        societies = await self.societies.collection.find(query).sort("created_at", -1).to_list(length=MAX_ROWS)
        ids = [str(s["_id"]) for s in societies]
        counts = await self._counts(ids)
        start, end = _month_bounds(month)
        revenue_rows = await self.db.society_payments.aggregate([
            {"$match": {"society_id": {"$in": ids}, "created_at": {"$gte": start, "$lt": end}}},
            {"$group": {"_id": "$society_id", "amount": {"$sum": "$amount"}}},
        ]).to_list(length=None) if ids else []
        revenue = {r["_id"]: r["amount"] for r in revenue_rows}
        att_rows = await self.db.society_attendance.aggregate([
            {"$match": {"society_id": {"$in": ids}, "date": {"$gte": start.date().isoformat(), "$lt": end.date().isoformat()}}},
            {"$group": {"_id": "$society_id", "days": {"$sum": 1}}},
        ]).to_list(length=None) if ids else []
        att_days = {r["_id"]: r["days"] for r in att_rows}
        today = today_ist().isoformat()
        today_att = {
            a["society_id"] for a in await self.db.society_attendance.find({"society_id": {"$in": ids}, "date": today}, {"society_id": 1}).to_list(length=len(ids) or 1)
        } if ids else set()
        centers = await self._center_names([s.get("service_center_id") for s in societies])
        captains = await self._users_by_id([self.today_captain_id(s, today) for s in societies if self.today_captain_id(s, today)])
        rows = []
        for s in societies:
            sid = str(s["_id"])
            cap = captains.get(self.today_captain_id(s, today) or "") or {}
            rows.append({
                "id": sid,
                "name": s.get("name"),
                "area": s.get("area"),
                "city": s.get("city"),
                "pincode": s.get("pincode"),
                "service_center_id": s.get("service_center_id"),
                "service_center_name": centers.get(s.get("service_center_id") or ""),
                "today_captain_name": cap.get("full_name"),
                "attended_today": sid in today_att,
                "revenue_month": round_rupees(revenue.get(sid, 0)),
                "attendance_days_month": att_days.get(sid, 0),
                "form_enabled": s.get("form_enabled", True),
                "is_active": s.get("is_active", True),
                **counts.get(sid, {}),
            })
        kpis = {
            "societies": len(rows),
            "residents": sum(r["residents"] for r in rows),
            "active_cars": sum(r["active_cars"] for r in rows),
            "requests": sum(r["requests"] for r in rows),
            "open_issues": sum(r.get("open_issues", 0) for r in rows),
            "revenue_month": sum(r["revenue_month"] for r in rows),
            "attended_today": sum(1 for r in rows if r["attended_today"]),
        }
        return {"kpis": kpis, "rows": rows, "month": start.strftime("%Y-%m")}

    # ------------------------------------------------------------------
    # Public form + quoting
    # ------------------------------------------------------------------

    async def public_form(self, society: dict, customer_id: str | None) -> dict:
        card = await self.rate_card()
        services = await self._services_by_id(card["premium_service_ids"])
        return {
            "society": {"name": society.get("name"), "area": society.get("area"), "city": society.get("city")},
            "plans": await self.plans_for(society, customer_id),
            "customise": {
                "enabled": bool(card.get("allow_customise")),
                "bucket_day_options": [{"days": d, "label": bucket_label(d)} for d in card["bucket_day_options"]],
                "premium_count_options": card["premium_count_options"],
                "premium_services": [{"id": sid, "name": services[sid].get("name")} for sid in card["premium_service_ids"] if sid in services],
            },
            "vehicle_types": await self._car_types(card["premium_service_ids"]),
            "online_payment": online_payment_available(),
            "lead_days": 1,
        }

    async def quote(self, society: dict, choice, vehicle_types: list[str], customer_id: str | None) -> dict:
        plan, service = await self.resolve_choice(society, choice, customer_id, create=False)
        names = await self._type_names()
        cars = []
        for vt in vehicle_types:
            cars.append({"vehicle_type": vt, "vehicle_type_name": names.get(vt), **self._car_price(plan, service, vt, names)})
        total = sum(c["price"] for c in cars)
        code = getattr(choice, "coupon_code", None)
        coupon = await self.coupon_preview(code, total, customer_id) if code else None
        discount = coupon["discount"] if coupon and coupon["valid"] else 0
        return {
            "plan_id": str(plan["_id"]) if plan.get("_id") else None,
            "plan_name": plan.get("name"),
            "bucket_label": bucket_label(plan.get("society_bucket_days") or 0),
            "premium_service_name": service.get("name"),
            "premium_count": plan.get("society_premium_count"),
            "cars": cars,
            "total": total,
            "mrp_total": sum(c["mrp"] for c in cars),
            "coupon": coupon,
            "discount": discount,
            "payable_total": total - discount,
        }

    # ------------------------------------------------------------------
    # Coupons — CouponService's rules on the society total, whole rupees.
    # Counted once when the plan activates (or a renewal goes through),
    # never on the request; given back if a counted request is withdrawn.
    # ------------------------------------------------------------------

    async def coupon_discount(self, code: str, amount: int, customer_id: str | None) -> dict:
        """{"code", "coupon_id", "discount"} for an existing coupon on this
        amount: active, inside its dates, under its total and per-resident
        limits (when the resident is known) and over its minimum order —
        exactly the checks a booking runs. Raises BadRequestException with
        the reason otherwise."""
        from app.services.coupon_service import CouponService

        coupons = CouponService(self.db)
        coupon = await coupons._valid_coupon(code, float(amount), customer_id or None)
        if coupon.get("offer_kind", "standard") != "standard":
            raise BadRequestException("This offer works on bookings only, not on society plans.")
        discount = min(round_rupees(coupons._standard_discount(coupon, float(amount))), int(amount))
        return {"code": coupon["code"], "coupon_id": str(coupon["_id"]), "discount": int(discount)}

    async def coupon_preview(self, code: str | None, amount: int, customer_id: str | None) -> dict | None:
        """coupon_discount for a price summary — never raises."""
        if not code:
            return None
        try:
            c = await self.coupon_discount(code, amount, customer_id)
            return {"code": c["code"], "valid": True, "discount": c["discount"], "error": None}
        except BadRequestException as exc:
            return {"code": code, "valid": False, "discount": 0, "error": exc.message}

    async def _record_coupon(self, code: str, customer_id: str, ref: str) -> bool:
        """Counts one use (atomic total-limit guard in record_usage). The
        money has already been taken at the discounted price, so a refusal
        here is logged, never raised."""
        from app.services.coupon_service import CouponService

        try:
            coupons = CouponService(self.db)
            coupon = await coupons.repo.find_by_code(code)
            if not coupon:
                return False
            await coupons.record_usage(str(coupon["_id"]), customer_id, ref)
            return True
        except Exception:  # noqa: BLE001
            logger.exception("Could not count coupon %s for %s", code, ref)
            return False

    @staticmethod
    def _pending_total(enrollment: dict) -> int:
        return sum(int(c.get("price") or 0) for c in enrollment.get("cars") or [] if c.get("status") == "pending")

    @classmethod
    def _request_discount(cls, enrollment: dict) -> int:
        """The coupon discount on an open request, capped at what's due."""
        if not enrollment.get("coupon_code"):
            return 0
        return min(int(enrollment.get("discount_amount") or 0), cls._pending_total(enrollment))

    async def enrollment_coupon_preview(self, enrollment: dict, code: str | None, renewal: bool) -> dict:
        """Price summary for 'Mark paid' / 'Renew' with a coupon: subtotal,
        discount, payable. Without a code, an open request shows the
        resident's own coupon (if any)."""
        if renewal:
            subs = await self._renewable_subs(enrollment)
            prices = {c.get("subscription_id"): int(c.get("price") or 0) for c in enrollment.get("cars") or []}
            subtotal = sum(prices.get(str(s["_id"]), 0) for s in subs)
        else:
            subtotal = self._pending_total(enrollment)
            code = code or enrollment.get("coupon_code")
        coupon = await self.coupon_preview(code, subtotal, enrollment.get("customer_id"))
        discount = coupon["discount"] if coupon and coupon["valid"] else 0
        return {"subtotal": subtotal, "discount": discount, "payable": subtotal - discount, "coupon": coupon}

    @staticmethod
    def _car_price(plan: dict, service: dict, vehicle_type: str, names: dict[str, str]) -> dict:
        if vehicle_type not in names:
            raise BadRequestException("Pick a valid car type.")
        priced = plan_price_for(plan, vehicle_type) if service_offered_for(service, vehicle_type) else None
        if not priced:
            raise BadRequestException(f"{plan.get('name')} isn't offered for {names.get(vehicle_type) or 'that car type'}.")
        return {"price": priced[0], "mrp": priced[1]}

    # ------------------------------------------------------------------
    # Enrollment
    # ------------------------------------------------------------------

    async def _ensure_vehicle(self, customer_id: str, vehicle_type: str, plate: str, type_name: str) -> dict:
        from app.schemas.profile_schema import VehicleCreateRequest
        from app.services.profile_service import VehicleService

        normalized = normalize_plate(plate)
        existing = await self.db.vehicles.find_one(
            {"owner_id": customer_id, "registration_number_normalized": normalized, "is_deleted": {"$ne": True}}
        )
        if existing:
            if existing.get("vehicle_type") != vehicle_type:
                # Refuse BEFORE retyping the saved car: a car already on a
                # live pass must keep the type that pass was sold for.
                if await self._live_pass_on(customer_id, str(existing["_id"])):
                    raise BadRequestException(f"{plate} already has an active plan.")
                await self.db.vehicles.update_one({"_id": existing["_id"]}, {"$set": {"vehicle_type": vehicle_type, "updated_at": now_ist()}})
                existing["vehicle_type"] = vehicle_type
            return existing
        created = await VehicleService(self.db).create(
            customer_id,
            VehicleCreateRequest(vehicle_type=vehicle_type, brand=type_name or "Car", model="", registration_number=plate, acknowledge_shared_registration=True),
        )
        return await self.db.vehicles.find_one({"_id": ObjectId(created["id"])})

    async def _ensure_address(self, customer_id: str, society: dict, flat: str) -> str:
        sid = str(society["_id"])
        fields = {
            "label": "Society",
            "line1": f"{flat}, {society.get('name')}"[:300],
            "line2": society.get("address_line"),
            "landmark": society.get("area"),
            "city": society.get("city") or "Indore",
            "state": society.get("state") or "Madhya Pradesh",
            "pincode": society.get("pincode"),
            "latitude": society.get("latitude"),
            "longitude": society.get("longitude"),
        }
        existing = await self.db.addresses.find_one({"owner_id": customer_id, "society_id": sid, "is_deleted": {"$ne": True}})
        if existing:
            await self.db.addresses.update_one({"_id": existing["_id"]}, {"$set": {**fields, "updated_at": now_ist()}})
            return str(existing["_id"])
        has_any = await self.db.addresses.count_documents({"owner_id": customer_id, "is_deleted": {"$ne": True}}, limit=1)
        now = datetime.now(timezone.utc)
        result = await self.db.addresses.insert_one({
            **fields, "owner_id": customer_id, "society_id": sid, "is_default": not has_any,
            "is_deleted": False, "created_at": now, "updated_at": now,
        })
        return str(result.inserted_id)

    async def _live_pass_on(self, customer_id: str, vehicle_id: str) -> dict | None:
        now = now_ist()
        subs = await self.db.user_subscriptions.find(
            {"customer_id": customer_id, "vehicle_id": vehicle_id, "status": "active", "is_deleted": {"$ne": True}}
        ).to_list(length=20)
        return next((s for s in subs if (_aware(s.get("end_date")) or now) > now), None)

    async def enroll(self, society: dict, payload, customer: dict, *, source: str, actor_id: str | None = None) -> dict:
        customer_id = str(customer["_id"])
        sid = str(society["_id"])
        if not society.get("is_active", True):
            raise BadRequestException("This society isn't taking new residents right now.")
        plan, service = await self.resolve_choice(society, payload, customer_id, create=True)
        names = await self._type_names()
        # A coupon is checked now (the resident is known: per-resident limit
        # too) and frozen on the request; it is COUNTED only on activation.
        coupon = {"coupon_code": None, "coupon_id": None, "discount_amount": 0}
        code = getattr(payload, "coupon_code", None)
        if code:
            subtotal = sum(self._car_price(plan, service, car.vehicle_type, names)["price"] for car in payload.cars)
            c = await self.coupon_discount(code, subtotal, customer_id)
            coupon = {"coupon_code": c["code"], "coupon_id": c["coupon_id"], "discount_amount": c["discount"]}
        cars = []
        for car in payload.cars:
            priced = self._car_price(plan, service, car.vehicle_type, names)
            vehicle = await self._ensure_vehicle(customer_id, car.vehicle_type, car.registration_number, names.get(car.vehicle_type, ""))
            vid = str(vehicle["_id"])
            if await self._live_pass_on(customer_id, vid):
                raise BadRequestException(f"{car.registration_number} already has an active plan.")
            cars.append({
                "vehicle_id": vid, "vehicle_type": car.vehicle_type, "registration_number": car.registration_number,
                "price": priced["price"], "mrp": priced["mrp"], "subscription_id": None, "status": "pending",
            })
        address_id = await self._ensure_address(customer_id, society, payload.flat)
        status = "awaiting_payment" if payload.pay_now and online_payment_available() else "requested"
        fields = {
            **coupon,
            "society_id": sid,
            "service_center_id": society.get("service_center_id"),
            "customer_id": customer_id,
            "resident_name": payload.resident_name,
            "phone": customer.get("phone") or payload.phone,
            "flat": payload.flat,
            "address_id": address_id,
            "plan_id": str(plan["_id"]),
            "plan_name": plan.get("name"),
            "bucket_days": plan.get("society_bucket_days"),
            "premium_service_id": plan.get("society_premium_service_id"),
            "premium_count": plan.get("society_premium_count"),
            "source": source,
            "status": status,
            "cars": cars,
            "total_amount": sum(c["price"] for c in cars),
            "mrp_total": sum(c["mrp"] for c in cars),
            "updated_by": actor_id or customer_id,
        }
        # One open request per resident per society: a resubmit replaces it.
        open_one = await self.enrollments.collection.find_one_and_update(
            {"society_id": sid, "customer_id": customer_id, "status": {"$in": list(OPEN_STATUSES)}, "is_deleted": {"$ne": True}},
            {"$set": {**fields, "updated_at": now_ist()}, "$inc": {"revision": 1}},
            return_document=ReturnDocument.AFTER,
        )
        if open_one:
            enrollment = open_one
        else:
            try:
                enrollment = await self.enrollments.create({**fields, "revision": 1, "created_by": actor_id or customer_id})
            except DuplicateKeyError:
                # A simultaneous submit created the open request first —
                # merge into it, exactly as a later resubmit would.
                enrollment = await self.enrollments.collection.find_one_and_update(
                    {"society_id": sid, "customer_id": customer_id, "status": {"$in": list(OPEN_STATUSES)}, "is_deleted": {"$ne": True}},
                    {"$set": {**fields, "updated_at": now_ist()}, "$inc": {"revision": 1}},
                    return_document=ReturnDocument.AFTER,
                )
                if not enrollment:
                    raise
                return await self.enrollment_view(enrollment)
            await self._tell_managers(
                society, "New society request",
                f"{payload.resident_name} ({payload.flat}) — {len(cars)} car{'s' if len(cars) != 1 else ''} on {plan.get('name')} at {society.get('name')}.",
            )
        return await self.enrollment_view(enrollment)

    async def _tell_managers(self, society: dict, title: str, message: str) -> None:
        from app.services.notification_service import NotificationService

        try:
            managers = await self.db.users.find(
                {"role": "manager", "service_center_id": society.get("service_center_id"), "is_deleted": {"$ne": True}}, {"_id": 1}
            ).to_list(length=10)
            notifications = NotificationService(self.db)
            for m in managers:
                await notifications.notify(str(m["_id"]), title, message, send_whatsapp=False)
        except Exception:  # noqa: BLE001 — an alert must never fail the action
            logger.exception("Could not alert managers for society %s", society.get("_id"))

    async def enrollment_view(
        self, enrollment: dict, subs: dict[str, dict] | None = None, bucket_used: dict[str, int] | None = None,
        names: dict[str, str] | None = None, service_names: dict[str, str] | None = None,
    ) -> dict:
        names = names if names is not None else await self._type_names()
        subs = subs if subs is not None else await self._subs_by_id([c.get("subscription_id") for c in enrollment.get("cars") or []])
        if service_names is None:
            service_names = await self._service_names([enrollment.get("premium_service_id")])
        now = now_ist()
        cars = []
        renew_open = False
        for c in enrollment.get("cars") or []:
            sub = subs.get(c.get("subscription_id") or "")
            sub_view = None
            if sub:
                end = _aware(sub.get("end_date"))
                live = sub_is_live(sub, now)
                can_renew = c.get("status") == "active" and sub.get("status") != "cancelled" and end is not None and end - now <= RENEW_WINDOW
                renew_open = renew_open or can_renew
                sub_view = {
                    "id": str(sub["_id"]),
                    "status": "active" if live else ("cancelled" if sub.get("status") == "cancelled" else "expired"),
                    "remaining": int(sub.get("remaining_service_count") or 0),
                    "total": int(sub.get("total_service_count") or 0),
                    "end_date": _iso(sub.get("end_date")),
                    "cycle_start": _iso(sub.get("cycle_start") or sub.get("start_date")),
                    "can_renew": can_renew,
                }
            cars.append({
                "vehicle_id": c.get("vehicle_id"),
                "vehicle_type": c.get("vehicle_type"),
                "vehicle_type_name": names.get(c.get("vehicle_type") or ""),
                "registration_number": c.get("registration_number"),
                "price": c.get("price"),
                "mrp": c.get("mrp"),
                "status": c.get("status"),
                "note": c.get("note"),
                "subscription": sub_view,
                "bucket_used": (bucket_used or {}).get(c.get("vehicle_id") or "", None),
                "bucket_allowance": sub_bucket_allowance(sub, today_ist()) if sub else enrollment.get("bucket_days"),
            })
        renew_cars = [c for c in cars if c["subscription"] and c["subscription"]["can_renew"]]
        pending_total = sum(int(c.get("price") or 0) for c in enrollment.get("cars") or [] if c.get("status") == "pending")
        base_total = pending_total or int(enrollment.get("total_amount") or 0)
        discount = min(int(enrollment.get("discount_amount") or 0), base_total) if enrollment.get("coupon_code") else 0
        return {
            "id": str(enrollment["_id"]),
            "society_id": enrollment.get("society_id"),
            "customer_id": enrollment.get("customer_id"),
            "resident_name": enrollment.get("resident_name"),
            "phone": enrollment.get("phone"),
            "flat": enrollment.get("flat"),
            "plan_id": enrollment.get("plan_id"),
            "plan_name": enrollment.get("plan_name"),
            "bucket_days": enrollment.get("bucket_days"),
            "bucket_label": bucket_label(enrollment.get("bucket_days") or 0),
            "bucket_short_label": bucket_short_label(enrollment.get("bucket_days") or 0),
            "premium_service_id": enrollment.get("premium_service_id"),
            "premium_service_name": service_names.get(enrollment.get("premium_service_id") or "") or "Premium wash",
            "premium_count": enrollment.get("premium_count"),
            "source": enrollment.get("source"),
            "status": enrollment.get("status"),
            # Which version of the request this view shows — "Mark paid"
            # sends it back, so a resubmit in between can't be activated
            # (and booked as cash) unseen.
            "revision": int(enrollment.get("revision") or 1),
            "cars": cars,
            "total_amount": enrollment.get("total_amount"),
            "mrp_total": enrollment.get("mrp_total"),
            # Coupon on the request (applied on activation; whole rupees).
            "coupon_code": enrollment.get("coupon_code"),
            "discount_amount": discount,
            "payable_amount": max(0, base_total - discount),
            "payment": {
                "method": (enrollment.get("payment") or {}).get("method"),
                "amount": (enrollment.get("payment") or {}).get("amount"),
                "discount": (enrollment.get("payment") or {}).get("discount") or 0,
                "coupon_code": (enrollment.get("payment") or {}).get("coupon_code"),
                "at": _iso((enrollment.get("payment") or {}).get("at")),
            } if enrollment.get("payment") else None,
            "renew_open": renew_open,
            "renew_amount": sum(int(c["price"] or 0) for c in renew_cars),
            "created_at": _iso(enrollment.get("created_at")),
            "activated_at": _iso(enrollment.get("activated_at")),
            "cancelled_at": _iso(enrollment.get("cancelled_at")),
        }

    async def _subs_by_id(self, ids) -> dict[str, dict]:
        oids = [ObjectId(i) for i in set(ids) if i and ObjectId.is_valid(str(i))]
        if not oids:
            return {}
        rows = await self.db.user_subscriptions.find({"_id": {"$in": oids}}).to_list(length=len(oids))
        return {str(s["_id"]): s for s in rows}

    async def _bucket_usage(self, society_id: str, subs: list[dict], exclude_today: bool = False) -> dict[str, int]:
        """vehicle_id -> bucket washes in that car's current window. One
        indexed read of the society's last ~2 cycles of attendance."""
        if not subs:
            return {}
        today = today_ist()
        since = (today - timedelta(days=CYCLE_DAYS * 2 + 5)).isoformat()
        rows = await self.db.society_attendance.find(
            {"society_id": society_id, "date": {"$gte": since}}, {"date": 1, "washed_vehicle_ids": 1}
        ).to_list(length=CYCLE_DAYS * 2 + 10)
        out: dict[str, int] = {}
        for sub in subs:
            vid = sub.get("vehicle_id")
            if not vid:
                continue
            start, end = bucket_window(sub, today)
            used = 0
            for r in rows:
                d = date.fromisoformat(r["date"])
                if exclude_today and d == today:
                    continue
                if start <= d < end and vid in (r.get("washed_vehicle_ids") or []):
                    used += 1
            out[vid] = used
        return out

    async def enrollments_for_society(self, society: dict, status: str | None = None) -> list[dict]:
        sid = str(society["_id"])
        query: dict = {"society_id": sid, "is_deleted": {"$ne": True}}
        if status == "open":
            query["status"] = {"$in": list(OPEN_STATUSES)}
        elif status:
            query["status"] = status
        rows = await self.enrollments.collection.find(query).sort("created_at", -1).to_list(length=MAX_ROWS)
        subs = await self._subs_by_id([c.get("subscription_id") for e in rows for c in e.get("cars") or []])
        usage = await self._bucket_usage(sid, [s for s in subs.values() if s.get("status") == "active"])
        names = await self._type_names()
        service_names = await self._service_names([e.get("premium_service_id") for e in rows])
        return [await self.enrollment_view(e, subs, usage, names, service_names) for e in rows]

    async def get_enrollment(self, enrollment_id: str) -> dict:
        enrollment = await self.enrollments.find_by_id(enrollment_id) if ObjectId.is_valid(enrollment_id or "") else None
        if not enrollment:
            raise NotFoundException("Enrollment not found")
        return enrollment

    async def enrollment_for_actor(self, enrollment_id: str, actor_role: str, actor_center_id: str | None) -> dict:
        enrollment = await self.get_enrollment(enrollment_id)
        if actor_role not in ("admin", "manager"):
            raise ForbiddenException("You don't have access to this enrollment")
        ensure_own_center(actor_role, actor_center_id, enrollment.get("service_center_id"))
        return enrollment

    async def my_hub(self, society: dict, customer_id: str) -> dict:
        sid = str(society["_id"])
        rows = await self.enrollments.collection.find(
            {"society_id": sid, "customer_id": customer_id, "is_deleted": {"$ne": True}}
        ).sort("created_at", -1).to_list(length=20)
        subs = await self._subs_by_id([c.get("subscription_id") for e in rows for c in e.get("cars") or []])
        usage = await self._bucket_usage(sid, [s for s in subs.values() if s.get("status") == "active"])
        names = await self._type_names()
        service_names = await self._service_names([e.get("premium_service_id") for e in rows])
        views = [await self.enrollment_view(e, subs, usage, names, service_names) for e in rows]
        live_sub_ids = [k for k, s in subs.items() if sub_is_live(s)]
        upcoming = []
        if live_sub_ids:
            bookings = await self.db.bookings.find(
                {"subscription_id": {"$in": live_sub_ids}, "status": {"$in": LIVE_BOOKING_STATUSES}, "is_deleted": {"$ne": True}}
            ).sort("scheduled_date", 1).to_list(length=20)
            plate_of = {k: s.get("vehicle_id") for k, s in subs.items()}
            plates = {c.get("vehicle_id"): c.get("registration_number") for e in rows for c in e.get("cars") or []}
            for b in bookings:
                sub = subs.get(b.get("subscription_id") or "") or {}
                upcoming.append({
                    "id": str(b["_id"]),
                    "booking_number": b.get("booking_number"),
                    "service_name": service_names.get(sub.get("service_id") or "") or None,
                    "plan_label": society_plan_label(society.get("name")),
                    "scheduled_date": b["scheduled_date"].strftime("%Y-%m-%d") if isinstance(b.get("scheduled_date"), datetime) else b.get("scheduled_date"),
                    "scheduled_slot": b.get("scheduled_slot"),
                    "slot_label": format_slot_12h(b.get("scheduled_slot")),
                    "status": b.get("status"),
                    "registration_number": plates.get(plate_of.get(b.get("subscription_id") or "")),
                    "vehicle_type_name": names.get(sub.get("vehicle_type") or "") or None,
                })
        return {
            "society": {"id": sid, "name": society.get("name"), "area": society.get("area"), "city": society.get("city")},
            "service_center_id": society.get("service_center_id"),
            "enrollments": [v for v in views if v["status"] != "cancelled"],
            "upcoming": upcoming,
            "online_payment": online_payment_available(),
            "lead_days": 1,
        }

    # ------------------------------------------------------------------
    # Activation, renewal, cancellation
    # ------------------------------------------------------------------

    async def activate(
        self, enrollment: dict, *, method: str, actor_id: str | None, order: dict | None = None, note: str | None = None,
        expected_revision: int | None = None, coupon_code: str | None = None, remove_coupon: bool = False,
    ) -> dict:
        """Requested/awaiting-payment -> active: one pass per car. Claimed
        atomically, so a double tap or a webhook racing the verify can only
        activate once. A car that meanwhile got another live pass is
        skipped (and reported) — never double-passed. A coupon on the
        request (or applied now by staff) takes its whole-rupee discount off
        and is counted once, here."""
        eid = str(enrollment["_id"])
        pending = [c for c in enrollment.get("cars") or [] if c.get("status") == "pending"]
        if order is None and pending:
            clashes = [c for c in pending if await self._live_pass_on(enrollment["customer_id"], c["vehicle_id"])]
            if len(clashes) == len(pending):
                raise BadRequestException("Every car here already has another active plan — nothing to activate.")
        if order is None:
            enrollment = await self._apply_staff_coupon(enrollment, coupon_code, remove_coupon, expected_revision)
        claim_filter: dict = {"_id": enrollment["_id"], "status": {"$in": list(OPEN_STATUSES)}, "is_deleted": {"$ne": True}}
        if expected_revision is not None:
            # Exactly the request that was reviewed / paid for — a resident
            # resubmitting (more cars, a dearer plan) in between must not
            # ride along on that approval or that payment.
            claim_filter["$or"] = self._revision_match(expected_revision)
        claimed = await self.enrollments.collection.find_one_and_update(
            claim_filter,
            {"$set": {"status": "active", "activated_at": now_ist(), "updated_at": now_ist()}},
            return_document=ReturnDocument.AFTER,
        )
        if not claimed:
            fresh = await self.get_enrollment(eid)
            if fresh.get("status") == "active":
                raise BadRequestException("This enrollment is already active.")
            if fresh.get("status") in OPEN_STATUSES:
                raise ConflictException("The resident changed this request since you opened it — review it again before marking it paid.")
            raise BadRequestException("This enrollment was cancelled.")
        plan = await self.plans.find_by_id(claimed["plan_id"]) or {}
        society = await self.societies.find_by_id(claimed["society_id"]) or {}
        now = now_ist()
        end = plan_month_end(now)
        count = int(claimed.get("premium_count") or plan.get("society_premium_count") or 0)
        pending_now = [c for c in claimed.get("cars") or [] if c.get("status") == "pending"]
        activatable = [c for c in pending_now if not await self._live_pass_on(claimed["customer_id"], c["vehicle_id"])]
        share_of = self._discount_shares(claimed, pending_now, activatable)
        cars = []
        created_ids = []
        skipped = []
        for car in claimed.get("cars") or []:
            if car.get("status") != "pending":
                cars.append(car)
                continue
            if await self._live_pass_on(claimed["customer_id"], car["vehicle_id"]):
                cars.append({**car, "status": "skipped", "note": "Car already had another active plan"})
                skipped.append(car["registration_number"])
                continue
            share = int(share_of.get(car["vehicle_id"], 0))
            sub = await self.subs.create({
                "customer_id": claimed["customer_id"],
                "plan_id": claimed["plan_id"],
                "service_center_id": claimed.get("service_center_id"),
                "vehicle_id": car["vehicle_id"],
                "service_id": claimed.get("premium_service_id"),
                "vehicle_type": car["vehicle_type"],
                "purchased_price": float(car["price"]),
                "amount_paid": float(int(car["price"]) - share),
                "coupon_code": claimed.get("coupon_code") if share else None,
                "payment_method": method,
                "status": SubscriptionStatus.ACTIVE.value,
                "total_service_count": count,
                "remaining_service_count": count,
                "total_by_category": {},
                "remaining_by_category": {},
                "start_date": now,
                "end_date": end,
                "auto_renew": False,
                "razorpay_subscription_id": None,
                "renewal_count": 0,
                "plan_kind": PLAN_TYPE,
                "society_id": claimed["society_id"],
                "enrollment_id": eid,
                "bucket_days": int(claimed.get("bucket_days") or 0),
                "cycle_start": now,
                "prev_cycle_start": None,
                "granted_by": actor_id,
            })
            created_ids.append(str(sub["_id"]))
            cars.append({**car, "status": "active", "subscription_id": str(sub["_id"]), "discount": share})
        activated_cars = [c for c in cars if c.get("subscription_id") in created_ids]
        gross = sum(int(c["price"]) for c in activated_cars)
        discount = sum(int(c.get("discount") or 0) for c in activated_cars)
        amount = gross - discount
        coupon = claimed.get("coupon_code") if discount else None
        update: dict = {
            "cars": cars,
            "payment": {
                "method": method, "amount": amount, "gross": gross, "discount": discount, "coupon_code": coupon,
                "collected_by": actor_id if method == "cash" else None,
                "order_id": str(order["_id"]) if order else None, "at": now, "note": note,
            },
        }
        if coupon and await self._record_coupon(coupon, claimed["customer_id"], f"society:{eid}"):
            update["coupon_usage_recorded"] = True
        if not created_ids:
            # Nothing could be activated (every car got another plan in the
            # meantime). An online payment for it is parked by the caller.
            update["status"] = "awaiting_payment" if order else "requested"
        await self.enrollments.update_by_id(eid, update)
        if created_ids:
            await self.payments.create({
                "society_id": claimed["society_id"], "enrollment_id": eid, "customer_id": claimed["customer_id"],
                "service_center_id": claimed.get("service_center_id"), "amount": amount, "method": method,
                "gross_amount": gross, "discount_amount": discount, "coupon_code": coupon,
                "kind": "activation", "car_count": len(created_ids), "subscription_ids": created_ids,
                "collected_by": actor_id if method == "cash" else None,
                "razorpay_order_id": (order or {}).get("razorpay_order_id"),
            })
            await self._tell_resident(
                claimed["customer_id"], "Society plan active",
                f"Your {claimed.get('plan_name')} at {society.get('name')} is active for "
                f"{', '.join(c['registration_number'] for c in cars if c.get('subscription_id') in created_ids)}. "
                f"Valid till {end.strftime('%d %b %Y')}.",
            )
        fresh = await self.get_enrollment(eid)
        return {"enrollment": await self.enrollment_view(fresh), "activated": len(created_ids), "skipped": skipped}

    @staticmethod
    def _revision_match(expected_revision: int) -> list[dict]:
        return [{"revision": int(expected_revision)}] + ([{"revision": {"$exists": False}}] if int(expected_revision) == 1 else [])

    async def _apply_staff_coupon(self, enrollment: dict, code: str | None, remove: bool, expected_revision: int | None) -> dict:
        """Mark paid (cash): the coupon staff applied now, or the resident's
        own re-checked (it may have expired or run out since the request),
        or none (remove). Written on the open request before it's claimed,
        so the claim activates exactly what was shown."""
        current = enrollment.get("coupon_code")
        if remove:
            fields = {"coupon_code": None, "coupon_id": None, "discount_amount": 0}
        elif code or current:
            use = code or current
            try:
                c = await self.coupon_discount(use, self._pending_total(enrollment), enrollment.get("customer_id"))
            except BadRequestException as exc:
                raise BadRequestException(f"Coupon {use}: {exc.message.rstrip('.')}. Remove it or try another code.") from exc
            fields = {"coupon_code": c["code"], "coupon_id": c["coupon_id"], "discount_amount": c["discount"]}
        else:
            return enrollment
        if all(enrollment.get(k) == v for k, v in fields.items()):
            return enrollment
        flt: dict = {"_id": enrollment["_id"], "status": {"$in": list(OPEN_STATUSES)}}
        if expected_revision is not None:
            flt["$or"] = self._revision_match(expected_revision)
        updated = await self.enrollments.collection.find_one_and_update(
            flt, {"$set": {**fields, "updated_at": now_ist()}}, return_document=ReturnDocument.AFTER,
        )
        return updated or enrollment

    @classmethod
    def _discount_shares(cls, enrollment: dict, pending: list[dict], activatable: list[dict]) -> dict[str, int]:
        """vehicle_id -> whole rupees of the request's coupon discount, split
        by price over the cars that will activate (a skipped car takes its
        part of the discount with it)."""
        discount = cls._request_discount(enrollment)
        if not discount or not activatable:
            return {}
        full = sum(int(c["price"]) for c in pending) or 1
        got = sum(int(c["price"]) for c in activatable)
        if got < full:
            discount = round_rupees(discount * got / full)
        prices = [float(c["price"]) for c in activatable]
        return {c["vehicle_id"]: p for c, p in zip(activatable, split_whole_rupees(discount, prices, prices))}

    async def _reverse_coupon(self, code: str, customer_id: str, ref: str) -> None:
        from app.services.coupon_service import CouponService

        try:
            await CouponService(self.db).reverse_usage(code, customer_id, ref)
        except Exception:  # noqa: BLE001
            logger.exception("Could not give back coupon %s for %s", code, ref)

    async def _tell_resident(self, customer_id: str, title: str, message: str) -> None:
        from app.services.notification_service import NotificationService

        try:
            await NotificationService(self.db).notify(customer_id, title, message, background=True)
        except Exception:  # noqa: BLE001
            logger.exception("Could not notify resident %s", customer_id)

    async def _renewable_subs(self, enrollment: dict) -> list[dict]:
        if enrollment.get("status") != "active":
            raise BadRequestException("Only an active society plan can be renewed.")
        subs = await self._subs_by_id([c.get("subscription_id") for c in enrollment.get("cars") or [] if c.get("status") == "active"])
        now = now_ist()
        eligible = [
            s for s in subs.values()
            if s.get("status") != SubscriptionStatus.CANCELLED.value and (_aware(s.get("end_date")) or now) - now <= RENEW_WINDOW
        ]
        if not eligible:
            ends = sorted(_aware(s["end_date"]) for s in subs.values() if s.get("end_date"))
            when = from_stored(ends[0]).strftime("%d %b") if ends else "the plan's end date"
            raise BadRequestException(f"Renewal opens 3 days before the plan ends ({when}).")
        eligible = [s for s in eligible if not await self._car_on_another_pass(s)]
        if not eligible:
            raise BadRequestException("These cars are on another active plan now — nothing to renew here.")
        plan = await self.plans.find_by_id(enrollment["plan_id"])
        if not plan or not plan.get("is_active"):
            raise BadRequestException("This plan isn't sold any more — ask the manager to move you to another plan.")
        return eligible

    async def renew(
        self, enrollment: dict, *, method: str, actor_id: str | None, order: dict | None = None, subscription_ids: list[str] | None = None,
        coupon_code: str | None = None,
    ) -> dict:
        """Next plan month for every car in its renewal window (or the
        exact cars an online renewal order was minted for). A coupon (staff
        cash renewal, or the one an online renewal order was priced with)
        comes off in whole rupees and is counted once per renewal."""
        if subscription_ids:
            subs = list((await self._subs_by_id(subscription_ids)).values())
            subs = [s for s in subs if s.get("enrollment_id") == str(enrollment["_id"]) and s.get("status") != SubscriptionStatus.CANCELLED.value]
            # Paid for, but the car moved to another plan meanwhile: skipped,
            # so the order is parked for a human (renewed < paid for).
            subs = [s for s in subs if not await self._car_on_another_pass(s)]
            if not subs:
                raise BadRequestException("Nothing left to renew on this enrollment.")
        else:
            subs = await self._renewable_subs(enrollment)
        count = int(enrollment.get("premium_count") or 0)
        prices = {c.get("subscription_id"): int(c.get("price") or 0) for c in enrollment.get("cars") or []}
        coupon, discount_total = None, 0
        if order is not None:
            coupon, discount_total = order.get("society_coupon_code"), int(order.get("society_discount") or 0)
        elif coupon_code:
            c = await self.coupon_discount(coupon_code, sum(prices.get(str(s["_id"]), 0) for s in subs), enrollment.get("customer_id"))
            coupon, discount_total = c["code"], c["discount"]
        sub_prices = [float(prices.get(str(s["_id"]), 0)) for s in subs]
        shares = dict(zip((str(s["_id"]) for s in subs), split_whole_rupees(discount_total, sub_prices, sub_prices))) if discount_total else {}
        renewed = []
        for sub in subs:
            read_end = sub.get("end_date")
            # Guarded on the doc we read: a booking spending quota meanwhile
            # just makes us re-read and try again (fields recomputed from the
            # fresh doc). A cash renewal whose car was renewed by someone
            # else in between (a double tap, or the resident paying online)
            # stops — one payment never buys two cycles. An online order
            # always applies: that money is in, for its own cycle.
            for _ in range(5):
                price = prices.get(str(sub["_id"]))
                fields = self._renewal_fields(sub, count, None if price is None else price - shares.get(str(sub["_id"]), 0), method, now_ist())
                result = await self.subs.update_if(str(sub["_id"]), {"updated_at": sub.get("updated_at")}, fields)
                if result is not None:
                    renewed.append(str(sub["_id"]))
                    break
                fresh = await self.subs.find_by_id(str(sub["_id"]))
                if not fresh or (order is None and fresh.get("end_date") != read_end):
                    break
                sub = fresh
        gross = sum(prices.get(s, 0) for s in renewed)
        discount = sum(shares.get(s, 0) for s in renewed)
        amount = gross - discount
        if renewed:
            payment = await self.payments.create({
                "society_id": enrollment["society_id"], "enrollment_id": str(enrollment["_id"]), "customer_id": enrollment["customer_id"],
                "service_center_id": enrollment.get("service_center_id"), "amount": amount, "method": method,
                "gross_amount": gross, "discount_amount": discount, "coupon_code": coupon if discount else None,
                "kind": "renewal", "car_count": len(renewed), "subscription_ids": renewed,
                "collected_by": actor_id if method == "cash" else None,
                "razorpay_order_id": (order or {}).get("razorpay_order_id"),
            })
            if coupon and discount:
                await self._record_coupon(coupon, enrollment["customer_id"], f"society-renewal:{payment['_id']}")
            await self._tell_resident(enrollment["customer_id"], "Society plan renewed", f"{enrollment.get('plan_name')} renewed for {len(renewed)} car{'s' if len(renewed) != 1 else ''}.")
        fresh = await self.get_enrollment(str(enrollment["_id"]))
        return {"enrollment": await self.enrollment_view(fresh), "renewed": len(renewed), "amount": amount, "discount": discount}

    @staticmethod
    def _renewal_fields(sub: dict, count: int, price: int | None, method: str, now: datetime) -> dict:
        """The next plan month for one car's pass, from the doc as read.
        Early (before end_date): starts at the old end and keeps the unused
        premium washes; late: starts now with a fresh quota."""
        old_end = _aware(sub.get("end_date")) or now
        early = old_end > now
        new_start = old_end if early else now
        remaining = (int(sub.get("remaining_service_count") or 0) + count) if early else count
        fields = {
            "status": SubscriptionStatus.ACTIVE.value,
            "total_service_count": remaining,
            "remaining_service_count": remaining,
            "end_date": plan_month_end(new_start),
            "cycle_start": new_start,
            "prev_cycle_start": (sub.get("cycle_start") or sub.get("start_date")) if early else None,
            "renewal_count": int(sub.get("renewal_count") or 0) + 1,
            "last_renewed_at": now,
            "amount_paid": float(price if price is not None else sub.get("purchased_price") or 0),
            "payment_method": method,
            "expiry_reminder_sent": False,
            "wash_reminder_sent_at": None,
            "used_up_notice_sent_at": None,
        }
        if not early:
            fields["start_date"] = now
        return fields

    async def _car_on_another_pass(self, sub: dict) -> bool:
        """True when this pass's car has picked up a DIFFERENT live pass
        meanwhile (e.g. the society pass lapsed and the car was enrolled
        again) — renewing it would put two live passes on one car."""
        if not sub.get("vehicle_id"):
            return False
        now = now_ist()
        others = await self.db.user_subscriptions.find(
            {"customer_id": sub.get("customer_id"), "vehicle_id": sub["vehicle_id"], "_id": {"$ne": sub["_id"]},
             "status": "active", "is_deleted": {"$ne": True}},
            {"end_date": 1},
        ).to_list(length=20)
        return any((_aware(o.get("end_date")) or now) > now for o in others)

    async def cancel(self, enrollment: dict, *, vehicle_ids: list[str] | None, actor_id: str, reason: str | None = None) -> dict:
        eid = str(enrollment["_id"])
        if enrollment.get("status") == "cancelled":
            raise BadRequestException("This enrollment is already cancelled.")
        now = now_ist()
        if enrollment.get("status") in OPEN_STATUSES:
            result = await self.enrollments.collection.update_one(
                {"_id": enrollment["_id"], "status": {"$in": list(OPEN_STATUSES)}},
                {"$set": {"status": "cancelled", "cancelled_at": now, "cancel_reason": reason, "updated_at": now}},
            )
            # A withdrawn request never keeps a coupon use — if one was
            # counted (an activation that couldn't place any car falls back
            # to an open request), it goes back to the resident.
            if result.modified_count and enrollment.get("coupon_usage_recorded") and enrollment.get("coupon_code"):
                await self._reverse_coupon(enrollment["coupon_code"], enrollment["customer_id"], f"society:{eid}")
                await self.enrollments.update_by_id(eid, {"coupon_usage_recorded": False})
        else:
            targets = set(vehicle_ids or [c.get("vehicle_id") for c in enrollment.get("cars") or []])
            cars = []
            sub_ids = []
            for c in enrollment.get("cars") or []:
                if c.get("vehicle_id") in targets and c.get("status") == "active":
                    sub_ids.append(c.get("subscription_id"))
                    c = {**c, "status": "cancelled"}
                cars.append(c)
            if not sub_ids:
                raise BadRequestException("Nothing to cancel — pick an active car.")
            await self.db.user_subscriptions.update_many(
                {"_id": {"$in": [ObjectId(s) for s in sub_ids if s and ObjectId.is_valid(s)]}},
                {"$set": {"status": SubscriptionStatus.CANCELLED.value, "auto_renew": False, "updated_at": now}},
            )
            update: dict = {"cars": cars, "updated_at": now}
            if not any(c.get("status") == "active" for c in cars):
                update.update(status="cancelled", cancelled_at=now, cancel_reason=reason)
            await self.enrollments.update_by_id(eid, update)
        fresh = await self.get_enrollment(eid)
        return await self.enrollment_view(fresh)

    # -- online payment (called by PaymentService) ------------------------

    async def payment_quote(self, customer_id: str, enrollment_id: str | None, renewal: bool, coupon_code: str | None = None) -> tuple[int, str, dict]:
        if not enrollment_id:
            raise BadRequestException("society_enrollment_id is required for a society payment")
        enrollment = await self.get_enrollment(enrollment_id)
        if enrollment.get("customer_id") != customer_id:
            raise NotFoundException("Enrollment not found")
        society = await self.societies.find_by_id(enrollment["society_id"]) or {}
        if renewal:
            subs = await self._renewable_subs(enrollment)
            prices = {c.get("subscription_id"): int(c.get("price") or 0) for c in enrollment.get("cars") or []}
            sub_ids = [str(s["_id"]) for s in subs]
            amount = sum(prices.get(s, 0) for s in sub_ids)
            reference = {
                "receipt": f"socr-{enrollment_id[-10:]}", "society_enrollment_id": enrollment_id,
                "society_renewal": True, "society_subscription_ids": sub_ids,
            }
            if coupon_code:
                c = await self.coupon_discount(coupon_code, amount, customer_id)
                amount -= c["discount"]
                reference.update(society_coupon_code=c["code"], society_discount=c["discount"])
            return int(amount * 100), f"Renewal — {enrollment.get('plan_name')} ({len(sub_ids)} car{'s' if len(sub_ids) != 1 else ''})"[:255], reference
        if enrollment.get("status") not in OPEN_STATUSES:
            raise BadRequestException("This society plan is already active." if enrollment.get("status") == "active" else "This request was cancelled.")
        for car in enrollment.get("cars") or []:
            if car.get("status") == "pending" and await self._live_pass_on(customer_id, car["vehicle_id"]):
                raise BadRequestException(f"{car['registration_number']} already has an active plan — remove it and submit again.")
        discount = self._request_discount(enrollment)
        if discount:
            # The coupon frozen on the request must still be good now
            # (dates, limits) — it's counted when this payment activates.
            try:
                await self.coupon_discount(enrollment["coupon_code"], self._pending_total(enrollment), customer_id)
            except BadRequestException as exc:
                raise BadRequestException(
                    f"Coupon {enrollment['coupon_code']}: {exc.message.rstrip('.')}. Submit the form again without it, or ask your manager."
                ) from exc
        await self.enrollments.collection.update_one(
            {"_id": enrollment["_id"], "status": "requested"}, {"$set": {"status": "awaiting_payment", "updated_at": now_ist()}}
        )
        amount = self._pending_total(enrollment) - discount
        cars = len([c for c in enrollment.get("cars") or [] if c.get("status") == "pending"])
        return amount * 100, f"{enrollment.get('plan_name')} — {society.get('name')} ({cars} car{'s' if cars != 1 else ''})"[:255], {
            "receipt": f"soc-{enrollment_id[-12:]}", "society_enrollment_id": enrollment_id,
            "society_renewal": False, "society_revision": int(enrollment.get("revision") or 1),
            **({"society_coupon_code": enrollment["coupon_code"], "society_discount": discount} if discount else {}),
        }

    async def on_order_paid(self, order: dict) -> dict:
        """A verified society payment. Returns {"ok": bool, ...}; not ok ->
        the payment service parks the order for a human (money is in)."""
        try:
            enrollment = await self.get_enrollment(order.get("society_enrollment_id") or "")
        except NotFoundException:
            return {"ok": False}
        if order.get("society_renewal"):
            result = await self.renew(enrollment, method="online", actor_id=None, order=order, subscription_ids=order.get("society_subscription_ids"))
            paid_for = len(order.get("society_subscription_ids") or [])
            return {"ok": result["renewed"] == paid_for and paid_for > 0, "enrollment_id": str(enrollment["_id"])}
        if int(enrollment.get("revision") or 1) != int(order.get("society_revision") or 1):
            return {"ok": False, "enrollment_id": str(enrollment["_id"])}
        expected = (self._pending_total(enrollment) - self._request_discount(enrollment)) * 100
        if expected != int(order.get("amount_paise") or 0):
            return {"ok": False, "enrollment_id": str(enrollment["_id"])}
        try:
            result = await self.activate(
                enrollment, method="online", actor_id=None, order=order, expected_revision=int(order.get("society_revision") or 1),
            )
        except (BadRequestException, ConflictException):
            return {"ok": False, "enrollment_id": str(enrollment["_id"])}
        return {"ok": result["activated"] > 0 and not result["skipped"], "enrollment_id": str(enrollment["_id"])}

    # ------------------------------------------------------------------
    # Premium bookings
    # ------------------------------------------------------------------

    async def book_premium(
        self, payload, *, actor_id: str, actor_role: str, actor_center_id: str | None, society_id: str | None = None,
    ) -> dict:
        from app.models.enums import PaymentMethod
        from app.schemas.booking_schema import BookingCreateRequest, BookingGroupCreateRequest, GroupVehicleRequest
        from app.services.booking_service import BookingService

        subs = await self._subs_by_id(payload.subscription_ids)
        if len(subs) != len(set(payload.subscription_ids)):
            raise NotFoundException("Plan not found")
        rows = list(subs.values())
        customer_ids = {s.get("customer_id") for s in rows}
        society_ids = {s.get("society_id") for s in rows}
        if None in society_ids or len(customer_ids) != 1 or len(society_ids) != 1:
            raise BadRequestException("Pick cars from one resident's society plan.")
        customer_id = customer_ids.pop()
        plan_society_id = society_ids.pop()
        if society_id is not None and plan_society_id != society_id:
            # The staff route is scoped to ONE society (and audited as such).
            raise NotFoundException("Plan not found")
        society = await self.get_society(plan_society_id)
        if actor_role == "customer":
            if customer_id != actor_id:
                raise NotFoundException("Plan not found")
            source = "app"
        elif actor_role in ("manager", "admin"):
            ensure_own_center(actor_role, actor_center_id, society.get("service_center_id"))
            source = "staff"
        else:
            raise ForbiddenException("You can't book this")
        now = now_ist()
        for s in rows:
            if not sub_is_live(s, now):
                raise BadRequestException("This society plan has ended — renew it to book premium washes.")
            if int(s.get("remaining_service_count") or 0) < 1:
                raise BadRequestException("No premium washes left this cycle on one of these cars.")
        try:
            day = datetime.strptime(payload.scheduled_date, "%Y-%m-%d")
        except ValueError as exc:
            raise BadRequestException("Pick a valid date.") from exc
        if source == "app" and day.date() <= today_ist():
            raise BadRequestException("Premium washes are booked at least a day ahead — pick tomorrow or later.")
        enrollment = await self.get_enrollment(rows[0].get("enrollment_id") or "")
        address_id = await self._ensure_address(customer_id, society, enrollment.get("flat") or "Flat")
        booking = BookingService(self.db)
        notes = payload.notes or f"Society premium wash — {society.get('name')}, {enrollment.get('flat')}"
        # A premium wash is plan-covered: the car's society pass pays for it
        # (payment_method "subscription"), only the premium service, no
        # add-ons. The group path used to send payment_method=None, which
        # BookingCreateRequest refuses — every multi-car staff booking 500'd.
        if len(rows) == 1:
            s = rows[0]
            result = await booking.create_booking(
                customer_id,
                BookingCreateRequest(
                    vehicle_id=s["vehicle_id"], address_id=address_id, service_ids=[s["service_id"]],
                    scheduled_date=day, scheduled_slot=payload.scheduled_slot,
                    subscription_id=str(s["_id"]), customer_notes=notes, payment_method=PaymentMethod.SUBSCRIPTION,
                ),
                _skip_verification_gate=source == "staff", source=source, _allow_pinless=source == "staff",
            )
            created = [result]
        else:
            visit = await booking.create_booking_group(
                customer_id,
                BookingGroupCreateRequest(
                    vehicles=[GroupVehicleRequest(vehicle_id=s["vehicle_id"], service_ids=[s["service_id"]], subscription_id=str(s["_id"])) for s in rows],
                    address_id=address_id, scheduled_date=payload.scheduled_date, scheduled_slot=payload.scheduled_slot,
                    customer_notes=notes, payment_method=PaymentMethod.SUBSCRIPTION,
                ),
                source=source, allow_pinless=source == "staff",
            )
            created = visit["bookings"]
        return {
            "bookings": [{"id": b["id"], "booking_number": b.get("booking_number"), "status": b.get("status"), "total_amount": b.get("total_amount")} for b in created],
            "scheduled_date": payload.scheduled_date,
            "scheduled_slot": payload.scheduled_slot,
            "slot_label": format_slot_12h(payload.scheduled_slot),
        }

    # ------------------------------------------------------------------
    # Captain: today's societies + attendance
    # ------------------------------------------------------------------

    async def _society_for_captain(self, society_id: str, captain_id: str) -> dict:
        society = await self.societies.find_by_id(society_id) if ObjectId.is_valid(society_id or "") else None
        if not society or not society.get("is_active", True) or self.today_captain_id(society) != captain_id:
            raise ForbiddenException("You're not today's captain for this society.")
        return society

    async def _live_society_subs(self, society_id: str) -> list[dict]:
        now = now_ist()
        return await self.db.user_subscriptions.find(
            {"society_id": society_id, "status": "active", "end_date": {"$gt": now}, "is_deleted": {"$ne": True}}
        ).to_list(length=MAX_ROWS)

    async def _captain_card(self, society: dict, captain_id: str) -> dict:
        sid = str(society["_id"])
        today = today_ist()
        att = await self.db.society_attendance.find_one({"society_id": sid, "date": today.isoformat()})
        subs = await self._live_society_subs(sid)
        usage = await self._bucket_usage(sid, subs, exclude_today=True)
        names = await self._type_names()
        enrollments = await self._enrollments_by_id([s.get("enrollment_id") for s in subs])
        service_names = await self._service_names([s.get("service_id") for s in subs])
        washed = set((att or {}).get("washed_vehicle_ids") or [])
        cars = []
        for s in subs:
            e = enrollments.get(s.get("enrollment_id") or "") or {}
            plate = next((c.get("registration_number") for c in e.get("cars") or [] if c.get("vehicle_id") == s.get("vehicle_id")), None)
            used = usage.get(s.get("vehicle_id") or "", 0)
            allowance = sub_bucket_allowance(s, today_ist())
            cars.append({
                "vehicle_id": s.get("vehicle_id"),
                "registration_number": plate,
                "vehicle_type_name": names.get(s.get("vehicle_type") or ""),
                "flat": e.get("flat"),
                "used": used,
                "allowance": allowance,
                "allowance_left": max(0, allowance - used),
                "bucket_label": bucket_short_label(allowance),
                "premium_service_name": service_names.get(s.get("service_id") or "") or "Premium wash",
                "premium_remaining": int(s.get("remaining_service_count") or 0),
                "premium_total": int(s.get("total_service_count") or 0),
                "washed_today": s.get("vehicle_id") in washed,
            })
        cars.sort(key=lambda c: ((c["flat"] or "").lower(), c["registration_number"] or ""))
        return {
            "id": sid,
            "name": society.get("name"),
            "address_line": society.get("address_line"),
            "area": society.get("area"),
            "latitude": society.get("latitude"),
            "longitude": society.get("longitude"),
            "is_substitute": (society.get("substitute") or {}).get("date") == today.isoformat() and (society.get("substitute") or {}).get("captain_id") == captain_id,
            "attendance": {
                "arrived_at": _iso(att.get("arrived_at")),
                "distance_m": att.get("distance_m"),
                "far_from_society": bool(att.get("far_from_society")),
                "location_missing": bool(att.get("location_missing")),
                "washed_count": len(washed),
            } if att else None,
            "cars": cars,
        }

    async def _enrollments_by_id(self, ids) -> dict[str, dict]:
        oids = [ObjectId(i) for i in set(ids) if i and ObjectId.is_valid(str(i))]
        if not oids:
            return {}
        rows = await self.db.society_enrollments.find(
            {"_id": {"$in": oids}},
            {"flat": 1, "cars": 1, "resident_name": 1, "bucket_days": 1, "premium_count": 1, "premium_service_id": 1},
        ).to_list(length=len(oids))
        return {str(r["_id"]): r for r in rows}

    async def captain_today(self, captain_id: str) -> dict:
        today = today_ist().isoformat()
        societies = await self.societies.collection.find({
            "is_active": True, "is_deleted": {"$ne": True},
            "$or": [{"daily_captain_id": captain_id}, {"substitute.captain_id": captain_id, "substitute.date": today}],
        }).to_list(length=50)
        mine = [s for s in societies if self.today_captain_id(s, today) == captain_id]
        return {"date": today, "societies": [await self._captain_card(s, captain_id) for s in mine]}

    async def arrive(self, captain_id: str, society_id: str, payload) -> dict:
        society = await self._society_for_captain(society_id, captain_id)
        sid = str(society["_id"])
        today = today_ist().isoformat()
        distance_m = None
        has_fix = payload.latitude is not None and payload.longitude is not None
        if has_fix and society.get("latitude") is not None:
            distance_m = round(haversine_km(payload.latitude, payload.longitude, society["latitude"], society["longitude"]) * 1000)
        far = distance_m is not None and distance_m > ARRIVAL_RADIUS_M
        doc = {
            "society_id": sid,
            "service_center_id": society.get("service_center_id"),
            "date": today,
            "captain_id": captain_id,
            "arrived_at": now_ist(),
            "location": {"latitude": payload.latitude, "longitude": payload.longitude, "accuracy_m": payload.accuracy_m} if has_fix else None,
            "distance_m": distance_m,
            "far_from_society": far,
            "location_missing": not has_fix,
            "washed_vehicle_ids": [],
        }
        try:
            await self.attendance.create(doc)
        except DuplicateKeyError:
            existing = await self.db.society_attendance.find_one({"society_id": sid, "date": today})
            if existing and existing.get("captain_id") != captain_id:
                raise BadRequestException("Attendance for this society is already marked today.")
        else:
            if has_fix:
                try:
                    from app.repositories.captain_location_repository import CaptainLocationRepository

                    await CaptainLocationRepository(self.db).record(captain_id, payload.latitude, payload.longitude, now_ist(), source="society_arrival")
                except Exception:  # noqa: BLE001 — the breadcrumb is a courtesy
                    logger.exception("Could not record the arrival breadcrumb")
            if far or not has_fix:
                captain = await self.db.users.find_one({"_id": ObjectId(captain_id)}, {"full_name": 1}) or {}
                what = f"{distance_m} m from the society pin" if far else "without a location"
                await self._tell_managers(society, "Society arrival flagged", f"{captain.get('full_name') or 'Captain'} marked arrival at {society.get('name')} {what}.")
        return await self._captain_card(society, captain_id)

    async def set_washed(self, captain_id: str, society_id: str, vehicle_ids: list[str]) -> dict:
        society = await self._society_for_captain(society_id, captain_id)
        sid = str(society["_id"])
        today = today_ist().isoformat()
        att = await self.db.society_attendance.find_one({"society_id": sid, "date": today})
        if not att:
            raise BadRequestException("Mark your arrival first.")
        if att.get("captain_id") != captain_id:
            raise ForbiddenException("Another captain marked attendance here today.")
        subs = await self._live_society_subs(sid)
        by_vehicle = {s.get("vehicle_id"): s for s in subs}
        wanted = list(dict.fromkeys(vehicle_ids))
        unknown = [v for v in wanted if v not in by_vehicle]
        if unknown:
            raise BadRequestException("One of those cars isn't on a live plan in this society.")
        already = set(att.get("washed_vehicle_ids") or [])
        added = [v for v in wanted if v not in already]
        if added:
            usage = await self._bucket_usage(sid, [by_vehicle[v] for v in added], exclude_today=True)
            enrollments = await self._enrollments_by_id([by_vehicle[v].get("enrollment_id") for v in added])
            for v in added:
                allowance = sub_bucket_allowance(by_vehicle[v], today_ist())
                if usage.get(v, 0) >= allowance:
                    e = enrollments.get(by_vehicle[v].get("enrollment_id") or "") or {}
                    plate = next((c.get("registration_number") for c in e.get("cars") or [] if c.get("vehicle_id") == v), "This car")
                    raise BadRequestException(f"{plate} has used all {allowance} bucket washes this cycle.")
        await self.db.society_attendance.update_one(
            {"_id": att["_id"]}, {"$set": {"washed_vehicle_ids": wanted, "washed_updated_at": now_ist(), "updated_at": now_ist()}}
        )
        return await self._captain_card(society, captain_id)

    # ------------------------------------------------------------------
    # Reports
    # ------------------------------------------------------------------

    async def attendance_month(self, society: dict, month: str | None) -> dict:
        sid = str(society["_id"])
        start, end = _month_bounds(month)
        rows = await self.db.society_attendance.find(
            {"society_id": sid, "date": {"$gte": start.date().isoformat(), "$lt": end.date().isoformat()}}
        ).sort("date", 1).to_list(length=40)
        captains = await self._users_by_id([r.get("captain_id") for r in rows])
        today = today_ist()
        created = from_stored(society["created_at"]).date() if society.get("created_at") else start.date()
        days = []
        d = start.date()
        by_date = {r["date"]: r for r in rows}
        while d < end.date():
            r = by_date.get(d.isoformat())
            tracked = created <= d <= today
            days.append({
                "date": d.isoformat(),
                "present": bool(r),
                "missed": tracked and not r and d < today and d.weekday() != 6,
                "future": d > today,
                "arrived_at": _iso(r.get("arrived_at")) if r else None,
                "captain_name": (captains.get(r.get("captain_id") or "") or {}).get("full_name") if r else None,
                "washed_count": len(r.get("washed_vehicle_ids") or []) if r else 0,
                "far_from_society": bool(r.get("far_from_society")) if r else False,
                "location_missing": bool(r.get("location_missing")) if r else False,
                "distance_m": r.get("distance_m") if r else None,
            })
            d += timedelta(days=1)
        return {
            "month": start.strftime("%Y-%m"),
            "days": days,
            "present_days": sum(1 for x in days if x["present"]),
            "missed_days": sum(1 for x in days if x["missed"]),
            "washes": sum(x["washed_count"] for x in days),
        }

    async def payments_for(self, society: dict, limit: int = 50) -> list[dict]:
        rows = await self.db.society_payments.find({"society_id": str(society["_id"])}).sort("created_at", -1).to_list(length=limit)
        enrollments = await self._enrollments_by_id([r.get("enrollment_id") for r in rows])
        names = await self._type_names() if rows else {}
        service_names = await self._service_names([e.get("premium_service_id") for e in enrollments.values()])
        out = []
        for r in rows:
            e = enrollments.get(r.get("enrollment_id") or "") or {}
            paid_for = set(r.get("subscription_ids") or [])
            # The cars this payment covered (plate + car type) and what the
            # plan covers — so a payment row reads on its own.
            cars = [
                {"registration_number": c.get("registration_number"), "vehicle_type_name": names.get(c.get("vehicle_type") or "") or None}
                for c in e.get("cars") or []
                if not paid_for or c.get("subscription_id") in paid_for
            ]
            service = service_names.get(e.get("premium_service_id") or "")
            out.append({
                "id": str(r["_id"]),
                "amount": r.get("amount"),
                "method": r.get("method"),
                "kind": r.get("kind"),
                "car_count": r.get("car_count"),
                "resident_name": e.get("resident_name"),
                "flat": e.get("flat"),
                "plan_services": combo_name(e.get("bucket_days") or 0, int(e.get("premium_count") or 0), service) if e and service else None,
                "cars": cars,
                "created_at": _iso(r.get("created_at")),
            })
        return out

    # ------------------------------------------------------------------
    # Reminder-loop sweep
    # ------------------------------------------------------------------

    async def alert_missing_attendance(self, limit: int = 100) -> int:
        """One in-app alert per society per day when its captain hasn't
        marked arrival by ATTENDANCE_ALERT_HOUR (IST, Sundays off). Bounded:
        a capped, indexed batch per pass, and every society looked at is
        stamped for the day, so it's never re-read until tomorrow."""
        now = now_ist()
        if now.hour < ATTENDANCE_ALERT_HOUR or now.weekday() == 6:
            return 0
        today = now.date().isoformat()
        due = await self.societies.collection.find(
            {"is_active": True, "is_deleted": {"$ne": True}, "attendance_alert_date": {"$ne": today}, "daily_captain_id": {"$nin": [None, ""]}},
            {"name": 1, "service_center_id": 1, "daily_captain_id": 1, "substitute": 1},
        ).limit(limit).to_list(length=limit)
        if not due:
            return 0
        ids = [str(s["_id"]) for s in due]
        present = {a["society_id"] for a in await self.db.society_attendance.find({"society_id": {"$in": ids}, "date": today}, {"society_id": 1}).to_list(length=len(ids))}
        alerted = 0
        for s in due:
            if str(s["_id"]) not in present:
                captain_id = self.today_captain_id(s, today) or ""
                captain = await self.db.users.find_one({"_id": ObjectId(captain_id)}, {"full_name": 1}) if ObjectId.is_valid(captain_id) else None
                who = f" ({captain.get('full_name')})" if captain and captain.get("full_name") else ""
                await self._tell_managers(s, "Society attendance missing", f"No arrival marked at {s.get('name')} yet today{who}.")
                alerted += 1
        await self.societies.collection.update_many({"_id": {"$in": [s["_id"] for s in due]}}, {"$set": {"attendance_alert_date": today}})
        return alerted


def _month_bounds(month: str | None) -> tuple[datetime, datetime]:
    """[first day, first day of next month) of 'YYYY-MM' (default: this
    month), as IST-aware datetimes."""
    from app.utils.timezone import IST

    base = now_ist()
    if month:
        try:
            base = datetime.strptime(month, "%Y-%m").replace(tzinfo=IST)
        except ValueError as exc:
            raise BadRequestException("Month must look like 2026-10.") from exc
        # 9999-12 would overflow the "next month" below (a 500).
        if not 2000 <= base.year <= 2200:
            raise BadRequestException("Pick a month between 2000 and 2200.")
    start = base.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    end = (start + timedelta(days=32)).replace(day=1)
    return start, end

