"""DEP-05: /api/health is LIVENESS (process up, no dependencies) and stays
200 with the database down; /api/ready is READINESS — it pings Mongo with a
short timeout and checks the critical indexes, answering 503 with a JSON
reason when the instance must not take traffic."""
import pytest
from httpx import ASGITransport, AsyncClient
from motor.motor_asyncio import AsyncIOMotorClient

from app import main
from app.core import database
from app.core.database import mongodb

pytestmark = pytest.mark.asyncio


def _client() -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=main.app), base_url="http://test")


@pytest.fixture(autouse=True)
def _fresh_index_cache(monkeypatch):
    monkeypatch.setattr(main, "_indexes_ok_until", 0.0)


async def test_ready_is_200_when_the_database_answers(db):
    async with _client() as c:
        r = await c.get("/api/ready")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["success"] is True and body["checks"]["database"] == "ok"
    assert body["checks"]["indexes"] in ("ok", "not_checked")


async def test_ready_is_503_and_health_stays_200_when_the_database_is_unreachable(db):
    real = mongodb.db
    bad = AsyncIOMotorClient("mongodb://127.0.0.1:1/?directConnection=true", serverSelectionTimeoutMS=300)
    mongodb.db = bad["x"]
    try:
        async with _client() as c:
            ready = await c.get("/api/ready")
            health = await c.get("/api/health")
    finally:
        mongodb.db = real
        bad.close()
    assert ready.status_code == 503, ready.text
    body = ready.json()
    assert body["success"] is False and body["error_code"] == "NOT_READY"
    assert body["checks"]["database"] != "ok"
    assert "127.0.0.1" not in ready.text, "no connection details in the public answer"
    assert health.status_code == 200, "liveness never depends on the database"


async def test_ready_is_503_before_the_database_is_connected(db):
    real = mongodb.db
    mongodb.db = None
    try:
        async with _client() as c:
            r = await c.get("/api/ready")
    finally:
        mongodb.db = real
    assert r.status_code == 503 and r.json()["checks"]["database"] == "not_connected"


async def test_ready_is_503_when_critical_indexes_are_missing(db, monkeypatch):
    async def missing(*_a, **_k):
        return ["bookings.uniq_active_visit_v3"]

    monkeypatch.setattr(database, "missing_critical_indexes", missing, raising=False)
    async with _client() as c:
        r = await c.get("/api/ready")
    assert r.status_code == 503, r.text
    assert r.json()["checks"]["indexes"] == "missing"
    assert r.json()["missing_indexes"] == ["bookings.uniq_active_visit_v3"]


async def test_ready_accepts_a_sync_or_db_taking_index_checker(db, monkeypatch):
    seen: list = []

    def checker(database_):
        seen.append(database_)
        return []

    monkeypatch.setattr(database, "missing_critical_indexes", checker, raising=False)
    async with _client() as c:
        r = await c.get("/api/ready")
    assert r.status_code == 200 and r.json()["checks"]["indexes"] == "ok"
    assert seen and seen[0] is mongodb.db


async def test_ready_index_check_failure_is_not_ready(db, monkeypatch):
    async def broken():
        raise RuntimeError("listIndexes failed")

    monkeypatch.setattr(database, "missing_critical_indexes", broken, raising=False)
    async with _client() as c:
        r = await c.get("/api/ready")
    assert r.status_code == 503 and r.json()["checks"]["indexes"] == "error"


async def test_ready_without_the_index_checker_reports_not_checked(db, monkeypatch):
    monkeypatch.delattr(database, "missing_critical_indexes", raising=False)
    async with _client() as c:
        r = await c.get("/api/ready")
    assert r.status_code == 200 and r.json()["checks"]["indexes"] == "not_checked"


async def test_ready_uses_the_real_core_index_checker_when_present(db):
    """Once app.core.database.missing_critical_indexes exists (CORE), a
    freshly indexed test database is fully ready."""
    if not hasattr(database, "missing_critical_indexes"):
        pytest.skip("CORE's index checker isn't in this build yet")
    async with _client() as c:
        r = await c.get("/api/ready")
    assert r.status_code == 200, r.text
    assert r.json()["checks"]["indexes"] == "ok"
