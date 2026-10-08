"""
NOTIFY feature pass (2026-10-07), spec 1.7 / 1.8:

  - the manager's "WhatsApp me new bookings" switch (on by default; off =
    in-app only), its API (self only for managers) and UserPublic field;
  - the universal message (admin / manager -> one customer, via its
    template, audited, manager limited to customers known to the center);
  - the admin's Google review URL setting (https only).
"""
from datetime import datetime, timezone

import pytest
from bson import ObjectId
from httpx import ASGITransport, AsyncClient

from app.models.enums import NotificationType
from app.schemas.user_schema import UserPublic
from app.services.notification_service import NotificationService

from tests.factories import make_customer, make_manager, make_service_center

pytestmark = pytest.mark.asyncio

MGR_PARAMS = ["Asha", "9000000000", "Hatchback", "Star Wash", "8 Oct · 9:00 AM", "Vijay Nagar"]


def _auth(user_id: str, role: str, center_id: str | None = None) -> dict:
    from app.core.security import create_access_token

    return {"Authorization": f"Bearer {create_access_token(user_id, role, {'service_center_id': center_id, 'tv': 0})}"}


async def _client():
    from app.main import app

    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


@pytest.fixture
async def center_manager(db, cleanup):
    center_id = await make_service_center(db)
    manager_id = await make_manager(db, center_id)
    phone = (await db.users.find_one({"_id": ObjectId(manager_id)}))["phone"]
    for coll, flt in (
        ("users", {"_id": ObjectId(manager_id)}), ("service_centers", {"_id": ObjectId(center_id)}),
        ("notifications", {"user_id": manager_id}), ("whatsapp_outbox", {"phone": phone}),
        ("whatsapp_queue", {"user_id": manager_id}), ("audit_logs", {"actor_id": manager_id}),
    ):
        cleanup.append((coll, flt))
    return {"id": manager_id, "center_id": center_id, "phone": phone}


async def _customer(db, cleanup) -> tuple[str, str]:
    cid = await make_customer(db)
    phone = (await db.users.find_one({"_id": ObjectId(cid)}))["phone"]
    for coll, flt in (
        ("users", {"_id": ObjectId(cid)}), ("notifications", {"user_id": cid}),
        ("whatsapp_outbox", {"phone": phone}), ("whatsapp_queue", {"user_id": cid}),
    ):
        cleanup.append((coll, flt))
    return cid, phone


# ------------------------------------------------------------ manager toggle


async def test_toggle_defaults_on_and_off_means_in_app_only(db, center_manager):
    notes = NotificationService(db)
    await notes.notify(center_manager["id"], "New booking — Star Wash", "details", NotificationType.BOOKING,
                       str(ObjectId()), wa_event="manager_new_booking", wa_params=MGR_PARAMS)
    assert await db.whatsapp_outbox.count_documents({"phone": center_manager["phone"]}) == 1

    await db.users.update_one({"_id": ObjectId(center_manager["id"])}, {"$set": {"whatsapp_new_booking_alerts": False}})
    await notes.notify(center_manager["id"], "New booking — Foam Wash", "details", NotificationType.BOOKING,
                       str(ObjectId()), wa_event="manager_new_booking", wa_params=MGR_PARAMS)
    assert await db.whatsapp_outbox.count_documents({"phone": center_manager["phone"]}) == 1  # no new WhatsApp
    assert await db.notifications.count_documents({"user_id": center_manager["id"]}) == 2  # bell still has both


async def test_other_manager_alerts_stay_in_app_even_when_toggle_on(db, center_manager):
    await NotificationService(db).notify(center_manager["id"], "Still needs a captain", "details",
                                         wa_event="captain_assigned", wa_params=["x"])
    assert await db.whatsapp_outbox.count_documents({"phone": center_manager["phone"]}) == 0


async def test_user_public_exposes_the_toggle(db, center_manager):
    doc = await db.users.find_one({"_id": ObjectId(center_manager["id"])})
    assert UserPublic.from_doc(doc).whatsapp_new_booking_alerts is True
    doc["whatsapp_new_booking_alerts"] = False
    assert UserPublic.from_doc(doc).whatsapp_new_booking_alerts is False


