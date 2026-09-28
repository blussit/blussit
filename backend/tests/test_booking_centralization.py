"""
One booking system, many doors: the website (guest + signed in), the manager
panel (book / log a done job), the older API shapes and the WhatsApp bot all
price, plan, park and list bookings through the same code.

  - POST /bookings/quote (BookingService.quote_visit) shows exactly what the
    booking then charges: first-wash price by phone (first car only), the
    customer's pass, the coupon (first car), the distance charge (once) and
    the prepaid rule.
  - The older entry points (POST /bookings, /bookings/group, manager-create)
    apply the customer's pass and send the pay link like /bookings/quick.
  - An anonymous booking refused for what/where/when leaves its one-time
    code unspent for the corrected retry.
  - The manager queue filters/pages on the server, so an old pending job is
    never lost behind the newest 100.
  - The bot lists services from the live catalogue and its confirm total is
    the booking's total.

Razorpay is always stubbed (never the real API).
"""
import itertools
from datetime import datetime, timedelta, timezone

import pytest
from bson import ObjectId

from app.controllers.booking_controller import BookingController
from app.core.dependencies import CurrentUser
from app.core.exceptions import BadRequestException
from app.models.enums import BookingStatus
from app.schemas.booking_schema import (
    BookingCreateRequest,
    BookingGroupCreateRequest,
    BookingQuoteRequest,
    GroupVehicleRequest,
    ManagerBookingCreateRequest,
    ManagerLogBookingRequest,
    QuickAddress,
    QuickBookingLine,
    QuickBookingRequest,
)
from app.services import payment_service
from app.services.booking_service import BookingService, center_queue_filters
from app.utils.timezone import now_ist
from tests.factories import get_hatchback_type_id, make_customer, make_manager, make_service_center

pytestmark = pytest.mark.asyncio

# Far from every other test center, so the nearest-center lookup lands here.
CENTER_LAT, CENTER_LNG = 26.1, 80.2
PINCODE = "452277"
KM_PER_DEG_LAT = 111.195
STUB_LINK = "https://rzp.io/l/central-stub"
_seq = itertools.count(1)


class _StubLinks:
    def create(self, payload):
        return {"id": f"plink_central_{next(_seq):06d}", "short_url": STUB_LINK, **payload}

    def cancel(self, link_id):
        return {"id": link_id, "status": "cancelled"}


class _StubOrders:
    def create(self, payload):
        return {"id": f"order_central_{next(_seq):06d}", **payload}


class _StubClient:
    order = _StubOrders()
    payment_link = _StubLinks()


@pytest.fixture
def gateway(monkeypatch):
    monkeypatch.setattr(payment_service, "_razorpay_client", lambda: _StubClient())


def _day(offset: int) -> str:
    return (now_ist() + timedelta(days=offset)).strftime("%Y-%m-%d")


async def _service(db, cleanup, name: str, price: float, **fields) -> str:
    category = await db.categories.find_one({"slug": "car-care"})
    result = await db.services.insert_one({
        "category_id": str(category["_id"]), "name": name, "slug": f"central-test-{next(_seq)}",
        "vehicle_types": [], "price": price, "discounted_price": None,
        "vehicle_type_prices": {}, "vehicle_type_discounted_prices": {},
        "duration_minutes": 30, "is_addon": False, "is_active": True, "is_deleted": False,
        "display_order": 0,
        **fields,
    })
    cleanup.append(("services", {"_id": result.inserted_id}))
    return str(result.inserted_id)


@pytest.fixture
async def rig(db, cleanup):
    center_id = await make_service_center(db, pincode=PINCODE)
    await db.service_centers.update_one(
        {"_id": ObjectId(center_id)},
        {"$set": {"location.latitude": CENTER_LAT, "location.longitude": CENTER_LNG, "location.radius_km": 25.0}},
    )
    manager_id = await make_manager(db, center_id)
    await db.service_centers.update_one({"_id": ObjectId(center_id)}, {"$set": {"manager_id": manager_id}})
    for coll, flt in (
        ("slot_capacity", {"service_center_id": center_id}), ("daily_capacity", {"service_center_id": center_id}),
        ("slot_holds", {"service_center_id": center_id}), ("bookings", {"service_center_id": center_id}),
        ("notifications", {"user_id": manager_id}), ("users", {"_id": ObjectId(manager_id)}),
        ("service_centers", {"_id": ObjectId(center_id)}),
    ):
        cleanup.append((coll, flt))
    return {
        "db": db,
        "center_id": center_id,
        "manager_id": manager_id,
        "hatchback": await get_hatchback_type_id(db),
        # ₹200 for a first-time phone, ₹300 after.
        "first": await _service(db, cleanup, "Central First Wash", 300.0, discounted_price=200.0, original_price=450.0),
        "travel": await _service(db, cleanup, "Central Travel Wash", 300.0, charges_travel=True),
        "offer": await _service(db, cleanup, "Central Offer Wash", 149.0, prepaid_only=True, charges_travel=True, offer_tag="Launch offer", original_price=299.0),
        "plain": await _service(db, cleanup, "Central Plain Wash", 200.0),
    }


