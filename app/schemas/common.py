from typing import Any

from pydantic import BaseModel, Field


class ErrorDetail(BaseModel):
    code: str = Field(description="Stable machine-readable error code.", examples=["NOT_FOUND"])
    message: str = Field(description="Human-readable explanation, safe to display.")
    details: dict[str, Any] = Field(default_factory=dict)


class ErrorResponse(BaseModel):
    error: ErrorDetail


class Pagination(BaseModel):
    total: int = Field(description="Total number of items available.")
    limit: int
    offset: int
