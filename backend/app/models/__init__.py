"""Model registry.

Importing this module registers every table on `Base.metadata`, which Alembic
autogenerate and the test bootstrap both rely on.
"""

from app.db.base import Base
from app.models.dish import Dish, DishAlias, RestaurantDish
from app.models.enums import (
    AIState,
    AspectType,
    BookmarkTarget,
    ConflictKind,
    ConflictStatus,
    DishCategory,
    ExtractionMethod,
    JobStatus,
    ModerationReason,
    ModerationStatus,
    ReviewStatus,
    ScoreStatus,
    SourceType,
    TrendDirection,
    TrendSubject,
    UserRole,
    ValueSignal,
)
from app.models.ops import AIProcessingJob, EntityConflict, IngestionJob, ModerationQueueItem
from app.models.restaurant import City, Restaurant, RestaurantAlias, RestaurantSource
from app.models.review import Review, ReviewAspect, ReviewDishMention, ReviewSource
from app.models.score import DishScore, RankingSnapshot, RestaurantScore, TrendMetric
from app.models.user import (
    Bookmark,
    BookmarkCollection,
    GamificationEvent,
    Like,
    Profile,
    User,
    UserBadge,
)

__all__ = [
    "AIProcessingJob",
    "AIState",
    "AspectType",
    "Base",
    "Bookmark",
    "BookmarkCollection",
    "BookmarkTarget",
    "City",
    "ConflictKind",
    "ConflictStatus",
    "Dish",
    "DishAlias",
    "DishCategory",
    "DishScore",
    "EntityConflict",
    "ExtractionMethod",
    "GamificationEvent",
    "IngestionJob",
    "JobStatus",
    "Like",
    "ModerationQueueItem",
    "ModerationReason",
    "ModerationStatus",
    "Profile",
    "RankingSnapshot",
    "Restaurant",
    "RestaurantAlias",
    "RestaurantDish",
    "RestaurantScore",
    "RestaurantSource",
    "Review",
    "ReviewAspect",
    "ReviewDishMention",
    "ReviewSource",
    "ReviewStatus",
    "ScoreStatus",
    "SourceType",
    "TrendDirection",
    "TrendMetric",
    "TrendSubject",
    "User",
    "UserBadge",
    "UserRole",
    "ValueSignal",
]
