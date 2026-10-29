"""Society schedule settings (GET/PUT /society-schedule/settings): global, so a
manager (or admin) reads them and only an admin saves; values are validated;
the save says what changed, is audited with before/after, and cannot ask for a
lead time bookings can't honour."""
import pytest

from tests.society_factories import auth, client, make_admin, make_center, staff

pytestmark = pytest.mark.asyncio


@pytest.fixture
async def who(db, cleanup):
    center = await make_center(db, cleanup, spot=2)
    manager, captain = await staff(db, cleanup, center["id"])
    admin = await make_admin(db, cleanup)
    before = await db.society_settings.find_one({"_id": "schedule"})
    cleanup.append(("audit_logs", {"actor_id": admin}))
    yield {"admin": auth(admin, "admin"), "manager": auth(manager, "manager", center["id"]), "captain": auth(captain, "captain", center["id"]), "admin_id": admin}
    # put the global document back exactly as it was
    await db.society_settings.delete_one({"_id": "schedule"})
    if before:
        await db.society_settings.insert_one(before)


async def test_manager_and_admin_read_but_captain_and_anonymous_cannot(who):
    async with client() as c:
        for role in ("manager", "admin"):
            res = await c.get("/api/v1/society-schedule/settings", headers=who[role])
            assert res.status_code == 200, res.text
            data = res.json()["data"]
            assert 1 <= data["generate_days_ahead"] <= 5 and 1 <= data["default_washes_per_captain"] <= 20
            assert data["max_advance_days"] >= 1
        assert (await c.get("/api/v1/society-schedule/settings", headers=who["captain"])).status_code == 403
        assert (await c.get("/api/v1/society-schedule/settings")).status_code in (401, 403)


async def test_only_an_admin_can_save(who):
    body = {"generate_days_ahead": 3, "default_washes_per_captain": 8}
    async with client() as c:
        start = (await c.get("/api/v1/society-schedule/settings", headers=who["admin"])).json()["data"]
        for role in ("manager", "captain"):
            assert (await c.put("/api/v1/society-schedule/settings", headers=who[role], json=body)).status_code == 403
        assert (await c.put("/api/v1/society-schedule/settings", json=body)).status_code in (401, 403)
        # And the refusals changed nothing.
        end = (await c.get("/api/v1/society-schedule/settings", headers=who["admin"])).json()["data"]
        assert (end["generate_days_ahead"], end["default_washes_per_captain"]) == (start["generate_days_ahead"], start["default_washes_per_captain"])


@pytest.mark.parametrize(
    "body",
    [
        {"generate_days_ahead": 0, "default_washes_per_captain": 6},
        {"generate_days_ahead": 6, "default_washes_per_captain": 6},
        {"generate_days_ahead": 2, "default_washes_per_captain": 0},
        {"generate_days_ahead": 2, "default_washes_per_captain": 21},
        {"generate_days_ahead": "soon", "default_washes_per_captain": 6},
        {"generate_days_ahead": 2},
        {"default_washes_per_captain": 6},
        {"generate_days_ahead": 2, "default_washes_per_captain": 6, "surprise": 1},
    ],
)
async def test_values_are_validated(who, body):
    async with client() as c:
        assert (await c.put("/api/v1/society-schedule/settings", headers=who["admin"], json=body)).status_code == 422


async def test_save_reports_what_changed_and_is_audited(db, who):
    async with client() as c:
        first = await c.put("/api/v1/society-schedule/settings", headers=who["admin"], json={"generate_days_ahead": 3, "default_washes_per_captain": 9})
        assert first.status_code == 200, first.text
        body = first.json()
        assert body["data"]["generate_days_ahead"] == 3 and body["data"]["default_washes_per_captain"] == 9
        assert "3 days ahead" in body["message"] and "9 washes" in body["message"]
        assert set(body["data"]["changes"]) == {"generate_days_ahead", "default_washes_per_captain"}

        # Same numbers again: nothing to say.
        same = await c.put("/api/v1/society-schedule/settings", headers=who["admin"], json={"generate_days_ahead": 3, "default_washes_per_captain": 9})
        assert same.status_code == 200 and same.json()["message"].startswith("Nothing changed")

        # One number moves: only that one is named, and a singular reads right.
        one = await c.put("/api/v1/society-schedule/settings", headers=who["admin"], json={"generate_days_ahead": 1, "default_washes_per_captain": 9})
        assert "1 day ahead" in one.json()["message"] and "washes per captain" not in one.json()["message"]

        read = (await c.get("/api/v1/society-schedule/settings", headers=who["manager"])).json()["data"]
        assert (read["generate_days_ahead"], read["default_washes_per_captain"]) == (1, 9)
        assert read["updated_at"] and read["updated_by_name"]

    rows = await db.audit_logs.find({"actor_id": who["admin_id"], "action": "UPDATE_SOCIETY_SCHEDULE_SETTINGS"}).sort("created_at", 1).to_list(length=10)
    assert len(rows) == 3
    assert rows[0]["details"]["changes"]["default_washes_per_captain"]["after"] == 9
    assert rows[1]["details"]["changes"] == {}
    assert rows[2]["details"]["changes"] == {"generate_days_ahead": {"before": 3, "after": 1}}


async def test_lead_time_longer_than_bookings_can_open_is_refused(who, monkeypatch):
    """Bookings open `max_advance_days` ahead; a visit day booked further out than
    max_advance_days - 1 days before could never succeed."""
    from app.services.society_schedule_service import SocietyScheduleService

    async def policy(self):
        return {"max_advance_days": 3}

    monkeypatch.setattr(SocietyScheduleService, "_policy", policy)
    async with client() as c:
        too_far = await c.put("/api/v1/society-schedule/settings", headers=who["admin"], json={"generate_days_ahead": 3, "default_washes_per_captain": 6})
        assert too_far.status_code == 400 and "3 days ahead" in too_far.json()["message"]
        ok = await c.put("/api/v1/society-schedule/settings", headers=who["admin"], json={"generate_days_ahead": 2, "default_washes_per_captain": 6})
        assert ok.status_code == 200, ok.text
        assert (await c.get("/api/v1/society-schedule/settings", headers=who["manager"])).json()["data"]["max_advance_days"] == 3
