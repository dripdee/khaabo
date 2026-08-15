"""Dish-specific ranking.

Design notes
------------
* The scoring core is **pure**: `compute_dish_score` takes plain dataclasses and
  returns a plain dataclass. No DB, no clock, no config globals. That is what makes
  the "2 reviews vs 500 reviews" property testable and stable.
* Weights are injected (`RankingWeights`), defaulting to settings, so re-tuning is a
  config change and every persisted score records its `weights_version`.
* Bayesian shrinkage toward a dish/city prior is the safety property that stops a
  handful of glowing mentions from outranking established evidence.
* Explanations are **reason codes**, never generated prose (see docs/ranking.md §6).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import UTC, datetime
from statistics import fmean, pstdev

from app.core.config import settings
from app.models.enums import ScoreStatus, SourceType

# Per-source trust. Deliberately explicit rather than learned: it must be auditable.
SOURCE_QUALITY: dict[str, float] = {
    SourceType.USER: 0.90,
    SourceType.MANUAL: 0.85,
    SourceType.REDDIT: 0.75,
    SourceType.YOUTUBE: 0.60,
    SourceType.OSM: 0.40,
}
DEFAULT_SOURCE_QUALITY = 0.5

# A new/unverified account's evidence is damped until it has an accepted review.
UNVERIFIED_USER_QUALITY = 0.60


def source_quality_for(source: str, *, verified: bool = True) -> float:
    base = SOURCE_QUALITY.get(source, DEFAULT_SOURCE_QUALITY)
    if source == SourceType.USER and not verified:
        return UNVERIFIED_USER_QUALITY
    return base


@dataclass(frozen=True, slots=True)
class RankingWeights:
    sentiment: float = 0.35
    recency: float = 0.20
    consistency: float = 0.15
    volume: float = 0.10
    source_quality: float = 0.10
    engagement: float = 0.05
    confidence: float = 0.05
    version: str = "v1"

    halflife_days: float = 180.0
    bayes_m: float = 6.0
    bayes_prior: float = 0.62
    volume_saturation: float = 50.0
    engagement_saturation: float = 500.0
    min_mentions: int = 3
    min_weight: float = 1.5

    @classmethod
    def from_settings(cls) -> RankingWeights:
        return cls(
            sentiment=settings.ranking_w_sentiment,
            recency=settings.ranking_w_recency,
            consistency=settings.ranking_w_consistency,
            volume=settings.ranking_w_volume,
            source_quality=settings.ranking_w_source_quality,
            engagement=settings.ranking_w_engagement,
            confidence=settings.ranking_w_confidence,
            version=settings.ranking_weights_version,
            halflife_days=settings.ranking_halflife_days,
            bayes_m=settings.ranking_bayes_m,
            bayes_prior=settings.ranking_bayes_prior,
            volume_saturation=settings.ranking_volume_saturation,
            min_mentions=settings.ranking_min_mentions,
            min_weight=settings.ranking_min_weight,
        )

    def as_dict(self) -> dict[str, float]:
        return {
            "sentiment": self.sentiment,
            "recency": self.recency,
            "consistency": self.consistency,
            "volume": self.volume,
            "source_quality": self.source_quality,
            "engagement": self.engagement,
            "confidence": self.confidence,
        }

    def validate(self) -> None:
        total = sum(self.as_dict().values())
        if abs(total - 1.0) > 1e-3:
            raise ValueError(f"Ranking weights must sum to 1.0, got {total:.4f}")


@dataclass(frozen=True, slots=True)
class Observation:
    """One dish mention, flattened for scoring."""

    sentiment: float  # -1..1
    confidence: float = 0.5  # 0..1
    source: str = SourceType.USER
    engagement: int = 0
    observed_at: datetime | None = None
    price: float | None = None
    attributes: tuple[str, ...] = ()
    verified_author: bool = True
    extraction_method: str = "alias"

    @property
    def positivity(self) -> float:
        """Sentiment mapped to 0..1."""
        return (_clamp(self.sentiment, -1.0, 1.0) + 1.0) / 2.0


@dataclass(slots=True)
class ScoreComponents:
    sentiment: float = 0.0
    recency: float = 0.0
    consistency: float = 0.0
    volume: float = 0.0
    source_quality: float = 0.0
    engagement: float = 0.0
    confidence: float = 0.0

    def as_dict(self) -> dict[str, float]:
        return {
            "sentiment": self.sentiment,
            "recency": self.recency,
            "consistency": self.consistency,
            "volume": self.volume,
            "source_quality": self.source_quality,
            "engagement": self.engagement,
            "confidence": self.confidence,
        }


@dataclass(slots=True)
class DishScoreResult:
    status: ScoreStatus
    score: float | None
    raw_score: float | None
    components: ScoreComponents
    positive_ratio: float
    mention_count: int
    evidence_weight: float
    consistency: float
    recency_days: float | None
    bayesian_score: float | None
    price_avg: float | None
    value_score: float | None
    why: list[dict] = field(default_factory=list)
    top_attributes: list[str] = field(default_factory=list)
    weights_version: str = "v1"
    # Pre-shrinkage weighted positivity. Used by "hidden gem", which is *about*
    # low-volume entries: judging it on the shrunk score would make the badge
    # unreachable, since shrinkage exists precisely to damp thin evidence.
    observed_positivity: float = 0.0
    # Badges are assigned by `assign_badges` once the whole peer group is known,
    # because "hidden gem" and "best value" are relative, not absolute, properties.
    is_best_value: bool = False
    is_hidden_gem: bool = False
    is_most_consistent: bool = False
    trend: str | None = None
    trend_delta: float | None = None

    @property
    def is_ranked(self) -> bool:
        return self.status is ScoreStatus.RANKED


# ── helpers ──────────────────────────────────────────────────────────────────


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def time_decay(age_days: float, halflife_days: float) -> float:
    """Exponential decay. Old evidence fades but never becomes worthless."""
    if age_days <= 0:
        return 1.0
    if halflife_days <= 0:
        return 1.0
    return 0.5 ** (age_days / halflife_days)


def _age_days(observed_at: datetime | None, now: datetime) -> float:
    if observed_at is None:
        return 365.0  # unknown timestamp is treated as a year old, not as fresh
    if observed_at.tzinfo is None:
        observed_at = observed_at.replace(tzinfo=UTC)
    return max(0.0, (now - observed_at).total_seconds() / 86400.0)


def bayesian_shrink(observed: float, weight: float, prior: float, m: float) -> float:
    """Pull a mean toward `prior` with strength `m` measured in evidence units."""
    if weight <= 0:
        return prior
    return (observed * weight + prior * m) / (weight + m)


# ── core ─────────────────────────────────────────────────────────────────────


def compute_dish_score(
    observations: list[Observation],
    *,
    weights: RankingWeights | None = None,
    prior: float | None = None,
    now: datetime | None = None,
    city_median_price: float | None = None,
) -> DishScoreResult:
    """Score one (dish, restaurant) pair from its observations.

    Returns `status=insufficient_data` with `score=None` when the evidence is too
    thin. Callers must render "Not enough data" rather than a fabricated rank.
    """
    w = weights or RankingWeights.from_settings()
    now = now or datetime.now(UTC)
    prior_value = prior if prior is not None else w.bayes_prior

    if not observations:
        return DishScoreResult(
            status=ScoreStatus.INSUFFICIENT_DATA,
            score=None,
            raw_score=None,
            components=ScoreComponents(),
            positive_ratio=0.0,
            mention_count=0,
            evidence_weight=0.0,
            consistency=0.0,
            recency_days=None,
            bayesian_score=None,
            price_avg=None,
            value_score=None,
            why=[],
            weights_version=w.version,
        )

    n = len(observations)
    ages = [_age_days(o.observed_at, now) for o in observations]
    decays = [time_decay(a, w.halflife_days) for a in ages]

    # Evidence weight blends how sure we are, how fresh it is, and how much the
    # source is trusted. All three must be present for a mention to carry weight.
    quals = [source_quality_for(o.source, verified=o.verified_author) for o in observations]
    obs_weights = [
        max(0.0, _clamp(o.confidence)) * d * q
        for o, d, q in zip(observations, decays, quals, strict=True)
    ]
    total_weight = sum(obs_weights)

    # Sufficiency is judged on *undecayed* weight. Age is already priced into the
    # recency component; letting it also gate sufficiency would double-count it and
    # make a well-documented older dish disappear instead of merely ranking lower.
    gate_weight = sum(
        max(0.0, _clamp(o.confidence)) * q for o, q in zip(observations, quals, strict=True)
    )

    positivities = [o.positivity for o in observations]

    # 1. sentiment (weighted, then shrunk toward the prior)
    if total_weight > 0:
        weighted_p = (
            sum(p * wt for p, wt in zip(positivities, obs_weights, strict=True)) / total_weight
        )
    else:
        weighted_p = fmean(positivities)
    sentiment_component = _clamp(bayesian_shrink(weighted_p, total_weight, prior_value, w.bayes_m))

    # 2. recency, from the freshest mention
    min_age = min(ages)
    recency_component = _clamp(time_decay(min_age, w.halflife_days))

    # 3. consistency — dispersion, not level. Unknown (<3) is 0.5, not 1.0.
    if n >= 3:
        spread = pstdev(positivities)
        consistency_component = _clamp(1.0 - min(1.0, spread / 0.5))
    else:
        consistency_component = 0.5

    # 4. volume — log-saturating so a viral thread cannot buy rank
    volume_component = _clamp(math.log1p(n) / math.log1p(w.volume_saturation))

    # 5. source quality — decay-weighted mean (decay only, to avoid double counting)
    decay_total = sum(decays) or 1.0
    source_component = _clamp(sum(q * d for q, d in zip(quals, decays, strict=True)) / decay_total)

    # 6. engagement — log-saturating total
    engagement_total = sum(max(0, o.engagement) for o in observations)
    engagement_component = _clamp(
        math.log1p(engagement_total) / math.log1p(w.engagement_saturation)
    )

    # 7. confidence — mean extraction confidence, penalised for alias-only and thin data
    mean_conf = fmean(_clamp(o.confidence) for o in observations)
    alias_only = all(o.extraction_method != "ai" for o in observations)
    confidence_component = _clamp(
        mean_conf * (0.85 if alias_only else 1.0) * (1.0 if n >= 3 else 0.8)
    )

    components = ScoreComponents(
        sentiment=sentiment_component,
        recency=recency_component,
        consistency=consistency_component,
        volume=volume_component,
        source_quality=source_component,
        engagement=engagement_component,
        confidence=confidence_component,
    )

    raw = sum(components.as_dict()[k] * v for k, v in w.as_dict().items())
    raw = _clamp(raw)

    positive_ratio = sum(1 for o in observations if o.sentiment > 0.15) / n

    prices = [o.price for o in observations if o.price and o.price > 0]
    price_avg = fmean(prices) if prices else None
    value_score = _value_score(sentiment_component, price_avg, city_median_price)

    insufficient = n < w.min_mentions or gate_weight < w.min_weight
    if insufficient:
        return DishScoreResult(
            status=ScoreStatus.INSUFFICIENT_DATA,
            score=None,
            raw_score=round(raw, 5),
            components=components,
            positive_ratio=round(positive_ratio, 4),
            mention_count=n,
            evidence_weight=round(total_weight, 4),
            consistency=round(consistency_component, 4),
            recency_days=round(min_age, 2),
            bayesian_score=round(sentiment_component, 4),
            price_avg=round(price_avg, 2) if price_avg else None,
            value_score=value_score,
            why=[],
            top_attributes=top_attributes(observations),
            weights_version=w.version,
            observed_positivity=round(weighted_p, 4),
        )

    return DishScoreResult(
        status=ScoreStatus.RANKED,
        score=round(raw * 100.0, 3),
        raw_score=round(raw, 5),
        components=components,
        positive_ratio=round(positive_ratio, 4),
        mention_count=n,
        evidence_weight=round(total_weight, 4),
        consistency=round(consistency_component, 4),
        recency_days=round(min_age, 2),
        bayesian_score=round(sentiment_component, 4),
        price_avg=round(price_avg, 2) if price_avg else None,
        value_score=value_score,
        why=build_why(components, positive_ratio, n, min_age),
        top_attributes=top_attributes(observations),
        weights_version=w.version,
        observed_positivity=round(weighted_p, 4),
    )


def _value_score(
    sentiment: float, price_avg: float | None, city_median_price: float | None
) -> float | None:
    """Quality per rupee. Returns None without a price signal — no guessing."""
    if not price_avg or not city_median_price or city_median_price <= 0:
        return None
    factor = _clamp(city_median_price / price_avg, 0.5, 1.6)
    return round(_clamp(sentiment * factor), 4)


def top_attributes(observations: list[Observation], limit: int = 6) -> list[str]:
    counts: dict[str, int] = {}
    for obs in observations:
        for attr in obs.attributes:
            counts[attr] = counts.get(attr, 0) + 1
    return [a for a, _ in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:limit]]


# ── explanations ─────────────────────────────────────────────────────────────

WHY_THRESHOLDS = {
    "positive_ratio": 0.70,
    "recent": 0.60,
    "consistency": 0.65,
    "source_quality": 0.70,
    "engagement": 0.40,
}


def build_why(
    components: ScoreComponents,
    positive_ratio: float,
    mention_count: int,
    recency_days: float,
) -> list[dict]:
    """Structured reason codes.

    Only reasons that actually cleared their threshold are emitted, capped at four,
    with mention count always included so the reader can judge the evidence base.
    A caveat is emitted for genuinely inconsistent places — the explanation has to
    be able to say something unflattering, otherwise it is marketing, not evidence.
    """
    reasons: list[dict] = []

    if positive_ratio >= WHY_THRESHOLDS["positive_ratio"]:
        reasons.append(
            {
                "code": "positive_ratio",
                "label": f"{round(positive_ratio * 100)}% positive dish sentiment",
                "value": round(positive_ratio, 4),
            }
        )

    if components.recency >= WHY_THRESHOLDS["recent"]:
        reasons.append(
            {
                "code": "recent",
                "label": "strong recent reviews",
                "value": round(components.recency, 4),
            }
        )
    elif recency_days > 365:
        reasons.append(
            {
                "code": "stale",
                "label": "no recent mentions",
                "value": round(recency_days, 1),
            }
        )

    if components.consistency >= WHY_THRESHOLDS["consistency"]:
        reasons.append(
            {
                "code": "consistency",
                "label": "consistent quality",
                "value": round(components.consistency, 4),
            }
        )
    elif components.consistency < 0.35:
        reasons.append(
            {
                "code": "inconsistent",
                "label": "mixed reports",
                "value": round(components.consistency, 4),
            }
        )

    if components.source_quality >= WHY_THRESHOLDS["source_quality"]:
        reasons.append(
            {
                "code": "source_quality",
                "label": "trusted sources",
                "value": round(components.source_quality, 4),
            }
        )

    reasons = reasons[:3]
    reasons.append(
        {
            "code": "mentions",
            "label": f"{mention_count} dish mention{'s' if mention_count != 1 else ''}",
            "value": mention_count,
        }
    )
    return reasons


# ── badges ───────────────────────────────────────────────────────────────────


@dataclass(slots=True)
class RankedEntry:
    restaurant_id: str
    result: DishScoreResult


def assign_badges(entries: list[RankedEntry]) -> None:
    """Mark best-value / hidden-gem / most-consistent in place.

    All three require a ranked status; badges on thin data would be noise.
    """
    ranked = [e for e in entries if e.result.is_ranked]
    if not ranked:
        return

    # Best value: highest value_score among rows that actually have a price signal.
    valued = [e for e in ranked if e.result.value_score is not None]
    if valued:
        best = max(valued, key=lambda e: e.result.value_score or 0.0)
        best.result.is_best_value = True

    # Most consistent: needs a real sample, otherwise "consistent" is meaningless.
    consistent_pool = [e for e in ranked if e.result.mention_count >= 5]
    if consistent_pool:
        most = max(consistent_pool, key=lambda e: e.result.consistency)
        most.result.is_most_consistent = True

    # Hidden gem: excellent and consistent, but under-discussed relative to peers.
    # Judged on `observed_positivity` (pre-shrinkage) because shrinkage deliberately
    # damps low-volume entries, which is exactly the population this badge targets.
    counts = sorted(e.result.mention_count for e in ranked)
    if len(counts) >= 3:
        cutoff = counts[max(0, len(counts) // 3 - 1)]
        for e in ranked:
            r = e.result
            if (
                r.observed_positivity >= 0.85
                and r.consistency >= 0.60
                and r.mention_count <= cutoff
            ):
                r.is_hidden_gem = True


def aggregate_dish_score(restaurant_scores: list[float], limit: int = 10) -> float | None:
    """City-level score for a dish: mean of its top N restaurants.

    Averaging the top few (rather than everything, or just the best) keeps one
    outstanding outlier from implying the whole city does this dish well.
    """
    ranked = sorted((s for s in restaurant_scores if s is not None), reverse=True)[:limit]
    if not ranked:
        return None
    return round(fmean(ranked), 3)
