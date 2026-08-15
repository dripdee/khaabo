"""Ingestion adapter tests.

External calls are never made here. What is verified is the part that determines data
quality: normalization, licence/attribution capture, relevance filtering, quota
guarding, and that adapters degrade to a no-op when unconfigured rather than crashing
a scheduled job.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.ingestion.base import CityRef
from app.ingestion.osm import OSM_ATTRIBUTION, OverpassAdapter, build_overpass_query
from app.ingestion.reddit import RedditAdapter
from app.ingestion.registry import enabled_adapters, get_adapter, interval_hours
from app.ingestion.youtube import YouTubeAdapter
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
