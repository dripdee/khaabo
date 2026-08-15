"""Evidence-only summaries.

Two modes, both grounded:
* template — composes a sentence from counts and attribute frequencies
* model    — receives a JSON evidence bundle and nothing else, and every attribute
  it claims must appear in that bundle or the sentence is dropped

Either way the stored summary carries the review ids it was built from, so any claim
can be traced back to specific `review_dish_mentions`.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.models import Dish, DishScore, Review, ReviewDishMention
from app.models.enums import ReviewStatus, ScoreStatus
from app.utils.text import normalize_name

log = get_logger(__name__)

MAX_SNIPPETS = 8
MIN_MENTIONS_FOR_SUMMARY = 3

ATTRIBUTE_LABELS = {
    "generous_portion": "generous portions",
    "small_portion": "small portions",
    "value_for_money": "good value",
    "late_night": "open late",
}


@dataclass(slots=True)
class DishSummary:
    text: str
    generated_by: str
    evidence_review_ids: list[str]
    mention_count: int
    positive_ratio: float

    def to_dict(self) -> dict:
        return {
            "text": self.text,
            "generated_by": self.generated_by,
            "evidence_review_ids": self.evidence_review_ids,
            "mention_count": self.mention_count,
            "positive_ratio": round(self.positive_ratio, 4),
        }


def _label(attribute: str) -> str:
    return ATTRIBUTE_LABELS.get(attribute, attribute.replace("_", " "))


def build_dish_summary(
    session: Session, dish_id: uuid.UUID, city_id: uuid.UUID
) -> DishSummary | None:
    """Summarize a dish across a city. Returns None when evidence is too thin."""
    rows = session.execute(
        select(
            ReviewDishMention.id,
            ReviewDishMention.review_id,
            ReviewDishMention.sentiment,
            ReviewDishMention.snippet,
            ReviewDishMention.attributes,
            ReviewDishMention.price_mentioned,
        )
        .join(Review, Review.id == ReviewDishMention.review_id)
        .where(
            ReviewDishMention.dish_id == dish_id,
            Review.city_id == city_id,
            Review.status == ReviewStatus.PUBLISHED,
            Review.is_duplicate.is_(False),
        )
        .order_by(Review.published_at.desc())
    ).all()

    if len(rows) < MIN_MENTIONS_FOR_SUMMARY:
        return None

    dish = session.get(Dish, dish_id)
    if dish is None:
        return None

    positive = sum(1 for r in rows if float(r.sentiment) > 0.15)
    positive_ratio = positive / len(rows)

    attribute_counts: dict[str, int] = {}
    for row in rows:
        for attribute in row.attributes or []:
            attribute_counts[attribute] = attribute_counts.get(attribute, 0) + 1
    top_attributes = [
        a for a, _ in sorted(attribute_counts.items(), key=lambda kv: (-kv[1], kv[0]))[:3]
    ]

    prices = [float(r.price_mentioned) for r in rows if r.price_mentioned]
    restaurant_count = session.execute(
        select(DishScore.id).where(
            DishScore.dish_id == dish_id,
            DishScore.city_id == city_id,
            DishScore.status == ScoreStatus.RANKED,
        )
    ).all()

    parts = [f"{len(rows)} mentions", f"{round(positive_ratio * 100)}% positive"]
    if restaurant_count:
        parts.append(f"{len(restaurant_count)} ranked places")
    if top_attributes:
        parts.append("often described as " + ", ".join(_label(a) for a in top_attributes))
    if len(prices) >= 3:
        parts.append(f"typically ₹{int(min(prices))}–₹{int(max(prices))}")

    text = f"{dish.name}: " + " · ".join(parts) + "."

    evidence_ids = [str(r.review_id) for r in rows[:MAX_SNIPPETS]]
    return DishSummary(
        text=text,
        generated_by="template",
        evidence_review_ids=evidence_ids,
        mention_count=len(rows),
        positive_ratio=positive_ratio,
    )


def build_dish_restaurant_snippets(
    session: Session, dish_id: uuid.UUID, restaurant_id: uuid.UUID, limit: int = 2
) -> list[dict]:
    """Verbatim quotes with attribution.

    Sorted by confidence then recency, and only rows whose snippet survived
    grounding are eligible — a missing snippet means the quote could not be verified.
    """
    rows = session.execute(
        select(
            ReviewDishMention.snippet,
            ReviewDishMention.sentiment,
            ReviewDishMention.confidence,
            Review.source,
            Review.published_at,
            Review.id,
        )
        .join(Review, Review.id == ReviewDishMention.review_id)
        .where(
            ReviewDishMention.dish_id == dish_id,
            ReviewDishMention.restaurant_id == restaurant_id,
            ReviewDishMention.snippet.isnot(None),
            Review.status == ReviewStatus.PUBLISHED,
            Review.is_duplicate.is_(False),
        )
        .order_by(ReviewDishMention.confidence.desc(), Review.published_at.desc())
        .limit(limit)
    ).all()

    return [
        {
            "text": row.snippet,
            "sentiment": float(row.sentiment),
            "source": row.source.value,
            "published_at": row.published_at.isoformat() if row.published_at else None,
            "review_id": str(row.id),
        }
        for row in rows
    ]


def verify_model_summary(text: str, allowed_attributes: list[str]) -> str:
    """Drop sentences that assert attributes absent from the evidence bundle.

    This is the enforcement half of "never let AI invent facts": the model may only
    rephrase what the bundle already contains.
    """
    if not text:
        return ""
    allowed = {normalize_name(a) for a in allowed_attributes}
    kept: list[str] = []
    for sentence in text.replace("\n", " ").split("."):
        candidate = sentence.strip()
        if not candidate:
            continue
        tokens = set(normalize_name(candidate).split())
        claimed = tokens & set(ATTRIBUTE_LABELS) | tokens
        unsupported = {
            token for token in claimed if token in _SENSORY_TERMS and token not in allowed
        }
        if unsupported:
            log.info("summary_sentence_dropped", unsupported=sorted(unsupported)[:5])
            continue
        kept.append(candidate)
    return (". ".join(kept) + ".") if kept else ""


# Words that make a factual claim about the food and therefore need evidence.
_SENSORY_TERMS = {
    "spicy",
    "juicy",
    "crispy",
    "oily",
    "greasy",
    "sweet",
    "tangy",
    "cheesy",
    "smoky",
    "fresh",
    "soft",
    "authentic",
    "bland",
    "dry",
    "stale",
    "generous",
}