async def _customer(rig, cleanup, km: float = 7.4) -> tuple[dict, str]:
    """A customer with one pinned address `km` north of the rig center."""
    db = rig["db"]
    customer_id = await make_customer(db)
    address = await db.addresses.insert_one({
        "owner_id": customer_id, "label": "Home", "line1": f"Central Street {next(_seq)}", "city": "Indore", "state": "MP",
        "pincode": PINCODE, "latitude": CENTER_LAT + km / KM_PER_DEG_LAT, "longitude": CENTER_LNG,
        "is_default": True, "is_deleted": False,
    })
    for coll, flt in (
        ("bookings", {"customer_id": customer_id}), ("payment_orders", {"customer_id": customer_id}),
        ("notifications", {"user_id": customer_id}), ("addresses", {"owner_id": customer_id}),
        ("user_subscriptions", {"customer_id": customer_id}), ("coupon_usages", {"user_id": customer_id}),
        ("purchase_confirmations", {"customer_id": customer_id}), ("users", {"_id": ObjectId(customer_id)}),
    ):
        cleanup.append((coll, flt))
    return await db.users.find_one({"_id": ObjectId(customer_id)}), str(address.inserted_id)


async def _pass(rig, customer: dict, service_key: str, remaining: int = 3) -> str:
    db = rig["db"]
    plan = await db.subscription_plans.find_one({"is_active": True, "is_deleted": {"$ne": True}})
    now = now_ist()
    res = await db.user_subscriptions.insert_one({
        "customer_id": str(customer["_id"]), "plan_id": str(plan["_id"]), "service_id": rig[service_key],
        "vehicle_type": rig["hatchback"], "status": "active", "remaining_service_count": remaining, "total_service_count": 4,
        "start_date": now - timedelta(days=1), "end_date": now + timedelta(days=20),
        "is_deleted": False, "created_at": now, "updated_at": now,
    })
    return str(res.inserted_id)


async def _slot(rig, day: str) -> str:
    slots = await BookingService(rig["db"]).available_slots(rig["center_id"], day)
    slot = next((s["key"] for s in slots if s["status"] == "available"), None)
    if slot is None:
        pytest.skip(f"no bookable slot left for {day}")
    return slot


def _line(rig, key: str, quantity: int = 1, use_subscription: bool = True) -> QuickBookingLine:
    return QuickBookingLine(vehicle_type=rig["hatchback"], quantity=quantity, service_ids=[rig[key]], use_subscription=use_subscription)


async def _quote(rig, customer: dict | None, lines, address_id: str | None = None, **kw) -> dict:
    address = await rig["db"].addresses.find_one({"_id": ObjectId(address_id)}) if address_id else None
    return await BookingService(rig["db"]).quote_visit(
        customer_id=str(customer["_id"]) if customer else None,
        phone=customer["phone"] if customer else kw.pop("phone", None),
        lines=lines, address=address, **kw,
    )


async def _book(rig, customer: dict, address_id: str, lines, *, method="cash", source="app", coupon=None, day=2) -> dict:
    date = _day(day)
    return await BookingService(rig["db"]).create_quick_booking(
        QuickBookingRequest(
            customer_name=customer["full_name"], customer_phone=customer["phone"], address_id=address_id,
            lines=lines, scheduled_date=date, scheduled_slot=await _slot(rig, date), payment_method=method, coupon_code=coupon,
        ),
        customer=customer, source=source, allow_pinless=True, notify_background=False,
    )


# --------------------------------------------------------------- the quote


