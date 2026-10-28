"""
Festival / broadcast campaigns: sanitising, the template definition, selection
resolution (all / excluded / included / filters), consent + staff exclusion,
idempotent enqueue and drain with the log provider, cancel, resume after a
crash, admin-only access, a 20 000-person audience, and Meta status webhooks.
"""
import asyncio
import json
import subprocess
import sys
import time
import tracemalloc
from datetime import datetime, timedelta, timezone
from itertools import count

import pytest
from bson import ObjectId
from httpx import ASGITransport, AsyncClient

from app.core.config import settings
from app.core.exceptions import BadRequestException, ConflictException
from app.schemas.whatsapp_campaign_schema import AudienceFilter, Selection
from app.services import whatsapp_campaign_service as svc
from app.services.whatsapp_campaign_template import (
    FESTIVAL_BODY,
    MAX_MESSAGE_CHARS,
    festival_template_payload,
    sanitize_message,
    submit_festival_template,
)
from app.services.whatsapp_service import SendOutcome
from tests.factories import make_captain, make_customer, make_manager, make_service_center

_phones = count(int(time.time()) % 90_000_000 + 1_000_000)
IMG = "https://cdn.example.com/festival.png"


def _auth(user_id: str, role: str, center_id: str | None = None, tv: int = 0) -> dict:
    from app.core.security import create_access_token

    return {"Authorization": f"Bearer {create_access_token(user_id, role, {'service_center_id': center_id, 'tv': tv})}"}


def _client():
    from app.main import app

    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


class Actor:
    def __init__(self, id: str, role: str = "admin"):
        self.id, self.role, self.full_name = id, role, "Admin"


@pytest.fixture
def city():
    return f"BroadcastCity{ObjectId()}"


@pytest.fixture(autouse=True)
def _loose(monkeypatch):
    """Fast pacing, generous caps, image check bypassed (HTTP tests upload a real one)."""
    monkeypatch.setattr(settings, "WHATSAPP_CAMPAIGN_RATE_PER_SECOND", 100000)
    monkeypatch.setattr(settings, "WHATSAPP_CAMPAIGN_MAX_RECIPIENTS", 50000)
    monkeypatch.setattr(settings, "WHATSAPP_CAMPAIGN_DAILY_CAP", 1000000)

    async def ok(db, url):
        return url

    monkeypatch.setattr(svc, "validate_image_url", ok)


@pytest.fixture(autouse=True)
async def _clean(db):
    """One campaign at a time is a rule, so every test starts from none."""
    for coll in (svc.CAMPAIGNS, svc.RECIPIENTS, svc.STATE):
        await db[coll].delete_many({})
    await db.whatsapp_outbox.delete_many({"kind": {"$in": ["campaign", "campaign_test"]}})
    yield
    for coll in (svc.CAMPAIGNS, svc.RECIPIENTS, svc.STATE):
        await db[coll].delete_many({})
    await db.whatsapp_outbox.delete_many({"kind": {"$in": ["campaign", "campaign_test"]}})


async def make_people(db, n, city, **over):
    now = datetime.now(timezone.utc)
    docs = []
    for _ in range(n):
        p = f"6{next(_phones):09d}"
        docs.append({
            "full_name": f"Broadcast {p}", "email": f"b{p}@example.com", "phone": p, "role": "customer", "status": "active",
            "is_deleted": False, "home_city": city, "bookings_count": 0, "created_at": now, "updated_at": now, **over,
        })
    res = await db.users.insert_many(docs)
    return [str(i) for i in res.inserted_ids]


async def _cleanup_city(db, city):
    ids = [str(u["_id"]) async for u in db.users.find({"home_city": city}, {"_id": 1})]
    await db.user_subscriptions.delete_many({"customer_id": {"$in": ids}})
    await db.users.delete_many({"home_city": city})


@pytest.fixture(autouse=True)
async def _city_cleanup(db, city):
    yield
    await _cleanup_city(db, city)


def sel_all(city, **kw):
    excluded = kw.pop("excluded_ids", [])
    return Selection(mode="all", filter=AudienceFilter(city=city, **kw), excluded_ids=excluded)


async def draft(db, name="Diwali", message="Happy Diwali!"):
    return await svc.create_draft(db, Actor(str(ObjectId())), name, message, IMG)


async def fire(db, sel, name="Diwali"):
    c = await draft(db, name)
    n = await svc.count_selection(db, sel)
    return await svc.send_campaign(db, Actor(str(ObjectId())), str(c["_id"]), sel, n)


async def run_all(db, max_passes=50):
    last = None
    for _ in range(max_passes):
        last = await svc.run_pass(db, budget_seconds=5.0)
        if last["status"] in ("done", "cancelled", "stopped", None):
            return last
    return last


