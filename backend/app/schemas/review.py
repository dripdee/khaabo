"""Review, user, bookmark and moderation schemas."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.enums import BookmarkTarget, ModerationReason
from app.utils.text import clean_text


class ReviewCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    restaurant_id: str
    body: str = Field(min_length=20, max_length=5000)
    title: str | None = Field(default=None, max_length=200)
    rating: float | None = Field(default=None, ge=0, le=5)
    dish_hints: list[str] = Field(default_factory=list, max_length=10)

    @field_validator("body", "title")
    @classmethod
    def _strip_markup(cls, value: str | None) -> str | None:
        """Strip markup at the boundary so stored text is always safe to render."""
        if value is None:
            return None
        cleaned = clean_text(value)
        if not cleaned:
            raise ValueError("Text is empty after cleaning")
        return cleaned

    @field_validator("body")
    @classmethod
    def _reject_control_chars(cls, value: str) -> str:
        if any(ord(ch) < 9 for ch in value):
            raise ValueError("Body contains control characters")
        return value


class ReviewAuthor(BaseModel):
    username: str | None = None
    display_name: str | None = None
    avatar_url: str | None = None


class DishMentionOut(BaseModel):
    dish_slug: str
    dish_name: str
    sentiment: float
    snippet: str | None = None
    attributes: list[str] = Field(default_factory=list)
    price_mentioned: float | None = None


class ReviewOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    restaurant_id: str
    restaurant_name: str | None = None
    source: str
    title: str | None = None
    body: str
    rating: float | None = None
    lang: str = "en"
    overall_sentiment: float | None = None
    like_count: int = 0
    liked_by_me: bool = False
    status: str
    published_at: datetime
    author: ReviewAuthor | None = None
    dish_mentions: list[DishMentionOut] = Field(default_factory=list)
    source_url: str | None = None
    attribution: str | None = None


class ReviewCreatedOut(BaseModel):
    """202 response: the review exists but is not visible until moderation clears."""

    id: str
    status: str
    ai_state: str
    moderation: dict
    message: str


class ReviewReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: ModerationReason = ModerationReason.USER_REPORT
    note: str | None = Field(default=None, max_length=500)


class LikeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    review_id: str


class LikeOut(BaseModel):
    review_id: str
    liked: bool
    like_count: int


class BookmarkCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target_type: BookmarkTarget
    dish_id: str | None = None
    restaurant_id: str | None = None
    collection_id: str | None = None
    note: str | None = Field(default=None, max_length=280)


class BookmarkOut(BaseModel):
    id: str
    target_type: str
    dish_id: str | None = None
    restaurant_id: str | None = None
    dish_name: str | None = None
    dish_slug: str | None = None
    restaurant_name: str | None = None
    collection_id: str | None = None
    note: str | None = None
    created_at: datetime


class CollectionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=80)
    description: str | None = Field(default=None, max_length=280)
    is_public: bool = False


class CollectionOut(BaseModel):
    id: str
    name: str
    slug: str
    description: str | None = None
    is_public: bool = False
    bookmark_count: int = 0


class BadgeOut(BaseModel):
    code: str
    label: str
    description: str
    emoji: str
    level: int = 1
    progress: int = 0
    target: int | None = None
    awarded_at: datetime | None = None


class ProfileOut(BaseModel):
    username: str
    display_name: str | None = None
    avatar_url: str | None = None
    bio: str | None = None
    city_slug: str | None = None
    review_count: int = 0
    published_review_count: int = 0
    like_received_count: int = 0
    contribution_score: int = 0
    badges: list[BadgeOut] = Field(default_factory=list)
    favourite_dishes: list[dict] = Field(default_factory=list)
    favourite_restaurants: list[dict] = Field(default_factory=list)
    created_at: datetime | None = None


class ProfileUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    username: str | None = Field(default=None, min_length=3, max_length=40, pattern=r"^[a-z0-9_]+$")
    display_name: str | None = Field(default=None, max_length=80)
    bio: str | None = Field(default=None, max_length=280)
    avatar_url: str | None = Field(default=None, max_length=500)
    city_slug: str | None = None
    favourite_dish_ids: list[str] | None = Field(default=None, max_length=20)
    favourite_restaurant_ids: list[str] | None = Field(default=None, max_length=20)


class MeOut(BaseModel):
    id: str
    email: str | None = None
    role: str
    profile: ProfileOut | None = None


class ModerationItemOut(BaseModel):
    id: str
    review_id: str
    reason: str
    status: str
    severity: int
    review_body: str | None = None
    review_status: str | None = None
    spam_score: float | None = None
    is_duplicate: bool = False
    created_at: datetime
    history: list[dict] = Field(default_factory=list)


class ModerationDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: str = Field(pattern=r"^(publish|reject|flag|dismiss)$")
    note: str | None = Field(default=None, max_length=500)