async def test_quote_equals_the_booking_first_wash_on_the_first_car_only(rig, cleanup):
    customer, address_id = await _customer(rig, cleanup)
    lines = [_line(rig, "first", quantity=2)]
    quote = await _quote(rig, customer, lines, address_id)
    assert quote["first_time_eligible"] is True
    # Cars are priced in order: only the first gets the first-wash price.
    assert quote["subtotal"] == 500.0 and quote["regular_subtotal"] == 600.0 and quote["first_time_savings"] == 100.0
    assert quote["total_amount"] == 500.0 and quote["online_only"] is False

    booked = await _book(rig, customer, address_id, lines)
    assert booked["total_amount"] == quote["total_amount"]

    # The phone has booked now — the next quote (and booking) is regular.
    again = await _quote(rig, customer, [_line(rig, "first")], address_id)
    assert again["first_time_eligible"] is False and again["total_amount"] == 300.0
    rebooked = await _book(rig, customer, address_id, [_line(rig, "first")], day=3)
    assert rebooked["total_amount"] == 300.0


async def test_quote_applies_pass_and_one_distance_charge_like_the_booking(rig, cleanup):
    customer, address_id = await _customer(rig, cleanup)
    await _pass(rig, customer, "travel", remaining=1)
    lines = [_line(rig, "travel", quantity=2)]
    quote = await _quote(rig, customer, lines, address_id)
    row = quote["lines"][0]
    assert row["plan_covered"] == 1 and quote["plan_discount"] == 300.0
    assert quote["travel"]["charge"] == 5 and quote["travel_charge"] == 5.0
    assert quote["total_amount"] == 305.0

    booked = await _book(rig, customer, address_id, lines)
    assert booked["total_amount"] == quote["total_amount"]
    assert sum(1 for b in booked["bookings"] if b.get("subscription_id")) == 1

    # A line whose "use my plan" switch is off claims no pass — quote agrees.
    off = await _quote(rig, customer, [_line(rig, "plain", use_subscription=False)], address_id)
    assert off["plan_discount"] == 0.0 and off["total_amount"] == 200.0


async def test_quote_prepaid_offer_is_online_only_and_log_mode_is_exempt(rig, cleanup):
    customer, address_id = await _customer(rig, cleanup)
    quote = await _quote(rig, customer, [_line(rig, "offer")], address_id)
    assert quote["prepaid_service"] == "Central Offer Wash" and quote["online_only"] is True
    assert quote["total_amount"] == 154.0  # ₹149 + the distance charge

    # No address yet: the charge can't be measured — the quote says so.
    early = await _quote(rig, customer, [_line(rig, "offer")])
    assert early["travel"] is None and early["travel_pending"] is True

    logged = await _quote(rig, customer, [_line(rig, "offer")], address_id, log_mode=True)
    assert logged["online_only"] is False and logged["travel"] is None and logged["total_amount"] == 149.0


async def test_quote_coupon_rides_on_the_first_car_and_a_bad_code_is_reported(rig, cleanup):
    db = rig["db"]
    customer, address_id = await _customer(rig, cleanup)
    code = f"CENT{next(_seq)}"
    now = datetime.now(timezone.utc)
    await db.coupons.insert_one({
        "code": code, "description": "test", "coupon_type": "percentage", "value": 10.0, "min_order_value": 0.0,
        "max_discount_amount": None, "usage_limit_per_user": 5, "total_usage_limit": None, "total_used": 0,
        "valid_from": now - timedelta(days=1), "valid_until": now + timedelta(days=5), "is_active": True,
        "offer_kind": "standard", "is_deleted": False,
    })
    cleanup.append(("coupons", {"code": code}))
    lines = [_line(rig, "plain", quantity=2)]
    quote = await _quote(rig, customer, lines, address_id, coupon_code=code)
    assert quote["coupon_code"] == code and quote["coupon_discount"] == 20.0  # 10% of the first car only
    assert quote["total_amount"] == 380.0

    booked = await _book(rig, customer, address_id, lines, coupon=code)
    assert booked["total_amount"] == quote["total_amount"]

    bad = await _quote(rig, customer, lines, address_id, coupon_code="NOPE123")
    assert bad["coupon_error"] and bad["coupon_discount"] == 0.0 and bad["total_amount"] == 400.0


