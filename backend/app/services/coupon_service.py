
import hashlib
import logging
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone

from motor.motor_asyncio import AsyncIOMotorDatabase
from pymongo.errors import DuplicateKeyError

from app.core.config import settings
from app.core.exceptions import BadRequestException, ConflictException, NotFoundException
from app.core.rate_limit import current_client, current_device
from app.models.enums import CouponType
from app.repositories.coupon_repository import (
    CouponBundleRepository,
    CouponCodeRepository,
    CouponRepository,
    CouponUsageRepository,
    CouponUserCounterRepository,
)
from app.services import auth_guard
from app.services.coupon_rules import USE_LABEL, WEEKDAYS, rules_of
from app.utils.coupon_codes import mask_code
from bson import ObjectId
from app.schemas.coupon_schema import CouponCreateRequest, CouponUpdateRequest
from app.utils.serializers import serialize_doc, serialize_list
from app.utils.timezone import from_stored, now_ist
from app.utils.money import round_rupees

logger = logging.getLogger(__name__)

# What a bundle code says when it can't be used (Title Case, like every
# person-facing line). Unknown / switched-off codes keep the legacy words.
BUNDLE_EXPIRED = "This Coupon Has Expired"
BUNDLE_USED = "This Coupon Has Already Been Used"

# The least a plan / society sale may cost after a coupon (Razorpay cannot
# charge less than one rupee, and a coupon must never give a plan away).
MIN_NET_PLAN_PRICE = 1

NOT_FOR_USE = {
    "services": "This Coupon Is Not Valid For Bookings",
    "plans": "This Coupon Is Not Valid For Plans",
    "society": "This Coupon Is Not Valid For Society Plans",
    "custom": "This Coupon Is Not Valid For Custom Plans",
}


# The WhatsApp webhook always arrives from Meta's addresses: counting coupon
# refusals against that "IP" would hold every chat at once, so the bot turns
# the IP source off for its request (the WhatsApp number's own account is
# still counted).
_ignore_ip: ContextVar[bool] = ContextVar("coupon_ignore_ip", default=False)


@contextmanager
def ignore_ip_scope():
    token = _ignore_ip.set(True)
    try:
        yield
    finally:
        _ignore_ip.reset(token)


class CouponHeldException(BadRequestException):
    """Too many refused codes from this user / device / IP: every coupon
    check is held for the rest of the window. A BadRequest subclass so a
    quote shows it beside the coupon field instead of failing the quote."""

    status_code = 429
    error_code = "COUPON_HELD"


