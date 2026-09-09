"""
Captain KYC / background verification: captain submits the packet from
their profile, the CENTER MANAGER reviews (verified/rejected + note),
editing locks once verified, numbers are masked on list surfaces.
"""
import pytest
from bson import ObjectId

from app.core.exceptions import BadRequestException, ForbiddenException
from app.schemas.staff_kyc_schema import KycReviewRequest, KycSubmitRequest
from app.services.staff_kyc_service import StaffKycService, masked_kyc

from tests.factories import make_captain, make_manager, make_service_center


@pytest.fixture
async def rig(db, cleanup):
    center_id = await make_service_center(db)
    cleanup.append(("service_centers", {"_id": ObjectId(center_id)}))
    captain_id = await make_captain(db, center_id)
    cleanup.append(("users", {"_id": ObjectId(captain_id)}))
    cleanup.append(("captain_wallets", {"captain_id": captain_id}))
    manager_id = await make_manager(db, center_id)
    cleanup.append(("users", {"_id": ObjectId(manager_id)}))
    cleanup.append(("notifications", {}))
    cleanup.append(("audit_logs", {}))
    return {"db": db, "center_id": center_id, "captain_id": captain_id, "manager_id": manager_id}


PACKET = KycSubmitRequest(
    aadhaar_number="123412341234",
    pan_number="ABCDE1234F",
    local_address="12 Local Lane, Indore",
    same_as_local=True,
)


@pytest.mark.asyncio
async def test_submit_and_manager_verify(rig, db):
    svc = StaffKycService(db)
    kyc = await svc.submit(rig["captain_id"], PACKET)
    assert kyc["status"] == "submitted"
    assert kyc["permanent_address"] == "12 Local Lane, Indore"  # same_as_local copies it

    reviewed = await svc.review(rig["captain_id"], KycReviewRequest(status="verified"), rig["manager_id"], "manager", rig["center_id"])
    assert reviewed["status"] == "verified"

    # Verified locks self-editing.
    with pytest.raises(BadRequestException, match="already verified"):
        await svc.submit(rig["captain_id"], PACKET)

    # The captain got told.
    note = await db.notifications.find_one({"user_id": rig["captain_id"], "title": "Background verification approved"})
    assert note is not None


@pytest.mark.asyncio
async def test_reject_reopens_editing_and_resubmit_clears_note(rig, db):
    svc = StaffKycService(db)
    await svc.submit(rig["captain_id"], PACKET)
    rejected = await svc.review(
        rig["captain_id"], KycReviewRequest(status="rejected", note="Aadhaar photo unreadable"), rig["manager_id"], "manager", rig["center_id"]
    )
    assert rejected["status"] == "rejected"
    assert rejected["review_note"] == "Aadhaar photo unreadable"

    resubmitted = await svc.submit(rig["captain_id"], PACKET)
    assert resubmitted["status"] == "submitted"
    assert resubmitted["review_note"] is None


@pytest.mark.asyncio
async def test_other_center_manager_cannot_review(rig, db, cleanup):
    other_center = await make_service_center(db)
    cleanup.append(("service_centers", {"_id": ObjectId(other_center)}))
    svc = StaffKycService(db)
    await svc.submit(rig["captain_id"], PACKET)
    with pytest.raises(ForbiddenException):
        await svc.review(rig["captain_id"], KycReviewRequest(status="verified"), rig["manager_id"], "manager", other_center)


def test_masking_shows_last_four_only():
    masked = masked_kyc({"status": "submitted", "aadhaar_number": "123412341234", "pan_number": "ABCDE1234F"})
    assert masked["aadhaar_number"].endswith("1234") and masked["aadhaar_number"].startswith("•")
    assert "1234" not in masked["aadhaar_number"][:-4]
    assert masked["pan_number"].endswith("234F") and masked["pan_number"].startswith("•")
    assert "doc_url" not in masked and "local_address" not in masked