async def test_quote_endpoint_scopes_plans_and_addresses_to_the_caller(rig, cleanup):
    db = rig["db"]
    customer, address_id = await _customer(rig, cleanup)
    await _pass(rig, customer, "plain")
    controller = BookingController(db)
    body = {"lines": [{"vehicle_type": rig["hatchback"], "quantity": 1, "service_ids": [rig["plain"]]}], "customer_phone": customer["phone"]}

    as_customer = (await controller.quote(CurrentUser(str(customer["_id"]), "customer"), BookingQuoteRequest(**body)))["data"]
    assert as_customer["plan_discount"] == 200.0 and as_customer["total_amount"] == 0.0

    manager = CurrentUser(rig["manager_id"], "manager", service_center_id=rig["center_id"])
    as_manager = (await controller.quote(manager, BookingQuoteRequest(**body, address_id=address_id)))["data"]
    assert as_manager["plan_discount"] == 200.0

    # A stranger typing someone's number learns nothing about their plans,
    # and can't borrow their saved address.
    anonymous = (await controller.quote(None, BookingQuoteRequest(**body, address_id=address_id)))["data"]
    assert anonymous["plan_discount"] == 0.0 and anonymous["total_amount"] == 200.0 and anonymous["travel"] is None

    # A half-typed number is "not known yet", never a 422.
    assert BookingQuoteRequest(**{**body, "customer_phone": "98"}).customer_phone is None


# ---------------------------------------------------------- older entry points


async def test_manager_create_applies_the_customers_pass_like_manager_quick(rig, cleanup):
    customer, address_id = await _customer(rig, cleanup)
    sub_id = await _pass(rig, customer, "plain")
    date = _day(2)
    result = await BookingService(rig["db"]).create_booking_for_customer(
        rig["manager_id"],
        ManagerBookingCreateRequest(
            customer_id=str(customer["_id"]), vehicle_type=rig["hatchback"], address_id=address_id,
            service_ids=[rig["plain"]], scheduled_date=date, scheduled_slot=await _slot(rig, date),
        ),
    )
    assert result["subscription_id"] == sub_id and result["total_amount"] == 0.0
    assert result["awaiting_payment"] is False and result["payment_link"] is None


async def test_self_service_booking_parks_a_prepaid_visit_with_a_link(rig, cleanup, gateway):
    customer, address_id = await _customer(rig, cleanup)
    date = _day(2)
    request = BookingCreateRequest(
        vehicle_type=rig["hatchback"], address_id=address_id, service_ids=[rig["offer"]],
        scheduled_date=datetime.strptime(date, "%Y-%m-%d"), scheduled_slot=await _slot(rig, date), payment_method="online",
    )
    result = await BookingService(rig["db"]).create_self_service_booking(str(customer["_id"]), request)
    assert result["status"] == BookingStatus.AWAITING_PAYMENT.value
    assert result["awaiting_payment"] is True and result["payment_link"] == STUB_LINK
    assert result["service_code"] and result["prepaid_only"] is True

    with pytest.raises(BadRequestException, match="prepaid"):
        await BookingService(rig["db"]).create_self_service_booking(str(customer["_id"]), request.model_copy(update={"payment_method": "cash"}))


async def test_self_service_group_gives_each_car_its_own_pass(rig, cleanup):
    customer, address_id = await _customer(rig, cleanup)
    await _pass(rig, customer, "plain", remaining=1)
    date = _day(2)
    result = await BookingService(rig["db"]).create_self_service_group(
        str(customer["_id"]),
        BookingGroupCreateRequest(
            vehicles=[GroupVehicleRequest(vehicle_type=rig["hatchback"], quantity=2, service_ids=[rig["plain"]])],
            address_id=address_id, scheduled_date=date, scheduled_slot=await _slot(rig, date), payment_method="cash",
        ),
    )
    assert result["vehicle_count"] == 2
    assert sorted(float(b["total_amount"]) for b in result["bookings"]) == [0.0, 200.0]
    assert result["awaiting_payment"] is False


# ------------------------------------------------ the anonymous one-time code


