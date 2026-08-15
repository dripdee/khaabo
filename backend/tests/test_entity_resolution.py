"""Entity resolution tests.

The behaviour that matters most is the *refusal*: when two candidates are equally
plausible, the resolver must decline and let an admin decide. A wrong merge silently
corrupts every ranking that touches the restaurant.
"""

from __future__ import annotations

from app.services.entity_resolution import (
    AMBIGUITY_MARGIN,
    CandidateRestaurant,
    IncomingPlace,
    MatchMethod,
    haversine_m,
    name_similarity,
    resolve_candidate,
)

# Two branches of a chain, ~4 km apart in Kolkata.
WOW_SALT_LAKE = CandidateRestaurant(
    id="r1",
    name="Wow! Momo",
    normalized_name="wow momo",
    lat=22.5800,
    lng=88.4200,
    source_keys=(("osm", "node/1"),),
)
WOW_PARK_STREET = CandidateRestaurant(
    id="r2",
    name="Wow! Momo",
    normalized_name="wow momo",
    lat=22.5530,
    lng=88.3520,
)
MOMO_PLAZA = CandidateRestaurant(
    id="r3",
    name="Momo Plaza",
    normalized_name="momo plaza",
    lat=22.5800,
    lng=88.4201,
    aliases=("momo plaza salt lake",),
)


class TestNameSimilarity:
    def test_identical_names_are_one(self):
        assert name_similarity("Wow! Momo", "wow momo") == 1.0

    def test_containment_is_a_strong_signal(self):
        assert name_similarity("Wow Momo", "Wow Momo Salt Lake") > 0.8

    def test_unrelated_names_are_low(self):
        assert name_similarity("Momo Plaza", "Arsalan Biryani") < 0.4

    def test_shared_prefix_alone_is_not_enough(self):
        """ "Momo Mia" vs "Momo Mahal" must not read as the same restaurant."""
        assert name_similarity("Momo Mia", "Momo Mahal") < 0.82

    def test_ampersand_and_punctuation_are_normalized(self):
        assert name_similarity("Peter Cat & Co.", "peter cat and co") == 1.0

    def test_empty_input_is_zero(self):
        assert name_similarity("", "anything") == 0.0


class TestDistance:
    def test_haversine_matches_known_distance(self):
        # Park Street to Salt Lake Sector V, roughly 7-8 km.
        metres = haversine_m(22.5530, 88.3520, 22.5800, 88.4200)
        assert 6000 < metres < 9000

    def test_same_point_is_zero(self):
        assert haversine_m(22.5, 88.3, 22.5, 88.3) == 0.0


class TestSourceKeyMatch:
    def test_provenance_key_wins_immediately(self):
        result = resolve_candidate(
            IncomingPlace(
                name="Completely Different Name",
                lat=22.9,
                lng=88.9,
                source="osm",
                external_id="node/1",
            ),
            [WOW_SALT_LAKE, WOW_PARK_STREET],
        )
        assert result.method is MatchMethod.SOURCE_KEY
        assert result.matched_id == "r1"
        assert result.confidence == 1.0


class TestExactAndAlias:
    def test_exact_name_within_range_matches(self):
        result = resolve_candidate(
            IncomingPlace(name="Momo Plaza", lat=22.5800, lng=88.4201),
            [MOMO_PLAZA],
        )
        assert result.method is MatchMethod.EXACT_NAME
        assert result.matched_id == "r3"
        assert result.confidence >= 0.95

    def test_alias_hit_matches(self):
        result = resolve_candidate(
            IncomingPlace(name="Momo Plaza Salt Lake", lat=22.5801, lng=88.4202),
            [MOMO_PLAZA],
        )
        assert result.matched_id == "r3"
        assert result.confidence >= 0.9

    def test_exact_name_far_away_is_not_matched(self):
        """Same name 40 km away is a different establishment, not the same one."""
        result = resolve_candidate(
            IncomingPlace(name="Momo Plaza", lat=23.0, lng=89.0),
            [MOMO_PLAZA],
        )
        assert result.matched_id is None
        assert result.method is MatchMethod.NEW


