"""Trend detection tests.

The critical property: no trend unless *both* windows carry enough observations.
A directional arrow from two data points is worse than no arrow at all.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.models.enums import TrendDirection
from app.services.ranking import Observation
from app.services.trends import TrendConfig, detect_trend, trend_label

NOW = datetime(2026, 8, 15, tzinfo=UTC)
CONFIG = TrendConfig(recent_days=60, historical_days=240, delta_threshold=0.08, min_observations=3)


def obs(sentiment: float, days_ago: int) -> Observation:
    return Observation(
        sentiment=sentiment, confidence=0.8, observed_at=NOW - timedelta(days=days_ago)
    )


class TestGating:
    def test_no_trend_without_recent_observations(self):
        result = detect_trend([obs(0.8, 100) for _ in range(10)], config=CONFIG, now=NOW)
        assert result.direction is None
        assert result.reason == "insufficient_data"

    def test_no_trend_without_historical_baseline(self):
        result = detect_trend([obs(0.8, 5) for _ in range(10)], config=CONFIG, now=NOW)
        assert result.direction is None
        assert result.has_trend is False

    def test_no_trend_with_two_and_two(self):
        observations = [obs(0.9, 10), obs(0.9, 20), obs(0.2, 100), obs(0.2, 120)]
        result = detect_trend(observations, config=CONFIG, now=NOW)
        assert result.direction is None

    def test_trend_appears_at_the_threshold(self):
        observations = [
            obs(0.9, 10),
            obs(0.9, 20),
            obs(0.9, 30),
            obs(0.1, 100),
            obs(0.1, 120),
            obs(0.1, 150),
        ]
        result = detect_trend(observations, config=CONFIG, now=NOW)
        assert result.direction is TrendDirection.RISING

    def test_empty_input_has_no_trend(self):
        result = detect_trend([], config=CONFIG, now=NOW)
        assert result.direction is None
        assert result.recent_count == 0


class TestDirection:
    def test_sharp_improvement_is_rising(self):
        recent = [obs(0.9, d) for d in (5, 15, 25, 35)]
        historical = [obs(0.1, d) for d in (90, 120, 150, 180)]
        result = detect_trend(recent + historical, config=CONFIG, now=NOW)
        assert result.direction is TrendDirection.RISING
        assert result.significant
        assert result.delta > 0.08

    def test_sharp_decline_is_declining(self):
        recent = [obs(-0.6, d) for d in (5, 15, 25, 35)]
        historical = [obs(0.9, d) for d in (90, 120, 150, 180)]
        result = detect_trend(recent + historical, config=CONFIG, now=NOW)
        assert result.direction is TrendDirection.DECLINING
        assert result.significant
        assert result.delta < -0.08

    def test_flat_sentiment_is_stable(self):
        recent = [obs(0.7, d) for d in (5, 20, 40)]
        historical = [obs(0.7, d) for d in (90, 130, 170)]
        result = detect_trend(recent + historical, config=CONFIG, now=NOW)
        assert result.direction is TrendDirection.STABLE
        assert not result.significant

    def test_small_change_stays_stable(self):
        recent = [obs(0.72, d) for d in (5, 20, 40)]
        historical = [obs(0.70, d) for d in (90, 130, 170)]
        result = detect_trend(recent + historical, config=CONFIG, now=NOW)
        assert result.direction is TrendDirection.STABLE


class TestVolumeSurge:
    def test_attention_surge_at_flat_sentiment_is_rising_but_not_significant(self):
        """Windows have different lengths, so the comparison is per-day, not raw counts."""
        recent = [obs(0.7, d) for d in range(1, 40)]
        historical = [obs(0.7, d) for d in (90, 140, 190)]
        result = detect_trend(recent + historical, config=CONFIG, now=NOW)
        assert result.direction is TrendDirection.RISING
        assert result.significant is False
        assert result.reason == "volume_surge"

    def test_equal_rates_do_not_trigger_a_surge(self):
        recent = [obs(0.7, d) for d in (10, 30, 50)]
        historical = [obs(0.7, d) for d in (70, 100, 130, 160, 190, 220, 235, 238, 239)]
        result = detect_trend(recent + historical, config=CONFIG, now=NOW)
        assert result.reason != "volume_surge"


class TestWindowing:
    def test_observations_beyond_the_historical_window_are_ignored(self):
        recent = [obs(0.9, d) for d in (5, 15, 25)]
        historical = [obs(0.1, d) for d in (90, 120, 150)]
        ancient = [obs(-1.0, d) for d in (400, 500, 900)]
        with_ancient = detect_trend(recent + historical + ancient, config=CONFIG, now=NOW)
        without = detect_trend(recent + historical, config=CONFIG, now=NOW)
        assert with_ancient.delta == without.delta
        assert with_ancient.historical_count == 3

    def test_undated_observations_are_excluded(self):
        undated = [Observation(sentiment=0.9, confidence=0.8) for _ in range(20)]
        recent = [obs(0.9, d) for d in (5, 15, 25)]
        historical = [obs(0.5, d) for d in (90, 120, 150)]
        result = detect_trend(undated + recent + historical, config=CONFIG, now=NOW)
        assert result.recent_count == 3
        assert result.historical_count == 3

    def test_future_dated_observations_are_ignored(self):
        future = [Observation(sentiment=1.0, confidence=1.0, observed_at=NOW + timedelta(days=5))]
        recent = [obs(0.5, d) for d in (5, 15, 25)]
        historical = [obs(0.5, d) for d in (90, 120, 150)]
        result = detect_trend(future + recent + historical, config=CONFIG, now=NOW)
        assert result.recent_count == 3


class TestReporting:
    def test_counts_and_means_are_reported(self):
        recent = [obs(1.0, d) for d in (5, 15, 25, 35)]
        historical = [obs(0.0, d) for d in (90, 120, 150)]
        result = detect_trend(recent + historical, config=CONFIG, now=NOW)
        assert result.recent_count == 4
        assert result.historical_count == 3
        assert result.recent_sentiment == 1.0
        assert result.historical_sentiment == 0.5

    def test_labels_are_human_readable(self):
        assert trend_label(TrendDirection.RISING) == "Rising"
        assert trend_label(TrendDirection.DECLINING) == "Declining"
        assert trend_label(None) == ""

    def test_partial_data_still_reports_available_means(self):
        result = detect_trend([obs(0.9, 5), obs(0.9, 20)], config=CONFIG, now=NOW)
        assert result.direction is None
        assert result.recent_sentiment is not None
        assert result.historical_sentiment is None


class TestConfigurability:
    def test_threshold_is_configurable(self):
        recent = [obs(0.72, d) for d in (5, 20, 40)]
        historical = [obs(0.70, d) for d in (90, 130, 170)]
        strict = detect_trend(recent + historical, config=CONFIG, now=NOW)
        loose = detect_trend(
            recent + historical,
            config=TrendConfig(60, 240, delta_threshold=0.005, min_observations=3),
            now=NOW,
        )
        assert strict.direction is TrendDirection.STABLE
        assert loose.direction is TrendDirection.RISING

    def test_min_observations_is_configurable(self):
        observations = [obs(0.9, 10), obs(0.9, 20), obs(0.1, 100), obs(0.1, 130)]
        default = detect_trend(observations, config=CONFIG, now=NOW)
        relaxed = detect_trend(
            observations,
            config=TrendConfig(60, 240, delta_threshold=0.08, min_observations=2),
            now=NOW,
        )
        assert default.direction is None
        assert relaxed.direction is TrendDirection.RISING
