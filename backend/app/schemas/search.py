"""Search request/response schemas."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.schemas.dish import DishCardOut, DishRestaurantOut

SortOption = Literal["score", "distance", "trending", "price", "relevance"]


class SearchFilters(BaseModel):
    """Explicit filters. These always override anything parsed from `q`."""

    model_config = ConfigDict(extra="forbid")

    q: str | None = Field(default=None, max_length=200)
    city: str | None = Field(default=None, max_length=80)
    dish: str | None = Field(default=None, max_length=120)
    cuisine: str | None = Field(default=None, max_length=80)
    area: str | None = Field(default=None, max_length=120)
    lat: float | None = Field(default=None, ge=-90, le=90)
    lng: float | None = Field(default=None, ge=-180, le=180)
    radius_m: int | None = Field(default=None, ge=100, le=50000)
    min_price: float | None = Field(default=None, ge=0, le=100000)
    max_price: float | None = Field(default=None, ge=0, le=100000)
    dietary: str | None = Field(default=None, max_length=40)
    mood: str | None = Field(default=None, max_length=40)
    trend: str | None = Field(default=None, max_length=20)
    sort: SortOption = "score"
    page: int = Field(default=1, ge=1, le=200)
    page_size: int = Field(default=20, ge=1, le=50)

    @model_validator(mode="after")
    def _check(self) -> SearchFilters:
        if (
            self.min_price is not None
            and self.max_price is not None
            and self.min_price > self.max_price
        ):
            raise ValueError("min_price cannot exceed max_price")
        if self.sort == "distance" and (self.lat is None or self.lng is None):
            raise ValueError("sort=distance requires lat and lng")
        if (self.lat is None) != (self.lng is None):
            raise ValueError("lat and lng must be provided together")
        return self


class ParsedQueryOut(BaseModel):
    raw: str
    dish_terms: list[str] = Field(default_factory=list)
    cuisine: str | None = None
    area: str | None = None
    min_price: float | None = None
    max_price: float | None = None
    dietary: str | None = None
    mood: str | None = None
    near_me: bool = False
    superlative: bool = False
    price_band: str | None = None
    intent: str = "dish"


class SearchResponse(BaseModel):
    intent: str
    parsed: ParsedQueryOut | None = None
    dishes: list[DishCardOut] = Field(default_factory=list)
    restaurants: list[DishRestaurantOut] = Field(default_factory=list)
    page: int = 1
    page_size: int = 20
    total: int = 0
    has_more: bool = False
    city_slug: str | None = None
    attribution: list[str] = Field(default_factory=list)


class SuggestItem(BaseModel):
    kind: Literal["dish", "restaurant", "area", "cuisine"]
    label: str
    slug: str | None = None
    id: str | None = None
    subtitle: str | None = None


class SuggestResponse(BaseModel):
    items: list[SuggestItem] = Field(default_factory=list)
