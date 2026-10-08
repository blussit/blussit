"""
NOTIFY feature pass (2026-10-07) — why a manager's new-booking WhatsApp
could vanish, and the fixes (a)–(k):

  (a) manager phone == business number -> queue row failure "business_number"
      + a config warning in delivery health;
  (b) no phone -> queue row failure "no_recipient" (was a silent return);
  (c) config warnings when the manager / generic template isn't approved;
  (d) Meta 401/403 -> failure "auth", ERROR log, retried a few times, dead;
  (e) the template's own language is used;
  (f) template sync pages past 100;
  (g) a configured template name a completed sync didn't return is unusable;
  (h) a failed delivery reported by the status webhook updates the queue;
  (i) leading 0 / +91 / 91 numbers normalise to 91XXXXXXXXXX;
  (k) a template Meta re-categorised as MARKETING respects opt-outs.

Never calls Meta: the Cloud provider runs against a fake transport.
"""
import logging
import uuid
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from bson import ObjectId

from app.core.config import settings
from app.models.enums import NotificationType
from app.services import whatsapp_service
from app.services.notification_service import NotificationService
from app.services.whatsapp_crm_service import EVENT_TEMPLATES, WhatsAppCrmService
from app.services.whatsapp_service import MetaCloudWhatsAppProvider, WhatsAppService
from app.utils.phone import to_whatsapp_e164

from tests.factories import make_customer, make_manager, make_service_center

pytestmark = pytest.mark.asyncio

MGR_PARAMS = ["Asha", "9000000000", "Hatchback", "Star Wash", "8 Oct · 9:00 AM", "Vijay Nagar"]
GENERIC = "blussit_feat_generic_update"
SYNC_STATE = {"key": "whatsapp_template_sync"}


@pytest.fixture
def meta(monkeypatch):
    """Cloud provider over a fake transport. install(respond) where
    respond(to, template_name, payload) -> httpx.Response (or raises)."""
    monkeypatch.setattr(whatsapp_service.settings, "WHATSAPP_PROVIDER", "meta_cloud")
    monkeypatch.setattr(whatsapp_service.settings, "WHATSAPP_ACCESS_TOKEN", "x")
    monkeypatch.setattr(whatsapp_service.settings, "WHATSAPP_PHONE_NUMBER_ID", "123")
    monkeypatch.setattr(whatsapp_service.settings, "WHATSAPP_UPDATE_TEMPLATE_NAME", GENERIC)
    calls: list[dict] = []

    def install(respond, list_pages=None):
        class _FakeMeta:
            async def post(self, url, json=None, headers=None, **kw):
                payload = json or {}
                name = (payload.get("template") or {}).get("name") or payload.get("type")
                calls.append({"to": payload.get("to", ""), "name": name, "payload": payload})
                return await respond(payload.get("to", ""), name, payload)

            async def get(self, url, headers=None, params=None, **kw):
                calls.append({"get": url, "params": params})
                return list_pages(url, params)

        monkeypatch.setattr(whatsapp_service, "_meta_http", lambda: _FakeMeta())
        return calls

    return install


def _ok(payload=None):
    req = httpx.Request("POST", "https://graph.facebook.com/x")
    return httpx.Response(200, json={"messages": [{"id": f"wamid.FEAT{uuid.uuid4().hex[:12]}"}]}, request=req)


async def _ok_respond(to, name, payload):
    return _ok()


async def _customer(db, cleanup, **extra) -> tuple[str, str]:
    cid = await make_customer(db)
    if extra:
        await db.users.update_one({"_id": ObjectId(cid)}, {"$set": extra})
    phone = (await db.users.find_one({"_id": ObjectId(cid)}))["phone"]
    for coll, flt in (
        ("users", {"_id": ObjectId(cid)}), ("notifications", {"user_id": cid}),
        ("whatsapp_outbox", {"phone": phone}), ("whatsapp_queue", {"user_id": cid}),
    ):
        cleanup.append((coll, flt))
    return cid, phone


