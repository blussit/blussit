"""Captain pay: input shapes (ARCHITECTURE.md §7.13). Money is integer PAISE,
days are IST `YYYY-MM-DD`, months `YYYY-MM`. Strings are stripped; every
bound here is the server's own — the app's checks are only a convenience."""
from typing import Literal, Optional

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, field_validator, model_validator

DAY_PATTERN = r"^\d{4}-\d{2}-\d{2}$"
MONTH_PATTERN = r"^\d{4}-(0[1-9]|1[0-2])$"
MAX_SALARY_PAISE = 10_00_000 * 100  # ₹10,00,000 a month
MAX_PAYMENT_PAISE = 1_00_00_000 * 100  # ₹1 crore in one entry
MAX_DEPOSIT_PAISE = 10_00_000 * 100
MAX_PETROL_PAISE = 50_000 * 100  # a day

PayBasisLiteral = Literal["salary", "commission", "legacy"]


class _Strip(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)


class PayKitItem(_Strip):
    name: str = Field(min_length=1, max_length=60)
    qty: int = Field(default=1, ge=1, le=50)
    serial: Optional[str] = Field(default=None, max_length=40)
    value_paise: int = Field(default=0, ge=0, le=MAX_PAYMENT_PAISE)


class PayKitIn(_Strip):
    provided: bool = False
    items: list[PayKitItem] = Field(default_factory=list, max_length=30)
    issued_on: Optional[str] = Field(default=None, pattern=DAY_PATTERN)
    returned_on: Optional[str] = Field(default=None, pattern=DAY_PATTERN)
    notes: Optional[str] = Field(default=None, max_length=300)


class PayCommissionIn(_Strip):
    mode: Literal["percent", "per_booking"] = "percent"
    percent: float = Field(default=0.0, ge=0, le=100)


class PayTermsIn(_Strip):
    """Every field optional: on create the gaps are filled from the center's
    default and the global defaults; on a terms edit a missing field keeps
    its current value."""

    basis: Optional[PayBasisLiteral] = None
    salary_monthly_paise: Optional[int] = Field(default=None, ge=0, le=MAX_SALARY_PAISE)
    commission: Optional[PayCommissionIn] = None
    # Weekly-off weekdays (0 = Monday … 6 = Sunday); default Saturday + Sunday. The old
    # single `weekly_off` day is still accepted (= that one day).
    weekly_off_days: Optional[list[int]] = Field(default=None, max_length=6)
    weekly_off: Optional[int] = Field(default=None, ge=0, le=6)
    paid_leaves_per_month: Optional[int] = Field(default=None, ge=0, le=10)
    petrol_per_day_paise: Optional[int] = Field(default=None, ge=0, le=MAX_PETROL_PAISE)
    extra_day_on_off: Optional[bool] = None
    deposit_expected_paise: Optional[int] = Field(default=None, ge=0, le=MAX_DEPOSIT_PAISE)
    kit: Optional[PayKitIn] = None
    # The captain's first / last paid day (default: the day the account was made).
    start_date: Optional[str] = Field(default=None, pattern=DAY_PATTERN)
    end_date: Optional[str] = Field(default=None, pattern=DAY_PATTERN)


    @field_validator("weekly_off_days")
    @classmethod
    def _days(cls, value):
        if value is None:
            return value
        if any((not isinstance(d, int)) or isinstance(d, bool) or d < 0 or d > 6 for d in value):
            raise ValueError("Weekly off days are weekdays from Monday to Sunday.")
        return sorted(set(value))


class PayTermsCreate(PayTermsIn):
    """The `pay` block of POST /auth/staff for a captain. `deposit_received`:
    the deposit was handed over right now (posts the ledger row + Finance credit)."""

    deposit_received: bool = False


class PayTermsUpdate(PayTermsIn):
    """PUT /payroll/captains/{id}/terms — an edit takes effect from
    `effective_from` (default today); nothing already accrued is rewritten."""

    effective_from: Optional[str] = Field(default=None, pattern=DAY_PATTERN)
    reason: Optional[str] = Field(default=None, max_length=200)


class PaymentCreate(_Strip):
    kind: Literal["salary", "commission", "petrol", "advance", "other"] = "salary"
    method: Literal["cash", "upi", "bank", "other"] = "cash"
    amount_paise: int = Field(gt=0, le=MAX_PAYMENT_PAISE)
    paid_on: Optional[str] = Field(default=None, pattern=DAY_PATTERN, validation_alias=AliasChoices("paid_on", "date"))
    note: Optional[str] = Field(default=None, max_length=300)
    idempotency_key: str = Field(min_length=8, max_length=80, pattern=r"^[A-Za-z0-9._:\-]+$")
    cash_offset_paise: int = Field(default=0, ge=0, le=MAX_PAYMENT_PAISE)

    @model_validator(mode="after")
    def _offset_within_amount(self):
        if self.cash_offset_paise > self.amount_paise:
            raise ValueError("The cash offset can't be more than the payment.")
        return self


