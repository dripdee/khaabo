"""Ingestion adapter tests.

External calls are never made here. What is verified is the part that determines data
quality: normalization, licence/attribution capture, relevance filtering, quota
guarding, and that adapters degrade to a no-op when unconfigured rather than crashing
a scheduled job.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import httpx
import pytest

from app.core.errors import TransientSourceError
from app.ingestion.base import CityRef
from app.ingestion.google_places import (
    FIELD_MASK,
    MAX_PAGES_PER_CELL,
    GooglePlacesAdapter,
    PlacesQuotaGuard,
    grid_centers,
    haversine_m,
)
from app.ingestion.osm import OSM_ATTRIBUTION, OverpassAdapter, build_overpass_query
from app.ingestion.reddit import RedditAdapter
from app.ingestion.registry import enabled_adapters, get_adapter, interval_hours
from app.ingestion.youtube import (
    COST_COMMENTS,
    COST_SEARCH,
    COST_VIDEOS,
    QuotaGuard,
    YouTubeAdapter,
)
from app.models.enums import SourceType
from app.utils.text import clean_text, content_hash, extract_prices, normalize_name, slugify

KOLKATA = CityRef(id="c1", slug="kolkata", name="Kolkata", lat=22.5726, lng=88.3639)


class TestOverpassQuery:
    def test_query_is_scoped_to_the_city(self):
        query = build_overpass_query(KOLKATA)
        assert "22.5726" in query
        assert "88.3639" in query
        assert "25000" in query

    def test_query_covers_nodes_and_ways(self):
        query = build_overpass_query(KOLKATA)
        assert "node[" in query
        assert "way[" in query

    def test_query_requests_centres_for_ways(self):
        assert "out center tags" in build_overpass_query(KOLKATA)

    def test_query_targets_food_amenities_only(self):
        query = build_overpass_query(KOLKATA)
        for amenity in ("restaurant", "cafe", "fast_food"):
            assert amenity in query


class TestOverpassNormalization:
    def setup_method(self):
        self.adapter = OverpassAdapter()

    def test_node_becomes_a_place(self):
        place = self.adapter._to_place(
            {
                "type": "node",
                "id": 12345,
                "lat": 22.58,
                "lon": 88.42,
                "tags": {
                    "name": "Momo Ghar",
                    "amenity": "restaurant",
                    "cuisine": "tibetan;chinese",
                    "addr:street": "Salt Lake Road",
                    "addr:suburb": "Salt Lake",
                    "phone": "+91 33 1234 5678",
                },
            }
        )
        assert place is not None
        assert place.name == "Momo Ghar"
        assert place.external_id == "node/12345"
        assert place.cuisines == ["tibetan", "chinese"]
        assert place.area == "Salt Lake"
        assert "Salt Lake Road" in (place.address or "")

    def test_licence_and_attribution_are_recorded(self):
        """Attribution has to be stored per record to be renderable in the UI."""
        place = self.adapter._to_place(
            {"type": "node", "id": 1, "lat": 22.5, "lon": 88.3, "tags": {"name": "X"}}
        )
        assert place is not None
        assert place.attribution == OSM_ATTRIBUTION
        assert place.license == "ODbL-1.0"

    def test_unnamed_elements_are_dropped(self):
        assert (
            self.adapter._to_place(
                {"type": "node", "id": 1, "lat": 22.5, "lon": 88.3, "tags": {"amenity": "cafe"}}
            )
            is None
        )

    def test_elements_without_coordinates_are_dropped(self):
        assert (
            self.adapter._to_place({"type": "node", "id": 1, "tags": {"name": "Nowhere"}}) is None
        )

    def test_way_uses_its_centre(self):
        place = self.adapter._to_place(
            {
                "type": "way",
                "id": 999,
                "center": {"lat": 22.6, "lon": 88.4},
                "tags": {"name": "Big Restaurant"},
            }
        )
        assert place is not None
        assert place.lat == 22.6
        assert place.external_id == "way/999"

    def test_cafe_amenity_gains_a_cafe_cuisine(self):
        place = self.adapter._to_place(
            {
                "type": "node",
                "id": 2,
                "lat": 22.5,
                "lon": 88.3,
                "tags": {"name": "Coffee Place", "amenity": "cafe"},
            }
        )
        assert place is not None
        assert "cafe" in place.cuisines


class TestRedditRelevance:
    def setup_method(self):
        self.adapter = RedditAdapter()

    def test_city_subreddit_only_needs_a_food_cue(self):
        assert self.adapter._is_relevant(
            {"title": "Best biryani around?", "selftext": "Looking for recommendations"},
            {"kolkata"},
            lenient=True,
        )

    def test_general_subreddit_requires_the_city_named(self):
        assert not self.adapter._is_relevant(
            {"title": "Best biryani in India", "selftext": "Anywhere really"},
            {"kolkata"},
            lenient=False,
        )
        assert self.adapter._is_relevant(
            {"title": "Best biryani in Kolkata", "selftext": "Anywhere really"},
            {"kolkata"},
            lenient=False,
        )

    def test_non_food_posts_are_skipped(self):
        assert not self.adapter._is_relevant(
            {"title": "Traffic on EM Bypass", "selftext": "Terrible today"},
            {"kolkata"},
            lenient=True,
        )

    def test_short_posts_are_not_stored_as_evidence(self):
        """A bare title is a question, not an observation about a dish."""
        assert (
            self.adapter._post_to_review(
                {
                    "id": "abc",
                    "title": "Momo?",
                    "selftext": "where",
                    "created_utc": datetime.now(UTC).timestamp(),
                    "author": "u1",
                },
                "kolkata",
            )
            is None
        )

    def test_substantial_post_becomes_a_review(self):
        review = self.adapter._post_to_review(
            {
                "id": "abc",
                "title": "Momo review",
                "selftext": "The chicken momo at Momo Ghar was juicy and hot, worth the trip.",
                "created_utc": datetime.now(UTC).timestamp(),
                "author": "u1",
                "score": 42,
                "permalink": "/r/kolkata/comments/abc",
            },
            "kolkata",
        )
        assert review is not None
        assert review.source is SourceType.REDDIT
        assert review.external_id == "t3_abc"
        assert review.engagement == 42
        assert review.attribution == "r/kolkata"

    def test_nsfw_and_removed_posts_are_dropped(self):
        base = {
            "id": "abc",
            "title": "Momo review",
            "selftext": "The chicken momo was juicy and hot, definitely worth the trip.",
            "created_utc": datetime.now(UTC).timestamp(),
            "author": "u1",
        }
        assert self.adapter._post_to_review({**base, "over_18": True}, "kolkata") is None
        assert (
            self.adapter._post_to_review({**base, "removed_by_category": "moderator"}, "kolkata")
            is None
        )

    def test_unconfigured_adapter_reports_not_configured(self):
        assert self.adapter.configured is False

    async def test_unconfigured_adapter_returns_nothing_instead_of_raising(self):
        """A scheduled job must not fail merely because a source is not set up."""
        assert await self.adapter.fetch_reviews(KOLKATA) == []


class TestYouTubeQuota:
    def setup_method(self):
        self.adapter = YouTubeAdapter()

    def test_unconfigured_adapter_is_inert(self):
        assert self.adapter.configured is False

    async def test_unconfigured_adapter_returns_nothing(self):
        assert await self.adapter.fetch_reviews(KOLKATA) == []

    def test_video_must_mention_the_city(self):
        """A generic food channel must not contribute evidence to the wrong city."""
        assert (
            self.adapter._video_to_review(
                {
                    "id": "v1",
                    "snippet": {
                        "title": "Best street food in Delhi",
                        "description": "A tour of Delhi's finest street food stalls and more",
                        "publishedAt": "2026-01-01T00:00:00Z",
                        "channelTitle": "Foodie",
                    },
                    "statistics": {"likeCount": "100"},
                },
                KOLKATA,
            )
            is None
        )

    def test_matching_video_becomes_a_review(self):
        review = self.adapter._video_to_review(
            {
                "id": "v2",
                "snippet": {
                    "title": "Best momo in Kolkata",
                    "description": "We tried eight momo places across Kolkata this week",
                    "publishedAt": "2026-01-01T00:00:00Z",
                    "channelTitle": "Foodie",
                },
                "statistics": {"likeCount": "250"},
            },
            KOLKATA,
        )
        assert review is not None
        assert review.source is SourceType.YOUTUBE
        assert review.engagement == 250
        assert "YouTube" in (review.attribution or "")

    def test_quota_budget_is_bounded(self):
        assert self.adapter.quota.budget > 0


class FakeRedis:
    """Minimal async stand-in for the parts of redis used by QuotaGuard."""

    def __init__(self) -> None:
        self.store: dict[str, int] = {}

    async def get(self, key):
        return self.store.get(key)

    async def incrby(self, key, amount):
        self.store[key] = self.store.get(key, 0) + amount
        return self.store[key]

    async def expire(self, key, ttl, nx=False):
        return True


class TestQuotaGuard:
    """The daily YouTube quota must be hard-enforced: YouTube's free tier is
    10k units/day, and a search costs 100 — an overrun blocks ingestion for the
    rest of the day."""

    async def test_reservations_succeed_within_budget(self, monkeypatch):
        fake = FakeRedis()
        monkeypatch.setattr("app.ingestion.youtube.get_redis", lambda: fake)
        guard = QuotaGuard(budget=300)

        assert await guard.reserve(COST_SEARCH + COST_VIDEOS) is True
        assert await guard.reserve(COST_SEARCH + COST_VIDEOS) is True
        assert await guard.spent() == 2 * (COST_SEARCH + COST_VIDEOS)

    async def test_reservation_crossing_budget_is_refused_and_rolled_back(self, monkeypatch):
        fake = FakeRedis()
        monkeypatch.setattr("app.ingestion.youtube.get_redis", lambda: fake)
        guard = QuotaGuard(budget=COST_SEARCH + COST_VIDEOS + 50)

        assert await guard.reserve(COST_SEARCH + COST_VIDEOS) is True
        # A second full reservation would cross the budget: refuse it AND
        # leave the counter untouched (nothing was actually spent).
        assert await guard.reserve(COST_SEARCH + COST_VIDEOS) is False
        assert await guard.spent() == COST_SEARCH + COST_VIDEOS

    async def test_refund_returns_units_when_the_call_fails(self, monkeypatch):
        fake = FakeRedis()
        monkeypatch.setattr("app.ingestion.youtube.get_redis", lambda: fake)
        guard = QuotaGuard(budget=COST_SEARCH + COST_VIDEOS)

        assert await guard.reserve(COST_SEARCH) is True
        await guard.refund(COST_SEARCH)
        assert await guard.spent() == 0
        # Budget is intact after the failed call: a fresh reservation still fits.
        assert await guard.reserve(COST_SEARCH + COST_VIDEOS) is True

    async def test_remaining_reflects_budget(self, monkeypatch):
        fake = FakeRedis()
        monkeypatch.setattr("app.ingestion.youtube.get_redis", lambda: fake)
        guard = QuotaGuard(budget=500)

        assert await guard.remaining() == 500
        await guard.reserve(COST_SEARCH)
        assert await guard.remaining() == 500 - COST_SEARCH

    async def test_no_redis_means_run_is_allowed_but_capped_by_item_limits(self, monkeypatch):
        monkeypatch.setattr("app.ingestion.youtube.get_redis", lambda: None)
        guard = QuotaGuard(budget=100)
        # Without a Redis we can't enforce, but we also don't want to refuse
        # ingestion entirely — per-run item caps still bound the spend.
        assert await guard.reserve(COST_SEARCH) is True
        assert await guard.spent() == 0

    async def test_never_reserves_more_units_than_the_budget_across_many_calls(self, monkeypatch):
        """Simulates the worst case: a burst of reservations from concurrent
        retries. Total accepted spend must stay at or under the budget."""
        fake = FakeRedis()
        monkeypatch.setattr("app.ingestion.youtube.get_redis", lambda: fake)
        budget = 9000
        guard = QuotaGuard(budget=budget)

        accepted = 0
        for _ in range(100):
            if await guard.reserve(COST_SEARCH + COST_VIDEOS):
                accepted += COST_SEARCH + COST_VIDEOS

        assert accepted <= budget
        assert await guard.spent() == accepted

    async def test_adapter_stops_fetching_once_budget_exhausted(self, monkeypatch):
        """When the quota is gone, the adapter must not issue any API call."""
        fake = FakeRedis()
        monkeypatch.setattr("app.ingestion.youtube.get_redis", lambda: fake)
        monkeypatch.setattr("app.ingestion.youtube.settings.youtube_api_key", "test-key")

        adapter = YouTubeAdapter()
        adapter.quota = QuotaGuard(budget=1)  # far too small for one search

        assert await adapter.fetch_reviews(KOLKATA) == []
        # Nothing was ever reserved because the first reservation was refused.
        assert await adapter.quota.spent() == 0

    async def test_search_unit_cost_is_100(self):
        """Documents Google's pricing assumption; if Google changes this the
        guard must be re-derived."""
        assert COST_SEARCH == 100
        assert COST_VIDEOS == 1


def _comment_item(comment_id: str, *, text: str, likes: int = 5, **overrides) -> dict:
    """A commentThreads.list item as the YouTube API returns it, with optional
    field overrides so individual shapes (`updatedAt`, missing snippets) can be
    exercised without hand-writing the nesting."""
    snippet = {
        "textDisplay": text,
        "authorDisplayName": "HungryHuman",
        "publishedAt": "2025-05-01T12:00:00Z",
        "updatedAt": "2025-05-02T12:00:00Z",
        "likeCount": likes,
    }
    snippet.update(overrides)
    return {"id": comment_id, "snippet": {"topLevelComment": {"snippet": snippet}}}


class _FakeYouTubeClient:
    """httpx.AsyncClient stand-in that routes GET /commentThreads per videoId.

    Each value of the route dict is a payload to return as JSON, an
    `httpx.Response` to return verbatim (for 403 plumbing), or an
    `httpx.HTTPError` to raise from the transport. Unknown video ids raise a
    transport error so a misconfigured test fails loudly rather than silently
    passing with an empty payload.
    """

    def __init__(self, routes: dict[str, object]) -> None:
        self._routes = routes
        self.requested: list[dict] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        return False

    async def request(self, method, url, *, headers=None, params=None):
        params = params or {}
        self.requested.append(dict(params))

        route = self._routes.get(params.get("videoId"))
        if isinstance(route, httpx.HTTPError):
            raise route
        if route is None:
            raise httpx.ConnectError(f"no route for video {params.get('videoId')!r}")

        fake_request = httpx.Request("GET", "https://youtube.test/commentThreads")
        if isinstance(route, httpx.Response):
            return route
        return httpx.Response(200, json=route, request=fake_request)


def _patch_youtube_client(monkeypatch, routes: dict[str, object]) -> _FakeYouTubeClient:
    client = _FakeYouTubeClient(routes)
    monkeypatch.setattr(
        "app.ingestion.youtube.httpx.AsyncClient",
        lambda **kwargs: client,
    )
    return client


class TestYouTubeComments:
    """Comment-thread ingestion: 1 unit per call for up to 100 comments. The
    parent video is already city-verified, so the adapter must not re-gate on
    city text — only on comment length. A 403 means comments are disabled on
    that video and must be skipped, not treated as a hard failure."""

    def setup_method(self):
        self.adapter = YouTubeAdapter()
        self.adapter.quota = AsyncMock()
        self.adapter.quota.reserve = AsyncMock(return_value=True)
        self.adapter.quota.refund = AsyncMock()
        self.adapter.quota.spent = AsyncMock(return_value=0)

    def _configured(self, monkeypatch):
        monkeypatch.setattr("app.ingestion.youtube.settings.youtube_api_key", "test-key")

    async def test_unconfigured_adapter_returns_nothing(self):
        assert self.adapter.configured is False
        assert await self.adapter.fetch_comments_for_videos(["v1"], KOLKATA) == []
        self.adapter.quota.reserve.assert_not_awaited()

    async def test_comment_becomes_a_review_with_expected_fields(self, monkeypatch):
        self._configured(monkeypatch)
        payload = {
            "items": [
                _comment_item("c1", text="This biryani changed my life, I'm serious", likes=42)
            ]
        }
        _patch_youtube_client(monkeypatch, {"v1": payload})

        reviews = await self.adapter.fetch_comments_for_videos(["v1"], KOLKATA)

        assert len(reviews) == 1
        r = reviews[0]
        assert r.source is SourceType.YOUTUBE
        assert r.external_id == "yt:v1#c:c1"
        assert "#c:" in r.external_id
        assert r.engagement == 42
        assert r.author == "HungryHuman"
        assert r.url == "https://www.youtube.com/watch?v=v1"
        assert r.license == "youtube-api-tos"
        assert "YouTube" in (r.attribution or "")
        assert r.raw == {"video_id": "v1", "comment_id": "c1"}
        assert r.restaurant_hint is None  # prose gates are handled downstream

    async def test_published_at_falls_back_to_updated_at(self, monkeypatch):
        """Some comments carry only `updatedAt`; the DTO must still parse."""
        self._configured(monkeypatch)
        payload = {
            "items": [
                _comment_item(
                    "c2",
                    text="I detour across the city for this roll, always fresh",
                    publishedAt=None,
                )
            ]
        }
        _patch_youtube_client(monkeypatch, {"v1": payload})

        reviews = await self.adapter.fetch_comments_for_videos(["v1"], KOLKATA)

        assert reviews[0].published_at == datetime(2025, 5, 2, 12, tzinfo=UTC)

    async def test_short_comments_are_filtered(self, monkeypatch):
        """Anything under MIN_TEXT_LENGTH is chit-chat, not evidence."""
        self._configured(monkeypatch)
        payload = {
            "items": [
                _comment_item("short", text="lol nice"),
                _comment_item(
                    "long", text="The phuchka here is genuinely the best I've had this year"
                ),
            ]
        }
        _patch_youtube_client(monkeypatch, {"v1": payload})

        reviews = await self.adapter.fetch_comments_for_videos(["v1"], KOLKATA)

        assert [r.external_id for r in reviews] == ["yt:v1#c:long"]

    async def test_comments_disabled_video_is_skipped_not_fatal(self, monkeypatch):
        """A 403 means comments are disabled on that video: skip it and keep
        the run alive. The reserved unit is refunded, and the next video is
        still fetched."""
        self._configured(monkeypatch)
        fake_request = httpx.Request("GET", "https://youtube.test/commentThreads")
        payload_v2 = {
            "items": [
                _comment_item("cz", text="Second video actually has a really great comment here")
            ]
        }
        _patch_youtube_client(
            monkeypatch,
            {
                "v1": httpx.Response(403, request=fake_request),
                "v2": payload_v2,
            },
        )

        reviews = await self.adapter.fetch_comments_for_videos(["v1", "v2"], KOLKATA)

        assert [r.external_id for r in reviews] == ["yt:v2#c:cz"]
        # The 403 call still reached YouTube (1 unit billed), so it is NOT
        # refunded — only failed calls are. Both videos reserved one unit each.
        assert self.adapter.quota.reserve.await_count == 2
        self.adapter.quota.refund.assert_not_awaited()

    async def test_quota_refusal_mid_run_returns_partial(self, monkeypatch):
        """A refused reservation stops the run with what was collected so far,
        and no API call is issued for the video that was denied."""
        self._configured(monkeypatch)
        self.adapter.quota.reserve = AsyncMock(side_effect=[True, False])
        payload = {
            "items": [
                _comment_item(
                    "c1", text="A long, tasty comment about the phuchka at this exact stall"
                )
            ]
        }
        client = _patch_youtube_client(monkeypatch, {"v1": payload})

        reviews = await self.adapter.fetch_comments_for_videos(["v1", "v2"], KOLKATA)

        assert [r.external_id for r in reviews] == ["yt:v1#c:c1"]
        assert self.adapter.quota.reserve.await_count == 2
        # Only one API call was made: v2 was refused before touching the network.
        assert [p.get("videoId") for p in client.requested] == ["v1"]

    async def test_request_error_refunds_the_reservation(self, monkeypatch):
        """A transient 5xx/timeout must refund the unit and surface the error as
        a `TransientSourceError` so Celery can back off and retry."""
        self._configured(monkeypatch)
        _patch_youtube_client(monkeypatch, {"v1": httpx.ConnectTimeout("slow")})

        with pytest.raises(TransientSourceError):
            await self.adapter.fetch_comments_for_videos(["v1"], KOLKATA)

        assert self.adapter.quota.refund.await_count == 1
        self.adapter.quota.refund.assert_awaited_with(COST_COMMENTS)

    async def test_permanent_error_refunds_and_halts(self, monkeypatch):
        """A non-403 client failure (e.g. 404) surfaces as PermanentSourceError,
        refunding the reserved unit and halting the run — it is not the
        commentsDisabled skip path."""
        self._configured(monkeypatch)
        fake_request = httpx.Request("GET", "https://youtube.test/commentThreads")
        _patch_youtube_client(
            monkeypatch, {"v1": httpx.Response(404, request=fake_request, text="Not Found")}
        )

        from app.core.errors import PermanentSourceError

        with pytest.raises(PermanentSourceError):
            await self.adapter.fetch_comments_for_videos(["v1"], KOLKATA)

        assert self.adapter.quota.refund.await_count == 1
        self.adapter.quota.refund.assert_awaited_with(COST_COMMENTS)


class TestRegistry:
    def test_known_sources_resolve(self):
        assert isinstance(get_adapter(SourceType.OSM), OverpassAdapter)
        assert isinstance(get_adapter("reddit"), RedditAdapter)
        assert isinstance(get_adapter("youtube"), YouTubeAdapter)

    def test_unknown_source_raises(self):
        with pytest.raises(ValueError, match="No ingestion adapter"):
            get_adapter("tripadvisor")

    def test_user_source_has_no_fetch_adapter(self):
        """User reviews arrive through the API, not a scheduled fetch."""
        assert all(adapter.source is not SourceType.USER for adapter in enabled_adapters())

    def test_intervals_are_within_the_required_range(self):
        for source in (SourceType.OSM, SourceType.REDDIT, SourceType.YOUTUBE):
            assert 1 <= interval_hours(source) <= 168

    def test_google_source_resolves_with_a_monthly_interval(self):
        assert isinstance(get_adapter("google"), GooglePlacesAdapter)
        assert 1 <= interval_hours(SourceType.GOOGLE) <= 2160


def _gplace(
    place_id: str, *, name: str = "Test Kitchen", rating=4.2, count=123, **overrides
) -> dict:
    """A Places API (New) item shaped like a `places:searchNearby` response row."""
    item: dict = {
        "id": place_id,
        "displayName": {"text": name, "languageCode": "en"},
        "formattedAddress": "1 Park Street, Kolkata, West Bengal 700016",
        "location": {"latitude": 22.55, "longitude": 88.35},
        "rating": rating,
        "userRatingCount": count,
        "types": ["restaurant"],
        "priceLevel": "PRICE_LEVEL_MODERATE",
        "websiteUri": "https://example.com",
    }
    item.update(overrides)
    return item


class _FakePlacesClient:
    """httpx.AsyncClient stand-in routing POST places:searchNearby through a
    handler(body, call_index) that returns a JSON payload, an httpx.Response
    (for 4xx plumbing) or raises."""

    def __init__(self, handler) -> None:
        self._handler = handler
        self.requests: list[dict] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        return False

    async def request(self, method, url, *, headers=None, **kwargs):
        self.requests.append(
            {"method": method, "url": url, "json": kwargs.get("json"), "headers": headers or {}}
        )
        result = self._handler(kwargs.get("json") or {}, len(self.requests))
        if isinstance(result, Exception):
            raise result
        fake_request = httpx.Request("POST", "https://places.googleapis.com/v1/places:searchNearby")
        if isinstance(result, httpx.Response):
            return result
        return httpx.Response(200, json=result, request=fake_request)


def _patch_places_client(monkeypatch, handler) -> _FakePlacesClient:
    client = _FakePlacesClient(handler)
    monkeypatch.setattr(
        "app.ingestion.google_places.httpx.AsyncClient",
        lambda **kwargs: client,
    )
    return client


class TestGooglePlacesParsing:
    def setup_method(self):
        self.adapter = GooglePlacesAdapter()

    def test_item_becomes_a_place_with_rating_fields(self):
        place = self.adapter._to_place(_gplace("ChIJabc123"))
        assert place is not None
        assert place.source is SourceType.GOOGLE
        assert place.external_id == "ChIJabc123"
        assert place.name == "Test Kitchen"
        assert place.lat == 22.55
        assert place.lng == 88.35
        assert place.rating == 4.2
        assert place.rating_count == 123
        assert place.price_level == 2
        assert place.website == "https://example.com"
        assert place.url == "https://www.google.com/maps/place/?q=place_id:ChIJabc123"
        assert place.raw == {"place_id": "ChIJabc123", "types": ["restaurant"]}

    def test_licence_and_attribution_are_recorded(self):
        place = self.adapter._to_place(_gplace("ChIJabc123"))
        assert place is not None
        assert place.license == "google-maps-platform-tos"
        assert place.attribution == "Google"

    def test_permanently_closed_places_are_dropped(self):
        assert self.adapter._to_place(_gplace("ChIJx", businessStatus="CLOSED_PERMANENTLY")) is None

    def test_places_without_coordinates_are_dropped(self):
        assert self.adapter._to_place(_gplace("ChIJx", location={})) is None

    def test_unnamed_places_are_dropped(self):
        assert self.adapter._to_place(_gplace("ChIJx", displayName={})) is None

    def test_missing_rating_stays_none(self):
        place = self.adapter._to_place(_gplace("ChIJx", rating=None, userRatingCount=None))
        assert place is not None
        assert place.rating is None
        assert place.rating_count is None


class TestGooglePlacesFieldMask:
    """The bill is per-field-tier. reviews/editorialSummary/photos are Enterprise
    SKUs ($6-12/1000) — the mask must stay pinned to Pro fields only."""

    def test_mask_carries_the_pro_rating_fields(self):
        assert "places.rating" in FIELD_MASK
        assert "places.userRatingCount" in FIELD_MASK

    def test_mask_contains_no_enterprise_fields(self):
        for banned in ("reviews", "editorialSummary", "photos", "tmosphere"):
            assert banned not in FIELD_MASK

    def test_mask_is_the_frozen_policy_string(self):
        assert FIELD_MASK == (
            "places.id,places.displayName,places.formattedAddress,places.location,"
            "places.rating,places.userRatingCount,places.types,places.priceLevel,"
            "places.websiteUri"
        )


class TestGooglePlacesGrid:
    def test_grid_stays_inside_the_city_radius(self):
        centers = grid_centers(KOLKATA.lat, KOLKATA.lng, KOLKATA.radius_m)
        assert 250 <= len(centers) <= 550
        for lat, lng in centers:
            assert haversine_m(KOLKATA.lat, KOLKATA.lng, lat, lng) <= KOLKATA.radius_m

    def test_call_count_is_bounded_by_cells_times_pages(self, monkeypatch):
        """One cell = at most MAX_PAGES_PER_CELL requests, ever."""
        monkeypatch.setattr("app.ingestion.google_places.settings.google_places_api_key", "k")
        monkeypatch.setattr("app.ingestion.google_places._places_limiter.min_interval", 0.0)
        adapter = GooglePlacesAdapter()
        adapter.quota = AsyncMock()
        adapter.quota.reserve = AsyncMock(return_value=True)
        adapter.quota.refund = AsyncMock()
        adapter.quota.spent = AsyncMock(return_value=0)

        def handler(_body, call_index):
            return {"places": [_gplace(f"p{call_index}")]}

        client = _patch_places_client(monkeypatch, handler)
        expected_cells = len(grid_centers(KOLKATA.lat, KOLKATA.lng, KOLKATA.radius_m))

        places = asyncio.run(adapter.discover_places(KOLKATA))

        assert len(client.requests) == expected_cells  # no nextPageToken → 1 call/cell
        assert len(client.requests) <= expected_cells * MAX_PAGES_PER_CELL
        assert len(places) == expected_cells


class TestGooglePlacesSweep:
    """Sweep mechanics against a single-cell city: reserve/refund discipline,
    quota exhaustion, per-run cap, pagination and dedup."""

    def setup_method(self):
        self.adapter = GooglePlacesAdapter()
        self.adapter.quota = AsyncMock()
        self.adapter.quota.reserve = AsyncMock(return_value=True)
        self.adapter.quota.refund = AsyncMock()
        self.adapter.quota.spent = AsyncMock(return_value=0)
        self.small_city = CityRef(
            id="c2", slug="tiny", name="Tiny", lat=22.57, lng=88.36, radius_m=800
        )

    def _configured(self, monkeypatch):
        monkeypatch.setattr(
            "app.ingestion.google_places.settings.google_places_api_key", "test-key"
        )
        monkeypatch.setattr("app.ingestion.google_places._places_limiter.min_interval", 0.0)

    async def test_unconfigured_adapter_returns_empty_without_touching_quota(self):
        assert self.adapter.configured is False
        assert await self.adapter.discover_places(self.small_city) == []
        self.adapter.quota.reserve.assert_not_awaited()

    async def test_successful_sweep_requires_and_never_refunds(self, monkeypatch):
        """A successful response is billed by Google — it must never be refunded."""
        self._configured(monkeypatch)
        _patch_places_client(monkeypatch, lambda body, i: {"places": [_gplace(f"p{i}")]})

        places = await self.adapter.discover_places(self.small_city)

        assert len(places) == 1
        assert self.adapter.quota.reserve.await_count == 1
        self.adapter.quota.refund.assert_not_awaited()

    async def test_monthly_guard_exhaustion_mid_sweep_returns_partial(self, monkeypatch):
        """Once the monthly budget is gone the sweep stops with what it has,
        and no further API call is issued."""
        self._configured(monkeypatch)
        self.adapter.quota.reserve = AsyncMock(side_effect=[True, False] + [False] * 10)

        def handler(body, call_index):
            return {"places": [_gplace("kept")]}

        client = _patch_places_client(monkeypatch, handler)
        places = await self.adapter.discover_places(self.small_city)

        assert [p.external_id for p in places] == ["kept"]
        assert len(client.requests) == 1  # refused reservation → no network call

    async def test_failed_call_refunds_the_reserved_unit(self, monkeypatch):
        """A network failure is not billable: refund and surface as transient."""
        from app.core.errors import TransientSourceError as _Transient

        self._configured(monkeypatch)
        _patch_places_client(monkeypatch, lambda body, i: httpx.ConnectError("no route"))

        with pytest.raises(_Transient):
            await self.adapter.discover_places(self.small_city)

        self.adapter.quota.refund.assert_awaited_once_with(1)

    async def test_429_is_transient_and_refunded(self, monkeypatch):
        from app.core.errors import TransientSourceError as _Transient

        self._configured(monkeypatch)
        fake_request = httpx.Request("POST", "https://places.googleapis.com/v1/places:searchNearby")
        _patch_places_client(monkeypatch, lambda body, i: httpx.Response(429, request=fake_request))

        with pytest.raises(_Transient):
            await self.adapter.discover_places(self.small_city)

        self.adapter.quota.refund.assert_awaited_once_with(1)

    async def test_credential_error_is_not_refunded(self, monkeypatch):
        """401/403 reached Google (billed) — no refund; the run fails with a
        PermanentSourceError so the job row marks FAILED."""
        from app.core.errors import PermanentSourceError as _Permanent

        self._configured(monkeypatch)
        fake_request = httpx.Request("POST", "https://places.googleapis.com/v1/places:searchNearby")
        _patch_places_client(monkeypatch, lambda body, i: httpx.Response(403, request=fake_request))

        with pytest.raises(_Permanent):
            await self.adapter.discover_places(self.small_city)

        self.adapter.quota.refund.assert_not_awaited()

    async def test_per_run_cap_stops_the_sweep(self, monkeypatch):
        monkeypatch.setattr(
            "app.ingestion.google_places.settings.google_places_max_requests_per_run", 2
        )
        self._configured(monkeypatch)

        def handler(body, call_index):
            return {"places": [_gplace(f"p{call_index}")]}

        client = _patch_places_client(monkeypatch, handler)
        # Full-size city: more cells than the cap, so the loop must stop at 2.
        places = await self.adapter.discover_places(KOLKATA)

        assert len(client.requests) == 2
        assert len(places) == 2

    async def test_pagination_follows_next_page_token_up_to_three_pages(self, monkeypatch):
        self._configured(monkeypatch)

        def handler(body, call_index):
            token = body.get("pageToken")
            if token is None:
                return {"places": [_gplace("a1"), _gplace("a2")], "nextPageToken": "tok1"}
            if token == "tok1":
                return {"places": [_gplace("a3")], "nextPageToken": "tok2"}
            return {"places": [_gplace("a4")]}  # third page, no token → stop

        client = _patch_places_client(monkeypatch, handler)
        places = await self.adapter.discover_places(self.small_city)

        assert len(client.requests) == 3  # hard page cap, never more
        assert [p.external_id for p in places] == ["a1", "a2", "a3", "a4"]

    async def test_duplicate_place_ids_across_cells_are_deduped(self, monkeypatch):
        self._configured(monkeypatch)
        _patch_places_client(monkeypatch, lambda body, i: {"places": [_gplace("same-place")]})

        places = await self.adapter.discover_places(self.small_city)

        assert [p.external_id for p in places] == ["same-place"]

    async def test_non_credential_4xx_skips_the_cell_only(self, monkeypatch):
        """A 400 is per-cell and billed: no refund, skip the bad cell, keep the run."""
        self._configured(monkeypatch)
        fake_request = httpx.Request("POST", "https://places.googleapis.com/v1/places:searchNearby")

        def handler(body, call_index):
            if call_index == 1:
                return httpx.Response(400, request=fake_request, text="INVALID_ARGUMENT")
            return {"places": [_gplace("ok")]}

        client = _patch_places_client(monkeypatch, handler)
        # Full-size city: cell 1 returns 400 (skipped, still billed), the rest
        # succeed and dedupe down to a single place.
        places = await self.adapter.discover_places(KOLKATA)

        assert len(client.requests) > 1  # the run continued past the bad cell
        assert [p.external_id for p in places] == ["ok"]
        self.adapter.quota.refund.assert_not_awaited()


class TestPlacesQuotaGuard:
    """The monthly Google budget must be hard-enforced: 35k free Pro requests on
    the India profile, guard budget 30k — an overrun is real money."""

    async def test_reservations_succeed_within_budget(self, monkeypatch):
        fake = FakeRedis()
        monkeypatch.setattr("app.ingestion.google_places.get_redis", lambda: fake)
        guard = PlacesQuotaGuard(budget=100)

        assert await guard.reserve(1) is True
        assert await guard.reserve(1) is True
        assert await guard.spent() == 2

    async def test_reservation_crossing_budget_is_refused_and_rolled_back(self, monkeypatch):
        fake = FakeRedis()
        monkeypatch.setattr("app.ingestion.google_places.get_redis", lambda: fake)
        guard = PlacesQuotaGuard(budget=2)

        assert await guard.reserve(2) is True
        assert await guard.reserve(1) is False
        assert await guard.spent() == 2

    async def test_key_is_monthly(self, monkeypatch):
        fake = FakeRedis()
        monkeypatch.setattr("app.ingestion.google_places.get_redis", lambda: fake)
        guard = PlacesQuotaGuard(budget=10)

        await guard.reserve(1)
        assert list(fake.store) == [f"gplaces:calls:{datetime.now(UTC):%Y-%m}"]

    async def test_refund_returns_units_when_the_call_is_not_billed(self, monkeypatch):
        fake = FakeRedis()
        monkeypatch.setattr("app.ingestion.google_places.get_redis", lambda: fake)
        guard = PlacesQuotaGuard(budget=10)

        assert await guard.reserve(1) is True
        await guard.refund(1)
        assert await guard.spent() == 0

    async def test_no_redis_means_run_is_allowed_but_capped_by_run_limit(self, monkeypatch):
        monkeypatch.setattr("app.ingestion.google_places.get_redis", lambda: None)
        guard = PlacesQuotaGuard(budget=100)

        assert await guard.reserve(1) is True
        assert await guard.spent() == 0


class TestNormalization:
    def test_names_normalize_for_matching(self):
        assert normalize_name("Peter Cat & Co.") == "peter cat and co"
        assert normalize_name("  WOW!  Momo  ") == "wow momo"

    def test_accents_are_folded(self):
        assert normalize_name("Café Déjà Vu") == "cafe deja vu"

    def test_slugs_are_url_safe(self):
        assert slugify("Wow! Momo — Salt Lake") == "wow-momo-salt-lake"

    def test_markup_and_urls_are_stripped(self):
        cleaned = clean_text("**Great** momo! see https://example.com for more")
        assert "**" not in cleaned
        assert "https://" not in cleaned
        assert "momo" in cleaned

    def test_text_is_truncated_for_model_safety(self):
        assert len(clean_text("x" * 20000)) == 8000

    def test_prices_are_extracted_in_several_formats(self):
        assert 120.0 in extract_prices("it was ₹120")
        assert 200.0 in extract_prices("Rs. 200 for two")
        assert 150.0 in extract_prices("150/-")
        assert 90.0 in extract_prices("INR 90")

    def test_absurd_amounts_are_ignored(self):
        assert extract_prices("₹99999999") == []

    def test_content_hash_ignores_whitespace_and_case(self):
        assert content_hash("The Momo Was Great") == content_hash("  the   momo was great  ")

    def test_content_hash_differs_for_different_text(self):
        assert content_hash("great momo") != content_hash("terrible momo")