async def test_a_refused_anonymous_booking_leaves_the_code_usable(rig, cleanup, gateway):
    db = rig["db"]
    phone, code = "9555510077", "246810"
    now = datetime.now(timezone.utc)
    await db.otp_requests.insert_one({
        "_id": f"otp:{phone}", "identifier": phone, "otp": code, "purpose": "booking",
        "expires_at": now + timedelta(minutes=10), "last_sent_at": now, "attempts": 0, "verified": False,
    })
    for coll, flt in (("otp_requests", {"identifier": phone}), ("bookings", {"customer_phone": phone}), ("users", {"phone": phone})):
        cleanup.append((coll, flt))
    date = _day(2)
    body = dict(
        customer_name="Guest Retry", customer_phone=phone,
        address=QuickAddress(line1=f"Guest Lane {next(_seq)}", pincode=PINCODE, latitude=CENTER_LAT + 0.01, longitude=CENTER_LNG),
        lines=[_line(rig, "offer")], scheduled_date=date, scheduled_slot=await _slot(rig, date), phone_otp=code,
    )
    controller = BookingController(db)

    with pytest.raises(BadRequestException, match="prepaid"):
        await controller.quick_create(None, QuickBookingRequest(**body, payment_method="cash"))
    with pytest.raises(BadRequestException, match="isn't available"):
        await controller.quick_create(None, QuickBookingRequest(**{**body, "scheduled_slot": "03:00-04:00"}, payment_method="online"))
    service = BookingService(db)
    await service.set_slot_capacity(rig["center_id"], date, body["scheduled_slot"], capacity=None, is_closed=True)
    with pytest.raises(BadRequestException, match="closed"):
        await controller.quick_create(None, QuickBookingRequest(**body, payment_method="online"))
    await service.set_slot_capacity(rig["center_id"], date, body["scheduled_slot"], capacity=None, is_closed=False)
    record = await db.otp_requests.find_one({"_id": f"otp:{phone}"})
    assert record is not None and record["attempts"] == 0  # never even checked
    assert await db.users.find_one({"phone": phone}) is None  # no profile for an unproven phone

    ok = (await controller.quick_create(None, QuickBookingRequest(**body, payment_method="online")))["data"]
    cleanup.append(("payment_orders", {"customer_id": ok["customer_id"]}))
    assert ok["awaiting_payment"] is True and ok["payment_link"] == STUB_LINK
    assert await db.otp_requests.find_one({"_id": f"otp:{phone}"}) is None  # spent by the booking that went through
    addresses = await db.addresses.find({"owner_id": ok["customer_id"]}).to_list(None)
    cleanup.append(("addresses", {"owner_id": ok["customer_id"]}))
    cleanup.append(("notifications", {"user_id": ok["customer_id"]}))
    cleanup.append(("purchase_confirmations", {"customer_id": ok["customer_id"]}))
    assert len(addresses) == 1

    # A wrong code is still refused after the checks pass.
    with pytest.raises(BadRequestException):
        await controller.quick_create(None, QuickBookingRequest(**{**body, "phone_otp": "000000"}, payment_method="online"))


# ------------------------------------------------------------ manager queue


async def _raw_booking(db, center_id: str, n: int, **fields) -> None:
    now = now_ist()
    await db.bookings.insert_one({
        "booking_number": f"BKQ{n:05d}", "customer_id": fields.pop("customer_id", "c-queue"), "service_center_id": center_id,
        "status": "completed", "scheduled_date": datetime(2026, 1, 1) + timedelta(days=n % 200), "scheduled_slot": "09:00-12:00",
        "total_amount": 100.0, "created_at": now - timedelta(minutes=n), "is_deleted": False, **fields,
    })


async def test_center_queue_reaches_old_actionable_jobs_and_counts_them(rig, cleanup):
    db = rig["db"]
    center_id = rig["center_id"]
    for n in range(105):
        await _raw_booking(db, center_id, n)
    # Old, still waiting for a captain — created before all 105 above.
    await _raw_booking(db, center_id, 900, booking_number="BKQOLD01", status="pending", created_at=now_ist() - timedelta(days=40), scheduled_date=datetime(2026, 9, 1))
    await _raw_booking(db, center_id, 901, booking_number="BKQOLD02", status="assigned", issue_flag="captain_not_started", issue_resolved=False, created_at=now_ist() - timedelta(days=41))
    await _raw_booking(db, center_id, 902, booking_number="BKQOLD03", status="assigned", created_at=now_ist() - timedelta(days=42))
    service = BookingService(db)

    newest, total = await service.list_for_center(center_id, {}, 1, 100, "manager", center_id)
    assert total == 108 and "BKQOLD01" not in {b["booking_number"] for b in newest}  # the old blind spot

    queue = await center_queue_filters(db, scope="attention")
    items, total = await service.list_for_center(center_id, {}, 1, 100, "manager", center_id, queue=queue, sort="scheduled_asc")
    assert total == 2 and [b["booking_number"] for b in items] == ["BKQOLD02", "BKQOLD01"]

    active, total = await service.list_for_center(center_id, {}, 1, 100, "manager", center_id, queue=await center_queue_filters(db, scope="active"))
    assert total == 3
    waiting, total = await service.list_for_center(center_id, {}, 1, 100, "manager", center_id, queue=await center_queue_filters(db, scope="needs_captain"))
    assert total == 1 and waiting[0]["booking_number"] == "BKQOLD01"

    # Another center's manager still can't read it.
    from app.core.exceptions import ForbiddenException
    with pytest.raises(ForbiddenException):
        await service.list_for_center(center_id, {}, 1, 10, "manager", "some-other-center", queue=queue)


