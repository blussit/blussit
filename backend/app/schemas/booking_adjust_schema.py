"""
Requests for the two post-booking money tools (founder 2026-10-28):

  - BookingDiscountRequest   a manager (own center) or an admin gives, raises,
                             lowers or removes a discount on an EXISTING
                             booking — POST /bookings/{id}/discount;
  - AdminBookingEditRequest  an admin edits anything on any booking —
                             PATCH /admin/bookings/{id}.

Both forbid unknown fields (extra="forbid"): a body carrying `total_amount`,
`amount_paid`, `platform_earning`, `captain_earning`, `status` (on the
discount) or `center_id` is a 422, never silently dropped — mass assignment
is visible, not hidden. Money in is whole rupees for discounts and tips;
payment corrections may carry paise. NaN / Infinity are refused everywhere.
"""
from __future__ import annotations

import re
from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.schemas.booking_schema import ist_calendar_day

_OBJECT_ID = re.compile(r"^[0-9a-fA-F]{24}$")
_PLAIN_NUMBER = re.compile(r"^[0-9]{1,9}(\.0+)?$", re.ASCII)
_KEY = re.compile(r"^[A-Za-z0-9_.:\-]{6,64}$")
# Control characters (incl. NUL, bidi overrides and zero-width tricks) never
# belong in a reason, note or address line.
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f​-‏‪-‮⁦-⁩﻿]")

MAX_RUPEES = 10_000_000


def clean_text(value, *, min_len: int = 0, max_len: int = 300, field: str = "Text") -> str | None:
    """NFKC-folded, control-stripped, whitespace-collapsed. Newlines survive
    as single spaces: every field here is one line."""
    if value is None:
        return None
    import unicodedata

    text = unicodedata.normalize("NFKC", str(value))
    text = _CONTROL.sub("", text)
    text = " ".join(text.split())
    if len(text) < min_len:
        raise ValueError(f"{field} must be at least {min_len} characters.")
    if len(text) > max_len:
        raise ValueError(f"{field} must be at most {max_len} characters.")
    return text


def _whole_rupees(value, *, field: str):
    """Strict whole-rupee number: no booleans, no NaN / Infinity, no paise."""
    if value is None:
        return None
    if isinstance(value, bool):
        raise ValueError(f"{field} must be a number of rupees.")
    if isinstance(value, str):
        # ASCII digits only: float() would also take "٣٠" (Arabic-Indic),
        # "1e3", "  12 ", "nan" — a string is for a number typed in a box.
        text = value.strip()
        if not _PLAIN_NUMBER.match(text):
            raise ValueError(f"{field} must be a number of rupees.")
        value = float(text)
    if isinstance(value, (int, float)):
        number = float(value)
        if number != number or number in (float("inf"), float("-inf")):
            raise ValueError(f"{field} must be a real number.")
        if number != int(number):
            raise ValueError(f"{field} must be in whole rupees (no paise).")
        return int(number)
    raise ValueError(f"{field} must be a number of rupees.")


class BookingDiscountRequest(BaseModel):
    """Set the TOTAL manager discount on a booking (or its whole visit) — an
    amount in whole rupees, or a percentage of the bill; 0 removes it. The
    discount is a target, not an increment: sending the same request twice
    changes nothing the second time."""
    model_config = ConfigDict(extra="forbid")

    amount: Optional[int] = None
    percent: Optional[float] = Field(default=None, ge=0, le=100, allow_inf_nan=False)
    reason: str = Field(min_length=5, max_length=300)
    scope: Literal["booking", "visit"] = "booking"
    idempotency_key: Optional[str] = None
    expected_version: Optional[str] = Field(default=None, max_length=64)
    dry_run: bool = False

    @field_validator("amount", mode="before")
    @classmethod
    def _amount(cls, v):
        return _whole_rupees(v, field="The discount")

    @field_validator("percent", mode="before")
    @classmethod
    def _percent(cls, v):
        if isinstance(v, bool):
            raise ValueError("The percentage must be a number.")
        return v

    @field_validator("reason", mode="before")
    @classmethod
    def _reason(cls, v):
        return clean_text(v, min_len=5, max_len=300, field="The reason")

    @field_validator("idempotency_key")
    @classmethod
    def _idem(cls, v):
        if v is not None and not _KEY.match(v):
            raise ValueError("The idempotency key must be 6–64 letters, digits or - _ . :")
        return v

    @model_validator(mode="after")
    def _one_of(self):
        if (self.amount is None) == (self.percent is None):
            raise ValueError("Send either an amount (rupees) or a percent — exactly one.")
        if self.amount is not None and not (0 <= self.amount <= MAX_RUPEES):
            raise ValueError(f"The discount must be between ₹0 and ₹{MAX_RUPEES:,}.")
        return self