class TestChainHandling:
    def test_two_branches_resolve_separately(self):
        """One branch can be much better than another, so they must stay distinct."""
        result = resolve_candidate(
            IncomingPlace(name="Wow! Momo", lat=22.5801, lng=88.4201),
            [WOW_SALT_LAKE, WOW_PARK_STREET],
        )
        assert result.matched_id == "r1"

        other = resolve_candidate(
            IncomingPlace(name="Wow! Momo", lat=22.5531, lng=88.3521),
            [WOW_SALT_LAKE, WOW_PARK_STREET],
        )
        assert other.matched_id == "r2"


class TestAmbiguityRefusal:
    def test_two_identical_candidates_at_the_same_spot_are_refused(self):
        twin_a = CandidateRestaurant("a", "Cafe Kolkata", "cafe kolkata", 22.5726, 88.3639)
        twin_b = CandidateRestaurant("b", "Cafe Kolkata", "cafe kolkata", 22.5727, 88.3640)

        result = resolve_candidate(
            IncomingPlace(name="Cafe Kolkata", lat=22.5726, lng=88.3639),
            [twin_a, twin_b],
        )
        assert result.matched_id is None
        assert result.method is MatchMethod.AMBIGUOUS
        assert result.needs_review
        assert result.runner_up_id is not None

    def test_a_clear_winner_is_not_refused(self):
        strong = CandidateRestaurant("a", "Arsalan", "arsalan", 22.5726, 88.3639)
        weak = CandidateRestaurant(
            "b", "Arsalan Hotel Zeeshan", "arsalan hotel zeeshan", 22.5726, 88.3639
        )

        result = resolve_candidate(
            IncomingPlace(name="Arsalan", lat=22.5726, lng=88.3639), [strong, weak]
        )
        assert result.matched_id == "a"
        assert result.method is MatchMethod.EXACT_NAME

    def test_ambiguity_margin_is_documented_and_small(self):
        assert 0 < AMBIGUITY_MARGIN <= 0.1


class TestNoCoordinates:
    def test_text_mention_requires_a_near_identical_name(self):
        """Reddit mentions have no coordinates, so the name bar is higher."""
        result = resolve_candidate(
            IncomingPlace(name="Wow Momo", lat=None, lng=None), [WOW_SALT_LAKE]
        )
        assert result.matched_id == "r1"
        assert result.method is MatchMethod.FUZZY_STRICT

    def test_loose_text_mention_is_not_matched(self):
        result = resolve_candidate(
            IncomingPlace(name="that momo place near the mall", lat=None, lng=None),
            [WOW_SALT_LAKE, MOMO_PLAZA],
        )
        assert result.matched_id is None

    def test_missing_coordinates_fail_the_distance_gate_closed(self):
        """Absent proximity evidence must not be treated as proximity."""
        result = resolve_candidate(
            IncomingPlace(name="Momo Plaza", lat=None, lng=None), [MOMO_PLAZA]
        )
        assert result.method is not MatchMethod.EXACT_NAME


class TestNewEntities:
    def test_unknown_place_is_created(self):
        result = resolve_candidate(
            IncomingPlace(name="Brand New Momo Corner", lat=22.60, lng=88.40),
            [WOW_SALT_LAKE, MOMO_PLAZA],
        )
        assert result.should_create
        assert result.matched_id is None
        assert result.confidence <= 0.4

    def test_empty_candidate_list_creates(self):
        result = resolve_candidate(IncomingPlace(name="Anything", lat=22.5, lng=88.3), [])
        assert result.should_create

    def test_blank_name_is_not_matched(self):
        result = resolve_candidate(IncomingPlace(name="   ", lat=22.5, lng=88.3), [MOMO_PLAZA])
        assert result.method is MatchMethod.NEW


class TestDeterminism:
    def test_resolution_is_stable_across_candidate_ordering(self):
        forward = resolve_candidate(
            IncomingPlace(name="Momo Plaza", lat=22.5800, lng=88.4201),
            [WOW_SALT_LAKE, MOMO_PLAZA, WOW_PARK_STREET],
        )
        reverse = resolve_candidate(
            IncomingPlace(name="Momo Plaza", lat=22.5800, lng=88.4201),
            [WOW_PARK_STREET, MOMO_PLAZA, WOW_SALT_LAKE],
        )
        assert forward.matched_id == reverse.matched_id
        assert forward.method is reverse.method
