"""Thin HTTP adaptation for deterministic Candidate Pool targeting operations."""

import logging
from collections.abc import Awaitable
from typing import Annotated, Any
from uuid import UUID

from backend_core.auth.service import AuthContext
from backend_core.config import get_settings
from backend_core.growth.enums import CandidatePoolRunStatus
from backend_core.growth.schemas import (
    CandidatePoolCreateInput,
    CandidatePoolPage,
    CandidatePoolPublic,
    CandidatePoolRunMemberPage,
    CandidatePoolRunPublic,
    CandidatePoolRunRequest,
    TargetingPolicyCreateInput,
    TargetingPolicyCreateResultPublic,
    TargetingPolicyPublic,
)
from backend_core.growth.service import CandidatePoolService, TargetingError
from backend_core.influencers.freshness import FreshnessPolicy
from fastapi import APIRouter, Depends, Header, Query, Request, status
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.http.dependencies import (
    get_client_ip,
    get_database_session,
    get_targeting_task_dispatcher,
    get_user_agent,
    require_auth,
    require_targeting_mutation,
)
from app.http.errors import ApiError
from app.http.responses import ErrorEnvelope, SuccessEnvelope, envelope
from app.http.targeting_tasks import TargetingTaskDispatcher

router = APIRouter(prefix="/api/v1/candidate-pools", tags=["candidate-pools"])
settings = get_settings()
logger = logging.getLogger(__name__)

KEYSET_QUERY_PARAMETERS = frozenset({"cursor", "limit"})


def _error_responses(*status_codes: int) -> dict[int | str, dict[str, Any]]:
    return {
        status_code: {"model": ErrorEnvelope, "description": "Error response"}
        for status_code in status_codes
    }


def get_candidate_pool_service(
    session: Annotated[AsyncSession, Depends(get_database_session)],
) -> CandidatePoolService:
    return CandidatePoolService(
        session,
        freshness_policy=FreshnessPolicy.from_day_thresholds(
            settings.freshness_fresh_days,
            settings.freshness_aging_days,
            settings.freshness_stale_days,
        ),
    )


def reject_invalid_keyset_query_parameters(request: Request) -> None:
    """Keep Candidate Pool pagination closed and unambiguous."""

    unexpected = sorted(set(request.query_params) - KEYSET_QUERY_PARAMETERS)
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
    for name in KEYSET_QUERY_PARAMETERS:
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


async def _service_call[ResultT](awaitable: Awaitable[ResultT]) -> ResultT:
    try:
        return await awaitable
    except TargetingError as exc:
        raise ApiError(exc.status_code, exc.code, exc.message) from exc


def _single_idempotency_key(request: Request, idempotency_key: str | None) -> str:
    """Require exactly one non-empty opaque Idempotency-Key header."""

    if len(request.headers.getlist("idempotency-key")) != 1 or not idempotency_key:
        raise ApiError(422, "IDEMPOTENCY_KEY_INVALID", "Idempotency-Key is invalid")
    return idempotency_key


def _log_dispatch_failure(*, run_id: UUID) -> None:
    logger.warning(
        "candidate_pool_run_immediate_dispatch_failed",
        extra={"candidate_pool_run_id": str(run_id)},
    )


