"""HTTP dependency injection for shared backend_core services."""

import hmac
from collections.abc import AsyncIterator
from typing import Annotated, cast

from backend_core.auth import AuthContext, AuthError, AuthService, Role
from backend_core.auth.throttle import RedisLoginThrottle
from backend_core.config import get_settings
from fastapi import Depends, Header, Request
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

settings = get_settings()


async def get_database_session(request: Request) -> AsyncIterator[AsyncSession]:
    async with request.app.state.database.session_factory() as session:
        yield session


def get_redis(request: Request) -> Redis:
    return cast(Redis, request.app.state.redis)


def get_client_ip(request: Request) -> str:
    forwarded_for = request.headers.get("X-Forwarded-For")
    if forwarded_for:
        return forwarded_for.split(",", maxsplit=1)[0].strip()[:64]
    if request.client is None:
        return "unknown"
    return request.client.host[:64]


def get_user_agent(request: Request) -> str:
    return request.headers.get("User-Agent", "unknown")[:512]


def get_auth_service(
    session: Annotated[AsyncSession, Depends(get_database_session)],
    redis: Annotated[Redis, Depends(get_redis)],
) -> AuthService:
    throttle = RedisLoginThrottle(
        redis,
        max_failures=settings.login_max_failures,
        lock_seconds=settings.login_lock_seconds,
    )
    return AuthService(
        session,
        throttle,
        default_hours=settings.session_default_hours,
        remember_days=settings.session_remember_days,
    )


async def require_auth(
    request: Request,
    service: Annotated[AuthService, Depends(get_auth_service)],
) -> AuthContext:
    return await service.authenticate(request.cookies.get(settings.session_cookie_name))


async def require_csrf_context(
    request: Request,
    context: Annotated[AuthContext, Depends(require_auth)],
    service: Annotated[AuthService, Depends(get_auth_service)],
    csrf_header: Annotated[str | None, Header(alias="X-CSRF-Token")] = None,
) -> AuthContext:
    csrf_cookie = request.cookies.get(settings.csrf_cookie_name)
    if (
        csrf_header is None
        or csrf_cookie is None
        or not hmac.compare_digest(csrf_header, csrf_cookie)
    ):
        raise AuthError(403, "CSRF_FAILED", "CSRF validation failed")
    service.validate_csrf(context, csrf_header)
    return context


async def require_super_admin(
    context: Annotated[AuthContext, Depends(require_csrf_context)],
) -> AuthContext:
    if context.operator is None:
        raise AuthError(409, "OPERATOR_REQUIRED", "Select an operator first")
    if context.role != Role.SUPER_ADMIN:
        raise AuthError(403, "PERMISSION_DENIED", "Super admin permission required")
    return context
