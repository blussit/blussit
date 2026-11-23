"""Service-center operation (franchise / self-operated) and franchise
settlements — ARCHITECTURE.md §7.13 (D18, D19).

Money is integer paise, days are IST `YYYY-MM-DD`. Validation is strict: a
franchise center needs a partner and a share, a self-operated one carries
neither. `OperationInput` is both the create payload and (merged onto the
stored operation by ServiceCenterService.update) the edit payload."""
import re
from datetime import date, timedelta
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.utils.timezone import now_ist

MAX_SALARY_PAISE = 10_00_000 * 100  # ₹10,00,000 a month
MAX_FEE_PAISE = 10_00_00_000 * 100  # ₹10 crore
MAX_SETTLEMENT_PAISE = 1_00_00_000 * 100  # ₹1 crore — same cap as a finance entry
MAX_KITS = 1000
MAX_COMMISSION_PERCENT = 70.0
_PHONE = re.compile(r"^\+?[0-9][0-9 \-]{6,18}$")
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_DAY = re.compile(r"^\d{4}-\d{2}-\d{2}$")

OperationType = Literal["self_operated", "franchise"]
PayManagedBy = Literal["blussit", "franchise"]
PayBasis = Literal["salary", "commission"]
SettlementKind = Literal["pay_partner", "receive_from_partner", "franchise_fee", "kit_sale"]


def today_ist() -> date:
    return now_ist().date()


def check_day(value: str, name: str, *, max_future_days: int, max_past_days: int) -> str:
    """A real IST calendar day inside the allowed window."""
    value = (value or "").strip()
    if not _DAY.match(value):
        raise ValueError(f"{name} must be a day as YYYY-MM-DD.")
    try:
        day = date.fromisoformat(value)
    except ValueError:
        raise ValueError(f"{name} is not a real day.") from None
    today = today_ist()
    if day > today + timedelta(days=max_future_days):
        raise ValueError(f"{name} is too far ahead.")
    if day < today - timedelta(days=max_past_days):
        raise ValueError(f"{name} is too far back.")
    return value


class PartnerInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=2, max_length=120)
    phone: Optional[str] = Field(default=None, max_length=24)
    email: Optional[str] = Field(default=None, max_length=120)

    @field_validator("name")
    @classmethod
    def _name(cls, v: str) -> str:
        v = v.strip()
        if len(v) < 2:
            raise ValueError("Enter the partner's name.")
        return v

    @field_validator("phone")
    @classmethod
    def _phone(cls, v):
        v = (v or "").strip()
        if v and not _PHONE.match(v):
            raise ValueError("Enter a valid partner phone number.")
        return v or None

    @field_validator("email")
    @classmethod
    def _email(cls, v):
        v = (v or "").strip().lower()
        if v and not _EMAIL.match(v):
            raise ValueError("Enter a valid partner email.")
        return v or None


class DefaultCaptainPay(BaseModel):
    """What a new captain of this center is prefilled with (never forced)."""

    model_config = ConfigDict(extra="forbid")

    basis: Optional[PayBasis] = None
    salary_monthly_paise: Optional[int] = Field(default=None, ge=0, le=MAX_SALARY_PAISE)
    commission_percent: Optional[float] = Field(default=None, ge=0, le=MAX_COMMISSION_PERCENT)

    @model_validator(mode="after")
    def _by_basis(self) -> "DefaultCaptainPay":
        if self.basis == "salary":
            if not self.salary_monthly_paise:
                raise ValueError("Enter the default monthly salary (more than ₹0, up to ₹10,00,000).")
            if self.commission_percent is not None:
                raise ValueError("A salary default can't also carry a commission percent.")
        elif self.basis == "commission":
            if self.commission_percent is None:
                raise ValueError("Enter the default commission percent (0 to 70).")
            if self.salary_monthly_paise is not None:
                raise ValueError("A commission default can't also carry a salary.")
        elif self.salary_monthly_paise is not None or self.commission_percent is not None:
            raise ValueError("Pick a pay basis (salary or commission) for the default captain pay.")
        return self