# ---------------------------------------------------------------- sanitising

def test_sanitize_collapses_whitespace_and_keeps_emoji():
    out = sanitize_message("Happy\nDiwali!\t\tFrom   us      all ​\u0007 \U0001FA94\U0001F697")
    assert out == "Happy Diwali! From us all \U0001FA94\U0001F697"
    assert "\n" not in out and "\t" not in out and "    " not in out


def test_sanitize_limits():
    assert len(sanitize_message("a" * MAX_MESSAGE_CHARS)) == MAX_MESSAGE_CHARS
    with pytest.raises(BadRequestException):
        sanitize_message("a" * (MAX_MESSAGE_CHARS + 1))
    for empty in ("", "   \n\t ", None, "​"):
        with pytest.raises(BadRequestException):
            sanitize_message(empty)


def test_template_definition_and_script():
    p = festival_template_payload("HANDLE")
    assert (p["name"], p["language"], p["category"]) == ("blussit_festival_greeting", "en", "MARKETING")
    kinds = [c["type"] for c in p["components"]]
    assert kinds == ["HEADER", "BODY", "FOOTER"], "no buttons: STOP is handled as a keyword"
    assert p["components"][0] == {"type": "HEADER", "format": "IMAGE", "example": {"header_handle": ["HANDLE"]}}
    body = p["components"][1]["text"]
    assert body == FESTIVAL_BODY and body.count("{{") == 1 and body.count("{{1}}") == 1
    assert not body.startswith("{{") and not body.rstrip().endswith("}}")
    assert p["components"][2]["text"] == "Reply STOP to opt out"
    out = subprocess.run([sys.executable, "-m", "app.scripts.print_festival_template", "--header-handle", "HANDLE"],
                         capture_output=True, text=True, check=True, cwd=str(__import__("pathlib").Path(__file__).resolve().parents[1]))
    assert json.loads(out.stdout) == p


@pytest.mark.asyncio
async def test_bootstrap_submits_with_handle_and_skips_without(db, monkeypatch):
    await db.whatsapp_templates.delete_many({"name": "blussit_festival_greeting"})
    monkeypatch.setattr(settings, "WHATSAPP_FESTIVAL_HEADER_HANDLE", "")
    assert (await submit_festival_template(db))["status"] == "SKIPPED"
    sent = []

    class Fake:
        async def create_template(self, payload):
            sent.append(payload)
            return {"status": "PENDING", "category": "MARKETING"}

    monkeypatch.setattr("app.services.whatsapp_service.get_whatsapp_provider", lambda _db: Fake())
    monkeypatch.setattr(settings, "WHATSAPP_FESTIVAL_HEADER_HANDLE", "H123")
    res = await submit_festival_template(db)
    assert res["status"] == "PENDING" and sent[0]["components"][0]["example"]["header_handle"] == ["H123"]
    assert (await submit_festival_template(db))["note"] == "already exists"
    await db.whatsapp_templates.delete_many({"name": "blussit_festival_greeting"})


@pytest.mark.asyncio
async def test_log_provider_image_template_send(db):
    from app.services.whatsapp_service import LogWhatsAppProvider

    out = await LogWhatsAppProvider(db).send_header_image_template_outcome(
        "9876543210", "blussit_festival_greeting", "en", IMG, ["Hello"], extra={"kind": "campaign"})
    assert out.ok and out.wamid
    row = await db.whatsapp_outbox.find_one({"wamid": out.wamid})
    assert IMG in row["message"] and "Hello" in row["message"]


@pytest.mark.asyncio
async def test_meta_payload_has_header_image_and_body_param(db, monkeypatch):
    from app.services import whatsapp_service as ws

    posted = {}

    class Resp:
        status_code = 200
        text = "{}"

        def json(self):
            return {"messages": [{"id": "wamid.X"}]}

    class Http:
        async def post(self, url, json=None, headers=None):
            posted["body"] = json
            return Resp()

    monkeypatch.setattr(ws, "_meta_http", lambda: Http())
    prov = ws.MetaCloudWhatsAppProvider(db, "tok", "123", "v21.0")
    out = await prov.send_header_image_template_outcome("9876543210", "blussit_festival_greeting", "en", IMG, ["Hi\nthere"], extra={"kind": "campaign"})
    comps = posted["body"]["template"]["components"]
    assert comps[0] == {"type": "header", "parameters": [{"type": "image", "image": {"link": IMG}}]}
    assert comps[1] == {"type": "body", "parameters": [{"type": "text", "text": "Hi there"}]}
    assert out.ok and out.wamid == "wamid.X"
    await db.whatsapp_outbox.delete_many({"wamid": "wamid.X"})


# ---------------------------------------------------------------- selection

