from typing import Optional

from app.models.base import BusinessRecordBase
from app.models.enums import WalletTransactionType, WithdrawalStatus


class CaptainWalletModel(BusinessRecordBase):
    captain_id: str
    balance: float = 0
    minimum_balance: float = 50.0
    bank_account_number: Optional[str] = None
    bank_ifsc: Optional[str] = None
    bank_account_holder: Optional[str] = None


class WalletTransactionModel(BusinessRecordBase):
    captain_id: str
    wallet_id: str
    type: WalletTransactionType
    amount: float
    balance_after: float
    booking_id: Optional[str] = None
    description: str = ""


class WithdrawalRequestModel(BusinessRecordBase):
    captain_id: str
    amount: float
    status: WithdrawalStatus = WithdrawalStatus.PENDING
    reviewed_by: Optional[str] = None
    review_note: Optional[str] = None
