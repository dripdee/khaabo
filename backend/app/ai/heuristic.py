"""Zero-dependency AI provider.

This is the default, and it is not a stub: clause splitting, alias matching,
negation-aware lexicon sentiment, aspect detection and spam scoring all run here.
The product ships fully functional with no model server, no API key and no quota.
"""

from __future__ import annotations

from app.ai.base import AIProvider
from app.ai.schemas import AspectOut, DishMentionOut, ReviewAnalysis, ReviewAnalysisRequest
from app.models.enums import AspectType, ValueSignal
from app.services.dedup import spam_score
from app.services.dish_extraction import (
    detect_aspects,
    detect_language,
    extract_dish_mentions,
    score_clause,
    split_clauses,
)
from app.utils.text import extract_prices


class HeuristicProvider(AIProvider):
    name = "heuristic"
    model = "heuristic-v1"

    async def analyze_review(self, request: ReviewAnalysisRequest) -> ReviewAnalysis:
        text = request.text
        language = request.lang_hint or detect_language(text)
        spam = spam_score(text)

        hits = extract_dish_mentions(text, request.alias_index)

        mentions: list[DishMentionOut] = []
        for hit in hits:
            mentions.append(
                DishMentionOut(
                    dish_name=hit.matched_alias,
                    matched_alias=hit.matched_alias,
                    snippet=hit.clause,
                    sentiment=hit.sentiment,
                    confidence=hit.confidence,
                    attributes=hit.attributes,
                    price_mentioned=hit.price,
                    is_recommended=_recommendation(hit.sentiment),
                    aspects=[
                        AspectOut(aspect=AspectType(a), sentiment=s, confidence=hit.confidence)
                        for a, s in hit.aspects
                        if a in AspectType.__members__.values() or _is_aspect(a)
                    ],
                )
            )

        overall = _overall_sentiment(text, request)
        aspects = _review_level_aspects(text)

        return ReviewAnalysis(
            language=language,
            is_spam=spam >= 0.6,
            spam_score=spam,
            overall_sentiment=overall,
            value_signal=_value_signal(text),
            dish_mentions=mentions,
            aspects=aspects,
            provider=self.name,
            model=self.model,
        )


def _is_aspect(value: str) -> bool:
    try:
        AspectType(value)
    except ValueError:
        return False
    return True


def _recommendation(sentiment: float) -> bool | None:
    if sentiment >= 0.5:
        return True
    if sentiment <= -0.4:
        return False
    return None


def _overall_sentiment(text: str, request: ReviewAnalysisRequest) -> float | None:
    """Prefer an explicit star rating when present; it is a stronger signal than
    lexicon inference. Otherwise average clause sentiment weighted by confidence."""
    if request.rating is not None and request.rating_scale:
        normalized = (float(request.rating) / float(request.rating_scale)) * 2.0 - 1.0
        return round(max(-1.0, min(1.0, normalized)), 3)

    scored = [score_clause(c) for c in split_clauses(text)]
    scored = [(s, c) for s, c in scored if c > 0]
    if not scored:
        return None
    total_conf = sum(c for _, c in scored) or 1.0
    value = sum(s * c for s, c in scored) / total_conf
    return round(max(-1.0, min(1.0, value)), 3)


def _review_level_aspects(text: str) -> list[AspectOut]:
    """Aspects that describe the visit rather than a specific dish."""
    found: dict[str, tuple[float, float]] = {}
    for clause in split_clauses(text):
        sentiment, confidence = score_clause(clause)
        if confidence <= 0.2:
            continue
        for aspect, value in detect_aspects(clause, sentiment):
            if aspect in {"service", "ambience", "hygiene", "wait_time", "price"}:
                prev = found.get(aspect)
                if prev is None or confidence > prev[1]:
                    found[aspect] = (value, confidence)
    return [
        AspectOut(aspect=AspectType(a), sentiment=v, confidence=c) for a, (v, c) in found.items()
    ]


def _value_signal(text: str) -> ValueSignal:
    lowered = text.lower()
    prices = extract_prices(text)
    if any(w in lowered for w in ("overpriced", "too expensive", "not worth the price")):
        return ValueSignal.EXPENSIVE
    if any(w in lowered for w in ("value for money", "cheap", "affordable", "worth every")):
        return ValueSignal.CHEAP
    if prices:
        return ValueSignal.FAIR
    return ValueSignal.UNKNOWN
