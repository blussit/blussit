"""
Captain KYC / background verification.

The captain submits their packet (photo, Aadhaar, PAN, doc images,
local/permanent address) from their own profile; their CENTER MANAGER
reviews it and marks it verified/rejected with a note. Status flow:
pending (nothing yet) → submitted → verified | rejected → (resubmit) →
submitted → ... Editing locks once verified — a manager must reject (with
a note) to reopen it, so a verified identity can't silently drift.

Aadhaar/PAN numbers are sensitive: every LIST surface gets them masked
(last 4 only, via masked_kyc). The full numbers appear only on the
captain's own profile and to an ADMIN — a manager's review screen shows
the last 4 (DATA-1); the card images themselves stay viewable there, which
is what the review actually checks.
"""
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.core.authz import ensure_own_center
from app.core.exceptions import BadRequestException, NotFoundException
from app.repositories.user_repository import UserRepository
from app.schemas.staff_kyc_schema import KycReviewRequest, KycSubmitRequest
from app.services.audit_service import AuditService
from app.services.notification_service import NotificationService
from app.utils.timezone import from_stored, now_ist


def _mask_tail(value: str | None, visible: int = 4) -> str | None:
    if not value:
        return None
    return "•" * max(0, len(value) - visible) + value[-visible:]


def masked_kyc(kyc: dict | None) -> dict | None:
    """List-view shape: status + masked numbers, no doc URLs, no addresses."""
    if not kyc:
        return None
    return {
        "status": kyc.get("status", "pending"),
        "aadhaar_number": _mask_tail(kyc.get("aadhaar_number")),
        "pan_number": _mask_tail(kyc.get("pan_number")),
        "submitted_at": kyc.get("submitted_at"),
        "reviewed_at": kyc.get("reviewed_at"),
    }


def _serialized(kyc: dict) -> dict:
    out = dict(kyc)
    for key in ("submitted_at", "reviewed_at"):
        if out.get(key) is not None and not isinstance(out[key], str):
            out[key] = from_stored(out[key]).isoformat()
    return out


def _for_reviewer(kyc: dict, actor_role: str) -> dict:
    """The review packet as this reviewer may see it: an admin gets the
    full numbers, a manager the last 4 (DATA-1). Doc URLs, photo and
    addresses are unchanged — the documents are what gets verified."""
    out = _serialized(kyc)
    if actor_role != "admin":
        for key in ("aadhaar_number", "pan_number"):
            if out.get(key):
                out[key] = _mask_tail(str(out[key]))
    return out


# Everything the manager needs to verify a captain — a packet missing any
# of these is refused rather than sent for review half-empty.
KYC_REQUIRED = (
    ("photo_url", "your photo"),
    ("aadhaar_number", "Aadhaar number"),
    ("pan_number", "PAN number"),
    ("aadhaar_doc_url", "Aadhaar card photo"),
    ("pan_doc_url", "PAN card photo"),
    ("local_address", "local address"),
    ("permanent_address", "permanent address"),
)


