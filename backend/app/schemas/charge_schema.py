"""Customer account charges (late-cancellation charges, founder 2026-10-07)
— request shapes. See app/services/customer_charge_service.py."""
from typing import Optional

from pydantic import BaseModel, Field, field_validator

from app.schemas.content_schema import BookingPolicyUpdateRequest
from app.services.booking_policy_service import CANCELLATION_FEE_MAX


def _whole_rupees(v):
    from app.utils.money import round_rupees

    return None if v is None else float(round_rupees(v))


class ChargeAdjustRequest(BaseModel):
    """A manager (own center) or admin REDUCING a charge — never raising it.
    0 removes it (status "waived")."""
    amount: float = Field(ge=0, le=CANCELLATION_FEE_MAX)
    note: Optional[str] = Field(default=None, max_length=300)

    _rupees = field_validator("amount")(_whole_rupees)

    @field_validator("note")
    @classmethod
    def _note(cls, v: Optional[str]) -> Optional[str]:
        v = " ".join((v or "").split())
        return v or None


class BookingPolicyWithChargesUpdateRequest(BookingPolicyUpdateRequest):
    """PUT /booking-policy — the existing validated policy fields plus the
    three late-cancellation charge amounts (₹0..₹2000, whole rupees)."""
    cancellation_fee_1_to_4h: Optional[float] = Field(default=None, ge=0, le=CANCELLATION_FEE_MAX)
    cancellation_fee_under_1h: Optional[float] = Field(default=None, ge=0, le=CANCELLATION_FEE_MAX)
    cancellation_fee_after_captain_left: Optional[float] = Field(default=None, ge=0, le=CANCELLATION_FEE_MAX)

    _fees = field_validator(
        "cancellation_fee_1_to_4h", "cancellation_fee_under_1h", "cancellation_fee_after_captain_left",
    )(_whole_rupees)
