from typing import Literal, Optional

from pydantic import BaseModel, Field


class KycSubmitRequest(BaseModel):
    """Captain submitting/updating their own KYC packet. Everything is
    optional so a captain can fill it in over multiple sessions; the
    completeness check lives in the UI, the manager judges the packet."""
    photo_url: Optional[str] = None
    aadhaar_number: Optional[str] = Field(default=None, pattern=r"^\d{12}$")
    pan_number: Optional[str] = Field(default=None, pattern=r"^[A-Za-z]{5}\d{4}[A-Za-z]$")
    aadhaar_doc_url: Optional[str] = None
    pan_doc_url: Optional[str] = None
    local_address: Optional[str] = Field(default=None, max_length=500)
    permanent_address: Optional[str] = Field(default=None, max_length=500)
    same_as_local: bool = False


class KycReviewRequest(BaseModel):
    status: Literal["verified", "rejected"]
    note: Optional[str] = Field(default=None, max_length=500)
