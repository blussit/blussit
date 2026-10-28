"""Admin input for the finance ledger. Money is integer paise; entry_date is
an IST calendar day ("YYYY-MM-DD")."""
from typing import Literal

from pydantic import BaseModel, Field, field_validator

DATE_PATTERN = r"^\d{4}-\d{2}-\d{2}$"
MAX_AMOUNT_PAISE = 1_00_00_000 * 100  # ₹1 crore


class _Fields(BaseModel):
    @field_validator("title", "done_by", "category", "reference", "description", "reason", mode="before", check_fields=False)
    @classmethod
    def _strip(cls, value):
        return value.strip() if isinstance(value, str) else value

    @field_validator("category", "reference", check_fields=False)
    @classmethod
    def _blank_is_none(cls, value):
        return value or None


class FinanceEntryCreate(_Fields):
    title: str = Field(min_length=1, max_length=120)
    type: Literal["credit", "debit"]
    amount_paise: int = Field(gt=0, le=MAX_AMOUNT_PAISE)
    entry_date: str | None = Field(default=None, pattern=DATE_PATTERN)
    category: str | None = Field(default=None, max_length=50)
    description: str = Field(default="", max_length=500)
    done_by: str = Field(min_length=1, max_length=100)
    reference: str | None = Field(default=None, max_length=80)


class FinanceEntryUpdate(_Fields):
    title: str | None = Field(default=None, min_length=1, max_length=120)
    type: Literal["credit", "debit"] | None = None
    amount_paise: int | None = Field(default=None, gt=0, le=MAX_AMOUNT_PAISE)
    entry_date: str | None = Field(default=None, pattern=DATE_PATTERN)
    category: str | None = Field(default=None, max_length=50)
    description: str | None = Field(default=None, max_length=500)
    done_by: str | None = Field(default=None, min_length=1, max_length=100)
    reference: str | None = Field(default=None, max_length=80)


class FinanceVoidRequest(_Fields):
    reason: str = Field(min_length=3, max_length=200)
