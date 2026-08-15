"""Supabase JWT verification + RBAC.

The backend never mints tokens. It verifies Supabase access tokens (HS256 shared
secret or RS256 via JWKS) and JIT-provisions a local `users` row keyed by the
Supabase `sub`, so no mapping table is needed.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

import httpx
from jose import jwt
from jose.exceptions import JWTError

from app.core.config import settings
from app.core.errors import ForbiddenError, UnauthorizedError
from app.core.logging import get_logger

log = get_logger(__name__)

ROLE_ORDER = {"user": 0, "moderator": 1, "admin": 2}

_jwks_cache: dict[str, Any] | None = None
_jwks_fetched_at: float = 0.0
_JWKS_TTL = 3600.0


@dataclass(frozen=True, slots=True)
class TokenClaims:
    subject: str
    email: str | None
    role: str = "user"
    raw: dict[str, Any] | None = None


async def _get_jwks() -> dict[str, Any]:
    global _jwks_cache, _jwks_fetched_at
    now = time.time()
    if _jwks_cache is not None and now - _jwks_fetched_at < _JWKS_TTL:
        return _jwks_cache
    url = settings.supabase_jwks_url
    async with httpx.AsyncClient(timeout=5.0) as client:
        resp = await client.get(url)
    if resp.status_code != 200:
        raise UnauthorizedError("Unable to verify token signing keys")
    _jwks_cache = resp.json()
    _jwks_fetched_at = now
    return _jwks_cache


async def verify_token(token: str) -> TokenClaims:
    """Verify a bearer token and return its claims.

    Development escape hatch: when AUTH_DEV_BYPASS is on (never in production, the
    config validator enforces this), `dev:email[:role]` is accepted so the stack is
    usable without Supabase credentials.
    """
    if settings.auth_dev_bypass and token.startswith("dev:"):
        parts = token.split(":")
        email = parts[1] if len(parts) > 1 and parts[1] else "dev@example.com"
        role = parts[2] if len(parts) > 2 and parts[2] in ROLE_ORDER else "user"
        return TokenClaims(subject=_stable_dev_uuid(email), email=email, role=role)

    if settings.supabase_jwks_url:
        jwks = await _get_jwks()
        try:
            payload = jwt.decode(
                token,
                jwks,
                algorithms=["RS256", "ES256"],
                audience=settings.supabase_jwt_audience,
                options={"verify_aud": bool(settings.supabase_jwt_audience)},
            )
        except JWTError as exc:
            raise UnauthorizedError("Invalid or expired token") from exc
    elif settings.supabase_jwt_secret:
        try:
            payload = jwt.decode(
                token,
                settings.supabase_jwt_secret,
                algorithms=["HS256"],
                audience=settings.supabase_jwt_audience,
                options={"verify_aud": bool(settings.supabase_jwt_audience)},
            )
        except JWTError as exc:
            raise UnauthorizedError("Invalid or expired token") from exc
    else:
        raise UnauthorizedError("Authentication is not configured on this server")

    sub = payload.get("sub")
    if not sub:
        raise UnauthorizedError("Token is missing a subject")

    # Supabase puts app metadata role under app_metadata; local role still wins,
    # this is only a hint used at provisioning time.
    meta = payload.get("app_metadata") or {}
    role = meta.get("role") if meta.get("role") in ROLE_ORDER else "user"

    return TokenClaims(subject=str(sub), email=payload.get("email"), role=role, raw=payload)


def _stable_dev_uuid(email: str) -> str:
    import hashlib
    import uuid

    digest = hashlib.sha256(email.encode()).digest()[:16]
    return str(uuid.UUID(bytes=digest, version=5))


def require_role(user_role: str, minimum: str) -> None:
    if ROLE_ORDER.get(user_role, -1) < ROLE_ORDER[minimum]:
        raise ForbiddenError(f"This action requires the '{minimum}' role")
