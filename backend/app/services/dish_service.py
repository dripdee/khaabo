"""Dish read model.

All API-shaped reads live here so route handlers stay transport-only. Distance
filtering uses PostGIS `ST_DWithin` against the GIST index; ranking comes from the
materialized `dish_scores` table rather than being computed per request.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from geoalchemy2.functions import ST_Distance, ST_DWithin
from sqlalchemy import Select, case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError
from app.models import (
    City,
    Dish,
    DishScore,
    Restaurant,
    RestaurantDish,
    Review,
    ReviewDishMention,
    TrendMetric,
)
from app.models.enums import ReviewStatus, ScoreStatus, TrendSubject
from app.schemas.common import AttributeCount, PriceRange, TrendOut, WhyReason
from app.schemas.dish import (
    DishBrief,
    DishDetailOut,
    DishHighlights,
    DishMapOut,
    DishRestaurantOut,
    DishSummaryOut,
    MapMarkerOut,
    RecentSignal,
    SnippetOut,
)

OSM_ATTRIBUTION = "© OpenStreetMap contributors"


async def get_city(session: AsyncSession, slug: str | None) -> City:
    from app.core.config import settings

    target = slug or settings.default_city_slug
    city = (
        await session.execute(select(City).where(City.slug == target, City.active.is_(True)))
    ).scalar_one_or_none()
    if city is None:
        raise NotFoundError(f"Unknown or inactive city '{target}'")
    return city


async def get_dish_by_slug(session: AsyncSession, slug: str) -> Dish:
    dish = (
        await session.execute(select(Dish).where(Dish.slug == slug, Dish.is_active.is_(True)))
    ).scalar_one_or_none()
    if dish is None:
        raise NotFoundError(f"Unknown dish '{slug}'")
    return dish


def _dish_brief(dish: Dish) -> DishBrief:
    return DishBrief(
        id=str(dish.id),
        slug=dish.slug,
        name=dish.name,
        cuisine=dish.cuisine,
        category=dish.category.value,
        is_veg=dish.is_veg,
        hero_image_url=dish.hero_image_url,
    )


def trend_out(direction, delta, significant: bool = False) -> TrendOut | None:
    """No direction means no arrow. Never fabricate a trend from thin data."""
    if direction is None:
        return None
    return TrendOut(
        direction=direction.value if hasattr(direction, "value") else str(direction),
        delta=float(delta) if delta is not None else None,
        significant=significant,
    )


def _badges(row: DishScore) -> list[str]:
    badges: list[str] = []
    if row.is_best_value:
        badges.append("best_value")
    if row.is_hidden_gem:
        badges.append("hidden_gem")
    if row.is_most_consistent:
        badges.append("most_consistent")
    return badges


def _why(row: DishScore) -> list[WhyReason]:
    return [WhyReason(**reason) for reason in (row.why or [])]


def _apply_geo(
    stmt: Select,
    lat: float | None,
    lng: float | None,
    radius_m: int | None,
) -> Select:
    if lat is None or lng is None:
        return stmt
    point = func.ST_SetSRID(func.ST_MakePoint(lng, lat), 4326)
    if radius_m:
        stmt = stmt.where(ST_DWithin(Restaurant.location, point, radius_m))
    return stmt


def _distance_column(lat: float | None, lng: float | None):
    if lat is None or lng is None:
        return None
    point = func.ST_SetSRID(func.ST_MakePoint(lng, lat), 4326)
    return ST_Distance(Restaurant.location, point).label("distance_m")


async def list_dish_restaurants(
    session: AsyncSession,
    *,
    dish: Dish,
    city: City,
    lat: float | None = None,
    lng: float | None = None,
    radius_m: int | None = None,
    max_price: float | None = None,
    min_price: float | None = None,
    area: str | None = None,
    trend: str | None = None,
    sort: str = "score",
    page: int = 1,
    page_size: int = 20,
    include_snippets: bool = True,
) -> tuple[list[DishRestaurantOut], list[DishRestaurantOut], int]:
    """Ranked restaurants for a dish.

    Returns `(ranked, insufficient, total)`. Rows with too little evidence are kept
    in a separate bucket so they are never interleaved into a ranking.
    """
    distance_col = _distance_column(lat, lng)
    columns = [DishScore, Restaurant]
    if distance_col is not None:
        columns.append(distance_col)

    stmt = (
        select(*columns)
        .join(Restaurant, Restaurant.id == DishScore.restaurant_id)
        .where(
            DishScore.dish_id == dish.id,
            DishScore.city_id == city.id,
            Restaurant.is_closed.is_(False),
        )
    )

    stmt = _apply_geo(stmt, lat, lng, radius_m)

    if max_price is not None:
        stmt = stmt.where(DishScore.price_avg <= max_price)
    if min_price is not None:
        stmt = stmt.where(DishScore.price_avg >= min_price)
    if area:
        stmt = stmt.where(Restaurant.area.ilike(f"%{area}%"))
    if trend:
        stmt = stmt.where(DishScore.trend == trend)

    if sort == "distance" and distance_col is not None:
        stmt = stmt.order_by(distance_col.asc())
    elif sort == "price":
        stmt = stmt.order_by(DishScore.price_avg.asc().nulls_last())
    elif sort == "trending":
        stmt = stmt.order_by(DishScore.trend_delta.desc().nulls_last())
    else:
        stmt = stmt.order_by(
            DishScore.status.asc(),  # 'insufficient_data' sorts after 'ranked'
            DishScore.score.desc().nulls_last(),
        )

    total = (
        await session.execute(
            select(func.count())
            .select_from(DishScore)
            .join(Restaurant, Restaurant.id == DishScore.restaurant_id)
            .where(
                DishScore.dish_id == dish.id,
                DishScore.city_id == city.id,
                DishScore.status == ScoreStatus.RANKED,
            )
        )
    ).scalar_one()

    rows = (await session.execute(stmt.offset((page - 1) * page_size).limit(page_size))).all()

    ranked: list[DishRestaurantOut] = []
    insufficient: list[DishRestaurantOut] = []

    for row in rows:
        score_row: DishScore = row[0]
        restaurant: Restaurant = row[1]
        distance = float(row[2]) if distance_col is not None and row[2] is not None else None

        snippets = []
        if include_snippets and score_row.status == ScoreStatus.RANKED:
            snippets = await dish_snippets(session, dish.id, restaurant.id)

        item = DishRestaurantOut(
            id=str(restaurant.id),
            name=restaurant.name,
            slug=restaurant.slug,
            area=restaurant.area,
            lat=float(restaurant.lat),
            lng=float(restaurant.lng),
            cuisines=list(restaurant.cuisines or []),
            price_level=restaurant.price_level,
            score=float(score_row.score) if score_row.score is not None else None,
            status=score_row.status.value,
            positive_ratio=float(score_row.positive_ratio),
            mention_count=int(score_row.mention_count),
            consistency=float(score_row.consistency),
            price_avg=float(score_row.price_avg) if score_row.price_avg else None,
            value_score=float(score_row.value_score) if score_row.value_score else None,
            trend=trend_out(score_row.trend, score_row.trend_delta),
            badges=_badges(score_row),
            why=_why(score_row),
            top_attributes=list(score_row.top_attributes or []),
            snippets=snippets,
            distance_m=round(distance, 1) if distance is not None else None,
        )

        if score_row.status == ScoreStatus.RANKED:
            ranked.append(item)
        else:
            insufficient.append(item)

    return ranked, insufficient, int(total)


async def dish_snippets(
    session: AsyncSession, dish_id: uuid.UUID, restaurant_id: uuid.UUID, limit: int = 2
) -> list[SnippetOut]:
    rows = (
        await session.execute(
            select(
                ReviewDishMention.snippet,
                ReviewDishMention.sentiment,
                Review.source,
                Review.published_at,
                Review.id,
            )
            .join(Review, Review.id == ReviewDishMention.review_id)
            .where(
                ReviewDishMention.dish_id == dish_id,
                ReviewDishMention.restaurant_id == restaurant_id,
                ReviewDishMention.snippet.isnot(None),
                Review.status == ReviewStatus.PUBLISHED,
                Review.is_duplicate.is_(False),
            )
            .order_by(ReviewDishMention.confidence.desc(), Review.published_at.desc())
            .limit(limit)
        )
    ).all()

    return [
        SnippetOut(
            text=row.snippet,
            sentiment=float(row.sentiment),
            source=row.source.value,
            published_at=row.published_at.isoformat() if row.published_at else None,
            review_id=str(row.id),
        )
        for row in rows
    ]


async def get_dish_detail(
    session: AsyncSession,
    *,
    dish: Dish,
    city: City,
    lat: float | None = None,
    lng: float | None = None,
) -> DishDetailOut:
    """Everything the dish page needs, in one payload."""
    ranked, insufficient, total = await list_dish_restaurants(
        session,
        dish=dish,
        city=city,
        lat=lat,
        lng=lng,
        page=1,
        page_size=50,
    )

    aggregate_score = None
    if ranked:
        top = [r.score for r in ranked[:10] if r.score is not None]
        aggregate_score = round(sum(top) / len(top), 2) if top else None

    price_rows = (
        await session.execute(
            select(
                func.min(RestaurantDish.price_min),
                func.max(RestaurantDish.price_max),
                func.avg(RestaurantDish.price_avg),
            )
            .join(Restaurant, Restaurant.id == RestaurantDish.restaurant_id)
            .where(RestaurantDish.dish_id == dish.id, Restaurant.city_id == city.id)
        )
    ).one()

    price_range = None
    if any(v is not None for v in price_rows):
        price_range = PriceRange(
            min=float(price_rows[0]) if price_rows[0] else None,
            max=float(price_rows[1]) if price_rows[1] else None,
            avg=round(float(price_rows[2]), 2) if price_rows[2] else None,
        )

    mention_count = (
        await session.execute(
            select(func.count())
            .select_from(ReviewDishMention)
            .join(Review, Review.id == ReviewDishMention.review_id)
            .where(
                ReviewDishMention.dish_id == dish.id,
                Review.city_id == city.id,
                Review.status == ReviewStatus.PUBLISHED,
                Review.is_duplicate.is_(False),
            )
        )
    ).scalar_one()

    positive, negative = await _attribute_breakdown(session, dish.id, city.id)
    trend = await _dish_trend(session, dish.id, city.id)
    highlights = _highlights(ranked)
    summary = await _summary(session, dish, city, int(mention_count))

    status = ScoreStatus.RANKED.value if ranked else ScoreStatus.INSUFFICIENT_DATA.value

    return DishDetailOut(
        dish=_dish_brief(dish),
        city_slug=city.slug,
        score=aggregate_score,
        status=status,
        trend=trend,
        mention_count=int(mention_count),
        restaurant_count=total,
        price_range=price_range,
        positive_attributes=positive,
        negative_attributes=negative,
        summary=summary,
        highlights=highlights,
        recent_signals=await _recent_signals(session, dish.id, city.id),
        attribution=[OSM_ATTRIBUTION],
    )


def _highlights(ranked: list[DishRestaurantOut]) -> DishHighlights:
    """Named picks come from persisted badges, so they match the stored ranking."""
    return DishHighlights(
        top=ranked[0] if ranked else None,
        best_value=next((r for r in ranked if "best_value" in r.badges), None),
        hidden_gem=next((r for r in ranked if "hidden_gem" in r.badges), None),
        most_consistent=next((r for r in ranked if "most_consistent" in r.badges), None),
    )


async def _attribute_breakdown(
    session: AsyncSession, dish_id: uuid.UUID, city_id: uuid.UUID
) -> tuple[list[AttributeCount], list[AttributeCount]]:
    """Split attributes by the sentiment of the mention that carried them."""
    rows = (
        await session.execute(
            select(ReviewDishMention.attributes, ReviewDishMention.sentiment)
            .join(Review, Review.id == ReviewDishMention.review_id)
            .where(
                ReviewDishMention.dish_id == dish_id,
                Review.city_id == city_id,
                Review.status == ReviewStatus.PUBLISHED,
                Review.is_duplicate.is_(False),
            )
        )
    ).all()

    positive: dict[str, int] = {}
    negative: dict[str, int] = {}
    for attributes, sentiment in rows:
        bucket = positive if float(sentiment) >= 0 else negative
        for attribute in attributes or []:
            bucket[attribute] = bucket.get(attribute, 0) + 1

    def _top(counts: dict[str, int]) -> list[AttributeCount]:
        return [
            AttributeCount(label=key.replace("_", " "), count=value)
            for key, value in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:8]
        ]

    return _top(positive), _top(negative)


async def _dish_trend(
    session: AsyncSession, dish_id: uuid.UUID, city_id: uuid.UUID
) -> TrendOut | None:
    row = (
        await session.execute(
            select(TrendMetric)
            .where(
                TrendMetric.subject_type == TrendSubject.DISH,
                TrendMetric.dish_id == dish_id,
                TrendMetric.city_id == city_id,
            )
            .order_by(TrendMetric.computed_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if row is None or row.direction is None:
        return None
    return trend_out(row.direction, row.delta, row.significant)


async def _summary(
    session: AsyncSession, dish: Dish, city: City, mention_count: int
) -> DishSummaryOut | None:
    """Template summary built from stored counts — traceable, never invented."""
    if mention_count < 3:
        return None

    rows = (
        await session.execute(
            select(ReviewDishMention.review_id, ReviewDishMention.sentiment)
            .join(Review, Review.id == ReviewDishMention.review_id)
            .where(
                ReviewDishMention.dish_id == dish.id,
                Review.city_id == city.id,
                Review.status == ReviewStatus.PUBLISHED,
                Review.is_duplicate.is_(False),
            )
            .order_by(Review.published_at.desc())
            .limit(50)
        )
    ).all()

    if not rows:
        return None

    positive = sum(1 for _, sentiment in rows if float(sentiment) > 0.15)
    ratio = positive / len(rows)

    ranked_count = (
        await session.execute(
            select(func.count())
            .select_from(DishScore)
            .where(
                DishScore.dish_id == dish.id,
                DishScore.city_id == city.id,
                DishScore.status == ScoreStatus.RANKED,
            )
        )
    ).scalar_one()

    parts = [f"{mention_count} mentions", f"{round(ratio * 100)}% positive"]
    if ranked_count:
        parts.append(f"{int(ranked_count)} ranked places in {city.name}")

    return DishSummaryOut(
        text=f"{dish.name}: " + " · ".join(parts) + ".",
        generated_by="template",
        evidence_review_ids=[str(r[0]) for r in rows[:8]],
        mention_count=mention_count,
        positive_ratio=round(ratio, 4),
    )


async def _recent_signals(
    session: AsyncSession, dish_id: uuid.UUID, city_id: uuid.UUID, months: int = 6
) -> list[RecentSignal]:
    cutoff = datetime.now(UTC) - timedelta(days=30 * months)
    period = func.to_char(Review.published_at, "YYYY-MM").label("period")

    rows = (
        await session.execute(
            select(
                period,
                func.count().label("mentions"),
                func.avg(case((ReviewDishMention.sentiment > 0.15, 1.0), else_=0.0)).label(
                    "positive_ratio"
                ),
            )
            .select_from(ReviewDishMention)
            .join(Review, Review.id == ReviewDishMention.review_id)
            .where(
                ReviewDishMention.dish_id == dish_id,
                Review.city_id == city_id,
                Review.published_at >= cutoff,
                Review.status == ReviewStatus.PUBLISHED,
                Review.is_duplicate.is_(False),
            )
            .group_by(period)
            .order_by(period)
        )
    ).all()

    return [
        RecentSignal(
            period=row.period,
            positive_ratio=round(float(row.positive_ratio or 0), 4),
            mentions=int(row.mentions),
        )
        for row in rows
    ]


async def get_dish_map(
    session: AsyncSession,
    *,
    dish: Dish,
    city: City,
    lat: float | None = None,
    lng: float | None = None,
    radius_m: int | None = None,
    max_price: float | None = None,
    trend: str | None = None,
    limit: int = 300,
) -> DishMapOut:
    """Marker-only payload. Changing the dish swaps the whole marker set."""
    stmt = (
        select(
            Restaurant.id,
            Restaurant.name,
            Restaurant.lat,
            Restaurant.lng,
            DishScore.score,
            DishScore.status,
            DishScore.price_avg,
            DishScore.trend,
            DishScore.is_best_value,
            DishScore.is_hidden_gem,
            DishScore.is_most_consistent,
        )
        .join(DishScore, DishScore.restaurant_id == Restaurant.id)
        .where(
            DishScore.dish_id == dish.id,
            DishScore.city_id == city.id,
            Restaurant.is_closed.is_(False),
        )
        .order_by(DishScore.score.desc().nulls_last())
        .limit(limit)
    )

    stmt = _apply_geo(stmt, lat, lng, radius_m)
    if max_price is not None:
        stmt = stmt.where(DishScore.price_avg <= max_price)
    if trend:
        stmt = stmt.where(DishScore.trend == trend)

    rows = (await session.execute(stmt)).all()

    markers = [
        MapMarkerOut(
            id=str(row.id),
            name=row.name,
            lat=float(row.lat),
            lng=float(row.lng),
            score=float(row.score) if row.score is not None else None,
            status=row.status.value,
            price_avg=float(row.price_avg) if row.price_avg else None,
            trend=row.trend.value if row.trend else None,
            badges=[
                name
                for name, flag in (
                    ("best_value", row.is_best_value),
                    ("hidden_gem", row.is_hidden_gem),
                    ("most_consistent", row.is_most_consistent),
                )
                if flag
            ],
        )
        for row in rows
    ]

    bounds = None
    if markers:
        lats = [m.lat for m in markers]
        lngs = [m.lng for m in markers]
        bounds = {
            "south": min(lats),
            "west": min(lngs),
            "north": max(lats),
            "east": max(lngs),
        }

    return DishMapOut(
        dish=_dish_brief(dish),
        city_slug=city.slug,
        markers=markers,
        bounds=bounds,
        attribution=[OSM_ATTRIBUTION],
    )
