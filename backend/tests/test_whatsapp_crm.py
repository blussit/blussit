"""
WhatsApp CRM — the contracts that make the inbox trustworthy:
  - every inbound webhook message is recorded even though the bot also
    processes it, and conversations track unread/last-message state;
  - a resolved conversation reopens when the customer writes again;
  - the 24h customer-service window is ENFORCED server-side on free-text
    sends (not just hinted in the UI);
  - an agent's manual message pauses the booking bot for that thread,
    and resolving hands the customer back to the bot;
  - OTP / temp-password traffic is redacted in threads;
  - the automation engine only uses a per-event template once it is
    APPROVED, falling back to the generic update template otherwise.
"""
from datetime import datetime, timedelta, timezone

import pytest

from app.core.exceptions import BadRequestException
from app.services.whatsapp_bot_service import WhatsAppBotService
from app.services.whatsapp_crm_service import WhatsAppCrmService

from tests.test_whatsapp_bot import wa_payload


def _cleanup_wa(cleanup, wa_id, phone):
    cleanup.append(("whatsapp_conversations", {"wa_id": wa_id}))
    cleanup.append(("whatsapp_inbox", {"wa_id": wa_id}))
    cleanup.append(("whatsapp_outbox", {"phone": {"$in": [phone, wa_id]}}))
    cleanup.append(("whatsapp_message_dedup", {"wamid": {"$regex": "^wamid.TEST"}}))
    cleanup.append(("users", {"phone": phone}))


@pytest.mark.asyncio
async def test_inbound_recorded_and_conversation_upserted(db, cleanup):
    wa_id, phone = "918887771001", "8887771001"
    _cleanup_wa(cleanup, wa_id, phone)
    await WhatsAppBotService(db).handle_webhook(wa_payload(wa_id, text="hello CRM", name="CRM Tester"))

    row = await db.whatsapp_inbox.find_one({"wa_id": wa_id})
    assert row and row["text"] == "hello CRM" and row["message_type"] == "text"
    convo = await db.whatsapp_conversations.find_one({"wa_id": wa_id})
    assert convo["unread_count"] == 1
    assert convo["last_message_direction"] == "in"
    assert convo.get("crm_status", "open") == "open"

    # Thread merge: the inbound + the bot's outbound replies both appear.
    thread = await WhatsAppCrmService(db).get_thread(wa_id)
    directions = {m["direction"] for m in thread["messages"]}
    assert directions == {"in", "out"}
    assert thread["conversation"]["window"]["active"] is True


@pytest.mark.asyncio
async def test_resolved_conversation_reopens_on_new_message(db, cleanup):
    wa_id, phone = "918887771002", "8887771002"
    _cleanup_wa(cleanup, wa_id, phone)
    bot = WhatsAppBotService(db)
    await bot.handle_webhook(wa_payload(wa_id, text="hi"))
    crm = WhatsAppCrmService(db)
    await crm.set_status(wa_id, "resolved")
    assert (await db.whatsapp_conversations.find_one({"wa_id": wa_id}))["crm_status"] == "resolved"
    await bot.handle_webhook(wa_payload(wa_id, text="one more thing"))
    assert (await db.whatsapp_conversations.find_one({"wa_id": wa_id}))["crm_status"] == "open"


@pytest.mark.asyncio
async def test_window_enforced_on_free_text(db, cleanup):
    wa_id, phone = "918887771003", "8887771003"
    _cleanup_wa(cleanup, wa_id, phone)
    await WhatsAppBotService(db).handle_webhook(wa_payload(wa_id, text="hi"))
    crm = WhatsAppCrmService(db)

    # Inside the window: send succeeds (log provider) and pauses the bot.
    await crm.send_text(wa_id, "Agent here, happy to help!", agent_id="000000000000000000000001")
    convo = await db.whatsapp_conversations.find_one({"wa_id": wa_id})
    assert convo["bot_paused"] is True
    out = await db.whatsapp_outbox.find_one({"phone": phone}, sort=[("_id", -1)])
    assert out["kind"] == "agent" and out["agent_id"] == "000000000000000000000001"

    # Expire the window: free text must be refused.
    await db.whatsapp_conversations.update_one(
        {"wa_id": wa_id}, {"$set": {"last_inbound_at": datetime.now(timezone.utc) - timedelta(hours=25)}})
    with pytest.raises(BadRequestException, match="window has expired"):
        await crm.send_text(wa_id, "too late", agent_id="000000000000000000000001")


