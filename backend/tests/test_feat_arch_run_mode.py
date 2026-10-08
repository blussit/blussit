"""ARCH 2026-10-07 — API + Worker split (feature plan 1.9).

One image, RUN_MODE picks what a process runs:

  api     routes, webhooks, websockets + relay, health/ready; NO loops, no
          index builds, no boot backfills
  worker  the loops + index builds + boot backfills + health/ready; every
          other path is 404 (and the business routers aren't even mounted)
  all     everything (local dev, tests — the default)

Plus: graceful shutdown in each mode, readiness in both, a dead loop fails
liveness, the lease keeps a second worker idle, and a returning worker
takes the sweeps over again.
"""
import asyncio
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from app import main
from app.core.config import ConfigError, Settings, ensure_valid_settings, settings, validate_settings

BACKEND = Path(__file__).resolve().parents[1]

pytestmark = pytest.mark.asyncio


def _client() -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=main.app), base_url="http://test")


@pytest.fixture
def mode(monkeypatch):
    """Switch the module's run mode for one test (read per call/request)."""

    def set_mode(value: str) -> None:
        monkeypatch.setattr(settings, "RUN_MODE", value)

    return set_mode


@pytest.fixture(autouse=True)
def _fresh_index_cache(monkeypatch):
    monkeypatch.setattr(main, "_indexes_ok_until", 0.0)


# --------------------------------------------------------------- the modes
def test_default_mode_is_all_so_local_dev_and_tests_behave_as_before():
    assert Settings(_env_file=None).RUN_MODE == "all"
    assert main._run_mode() == "all"
    assert main._runs_background_jobs() and main._serves_public_routes()


@pytest.mark.parametrize("value, jobs, routes", [("api", False, True), ("worker", True, False), ("all", True, True)])
async def test_mode_helpers(mode, value, jobs, routes):
    mode(value)
    assert main._runs_background_jobs() is jobs
    assert main._serves_public_routes() is routes


def _routes_in_a_fresh_process(run_mode: str) -> dict:
    """Import app.main in a clean interpreter with RUN_MODE set — exactly
    what Cloud Run does — and list what got mounted."""
    code = (
        "import json, app.main as m\n"
        "paths = sorted({getattr(r, 'path', '') for r in m.app.routes})\n"
        "print(json.dumps({'paths': paths, 'mode': m._run_mode()}))\n"
    )
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": os.environ.get("HOME", "/tmp"),
        "ENV_FILE": "/dev/null",
        "RUN_MODE": run_mode,
        "PYTHONPATH": str(BACKEND),
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    out = subprocess.run([sys.executable, "-c", code], cwd=BACKEND, env=env, capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout.strip().splitlines()[-1])


async def test_a_worker_process_mounts_no_business_routes():
    got = _routes_in_a_fresh_process("worker")
    assert got["mode"] == "worker"
    assert set(got["paths"]) <= {"/api/health", "/api/ready", "/robots.txt"}, got["paths"]
    assert not any(p.startswith("/api/v1") or p.startswith("/uploads") for p in got["paths"])


async def test_an_api_process_mounts_every_business_route():
    got = _routes_in_a_fresh_process("api")
    assert got["mode"] == "api"
    paths = set(got["paths"])
    for needed in ("/api/health", "/api/ready", "/api/v1/bookings", "/api/v1/payments/webhook", "/api/v1/ws", "/uploads"):
        assert needed in paths, needed


async def test_worker_answers_only_its_probes(db, mode):
    """Belt and braces: even with the routers mounted (this process imported
    in `all`), a worker 404s every business path — public reads, webhooks,
    uploads, CORS preflights — and refuses websockets."""
    mode("worker")
    async with _client() as c:
        health = await c.get("/api/health")
        ready = await c.get("/api/ready")
        services = await c.get("/api/v1/services")
        webhook = await c.post("/api/v1/payments/webhook", content=b"{}")
        upload = await c.get("/uploads/photos/x.jpg")
        robots = await c.get("/robots.txt")
        preflight = await c.options(
            "/api/v1/bookings", headers={"Origin": "http://localhost:5173", "Access-Control-Request-Method": "POST"}
        )
    assert health.status_code == 200 and health.json()["mode"] == "worker"
    assert ready.status_code == 200, ready.text
    assert ready.json()["mode"] == "worker"
    for r in (services, webhook, upload, robots, preflight):
        assert r.status_code == 404, (r.request.url, r.status_code)
    assert services.json()["error_code"] == "NOT_FOUND"
    assert "x-request-id" in services.headers, "the request-id middleware still wraps the guard"


def test_worker_refuses_websockets():
    from fastapi.testclient import TestClient
    from starlette.websockets import WebSocketDisconnect

    original = settings.RUN_MODE
    settings.RUN_MODE = "worker"
    try:
        client = TestClient(main.app)
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect("/api/v1/ws?token=garbage") as ws:
                ws.receive_json()
    finally:
        settings.RUN_MODE = original


