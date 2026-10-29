"""The rule fields a coupon / bundle carries (see services/coupon_rules.py),
as request mixins shared by the coupon and bundle schemas."""
from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator, model_validator

from app.services.coupon_rules import CUSTOM_COUPONS_ENABLED, clean_phones, is_hhmm

Use = Literal["services", "plans", "society", "custom"]
_IDS = Field(default=None, max_length=200)


def _ids(values: list[str] | None) -> list[str] | None:
    if values is None:
        return None
    out: list[str] = []
    for v in values:
        text = str(v).strip()
        if len(text) > 64:
            raise ValueError("An id in the list is too long")
        if text and text not in out:
            out.append(text)
    return out


class _RuleChecks(BaseModel):
    @field_validator("applies_to", "applicable_service_ids", "applicable_vehicle_types", "applicable_center_ids", check_fields=False)
    @classmethod
    def _dedupe(cls, v):
        return _ids(v) if v is not None else v

    @field_validator("applies_to", check_fields=False)
    @classmethod
    def _custom_not_yet(cls, v):
        if v and "custom" in v and not CUSTOM_COUPONS_ENABLED:
            raise ValueError("Custom plan carts don't take coupons yet")
        return v

    @field_validator("allowed_customer_phones", check_fields=False)
    @classmethod
    def _phones(cls, v):
        return clean_phones(v) if v is not None else v

    @field_validator("allowed_weekdays", check_fields=False)
    @classmethod
    def _days(cls, v):
        if v is None:
            return v
        if any(not isinstance(d, int) or d < 0 or d > 6 for d in v):
            raise ValueError("Weekdays are 0 (Monday) to 6 (Sunday)")
        return sorted(set(v))

    @model_validator(mode="after")
    def _window(self):
        start, end = getattr(self, "time_window_start", None), getattr(self, "time_window_end", None)
        if (start or end) and not (is_hhmm(start) and is_hhmm(end)):
            raise ValueError("Give both a start and an end time (HH:MM) for the time window")
        if start and end and start >= end:
            raise ValueError("The time window must end after it starts")
        return self


class RulesCreate(_RuleChecks):
    """Create: unspecified = the safe default (bookings only, no restriction)."""

    applies_to: list[Use] = Field(default_factory=lambda: ["services"], min_length=1, max_length=4)
    applicable_service_ids: list[str] = Field(default_factory=list, max_length=200)
    applicable_vehicle_types: list[str] = Field(default_factory=list, max_length=200)
    applicable_center_ids: list[str] = Field(default_factory=list, max_length=200)
    first_booking_only: bool = False
    new_customers_only: bool = False
    allowed_customer_phones: list[str] = Field(default_factory=list, max_length=5000)
    allowed_weekdays: list[int] = Field(default_factory=list, max_length=7)
    time_window_start: Optional[str] = None
    time_window_end: Optional[str] = None
    stackable_with_offers: bool = False
    combine_with_plan: bool = False


class RulesUpdate(_RuleChecks):
    """Update: only what is sent changes; lists sent empty clear the restriction."""

    applies_to: Optional[list[Use]] = Field(default=None, min_length=1, max_length=4)
    applicable_service_ids: Optional[list[str]] = Field(default=None, max_length=200)
    applicable_vehicle_types: Optional[list[str]] = Field(default=None, max_length=200)
    applicable_center_ids: Optional[list[str]] = Field(default=None, max_length=200)
    first_booking_only: Optional[bool] = None
    new_customers_only: Optional[bool] = None
    allowed_customer_phones: Optional[list[str]] = Field(default=None, max_length=5000)
    allowed_weekdays: Optional[list[int]] = Field(default=None, max_length=7)
    time_window_start: Optional[str] = None
    time_window_end: Optional[str] = None
    stackable_with_offers: Optional[bool] = None
    combine_with_plan: Optional[bool] = None
