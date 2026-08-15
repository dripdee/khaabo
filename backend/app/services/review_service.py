"""Review submission, moderation and engagement.

Business rules live here, not in route handlers, so the same logic serves the API,
the admin endpoints and any CLI/backfill script.

Submission flow: validate → dedupe → store as `pending` → enqueue AI → moderate.
A user's review is never published on the strength of its own author's word.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import DuplicateReviewError, ForbiddenError, NotFoundError, ValidationError
from app.core.logging import get_logger
from app.models import (
    Like,
    ModerationQueueItem,
    Profile,
    Restaurant,
    Review,
    ReviewDishMention,
    User,
)
from app.models.enums import (
    AIState,
    ModerationReason,
    ModerationStatus,
    ReviewStatus,
    SourceType,
    UserRole,
)
from app.schemas.review import ReviewCreate, ReviewCreatedOut
from app.services.dedup import review_fingerprint, simhash, spam_score, to_signed_64
from app.services.gamification import award_for_review
from app.services.ranking import source_quality_for

log = get_logger(__name__)

AUTO_FLAG_SPAM_THRESHOLD = 0.6
# Established contributors skip the queue; new accounts do not.
AUTO_PUBLISH_MIN_PUBLISHED = 3


async def create_review(
    session: AsyncSession,
    *,
    user: User,
    payload: ReviewCreate,
) -> ReviewCreatedOut:
    if user.is_banned:
        raise ForbiddenError("This account cannot submit reviews")

    try:
        restaurant_id = uuid.UUID(payload.restaurant_id)
    except ValueError as exc:
        raise ValidationError("restaurant_id must be a UUID") from exc

    restaurant = (
        await session.execute(select(Restaurant).where(Restaurant.id == restaurant_id))
    ).scalar_one_or_none()
    if restaurant is None:
        raise NotFoundError("Unknown restaurant")

    fingerprint = review_fingerprint(payload.body, str(user.id), None)
    existing = (
        await session.execute(select(Review.id).where(Review.content_hash == fingerprint))
    ).first()
    if existing:
        raise DuplicateReviewError()

    spam = spam_score(payload.body)
    profile = (
        await session.execute(select(Profile).where(Profile.user_id == user.id))
    ).scalar_one_or_none()

    trusted = bool(profile and profile.published_review_count >= AUTO_PUBLISH_MIN_PUBLISHED)
    flagged = spam >= AUTO_FLAG_SPAM_THRESHOLD

    if flagged:
        status = ReviewStatus.FLAGGED
    elif trusted:
        status = ReviewStatus.PUBLISHED
    else:
        status = ReviewStatus.PENDING

    review = Review(
        restaurant_id=restaurant.id,
        city_id=restaurant.city_id,
        user_id=user.id,
        source=SourceType.USER,
        title=payload.title,
        body=payload.body,
        rating=payload.rating,
        rating_scale=5 if payload.rating is not None else None,
        source_quality=source_quality_for(SourceType.USER.value, verified=trusted),
        published_at=datetime.now(UTC),
        ingested_at=datetime.now(UTC),
        content_hash=fingerprint,
        simhash=to_signed_64(simhash(payload.body)),
        status=status,
        ai_state=AIState.PENDING,
        spam_score=spam,
    )
    session.add(review)
    await session.flush()

    if flagged:
        await _queue_moderation(session, review, ModerationReason.SPAM, severity=2)
    elif status is ReviewStatus.PENDING:
        await _queue_moderation(session, review, ModerationReason.MANUAL, severity=1)

    if profile is not None:
        profile.review_count = (profile.review_count or 0) + 1
        if status is ReviewStatus.PUBLISHED:
            profile.published_review_count = (profile.published_review_count or 0) + 1

    if status is ReviewStatus.PUBLISHED:
        restaurant.review_count = (restaurant.review_count or 0) + 1
        await award_for_review(session, user, review, payload.dish_hints)

    log.info(
        "review_submitted",
        review_id=str(review.id),
        status=status.value,
        spam_score=spam,
        trusted=trusted,
    )

    return ReviewCreatedOut(
        id=str(review.id),
        status=status.value,
        ai_state=review.ai_state.value,
        moderation={
            "status": status.value,
            "queued": status is not ReviewStatus.PUBLISHED,
            "eta_seconds": 120 if status is ReviewStatus.PUBLISHED else None,
        },
        message=(
            "Published. Rankings will update within a couple of minutes."
            if status is ReviewStatus.PUBLISHED
            else "Received. It will appear once a moderator approves it."
        ),
    )


async def _queue_moderation(
    session: AsyncSession,
    review: Review,
    reason: ModerationReason,
    *,
    severity: int = 1,
    reporter_id: uuid.UUID | None = None,
    note: str | None = None,
) -> None:
    existing = (
        await session.execute(
            select(ModerationQueueItem).where(
                ModerationQueueItem.review_id == review.id,
                ModerationQueueItem.reason == reason,
            )
        )
    ).scalar_one_or_none()

    entry = {
        "at": datetime.now(UTC).isoformat(),
        "actor": str(reporter_id) if reporter_id else "system",
        "from": None,
        "to": ModerationStatus.OPEN.value,
        "reason": reason.value,
        "note": note,
    }

    if existing is not None:
        # Repeated reports raise severity rather than creating duplicate rows.
        existing.severity = min(5, existing.severity + 1)
        existing.status = ModerationStatus.OPEN
        existing.history = [*(existing.history or []), entry]
        return

    session.add(
        ModerationQueueItem(
            review_id=review.id,
            reason=reason,
            severity=severity,
            reporter_user_id=reporter_id,
            notes=note,
            history=[entry],
        )
    )


async def report_review(
    session: AsyncSession,
    *,
    review_id: str,
    user: User,
    reason: ModerationReason,
    note: str | None,
) -> dict:
    review = await get_review_or_404(session, review_id)
    review.report_count = (review.report_count or 0) + 1

    # Enough independent reports hide the content pending review. This is a
    # deliberate trade: brief false-positive hiding beats leaving abuse visible.
    if review.report_count >= 3 and review.status is ReviewStatus.PUBLISHED:
        review.status = ReviewStatus.FLAGGED

    await _queue_moderation(
        session,
        review,
        reason,
        severity=2,
        reporter_id=user.id,
        note=note,
    )
    return {"status": "reported", "review_id": str(review.id)}


async def toggle_like(session: AsyncSession, *, user: User, review_id: str) -> dict:
    """Idempotent toggle so the frontend can apply optimistic updates safely."""
    review = await get_review_or_404(session, review_id)
    if review.status is not ReviewStatus.PUBLISHED:
        raise ForbiddenError("Only published reviews can be liked")

    existing = (
        await session.execute(
            select(Like).where(Like.user_id == user.id, Like.review_id == review.id)
        )
    ).scalar_one_or_none()

    if existing is not None:
        await session.delete(existing)
        review.like_count = max(0, (review.like_count or 0) - 1)
        liked = False
    else:
        session.add(Like(user_id=user.id, review_id=review.id))
        review.like_count = (review.like_count or 0) + 1
        liked = True

    if review.user_id and review.user_id != user.id:
        await session.execute(
            update(Profile)
            .where(Profile.user_id == review.user_id)
            .values(
                like_received_count=func.greatest(
                    0, Profile.like_received_count + (1 if liked else -1)
                )
            )
        )

    return {"review_id": str(review.id), "liked": liked, "like_count": int(review.like_count)}


async def decide_moderation(
    session: AsyncSession,
    *,
    item_id: str,
    moderator: User,
    action: str,
    note: str | None = None,
) -> dict:
    """Apply a moderation decision and keep full history.

    Returns the review id so the caller can trigger a ranking recompute — a
    publish/reject changes which evidence counts.
    """
    if moderator.role not in {UserRole.MODERATOR, UserRole.ADMIN}:
        raise ForbiddenError("Moderator role required")

    item = (
        await session.execute(
            select(ModerationQueueItem).where(ModerationQueueItem.id == uuid.UUID(item_id))
        )
    ).scalar_one_or_none()
    if item is None:
        raise NotFoundError("Unknown moderation item")

    review = await session.get(Review, item.review_id)
    if review is None:
        raise NotFoundError("Review no longer exists")

    previous = review.status

    if action == "publish":
        review.status = ReviewStatus.PUBLISHED
        item.status = ModerationStatus.RESOLVED
    elif action == "reject":
        review.status = ReviewStatus.REJECTED
        item.status = ModerationStatus.RESOLVED
    elif action == "flag":
        review.status = ReviewStatus.FLAGGED
        item.status = ModerationStatus.OPEN
    elif action == "dismiss":
        item.status = ModerationStatus.DISMISSED
    else:
        raise ValidationError(f"Unknown moderation action '{action}'")

    item.decided_by = moderator.id
    item.decided_at = datetime.now(UTC)
    item.notes = note or item.notes
    item.history = [
        *(item.history or []),
        {
            "at": datetime.now(UTC).isoformat(),
            "actor": str(moderator.id),
            "from": previous.value,
            "to": review.status.value,
            "action": action,
            "note": note,
        },
    ]

    if review.user_id:
        await _sync_author_counters(session, review.user_id)

    log.info(
        "moderation_decided",
        item_id=item_id,
        action=action,
        review_id=str(review.id),
        moderator=str(moderator.id),
    )
    return {"review_id": str(review.id), "status": review.status.value, "action": action}


async def _sync_author_counters(session: AsyncSession, user_id: uuid.UUID) -> None:
    """Recompute from rows rather than incrementing, so counters cannot drift."""
    published = (
        await session.execute(
            select(func.count())
            .select_from(Review)
            .where(Review.user_id == user_id, Review.status == ReviewStatus.PUBLISHED)
        )
    ).scalar_one()
    total = (
        await session.execute(
            select(func.count()).select_from(Review).where(Review.user_id == user_id)
        )
    ).scalar_one()

    await session.execute(
        update(Profile)
        .where(Profile.user_id == user_id)
        .values(published_review_count=int(published), review_count=int(total))
    )


async def get_review_or_404(session: AsyncSession, review_id: str) -> Review:
    try:
        parsed = uuid.UUID(review_id)
    except ValueError as exc:
        raise NotFoundError("Unknown review") from exc
    review = await session.get(Review, parsed)
    if review is None:
        raise NotFoundError("Unknown review")
    return review


async def delete_review(session: AsyncSession, *, review_id: str, user: User) -> dict:
    """Soft delete: reject rather than destroy, so moderation history survives."""
    review = await get_review_or_404(session, review_id)
    is_owner = review.user_id == user.id
    is_moderator = user.role in {UserRole.MODERATOR, UserRole.ADMIN}
    if not (is_owner or is_moderator):
        raise ForbiddenError("You can only delete your own reviews")

    review.status = ReviewStatus.REJECTED
    if review.user_id:
        await _sync_author_counters(session, review.user_id)
    return {"status": "deleted", "review_id": str(review.id)}


async def pairs_for_review(session: AsyncSession, review_id: uuid.UUID) -> list[tuple[str, str]]:
    rows = (
        await session.execute(
            select(ReviewDishMention.dish_id, ReviewDishMention.restaurant_id).where(
                ReviewDishMention.review_id == review_id
            )
        )
    ).all()
    return [(str(row.dish_id), str(row.restaurant_id)) for row in rows]