class InvoicePay(_Strip):
    """POST /payroll/captains/{id}/pay-invoice (§7.14). `salary` pays one month
    (`month`), `commission` one period (`from_date` – `to_date`, optionally only
    `booking_ids`); advance / petrol / other take an `amount_paise`. Without an
    `amount_paise` the invoice's own total is paid; a different amount needs a
    `reason`. `expected_gross_paise` (from the preview) refuses a stale screen."""

    kind: Literal["salary", "commission", "petrol", "advance", "other"]
    month: Optional[str] = Field(default=None, pattern=MONTH_PATTERN)
    from_date: Optional[str] = Field(default=None, pattern=DAY_PATTERN)
    to_date: Optional[str] = Field(default=None, pattern=DAY_PATTERN)
    booking_ids: Optional[list[str]] = Field(default=None, max_length=1500)
    amount_paise: Optional[int] = Field(default=None, gt=0, le=MAX_PAYMENT_PAISE)
    expected_gross_paise: Optional[int] = Field(default=None, ge=-MAX_PAYMENT_PAISE, le=MAX_PAYMENT_PAISE)
    reason: Optional[str] = Field(default=None, max_length=200)
    method: Literal["cash", "upi", "bank", "other"] = "cash"
    paid_on: Optional[str] = Field(default=None, pattern=DAY_PATTERN)
    note: Optional[str] = Field(default=None, max_length=300)
    idempotency_key: str = Field(min_length=8, max_length=80, pattern=r"^[A-Za-z0-9._:\-]+$")
    cash_offset_paise: int = Field(default=0, ge=0, le=MAX_PAYMENT_PAISE)

    @model_validator(mode="after")
    def _shape(self):
        if self.kind == "salary" and not self.month:
            raise ValueError("Pick the salary month.")
        if self.kind == "commission" and not (self.from_date or self.to_date):
            raise ValueError("Pick the commission period.")
        if self.kind in ("advance", "petrol", "other") and self.amount_paise is None:
            raise ValueError("Enter the amount.")
        if self.amount_paise is not None and self.cash_offset_paise > self.amount_paise:
            raise ValueError("The cash offset can't be more than the payment.")
        return self


class ReasonRequest(_Strip):
    reason: str = Field(min_length=3, max_length=200)


class AdjustmentCreate(_Strip):
    kind: Literal["bonus", "deduction", "allowance", "commission_extra", "commission_cut"]
    # A positive amount; the kind decides the sign (deduction / commission_cut subtract).
    amount_paise: int = Field(gt=0, le=MAX_PAYMENT_PAISE)
    note: str = Field(min_length=3, max_length=300)
    booking_id: Optional[str] = Field(default=None, max_length=40)
    idempotency_key: str = Field(min_length=8, max_length=80, pattern=r"^[A-Za-z0-9._:\-]+$")


class AttendanceOverride(_Strip):
    date: str = Field(pattern=DAY_PATTERN)
    status: Literal["present", "half_day", "absent", "leave"]
    paid_leave: bool = False
    note: str = Field(min_length=3, max_length=300)


class CommissionSet(_Strip):
    """PUT /payroll/bookings/{id}/commission — one booking's commission set
    by hand: a rupee amount (paise) or a percent of the booking's base."""

    amount_paise: Optional[int] = Field(default=None, ge=0, le=MAX_PAYMENT_PAISE)
    percent: Optional[float] = Field(default=None, ge=0, le=100)
    reason: str = Field(min_length=3, max_length=200)

    @model_validator(mode="after")
    def _one_of(self):
        if (self.amount_paise is None) == (self.percent is None):
            raise ValueError("Send either an amount or a percent — not both, not neither.")
        return self


class FinalizeRequest(_Strip):
    override_amount_paise: Optional[int] = Field(
        default=None, ge=0, le=MAX_PAYMENT_PAISE, validation_alias=AliasChoices("override_amount_paise", "payable_paise"),
    )
    reason: Optional[str] = Field(default=None, max_length=200)


class DepositAction(_Strip):
    action: Literal["received", "refund", "forfeit", "expect"]
    amount_paise: Optional[int] = Field(default=None, ge=0, le=MAX_DEPOSIT_PAISE, validation_alias=AliasChoices("amount_paise", "expected_paise"))
    method: Literal["cash", "upi", "bank", "other"] = "cash"
    note: Optional[str] = Field(default=None, max_length=300)
    key: str = Field(min_length=8, max_length=80, pattern=r"^[A-Za-z0-9._:\-]+$", validation_alias=AliasChoices("key", "idempotency_key"))

    @field_validator("action", mode="before")
    @classmethod
    def _alias(cls, value):
        return "received" if value == "receive" else value

    @model_validator(mode="after")
    def _amount(self):
        if self.amount_paise is None or (self.amount_paise <= 0 and self.action != "expect"):
            raise ValueError("Enter an amount.")
        return self


class KitReturn(_Strip):
    returned_on: Optional[str] = Field(default=None, pattern=DAY_PATTERN)
    note: Optional[str] = Field(default=None, max_length=300)


class KitUpdate(PayKitIn):
    pass
