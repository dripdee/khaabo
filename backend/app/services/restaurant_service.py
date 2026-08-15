"""Restaurant read model, including Food DNA and per-restaurant dish ranking."""

from __future__ import annotations

import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError
from app.models import (
    City,
    Dish,
    DishScore,
    Restaurant,
    RestaurantDish,
    RestaurantScore,
    Review,
    ReviewDishMention,
    ReviewSource,
)
from app.models.enums import ReviewStatus, ScoreStatus
from app.schemas.common import WhyReason
from app.schemas.dish import (
    DishBrief,
    DnaChipOut,
    FoodDnaOut,
    RestaurantDetailOut,
    RestaurantDishOut,
)
from app.schemas.review import DishMentionOut, ReviewAuthor, ReviewOut
from app.services.dish_service import OSM_ATTRIBUTION, trend_out


async def get_restaurant(session: AsyncSession, restaurant_id: str) -> Restaurant:
    try:
        parsed = uuid.UUID(restaurant_id)
    except ValueError as exc:
        raise NotFoundError("Unknown restaurant") from exc

    restaurant = (
        await session.execute(select(Restaurant).where(Restaurant.id == parsed))
    ).scalar_one_or_none()
    if restaurant is None:
        raise NotFoundError("Unknown restaurant")
    return restaurant


async def get_food_dna(session: AsyncSession, restaurant: Restaurant) -> FoodDnaOut:
    """Food DNA is read from `restaurant_scores`, which the ranking job derives from
    evidence. An empty chip list means the evidence was too thin — not an error."""
    row = (
        await session.execute(
            select(RestaurantScore).where(RestaurantScore.restaurant_id == restaurant.id)
        )
    ).scalar_one_or_none()

    if row is None:
        return FoodDnaOut(
            restaurant_id=str(restaurant.id),
            chips=[],
            status=ScoreStatus.INSUFFICIENT_DATA.value,
        )

    return FoodDnaOut(
        restaurant_id=str(restaurant.id),
        chips=[DnaChipOut(**chip) for chip in (row.dna or [])],
        overall_score=float(row.overall_score) if row.overall_score is not None else None,
        sentiment=float(row.sentiment),
        consistency=float(row.consistency),
        value_score=float(row.value_score) if row.value_score is not None else None,
        trend=trend_out(row.trend, row.trend_delta),
        evidence_count=int(row.evidence_count),
        status=row.status.value,
    )


async def list_restaurant_dishes(
    session: AsyncSession,
    restaurant: Restaurant,
    *,
    page: int = 1,
    page_size: int = 20,
) -> tuple[list[RestaurantDishOut], int]:
    """Dishes ranked *within* this restaurant — what to order here."""
    stmt = (
        select(DishScore, Dish, RestaurantDish.is_signature)
        .join(Dish, Dish.id == DishScore.dish_id)
        .outerjoin(
            RestaurantDish,
            (RestaurantDish.dish_id == DishScore.dish_id)
            & (RestaurantDish.restaurant_id == DishScore.restaurant_id),
        )
        .where(DishScore.restaurant_id == restaurant.id)
        .order_by(
            DishScore.status.asc(),
            DishScore.score.desc().nulls_last(),
        )
    )

    total = (
        await session.execute(
            select(func.count())
            .select_from(DishScore)
            .where(DishScore.restaurant_id == restaurant.id)
        )
    ).scalar_one()

    rows = (await session.execute(stmt.offset((page - 1) * page_size).limit(page_size))).all()

    return [
        RestaurantDishOut(
            dish=DishBrief(
                id=str(dish.id),
                slug=dish.slug,
                name=dish.name,
                cuisine=dish.cuisine,
                category=dish.category.value,
                is_veg=dish.is_veg,
                hero_image_url=dish.hero_image_url,
            ),
            score=float(score.score) if score.score is not None else None,
            status=score.status.value,
            mention_count=int(score.mention_count),
            positive_ratio=float(score.positive_ratio),
            price_avg=float(score.price_avg) if score.price_avg else None,
            is_signature=bool(is_signature),
            trend=trend_out(score.trend, score.trend_delta),
            why=[WhyReason(**reason) for reason in (score.why or [])],
        )
        for score, dish, is_signature in rows
    ], int(total)


