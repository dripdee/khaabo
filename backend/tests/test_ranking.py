"""Ranking algorithm tests.

The safety properties asserted here are the ones the product's credibility rests on:
* a handful of glowing mentions must not outrank established evidence
* thin evidence must return `insufficient_data`, never a fabricated score
* consistency must reward low dispersion, not a high mean
* explanations must be derived from the numbers, never invented
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.models.enums import ScoreStatus, SourceType
from app.services.ranking import (
    Observation,
    RankedEntry,
    RankingWeights,
    aggregate_dish_score,
    assign_badges,
    bayesian_shrink,
    build_why,
    compute_dish_score,
    source_quality_for,
    time_decay,
)

NOW = datetime(2026, 8, 15, tzinfo=UTC)


def obs(
    sentiment: float,
    *,
    days_ago: int = 10,
    confidence: float = 0.8,
    source: str = SourceType.USER,
    engagement: int = 0,
    price: float | None = None,
    attributes: tuple[str, ...] = (),
    method: str = "ai",
) -> Observation:
    return Observation(
        sentiment=sentiment,
        confidence=confidence,
        source=source,
        engagement=engagement,
        observed_at=NOW - timedelta(days=days_ago),
        price=price,
        attributes=attributes,
        extraction_method=method,
    )


class TestWeights:
    def test_default_weights_sum_to_one(self):
        RankingWeights().validate()

    def test_settings_weights_sum_to_one(self):
        RankingWeights.from_settings().validate()

    def test_mismatched_weights_are_rejected(self):
        with pytest.raises(ValueError, match="sum to 1.0"):
            RankingWeights(sentiment=0.9, recency=0.9).validate()

    def test_weights_version_is_recorded_on_result(self):
        result = compute_dish_score([obs(0.9)] * 5, weights=RankingWeights(version="v7"), now=NOW)
        assert result.weights_version == "v7"


class TestInsufficientData:
    def test_no_observations_returns_insufficient(self):
        result = compute_dish_score([], now=NOW)
        assert result.status is ScoreStatus.INSUFFICIENT_DATA
        assert result.score is None

    def test_two_mentions_is_not_enough(self):
        result = compute_dish_score([obs(1.0), obs(0.95)], now=NOW)
        assert result.status is ScoreStatus.INSUFFICIENT_DATA
        assert result.score is None
        assert result.mention_count == 2

    def test_three_mentions_clears_the_gate(self):
        result = compute_dish_score([obs(0.9)] * 3, now=NOW)
        assert result.status is ScoreStatus.RANKED
        assert result.score is not None

    def test_many_but_very_weak_mentions_stay_insufficient(self):
        """Low confidence + ancient + weak source must not clear the weight floor."""
        weak = [obs(0.9, confidence=0.05, days_ago=2000, source=SourceType.OSM) for _ in range(4)]
        result = compute_dish_score(weak, now=NOW)
        assert result.status is ScoreStatus.INSUFFICIENT_DATA

    def test_insufficient_result_has_no_why_reasons(self):
        result = compute_dish_score([obs(1.0), obs(1.0)], now=NOW)
        assert result.why == []


class TestBayesianShrinkage:
    def test_shrink_pulls_toward_prior_with_low_weight(self):
        assert bayesian_shrink(1.0, 1.0, 0.6, 6.0) == pytest.approx(0.6571, abs=1e-3)

    def test_shrink_barely_moves_with_high_weight(self):
        assert bayesian_shrink(0.9, 400.0, 0.6, 6.0) == pytest.approx(0.8956, abs=1e-3)

    def test_zero_weight_returns_prior(self):
        assert bayesian_shrink(1.0, 0.0, 0.62, 6.0) == 0.62

    def test_two_perfect_reviews_cannot_beat_five_hundred_good_ones(self):
        """The central safety property of the whole ranking."""
        tiny = compute_dish_score([obs(1.0), obs(1.0), obs(1.0)], prior=0.62, now=NOW)
        established = compute_dish_score(
            [obs(0.75, days_ago=i % 300) for i in range(500)], prior=0.62, now=NOW
        )

        assert tiny.status is ScoreStatus.RANKED
        assert established.status is ScoreStatus.RANKED
        assert established.score > tiny.score, (
            f"500 consistent mentions ({established.score}) must outrank "
            f"3 perfect ones ({tiny.score})"
        )

    def test_shrinkage_reduces_sentiment_more_for_small_samples(self):
        small = compute_dish_score([obs(1.0)] * 3, prior=0.6, now=NOW)
        large = compute_dish_score([obs(1.0)] * 100, prior=0.6, now=NOW)
        assert large.bayesian_score > small.bayesian_score


class TestRecency:
    def test_decay_at_halflife_is_half(self):
        assert time_decay(180, 180) == pytest.approx(0.5)

    def test_decay_is_one_for_fresh_evidence(self):
        assert time_decay(0, 180) == 1.0

    def test_recent_beats_stale_at_equal_sentiment(self):
        fresh = compute_dish_score([obs(0.8, days_ago=3)] * 6, now=NOW)
        stale = compute_dish_score([obs(0.8, days_ago=900)] * 6, now=NOW)
        assert fresh.score > stale.score

    def test_age_lowers_rank_but_does_not_disqualify(self):
        """Age is priced into the recency component only.

        Sufficiency is judged on undecayed evidence weight, so a well-documented
        older dish ranks lower rather than vanishing into "not enough data".
        """
        stale = compute_dish_score([obs(0.8, days_ago=900)] * 6, now=NOW)
        assert stale.status is ScoreStatus.RANKED
        assert stale.components.recency < 0.1

    def test_recency_uses_the_freshest_mention(self):
        mixed = compute_dish_score(
            [obs(0.8, days_ago=900), obs(0.8, days_ago=900), obs(0.8, days_ago=1)], now=NOW
        )
        assert mixed.components.recency > 0.9

    def test_undated_observations_are_treated_as_old_not_fresh(self):
        undated = [Observation(sentiment=0.8, confidence=0.8) for _ in range(5)]
        result = compute_dish_score(undated, now=NOW)
        assert result.components.recency < 0.4


class TestConsistency:
    def test_low_dispersion_scores_higher_than_high_dispersion(self):
        steady = compute_dish_score([obs(0.9), obs(0.88), obs(0.92), obs(0.9), obs(0.89)], now=NOW)
        erratic = compute_dish_score([obs(1.0), obs(1.0), obs(-0.6), obs(1.0), obs(-0.5)], now=NOW)
        assert steady.consistency > erratic.consistency

    def test_unknown_consistency_is_neutral_not_perfect(self):
        result = compute_dish_score([obs(0.9), obs(0.9)], now=NOW)
        assert result.consistency == 0.5

    def test_identical_sentiment_is_maximally_consistent(self):
        result = compute_dish_score([obs(0.8)] * 6, now=NOW)
        assert result.consistency == pytest.approx(1.0)


class TestVolumeSaturation:
    def test_volume_saturates(self):
        fifty = compute_dish_score([obs(0.8)] * 50, now=NOW)
        five_hundred = compute_dish_score([obs(0.8)] * 500, now=NOW)
        assert five_hundred.components.volume - fifty.components.volume < 0.35

    def test_volume_component_is_bounded(self):
        result = compute_dish_score([obs(0.8)] * 5000, now=NOW)
        assert result.components.volume <= 1.0


class TestSourceQuality:
    def test_source_trust_ordering(self):
        assert source_quality_for(SourceType.USER) > source_quality_for(SourceType.REDDIT)
        assert source_quality_for(SourceType.REDDIT) > source_quality_for(SourceType.YOUTUBE)
        assert source_quality_for(SourceType.YOUTUBE) > source_quality_for(SourceType.OSM)

    def test_unverified_user_is_damped(self):
        assert source_quality_for(SourceType.USER, verified=False) < source_quality_for(
            SourceType.USER
        )

    def test_trusted_sources_score_higher(self):
        user = compute_dish_score([obs(0.8, source=SourceType.USER)] * 5, now=NOW)
        osm = compute_dish_score([obs(0.8, source=SourceType.OSM)] * 5, now=NOW)
        assert user.score > osm.score


class TestValueAndPrices:
    def test_value_score_is_none_without_price_signal(self):
        result = compute_dish_score([obs(0.9)] * 5, now=NOW)
        assert result.value_score is None
        assert result.price_avg is None

    def test_cheaper_at_equal_quality_scores_better_value(self):
        cheap = compute_dish_score([obs(0.9, price=100)] * 5, city_median_price=200, now=NOW)
        pricey = compute_dish_score([obs(0.9, price=400)] * 5, city_median_price=200, now=NOW)
        assert cheap.value_score > pricey.value_score

    def test_price_average_is_computed_from_mentions(self):
        result = compute_dish_score(
            [obs(0.8, price=100), obs(0.8, price=200), obs(0.8, price=300)], now=NOW
        )
        assert result.price_avg == pytest.approx(200.0)


class TestWhyExplanations:
    def test_why_always_includes_mention_count(self):
        result = compute_dish_score([obs(0.9)] * 42, now=NOW)
        codes = {reason["code"] for reason in result.why}
        assert "mentions" in codes
        mentions = next(r for r in result.why if r["code"] == "mentions")
        assert mentions["label"] == "42 dish mentions"

    def test_why_is_capped_at_four_reasons(self):
        result = compute_dish_score([obs(0.95, days_ago=1)] * 40, now=NOW)
        assert len(result.why) <= 4

    def test_why_reports_high_positive_ratio(self):
        result = compute_dish_score([obs(0.9)] * 10, now=NOW)
        labels = " ".join(r["label"] for r in result.why)
        assert "positive dish sentiment" in labels

    def test_why_can_be_unflattering(self):
        """An explanation that can only praise is marketing, not evidence."""
        reasons = build_why(
            components=_components(consistency=0.2, recency=0.3),
            positive_ratio=0.4,
            mention_count=9,
            recency_days=500,
        )
        codes = {r["code"] for r in reasons}
        assert "inconsistent" in codes or "stale" in codes

    def test_why_values_match_the_numbers(self):
        result = compute_dish_score([obs(0.9)] * 10, now=NOW)
        mentions = next(r for r in result.why if r["code"] == "mentions")
        assert mentions["value"] == result.mention_count


def _components(**overrides):
    from app.services.ranking import ScoreComponents

    defaults = {
        "sentiment": 0.8,
        "recency": 0.8,
        "consistency": 0.8,
        "volume": 0.5,
        "source_quality": 0.8,
        "engagement": 0.2,
        "confidence": 0.7,
    }
    defaults.update(overrides)
    return ScoreComponents(**defaults)


class TestBadges:
    def test_best_value_requires_a_price_signal(self):
        entries = [
            RankedEntry("a", compute_dish_score([obs(0.9)] * 5, now=NOW)),
            RankedEntry("b", compute_dish_score([obs(0.9)] * 5, now=NOW)),
        ]
        assign_badges(entries)
        assert not any(e.result.is_best_value for e in entries)

    def test_best_value_goes_to_the_cheapest_good_option(self):
        entries = [
            RankedEntry(
                "cheap",
                compute_dish_score([obs(0.9, price=80)] * 6, city_median_price=200, now=NOW),
            ),
            RankedEntry(
                "pricey",
                compute_dish_score([obs(0.9, price=500)] * 6, city_median_price=200, now=NOW),
            ),
        ]
        assign_badges(entries)
        assert entries[0].result.is_best_value
        assert not entries[1].result.is_best_value

    def test_most_consistent_requires_a_real_sample(self):
        entries = [
            RankedEntry("thin", compute_dish_score([obs(0.9)] * 3, now=NOW)),
            RankedEntry("thin2", compute_dish_score([obs(0.9)] * 4, now=NOW)),
        ]
        assign_badges(entries)
        assert not any(e.result.is_most_consistent for e in entries)

    def test_hidden_gem_is_excellent_but_under_discussed(self):
        entries = [
            RankedEntry("gem", compute_dish_score([obs(0.98, days_ago=5)] * 4, now=NOW)),
            RankedEntry("popular", compute_dish_score([obs(0.8)] * 80, now=NOW)),
            RankedEntry("mid", compute_dish_score([obs(0.7)] * 40, now=NOW)),
        ]
        assign_badges(entries)
        gem = next(e for e in entries if e.restaurant_id == "gem")
        popular = next(e for e in entries if e.restaurant_id == "popular")
        assert gem.result.is_hidden_gem
        assert not popular.result.is_hidden_gem

    def test_badges_are_never_given_to_insufficient_rows(self):
        entries = [RankedEntry("thin", compute_dish_score([obs(1.0)], now=NOW))]
        assign_badges(entries)
        result = entries[0].result
        assert not (result.is_best_value or result.is_hidden_gem or result.is_most_consistent)


class TestDishAggregate:
    def test_aggregate_uses_top_n_only(self):
        scores = [95.0, 90.0, 20.0, 10.0, 5.0]
        assert aggregate_dish_score(scores, limit=2) == pytest.approx(92.5)

    def test_aggregate_of_nothing_is_none(self):
        assert aggregate_dish_score([]) is None

    def test_one_outlier_does_not_define_the_dish(self):
        with_outlier = aggregate_dish_score([99.0, 40.0, 38.0, 35.0], limit=10)
        assert with_outlier < 99.0


class TestScoreBounds:
    def test_score_is_within_zero_to_hundred(self):
        for sentiment in (-1.0, -0.5, 0.0, 0.5, 1.0):
            result = compute_dish_score([obs(sentiment)] * 8, now=NOW)
            assert 0.0 <= result.score <= 100.0

    def test_all_components_are_normalized(self):
        result = compute_dish_score(
            [obs(0.9, engagement=100000, price=50, attributes=("spicy",))] * 20, now=NOW
        )
        for name, value in result.components.as_dict().items():
            assert 0.0 <= value <= 1.0, f"{name} out of range: {value}"

    def test_negative_sentiment_ranks_below_positive(self):
        good = compute_dish_score([obs(0.9)] * 10, now=NOW)
        bad = compute_dish_score([obs(-0.9)] * 10, now=NOW)
        assert bad.score < good.score

    def test_attributes_are_aggregated_by_frequency(self):
        result = compute_dish_score(
            [
                obs(0.8, attributes=("spicy", "juicy")),
                obs(0.8, attributes=("spicy",)),
                obs(0.8, attributes=("oily",)),
            ],
            now=NOW,
        )
        assert result.top_attributes[0] == "spicy"
