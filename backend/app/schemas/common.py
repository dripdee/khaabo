"""Shared response primitives."""

from __future__ import annotations

from typing import Generic, TypeVar

from pydantic import BaseModel, ConfigDict, Field

T = TypeVar("T")


class Page(BaseModel, Generic[T]):
    items: list[T]
    page: int = 1
    page_size: int = 20
    total: int = 0
    has_more: bool = False


class PaginationParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    page: int = Field(default=1, ge=1, le=500)
    page_size: int = Field(default=20, ge=1, le=100)

    @property
    def offset(self) -> int:
        return (self.page - 1) * self.page_size


class WhyReason(BaseModel):
    """Server-composed explanation fragment.

    The frontend renders these labels verbatim and never invents its own, so the
    explanation always matches the stored score.
    """

    code: str
    label: str
    value: float | int | None = None


class TrendOut(BaseModel):
    direction: str | None = None
    delta: float | None = None
    significant: bool = False


class PriceRange(BaseModel):
    min: float | None = None
    max: float | None = None
    avg: float | None = None
    currency: str = "INR"


class AttributeCount(BaseModel):
    label: str
    count: int


class CityOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    slug: str
    name: str
    country: str
    lat: float
    lng: float
    timezone: str


class ErrorBody(BaseModel):
    code: str
    message: str
    details: dict | None = None


class ErrorResponse(BaseModel):
    error: ErrorBody


class HealthComponent(BaseModel):
    name: str
    ok: bool
    detail: str | None = None


class HealthOut(BaseModel):
    status: str
    version: str
    components: list[HealthComponent]