@router.get(
    "",
    response_model=SuccessEnvelope[CandidatePoolPage],
    dependencies=[Depends(reject_invalid_keyset_query_parameters)],
    responses=_error_responses(401, 422),
)
async def list_candidate_pools(
    request: Request,
    context: Annotated[AuthContext, Depends(require_auth)],
    service: Annotated[CandidatePoolService, Depends(get_candidate_pool_service)],
    cursor: Annotated[UUID | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> dict[str, Any]:
    page = await _service_call(service.list_pools(context, cursor=cursor, limit=limit))
    return envelope(request, data=page)


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    response_model=SuccessEnvelope[CandidatePoolPublic],
    responses=_error_responses(401, 403, 409, 422),
)
async def create_candidate_pool(
    payload: CandidatePoolCreateInput,
    request: Request,
    context: Annotated[AuthContext, Depends(require_targeting_mutation)],
    service: Annotated[CandidatePoolService, Depends(get_candidate_pool_service)],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> dict[str, Any]:
    pool = await _service_call(
        service.create_pool(
            context,
            payload,
            idempotency_key=_single_idempotency_key(request, idempotency_key),
            ip=get_client_ip(request),
            user_agent=get_user_agent(request),
        )
    )
    return envelope(request, data=pool)


@router.get(
    "/{pool_id}/policies",
    response_model=SuccessEnvelope[tuple[TargetingPolicyPublic, ...]],
    responses=_error_responses(401, 404, 422),
)
async def list_candidate_pool_policies(
    pool_id: UUID,
    request: Request,
    context: Annotated[AuthContext, Depends(require_auth)],
    service: Annotated[CandidatePoolService, Depends(get_candidate_pool_service)],
) -> dict[str, Any]:
    policies = await _service_call(service.list_policies(context, pool_id))
    return envelope(request, data=policies)


@router.post(
    "/{pool_id}/policies",
    status_code=status.HTTP_201_CREATED,
    response_model=SuccessEnvelope[TargetingPolicyCreateResultPublic],
    responses=_error_responses(401, 403, 404, 409, 422),
)
async def create_candidate_pool_policy(
    pool_id: UUID,
    payload: TargetingPolicyCreateInput,
    request: Request,
    context: Annotated[AuthContext, Depends(require_targeting_mutation)],
    service: Annotated[CandidatePoolService, Depends(get_candidate_pool_service)],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> dict[str, Any]:
    policy = await _service_call(
        service.append_policy(
            context,
            pool_id,
            payload,
            idempotency_key=_single_idempotency_key(request, idempotency_key),
            ip=get_client_ip(request),
            user_agent=get_user_agent(request),
        )
    )
    return envelope(request, data=policy)


@router.get(
    "/{pool_id}/runs/{run_id}",
    response_model=SuccessEnvelope[CandidatePoolRunPublic],
    responses=_error_responses(401, 404, 422),
)
async def get_candidate_pool_run(
    pool_id: UUID,
    run_id: UUID,
    request: Request,
    context: Annotated[AuthContext, Depends(require_auth)],
    service: Annotated[CandidatePoolService, Depends(get_candidate_pool_service)],
) -> dict[str, Any]:
    run = await _service_call(service.get_run(context, pool_id=pool_id, run_id=run_id))
    return envelope(request, data=run)


@router.get(
    "/{pool_id}/runs/{run_id}/members",
    response_model=SuccessEnvelope[CandidatePoolRunMemberPage],
    dependencies=[Depends(reject_invalid_keyset_query_parameters)],
    responses=_error_responses(401, 404, 422),
)
async def list_candidate_pool_run_members(
    pool_id: UUID,
    run_id: UUID,
    request: Request,
    context: Annotated[AuthContext, Depends(require_auth)],
    service: Annotated[CandidatePoolService, Depends(get_candidate_pool_service)],
    cursor: Annotated[UUID | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> dict[str, Any]:
    page = await _service_call(
        service.list_run_members(
            context,
            pool_id=pool_id,
            run_id=run_id,
            cursor=cursor,
            limit=limit,
        )
    )
    return envelope(request, data=page)


@router.post(
    "/{pool_id}/runs",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=SuccessEnvelope[CandidatePoolRunPublic],
    responses={
        status.HTTP_200_OK: {
            "model": SuccessEnvelope[CandidatePoolRunPublic],
            "description": "Idempotent run reservation replay",
        },
        **_error_responses(401, 403, 404, 409, 422),
    },
)
async def reserve_candidate_pool_run(
    pool_id: UUID,
    payload: CandidatePoolRunRequest,
    request: Request,
    context: Annotated[AuthContext, Depends(require_targeting_mutation)],
    service: Annotated[CandidatePoolService, Depends(get_candidate_pool_service)],
    dispatcher: Annotated[TargetingTaskDispatcher, Depends(get_targeting_task_dispatcher)],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> JSONResponse:
    # The closed request body is intentionally empty; server state supplies the policy and as_of.
    _ = payload
    header_values = request.headers.getlist("idempotency-key")
    if len(header_values) > 1:
        raise ApiError(422, "IDEMPOTENCY_KEY_INVALID", "Idempotency-Key is invalid")
    run = await _service_call(
        service.reserve_run(
            context,
            pool_id=pool_id,
            idempotency_key=idempotency_key or "",
            ip=get_client_ip(request),
            user_agent=get_user_agent(request),
        )
    )
    if run.status is CandidatePoolRunStatus.PENDING:
        try:
            # reserve_run commits before returning, so publication cannot outrun
            # the durable PENDING record that the worker will materialize.
            await dispatcher.materialize(run.id)
        except Exception:
            # Retain PENDING state for the worker reconciler to republish.
            _log_dispatch_failure(run_id=run.id)
    response_status = status.HTTP_200_OK if run.idempotent_replay else status.HTTP_202_ACCEPTED
    return JSONResponse(
        status_code=response_status,
        content=jsonable_encoder(envelope(request, data=run)),
    )


@router.get(
    "/{pool_id}",
    response_model=SuccessEnvelope[CandidatePoolPublic],
    responses=_error_responses(401, 404, 422),
)
async def get_candidate_pool(
    pool_id: UUID,
    request: Request,
    context: Annotated[AuthContext, Depends(require_auth)],
    service: Annotated[CandidatePoolService, Depends(get_candidate_pool_service)],
) -> dict[str, Any]:
    pool = await _service_call(service.get_pool(context, pool_id))
    return envelope(request, data=pool)
