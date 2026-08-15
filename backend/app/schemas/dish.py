"""Dish and restaurant response schemas."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.common import AttributeCount, PriceRange, TrendOut, WhyReason


class DishBrief(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    slug: str
    name: str
    cuisine: str | None = None
    category: str
    is_veg: bool | None = None
    hero_image_url: str | None = None


class DishCardOut(DishBrief):
    """Dish as it appears in search results and listings."""

    score: float | None = None
    status: str = "insufficient_data"
    trend: TrendOut | None = None
    mention_count: int = 0
    restaurant_count: int = 0
    price_range: PriceRange | None = None
    top_restaurant_name: str | None = None


class SnippetOut(BaseModel):
    text: str
    sentiment: float
    source: str
    published_at: str | None = None
    review_id: str


class RestaurantBrief(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    slug: str
    area: str | None = None
    lat: float
    lng: float
    cuisines: list[str] = Field(default_factory=list)
    price_level: int | None = None


class DishRestaurantOut(RestaurantBrief):
    """A restaurant ranked *for a specific dish* — the core of the product."""

    score: float | None = None
    status: str = "insufficient_data"
    positive_ratio: float = 0.0
    mention_count: int = 0
    consistency: float = 0.0
    price_avg: float | None = None
    value_score: float | None = None
    trend: TrendOut | None = None
    badges: list[str] = Field(default_factory=list)
    why: list[WhyReason] = Field(default_factory=list)
    top_attributes: list[str] = Field(default_factory=list)
    snippets: list[SnippetOut] = Field(default_factory=list)
    distance_m: float | None = None


class DishHighlights(BaseModel):
    """Named picks. Any of these may be null when the evidence does not support one."""

    top: DishRestaurantOut | None = None
    best_value: DishRestaurantOut | None = None
    hidden_gem: DishRestaurantOut | None = None
    most_consistent: DishRestaurantOut | None = None


class DishSummaryOut(BaseModel):
    text: str
    generated_by: str = "template"
    evidence_review_ids: list[str] = Field(default_factory=list)
    mention_count: int = 0
    positive_ratio: float = 0.0


class RecentSignal(BaseModel):
    period: str
    positive_ratio: float
    mentions: int


class DishDetailOut(BaseModel):
    dish: DishBrief
    city_slug: str
    score: float | None = None
    status: str = "insufficient_data"
    trend: TrendOut | None = None
    mention_count: int = 0
    restaurant_count: int = 0
    price_range: PriceRange | None = None
    positive_attributes: list[AttributeCount] = Field(default_factory=list)
    negative_attributes: list[AttributeCount] = Field(default_factory=list)
    summary: DishSummaryOut | None = None
    highlights: DishHighlights = Field(default_factory=DishHighlights)
    recent_signals: list[RecentSignal] = Field(default_factory=list)
    attribution: list[str] = Field(default_factory=list)


class MapMarkerOut(BaseModel):
    """Deliberately minimal: the map must not download review text."""

    id: str
    name: str
    lat: float
    lng: float
    score: float | None = None
    status: str = "insufficient_data"
    price_avg: float | None = None
    trend: str | None = None
    badges: list[str] = Field(default_factory=list)


class DishMapOut(BaseModel):
    dish: DishBrief
    city_slug: str
    markers: list[MapMarkerOut]
    bounds: dict | None = None
    attribution: list[str] = Field(default_factory=list)


class DnaChipOut(BaseModel):
    code: str
    label: str
    emoji: str
    group: str
    value: float | None = None


class FoodDnaOut(BaseModel):
    restaurant_id: str
    chips: list[DnaChipOut] = Field(default_factory=list)
    overall_score: float | None = None
    sentiment: float = 0.0
    consistency: float = 0.0
    value_score: float | None = None
    trend: TrendOut | None = None
    evidence_count: int = 0
    status: str = "insufficient_data"


class RestaurantDishOut(BaseModel):
    dish: DishBrief
    score: float | None = None
    status: str = "insufficient_data"
    mention_count: int = 0
    positive_ratio: float = 0.0
    price_avg: float | None = None
    is_signature: bool = False
    trend: TrendOut | None = None
    why: list[WhyReason] = Field(default_factory=list)


class RestaurantDetailOut(RestaurantBrief):
    address: str | None = None
    phone: str | None = None
    website: str | None = None
    opening_hours: str | None = None
    is_closed: bool = False
    is_verified: bool = False
    review_count: int = 0
    city_slug: str
    food_dna: FoodDnaOut | None = None
    top_dishes: list[RestaurantDishOut] = Field(default_factory=list)
    attribution: list[str] = Field(default_factory=list)


class TrendingItemOut(BaseModel):
    kind: str
    dish: DishBrief | None = None
    restaurant: RestaurantBrief | None = None
    direction: str
    delta: float | None = None
    recent_count: int = 0
    significant: bool = False
    score: float | None = None
