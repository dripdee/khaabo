"""Query parser tests.

These cover the exact example queries in the brief, plus the ordering rule that
multi-word signals (areas, moods, dietary phrases) are removed before tokenizing —
otherwise "salt lake" leaks "salt" into the dish terms.
"""

from __future__ import annotations

from app.services.query_parser import parse_query, price_band_bounds


class TestBriefExamples:
    def test_best_chicken_momo_near_me(self):
        parsed = parse_query("best chicken momo near me")
        assert parsed.dish_terms == ["chicken momo"]
        assert parsed.near_me
        assert parsed.superlative
        assert parsed.intent == "dish"

    def test_cheap_biryani_in_kolkata(self):
        parsed = parse_query("cheap biryani in Kolkata")
        assert parsed.dish_terms == ["biryani"]
        assert parsed.price_band == "cheap"
        assert parsed.max_price == 300.0

    def test_best_cafe_for_studying(self):
        parsed = parse_query("best cafe for studying")
        assert parsed.mood == "study"
        assert parsed.superlative

    def test_best_biryani_under_300(self):
        parsed = parse_query("best biryani under ₹300")
        assert parsed.dish_terms == ["biryani"]
        assert parsed.max_price == 300.0

    def test_best_ramen_near_salt_lake(self):
        parsed = parse_query("best ramen near Salt Lake")
        assert parsed.dish_terms == ["ramen"]
        assert parsed.area == "Salt Lake"

    def test_best_cafes_for_working(self):
        parsed = parse_query("best cafes for working")
        assert parsed.mood == "work"


class TestPriceParsing:
    def test_under_with_rupee_symbol(self):
        assert parse_query("momo under ₹200").max_price == 200.0

    def test_under_with_rs(self):
        assert parse_query("momo under Rs. 200").max_price == 200.0

    def test_below_keyword(self):
        assert parse_query("biryani below 250").max_price == 250.0

    def test_over_keyword(self):
        assert parse_query("steak above 800").min_price == 800.0

    def test_between_range(self):
        parsed = parse_query("thali between 200 and 400")
        assert parsed.min_price == 200.0
        assert parsed.max_price == 400.0

    def test_budget_synonyms_map_to_cheap(self):
        for term in ("budget", "affordable", "pocket friendly"):
            assert parse_query(f"{term} momo").price_band == "cheap"

    def test_premium_synonyms(self):
        assert parse_query("fine dining sushi").price_band == "premium"

    def test_price_band_bounds(self):
        assert price_band_bounds("cheap") == (None, 300.0)
        assert price_band_bounds("premium") == (700.0, None)
        assert price_band_bounds(None) == (None, None)


class TestAreaExtraction:
    def test_known_area_is_recognized(self):
        assert parse_query("momo in Park Street").area == "Park Street"

    def test_multiword_area_does_not_leak_into_dish_terms(self):
        """ "salt lake" must not leave "salt" behind as a dish term."""
        parsed = parse_query("best momo in salt lake")
        assert parsed.area == "Salt Lake"
        assert "salt" not in " ".join(parsed.dish_terms)

    def test_longest_area_match_wins(self):
        assert parse_query("cafe in Sector V").area == "Sector V"

    def test_unknown_area_after_near_is_captured(self):
        parsed = parse_query("biryani near Chandni Chowk")
        assert parsed.area is not None
        assert "chandni" in parsed.area.lower()


class TestDietaryAndMood:
    def test_veg_variants(self):
        assert parse_query("veg momo").dietary == "veg"
        assert parse_query("vegetarian thali").dietary == "veg"

    def test_non_veg_is_distinct_from_veg(self):
        assert parse_query("non veg biryani").dietary == "non_veg"

    def test_halal_and_jain(self):
        assert parse_query("halal biryani").dietary == "halal"
        assert parse_query("jain food").dietary == "jain"

    def test_mood_variants(self):
        assert parse_query("cafe for a date").mood == "date"
        assert parse_query("late night food").mood == "late_night"
        assert parse_query("quick bite near me").mood == "quick"


class TestIntentClassification:
    def test_dish_intent(self):
        assert parse_query("chicken momo").intent == "dish"

    def test_cuisine_intent_without_a_dish(self):
        assert parse_query("bengali").intent == "cuisine"

    def test_mood_intent_without_a_dish(self):
        assert parse_query("for studying").intent == "mood"

    def test_area_intent_without_a_dish(self):
        assert parse_query("in gariahat").intent == "area"

    def test_empty_query(self):
        assert parse_query("").intent == "empty"

    def test_noise_only_query_is_browse(self):
        assert parse_query("best food places").intent == "browse"


class TestCuisine:
    def test_cuisine_is_extracted_alongside_a_dish(self):
        parsed = parse_query("bengali kosha mangsho")
        assert parsed.cuisine == "Bengali"
        assert "kosha mangsho" in " ".join(parsed.dish_terms)

    def test_multiword_cuisine(self):
        assert parse_query("north indian thali").cuisine == "North Indian"


class TestNoiseRemoval:
    def test_filler_words_are_stripped(self):
        parsed = parse_query("where should i eat the best chicken momo in kolkata")
        assert parsed.dish_terms == ["chicken momo"]

    def test_city_name_is_not_a_dish(self):
        parsed = parse_query("kolkata food")
        assert parsed.dish_terms == []

    def test_serializable_output(self):
        payload = parse_query("best momo under 200 near salt lake").to_dict()
        assert payload["max_price"] == 200.0
        assert payload["area"] == "Salt Lake"
        assert payload["intent"] == "dish"
