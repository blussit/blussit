"""
Consistent API response envelope used across every endpoint so the
frontend can rely on a single shape for success, pagination, and errors.
"""
from typing import Any, Generic, TypeVar

from pydantic import BaseModel

T = TypeVar("T")


class Meta(BaseModel):
    page: int
    page_size: int
    total: int
    total_pages: int


class SuccessResponse(BaseModel, Generic[T]):
    success: bool = True
    message: str = "Success"
    data: T | None = None


class PaginatedResponse(BaseModel, Generic[T]):
    success: bool = True
    message: str = "Success"
    data: list[T]
    meta: Meta


class ErrorResponse(BaseModel):
    success: bool = False
    error_code: str
    message: str
    details: dict[str, Any] = {}


def build_meta(page: int, page_size: int, total: int) -> Meta:
    total_pages = (total + page_size - 1) // page_size if page_size else 0
    return Meta(page=page, page_size=page_size, total=total, total_pages=max(total_pages, 1) if total else 0)


def success(data: Any = None, message: str = "Success") -> dict:
    return {"success": True, "message": message, "data": data}


def paginated(data: list, page: int, page_size: int, total: int, message: str = "Success") -> dict:
    return {
        "success": True,
        "message": message,
        "data": data,
        "meta": build_meta(page, page_size, total).model_dump(),
    }
