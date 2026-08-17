"""Canonical dishes, their aliases, and the dish↔restaurant bridge."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    Computed,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import TSVECTOR
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDMixin
from app.models.enums import DishCategory, pg_enum


class Dish(UUIDMixin, TimestampMixin, Base):
    """A dish concept, deliberately city-agnostic.

    `is_veg` is nullable because dishes like `momo` exist in both forms; a null
    means "depends on the variant" rather than unknown-and-broken.
    """

    __tablename__ = "dishes"

    name: Mapped[str] = mapped_column(String(160), nullable=False)
    slug: Mapped[str] = mapped_column(String(180), nullable=False, unique=True)
    normalized_name: Mapped[str] = mapped_column(String(160), nullable=False)
    cuisine: Mapped[str | None] = mapped_column(String(80))
    category: Mapped[DishCategory] = mapped_column(
        pg_enum(DishCategory, name="dish_category"), nullable=False, default=DishCategory.OTHER
    )
    is_veg: Mapped[bool | None] = mapped_column(Boolean)
    description: Mapped[str | None] = mapped_column(Text)
    hero_image_url: Mapped[str | None] = mapped_column(Text)
    mention_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    restaurant_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    search_vector: Mapped[str | None] = mapped_column(
        TSVECTOR,
        Computed(
            "to_tsvector('simple', coalesce(name,'') || ' ' || coalesce(normalized_name,'')"
            " || ' ' || coalesce(cuisine,'') || ' ' || coalesce(description,''))",
            persisted=True,
        ),
    )

    aliases: Mapped[list[DishAlias]] = relationship(
        back_populates="dish", cascade="all, delete-orphan"
    )

    __table_args__ = (
        Index("ix_dishes_search_vector", "search_vector", postgresql_using="gin"),
        Index(
            "ix_dishes_normalized_name_trgm",
            "normalized_name",
            postgresql_using="gin",
            postgresql_ops={"normalized_name": "gin_trgm_ops"},
        ),
        Index("ix_dishes_category", "category"),
    )


class DishAlias(UUIDMixin, TimestampMixin, Base):
    """Drives deterministic dish extraction, including transliterations.

    Unique on (normalized_alias, lang): one surface form maps to exactly one dish
    per language, otherwise extraction would be ambiguous by construction.
    """

    __tablename__ = "dish_aliases"

    dish_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("dishes.id", ondelete="CASCADE"), nullable=False
    )
    alias: Mapped[str] = mapped_column(String(160), nullable=False)
    normalized_alias: Mapped[str] = mapped_column(String(160), nullable=False)
    lang: Mapped[str] = mapped_column(String(8), nullable=False, default="en")
    weight: Mapped[float] = mapped_column(Numeric(4, 3), nullable=False, default=1.0)

    dish: Mapped[Dish] = relationship(back_populates="aliases")

    __table_args__ = (
        UniqueConstraint("normalized_alias", "lang", name="uq_dish_aliases_normalized_alias_lang"),
        Index("ix_dish_aliases_dish_id", "dish_id"),
        Index(
            "ix_dish_aliases_normalized_trgm",
            "normalized_alias",
            postgresql_using="gin",
            postgresql_ops={"normalized_alias": "gin_trgm_ops"},
        ),
    )


class RestaurantDish(UUIDMixin, TimestampMixin, Base):
    """Aggregated evidence for one (restaurant, dish) pair — the dish-first bridge."""

    __tablename__ = "restaurant_dishes"

    restaurant_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("restaurants.id", ondelete="CASCADE"), nullable=False
    )
    dish_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("dishes.id", ondelete="CASCADE"), nullable=False
    )

    mention_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    positive_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    negative_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    neutral_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    price_min: Mapped[float | None] = mapped_column(Numeric(10, 2))
    price_max: Mapped[float | None] = mapped_column(Numeric(10, 2))
    price_avg: Mapped[float | None] = mapped_column(Numeric(10, 2))
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="INR")

    is_signature: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    first_mentioned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_mentioned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        UniqueConstraint(
            "restaurant_id", "dish_id", name="uq_restaurant_dishes_restaurant_id_dish_id"
        ),
        Index("ix_restaurant_dishes_dish_id", "dish_id"),
    )