async def test_preferences_api_self_only_for_managers(db, center_manager, cleanup):
    other_id = await make_manager(db, center_manager["center_id"])
    cleanup.append(("users", {"_id": ObjectId(other_id)}))
    headers = _auth(center_manager["id"], "manager", center_manager["center_id"])
    async with await _client() as client:
        res = await client.get("/api/v1/notifications/preferences", headers=headers)
        assert res.status_code == 200, res.text
        assert res.json()["data"]["whatsapp_new_booking_alerts"] is True

        res = await client.put("/api/v1/notifications/preferences", headers=headers, json={"whatsapp_new_booking_alerts": False})
        assert res.status_code == 200, res.text
        assert res.json()["data"]["whatsapp_new_booking_alerts"] is False
        assert (await db.users.find_one({"_id": ObjectId(center_manager["id"])}))["whatsapp_new_booking_alerts"] is False
        assert await db.audit_logs.count_documents({"actor_id": center_manager["id"], "action": "UPDATE_NOTIFICATION_PREFERENCES"}) == 1

        # A manager can't switch another manager's alerts.
        res = await client.put("/api/v1/notifications/preferences", headers=headers,
                               json={"whatsapp_new_booking_alerts": False, "user_id": other_id})
        assert res.status_code == 403, res.text
        assert (await db.users.find_one({"_id": ObjectId(other_id)})).get("whatsapp_new_booking_alerts") is None

        # An admin can.
        admin = await db.users.find_one({"role": "admin"})
        admin_headers = _auth(str(admin["_id"]), "admin")
        res = await client.put("/api/v1/notifications/preferences", headers=admin_headers,
                               json={"whatsapp_new_booking_alerts": False, "user_id": other_id})
        assert res.status_code == 200, res.text
        assert (await db.users.find_one({"_id": ObjectId(other_id)}))["whatsapp_new_booking_alerts"] is False
        cleanup.append(("audit_logs", {"actor_id": str(admin["_id"]), "action": "UPDATE_NOTIFICATION_PREFERENCES"}))


async def test_preferences_api_refuses_customers(db, cleanup):
    cid, _ = await _customer(db, cleanup)
    async with await _client() as client:
        res = await client.put("/api/v1/notifications/preferences", headers=_auth(cid, "customer"),
                               json={"whatsapp_new_booking_alerts": False})
    assert res.status_code == 403


# ------------------------------------------------------------ universal message


async def _approve(db, cleanup, name: str, body: str, params: int, category: str = "UTILITY", language: str = "en_US"):
    await db.whatsapp_templates.update_one(
        {"name": name},
        {"$set": {"name": name, "status": "APPROVED", "category": category, "language": language, "body": body,
                  "param_count": params, "has_url_param": False, "disabled": False}},
        upsert=True,
    )
    cleanup.append(("whatsapp_templates", {"name": name}))


async def test_admin_sends_universal_message_via_its_template(db, cleanup):
    from app.services.whatsapp_crm_service import EVENT_TEMPLATES

    cid, phone = await _customer(db, cleanup)
    name = EVENT_TEMPLATES["universal_message"]
    await _approve(db, cleanup, name, "Hi {{1}},\n{{2}}\n— Team Blussit", 2)
    admin = await db.users.find_one({"role": "admin"})
    cleanup.append(("audit_logs", {"actor_id": str(admin["_id"]), "action": "WHATSAPP_UNIVERSAL_MESSAGE"}))
    async with await _client() as client:
        res = await client.post("/api/v1/notifications/universal-message", headers=_auth(str(admin["_id"]), "admin"),
                                json={"customer_id": cid, "message": "Your car is ready for pickup.\nThanks!"})
    assert res.status_code == 200, res.text
    data = res.json()["data"]
    assert data["status"] == "sent" and data["template_name"] == name
    out = await db.whatsapp_outbox.find_one({"phone": phone})
    assert out["template_name"] == name and "Your car is ready for pickup." in out["message"]
    assert await db.notifications.count_documents({"user_id": cid}) == 1
    assert await db.audit_logs.count_documents({"actor_id": str(admin["_id"]), "action": "WHATSAPP_UNIVERSAL_MESSAGE", "target_id": cid}) == 1