async def _manager(db, cleanup, **extra) -> dict:
    center_id = await make_service_center(db)
    mid = await make_manager(db, center_id)
    if extra:
        await db.users.update_one({"_id": ObjectId(mid)}, {"$set": extra})
    phone = (await db.users.find_one({"_id": ObjectId(mid)})).get("phone")
    for coll, flt in (
        ("users", {"_id": ObjectId(mid)}), ("service_centers", {"_id": ObjectId(center_id)}),
        ("notifications", {"user_id": mid}), ("whatsapp_queue", {"user_id": mid}),
    ):
        cleanup.append((coll, flt))
    if phone:
        cleanup.append(("whatsapp_outbox", {"phone": phone}))
    return {"id": mid, "center_id": center_id, "phone": phone}


async def _template(db, cleanup, name: str, body: str = "x {{1}} y {{2}}", status: str = "APPROVED", **extra):
    await db.whatsapp_templates.update_one(
        {"name": name},
        {"$set": {"name": name, "status": status, "category": extra.pop("category", "UTILITY"),
                  "language": extra.pop("language", "en_US"), "body": body, "param_count": body.count("{{"),
                  "has_url_param": extra.pop("has_url_param", False), "disabled": False, **extra}},
        upsert=True,
    )
    cleanup.append(("whatsapp_templates", {"name": name}))


async def _new_booking(db, user_id: str, **kw):
    await NotificationService(db).notify(
        user_id, "New booking — Star Wash", "details", NotificationType.BOOKING, str(ObjectId()),
        wa_event="manager_new_booking", wa_params=MGR_PARAMS, background=False, **kw,
    )


def _codes(warnings: list[dict]) -> set[str]:
    return {w["code"] for w in warnings}


# ---------------------------------------------------------------- (a)
async def test_manager_on_business_number_is_a_visible_failure(db, cleanup, monkeypatch):
    monkeypatch.setattr(settings, "WHATSAPP_BUSINESS_NUMBER", "918962200077")
    mgr = await _manager(db, cleanup)
    await db.users.update_one({"_id": ObjectId(mgr["id"])}, {"$set": {"phone": "8962200077"}})
    cleanup.append(("whatsapp_outbox", {"phone": "8962200077"}))
    await _new_booking(db, mgr["id"])
    row = await db.whatsapp_queue.find_one({"user_id": mgr["id"]})
    assert row is not None and row["status"] == "failed" and row["failure"] == "business_number"
    warnings = await NotificationService(db).config_warnings()
    mine = [w for w in warnings if w["code"] == "manager_business_number" and w.get("user_id") == mgr["id"]]
    assert mine and mine[0]["severity"] == "error" and "8962200077" not in str(mine[0])  # masked
    health = await NotificationService(db).delivery_health(days=1)
    assert health["failures_by_reason"].get("business_number", 0) >= 1
    assert "config_warnings" in health


# ---------------------------------------------------------------- (b)
async def test_manager_without_phone_is_counted_not_silent(db, cleanup):
    mgr = await _manager(db, cleanup)
    await db.users.update_one({"_id": ObjectId(mgr["id"])}, {"$unset": {"phone": ""}})
    await _new_booking(db, mgr["id"])
    row = await db.whatsapp_queue.find_one({"user_id": mgr["id"]})
    assert row is not None and row["status"] == "failed" and row["failure"] == "no_recipient"
    assert await db.notifications.count_documents({"user_id": mgr["id"]}) == 1
    health = await NotificationService(db).delivery_health(days=1)
    assert health["failures_by_reason"].get("no_recipient", 0) >= 1
    assert any(w["code"] == "manager_no_phone" and w.get("user_id") == mgr["id"] for w in health["config_warnings"])


async def test_unverified_manager_phone_and_alerts_off_are_reported(db, cleanup):
    mgr = await _manager(db, cleanup, phone_verified=False, whatsapp_new_booking_alerts=False)
    warnings = await NotificationService(db).config_warnings()
    codes = {w["code"] for w in warnings if w.get("user_id") == mgr["id"]}
    assert {"manager_phone_unverified", "manager_alerts_off"} <= codes


