"""Materialized ranking output, rollups, snapshots and trend metrics."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    SmallInteger,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDMixin
from app.models.enums import ScoreStatus, TrendDirection, TrendSubject, pg_enum


class DishScore(UUIDMixin, TimestampMixin, Base):
    """Score for one (dish, restaurant, city).

    `why` holds structured reason codes, not prose: the explanation is a rendering
    of these numbers, so it can never drift from the score or be model-invented.
    """

    __tablename__ = "dish_scores"

    dish_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("dishes.id", ondelete="CASCADE"), nullable=False
    )
    restaurant_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("restaurants.id", ondelete="CASCADE"), nullable=False
    )
    city_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("cities.id", ondelete="RESTRICT"), nullable=False
    )

    score: Mapped[float | None] = mapped_column(Numeric(6, 3))
    raw_score: Mapped[float | None] = mapped_column(Numeric(6, 5))

    sentiment_component: Mapped[float] = mapped_column(Numeric(5, 4), nullable=False, default=0)
    recency_component: Mapped[float] = mapped_column(Numeric(5, 4), nullable=False, default=0)
    consistency_component: Mapped[float] = mapped_column(Numeric(5, 4), nullable=False, default=0)
    volume_component: Mapped[float] = mapped_column(Numeric(5, 4), nullable=False, default=0)
    source_quality_component: Mapped[float] = mapped_column(
        Numeric(5, 4), nullable=False, default=0
    )
    engagement_component: Mapped[float] = mapped_column(Numeric(5, 4), nullable=False, default=0)
    confidence_component: Mapped[float] = mapped_column(Numeric(5, 4), nullable=False, default=0)

    positive_ratio: Mapped[float] = mapped_column(Numeric(5, 4), nullable=False, default=0)
    # Pre-shrinkage weighted positivity. Persisted because badge assignment happens
    # after a DB round-trip and "hidden gem" is judged on the unshrunk value — the
    # shrunk score deliberately damps exactly the low-volume rows that badge targets.
    observed_positivity: Mapped[float] = mapped_column(Numeric(5, 4), nullable=False, default=0)
    mention_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    evidence_weight: Mapped[float] = mapped_column(Numeric(8, 4), nullable=False, default=0)
    consistency: Mapped[float] = mapped_column(Numeric(5, 4), nullable=False, default=0)
    recency_days: Mapped[float | None] = mapped_column(Numeric(8, 2))
    bayesian_score: Mapped[float | None] = mapped_column(Numeric(5, 4))

    price_avg: Mapped[float | None] = mapped_column(Numeric(10, 2))
    value_score: Mapped[float | None] = mapped_column(Numeric(5, 4))

    is_hidden_gem: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_best_value: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_most_consistent: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    trend: Mapped[TrendDirection | None] = mapped_column(
        pg_enum(TrendDirection, name="trend_direction")
    )
    trend_delta: Mapped[float | None] = mapped_column(Numeric(5, 4))

    why: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    top_attributes: Mapped[list[str]] = mapped_column(
        ARRAY(String(48)), nullable=False, default=list
    )
    status: Mapped[ScoreStatus] = mapped_column(
        pg_enum(ScoreStatus, name="score_status"),
        nullable=False,
        default=ScoreStatus.INSUFFICIENT_DATA,
    )
    weights_version: Mapped[str] = mapped_column(String(32), nullable=False, default="v1")
    computed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        UniqueConstraint(
            "dish_id", "restaurant_id", "city_id", name="uq_dish_scores_dish_restaurant_city"
        ),
        # The hottest query in the product: top restaurants for a dish in a city.
        Index("ix_dish_scores_dish_city_score", "dish_id", "city_id", "score"),
        Index("ix_dish_scores_restaurant_id", "restaurant_id"),
        Index("ix_dish_scores_status", "status"),
        Index("ix_dish_scores_trend", "trend"),
    )


class RestaurantScore(UUIDMixin, TimestampMixin, Base):
    """Restaurant rollup + Food DNA, derived entirely from evidence."""

    __tablename__ = "restaurant_scores"

    restaurant_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("restaurants.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    city_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("cities.id", ondelete="RESTRICT"), nullable=False
    )

    overall_score: Mapped[float | None] = mapped_column(Numeric(6, 3))
    sentiment: Mapped[float] = mapped_column(Numeric(5, 4), nullable=False, default=0)
    consistency: Mapped[float] = mapped_column(Numeric(5, 4), nullable=False, default=0)
    value_score: Mapped[float | None] = mapped_column(Numeric(5, 4))
    price_level: Mapped[int | None] = mapped_column(SmallInteger)

    trend: Mapped[TrendDirection | None] = mapped_column(
        pg_enum(TrendDirection, name="trend_direction")
    )
    trend_delta: Mapped[float | None] = mapped_column(Numeric(5, 4))

    dna: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    top_dish_ids: Mapped[list[uuid.UUID]] = mapped_column(
        ARRAY(PGUUID(as_uuid=True)), nullable=False, default=list
    )
    evidence_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status: Mapped[ScoreStatus] = mapped_column(
        pg_enum(ScoreStatus, name="score_status"),
        nullable=False,
        default=ScoreStatus.INSUFFICIENT_DATA,
    )
    computed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (Index("ix_restaurant_scores_city_score", "city_id", "overall_score"),)


class RankingSnapshot(UUIDMixin, Base):
    """Append-only rank history, so movement can be audited after the fact."""

    __tablename__ = "ranking_snapshots"

    dish_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("dishes.id", ondelete="CASCADE"), nullable=False
    )
    restaurant_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("restaurants.id", ondelete="CASCADE"), nullable=False
    )
    city_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("cities.id", ondelete="RESTRICT"), nullable=False
    )
    score: Mapped[float | None] = mapped_column(Numeric(6, 3))
    rank: Mapped[int | None] = mapped_column(Integer)
    mention_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    weights_version: Mapped[str] = mapped_column(String(32), nullable=False, default="v1")
    taken_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        Index("ix_ranking_snapshots_dish_city_taken", "dish_id", "city_id", "taken_at"),
    )


class TrendMetric(UUIDMixin, TimestampMixin, Base):
    """Recent-vs-historical comparison. `direction` is NULL when data is too thin."""

    __tablename__ = "trend_metrics"

    subject_type: Mapped[TrendSubject] = mapped_column(
        pg_enum(TrendSubject, name="trend_subject"), nullable=False
    )
    dish_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("dishes.id", ondelete="CASCADE")
    )
    restaurant_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("restaurants.id", ondelete="CASCADE")
    )
    city_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("cities.id", ondelete="RESTRICT"), nullable=False
    )

    window_days: Mapped[int] = mapped_column(Integer, nullable=False)
    recent_sentiment: Mapped[float | None] = mapped_column(Numeric(5, 4))
    historical_sentiment: Mapped[float | None] = mapped_column(Numeric(5, 4))
    recent_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    historical_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    delta: Mapped[float | None] = mapped_column(Numeric(5, 4))
    direction: Mapped[TrendDirection | None] = mapped_column(
        pg_enum(TrendDirection, name="trend_direction")
    )
    significant: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    computed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        UniqueConstraint(
            "subject_type",
            "dish_id",
            "restaurant_id",
            "city_id",
            "window_days",
            name="uq_trend_metrics_subject",
        ),
        Index("ix_trend_metrics_city_direction", "city_id", "direction"),
    )
