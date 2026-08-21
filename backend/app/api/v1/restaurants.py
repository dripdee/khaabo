"""Restaurant endpoints."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select

from app.api.deps import DbSession, OptionalUser, PaginationDep, rate_limit_read
from app.models import Restaurant, RestaurantScore
from app.schemas.dish import CityMapPointOut, FoodDnaOut, RestaurantBrief, RestaurantDetailOut
from app.services.dish_service import get_city
from app.services.restaurant_service import (
    get_food_dna,
    get_restaurant,
    get_restaurant_detail,
    list_restaurant_dishes,
    list_restaurant_reviews,
)

router = APIRouter(
    prefix="/restaurants", tags=["restaurants"], dependencies=[Depends(rate_limit_read)]
)


@router.get("", response_model=dict, summary="List restaurants")
async def list_restaurants(
    session: DbSession,
    pagination: PaginationDep,
    city: Annotated[str | None, Query(max_length=80)] = None,
    cuisine: Annotated[str | None, Query(max_length=80)] = None,
    area: Annotated[str | None, Query(max_length=120)] = None,
    q: Annotated[str | None, Query(max_length=120)] = None,
) -> dict:
    city_row = await get_city(session, city)

    stmt = select(Restaurant).where(
        Restaurant.city_id == city_row.id, Restaurant.is_closed.is_(False)
    )
    if cuisine:
        stmt = stmt.where(Restaurant.cuisines.any(cuisine))
    if area:
        stmt = stmt.where(Restaurant.area.ilike(f"%{area}%"))
    if q:
        stmt = stmt.where(Restaurant.name.ilike(f"%{q}%"))

    total = (await session.execute(select(func.count()).select_from(stmt.subquery()))).scalar_one()

    rows = (
        (
            await session.execute(
                stmt.outerjoin(RestaurantScore, RestaurantScore.restaurant_id == Restaurant.id)
                .order_by(RestaurantScore.overall_score.desc().nulls_last(), Restaurant.name)
                .offset(pagination.offset)
                .limit(pagination.page_size)
            )
        )
        .scalars()
        .all()
    )

    return {
        "items": [
            RestaurantBrief(
                id=str(r.id),
                name=r.name,
                slug=r.slug,
                area=r.area,
                lat=float(r.lat),
                lng=float(r.lng),
                cuisines=list(r.cuisines or []),
                price_level=r.price_level,
                google_rating=float(r.google_rating) if r.google_rating is not None else None,
                google_rating_count=r.google_rating_count,
            ).model_dump(mode="json")
            for r in rows
        ],
        "page": pagination.page,
        "page_size": pagination.page_size,
        "total": int(total),
        "has_more": pagination.page * pagination.page_size < int(total),
        "city_slug": city_row.slug,
        "attribution": ["© OpenStreetMap contributors"],
    }


@router.get("/locations", response_model=dict, summary="Dots for the city-wide map")
async def restaurant_locations(
    session: DbSession,
    city: Annotated[str | None, Query(max_length=80)] = None,
) -> dict:
    """Slim location list for the whole-city map — no reviews, no scores.

    Declared before `/{restaurant_id}` so the literal path is not shadowed by
    the parameter route.
    """
    city_row = await get_city(session, city)

    rows = (
        await session.execute(
            select(
                Restaurant.id,
                Restaurant.name,
                Restaurant.slug,
                Restaurant.lat,
                Restaurant.lng,
                Restaurant.google_rating,
                Restaurant.google_rating_count,
            )
            .where(Restaurant.city_id == city_row.id, Restaurant.is_closed.is_(False))
            .order_by(Restaurant.name)
        )
    ).all()

    return {
        "city_slug": city_row.slug,
        "items": [
            CityMapPointOut(
                id=str(r.id),
                name=r.name,
                slug=r.slug,
                lat=float(r.lat),
                lng=float(r.lng),
                google_rating=float(r.google_rating) if r.google_rating is not None else None,
                google_rating_count=r.google_rating_count,
            ).model_dump(mode="json")
            for r in rows
        ],
        "attribution": ["© OpenStreetMap contributors", "Ratings: Google"],
    }


@router.get("/{restaurant_id}", response_model=RestaurantDetailOut, summary="Restaurant detail")
async def restaurant_detail(restaurant_id: str, session: DbSession) -> RestaurantDetailOut:
    restaurant = await get_restaurant(session, restaurant_id)
    return await get_restaurant_detail(session, restaurant)


@router.get(
    "/{restaurant_id}/food-dna",
    response_model=FoodDnaOut,
    summary="Evidence-derived Food DNA",
)
async def restaurant_food_dna(restaurant_id: str, session: DbSession) -> FoodDnaOut:
    """Chips are derived from actual dish mentions, aspects and prices.

    An empty `chips` list with `status="insufficient_data"` is the honest answer for
    a restaurant with little evidence — no manual tags are invented to fill it.
    """
    restaurant = await get_restaurant(session, restaurant_id)
    return await get_food_dna(session, restaurant)


@router.get("/{restaurant_id}/dishes", response_model=dict, summary="Dishes ranked here")
async def restaurant_dishes(
    restaurant_id: str, session: DbSession, pagination: PaginationDep
) -> dict:
    restaurant = await get_restaurant(session, restaurant_id)
    items, total = await list_restaurant_dishes(
        session, restaurant, page=pagination.page, page_size=pagination.page_size
    )
    return {
        "restaurant_id": str(restaurant.id),
        "items": [item.model_dump(mode="json") for item in items],
        "page": pagination.page,
        "page_size": pagination.page_size,
        "total": total,
        "has_more": pagination.page * pagination.page_size < total,
    }


@router.get("/{restaurant_id}/reviews", response_model=dict, summary="Published reviews")
async def restaurant_reviews(
    restaurant_id: str,
    session: DbSession,
    pagination: PaginationDep,
    user: OptionalUser = None,
) -> dict:
    restaurant = await get_restaurant(session, restaurant_id)
    items, total = await list_restaurant_reviews(
        session,
        restaurant,
        page=pagination.page,
        page_size=pagination.page_size,
        viewer_id=user.id if user else None,
    )
    return {
        "restaurant_id": str(restaurant.id),
        "items": [item.model_dump(mode="json") for item in items],
        "page": pagination.page,
        "page_size": pagination.page_size,
        "total": total,
        "has_more": pagination.page * pagination.page_size < total,
    }
