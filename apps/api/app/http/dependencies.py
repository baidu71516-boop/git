"""HTTP dependency injection for shared backend_core services."""

import hmac
from collections.abc import AsyncIterator
from typing import Annotated, cast

from backend_core.auth import AuthContext, AuthError, AuthService, Role
from backend_core.auth.throttle import RedisLoginThrottle
from backend_core.config import get_settings
from backend_core.imports.errors import ImportDomainError
from backend_core.imports.parsers import ParserLimits
from backend_core.imports.service import ImportService
from backend_core.imports.storage import StorageAdapter
from fastapi import Depends, Header, Request
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.http.import_tasks import ImportTaskDispatcher

settings = get_settings()


async def get_database_session(request: Request) -> AsyncIterator[AsyncSession]:
    async with request.app.state.database.session_factory() as session:
        yield session


def get_redis(request: Request) -> Redis:
    return cast(Redis, request.app.state.redis)


def get_import_storage(request: Request) -> StorageAdapter:
    return cast(StorageAdapter, request.app.state.import_storage)


def get_import_task_dispatcher(request: Request) -> ImportTaskDispatcher:
    return cast(ImportTaskDispatcher, request.app.state.import_task_dispatcher)


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


def get_import_service(
    session: Annotated[AsyncSession, Depends(get_database_session)],
    storage: Annotated[StorageAdapter, Depends(get_import_storage)],
) -> ImportService:
    return ImportService(
        session,
        storage,
        parser_limits=ParserLimits(
            max_xlsx_uncompressed_bytes=settings.import_max_xlsx_uncompressed_bytes,
            max_xlsx_entries=settings.import_max_xlsx_entries,
            max_xlsx_compression_ratio=settings.import_max_xlsx_compression_ratio,
            max_rows=settings.import_max_rows,
            max_columns=settings.import_max_columns,
            max_cells=settings.import_max_cells,
            max_cell_chars=settings.import_max_cell_chars,
            max_warnings=settings.import_max_warnings,
        ),
        max_file_bytes=settings.import_max_file_bytes,
        retention_days=settings.import_retention_days,
        max_batch_files=settings.import_max_batch_files,
        max_batch_bytes=settings.import_max_batch_bytes,
        source_acquired_clock_skew_seconds=(settings.import_source_acquired_clock_skew_seconds),
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


async def require_import_mutation(
    context: Annotated[AuthContext, Depends(require_csrf_context)],
) -> AuthContext:
    if context.operator is None:
        raise ImportDomainError("OPERATOR_REQUIRED", "Select an operator first", status_code=409)
    if context.role == Role.VIEWER:
        raise ImportDomainError("PERMISSION_DENIED", "Viewer role is read-only", status_code=403)
    return context