# ---------------------------------------------------------------- (c)
async def test_template_config_warnings(db, cleanup, monkeypatch):
    manager_names = [EVENT_TEMPLATES["manager_new_booking"], "blussit_manager_new_booking_v1"]
    for name in manager_names:
        await _template(db, cleanup, name, status="PENDING")
    monkeypatch.setattr(settings, "WHATSAPP_UPDATE_TEMPLATE_NAME", "")
    codes = _codes(await NotificationService(db).config_warnings())
    assert {"generic_template_not_approved", "manager_template_not_approved"} <= codes

    monkeypatch.setattr(settings, "WHATSAPP_UPDATE_TEMPLATE_NAME", GENERIC)
    await _template(db, cleanup, GENERIC, "*{{1}}*\n{{2}}\nCall us for any query.")
    await _template(db, cleanup, EVENT_TEMPLATES["manager_new_booking"], "New Booking {{1}} {{2}} {{3}} {{4}} {{5}} {{6}} end")
    codes = _codes(await NotificationService(db).config_warnings())
    assert "generic_template_not_approved" not in codes and "manager_template_not_approved" not in codes


# ---------------------------------------------------------------- (d)
async def test_meta_401_is_an_auth_failure_retried_then_dead(db, cleanup, meta, caplog):
    cid, phone = await _customer(db, cleanup)

    async def respond(to, name, payload):
        return httpx.Response(401, json={"error": {"code": 190, "message": "Error validating access token"}},
                              request=httpx.Request("POST", "https://graph.facebook.com/x"))

    calls = meta(respond)
    service = NotificationService(db)
    with caplog.at_level(logging.ERROR):
        await service.notify(cid, "Captain assigned", "Ravi is your captain.", NotificationType.BOOKING, str(ObjectId()), background=False)
    row = await db.whatsapp_queue.find_one({"user_id": cid})
    assert row["status"] == "pending" and row["failure"] == "auth"
    assert any("access token" in r.getMessage().lower() or "credentials" in r.getMessage().lower() for r in caplog.records if r.levelno >= logging.ERROR)
    out = await db.whatsapp_outbox.find_one({"phone": phone})
    assert out["failure"] == "auth"
    for _ in range(5):
        await db.whatsapp_queue.update_one({"_id": row["_id"]}, {"$set": {"next_at": datetime.now(timezone.utc) - timedelta(seconds=1)}})
        await service.retry_outbox()
    row = await db.whatsapp_queue.find_one({"user_id": cid})
    assert row["status"] == "dead" and row["failure"] == "auth"
    assert row["attempts"] <= 4, "a credentials problem isn't retried for hours"
    assert len([c for c in calls if c.get("to", "").endswith(phone)]) == row["attempts"]
    assert "whatsapp_auth_failed" in _codes(await service.config_warnings())
    await db.whatsapp_queue.delete_many({"failure": "auth", "user_id": cid})


async def test_event_template_auth_failure_does_not_try_the_generic(db, cleanup, meta):
    cid, phone = await _customer(db, cleanup)
    await _template(db, cleanup, EVENT_TEMPLATES["captain_assigned"], "a {{1}} b {{2}} c {{3}} d {{4}} e {{5}} f {{6}} g")

    async def respond(to, name, payload):
        return httpx.Response(403, json={"error": {"code": 200, "message": "Permissions error"}},
                              request=httpx.Request("POST", "https://graph.facebook.com/x"))

    calls = meta(respond)
    await NotificationService(db).notify(cid, "Captain assigned", "x", NotificationType.BOOKING, str(ObjectId()),
                                         wa_event="captain_assigned", wa_params=["a", "b", "c", "d", "e", "f"], background=False)
    mine = [c for c in calls if c.get("to", "").endswith(phone)]
    assert len(mine) == 1 and mine[0]["name"] == EVENT_TEMPLATES["captain_assigned"]
    assert (await db.whatsapp_queue.find_one({"user_id": cid}))["failure"] == "auth"
    await db.whatsapp_queue.delete_many({"user_id": cid})


# ---------------------------------------------------------------- (e)
async def test_template_language_comes_from_the_template_row(db, cleanup, meta):
    cid, phone = await _customer(db, cleanup)
    await _template(db, cleanup, GENERIC, "*{{1}}*\n{{2}}\nCall us for any query.", language="en")
    await _template(db, cleanup, EVENT_TEMPLATES["captain_assigned"], "a {{1}} b {{2}} c {{3}} d {{4}} e {{5}} f {{6}} g", language="en_GB")
    calls = meta(_ok_respond)
    notes = NotificationService(db)
    await notes.notify(cid, "Complaint update", "Your complaint has an update.", background=False)
    await notes.notify(cid, "Captain assigned", "x", NotificationType.BOOKING, str(ObjectId()),
                       wa_event="captain_assigned", wa_params=["a", "b", "c", "d", "e", "f"], background=False)
    langs = {c["name"]: c["payload"]["template"]["language"]["code"] for c in calls if c.get("to", "").endswith(phone)}
    assert langs == {GENERIC: "en", EVENT_TEMPLATES["captain_assigned"]: "en_GB"}