async def test_api_serves_business_routes_and_probes(db, mode):
    """The API alone serves customers — nothing it needs is in the worker."""
    mode("api")
    async with _client() as c:
        services = await c.get("/api/v1/services")
        health = await c.get("/api/health")
        ready = await c.get("/api/ready")
    assert services.status_code == 200, services.text
    assert health.status_code == 200 and health.json()["mode"] == "api"
    assert ready.status_code == 200 and ready.json()["mode"] == "api"
    assert ready.json()["checks"]["database"] == "ok"


async def test_readiness_fails_in_both_modes_when_critical_indexes_are_missing(db, mode, monkeypatch):
    from app.core import database

    async def missing(*_a, **_k):
        return ["bookings: uniq_active_visit_v3"]

    monkeypatch.setattr(database, "missing_critical_indexes", missing, raising=False)
    for value in ("api", "worker"):
        mode(value)
        monkeypatch.setattr(main, "_indexes_ok_until", 0.0)
        async with _client() as c:
            r = await c.get("/api/ready")
        assert r.status_code == 503, value
        assert r.json()["checks"]["indexes"] == "missing" and r.json()["mode"] == value


# ---------------------------------------------------- start-up / shut-down
@pytest.fixture
def boot(monkeypatch, db):
    """Run the real on_startup / on_shutdown with every loop and external
    effect replaced by a recorder. Loops block until shutdown sets the stop
    event (like the real ones), so a clean shutdown proves they stop."""
    started: list[str] = []
    connect_calls: list[dict] = []
    order: list[str] = []

    def loop(name):
        async def run(*_a, **_k):
            started.append(name)
            while not main._stopping():
                await main._sleep_unless_stopping(0.05)
            order.append(f"{name}_stopped")

        return run

    async def deferred():
        started.append("deferred_init")

    async def connect(build_indexes: bool = True):
        connect_calls.append({"build_indexes": build_indexes})

    async def close_clients():
        order.append("http_clients_closed")

    async def close_mongo():
        order.append("mongo_closed")

    from app.core import http_client
    from app.core.ws_manager import manager as ws_manager

    monkeypatch.setattr(main, "ensure_valid_settings", lambda *_a, **_k: None)
    monkeypatch.setattr(main, "init_error_tracking", lambda *_a, **_k: None)
    monkeypatch.setattr(main, "connect_to_mongo", connect)
    monkeypatch.setattr(main, "_deferred_init", deferred)
    monkeypatch.setattr(main, "_reminder_loop", loop("reminder"))
    monkeypatch.setattr(main, "_whatsapp_retry_loop", loop("whatsapp_retry"))
    monkeypatch.setattr(main, "_template_sync_loop", loop("template_sync"))
    monkeypatch.setattr(ws_manager, "run_relay", loop("ws_relay"))
    monkeypatch.setattr(http_client, "close_shared_clients", close_clients)
    monkeypatch.setattr(main, "close_mongo_connection", close_mongo)
    # Restore the module's globals afterwards.
    monkeypatch.setattr(main, "_reminder_task", None)
    monkeypatch.setattr(main, "_stop_event", None)
    monkeypatch.setattr(main, "_startup_tasks", set())
    monkeypatch.setattr(main, "_maintenance_tasks", set())
    monkeypatch.setattr(main, "_loop_tasks", {})

    class Boot:
        pass

    b = Boot()
    b.started, b.connect_calls, b.order = started, connect_calls, order
    return b


async def _start_then_stop(boot) -> None:
    await main.on_startup()
    await asyncio.sleep(0.15)  # let the tasks begin
    assert main._dead_loops() == []
    await main.on_shutdown()


async def test_api_mode_starts_no_background_loops_and_builds_no_indexes(boot, mode):
    mode("api")
    await _start_then_stop(boot)
    assert sorted(boot.started) == ["ws_relay"], "the API runs only the websocket relay"
    assert boot.connect_calls == [{"build_indexes": False}]
    assert boot.order[-2:] == ["http_clients_closed", "mongo_closed"]
    assert "ws_relay_stopped" in boot.order


async def test_worker_mode_starts_every_loop_and_the_boot_work(boot, mode):
    mode("worker")
    await _start_then_stop(boot)
    assert sorted(boot.started) == ["deferred_init", "reminder", "template_sync", "whatsapp_retry", "ws_relay"]
    assert boot.connect_calls == [{"build_indexes": False}], "indexes are built in the background, not before the probes answer"
    # Clean shutdown: every loop stopped at its checkpoint BEFORE the clients
    # and the database were closed.
    closed = boot.order.index("http_clients_closed")
    for name in ("reminder", "whatsapp_retry", "template_sync", "ws_relay"):
        assert boot.order.index(f"{name}_stopped") < closed, name
    assert boot.order[-1] == "mongo_closed"


async def test_all_mode_runs_everything_in_one_process(boot, mode):
    mode("all")
    await _start_then_stop(boot)
    assert sorted(boot.started) == ["deferred_init", "reminder", "template_sync", "whatsapp_retry", "ws_relay"]


