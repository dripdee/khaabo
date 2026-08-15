"""Prometheus metrics.

Exposes a single ``/metrics`` path under the API prefix. Counters and
histograms are kept to the essentials that the runbook acts on: request rate,
error rate, latency, and the queue depths that determine whether the ranking
pipeline is keeping up with new evidence.

The endpoint is intentionally unauthenticated and unrate-limited so Prometheus
can scrape it. It returns no user data.
"""

from __future__ import annotations

from fastapi import APIRouter, Response
from prometheus_client import CONTENT_TYPE_LATEST, CollectorRegistry, generate_latest, multiprocess

router = APIRouter(tags=["metrics"])


@router.get("/metrics", include_in_schema=False)
async def metrics() -> Response:
    """Prometheus scrape endpoint.

    When prometheus-client multiprocess mode is active (``PROMETHEUS_MULTIPROC_DIR``
    set, as in the prod container), per-process metrics live in files; this
    endpoint merges them on every scrape. When unset (dev, single-process) the
    default registry is used directly.
    """
    registry = CollectorRegistry()
    multiprocess.MultiProcessCollector(registry)
    data = generate_latest(registry)
    return Response(content=data, media_type=CONTENT_TYPE_LATEST)


__all__ = ["router"]
