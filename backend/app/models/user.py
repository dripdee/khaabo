"""Users, profiles, bookmarks, likes and lightweight gamification."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDMixin
from app.models.enums import BookmarkTarget, UserRole


class User(TimestampMixin, Base):
    """`id` is the Supabase `sub`, so no mapping table is required."""

    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    email: Mapped[str | None] = mapped_column(String(320), unique=True)
    role: Mapped[UserRole] = mapped_column(
        Enum(UserRole, name="user_role"), nullable=False, default=UserRole.USER
    )
    is_banned: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    profile: Mapped[Profile | None] = relationship(
        back_populates="user", cascade="all, delete-orphan", uselist=False
    )

    __table_args__ = (Index("ix_users_role", "role"),)


class Profile(TimestampMixin, Base):
    __tablename__ = "profiles"

    user_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    username: Mapped[str] = mapped_column(String(40), nullable=False, unique=True)
    display_name: Mapped[str | None] = mapped_column(String(80))
    avatar_url: Mapped[str | None] = mapped_column(Text)
    bio: Mapped[str | None] = mapped_column(String(280))
    city_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("cities.id", ondelete="SET NULL")
    )

    review_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    published_review_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    like_received_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    contribution_score: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    favourite_dish_ids: Mapped[list[uuid.UUID]] = mapped_column(
        ARRAY(PGUUID(as_uuid=True)), nullable=False, default=list
    )
    favourite_restaurant_ids: Mapped[list[uuid.UUID]] = mapped_column(
        ARRAY(PGUUID(as_uuid=True)), nullable=False, default=list
    )

    user: Mapped[User] = relationship(back_populates="profile")

    __table_args__ = (CheckConstraint("username ~ '^[a-z0-9_]{3,40}$'", name="username_format"),)


class BookmarkCollection(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "bookmark_collections"

    user_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    slug: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[str | None] = mapped_column(String(280))
    is_public: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    __table_args__ = (
        UniqueConstraint("user_id", "slug", name="uq_bookmark_collections_user_id_slug"),
    )


class Bookmark(UUIDMixin, TimestampMixin, Base):
    """A saved dish, restaurant, or a specific dish-at-restaurant pairing."""

    __tablename__ = "bookmarks"

    user_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    collection_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("bookmark_collections.id", ondelete="SET NULL")
    )
    target_type: Mapped[BookmarkTarget] = mapped_column(
        Enum(BookmarkTarget, name="bookmark_target"), nullable=False
    )
    dish_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("dishes.id", ondelete="CASCADE")
    )
    restaurant_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("restaurants.id", ondelete="CASCADE")
    )
    note: Mapped[str | None] = mapped_column(String(280))

    __table_args__ = (
        # NULLS NOT DISTINCT makes the uniqueness meaningful when one FK is null.
        UniqueConstraint(
            "user_id",
            "target_type",
            "dish_id",
            "restaurant_id",
            name="uq_bookmarks_user_target",
            postgresql_nulls_not_distinct=True,
        ),
        CheckConstraint(
            "(target_type = 'dish' AND dish_id IS NOT NULL AND restaurant_id IS NULL) OR "
            "(target_type = 'restaurant' AND restaurant_id IS NOT NULL AND dish_id IS NULL) OR "
            "(target_type = 'dish_restaurant' AND dish_id IS NOT NULL "
            "AND restaurant_id IS NOT NULL)",
            name="bookmark_target_shape",
        ),
        Index("ix_bookmarks_user_id_created_at", "user_id", "created_at"),
    )


class Like(UUIDMixin, Base):
    __tablename__ = "likes"

    user_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    review_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("reviews.id", ondelete="CASCADE"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        UniqueConstraint("user_id", "review_id", name="uq_likes_user_id_review_id"),
        Index("ix_likes_review_id", "review_id"),
    )


class UserBadge(UUIDMixin, Base):
    __tablename__ = "user_badges"

    user_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    badge_code: Mapped[str] = mapped_column(String(60), nullable=False)
    level: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=1)
    progress: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    awarded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        UniqueConstraint("user_id", "badge_code", name="uq_user_badges_user_id_badge_code"),
    )


class GamificationEvent(UUIDMixin, Base):
    """Points are stored as events so totals can be honestly recomputed after a purge."""

    __tablename__ = "gamification_events"

    user_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    event_type: Mapped[str] = mapped_column(String(60), nullable=False)
    points: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    dish_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("dishes.id", ondelete="SET NULL")
    )
    restaurant_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("restaurants.id", ondelete="SET NULL")
    )
    review_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("reviews.id", ondelete="SET NULL")
    )
    meta: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (Index("ix_gamification_events_user_id_created_at", "user_id", "created_at"),)
