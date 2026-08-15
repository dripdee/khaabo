"""Sentry error monitoring lifecycle.

Init is opt-in via `SENTRY_DSN`. When unset (the default for local dev), every
function here is a no-op so the app boots identically with or without the SDK
installed. This keeps Sentry a soft dependency: a missing or broken SDK install
must never block startup.
"""

from __future__ import annotations

from app.core.config import settings

_sentry_available = False

try:
    import sentry_sdk
    from sentry_sdk.integrations.celery import CeleryIntegration
    from sentry_sdk.integrations.fastapi import FastApiIntegration
    from sentry_sdk.integrations.redis import RedisIntegration
    from sentry_sdk.integrations.sqlalchemy import SqlalchemyIntegration

    _sentry_available = True
except ImportError:
    sentry_sdk = None  # type: ignore[assignment]


def init_sentry() -> bool:
    """Initialize Sentry if and only if SENTRY_DSN is set and the SDK importable.

    Returns ``True`` when Sentry is actively capturing, ``False`` otherwise.
    Safe to call from both the FastAPI startup and the Celery worker.
    """
    if not settings.sentry_dsn:
        return False
    if not _sentry_available:
        return False

    sentry_sdk.init(
        dsn=settings.sentry_dsn,
        environment=settings.sentry_environment or settings.env,
        traces_sample_rate=settings.sentry_traces_sample_rate,
        send_default_pii=False,
        max_breadcrumbs=50,
        integrations=[
            FastApiIntegration(),
            CeleryIntegration(),
            RedisIntegration(),
            SqlalchemyIntegration(),
        ],
    )
    return True


def capture_exception(exc: BaseException, **kwargs: object) -> None:
    """Forward to ``sentry_sdk.capture_exception`` when active, else no-op."""
    if not _sentry_available or not settings.sentry_dsn:
        return
    sentry_sdk.capture_exception(exc, **kwargs)  # type: ignore[union-attr]


def set_tag(key: str, value: str) -> None:
    if not _sentry_available or not settings.sentry_dsn:
        return
    sentry_sdk.set_tag(key, value)  # type: ignore[union-attr]
