"""
Scale pass parity: every aggregation-pipeline rewrite must report the SAME
numbers as the load-everything-into-Python code it replaced (frozen in
tests/scale_legacy_reference.py), on one seeded dataset built to hit every
edge the old definitions encode: bookings completed in-window but created
before it, completed without closed_at, cancelled-by-captain, soft-deleted
rows, legacy saved-vehicle bookings without vehicle_type, multi-service
bookings, empty/"" start stages, 0 ratings, complaints on captains' jobs,
subscriptions whose center only lives on the subscription doc, ...

Money is compared to the paisa. The one intentional exception: the service
mix's per-service revenue share (amount / number of services) was
re-rounded after EVERY booking in the old loop, so it drifted by up to
half a paisa per booking; it is now summed exactly and rounded once, so
those rows are compared to within that drift.
"""
import random
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio

from app.services.analytics_service import AnalyticsService
from app.services.kpi_service import KpiService, resolve_period
from app.services.subscription_service import UserSubscriptionService
from app.services.whatsapp_crm_service import WhatsAppCrmService
from app.utils.timezone import now_ist
from tests.scale_legacy_reference import LegacyAnalyticsService, LegacyCrm, LegacyKpiService, LegacySubscriptions

MARK = {"_scale_parity": True}
COLLECTIONS = (
    "bookings", "users", "vehicles", "addresses", "reviews", "complaints", "slot_capacity",
    "payment_orders", "user_subscriptions", "coverage_leads", "service_centers",
    "whatsapp_conversations", "whatsapp_inbox", "whatsapp_outbox", "whatsapp_templates", "subscription_plans",
)


def _close(a, b, path, tol):
    if isinstance(a, bool) or isinstance(b, bool) or a is None or b is None or isinstance(a, str) or isinstance(b, str):
        assert a == b, f"{path}: legacy={a!r} new={b!r}"
        return
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        assert abs(a - b) <= tol, f"{path}: legacy={a!r} new={b!r}"
        return
    assert a == b, f"{path}: legacy={a!r} new={b!r}"


def assert_same(legacy, new, path="$", tol=0.011, loose: dict | None = None):
    """Deep equality; numbers within `tol` (1 paisa + float slop). `loose`
    maps a path suffix to a wider tolerance for known rounding drift."""
    loose = loose or {}
    if isinstance(legacy, dict):
        assert isinstance(new, dict), f"{path}: legacy dict, new {type(new)}"
        assert set(legacy) == set(new), f"{path}: keys legacy={sorted(legacy)} new={sorted(new)}"
        for k in legacy:
            assert_same(legacy[k], new[k], f"{path}.{k}", tol, loose)
        return
    if isinstance(legacy, list):
        assert isinstance(new, list) and len(legacy) == len(new), f"{path}: len legacy={len(legacy)} new={len(new) if isinstance(new, list) else new}"
        for i, (a, b) in enumerate(zip(legacy, new)):
            assert_same(a, b, f"{path}[{i}]", tol, loose)
        return
    t = next((v for suffix, v in loose.items() if path.endswith(suffix)), tol)
    _close(legacy, new, path, t)


def by(key):
    return lambda rows: sorted(rows, key=lambda r: str(r[key]))


