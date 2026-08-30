"""
Coverage leads — uncovered-area demand capture from the public landing
page. Repeated interest from the same phone+pincode must upsert (bumping
requests_count), never pile up duplicate rows; the admin summary must
aggregate demand by pincode.
"""
import pytest

from app.services.coverage_lead_service import CoverageLeadService


@pytest.fixture
async def svc(db, cleanup):
    cleanup.append(("coverage_leads", {"phone": {"$regex": "^77700"}}))
    return CoverageLeadService(db)


@pytest.mark.asyncio
async def test_capture_creates_one_row_and_dedupes_repeats(svc, db):
    await svc.capture("Asha Verma", "7770001111", "455001", city_area="Dewas", service_interest="Foam Wash")
    await svc.capture("Asha Verma", "7770001111", "455001")  # asked again later

    docs = await db.coverage_leads.find({"phone": "7770001111"}).to_list(length=10)
    assert len(docs) == 1
    assert docs[0]["requests_count"] == 2
    # A bare re-submit must not blank out earlier detail.
    assert docs[0]["city_area"] == "Dewas"
    assert docs[0]["service_interest"] == "Foam Wash"


@pytest.mark.asyncio
async def test_same_phone_different_pincode_is_a_separate_lead(svc, db):
    await svc.capture("Asha Verma", "7770002222", "455001")
    await svc.capture("Asha Verma", "7770002222", "456010")
    assert await db.coverage_leads.count_documents({"phone": "7770002222"}) == 2


@pytest.mark.asyncio
async def test_admin_list_and_summary(svc, db):
    await svc.capture("A", "7770003331", "458001")
    await svc.capture("B", "7770003332", "458001")
    await svc.capture("C", "7770003333", "459001")
    await svc.capture("B", "7770003332", "458001")  # B asks twice

    items, total = await svc.list_for_admin(1, 50)
    ours = [i for i in items if i["phone"].startswith("777000333")]
    assert len(ours) == 3

    summary = await svc.summary_for_admin()
    by_pin = {t["pincode"]: t for t in summary["top_pincodes"]}
    assert by_pin["458001"]["people"] == 2
    assert by_pin["458001"]["requests"] == 3