async def test_center_queue_search_window_sort_and_stable_pages(rig, cleanup):
    db = rig["db"]
    center_id = rig["center_id"]
    customer_id = await make_customer(db, name="Queue Findme Sharma")
    cleanup.append(("users", {"_id": ObjectId(customer_id)}))
    for n in range(30):
        await _raw_booking(db, center_id, n, customer_phone=f"98765{n:05d}", scheduled_date=datetime(2026, 10, 1 + n % 5))
    await _raw_booking(db, center_id, 77, booking_number="BK0077", customer_id=customer_id, customer_phone="9111122222")
    service = BookingService(db)

    async def search(**kw):
        return await service.list_for_center(center_id, {}, 1, 100, "manager", center_id, queue=await center_queue_filters(db, **kw))

    assert [b["booking_number"] for b in (await search(search="77"))[0]] == ["BK0077"]  # zero-padded number
    assert [b["booking_number"] for b in (await search(search="bk0077"))[0]] == ["BK0077"]
    assert [b["booking_number"] for b in (await search(search="91111"))[0]] == ["BK0077"]  # phone prefix
    assert [b["booking_number"] for b in (await search(search="findme"))[0]] == ["BK0077"]  # customer name
    _, in_window = await search(date_from="2026-10-02", date_to="2026-10-03")
    assert in_window == 12  # days 2 and 3 of the five-day spread

    # Pages over tied sort keys never repeat or drop a row.
    seen: list[str] = []
    for page in (1, 2, 3, 4):
        rows, total = await service.list_for_center(center_id, {}, page, 9, "manager", center_id, sort="scheduled_asc")
        seen.extend(b["booking_number"] for b in rows)
    assert total == 31 and len(seen) == 31 and len(set(seen)) == 31

    with pytest.raises(ValueError):
        await center_queue_filters(db, scope="everything")
    with pytest.raises(ValueError):
        await center_queue_filters(db, date_from="01-10-2026")
    with pytest.raises(BadRequestException):
        await service.list_for_center(center_id, {}, 1, 10, "manager", center_id, sort="random")


# ---------------------------------------------------------------- WhatsApp


async def test_bot_lists_live_admin_prices_offer_and_prepaid(rig, db, cleanup):
    from app.schemas.catalog_schema import ServiceUpdateRequest
    from app.services.catalog_service import ServiceCatalogService
    from app.services.whatsapp_bot_service import WhatsAppBotService
    from tests.test_whatsapp_bot import last_out, wa_payload

    customer, _ = await _customer(rig, cleanup)
    phone = customer["phone"]
    wa_id = f"91{phone}"
    cleanup.append(("whatsapp_outbox", {"phone": phone}))
    cleanup.append(("whatsapp_conversations", {"wa_id": wa_id}))
    cleanup.append(("whatsapp_inbox", {"wa_id": wa_id}))
    bot = WhatsAppBotService(db)
    await bot.handle_webhook(wa_payload(wa_id, text="hi"))
    await bot.handle_webhook(wa_payload(wa_id, reply="menu:book"))
    await bot.handle_webhook(wa_payload(wa_id, reply=f"vt:{rig['hatchback']}"))
    rows = {r["id"]: r for r in (await last_out(db, phone))["options"]}
    assert rows[f"svc:{rig['offer']}"]["description"] == "₹149 (was ₹299) · Launch offer · prepaid"
    # This phone never booked: the first-wash price is what the bot quotes.
    assert rows[f"svc:{rig['first']}"]["description"] == "₹200 (was ₹300) · first wash"

    # An admin edit shows in the very next list — no cache in between.
    await ServiceCatalogService(db).update(rig["offer"], ServiceUpdateRequest(price=129.0, offer_tag=""))
    await bot.handle_webhook(wa_payload(wa_id, text="menu"))
    await bot.handle_webhook(wa_payload(wa_id, reply="menu:book"))
    await bot.handle_webhook(wa_payload(wa_id, reply=f"vt:{rig['hatchback']}"))
    rows = {r["id"]: r for r in (await last_out(db, phone))["options"]}
    assert rows[f"svc:{rig['offer']}"]["description"] == "₹129 (was ₹299) · prepaid"


