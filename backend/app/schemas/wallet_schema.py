from typing import Literal, Optional

from pydantic import BaseModel, Field


class WithdrawalCreateRequest(BaseModel):
    amount: float = Field(gt=0)


class WithdrawalReviewRequest(BaseModel):
    # Literal, not a bare str: a typo'd/case-variant status ("Approved")
    # used to be written verbatim while silently skipping the debit.
    status: Literal["approved", "rejected", "paid"]
    review_note: Optional[str] = None


class BankDetailsRequest(BaseModel):
    bank_account_number: str
    bank_ifsc: str
    bank_account_holder: str


class WalletAdjustmentRequest(BaseModel):
    amount: float
    description: str
