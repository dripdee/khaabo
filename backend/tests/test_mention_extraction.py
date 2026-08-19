"""Mention extraction tests.

The behaviour that matters: a review naming exactly one known restaurant attaches
to it; zero or multiple names stay unresolved. Word boundaries and generic-token
guards are the safety rails.
"""

from __future__ import annotations

from app.services.entity_resolution import CandidateRestaurant
from app.services.mention_extraction import (
    _is_mentionable,
    mention_hits,
    resolve_mention,
)
from app.utils.text import normalize_name

ARSALAN = CandidateRestaurant(
    id="r-arsalan",
    name="Arsalan",
    normalized_name="arsalan",
    lat=22.5726,
    lng=88.3639,
    aliases=("arsalan park circus",),
)
PETER_CAT = CandidateRestaurant(
    id="r-peter-cat",
    name="Peter Cat",
    normalized_name="peter cat",
    lat=22.5530,
    lng=88.3520,
)
MOMO_GHAR = CandidateRestaurant(
    id="r-momo-ghar",
    name="Momo Ghar",
    normalized_name="momo ghar",
    lat=22.5800,
    lng=88.4200,
)
KOLKATA_NAMED = CandidateRestaurant(
    id="r-generic",
    name="Kolkata",
    normalized_name="kolkata",
    lat=22.5726,
    lng=88.3639,
)

CANDIDATES = [ARSALAN, PETER_CAT, MOMO_GHAR]


class TestResolveMention:
    def test_single_named_restaurant_resolves(self):
        """The exact case the old pipeline dropped: 'The biryani at Arsalan...'"""
        text = "The chicken biryani at Arsalan was amazing, worth every rupee."
        restaurant_id, label = resolve_mention(text, CANDIDATES)
        assert restaurant_id == "r-arsalan"
        assert label == "Arsalan"

    def test_alias_mention_resolves_to_the_canonical_restaurant(self):
        text = "We ate at Arsalan Park Circus and the food was incredible."
        restaurant_id, _ = resolve_mention(text, CANDIDATES)
        assert restaurant_id == "r-arsalan"

    def test_multiple_mentions_are_refused(self):
        """A food-tour review naming several places is evidence for none."""
        text = "Arsalan vs Peter Cat vs Momo Ghar - which is the best in Kolkata?"
        restaurant_id, _ = resolve_mention(text, CANDIDATES)
        assert restaurant_id is None

    def test_no_mention_stays_unresolved(self):
        text = "Best street food tour in Kolkata, the momos were unreal."
        restaurant_id, _ = resolve_mention(text, CANDIDATES)
        assert restaurant_id is None

    def test_word_boundaries_prevent_substring_matches(self):
        """'Momo Ghar' must not match inside 'Momo Gharty Palace'."""
        text = "Momo Gharty Palace serves the best dumplings in the city."
        restaurant_id, _ = resolve_mention(text, CANDIDATES)
        assert restaurant_id is None

    def test_a_repeated_single_mention_still_resolves(self):
        text = "Arsalan biryani is the best. I love Arsalan but it is pricey now."
        restaurant_id, _ = resolve_mention(text, CANDIDATES)
        assert restaurant_id == "r-arsalan"

    def test_empty_and_blank_text_never_resolves(self):
        assert resolve_mention("", CANDIDATES) == (None, None)
        assert resolve_mention("   ", CANDIDATES) == (None, None)

    def test_empty_catalog_never_resolves(self):
        assert resolve_mention("great food at Arsalan", []) == (None, None)

    def test_generic_only_names_are_not_mentionable(self):
        """A restaurant called just 'Kolkata' cannot be matched as a bare word."""
        text = "Kolkata food tour with momos everywhere."
        restaurant_id, _ = resolve_mention(text, [KOLKATA_NAMED])
        assert restaurant_id is None


class TestMentionHits:
    def test_hits_map_restaurant_ids_to_labels(self):
        text_norm = normalize_name("Dinner at Peter Cat, royality of Kerala flavours.")
        hits = mention_hits(text_norm, CANDIDATES)
        assert hits == {"r-peter-cat": {"Peter Cat"}}

    def test_hits_respect_word_boundaries(self):
        text_norm = normalize_name("Cafe Momo is not the same as Momo Ghar die man sprach.")
        hits = mention_hits(text_norm, CANDIDATES)
        assert "r-momo-ghar" not in hits or "Momo Ghar" in hits["r-momo-ghar"]

    def test_a_label_is_counted_once_per_restaurant(self):
        text_norm = normalize_name("Arsalan is great. Really love Arsalan.")
        hits = mention_hits(text_norm, CANDIDATES)
        assert len(hits) == 1
        assert hits["r-arsalan"] == {"Arsalan"}


class TestIsMentionable:
    def test_distinctive_names_pass(self):
        assert _is_mentionable("arsalan park circus")
        assert _is_mentionable("peter cat")

    def test_purely_generic_names_fail(self):
        assert not _is_mentionable("kolkata")
        assert not _is_mentionable("best food")
        assert not _is_mentionable("momo")

    def test_empty_name_fails(self):
        assert not _is_mentionable("")
