"""HTTP dependency injection for shared backend_core services."""

import hmac
from collections.abc import AsyncIterator, Callable
from typing import Annotated, Any, cast

from backend_core.auth import (
    AuthContext,
    AuthError,
    AuthService,
    EffectiveAuthorizationContext,
    ModuleRequirement,
    require_module_mutation,
    require_module_read,
)
from backend_core.auth.throttle import RedisLoginThrottle
from backend_core.config import get_settings
from backend_core.imports.parsers import ParserLimits
from backend_core.imports.service import ImportService
from backend_core.imports.storage import StorageAdapter
from fastapi import Depends, Header, Request
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.http.content_activity_tasks import ContentActivityTaskDispatcher
from app.http.import_tasks import ImportTaskDispatcher
from app.http.targeting_tasks import TargetingTaskDispatcher

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


def get_targeting_task_dispatcher(request: Request) -> TargetingTaskDispatcher:
    return cast(TargetingTaskDispatcher, request.app.state.targeting_task_dispatcher)


def get_content_activity_task_dispatcher(request: Request) -> ContentActivityTaskDispatcher:
    return cast(ContentActivityTaskDispatcher, request.app.state.content_activity_task_dispatcher)


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
        max_batch_rows=settings.import_max_batch_rows,
        source_acquired_clock_skew_seconds=(settings.import_source_acquired_clock_skew_seconds),
        task_settings=settings,
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


async def require_effective_authorization(
    context: Annotated[AuthContext, Depends(require_auth)],
    service: Annotated[AuthService, Depends(get_auth_service)],
) -> EffectiveAuthorizationContext:
    """Resolve fresh selected-Operator authority for one Human business request."""

    return await service.resolve_effective_authorization(context)


def _mark_module_guard[
    GuardT: Callable[..., Any],
](
    guard: GuardT,
    *,
    requirement: ModuleRequirement,
    write: bool,
) -> GuardT:
    """Expose one inspectable route declaration without widening runtime input."""

    setattr(guard, "permissions_v1_requirement", requirement)  # noqa: B010
    setattr(guard, "permissions_v1_write", write)  # noqa: B010
    return guard


def require_module(requirement: ModuleRequirement) -> Callable[..., Any]:
    """Build the centralized read guard for one frozen Human route requirement."""

    async def guard(
        authorization: Annotated[
            EffectiveAuthorizationContext,
            Depends(require_effective_authorization),
        ],
    ) -> EffectiveAuthorizationContext:
        return require_module_read(authorization, requirement)

    return _mark_module_guard(guard, requirement=requirement, write=False)


def require_module_write(requirement: ModuleRequirement) -> Callable[..., Any]:
    """Build the centralized CSRF-protected write guard for one Human route."""

    async def guard(
        request: Request,
        context: Annotated[AuthContext, Depends(require_auth)],
        service: Annotated[AuthService, Depends(get_auth_service)],
        authorization: Annotated[
            EffectiveAuthorizationContext,
            Depends(require_effective_authorization),
        ],
        csrf_header: Annotated[str | None, Header(alias="X-CSRF-Token")] = None,
    ) -> EffectiveAuthorizationContext:
        authorized = require_module_mutation(authorization, requirement)
        csrf_cookie = request.cookies.get(settings.csrf_cookie_name)
        if (
            csrf_header is None
            or csrf_cookie is None
            or not hmac.compare_digest(csrf_header, csrf_cookie)
        ):
            raise AuthError(403, "CSRF_FAILED", "CSRF validation failed")
        service.validate_csrf(context, csrf_header)
        return authorized

    return _mark_module_guard(guard, requirement=requirement, write=True)
