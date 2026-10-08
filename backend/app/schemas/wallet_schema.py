import re
from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator

# Money fields refuse NaN/±Infinity (allow_inf_nan=False): a NaN adjustment
# was $inc'ed into a captain's balance — permanently "nan" — before
# anything downstream failed, and `inf` passes a plain gt=0 check.


class WithdrawalCreateRequest(BaseModel):
    amount: float = Field(gt=0, allow_inf_nan=False)


class WithdrawalReviewRequest(BaseModel):
    # Literal, not a bare str: a typo'd/case-variant status ("Approved")
    # used to be written verbatim while silently skipping the debit.
    status: Literal["approved", "rejected", "paid"]
    review_note: Optional[str] = None


class BankDetailsRequest(BaseModel):
    """Where a captain's payouts go. Validated to the Indian formats, so a
    typo is caught here rather than at the bank on payout day."""
    bank_account_number: str
    bank_ifsc: str
    bank_account_holder: str = Field(min_length=2, max_length=100)

    @field_validator("bank_account_number")
    @classmethod
    def _account_number(cls, v: str) -> str:
        digits = re.sub(r"[\s-]", "", v)
        if not re.fullmatch(r"\d{9,18}", digits):
            raise ValueError("Enter a valid bank account number (9 to 18 digits).")
        return digits

    @field_validator("bank_ifsc")
    @classmethod
    def _ifsc(cls, v: str) -> str:
        code = v.strip().upper()
        if not re.fullmatch(r"[A-Z]{4}0[A-Z0-9]{6}", code):
            raise ValueError("Enter a valid 11-character IFSC code, e.g. SBIN0001234.")
        return code

    @field_validator("bank_account_holder")
    @classmethod
    def _holder(cls, v: str) -> str:
        name = " ".join(v.split())
        if len(name) < 2:
            raise ValueError("Enter the account holder's name.")
        return name


class WalletAdjustmentRequest(BaseModel):
    amount: float = Field(allow_inf_nan=False)
    description: str