class OperationInput(BaseModel):
    """How a center is run. On create every field is optional (defaults:
    self-operated). On update only the fields sent change; the merged result
    is validated as a whole (see `finalize_operation`)."""

    model_config = ConfigDict(extra="forbid")

    type: Optional[OperationType] = None
    partner: Optional[PartnerInput] = None
    revenue_share_percent: Optional[float] = Field(default=None, ge=0, le=100)
    franchise_fee_paise: Optional[int] = Field(default=None, ge=0, le=MAX_FEE_PAISE)
    kits_purchased: Optional[int] = Field(default=None, ge=0, le=MAX_KITS)
    kits_given: Optional[int] = Field(default=None, ge=0, le=MAX_KITS)
    captain_pay_managed_by: Optional[PayManagedBy] = None
    default_captain_pay: Optional[DefaultCaptainPay] = None
    # IST day the new type / share / pay-manager takes effect (default today).
    effective_from: Optional[str] = None
    # Create only: the franchise fee was already received (finance credit now)
    # vs still pending (recorded, no finance entry until a settlement posts it).
    franchise_fee_received_now: Optional[bool] = None
    franchise_fee_method: Optional[str] = Field(default=None, max_length=30)

    @field_validator("effective_from")
    @classmethod
    def _eff(cls, v):
        if v is None:
            return v
        # A new rule applies to jobs completed from this day: never backdated
        # (jobs already completed keep their frozen share), at most a year ahead.
        return check_day(v, "Effective from", max_future_days=366, max_past_days=0)


def finalize_operation(merged: dict) -> dict:
    """Validate a complete operation dict (stored values + the edit) and
    return its normalised form. Raises ValueError with a plain message."""
    typ = merged.get("type") or "self_operated"
    partner = merged.get("partner")
    share = merged.get("revenue_share_percent")
    fee = int(merged.get("franchise_fee_paise") or 0)
    purchased = int(merged.get("kits_purchased") or 0)
    given = int(merged.get("kits_given") or 0)
    managed_by = merged.get("captain_pay_managed_by") or "blussit"
    if typ == "franchise":
        if not partner or not (partner.get("name") or "").strip():
            raise ValueError("A franchise center needs the partner's name.")
        if share is None or not (0 < float(share) <= 100):
            raise ValueError("Enter the partner's revenue share (more than 0 and at most 100 percent).")
        if given:
            raise ValueError("Kits given belongs to a self-operated center; enter kits purchased for a franchise.")
        share = round(float(share), 2)
    else:
        if partner:
            raise ValueError("A self-operated center has no franchise partner.")
        if share is not None:
            raise ValueError("A self-operated center has no revenue share.")
        if fee:
            raise ValueError("A self-operated center has no franchise fee.")
        if purchased:
            raise ValueError("Kits purchased belongs to a franchise center; enter kits given for a self-operated one.")
        if managed_by != "blussit":
            raise ValueError("Only a franchise center can have captain pay managed by the franchise.")
        partner, share = None, None
    pay = merged.get("default_captain_pay") or {}
    pay_model = DefaultCaptainPay(**{k: pay.get(k) for k in ("basis", "salary_monthly_paise", "commission_percent")})
    return {
        "type": typ,
        "partner": ({"name": partner["name"].strip(), "phone": partner.get("phone"), "email": partner.get("email")} if partner else None),
        "revenue_share_percent": share,
        "franchise_fee_paise": fee,
        "kits_purchased": purchased,
        "kits_given": given,
        "captain_pay_managed_by": managed_by,
        "default_captain_pay": pay_model.model_dump(),
        "effective_from": merged.get("effective_from"),
    }


class SettlementCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: SettlementKind
    amount_paise: int = Field(gt=0, le=MAX_SETTLEMENT_PAISE)
    method: Literal["cash", "bank", "upi", "other"] = "bank"
    paid_on: Optional[str] = None
    note: str = Field(default="", max_length=300)
    idempotency_key: Optional[str] = Field(default=None, min_length=8, max_length=80)

    @field_validator("paid_on")
    @classmethod
    def _day(cls, v):
        if v is None:
            return v
        return check_day(v, "Paid on", max_future_days=1, max_past_days=5 * 365)

    @field_validator("note")
    @classmethod
    def _note(cls, v: str) -> str:
        return v.strip()


class SettlementVoid(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str = Field(min_length=3, max_length=200)