async def get_restaurant_detail(
    session: AsyncSession, restaurant: Restaurant
) -> RestaurantDetailOut:
    city = await session.get(City, restaurant.city_id)
    dna = await get_food_dna(session, restaurant)
    top_dishes, _ = await list_restaurant_dishes(session, restaurant, page=1, page_size=8)

    return RestaurantDetailOut(
        id=str(restaurant.id),
        name=restaurant.name,
        slug=restaurant.slug,
        area=restaurant.area,
        lat=float(restaurant.lat),
        lng=float(restaurant.lng),
        cuisines=list(restaurant.cuisines or []),
        price_level=restaurant.price_level,
        address=restaurant.address,
        phone=restaurant.phone,
        website=restaurant.website,
        opening_hours=restaurant.opening_hours,
        is_closed=restaurant.is_closed,
        is_verified=restaurant.is_verified,
        review_count=int(restaurant.review_count or 0),
        city_slug=city.slug if city else "",
        food_dna=dna,
        top_dishes=top_dishes,
        attribution=[OSM_ATTRIBUTION],
    )


async def list_restaurant_reviews(
    session: AsyncSession,
    restaurant: Restaurant,
    *,
    page: int = 1,
    page_size: int = 20,
    viewer_id: uuid.UUID | None = None,
) -> tuple[list[ReviewOut], int]:
    """Published, non-duplicate reviews only.

    Pending and rejected content is never exposed on a public endpoint, even to its
    author — the author reads their own submissions via /users/me.
    """
    from app.models import Like, Profile

    base = select(Review).where(
        Review.restaurant_id == restaurant.id,
        Review.status == ReviewStatus.PUBLISHED,
        Review.is_duplicate.is_(False),
    )

    total = (await session.execute(select(func.count()).select_from(base.subquery()))).scalar_one()

    rows = (
        (
            await session.execute(
                base.order_by(Review.published_at.desc())
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        )
        .scalars()
        .all()
    )

    review_ids = [r.id for r in rows]
    if not review_ids:
        return [], int(total)

    mention_rows = (
        await session.execute(
            select(
                ReviewDishMention.review_id,
                ReviewDishMention.sentiment,
                ReviewDishMention.snippet,
                ReviewDishMention.attributes,
                ReviewDishMention.price_mentioned,
                Dish.slug,
                Dish.name,
            )
            .join(Dish, Dish.id == ReviewDishMention.dish_id)
            .where(ReviewDishMention.review_id.in_(review_ids))
        )
    ).all()

    mentions_by_review: dict[uuid.UUID, list[DishMentionOut]] = {}
    for row in mention_rows:
        mentions_by_review.setdefault(row.review_id, []).append(
            DishMentionOut(
                dish_slug=row.slug,
                dish_name=row.name,
                sentiment=float(row.sentiment),
                snippet=row.snippet,
                attributes=list(row.attributes or []),
                price_mentioned=float(row.price_mentioned) if row.price_mentioned else None,
            )
        )

    source_rows = (
        await session.execute(
            select(ReviewSource.review_id, ReviewSource.url, ReviewSource.attribution).where(
                ReviewSource.review_id.in_(review_ids)
            )
        )
    ).all()
    sources = {row.review_id: (row.url, row.attribution) for row in source_rows}

    author_ids = [r.user_id for r in rows if r.user_id]
    authors: dict[uuid.UUID, ReviewAuthor] = {}
    if author_ids:
        profile_rows = (
            (await session.execute(select(Profile).where(Profile.user_id.in_(author_ids))))
            .scalars()
            .all()
        )
        authors = {
            p.user_id: ReviewAuthor(
                username=p.username, display_name=p.display_name, avatar_url=p.avatar_url
            )
            for p in profile_rows
        }

    liked: set[uuid.UUID] = set()
    if viewer_id:
        liked_rows = (
            (
                await session.execute(
                    select(Like.review_id).where(
                        Like.user_id == viewer_id, Like.review_id.in_(review_ids)
                    )
                )
            )
            .scalars()
            .all()
        )
        liked = set(liked_rows)

    return [
        ReviewOut(
            id=str(review.id),
            restaurant_id=str(review.restaurant_id),
            restaurant_name=restaurant.name,
            source=review.source.value,
            title=review.title,
            body=review.body,
            rating=float(review.rating) if review.rating is not None else None,
            lang=review.lang,
            overall_sentiment=(
                float(review.overall_sentiment) if review.overall_sentiment is not None else None
            ),
            like_count=int(review.like_count or 0),
            liked_by_me=review.id in liked,
            status=review.status.value,
            published_at=review.published_at,
            author=authors.get(review.user_id) if review.user_id else None,
            dish_mentions=mentions_by_review.get(review.id, []),
            source_url=sources.get(review.id, (None, None))[0],
            attribution=sources.get(review.id, (None, None))[1],
        )
        for review in rows
    ], int(total)
