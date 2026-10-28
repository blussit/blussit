"""
The festival / broadcast WhatsApp template and the rules for the one thing
an admin types into it.

`blussit_festival_greeting` (MARKETING, en): IMAGE header (the festival
picture, sent by public URL), a BODY with exactly one variable {{1}} (the
admin's message) and the footer "Reply STOP to opt out". No buttons: the bot
already treats a free-text "STOP" as the marketing opt-out
(WhatsAppBotService._OPT_OUT_WORDS), so a quick-reply button would add
nothing. Definition only — the founder submits it (Admin -> WhatsApp ->
Templates, or `python -m app.scripts.print_festival_template` for Meta's
create-template call).
"""
import re
import unicodedata

from app.core.config import settings
from app.core.exceptions import BadRequestException

FESTIVAL_TEMPLATE_LANGUAGE = "en"
MAX_MESSAGE_CHARS = 600
FOOTER_TEXT = "Reply STOP to opt out"

# A variable may not start or end a body, so fixed words sit on both sides.
FESTIVAL_BODY = "Greetings from Blussit!\n\n{{1}}\n\nWarm wishes, Team Blussit"
FESTIVAL_EXAMPLE_MESSAGE = (
    "Wishing you and your family a very happy Diwali! Treat your car to a sparkling clean this festive week."
)
HEADER_HANDLE_PLACEHOLDER = "<HEADER_HANDLE_FROM_META_RESUMABLE_UPLOAD>"

# Zero-width / bidi format characters are dropped, except the joiner and the
# variation selector that emoji sequences (family, flags, skin tones) need.
_KEEP_FORMAT = {"‍", "️"}


def festival_template_name() -> str:
    return settings.WHATSAPP_FESTIVAL_TEMPLATE_NAME or "blussit_festival_greeting"


def sanitize_message(text: str | None, *, max_chars: int = MAX_MESSAGE_CHARS) -> str:
    """The admin's text as one safe template parameter. Meta rejects a
    parameter with a newline, a tab or four or more spaces in a row (132000),
    so every whitespace run — line breaks included — becomes ONE space.
    Control characters go; emoji stay. Raises when nothing is left or the
    result is longer than `max_chars` (never silently cut: the admin would
    send a sentence they did not write)."""
    cleaned = []
    for ch in unicodedata.normalize("NFC", str(text or "")):
        cat = unicodedata.category(ch)
        if ch.isspace() or cat in ("Zl", "Zp", "Zs"):
            cleaned.append(" ")
        elif cat == "Cc":
            continue
        elif cat == "Cf" and ch not in _KEEP_FORMAT:
            continue
        else:
            cleaned.append(ch)
    out = re.sub(r" +", " ", "".join(cleaned)).strip()
    if not out:
        raise BadRequestException("Write the message first")
    if len(out) > max_chars:
        raise BadRequestException(f"The message is {len(out)} characters - keep it to {max_chars} or fewer")
    return out


def render_message(message: str) -> str:
    """What the customer reads under the image (for the preview)."""
    return FESTIVAL_BODY.replace("{{1}}", message)


def festival_template_payload(header_handle: str | None = None, name: str | None = None) -> dict:
    """The exact body of Meta's create-template call
    (POST /{WABA_ID}/message_templates)."""
    return {
        "name": name or festival_template_name(),
        "language": FESTIVAL_TEMPLATE_LANGUAGE,
        "category": "MARKETING",
        "components": [
            {"type": "HEADER", "format": "IMAGE", "example": {"header_handle": [header_handle or HEADER_HANDLE_PLACEHOLDER]}},
            {"type": "BODY", "text": FESTIVAL_BODY, "example": {"body_text": [[FESTIVAL_EXAMPLE_MESSAGE]]}},
            {"type": "FOOTER", "text": FOOTER_TEXT},
        ],
    }


async def submit_festival_template(db) -> dict:
    """Bootstrap hook: submit the festival template through the same provider
    call the other templates use. Needs WHATSAPP_FESTIVAL_HEADER_HANDLE (Meta
    wants a sample image handle, which only its upload API can mint), so
    without it the result is SKIPPED with the fix in the note."""
    from datetime import datetime, timezone

    from app.services.whatsapp_service import get_whatsapp_provider

    name = festival_template_name()
    existing = await db.whatsapp_templates.find_one({"name": name}, {"status": 1})
    if existing and existing.get("status") in ("APPROVED", "PENDING", "IN_APPEAL"):
        return {"name": name, "status": existing["status"], "note": "already exists"}
    handle = settings.WHATSAPP_FESTIVAL_HEADER_HANDLE
    if not handle:
        return {"name": name, "status": "SKIPPED",
                "note": "Needs WHATSAPP_FESTIVAL_HEADER_HANDLE (sample-image handle), or submit the JSON from "
                        "`python -m app.scripts.print_festival_template` in Meta Business Manager"}
    result = await get_whatsapp_provider(db).create_template(festival_template_payload(handle, name))
    if result.get("error"):
        return {"name": name, "status": "ERROR", "note": str(result["error"])}
    await db.whatsapp_templates.update_one(
        {"name": name},
        {"$set": {"name": name, "status": result.get("status", "PENDING"), "category": result.get("category", "MARKETING"),
                  "language": FESTIVAL_TEMPLATE_LANGUAGE, "body": FESTIVAL_BODY, "param_count": 1, "disabled": False,
                  "has_url_param": False, "synced_at": datetime.now(timezone.utc)},
         "$setOnInsert": {"created_at": datetime.now(timezone.utc)}},
        upsert=True,
    )
    return {"name": name, "status": result.get("status", "PENDING"), "category": result.get("category", "MARKETING"),
            "note": "festival campaign template submitted"}
