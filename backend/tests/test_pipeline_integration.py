"""End-to-end pipeline test, without a database.

Runs real review text through the real stages — extraction → observations → scoring →
badges → trends → explanation — and asserts the product's promises hold on the whole
chain, not just on each unit in isolation.
"""

from __future__ import annotations

import random
from datetime import UTC, datetime, timedelta

import pytest

from app.ai import HeuristicProvider, ReviewAnalysisRequest, enforce_grounding
from app.models.enums import ScoreStatus, SourceType, TrendDirection
from app.services.dedup import is_near_duplicate, review_fingerprint, simhash
from app.services.ranking import (
    Observation,
    RankedEntry,
    assign_badges,
    compute_dish_score,
)
from app.services.trends import TrendConfig, detect_trend

NOW = datetime(2026, 8, 15, tzinfo=UTC)

ALIAS_INDEX = {
    "chicken momo": "chicken-momo",
    "momo": "steamed-momo",
    "biryani": "chicken-biryani",
    "chicken biryani": "chicken-biryani",
    "kathi roll": "kathi-roll",
    "cold coffee": "cold-coffee",
}
KNOWN_DISHES = ["Chicken Momo", "Steamed Momo", "Chicken Biryani", "Kathi Roll", "Cold Coffee"]

TREND_CONFIG = TrendConfig(
    recent_days=60, historical_days=240, delta_threshold=0.08, min_observations=3
)


async def analyze(text: str, *, rating: float | None = None) -> list:
    """Run one review through extraction + grounding, as the worker would."""
    request = ReviewAnalysisRequest(
        text=text,
        known_dishes=KNOWN_DISHES,
        alias_index=ALIAS_INDEX,
        rating=rating,
        rating_scale=5.0 if rating is not None else None,
    )
    analysis = await HeuristicProvider().analyze_review(request)
    return enforce_grounding(analysis, request).dish_mentions


def to_observation(mention, *, days_ago: int, source: str = SourceType.USER) -> Observation:
    return Observation(
        sentiment=mention.sentiment,
        confidence=mention.confidence,
        source=source,
        observed_at=NOW - timedelta(days=days_ago),
        price=mention.price_mentioned,
        attributes=tuple(mention.attributes),
        extraction_method="alias",
    )


class TestFullChain:
    async def test_review_text_becomes_a_ranked_score_with_an_explanation(self):
        texts = [
            "The chicken momo here is amazing, juicy and hot at ₹120.",
            "Chicken momo was excellent and very fresh, worth every rupee at ₹130.",
            "Loved the chicken momo, soft and perfectly spiced. ₹110 for eight.",
            "Chicken momo is delicious and the portion was generous, ₹125.",
            "The chicken momo was great, definitely recommend it. ₹120.",
        ]

        observations: list[Observation] = []
        for index, text in enumerate(texts):
            mentions = await analyze(text)
            assert mentions, f"extraction found nothing in: {text}"
            for mention in mentions:
                if mention.matched_alias == "chicken momo":
                    observations.append(to_observation(mention, days_ago=5 + index * 3))

        result = compute_dish_score(observations, now=NOW)

        assert result.status is ScoreStatus.RANKED
        assert result.score is not None and result.score > 60
        assert result.mention_count == 5
        assert result.price_avg is not None
        assert any(reason["code"] == "mentions" for reason in result.why)
        assert f"{result.mention_count} dish mentions" in " ".join(
            reason["label"] for reason in result.why
        )

    async def test_multi_dish_review_splits_into_independent_scores(self):
        """The brief's canonical case, carried all the way to two separate scores."""
        text = "Chicken momo is amazing but the biryani is average."
        mentions = await analyze(text)

        by_dish = {mention.matched_alias: mention for mention in mentions}
        assert "chicken momo" in by_dish
        assert "biryani" in by_dish

        momo = compute_dish_score(
            [to_observation(by_dish["chicken momo"], days_ago=i) for i in range(1, 6)], now=NOW
        )
        biryani = compute_dish_score(
            [to_observation(by_dish["biryani"], days_ago=i) for i in range(1, 6)], now=NOW
        )

        assert momo.status is ScoreStatus.RANKED
        assert biryani.status is ScoreStatus.RANKED
        assert (
            momo.score > biryani.score
        ), "praised dish must outrank the one called average at the same place"

    async def test_negative_review_lowers_the_score(self):
        good = "The chicken momo was excellent, juicy and fresh."
        bad = "The chicken momo was terrible, cold and stale."

        good_mentions = await analyze(good)
        bad_mentions = await analyze(bad)

        good_score = compute_dish_score(
            [to_observation(good_mentions[0], days_ago=i) for i in range(1, 7)], now=NOW
        )
        bad_score = compute_dish_score(
            [to_observation(bad_mentions[0], days_ago=i) for i in range(1, 7)], now=NOW
        )
        assert bad_score.score < good_score.score


class TestInsufficientDataEndToEnd:
    async def test_a_single_glowing_review_does_not_produce_a_rank(self):
        mentions = await analyze("The chicken momo was the best thing I have ever eaten.")
        result = compute_dish_score([to_observation(mentions[0], days_ago=2)], now=NOW)

        assert result.status is ScoreStatus.INSUFFICIENT_DATA
        assert result.score is None
        assert result.why == []