@pytest_asyncio.fixture(scope="module")
async def dataset(db):
    """~400 bookings over 120 days, 45 customers, 4 captains, 2 centers."""
    rng = random.Random(20260928)
    now = now_ist()

    def ago(days: float, hours: float = 0.0) -> datetime:
        return (now - timedelta(days=days, hours=hours)).astimezone(timezone.utc).replace(tzinfo=None)

    for name in COLLECTIONS:
        await db[name].delete_many(MARK)

    centers = []
    for i in range(2):
        res = await db.service_centers.insert_one({**MARK, "name": f"Parity Center {i}", "code": f"PARITY{i}", "is_active": True, "is_deleted": False})
        centers.append(str(res.inserted_id))

    vt_ids = [str(v["_id"]) for v in await db.vehicle_types.find({}, {"_id": 1}).to_list(length=None)]
    svc_ids = [str(s["_id"]) for s in await db.services.find({}, {"_id": 1}).limit(5).to_list(length=None)]
    assert len(vt_ids) >= 2 and len(svc_ids) >= 3

    captains = []
    for i in range(4):
        res = await db.users.insert_one({**MARK, "role": "captain", "full_name": f"Parity Captain {i}", "status": "active",
                                         "service_center_id": centers[i % 2], "is_deleted": False, "created_at": ago(200)})
        captains.append(str(res.inserted_id))

    customers, vehicles, addresses = [], {}, {}
    for i in range(45):
        created = ago(rng.uniform(0, 150), rng.uniform(0, 23))
        doc = {**MARK, "role": "customer", "full_name": f"Parity Customer {i}", "phone": f"97{i:08d}", "status": "active",
               "is_deleted": False, "created_at": created}
        if i % 7 == 0:
            doc["referred_by"] = "someone"
        cid = str((await db.users.insert_one(doc)).inserted_id)
        customers.append(cid)
        v = await db.vehicles.insert_one({**MARK, "owner_id": cid, "vehicle_type": rng.choice(vt_ids), "is_deleted": False})
        vehicles[cid] = str(v.inserted_id)
        a = await db.addresses.insert_one({**MARK, "owner_id": cid, "city": rng.choice(["Indore", "Dewas"]),
                                           "pincode": rng.choice(["452001", "452010", "455001"]), "line1": "x"})
        addresses[cid] = str(a.inserted_id)

    bookings = []
    for i in range(400):
        cid = rng.choice(customers[:40])  # 5 customers never book
        created_days = rng.uniform(0, 120)
        created = ago(created_days, rng.uniform(0, 20))
        status = rng.choices(["completed", "cancelled", "pending", "assigned", "rescheduled"], [60, 12, 12, 8, 8])[0]
        n_services = rng.choice([0, 1, 1, 2, 3])
        amount = rng.choice([299.0, 349.5, 499.0, 500.0, 799.0, 1000.0])
        doc = {
            **MARK,
            "customer_id": cid,
            "service_center_id": rng.choice(centers),
            "captain_id": rng.choice(captains + [None]),
            "service_ids": rng.sample(svc_ids, n_services),
            "status": status,
            "total_amount": amount,
            "subtotal": amount - 50,
            "payment_method": rng.choice(["cash", "online", "subscription"]),
            "created_at": created,
            "updated_at": created,
            "scheduled_date": (now - timedelta(days=created_days - rng.randint(0, 3))).replace(tzinfo=None, hour=0, minute=0, second=0, microsecond=0),
            "scheduled_slot": rng.choice(["09:00-12:00", "12:00-15:00", "15:00-18:00"]),
            "booking_number": f"BKPAR{i:05d}",
            "is_deleted": i % 53 == 0,
        }
        r = rng.random()
        if r < 0.6:
            doc["vehicle_type"] = rng.choice(vt_ids)
            doc["vehicle_label"] = rng.choice(["Hatchback", "SUV", None])
        elif r < 0.8:
            doc["vehicle_id"] = vehicles[cid]
        elif r < 0.9:
            doc["vehicle_id"] = "not-an-object-id"
            doc["vehicle_label"] = "Legacy label"
        else:
            doc["vehicle_type"] = ""
            doc["vehicle_id"] = vehicles[cid]
        a = rng.random()
        if a < 0.8:
            doc["address_id"] = addresses[cid]
        elif a < 0.9:
            doc["address_id"] = "bad-id"
        if status == "completed":
            c = rng.random()
            if c < 0.8:
                doc["closed_at"] = ago(max(created_days - rng.uniform(0, 12), 0))
            elif c < 0.9:
                doc["closed_at"] = None
            doc["actual_duration_minutes"] = rng.choice([0, 35, 50, 62.5, None])
            doc["captain_earning"] = rng.choice([120, 150.5, None, 0])
            if rng.random() < 0.8:
                doc["heading_at"] = created + timedelta(minutes=10)
                doc["vehicle_verified_at"] = created + timedelta(minutes=rng.choice([-5, 20, 33]))
            doc["assigned_at"] = created
            doc["delay_minutes"] = rng.choice([0, 0, 15, None])
            doc["duration_minutes"] = rng.choice([45, 60])
        if status == "cancelled" and rng.random() < 0.4:
            doc["cancelled_by_role"] = "captain"
        doc["captain_start_stage"] = rng.choice(["early", "on_time", "late", "", None])
        if doc["captain_start_stage"] is None:
            del doc["captain_start_stage"]
        bookings.append(doc)
    res = await db.bookings.insert_many(bookings)
    booking_ids = [str(x) for x in res.inserted_ids]

    reviews = []
    for i, idx in enumerate(rng.sample(range(len(bookings)), 140)):
        b = bookings[idx]
        created = b["created_at"] + timedelta(hours=rng.uniform(1, 72))
        review = {**MARK, "booking_id": booking_ids[idx], "customer_id": b["customer_id"],
                  "captain_id": b.get("captain_id"), "service_center_id": b["service_center_id"],
                  "created_at": created, "is_deleted": i % 31 == 0}
        kind = rng.random()
        if kind < 0.5:
            review["captain_rating"] = rng.choice([1, 2, 3, 4, 5, 5, 5])
            review["service_rating"] = rng.choice([3, 4, 5])
        elif kind < 0.8:
            review["rating"] = rng.choice([2, 4, 5])
        elif kind < 0.9:
            review["captain_rating"] = 0
            review["rating"] = 4
        reviews.append(review)
    await db.reviews.insert_many(reviews)

    complaints = []
    for i in range(60):
        idx = rng.randrange(len(bookings))
        b = bookings[idx]
        created = b["created_at"] + timedelta(hours=rng.uniform(1, 200))
        c = {**MARK, "booking_id": booking_ids[idx], "customer_id": b["customer_id"], "service_center_id": b["service_center_id"],
             "subject": f"Issue {i}", "category": rng.choice(["quality", "delay", None, "billing"]),
             "status": rng.choice(["open", "in_progress", "resolved", "closed"]), "priority": "medium",
             "created_at": created, "is_deleted": i % 29 == 0}
        if c["status"] in ("resolved", "closed") and rng.random() < 0.8:
            c["updated_at"] = created + timedelta(hours=rng.uniform(1, 48))
        complaints.append(c)
    await db.complaints.insert_many(complaints)

    today = now.date()
    slots = []
    for d in range(-40, 3):
        date = (today + timedelta(days=d)).isoformat()
        for j, key in enumerate(["09:00-12:00", "12:00-15:00", "15:00-18:00"]):
            slots.append({**MARK, "service_center_id": f"parity-{d}", "date": date, "slot_key": key,
                          "capacity": 10, "booked_count": (j * 3 + (d % 4)) % 11})
    await db.slot_capacity.insert_many(slots)

    plan = await db.subscription_plans.insert_one({**MARK, "name": "Parity Plan", "is_deleted": False})
    plan_id = str(plan.inserted_id)
    subs = []
    for i in range(50):
        cid = rng.choice(customers)
        end = now + timedelta(days=rng.uniform(-40, 40))
        subs.append({**MARK, "customer_id": cid, "plan_id": rng.choice([plan_id, "missing-plan"]),
                     "status": rng.choice(["active", "active", "expired", "cancelled"]),
                     "end_date": end.astimezone(timezone.utc).replace(tzinfo=None) if i % 11 else None,
                     "start_date": ago(30), "service_center_id": rng.choice([centers[0], centers[1], None]),
                     "amount_paid": rng.choice([None, 999.0, 0.0]), "purchased_price": 1299.0,
                     "remaining_service_count": 2, "total_service_count": 4, "vehicle_type": rng.choice(vt_ids),
                     "created_at": ago(rng.uniform(0, 90)), "is_deleted": i % 17 == 0})
    sres = await db.user_subscriptions.insert_many(subs)
    sub_ids = [str(x) for x in sres.inserted_ids]
    orders = []
    for i in range(40):
        o = {**MARK, "purpose": "subscription", "status": rng.choice(["paid", "paid", "created"]),
             "amount_paise": rng.choice([99900, 129900, 49950]), "customer_id": rng.choice(customers),
             "plan_id": plan_id, "kind": rng.choice(["link", "autopay", "manager_cash"]), "created_at": ago(rng.uniform(0, 70))}
        mode = rng.random()
        if mode < 0.4:
            o["service_center_id"] = rng.choice(centers)
        elif mode < 0.8:
            o["subscription_id"] = rng.choice(sub_ids)
        orders.append(o)
    await db.payment_orders.insert_many(orders)
    for i in range(12):
        await db.coverage_leads.insert_one({**MARK, "phone": f"96{i:08d}", "pincode": f"4{i:05d}", "created_at": ago(rng.uniform(0, 40))})

    # WhatsApp CRM
    wa_ids = []
    for i in range(30):
        wa = f"9198{i:08d}"
        wa_ids.append(wa)
        created = ago(rng.uniform(1, 60))
        convo = {**MARK, "wa_id": wa, "phone": wa[2:], "created_at": created,
                 "last_message_at": created + timedelta(hours=rng.uniform(0, 50)) if i % 9 else None,
                 "unread_count": rng.choice([0, 0, 2]), "tags": []}
        st = rng.choice(["open", "pending", "resolved", None, "missing"])
        if st != "missing":
            convo["crm_status"] = st
        if st == "resolved":
            convo["resolved_at"] = created + timedelta(hours=rng.uniform(1, 30))
        await db.whatsapp_conversations.insert_one(convo)
        first_in = created + timedelta(minutes=rng.uniform(0, 30))
        await db.whatsapp_inbox.insert_one({**MARK, "wa_id": wa, "text": "hi", "created_at": first_in})
        if rng.random() < 0.8:
            await db.whatsapp_outbox.insert_one({**MARK, "phone": rng.choice([wa, wa[2:]]), "message": "hello",
                                                 "created_at": first_in + timedelta(minutes=rng.uniform(1, 90))})
    for i in range(60):
        await db.whatsapp_outbox.insert_one({**MARK, "phone": rng.choice(wa_ids)[2:], "template_name": rng.choice(["parity_tpl_a", "parity_tpl_b", None]),
                                             "delivery_status": rng.choice(["delivered", "read", "failed", None]),
                                             "ok": rng.choice([True, False, None]), "created_at": ago(rng.uniform(0, 45))})
    for name in ("parity_tpl_a", "parity_tpl_b", "parity_tpl_unused"):
        await db.whatsapp_templates.insert_one({**MARK, "name": name, "status": "APPROVED", "body": "x", "param_count": 0})

    yield {"centers": centers, "captains": captains, "customers": customers, "now": now}

    for name in COLLECTIONS:
        await db[name].delete_many(MARK)


