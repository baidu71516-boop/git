"""Thin HTTP adaptation for deterministic Candidate Pool targeting operations."""

import logging
from collections.abc import Awaitable
from typing import Annotated, Any, Literal
from uuid import UUID

from backend_core.auth.service import AuthContext
from backend_core.campaigns.access import DepartmentScope
from backend_core.config import get_settings
from backend_core.growth.enums import CandidatePoolKind, CandidatePoolRunStatus, CandidateResult
from backend_core.growth.schemas import (
    CandidatePoolCreateInput,
    CandidatePoolPage,
    CandidatePoolPublic,
    CandidatePoolRunMemberPage,
    CandidatePoolRunPage,
    CandidatePoolRunPublic,
    CandidatePoolRunRequest,
    TargetingPolicyCreateInput,
    TargetingPolicyCreateResultPublic,
    TargetingPolicyPublic,
)
from backend_core.growth.service import CandidatePoolService, TargetingError
from backend_core.growth.targeting import TargetingPolicyDefinition
from backend_core.influencers.freshness import FreshnessPolicy
from fastapi import APIRouter, Depends, Header, Query, Request, status
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import Field, StrictStr
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
from app.http.phase3a_http import Phase3AHttpWrite
from app.http.phase3a_scope import resolve_phase3a_department_scope
from app.http.responses import ErrorEnvelope, SuccessEnvelope, envelope
from app.http.targeting_tasks import TargetingTaskDispatcher

router = APIRouter(prefix="/api/v1/candidate-pools", tags=["candidate-pools"])
settings = get_settings()
logger = logging.getLogger(__name__)

POOL_LIST_QUERY_PARAMETERS = frozenset({"cursor", "limit"})
RUN_LIST_QUERY_PARAMETERS = frozenset({"cursor", "limit"})
RUN_MEMBER_LIST_QUERY_PARAMETERS = frozenset({"cursor", "limit", "result"})
type CandidateRunMemberResult = Literal[CandidateResult.MATCH, CandidateResult.UNKNOWN]


class CandidatePoolCreateRequest(Phase3AHttpWrite):
    """Closed HTTP create body; Department scope is selected by the header/session."""

    name: StrictStr = Field(min_length=1, max_length=200)
    kind: CandidatePoolKind
    source_collection_job_id: UUID | None = None
    owner_operator_id: UUID | None = Field(
        default=None,
        description=(
            "Active owner in the resolved Department. Same-Department requests may omit it "
            "to default to the selected Operator; cross-Department Super Admin requests must "
            "provide a target-Department owner."
        ),
    )
    policy: TargetingPolicyDefinition


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


def _reject_invalid_query_parameters(request: Request, *, allowed: frozenset[str]) -> None:
    """Keep Candidate Pool pagination closed and unambiguous."""

    unexpected = sorted(set(request.query_params) - allowed)
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
    for name in allowed:
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


def reject_invalid_candidate_pool_list_query_parameters(request: Request) -> None:
    _reject_invalid_query_parameters(request, allowed=POOL_LIST_QUERY_PARAMETERS)


def reject_invalid_candidate_run_list_query_parameters(request: Request) -> None:
    _reject_invalid_query_parameters(request, allowed=RUN_LIST_QUERY_PARAMETERS)


