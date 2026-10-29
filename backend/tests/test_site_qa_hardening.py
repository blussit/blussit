"""Site-attack QA 2026-10-29 fixes: health routes answer HEAD, unknown API paths
use the app's error envelope, a failed OTP check is success:false, names and
pincodes are validated."""
import pytest
from pydantic import ValidationError

from app.schemas.booking_schema import QuickAddress
from app.schemas.catalog_schema import CategoryCreateRequest, ServiceCreateRequest
from app.schemas.profile_schema import AddressCreateRequest, AddressUpdateRequest
from app.schemas.service_center_schema import ServiceCenterCreateRequest
from app.schemas.subscription_schema import SubscriptionPlanCreateRequest
from app.schemas.vehicle_type_schema import VehicleTypeCreateRequest


def _client():
    from httpx import ASGITransport, AsyncClient

    from app.main import app

    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def test_health_answers_head_and_get():
    async with _client() as c:
        for path in ("/api/health", "/api/ready"):
            assert (await c.head(path)).status_code != 405
        assert (await c.get("/api/health")).status_code == 200


async def test_unknown_api_path_uses_the_app_envelope():
    async with _client() as c:
        r = await c.get("/api/v1/nope")
    assert r.status_code == 404
    body = r.json()
    assert body["success"] is False and body["error_code"] == "NOT_FOUND"


async def test_other_http_errors_keep_their_default_shape():
    async with _client() as c:
        r = await c.get("/api/v1/auth/login")  # POST-only route
    assert r.status_code == 405
    assert r.json() == {"detail": "Method Not Allowed"}


async def test_wrong_otp_is_success_false(db):
    async with _client() as c:
        r = await c.post("/api/v1/auth/otp/verify", json={"identifier": "9876500011", "otp": "000000"})
    if r.status_code == 200:
        body = r.json()
        assert body["data"]["verified"] is False
        assert body["success"] is False


@pytest.mark.parametrize("pin", ["aaaa", "<script>", "4520", "4520011", " "])
def test_bad_pincodes_are_refused(pin):
    base = dict(line1="12 Palasia", city="Indore", state="MP")
    with pytest.raises(ValidationError):
        AddressCreateRequest(pincode=pin, **base)
    with pytest.raises(ValidationError):
        AddressUpdateRequest(pincode=pin)
    with pytest.raises(ValidationError):
        QuickAddress(line1="12 Palasia", pincode=pin)


def test_a_six_digit_pincode_passes():
    assert AddressCreateRequest(line1="x", city="Indore", state="MP", pincode="452001").pincode == "452001"
    assert QuickAddress(line1="12 Palasia", pincode="452001").pincode == "452001"


def test_one_character_names_are_refused():
    with pytest.raises(ValidationError):
        VehicleTypeCreateRequest(name="a")
    with pytest.raises(ValidationError):
        CategoryCreateRequest(name="a")
    VehicleTypeCreateRequest(name="SUV")
