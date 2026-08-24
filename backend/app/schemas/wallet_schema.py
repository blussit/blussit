from typing import Optional

from pydantic import BaseModel, Field


class TopUpRequest(BaseModel):
    amount: float = Field(gt=0)


class WithdrawalCreateRequest(BaseModel):
    amount: float = Field(gt=0)


class WithdrawalReviewRequest(BaseModel):
    status: str  # approved | rejected | paid
    review_note: Optional[str] = None


class BankDetailsRequest(BaseModel):
    bank_account_number: str
    bank_ifsc: str
    bank_account_holder: str


class WalletAdjustmentRequest(BaseModel):
    amount: float
    description: str
