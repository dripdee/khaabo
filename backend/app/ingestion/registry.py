"""Adapter registry.

Adding a source means adding one class and one registry entry; nothing in the
domain or API layer changes.
"""

from __future__ import annotations

from app.core.config import settings
from app.ingestion.base import SourceAdapter
from app.ingestion.google_places import GooglePlacesAdapter
from app.ingestion.osm import NominatimAdapter, OverpassAdapter
from app.ingestion.reddit import RedditAdapter
from app.ingestion.youtube import YouTubeAdapter
from app.models.enums import SourceType

_ADAPTERS: dict[SourceType, type[SourceAdapter]] = {
    SourceType.OSM: OverpassAdapter,
    SourceType.REDDIT: RedditAdapter,
    SourceType.YOUTUBE: YouTubeAdapter,
    SourceType.GOOGLE: GooglePlacesAdapter,
}


def get_adapter(source: SourceType | str) -> SourceAdapter:
    try:
        key = SourceType(source)
    except ValueError as exc:
        raise ValueError(f"No ingestion adapter registered for source '{source}'") from exc

    adapter_cls = _ADAPTERS.get(key)
    if adapter_cls is None:
        raise ValueError(f"No ingestion adapter registered for source '{source}'")
    return adapter_cls()


def enabled_adapters() -> list[SourceAdapter]:
    """Only sources listed in SOURCES_ENABLED, and only ones with an adapter."""
    out: list[SourceAdapter] = []
    for name in settings.enabled_sources:
        if name == SourceType.USER.value:
            continue  # user reviews arrive via the API, not a fetch job
        try:
            out.append(get_adapter(name))
        except ValueError:
            continue
    return out


def interval_hours(source: SourceType | str) -> int:
    return {
        SourceType.OSM: settings.source_interval_osm_hours,
        SourceType.REDDIT: settings.source_interval_reddit_hours,
        SourceType.YOUTUBE: settings.source_interval_youtube_hours,
        SourceType.GOOGLE: settings.google_refresh_interval_hours,
    }.get(SourceType(source), 24)


__all__ = [
    "GooglePlacesAdapter",
    "NominatimAdapter",
    "OverpassAdapter",
    "RedditAdapter",
    "YouTubeAdapter",
    "enabled_adapters",
    "get_adapter",
    "interval_hours",
]
