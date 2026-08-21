"""Geography, restaurants and their provenance."""

from __future__ import annotations

import uuid
from datetime import datetime

from geoalchemy2 import Geography
from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
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
from app.models.enums import SourceType, pg_enum


class City(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "cities"

    name: Mapped[str] = mapped_column(String(120), nullable=False)
    slug: Mapped[str] = mapped_column(String(120), nullable=False, unique=True)
    country: Mapped[str] = mapped_column(String(2), nullable=False, default="IN")
    center: Mapped[object] = mapped_column(
        Geography(geometry_type="POINT", srid=4326), nullable=False
    )
    lat: Mapped[float] = mapped_column(Float, nullable=False)
    lng: Mapped[float] = mapped_column(Float, nullable=False)
    radius_m: Mapped[int] = mapped_column(Integer, nullable=False, default=25000)
    timezone: Mapped[str] = mapped_column(String(64), nullable=False, default="Asia/Kolkata")
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    restaurants: Mapped[list[Restaurant]] = relationship(back_populates="city")

    __table_args__ = (Index("ix_cities_active", "active"),)


class Restaurant(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "restaurants"

    city_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("cities.id", ondelete="RESTRICT"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    slug: Mapped[str] = mapped_column(String(280), nullable=False)
    normalized_name: Mapped[str] = mapped_column(String(255), nullable=False)

    location: Mapped[object] = mapped_column(
        Geography(geometry_type="POINT", srid=4326), nullable=False
    )
    lat: Mapped[float] = mapped_column(Float, nullable=False)
    lng: Mapped[float] = mapped_column(Float, nullable=False)

    address: Mapped[str | None] = mapped_column(Text)
    area: Mapped[str | None] = mapped_column(String(160))
    cuisines: Mapped[list[str]] = mapped_column(ARRAY(String(80)), nullable=False, default=list)
    price_level: Mapped[int | None] = mapped_column(SmallInteger)
    phone: Mapped[str | None] = mapped_column(String(40))
    website: Mapped[str | None] = mapped_column(Text)
    opening_hours: Mapped[str | None] = mapped_column(Text)

    osm_type: Mapped[str | None] = mapped_column(String(12))
    # OSM node ids are beyond 32 bits now; int4 overflows on insert.
    osm_id: Mapped[int | None] = mapped_column(BigInteger)

    is_closed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_verified: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    data_confidence: Mapped[float] = mapped_column(Numeric(4, 3), nullable=False, default=0.5)
    review_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # Google aggregate rating only (ToS: no review text, monthly refresh cadence).
    google_rating: Mapped[float | None] = mapped_column(Float)
    google_rating_count: Mapped[int | None] = mapped_column(Integer)

    first_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_ingested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    city: Mapped[City] = relationship(back_populates="restaurants")
    sources: Mapped[list[RestaurantSource]] = relationship(
        back_populates="restaurant", cascade="all, delete-orphan"
    )
    aliases: Mapped[list[RestaurantAlias]] = relationship(
        back_populates="restaurant", cascade="all, delete-orphan"
    )

    __table_args__ = (
        UniqueConstraint("city_id", "slug", name="uq_restaurants_city_id_slug"),
        CheckConstraint(
            "price_level IS NULL OR (price_level BETWEEN 1 AND 4)",
            name="price_level_range",
        ),
        # GIST index for ST_DWithin distance filtering - created in migration
        Index("ix_restaurants_location", "location", postgresql_using="gist"),
        Index("ix_restaurants_city_id_name", "city_id", "name"),
        Index(
            "ix_restaurants_normalized_name_trgm",
            "normalized_name",
            postgresql_using="gin",
            postgresql_ops={"normalized_name": "gin_trgm_ops"},
        ),
        Index("ix_restaurants_cuisines", "cuisines", postgresql_using="gin"),
        Index(
            "uq_restaurants_osm",
            "osm_type",
            "osm_id",
            unique=True,
            postgresql_where="osm_id IS NOT NULL",
        ),
    )


class RestaurantSource(UUIDMixin, TimestampMixin, Base):
    """Provenance row: the idempotency anchor for ingestion."""

    __tablename__ = "restaurant_sources"

    restaurant_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("restaurants.id", ondelete="CASCADE"), nullable=False
    )
    source: Mapped[SourceType] = mapped_column(
        pg_enum(SourceType, name="source_type"), nullable=False
    )
    external_id: Mapped[str] = mapped_column(String(255), nullable=False)
    url: Mapped[str | None] = mapped_column(Text)
    raw: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    license: Mapped[str | None] = mapped_column(String(120))
    attribution: Mapped[str | None] = mapped_column(String(255))
    fetched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    restaurant: Mapped[Restaurant] = relationship(back_populates="sources")

    __table_args__ = (
        UniqueConstraint("source", "external_id", name="uq_restaurant_sources_source_external_id"),
        Index("ix_restaurant_sources_restaurant_id", "restaurant_id"),
    )


class RestaurantAlias(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "restaurant_aliases"

    restaurant_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("restaurants.id", ondelete="CASCADE"), nullable=False
    )
    alias: Mapped[str] = mapped_column(String(255), nullable=False)
    normalized_alias: Mapped[str] = mapped_column(String(255), nullable=False)
    source: Mapped[SourceType] = mapped_column(
        pg_enum(SourceType, name="source_type"), nullable=False, default=SourceType.MANUAL
    )
    confidence: Mapped[float] = mapped_column(Numeric(4, 3), nullable=False, default=0.8)

    restaurant: Mapped[Restaurant] = relationship(back_populates="aliases")

    __table_args__ = (
        UniqueConstraint(
            "restaurant_id", "normalized_alias", name="uq_restaurant_aliases_restaurant_id_alias"
        ),
        Index(
            "ix_restaurant_aliases_normalized_trgm",
            "normalized_alias",
            postgresql_using="gin",
            postgresql_ops={"normalized_alias": "gin_trgm_ops"},
        ),
    )
