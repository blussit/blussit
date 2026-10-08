from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator

from app.core.storage import MAX_STORED_URL_LENGTH, is_own_upload_url


class KycSubmitRequest(BaseModel):
    """Captain submitting their own KYC packet for review. Fields are
    optional here so a bad one gets a specific message: StaffKycService.submit
    refuses a packet with anything missing (photo, Aadhaar + PAN numbers and
    card photos, address)."""
    photo_url: Optional[str] = Field(default=None, max_length=MAX_STORED_URL_LENGTH)
    aadhaar_number: Optional[str] = Field(default=None, pattern=r"^\d{12}$")
    pan_number: Optional[str] = Field(default=None, pattern=r"^[A-Za-z]{5}\d{4}[A-Za-z]$")
    # Must be URLs our own upload endpoints returned; the document ones are
    # further checked against the caller's own uploads in StaffKycService.
    aadhaar_doc_url: Optional[str] = Field(default=None, max_length=MAX_STORED_URL_LENGTH)
    pan_doc_url: Optional[str] = Field(default=None, max_length=MAX_STORED_URL_LENGTH)
    local_address: Optional[str] = Field(default=None, max_length=500)
    permanent_address: Optional[str] = Field(default=None, max_length=500)
    same_as_local: bool = False

    @field_validator("photo_url", "aadhaar_doc_url", "pan_doc_url")
    @classmethod
    def _uploaded_by_us(cls, v: Optional[str]) -> Optional[str]:
        if v and not is_own_upload_url(v):
            raise ValueError("Please upload this file again from your profile.")
        return v


class KycReviewRequest(BaseModel):
    status: Literal["verified", "rejected"]
    note: Optional[str] = Field(default=None, max_length=500)
