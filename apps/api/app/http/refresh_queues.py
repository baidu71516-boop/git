"""Thin HTTP adaptation for deterministic Department-owned Refresh Queues."""

from collections.abc import Awaitable
from typing import Annotated, Any
from uuid import UUID

from backend_core.auth.service import AuthContext
from backend_core.config import get_settings
from backend_core.influencers.freshness import FreshnessPolicy
from backend_core.refresh.schemas import (
    RefreshQueueCreateInput,
    RefreshQueueDetailResponse,
    RefreshQueueItemListQuery,
    RefreshQueueItemPage,
    RefreshQueueListPage,
    RefreshQueueListQuery,
)
from backend_core.refresh.service import RefreshQueueError, RefreshQueueService
from fastapi import APIRouter, Depends, Query, Request, Response, status
from fastapi.exceptions import RequestValidationError
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.http.dependencies import (
    get_client_ip,
    get_database_session,
    get_user_agent,
    require_auth,
    require_refresh_mutation,
)
from app.http.errors import ApiError
from app.http.responses import ErrorEnvelope, SuccessEnvelope, envelope

router = APIRouter(prefix="/api/v1/refresh-queues", tags=["refresh-queues"])
settings = get_settings()

PAGINATION_QUERY_PARAMETERS = frozenset({"offset", "limit"})


def _error_responses(*status_codes: int) -> dict[int | str, dict[str, Any]]:
    return {
        status_code: {"model": ErrorEnvelope, "description": "Error response"}
        for status_code in status_codes
    }


def get_refresh_queue_service(
    session: Annotated[AsyncSession, Depends(get_database_session)],
) -> RefreshQueueService:
    return RefreshQueueService(
        session,
        freshness_policy=FreshnessPolicy.from_day_thresholds(
            settings.freshness_fresh_days,
            settings.freshness_aging_days,
            settings.freshness_stale_days,
        ),
    )


def reject_invalid_pagination_parameters(request: Request) -> None:
    unexpected = sorted(set(request.query_params) - PAGINATION_QUERY_PARAMETERS)
    if unexpected:
        name = unexpected[0]
        raise RequestValidationError(
            [
                {
                    "type": "extra_forbidden",
                    "loc": ("query", name),
                    "msg": "Extra inputs are not permitted",
                    "input": request.query_params.get(name),
                }
            ]
        )
    for name in PAGINATION_QUERY_PARAMETERS:
        if len(request.query_params.getlist(name)) > 1:
            raise RequestValidationError(
                [
                    {
                        "type": "value_error",
                        "loc": ("query", name),
                        "msg": "Value error, query parameter must not be repeated",
                        "input": None,
                        "ctx": {"error": ValueError("query parameter must not be repeated")},
                    }
                ]
            )


def _strict_query_integers(request: Request) -> dict[str, int]:
    values: dict[str, int] = {}
    for name in PAGINATION_QUERY_PARAMETERS:
        if name not in request.query_params:
            continue
        candidate = request.query_params[name].strip()
        unsigned = candidate[1:] if candidate[:1] in {"+", "-"} else candidate
        if not unsigned or not unsigned.isascii() or not unsigned.isdigit():
            raise RequestValidationError(
                [
                    {
                        "type": "int_parsing",
                        "loc": ("query", name),
                        "msg": "Input should be a valid integer",
                        "input": None,
                    }
                ]
            )
        values[name] = int(candidate)
    return values


