"""Admin endpoints.

Every mutation records who did it. Entity merges and dish remaps exist because
automated resolution deliberately refuses ambiguous cases rather than guessing.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Query
from sqlalchemy import func, select, update

from app.api.deps import AdminUser, DbSession, ModeratorUser, PaginationDep
from app.core.errors import NotFoundError, ValidationError
from app.core.logging import get_logger
from app.models import (
    AIProcessingJob,
    Dish,
    DishScore,
    EntityConflict,
    IngestionJob,
    Restaurant,
    RestaurantAlias,
    RestaurantDish,
    RestaurantSource,
    Review,
    ReviewDishMention,
    ReviewSource,
)
from app.models.enums import ConflictStatus, JobStatus, ScoreStatus

log = get_logger(__name__)

router = APIRouter(prefix="/admin", tags=["admin"])


@router.get("/restaurants", response_model=dict, summary="Browse restaurants")
async def admin_restaurants(
    session: DbSession,
    moderator: ModeratorUser,
    pagination: PaginationDep,
    q: Annotated[str | None, Query(max_length=120)] = None,
    unverified_only: Annotated[bool, Query()] = False,
) -> dict:
    stmt = select(Restaurant)
    if q:
        stmt = stmt.where(Restaurant.name.ilike(f"%{q}%"))
    if unverified_only:
        stmt = stmt.where(Restaurant.is_verified.is_(False))

    total = (await session.execute(select(func.count()).select_from(stmt.subquery()))).scalar_one()
    rows = (
        (
            await session.execute(
                stmt.order_by(Restaurant.data_confidence.asc(), Restaurant.name)
                .offset(pagination.offset)
                .limit(pagination.page_size)
            )
        )
        .scalars()
        .all()
    )

    return {
        "items": [
            {
                "id": str(r.id),
                "name": r.name,
                "slug": r.slug,
                "area": r.area,
                "cuisines": list(r.cuisines or []),
                "review_count": int(r.review_count or 0),
                "data_confidence": float(r.data_confidence),
                "is_verified": r.is_verified,
                "is_closed": r.is_closed,
                "osm_id": r.osm_id,
            }
            for r in rows
        ],
        "page": pagination.page,
        "page_size": pagination.page_size,
        "total": int(total),
    }


@router.post("/restaurants/{restaurant_id}/merge", response_model=dict, summary="Merge duplicates")
async def merge_restaurants(
    restaurant_id: str,
    payload: dict,
    session: DbSession,
    admin: AdminUser,
) -> dict:
    """Merge `duplicate_id` into `restaurant_id`.

    Evidence is repointed rather than deleted, the losing name becomes an alias, and
    the duplicate is marked closed so it stops appearing without losing its history.
    Scores for both are cleared so the next recompute rebuilds them cleanly.
    """
    duplicate_id = payload.get("duplicate_id")
    if not duplicate_id:
        raise ValidationError("duplicate_id is required")

    target = await session.get(Restaurant, uuid.UUID(restaurant_id))
    duplicate = await session.get(Restaurant, uuid.UUID(duplicate_id))
    if target is None or duplicate is None:
        raise NotFoundError("Restaurant not found")
    if target.id == duplicate.id:
        raise ValidationError("Cannot merge a restaurant into itself")

    for model in (Review, ReviewDishMention, RestaurantSource):
        await session.execute(
            update(model).where(model.restaurant_id == duplicate.id).values(restaurant_id=target.id)
        )

    # RestaurantDish and DishScore are (restaurant, dish)-unique, so repointing could
    # collide. Delete and let the recompute rebuild from the moved evidence.
    from sqlalchemy import delete

    await session.execute(
        delete(RestaurantDish).where(RestaurantDish.restaurant_id == duplicate.id)
    )
    await session.execute(delete(DishScore).where(DishScore.restaurant_id == duplicate.id))
    await session.execute(delete(DishScore).where(DishScore.restaurant_id == target.id))

    session.add(
        RestaurantAlias(
            restaurant_id=target.id,
            alias=duplicate.name,
            normalized_alias=duplicate.normalized_name,
            confidence=1.0,
        )
    )

    duplicate.is_closed = True
    duplicate.name = f"{duplicate.name} (merged)"
    target.review_count = (
        await session.execute(
            select(func.count()).select_from(Review).where(Review.restaurant_id == target.id)
        )
    ).scalar_one()
    target.is_verified = True

    log.info(
        "restaurants_merged",
        target=str(target.id),
        duplicate=str(duplicate.id),
        actor=str(admin.id),
    )
    return {"status": "merged", "target_id": str(target.id), "duplicate_id": duplicate_id}


@router.patch(
    "/reviews/{review_id}/mentions/{mention_id}",
    response_model=dict,
    summary="Remap a dish mention",
)
async def remap_mention(
    review_id: str,
    mention_id: str,
    payload: dict,
    session: DbSession,
    moderator: ModeratorUser,
) -> dict:
    """Correct a wrong dish attribution by hand."""
    mention = await session.get(ReviewDishMention, uuid.UUID(mention_id))
    if mention is None or str(mention.review_id) != review_id:
        raise NotFoundError("Mention not found on that review")

    new_dish_slug = payload.get("dish_slug")
    if not new_dish_slug:
        raise ValidationError("dish_slug is required")

    dish = (
        await session.execute(select(Dish).where(Dish.slug == new_dish_slug))
    ).scalar_one_or_none()
    if dish is None:
        raise NotFoundError(f"Unknown dish '{new_dish_slug}'")

    old_dish_id = mention.dish_id
    mention.dish_id = dish.id

    log.info(
        "mention_remapped",
        mention_id=mention_id,
        from_dish=str(old_dish_id),
        to_dish=str(dish.id),
        actor=str(moderator.id),
    )
    return {
        "status": "remapped",
        "mention_id": mention_id,
        "dish_slug": dish.slug,
        "recompute": [str(old_dish_id), str(dish.id)],
    }


@router.get("/entity-conflicts", response_model=dict, summary="Unresolved entity conflicts")
async def entity_conflicts(
    session: DbSession,
    moderator: ModeratorUser,
    pagination: PaginationDep,
    status_filter: Annotated[str, Query(alias="status")] = "open",
) -> dict:
    stmt = select(EntityConflict).where(EntityConflict.status == ConflictStatus(status_filter))
    total = (await session.execute(select(func.count()).select_from(stmt.subquery()))).scalar_one()
    rows = (
        (
            await session.execute(
                stmt.order_by(EntityConflict.created_at.desc())
                .offset(pagination.offset)
                .limit(pagination.page_size)
            )
        )
        .scalars()
        .all()
    )

    return {
        "items": [
            {
                "id": str(row.id),
                "kind": row.kind.value,
                "candidate_a": str(row.candidate_a) if row.candidate_a else None,
                "candidate_b": str(row.candidate_b) if row.candidate_b else None,
                "similarity": float(row.similarity) if row.similarity is not None else None,
                "payload": row.payload,
                "created_at": row.created_at.isoformat(),
            }
            for row in rows
        ],
        "page": pagination.page,
        "page_size": pagination.page_size,
        "total": int(total),
    }


@router.post(
    "/entity-conflicts/{conflict_id}/resolve", response_model=dict, summary="Resolve a conflict"
)
async def resolve_conflict(
    conflict_id: str,
    payload: dict,
    session: DbSession,
    admin: AdminUser,
) -> dict:
    conflict = await session.get(EntityConflict, uuid.UUID(conflict_id))
    if conflict is None:
        raise NotFoundError("Unknown conflict")

    action = payload.get("action")
    if action not in {"merged", "rejected"}:
        raise ValidationError("action must be 'merged' or 'rejected'")

    conflict.status = ConflictStatus(action)
    conflict.resolved_by = admin.id
    conflict.resolved_at = datetime.now(UTC)
    return {"status": conflict.status.value, "id": conflict_id}


@router.get("/jobs/failed", response_model=dict, summary="Failed jobs")
async def failed_jobs(
    session: DbSession,
    moderator: ModeratorUser,
    pagination: PaginationDep,
) -> dict:
    ingestion = (
        (
            await session.execute(
                select(IngestionJob)
                .where(IngestionJob.status == JobStatus.FAILED)
                .order_by(IngestionJob.created_at.desc())
                .limit(pagination.page_size)
            )
        )
        .scalars()
        .all()
    )

    ai_jobs = (
        (
            await session.execute(
                select(AIProcessingJob)
                .where(AIProcessingJob.status == JobStatus.FAILED)
                .order_by(AIProcessingJob.created_at.desc())
                .limit(pagination.page_size)
            )
        )
        .scalars()
        .all()
    )

    return {
        "ingestion": [
            {
                "id": str(j.id),
                "source": j.source.value,
                "job_key": j.job_key,
                "attempt": j.attempt,
                "error": j.error,
                "created_at": j.created_at.isoformat(),
            }
            for j in ingestion
        ],
        "ai": [
            {
                "id": str(j.id),
                "review_id": str(j.review_id),
                "attempt": j.attempt,
                "provider": j.provider,
                "error": j.error,
                "created_at": j.created_at.isoformat(),
            }
            for j in ai_jobs
        ],
    }


@router.post("/jobs/{job_id}/retry", response_model=dict, summary="Retry a failed job")
async def retry_job(job_id: str, session: DbSession, admin: AdminUser) -> dict:
    from app.models.enums import AIState

    ingestion = await session.get(IngestionJob, uuid.UUID(job_id))
    if ingestion is not None:
        # A new job_key is required: the old one is consumed by the unique constraint
        # that provides idempotency.
        ingestion.status = JobStatus.QUEUED
        ingestion.attempt += 1
        ingestion.error = None
        return {"status": "queued", "kind": "ingestion", "id": job_id}

    ai_job = await session.get(AIProcessingJob, uuid.UUID(job_id))
    if ai_job is not None:
        review = await session.get(Review, ai_job.review_id)
        if review is not None:
            review.ai_state = AIState.PENDING
            review.ai_attempts = 0
        return {"status": "queued", "kind": "ai", "id": job_id, "review_id": str(ai_job.review_id)}

    raise NotFoundError("Unknown job")


@router.get("/source-records", response_model=dict, summary="Raw source records")
async def source_records(
    session: DbSession,
    moderator: ModeratorUser,
    pagination: PaginationDep,
    source: Annotated[str | None, Query()] = None,
) -> dict:
    stmt = select(ReviewSource)
    if source:
        from app.models.enums import SourceType

        stmt = stmt.where(ReviewSource.source == SourceType(source))

    rows = (
        (
            await session.execute(
                stmt.order_by(ReviewSource.created_at.desc())
                .offset(pagination.offset)
                .limit(pagination.page_size)
            )
        )
        .scalars()
        .all()
    )

    return {
        "items": [
            {
                "id": str(row.id),
                "review_id": str(row.review_id),
                "source": row.source.value,
                "external_id": row.external_id,
                "url": row.url,
                "license": row.license,
                "attribution": row.attribution,
            }
            for row in rows
        ]
    }


@router.get("/ai-outputs", response_model=dict, summary="Recent AI outputs")
async def ai_outputs(
    session: DbSession, moderator: ModeratorUser, pagination: PaginationDep
) -> dict:
    rows = (
        (
            await session.execute(
                select(AIProcessingJob)
                .order_by(AIProcessingJob.created_at.desc())
                .offset(pagination.offset)
                .limit(pagination.page_size)
            )
        )
        .scalars()
        .all()
    )

    return {
        "items": [
            {
                "id": str(j.id),
                "review_id": str(j.review_id),
                "status": j.status.value,
                "provider": j.provider,
                "model": j.model,
                "latency_ms": j.latency_ms,
                "mentions_created": j.mentions_created,
                "payload": j.payload,
                "created_at": j.created_at.isoformat(),
            }
            for j in rows
        ]
    }


@router.get("/ranking", response_model=dict, summary="Ranking overview")
async def ranking_overview(session: DbSession, moderator: ModeratorUser) -> dict:
    from app.core.config import settings

    ranked = (
        await session.execute(
            select(func.count())
            .select_from(DishScore)
            .where(DishScore.status == ScoreStatus.RANKED)
        )
    ).scalar_one()
    insufficient = (
        await session.execute(
            select(func.count())
            .select_from(DishScore)
            .where(DishScore.status == ScoreStatus.INSUFFICIENT_DATA)
        )
    ).scalar_one()
    pending_ai = (
        await session.execute(
            select(func.count()).select_from(Review).where(Review.ai_state == "pending")
        )
    ).scalar_one()

    return {
        "weights": settings.ranking_weights,
        "weights_version": settings.ranking_weights_version,
        "halflife_days": settings.ranking_halflife_days,
        "bayes_m": settings.ranking_bayes_m,
        "min_mentions": settings.ranking_min_mentions,
        "ranked_pairs": int(ranked),
        "insufficient_pairs": int(insufficient),
        "reviews_pending_ai": int(pending_ai),
    }


@router.post("/ranking/recompute", response_model=dict, summary="Force a recompute")
async def force_recompute(
    payload: dict,
    session: DbSession,
    admin: AdminUser,
) -> dict:
    from app.workers.ranking_tasks import full_recompute, nightly_sweep

    scope = payload.get("scope", "stale")
    try:
        task = full_recompute.delay() if scope == "all" else nightly_sweep.delay()
    except Exception as exc:  # noqa: BLE001
        raise ValidationError(f"Could not enqueue recompute: {exc}") from exc

    log.info("ranking_recompute_requested", scope=scope, actor=str(admin.id))
    return {"status": "queued", "scope": scope, "task_id": str(task.id)}
