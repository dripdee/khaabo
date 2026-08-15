"""Review submission and engagement endpoints."""

from __future__ import annotations

import contextlib
from typing import Annotated

from fastapi import APIRouter, Depends, Query, status

from app.api.deps import CurrentUser, DbSession, ModeratorUser, OptionalUser, rate_limit_write
from app.core.rate_limit import check_review_limits
from app.models.enums import ReviewStatus
from app.schemas.review import (
    LikeOut,
    LikeRequest,
    ReviewCreate,
    ReviewCreatedOut,
    ReviewOut,
    ReviewReport,
)
from app.services import review_service

router = APIRouter(tags=["reviews"])


@router.post(
    "/reviews",
    response_model=ReviewCreatedOut,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Submit a review",
)
async def create_review(
    payload: ReviewCreate,
    session: DbSession,
    user: CurrentUser,
    _: Annotated[None, Depends(rate_limit_write)] = None,
) -> ReviewCreatedOut:
    """Accepted for asynchronous processing.

    AI extraction and ranking happen in the background, so the response is 202 and
    the review may be `pending` until moderation clears it. Per-user hourly and
    daily caps are enforced before any write.
    """
    await check_review_limits(str(user.id))
    result = await review_service.create_review(session, user=user, payload=payload)

    # Commit before enqueueing so the worker cannot read a row that does not exist yet.
    await session.commit()

    from app.workers.ai_tasks import process_review_task

    # A broker outage must not fail the submission. The periodic
    # `ai.process_pending` sweep picks up anything that was not enqueued.
    with contextlib.suppress(Exception):
        process_review_task.delay(result.id)

    return result


@router.get("/reviews/{review_id}", response_model=ReviewOut, summary="Get one review")
async def get_review(
    review_id: str,
    session: DbSession,
    user: OptionalUser = None,
) -> ReviewOut:
    from app.core.errors import ForbiddenError
    from app.models.enums import UserRole
    from app.services.restaurant_service import get_restaurant, list_restaurant_reviews

    review = await review_service.get_review_or_404(session, review_id)

    is_owner = user is not None and review.user_id == user.id
    is_moderator = user is not None and user.role in {UserRole.MODERATOR, UserRole.ADMIN}
    if review.status is not ReviewStatus.PUBLISHED and not (is_owner or is_moderator):
        raise ForbiddenError("This review is not publicly visible")

    restaurant = await get_restaurant(session, str(review.restaurant_id))
    items, _ = await list_restaurant_reviews(
        session, restaurant, page=1, page_size=100, viewer_id=user.id if user else None
    )
    match = next((item for item in items if item.id == str(review.id)), None)
    if match is not None:
        return match

    return ReviewOut(
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
        status=review.status.value,
        published_at=review.published_at,
    )


@router.delete("/reviews/{review_id}", response_model=dict, summary="Delete own review")
async def delete_review(review_id: str, session: DbSession, user: CurrentUser) -> dict:
    result = await review_service.delete_review(session, review_id=review_id, user=user)
    await session.commit()
    _enqueue_recompute(review_id)
    return result


@router.post("/reviews/{review_id}/report", response_model=dict, summary="Report a review")
async def report_review(
    review_id: str,
    payload: ReviewReport,
    session: DbSession,
    user: CurrentUser,
    _: Annotated[None, Depends(rate_limit_write)] = None,
) -> dict:
    return await review_service.report_review(
        session, review_id=review_id, user=user, reason=payload.reason, note=payload.note
    )


@router.post("/likes", response_model=LikeOut, summary="Like or unlike a review")
async def like_review(
    payload: LikeRequest,
    session: DbSession,
    user: CurrentUser,
    _: Annotated[None, Depends(rate_limit_write)] = None,
) -> LikeOut:
    """Idempotent toggle, so optimistic UI updates reconcile cleanly."""
    result = await review_service.toggle_like(session, user=user, review_id=payload.review_id)
    return LikeOut(**result)


@router.delete("/likes/{review_id}", response_model=LikeOut, summary="Remove a like")
async def unlike_review(review_id: str, session: DbSession, user: CurrentUser) -> LikeOut:
    from sqlalchemy import select

    from app.models import Like

    existing = (
        await session.execute(
            select(Like).where(Like.user_id == user.id, Like.review_id == review_id)
        )
    ).scalar_one_or_none()

    if existing is None:
        review = await review_service.get_review_or_404(session, review_id)
        return LikeOut(review_id=review_id, liked=False, like_count=int(review.like_count or 0))

    result = await review_service.toggle_like(session, user=user, review_id=review_id)
    return LikeOut(**result)


moderation_router = APIRouter(prefix="/moderation", tags=["moderation"])


@moderation_router.get("/queue", response_model=dict, summary="Moderation queue")
async def moderation_queue(
    session: DbSession,
    moderator: ModeratorUser,
    status_filter: Annotated[
        str, Query(alias="status", pattern="^(open|resolved|dismissed)$")
    ] = "open",
    page: Annotated[int, Query(ge=1, le=200)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 25,
) -> dict:
    from sqlalchemy import func, select

    from app.models import ModerationQueueItem, Review
    from app.models.enums import ModerationStatus
    from app.schemas.review import ModerationItemOut

    stmt = (
        select(ModerationQueueItem, Review)
        .join(Review, Review.id == ModerationQueueItem.review_id)
        .where(ModerationQueueItem.status == ModerationStatus(status_filter))
        .order_by(ModerationQueueItem.severity.desc(), ModerationQueueItem.created_at.asc())
    )

    total = (await session.execute(select(func.count()).select_from(stmt.subquery()))).scalar_one()

    rows = (await session.execute(stmt.offset((page - 1) * page_size).limit(page_size))).all()

    return {
        "items": [
            ModerationItemOut(
                id=str(item.id),
                review_id=str(item.review_id),
                reason=item.reason.value,
                status=item.status.value,
                severity=item.severity,
                review_body=review.body[:600],
                review_status=review.status.value,
                spam_score=float(review.spam_score),
                is_duplicate=review.is_duplicate,
                created_at=item.created_at,
                history=list(item.history or []),
            ).model_dump(mode="json")
            for item, review in rows
        ],
        "page": page,
        "page_size": page_size,
        "total": int(total),
        "has_more": page * page_size < int(total),
    }


@moderation_router.post("/{item_id}/decide", response_model=dict, summary="Decide an item")
async def decide(
    item_id: str,
    payload: dict,
    session: DbSession,
    moderator: ModeratorUser,
) -> dict:
    """Publishing or rejecting changes which evidence counts, so ranking is requeued."""
    from app.schemas.review import ModerationDecision

    decision = ModerationDecision.model_validate(payload)
    result = await review_service.decide_moderation(
        session,
        item_id=item_id,
        moderator=moderator,
        action=decision.action,
        note=decision.note,
    )
    await session.commit()
    _enqueue_recompute(result["review_id"])
    return result


def _enqueue_recompute(review_id: str) -> None:
    from app.workers.ranking_tasks import recompute_for_review

    # The nightly sweep is the fallback if the broker is unavailable.
    with contextlib.suppress(Exception):
        recompute_for_review.delay(review_id)