@pytest.mark.asyncio
async def test_selection_modes(db, city):
    ids = await make_people(db, 10, city)
    assert await svc.count_selection(db, sel_all(city)) == 10
    assert await svc.count_selection(db, sel_all(city, excluded_ids=ids[:3])) == 7
    assert await svc.count_selection(db, Selection(mode="none", filter=AudienceFilter(city=city))) == 0
    assert await svc.count_selection(db, Selection(mode="none", included_ids=ids[:4])) == 4
    assert await svc.count_selection(db, Selection(mode="ids", included_ids=ids[:2] + ["not-an-id"])) == 2
    # Select all, then unselect one: the row flags follow.
    page = await svc.list_audience(db, AudienceFilter(city=city), sel_all(city, excluded_ids=[ids[0]]), 1, 100)
    assert page["total"] == 10 and sum(r["selected"] for r in page["items"]) == 9


@pytest.mark.asyncio
async def test_selection_filters(db, city):
    now = datetime.now(timezone.utc)
    a, b, c, d = await make_people(db, 4, city)
    await db.users.update_one({"_id": ObjectId(a)}, {"$set": {"bookings_count": 5, "last_booking_at": now - timedelta(days=2), "home_center_id": "C1", "full_name": "Zed Unique"}})
    await db.users.update_one({"_id": ObjectId(b)}, {"$set": {"bookings_count": 1, "last_booking_at": now - timedelta(days=40), "home_center_id": "C2"}})
    await db.users.update_one({"_id": ObjectId(c)}, {"$unset": {"bookings_count": ""}})
    await db.user_subscriptions.insert_one({"customer_id": a, "status": "active", "is_deleted": False})
    n = lambda **kw: svc.count_selection(db, sel_all(city, **kw))  # noqa: E731
    assert await n(center_id="C1") == 1
    assert await n(has_active_plan=True) == 1 and await n(has_active_plan=False) == 3
    assert await n(bookings_min=2) == 1
    assert await n(bookings_max=1) == 3  # b=1, c missing=0, d=0
    assert await n(no_booking=True) == 2
    assert await n(last_booking_from=(now - timedelta(days=10)).date()) == 1
    assert await n(last_booking_from=(now - timedelta(days=60)).date(), last_booking_to=(now - timedelta(days=20)).date()) == 1
    assert await n(search="zed uni") == 1
    phone_a = (await db.users.find_one({"_id": ObjectId(a)}))["phone"]
    assert await n(search=phone_a[2:9]) >= 1


@pytest.mark.asyncio
async def test_eligibility_excludes_opt_out_staff_invalid_inactive(db, city, monkeypatch):
    ok, opted, inactive, deleted, badphone, short = await make_people(db, 6, city)
    await db.users.update_one({"_id": ObjectId(opted)}, {"$set": {"marketing_opt_out": True}})
    await db.users.update_one({"_id": ObjectId(inactive)}, {"$set": {"status": "suspended"}})
    await db.users.update_one({"_id": ObjectId(deleted)}, {"$set": {"is_deleted": True}})
    await db.users.update_one({"_id": ObjectId(badphone)}, {"$set": {"phone": "5123456789"}})
    await db.users.update_one({"_id": ObjectId(short)}, {"$set": {"phone": "98765"}})
    captain = (await make_people(db, 1, city, role="captain"))[0]
    biz = (await make_people(db, 1, city))[0]
    biz_phone = (await db.users.find_one({"_id": ObjectId(biz)}))["phone"]
    monkeypatch.setattr(settings, "WHATSAPP_BUSINESS_NUMBER", biz_phone)
    assert await svc.count_selection(db, sel_all(city)) == 1
    # Even by explicit id they are never reachable.
    every = [ok, opted, inactive, deleted, badphone, short, captain, biz]
    assert await svc.count_selection(db, Selection(mode="ids", included_ids=every)) == 1
    mgr_phone = "7" + str(next(_phones)).zfill(9)
    await db.users.insert_one({"full_name": "Staff", "role": "manager", "phone": mgr_phone, "status": "active", "home_city": city})
    assert mgr_phone in await svc._staff_phone_exclusions(db)
    # A STOP that arrives after the audience was chosen still wins at send time.
    sel = Selection(mode="ids", included_ids=[ok])
    c = await fire(db, sel)
    doc = await svc.get_campaign(db, c["id"])
    await svc.materialize_batch(db, doc)
    await db.users.update_one({"_id": ObjectId(ok)}, {"$set": {"marketing_opt_out": True}})
    await run_all(db)
    rec = await db[svc.RECIPIENTS].find_one({"user_id": ok})
    assert (rec["status"], rec["reason"]) == ("skipped", "opted_out")
    assert await db.whatsapp_outbox.count_documents({"kind": "campaign"}) == 0


