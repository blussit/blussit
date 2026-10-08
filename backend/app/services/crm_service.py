from motor.motor_asyncio import AsyncIOMotorDatabase

from app.core.authz import customers_known_to_center, ensure_customer_in_scope
from app.core.exceptions import NotFoundException
from app.models.enums import UserRole
from app.repositories.address_repository import AddressRepository
from app.repositories.booking_repository import BookingRepository
from app.repositories.catalog_repository import ServiceRepository
from app.repositories.complaint_repository import ComplaintRepository
from app.repositories.service_center_repository import ServiceCenterRepository
from app.repositories.subscription_repository import SubscriptionPlanRepository, UserSubscriptionRepository
from app.repositories.user_repository import UserRepository
from app.repositories.vehicle_repository import VehicleRepository
from app.repositories.vehicle_type_repository import VehicleTypeRepository
from app.schemas.user_schema import UserPublic
from app.services.subscription_service import _with_effective_statuses
from app.utils.serializers import serialize_list


class CRMService:
    """Builds a 360-degree customer profile view as required by the PRD's CRM module."""

    def __init__(self, db: AsyncIOMotorDatabase):
        self.user_repo = UserRepository(db)
        self.booking_repo = BookingRepository(db)
        self.subscription_repo = UserSubscriptionRepository(db)
        self.plan_repo = SubscriptionPlanRepository(db)
        self.complaint_repo = ComplaintRepository(db)
        self.center_repo = ServiceCenterRepository(db)
        self.vehicle_repo = VehicleRepository(db)
        self.address_repo = AddressRepository(db)
        self.service_repo = ServiceRepository(db)
        self.vehicle_type_repo = VehicleTypeRepository(db)

    async def find_customer_by_phone(
        self, phone: str, actor_role: str = "admin", actor_center_id: str | None = None,
    ) -> dict | None:
        """Used by the manager 'book on behalf of a customer' flow. Never
        leaks a staff account via a phone search — customers only. Same
        shape rule as search_customers' full-number match: a manager gets
        only what booking needs (id, name, phone), never the email or
        account flags of a customer their center may never have served;
        an unlinked manager gets nothing."""
        if actor_role != "admin" and not actor_center_id:
            return None
        user = await self.user_repo.find_by_phone(phone)
        if not user or user.get("role") != UserRole.CUSTOMER.value or user.get("is_deleted"):
            return None
        if actor_role != "admin":
            return self._lookup_row(user)
        return UserPublic.from_doc(user).model_dump()

    # How many name matches a manager's typeahead looks through before
    # keeping only their own center's customers — bounds the work per
    # keystroke (a common name can match thousands platform-wide).
    _MANAGER_TYPEAHEAD_CANDIDATES = 200

    async def search_customers(
        self, q: str, limit: int = 8, actor_role: str = "admin", actor_center_id: str | None = None,
    ) -> list[dict]:
        """Typeahead for the manager's 'add a booking' / 'sell a plan' forms
        and the header "Find customer" box — matching by name OR phone as
        they type, so an existing customer is picked directly instead of
        accidentally re-created. Customers only, same rule as
        find_customer_by_phone above.

        Scope (decided 2026-10, final security pass): customers aren't
        owned by a center, but a manager has no business BROWSING the whole
        platform's customer list by name. So for a manager:
          - a full 10-digit number is matched platform-wide — the caller is
            on the phone with that number in hand, and finding the existing
            account (not creating a duplicate) is the whole point;
          - a name or a partial number only matches customers already
            known to THEIR center: a booking there, a plan granted there, or
            a society enrolment in one of its societies;
          - either way only what booking needs comes back (id, name, phone)
            — no email, no account flags.
        An admin searches everything and sees the full public profile."""
        from app.repositories.base_repository import build_search_filter

        q = (q or "").strip()
        if q and all(ch.isdigit() or ch in " +-" for ch in q):
            # A pasted "+91 98765 43210" must still find the stored 10 digits.
            q = "".join(ch for ch in q if ch.isdigit())
            if len(q) == 12 and q.startswith("91"):
                q = q[2:]
        if len(q) < 2:
            return []
        base = {"role": UserRole.CUSTOMER.value, "is_deleted": {"$ne": True}}
        if actor_role == "admin":
            # No count_documents here (find_many would run one over every
            # customer per keystroke) — a typeahead only needs the first few.
            query = {**base, **build_search_filter(q, ["full_name", "email", "phone"])}
            items = await self.user_repo.collection.find(query).sort("created_at", -1).limit(limit).to_list(length=limit)
            return [UserPublic.from_doc(u).model_dump() for u in items]

        if not actor_center_id:
            return []  # fail closed — an unlinked manager has no customers
        if len(q) == 10 and q.isdigit():
            items = await self.user_repo.collection.find({**base, "phone": q}).limit(1).to_list(length=1)
            return [self._lookup_row(u) for u in items]
        query = {**base, **build_search_filter(q, ["full_name", "phone"])}
        candidates = await self.user_repo.collection.find(query, {"full_name": 1, "phone": 1, "created_at": 1}).sort(
            "created_at", -1
        ).limit(self._MANAGER_TYPEAHEAD_CANDIDATES).to_list(length=self._MANAGER_TYPEAHEAD_CANDIDATES)
        if not candidates:
            return []
        known = await self._known_to_center([str(u["_id"]) for u in candidates], actor_center_id)
        return [self._lookup_row(u) for u in candidates if str(u["_id"]) in known][:limit]

    @staticmethod
    def _lookup_row(user: dict) -> dict:
        return {
            "id": str(user["_id"]), "full_name": user.get("full_name") or "", "phone": user.get("phone"),
            "role": UserRole.CUSTOMER.value,
        }

    async def _known_to_center(self, customer_ids: list[str], center_id: str) -> set[str]:
        """Which of these customers this center has dealt with — the shared
        rule in core/authz (customers_known_to_center)."""
        return await customers_known_to_center(self.user_repo.collection.database, customer_ids, center_id)

    async def get_customer_360(self, customer_id: str, actor_role: str | None = None, actor_center_id: str | None = None) -> dict:
        user = await self.user_repo.find_by_id(customer_id)
        # Customers only — same rule as find_customer_by_phone above. Without
        # this, passing a STAFF user id here dumped that staff member's
        # profile through a customer-CRM endpoint.
        if not user or user.get("role") != UserRole.CUSTOMER.value:
            raise NotFoundException("Customer not found")
        # A manager may open only a customer THEIR center has dealt with
        # (same relationship rule as the typeahead's name search). The
        # narrowing further down scopes bookings/plans/complaints, but the
        # profile, every saved address and every vehicle went out for ANY
        # customer id — and an unlinked manager fell open to all of them.
        # 404, not 403: an id outside the center must not confirm it exists.
        await ensure_customer_in_scope(self.user_repo.collection.database, actor_role or "", actor_center_id, customer_id)

        # High page_size, not the usual UI-page 20/50 — lifetime_spend,
        # total_bookings and same_day_repeat_dates below all need the
        # customer's REAL full history, not a truncated recent slice (this
        # silently under-counted a loyal repeat customer's lifetime value
        # before — found while adding those two fields).
        bookings, _ = await self.booking_repo.list_for_customer(customer_id, None, 1, 2000)
        # _with_effective_statuses corrects for expiry-by-date even when the
        # stored `status` hasn't been lazily flipped yet — without it this
        # view's plan badges read raw `status` and can show a stale "active"
        # (or, with no `effective_status` key at all, "Unknown") on a plan
        # that's actually expired.
        subscriptions = _with_effective_statuses(await self.subscription_repo.list_for_customer(customer_id))
        complaints, _ = await self.complaint_repo.list_for_customer(customer_id, 1, 50)

        # A manager only ever reaches a customer through THEIR OWN center's
        # own booking/plan lists — without this, everything below (spend,
        # dates, what was bought, where) was this customer's FULL history
        # platform-wide, leaking a different center's activity to a
        # manager with no reason to see it. Admin is unrestricted (oversees
        # every center) — this only narrows the manager path, and it
        # narrows the SOURCE lists so every figure derived below (lifetime
        # spend, same-day-repeat, preferred center) is naturally correct
        # for the scoped view too, not just the raw lists.
        # Any non-admin is narrowed — a manager with NO center linked sees
        # nothing center-owned (None matches no booking), never the full
        # platform-wide history an `and actor_center_id` guard fell open to.
        if actor_role != "admin":
            bookings = [b for b in bookings if actor_center_id and b.get("service_center_id") == actor_center_id]
            # A self-serve plan has no service_center_id at all (no center
            # owns it) — still shown, since a manager legitimately needs to
            # know a customer's own active pass regardless of who sold it.
            # Only a plan explicitly granted by a DIFFERENT center is hidden.
            subscriptions = [s for s in subscriptions if s.get("service_center_id") in (None, actor_center_id)]
            own_booking_ids = {str(b["_id"]) for b in bookings}
            complaints = [c for c in complaints if c.get("booking_id") in own_booking_ids]

        completed_bookings = [b for b in bookings if b["status"] == "completed"]
        lifetime_spend = sum(b.get("total_amount", 0) for b in completed_bookings)
        last_service_date = max((b["scheduled_date"] for b in completed_bookings), default=None)

        # Plan spend is tracked separately from booking spend (a plan's
        # amount_paid is charged once, up front — never per-wash), but the
        # combined figure is what "how much has this customer actually
        # brought in" really means.
        lifetime_plan_spend = 0.0
        for s in subscriptions:
            paid = s.get("amount_paid")
            if paid is None and not s.get("service_center_id"):
                paid = s.get("purchased_price")
            lifetime_plan_spend += float(paid or 0)

        # Same-CALENDAR-DAY (IST) repeat bookings — a founder-requested flag,
        # since two bookings from one customer on one day is worth a manual
        # look (an accidental double-book, or a genuinely busy customer).
        from collections import Counter

        from app.utils.timezone import from_stored

        day_counts = Counter(from_stored(b["created_at"]).strftime("%Y-%m-%d") for b in bookings if b.get("created_at"))
        same_day_repeat_dates = sorted(day for day, count in day_counts.items() if count > 1)

        center_counts: dict[str, int] = {}
        for b in bookings:
            center_counts[b["service_center_id"]] = center_counts.get(b["service_center_id"], 0) + 1
        preferred_center_id = max(center_counts, key=center_counts.get) if center_counts else None

        vehicles = await self.vehicle_repo.list_by_owner(customer_id)
        addresses = await self.address_repo.list_by_owner(customer_id)

        # Display names for the admin/manager detail view below — resolved
        # once here rather than making the frontend chase
        # service_ids/address_id/vehicle_type/plan_id itself. Deliberately
        # NOT reusing BookingService._enrich_bookings (a private helper
        # scoped to that service) — same "resolve locally" call as
        # ReviewService._enrich makes, for the same reason.
        service_ids = {sid for b in bookings for sid in (b.get("service_ids") or [])}
        # A monthly pass names one car type + one wash — shown on each plan row.
        service_ids.update(s.get("service_id") for s in subscriptions if s.get("service_id"))
        # A booking shows its own address_snapshot (spec 1.3); only rows from
        # before snapshots fall back to the saved address.
        booking_address_ids = {b.get("address_id") for b in bookings if b.get("address_id") and not b.get("address_snapshot")}
        vehicle_type_ids = {b.get("vehicle_type") for b in bookings if b.get("vehicle_type")}
        vehicle_type_ids.update(v.get("vehicle_type") for v in vehicles if v.get("vehicle_type"))
        vehicle_type_ids.update(s.get("vehicle_type") for s in subscriptions if s.get("vehicle_type"))
        center_ids = {b.get("service_center_id") for b in bookings if b.get("service_center_id")}
        plan_ids = {s.get("plan_id") for s in subscriptions if s.get("plan_id")}

        services = {str(s["_id"]): s.get("name") for s in await self.service_repo.find_by_ids(list(service_ids))} if service_ids else {}
        booking_addresses = {str(a["_id"]): a for a in await self.address_repo.find_by_ids(list(booking_address_ids))} if booking_address_ids else {}
        vehicle_type_names = {str(v["_id"]): v.get("name") for v in await self.vehicle_type_repo.find_by_ids(list(vehicle_type_ids))} if vehicle_type_ids else {}
        centers = {str(c["_id"]): c.get("name") for c in await self.center_repo.find_by_ids(list(center_ids))} if center_ids else {}
        plans = {str(p["_id"]): p.get("name") for p in await self.plan_repo.find_by_ids(list(plan_ids))} if plan_ids else {}

        enriched_bookings = []
        for b in bookings:
            addr = b.get("address_snapshot") or booking_addresses.get(b.get("address_id") or "")
            enriched_bookings.append({
                **b,
                "vehicle_label": b.get("vehicle_label") or vehicle_type_names.get(b.get("vehicle_type") or "") or "Vehicle",
                "vehicle_type_name": vehicle_type_names.get(b.get("vehicle_type") or "") or b.get("vehicle_label"),
                "service_names": [services.get(sid, "Service") for sid in (b.get("service_ids") or [])],
                "address_text": f"{addr.get('line1')}, {addr.get('city')}" if addr else None,
                "service_center_name": centers.get(b.get("service_center_id") or ""),
            })
        enriched_vehicles = [{**v, "vehicle_type_name": vehicle_type_names.get(v.get("vehicle_type") or "")} for v in vehicles]
        enriched_subscriptions = [
            {
                **s,
                "plan_name": plans.get(s.get("plan_id") or "", "Unknown plan"),
                "vehicle_type_name": vehicle_type_names.get(s.get("vehicle_type") or ""),
                "service_name": services.get(s.get("service_id") or ""),
            }
            for s in subscriptions
        ]

        return {
            "profile": UserPublic.from_doc(user).model_dump(),
            "bookings": serialize_list(enriched_bookings),
            "subscriptions": serialize_list(enriched_subscriptions),
            "complaints": serialize_list(complaints),
            "vehicles": serialize_list(enriched_vehicles),
            "addresses": serialize_list(addresses),
            "lifetime_spend": round(lifetime_spend, 2),
            "lifetime_plan_spend": round(lifetime_plan_spend, 2),
            "lifetime_total_spend": round(lifetime_spend + lifetime_plan_spend, 2),
            "last_service_date": last_service_date.isoformat() if last_service_date else None,
            "preferred_service_center_id": preferred_center_id,
            "preferred_service_center_name": centers.get(preferred_center_id or ""),
            "total_bookings": len(bookings),
            "same_day_repeat_dates": same_day_repeat_dates,
        }