@pytest.mark.asyncio
async def test_bot_stays_silent_while_agent_owns_the_thread(db, cleanup):
    wa_id, phone = "918887771004", "8887771004"
    _cleanup_wa(cleanup, wa_id, phone)
    bot = WhatsAppBotService(db)
    await bot.handle_webhook(wa_payload(wa_id, text="hi"))
    await WhatsAppCrmService(db).send_text(wa_id, "Human agent taking over", agent_id="000000000000000000000002")

    before = await db.whatsapp_outbox.count_documents({"phone": phone})
    await bot.handle_webhook(wa_payload(wa_id, text="hi"))  # would normally trigger the menu
    after = await db.whatsapp_outbox.count_documents({"phone": phone})
    assert after == before  # no bot reply
    assert await db.whatsapp_inbox.count_documents({"wa_id": wa_id}) == 2  # but still recorded

    # Resolving hands the customer back to the bot.
    await WhatsAppCrmService(db).set_status(wa_id, "resolved")
    await bot.handle_webhook(wa_payload(wa_id, text="hi"))
    assert await db.whatsapp_outbox.count_documents({"phone": phone}) > after


@pytest.mark.asyncio
async def test_otp_rows_are_redacted_in_thread(db, cleanup):
    wa_id, phone = "918887771005", "8887771005"
    _cleanup_wa(cleanup, wa_id, phone)
    await WhatsAppBotService(db).handle_webhook(wa_payload(wa_id, text="hi"))
    from app.services.whatsapp_service import WhatsAppService

    await WhatsAppService(db).send_otp(phone, "123456")
    thread = await WhatsAppCrmService(db).get_thread(wa_id)
    otp_msgs = [m for m in thread["messages"] if m["type"] == "redacted"]
    assert otp_msgs, "OTP send should appear as a redacted message"
    assert all("123456" not in m["text"] for m in thread["messages"])


@pytest.mark.asyncio
async def test_event_template_gating_and_fallback(db, cleanup):
    crm = WhatsAppCrmService(db)
    # Not approved (or absent) -> None, so notify() falls back to generic.
    await db.whatsapp_templates.delete_one({"name": "test_event_gate"})
    assert await crm.event_template_if_ready("nonexistent_event") is None

    await db.whatsapp_templates.update_one(
        {"name": "blussit_service_completed"},
        {"$set": {"name": "blussit_service_completed", "status": "PENDING", "disabled": False}}, upsert=True)
    cleanup.append(("whatsapp_templates", {"name": "blussit_service_completed", "status": "PENDING"}))
    assert await crm.event_template_if_ready("service_completed") is None  # pending != approved

    await db.whatsapp_templates.update_one(
        {"name": "blussit_service_completed"}, {"$set": {"status": "APPROVED"}})
    tpl = await crm.event_template_if_ready("service_completed")
    assert tpl and tpl["name"] == "blussit_service_completed"
    # restore to PENDING so the dev DB reflects Meta's actual state
    await db.whatsapp_templates.update_one(
        {"name": "blussit_service_completed"}, {"$set": {"status": "PENDING"}})


@pytest.mark.asyncio
async def test_assignment_requires_staff_role(db, cleanup):
    wa_id, phone = "918887771006", "8887771006"
    _cleanup_wa(cleanup, wa_id, phone)
    await WhatsAppBotService(db).handle_webhook(wa_payload(wa_id, text="hi"))
    crm = WhatsAppCrmService(db)
    customer = await db.users.find_one({"phone": phone})
    with pytest.raises(BadRequestException, match="admin or manager"):
        await crm.assign(wa_id, str(customer["_id"]))  # customers can't be agents
    # Unassign always allowed.
    out = await crm.assign(wa_id, None)
    assert out["assigned_to"] is None