def parse_queue_pagination(
    request: Request,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> RefreshQueueListQuery:
    """Keep OpenAPI typed while the core strict-integer contract remains final."""

    _ = (offset, limit)
    try:
        return RefreshQueueListQuery.model_validate(_strict_query_integers(request))
    except ValidationError as exc:
        raise RequestValidationError(exc.errors()) from exc


def parse_item_pagination(
    request: Request,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> RefreshQueueItemListQuery:
    """Validate Item pagination using its closed core contract."""

    _ = (offset, limit)
    try:
        return RefreshQueueItemListQuery.model_validate(_strict_query_integers(request))
    except ValidationError as exc:
        raise RequestValidationError(exc.errors()) from exc


async def _service_call[ResultT](awaitable: Awaitable[ResultT]) -> ResultT:
    try:
        return await awaitable
    except RefreshQueueError as exc:
        raise ApiError(exc.status_code, exc.code, exc.message) from exc


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    response_model=SuccessEnvelope[RefreshQueueDetailResponse],
    responses=_error_responses(401, 403, 409, 422),
)
async def create_refresh_queue(
    payload: RefreshQueueCreateInput,
    request: Request,
    context: Annotated[AuthContext, Depends(require_refresh_mutation)],
    service: Annotated[RefreshQueueService, Depends(get_refresh_queue_service)],
) -> dict[str, Any]:
    detail = await _service_call(
        service.create_queue(
            context,
            payload,
            ip=get_client_ip(request),
            user_agent=get_user_agent(request),
        )
    )
    return envelope(request, data=detail)


@router.get(
    "",
    response_model=SuccessEnvelope[RefreshQueueListPage],
    dependencies=[Depends(reject_invalid_pagination_parameters)],
    responses=_error_responses(401, 422),
)
async def list_refresh_queues(
    request: Request,
    query: Annotated[RefreshQueueListQuery, Depends(parse_queue_pagination)],
    context: Annotated[AuthContext, Depends(require_auth)],
    service: Annotated[RefreshQueueService, Depends(get_refresh_queue_service)],
) -> dict[str, Any]:
    page = await _service_call(service.list_queues(context, query))
    return envelope(request, data=page)


@router.get(
    "/{refresh_queue_id}",
    response_model=SuccessEnvelope[RefreshQueueDetailResponse],
    responses=_error_responses(401, 404, 422),
)
async def get_refresh_queue(
    refresh_queue_id: UUID,
    request: Request,
    context: Annotated[AuthContext, Depends(require_auth)],
    service: Annotated[RefreshQueueService, Depends(get_refresh_queue_service)],
) -> dict[str, Any]:
    detail = await _service_call(service.get_queue_detail(context, refresh_queue_id))
    return envelope(request, data=detail)


@router.get(
    "/{refresh_queue_id}/items",
    response_model=SuccessEnvelope[RefreshQueueItemPage],
    dependencies=[Depends(reject_invalid_pagination_parameters)],
    responses=_error_responses(401, 404, 422),
)
async def list_refresh_queue_items(
    refresh_queue_id: UUID,
    request: Request,
    query: Annotated[RefreshQueueItemListQuery, Depends(parse_item_pagination)],
    context: Annotated[AuthContext, Depends(require_auth)],
    service: Annotated[RefreshQueueService, Depends(get_refresh_queue_service)],
) -> dict[str, Any]:
    page = await _service_call(service.list_queue_items(context, refresh_queue_id, query))
    return envelope(request, data=page)


@router.post(
    "/{refresh_queue_id}/export",
    response_class=Response,
    responses={
        200: {
            "content": {"text/csv": {}},
            "description": "Frozen Refresh Queue CSV attachment",
        },
        **_error_responses(401, 403, 404, 409, 422),
    },
)
async def export_refresh_queue(
    refresh_queue_id: UUID,
    request: Request,
    context: Annotated[AuthContext, Depends(require_refresh_mutation)],
    service: Annotated[RefreshQueueService, Depends(get_refresh_queue_service)],
) -> Response:
    result = await _service_call(
        service.export_queue(
            context,
            refresh_queue_id,
            ip=get_client_ip(request),
            user_agent=get_user_agent(request),
        )
    )
    return Response(
        content=result.content,
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{result.filename}"'},
    )


@router.post(
    "/{refresh_queue_id}/cancel",
    response_model=SuccessEnvelope[RefreshQueueDetailResponse],
    responses=_error_responses(401, 403, 404, 409, 422),
)
async def cancel_refresh_queue(
    refresh_queue_id: UUID,
    request: Request,
    context: Annotated[AuthContext, Depends(require_refresh_mutation)],
    service: Annotated[RefreshQueueService, Depends(get_refresh_queue_service)],
) -> dict[str, Any]:
    detail = await _service_call(
        service.cancel_queue(
            context,
            refresh_queue_id,
            ip=get_client_ip(request),
            user_agent=get_user_agent(request),
        )
    )
    return envelope(request, data=detail)