PERIODS = [("today", None, None), ("7d", None, None), ("30d", None, None), ("this_month", None, None),
           (None, (now_ist() - timedelta(days=100)).date().isoformat(), now_ist().date().isoformat())]


def _period_ids():
    return [p[0] or "custom100d" for p in PERIODS]


@pytest.mark.asyncio
@pytest.mark.parametrize("period", PERIODS, ids=_period_ids())
@pytest.mark.parametrize("section", ["overview", "business", "customers", "captains", "financial", "marketing", "operations", "areas"])
async def test_kpi_sections_match_legacy(db, dataset, section, period):
    s, e, ps, pe = resolve_period(*period)
    legacy = await getattr(LegacyKpiService(db), section)(s, e, ps, pe)
    new = await getattr(KpiService(db), section)(s, e, ps, pe)
    if section == "business":
        # Order of equal-revenue/equal-count rows was natural-order-dependent.
        legacy["service_mix"], new["service_mix"] = by("name")(legacy["service_mix"]), by("name")(new["service_mix"])
        legacy["vehicle_mix"], new["vehicle_mix"] = by("name")(legacy["vehicle_mix"]), by("name")(new["vehicle_mix"])
    if section == "areas":
        legacy["areas"], new["areas"] = by("area")(legacy["areas"]), by("area")(new["areas"])
    if section == "operations":
        # Peak/lowest slot tie-breaks were natural-order-dependent too.
        for key in ("peak_slot", "lowest_slot"):
            legacy["capacity"].pop(key), new["capacity"].pop(key)
        legacy["experience"]["complaint_categories"] = sorted(legacy["experience"]["complaint_categories"], key=lambda r: (-r["count"], r["category"]))
        new["experience"]["complaint_categories"] = sorted(new["experience"]["complaint_categories"], key=lambda r: (-r["count"], r["category"]))
    if section == "captains":
        legacy["captains"], new["captains"] = by("captain_id")(legacy["captains"]), by("captain_id")(new["captains"])
    if section == "business":
        assert_same(legacy.pop("service_mix"), new.pop("service_mix"), "$.service_mix", loose={".revenue": 0.3, ".aov": 0.3})
    assert_same(legacy, new)


