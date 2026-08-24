"""
Shared base classes for domain models stored in MongoDB. Every business
record includes created/updated timestamps, createdBy/updatedBy, and
soft-delete support as required by the PRD.
"""
from datetime import datetime, timezone
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

from app.utils.object_id import PyObjectId


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class MongoBaseModel(BaseModel):
    """Base for models that map 1:1 to a Mongo document."""

    model_config = ConfigDict(populate_by_name=True, arbitrary_types_allowed=True)

    id: Optional[PyObjectId] = Field(default=None, alias="_id")


class TimestampMixin(BaseModel):
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class AuditMixin(BaseModel):
    created_by: Optional[str] = None
    updated_by: Optional[str] = None


class SoftDeleteMixin(BaseModel):
    is_deleted: bool = False
    deleted_at: Optional[datetime] = None


class BusinessRecordBase(MongoBaseModel, TimestampMixin, AuditMixin, SoftDeleteMixin):
    """
    Standard base for all business collections (bookings, services,
    complaints, etc.) — bundles timestamps, audit fields, and soft delete.
    """
    pass
