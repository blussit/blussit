from typing import Literal, Optional

from pydantic import BaseModel, Field


class KycSubmitRequest(BaseModel):
    """Captain submitting their own KYC packet for review. Fields are
    optional here so a bad one gets a specific message: StaffKycService.submit
    refuses a packet with anything missing (photo, Aadhaar + PAN numbers and
    card photos, address)."""
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