async def test_bot_confirm_total_is_the_booking_total_with_a_plan(rig, db, cleanup):
    from app.services.whatsapp_bot_service import WhatsAppBotService
    from tests.test_whatsapp_bot import last_out, wa_payload

    customer, address_id = await _customer(rig, cleanup)
    await _pass(rig, customer, "travel")
    phone = customer["phone"]
    wa_id = f"91{phone}"
    cleanup.append(("whatsapp_outbox", {"phone": phone}))
    cleanup.append(("whatsapp_conversations", {"wa_id": wa_id}))
    cleanup.append(("whatsapp_inbox", {"wa_id": wa_id}))
    bot = WhatsAppBotService(db)
    await bot.handle_webhook(wa_payload(wa_id, text="hi"))
    await bot.handle_webhook(wa_payload(wa_id, reply="menu:book"))
    await bot.handle_webhook(wa_payload(wa_id, reply=f"vt:{rig['hatchback']}"))
    await bot.handle_webhook(wa_payload(wa_id, reply=f"svc:{rig['travel']}"))
    when = (await last_out(db, phone))["options"][0]["id"]
    # The saved address resolved THIS center, so its grid was offered.
    assert when.startswith("when:")
    await bot.handle_webhook(wa_payload(wa_id, reply=when))
    await bot.handle_webhook(wa_payload(wa_id, reply=f"addr:{address_id}"))
    summary = (await last_out(db, phone))["message"]
    # Plan covers the wash; the plan-covered car carries no distance charge.
    assert "Your plan covers: −₹300" in summary and summary.rstrip().endswith("💰 ₹0")

    await bot.handle_webhook(wa_payload(wa_id, reply="confirm:yes"))
    booking = await db.bookings.find_one({"customer_id": str(customer["_id"])})
    assert booking is not None and booking["total_amount"] == 0.0 and booking["subscription_id"]


async def test_the_ended_launch_offer_is_never_recreated(db):
    from app.services.coupon_service import CouponService

    original = await db.coupons.find_one({"code": "FREEBIKE"})
    await db.coupons.delete_many({"code": "FREEBIKE"})
    try:
        await CouponService(db).ensure_default_launch_offer()
        assert await db.coupons.find_one({"code": "FREEBIKE"}) is None
    finally:
        if original:
            await db.coupons.insert_one(original)


async def test_manager_logged_job_quote_matches_the_logged_total(rig, cleanup):
    db = rig["db"]
    phone = "9555510088"
    cleanup.append(("bookings", {"customer_phone": phone}))
    cleanup.append(("addresses", {"line1": "Central Log Lane 1"}))
    cleanup.append(("users", {"phone": phone}))
    manager = CurrentUser(rig["manager_id"], "manager", service_center_id=rig["center_id"])
    yesterday = (now_ist() - timedelta(days=1)).strftime("%Y-%m-%d")
    quote = (await BookingController(db).quote(manager, BookingQuoteRequest(
        customer_phone=phone, lines=[{"vehicle_type": rig["hatchback"], "quantity": 1, "service_ids": [rig["first"]]}],
        mode="log", scheduled_date=yesterday, service_time="10:30",
    )))["data"]
    assert quote["first_time_eligible"] is True and quote["total_amount"] == 200.0 and quote["online_only"] is False
    result = await BookingService(db).create_manager_logged_visit(
        ManagerLogBookingRequest(
            customer_name="Logged Central", customer_phone=phone, lines=[_line(rig, "first")],
            scheduled_date=yesterday, service_time="10:30", address_line="Central Log Lane 1", payment_method="cash", send_whatsapp=False,
        ),
        manager_id=rig["manager_id"], manager_center_id=rig["center_id"],
    )
    assert result["total_amount"] == quote["total_amount"]


# ------------------------------------------- cancelling voids the pay link