# ------------------------------------------------------- enqueue + drain

@pytest.mark.asyncio
async def test_send_enqueues_and_drains_exactly_once(db, city):
    ids = await make_people(db, 45, city)
    c = await fire(db, sel_all(city, excluded_ids=ids[:5]))
    assert c["status"] == "queued" and c["total_recipients"] == 40
    assert await db[svc.RECIPIENTS].count_documents({}) == 0, "the API returns before any row is written"
    last = await run_all(db)
    assert last["status"] == "done"
    prog = await svc.progress(db, c["id"])
    assert prog["status"] == "done" and prog["counts"] == {"queued": 0, "sent": 40, "delivered": 0, "read": 0, "failed": 0, "skipped": 0}
    assert await db.whatsapp_outbox.count_documents({"kind": "campaign", "campaign_id": c["id"]}) == 40
    # Retry / redeploy / double click: nothing more goes out.
    again = await svc.send_campaign(db, Actor(str(ObjectId())), c["id"], sel_all(city), 40)
    assert again["already_started"]
    await svc.run_pass(db)
    assert await db.whatsapp_outbox.count_documents({"kind": "campaign", "campaign_id": c["id"]}) == 40
    assert await db[svc.RECIPIENTS].count_documents({"campaign_id": ObjectId(c["id"])}) == 40
    # The message really is the festival template with image + one param.
    row = await db.whatsapp_outbox.find_one({"kind": "campaign", "campaign_id": c["id"]})
    assert row["template_name"] == "blussit_festival_greeting" and IMG in row["message"] and "Happy Diwali!" in row["message"]
    assert (await svc.get_campaign(db, c["id"]))["status"] == "done"


@pytest.mark.asyncio
async def test_materialize_twice_never_duplicates(db, city):
    await make_people(db, 30, city)
    c = await fire(db, sel_all(city))
    doc = await svc.get_campaign(db, c["id"])
    await svc.materialize_batch(db, doc, limit=10)
    # A crashed pass that wrote rows but not the cursor: rewind and redo.
    await db[svc.CAMPAIGNS].update_one({"_id": doc["_id"]}, {"$set": {"materialized_through": None, "materialized_count": 0}})
    doc = await svc.get_campaign(db, c["id"])
    while not doc.get("materialized"):
        await svc.materialize_batch(db, doc, limit=10)
    assert await db[svc.RECIPIENTS].count_documents({"campaign_id": doc["_id"]}) == 30


@pytest.mark.asyncio
async def test_confirm_count_and_caps_and_single_running(db, city, monkeypatch):
    await make_people(db, 6, city)
    c = await draft(db)
    with pytest.raises(ConflictException):
        await svc.send_campaign(db, Actor("a"), str(c["_id"]), sel_all(city), 5)
    with pytest.raises(BadRequestException):
        await svc.send_campaign(db, Actor("a"), str(c["_id"]), Selection(mode="none"), 0)
    monkeypatch.setattr(settings, "WHATSAPP_CAMPAIGN_MAX_RECIPIENTS", 5)
    with pytest.raises(BadRequestException, match="per-campaign limit"):
        await svc.send_campaign(db, Actor("a"), str(c["_id"]), sel_all(city), 6)
    monkeypatch.setattr(settings, "WHATSAPP_CAMPAIGN_MAX_RECIPIENTS", 50000)
    monkeypatch.setattr(settings, "WHATSAPP_CAMPAIGN_DAILY_CAP", 7)
    await db[svc.RECIPIENTS].insert_one({"campaign_id": ObjectId(), "user_id": "x", "status": "sent", "sent_at": datetime.now(timezone.utc)})
    await db[svc.RECIPIENTS].insert_one({"campaign_id": ObjectId(), "user_id": "y", "status": "sent", "sent_at": datetime.now(timezone.utc)})
    with pytest.raises(BadRequestException, match="daily limit"):
        await svc.send_campaign(db, Actor("a"), str(c["_id"]), sel_all(city), 6)
    monkeypatch.setattr(settings, "WHATSAPP_CAMPAIGN_DAILY_CAP", 1000000)
    await svc.send_campaign(db, Actor("a"), str(c["_id"]), sel_all(city), 6)
    c2 = await draft(db, "Second")
    with pytest.raises(BadRequestException, match="Another campaign"):
        await svc.send_campaign(db, Actor("a"), str(c2["_id"]), sel_all(city), 6)


