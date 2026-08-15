"""Deterministic dish extraction and clause-level sentiment.

This module is the reason the product works with **no model at all**. It:

* splits text into clauses on contrast markers, so
  "chicken momo is amazing but biryani is average" yields two opposing observations
* matches dish aliases on word boundaries (never substrings — "momo" must not fire
  inside "momos are" incorrectly, and must not fire inside unrelated words)
* scores each clause with a small lexicon plus negation and intensifier handling

The AI provider refines these results; it does not replace them.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.utils.text import clean_text, extract_prices, normalize_name

# Contrast markers split a sentence into independently-sentimented clauses.
_CONTRAST = (
    "but",
    "however",
    "though",
    "although",
    "whereas",
    "while",
    "on the other hand",
    "that said",
    "lekin",
    "kintu",
    "যদিও",
    "কিন্তু",
)
_CLAUSE_SPLIT = re.compile(
    r"(?:(?<=[.!?;])\s+)|(?:\s*\b(?:" + "|".join(re.escape(c) for c in _CONTRAST) + r")\b\s*)",
    re.IGNORECASE,
)

_POSITIVE = {
    "amazing": 0.9,
    "excellent": 0.9,
    "outstanding": 0.95,
    "best": 0.85,
    "perfect": 0.95,
    "delicious": 0.85,
    "tasty": 0.7,
    "great": 0.75,
    "good": 0.55,
    "nice": 0.5,
    "lovely": 0.7,
    "fantastic": 0.9,
    "superb": 0.9,
    "loved": 0.85,
    "love": 0.8,
    "fresh": 0.6,
    "juicy": 0.65,
    "flavourful": 0.75,
    "flavorful": 0.75,
    "authentic": 0.7,
    "worth": 0.6,
    "recommend": 0.75,
    "favourite": 0.8,
    "favorite": 0.8,
    "heavenly": 0.95,
    "soft": 0.45,
    "crispy": 0.5,
    "generous": 0.6,
    "value": 0.55,
    "cheap": 0.4,
    "affordable": 0.55,
    "hot": 0.3,
    "solid": 0.6,
    "decent": 0.35,
    "yum": 0.8,
    "yummy": 0.8,
    "top": 0.6,
    "unbeatable": 0.9,
    "must": 0.6,
}
_NEGATIVE = {
    "terrible": -0.9,
    "awful": -0.9,
    "worst": -0.95,
    "bad": -0.7,
    "poor": -0.7,
    "bland": -0.65,
    "tasteless": -0.8,
    "stale": -0.85,
    "cold": -0.5,
    "soggy": -0.6,
    "oily": -0.45,
    "greasy": -0.5,
    "overpriced": -0.6,
    "expensive": -0.35,
    "disappointing": -0.75,
    "disappointed": -0.75,
    "avoid": -0.85,
    "rubbery": -0.6,
    "dry": -0.5,
    "burnt": -0.7,
    "raw": -0.6,
    "small": -0.3,
    "tiny": -0.4,
    "rude": -0.6,
    "dirty": -0.8,
    "unhygienic": -0.9,
    "slow": -0.4,
    "average": 0.05,
    "mediocre": -0.35,
    "okay": 0.05,
    "ok": 0.05,
    "meh": -0.3,
    "nothing special": -0.3,
    "not worth": -0.7,
    "never again": -0.9,
}
_NEGATORS = {
    "not",
    "no",
    "never",
    "hardly",
    "barely",
    "isn't",
    "wasn't",
    "aren't",
    "don't",
    "didn't",
    "doesn't",
    "won't",
    "can't",
    "nahi",
    "na",
}
_INTENSIFIERS = {
    "very": 1.3,
    "really": 1.25,
    "extremely": 1.45,
    "super": 1.3,
    "so": 1.15,
    "absolutely": 1.4,
    "quite": 1.1,
    "too": 1.1,
    "insanely": 1.4,
    "bit": 0.7,
    "slightly": 0.6,
    "somewhat": 0.7,
    "kinda": 0.75,
}

# Attributes are descriptive facts about the dish, kept separate from sentiment.
ATTRIBUTE_LEXICON = {
    "spicy": ("spicy", "hot and spicy", "jhaal", "fiery"),
    "juicy": ("juicy", "succulent"),
    "crispy": ("crispy", "crisp", "crunchy"),
    "oily": ("oily", "greasy"),
    "generous_portion": ("generous", "huge portion", "big portion", "large portion", "filling"),
    "small_portion": ("small portion", "tiny portion", "portion is small"),
    "fresh": ("fresh", "freshly made"),
    "soft": ("soft", "fluffy", "melt in mouth"),
    "authentic": ("authentic", "traditional"),
    "sweet": ("sweet", "mishti"),
    "tangy": ("tangy", "sour", "tok"),
    "cheesy": ("cheesy", "loaded with cheese"),
    "smoky": ("smoky", "charred", "tandoori"),
    "value_for_money": ("value for money", "worth the price", "cheap and", "affordable"),
    "late_night": ("late night", "open till", "after midnight"),
}

ASPECT_LEXICON = {
    "taste": ("taste", "tastes", "flavour", "flavor", "tasty", "delicious", "bland"),
    "portion": ("portion", "quantity", "serving", "filling"),
    "price": ("price", "cost", "expensive", "cheap", "overpriced", "value", "worth"),
    "service": ("service", "staff", "waiter", "rude", "polite", "attentive"),
    "ambience": ("ambience", "ambiance", "atmosphere", "decor", "vibe", "seating", "music"),
    "hygiene": ("hygiene", "clean", "dirty", "unhygienic", "sanitary"),
    "wait_time": ("wait", "waiting", "slow", "quick", "fast", "delay", "queue"),
    "consistency": ("consistent", "inconsistent", "every time", "used to be"),
    "spice": ("spicy", "spice", "jhaal", "mild", "hot"),
}


@dataclass(slots=True)
class DishHit:
    dish_key: str  # slug or alias key resolved by the caller
    matched_alias: str
    clause: str
    sentiment: float
    confidence: float
    attributes: list[str] = field(default_factory=list)
    price: float | None = None
    aspects: list[tuple[str, float]] = field(default_factory=list)


def split_clauses(text: str) -> list[str]:
    """Split on sentence boundaries and contrast markers.

    Contrast splitting is what makes opposing sentiments in one sentence work; a
    comma is *not* treated as a boundary because it splits noun lists incorrectly.
    """
    cleaned = clean_text(text)
    if not cleaned:
        return []
    parts = [p.strip(" ,;") for p in _CLAUSE_SPLIT.split(cleaned)]
    return [p for p in parts if p]


def _alias_pattern(alias: str) -> re.Pattern[str]:
    """Word-boundary pattern tolerant of an optional plural and internal spacing."""
    tokens = [re.escape(t) for t in normalize_name(alias).split() if t]
    if not tokens:
        return re.compile(r"(?!x)x")
    body = r"[\s\-]*".join(tokens)
    return re.compile(rf"(?<![a-z0-9]){body}(?:s|es)?(?![a-z0-9])", re.IGNORECASE)


def score_clause(clause: str) -> tuple[float, float]:
    """Lexicon sentiment for one clause → (sentiment in -1..1, confidence in 0..1).

    Negation flips and dampens (`not great` is mildly negative, not maximally so),
    intensifiers scale, and an empty match yields 0.0 with low confidence rather
    than a fabricated neutral-positive.
    """
    normalized = normalize_name(clause)
    if not normalized:
        return 0.0, 0.0

    for phrase, value in (("nothing special", -0.3), ("not worth", -0.7), ("never again", -0.9)):
        if phrase in normalized:
            return value, 0.7

    tokens = normalized.split()
    scores: list[float] = []

    for idx, token in enumerate(tokens):
        base = _POSITIVE.get(token) or _NEGATIVE.get(token)
        if base is None:
            continue

        multiplier = 1.0
        negated = False
        for back in range(1, 4):
            if idx - back < 0:
                break
            prev = tokens[idx - back]
            if prev in _NEGATORS:
                negated = True
            if prev in _INTENSIFIERS:
                multiplier *= _INTENSIFIERS[prev]

        value = base * multiplier
        if negated:
            # Flip, then damp: "not amazing" is disappointment, not disgust.
            value = -value * 0.65
        scores.append(max(-1.0, min(1.0, value)))

    if not scores:
        return 0.0, 0.2

    # Extremes dominate perception, so weight by magnitude rather than plain mean.
    weight_total = sum(abs(s) for s in scores) or 1.0
    sentiment = sum(s * abs(s) for s in scores) / weight_total
    confidence = min(0.9, 0.45 + 0.12 * len(scores))
    return round(max(-1.0, min(1.0, sentiment)), 3), round(confidence, 3)


def detect_attributes(clause: str) -> list[str]:
    normalized = normalize_name(clause)
    found: list[str] = []
    for attribute, phrases in ATTRIBUTE_LEXICON.items():
        if any(normalize_name(p) in normalized for p in phrases):
            found.append(attribute)
    return found


def detect_aspects(clause: str, sentiment: float) -> list[tuple[str, float]]:
    normalized = normalize_name(clause)
    aspects: list[tuple[str, float]] = []
    for aspect, cues in ASPECT_LEXICON.items():
        if any(re.search(rf"(?<![a-z]){re.escape(c)}(?![a-z])", normalized) for c in cues):
            aspects.append((aspect, sentiment))
    return aspects


def extract_dish_mentions(
    text: str,
    alias_index: dict[str, str],
) -> list[DishHit]:
    """Extract one observation per dish mentioned.

    `alias_index` maps normalized alias → dish key (slug or id). Matching is
    longest-alias-first so "chicken momo" wins over "momo" and the specific dish is
    credited rather than the generic one.

    A dish appearing in several clauses is merged, keeping the strongest-confidence
    clause — the DB constraint is one mention per (review, dish), and averaging
    contradictory clauses would erase the signal.
    """
    clauses = split_clauses(text)
    if not clauses or not alias_index:
        return []

    aliases_sorted = sorted(alias_index.items(), key=lambda kv: (-len(kv[0]), kv[0]))
    patterns = [(alias, dish_key, _alias_pattern(alias)) for alias, dish_key in aliases_sorted]

    by_dish: dict[str, DishHit] = {}

    for clause in clauses:
        clause_norm = normalize_name(clause)
        if not clause_norm:
            continue

        sentiment, confidence = score_clause(clause)
        attributes = detect_attributes(clause)
        prices = extract_prices(clause)
        price = min(prices) if prices else None
        aspects = detect_aspects(clause, sentiment)

        consumed: list[tuple[int, int]] = []
        for alias, dish_key, pattern in patterns:
            match = pattern.search(clause_norm)
            if not match:
                continue
            span = match.span()
            # Longest-first means a shorter alias inside an already-matched span is
            # the same dish being re-detected; skip it.
            if any(span[0] >= s and span[1] <= e for s, e in consumed):
                continue
            consumed.append(span)

            hit = DishHit(
                dish_key=dish_key,
                matched_alias=alias,
                clause=clause[:320],
                sentiment=sentiment,
                confidence=confidence,
                attributes=attributes,
                price=price,
                aspects=aspects,
            )
            existing = by_dish.get(dish_key)
            if existing is None or hit.confidence > existing.confidence:
                by_dish[dish_key] = hit

    return list(by_dish.values())


def detect_language(text: str) -> str:
    """Script-and-stopword heuristic for en / bn / hi.

    Enough to route text and record provenance; a real classifier can replace this
    behind the same signature without touching callers.
    """
    if not text:
        return "en"
    bengali = sum(1 for ch in text if "\u0980" <= ch <= "\u09ff")
    devanagari = sum(1 for ch in text if "\u0900" <= ch <= "\u097f")
    letters = sum(1 for ch in text if ch.isalpha()) or 1

    if bengali / letters > 0.2:
        return "bn"
    if devanagari / letters > 0.2:
        return "hi"

    tokens = set(normalize_name(text).split())
    if tokens & {"khub", "bhalo", "kemon", "khete", "darun", "mishti"}:
        return "bn-Latn"
    if tokens & {"acha", "accha", "bahut", "khana", "sabse", "lekin", "nahi"}:
        return "hi-Latn"
    return "en"
