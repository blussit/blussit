from fastapi import APIRouter, Depends
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.core.dependencies import get_db
from app.core.exceptions import NotFoundException
from app.core.responses import success
from app.services.purchase_confirmation_service import PurchaseConfirmationService

# Deliberately NO auth dependency anywhere in this router — a guest who
# just completed a purchase (once guest checkout exists) has no session
# yet, and even a logged-in customer's Thank You page load shouldn't need
# a fresh token refresh race. Security comes from the token itself being
# unguessable + short-lived (see PurchaseConfirmationService), not from
# authentication.
router = APIRouter(prefix="/purchase-confirmations", tags=["Purchase Confirmations"])


@router.get("/{token}")
async def get_confirmation(token: str, db: AsyncIOMotorDatabase = Depends(get_db)):
    result = await PurchaseConfirmationService(db).redeem(token)
    if not result:
        raise NotFoundException("This confirmation link is invalid or has expired.")
    return success(result)
