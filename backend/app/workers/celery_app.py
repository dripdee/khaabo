"""Celery application, queues and beat schedule.

Four queues with distinct failure characteristics, so a slow AI model cannot starve
ingestion and a ranking sweep cannot delay a user's review appearing.
"""

from __future__ import annotations

from celery import Celery
from celery.schedules import crontab

from app.core.config import settings
from app.core.logging import configure_logging
from app.core.sentry import init_sentry

configure_logging()
init_sentry()

celery_app = Celery(
    "khaabo",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
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
    "process-pending-ai": {
        "task": "ai.process_pending",
        "schedule": 120.0,  # near-real-time for user reviews
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
}
