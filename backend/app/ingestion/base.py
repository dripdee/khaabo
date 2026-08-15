"""Ingestion adapter interface and shared transport helpers.

Everything external goes through here so a provider can be swapped, disabled or
mocked without touching domain code. The HTTP client enforces per-host minimum
intervals because Overpass and Nominatim usage policies are rate-based, not
quota-based, and violating them gets an IP banned.
"""

from __future__ import annotations

import asyncio
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime

import httpx

from app.core.config import settings
from app.core.errors import PermanentSourceError, TransientSourceError
from app.core.logging import get_logger
from app.models.enums import SourceType

log = get_logger(__name__)

RETRYABLE_STATUS = {408, 425, 429, 500, 502, 503, 504}


@dataclass(slots=True)
class RawPlace:
    """Provider-agnostic place record."""

    source: SourceType
    external_id: str
    name: str
    lat: float
    lng: float
    address: str | None = None
    area: str | None = None
    cuisines: list[str] = field(default_factory=list)
    price_level: int | None = None
    phone: str | None = None
    website: str | None = None
    opening_hours: str | None = None
    url: str | None = None
    license: str | None = None
    attribution: str | None = None
    raw: dict = field(default_factory=dict)


@dataclass(slots=True)
class RawReview:
    """Provider-agnostic text record.

    `restaurant_hint` is a name string when the source does not know the entity —
    entity resolution turns it into a restaurant id, or declines to.
    """

    source: SourceType
    external_id: str
    text: str
    published_at: datetime
    title: str | None = None
    author: str | None = None
    rating: float | None = None
    rating_scale: float | None = None
    engagement: int = 0
    url: str | None = None
    permalink: str | None = None
    restaurant_hint: str | None = None
    lat: float | None = None
    lng: float | None = None
    license: str | None = None
    attribution: str | None = None
    raw: dict = field(default_factory=dict)


@dataclass(slots=True)
class CityRef:
    """Minimal city projection so adapters never import ORM models."""

    id: str
    slug: str
    name: str
    lat: float
    lng: float
    radius_m: int = 25000


class RateLimiter:
    """Process-local minimum-interval gate, per host.

    Not distributed — with a single worker container this is sufficient, and the
    adapters additionally cap items per run so a multi-worker deployment still
    stays inside policy.
    """

    def __init__(self, min_interval: float) -> None:
        self.min_interval = min_interval
        self._last = 0.0
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        async with self._lock:
            elapsed = time.monotonic() - self._last
            wait = self.min_interval - elapsed
            if wait > 0:
                await asyncio.sleep(wait)
            self._last = time.monotonic()


async def request_json(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    *,
    limiter: RateLimiter | None = None,
    **kwargs: object,
) -> dict:
    """One HTTP call with policy-compliant headers and error classification.

    Retryable conditions raise `TransientSourceError` (Celery retries with
    backoff); contract/credential failures raise `PermanentSourceError` so the job
    fails fast and shows up in the admin failed-jobs view instead of looping.
    """
    if limiter:
        await limiter.acquire()

    headers = {"User-Agent": settings.user_agent, "Accept": "application/json"}
    headers.update(kwargs.pop("headers", {}) or {})  # type: ignore[arg-type]

    try:
        response = await client.request(method, url, headers=headers, **kwargs)  # type: ignore[arg-type]
    except (httpx.TimeoutException, httpx.NetworkError) as exc:
        raise TransientSourceError(f"Network failure calling {url}: {exc}") from exc

    if response.status_code in RETRYABLE_STATUS:
        retry_after = response.headers.get("Retry-After")
        raise TransientSourceError(
            f"{url} returned {response.status_code}",
            retry_after=float(retry_after) if retry_after and retry_after.isdigit() else None,
            details={"status": response.status_code},
        )

    if response.status_code in {401, 403}:
        raise PermanentSourceError(f"{url} rejected credentials ({response.status_code})")

    if response.status_code >= 400:
        raise PermanentSourceError(f"{url} returned {response.status_code}: {response.text[:200]}")

    try:
        return response.json()
    except ValueError as exc:
        raise PermanentSourceError(f"{url} returned non-JSON body") from exc


class SourceAdapter(ABC):
    source: SourceType
    default_interval_hours: int = 24

    @property
    def enabled(self) -> bool:
        return self.source.value in settings.enabled_sources

    async def discover_places(self, city: CityRef) -> list[RawPlace]:
        """Sources that only carry text return nothing here."""
        return []

    async def fetch_reviews(self, city: CityRef, since: datetime | None = None) -> list[RawReview]:
        """Sources that only carry places return nothing here."""
        return []

    @abstractmethod
    async def health(self) -> bool: ...
