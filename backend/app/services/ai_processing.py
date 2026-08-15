"""AI processing: turn a stored review into dish-level observations.

Idempotent by construction: a reprocess deletes the review's prior mentions and
aspects inside the same transaction before inserting new ones, so a retry can never
double-count evidence.

Returns the dirty `(dish_id, restaurant_id)` pairs so ranking can be incremental.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.ai import ReviewAnalysisRequest, enforce_grounding, get_provider
from app.ai.schemas import ReviewAnalysis
from app.core.config import settings
from app.core.logging import get_logger
from app.models import (
    AIProcessingJob,
    Dish,
    DishAlias,
    ModerationQueueItem,
    Restaurant,
    Review,
    ReviewAspect,
    ReviewDishMention,
)
from app.models.enums import (
    AIState,
    DishCategory,
    ExtractionMethod,
    JobStatus,
    ModerationReason,
    ReviewStatus,
    SourceType,
)
from app.services.dedup import is_near_duplicate, simhash, to_signed_64
from app.utils.text import normalize_name, slugify

log = get_logger(__name__)

MAX_ATTEMPTS = 5
NEAR_DUPE_CANDIDATE_LIMIT = 40


@dataclass(slots=True)
class ProcessResult:
    review_id: str
    mentions: int = 0
    aspects: int = 0
    pairs: set[tuple[uuid.UUID, uuid.UUID]] = field(default_factory=set)
    provider: str = "heuristic"
    model: str | None = None
    spam: bool = False
    duplicate: bool = False
    latency_ms: int = 0
    degraded: bool = False


def load_alias_index(session: Session) -> dict[str, str]:
    """normalized alias → dish slug.

    Includes canonical dish names so a dish is matchable even without an explicit
    alias row.
    """
    index: dict[str, str] = {}

    for name, slug in session.execute(
        select(Dish.normalized_name, Dish.slug).where(Dish.is_active.is_(True))
    ).all():
        if name:
            index[name] = slug

    for alias, slug in session.execute(
        select(DishAlias.normalized_alias, Dish.slug).join(Dish, Dish.id == DishAlias.dish_id)
    ).all():
        if alias:
            index[alias] = slug

    return index


def claim_pending_reviews(session: Session, limit: int = 20) -> list[Review]:
    """Claim work safely across concurrent workers.

    `FOR UPDATE SKIP LOCKED` is what makes horizontal scaling of the worker safe;
    without it two workers would process the same review and race on writes.
    """
    reviews = (
        session.execute(
            select(Review)
            .where(Review.ai_state == AIState.PENDING, Review.ai_attempts < MAX_ATTEMPTS)
            .order_by(Review.ingested_at.nulls_last(), Review.created_at)
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
        .scalars()
        .all()
    )

    for review in reviews:
        review.ai_state = AIState.PROCESSING
        review.ai_attempts = (review.ai_attempts or 0) + 1
    session.flush()
    return list(reviews)


async def process_review(session: Session, review: Review) -> ProcessResult:
    """Analyze one review and persist its observations."""
    started = time.perf_counter()
    restaurant = session.get(Restaurant, review.restaurant_id)
    alias_index = load_alias_index(session)

    known_dishes = (
        session.execute(select(Dish.name).where(Dish.is_active.is_(True)).limit(400))
        .scalars()
        .all()
    )

    request = ReviewAnalysisRequest(
        review_id=str(review.id),
        text=review.body,
        title=review.title,
        lang_hint=review.lang if review.lang != "en" else None,
        restaurant_name=restaurant.name if restaurant else None,
        known_dishes=list(known_dishes),
        alias_index=alias_index,
        rating=float(review.rating) if review.rating is not None else None,
        rating_scale=float(review.rating_scale) if review.rating_scale else None,
    )

    provider = get_provider()
    analysis = await provider.analyze_review(request)
    analysis = enforce_grounding(analysis, request)

    result = _persist_analysis(session, review, analysis, alias_index)
    result.latency_ms = int((time.perf_counter() - started) * 1000)

    session.add(
        AIProcessingJob(
            review_id=review.id,
            status=JobStatus.SUCCESS,
            attempt=review.ai_attempts or 1,
            provider=analysis.provider,
            model=analysis.model,
            latency_ms=result.latency_ms,
            mentions_created=result.mentions,
            payload={
                "dish_count": result.mentions,
                "spam_score": analysis.spam_score,
                "degraded": analysis.degraded,
            },
            finished_at=datetime.now(UTC),
        )
    )
    session.flush()
    return result


def _persist_analysis(
    session: Session,
    review: Review,
    analysis: ReviewAnalysis,
    alias_index: dict[str, str],
) -> ProcessResult:
    result = ProcessResult(
        review_id=str(review.id),
        provider=analysis.provider,
        model=analysis.model,
        spam=analysis.is_spam,
        degraded=analysis.degraded,
    )

    # Idempotency: clear prior derived rows for this review before re-inserting.
    # Pairs that existed before must still be recomputed even if they vanish now,
    # otherwise a corrected extraction would leave a stale score behind.
    previous_pairs = set(
        session.execute(
            select(ReviewDishMention.dish_id, ReviewDishMention.restaurant_id).where(
                ReviewDishMention.review_id == review.id
            )
        ).all()
    )
    session.execute(delete(ReviewAspect).where(ReviewAspect.review_id == review.id))
    session.execute(delete(ReviewDishMention).where(ReviewDishMention.review_id == review.id))
    result.pairs.update((d, r) for d, r in previous_pairs)

    review.lang = analysis.language or review.lang
    review.spam_score = analysis.spam_score
    review.overall_sentiment = analysis.overall_sentiment
    review.value_signal = analysis.value_signal
    review.simhash = to_signed_64(simhash(review.body))

    duplicate = _flag_near_duplicate(session, review)
    result.duplicate = duplicate

    if analysis.is_spam:
        _queue_moderation(session, review, ModerationReason.SPAM, severity=2)
        if review.source is SourceType.USER:
            review.status = ReviewStatus.FLAGGED

    for mention in analysis.dish_mentions:
        dish = _resolve_dish(session, mention.dish_name, mention.matched_alias, alias_index)
        if dish is None:
            continue

        row = ReviewDishMention(
            review_id=review.id,
            dish_id=dish.id,
            restaurant_id=review.restaurant_id,
            snippet=mention.snippet,
            sentiment=mention.sentiment,
            confidence=mention.confidence,
            attributes=mention.attributes,
            price_mentioned=mention.price_mentioned,
            is_recommended=mention.is_recommended,
            extraction_method=(
                ExtractionMethod.AI if analysis.provider != "heuristic" else ExtractionMethod.ALIAS
            ),
        )
        session.add(row)
        session.flush()
        result.mentions += 1
        result.pairs.add((dish.id, review.restaurant_id))

        for aspect in mention.aspects:
            session.add(
                ReviewAspect(
                    review_id=review.id,
                    dish_mention_id=row.id,
                    aspect=aspect.aspect,
                    sentiment=aspect.sentiment,
                    confidence=aspect.confidence,
                    snippet=aspect.snippet,
                )
            )
            result.aspects += 1

    for aspect in analysis.aspects:
        session.add(
            ReviewAspect(
                review_id=review.id,
                aspect=aspect.aspect,
                sentiment=aspect.sentiment,
                confidence=aspect.confidence,
                snippet=aspect.snippet,
            )
        )
        result.aspects += 1

    review.ai_state = AIState.DONE
    session.flush()
    return result


def _resolve_dish(
    session: Session,
    dish_name: str,
    matched_alias: str | None,
    alias_index: dict[str, str],
) -> Dish | None:
    """Map an extracted name to a canonical dish.

    Creation is off by default (`AI_ALLOW_DISH_CREATION`): an unrecognised dish is
    dropped rather than silently expanding the taxonomy from model output.
    """
    for candidate in (matched_alias, dish_name):
        if not candidate:
            continue
        normalized = normalize_name(candidate)
        slug = alias_index.get(normalized)
        if slug:
            dish = session.execute(select(Dish).where(Dish.slug == slug)).scalar_one_or_none()
            if dish is not None:
                return dish

    if not settings.ai_allow_dish_creation:
        return None

    name = (dish_name or "").strip()
    if len(name) < 3:
        return None

    slug = slugify(name)
    existing = session.execute(select(Dish).where(Dish.slug == slug)).scalar_one_or_none()
    if existing is not None:
        return existing

    dish = Dish(
        name=name.title(),
        slug=slug,
        normalized_name=normalize_name(name),
        category=DishCategory.OTHER,
    )
    session.add(dish)
    session.flush()
    log.info("dish_created_from_extraction", dish=dish.slug)
    return dish


def _flag_near_duplicate(session: Session, review: Review) -> bool:
    """Compare against recent reviews for the same restaurant.

    Duplicates are kept for audit but excluded from ranking, and user-submitted ones
    go to moderation rather than being deleted outright.
    """
    candidates = session.execute(
        select(Review.id, Review.simhash, Review.body)
        .where(
            Review.restaurant_id == review.restaurant_id,
            Review.id != review.id,
            Review.simhash.isnot(None),
        )
        .order_by(Review.published_at.desc())
        .limit(NEAR_DUPE_CANDIDATE_LIMIT)
    ).all()

    verdict = is_near_duplicate(
        review.body,
        [(str(row.id), int(row.simhash), row.body) for row in candidates],
    )
    if not verdict.is_duplicate:
        return False

    review.is_duplicate = True
    _queue_moderation(session, review, ModerationReason.DUPLICATE, severity=1)
    log.info(
        "near_duplicate_flagged",
        review_id=str(review.id),
        matched=verdict.matched_id,
        similarity=verdict.similarity,
    )
    return True


def _queue_moderation(
    session: Session, review: Review, reason: ModerationReason, *, severity: int = 1
) -> None:
    exists = session.execute(
        select(ModerationQueueItem.id).where(
            ModerationQueueItem.review_id == review.id,
            ModerationQueueItem.reason == reason,
        )
    ).first()
    if exists:
        return
    session.add(
        ModerationQueueItem(
            review_id=review.id,
            reason=reason,
            severity=severity,
            history=[
                {
                    "at": datetime.now(UTC).isoformat(),
                    "actor": "system",
                    "from": None,
                    "to": "open",
                    "reason": reason.value,
                }
            ],
        )
    )


def mark_failed(session: Session, review: Review, error: str) -> None:
    review.ai_state = AIState.FAILED if review.ai_attempts >= MAX_ATTEMPTS else AIState.PENDING
    session.add(
        AIProcessingJob(
            review_id=review.id,
            status=JobStatus.FAILED,
            attempt=review.ai_attempts or 1,
            error=error[:2000],
            finished_at=datetime.now(UTC),
        )
    )
    log.warning(
        "ai_processing_failed",
        review_id=str(review.id),
        attempt=review.ai_attempts,
        error=error[:300],
    )
