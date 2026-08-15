"""AI processing tasks."""

from __future__ import annotations

import asyncio

from app.core.config import settings
from app.core.logging import get_logger
from app.db.session import sync_session
from app.services.ai_processing import claim_pending_reviews, mark_failed, process_review
from app.workers.celery_app import celery_app

log = get_logger(__name__)


@celery_app.task(name="ai.process_pending", bind=True, max_retries=3)
def process_pending(self, limit: int | None = None) -> dict:
    """Claim and process a batch of pending reviews, then trigger ranking.

    Each review is committed independently: one poisoned item must not roll back a
    whole batch of good work. Failures are recorded and retried by attempt count.
    """
    from app.workers.ranking_tasks import recompute_pairs_task

    batch = limit or settings.ai_batch_size
    processed = 0
    failed = 0
    dirty: set[tuple[str, str]] = set()

    with sync_session() as session:
        reviews = claim_pending_reviews(session, limit=batch)
        session.commit()

        for review in reviews:
            try:
                result = asyncio.run(process_review(session, review))
                session.commit()
                processed += 1
                dirty.update((str(d), str(r)) for d, r in result.pairs)
            except Exception as exc:  # noqa: BLE001 - one bad review must not stop the batch
                session.rollback()
                mark_failed(session, review, str(exc))
                session.commit()
                failed += 1

    if dirty:
        recompute_pairs_task.delay(sorted(dirty))

    log.info("ai_batch_done", processed=processed, failed=failed, pairs=len(dirty))
    return {"processed": processed, "failed": failed, "pairs": len(dirty)}


@celery_app.task(
    name="ai.process_review",
    bind=True,
    max_retries=5,
    retry_backoff=15,
    retry_backoff_max=900,
    retry_jitter=True,
)
def process_review_task(self, review_id: str) -> dict:
    """Process one specific review — used for near-real-time user submissions."""
    import uuid

    from app.models import Review
    from app.models.enums import AIState
    from app.workers.ranking_tasks import recompute_pairs_task

    with sync_session() as session:
        review = session.get(Review, uuid.UUID(review_id))
        if review is None:
            return {"status": "not_found", "review_id": review_id}
        if review.ai_state == AIState.DONE:
            return {"status": "already_done", "review_id": review_id}

        review.ai_state = AIState.PROCESSING
        review.ai_attempts = (review.ai_attempts or 0) + 1
        session.commit()

        try:
            result = asyncio.run(process_review(session, review))
            session.commit()
        except Exception as exc:
            session.rollback()
            mark_failed(session, review, str(exc))
            session.commit()
            raise self.retry(exc=exc) from exc

    pairs = sorted((str(d), str(r)) for d, r in result.pairs)
    if pairs:
        recompute_pairs_task.delay(pairs)

    return {
        "status": "done",
        "review_id": review_id,
        "mentions": result.mentions,
        "pairs": len(pairs),
    }
