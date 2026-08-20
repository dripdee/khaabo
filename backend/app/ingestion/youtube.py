"""YouTube Data API v3 adapter with a hard quota guard.

The free tier is 10,000 units/day and `search.list` alone costs 100 units. The
guard atomically reserves units in Redis before every call (rolling back if the
call fails) and **stops the run** (`skipped`) once the daily budget would be
exceeded — an exhausted quota breaks ingestion for the rest of the day.

Only official API endpoints are used; no scraping, no transcript extraction.
"""

from __future__ import annotations

from contextlib import suppress
from datetime import UTC, datetime, timedelta

import httpx

from app.core.cache import get_redis
from app.core.config import settings
from app.core.errors import PermanentSourceError
from app.core.logging import get_logger
from app.ingestion.base import CityRef, RateLimiter, RawReview, SourceAdapter, request_json
from app.models.enums import SourceType
from app.utils.text import clean_text, normalize_name

log = get_logger(__name__)

# Politeness between comment calls, mirroring the per-host limiters used elsewhere.
_comments_limiter = RateLimiter(1.0)

API_BASE = "https://www.googleapis.com/youtube/v3"
YT_LICENSE = "youtube-api-tos"
YT_ATTRIBUTION = "YouTube"

COST_SEARCH = 100
COST_VIDEOS = 1
COST_COMMENTS = 1

MIN_TEXT_LENGTH = 40

QUERY_TEMPLATES = (
    "best street food in {city}",
    "{city} food tour",
    "best momo in {city}",
    "best biryani in {city}",
    "{city} cafe review",
)


def _parse_published(*values: str | None) -> datetime:
    """First parseable ISO timestamp wins; fall back to now."""
    for value in values:
        if not value:
            continue
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            continue
    return datetime.now(UTC)


def _comment_snippet(snippet: dict) -> dict:
    """The commentThreads item nests the comment one level deeper than videos."""
    top = snippet.get("topLevelComment") or {}
    return top.get("snippet") or {}


class QuotaGuard:
    """Daily unit budget, tracked in Redis so it survives worker restarts.

    `reserve()` is atomic (INCRBY, then roll back if the budget would be
    crossed), so concurrent beat ticks or retries can never overshoot the cap.

    Without Redis the run is still allowed: the per-run item caps bound the
    spend, and refusing to ingest at all would be worse.
    """

    def __init__(self, budget: int) -> None:
        self.budget = budget

    def _key(self) -> str:
        return f"yt:quota:{datetime.now(UTC).date().isoformat()}"

    async def spent(self) -> int:
        client = get_redis()
        if client is None:
            return 0
        try:
            value = await client.get(self._key())
            return int(value or 0)
        except Exception:
            return 0

    async def remaining(self) -> int:
        return max(0, self.budget - await self.spent())

    async def reserve(self, cost: int) -> bool:
        """Atomically reserve `cost` units against the daily budget.

        Returns False (having reserved nothing) if the reservation would push
        the day's spend past budget. Callers must never issue the API call
        when this returns False.
        """
        client = get_redis()
        if client is None:
            return True
        try:
            new_total = await client.incrby(self._key(), cost)
            await client.expire(self._key(), 172800, nx=True)
            if new_total > self.budget:
                # Over budget: take the reservation back and refuse the work.
                await client.incrby(self._key(), -cost)
                return False
            return True
        except Exception:
            # Redis hiccup: allow the run — per-run caps still bound the spend.
            return True

    async def refund(self, cost: int) -> None:
        """Return reserved units if the corresponding API call errored out."""
        client = get_redis()
        if client is None:
            return
        with suppress(Exception):
            await client.incrby(self._key(), -cost)


