"""Dish endpoints — the heart of the product."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.api.deps import DbSession, rate_limit_read
from app.core.cache import cache_key, cached
from app.core.config import settings
from app.schemas.dish import DishDetailOut, DishMapOut
from app.services.dish_service import (
    get_city,
    get_dish_by_slug,
    get_dish_detail,
    get_dish_map,
    list_dish_restaurants,
)

router = APIRouter(prefix="/dishes", tags=["dishes"], dependencies=[Depends(rate_limit_read)])


@router.get("/{slug}", response_model=DishDetailOut, summary="Dish page payload")
async def dish_detail(
    slug: str,
    session: DbSession,
    city: Annotated[str | None, Query(max_length=80)] = None,
    lat: Annotated[float | None, Query(ge=-90, le=90)] = None,
    lng: Annotated[float | None, Query(ge=-180, le=180)] = None,
) -> DishDetailOut:
    """Score, trend, highlights, attributes, price range and an evidence summary.

    When the evidence is too thin the response carries
    `status="insufficient_data"` and null scores; clients must render
    "Not enough data" rather than a placeholder ranking.
    """
    city_row = await get_city(session, city)
    dish = await get_dish_by_slug(session, slug)

    # Personalized (lat/lng) responses bypass the shared cache.
    if lat is not None or lng is not None:
        return await get_dish_detail(session, dish=dish, city=city_row, lat=lat, lng=lng)

    key = cache_key(f"dish:{slug}:detail", city=city_row.slug)

    async def produce() -> dict:
        detail = await get_dish_detail(session, dish=dish, city=city_row)
        return detail.model_dump(mode="json")

    payload = await cached(key, settings.cache_ttl_dish, produce)
    return DishDetailOut.model_validate(payload)


@router.get(
    "/{slug}/restaurants",
    response_model=dict,
    summary="Restaurants ranked for this dish",
)
async def dish_restaurants(
    slug: str,
    session: DbSession,
    city: Annotated[str | None, Query(max_length=80)] = None,
    lat: Annotated[float | None, Query(ge=-90, le=90)] = None,
    lng: Annotated[float | None, Query(ge=-180, le=180)] = None,
    radius_m: Annotated[int | None, Query(ge=100, le=50000)] = None,
    min_price: Annotated[float | None, Query(ge=0, le=100000)] = None,
    max_price: Annotated[float | None, Query(ge=0, le=100000)] = None,
    area: Annotated[str | None, Query(max_length=120)] = None,
    trend: Annotated[str | None, Query(max_length=20)] = None,
    sort: Annotated[str, Query(pattern="^(score|distance|trending|price)$")] = "score",
    page: Annotated[int, Query(ge=1, le=200)] = 1,
    page_size: Annotated[int, Query(ge=1, le=50)] = 20,
) -> dict:
    """Ranked list plus a separate `insufficient` bucket.

    Rows without enough evidence are never interleaved into the ranking, so the
    ordering the user sees always means something.
    """
    city_row = await get_city(session, city)
    dish = await get_dish_by_slug(session, slug)

    ranked, insufficient, total = await list_dish_restaurants(
        session,
        dish=dish,
        city=city_row,
        lat=lat,
        lng=lng,
        radius_m=radius_m,
        min_price=min_price,
        max_price=max_price,
        area=area,
        trend=trend,
        sort=sort,
        page=page,
        page_size=page_size,
    )

    return {
        "dish_slug": dish.slug,
        "city_slug": city_row.slug,
        "items": [item.model_dump(mode="json") for item in ranked],
        "insufficient": [item.model_dump(mode="json") for item in insufficient],
        "page": page,
        "page_size": page_size,
        "total": total,
        "has_more": page * page_size < total,
        "attribution": ["© OpenStreetMap contributors"],
    }


@router.get("/{slug}/map", response_model=DishMapOut, summary="Dish-scoped map markers")
async def dish_map(
    slug: str,
    session: DbSession,
    city: Annotated[str | None, Query(max_length=80)] = None,
    lat: Annotated[float | None, Query(ge=-90, le=90)] = None,
    lng: Annotated[float | None, Query(ge=-180, le=180)] = None,
    radius_m: Annotated[int | None, Query(ge=100, le=50000)] = None,
    max_price: Annotated[float | None, Query(ge=0, le=100000)] = None,
    trend: Annotated[str | None, Query(max_length=20)] = None,
) -> DishMapOut:
    """Marker-only payload, so switching the selected dish swaps the whole map."""
    city_row = await get_city(session, city)
    dish = await get_dish_by_slug(session, slug)
    return await get_dish_map(
        session,
        dish=dish,
        city=city_row,
        lat=lat,
        lng=lng,
        radius_m=radius_m,
        max_price=max_price,
        trend=trend,
    )


@router.get("/{slug}/summary", response_model=dict, summary="Evidence-based summary")
async def dish_summary(
    slug: str,
    session: DbSession,
    city: Annotated[str | None, Query(max_length=80)] = None,
) -> dict:
    city_row = await get_city(session, city)
    dish = await get_dish_by_slug(session, slug)
    detail = await get_dish_detail(session, dish=dish, city=city_row)

    if detail.summary is None:
        return {
            "status": "insufficient_data",
            "message": "Not enough data",
            "dish_slug": dish.slug,
        }
    return {"status": "ok", "dish_slug": dish.slug, **detail.summary.model_dump(mode="json")}
