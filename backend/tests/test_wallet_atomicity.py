"""
C1 regression: wallet balance changes are single atomic $inc operations.
Concurrent credits/debits must all land (no lost updates), and a debit
guarded by allow_negative=False must be unable to race the balance below
zero — the guard is part of the update filter, not a Python-side check.
"""
import asyncio

import pytest
from bson import ObjectId

from app.core.exceptions import BadRequestException
from app.services.wallet_service import WalletService

from tests.factories import make_captain, make_service_center


@pytest.fixture
async def rig(db, cleanup):
    center_id = await make_service_center(db)
    cleanup.append(("service_centers", {"_id": ObjectId(center_id)}))
    captain_id = await make_captain(db, center_id, wallet_balance=0.0)
    cleanup.append(("users", {"_id": ObjectId(captain_id)}))
    cleanup.append(("captain_wallets", {"captain_id": captain_id}))
    cleanup.append(("wallet_transactions", {"captain_id": captain_id}))
    return {"db": db, "captain_id": captain_id}


@pytest.mark.asyncio
async def test_concurrent_credits_and_debits_never_lose_updates(rig, db):
    svc = WalletService(db)
    cid = rig["captain_id"]
    await svc.credit(cid, 1000.0, None, "test seed")

    # 10 credits of 10 + 10 debits of 5, all in flight at once.
    await asyncio.gather(
        *[svc.credit(cid, 10.0, None, "job payout") for _ in range(10)],
        *[svc.debit(cid, 5.0, None, "cash collected", allow_negative=True) for _ in range(10)],
    )
    wallet = await svc.get_wallet_view(cid)
    assert wallet["balance"] == 1000.0 + 100.0 - 50.0  # every operation counted exactly once
    assert await db.wallet_transactions.count_documents({"captain_id": cid}) == 21


@pytest.mark.asyncio
async def test_guarded_debit_cannot_race_below_zero(rig, db):
    svc = WalletService(db)
    cid = rig["captain_id"]
    await svc.credit(cid, 100.0, None, "test seed")

    # Five concurrent guarded debits of 60 against a balance of 100:
    # exactly ONE may succeed; the rest must fail cleanly.
    results = await asyncio.gather(
        *[svc.debit(cid, 60.0, None, "cash booking", allow_negative=False) for _ in range(5)],
        return_exceptions=True,
    )
    successes = [r for r in results if isinstance(r, dict)]
    failures = [r for r in results if isinstance(r, BadRequestException)]
    assert len(successes) == 1 and len(failures) == 4
    wallet = await svc.get_wallet_view(cid)
    assert wallet["balance"] == 40.0  # never negative, never double-debited