@pytest.mark.asyncio
async def test_worker_stops_at_daily_cap_then_resumes(db, city, monkeypatch):
    await make_people(db, 6, city)
    c = await fire(db, sel_all(city))
    monkeypatch.setattr(settings, "WHATSAPP_CAMPAIGN_DAILY_CAP", 4)
    r = await svc.run_pass(db, budget_seconds=3)
    assert r["status"] == "waiting_daily_cap"
    assert await db.whatsapp_outbox.count_documents({"kind": "campaign", "campaign_id": c["id"]}) == 4, "stops exactly at the cap"
    monkeypatch.setattr(settings, "WHATSAPP_CAMPAIGN_DAILY_CAP", 100)
    assert (await run_all(db))["status"] == "done"
    assert await db.whatsapp_outbox.count_documents({"kind": "campaign", "campaign_id": c["id"]}) == 6


# ------------------------------------------------------- cancel / resume

@pytest.mark.asyncio
async def test_cancel_stops_unsent(db, city):
    await make_people(db, 50, city)
    c = await fire(db, sel_all(city))
    doc = await svc.get_campaign(db, c["id"])
    doc["status"] = "sending"
    await db[svc.CAMPAIGNS].update_one({"_id": doc["_id"]}, {"$set": {"status": "sending"}})
    while not doc.get("materialized"):
        await svc.materialize_batch(db, doc)
    rows = await svc._claim_batch(db, doc["_id"], "o1", 10)
    await svc._drain_batch(db, svc.get_whatsapp_provider(db), doc, rows, "o1")
    out = await svc.cancel_campaign(db, Actor("a"), c["id"])
    assert out["status"] == "cancelled" and out["counts"]["sent"] == 10 and out["counts"]["skipped"] == 40 and out["counts"]["queued"] == 0
    await svc.run_pass(db)
    assert await db.whatsapp_outbox.count_documents({"kind": "campaign", "campaign_id": c["id"]}) == 10
    again = await svc.cancel_campaign(db, Actor("a"), c["id"])
    assert again["already_finished"]
    # The slot is free again.
    await make_people(db, 2, city)
    await fire(db, Selection(mode="all", filter=AudienceFilter(city=city)), "Next")


@pytest.mark.asyncio
async def test_resume_after_crash_does_not_double_send(db, city):
    await make_people(db, 30, city)
    c = await fire(db, sel_all(city))
    doc = await svc.get_campaign(db, c["id"])
    await db[svc.CAMPAIGNS].update_one({"_id": doc["_id"]}, {"$set": {"status": "sending"}})
    await svc.materialize_batch(db, doc, limit=20)  # worker died half-way through materialising
    rows = await svc._claim_batch(db, doc["_id"], "dead-owner", 10)
    # It sent the first three to Meta (outbox rows exist) and died before recording any of it.
    for r in rows[:3]:
        await db.whatsapp_outbox.insert_one({
            "phone": r["phone"], "kind": "campaign", "campaign_id": c["id"], "campaign_recipient_id": str(r["_id"]),
            "ok": True, "wamid": f"wamid.crash{r['_id']}", "created_at": datetime.now(timezone.utc),
        })
    await db[svc.RECIPIENTS].update_many({"campaign_id": doc["_id"], "status": "sending"}, {"$set": {"lease_until": datetime.now(timezone.utc) - timedelta(seconds=1)}})
    await db[svc.CAMPAIGNS].update_one({"_id": doc["_id"]}, {"$set": {"drain_owner": "dead-owner", "drain_lease_until": datetime.now(timezone.utc) - timedelta(seconds=1)}})
    last = await run_all(db)
    assert last["status"] == "done"
    prog = await svc.progress(db, c["id"])
    assert prog["counts"]["sent"] == 30 and prog["counts"]["queued"] == 0
    per_recipient = {}
    async for o in db.whatsapp_outbox.find({"kind": "campaign", "campaign_id": c["id"]}):
        per_recipient[o["campaign_recipient_id"]] = per_recipient.get(o["campaign_recipient_id"], 0) + 1
    assert len(per_recipient) == 30 and set(per_recipient.values()) == {1}


@pytest.mark.asyncio
async def test_live_drain_lease_blocks_second_worker(db, city):
    await make_people(db, 5, city)
    c = await fire(db, sel_all(city))
    await db[svc.CAMPAIGNS].update_one({"_id": ObjectId(c["id"])}, {"$set": {"drain_owner": "w1", "drain_lease_until": datetime.now(timezone.utc) + timedelta(seconds=30)}})
    assert (await svc.run_pass(db, owner="w2"))["campaign"] is None


# ----------------------------------------- Meta outcomes: person vs campaign

class ScriptedProvider:
    def __init__(self, script):
        self.script, self.calls = script, []

    async def send_header_image_template_outcome(self, phone, template, lang, url, params, extra=None):
        self.calls.append(phone)
        return self.script(len(self.calls), phone)