class CouponService:
    def __init__(self, db: AsyncIOMotorDatabase):
        self.repo = CouponRepository(db)
        self.usage_repo = CouponUsageRepository(db)
        self.user_counters = CouponUserCounterRepository(db)
        self.bundles = CouponBundleRepository(db)
        self.codes = CouponCodeRepository(db)
        self.db = db

    async def list_all(self, page: int, page_size: int, active_only: bool = False):
        filters = {"is_active": True} if active_only else {}
        items, total = await self.repo.find_many(filters, page=page, page_size=page_size)
        return serialize_list(items), total

    async def ensure_default_launch_offer(self) -> None:
        """Kept only because app startup still calls it. The FREEBIKE launch
        offer has ended (founder, 2026-09-27) — startup must never create it
        again on a fresh database. Offers are admin data now (Service
        offer_tag / prices, or a coupon an admin creates).

        It still runs the one-time rules migration below — startup is where
        every coupon gets its explicit use-type."""
        await self.migrate_legacy_rules()
        return None

    async def migrate_legacy_rules(self) -> int:
        """Gives every coupon written before use-types existed its explicit
        rules, keeping what it already did: bookings always; plans /
        society only if it was actually used there; stackable and plan-
        combinable (as it behaved). Idempotent (only coupons without
        `applies_to`), safe to run on every start."""
        done = 0
        async for coupon in self.db.coupons.find({"applies_to": {"$exists": False}}, {"code": 1}):
            code = coupon.get("code")
            uses = ["services"]
            if await self.db.user_subscriptions.find_one({"coupon_code": code}, {"_id": 1}) or await self.db.payment_orders.find_one(
                {"coupon_code": code, "purpose": "subscription"}, {"_id": 1}
            ):
                uses.append("plans")
            if await self.db.society_enrollments.find_one({"coupon_code": code}, {"_id": 1}) or await self.db.society_payments.find_one(
                {"coupon_code": code}, {"_id": 1}
            ):
                uses.append("society")
            await self.db.coupons.update_one(
                {"_id": coupon["_id"], "applies_to": {"$exists": False}},
                {"$set": {
                    "applies_to": uses, "applicable_service_ids": [], "applicable_vehicle_types": [], "applicable_center_ids": [],
                    "first_booking_only": False, "new_customers_only": False, "allowed_customer_phones": [], "allowed_weekdays": [],
                    "time_window_start": None, "time_window_end": None, "stackable_with_offers": True, "combine_with_plan": True,
                }},
            )
            done += 1
        if done:
            logger.info("Coupon rules migrated for %d coupons", done)
        return done

    async def create(self, payload: CouponCreateRequest) -> dict:
        if await self.repo.find_by_code(payload.code):
            raise ConflictException("A coupon with this code already exists")
        doc = payload.model_dump()
        doc["code"] = payload.code.upper()
        doc["eligible_service_keywords"] = self._normalize_keywords(doc.get("eligible_service_keywords"))
        doc["free_addon_keywords"] = self._normalize_keywords(doc.get("free_addon_keywords"))
        # Server-owned counter, never something a create request can set —
        # not part of CouponCreateRequest at all, so it must be seeded here
        # or a brand new coupon has no total_used field until its first use
        # (record_usage's $inc still works on a missing field, but every
        # READ of total_used before that — the admin list, this very
        # method's own response — would be a KeyError/None instead of 0).
        doc["total_used"] = 0
        created = await self.repo.create(doc)
        return serialize_doc(created)

    # Optional limits an admin can remove again (sent as null).
    _CLEARABLE = ("description", "max_discount_amount", "total_usage_limit", "time_window_start", "time_window_end")

    async def update(self, coupon_id: str, payload: CouponUpdateRequest) -> dict:
        data = {k: v for k, v in payload.model_dump(exclude_unset=True).items() if v is not None or k in self._CLEARABLE}
        current = await self.repo.find_by_id(coupon_id)
        if not current:
            raise NotFoundException("Coupon not found")
        # Validate the coupon as it WILL be, not just the fields sent — the
        # same rules create enforces.
        merged = {**current, **data}
        kind = merged.get("offer_kind") or "standard"
        ctype = merged.get("coupon_type")
        ctype = ctype.value if hasattr(ctype, "value") else ctype
        value = float(merged.get("value") or 0)
        if kind == "standard" and value <= 0:
            raise BadRequestException("Standard coupons need a discount value")
        if ctype == CouponType.PERCENTAGE.value and value > 100:
            raise BadRequestException("A percentage discount can't be more than 100%")
        if merged.get("valid_from") and merged.get("valid_until") and from_stored(merged["valid_until"]) <= from_stored(merged["valid_from"]):
            raise BadRequestException("The end date must be after the start date")
        start, end = merged.get("time_window_start"), merged.get("time_window_end")
        if bool(start) != bool(end) or (start and end and start >= end):
            raise BadRequestException("Give both a start and an end time for the time window, the end after the start")
        if "eligible_service_keywords" in data:
            data["eligible_service_keywords"] = self._normalize_keywords(data.get("eligible_service_keywords"))
        if "free_addon_keywords" in data:
            data["free_addon_keywords"] = self._normalize_keywords(data.get("free_addon_keywords"))
        updated = await self.repo.update_by_id(coupon_id, data)
        if not updated:
            raise NotFoundException("Coupon not found")
        return serialize_doc(updated)

    async def delete(self, coupon_id: str) -> None:
        if not await self.repo.soft_delete(coupon_id):
            raise NotFoundException("Coupon not found")

    @staticmethod
    def _normalize_keywords(values: list[str] | None) -> list[str]:
        return [v.strip().lower() for v in (values or []) if v and v.strip()]

    # ------------------------------------------------------------------
    # Code-guessing guard. A refused code (unknown / switched off / expired /
    # used up) counts once per distinct code against the user, the device
    # and the IP in rate_counters (shared by every instance); past the limit
    # every coupon check from that source is held until the window ends.
    # Repeating the SAME wrong code (a cart re-quoting) is counted once.
    # ------------------------------------------------------------------

    @staticmethod
    def _miss_sources(user_id: str | None) -> list[tuple[str, str, int]]:
        sources: list[tuple[str, str, int]] = []
        if user_id:
            sources.append(("u", user_id, settings.COUPON_MISS_LIMIT))
        device = auth_guard.device_hash(current_device())
        if device:
            sources.append(("d", device, settings.COUPON_MISS_LIMIT))
        client = None if _ignore_ip.get() else current_client()
        if client:
            sources.append(("i", client, settings.COUPON_MISS_IP_LIMIT))
        return sources

    async def _guess_gate(self, user_id: str | None) -> None:
        if settings.COUPON_MISS_LIMIT <= 0:
            return
        now = auth_guard.utcnow()
        for scope, value, limit in self._miss_sources(user_id):
            doc = await self.db[auth_guard.COUNTERS].find_one({"_id": f"coupon-miss:{scope}:{auth_guard.key_hash(value)}", "expires_at": {"$gt": now}})
            if doc and int(doc.get("n", 0)) >= limit:
                wait = (auth_guard.aware(doc["expires_at"]) - now).total_seconds()
                raise CouponHeldException(
                    f"Too Many Coupon Attempts. Please Try Again In {auth_guard.human_wait(wait)}.",
                    {"retry_after": max(1, int(wait))},
                )

    async def _count_miss(self, user_id: str | None, code: str) -> None:
        if settings.COUPON_MISS_LIMIT <= 0:
            return
        window = settings.COUPON_MISS_WINDOW_SECONDS
        now = auth_guard.utcnow()
        for scope, value, _limit in self._miss_sources(user_id):
            who = auth_guard.key_hash(value)
            seen, _end = await auth_guard.window_hit(
                self.db, f"coupon-miss-code:{scope}:{who}:{hashlib.sha256(code.encode()).hexdigest()[:16]}", window, now
            )
            if seen == 1:  # a code this source hasn't been refused on yet
                await auth_guard.window_hit(self.db, f"coupon-miss:{scope}:{who}", window, now)
        logger.info("Coupon code %s refused", mask_code(code))

    async def _refuse(self, user_id: str | None, code: str, message: str) -> None:
        """Count a guess-like refusal, then raise it."""
        await self._count_miss(user_id, code)
        raise BadRequestException(message)

    async def _valid_coupon(
        self, code: str, order_value: float, user_id: str | None = None, *, use: str, first_time_eligible: bool | None = None,
    ) -> dict:
        """THE validity check — who may use this code, where, and how often.
        Every flow (quote, create, plan sale, society, bot, edit) comes
        through here; `use` names the flow and is required so no caller can
        skip the use-type rule. `first_time_eligible`: the caller's
        platform-wide answer (phone AND plate) when it has one."""
        await self._guess_gate(user_id)
        coupon = await self.repo.find_by_code(code)
        if not coupon or not coupon.get("is_active"):
            await self._refuse(user_id, code, "Invalid coupon code")
        rules = rules_of(coupon)
        if use not in rules["applies_to"]:
            await self._refuse(user_id, coupon["code"], NOT_FOR_USE.get(use, "This Coupon Can't Be Used Here"))

        now = now_ist()
        # valid_from/valid_until are aware-UTC-semantic (frontend sends real
        # "Z" timestamps), not naive user-entered IST digits — from_stored()
        # is the correct helper here, not to_ist(). See timezone.py.
        if coupon.get("_bundle_id"):
            await self._valid_bundle_coupon(coupon, order_value, user_id, now)
        else:
            valid_from = from_stored(coupon["valid_from"])
            valid_until = from_stored(coupon["valid_until"])
            if now < valid_from or now > valid_until:
                await self._refuse(user_id, coupon["code"], "This coupon has expired or is not yet active")

            if order_value < coupon.get("min_order_value", 0):
                await self._refuse(user_id, coupon["code"], f"Minimum order value of ₹{coupon['min_order_value']:g} required for this coupon")

            if coupon.get("total_usage_limit") and coupon.get("total_used", 0) >= coupon["total_usage_limit"]:
                await self._refuse(user_id, coupon["code"], "This coupon has reached its usage limit")

            if user_id:
                user_usage_count = await self.usage_repo.count_for_user(str(coupon["_id"]), user_id)
                if user_usage_count >= coupon.get("usage_limit_per_user", 1):
                    raise BadRequestException("You have already used this coupon the maximum number of times")

        await self._check_customer(coupon, rules, user_id, first_time_eligible)
        return coupon

    async def _check_customer(self, coupon: dict, rules: dict, user_id: str | None, first_time_eligible: bool | None) -> None:
        """Rules about WHO: specific customers, first booking, new customer.
        Skipped for an anonymous quote (nobody is proven yet) — the booking
        itself re-checks them with the verified account."""
        if not user_id:
            return
        if not (rules["allowed_customer_phones"] or rules["first_booking_only"] or rules["new_customers_only"]):
            return
        user = await self.db.users.find_one({"_id": ObjectId(user_id)}, {"phone": 1}) if ObjectId.is_valid(user_id) else None
        phone = (user or {}).get("phone")
        code = coupon["code"]
        if rules["allowed_customer_phones"] and phone not in rules["allowed_customer_phones"]:
            await self._refuse(user_id, code, "This Coupon Isn't Available For Your Account")
        bookings = self.db.bookings
        if rules["new_customers_only"]:
            # Nothing before: no booking of ANY status (a cancelled one
            # still means they were a customer), no plan, no society seat.
            who = [{"customer_id": user_id}] + ([{"customer_phone": phone}] if phone else [])
            used_before = (
                await bookings.find_one({"$or": who, "is_deleted": {"$ne": True}}, {"_id": 1})
                or await self.db.user_subscriptions.find_one({"customer_id": user_id}, {"_id": 1})
                or await self.db.society_enrollments.find_one({"customer_id": user_id}, {"_id": 1})
            )
            if used_before or first_time_eligible is False:
                await self._refuse(user_id, code, "This Coupon Is For New Customers Only")
        if rules["first_booking_only"]:
            if first_time_eligible is None:
                first_time_eligible = not (phone and await bookings.find_one({"customer_phone": phone, "status": {"$ne": "cancelled"}}, {"_id": 1}))
            if not first_time_eligible:
                await self._refuse(user_id, code, "This Coupon Is For First Bookings Only")

    async def _valid_bundle_coupon(self, coupon: dict, order_value: float, user_id: str | None, now) -> dict:
        """A bundle code is usable until its bundle's expiry (the instant
        itself is already expired), while it has uses left, and — per
        customer — within the bundle's per-customer allowance. Only reads:
        a quote or preview never spends a use (record_usage does)."""
        code = coupon["code"]
        if coupon.get("valid_until") is not None and now >= from_stored(coupon["valid_until"]):
            await self._refuse(user_id, code, BUNDLE_EXPIRED)
        if coupon.get("valid_from") is not None and now < from_stored(coupon["valid_from"]):
            await self._refuse(user_id, code, "This coupon has expired or is not yet active")
        if user_id:
            mine = await self.usage_repo.count_for_user(str(coupon["_id"]), user_id)
            if mine >= int(coupon.get("usage_limit_per_user") or 1):
                raise BadRequestException("You have already used this coupon the maximum number of times")
        if coupon["total_used"] >= coupon["total_usage_limit"]:
            await self._refuse(user_id, code, BUNDLE_USED)
        if order_value < coupon.get("min_order_value", 0):
            await self._refuse(user_id, code, f"Minimum order value of ₹{coupon['min_order_value']:g} required for this coupon")
        return coupon

    # ------------------------------------------------------------------
    # What is being bought: the service / car / center / day / offer rules.
    # Bookings only — a plan or society sale has no cart of services.
    # ------------------------------------------------------------------

    @staticmethod
    def _weekday(value) -> int | None:
        if value is None:
            return None
        if isinstance(value, str):
            try:
                return datetime.strptime(value[:10], "%Y-%m-%d").weekday()
            except ValueError:
                return None
        return value.weekday()

    async def check_applicability(
        self,
        coupon: dict,
        user_id: str | None,
        *,
        services: list[dict] | None,
        vehicle_type: str | None,
        center_id: str | None = None,
        scheduled_date=None,
        scheduled_slot: str | None = None,
        offer_applied: bool = False,
    ) -> None:
        r = rules_of(coupon)
        code = coupon["code"]
        if r["applicable_service_ids"] and services is not None:
            ids = {str(sv["_id"]) for sv in services}
            if not ids & set(r["applicable_service_ids"]):
                await self._refuse(user_id, code, "This Coupon Doesn't Apply To The Selected Service")
        if r["applicable_vehicle_types"] and vehicle_type is not None and str(vehicle_type) not in r["applicable_vehicle_types"]:
            await self._refuse(user_id, code, "This Coupon Doesn't Apply To This Vehicle Type")
        if r["applicable_center_ids"] and center_id is not None and str(center_id) not in r["applicable_center_ids"]:
            await self._refuse(user_id, code, "This Coupon Isn't Valid At This Service Center")
        if r["allowed_weekdays"]:
            day = self._weekday(scheduled_date)
            if day is not None and day not in r["allowed_weekdays"]:
                names = ", ".join(WEEKDAYS[d] for d in sorted(r["allowed_weekdays"]))
                await self._refuse(user_id, code, f"This Coupon Is Only Valid On {names}")
        if r["time_window_start"] and r["time_window_end"] and scheduled_slot:
            begins = str(scheduled_slot).split("-")[0].strip()
            if not (r["time_window_start"] <= begins < r["time_window_end"]):
                await self._refuse(user_id, code, "This Coupon Is Not Valid For This Time Slot")
        if offer_applied and not r["stackable_with_offers"]:
            await self._refuse(user_id, code, "This Coupon Can't Be Combined With The First Wash Offer")

    async def check_held_when(self, code: str | None, scheduled_date, scheduled_slot: str | None) -> None:
        """A booking that already holds a coupon is moved to a new day /
        slot (reschedule, edit): the coupon's weekday and time-window rules
        still have to hold for the new time, or the move is refused."""
        if not code:
            return
        coupon = await self.repo.find_by_code(code)
        if coupon:
            await self.check_applicability(
                coupon, None, services=None, vehicle_type=None, scheduled_date=scheduled_date, scheduled_slot=scheduled_slot,
            )

    async def check_visit_plan_mix(self, code: str | None, covered_cars: int, total_cars: int) -> None:
        """A visit where a plan pays for a car: the coupon (which rides on a
        paying car) only combines when the coupon allows it. An all-covered
        visit carries no coupon at all (nothing is consumed)."""
        if not code or covered_cars <= 0 or covered_cars >= total_cars:
            return
        await self._guess_gate(None)
        coupon = await self.repo.find_by_code(code)
        if coupon and not rules_of(coupon)["combine_with_plan"]:
            # Counted like any refusal: this message exists only for real codes.
            await self._refuse(None, coupon["code"], "This Coupon Can't Be Used On A Visit Covered By A Plan")

    @staticmethod
    def _standard_discount(coupon: dict, order_value: float) -> float:
        if coupon["coupon_type"] == CouponType.PERCENTAGE.value:
            discount = order_value * (coupon["value"] / 100)
            if coupon.get("max_discount_amount"):
                discount = min(discount, coupon["max_discount_amount"])
        else:
            discount = coupon["value"]

        # Whole rupees only — 10% of ₹349 is ₹35 off, never ₹34.90. Never
        # negative (a bad row must not raise the price) and never above the bill.
        return float(max(0, min(round_rupees(max(discount, 0)), round_rupees(order_value))))

    async def validate_and_compute_discount(
        self, code: str, order_value: float, user_id: str | None, *, use: str, vehicle_type: str | None = None,
    ) -> tuple[dict, float]:
        """A coupon on something that is not a cart of services (a plan, a
        society plan, a custom plan). Plans never go below MIN_NET_PLAN_PRICE."""
        coupon = await self._valid_coupon(code, order_value, user_id, use=use)
        if coupon.get("offer_kind", "standard") != "standard":
            if use == "services":
                raise BadRequestException("This offer is applied automatically during booking.")
            raise BadRequestException(
                "This offer works on bookings only, not on society plans." if use == "society" else "This offer works on bookings only, not on plans."
            )
        r = rules_of(coupon)
        if r["applicable_vehicle_types"] and vehicle_type is not None and str(vehicle_type) not in r["applicable_vehicle_types"]:
            await self._refuse(user_id, coupon["code"], "This Coupon Doesn't Apply To This Vehicle Type")
        discount = self._standard_discount(coupon, order_value)
        if use != "services":
            discount = float(max(0, min(discount, round_rupees(order_value) - MIN_NET_PLAN_PRICE)))
        return coupon, discount

    # What an anonymous "is this offer live?" lookup may say about a code:
    # the customer-facing terms. Usage counters/limits and admin metadata
    # stay internal (a full doc told a code-guesser how close a coupon was
    # to its cap and how often each customer may reuse it).
    _PUBLIC_OFFER_FIELDS = (
        "id", "code", "description", "coupon_type", "value", "min_order_value", "max_discount_amount",
        "offer_kind", "eligible_service_keywords", "free_addon_keywords", "is_active", "valid_from", "valid_until",
    )

    async def validate_public_offer(self, code: str) -> dict:
        coupon = await self._valid_coupon(code, 0, None, use="services")
        if coupon.get("_bundle_id"):
            # Single-use codes are secrets: an anonymous lookup must never
            # confirm one (it would turn this endpoint into a code oracle).
            await self._refuse(None, code, "Invalid coupon code")
        coupon = serialize_doc(coupon)
        return {k: coupon.get(k) for k in self._PUBLIC_OFFER_FIELDS}

    async def validate_and_compute_booking_discount(
        self,
        code: str,
        order_value: float,
        user_id: str,
        *,
        services: list[dict],
        quantities: dict[str, int],
        vehicle_type: str,
        first_time_eligible: bool,
        price_resolver,
        center_id: str | None = None,
        scheduled_date=None,
        scheduled_slot: str | None = None,
        offer_applied: bool = False,
    ) -> tuple[dict, float]:
        coupon = await self._valid_coupon(code, order_value, user_id, use="services", first_time_eligible=first_time_eligible)
        await self.check_applicability(
            coupon, user_id, services=services, vehicle_type=vehicle_type, center_id=center_id,
            scheduled_date=scheduled_date, scheduled_slot=scheduled_slot, offer_applied=offer_applied,
        )
        offer_kind = coupon.get("offer_kind", "standard")
        if offer_kind == "standard":
            return coupon, self._standard_discount(coupon, order_value)
        if offer_kind != "free_addon_with_service":
            raise BadRequestException("Unsupported offer type")

        eligible_keywords = self._normalize_keywords(coupon.get("eligible_service_keywords"))
        free_keywords = self._normalize_keywords(coupon.get("free_addon_keywords"))
        if not eligible_keywords or not free_keywords:
            raise BadRequestException("This offer is not configured correctly.")

        def service_name(svc: dict) -> str:
            return f"{svc.get('name', '')} {svc.get('slug', '')}".lower()

        has_eligible_main = any(
            not svc.get("is_addon") and any(keyword in service_name(svc) for keyword in eligible_keywords)
            for svc in services
        )
        if not has_eligible_main:
            raise BadRequestException("This offer does not apply to the selected service.")

        discount = 0.0
        for svc in services:
            if not svc.get("is_addon"):
                continue
            if not any(keyword in service_name(svc) for keyword in free_keywords):
                continue
            qty = quantities.get(str(svc["_id"]), 1)
            discount += price_resolver(svc, vehicle_type, first_time_eligible) * qty
        if discount <= 0:
            raise BadRequestException("Add the free offer item to claim this offer.")
        return coupon, float(min(round_rupees(discount), round_rupees(order_value)))

    # The coupon's own cap: no limit (missing, null or 0 — the same reading
    # _valid_coupon gives it), or fewer uses than the limit. $ifNull because
    # {$eq: ["$total_usage_limit", null]} is FALSE for a doc that simply has
    # no such field, which made such a coupon unusable.
    _UNDER_TOTAL_LIMIT = {"$or": [
        {"$in": [{"$ifNull": ["$total_usage_limit", None]}, [None, 0]]},
        {"$lt": [{"$ifNull": ["$total_used", 0]}, "$total_usage_limit"]},
    ]}

    async def record_usage(self, coupon_id: str, user_id: str, booking_id: str) -> None:
        """Spends one use, atomically on BOTH limits: the customer's own
        (usage_limit_per_user, a guarded $inc on their counter doc — see
        CouponUserCounterRepository) and the coupon's total (a guarded $inc
        on the coupon itself). A request that loses either race gets an
        error and leaves nothing behind; only then is the usage row (the
        per-booking record reverse_usage undoes) written."""
        coupon = await self.repo.find_by_id(coupon_id)
        if not coupon:
            bundle_code = await self.codes.find_effective_by_id(coupon_id)
            if bundle_code:
                await self._record_bundle_usage(bundle_code, user_id, booking_id)
                return
            raise BadRequestException("Invalid coupon code")
        per_user = coupon.get("usage_limit_per_user", 1)
        per_user = 1 if per_user is None else int(per_user)
        claimed = await self.user_counters.claim(
            coupon_id, user_id, per_user, lambda: self.usage_repo.count_for_user(coupon_id, user_id)
        )
        if not claimed:
            raise BadRequestException("You have already used this coupon the maximum number of times")
        result = await self.repo.collection.update_one(
            {"_id": self.repo._oid(coupon_id), "$expr": self._UNDER_TOTAL_LIMIT},
            {"$inc": {"total_used": 1}},
        )
        if result.matched_count == 0:
            await self.user_counters.release(coupon_id, user_id)
            raise BadRequestException("This coupon has just reached its usage limit — please try a different code.")
        await self.usage_repo.create({"coupon_id": coupon_id, "user_id": user_id, "booking_id": booking_id})

    async def reverse_usage(self, coupon_code: str, user_id: str, booking_id: str) -> None:
        """Undoes record_usage — called when a coupon-paid booking is
        cancelled, so the coupon's capacity and the customer's own
        per-user allowance aren't permanently burned by a booking that
        never actually happened. Safe to call even if no usage row exists
        (e.g. the booking predates this fix, or usage was never recorded)."""
        coupon = await self.repo.find_by_code(coupon_code)
        if not coupon:
            return
        if coupon.get("_bundle_id"):
            await self._reverse_bundle_usage(coupon, user_id, booking_id)
            return
        deleted = await self.usage_repo.collection.delete_one({"coupon_id": str(coupon["_id"]), "user_id": user_id, "booking_id": booking_id})
        if deleted.deleted_count:
            await self.repo.collection.update_one({"_id": coupon["_id"]}, {"$inc": {"total_used": -1}})
            await self.user_counters.release(str(coupon["_id"]), user_id)

    # ------------------------------------------------------------------
    # Bundle codes. Spent with three guarded writes, in this order, each
    # undone by the one before it when a later one is refused:
    #   1. the usage row, keyed `{code}:{booking}` — insert-first, so the
    #      same booking recording twice (a retry) is a no-op, never a
    #      second use;
    #   2. the customer's own allowance counter (usage_limit_per_user);
    #   3. the code's `used_count`, a single update guarded by
    #      `used_count < uses_per_coupon` — of any number of parallel
    #      requests for the last use exactly one matches.
    # ------------------------------------------------------------------

    async def _record_bundle_usage(self, coupon: dict, user_id: str, booking_id: str) -> None:
        code_id = str(coupon["_id"])
        limit = int(coupon["total_usage_limit"])
        per_user = int(coupon.get("usage_limit_per_user") or 1)
        now = datetime.now(timezone.utc)
        row_id = f"{code_id}:{booking_id}"
        try:
            await self.usage_repo.collection.insert_one({
                "_id": row_id, "coupon_id": code_id, "user_id": user_id, "booking_id": booking_id,
                "bundle_id": coupon["_bundle_id"], "created_at": now, "is_deleted": False,
            })
        except DuplicateKeyError:
            return  # this booking already holds the use
        # Bundle codes have no pre-counter history: a missing counter starts
        # at 0 (seeding it from usage rows would count the rows parallel
        # claims inserted a moment ago, since the row is written first).
        async def _no_history() -> int:
            return 0

        claimed = await self.user_counters.claim(code_id, user_id, per_user, _no_history)
        if not claimed:
            await self.usage_repo.collection.delete_one({"_id": row_id})
            raise BadRequestException("You have already used this coupon the maximum number of times")
        result = await self.codes.collection.update_one(
            {"_id": coupon["_id"], "used_count": {"$lt": limit}},
            {"$inc": {"used_count": 1}, "$set": {
                "last_used_at": now, "last_use": {"user_id": user_id, "booking_id": booking_id, "at": now},
            }},
        )
        if result.matched_count == 0:
            await self.user_counters.release(code_id, user_id)
            await self.usage_repo.collection.delete_one({"_id": row_id})
            raise BadRequestException(BUNDLE_USED)

    async def _reverse_bundle_usage(self, coupon: dict, user_id: str, booking_id: str) -> None:
        """Gives a bundle code's use back. The usage row's delete is the
        gate: only the call that actually removed it hands the use back, so
        a cancel + a sweep (or a replay) can never restore it twice."""
        code_id = str(coupon["_id"])
        deleted = await self.usage_repo.collection.delete_one({"coupon_id": code_id, "user_id": user_id, "booking_id": booking_id})
        if not deleted.deleted_count:
            return
        await self.codes.collection.update_one({"_id": coupon["_id"], "used_count": {"$gt": 0}}, {"$inc": {"used_count": -1}})
        await self.user_counters.release(code_id, user_id)
        # "Last used" follows the newest use still standing (none = unused).
        latest = await self.usage_repo.collection.find_one({"coupon_id": code_id}, sort=[("created_at", -1)])
        if latest:
            await self.codes.collection.update_one({"_id": coupon["_id"]}, {"$set": {
                "last_used_at": latest["created_at"],
                "last_use": {"user_id": latest["user_id"], "booking_id": latest["booking_id"], "at": latest["created_at"]},
            }})
        else:
            await self.codes.collection.update_one({"_id": coupon["_id"]}, {"$set": {"last_used_at": None, "last_use": None}})
