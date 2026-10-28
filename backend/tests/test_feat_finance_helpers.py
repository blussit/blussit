"""Shared helpers for test_feat_finance_* (admin finance ledger). No tests."""
from datetime import timedelta

import pytest

from app.services import finance_service
from app.utils.timezone import now_ist
from tests import test_fix_core_helpers as h

BASE = "/api/v1/admin/finance"


def today() -> str:
    return now_ist().date().isoformat()


def day(offset: int) -> str:
    return (now_ist().date() + timedelta(days=offset)).isoformat()


def body(**kw) -> dict:
    b = {"title": "Office rent", "type": "debit", "amount_paise": 1500000, "category": "Rent", "description": "October", "done_by": "Asha"}
    b.update(kw)
    return {k: v for k, v in b.items() if v is not None}


async def wipe(db) -> None:
    await db.finance_entries.delete_many({})
    await db.audit_logs.delete_many({"module": "finance"})
    finance_service.clear_cache()


@pytest.fixture
async def clean(db):
    await wipe(db)
    yield
    await wipe(db)


async def admin_h(db) -> dict:
    return (await h.admin(db))["h"]


async def make(c, headers, **kw) -> dict:
    r = await c.post(f"{BASE}/entries", json=body(**kw), headers=headers)
    assert r.status_code == 200, r.text
    return r.json()["data"]