@pytest.mark.asyncio
async def test_person_errors_do_not_stop_campaign(db, city, monkeypatch):
    await make_people(db, 6, city)
    prov = ScriptedProvider(lambda i, p: SendOutcome(False, error_code=131026, error="undeliverable") if i % 2 else SendOutcome(True, wamid=f"w{i}"))
    monkeypatch.setattr(svc, "get_whatsapp_provider", lambda _db: prov)
    c = await fire(db, sel_all(city))
    assert (await run_all(db))["status"] == "done"
    prog = await svc.progress(db, c["id"])
    assert prog["counts"]["sent"] == 3 and prog["counts"]["failed"] == 3 and prog["failure_reasons"] == {"not_on_whatsapp": 3}
    bad = await svc.list_recipients(db, c["id"], "failed", 1, 50)
    assert bad["total"] == 3 and bad["items"][0]["reason"] == "not_on_whatsapp" and "…" in bad["items"][0]["phone"]


@pytest.mark.asyncio
async def test_template_paused_stops_whole_campaign(db, city, monkeypatch):
    await make_people(db, 30, city)
    prov = ScriptedProvider(lambda i, p: SendOutcome(True, wamid=f"w{i}") if i <= 5 else SendOutcome(False, error_code=132015, error="paused"))
    monkeypatch.setattr(svc, "get_whatsapp_provider", lambda _db: prov)
    c = await fire(db, sel_all(city))
    assert (await run_all(db))["status"] == "stopped"
    prog = await svc.progress(db, c["id"])
    assert prog["status"] == "stopped" and "paused" in prog["stop_reason"]
    assert prog["counts"]["queued"] == 0 and prog["counts"]["sent"] == 5 and prog["counts"]["skipped"] == 25
    assert len(prov.calls) < 30
    # Slot released: a new campaign can start.
    await make_people(db, 1, city)
    monkeypatch.setattr(svc, "get_whatsapp_provider", lambda _db: ScriptedProvider(lambda i, p: SendOutcome(True)))
    await fire(db, sel_all(city), "After")


@pytest.mark.asyncio
async def test_template_unapproved_in_meta_mode_stops(db, city, monkeypatch):
    await make_people(db, 3, city)
    c = await fire(db, sel_all(city))
    monkeypatch.setattr(settings, "WHATSAPP_PROVIDER", "meta_cloud")
    await db.whatsapp_templates.update_one({"name": "blussit_festival_greeting"}, {"$set": {"name": "blussit_festival_greeting", "status": "PAUSED"}}, upsert=True)
    try:
        await svc.run_pass(db)
        assert (await svc.get_campaign(db, c["id"]))["status"] == "stopped"
    finally:
        await db.whatsapp_templates.delete_many({"name": "blussit_festival_greeting"})


@pytest.mark.asyncio
async def test_transport_failure_retries_then_sends(db, city, monkeypatch):
    await make_people(db, 2, city)
    prov = ScriptedProvider(lambda i, p: SendOutcome(False, transport_failure=True, error="5xx") if i <= 2 else SendOutcome(True, wamid=f"w{i}"))
    monkeypatch.setattr(svc, "get_whatsapp_provider", lambda _db: prov)
    monkeypatch.setattr(asyncio, "sleep", _fast_sleep)
    c = await fire(db, sel_all(city))
    await svc.run_pass(db, budget_seconds=1)
    assert (await svc.progress(db, c["id"]))["counts"]["queued"] == 2
    await db[svc.RECIPIENTS].update_many({}, {"$set": {"next_at": datetime.now(timezone.utc) - timedelta(seconds=1)}})
    assert (await run_all(db))["status"] == "done"
    assert (await svc.progress(db, c["id"]))["counts"]["sent"] == 2


_real_sleep = asyncio.sleep


async def _fast_sleep(t, *a, **k):
    await _real_sleep(0)


@pytest.mark.asyncio
async def test_auth_failure_stops(db, city, monkeypatch):
    await make_people(db, 4, city)
    monkeypatch.setattr(svc, "get_whatsapp_provider", lambda _db: ScriptedProvider(lambda i, p: SendOutcome(False, reason="auth")))
    c = await fire(db, sel_all(city))
    assert (await run_all(db))["status"] == "stopped"
    assert "credentials" in (await svc.get_campaign(db, c["id"]))["stop_reason"]


# ----------------------------------------------------------- status webhook

