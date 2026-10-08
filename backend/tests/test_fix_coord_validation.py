"""Coordinator P3 fixes:
  VAL-1  saved-address fields are bounded (a 1.5 MB line1 was stored).
  VAL-4  complaint attachments accept only our own upload URLs.
  VAL-6  malformed ids on zone delete/update and WhatsApp media → 404/400, never 500.
  PRICE-03  a first-wash price applies only when 0 < price < regular — the
            rule the website displays prices by.
"""
import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError

from app.core.security import create_access_token
from app.schemas.complaint_schema import ComplaintCreateRequest
from app.schemas.profile_schema import AddressCreateRequest, AddressUpdateRequest
from app.services.booking_service import BookingService
from tests.factories import make_customer, own_upload_url



def _client():
    from app.main import app

    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


def test_val1_address_fields_are_bounded():
    ok = dict(line1="Flat 12, Shivampuri Colony", city="Indore", state="Madhya Pradesh", pincode="452001")
    AddressCreateRequest(**ok)
    with pytest.raises(ValidationError):
        AddressCreateRequest(**{**ok, "line1": "x" * 1_500_000})
    with pytest.raises(ValidationError):
        AddressCreateRequest(**{**ok, "latitude": 999})
    with pytest.raises(ValidationError):
        AddressUpdateRequest(landmark="y" * 5000)


async def test_val1_oversized_address_is_refused_over_http(db, cleanup):
    from bson import ObjectId

    customer = await make_customer(db)
    cleanup.append(("users", {"_id": ObjectId(customer)}))
    cleanup.append(("addresses", {"owner_id": customer}))
    headers = {"Authorization": f"Bearer {create_access_token(customer, 'customer', {'tv': 0})}"}
    async with _client() as client:
        res = await client.post("/api/v1/addresses", headers=headers, json={
            "line1": "x" * 200_000, "city": "Indore", "state": "MP", "pincode": "452001",
        })
    assert res.status_code == 422
    assert await db.addresses.count_documents({"owner_id": customer}) == 0


def test_val4_complaint_attachments_only_our_uploads():
    base = dict(booking_id="0" * 24, subject="Water spots", description="Spots left on the bonnet")
    ComplaintCreateRequest(**base, attachments=[own_upload_url("photos/ok.jpg")])
    for bad in ("javascript:alert(document.cookie)", "data:text/html,<script>x</script>", "https://evil.example/x.jpg"):
        with pytest.raises(ValidationError):
            ComplaintCreateRequest(**base, attachments=[bad])


async def test_val6_malformed_ids_are_not_server_errors(db):
    admin = await db.users.find_one({"role": "admin"})
    headers = {"Authorization": f"Bearer {create_access_token(str(admin['_id']), 'admin', {'tv': admin.get('token_version', 0)})}"}
    async with _client() as client:
        for bad in ("not-an-id", "aaaaaaaaaaaa", "%24ne"):
            res = await client.delete(f"/api/v1/service-zones/{bad}", headers=headers)
            assert res.status_code == 404, (bad, res.text)
            res = await client.get(f"/api/v1/whatsapp/crm/media/{bad}", headers=headers)
            assert res.status_code < 500, (bad, res.text)


@pytest.mark.parametrize(
    "discounted, eligible, expected",
    [(199, True, 199), (199, False, 349), (0, True, 349), (349, True, 349), (499, True, 349), (None, True, 349)],
)
def test_price03_first_wash_price_only_when_a_real_discount(discounted, eligible, expected):
    item = {"price": 349, "discounted_price": discounted}
    assert BookingService._resolve_price(item, "hatch", eligible) == expected
