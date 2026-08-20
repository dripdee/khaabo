"""Ingestion tasks.

Idempotency contract: each run claims a `job_key` of
`{source}:{city}:{YYYY-MM-DDTHH}` under a UNIQUE constraint. A duplicate tick is
recorded as `skipped` instead of double-fetching. Retries are exponential with
jitter and only for genuinely transient failures.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

from celery import shared_task
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError

from app.core.errors import PermanentSourceError, TransientSourceError
from app.core.logging import get_logger
from app.db.session import sync_session
from app.ingestion.pipeline import (
    IngestCounters,
    city_ref,
    load_candidates,
    store_review,
    upsert_place,
)
from app.ingestion.registry import get_adapter, interval_hours
from app.models import City, IngestionJob, Review, ReviewSource
from app.models.enums import JobStatus, SourceType
from app.workers.celery_app import celery_app

log = get_logger(__name__)

RETRY_KWARGS = {
    "autoretry_for": (TransientSourceError,),
    "retry_backoff": 30,
    "retry_backoff_max": 1800,
    "retry_jitter": True,
    "max_retries": 5,
}


def _job_key(source: str, city_slug: str, interval: int, kind: str | None = None) -> str:
    """Bucket the clock by the source's interval so re-ticks collapse.

    `kind` separates sibling jobs on the same source (video fetch vs. comment
    fetch) that share one unique-key bucket otherwise.
    """
    now = datetime.now(UTC)
    bucket_hour = (now.hour // max(1, interval)) * max(1, interval)
    key = f"{source}:{city_slug}:{now:%Y-%m-%d}T{bucket_hour:02d}"
    return f"{key}:{kind}" if kind else key


def _claim_job(
    session,
    source: SourceType,
    city: City,
    params: dict,
    kind: str | None = None,
    interval: int | None = None,
) -> IngestionJob | None:
    key = _job_key(
        source.value,
        city.slug,
        interval if interval is not None else interval_hours(source),
        kind,
    )
    job = IngestionJob(
        source=source,
        city_id=city.id,
        job_key=key,
        status=JobStatus.RUNNING,
        params=params,
        started_at=datetime.now(UTC),
    )
    session.add(job)
    try:
        session.flush()
    except IntegrityError:
        session.rollback()
        log.info("ingestion_job_already_claimed", job_key=key)
        return None
    return job


def _active_cities(session) -> list[City]:
    return list(session.execute(select(City).where(City.active.is_(True))).scalars().all())


@celery_app.task(name="ingestion.discover_places", bind=True, **RETRY_KWARGS)
def discover_places(self, city_slug: str | None = None) -> dict:
    """Fetch places from OSM/Overpass for each active city."""
    totals: dict[str, dict] = {}

    with sync_session() as session:
        cities = (
            [c for c in _active_cities(session) if c.slug == city_slug]
            if city_slug
            else _active_cities(session)
        )

        for city in cities:
            adapter = get_adapter(SourceType.OSM)
            if not adapter.enabled:
                continue

            job = _claim_job(session, SourceType.OSM, city, {"kind": "discover_places"})
            if job is None:
                totals[city.slug] = {"status": "skipped_duplicate"}
                continue

            counters = IngestCounters()
            try:
                places = asyncio.run(adapter.discover_places(city_ref(city)))
                candidates = load_candidates(session, city.id)
                counters.seen = len(places)

                for i, place in enumerate(places, 1):
                    _, action = upsert_place(session, city, place, candidates=candidates)
                    if action == "created":
                        counters.created += 1
                        # Keep the in-memory candidate list current so two similar
                        # places in the same batch resolve against each other.
                        candidates = load_candidates(session, city.id)
                    elif action == "updated":
                        counters.updated += 1
                    elif action == "conflict":
                        counters.conflicts += 1
                        counters.skipped += 1
                    else:
                        counters.skipped += 1
                    # Chunked commits: Supabase's statement timeout kills a
                    # whole-city insert inside one long transaction.
                    if i % 100 == 0:
                        session.commit()
                session.commit()

                _finish_job(job, counters, JobStatus.SUCCESS)
                totals[city.slug] = {
                    "seen": counters.seen,
                    "created": counters.created,
                    "updated": counters.updated,
                    "skipped": counters.skipped,
                    "conflicts": counters.conflicts,
                }
            except PermanentSourceError as exc:
                _finish_job(job, counters, JobStatus.FAILED, str(exc))
                log.error("discover_places_permanent_failure", city=city.slug, error=str(exc))
            except TransientSourceError as exc:
                _finish_job(job, counters, JobStatus.FAILED, str(exc))
                session.commit()
                raise self.retry(exc=exc) from exc

    return totals


@celery_app.task(name="ingestion.fetch_reviews", bind=True, **RETRY_KWARGS)
def fetch_reviews(
    self, source: str, city_slug: str | None = None, lookback_days: int | None = None
) -> dict:
    """Fetch text evidence from a source, then hand off to AI processing.

    `lookback_days` overrides the incremental cursor for one-off backfills
    (e.g. re-processing past YouTube videos after the matcher improves);
    dedupe on `(source, external_id)` and content hash keeps it safe.
    """
    from app.workers.ai_tasks import process_pending

    source_type = SourceType(source)
    totals: dict[str, dict] = {}

    with sync_session() as session:
        adapter = get_adapter(source_type)
        if not adapter.enabled:
            return {"status": "disabled", "source": source}

        cities = (
            [c for c in _active_cities(session) if c.slug == city_slug]
            if city_slug
            else _active_cities(session)
        )

        for city in cities:
            job = _claim_job(session, source_type, city, {"kind": "fetch_reviews"})
            if job is None:
                totals[city.slug] = {"status": "skipped_duplicate"}
                continue

            counters = IngestCounters()
            try:
                since = _last_success_at(session, source_type, city)
                if lookback_days is not None:
                    since = datetime.now(UTC) - timedelta(days=lookback_days)
                raw_reviews = asyncio.run(adapter.fetch_reviews(city_ref(city), since))
                counters.seen = len(raw_reviews)
                candidates = load_candidates(session, city.id)

                for raw in raw_reviews:
                    review, action = store_review(session, city, raw, candidates=candidates)
                    if action == "created":
                        counters.created += 1
                        if review is not None:
                            counters.review_ids.append(str(review.id))
                    else:
                        counters.skipped += 1

                _finish_job(job, counters, JobStatus.SUCCESS)
                totals[city.slug] = {
                    "seen": counters.seen,
                    "created": counters.created,
                    "skipped": counters.skipped,
                }
            except PermanentSourceError as exc:
                _finish_job(job, counters, JobStatus.FAILED, str(exc))
                log.error("fetch_reviews_permanent_failure", source=source, error=str(exc))
            except TransientSourceError as exc:
                _finish_job(job, counters, JobStatus.FAILED, str(exc))
                session.commit()
                raise self.retry(exc=exc) from exc

    if any(v.get("created") for v in totals.values() if isinstance(v, dict)):
        process_pending.delay()

    return totals


MAX_COMMENT_VIDEOS_PER_RUN = 25


@celery_app.task(name="ingestion.fetch_youtube_comments", bind=True, **RETRY_KWARGS)
def fetch_youtube_comments(self, city_slug: str | None = None) -> dict:
    """Comment threads for already-stored YouTube videos, per city.

    `commentThreads.list` costs 1 unit for up to 100 comments, so this is the
    cheap way to multiply evidence per quota unit. Videos are taken from the
    dedup table (`review_sources`) and every city run claims its own job_key
    (`kind: comments` in params) so a duplicate beat tick cannot double-fetch.
    Commits happen per video so one slow statement cannot murder the txn.
    """
    from app.workers.ai_tasks import process_pending

    totals: dict[str, dict] = {}

    with sync_session() as session:
        adapter = get_adapter(SourceType.YOUTUBE)
        if not adapter.enabled:
            return {"status": "disabled", "source": "youtube"}

        cities = (
            [c for c in _active_cities(session) if c.slug == city_slug]
            if city_slug
            else _active_cities(session)
        )

        for city in cities:
            video_ids = _pending_comment_videos(session, city.id)
            if not video_ids:
                continue

            job = _claim_job(
                session,
                SourceType.YOUTUBE,
                city,
                {"kind": "comments"},
                kind="comments",
                interval=6,  # match the beat cadence so each 6 h tick is one run
            )
            if job is None:
                totals[city.slug] = {"status": "skipped_duplicate"}
                continue

            counters = IngestCounters()
            try:
                candidates = load_candidates(session, city.id)
                # The adapter reserves one quota unit per video and stops the
                # whole run if the daily budget runs out mid-way.
                raw_reviews = asyncio.run(
                    adapter.fetch_comments_for_videos(video_ids, city_ref(city))
                )
                counters.seen = len(raw_reviews)

                by_video: dict[str, list] = {}
                for raw in raw_reviews:
                    by_video.setdefault(raw.raw.get("video_id"), []).append(raw)

                # Dedupe order follows the fetch order so a partial (quota-cut)
                # run stores the oldest-vintage videos first.
                for video_id in video_ids:
                    for raw in by_video.get(video_id, []):
                        review, action = store_review(session, city, raw, candidates=candidates)
                        if action == "created":
                            counters.created += 1
                            if review is not None:
                                counters.review_ids.append(str(review.id))
                        else:
                            counters.skipped += 1

                    # Chunked commits: Supabase's statement timeout kills long txns.
                    session.commit()

                _finish_job(job, counters, JobStatus.SUCCESS)
                session.commit()
                totals[city.slug] = {
                    "videos": len(video_ids),
                    "seen": counters.seen,
                    "created": counters.created,
                    "skipped": counters.skipped,
                }
                log.info(
                    "youtube_comments_ingested",
                    city=city.slug,
                    videos=len(video_ids),
                    seen=counters.seen,
                    created=counters.created,
                    skipped=counters.skipped,
                )
            except PermanentSourceError as exc:
                _finish_job(job, counters, JobStatus.FAILED, str(exc))
                log.error("fetch_youtube_comments_permanent_failure", error=str(exc))
            except TransientSourceError as exc:
                _finish_job(job, counters, JobStatus.FAILED, str(exc))
                session.commit()
                raise self.retry(exc=exc) from exc

    if any(v.get("created") for v in totals.values() if isinstance(v, dict)):
        process_pending.delay()

    return totals


def _pending_comment_videos(session, city_id) -> list[str]:
    """Video ids with evidence stored for this city but no comment rows yet.

    `review_sources` carries no city column, so the join goes through `reviews`.
    Oldest source rows are considered first so repeated runs work through the
    backlog, MAX_COMMENT_VIDEOS_PER_RUN at a time.
    """
    rows = (
        session.execute(
            select(ReviewSource.external_id)
            .join(Review, ReviewSource.review_id == Review.id)
            .where(
                Review.city_id == city_id,
                ReviewSource.source == SourceType.YOUTUBE,
                ReviewSource.external_id.like("yt:%"),
                ReviewSource.external_id.notlike("yt:%#c:%"),
            )
            .order_by(ReviewSource.created_at)
        )
        .scalars()
        .all()
    )

    seen: set[str] = set()
    video_ids: list[str] = []
    for eid in rows:
        vid = eid.removeprefix("yt:")
        if vid and vid not in seen:
            seen.add(vid)
            video_ids.append(vid)
    if not video_ids:
        return []

    # One distinct scan of existing comment rows: their `yt:<vid>#c:` prefix
    # tells us which videos are already done.
    commented = {
        eid.split("#c:", 1)[0]
        for eid in session.execute(
            select(ReviewSource.external_id)
            .where(
                ReviewSource.source == SourceType.YOUTUBE,
                ReviewSource.external_id.like("yt:%#c:%"),
            )
            .distinct()
        )
        .scalars()
        .all()
    }
    pending = [vid for vid in video_ids if f"yt:{vid}" not in commented]
    return pending[:MAX_COMMENT_VIDEOS_PER_RUN]


def _finish_job(
    job: IngestionJob,
    counters: IngestCounters,
    status: JobStatus,
    error: str | None = None,
) -> None:
    job.status = status
    job.items_seen = counters.seen
    job.items_created = counters.created
    job.items_updated = counters.updated
    job.items_skipped = counters.skipped
    job.error = error[:2000] if error else None
    job.finished_at = datetime.now(UTC)


def _last_success_at(session, source: SourceType, city: City) -> datetime | None:
    """Incremental cursor: only fetch what is newer than the last good run."""
    row = session.execute(
        select(IngestionJob.finished_at)
        .where(
            IngestionJob.source == source,
            IngestionJob.city_id == city.id,
            IngestionJob.status == JobStatus.SUCCESS,
        )
        .order_by(IngestionJob.finished_at.desc())
        .limit(1)
    ).scalar_one_or_none()
    if row is None:
        return datetime.now(UTC) - timedelta(days=90)
    return row - timedelta(hours=2)  # small overlap so nothing is missed at the seam


@celery_app.task(name="ingestion.enrich_aliases")
def enrich_aliases(city_slug: str | None = None, create: bool = False) -> dict:
    """Weekly Wikidata enrichment: fresh aliases for the catalog.

    No `job_key` claim on purpose — the enricher is idempotent (alias rows are
    unique per normalized name, repeat places are skipped via their
    `wikidata:<id>` source row), so an overlapping tick is harmless.
    """
    from scripts.enrich_aliases import enrich_city

    totals: dict[str, dict] = {}
    with sync_session() as session:
        cities = (
            [c for c in _active_cities(session) if c.slug == city_slug]
            if city_slug
            else _active_cities(session)
        )

    for city in cities:
        try:
            totals[city.slug] = enrich_city(city, create=create)
        except Exception as exc:  # noqa: BLE001 - report per-city, keep crawling
            totals[city.slug] = {"error": str(exc)[:500]}
            log.error("enrich_aliases_failed", city=city.slug, error=str(exc))
    return totals


@shared_task(name="ingestion.prune_jobs")
def prune_jobs(days: int = 30) -> dict:
    """Keep the ops tables bounded; failures are retained longer for debugging."""
    cutoff = datetime.now(UTC) - timedelta(days=days)
    with sync_session() as session:
        removed = session.execute(
            delete(IngestionJob).where(
                IngestionJob.created_at < cutoff, IngestionJob.status == JobStatus.SUCCESS
            )
        ).rowcount
    return {"removed": int(removed or 0)}


@shared_task(name="ingestion.reset_stuck_reviews")
def reset_stuck_reviews(minutes: int = 30) -> dict:
    """Recover reviews left in `processing` by a killed worker."""
    from app.models.enums import AIState

    cutoff = datetime.now(UTC) - timedelta(minutes=minutes)
    with sync_session() as session:
        rows = (
            session.execute(
                select(Review).where(
                    Review.ai_state == AIState.PROCESSING, Review.updated_at < cutoff
                )
            )
            .scalars()
            .all()
        )
        for review in rows:
            review.ai_state = AIState.PENDING
    return {"reset": len(rows)}
