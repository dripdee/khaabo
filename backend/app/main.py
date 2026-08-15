"""Application entrypoint.

Cross-cutting concerns live in middleware and exception handlers so route handlers
stay thin: they parse, delegate to a service and return a schema.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import final

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import ValidationError as PydanticValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.gzip import GZipMiddleware

from app.api.v1.router import api_router
from app.api.v1.search import router as search_router
from app.core.cache import close_redis
from app.core.config import settings
from app.core.errors import AppError
from app.core.logging import configure_logging, get_logger, request_id_ctx
from app.core.sentry import capture_exception as sentry_capture
from app.db.session import dispose_engines

configure_logging()
log = get_logger(__name__)

MAX_BODY_BYTES = 1_048_576  # 1 MB

# ── Prometheus metrics ──────────────────────────────────────────────────────
try:
    from prometheus_client import Counter, Histogram

    HTTP_REQUESTS = Counter(
        "http_requests_total",
        "HTTP request count",
        labelnames=["method", "status", "path_group"],
    )
    HTTP_LATENCY = Histogram(
        "http_request_duration_seconds",
        "HTTP request latency",
        labelnames=["method", "path_group"],
        buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10),
    )
except ImportError:

    @final
    class _NoopMetric:
        def labels(self, *args, **kwargs):
            return self  # noqa: E731

        def inc(self, *args, **kwargs):
            pass

        def observe(self, *args, **kwargs):
            pass

    HTTP_REQUESTS = _NoopMetric()
    HTTP_LATENCY = _NoopMetric()


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    from app.ai import get_provider
    from app.core.sentry import init_sentry as init_sentry_runtime
    from app.services.ranking import RankingWeights

    # Fail fast on a mis-summed weight vector rather than serving skewed rankings.
    RankingWeights.from_settings().validate()

    init_sentry_runtime()

    provider = get_provider()
    log.info(
        "startup",
        env=settings.env,
        ai_provider=provider.name,
        sources=settings.enabled_sources,
        default_city=settings.default_city_slug,
    )
    yield
    await close_redis()
    await dispose_engines()
    log.info("shutdown")


app = FastAPI(
    title=f"{settings.project_name} API",
    description=(
        "Dish-first food discovery. Rankings are computed from stored evidence and "
        "every score carries a structured explanation. Place data © OpenStreetMap "
        "contributors."
    ),
    version="0.1.0",
    openapi_url=f"{settings.api_prefix}/openapi.json",
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    # Explicit allowlist. Wildcard + credentials is rejected by the config validator.
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
    max_age=600,
)
app.add_middleware(GZipMiddleware, minimum_size=1000)


def _path_group(path: str) -> str:
    """Bucket dynamic path segments so label cardinality stays bounded.

    `/dish/chicken-biryani` → `/dish/:slug` keeps a single counter per route
    family rather than one per dish. Metric cardinality cap matters because
    Prometheus memory grows with the number of distinct label values.
    """
    parts = path.strip("/").split("/")
    if len(parts) <= 1:
        return path
    top, *_ = parts
    if top in {"dish", "restaurant"}:
        return f"/{top}/:slug"
    if top == "search":
        return "/search"
    return path


@app.middleware("http")
async def request_context(request: Request, call_next):
    """Correlation id, timing, Prometheus metrics and a hard body-size limit."""
    request_id = request.headers.get("x-request-id") or uuid.uuid4().hex[:16]
    request_id_ctx.set(request_id)

    content_length = request.headers.get("content-length")
    if content_length and content_length.isdigit() and int(content_length) > MAX_BODY_BYTES:
        return JSONResponse(
            status_code=413,
            content={
                "error": {
                    "code": "payload_too_large",
                    "message": "Request body exceeds 1 MB",
                }
            },
        )

    started = time.perf_counter()
    try:
        response = await call_next(request)
    except Exception as exc:
        log.exception("unhandled_exception", path=request.url.path, method=request.method)
        sentry_capture(exc)
        raise

    duration_ms = int((time.perf_counter() - started) * 1000)
    response.headers["x-request-id"] = request_id

    group = _path_group(request.url.path)
    HTTP_REQUESTS.labels(
        method=request.method,
        status=str(response.status_code),
        path_group=group,
    ).inc()
    HTTP_LATENCY.labels(method=request.method, path_group=group).observe(
        time.perf_counter() - started
    )

    if request.url.path not in {
        "/docs",
        "/redoc",
        f"{settings.api_prefix}/openapi.json",
        f"{settings.api_prefix}/metrics",
    }:
        log.info(
            "request",
            method=request.method,
            path=request.url.path,
            status=response.status_code,
            latency_ms=duration_ms,
        )
    return response


@app.exception_handler(AppError)
async def app_error_handler(_: Request, exc: AppError) -> JSONResponse:
    """Domain errors → the single error envelope documented in docs/api.md."""
    return JSONResponse(status_code=exc.status_code, content=exc.to_dict())


@app.exception_handler(RequestValidationError)
async def validation_handler(_: Request, exc: RequestValidationError) -> JSONResponse:
    return JSONResponse(
        status_code=422,
        content={
            "error": {
                "code": "validation_error",
                "message": "Request validation failed",
                "details": {"errors": _safe_errors(exc.errors())},
            }
        },
    )


@app.exception_handler(PydanticValidationError)
async def pydantic_validation_handler(_: Request, exc: PydanticValidationError) -> JSONResponse:
    """Model-level validation raised outside request parsing.

    Cross-field rules on dependency models (e.g. `sort=distance` requires lat/lng)
    raise here rather than as a `RequestValidationError`, and without this handler
    they would surface as a 500 instead of the documented 422.
    """
    return JSONResponse(
        status_code=422,
        content={
            "error": {
                "code": "validation_error",
                "message": "Request validation failed",
                "details": {"errors": _safe_errors(exc.errors())},
            }
        },
    )


def _safe_errors(errors: list[dict]) -> list[dict]:
    """Pydantic error dicts can carry non-serializable `ctx` values."""
    cleaned: list[dict] = []
    for error in errors[:10]:
        cleaned.append(
            {
                "loc": [str(part) for part in error.get("loc", ())],
                "msg": str(error.get("msg", "")),
                "type": str(error.get("type", "")),
            }
        )
    return cleaned


@app.exception_handler(StarletteHTTPException)
async def http_error_handler(_: Request, exc: StarletteHTTPException) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "error": {
                "code": "http_error",
                "message": str(exc.detail),
            }
        },
    )


@app.exception_handler(Exception)
async def unhandled_handler(_: Request, exc: Exception) -> JSONResponse:
    """Never leak internals in production; the detail is in the structured log."""
    return JSONResponse(
        status_code=500,
        content={
            "error": {
                "code": "internal_error",
                "message": (str(exc) if settings.debug else "An unexpected error occurred"),
            }
        },
    )


app.include_router(search_router, prefix=settings.api_prefix)
app.include_router(api_router, prefix=settings.api_prefix)


@app.get("/", include_in_schema=False)
async def root() -> dict:
    return {
        "name": settings.project_name,
        "version": "0.1.0",
        "docs": "/docs",
        "api": settings.api_prefix,
        "attribution": ["© OpenStreetMap contributors"],
    }
