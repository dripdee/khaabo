"""Duplicate detection and spam heuristics.

Covers the brief's edge cases: identical reviews, whitespace/case variants,
near-identical text with one word changed, and genuinely different reviews that
must NOT collapse (the false-positive direction matters more here).
"""

from __future__ import annotations

from app.services.dedup import (
    hamming_distance,
    is_near_duplicate,
    is_spam,
    jaccard,
    review_fingerprint,
    simhash,
    spam_score,
    to_signed_64,
)

REVIEW_A = "The chicken momo here is absolutely amazing, juicy and perfectly steamed every time."
REVIEW_B = "The chicken momo here is absolutely amazing, juicy and perfectly steamed every time."
REVIEW_WHITESPACE = (
    "  The   CHICKEN momo here is absolutely amazing,  juicy and perfectly steamed every time. "
)
REVIEW_NEAR = "The chicken momo here is absolutely amazing, juicy and perfectly cooked every time."
REVIEW_DIFFERENT = (
    "The biryani was dry and the service was slow, though the kebabs were decent enough."
)


class TestExactDuplicates:
    def test_identical_text_has_the_same_fingerprint(self):
        assert review_fingerprint(REVIEW_A, "u1", None) == review_fingerprint(REVIEW_B, "u1", None)

    def test_whitespace_and_case_variants_collapse(self):
        """Otherwise the same review reposted with different spacing slips past UNIQUE."""
        assert review_fingerprint(REVIEW_A, "u1", None) == review_fingerprint(
            REVIEW_WHITESPACE, "u1", None
        )

    def test_different_text_has_a_different_fingerprint(self):
        assert review_fingerprint(REVIEW_A, "u1", None) != review_fingerprint(
            REVIEW_DIFFERENT, "u1", None
        )

    def test_different_authors_are_not_the_same_evidence(self):
        assert review_fingerprint(REVIEW_A, "u1", None) != review_fingerprint(REVIEW_A, "u2", None)


class TestSimhash:
    def test_identical_text_has_identical_hash(self):
        assert simhash(REVIEW_A) == simhash(REVIEW_B)

    def test_near_identical_text_is_within_the_candidate_threshold(self):
        from app.services.dedup import HAMMING_THRESHOLD

        assert hamming_distance(simhash(REVIEW_A), simhash(REVIEW_NEAR)) <= HAMMING_THRESHOLD

    def test_unrelated_text_is_beyond_the_candidate_threshold(self):
        from app.services.dedup import HAMMING_THRESHOLD

        assert hamming_distance(simhash(REVIEW_A), simhash(REVIEW_DIFFERENT)) > HAMMING_THRESHOLD

    def test_punctuation_only_differences_hash_identically(self):
        assert simhash("Great momo, hot and fresh!") == simhash("Great momo hot and fresh")

    def test_empty_text_hashes_to_zero(self):
        assert simhash("") == 0

    def test_signed_conversion_fits_bigint(self):
        value = to_signed_64(simhash(REVIEW_A))
        assert -(2**63) <= value < 2**63


class TestJaccard:
    def test_identical_is_one(self):
        assert jaccard(REVIEW_A, REVIEW_B) == 1.0

    def test_unrelated_is_low(self):
        assert jaccard(REVIEW_A, REVIEW_DIFFERENT) < 0.3

    def test_empty_is_zero(self):
        assert jaccard("", REVIEW_A) == 0.0


class TestNearDuplicates:
    def test_near_identical_is_flagged(self):
        existing = [("r1", simhash(REVIEW_A), REVIEW_A)]
        verdict = is_near_duplicate(REVIEW_NEAR, existing)
        assert verdict.is_duplicate
        assert verdict.matched_id == "r1"
        assert verdict.similarity >= 0.82

    def test_genuinely_different_reviews_do_not_collapse(self):
        """The false-positive direction: silencing a real reviewer is the worse error."""
        existing = [("r1", simhash(REVIEW_A), REVIEW_A)]
        verdict = is_near_duplicate(REVIEW_DIFFERENT, existing)
        assert not verdict.is_duplicate

    def test_no_candidates_means_no_duplicate(self):
        assert not is_near_duplicate(REVIEW_A, []).is_duplicate

    def test_similar_vocabulary_but_different_verdict_is_kept(self):
        """Same words, opposite opinion — must remain two distinct observations."""
        positive = "The momo was fresh and the chutney was spicy and delicious"
        negative = "The momo was stale and the chutney was watery and disappointing"
        existing = [("r1", simhash(positive), positive)]
        assert not is_near_duplicate(negative, existing).is_duplicate

    def test_mostly_shared_wording_with_an_opposite_verdict_is_kept(self):
        """Close on hash distance but below the Jaccard bar, so it must survive."""
        positive = "Best cold coffee in Sector V, thick and not too sweet."
        negative = "Best cold coffee in Sector V, watery and far too sweet."
        existing = [("r1", simhash(positive), positive)]
        assert not is_near_duplicate(negative, existing).is_duplicate

    def test_jaccard_confirmation_overrides_a_hash_collision(self):
        """A close hash alone is only a candidate; token overlap must confirm it."""
        existing = [("r1", simhash(REVIEW_A), REVIEW_DIFFERENT)]
        verdict = is_near_duplicate(REVIEW_A, existing, hamming_threshold=64)
        assert not verdict.is_duplicate

    def test_best_match_is_returned_when_several_are_close(self):
        existing = [
            ("r1", simhash(REVIEW_NEAR), REVIEW_NEAR),
            ("r2", simhash(REVIEW_A), REVIEW_A),
        ]
        verdict = is_near_duplicate(REVIEW_A, existing)
        assert verdict.matched_id == "r2"


class TestSpamScoring:
    def test_genuine_review_scores_low(self):
        assert spam_score(REVIEW_A) < 0.4

    def test_empty_text_is_maximally_spammy(self):
        assert spam_score("") == 1.0

    def test_contact_details_raise_the_score(self):
        text = "Best deals! WhatsApp +91 9999999999 for free delivery on your first order now"
        assert spam_score(text) > spam_score(REVIEW_A)

    def test_promotional_text_is_flagged(self):
        text = "Use promo code SAVE50 for discount, offer valid today, subscribe and click here"
        assert is_spam(text)

    def test_repetition_raises_the_score(self):
        assert spam_score("good good good good good good good good") > 0.3

    def test_shouting_raises_the_score(self):
        loud = spam_score("THIS PLACE IS THE ABSOLUTE BEST EVER GO THERE NOW")
        calm = spam_score("This place is the absolute best ever go there now")
        assert loud > calm

    def test_very_short_text_is_suspicious(self):
        assert spam_score("ok") > spam_score(REVIEW_A)

    def test_score_is_bounded(self):
        text = "BUY NOW!!! WhatsApp +91 999 click here www.spam.com promo code discount " * 5
        assert 0.0 <= spam_score(text) <= 1.0

    def test_links_increase_the_score(self):
        assert spam_score(REVIEW_A, link_count=4) > spam_score(REVIEW_A, link_count=0)

    def test_a_critical_review_is_not_spam(self):
        """Negative opinion is evidence, not abuse."""
        text = (
            "Honestly disappointed. The biryani was dry, the raita was warm, "
            "and for the price I expected much better."
        )
        assert not is_spam(text)
