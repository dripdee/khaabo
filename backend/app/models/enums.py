"""Shared enums.

Defined once as Python `StrEnum` and reused for both SQLAlchemy columns and
Pydantic schemas, so DB values and API values can never drift apart.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from sqlalchemy import Enum as SQLEnum


def pg_enum(enum_cls: Any, name: str, **kwargs: Any) -> SQLEnum:
    """Create a PostgreSQL enum column using enum string values rather than member names."""
    return SQLEnum(
        enum_cls,
        name=name,
        values_callable=lambda x: [e.value for e in x],
        **kwargs,
    )


class SourceType(StrEnum):
    OSM = "osm"
    REDDIT = "reddit"
    YOUTUBE = "youtube"
    USER = "user"
    MANUAL = "manual"


class ReviewStatus(StrEnum):
    PENDING = "pending"
    PUBLISHED = "published"
    REJECTED = "rejected"
    FLAGGED = "flagged"


class AIState(StrEnum):
    PENDING = "pending"
    PROCESSING = "processing"
    DONE = "done"
    FAILED = "failed"


class JobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    SKIPPED = "skipped"


class ScoreStatus(StrEnum):
    RANKED = "ranked"
    INSUFFICIENT_DATA = "insufficient_data"


class TrendDirection(StrEnum):
    RISING = "rising"
    STABLE = "stable"
    DECLINING = "declining"


class DishCategory(StrEnum):
    STREET_FOOD = "street_food"
    MAIN = "main"
    SNACK = "snack"
    DESSERT = "dessert"
    BEVERAGE = "beverage"
    BREAKFAST = "breakfast"
    SIDE = "side"
    OTHER = "other"


class AspectType(StrEnum):
    TASTE = "taste"
    PORTION = "portion"
    PRICE = "price"
    SERVICE = "service"
    AMBIENCE = "ambience"
    HYGIENE = "hygiene"
    WAIT_TIME = "wait_time"
    CONSISTENCY = "consistency"
    SPICE = "spice"


class ExtractionMethod(StrEnum):
    AI = "ai"
    ALIAS = "alias"
    USER = "user"


class UserRole(StrEnum):
    USER = "user"
    MODERATOR = "moderator"
    ADMIN = "admin"


class BookmarkTarget(StrEnum):
    DISH = "dish"
    RESTAURANT = "restaurant"
    DISH_RESTAURANT = "dish_restaurant"


class ModerationReason(StrEnum):
    SPAM = "spam"
    DUPLICATE = "duplicate"
    ABUSE = "abuse"
    USER_REPORT = "user_report"
    LOW_QUALITY = "low_quality"
    MANUAL = "manual"


class ModerationStatus(StrEnum):
    OPEN = "open"
    RESOLVED = "resolved"
    DISMISSED = "dismissed"


class ConflictKind(StrEnum):
    RESTAURANT = "restaurant"
    DISH = "dish"


class ConflictStatus(StrEnum):
    OPEN = "open"
    MERGED = "merged"
    REJECTED = "rejected"


class TrendSubject(StrEnum):
    DISH = "dish"
    RESTAURANT = "restaurant"
    DISH_RESTAURANT = "dish_restaurant"


class ValueSignal(StrEnum):
    CHEAP = "cheap"
    FAIR = "fair"
    EXPENSIVE = "expensive"
    UNKNOWN = "unknown"
