"""Mention extraction: attach a text-only review to the restaurant it names.

Text-only sources (YouTube videos, Reddit comments) carry no coordinates and no
structured entity reference, so `resolve_candidate` can never reach the confidence
the review-resolution gate requires. But reviews name places in prose, and that
evidence is usable when a review names exactly one known restaurant.

Policy, mirroring the resolver's refusal principle:
* exactly one catalog restaurant named (name or alias, word-bounded) → attach
* zero or many → skip. A food-tour review that names five places is not evidence
  for any single one, and guessing would distort rankings.
"""

from __future__ import annotations

import re

from app.services.entity_resolution import CandidateRestaurant
from app.utils.text import normalize_name, token_set

# Tokens that can make a whole normalized name word-boundable as a *thing* without
# naming a specific place. A name made only of these (e.g. "kolkata", "momo",
# "best food") is never a plausible bare mention in prose, so it is excluded.
_GENERIC_TOKENS = frozenset(
    {
        "kolkata",
        "calcutta",
        "india",
        "city",
        "town",
        "best",
        "top",
        "place",
        "places",
        "restaurant",
        "restaurants",
        "cafe",
        "cafes",
        "food",
        "foods",
        "street",
        "streetfood",
        "tour",
        "tours",
        "review",
        "reviews",
        "vlog",
        "vlogs",
        "experience",
        "market",
        "bazaar",
        "dhaba",
        "hotel",
        "kitchen",
        "biryani",
        "momo",
        "momos",
        "kolpara",
        "rolls",
        "phuchka",
        "dish",
        "dishes",
        "menu",
        "lunch",
        "dinner",
        "breakfast",
    }
)


def _is_mentionable(normalized_label: str) -> bool:
    """A name usable as a mention needs at least one distinctive token."""
    tokens = token_set(normalized_label)
    return bool(tokens and (tokens - _GENERIC_TOKENS))


def mention_hits(text_norm: str, candidates: list[CandidateRestaurant]) -> dict[str, set[str]]:
    """restaurant_id -> set of matched display labels, word-bounded in `text_norm`."""
    hits: dict[str, set[str]] = {}
    for cand in candidates:
        labels = {cand.name, *cand.aliases}
        matched: set[str] = set()
        for label in labels:
            key = normalize_name(label)
            if not key or not _is_mentionable(key):
                continue
            pattern = r"(?<!\w)" + re.escape(key) + r"(?!\w)"
            if re.search(pattern, text_norm):
                matched.add(label)
        if matched:
            hits[cand.id] = matched
    return hits


def resolve_mention(
    text: str, candidates: list[CandidateRestaurant]
) -> tuple[str | None, str | None]:
    """Return (restaurant_id, matched_label) when exactly one restaurant is named.

    Returns (None, None) for zero or multiple mentions. Word boundaries are
    respected so "da boudi" cannot match inside "dada boudi" and vice versa.
    """
    if not text or not candidates:
        return None, None

    text_norm = normalize_name(text)
    if not text_norm:
        return None, None

    hits = mention_hits(text_norm, candidates)
    if len(hits) != 1:
        return None, None

    restaurant_id = next(iter(hits))
    label = sorted(next(iter(hits.values())))[0]
    return restaurant_id, label