class YouTubeAdapter(SourceAdapter):
    source = SourceType.YOUTUBE

    def __init__(self) -> None:
        self.default_interval_hours = settings.source_interval_youtube_hours
        self.quota = QuotaGuard(settings.youtube_daily_quota_units)

    @property
    def configured(self) -> bool:
        return bool(settings.youtube_api_key)

    async def fetch_reviews(self, city: CityRef, since: datetime | None = None) -> list[RawReview]:
        if not self.configured:
            log.info("youtube_skipped_not_configured")
            return []

        published_after = (since or datetime.now(UTC) - timedelta(days=90)).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
        reviews: list[RawReview] = []
        seen_video_ids: set[str] = set()

        async with httpx.AsyncClient(timeout=30.0, base_url=API_BASE) as client:
            for template in QUERY_TEMPLATES:
                if len(seen_video_ids) >= settings.youtube_max_videos_per_run:
                    break
                # Reserve search + video-detail units up front. An API call
                # must never be issued without a held reservation.
                if not await self.quota.reserve(COST_SEARCH + COST_VIDEOS):
                    log.info("youtube_quota_exhausted", spent=await self.quota.spent())
                    break

                query = template.format(city=city.name)
                try:
                    payload = await request_json(
                        client,
                        "GET",
                        "/search",
                        params={
                            "key": settings.youtube_api_key,
                            "part": "snippet",
                            "q": query,
                            "type": "video",
                            "maxResults": 10,
                            "order": "relevance",
                            "publishedAfter": published_after,
                            "relevanceLanguage": "en",
                            "regionCode": "IN",
                        },
                    )
                except Exception:
                    await self.quota.refund(COST_SEARCH + COST_VIDEOS)
                    raise

                video_ids = [
                    item["id"]["videoId"]
                    for item in payload.get("items", [])
                    if item.get("id", {}).get("videoId")
                ]
                video_ids = [v for v in video_ids if v not in seen_video_ids]
                if not video_ids:
                    # Search succeeded; refund only the unused video-detail part.
                    await self.quota.refund(COST_VIDEOS)
                    continue
                seen_video_ids.update(video_ids)

                try:
                    details = await request_json(
                        client,
                        "GET",
                        "/videos",
                        params={
                            "key": settings.youtube_api_key,
                            "part": "snippet,statistics",
                            "id": ",".join(video_ids[:25]),
                        },
                    )
                except Exception:
                    await self.quota.refund(COST_VIDEOS)
                    raise

                for item in details.get("items", []):
                    review = self._video_to_review(item, city)
                    if review:
                        reviews.append(review)

        log.info("youtube_fetched", count=len(reviews), city=city.slug)
        return reviews

    async def fetch_comments_for_videos(
        self, video_ids: list[str], city: CityRef
    ) -> list[RawReview]:
        """Top-level comment threads (one page, up to 100) for given videos.

        The parent videos were already city-verified by the video fetch, so no
        city-name gate applies here. A per-video 403 means comments are disabled
        on that video — skip it and continue; anything else follows the same
        reserve/refund discipline as `/search` and `/videos`.
        """
        if not self.configured:
            log.info("youtube_comments_skipped_not_configured")
            return []

        reviews: list[RawReview] = []

        async with httpx.AsyncClient(timeout=30.0, base_url=API_BASE) as client:
            for video_id in video_ids:
                # One unit before the call; never issue a call without a reservation.
                if not await self.quota.reserve(COST_COMMENTS):
                    log.info(
                        "youtube_comments_quota_exhausted",
                        spent=await self.quota.spent(),
                        city=city.slug,
                        videos_fetched=len({r.raw.get("video_id") for r in reviews}),
                    )
                    break

                try:
                    payload = await request_json(
                        client,
                        "GET",
                        "/commentThreads",
                        limiter=_comments_limiter,
                        params={
                            "key": settings.youtube_api_key,
                            "part": "snippet",
                            "videoId": video_id,
                            "maxResults": 100,
                            "textFormat": "plainText",
                        },
                    )
                except PermanentSourceError as exc:
                    if exc.details.get("status") == 403:
                        # Comments are disabled on this video. The call still
                        # reached YouTube (1 unit billed), so do NOT refund;
                        # skip the video and keep the run going.
                        log.info("youtube_comments_disabled", video_id=video_id)
                        continue
                    await self.quota.refund(COST_COMMENTS)
                    raise
                except Exception:
                    await self.quota.refund(COST_COMMENTS)
                    raise

                for item in payload.get("items", []):
                    review = self._comment_to_review(item, video_id)
                    if review:
                        reviews.append(review)

        log.info("youtube_comments_fetched", count=len(reviews), city=city.slug)
        return reviews

    def _video_to_review(self, item: dict, city: CityRef) -> RawReview | None:
        snippet = item.get("snippet") or {}
        stats = item.get("statistics") or {}

        title = clean_text(snippet.get("title") or "")
        description = clean_text(snippet.get("description") or "")
        blob = f"{title}. {description}"

        if len(blob) < MIN_TEXT_LENGTH:
            return None

        # The video must actually be about this city, otherwise a generic food
        # channel would contribute evidence to the wrong place.
        if normalize_name(city.name) not in normalize_name(blob):
            return None

        published_at = _parse_published(snippet.get("publishedAt"))

        engagement = int(stats.get("likeValue", 0) or stats.get("likeCount", 0) or 0)

        return RawReview(
            source=SourceType.YOUTUBE,
            external_id=f"yt:{item['id']}",
            title=title,
            text=blob[:4000],
            author=snippet.get("channelTitle"),
            published_at=published_at,
            engagement=engagement,
            url=f"https://www.youtube.com/watch?v={item['id']}",
            license=YT_LICENSE,
            attribution=f"{YT_ATTRIBUTION} · {snippet.get('channelTitle', '')}".strip(" ·"),
            raw={"video_id": item["id"], "statistics": stats},
        )

    def _comment_to_review(self, item: dict, video_id: str) -> RawReview | None:
        """One commentThreads item → a RawReview.

        `yt:{video_id}#c:{comment_id}` is the dedup identity — the `#c:` segment
        keeps comment rows from colliding with the video's own `yt:{video_id}` row.
        """
        snippet = item.get("snippet") or {}
        top = _comment_snippet(snippet)

        text = clean_text(top.get("textDisplay") or "")
        if len(text) < MIN_TEXT_LENGTH:
            return None

        return RawReview(
            source=SourceType.YOUTUBE,
            external_id=f"yt:{video_id}#c:{item['id']}",
            text=text[:4000],
            author=top.get("authorDisplayName"),
            published_at=_parse_published(top.get("publishedAt"), top.get("updatedAt")),
            engagement=int(top.get("likeCount") or 0),
            url=f"https://www.youtube.com/watch?v={video_id}",
            license=YT_LICENSE,
            attribution=f"{YT_ATTRIBUTION} · {top.get('authorDisplayName', '')}".strip(" ·"),
            raw={"video_id": video_id, "comment_id": item["id"]},
        )

    async def health(self) -> bool:
        return self.configured and await self.quota.remaining() >= COST_SEARCH + COST_VIDEOS