async def test_startup_logs_the_mode(boot, mode, caplog):
    import logging

    mode("worker")
    with caplog.at_level(logging.WARNING, logger="app.main"):
        await _start_then_stop(boot)
    assert any("RUN_MODE=worker" in r.getMessage() for r in caplog.records)


# --------------------------------------------------------- loop liveness
async def test_health_fails_when_a_started_loop_died(db, monkeypatch):
    async def crashed():
        raise RuntimeError("bug")

    task = asyncio.create_task(crashed())
    await asyncio.sleep(0)
    await asyncio.gather(task, return_exceptions=True)
    monkeypatch.setattr(main, "_loop_tasks", {"reminder": task})
    monkeypatch.setattr(main, "_stop_event", asyncio.Event())
    async with _client() as c:
        r = await c.get("/api/health")
    assert r.status_code == 503 and r.json()["error_code"] == "LOOP_DEAD"
    assert r.json()["dead_loops"] == ["reminder"]
    # During shutdown a finished loop is expected, not dead.
    main._stop_event.set()
    async with _client() as c:
        assert (await c.get("/api/health")).status_code == 200


async def test_health_ignores_loops_that_were_never_started(db, monkeypatch):
    monkeypatch.setattr(main, "_loop_tasks", {})
    async with _client() as c:
        r = await c.get("/api/health")
    assert r.status_code == 200


# ------------------------------------------------------------- the lease
@pytest.fixture
async def clear_lease(db):
    await db.locks.delete_many({"_id": main._LEASE_ID})
    yield
    await db.locks.delete_many({"_id": main._LEASE_ID})


async def test_two_workers_by_mistake_never_both_sweep(db, clear_lease):
    """Two worker instances (a mis-set max-instances, or old + new revision
    during a deploy) race for the lease: exactly one wins, every time."""
    for round_ in range(10):
        await db.locks.delete_many({"_id": main._LEASE_ID})
        results = await asyncio.gather(*(main._claim_lease(db, f"worker-{round_}-{i}") for i in range(4)))
        assert results.count(True) == 1, results
    holder = (await db.locks.find_one({"_id": main._LEASE_ID}))["holder"]
    assert await main._claim_lease(db, holder), "the holder keeps renewing"
    assert not await main._claim_lease(db, "someone-else")


async def test_sweeps_resume_when_the_worker_comes_back(db, clear_lease):
    """A crashed worker's lease lapses and a new instance takes over; a
    cleanly stopped one hands over at once."""
    assert await main._claim_lease(db, "old-worker")
    assert not await main._claim_lease(db, "new-worker")
    # Crash: nobody renews; the lease expires.
    await db.locks.update_one(
        {"_id": main._LEASE_ID}, {"$set": {"expires_at": datetime.now(timezone.utc) - timedelta(seconds=1)}}
    )
    assert await main._claim_lease(db, "new-worker")
    # Clean stop: released immediately.
    await main._release_lease(db, "new-worker")
    assert await main._claim_lease(db, "third-worker")


# ------------------------------------------------------------ the config
def test_production_requires_an_explicit_run_mode():
    from tests.test_fix_infra_config import GOOD_PRODUCTION

    values = {k: v for k, v in GOOD_PRODUCTION.items() if k != "RUN_MODE"}
    problems = validate_settings(Settings(_env_file=None, **values))
    assert any("RUN_MODE" in p for p in problems), problems
    for value in ("api", "worker", "all"):
        assert validate_settings(Settings(_env_file=None, **{**values, "RUN_MODE": value})) == []


def test_unknown_run_mode_fails_closed_in_any_env():
    for value in ("API", "both", "web", ""):
        dev = Settings(_env_file=None, APP_ENV="development", DEBUG=True, RUN_MODE=value)
        assert any("RUN_MODE" in p for p in validate_settings(dev)), value
        with pytest.raises(ConfigError):
            ensure_valid_settings(dev)


def test_whatsapp_business_number_is_required_and_checked_in_production():
    from tests.test_fix_infra_config import GOOD_PRODUCTION

    missing = validate_settings(Settings(_env_file=None, **{**GOOD_PRODUCTION, "WHATSAPP_BUSINESS_NUMBER": ""}))
    assert any("WHATSAPP_BUSINESS_NUMBER" in p for p in missing), missing
    bad = validate_settings(Settings(_env_file=None, **{**GOOD_PRODUCTION, "WHATSAPP_BUSINESS_NUMBER": "12345"}))
    assert any("WHATSAPP_BUSINESS_NUMBER" in p for p in bad), bad
    for ok in ("9876500000", "919876500000", "+91 98765 00000"):
        assert validate_settings(Settings(_env_file=None, **{**GOOD_PRODUCTION, "WHATSAPP_BUSINESS_NUMBER": ok})) == []
    dev = Settings(_env_file=None, APP_ENV="development", DEBUG=True)
    assert not any("WHATSAPP_BUSINESS_NUMBER" in p for p in validate_settings(dev)), "production-only rule"
