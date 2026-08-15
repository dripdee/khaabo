"""Redis read-through cache.

Design rule: the cache must never be load-bearing. If Redis is missing or
misbehaving, every helper degrades to a direct call so the API keeps serving.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

import redis.asyncio as aioredis

from app.core.config import settings
from app.core.logging import get_logger

log = get_logger(__name__)
T = TypeVar("T")

_client: aioredis.Redis | None = None
_unavailable = False


def get_redis() -> aioredis.Redis | None:
    global _client, _unavailable
    if _unavailable or not settings.cache_enabled:
        return None
    if _client is None:
        try:
            _client = aioredis.from_url(
                settings.redis_url,
                encoding="utf-8",
                decode_responses=True,
                socket_connect_timeout=1.5,
                socket_timeout=1.5,
            )
        except Exception as exc:  # pragma: no cover - connection construction
            log.warning("redis_init_failed", error=str(exc))
            _unavailable = True
            return None
    return _client


async def close_redis() -> None:
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None


def cache_key(prefix: str, **parts: Any) -> str:
    """Stable key from arbitrary params (sorted, hashed when long)."""
    if not parts:
        return prefix
    raw = json.dumps(parts, sort_keys=True, default=str)
    if len(raw) <= 96:
        return f"{prefix}:{raw}"
    return f"{prefix}:{hashlib.sha256(raw.encode()).hexdigest()[:24]}"


async def cache_get(key: str) -> Any | None:
    client = get_redis()
    if client is None:
        return None
    try:
        raw = await client.get(key)
    except Exception as exc:
        log.warning("cache_get_failed", key=key, error=str(exc))
        return None
    if raw is None:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None


async def cache_set(key: str, value: Any, ttl: int) -> None:
    client = get_redis()
    if client is None:
        return
    try:
        await client.set(key, json.dumps(value, default=str), ex=ttl)
    except Exception as exc:
        log.warning("cache_set_failed", key=key, error=str(exc))


async def cache_delete_prefix(prefix: str) -> int:
    """Targeted invalidation after a ranking recompute. SCAN, never KEYS."""
    client = get_redis()
    if client is None:
        return 0
    removed = 0
    try:
        async for key in client.scan_iter(match=f"{prefix}*", count=200):
            removed += await client.delete(key)
    except Exception as exc:
        log.warning("cache_invalidate_failed", prefix=prefix, error=str(exc))
    return removed


async def cached(
    key: str,
    ttl: int,
    producer: Callable[[], Awaitable[T]],
) -> T:
    hit = await cache_get(key)
    if hit is not None:
        return hit  # type: ignore[return-value]
    value = await producer()
    await cache_set(key, value, ttl)
    return value
