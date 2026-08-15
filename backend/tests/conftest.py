"""Shared test fixtures.

Tests are split in two tiers:

* **unit** (default) — pure functions plus app wiring; no services required.
* **db** (`@pytest.mark.db`) — needs a live PostgreSQL + PostGIS. Skipped
  automatically when unreachable, so `pytest` is green on a clean checkout while the
  same suite runs fully in CI/Docker where the database exists.

PostGIS has no SQLite equivalent, so substituting a fake database for the geo paths
would test a different system than the one that ships.
"""

from __future__ import annotations

import asyncio
import os
import sys
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

os.environ.setdefault("AUTH_DEV_BYPASS", "true")
os.environ.setdefault("CACHE_ENABLED", "false")
os.environ.setdefault("RATE_LIMIT_ENABLED", "false")

if sys.platform == "win32":
    # psycopg's async mode cannot run on ProactorEventLoop, which is the Windows
    # default. Uvicorn/Docker deployments use the selector loop already; this makes
    # local Windows test runs match.
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


def _database_url() -> str:
    from app.core.config import settings

    return os.environ.get("TEST_DATABASE_URL", settings.database_url)


_db_probe: bool | None = None


async def _database_available() -> bool:
    """Probe once per session and memoize.

    Deliberately a plain coroutine with module-level caching rather than a
    session-scoped async fixture: a session-scoped async fixture would need a
    session-scoped event loop, which conflicts with the function-scoped loop the
    async tests use.
    """
    global _db_probe
    if _db_probe is not None:
        return _db_probe

    engine = create_async_engine(_database_url(), pool_pre_ping=True)
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT postgis_version()"))
        _db_probe = True
    except Exception:
        _db_probe = False
    finally:
        await engine.dispose()
    return _db_probe


@pytest_asyncio.fixture
async def db_engine():
    if not await _database_available():
        pytest.skip("PostgreSQL+PostGIS is not reachable; run `docker compose up -d db`")

    from app.models import Base

    engine = create_async_engine(_database_url(), pool_pre_ping=True)
    async with engine.begin() as conn:
        for extension in ("postgis", "pgcrypto", "pg_trgm", "unaccent"):
            await conn.execute(text(f'CREATE EXTENSION IF NOT EXISTS "{extension}"'))
        await conn.run_sync(Base.metadata.create_all)

    yield engine

    # Truncate rather than drop: keeps extensions and enums intact between tests and
    # is dramatically faster than recreating the schema each time.
    async with engine.begin() as conn:
        tables = ", ".join(f'"{table.name}"' for table in reversed(Base.metadata.sorted_tables))
        await conn.execute(text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))
    await engine.dispose()


@pytest_asyncio.fixture
async def session(db_engine) -> AsyncIterator[AsyncSession]:
    maker = async_sessionmaker(bind=db_engine, class_=AsyncSession, expire_on_commit=False)
    async with maker() as session:
        yield session
        await session.rollback()


@pytest_asyncio.fixture
async def client() -> AsyncIterator[AsyncClient]:
    """ASGI client that does not require a running server."""
    from app.main import app

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as async_client:
        yield async_client


@pytest.fixture
def auth_headers() -> dict[str, str]:
    """Development bypass token (`AUTH_DEV_BYPASS`), never valid in production."""
    return {"Authorization": "Bearer dev:tester@example.com"}


@pytest.fixture
def admin_headers() -> dict[str, str]:
    return {"Authorization": "Bearer dev:admin@example.com:admin"}
