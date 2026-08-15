"""Reviews and the dish-level observations extracted from them."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDMixin
from app.models.enums import (
    AIState,
    AspectType,
    ExtractionMethod,
    ReviewStatus,
    SourceType,
    ValueSignal,
)


class Review(UUIDMixin, TimestampMixin, Base):
    """One text item of evidence, from any source.

    `content_hash` is UNIQUE: exact duplicates are rejected at write time rather
    than filtered later. `simhash` supports near-duplicate candidate lookup.
    """

    __tablename__ = "reviews"

    restaurant_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("restaurants.id", ondelete="CASCADE"), nullable=False
    )
    city_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("cities.id", ondelete="RESTRICT"), nullable=False
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )

    source: Mapped[SourceType] = mapped_column(Enum(SourceType, name="source_type"), nullable=False)
    lang: Mapped[str] = mapped_column(String(8), nullable=False, default="en")
    title: Mapped[str | None] = mapped_column(String(300))
    body: Mapped[str] = mapped_column(Text, nullable=False)

    rating: Mapped[float | None] = mapped_column(Numeric(4, 2))
    rating_scale: Mapped[int | None] = mapped_column(SmallInteger)

    author_external: Mapped[str | None] = mapped_column(String(160))
    engagement_score: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    source_quality: Mapped[float] = mapped_column(Numeric(4, 3), nullable=False, default=0.5)

    published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ingested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    content_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    simhash: Mapped[int | None] = mapped_column(BigInteger)

    status: Mapped[ReviewStatus] = mapped_column(
        Enum(ReviewStatus, name="review_status"), nullable=False, default=ReviewStatus.PENDING
    )
    ai_state: Mapped[AIState] = mapped_column(
        Enum(AIState, name="ai_state"), nullable=False, default=AIState.PENDING
    )
    ai_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    overall_sentiment: Mapped[float | None] = mapped_column(Numeric(4, 3))
    value_signal: Mapped[ValueSignal] = mapped_column(
        Enum(ValueSignal, name="value_signal"), nullable=False, default=ValueSignal.UNKNOWN
    )
    spam_score: Mapped[float] = mapped_column(Numeric(4, 3), nullable=False, default=0.0)
    is_duplicate: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    like_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    report_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    mentions: Mapped[list[ReviewDishMention]] = relationship(
        back_populates="review", cascade="all, delete-orphan"
    )
    sources: Mapped[list[ReviewSource]] = relationship(
        back_populates="review", cascade="all, delete-orphan"
    )

    __table_args__ = (
        CheckConstraint("char_length(body) >= 1", name="body_not_empty"),
        CheckConstraint("rating IS NULL OR rating_scale IS NOT NULL", name="rating_requires_scale"),
        Index("ix_reviews_restaurant_id_published_at", "restaurant_id", "published_at"),
        Index("ix_reviews_status_ai_state", "status", "ai_state"),
        Index("ix_reviews_city_id_published_at", "city_id", "published_at"),
        Index("ix_reviews_simhash", "simhash"),
        Index("ix_reviews_user_id", "user_id"),
    )


class ReviewSource(UUIDMixin, TimestampMixin, Base):
    """Where a review came from, with licence/attribution kept for the UI."""

    __tablename__ = "review_sources"

    review_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("reviews.id", ondelete="CASCADE"), nullable=False
    )
    source: Mapped[SourceType] = mapped_column(Enum(SourceType, name="source_type"), nullable=False)
    external_id: Mapped[str] = mapped_column(String(255), nullable=False)
    url: Mapped[str | None] = mapped_column(Text)
    permalink: Mapped[str | None] = mapped_column(Text)
    raw: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    license: Mapped[str | None] = mapped_column(String(120))
    attribution: Mapped[str | None] = mapped_column(String(255))

    review: Mapped[Review] = relationship(back_populates="sources")

    __table_args__ = (
        UniqueConstraint("source", "external_id", name="uq_review_sources_source_external_id"),
        Index("ix_review_sources_review_id", "review_id"),
    )


class ReviewDishMention(UUIDMixin, TimestampMixin, Base):
    """The core observation: one dish, discussed in one review.

    A review mentioning two dishes produces two rows with independent sentiment.
    UNIQUE(review_id, dish_id) means a review can never double-count one dish.
    """

    __tablename__ = "review_dish_mentions"

    review_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("reviews.id", ondelete="CASCADE"), nullable=False
    )
    dish_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("dishes.id", ondelete="CASCADE"), nullable=False
    )
    restaurant_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("restaurants.id", ondelete="CASCADE"), nullable=False
    )

    snippet: Mapped[str | None] = mapped_column(String(320))
    sentiment: Mapped[float] = mapped_column(Numeric(4, 3), nullable=False)
    confidence: Mapped[float] = mapped_column(Numeric(4, 3), nullable=False, default=0.5)
    attributes: Mapped[list[str]] = mapped_column(ARRAY(String(48)), nullable=False, default=list)
    price_mentioned: Mapped[float | None] = mapped_column(Numeric(10, 2))
    is_recommended: Mapped[bool | None] = mapped_column(Boolean)
    extraction_method: Mapped[ExtractionMethod] = mapped_column(
        Enum(ExtractionMethod, name="extraction_method"),
        nullable=False,
        default=ExtractionMethod.ALIAS,
    )

    review: Mapped[Review] = relationship(back_populates="mentions")
    aspects: Mapped[list[ReviewAspect]] = relationship(
        back_populates="dish_mention", cascade="all, delete-orphan"
    )

    __table_args__ = (
        UniqueConstraint("review_id", "dish_id", name="uq_review_dish_mentions_review_id_dish_id"),
        CheckConstraint("sentiment BETWEEN -1 AND 1", name="sentiment_range"),
        CheckConstraint("confidence BETWEEN 0 AND 1", name="confidence_range"),
        Index("ix_review_dish_mentions_dish_id_restaurant_id", "dish_id", "restaurant_id"),
        Index("ix_review_dish_mentions_restaurant_id_dish_id", "restaurant_id", "dish_id"),
    )


class ReviewAspect(UUIDMixin, TimestampMixin, Base):
    """Aspect-level sentiment, optionally scoped to a specific dish mention."""

    __tablename__ = "review_aspects"

    review_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("reviews.id", ondelete="CASCADE"), nullable=False
    )
    dish_mention_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("review_dish_mentions.id", ondelete="CASCADE")
    )
    aspect: Mapped[AspectType] = mapped_column(Enum(AspectType, name="aspect_type"), nullable=False)
    sentiment: Mapped[float] = mapped_column(Numeric(4, 3), nullable=False)
    confidence: Mapped[float] = mapped_column(Numeric(4, 3), nullable=False, default=0.5)
    snippet: Mapped[str | None] = mapped_column(String(320))

    dish_mention: Mapped[ReviewDishMention | None] = relationship(back_populates="aspects")

    __table_args__ = (
        CheckConstraint("sentiment BETWEEN -1 AND 1", name="aspect_sentiment_range"),
        Index("ix_review_aspects_review_id", "review_id"),
        Index("ix_review_aspects_aspect", "aspect"),
    )