class TestPeerGroupBadges:
    async def test_badges_are_assigned_across_a_realistic_peer_group(self):
        async def entry(restaurant_id: str, text: str, count: int, days_ago: int) -> RankedEntry:
            mentions = await analyze(text)
            momo = next(m for m in mentions if m.matched_alias == "chicken momo")
            observations = [to_observation(momo, days_ago=days_ago + i) for i in range(count)]
            return RankedEntry(
                restaurant_id=restaurant_id,
                result=compute_dish_score(observations, now=NOW, city_median_price=200.0),
            )

        entries = [
            await entry(
                "cheap-and-good",
                "The chicken momo is excellent and fresh at ₹80.",
                8,
                3,
            ),
            await entry(
                "popular",
                "The chicken momo was good at ₹250.",
                60,
                4,
            ),
            await entry(
                "quiet-gem",
                "The chicken momo is absolutely amazing and juicy at ₹220.",
                6,
                5,
            ),
        ]

        assign_badges(entries)
        by_id = {entry.restaurant_id: entry.result for entry in entries}

        assert by_id["cheap-and-good"].is_best_value, "cheapest good option should win value"
        assert not by_id["popular"].is_hidden_gem, "a heavily reviewed place is not hidden"


class TestTrendEndToEnd:
    async def test_a_real_improvement_registers_as_rising(self):
        recent_mentions = await analyze("The chicken momo is amazing and fresh now.")
        old_mentions = await analyze("The chicken momo was bland and cold.")

        recent = [to_observation(recent_mentions[0], days_ago=day) for day in (5, 18, 33, 47)]
        historical = [to_observation(old_mentions[0], days_ago=day) for day in (95, 130, 168, 205)]

        trend = detect_trend(recent + historical, config=TREND_CONFIG, now=NOW)
        assert trend.direction is TrendDirection.RISING
        assert trend.significant

    async def test_a_real_decline_registers_as_declining(self):
        recent_mentions = await analyze("The chicken momo was stale and disappointing.")
        old_mentions = await analyze("The chicken momo was excellent and juicy.")

        recent = [to_observation(recent_mentions[0], days_ago=day) for day in (4, 20, 36, 52)]
        historical = [to_observation(old_mentions[0], days_ago=day) for day in (100, 140, 175, 210)]

        trend = detect_trend(recent + historical, config=TREND_CONFIG, now=NOW)
        assert trend.direction is TrendDirection.DECLINING


class TestDedupEndToEnd:
    def test_a_reposted_review_is_caught_before_it_double_counts(self):
        original = "The chicken momo here is juicy, hot and honestly the best in Salt Lake."
        repost = "  the CHICKEN momo here is juicy,  hot and honestly the best in Salt Lake. "

        assert review_fingerprint(original, "u1", None) == review_fingerprint(repost, "u1", None)

    def test_a_lightly_edited_repost_is_flagged_as_a_near_duplicate(self):
        original = "The chicken momo here is juicy, hot and honestly the best in Salt Lake."
        edited = "The chicken momo here is juicy, hot and honestly the finest in Salt Lake."

        verdict = is_near_duplicate(edited, [("r1", simhash(original), original)])
        assert verdict.is_duplicate

    def test_two_genuine_reviews_of_the_same_dish_both_count(self):
        first = "The chicken momo was juicy and the chutney had a real kick to it."
        second = "Came for the momo, stayed for the thukpa. Both were excellent value."

        verdict = is_near_duplicate(second, [("r1", simhash(first), first)])
        assert not verdict.is_duplicate


class TestVolumeStability:
    async def test_a_large_realistic_corpus_produces_a_stable_score(self):
        """Guards against a scoring change that only behaves on tiny inputs."""
        random.seed(7)
        positive = "The chicken momo was excellent, juicy and fresh at ₹120."
        mixed = "The chicken momo was decent, nothing special for ₹120."
        negative = "The chicken momo was oily and cold for ₹120."

        pool = []
        for text, weight in ((positive, 70), (mixed, 22), (negative, 8)):
            mentions = await analyze(text)
            momo = next(m for m in mentions if m.matched_alias == "chicken momo")
            pool.extend([momo] * weight)

        observations = [
            to_observation(mention, days_ago=random.randint(1, 300)) for mention in pool
        ]
        result = compute_dish_score(observations, now=NOW, city_median_price=200.0)

        assert result.status is ScoreStatus.RANKED
        assert 0 <= result.score <= 100
        assert result.mention_count == 100
        assert 0.6 <= result.positive_ratio <= 0.8
        assert result.value_score is not None
        for name, value in result.components.as_dict().items():
            assert 0.0 <= value <= 1.0, f"{name} escaped its range"

    async def test_scoring_is_deterministic(self):
        mentions = await analyze("The chicken momo was excellent and juicy at ₹120.")
        observations = [to_observation(mentions[0], days_ago=i) for i in range(1, 11)]

        first = compute_dish_score(observations, now=NOW)
        second = compute_dish_score(observations, now=NOW)
        assert first.score == second.score
        assert first.why == second.why


class TestGroundingEndToEnd:
    async def test_prices_only_survive_when_the_text_contains_them(self):
        with_price = await analyze("The chicken momo was great at ₹150.")
        without_price = await analyze("The chicken momo was great.")

        assert with_price[0].price_mentioned == 150.0
        assert without_price[0].price_mentioned is None

    async def test_an_unknown_dish_is_not_invented(self):
        mentions = await analyze("The tonkotsu tantanmen was unbelievable.")
        assert mentions == []

    @pytest.mark.parametrize(
        "text",
        [
            "",
            "   ",
            "ok",
            "!!!!!!",
            "12345",
        ],
    )
    async def test_degenerate_input_never_raises(self, text: str):
        """Ingested text is untrusted; the pipeline must not crash on junk."""
        if not text.strip():
            pytest.skip("empty text is rejected by the schema before analysis")
        mentions = await analyze(text)
        assert isinstance(mentions, list)