async def test_manager_universal_message_scoped_to_center(db, center_manager, cleanup):
    known, known_phone = await _customer(db, cleanup)
    stranger, _ = await _customer(db, cleanup)
    booking = await db.bookings.insert_one({
        "customer_id": known, "service_center_id": center_manager["center_id"], "status": "completed",
        "booking_number": f"BK-UNI{ObjectId()}", "is_deleted": False, "created_at": datetime.now(timezone.utc),
    })
    cleanup.append(("bookings", {"_id": booking.inserted_id}))
    headers = _auth(center_manager["id"], "manager", center_manager["center_id"])
    async with await _client() as client:
        res = await client.post("/api/v1/notifications/universal-message", headers=headers,
                                json={"customer_id": stranger, "message": "Hello there"})
        assert res.status_code == 404, res.text
        res = await client.post("/api/v1/notifications/universal-message", headers=headers,
                                json={"customer_id": known, "message": "Hello there"})
        assert res.status_code == 200, res.text
        # Customers / captains can't use it.
        res = await client.post("/api/v1/notifications/universal-message", headers=_auth(known, "customer"),
                                json={"customer_id": known, "message": "Hello there"})
        assert res.status_code == 403
        # Empty message refused.
        res = await client.post("/api/v1/notifications/universal-message", headers=headers,
                                json={"customer_id": known, "message": "   "})
        assert res.status_code in (400, 422)
    assert await db.whatsapp_outbox.count_documents({"phone": known_phone}) == 1


async def test_universal_message_never_free_text_outside_window_in_production(db, cleanup, monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "APP_ENV", "production")
    monkeypatch.setattr(settings, "WHATSAPP_UPDATE_TEMPLATE_NAME", "")
    cid, phone = await _customer(db, cleanup)
    admin = await db.users.find_one({"role": "admin"})
    cleanup.append(("audit_logs", {"actor_id": str(admin["_id"]), "action": "WHATSAPP_UNIVERSAL_MESSAGE"}))
    # No approved universal template and no generic one: refused, not sent as free text.
    await db.whatsapp_templates.delete_many({"name": {"$regex": "^blussit_universal_message"}})
    result = await NotificationService(db).send_universal_message(str(admin["_id"]), "admin", None, cid, "Hello there")
    assert result["status"] == "failed" and result["failure"] == "no_template"
    rows = await db.whatsapp_outbox.find({"phone": phone}).to_list(None)
    assert all(r.get("ok") is False for r in rows)


# ------------------------------------------------------------ google review URL


async def test_google_review_url_setting_https_only_admin_only(db, center_manager, cleanup):
    cleanup.append(("settings", {"key": "whatsapp_settings"}))
    cleanup.append(("settings_history", {"key": "whatsapp_settings"}))
    admin = await db.users.find_one({"role": "admin"})
    cleanup.append(("audit_logs", {"actor_id": str(admin["_id"]), "action": "UPDATE_WHATSAPP_SETTINGS"}))
    headers = _auth(str(admin["_id"]), "admin")
    async with await _client() as client:
        res = await client.get("/api/v1/whatsapp/crm/settings", headers=headers)
        assert res.status_code == 200 and res.json()["data"]["google_review_url"] == ""
        for bad in ("http://g.page/r/abc/review", "javascript:alert(1)", "https://", "ftp://x.y", "https://exa mple.com/x"):
            res = await client.put("/api/v1/whatsapp/crm/settings", headers=headers, json={"google_review_url": bad})
            assert res.status_code in (400, 422), (bad, res.text)
        res = await client.put("/api/v1/whatsapp/crm/settings", headers=headers,
                               json={"google_review_url": "https://g.page/r/CblussitTest/review"})
        assert res.status_code == 200, res.text
        assert res.json()["data"]["google_review_url"] == "https://g.page/r/CblussitTest/review"
        res = await client.get("/api/v1/whatsapp/crm/settings", headers=_auth(center_manager["id"], "manager", center_manager["center_id"]))
        assert res.status_code == 403
        # Clearing it is allowed (turns the review sweep off).
        res = await client.put("/api/v1/whatsapp/crm/settings", headers=headers, json={"google_review_url": ""})
        assert res.status_code == 200 and res.json()["data"]["google_review_url"] == ""
    assert await db.audit_logs.count_documents({"actor_id": str(admin["_id"]), "action": "UPDATE_WHATSAPP_SETTINGS"}) >= 2