@pytest.mark.asyncio
async def test_status_webhook_updates_counts(db, city):
    await make_people(db, 4, city)
    c = await fire(db, sel_all(city))
    await run_all(db)
    wamids = [r["wamid"] async for r in db[svc.RECIPIENTS].find({"campaign_id": ObjectId(c["id"])})]
    assert len(wamids) == 4 and all(w.startswith("log.") for w in wamids)
    assert await svc.apply_status(db, wamids[0], "delivered")
    assert await svc.apply_status(db, wamids[1], "read")           # read before delivered
    assert not await svc.apply_status(db, wamids[1], "delivered")  # late delivered doesn't downgrade
    assert await svc.apply_status(db, wamids[2], "failed", [{"code": 131026, "title": "Undeliverable"}])
    assert not await svc.apply_status(db, "wamid.unrelated", "delivered")
    counts = (await svc.progress(db, c["id"]))["counts"]
    assert counts == {"queued": 0, "sent": 3, "delivered": 2, "read": 1, "failed": 1, "skipped": 0}
    bad = await svc.list_recipients(db, c["id"], "failed", 1, 10)
    assert bad["items"][0]["reason"] == "not_on_whatsapp"


@pytest.mark.asyncio
async def test_webhook_path_feeds_campaign(db, city):
    from app.services.whatsapp_bot_service import WhatsAppBotService

    await make_people(db, 1, city)
    c = await fire(db, sel_all(city))
    await run_all(db)
    wamid = (await db[svc.RECIPIENTS].find_one({"campaign_id": ObjectId(c["id"])}))["wamid"]
    payload = {"entry": [{"changes": [{"field": "messages", "value": {"statuses": [{"id": wamid, "status": "delivered", "recipient_id": "919999999999"}]}}]}]}
    await WhatsAppBotService(db).handle_webhook(payload)
    assert (await svc.progress(db, c["id"]))["counts"]["delivered"] == 1


# ------------------------------------------------------------ admin access

@pytest.mark.asyncio
async def test_admin_only_access(db):
    admin = await db.users.find_one({"role": "admin"})
    center = await make_service_center(db)
    mgr = await make_manager(db, center)
    cap = await make_captain(db, center)
    cust = await make_customer(db)
    paths = [("get", "/whatsapp/campaigns"), ("get", "/whatsapp/campaigns/audience"), ("get", "/whatsapp/campaigns/limits"),
             ("post", "/whatsapp/campaigns/audience/count"), ("post", f"/whatsapp/campaigns/{ObjectId()}/send"),
             ("post", f"/whatsapp/campaigns/{ObjectId()}/cancel"), ("post", f"/whatsapp/campaigns/{ObjectId()}/test"),
             ("post", "/whatsapp/campaigns/image"), ("post", "/whatsapp/campaigns")]
    async with _client() as client:
        for method, path in paths:
            kw = {"json": {}} if method == "post" else {}
            res = await getattr(client, method)(f"/api/v1{path}", **kw)
            assert res.status_code == 401, (path, res.status_code)
            for uid, role in ((mgr, "manager"), (cap, "captain"), (cust, "customer")):
                res = await getattr(client, method)(f"/api/v1{path}", headers=_auth(uid, role, center if role != "customer" else None), **kw)
                assert res.status_code == 403, (path, role, res.status_code)
        ok = await client.get("/api/v1/whatsapp/campaigns", headers=_auth(str(admin["_id"]), "admin", None, admin.get("token_version", 0)))
        assert ok.status_code == 200


# ----------------------------------------------------- HTTP end-to-end

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