# ---------------------------------------------------------------- (f) + (g)
async def test_template_sync_pages_and_unknown_names_become_unusable(db, cleanup, meta, monkeypatch):
    monkeypatch.setattr(whatsapp_service.settings, "WHATSAPP_BUSINESS_ACCOUNT_ID", "waba1")
    cleanup.append(("settings", SYNC_STATE))
    names_p1 = [f"blussit_feat_page_{i}" for i in range(100)]
    names_p2 = ["blussit_feat_page_last", GENERIC]
    for n in names_p1 + names_p2:
        cleanup.append(("whatsapp_templates", {"name": n}))

    def page(name):
        return {"name": name, "status": "APPROVED", "category": "UTILITY", "language": "en",
                "components": [{"type": "BODY", "text": "Hi {{1}} there {{2}} ok"}]}

    def list_pages(url, params):
        req = httpx.Request("GET", url)
        if "after=P2" in url:
            return httpx.Response(200, json={"data": [page(n) for n in names_p2]}, request=req)
        return httpx.Response(200, json={"data": [page(n) for n in names_p1],
                                          "paging": {"next": "https://graph.facebook.com/v21.0/waba1/message_templates?after=P2"}}, request=req)

    meta(_ok_respond, list_pages)
    # Before any completed sync, a configured name is trusted.
    monkeypatch.setattr(whatsapp_service.settings, "WHATSAPP_TEMP_PASSWORD_TEMPLATE_NAME", "blussit_feat_typo_name")
    wa = WhatsAppService(db)
    assert await wa._usable_template("blussit_feat_typo_name") is not None

    result = await WhatsAppCrmService(db).sync_templates()
    assert result["synced"] == 102
    assert await db.whatsapp_templates.find_one({"name": "blussit_feat_page_last"})
    # After a completed sync, a name Meta didn't return is unusable…
    assert await wa._usable_template("blussit_feat_typo_name") is None
    assert await wa._usable_template(GENERIC) is not None
    codes = _codes(await NotificationService(db).config_warnings())
    assert "template_unknown" in codes


async def test_failed_page_is_not_a_completed_sync(db, cleanup, meta, monkeypatch):
    monkeypatch.setattr(whatsapp_service.settings, "WHATSAPP_BUSINESS_ACCOUNT_ID", "waba1")
    cleanup.append(("settings", SYNC_STATE))
    cleanup.append(("whatsapp_templates", {"name": "blussit_feat_half"}))
    await db.settings.delete_many(SYNC_STATE)

    def list_pages(url, params):
        req = httpx.Request("GET", url)
        if "after=P2" in url:
            return httpx.Response(500, json={"error": {"message": "boom"}}, request=req)
        return httpx.Response(200, json={"data": [{"name": "blussit_feat_half", "status": "APPROVED", "components": []}],
                                          "paging": {"next": "https://graph.facebook.com/v21.0/waba1/message_templates?after=P2"}}, request=req)

    meta(_ok_respond, list_pages)
    assert await WhatsAppService(db).list_templates() is None
    assert await db.settings.find_one(SYNC_STATE) is None


# ---------------------------------------------------------------- (h)
async def test_status_webhook_failure_marks_the_queue_row_undelivered(db, cleanup, meta):
    from app.services.whatsapp_bot_service import WhatsAppBotService

    cid, phone = await _customer(db, cleanup)
    await _template(db, cleanup, GENERIC, "*{{1}}*\n{{2}}\nCall us for any query.")
    meta(_ok_respond)
    await NotificationService(db).notify(cid, "Complaint update", "Your complaint has an update.", background=False)
    row = await db.whatsapp_queue.find_one({"user_id": cid})
    assert row["status"] == "sent"
    wamid = (await db.whatsapp_outbox.find_one({"phone": phone, "queue_id": row["_id"]}))["wamid"]
    await WhatsAppBotService(db).handle_webhook({"entry": [{"changes": [{"field": "messages", "value": {"statuses": [{
        "id": wamid, "status": "failed", "recipient_id": f"91{phone}",
        "errors": [{"code": 131026, "title": "Message undeliverable", "message": "Receiver is incapable of receiving this message"}],
    }]}}]}]})
    row = await db.whatsapp_queue.find_one({"user_id": cid})
    assert row["status"] == "undelivered" and row["failure"] == "undelivered"
    assert "131026" in (row.get("last_error") or "")
    health = await NotificationService(db).delivery_health(days=1)
    assert health["queue"].get("undelivered", 0) >= 1
    assert health["failures_by_reason"].get("undelivered", 0) >= 1
    assert any(r["id"] == row["_id"] for r in health["recent_failures"])


