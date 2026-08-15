"""Domain errors mapped to a single HTTP error envelope.

Services raise these; the API layer translates them. That keeps services free of
FastAPI/HTTP concerns so Celery tasks can call the same code.
"""

from __future__ import annotations

from typing import Any


class AppError(Exception):
    """Base class for all expected, translatable failures."""

    code: str = "internal_error"
    status_code: int = 500
    message: str = "Something went wrong"

    def __init__(
        self,
        message: str | None = None,
        *,
        details: dict[str, Any] | None = None,
    ) -> None:
        self.message = message or self.message
        self.details = details or {}
        super().__init__(self.message)

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.details:
            payload["details"] = self.details
        return {"error": payload}


class ValidationError(AppError):
    code = "validation_error"
    status_code = 422
    message = "Invalid request"


class NotFoundError(AppError):
    code = "not_found"
    status_code = 404
    message = "Resource not found"


class UnauthorizedError(AppError):
    code = "unauthorized"
    status_code = 401
    message = "Authentication required"


class ForbiddenError(AppError):
    code = "forbidden"
    status_code = 403
    message = "Not allowed"


class ConflictError(AppError):
    code = "conflict"
    status_code = 409
    message = "Conflicting state"


class DuplicateReviewError(ConflictError):
    code = "duplicate_review"
    message = "This review has already been submitted"


class RateLimitedError(AppError):
    code = "rate_limited"
    status_code = 429
    message = "Too many requests"


class InsufficientDataError(AppError):
    """Not an error condition in the product sense — the UI renders 'Not enough data'."""

    code = "insufficient_data"
    status_code = 200
    message = "Not enough data"


class UpstreamUnavailableError(AppError):
    code = "upstream_unavailable"
    status_code = 503
    message = "An upstream data source is unavailable"


class TransientSourceError(UpstreamUnavailableError):
    """Retryable ingestion failure (rate limit, timeout, 5xx). Celery retries on this."""

    def __init__(
        self,
        message: str | None = None,
        *,
        retry_after: float | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message, details=details)
        self.retry_after = retry_after


class PermanentSourceError(AppError):
    """Non-retryable ingestion failure (bad credentials, malformed contract)."""

    code = "source_error"
    status_code = 502
    message = "Data source rejected the request"
