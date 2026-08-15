"""Ranking persistence.

The pure scoring core lives in `app.services.ranking`. This module only:
1. loads observations for a (dish, restaurant) pair or a dirty set
2. calls the pure functions
3. upserts `dish_scores` / `restaurant_scores` / snapshots / trend metrics

Recomputation is **incremental**: AI processing reports the exact pairs it touched,
and only those pairs plus their dish aggregate are rebuilt. The nightly sweep is a
safety net, not the primary path.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from statistics import fmean, median

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.models import (
    Dish,
    DishScore,
    RankingSnapshot,
    Restaurant,
    RestaurantDish,
    RestaurantScore,
    Review,
    ReviewAspect,
    ReviewDishMention,
    TrendMetric,
)
from app.models.enums import ReviewStatus, ScoreStatus, TrendSubject
from app.services.food_dna import DnaInput, aggregate_aspect_sentiment, build_food_dna
from app.services.ranking import (
    Observation,
    RankedEntry,
    RankingWeights,
    assign_badges,
    compute_dish_score,
)
from app.services.trends import TrendConfig, detect_trend

log = get_logger(__name__)

Pair = tuple[uuid.UUID, uuid.UUID]  # (dish_id, restaurant_id)


@dataclass(slots=True)
class RecomputeSummary:
    pairs: int = 0
    ranked: int = 0
    insufficient: int = 0
    dishes: int = 0
    restaurants: int = 0


def _load_observations(
    session: Session, dish_id: uuid.UUID, restaurant_id: uuid.UUID
) -> list[Observation]:
    """Only published, non-duplicate evidence counts toward a score."""
    rows = session.execute(
        select(
            ReviewDishMention.sentiment,
            ReviewDishMention.confidence,
            ReviewDishMention.price_mentioned,
            ReviewDishMention.attributes,
            ReviewDishMention.extraction_method,
            Review.source,
            Review.engagement_score,
            Review.published_at,
            Review.user_id,
        )
        .join(Review, Review.id == ReviewDishMention.review_id)
        .where(
            ReviewDishMention.dish_id == dish_id,
            ReviewDishMention.restaurant_id == restaurant_id,
            Review.status == ReviewStatus.PUBLISHED,
            Review.is_duplicate.is_(False),
        )
    ).all()

    return [
        Observation(
            sentiment=float(row.sentiment),
            confidence=float(row.confidence),
            source=row.source.value,
            engagement=int(row.engagement_score or 0),
            observed_at=row.published_at,
            price=float(row.price_mentioned) if row.price_mentioned else None,
            attributes=tuple(row.attributes or ()),
            extraction_method=row.extraction_method.value,
        )
        for row in rows
    ]


def _city_dish_prior(session: Session, dish_id: uuid.UUID, city_id: uuid.UUID) -> float | None:
    """Mean positivity for this dish across the city — the shrinkage target.

    Using a dish-and-city prior rather than a global constant means a dish that is
    generally excellent in this city is not unfairly dragged down, and vice versa.
    """
    rows = (
        session.execute(
            select(ReviewDishMention.sentiment)
            .join(Review, Review.id == ReviewDishMention.review_id)
            .where(
                ReviewDishMention.dish_id == dish_id,
                Review.city_id == city_id,
                Review.status == ReviewStatus.PUBLISHED,
                Review.is_duplicate.is_(False),
            )
        )
        .scalars()
        .all()
    )

    if len(rows) < 10:
        return None  # too thin to be a prior; caller falls back to the configured default
    return fmean((float(s) + 1.0) / 2.0 for s in rows)


def _city_median_price(session: Session, dish_id: uuid.UUID, city_id: uuid.UUID) -> float | None:
    rows = (
        session.execute(
            select(ReviewDishMention.price_mentioned)
            .join(Review, Review.id == ReviewDishMention.review_id)
            .where(
                ReviewDishMention.dish_id == dish_id,
                Review.city_id == city_id,
                ReviewDishMention.price_mentioned.isnot(None),
                Review.status == ReviewStatus.PUBLISHED,
            )
        )
        .scalars()
        .all()
    )
    prices = [float(p) for p in rows if p]
    return median(prices) if len(prices) >= 3 else None


def recompute_pairs(session: Session, pairs: Iterable[Pair]) -> RecomputeSummary:
    """Recompute the given (dish, restaurant) pairs and their dish aggregates."""
    weights = RankingWeights.from_settings()
    trend_config = TrendConfig.from_settings()
    summary = RecomputeSummary()

    unique_pairs = set(pairs)
    touched_dishes: set[uuid.UUID] = set()
    touched_restaurants: set[uuid.UUID] = set()

    for dish_id, restaurant_id in unique_pairs:
        restaurant = session.get(Restaurant, restaurant_id)
        if restaurant is None:
            continue
        city_id = restaurant.city_id

        observations = _load_observations(session, dish_id, restaurant_id)
        prior = _city_dish_prior(session, dish_id, city_id)
        city_price = _city_median_price(session, dish_id, city_id)

        result = compute_dish_score(
            observations,
            weights=weights,
            prior=prior,
            city_median_price=city_price,
        )
        trend = detect_trend(observations, config=trend_config)

        _upsert_dish_score(session, dish_id, restaurant_id, city_id, result, trend)
        _upsert_restaurant_dish(session, dish_id, restaurant_id, observations, result)

        summary.pairs += 1
        if result.is_ranked:
            summary.ranked += 1
        else:
            summary.insufficient += 1

        touched_dishes.add(dish_id)
        touched_restaurants.add(restaurant_id)

    for dish_id in touched_dishes:
        _refresh_dish_aggregate(session, dish_id)
        summary.dishes += 1

    for restaurant_id in touched_restaurants:
        recompute_restaurant_score(session, restaurant_id)
        summary.restaurants += 1

    log.info(
        "ranking_recomputed",
        pairs=summary.pairs,
        ranked=summary.ranked,
        insufficient=summary.insufficient,
    )
    return summary


def _upsert_dish_score(
    session: Session,
    dish_id: uuid.UUID,
    restaurant_id: uuid.UUID,
    city_id: uuid.UUID,
    result,
    trend,
) -> DishScore:
    row = session.execute(
        select(DishScore).where(
            DishScore.dish_id == dish_id,
            DishScore.restaurant_id == restaurant_id,
            DishScore.city_id == city_id,
        )
    ).scalar_one_or_none()

    if row is None:
        row = DishScore(dish_id=dish_id, restaurant_id=restaurant_id, city_id=city_id)
        session.add(row)

    components = result.components
    row.score = result.score
    row.raw_score = result.raw_score
    row.sentiment_component = components.sentiment
    row.recency_component = components.recency
    row.consistency_component = components.consistency
    row.volume_component = components.volume
    row.source_quality_component = components.source_quality
    row.engagement_component = components.engagement
    row.confidence_component = components.confidence
    row.positive_ratio = result.positive_ratio
    row.observed_positivity = result.observed_positivity
    row.mention_count = result.mention_count
    row.evidence_weight = result.evidence_weight
    row.consistency = result.consistency
    row.recency_days = result.recency_days
    row.bayesian_score = result.bayesian_score
    row.price_avg = result.price_avg
    row.value_score = result.value_score
    row.why = result.why
    row.top_attributes = result.top_attributes
    row.status = result.status
    row.weights_version = result.weights_version
    row.computed_at = datetime.now(UTC)

    # Trend is written only when the gate passed; otherwise it is explicitly NULL so
    # the UI renders no arrow rather than a stale one.
    row.trend = trend.direction
    row.trend_delta = trend.delta

    session.flush()
    return row


def _upsert_restaurant_dish(
    session: Session,
    dish_id: uuid.UUID,
    restaurant_id: uuid.UUID,
    observations: list[Observation],
    result,
) -> None:
    row = session.execute(
        select(RestaurantDish).where(
            RestaurantDish.dish_id == dish_id,
            RestaurantDish.restaurant_id == restaurant_id,
        )
    ).scalar_one_or_none()

    if row is None:
        row = RestaurantDish(dish_id=dish_id, restaurant_id=restaurant_id)
        session.add(row)

    prices = [o.price for o in observations if o.price]
    dates = [o.observed_at for o in observations if o.observed_at]

    row.mention_count = len(observations)
    row.positive_count = sum(1 for o in observations if o.sentiment > 0.15)
    row.negative_count = sum(1 for o in observations if o.sentiment < -0.15)
    row.neutral_count = len(observations) - row.positive_count - row.negative_count
    row.price_min = min(prices) if prices else None
    row.price_max = max(prices) if prices else None
    row.price_avg = result.price_avg
    row.first_mentioned_at = min(dates) if dates else None
    row.last_mentioned_at = max(dates) if dates else None
    session.flush()


def _refresh_dish_aggregate(session: Session, dish_id: uuid.UUID) -> None:
    """Recompute dish-level counters, badges and the city aggregate score."""
    rows = session.execute(select(DishScore).where(DishScore.dish_id == dish_id)).scalars().all()

    # Badges are relative to the peer group, so they are assigned after all the
    # pair scores for this dish exist.
    entries = [RankedEntry(restaurant_id=str(r.restaurant_id), result=_as_result(r)) for r in rows]
    assign_badges(entries)
    for row, entry in zip(rows, entries, strict=True):
        row.is_best_value = entry.result.is_best_value
        row.is_hidden_gem = entry.result.is_hidden_gem
        row.is_most_consistent = entry.result.is_most_consistent

    dish = session.get(Dish, dish_id)
    if dish is not None:
        dish.mention_count = sum(int(r.mention_count) for r in rows)
        dish.restaurant_count = sum(1 for r in rows if r.status == ScoreStatus.RANKED)

    _persist_ranking_snapshots(session, rows)
    session.flush()


def _persist_ranking_snapshots(session: Session, rows: list[DishScore]) -> None:
    """Append ranking history rows, but only when something materially changed.

    Without this guard every refresh cycle would add a row per ranked pair, causing
    unbounded ``ranking_snapshots`` growth. A row is written only when the score moved
    by more than ``settings.snapshot_score_delta`` or mentions changed by at least
    ``settings.snapshot_mention_delta`` since the last snapshot for that pair. The first
    snapshot for a pair is always written.
    """
    from app.core.config import settings

    ranked = [r for r in rows if r.status == ScoreStatus.RANKED]
    if not ranked:
        return

    last_by_pair: dict[Pair, RankingSnapshot] = {}
    for row in ranked:
        last = (
            session.execute(
                select(RankingSnapshot)
                .where(
                    RankingSnapshot.dish_id == row.dish_id,
                    RankingSnapshot.restaurant_id == row.restaurant_id,
                )
                .order_by(RankingSnapshot.taken_at.desc())
                .limit(1)
            )
            .scalars()
            .first()
        )
        if last is not None:
            last_by_pair[(row.dish_id, row.restaurant_id)] = last

    score_delta = settings.snapshot_score_delta
    mention_delta = settings.snapshot_mention_delta

    for row in ranked:
        last = last_by_pair.get((row.dish_id, row.restaurant_id))
        if last is not None:
            score_changed = (
                (row.score is None and last.score is not None)
                or (row.score is not None and last.score is None)
                or (
                    row.score is not None
                    and last.score is not None
                    and abs(float(row.score) - float(last.score)) >= score_delta
                )
            )
            mentions_changed = (
                abs(int(row.mention_count) - int(last.mention_count)) >= mention_delta
            )
            if not score_changed and not mentions_changed:
                continue
        session.add(
            RankingSnapshot(
                dish_id=row.dish_id,
                restaurant_id=row.restaurant_id,
                city_id=row.city_id,
                score=row.score,
                mention_count=row.mention_count,
                weights_version=row.weights_version,
            )
        )


def _as_result(row: DishScore):
    """Adapt a persisted row back into the shape `assign_badges` expects.

    `observed_positivity` is read from the row rather than recomputed, so badges
    assigned after a DB round-trip match what the in-memory scorer would have chosen.
    """

    @dataclass(slots=True)
    class _R:
        status: ScoreStatus
        mention_count: int
        consistency: float
        value_score: float | None
        observed_positivity: float
        is_best_value: bool = False
        is_hidden_gem: bool = False
        is_most_consistent: bool = False

        @property
        def is_ranked(self) -> bool:
            return self.status == ScoreStatus.RANKED

    return _R(
        status=row.status,
        mention_count=int(row.mention_count),
        consistency=float(row.consistency),
        value_score=float(row.value_score) if row.value_score is not None else None,
        observed_positivity=float(row.observed_positivity or 0),
    )


def recompute_restaurant_score(session: Session, restaurant_id: uuid.UUID) -> None:
    """Rollup + Food DNA for one restaurant, from its dish scores and aspects."""
    restaurant = session.get(Restaurant, restaurant_id)
    if restaurant is None:
        return

    dish_rows = session.execute(
        select(DishScore, Dish.name)
        .join(Dish, Dish.id == DishScore.dish_id)
        .where(DishScore.restaurant_id == restaurant_id)
    ).all()

    aspect_rows = session.execute(
        select(ReviewAspect.aspect, ReviewAspect.sentiment)
        .join(Review, Review.id == ReviewAspect.review_id)
        .where(Review.restaurant_id == restaurant_id, Review.status == ReviewStatus.PUBLISHED)
    ).all()

    attribute_counts: dict[str, int] = {}
    for row, _ in dish_rows:
        for attribute in row.top_attributes or []:
            attribute_counts[attribute] = attribute_counts.get(attribute, 0) + 1

    total_mentions = sum(int(row.mention_count) for row, _ in dish_rows)
    ranked_scores = [float(row.score) for row, _ in dish_rows if row.score is not None]
    sentiments = [float(row.sentiment_component) for row, _ in dish_rows if row.mention_count]
    consistencies = [float(row.consistency) for row, _ in dish_rows if int(row.mention_count) >= 3]

    trend_row = (
        session.execute(
            select(TrendMetric)
            .where(
                TrendMetric.subject_type == TrendSubject.RESTAURANT,
                TrendMetric.restaurant_id == restaurant_id,
            )
            .order_by(TrendMetric.computed_at.desc())
        )
        .scalars()
        .first()
    )

    dna = build_food_dna(
        DnaInput(
            dish_labels=[
                (name, float(row.sentiment_component), int(row.mention_count))
                for row, name in dish_rows
            ],
            attribute_counts=attribute_counts,
            aspect_sentiment=aggregate_aspect_sentiment(
                [(a.value, float(s)) for a, s in aspect_rows]
            ),
            price_avg=_mean_or_none(
                [float(row.price_avg) for row, _ in dish_rows if row.price_avg]
            ),
            city_median_price=_city_price_level_median(session, restaurant.city_id),
            consistency=fmean(consistencies) if consistencies else 0.0,
            trend=trend_row.direction if trend_row else None,
            total_mentions=total_mentions,
            cuisines=list(restaurant.cuisines or []),
        )
    )

    row = session.execute(
        select(RestaurantScore).where(RestaurantScore.restaurant_id == restaurant_id)
    ).scalar_one_or_none()
    if row is None:
        row = RestaurantScore(restaurant_id=restaurant_id, city_id=restaurant.city_id)
        session.add(row)

    row.overall_score = round(fmean(ranked_scores), 3) if ranked_scores else None
    row.sentiment = round(fmean(sentiments), 4) if sentiments else 0.0
    row.consistency = round(fmean(consistencies), 4) if consistencies else 0.0
    row.value_score = _mean_or_none(
        [float(r.value_score) for r, _ in dish_rows if r.value_score is not None]
    )
    row.price_level = restaurant.price_level
    row.trend = trend_row.direction if trend_row else None
    row.trend_delta = trend_row.delta if trend_row else None
    row.dna = dna
    row.top_dish_ids = [
        r.dish_id for r, _ in sorted(dish_rows, key=lambda pair: -(float(pair[0].score or 0)))[:5]
    ]
    row.evidence_count = total_mentions
    row.status = ScoreStatus.RANKED if ranked_scores else ScoreStatus.INSUFFICIENT_DATA
    row.computed_at = datetime.now(UTC)

    # Signature dish: a meaningful share of what this restaurant is talked about for.
    if total_mentions:
        for r, _ in dish_rows:
            share = int(r.mention_count) / total_mentions
            session.execute(
                update(RestaurantDish)
                .where(
                    RestaurantDish.restaurant_id == restaurant_id,
                    RestaurantDish.dish_id == r.dish_id,
                )
                .values(is_signature=share >= 0.25 and int(r.mention_count) >= 3)
            )

    session.flush()


def _mean_or_none(values: list[float]) -> float | None:
    return round(fmean(values), 4) if values else None


def _city_price_level_median(session: Session, city_id: uuid.UUID) -> float | None:
    rows = (
        session.execute(
            select(RestaurantDish.price_avg)
            .join(Restaurant, Restaurant.id == RestaurantDish.restaurant_id)
            .where(Restaurant.city_id == city_id, RestaurantDish.price_avg.isnot(None))
        )
        .scalars()
        .all()
    )
    prices = [float(p) for p in rows if p]
    return median(prices) if len(prices) >= 5 else None


def pairs_for_review(session: Session, review_id: uuid.UUID) -> list[Pair]:
    rows = session.execute(
        select(ReviewDishMention.dish_id, ReviewDishMention.restaurant_id).where(
            ReviewDishMention.review_id == review_id
        )
    ).all()
    return [(row.dish_id, row.restaurant_id) for row in rows]


def stale_pairs(session: Session, *, older_than_hours: int = 24, limit: int = 5000) -> list[Pair]:
    """Nightly sweep target: pairs whose score predates their newest evidence."""
    cutoff = datetime.now(UTC) - timedelta(hours=older_than_hours)
    rows = session.execute(
        select(DishScore.dish_id, DishScore.restaurant_id)
        .where(DishScore.computed_at < cutoff)
        .order_by(DishScore.computed_at)
        .limit(limit)
    ).all()

    missing = session.execute(
        select(ReviewDishMention.dish_id, ReviewDishMention.restaurant_id)
        .outerjoin(
            DishScore,
            (DishScore.dish_id == ReviewDishMention.dish_id)
            & (DishScore.restaurant_id == ReviewDishMention.restaurant_id),
        )
        .where(DishScore.id.is_(None))
        .group_by(ReviewDishMention.dish_id, ReviewDishMention.restaurant_id)
        .limit(limit)
    ).all()

    return list({(r.dish_id, r.restaurant_id) for r in [*rows, *missing]})


def recompute_trends(session: Session, city_id: uuid.UUID | None = None) -> int:
    """Persist dish and restaurant trend metrics for the trending page."""
    config = TrendConfig.from_settings()
    written = 0

    dish_pairs = session.execute(
        select(ReviewDishMention.dish_id, Review.city_id)
        .join(Review, Review.id == ReviewDishMention.review_id)
        .where(
            Review.status == ReviewStatus.PUBLISHED,
            *([Review.city_id == city_id] if city_id else []),
        )
        .group_by(ReviewDishMention.dish_id, Review.city_id)
        .having(func.count() >= config.min_observations * 2)
    ).all()

    for dish_id, dish_city in dish_pairs:
        observations = _load_dish_city_observations(session, dish_id, dish_city)
        trend = detect_trend(observations, config=config)
        _upsert_trend(
            session,
            TrendSubject.DISH,
            dish_id=dish_id,
            restaurant_id=None,
            city_id=dish_city,
            trend=trend,
            window_days=config.recent_days,
        )
        written += 1

    session.flush()
    log.info("trends_recomputed", count=written)
    return written


def _load_dish_city_observations(
    session: Session, dish_id: uuid.UUID, city_id: uuid.UUID
) -> list[Observation]:
    rows = session.execute(
        select(
            ReviewDishMention.sentiment,
            ReviewDishMention.confidence,
            Review.source,
            Review.engagement_score,
            Review.published_at,
        )
        .join(Review, Review.id == ReviewDishMention.review_id)
        .where(
            ReviewDishMention.dish_id == dish_id,
            Review.city_id == city_id,
            Review.status == ReviewStatus.PUBLISHED,
            Review.is_duplicate.is_(False),
        )
    ).all()
    return [
        Observation(
            sentiment=float(r.sentiment),
            confidence=float(r.confidence),
            source=r.source.value,
            engagement=int(r.engagement_score or 0),
            observed_at=r.published_at,
        )
        for r in rows
    ]


def _upsert_trend(
    session: Session,
    subject: TrendSubject,
    *,
    dish_id: uuid.UUID | None,
    restaurant_id: uuid.UUID | None,
    city_id: uuid.UUID,
    trend,
    window_days: int,
) -> None:
    row = session.execute(
        select(TrendMetric).where(
            TrendMetric.subject_type == subject,
            TrendMetric.dish_id == dish_id,
            TrendMetric.restaurant_id == restaurant_id,
            TrendMetric.city_id == city_id,
            TrendMetric.window_days == window_days,
        )
    ).scalar_one_or_none()

    if row is None:
        row = TrendMetric(
            subject_type=subject,
            dish_id=dish_id,
            restaurant_id=restaurant_id,
            city_id=city_id,
            window_days=window_days,
        )
        session.add(row)

    row.recent_sentiment = trend.recent_sentiment
    row.historical_sentiment = trend.historical_sentiment
    row.recent_count = trend.recent_count
    row.historical_count = trend.historical_count
    row.delta = trend.delta
    row.direction = trend.direction
    row.significant = trend.significant
    row.computed_at = datetime.now(UTC)
