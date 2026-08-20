"""Celery application, queues and beat schedule.

Four queues with distinct failure characteristics, so a slow AI model cannot starve
ingestion and a ranking sweep cannot delay a user's review appearing.
"""

from __future__ import annotations

import os
from urllib.parse import parse_qsl, urlencode, urlsplit

from celery import Celery
from celery.schedules import crontab

from app.core.config import settings
from app.core.logging import configure_logging
from app.core.sentry import init_sentry

configure_logging()
init_sentry()


def _add_ssl_cert_reqs(url: str) -> str:
    """Upstash-style rediss URLs must declare ssl_cert_reqs, or Celery refuses
    to instantiate the Redis backend (E_REDIS_SSL_CERT_REQS_MISSING_INVALID)."""
    if not url.lower().startswith(("rediss://", "redis+ssl://")):
        return url
    parts = urlsplit(url)
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    query.setdefault("ssl_cert_reqs", "CERT_REQUIRED")
    return parts._replace(query=urlencode(query)).geturl()


# Celery merges CELERY_* env vars into its settings *after* constructor args,
# so the container env vars must be sanitized too — not just the pydantic ones.
for _var in ("CELERY_BROKER_URL", "CELERY_RESULT_BACKEND", "BROKER_URL"):
    if _val := os.environ.get(_var):
        os.environ[_var] = _add_ssl_cert_reqs(_val)


celery_app = Celery(
    "khaabo",
    broker=_add_ssl_cert_reqs(settings.celery_broker_url),
    backend=_add_ssl_cert_reqs(settings.celery_result_backend),
    include=[
        "app.workers.ingestion_tasks",
        "app.workers.ai_tasks",
        "app.workers.ranking_tasks",
    ],
)

celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
    task_acks_late=True,  # redeliver if a worker dies mid-task
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,  # long tasks: fair dispatch over throughput
    task_track_started=True,
    task_time_limit=1800,
    task_soft_time_limit=1500,
    result_expires=86400,
    broker_connection_retry_on_startup=True,
    # In prod the image's WORKDIR is not writable when the compose overlay fails
    # (or the user is non-root); /tmp always works and the schedule is ephemeral.
    beat_schedule_filename="/tmp/celerybeat-schedule",
    task_default_queue="ingestion",
    task_routes={
        "ingestion.*": {"queue": "ingestion"},
        "ai.*": {"queue": "ai_processing"},
        "ranking.*": {"queue": "ranking"},
        "summarization.*": {"queue": "summarization"},
    },
)

# Intervals are per-source and configurable (6–24 h). Beat only *enqueues*; the task
# itself claims a unique `job_key`, so an overlapping tick is recorded as skipped.
celery_app.conf.beat_schedule = {
    "discover-places": {
        "task": "ingestion.discover_places",
        "schedule": crontab(minute=15, hour=f"*/{settings.source_interval_osm_hours}"),
    },
    "fetch-reddit": {
        "task": "ingestion.fetch_reviews",
        "schedule": crontab(minute=5, hour=f"*/{settings.source_interval_reddit_hours}"),
        "kwargs": {"source": "reddit"},
    },
    "fetch-youtube": {
        "task": "ingestion.fetch_reviews",
        "schedule": crontab(minute=35, hour=f"*/{settings.source_interval_youtube_hours}"),
        "kwargs": {"source": "youtube"},
    },
    "fetch-youtube-comments": {
        "task": "ingestion.fetch_youtube_comments",
        "schedule": crontab(minute=50, hour="*/6"),
    },
    "process-pending-ai": {
        "task": "ai.process_pending",
        "schedule": crontab(minute="*/15"),  # user reviews pick up within 15 min;
        # a 2-minute cycle burned the free-tier Redis budget with no-op polls
    },
    "ranking-nightly-sweep": {
        "task": "ranking.nightly_sweep",
        "schedule": crontab(minute=30, hour=22),  # 03:30 IST
    },
    "trends-recompute": {
        "task": "ranking.recompute_trends",
        "schedule": crontab(minute=0, hour="*/6"),
    },
    "prune-jobs": {
        "task": "ingestion.prune_jobs",
        "schedule": crontab(minute=0, hour=1),
    },
    "enrich-aliases": {
        "task": "ingestion.enrich_aliases",
        "schedule": crontab(minute=0, hour=3, day_of_week=1),  # weekly, Monday 03:00 UTC
    },
}