class AdminAddress(BaseModel):
    model_config = ConfigDict(extra="forbid")

    line1: str = Field(min_length=3, max_length=200)
    landmark: Optional[str] = Field(default=None, max_length=120)
    city: Optional[str] = Field(default=None, max_length=80)
    state: Optional[str] = Field(default=None, max_length=80)
    pincode: Optional[str] = Field(default=None, pattern=r"^[0-9]{6}$")
    latitude: float = Field(ge=-90, le=90, allow_inf_nan=False)
    longitude: float = Field(ge=-180, le=180, allow_inf_nan=False)

    @field_validator("line1", "landmark", "city", "state", mode="before")
    @classmethod
    def _text(cls, v, info):
        return clean_text(v, max_len=200, field=info.field_name.title())

    @model_validator(mode="after")
    def _not_null_island(self):
        # A swapped or zeroed pin is the classic data-entry slip; India lies
        # well inside these bounds, so refuse the obviously wrong ones.
        if abs(self.latitude) < 0.0001 and abs(self.longitude) < 0.0001:
            raise ValueError("That map pin (0, 0) isn't a real place.")
        return self


class PaymentAdjustment(BaseModel):
    """A correction to the money RECEIVED, appended beside the payment rows
    (never changing them): `delta` rupees more (+) or less (−) than the
    payments show, by cash or online. `payment_key` names the payment row it
    corrects, when it corrects one."""
    model_config = ConfigDict(extra="forbid")

    delta: float = Field(allow_inf_nan=False)
    method: Literal["cash", "online"]
    payment_key: Optional[str] = Field(default=None, max_length=80)
    # Cash the captain handed to the platform / kept: moves his wallet.
    affects_captain_cash: bool = False
    note: Optional[str] = Field(default=None, max_length=200)

    @field_validator("delta")
    @classmethod
    def _delta(cls, v):
        if abs(v) < 0.005 or abs(v) > MAX_RUPEES:
            raise ValueError(f"A payment correction must be between ₹0.01 and ₹{MAX_RUPEES:,}.")
        return round(v, 2)

    @field_validator("note", mode="before")
    @classmethod
    def _note(cls, v):
        return clean_text(v, max_len=200, field="The note")


