"""
Shared pytest fixtures for the backend test suite.

Runs against a DISTINCT MongoDB database on the SAME Atlas cluster the app
already uses in dev (MONGO_URI is untouched — only MONGO_DB_NAME is
overridden to a "_test" database below) — no separate test infra to stand
up, and dev data is never read or written. The override MUST happen before
any `app.*` module is imported anywhere (app.core.config.Settings() is
constructed once, at first import, and @lru_cache'd) — that's why this is
the very first thing this file does, ahead of every other import, and why
every test file must import fixtures from `tests.conftest` (or rely on
pytest's automatic conftest discovery) rather than importing `app.*`
directly at module load time before conftest has run. pytest itself
guarantees conftest.py is imported before it imports any test module in
the same directory, so this ordering is safe.
"""
import os

os.environ["MONGO_DB_NAME"] = os.environ.get("TEST_MONGO_DB_NAME", "doorstep_vehicle_care_test")

import pytest
import pytest_asyncio
from bson import ObjectId
from motor.motor_asyncio import AsyncIOMotorClient

from app.core.config import settings
from app.core.database import close_mongo_connection, connect_to_mongo, mongodb
from app.seed import seed


@pytest_asyncio.fixture(scope="session")
async def db():
    """One connection for the whole test session, pointed at the isolated
    test database.

    The database is dropped before seeding on every run — this is what
    actually makes the suite "run again and again" without hand-cleanup:
    a prior run that crashed mid-test (so its own `cleanup` fixture never
    got to fire) would otherwise leave orphaned users/bookings with
    colliding unique emails/plates that fail the *next* run for an
    unrelated reason. Safe specifically because this is the dedicated
    "_test" database on the same cluster — MONGO_URI's dev database is
    never touched here.

    `seed()` is the same idempotent function `python -m app.seed` runs for
    local dev — reused here (rather than reinvented) so the reference data
    (vehicle types, services, categories, subscription plans, one service
    center with a manager+captain, pricing config) is the exact real shape
    the app expects. It opens its own connection AND closes it again when
    done (it's written to double as a standalone script) — so a second,
    real connect_to_mongo() is made here afterward for the actual test
    session to use, rather than reusing the one seed() already tore down.
    """
    throwaway = AsyncIOMotorClient(settings.MONGO_URI)
    await throwaway.drop_database(settings.MONGO_DB_NAME)
    throwaway.close()

    await seed()
    await connect_to_mongo()
    yield mongodb.db
    await close_mongo_connection()


@pytest_asyncio.fixture
async def cleanup(db):
    """Per-test cleanup registry. A test appends (collection_name, filter)
    tuples as it creates data; everything is deleted at teardown regardless
    of whether the test passed or failed, so a failing assertion never
    leaves orphaned documents for the next test run to trip over. Deletes
    run in registration order, which tests use to delete children (bookings,
    vehicles, addresses) before parents (users, service centers) where a
    unique index would otherwise make a re-run's insert collide."""
    registry: list[tuple[str, dict]] = []

    yield registry

    for collection_name, flt in registry:
        await db[collection_name].delete_many(flt)


def oid(value: str) -> ObjectId:
    return ObjectId(value)
