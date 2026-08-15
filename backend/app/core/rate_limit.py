"""Redis fixed-window rate limiting.

Fails **open** on Redis errors: a cache outage must not lock users out. Every
open-fail is logged so it is visible rather than silent.
"""

from __future__ import annotations

from app.core.cache import get_redis
from app.core.config import settings
from app.core.errors import RateLimitedError
from app.core.logging import get_logger

log = get_logger(__name__)


async def check_rate_limit(
    identity: str,
    bucket: str,
    limit: int,
    window_seconds: int,
    *,
    raise_on_exceed: bool = True,
) -> tuple[bool, int]:
    """Increment the counter for (identity, bucket). Returns (allowed, remaining)."""
    if not settings.rate_limit_enabled or limit <= 0:
        return True, limit

    client = get_redis()
    if client is None:
        return True, limit

    key = f"rl:{bucket}:{identity}"
    try:
        pipe = client.pipeline()
        pipe.incr(key)
        pipe.expire(key, window_seconds, nx=True)
        count = (await pipe.execute())[0]
    except Exception as exc:
        log.warning("rate_limit_unavailable", bucket=bucket, error=str(exc))
        return True, limit

    remaining = max(0, limit - int(count))
    if int(count) > limit:
        log.info("rate_limited", bucket=bucket, identity=identity, count=int(count))
        if raise_on_exceed:
            raise RateLimitedError(
                "Too many requests. Please slow down.",
                details={"retry_after": window_seconds, "limit": limit},
            )
        return False, 0
    return True, remaining


async def check_review_limits(user_id: str) -> None:
    """Two-tier limit so a burst is blocked and a slow grind is also capped."""
    await check_rate_limit(user_id, "reviews_hour", settings.rate_limit_reviews_per_hour, 3600)
    await check_rate_limit(user_id, "reviews_day", settings.rate_limit_reviews_per_day, 86400)
