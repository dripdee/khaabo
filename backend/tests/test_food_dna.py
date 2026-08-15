"""Food DNA and evidence-only summary tests.

The rule under test: DNA and summaries are *derived*, never asserted. Thin evidence
must produce an empty DNA rather than plausible-sounding chips, and a summary
sentence claiming an attribute the evidence does not contain must be dropped.
"""

from __future__ import annotations

from app.models.enums import TrendDirection
from app.services.food_dna import DnaInput, aggregate_aspect_sentiment, build_food_dna
from app.services.summaries import verify_model_summary


def make_input(**overrides) -> DnaInput:
    defaults = {
        "dish_labels": [("Chicken Momo", 0.9, 20)],
        "attribute_counts": {},
        "aspect_sentiment": {},
        "price_avg": None,
        "city_median_price": None,
        "consistency": 0.7,
        "trend": None,
        "total_mentions": 20,
        "cuisines": ["Tibetan"],
    }
    defaults.update(overrides)
    return DnaInput(**defaults)  # type: ignore[arg-type]


def codes(chips: list[dict]) -> set[str]:
    return {chip["code"] for chip in chips}


class TestEvidenceGate:
    def test_thin_evidence_produces_no_dna(self):
        """Two mentions cannot support "Affordable · Spicy · Consistent"."""
        assert build_food_dna(make_input(total_mentions=2)) == []

    def test_sufficient_evidence_produces_chips(self):
        chips = build_food_dna(make_input())
        assert len(chips) > 0

    def test_chip_count_is_capped(self):
        chips = build_food_dna(
            make_input(
                attribute_counts={
                    "spicy": 20,
                    "generous_portion": 20,
                    "oily": 20,
                    "authentic": 20,
                    "late_night": 20,
                },
                price_avg=100,
                city_median_price=200,
                aspect_sentiment={"service": 0.8, "ambience": 0.7},
                trend=TrendDirection.RISING,
                consistency=0.9,
            ),
            limit=6,
        )
        assert len(chips) <= 6


class TestSignatureDish:
    def test_dominant_positive_dish_becomes_signature(self):
        chips = build_food_dna(
            make_input(dish_labels=[("Chicken Momo", 0.92, 18)], total_mentions=20)
        )
        assert any(chip["code"].startswith("strong_") for chip in chips)

    def test_minor_dish_is_not_signature(self):
        chips = build_food_dna(
            make_input(dish_labels=[("Chicken Momo", 0.92, 3)], total_mentions=60)
        )
        assert not any(chip["code"].startswith("strong_") for chip in chips)

    def test_well_discussed_but_poor_dish_is_not_signature(self):
        chips = build_food_dna(
            make_input(dish_labels=[("Chicken Momo", 0.35, 30)], total_mentions=40)
        )
        assert not any(chip["code"].startswith("strong_") for chip in chips)

    def test_cuisine_is_the_fallback_when_no_signature_exists(self):
        chips = build_food_dna(
            make_input(dish_labels=[("Chicken Momo", 0.4, 2)], cuisines=["Bengali"])
        )
        assert any(chip["label"] == "Bengali" for chip in chips)


class TestPricePositioning:
    def test_cheap_relative_to_the_city_is_affordable(self):
        chips = build_food_dna(make_input(price_avg=90, city_median_price=200))
        assert "affordable" in codes(chips)

    def test_expensive_relative_to_the_city_is_premium(self):
        chips = build_food_dna(make_input(price_avg=400, city_median_price=200))
        assert "premium" in codes(chips)

    def test_no_price_chip_without_a_city_baseline(self):
        chips = build_food_dna(make_input(price_avg=200, city_median_price=None))
        assert not ({"affordable", "premium", "mid_range"} & codes(chips))

    def test_only_one_price_chip_is_emitted(self):
        chips = build_food_dna(make_input(price_avg=90, city_median_price=200))
        price_chips = [chip for chip in chips if chip["group"] == "price"]
        assert len(price_chips) == 1


class TestAttributeChips:
    def test_frequent_attribute_becomes_a_chip(self):
        chips = build_food_dna(make_input(attribute_counts={"spicy": 10}, total_mentions=20))
        assert "spicy" in codes(chips)

    def test_rare_attribute_is_ignored(self):
        chips = build_food_dna(make_input(attribute_counts={"spicy": 1}, total_mentions=40))
        assert "spicy" not in codes(chips)

    def test_unflattering_attributes_are_surfaced_too(self):
        """A DNA that can only flatter is marketing, not description."""
        chips = build_food_dna(
            make_input(attribute_counts={"oily": 12, "small_portion": 12}, total_mentions=30)
        )
        assert "oily" in codes(chips) or "small_portions" in codes(chips)


class TestConsistencyAndAspects:
    def test_consistent_needs_a_real_sample(self):
        thin = build_food_dna(make_input(consistency=0.9, total_mentions=5))
        thick = build_food_dna(make_input(consistency=0.9, total_mentions=20))
        assert "consistent" not in codes(thin)
        assert "consistent" in codes(thick)

    def test_hit_or_miss_is_reported(self):
        chips = build_food_dna(make_input(consistency=0.2, total_mentions=20))
        assert "hit_or_miss" in codes(chips)

    def test_hygiene_concerns_are_surfaced(self):
        chips = build_food_dna(make_input(aspect_sentiment={"hygiene": -0.7}, total_mentions=20))
        assert "hygiene_concerns" in codes(chips)

    def test_slow_service_is_surfaced(self):
        chips = build_food_dna(make_input(aspect_sentiment={"service": -0.6}, total_mentions=20))
        assert "slow_service" in codes(chips)


class TestTrendChip:
    def test_rising_is_shown(self):
        chips = build_food_dna(make_input(trend=TrendDirection.RISING))
        assert "rising" in codes(chips)

    def test_declining_is_shown(self):
        chips = build_food_dna(make_input(trend=TrendDirection.DECLINING))
        assert "declining" in codes(chips)

    def test_no_trend_means_no_chip(self):
        chips = build_food_dna(make_input(trend=None))
        assert not ({"rising", "declining"} & codes(chips))


class TestAspectAggregation:
    def test_aspects_are_averaged_per_type(self):
        result = aggregate_aspect_sentiment([("taste", 1.0), ("taste", 0.0), ("service", -0.5)])
        assert result["taste"] == 0.5
        assert result["service"] == -0.5

    def test_empty_input_is_empty(self):
        assert aggregate_aspect_sentiment([]) == {}


class TestSummaryGrounding:
    def test_unsupported_sensory_claim_is_dropped(self):
        text = "The momo is juicy. The biryani is spicy."
        result = verify_model_summary(text, allowed_attributes=["juicy"])
        assert "juicy" in result
        assert "spicy" not in result

    def test_supported_claims_survive(self):
        text = "Frequently described as juicy and fresh."
        result = verify_model_summary(text, allowed_attributes=["juicy", "fresh"])
        assert "juicy" in result

    def test_empty_input_returns_empty(self):
        assert verify_model_summary("", allowed_attributes=["juicy"]) == ""

    def test_non_sensory_sentences_are_untouched(self):
        text = "42 mentions across 7 places."
        assert "42 mentions" in verify_model_summary(text, allowed_attributes=[])
