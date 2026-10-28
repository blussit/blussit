"""
Moves identity documents (KYC) into private storage. Idempotent — safe to
run on every boot and to re-run.

* local provider (dev): files that an older build wrote to the PUBLIC
  uploads/documents folder are moved to the private directory, and any
  stored `<base>/uploads/documents/<file>` URL is rewritten to the
  authorized download route (the old static URL is also blocked in main).
* r2 (production): `migrate_r2_documents` copies a document that sits in
  the public bucket into the private bucket under a NEW random key and
  repoints the captain's packet + upload record at it. The old public
  object is NOT deleted by code — the founder removes it (or just makes
  the old bucket private) after checking; see docs/DEPLOYMENT.md.
  Run via `python -m app.scripts.migrate_kyc_documents` (dry-run default).
"""
import logging
import shutil
import uuid
from datetime import datetime, timezone
from urllib.parse import quote, unquote

from app.core import storage
from app.core.config import settings

logger = logging.getLogger(__name__)


def move_local_documents() -> int:
    """Move uploads/documents/* to the private dir. Returns files moved."""
    old = storage.upload_root() / "documents"
    if settings.STORAGE_PROVIDER != "local" or not old.is_dir():
        return 0
    new = storage.private_root() / "documents"
    new.mkdir(parents=True, exist_ok=True)
    moved = 0
    for path in old.iterdir():
        if path.is_file():
            target = new / path.name
            if not target.exists():
                shutil.move(str(path), str(target))
            else:
                path.unlink()
            moved += 1
    return moved


async def rewrite_local_document_urls(db) -> int:
    """`<base>/uploads/documents/<file>` -> the authorized route URL."""
    if settings.STORAGE_PROVIDER != "local":
        return 0
    old_base = storage._local_upload_base() + "documents/"
    new_base = storage._document_route_base() + "documents/"
    changed = 0
    for field in ("aadhaar_doc_url", "pan_doc_url"):
        async for user in db.users.find({f"captain_kyc.{field}": {"$regex": f"^{_re(old_base)}"}}, {f"captain_kyc.{field}": 1}):
            url = user["captain_kyc"][field]
            await db.users.update_one({"_id": user["_id"]}, {"$set": {f"captain_kyc.{field}": new_base + url[len(old_base):]}})
            changed += 1
    async for rec in db.uploaded_documents.find({"url": {"$regex": f"^{_re(old_base)}"}}):
        await db.uploaded_documents.update_one({"_id": rec["_id"]}, {"$set": {"url": new_base + rec["url"][len(old_base):]}})
        changed += 1
    return changed


def _re(text: str) -> str:
    import re

    return re.escape(text)


async def migrate_r2_documents(db, apply: bool = False) -> dict:
    """Copy public-bucket KYC documents into the private bucket (see module
    docstring). Returns counts; with apply=False nothing is written."""
    public_base = settings.R2_PUBLIC_BASE_URL.rstrip("/") + "/"
    report = {"found": 0, "copied": 0, "skipped": 0, "errors": 0}
    async for user in db.users.find({"captain_kyc": {"$exists": True}}, {"captain_kyc": 1}):
        for field in ("aadhaar_doc_url", "pan_doc_url"):
            url = (user.get("captain_kyc") or {}).get(field)
            if not url or not url.startswith(public_base):
                continue
            report["found"] += 1
            if not apply:
                continue
            old_key = unquote(url[len(public_base):]).strip("/")
            try:
                data = await storage._r2_get(settings.R2_BUCKET_NAME, old_key)
                if data is None:
                    report["skipped"] += 1
                    continue
                ext = old_key.rsplit(".", 1)[-1].lower()
                if ext == "jpeg":
                    ext = "jpg"
                if ext not in storage.DOCUMENT_MEDIA_TYPES:
                    report["skipped"] += 1
                    continue
                new_key = await storage._r2_upload(
                    data, ext, storage.DOCUMENT_MEDIA_TYPES[ext],
                    prefix="documents", bucket=settings.R2_PRIVATE_BUCKET_NAME, private=True,
                )
                new_url = f"{storage._document_route_base()}{quote(new_key, safe='/')}"
                await db.uploaded_documents.insert_one({
                    "key": new_key, "url": new_url, "owner_id": str(user["_id"]), "kind": "identity_document",
                    "migrated_from": old_key, "created_at": datetime.now(timezone.utc),
                })
                await db.users.update_one({"_id": user["_id"]}, {"$set": {f"captain_kyc.{field}": new_url}})
                report["copied"] += 1
            except Exception:  # noqa: BLE001 — one bad object must not stop the rest
                logger.exception("KYC document migration failed for captain %s (%s)", user["_id"], field)
                report["errors"] += 1
    return report