async def test_delivered_status_is_recorded_without_changing_sent(db, cleanup, meta):
    from app.services.whatsapp_bot_service import WhatsAppBotService

    cid, phone = await _customer(db, cleanup)
    await _template(db, cleanup, GENERIC, "*{{1}}*\n{{2}}\nCall us for any query.")
    meta(_ok_respond)
    await NotificationService(db).notify(cid, "Complaint update", "Your complaint has an update.", background=False)
    row = await db.whatsapp_queue.find_one({"user_id": cid})
    wamid = (await db.whatsapp_outbox.find_one({"phone": phone, "queue_id": row["_id"]}))["wamid"]
    await WhatsAppBotService(db).handle_webhook({"entry": [{"changes": [{"field": "messages", "value": {"statuses": [{
        "id": wamid, "status": "delivered", "recipient_id": f"91{phone}"}]}}]}]})
    row = await db.whatsapp_queue.find_one({"user_id": cid})
    assert row["status"] == "sent" and row.get("delivery_status") == "delivered"


# ---------------------------------------------------------------- (i)
def test_phone_normalisation_for_meta():
    for raw in ("9876543210", "09876543210", "+91 98765 43210", "919876543210", "+91-98765-43210", "0091 98765 43210"):
        assert to_whatsapp_e164(raw) == "919876543210", raw
    assert to_whatsapp_e164("+44 7700 900123") == "447700900123"


async def test_provider_sends_normalised_number(db, cleanup, meta):
    calls = meta(_ok_respond)
    cleanup.append(("whatsapp_outbox", {"phone": "09876500011"}))
    provider = MetaCloudWhatsAppProvider(db, "x", "123", "v21.0")
    assert await provider.send_template("09876500011", "any_tpl", "en_US", ["a"]) is True
    assert calls[-1]["to"] == "919876500011"


# ---------------------------------------------------------------- (k)
async def test_recategorised_marketing_template_respects_opt_out(db, cleanup, meta):
    opted, opted_phone = await _customer(db, cleanup, marketing_opt_out=True)
    open_, open_phone = await _customer(db, cleanup)
    name = EVENT_TEMPLATES["captain_assigned"]
    await _template(db, cleanup, name, "a {{1}} b {{2}} c {{3}} d {{4}} e {{5}} f {{6}} g", category="MARKETING")
    await _template(db, cleanup, GENERIC, "*{{1}}*\n{{2}}\nCall us for any query.")
    calls = meta(_ok_respond)
    for cid in (opted, open_):
        await NotificationService(db).notify(cid, "Captain assigned", "Ravi is on your booking.", NotificationType.BOOKING,
                                             str(ObjectId()), wa_event="captain_assigned", wa_params=list("abcdef"), background=False)
    by_phone = {}
    for c in calls:
        by_phone.setdefault(c["to"][-10:], []).append(c["name"])
    # Not opted out: the (now marketing) event template is fine.
    assert by_phone[open_phone] == [name]
    # Opted out: never the marketing template — the utility update instead.
    assert by_phone[opted_phone] == [GENERIC]


async def test_marketing_generic_template_is_not_sent_to_opted_out(db, cleanup, meta):
    opted, opted_phone = await _customer(db, cleanup, marketing_opt_out=True)
    await _template(db, cleanup, GENERIC, "*{{1}}*\n{{2}}\nCall us for any query.", category="MARKETING")
    calls = meta(_ok_respond)
    await NotificationService(db).notify(opted, "Complaint update", "Your complaint has an update.", background=False)
    assert not [c for c in calls if c.get("to", "").endswith(opted_phone)]
    row = await db.whatsapp_queue.find_one({"user_id": opted})
    assert row["status"] == "failed" and row["failure"] == "opted_out"
