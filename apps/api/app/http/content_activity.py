"""Admin-only, secret-safe XHS Content Activity command entrypoints."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Awaitable
from typing import Annotated, Any

from backend_core.auth import AuthContext
from backend_core.config import get_settings
from backend_core.content_activity.schemas import (
    XhsIdentityResolutionInput,
    XhsIdentityResolutionPublic,
    XhsRefreshCreateInput,
    XhsRefreshCreateResult,
)
from backend_core.content_activity.service import ContentActivityError, ContentActivityService
from fastapi import APIRouter, Depends, Header, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.http.content_activity_tasks import ContentActivityTaskDispatcher
from app.http.dependencies import (
    get_client_ip,
    get_content_activity_task_dispatcher,
    get_database_session,
    get_user_agent,
    require_super_admin,
)
from app.http.errors import ApiError
from app.http.phase3a_http import error_responses, require_single_idempotency_key
from app.http.responses import SuccessEnvelope, envelope

router = APIRouter(prefix="/api/v1/admin/content-activity", tags=["content-activity"])
settings = get_settings()
logger = logging.getLogger(__name__)


async def get_content_activity_service(
    session: Annotated[AsyncSession, Depends(get_database_session)],
) -> AsyncIterator[ContentActivityService]:
    """Yield a service and close only its local provider transport afterwards."""

    service = ContentActivityService(session, settings)
    try:
        yield service
    finally:
        await service.aclose()


async def _service_call[ResultT](awaitable: Awaitable[ResultT]) -> ResultT:
    try:
        return await awaitable
    except ContentActivityError as exc:
        raise ApiError(exc.status_code, exc.code, exc.message) from exc


def _log_dispatch_failure(request_token: str) -> None:
    """Keep deferred recovery diagnostics free of bootstrap/provider data."""

    logger.warning(
        "content_activity_immediate_dispatch_failed",
        extra={"content_activity_refresh_request_token": request_token},
    )


@router.post(
    "/xiaohongshu/identities/resolve",
    status_code=status.HTTP_201_CREATED,
    response_model=SuccessEnvelope[XhsIdentityResolutionPublic],
    responses=error_responses(401, 403, 404, 409, 422),
)
async def resolve_xiaohongshu_identity(
    payload: XhsIdentityResolutionInput,
    request: Request,
    context: Annotated[AuthContext, Depends(require_super_admin)],
    service: Annotated[ContentActivityService, Depends(get_content_activity_service)],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> dict[str, Any]:
    """Resolve one ephemeral bootstrap input; no raw input enters persistence."""

    result = await _service_call(
        service.resolve_xhs_identity(
            platform_account_id=payload.platform_account_id,
            bootstrap_input=payload.bootstrap_input.get_secret_value(),
            idempotency_key=require_single_idempotency_key(request, idempotency_key),
            operator_id=context.operator.id if context.operator is not None else None,
            department_id=context.department.id,
            ip=get_client_ip(request),
            user_agent=get_user_agent(request),
        )
    )
    return envelope(request, data=result)


@router.post(
    "/xiaohongshu/refreshes",
    status_code=status.HTTP_201_CREATED,
    response_model=SuccessEnvelope[XhsRefreshCreateResult],
    responses=error_responses(401, 403, 404, 409, 422),
)
async def create_xiaohongshu_refreshes(
    payload: XhsRefreshCreateInput,
    request: Request,
    context: Annotated[AuthContext, Depends(require_super_admin)],
    service: Annotated[ContentActivityService, Depends(get_content_activity_service)],
    dispatcher: Annotated[
        ContentActivityTaskDispatcher,
        Depends(get_content_activity_task_dispatcher),
    ],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> dict[str, Any]:
    """Persist native requests before publishing only their opaque UUID tokens."""

    refreshes = await _service_call(
        service.create_xhs_refresh_requests(
            platform_account_ids=payload.platform_account_ids,
            idempotency_key=require_single_idempotency_key(request, idempotency_key),
            operator_id=context.operator.id if context.operator is not None else None,
            department_id=context.department.id,
            ip=get_client_ip(request),
            user_agent=get_user_agent(request),
        )
    )
    # The durable PENDING record is already committed.  A transient broker failure
    # cannot erase it; the worker reconciliation sweep safely republishes it.
    for refresh in refreshes:
        try:
            await dispatcher.refresh(refresh.request_token)
        except Exception:
            _log_dispatch_failure(str(refresh.request_token))
    return envelope(request, data=XhsRefreshCreateResult(requests=refreshes))


__all__ = ["get_content_activity_service", "router"]
