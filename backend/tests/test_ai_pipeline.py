"""Dish extraction and AI pipeline tests.

The canonical case from the brief —
"Chicken momo is amazing but biryani is average" → two dish observations with
different sentiment — is asserted directly, and it must hold with **no model**.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError as PydanticValidationError

from app.ai import HeuristicProvider, ReviewAnalysisRequest, enforce_grounding
from app.ai.factory import build_provider
from app.ai.schemas import DishMentionOut, ReviewAnalysis
from app.services.dish_extraction import (
    detect_attributes,
    detect_language,
    extract_dish_mentions,
    score_clause,
    split_clauses,
)

ALIAS_INDEX = {
    "chicken momo": "chicken-momo",
    "momo": "momo",
    "biryani": "chicken-biryani",
    "chicken biryani": "chicken-biryani",
    "ramen": "ramen",
    "kosha mangsho": "kosha-mangsho",
    "puchka": "puchka",
    "cold coffee": "cold-coffee",
}


class TestClauseSplitting:
    def test_contrast_marker_splits_the_sentence(self):
        clauses = split_clauses("Chicken momo is amazing but biryani is average")
        assert len(clauses) == 2
        assert "momo" in clauses[0].lower()
        assert "biryani" in clauses[1].lower()

    def test_sentence_boundaries_split(self):
        clauses = split_clauses("The momo was great. The service was slow.")
        assert len(clauses) == 2

    def test_commas_do_not_split_noun_lists(self):
        """Splitting on commas would tear "momo, chowmein and rolls" apart."""
        clauses = split_clauses("We ordered momo, chowmein and rolls")
        assert len(clauses) == 1

    def test_bengali_contrast_marker_splits(self):
        clauses = split_clauses("momo bhalo kintu biryani kharap")
        assert len(clauses) == 2

    def test_empty_text_yields_nothing(self):
        assert split_clauses("") == []


class TestClauseSentiment:
    def test_strong_praise_is_positive(self):
        sentiment, confidence = score_clause("the momo is absolutely amazing")
        assert sentiment > 0.7
        assert confidence > 0.4

    def test_strong_criticism_is_negative(self):
        sentiment, _ = score_clause("the biryani was terrible and stale")
        assert sentiment < -0.6

    def test_average_is_near_neutral(self):
        sentiment, _ = score_clause("the biryani is average")
        assert -0.2 < sentiment < 0.2

    def test_negation_flips_sentiment(self):
        positive, _ = score_clause("the momo was great")
        negated, _ = score_clause("the momo was not great")
        assert positive > 0
        assert negated < 0

    def test_negation_is_damped_not_mirrored(self):
        """ "not amazing" is disappointment, not disgust."""
        _, _ = score_clause("amazing")
        negated, _ = score_clause("not amazing")
        assert -0.7 < negated < 0

    def test_intensifier_amplifies(self):
        plain, _ = score_clause("the momo was good")
        intense, _ = score_clause("the momo was extremely good")
        assert intense > plain

    def test_damper_reduces(self):
        plain, _ = score_clause("the momo was good")
        slight, _ = score_clause("the momo was slightly good")
        assert slight < plain

    def test_no_sentiment_words_gives_low_confidence(self):
        sentiment, confidence = score_clause("we went there on Tuesday")
        assert sentiment == 0.0
        assert confidence < 0.3


class TestDishExtraction:
    def test_canonical_multi_dish_case(self):
        """The exact example from the product brief."""
        hits = extract_dish_mentions("Chicken momo is amazing but biryani is average.", ALIAS_INDEX)
        by_dish = {hit.dish_key: hit for hit in hits}

        assert "chicken-momo" in by_dish
        assert "chicken-biryani" in by_dish
        assert by_dish["chicken-momo"].sentiment > 0.6
        assert by_dish["chicken-biryani"].sentiment < 0.3

    def test_longest_alias_wins(self):
        """ "chicken momo" must beat the generic "momo"."""
        hits = extract_dish_mentions("The chicken momo was great", ALIAS_INDEX)
        assert [h.dish_key for h in hits] == ["chicken-momo"]

    def test_no_substring_false_positives(self):
        hits = extract_dish_mentions("The mommy of all restaurants", ALIAS_INDEX)
        assert hits == []

    def test_plural_forms_match(self):
        hits = extract_dish_mentions("The momos were delicious", ALIAS_INDEX)
        assert any(h.dish_key == "momo" for h in hits)

    def test_one_row_per_dish_even_across_clauses(self):
        """The DB enforces UNIQUE(review_id, dish_id), so extraction must agree."""
        hits = extract_dish_mentions(
            "The momo was good. Later the momo was cold. Still the momo is fine.",
            ALIAS_INDEX,
        )
        assert len([h for h in hits if h.dish_key == "momo"]) == 1

    def test_price_is_captured_per_clause(self):
        hits = extract_dish_mentions("Chicken momo at ₹120 is great value", ALIAS_INDEX)
        assert hits[0].price == 120.0

    def test_three_dishes_three_observations(self):
        hits = extract_dish_mentions(
            "Chicken momo was amazing, however the ramen was bland "
            "although the cold coffee was excellent.",
            ALIAS_INDEX,
        )
        assert len(hits) == 3

    def test_empty_alias_index_yields_nothing(self):
        assert extract_dish_mentions("chicken momo is great", {}) == []

    def test_snippets_are_bounded(self):
        hits = extract_dish_mentions("momo " + "x" * 1000, ALIAS_INDEX)
        assert all(len(h.clause) <= 320 for h in hits)


class TestAttributes:
    def test_attributes_are_detected(self):
        assert "spicy" in detect_attributes("the momo was very spicy")
        assert "juicy" in detect_attributes("really juicy filling")
        assert "generous_portion" in detect_attributes("a generous portion for the price")

    def test_no_attributes_when_absent(self):
        assert detect_attributes("we went there yesterday") == []


class TestLanguageDetection:
    def test_english(self):
        assert detect_language("The momo was great") == "en"

    def test_bengali_script(self):
        assert detect_language("মোমো খুব ভালো ছিল") == "bn"

    def test_devanagari_script(self):
        assert detect_language("मोमो बहुत अच्छा था") == "hi"

    def test_romanized_bengali(self):
        assert detect_language("momo khub bhalo chilo darun") == "bn-Latn"

    def test_empty_defaults_to_english(self):
        assert detect_language("") == "en"


class TestHeuristicProvider:
    async def test_works_with_no_model_infrastructure(self):
        provider = HeuristicProvider()
        analysis = await provider.analyze_review(
            ReviewAnalysisRequest(
                text="Chicken momo is amazing but biryani is average.",
                known_dishes=["Chicken Momo", "Chicken Biryani"],
                alias_index=ALIAS_INDEX,
            )
        )
        assert len(analysis.dish_mentions) == 2
        assert analysis.provider == "heuristic"
        assert analysis.overall_sentiment is not None

    async def test_explicit_rating_drives_overall_sentiment(self):
        provider = HeuristicProvider()
        analysis = await provider.analyze_review(
            ReviewAnalysisRequest(
                text="Went here for lunch with family.",
                rating=5.0,
                rating_scale=5.0,
                alias_index=ALIAS_INDEX,
            )
        )
        assert analysis.overall_sentiment == pytest.approx(1.0)

    async def test_spam_is_scored(self):
        provider = HeuristicProvider()
        analysis = await provider.analyze_review(
            ReviewAnalysisRequest(
                text="BUY NOW! WhatsApp +91 9999999999 promo code SAVE50 click here discount",
                alias_index=ALIAS_INDEX,
            )
        )
        assert analysis.spam_score > 0.5
        assert analysis.is_spam

    async def test_value_signal_detected(self):
        provider = HeuristicProvider()
        analysis = await provider.analyze_review(
            ReviewAnalysisRequest(
                text="The momo is great value for money at this price",
                alias_index=ALIAS_INDEX,
            )
        )
        assert analysis.value_signal.value == "cheap"

    def test_default_provider_needs_no_configuration(self):
        assert build_provider("heuristic").name == "heuristic"

    def test_unknown_provider_degrades_to_heuristic(self):
        assert build_provider("does-not-exist").name == "heuristic"


class TestGrounding:
    def _request(self, text: str) -> ReviewAnalysisRequest:
        return ReviewAnalysisRequest(
            text=text,
            known_dishes=["Chicken Momo", "Chicken Biryani"],
            alias_index=ALIAS_INDEX,
        )

    def test_dish_not_in_text_is_dropped(self):
        """The model must not add a dish from the restaurant's reputation."""
        request = self._request("The service was slow and the place was crowded.")
        analysis = ReviewAnalysis(
            dish_mentions=[DishMentionOut(dish_name="chicken momo", sentiment=0.9, confidence=0.9)],
            provider="ollama",
        )
        grounded = enforce_grounding(analysis, request)
        assert grounded.dish_mentions == []

    def test_fabricated_quote_is_removed_but_observation_kept(self):
        request = self._request("The chicken momo was very good today.")
        analysis = ReviewAnalysis(
            dish_mentions=[
                DishMentionOut(
                    dish_name="chicken momo",
                    matched_alias="chicken momo",
                    snippet="the best momo in all of Kolkata, hands down",
                    sentiment=0.9,
                    confidence=0.9,
                )
            ],
            provider="ollama",
        )
        grounded = enforce_grounding(analysis, request)
        assert len(grounded.dish_mentions) == 1
        assert grounded.dish_mentions[0].snippet is None

    def test_real_quote_is_preserved(self):
        request = self._request("The chicken momo was very good today.")
        analysis = ReviewAnalysis(
            dish_mentions=[
                DishMentionOut(
                    dish_name="chicken momo",
                    matched_alias="chicken momo",
                    snippet="The chicken momo was very good",
                    sentiment=0.9,
                    confidence=0.9,
                )
            ],
            provider="ollama",
        )
        grounded = enforce_grounding(analysis, request)
        assert grounded.dish_mentions[0].snippet == "The chicken momo was very good"

    def test_price_without_evidence_is_stripped(self):
        request = self._request("The chicken momo was very good.")
        analysis = ReviewAnalysis(
            dish_mentions=[
                DishMentionOut(
                    dish_name="chicken momo",
                    matched_alias="chicken momo",
                    sentiment=0.9,
                    confidence=0.9,
                    price_mentioned=250.0,
                )
            ],
            provider="ollama",
        )
        grounded = enforce_grounding(analysis, request)
        assert grounded.dish_mentions[0].price_mentioned is None

    def test_price_with_evidence_is_kept(self):
        request = self._request("The chicken momo was ₹150 and very good.")
        analysis = ReviewAnalysis(
            dish_mentions=[
                DishMentionOut(
                    dish_name="chicken momo",
                    matched_alias="chicken momo",
                    sentiment=0.9,
                    confidence=0.9,
                    price_mentioned=150.0,
                )
            ],
            provider="ollama",
        )
        grounded = enforce_grounding(analysis, request)
        assert grounded.dish_mentions[0].price_mentioned == 150.0

    def test_low_confidence_mentions_are_discarded(self):
        request = self._request("Maybe there was chicken momo somewhere.")
        analysis = ReviewAnalysis(
            dish_mentions=[DishMentionOut(dish_name="chicken momo", sentiment=0.5, confidence=0.1)],
            provider="ollama",
        )
        assert enforce_grounding(analysis, request).dish_mentions == []

    def test_unknown_dish_is_dropped_when_creation_is_off(self):
        request = self._request("The tonkotsu tantanmen was incredible.")
        analysis = ReviewAnalysis(
            dish_mentions=[
                DishMentionOut(dish_name="tonkotsu tantanmen", sentiment=0.9, confidence=0.9)
            ],
            provider="ollama",
        )
        assert enforce_grounding(analysis, request).dish_mentions == []

    def test_contradictory_recommendation_is_cleared(self):
        request = self._request("The chicken momo was awful.")
        analysis = ReviewAnalysis(
            dish_mentions=[
                DishMentionOut(
                    dish_name="chicken momo",
                    matched_alias="chicken momo",
                    sentiment=-0.8,
                    confidence=0.9,
                    is_recommended=True,
                )
            ],
            provider="ollama",
        )
        grounded = enforce_grounding(analysis, request)
        assert grounded.dish_mentions[0].is_recommended is None


class TestSchemaValidation:
    def test_sentiment_out_of_range_is_rejected(self):
        with pytest.raises(PydanticValidationError):
            DishMentionOut(dish_name="momo", sentiment=5.0, confidence=0.5)

    def test_confidence_out_of_range_is_rejected(self):
        with pytest.raises(PydanticValidationError):
            DishMentionOut(dish_name="momo", sentiment=0.5, confidence=2.0)

    def test_attributes_are_normalized(self):
        mention = DishMentionOut(
            dish_name="momo",
            sentiment=0.5,
            attributes=["Very Spicy", "very spicy", "  JUICY  "],
        )
        assert "very_spicy" in mention.attributes
        assert mention.attributes.count("very_spicy") == 1
        assert "juicy" in mention.attributes
