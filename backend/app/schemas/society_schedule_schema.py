"""Request shapes for society premium-wash scheduling — docs/SOCIETY_PLANS.md §9."""
from datetime import date
from typing import Annotated, Literal, Optional

from pydantic import BaseModel, Field, field_validator, model_validator

DateStr = Annotated[str, Field(pattern=r"^\d{4}-\d{2}-\d{2}$")]
SlotKey = Annotated[str, Field(pattern=r"^\d{2}:\d{2}-\d{2}:\d{2}$")]
Id = Annotated[str, Field(min_length=1, max_length=64)]


def _valid_date(v: Optional[str]) -> Optional[str]:
    if v is None:
        return None
    try:
        date.fromisoformat(v)
    except ValueError as exc:
        raise ValueError("Pick a valid date") from exc
    return v


class RulePattern(BaseModel):
    """weekly: every <weekday>; monthly_nth: the <weeks> (1-5) <weekday>s of
    each month ("1st & 3rd Saturday"); every_n_weeks: every <interval_weeks>
    weeks from <anchor_date> (a rotation)."""

    kind: Literal["weekly", "monthly_nth", "every_n_weeks"] = "weekly"
    weekday: int = Field(ge=0, le=6)  # Monday = 0 … Sunday = 6
    weeks: list[int] = Field(default_factory=list, max_length=5)
    interval_weeks: int = Field(default=1, ge=1, le=8)
    anchor_date: Optional[DateStr] = None

    _d = field_validator("anchor_date")(_valid_date)

    @model_validator(mode="after")
    def _shape(self) -> "RulePattern":
        if self.kind == "monthly_nth":
            if not self.weeks or any(w < 1 or w > 5 for w in self.weeks):
                raise ValueError("Pick which weeks of the month (1st to 5th)")
            self.weeks = sorted(set(self.weeks))
        if self.kind == "every_n_weeks":
            if not self.anchor_date:
                raise ValueError("Pick the first date of the rotation")
            if date.fromisoformat(self.anchor_date).weekday() != self.weekday:
                raise ValueError("The first date must fall on the chosen day")
        return self


class SocietyRuleRequest(BaseModel):
    """A society's repeat visit day: which days, which slots, who goes and
    how many premium washes each captain does that day."""

    pattern: RulePattern
    slot_keys: list[SlotKey] = Field(min_length=1, max_length=6)
    captain_ids: list[Id] = Field(default_factory=list, max_length=6)
    washes_per_captain: int = Field(default=6, ge=1, le=20)
    start_date: Optional[DateStr] = None
    end_date: Optional[DateStr] = None
    notes: Optional[str] = Field(default=None, max_length=300)
    is_active: bool = True

    _d = field_validator("start_date", "end_date")(_valid_date)

    @model_validator(mode="after")
    def _dates(self) -> "SocietyRuleRequest":
        if len(set(self.captain_ids)) != len(self.captain_ids):
            raise ValueError("Each captain can be picked once")
        if self.start_date and self.end_date and self.end_date < self.start_date:
            raise ValueError("The end date must be after the start date")
        return self


class ResidentRuleRequest(BaseModel):
    """One resident's own repeat premium wash (e.g. every Saturday, 8–11)."""

    enrollment_id: Id
    subscription_ids: list[Id] = Field(min_length=1, max_length=10)
    pattern: RulePattern
    slot_key: SlotKey
    captain_id: Optional[Id] = None
    start_date: Optional[DateStr] = None
    end_date: Optional[DateStr] = None
    notes: Optional[str] = Field(default=None, max_length=300)
    is_active: bool = True

    _d = field_validator("start_date", "end_date")(_valid_date)

    @model_validator(mode="after")
    def _dates(self) -> "ResidentRuleRequest":
        if self.start_date and self.end_date and self.end_date < self.start_date:
            raise ValueError("The end date must be after the start date")
        return self


class RotationRequest(BaseModel):
    """Spread these societies (in this order) over the chosen days of the
    week, one society per day, repeating every round."""

    center_id: Optional[Id] = None  # admin only
    society_ids: list[Id] = Field(min_length=1, max_length=40)
    weekdays: list[int] = Field(min_length=1, max_length=7)
    start_date: DateStr
    slot_keys: list[SlotKey] = Field(min_length=1, max_length=6)
    captain_ids: list[Id] = Field(default_factory=list, max_length=6)
    washes_per_captain: int = Field(default=6, ge=1, le=20)
    dry_run: bool = False

    _d = field_validator("start_date")(_valid_date)

    @model_validator(mode="after")
    def _check(self) -> "RotationRequest":
        if any(d < 0 or d > 6 for d in self.weekdays):
            raise ValueError("Pick days of the week")
        if len(set(self.society_ids)) != len(self.society_ids):
            raise ValueError("Each society can be picked once")
        return self


class VisitCreateRequest(BaseModel):
    """A one-off society visit day."""

    date: DateStr
    slot_keys: list[SlotKey] = Field(min_length=1, max_length=6)
    captain_ids: list[Id] = Field(default_factory=list, max_length=6)
    washes_per_captain: int = Field(default=6, ge=1, le=20)
    notes: Optional[str] = Field(default=None, max_length=300)

    _d = field_validator("date")(_valid_date)


class VisitUpdateRequest(BaseModel):
    """Change ONE occurrence: move it, change its captains / slots / washes
    a captain does. Anything left out stays as it is."""

    date: Optional[DateStr] = None
    slot_keys: Optional[list[SlotKey]] = Field(default=None, min_length=1, max_length=6)
    captain_ids: Optional[list[Id]] = Field(default=None, max_length=6)
    washes_per_captain: Optional[int] = Field(default=None, ge=1, le=20)
    notes: Optional[str] = Field(default=None, max_length=300)

    _d = field_validator("date")(_valid_date)


class ExcludeRequest(BaseModel):
    """Take cars off (or back onto) one visit day."""

    subscription_ids: list[Id] = Field(min_length=1, max_length=20)
    excluded: bool = True


class ScheduleSettingsRequest(BaseModel):
    generate_days_ahead: int = Field(ge=1, le=5)
    default_washes_per_captain: int = Field(ge=1, le=20)


class ChangeRequestCreate(BaseModel):
    """A resident: skip, or move, one scheduled premium wash."""

    visit_id: Id
    kind: Literal["skip", "move"]
    preferred_date: Optional[DateStr] = None
    preferred_slot: Optional[SlotKey] = None
    note: Optional[str] = Field(default=None, max_length=300)

    _d = field_validator("preferred_date")(_valid_date)

    @model_validator(mode="after")
    def _move(self) -> "ChangeRequestCreate":
        if self.kind == "move" and not self.preferred_date:
            raise ValueError("Pick the date you'd like instead")
        return self


class ChangeRequestResolve(BaseModel):
    """Staff approving (optionally choosing the new date / slot / captain
    for a move) or declining a resident's request."""

    note: Optional[str] = Field(default=None, max_length=300)
    date: Optional[DateStr] = None
    slot_key: Optional[SlotKey] = None
    captain_id: Optional[Id] = None

    _d = field_validator("date")(_valid_date)
