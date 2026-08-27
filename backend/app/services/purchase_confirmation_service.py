"""
Issues and redeems the opaque token behind the public /thank-you page —
see PurchaseConfirmationModel for why this exists instead of just passing
a booking/subscription id in the URL.
"""
import secrets
from datetime import datetime, timedelta, timezone

from motor.motor_asyncio import AsyncIOMotorDatabase

from app.repositories.purchase_confirmation_repository import PurchaseConfirmationRepository
from app.utils.serializers import serialize_doc

_TICKET_LIFETIME_MINUTES = 30


class PurchaseConfirmationService:
    def __init__(self, db: AsyncIOMotorDatabase):
        self.repo = PurchaseConfirmationRepository(db)

    async def issue(self, type_: str, reference_id: str, customer_id: str | None, payload: dict) -> str:
        token = secrets.token_urlsafe(24)
        await self.repo.create({
            "token": token,
            "type": type_,
            "reference_id": reference_id,
            "customer_id": customer_id,
            "payload": payload,
            "expires_at": datetime.now(timezone.utc) + timedelta(minutes=_TICKET_LIFETIME_MINUTES),
        })
        return token

    async def redeem(self, token: str) -> dict | None:
        """Returns the confirmation payload, or None if the token doesn't
        exist or has expired — NOT marked single-use-and-gone on first
        read, deliberately: a customer legitimately refreshing the Thank
        You page (or React re-rendering it twice) must keep seeing it
        within the same short window. Unguessability + a 30-minute expiry
        is what actually satisfies "can't be reached by typing a URL" —
        strict single-use would only add a real risk of locking out a
        genuine reload for no real security gain (the token was never
        guessable in the first place)."""
        record = await self.repo.find_by_token(token)
        if not record:
            return None
        expires_at = record["expires_at"]
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        if expires_at < datetime.now(timezone.utc):
            return None
        return serialize_doc(record)