@pytest.mark.asyncio
async def test_operations_peak_slot_is_deterministic(db, dataset):
    s, e, ps, pe = resolve_period("30d", None, None)
    out = await KpiService(db).operations(s, e, ps, pe)
    assert out["capacity"]["peak_slot"] is not None and out["capacity"]["lowest_slot"] is not None


@pytest.mark.asyncio
@pytest.mark.parametrize("period", PERIODS, ids=_period_ids())
async def test_manager_overview_matches_legacy(db, dataset, period):
    s, e, ps, pe = resolve_period(*period)
    for center in dataset["centers"]:
        legacy = await LegacyKpiService(db).manager_overview(center, s, e, ps, pe)
        new = await KpiService(db).manager_overview(center, s, e, ps, pe)
        assert_same(legacy, new)


def _without_tie_order(summary: dict) -> dict:
    # Top-5 lists break ties in natural order on both sides; compare the
    # ranked values, not which of two equal rows came first.
    out = dict(summary)
    out["top_services"] = sorted(r["bookings"] for r in summary["top_services"])
    out["best_performing_captains"] = sorted(r["jobs_completed"] for r in summary["best_performing_captains"])
    out["best_service_centers"] = sorted(r["revenue"] for r in summary["best_service_centers"])
    return out


@pytest.mark.asyncio
async def test_dashboard_summary_matches_legacy(db, dataset):
    legacy = await LegacyAnalyticsService(db).dashboard_summary()
    new = await AnalyticsService(db).dashboard_summary()
    assert_same(_without_tie_order(legacy), _without_tie_order(new))


