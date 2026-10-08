"""Customer wallet — request shapes (spec §1.1). See
app/services/customer_wallet_service.py."""
from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator, model_validator


def _clean(v: Optional[str]) -> Optional[str]:
    v = " ".join((v or "").split())
    return v or None


class WalletPayoutRequest(BaseModel):
    """Founder 2026-10-07 (MONEY-2): a manager never adds money to a
    customer's wallet at will — he only PAYS BACK money for one of that
    customer's bookings that went wrong (cancelled, delayed, a complaint, a
    service problem), from the center's side, in cash or online, and
    records it here ("Paid By Manager"). Wallet credit is debited first; a
    customer with no credit gets a goodwill payback recorded on the
    booking. Per booking, all paybacks together never exceed what the
    customer paid for it (checked atomically server-side).

    `reference` is required unless `method` is cash. `idempotency_key`
    (optional, cash especially): the same key twice records once; without
    it a cash payback is de-duplicated on (booking, amount, minute)."""
    booking_id: str = Field(pattern=r"^[0-9a-fA-F]{24}$")
    reason: Literal["cancelled", "delayed", "complaint", "service_issue", "other"]
    method: Literal["cash", "upi", "bank_transfer", "other"]
    reference: Optional[str] = Field(default=None, max_length=100)
    amount: float = Field(gt=0, le=1_000_000)
    note: Optional[str] = Field(default=None, max_length=300)
    idempotency_key: Optional[str] = Field(default=None, min_length=6, max_length=64, pattern=r"^[A-Za-z0-9_\-:.]+$")

    _note = field_validator("note")(_clean)
    _reference = field_validator("reference")(_clean)

    @field_validator("amount")
    @classmethod
    def _paise(cls, v: float) -> float:
        return round(float(v), 2)

    @model_validator(mode="after")
    def _needs(self) -> "WalletPayoutRequest":
        if self.method != "cash" and len(self.reference or "") < 3:
            raise ValueError("Enter the transfer reference (UPI / bank reference)")
        if self.reason == "other" and len(self.note or "") < 10:
            raise ValueError("Say what went wrong (at least 10 characters)")
        return self


class WalletAdjustRequest(BaseModel):
    """Admin correction: + credits, − debits. Audited; the customer is told.
    `idempotency_key`: the same key twice applies once (a double submit)."""
    amount: float = Field(ge=-1_000_000, le=1_000_000)
    note: str = Field(min_length=3, max_length=300)
    idempotency_key: Optional[str] = Field(default=None, min_length=6, max_length=64, pattern=r"^[A-Za-z0-9_\-:.]+$")

    @field_validator("amount")
    @classmethod
    def _nonzero(cls, v: float) -> float:
        v = round(float(v), 2)
        if abs(v) < 0.01:
            raise ValueError("Enter a non-zero amount")
        return v

    _note = field_validator("note")(_clean)
