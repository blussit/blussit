from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.models.enums import ComplaintPriority, ComplaintStatus


class ComplaintCreateRequest(BaseModel):
    # Required — a customer picks one of their own existing bookings, never
    # types a free-floating complaint. ComplaintService.create re-verifies
    # this booking actually belongs to the requesting customer.
    booking_id: str = Field(pattern=r"^[0-9a-fA-F]{24}$")
    subject: str = Field(min_length=3, max_length=150)
    description: str = Field(min_length=5, max_length=2000)
    priority: ComplaintPriority = ComplaintPriority.MEDIUM
    # VAL-4: only links to our own uploads — never javascript:/data: or
    # arbitrary sites rendered as links on staff screens.
    attachments: list[str] = Field(default_factory=list, max_length=5)

    @field_validator("attachments")
    @classmethod
    def _own_uploads_only(cls, urls: list[str]) -> list[str]:
        from app.core.storage import is_own_upload_url

        for url in urls:
            if not isinstance(url, str) or len(url) > 500 or not is_own_upload_url(url):
                raise ValueError("Attachments must be photos uploaded through the app")
        return urls


class ComplaintUpdateRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    status: Optional[ComplaintStatus] = None
    priority: Optional[ComplaintPriority] = None
    resolution_note: Optional[str] = Field(None, max_length=2000)


class ComplaintReplyRequest(BaseModel):
    # Stripped first, so a whitespace-only "update" is never an empty
    # bubble on the thread.
    model_config = ConfigDict(str_strip_whitespace=True)

    # Optional when staff only change the STATUS — the thread then gets a
    # short "Status changed to …" line (ComplaintService.add_reply), so a
    # manager isn't forced to type something just to close a ticket.
    message: str = Field(default="", max_length=2000)
    status: Optional[ComplaintStatus] = None

    @model_validator(mode="after")
    def _something_to_post(self) -> "ComplaintReplyRequest":
        if not self.message and self.status is None:
            raise ValueError("Write an update or pick a new status.")
        return self