def reject_invalid_candidate_run_member_list_query_parameters(request: Request) -> None:
    _reject_invalid_query_parameters(request, allowed=RUN_MEMBER_LIST_QUERY_PARAMETERS)


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
    dependencies=[Depends(reject_invalid_candidate_pool_list_query_parameters)],
    responses=_error_responses(401, 404, 422),
)
async def list_candidate_pools(
    request: Request,
    scope: Annotated[DepartmentScope, Depends(resolve_phase3a_department_scope)],
    context: Annotated[AuthContext, Depends(require_auth)],
    service: Annotated[CandidatePoolService, Depends(get_candidate_pool_service)],
    cursor: Annotated[UUID | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> dict[str, Any]:
    page = await _service_call(
        service.list_pools(
            context,
            cursor=cursor,
            limit=limit,
            department_id=scope.department_id,
        )
    )
    return envelope(request, data=page)


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    response_model=SuccessEnvelope[CandidatePoolPublic],
    responses=_error_responses(401, 403, 404, 409, 422),
)
async def create_candidate_pool(
    payload: CandidatePoolCreateRequest,
    request: Request,
    scope: Annotated[DepartmentScope, Depends(resolve_phase3a_department_scope)],
    context: Annotated[AuthContext, Depends(require_targeting_mutation)],
    service: Annotated[CandidatePoolService, Depends(get_candidate_pool_service)],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> dict[str, Any]:
    pool = await _service_call(
        service.create_pool(
            context,
            CandidatePoolCreateInput.model_validate(payload.model_dump(mode="python")),
            department_id=scope.department_id,
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
    scope: Annotated[DepartmentScope, Depends(resolve_phase3a_department_scope)],
    context: Annotated[AuthContext, Depends(require_auth)],
    service: Annotated[CandidatePoolService, Depends(get_candidate_pool_service)],
) -> dict[str, Any]:
    policies = await _service_call(
        service.list_policies(context, pool_id, department_id=scope.department_id)
    )
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
    scope: Annotated[DepartmentScope, Depends(resolve_phase3a_department_scope)],
    context: Annotated[AuthContext, Depends(require_targeting_mutation)],
    service: Annotated[CandidatePoolService, Depends(get_candidate_pool_service)],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> dict[str, Any]:
    policy = await _service_call(
        service.append_policy(
            context,
            pool_id,
            payload,
            department_id=scope.department_id,
            idempotency_key=_single_idempotency_key(request, idempotency_key),
            ip=get_client_ip(request),
            user_agent=get_user_agent(request),
        )
    )
    return envelope(request, data=policy)


@router.get(
    "/{pool_id}/policies/{policy_id}",
    response_model=SuccessEnvelope[TargetingPolicyPublic],
    responses=_error_responses(401, 404, 422),
)
async def get_candidate_pool_policy(
    pool_id: UUID,
    policy_id: UUID,
    request: Request,
    scope: Annotated[DepartmentScope, Depends(resolve_phase3a_department_scope)],
    context: Annotated[AuthContext, Depends(require_auth)],
    service: Annotated[CandidatePoolService, Depends(get_candidate_pool_service)],
) -> dict[str, Any]:
    policy = await _service_call(
        service.get_policy(
            context,
            pool_id=pool_id,
            policy_id=policy_id,
            department_id=scope.department_id,
        )
    )
    return envelope(request, data=policy)


@router.get(
    "/{pool_id}/runs",
    response_model=SuccessEnvelope[CandidatePoolRunPage],
    dependencies=[Depends(reject_invalid_candidate_run_list_query_parameters)],
    responses=_error_responses(401, 404, 422),
)
async def list_candidate_pool_runs(
    pool_id: UUID,
    request: Request,
    scope: Annotated[DepartmentScope, Depends(resolve_phase3a_department_scope)],
    context: Annotated[AuthContext, Depends(require_auth)],
    service: Annotated[CandidatePoolService, Depends(get_candidate_pool_service)],
    cursor: Annotated[UUID | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> dict[str, Any]:
    page = await _service_call(
        service.list_runs(
            context,
            pool_id=pool_id,
            cursor=cursor,
            limit=limit,
            department_id=scope.department_id,
        )
    )
    return envelope(request, data=page)


@router.get(
    "/{pool_id}/runs/{run_id}",
    response_model=SuccessEnvelope[CandidatePoolRunPublic],
    responses=_error_responses(401, 404, 422),
)
async def get_candidate_pool_run(
    pool_id: UUID,
    run_id: UUID,
    request: Request,
    scope: Annotated[DepartmentScope, Depends(resolve_phase3a_department_scope)],
    context: Annotated[AuthContext, Depends(require_auth)],
    service: Annotated[CandidatePoolService, Depends(get_candidate_pool_service)],
) -> dict[str, Any]:
    run = await _service_call(
        service.get_run(
            context,
            pool_id=pool_id,
            run_id=run_id,
            department_id=scope.department_id,
        )
    )
    return envelope(request, data=run)


@router.get(
    "/{pool_id}/runs/{run_id}/members",
    response_model=SuccessEnvelope[CandidatePoolRunMemberPage],
    dependencies=[Depends(reject_invalid_candidate_run_member_list_query_parameters)],
    responses=_error_responses(401, 404, 422),
)
async def list_candidate_pool_run_members(
    pool_id: UUID,
    run_id: UUID,
    request: Request,
    scope: Annotated[DepartmentScope, Depends(resolve_phase3a_department_scope)],
    context: Annotated[AuthContext, Depends(require_auth)],
    service: Annotated[CandidatePoolService, Depends(get_candidate_pool_service)],
    cursor: Annotated[UUID | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    result: Annotated[CandidateRunMemberResult | None, Query()] = None,
) -> dict[str, Any]:
    page = await _service_call(
        service.list_run_members(
            context,
            pool_id=pool_id,
            run_id=run_id,
            cursor=cursor,
            limit=limit,
            result=result,
            department_id=scope.department_id,
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
    scope: Annotated[DepartmentScope, Depends(resolve_phase3a_department_scope)],
    context: Annotated[AuthContext, Depends(require_targeting_mutation)],
    service: Annotated[CandidatePoolService, Depends(get_candidate_pool_service)],
    dispatcher: Annotated[TargetingTaskDispatcher, Depends(get_targeting_task_dispatcher)],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> JSONResponse:
    # The closed request body is intentionally empty; server state supplies the policy and as_of.
    _ = payload
    run = await _service_call(
        service.reserve_run(
            context,
            pool_id=pool_id,
            department_id=scope.department_id,
            idempotency_key=_single_idempotency_key(request, idempotency_key),
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
    scope: Annotated[DepartmentScope, Depends(resolve_phase3a_department_scope)],
    context: Annotated[AuthContext, Depends(require_auth)],
    service: Annotated[CandidatePoolService, Depends(get_candidate_pool_service)],
) -> dict[str, Any]:
    pool = await _service_call(
        service.get_pool(context, pool_id, department_id=scope.department_id)
    )
    return envelope(request, data=pool)
