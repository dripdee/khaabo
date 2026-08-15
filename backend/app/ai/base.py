"""Provider interface and shared post-validation.

`enforce_grounding` is the important part: it runs on **every** provider's output,
including the model ones, so hallucinated dishes, invented quotes and unsupported
prices are stripped in one place rather than trusted per-provider.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from difflib import SequenceMatcher

from app.ai.schemas import DishMentionOut, ReviewAnalysis, ReviewAnalysisRequest
from app.core.config import settings
from app.core.logging import get_logger
from app.utils.text import extract_prices, normalize_name

log = get_logger(__name__)

SNIPPET_MATCH_THRESHOLD = 0.90


class AIProvider(ABC):
    name: str = "base"
    model: str | None = None

    @abstractmethod
    async def analyze_review(self, request: ReviewAnalysisRequest) -> ReviewAnalysis:
        """Return a validated analysis. Must not raise for ordinary bad input."""

    async def health(self) -> bool:
        return True


def _snippet_is_grounded(snippet: str, source_text: str) -> bool:
    """A quote must actually be in the review.

    Exact containment first (cheap), then a fuzzy check to tolerate the
    normalization the model may have applied to punctuation or casing.
    """
    if not snippet:
        return False
    norm_snippet = normalize_name(snippet)
    norm_source = normalize_name(source_text)
    if not norm_snippet or not norm_source:
        return False
    if norm_snippet in norm_source:
        return True
    matcher = SequenceMatcher(None, norm_snippet, norm_source)
    match = matcher.find_longest_match(0, len(norm_snippet), 0, len(norm_source))
    return (match.size / len(norm_snippet)) >= SNIPPET_MATCH_THRESHOLD


def enforce_grounding(
    analysis: ReviewAnalysis,
    request: ReviewAnalysisRequest,
) -> ReviewAnalysis:
    """Strip anything the source text does not support.

    Rules (see docs/ai-pipeline.md §3):
      1. a dish must resolve to the known vocabulary, unless creation is enabled
         and confidence is high
      2. a snippet must appear in the review, else it is dropped (never rewritten)
      3. a price is kept only if a currency amount exists in the text
      4. mentions below the confidence floor are discarded
      5. `is_recommended` must agree with sentiment sign
    """
    source_text = f"{request.title or ''} {request.text}"
    text_prices = set(extract_prices(source_text))
    known = {normalize_name(d) for d in request.known_dishes}
    alias_lookup = {normalize_name(a): d for a, d in request.alias_index.items()}

    kept: list[DishMentionOut] = []
    dropped: list[str] = []

    for mention in analysis.dish_mentions:
        if mention.confidence < settings.ai_min_mention_confidence:
            dropped.append(f"low_confidence:{mention.dish_name}")
            continue

        name_norm = normalize_name(mention.dish_name)
        alias_norm = normalize_name(mention.matched_alias or "")
        resolved = name_norm in known or alias_norm in alias_lookup or name_norm in alias_lookup

        if not resolved:
            allow_new = settings.ai_allow_dish_creation and mention.confidence >= 0.8
            if not allow_new:
                dropped.append(f"unknown_dish:{mention.dish_name}")
                continue

        # A dish name the model produced must at least appear in the text, otherwise
        # it is an association from training data rather than an observation.
        if not _snippet_is_grounded(mention.dish_name, source_text) and not (
            alias_norm and _snippet_is_grounded(mention.matched_alias or "", source_text)
        ):
            dropped.append(f"dish_not_in_text:{mention.dish_name}")
            continue

        snippet = mention.snippet
        if snippet and not _snippet_is_grounded(snippet, source_text):
            snippet = None  # drop the quote, keep the observation

        price = mention.price_mentioned
        if price is not None and price not in text_prices:
            price = None  # no currency evidence in the text

        recommended = mention.is_recommended
        if recommended is True and mention.sentiment < 0:
            recommended = None
        if recommended is False and mention.sentiment > 0.3:
            recommended = None

        kept.append(
            mention.model_copy(
                update={
                    "snippet": snippet,
                    "price_mentioned": price,
                    "is_recommended": recommended,
                }
            )
        )

    grounded_aspects = [
        aspect
        for aspect in analysis.aspects
        if aspect.snippet is None or _snippet_is_grounded(aspect.snippet, source_text)
    ]

    if dropped:
        log.info(
            "ai_grounding_filtered",
            review_id=request.review_id,
            provider=analysis.provider,
            dropped=dropped[:10],
            dropped_count=len(dropped),
        )

    return analysis.model_copy(update={"dish_mentions": kept, "aspects": grounded_aspects})
