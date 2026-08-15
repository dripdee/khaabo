"""FastAPI dependencies: auth, RBAC, rate limiting, pagination.

Note: this module intentionally does **not** use `from __future__ import
annotations`. FastAPI resolves `Annotated[...]` parameter metadata at import time,
and stringified annotations break dependency classes like `Pagination`.
"""

from typing import Annotated

from fastapi import Depends, Header, Query, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.errors import ForbiddenError, UnauthorizedError
from app.core.logging import user_id_ctx
from app.core.rate_limit import check_rate_limit
from app.core.security import TokenClaims, require_role, verify_token
from app.db.session import get_db
from app.models import Profile, User
from app.models.enums import UserRole
from app.utils.text import slugify

DbSession = Annotated[AsyncSession, Depends(get_db)]


def _bearer(authorization: str | None) -> str | None:
    if not authorization:
        return None
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        return None
    return token.strip()


async def get_claims(
    authorization: Annotated[str | None, Header()] = None,
) -> TokenClaims | None:
    token = _bearer(authorization)
    if token is None:
        return None
    return await verify_token(token)


async def provision_user(session: AsyncSession, claims: TokenClaims) -> User:
    """JIT-provision the local row for a Supabase subject.

    `users.id` *is* the Supabase `sub`, so there is no mapping table and no window
    where a valid token has no local identity.
    """
    import uuid as _uuid

    try:
        user_id = _uuid.UUID(claims.subject)
    except ValueError as exc:
        raise UnauthorizedError("Token subject is not a valid UUID") from exc

    user = await session.get(User, user_id)
    if user is None:
        user = User(
            id=user_id,
            email=claims.email,
            # The token's role is only a hint at creation; the local column is
            # authoritative afterwards so a compromised token cannot self-promote.
            role=UserRole(claims.role)
            if claims.role in UserRole.__members__.values()
            else UserRole.USER,
        )
        session.add(user)
        await session.flush()

        base = slugify((claims.email or "foodie").split("@")[0]).replace("-", "_")[:30] or "foodie"
        username = base
        suffix = 1
        while (
            await session.execute(select(Profile.user_id).where(Profile.username == username))
        ).first():
            suffix += 1
            username = f"{base}{suffix}"[:40]

        session.add(
            Profile(
                user_id=user.id,
                username=username,
                display_name=(claims.email or "").split("@")[0] or None,
            )
        )
        await session.flush()
    elif claims.email and user.email != claims.email:
        user.email = claims.email

    return user


async def get_current_user(
    session: DbSession,
    claims: Annotated[TokenClaims | None, Depends(get_claims)] = None,
) -> User:
    if claims is None:
        raise UnauthorizedError()
    user = await provision_user(session, claims)
    if user.is_banned:
        raise ForbiddenError("This account has been suspended")
    user_id_ctx.set(str(user.id))
    return user


async def get_optional_user(
    session: DbSession,
    claims: Annotated[TokenClaims | None, Depends(get_claims)] = None,
) -> User | None:
    """For endpoints that personalize when signed in but work anonymously."""
    if claims is None:
        return None
    try:
        user = await provision_user(session, claims)
    except UnauthorizedError:
        return None
    if user.is_banned:
        return None
    user_id_ctx.set(str(user.id))
    return user


async def require_moderator(
    user: Annotated[User, Depends(get_current_user)],
) -> User:
    require_role(user.role.value, UserRole.MODERATOR.value)
    return user


async def require_admin(
    user: Annotated[User, Depends(get_current_user)],
) -> User:
    require_role(user.role.value, UserRole.ADMIN.value)
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]
OptionalUser = Annotated[User | None, Depends(get_optional_user)]
ModeratorUser = Annotated[User, Depends(require_moderator)]
AdminUser = Annotated[User, Depends(require_admin)]


def client_identity(request: Request, user: User | None) -> str:
    """Prefer the user id; fall back to the forwarded client IP."""
    if user is not None:
        return f"user:{user.id}"
    forwarded = request.headers.get("x-forwarded-for", "")
    ip = (
        forwarded.split(",")[0].strip()
        if forwarded
        else (request.client.host if request.client else "unknown")
    )
    return f"ip:{ip}"


async def rate_limit_read(
    request: Request,
    user: OptionalUser = None,
) -> None:
    await check_rate_limit(
        client_identity(request, user),
        "read",
        settings.rate_limit_read_per_minute,
        60,
    )


async def rate_limit_write(
    request: Request,
    user: CurrentUser,
) -> None:
    await check_rate_limit(
        client_identity(request, user),
        "write",
        settings.rate_limit_write_per_minute,
        60,
    )


class Pagination:
    def __init__(
        self,
        page: Annotated[int, Query(ge=1, le=200)] = 1,
        page_size: Annotated[int, Query(ge=1, le=100)] = 20,
    ) -> None:
        self.page = page
        self.page_size = page_size

    @property
    def offset(self) -> int:
        return (self.page - 1) * self.page_size


PaginationDep = Annotated[Pagination, Depends(Pagination)]