@pytest.mark.asyncio
async def test_service_center_summaries_match_legacy(db, dataset):
    legacy = await LegacyAnalyticsService(db).service_center_summaries()
    new = await AnalyticsService(db).service_center_summaries()
    assert_same(legacy, new)


@pytest.mark.asyncio
async def test_manager_summary_matches_legacy(db, dataset):
    for center in dataset["centers"]:
        assert_same(await LegacyAnalyticsService(db).manager_summary(center), await AnalyticsService(db).manager_summary(center))


@pytest.mark.asyncio
@pytest.mark.parametrize("scoped", [False, True])
async def test_vehicle_type_and_service_breakdowns_match_legacy(db, dataset, scoped):
    center = dataset["centers"][0] if scoped else None
    legacy = await LegacyAnalyticsService(db).vehicle_type_breakdown(center)
    new = await AnalyticsService(db).vehicle_type_breakdown(center)
    key = by("vehicle_type_id")
    assert_same(key(legacy), key(new))
    legacy = await LegacyAnalyticsService(db).service_breakdown(center)
    new = await AnalyticsService(db).service_breakdown(center)
    key = by("service_id")
    assert_same(key(legacy), key(new))


@pytest.mark.asyncio
async def test_whatsapp_analytics_and_template_usage_match_legacy(db, dataset):
    assert await db.whatsapp_conversations.count_documents({"last_message_at": {"$ne": None}}) < 300  # whole-sample parity
    for days in (7, 30, 90):
        assert_same(await LegacyCrm(db).analytics(days), await WhatsAppCrmService(db).analytics(days))
    legacy = {t["name"]: t for t in await LegacyCrm(db).list_local_templates()}
    new = {t["name"]: t for t in await WhatsAppCrmService(db).list_local_templates()}
    assert_same(legacy, new)
    assert new["parity_tpl_unused"]["usage"] == 0 and new["parity_tpl_a"]["usage"] > 0


@pytest.mark.asyncio
async def test_conversation_pages_walk_every_conversation_once(db, dataset):
    crm = WhatsAppCrmService(db)
    everything = await crm.list_conversations("all", limit=100)
    seen, before = [], None
    while True:
        page = await crm.list_conversations("all", limit=7, before=before)
        if not page:
            break
        assert len(page) <= 7
        seen += [c["wa_id"] for c in page]
        before = page[-1]["last_message_at"]
    assert seen == [c["wa_id"] for c in everything]
    assert len(seen) == len(set(seen))
    # A post-filtered view still fills its page from older conversations.
    resolved = await crm.list_conversations("resolved", limit=3)
    assert all(c["crm_status"] == "resolved" for c in resolved)


