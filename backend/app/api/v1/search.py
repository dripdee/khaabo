"""Search endpoints."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.api.deps import DbSession, rate_limit_read
from app.core.cache import cache_key, cached
from app.core.config import settings
from app.schemas.search import SearchFilters, SearchResponse, SuggestResponse
from app.services.search_service import get_search_backend

router = APIRouter(tags=["search"], dependencies=[Depends(rate_limit_read)])


@router.get("/search", response_model=SearchResponse, summary="Unified food search")
async def search(
    session: DbSession,
    filters: Annotated[SearchFilters, Depends()],
) -> SearchResponse:
    """Dish-first search.

    Natural language is parsed into structured intent (`best chicken momo under 300
    near salt lake` → dish + price ceiling + area), and explicit query parameters
    always override the parsed values.
    """
    backend = get_search_backend()

    # Location-personalized searches are not shared across users.
    if filters.lat is not None or filters.lng is not None:
        return await backend.search(session, filters)

    key = cache_key("search", **filters.model_dump(exclude_none=True))

    async def produce() -> dict:
        result = await backend.search(session, filters)
        return result.model_dump(mode="json")

    payload = await cached(key, settings.cache_ttl_search, produce)
    return SearchResponse.model_validate(payload)


@router.get("/search/suggest", response_model=SuggestResponse, summary="Typeahead")
async def suggest(
    session: DbSession,
    q: Annotated[str, Query(min_length=1, max_length=80)],
    city: Annotated[str | None, Query(max_length=80)] = None,
    limit: Annotated[int, Query(ge=1, le=10)] = 6,
) -> SuggestResponse:
    return await get_search_backend().suggest(session, q, city, limit)
