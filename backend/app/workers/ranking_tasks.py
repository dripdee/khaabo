"""Ranking, trend and summarization tasks."""

from __future__ import annotations

import uuid

from sqlalchemy import select

from app.core.cache import cache_delete_prefix
from app.core.logging import get_logger
from app.db.session import sync_session
from app.models import City, Dish, DishScore
from app.services import ranking_service
from app.workers.celery_app import celery_app

log = get_logger(__name__)


@celery_app.task(name="ranking.recompute_pairs", bind=True, max_retries=3, retry_backoff=30)
def recompute_pairs_task(self, pairs: list[list[str] | tuple[str, str]]) -> dict:
    """Incremental recompute for the exact pairs AI processing touched."""
    parsed = [(uuid.UUID(d), uuid.UUID(r)) for d, r in pairs]
    if not parsed:
        return {"pairs": 0}

    with sync_session() as session:
        summary = ranking_service.recompute_pairs(session, parsed)
        dish_slugs = _dish_slugs(session, {d for d, _ in parsed})

    _invalidate(dish_slugs)

    return {
        "pairs": summary.pairs,
        "ranked": summary.ranked,
        "insufficient": summary.insufficient,
        "dishes": summary.dishes,
        "restaurants": summary.restaurants,
    }


@celery_app.task(name="ranking.recompute_review", bind=True, max_retries=3)
def recompute_for_review(self, review_id: str) -> dict:
    """Used after a moderation decision changes a review's publish state."""
    with sync_session() as session:
        pairs = ranking_service.pairs_for_review(session, uuid.UUID(review_id))
        if not pairs:
            return {"pairs": 0}
        summary = ranking_service.recompute_pairs(session, pairs)
        dish_slugs = _dish_slugs(session, {d for d, _ in pairs})

    _invalidate(dish_slugs)
    return {"pairs": summary.pairs, "ranked": summary.ranked}


@celery_app.task(name="ranking.nightly_sweep")
def nightly_sweep(limit: int = 5000) -> dict:
    """Safety net for anything the incremental path missed."""
    with sync_session() as session:
        pairs = ranking_service.stale_pairs(session, limit=limit)
        if not pairs:
            return {"pairs": 0}
        summary = ranking_service.recompute_pairs(session, pairs)
        dish_slugs = _dish_slugs(session, {d for d, _ in pairs})

    _invalidate(dish_slugs)
    log.info("nightly_sweep_done", pairs=summary.pairs)
    return {"pairs": summary.pairs, "ranked": summary.ranked}


@celery_app.task(name="ranking.recompute_trends")
def recompute_trends_task(city_slug: str | None = None) -> dict:
    with sync_session() as session:
        city_id = None
        if city_slug:
            city_id = session.execute(
                select(City.id).where(City.slug == city_slug)
            ).scalar_one_or_none()
        written = ranking_service.recompute_trends(session, city_id)

    _invalidate([])
    return {"written": written}


@celery_app.task(name="ranking.full_recompute")
def full_recompute(weights_version: str | None = None) -> dict:
    """One-off after a weight change. Every score records its weights_version, so a
    mixed state is detectable — but a full pass keeps rankings comparable."""
    with sync_session() as session:
        rows = session.execute(select(DishScore.dish_id, DishScore.restaurant_id)).all()
        pairs = [(r.dish_id, r.restaurant_id) for r in rows]
        summary = ranking_service.recompute_pairs(session, pairs)
        dish_slugs = _dish_slugs(session, {d for d, _ in pairs})

    _invalidate(dish_slugs)
    log.info("full_recompute_done", pairs=summary.pairs, weights_version=weights_version)
    return {"pairs": summary.pairs}


@celery_app.task(name="summarization.rebuild_dish_summaries")
def rebuild_dish_summaries(city_slug: str | None = None, limit: int = 200) -> dict:
    """Summaries are cheap to rebuild and are always derived from stored evidence."""
    from app.services.summaries import build_dish_summary

    built = 0
    with sync_session() as session:
        cities = (
            session.execute(
                select(City).where(
                    City.active.is_(True),
                    *([City.slug == city_slug] if city_slug else []),
                )
            )
            .scalars()
            .all()
        )

        for city in cities:
            dish_ids = (
                session.execute(
                    select(DishScore.dish_id)
                    .where(DishScore.city_id == city.id)
                    .group_by(DishScore.dish_id)
                    .limit(limit)
                )
                .scalars()
                .all()
            )

            for dish_id in dish_ids:
                if build_dish_summary(session, dish_id, city.id) is not None:
                    built += 1

    return {"built": built}


def _dish_slugs(session, dish_ids: set[uuid.UUID]) -> list[str]:
    if not dish_ids:
        return []
    return list(session.execute(select(Dish.slug).where(Dish.id.in_(dish_ids))).scalars().all())


def _invalidate(dish_slugs: list[str]) -> None:
    """Targeted cache invalidation.

    Runs in a throwaway event loop because Celery tasks are sync; failures are
    swallowed since a stale cache entry expires on its own TTL anyway.
    """
    import asyncio

    async def _run() -> None:
        for slug in dish_slugs:
            await cache_delete_prefix(f"dish:{slug}")
        await cache_delete_prefix("search:")
        await cache_delete_prefix("trending:")

    try:
        asyncio.run(_run())
    except Exception as exc:  # noqa: BLE001
        log.warning("cache_invalidate_skipped", error=str(exc))