class StaffKycService:
    def __init__(self, db: AsyncIOMotorDatabase):
        self.user_repo = UserRepository(db)
        self.notifications = NotificationService(db)
        self.audit = AuditService(db)

    async def get_my(self, captain_id: str) -> dict:
        captain = await self.user_repo.find_by_id(captain_id)
        if not captain:
            raise NotFoundException("Captain not found")
        return _serialized(captain.get("captain_kyc") or {"status": "pending"})

    async def submit(self, captain_id: str, payload: KycSubmitRequest) -> dict:
        captain = await self.user_repo.find_by_id(captain_id)
        if not captain:
            raise NotFoundException("Captain not found")
        current = captain.get("captain_kyc") or {}
        if current.get("status") == "verified":
            raise BadRequestException("Your documents are already verified — ask your manager to reopen them if something changed.")

        data = payload.model_dump()
        if data.get("same_as_local"):
            data["permanent_address"] = data.get("local_address")
        missing = [label for key, label in KYC_REQUIRED if not str(data.get(key) or "").strip()]
        if missing:
            raise BadRequestException(f"Please add {', '.join(missing)} before sending for review.")
        await self._ensure_own_documents(captain_id, data, current)
        kyc = {
            **{k: current.get(k) for k in ("reviewed_by", "reviewed_at")},
            **{k: v for k, v in data.items()},
            "status": "submitted",
            "submitted_at": now_ist(),
            # A resubmission answers the last rejection — clear the note so
            # the captain isn't staring at stale feedback.
            "review_note": None,
        }
        # Guarded on the packet state we read — if the manager's review (or
        # anything else) landed in between, this whole-subdocument write
        # would silently discard it. The loser retries with fresh state.
        guard = {"captain_kyc": {"$exists": False}} if not current else {"captain_kyc.status": current.get("status")}
        updated = await self.user_repo.update_if(captain_id, guard, {"captain_kyc": kyc})
        if updated is None:
            raise BadRequestException("Your verification status just changed — reload your profile and try again.")
        await self.audit.log_action(captain_id, "captain", "SUBMIT_KYC", "staff", captain_id, {"status": "submitted"})
        return _serialized(kyc)

    async def _ensure_own_documents(self, captain_id: str, data: dict, current: dict) -> None:
        """The Aadhaar/PAN images must be files THIS captain uploaded (POST
        /uploads/document records who uploaded what). Document downloads
        used to be authorized by whichever account's packet named the URL,
        so submitting another captain's document URL handed its Aadhaar to
        whoever submitted it."""
        db = self.user_repo.collection.database
        for key, label in (("aadhaar_doc_url", "Aadhaar card photo"), ("pan_doc_url", "PAN card photo")):
            url = data.get(key)
            if await db.uploaded_documents.find_one({"url": url, "owner_id": captain_id}, {"_id": 1}):
                continue
            # A resubmission may carry a file from this captain's OWN earlier
            # packet, uploaded before uploads were recorded — fine as long
            # as no record says it belongs to somebody else.
            legacy_own = url in (current.get("aadhaar_doc_url"), current.get("pan_doc_url"))
            if legacy_own and not await db.uploaded_documents.find_one({"url": url}, {"_id": 1}):
                continue
            raise BadRequestException(f"Please upload your {label} again from this screen.")

    async def get_for_review(self, captain_id: str, actor_role: str, actor_center_id: str | None) -> dict:
        captain = await self._own_center_captain(captain_id, actor_role, actor_center_id)
        return _for_reviewer(captain.get("captain_kyc") or {"status": "pending"}, actor_role)

    async def review(self, captain_id: str, payload: KycReviewRequest, reviewer_id: str, actor_role: str, actor_center_id: str | None) -> dict:
        captain = await self._own_center_captain(captain_id, actor_role, actor_center_id)
        kyc = captain.get("captain_kyc") or {}
        if kyc.get("status") not in ("submitted", "verified", "rejected"):
            raise BadRequestException("This captain hasn't submitted their documents yet.")
        kyc.update({
            "status": payload.status,
            "reviewed_by": reviewer_id,
            "reviewed_at": now_ist(),
            "review_note": payload.note,
        })
        # Guarded on submitted_at: if the captain RESUBMITTED between the
        # manager opening the packet and clicking Verify, this must not
        # stamp "verified" onto documents nobody reviewed.
        updated = await self.user_repo.update_if(
            captain_id, {"captain_kyc.submitted_at": (captain.get("captain_kyc") or {}).get("submitted_at")}, {"captain_kyc": kyc}
        )
        if updated is None:
            raise BadRequestException("The captain just resubmitted their documents — reload and review the new packet.")
        await self.audit.log_action(reviewer_id, actor_role, "REVIEW_KYC", "staff", captain_id, {"status": payload.status, "note": payload.note})
        title = "Background verification approved" if payload.status == "verified" else "Documents need another look"
        message = (
            "Your profile is now verified. Thank you!"
            if payload.status == "verified"
            else (payload.note or "Please review and resubmit your documents from your profile.")
        )
        await self.notifications.notify(captain_id, title, message)
        return _for_reviewer(kyc, actor_role)

    async def _own_center_captain(self, captain_id: str, actor_role: str, actor_center_id: str | None) -> dict:
        captain = await self.user_repo.find_by_id(captain_id)
        if not captain or captain.get("role") != "captain":
            raise NotFoundException("Captain not found")
        ensure_own_center(actor_role, actor_center_id, captain.get("service_center_id"))
        return captain