class AdminBookingEditRequest(BaseModel):
    """Everything an admin may change on a booking; send only what changes.
    `reason` is required and kept in the immutable edit history."""
    model_config = ConfigDict(extra="forbid")

    reason: str = Field(min_length=5, max_length=300)
    dry_run: bool = False
    expected_version: Optional[str] = Field(default=None, max_length=64)
    idempotency_key: Optional[str] = None

    # -- customer & location
    customer_id: Optional[str] = None
    customer_notes: Optional[str] = Field(default=None, max_length=500)
    admin_notes: Optional[str] = Field(default=None, max_length=500)
    alternate_contact_name: Optional[str] = Field(default=None, max_length=100)
    alternate_contact_phone: Optional[str] = Field(default=None, pattern=r"^[0-9+ \-]{8,20}$")
    address: Optional[AdminAddress] = None
    recompute_center: bool = False
    recompute_travel: bool = False

    # -- services & prices
    service_ids: Optional[list[str]] = Field(default=None, max_length=12)
    service_quantities: Optional[dict[str, int]] = None
    vehicle_type: Optional[str] = Field(default=None, max_length=40)
    remove_coupon: bool = False
    remove_added_services: list[int] = Field(default_factory=list, max_length=10)

    # -- schedule & captain
    scheduled_date: Optional[datetime] = None
    scheduled_slot: Optional[str] = Field(default=None, max_length=20)
    captain_id: Optional[str] = None
    clear_captain: bool = False
    completed_at: Optional[datetime] = None

    # -- money
    manager_discount: Optional[int] = None
    tip_amount: Optional[int] = None
    tip_method: Optional[Literal["cash", "online"]] = None
    payment_adjustments: list[PaymentAdjustment] = Field(default_factory=list, max_length=5)

    # -- status
    status: Optional[Literal[
        "awaiting_payment", "pending", "assigned", "rescheduled", "captain_on_the_way", "service_started", "completed", "cancelled",
    ]] = None
    force_status: bool = False

    @field_validator("reason", mode="before")
    @classmethod
    def _reason(cls, v):
        return clean_text(v, min_len=5, max_len=300, field="The reason")

    @field_validator("customer_notes", "admin_notes", "alternate_contact_name", mode="before")
    @classmethod
    def _notes(cls, v, info):
        return clean_text(v, max_len=500, field=info.field_name.replace("_", " ").title())

    @field_validator("scheduled_date", mode="before")
    @classmethod
    def _day(cls, v):
        return None if v is None else ist_calendar_day(v)

    @field_validator("manager_discount", "tip_amount", mode="before")
    @classmethod
    def _rupees(cls, v, info):
        out = _whole_rupees(v, field=info.field_name.replace("_", " ").title())
        if out is not None and not (0 <= out <= MAX_RUPEES):
            raise ValueError(f"Must be between ₹0 and ₹{MAX_RUPEES:,}.")
        return out

    @field_validator("customer_id", "captain_id")
    @classmethod
    def _oid(cls, v):
        if v is not None and not _OBJECT_ID.match(v):
            raise ValueError("That isn't a valid id.")
        return v

    @field_validator("service_ids")
    @classmethod
    def _service_ids(cls, v):
        if v is None:
            return v
        if not v or any(not _OBJECT_ID.match(i) for i in v) or len(set(v)) != len(v):
            raise ValueError("Pick at least one service (each once).")
        return v

    @field_validator("service_quantities")
    @classmethod
    def _quantities(cls, v):
        if v is None:
            return v
        if len(v) > 12 or any((not _OBJECT_ID.match(k)) or q < 1 or q > 10 for k, q in v.items()):
            raise ValueError("Quantities must be 1–10 per service.")
        return v

    @field_validator("idempotency_key")
    @classmethod
    def _idem(cls, v):
        if v is not None and not _KEY.match(v):
            raise ValueError("The idempotency key must be 6–64 letters, digits or - _ . :")
        return v

    @model_validator(mode="after")
    def _consistent(self):
        if self.captain_id and self.clear_captain:
            raise ValueError("Either pick a captain or clear the captain, not both.")
        if (self.scheduled_date is None) != (self.scheduled_slot is None):
            raise ValueError("Send the date and the slot together.")
        if self.service_quantities is not None and self.service_ids is None:
            raise ValueError("Send service_ids with the quantities.")
        return self

    def changed_fields(self) -> list[str]:
        """The fields this request actually sets (for the audit row)."""
        skip = {"reason", "dry_run", "expected_version", "idempotency_key", "force_status"}
        out = []
        for name in self.model_fields:
            if name in skip:
                continue
            value = getattr(self, name)
            if value in (None, False, [], {}):
                continue
            out.append(name)
        return out