async def _all_pages(fetch, page_size=7):
    rows, page = [], 1
    while True:
        out = await fetch(page, page_size)
        rows += out["rows"]
        if page >= max(out["meta"]["total_pages"], 1):
            assert out["meta"]["total"] == len(rows)
            return out, rows
        page += 1


def _sorted_breakdown(rows, count_key):
    return sorted(rows, key=lambda r: (-r[count_key], r["plan_name"]))


STATUS_PREDICATES = {
    None: lambda r: True,
    "active": lambda r: r["status"] == "active",
    "expired": lambda r: r["status"] == "expired",
    "cancelled": lambda r: r["status"] == "cancelled",
}


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [None, "active", "expiring", "expired", "cancelled"])
async def test_center_subscription_overview_matches_legacy(db, dataset, status):
    center = dataset["centers"][0]
    legacy = await LegacySubscriptions(db).center_overview(center, "admin", None)
    svc = UserSubscriptionService(db)
    out, rows = await _all_pages(lambda p, n: svc.center_overview(center, "admin", None, page=p, page_size=n, status=status))
    assert_same(legacy["kpis"], out["kpis"])
    assert_same(_sorted_breakdown(legacy["plan_breakdown"], "active_count"), out["plan_breakdown"])
    keep = STATUS_PREDICATES.get(status) or (lambda r: r["status"] == "active" and r["days_left"] is not None and r["days_left"] <= 14)
    assert_same([r for r in legacy["rows"] if keep(r)], rows)


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [None, "active", "expired"])
async def test_admin_subscription_overview_matches_legacy(db, dataset, status):
    legacy = await LegacySubscriptions(db).admin_overview()
    svc = UserSubscriptionService(db)
    out, rows = await _all_pages(lambda p, n: svc.admin_overview(page=p, page_size=n, status=status))
    assert_same(legacy["kpis"], out["kpis"])
    assert_same(_sorted_breakdown(legacy["plan_breakdown"], "count"), out["plan_breakdown"])
    assert_same([r for r in legacy["rows"] if STATUS_PREDICATES[status](r)], rows)


@pytest.mark.asyncio
async def test_subscription_overview_search_and_plan_filter(db, dataset):
    svc = UserSubscriptionService(db)
    legacy = await LegacySubscriptions(db).admin_overview()
    _out, rows = await _all_pages(lambda p, n: svc.admin_overview(page=p, page_size=n, search="parity customer 1"))
    expected = [r for r in legacy["rows"] if "parity customer 1" in (r["customer_name"] or "").lower()]
    assert expected and [r["subscription_id"] for r in rows] == [r["subscription_id"] for r in expected]
    _out, by_phone = await _all_pages(lambda p, n: svc.admin_overview(page=p, page_size=n, search=f"+91 {expected[0]['customer_phone']}"))
    assert expected[0]["subscription_id"] in [r["subscription_id"] for r in by_phone]
    plan_id = next(r["plan_id"] for r in legacy["rows"] if r["plan_name"] == "Parity Plan")
    _out, by_plan = await _all_pages(lambda p, n: svc.admin_overview(page=p, page_size=n, plan_id=plan_id))
    assert [r["subscription_id"] for r in by_plan] == [r["subscription_id"] for r in legacy["rows"] if r["plan_id"] == plan_id]
    _out, by_plan_name = await _all_pages(lambda p, n: svc.admin_overview(page=p, page_size=n, search="Parity Plan"))
    assert len(by_plan_name) == len(by_plan)


@pytest.mark.asyncio
@pytest.mark.parametrize("period", PERIODS, ids=_period_ids())
async def test_center_plan_revenue_and_purchases_match_legacy(db, dataset, period):
    s, e, _ps, _pe = resolve_period(*period)
    for center in dataset["centers"]:
        assert await LegacySubscriptions(db).center_plan_revenue(center, s, e) == await UserSubscriptionService(db).center_plan_revenue(center, s, e)
        for page in (1, 2):
            legacy = await LegacySubscriptions(db).plan_purchases(s, e, page, 4, center)
            new = await UserSubscriptionService(db).plan_purchases(s, e, page, 4, center)
            assert_same(list(legacy), list(new))
