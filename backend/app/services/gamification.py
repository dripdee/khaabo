"""Lightweight gamification.

Design constraint from the brief: reward useful contributions, not spam. So:
* points come from **published** reviews only, never from submissions
* dish-specific badges require distinct restaurants, not repeat visits
* everything is event-sourced, so a spam purge can honestly recompute totals
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.models import (
    Dish,
    GamificationEvent,
    Profile,
    Review,
    ReviewDishMention,
    User,
    UserBadge,
)
from app.models.enums import ReviewStatus

log = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class BadgeDef:
    code: str
    label: str
    description: str
    emoji: str
    dish_slug: str | None = None
    thresholds: tuple[int, ...] = (1, 5, 15)


BADGES: tuple[BadgeDef, ...] = (
    BadgeDef(
        "food_explorer",
        "Food Explorer",
        "Reviewed dishes at different restaurants",
        "🧭",
        thresholds=(3, 10, 25),
    ),
    BadgeDef(
        "momo_hunter",
        "Momo Hunter",
        "Reviewed momo at several places",
        "🥟",
        dish_slug="chicken-momo",
        thresholds=(3, 8, 20),
    ),
    BadgeDef(
        "biryani_scout",
        "Biryani Scout",
        "Reviewed biryani at several places",
        "🍛",
        dish_slug="chicken-biryani",
        thresholds=(3, 8, 20),
    ),
    BadgeDef(
        "hidden_gem_hunter",
        "Hidden Gem Hunter",
        "Reviewed places before they were popular",
        "💎",
        thresholds=(2, 6, 15),
    ),
    BadgeDef(
        "top_contributor",
        "Top Contributor",
        "Published reviews that others found useful",
        "🏆",
        thresholds=(10, 50, 150),
    ),
)

BADGES_BY_CODE = {badge.code: badge for badge in BADGES}

POINTS = {
    "review_published": 10,
    "review_with_dish": 4,
    "like_received": 1,
    "first_review_for_dish": 6,
}


async def award_for_review(
    session: AsyncSession,
    user: User,
    review: Review,
    dish_hints: list[str] | None = None,
) -> None:
    """Record points for a published review.

    Called only when a review reaches `published`, so spam that never clears
    moderation earns nothing.
    """
    if review.status is not ReviewStatus.PUBLISHED:
        return

    session.add(
        GamificationEvent(
            user_id=user.id,
            event_type="review_published",
            points=POINTS["review_published"],
            restaurant_id=review.restaurant_id,
            review_id=review.id,
            meta={"source": review.source.value},
        )
    )

    await recompute_contribution_score(session, user.id)
    await refresh_badges(session, user.id)


async def recompute_contribution_score(session: AsyncSession, user_id: uuid.UUID) -> int:
    """Sum from events rather than tracking a running total.

    Recomputation is cheap and means a purge of spam events immediately corrects the
    score instead of leaving inflated totals behind.
    """
    total = (
        await session.execute(
            select(func.coalesce(func.sum(GamificationEvent.points), 0)).where(
                GamificationEvent.user_id == user_id
            )
        )
    ).scalar_one()

    profile = (
        await session.execute(select(Profile).where(Profile.user_id == user_id))
    ).scalar_one_or_none()
    if profile is not None:
        profile.contribution_score = int(total)
    return int(total)


async def refresh_badges(session: AsyncSession, user_id: uuid.UUID) -> list[str]:
    """Evaluate every badge from current published evidence. Idempotent."""
    awarded: list[str] = []

    distinct_restaurants = (
        await session.execute(
            select(func.count(func.distinct(Review.restaurant_id))).where(
                Review.user_id == user_id, Review.status == ReviewStatus.PUBLISHED
            )
        )
    ).scalar_one()

    published_total = (
        await session.execute(
            select(func.count())
            .select_from(Review)
            .where(Review.user_id == user_id, Review.status == ReviewStatus.PUBLISHED)
        )
    ).scalar_one()

    for badge in BADGES:
        if badge.dish_slug:
            progress = await _distinct_restaurants_for_dish(session, user_id, badge.dish_slug)
        elif badge.code == "top_contributor":
            progress = int(published_total)
        elif badge.code == "hidden_gem_hunter":
            progress = await _hidden_gem_count(session, user_id)
        else:
            progress = int(distinct_restaurants)

        level = sum(1 for threshold in badge.thresholds if progress >= threshold)
        if level == 0:
            continue

        row = (
            await session.execute(
                select(UserBadge).where(
                    UserBadge.user_id == user_id, UserBadge.badge_code == badge.code
                )
            )
        ).scalar_one_or_none()

        if row is None:
            session.add(
                UserBadge(user_id=user_id, badge_code=badge.code, level=level, progress=progress)
            )
            awarded.append(badge.code)
        elif level > row.level or progress != row.progress:
            row.level = max(row.level, level)
            row.progress = progress

    if awarded:
        log.info("badges_awarded", user_id=str(user_id), badges=awarded)
    return awarded


async def _distinct_restaurants_for_dish(
    session: AsyncSession, user_id: uuid.UUID, dish_slug: str
) -> int:
    """Distinct restaurants, so ten reviews of the same shop is not a hunter badge."""
    return int(
        (
            await session.execute(
                select(func.count(func.distinct(ReviewDishMention.restaurant_id)))
                .join(Review, Review.id == ReviewDishMention.review_id)
                .join(Dish, Dish.id == ReviewDishMention.dish_id)
                .where(
                    Review.user_id == user_id,
                    Review.status == ReviewStatus.PUBLISHED,
                    Dish.slug == dish_slug,
                )
            )
        ).scalar_one()
    )


async def _hidden_gem_count(session: AsyncSession, user_id: uuid.UUID) -> int:
    """Reviews on restaurants with little other evidence — genuine discovery."""
    from app.models import Restaurant

    return int(
        (
            await session.execute(
                select(func.count(func.distinct(Review.restaurant_id)))
                .join(Restaurant, Restaurant.id == Review.restaurant_id)
                .where(
                    Review.user_id == user_id,
                    Review.status == ReviewStatus.PUBLISHED,
                    Restaurant.review_count <= 5,
                )
            )
        ).scalar_one()
    )


async def list_badges(session: AsyncSession, user_id: uuid.UUID) -> list[dict]:
    rows = (
        (await session.execute(select(UserBadge).where(UserBadge.user_id == user_id)))
        .scalars()
        .all()
    )

    out: list[dict] = []
    for row in rows:
        definition = BADGES_BY_CODE.get(row.badge_code)
        if definition is None:
            continue
        next_threshold = next((t for t in definition.thresholds if t > row.progress), None)
        out.append(
            {
                "code": definition.code,
                "label": definition.label,
                "description": definition.description,
                "emoji": definition.emoji,
                "level": row.level,
                "progress": row.progress,
                "target": next_threshold,
                "awarded_at": row.awarded_at,
            }
        )
    return out