async def _parked_visit(rig, cleanup, quantity: int = 1) -> tuple[dict, dict]:
    """A manager's prepaid booking: parked, with a payment link out."""
    customer, address_id = await _customer(rig, cleanup)
    result = await _book(rig, customer, address_id, [_line(rig, "offer", quantity=quantity)], method="online", source="staff")
    assert result["awaiting_payment"] is True and result["payment_link"] == STUB_LINK
    return customer, result


async def _link_status(db, booking_id: str) -> str:
    order = await db.payment_orders.find_one({"kind": "link", "$or": [{"booking_id": booking_id}, {"booking_ids": booking_id}]})
    return order["status"]


async def test_cancelling_a_booking_voids_its_unpaid_link(rig, cleanup, gateway):
    db = rig["db"]
    customer, result = await _parked_visit(rig, cleanup)
    booking_id = result["bookings"][0]["id"]
    assert await _link_status(db, booking_id) == "created"
    from app.schemas.booking_schema import BookingCancelRequest

    await BookingService(db).cancel_booking(booking_id, BookingCancelRequest(reason="Customer changed plans"), str(customer["_id"]), "customer")
    assert await _link_status(db, booking_id) == "voided"


async def test_cancelling_a_visit_or_deleting_it_voids_the_link(rig, cleanup, gateway):
    db = rig["db"]
    from app.schemas.booking_schema import BookingCancelRequest

    _, visit = await _parked_visit(rig, cleanup, quantity=2)
    await BookingService(db).cancel_booking_group(
        visit["booking_group_id"], BookingCancelRequest(reason="Called off"), rig["manager_id"], "manager", rig["center_id"]
    )
    assert await _link_status(db, visit["bookings"][0]["id"]) == "voided"

    _, single = await _parked_visit(rig, cleanup)
    admin = await db.users.find_one({"role": "admin"})
    await BookingService(db).soft_delete_booking(single["bookings"][0]["id"], str(admin["_id"]))
    assert await _link_status(db, single["bookings"][0]["id"]) == "voided"


async def test_a_gateway_failure_never_blocks_the_cancellation(rig, cleanup, gateway, monkeypatch):
    db = rig["db"]
    from app.schemas.booking_schema import BookingCancelRequest

    customer, result = await _parked_visit(rig, cleanup)
    booking_id = result["bookings"][0]["id"]

    def refuse(link_id):
        raise RuntimeError("razorpay down")

    monkeypatch.setattr(_StubLinks, "cancel", lambda self, link_id: refuse(link_id))
    cancelled = await BookingService(db).cancel_booking(booking_id, BookingCancelRequest(reason="Changed plans"), str(customer["_id"]), "customer")
    assert cancelled["status"] == BookingStatus.CANCELLED.value
    assert await _link_status(db, booking_id) == "created"  # left for the sweep


# ------------------------------------------------------------ HTTP wiring


def _auth(user_id: str, role: str, center_id: str | None = None) -> dict:
    from app.core.security import create_access_token

    return {"Authorization": f"Bearer {create_access_token(user_id, role, {'service_center_id': center_id, 'tv': 0})}"}


async def test_quote_and_queue_routes(rig, cleanup):
    from httpx import ASGITransport, AsyncClient

    from app.main import app

    db = rig["db"]
    await _raw_booking(db, rig["center_id"], 950, booking_number="BKQHTTP1", status="pending")
    manager = _auth(rig["manager_id"], "manager", rig["center_id"])
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        res = await client.post("/api/v1/bookings/quote", json={
            "lines": [{"vehicle_type": rig["hatchback"], "quantity": 1, "service_ids": [rig["offer"]]}],
            "address": {"latitude": CENTER_LAT + 7.4 / KM_PER_DEG_LAT, "longitude": CENTER_LNG},
        })
        assert res.status_code == 200, res.text
        assert res.json()["data"]["total_amount"] == 154.0 and res.json()["data"]["online_only"] is True

        res = await client.get(f"/api/v1/bookings/center/{rig['center_id']}", params={"scope": "attention", "sort": "scheduled_asc", "q": "BKQHTTP"}, headers=manager)
        assert res.status_code == 200, res.text
        assert [b["booking_number"] for b in res.json()["data"]] == ["BKQHTTP1"] and res.json()["meta"]["total"] == 1

        bad = await client.get(f"/api/v1/bookings/center/{rig['center_id']}", params={"scope": "everything"}, headers=manager)
        assert bad.status_code == 400
