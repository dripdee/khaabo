"""Trending, cities and health endpoints."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select

from app.api.deps import DbSession, rate_limit_read
from app.core.cache import cache_key, cached, get_redis
from app.core.config import settings
from app.models import City, Dish, DishScore, Restaurant, RestaurantScore, TrendMetric
from app.models.enums import ScoreStatus, TrendDirection, TrendSubject
from app.schemas.common import CityOut, HealthComponent, HealthOut
from app.schemas.dish import DishBrief, RestaurantBrief, TrendingItemOut
from app.services.dish_service import get_city

router = APIRouter(tags=["discovery"], dependencies=[Depends(rate_limit_read)])


@router.get("/cities", response_model=list[CityOut], summary="Active cities")
async def list_cities(session: DbSession) -> list[CityOut]:
    """Cities are data, not configuration branches — adding one needs no code change."""
    rows = (
        (await session.execute(select(City).where(City.active.is_(True)).order_by(City.name)))
        .scalars()
        .all()
    )
    return [
        CityOut(
            id=str(c.id),
            slug=c.slug,
            name=c.name,
            country=c.country,
            lat=float(c.lat),
            lng=float(c.lng),
            timezone=c.timezone,
        )
        for c in rows
    ]


@router.get("/trending", response_model=dict, summary="Rising dishes and restaurants")
async def trending(
    session: DbSession,
    city: Annotated[str | None, Query(max_length=80)] = None,
    direction: Annotated[str, Query(pattern="^(rising|declining)$")] = "rising",
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
) -> dict:
    """Only trends that cleared the significance gate are returned.

    A subject without enough observations in both windows has `direction = NULL` in
    `trend_metrics` and is therefore excluded here by construction.
    """
    city_row = await get_city(session, city)
    key = cache_key("trending", city=city_row.slug, direction=direction, limit=limit)

    async def produce() -> dict:
        target = TrendDirection(direction)

        dish_rows = (
            await session.execute(
                select(TrendMetric, Dish)
                .join(Dish, Dish.id == TrendMetric.dish_id)
                .where(
                    TrendMetric.city_id == city_row.id,
                    TrendMetric.subject_type == TrendSubject.DISH,
                    TrendMetric.direction == target,
                )
                .order_by(
                    TrendMetric.significant.desc(),
                    func.abs(TrendMetric.delta).desc().nulls_last(),
                )
                .limit(limit)
            )
        ).all()

        dish_scores = dict(
            (
                await session.execute(
                    select(DishScore.dish_id, func.avg(DishScore.score))
                    .where(
                        DishScore.city_id == city_row.id,
                        DishScore.status == ScoreStatus.RANKED,
                    )
                    .group_by(DishScore.dish_id)
                )
            ).all()
        )

        dishes = [
            TrendingItemOut(
                kind="dish",
                dish=DishBrief(
                    id=str(dish.id),
                    slug=dish.slug,
                    name=dish.name,
                    cuisine=dish.cuisine,
                    category=dish.category.value,
                    is_veg=dish.is_veg,
                    hero_image_url=dish.hero_image_url,
                ),
                direction=metric.direction.value,
                delta=float(metric.delta) if metric.delta is not None else None,
                recent_count=int(metric.recent_count),
                significant=metric.significant,
                score=(
                    round(float(dish_scores[dish.id]), 2)
                    if dish_scores.get(dish.id) is not None
                    else None
                ),
            ).model_dump(mode="json")
            for metric, dish in dish_rows
        ]

        restaurant_rows = (
            await session.execute(
                select(RestaurantScore, Restaurant)
                .join(Restaurant, Restaurant.id == RestaurantScore.restaurant_id)
                .where(
                    RestaurantScore.city_id == city_row.id,
                    RestaurantScore.trend == target,
                )
                .order_by(func.abs(RestaurantScore.trend_delta).desc().nulls_last())
                .limit(limit)
            )
        ).all()

        restaurants = [
            TrendingItemOut(
                kind="restaurant",
                restaurant=RestaurantBrief(
                    id=str(restaurant.id),
                    name=restaurant.name,
                    slug=restaurant.slug,
                    area=restaurant.area,
                    lat=float(restaurant.lat),
                    lng=float(restaurant.lng),
                    cuisines=list(restaurant.cuisines or []),
                    price_level=restaurant.price_level,
                    google_rating=(
                        float(restaurant.google_rating)
                        if restaurant.google_rating is not None
                        else None
                    ),
                    google_rating_count=restaurant.google_rating_count,
                ),
                direction=score.trend.value if score.trend else "stable",
                delta=float(score.trend_delta) if score.trend_delta is not None else None,
                significant=True,
                score=float(score.overall_score) if score.overall_score is not None else None,
            ).model_dump(mode="json")
            for score, restaurant in restaurant_rows
        ]

        return {
            "city_slug": city_row.slug,
            "direction": direction,
            "dishes": dishes,
            "restaurants": restaurants,
        }

    return await cached(key, settings.cache_ttl_trending, produce)


@router.get("/health", response_model=HealthOut, summary="Health check")
async def health(session: DbSession) -> HealthOut:
    """Component-level health.

    Redis and the AI provider are reported separately from the database, because a
    degraded cache or model endpoint must not mark the API unhealthy — the product
    still serves requests without either.
    """
    components: list[HealthComponent] = []

    try:
        await session.execute(select(func.now()))
        components.append(HealthComponent(name="database", ok=True))
    except Exception as exc:  # noqa: BLE001
        components.append(HealthComponent(name="database", ok=False, detail=str(exc)[:200]))

    redis_client = get_redis()
    if redis_client is None:
        components.append(
            HealthComponent(name="redis", ok=True, detail="disabled or unavailable (degraded)")
        )
    else:
        try:
            await redis_client.ping()
            components.append(HealthComponent(name="redis", ok=True))
        except Exception as exc:  # noqa: BLE001
            components.append(
                HealthComponent(name="redis", ok=True, detail=f"degraded: {str(exc)[:120]}")
            )

    from app.ai import get_provider

    provider = get_provider()
    components.append(
        HealthComponent(
            name="ai_provider",
            ok=True,
            detail=f"{provider.name} ({provider.model or 'n/a'})",
        )
    )

    database_ok = next((c.ok for c in components if c.name == "database"), False)
    return HealthOut(
        status="ok" if database_ok else "degraded",
        version="0.1.0",
        components=components,
    )
