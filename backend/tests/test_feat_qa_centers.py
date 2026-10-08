"""QA 2026-10-07: a service center created through the admin API must be
found by address lookup. `ServiceCenterService.create` stored no
`is_active`, while `find_nearest` / `find_by_pincode` filtered
`{"is_active": True}` — the new center was invisible to coverage checks and
quotes. Create now stores `is_active: True`, and the lookups treat a center
without the field as active (`$ne: False`), so centers created before the
fix work too. Local Mongo only."""
import itertools

import pytest
from bson import ObjectId

from tests.factories import get_hatchback_type_id
from tests.society_factories import auth, client, make_admin

pytestmark = pytest.mark.asyncio
_seq = itertools.count(1)

# A spot far from every other suite's centers (they sit at 22.7, 75.8 or the
# society spots), so "nearest center" can only be the one made here.
_LAT, _LNG = 25.4358, 81.8463


async def _jet_wash_id(db) -> str:
    svc = await db.services.find_one({"name": "Jet Wash", "is_deleted": {"$ne": True}})
    assert svc and svc.get("charges_travel"), "seed() makes Jet Wash a distance-charged offer"
    return str(svc["_id"])


async def test_center_created_via_api_is_found_by_coordinates(db, cleanup):
    admin = await make_admin(db, cleanup)
    n = next(_seq)
    pincode = f"2110{n:02d}"
    async with client() as http:
        resp = await http.post("/api/v1/service-centers", headers=auth(admin, "admin"), json={
            "name": f"QA Center {n}",
            "location": {
                "address": "Civil Lines", "city": "Prayagraj", "state": "UP", "pincode": pincode,
                "latitude": _LAT, "longitude": _LNG, "service_pincodes": [pincode], "radius_km": 6.0,
            },
        })
        assert resp.status_code in (200, 201), resp.text
        center_id = resp.json()["data"]["id"]
        cleanup.append(("service_centers", {"_id": ObjectId(center_id)}))
        stored = await db.service_centers.find_one({"_id": ObjectId(center_id)})
        assert stored["is_active"] is True

        # Coverage check (the booking wizard) at a pin ~1 km away, typed
        # pincode NOT the center's — only the coordinate lookup can match.
        near = {"latitude": _LAT + 0.008, "longitude": _LNG + 0.004, "pincode": "999999"}
        cov = await http.post("/api/v1/service-zones/coverage-check", json=near)
        assert cov.status_code == 200, cov.text
        assert cov.json()["data"]["covered"] is True
        assert cov.json()["data"]["center"]["id"] == center_id

        # A quote with a distance-charged service resolves the center too.
        quote = await http.post("/api/v1/bookings/quote", json={
            "lines": [{"vehicle_type": await get_hatchback_type_id(db), "quantity": 1, "service_ids": [await _jet_wash_id(db)]}],
            "address": near,
        })
        assert quote.status_code == 200, quote.text
        assert quote.json()["data"]["travel"] is not None


async def test_center_without_is_active_field_still_counts_as_active(db, cleanup):
    """Production may hold centers created before the fix (no field)."""
    from app.repositories.service_center_repository import ServiceCenterRepository

    n = next(_seq)
    pincode = f"2120{n:02d}"
    lat, lng = _LAT + 0.5, _LNG + 0.5
    doc = {
        "name": f"QA Legacy Center {n}", "code": f"QALEG{n:04d}",
        "location": {"address": "x", "city": "y", "state": "z", "pincode": pincode, "latitude": lat, "longitude": lng,
                     "service_pincodes": [pincode], "radius_km": 6.0},
        "working_hours_start": "07:00", "working_hours_end": "19:00", "is_deleted": False,
    }
    center_id = (await db.service_centers.insert_one(doc)).inserted_id
    cleanup.append(("service_centers", {"_id": center_id}))
    repo = ServiceCenterRepository(db)
    match = await repo.find_nearest(lat + 0.001, lng)
    assert match and match[0]["_id"] == center_id
    assert any(c["_id"] == center_id for c in await repo.find_by_pincode(pincode))
    # …while a center switched off on purpose stays out.
    await db.service_centers.update_one({"_id": center_id}, {"$set": {"is_active": False}})
    assert await repo.find_nearest(lat + 0.001, lng) is None
    assert not await repo.find_by_pincode(pincode)
