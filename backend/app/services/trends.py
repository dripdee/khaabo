"""Trend detection: recent evidence vs historical baseline.

The hard rule is the gate: a direction is emitted only when *both* windows carry
enough observations. Anything thinner returns `direction=None` and the UI shows no
arrow at all — a trend claimed from two data points is worse than no trend.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from statistics import fmean

from app.core.config import settings
from app.models.enums import TrendDirection
from app.services.ranking import Observation


@dataclass(frozen=True, slots=True)
class TrendConfig:
    recent_days: int = 60
    historical_days: int = 240
    delta_threshold: float = 0.08
    min_observations: int = 3
    volume_surge_ratio: float = 3.0
    # A surge is a claim about attention, so it needs more than the bare minimum
    # sample. Without this floor, 3 recent vs 3 historical trips the ratio purely
    # because the windows have different lengths.
    volume_surge_min_recent: int = 10

    @classmethod
    def from_settings(cls) -> TrendConfig:
        return cls(
            recent_days=settings.trend_recent_days,
            historical_days=settings.trend_historical_days,
            delta_threshold=settings.trend_delta_threshold,
            min_observations=settings.trend_min_observations,
        )


@dataclass(slots=True)
class TrendResult:
    direction: TrendDirection | None
    delta: float | None
    recent_sentiment: float | None
    historical_sentiment: float | None
    recent_count: int
    historical_count: int
    significant: bool
    reason: str

    @property
    def has_trend(self) -> bool:
        return self.direction is not None


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def detect_trend(
    observations: list[Observation],
    *,
    config: TrendConfig | None = None,
    now: datetime | None = None,
) -> TrendResult:
    """Compare the recent window against the preceding historical window."""
    cfg = config or TrendConfig.from_settings()
    now = now or datetime.now(UTC)

    recent_cutoff = now - timedelta(days=cfg.recent_days)
    historical_cutoff = now - timedelta(days=cfg.historical_days)

    recent: list[Observation] = []
    historical: list[Observation] = []

    for obs in observations:
        if obs.observed_at is None:
            continue  # undated evidence cannot be placed in a window
        ts = _aware(obs.observed_at)
        if ts > now:
            continue
        if ts >= recent_cutoff:
            recent.append(obs)
        elif ts >= historical_cutoff:
            historical.append(obs)

    if len(recent) < cfg.min_observations or len(historical) < cfg.min_observations:
        return TrendResult(
            direction=None,
            delta=None,
            recent_sentiment=fmean([o.positivity for o in recent]) if recent else None,
            historical_sentiment=(
                fmean([o.positivity for o in historical]) if historical else None
            ),
            recent_count=len(recent),
            historical_count=len(historical),
            significant=False,
            reason="insufficient_data",
        )

    recent_p = fmean(o.positivity for o in recent)
    historical_p = fmean(o.positivity for o in historical)
    delta = recent_p - historical_p

    # Normalise volume to a per-day rate; the windows have different lengths, so
    # comparing raw counts would report a surge that does not exist.
    historical_window = max(1, cfg.historical_days - cfg.recent_days)
    recent_rate = len(recent) / max(1, cfg.recent_days)
    historical_rate = len(historical) / historical_window
    volume_ratio = recent_rate / historical_rate if historical_rate > 0 else 0.0

    if delta >= cfg.delta_threshold:
        direction = TrendDirection.RISING
        significant = True
        reason = "sentiment_up"
    elif delta <= -cfg.delta_threshold:
        direction = TrendDirection.DECLINING
        significant = True
        reason = "sentiment_down"
    elif volume_ratio >= cfg.volume_surge_ratio and len(recent) >= cfg.volume_surge_min_recent:
        # Attention is rising even though opinion is flat. Reported, but marked
        # not-significant so the UI can present it more weakly.
        direction = TrendDirection.RISING
        significant = False
        reason = "volume_surge"
    else:
        direction = TrendDirection.STABLE
        significant = False
        reason = "flat"

    return TrendResult(
        direction=direction,
        delta=round(delta, 4),
        recent_sentiment=round(recent_p, 4),
        historical_sentiment=round(historical_p, 4),
        recent_count=len(recent),
        historical_count=len(historical),
        significant=significant,
        reason=reason,
    )


def trend_label(direction: TrendDirection | None) -> str:
    return {
        TrendDirection.RISING: "Rising",
        TrendDirection.STABLE: "Stable",
        TrendDirection.DECLINING: "Declining",
    }.get(direction, "")  # type: ignore[arg-type]
