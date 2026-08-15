"""Search read model.

`SearchBackend` is an ABC with a Postgres implementation, so adding OpenSearch later
means adding one class and flipping `SEARCH_BACKEND` — no route or schema changes.

Ranking of dish results comes from the materialized `dish_scores` table; full-text
matching uses the generated `tsvector` with a trigram fallback for typos.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import City, Dish, DishScore, Restaurant
from app.models.enums import ScoreStatus
from app.schemas.dish import DishCardOut
from app.schemas.search import (
    ParsedQueryOut,
    SearchFilters,
    SearchResponse,
    SuggestItem,
    SuggestResponse,
)
from app.services.dish_service import (
    OSM_ATTRIBUTION,
    get_city,
    list_dish_restaurants,
)
from app.services.query_parser import ParsedQuery, parse_query
from app.utils.text import normalize_name

TRIGRAM_THRESHOLD = 0.3


class SearchBackend(ABC):
    @abstractmethod
    async def search(self, session: AsyncSession, filters: SearchFilters) -> SearchResponse: ...

    @abstractmethod
    async def suggest(
        self, session: AsyncSession, query: str, city_slug: str | None, limit: int
    ) -> SuggestResponse: ...


class PostgresSearchBackend(SearchBackend):
    async def search(self, session: AsyncSession, filters: SearchFilters) -> SearchResponse:
        city = await get_city(session, filters.city)
        parsed = parse_query(filters.q or "")
        merged = _merge_filters(filters, parsed)

        dish = await self._resolve_dish(session, merged)

        # A resolved dish is the product's core intent: rank restaurants *for it*.
        if dish is not None:
            ranked, insufficient, total = await list_dish_restaurants(
                session,
                dish=dish,
                city=city,
                lat=merged.lat,
                lng=merged.lng,
                radius_m=merged.radius_m,
                min_price=merged.min_price,
                max_price=merged.max_price,
                area=merged.area,
                trend=merged.trend,
                sort=merged.sort,
                page=merged.page,
                page_size=merged.page_size,
            )
            dish_card = await self._dish_card(session, dish, city)
            return SearchResponse(
                intent="dish",
                parsed=_parsed_out(parsed),
                dishes=[dish_card] if dish_card else [],
                restaurants=ranked + insufficient,
                page=merged.page,
                page_size=merged.page_size,
                total=total,
                has_more=merged.page * merged.page_size < total,
                city_slug=city.slug,
                attribution=[OSM_ATTRIBUTION],
            )

        dishes, total = await self._search_dishes(session, city, merged, parsed)
        return SearchResponse(
            intent=parsed.intent if parsed.raw else "browse",
            parsed=_parsed_out(parsed) if parsed.raw else None,
            dishes=dishes,
            restaurants=[],
            page=merged.page,
            page_size=merged.page_size,
            total=total,
            has_more=merged.page * merged.page_size < total,
            city_slug=city.slug,
            attribution=[OSM_ATTRIBUTION],
        )

    async def _resolve_dish(self, session: AsyncSession, filters: SearchFilters) -> Dish | None:
        """Resolve an explicit dish slug, or the parsed dish phrase.

        Exact/alias match first, then trigram similarity — so "chiken momo" still
        finds chicken momo without silently matching an unrelated dish.
        """
        from app.models import DishAlias

        if filters.dish:
            dish = (
                await session.execute(
                    select(Dish).where(Dish.slug == filters.dish, Dish.is_active.is_(True))
                )
            ).scalar_one_or_none()
            if dish is not None:
                return dish

        if not filters.q:
            return None

        parsed = parse_query(filters.q)
        if not parsed.dish_terms:
            return None

        term = normalize_name(parsed.dish_terms[0])
        if len(term) < 3:
            return None

        dish = (
            await session.execute(
                select(Dish).where(Dish.normalized_name == term, Dish.is_active.is_(True))
            )
        ).scalar_one_or_none()
        if dish is not None:
            return dish

        dish = (
            await session.execute(
                select(Dish)
                .join(DishAlias, DishAlias.dish_id == Dish.id)
                .where(DishAlias.normalized_alias == term, Dish.is_active.is_(True))
                .limit(1)
            )
        ).scalar_one_or_none()
        if dish is not None:
            return dish

        similarity = func.similarity(Dish.normalized_name, term)
        return (
            await session.execute(
                select(Dish)
                .where(Dish.is_active.is_(True), similarity > 0.45)
                .order_by(similarity.desc())
                .limit(1)
            )
        ).scalar_one_or_none()

    async def _search_dishes(
        self,
        session: AsyncSession,
        city: City,
        filters: SearchFilters,
        parsed: ParsedQuery,
    ) -> tuple[list[DishCardOut], int]:
        """Browse/filter dishes. Only dishes with ranked evidence in this city."""
        score_agg = func.avg(DishScore.score).label("avg_score")
        ranked_count = func.count(DishScore.id).label("ranked_count")
        mentions = func.sum(DishScore.mention_count).label("mentions")

        stmt = (
            select(Dish, score_agg, ranked_count, mentions)
            .join(DishScore, DishScore.dish_id == Dish.id)
            .where(
                DishScore.city_id == city.id,
                DishScore.status == ScoreStatus.RANKED,
                Dish.is_active.is_(True),
            )
            .group_by(Dish.id)
        )

        term = normalize_name(filters.q or "")
        if term and len(term) >= 2:
            stmt = stmt.where(
                or_(
                    Dish.search_vector.op("@@")(func.plainto_tsquery("simple", term)),
                    func.similarity(Dish.normalized_name, term) > TRIGRAM_THRESHOLD,
                )
            )

        cuisine = filters.cuisine or parsed.cuisine
        if cuisine:
            stmt = stmt.where(Dish.cuisine.ilike(f"%{cuisine}%"))

        dietary = filters.dietary or parsed.dietary
        if dietary in {"veg", "vegan", "jain"}:
            stmt = stmt.where(or_(Dish.is_veg.is_(True), Dish.is_veg.is_(None)))
        elif dietary == "non_veg":
            stmt = stmt.where(or_(Dish.is_veg.is_(False), Dish.is_veg.is_(None)))

        if filters.max_price is not None:
            stmt = stmt.having(func.min(DishScore.price_avg) <= filters.max_price)
        if filters.min_price is not None:
            stmt = stmt.having(func.max(DishScore.price_avg) >= filters.min_price)
        if filters.trend:
            stmt = stmt.where(DishScore.trend == filters.trend)

        if filters.sort == "trending":
            stmt = stmt.order_by(func.avg(DishScore.trend_delta).desc().nulls_last())
        elif filters.sort == "price":
            stmt = stmt.order_by(func.avg(DishScore.price_avg).asc().nulls_last())
        else:
            stmt = stmt.order_by(score_agg.desc().nulls_last())

        count_stmt = select(func.count()).select_from(stmt.subquery())
        total = (await session.execute(count_stmt)).scalar_one()

        rows = (
            await session.execute(
                stmt.offset((filters.page - 1) * filters.page_size).limit(filters.page_size)
            )
        ).all()

        cards: list[DishCardOut] = []
        for dish, avg_score, count, mention_total in rows:
            cards.append(
                DishCardOut(
                    id=str(dish.id),
                    slug=dish.slug,
                    name=dish.name,
                    cuisine=dish.cuisine,
                    category=dish.category.value,
                    is_veg=dish.is_veg,
                    hero_image_url=dish.hero_image_url,
                    score=round(float(avg_score), 2) if avg_score is not None else None,
                    status=ScoreStatus.RANKED.value,
                    mention_count=int(mention_total or 0),
                    restaurant_count=int(count or 0),
                )
            )

        return cards, int(total)

    async def _dish_card(self, session: AsyncSession, dish: Dish, city: City) -> DishCardOut | None:
        row = (
            await session.execute(
                select(
                    func.avg(DishScore.score),
                    func.count(DishScore.id),
                    func.sum(DishScore.mention_count),
                    func.min(DishScore.price_avg),
                    func.max(DishScore.price_avg),
                ).where(
                    DishScore.dish_id == dish.id,
                    DishScore.city_id == city.id,
                    DishScore.status == ScoreStatus.RANKED,
                )
            )
        ).one()

        avg_score, count, mentions, price_min, price_max = row

        from app.schemas.common import PriceRange

        return DishCardOut(
            id=str(dish.id),
            slug=dish.slug,
            name=dish.name,
            cuisine=dish.cuisine,
            category=dish.category.value,
            is_veg=dish.is_veg,
            hero_image_url=dish.hero_image_url,
            score=round(float(avg_score), 2) if avg_score is not None else None,
            status=(ScoreStatus.RANKED.value if count else ScoreStatus.INSUFFICIENT_DATA.value),
            mention_count=int(mentions or 0),
            restaurant_count=int(count or 0),
            price_range=(
                PriceRange(
                    min=float(price_min) if price_min else None,
                    max=float(price_max) if price_max else None,
                )
                if price_min or price_max
                else None
            ),
        )

    async def suggest(
        self, session: AsyncSession, query: str, city_slug: str | None, limit: int
    ) -> SuggestResponse:
        term = normalize_name(query)
        if len(term) < 2:
            return SuggestResponse(items=[])

        city = await get_city(session, city_slug)
        items: list[SuggestItem] = []

        dish_similarity = func.similarity(Dish.normalized_name, term)
        dish_rows = (
            await session.execute(
                select(Dish.name, Dish.slug, Dish.cuisine)
                .where(
                    Dish.is_active.is_(True),
                    or_(
                        Dish.normalized_name.ilike(f"{term}%"),
                        dish_similarity > TRIGRAM_THRESHOLD,
                    ),
                )
                .order_by(dish_similarity.desc(), Dish.mention_count.desc())
                .limit(limit)
            )
        ).all()
        items.extend(
            SuggestItem(kind="dish", label=row.name, slug=row.slug, subtitle=row.cuisine)
            for row in dish_rows
        )

        restaurant_similarity = func.similarity(Restaurant.normalized_name, term)
        restaurant_rows = (
            await session.execute(
                select(Restaurant.id, Restaurant.name, Restaurant.area)
                .where(
                    Restaurant.city_id == city.id,
                    Restaurant.is_closed.is_(False),
                    or_(
                        Restaurant.normalized_name.ilike(f"{term}%"),
                        restaurant_similarity > TRIGRAM_THRESHOLD,
                    ),
                )
                .order_by(restaurant_similarity.desc(), Restaurant.review_count.desc())
                .limit(limit)
            )
        ).all()
        items.extend(
            SuggestItem(kind="restaurant", label=row.name, id=str(row.id), subtitle=row.area)
            for row in restaurant_rows
        )

        return SuggestResponse(items=items[: limit * 2])


def _merge_filters(filters: SearchFilters, parsed: ParsedQuery) -> SearchFilters:
    """Explicit params win; parsed values only fill gaps.

    This ordering matters: a user who set a filter in the UI must not have it
    silently overridden by text they typed earlier.
    """
    data = filters.model_dump()
    if data.get("max_price") is None and parsed.max_price is not None:
        data["max_price"] = parsed.max_price
    if data.get("min_price") is None and parsed.min_price is not None:
        data["min_price"] = parsed.min_price
    if not data.get("area") and parsed.area:
        data["area"] = parsed.area
    if not data.get("cuisine") and parsed.cuisine:
        data["cuisine"] = parsed.cuisine
    if not data.get("dietary") and parsed.dietary:
        data["dietary"] = parsed.dietary
    if not data.get("mood") and parsed.mood:
        data["mood"] = parsed.mood
    if parsed.near_me and data.get("radius_m") is None and data.get("lat") is not None:
        data["radius_m"] = 3000
    return SearchFilters.model_validate(data)


def _parsed_out(parsed: ParsedQuery) -> ParsedQueryOut:
    return ParsedQueryOut(**parsed.to_dict())


_backend: SearchBackend | None = None


def get_search_backend() -> SearchBackend:
    global _backend
    if _backend is None:
        # Only one implementation today; the seam exists so OpenSearch can be added
        # without touching callers.
        _backend = PostgresSearchBackend()
    return _backend