@pytest.mark.asyncio
async def test_http_flow_upload_create_preview_test_send_cancel_audit(db, city, monkeypatch):
    monkeypatch.undo()  # real image validation for this test
    monkeypatch.setattr(settings, "WHATSAPP_CAMPAIGN_MAX_RECIPIENTS", 50000)
    monkeypatch.setattr(settings, "WHATSAPP_CAMPAIGN_DAILY_CAP", 1000000)
    admin = await db.users.find_one({"role": "admin"})
    h = _auth(str(admin["_id"]), "admin", None, admin.get("token_version", 0))
    await db.users.update_one({"_id": admin["_id"]}, {"$set": {"phone": "9111111111"}})
    ids = await make_people(db, 8, city)
    async with _client() as client:
        bad = await client.post("/api/v1/whatsapp/campaigns/image", files={"file": ("a.gif", b"GIF89a", "image/gif")}, headers=h)
        assert bad.status_code == 400
        fake = await client.post("/api/v1/whatsapp/campaigns/image", files={"file": ("a.png", b"<html>not an image</html>", "image/png")}, headers=h)
        assert fake.status_code == 400
        big = await client.post("/api/v1/whatsapp/campaigns/image", files={"file": ("a.png", PNG + b"\x00" * (5 * 1024 * 1024), "image/png")}, headers=h)
        assert big.status_code in (400, 413)
        up = await client.post("/api/v1/whatsapp/campaigns/image", files={"file": ("a.png", PNG, "image/png")}, headers=h)
        assert up.status_code == 200, up.text
        url = up.json()["data"]["url"]
        # An image that was not uploaded here is refused.
        nope = await client.post("/api/v1/whatsapp/campaigns", json={"name": "X1", "message": "hi", "image_url": "https://evil.example.com/a.png"}, headers=h)
        assert nope.status_code == 400
        created = await client.post("/api/v1/whatsapp/campaigns", json={"name": "Diwali 2026", "message": "Happy\nDiwali   from us", "image_url": url}, headers=h)
        assert created.status_code == 200, created.text
        cid = created.json()["data"]["id"]
        assert created.json()["data"]["message"] == "Happy Diwali from us"
        sel = {"mode": "all", "filter": {"city": city}, "excluded_ids": ids[:2]}
        page = await client.post("/api/v1/whatsapp/campaigns/audience/selection?page_size=5", json={"selection": sel}, headers=h)
        d = page.json()["data"]
        assert d["total"] == 8 and d["selected_count"] == 6 and len(d["items"]) == 5 and d["next_after"]
        cnt = await client.post("/api/v1/whatsapp/campaigns/audience/count", json={"selection": sel}, headers=h)
        assert cnt.json()["data"] == {"selected": 6, "matching_total": 8}
        pv = await client.post(f"/api/v1/whatsapp/campaigns/{cid}/preview", json={"selection": sel}, headers=h)
        q = pv.json()["data"]
        assert q["recipient_count"] == 6 and q["cost_paise"] == 6 * 78 and q["can_send"] and q["image_url"] == url
        assert q["eta_seconds"] >= 1 and "opted out" in q["consent_note"]
        t = await client.post(f"/api/v1/whatsapp/campaigns/{cid}/test", headers=h)
        assert t.status_code == 200, t.text
        test_row = await db.whatsapp_outbox.find_one({"kind": "campaign_test", "campaign_id": cid})
        assert test_row and test_row["phone"] == "9111111111"
        wrong = await client.post(f"/api/v1/whatsapp/campaigns/{cid}/send", json={"selection": sel, "confirm_count": 5}, headers=h)
        assert wrong.status_code == 409
        sent = await client.post(f"/api/v1/whatsapp/campaigns/{cid}/send", json={"selection": sel, "confirm_count": 6}, headers=h)
        assert sent.status_code == 200 and sent.json()["data"]["status"] == "queued"
        edit = await client.put(f"/api/v1/whatsapp/campaigns/{cid}", json={"name": "Renamed"}, headers=h)
        assert edit.status_code == 409
        await run_all(db)
        prog = await client.get(f"/api/v1/whatsapp/campaigns/{cid}", headers=h)
        assert prog.json()["data"]["status"] == "done" and prog.json()["data"]["counts"]["sent"] == 6
        lst = await client.get("/api/v1/whatsapp/campaigns", headers=h)
        assert lst.json()["data"]["items"][0]["id"] == cid
        cancel = await client.post(f"/api/v1/whatsapp/campaigns/{cid}/cancel", headers=h)
        assert cancel.json()["data"]["already_finished"]
        lim = await client.put("/api/v1/whatsapp/campaigns/limits", json={"whatsapp_marketing_cost_paise": 90}, headers=h)
        assert lim.json()["data"]["whatsapp_marketing_cost_paise"] == 90
    actions = {a["action"] async for a in db.audit_logs.find({"target_id": cid})}
    assert {"CREATE_CAMPAIGN", "TEST_CAMPAIGN", "SEND_CAMPAIGN"} <= actions
    await db.settings.delete_many({"key": svc.SETTINGS_KEY})
    await db.audit_logs.delete_many({"target_id": cid})
    await db.uploaded_photos.delete_many({"uploader_id": str(admin["_id"])})


# ------------------------------------------------------------ the big one

@pytest.mark.asyncio
async def test_twenty_thousand_recipients_bounded_time_and_memory(db, city):
    await make_people(db, 20000, city)
    sel = sel_all(city, excluded_ids=[])
    t0 = time.perf_counter()
    c = await fire(db, sel)
    api_seconds = time.perf_counter() - t0
    assert c["total_recipients"] == 20000 and api_seconds < 5, api_seconds
    doc = await svc.get_campaign(db, c["id"])
    tracemalloc.start()
    t0 = time.perf_counter()
    batches = 0
    while not doc.get("materialized"):
        await svc.materialize_batch(db, doc)
        batches += 1
    elapsed = time.perf_counter() - t0
    _cur, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    assert await db[svc.RECIPIENTS].count_documents({"campaign_id": doc["_id"]}) == 20000
    assert batches == 20
    assert elapsed < 30, elapsed
    assert peak < 60 * 1024 * 1024, peak
    # A slice of the drain at full speed.
    r = await svc.run_pass(db, budget_seconds=3)
    assert r["sent"] > 0
