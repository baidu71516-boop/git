"""Employee-facing Market Prospect Rule V1 HTTP boundary."""

from __future__ import annotations

import logging
from collections.abc import Awaitable
from typing import Annotated, Any
from uuid import UUID

from backend_core.auth import ALL_OF, EXACT, EffectiveAuthorizationContext, ModuleKey
from backend_core.campaigns.access import DepartmentScope
from backend_core.growth.enums import CandidatePoolRunStatus
from backend_core.growth.schemas import (
    BuyerProspectRuleCreateInput,
    BuyerProspectRuleLifecycleInput,
    BuyerProspectRuleOptionsPublic,
    BuyerProspectRulePage,
    BuyerProspectRulePublic,
    BuyerProspectRuleUpdateInput,
    CandidatePoolRunPublic,
    CandidatePoolRunRequest,
)
from backend_core.growth.service import CandidatePoolService, TargetingError
from fastapi import APIRouter, Depends, Header, Query, Request, status
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse

from app.http.candidate_pools import get_candidate_pool_service
from app.http.dependencies import (
    get_client_ip,
    get_targeting_task_dispatcher,
    get_user_agent,
    require_module,
    require_module_write,
)
from app.http.errors import ApiError
from app.http.phase3a_http import Phase3AHttpWrite, reject_closed_query_parameters
from app.http.phase3a_scope import resolve_phase3a_department_scope
from app.http.responses import ErrorEnvelope, SuccessEnvelope, envelope
from app.http.targeting_tasks import TargetingTaskDispatcher

router = APIRouter(prefix="/api/v1/buyer-prospects", tags=["buyer-prospects"])
logger = logging.getLogger(__name__)

BuyerProspectsReadContext = Annotated[
    EffectiveAuthorizationContext,
    Depends(require_module(EXACT(ModuleKey.CANDIDATE_POOLS))),
]
BuyerProspectsDataReadContext = Annotated[
    EffectiveAuthorizationContext,
    Depends(require_module(ALL_OF(ModuleKey.CANDIDATE_POOLS, ModuleKey.DATA_COLLECTION))),
]
BuyerProspectsWriteContext = Annotated[
    EffectiveAuthorizationContext,
    Depends(require_module_write(EXACT(ModuleKey.CANDIDATE_POOLS))),
]
BuyerProspectsAuthoringWriteContext = Annotated[
    EffectiveAuthorizationContext,
    Depends(require_module_write(ALL_OF(ModuleKey.CANDIDATE_POOLS, ModuleKey.DATA_COLLECTION))),
]

BUYER_PROSPECT_LIST_QUERY_PARAMETERS = frozenset({"cursor", "limit", "include_archived"})
BUYER_PROSPECT_OPTIONS_QUERY_PARAMETERS = frozenset()


class BuyerProspectRuleRunRequest(Phase3AHttpWrite):
    """An explicit empty body: Market rules always run their current policy."""


def _error_responses(*status_codes: int) -> dict[int | str, dict[str, Any]]:
    return {
        status_code: {"model": ErrorEnvelope, "description": "Error response"}
        for status_code in status_codes
    }


async def _service_call[ResultT](awaitable: Awaitable[ResultT]) -> ResultT:
    try:
        return await awaitable
    except TargetingError as error:
        raise ApiError(error.status_code, error.code, error.message) from error


def _single_idempotency_key(request: Request, idempotency_key: str | None) -> str:
    if len(request.headers.getlist("idempotency-key")) != 1 or not idempotency_key:
        raise ApiError(422, "IDEMPOTENCY_KEY_INVALID", "Idempotency-Key is invalid")
    return idempotency_key


def reject_invalid_buyer_prospect_list_query_parameters(request: Request) -> None:
    reject_closed_query_parameters(request, BUYER_PROSPECT_LIST_QUERY_PARAMETERS)


def reject_invalid_buyer_prospect_options_query_parameters(request: Request) -> None:
    reject_closed_query_parameters(request, BUYER_PROSPECT_OPTIONS_QUERY_PARAMETERS)


def _log_dispatch_failure(*, run_id: UUID) -> None:
    logger.warning(
        "buyer_prospect_rule_run_immediate_dispatch_failed",
        extra={"candidate_pool_run_id": str(run_id)},
    )


@router.get(
    "",
    response_model=SuccessEnvelope[BuyerProspectRulePage],
    dependencies=[Depends(reject_invalid_buyer_prospect_list_query_parameters)],
    responses=_error_responses(401, 403, 404, 422),
)
async def list_buyer_prospect_rules(
    request: Request,
    scope: Annotated[DepartmentScope, Depends(resolve_phase3a_department_scope)],
    context: BuyerProspectsReadContext,
    service: Annotated[CandidatePoolService, Depends(get_candidate_pool_service)],
    cursor: Annotated[UUID | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    include_archived: Annotated[bool, Query()] = False,
) -> dict[str, Any]:
    page = await _service_call(
        service.list_buyer_prospect_rules(
            context,
            cursor=cursor,
            limit=limit,
            include_archived=include_archived,
            department_id=scope.department_id,
        )
    )
    return envelope(request, data=page)


@router.get(
    "/options",
    response_model=SuccessEnvelope[BuyerProspectRuleOptionsPublic],
    dependencies=[Depends(reject_invalid_buyer_prospect_options_query_parameters)],
    responses=_error_responses(401, 403, 404, 422),
)
async def buyer_prospect_rule_options(
    request: Request,
    scope: Annotated[DepartmentScope, Depends(resolve_phase3a_department_scope)],
    context: BuyerProspectsDataReadContext,
    service: Annotated[CandidatePoolService, Depends(get_candidate_pool_service)],
) -> dict[str, Any]:
    options = await _service_call(
        service.buyer_prospect_rule_options(
            context,
            department_id=scope.department_id,
        )
    )
    return envelope(request, data=options)


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    response_model=SuccessEnvelope[BuyerProspectRulePublic],
    responses=_error_responses(401, 403, 404, 409, 422),
)
async def create_buyer_prospect_rule(
    payload: BuyerProspectRuleCreateInput,
    request: Request,
    scope: Annotated[DepartmentScope, Depends(resolve_phase3a_department_scope)],
    context: BuyerProspectsAuthoringWriteContext,
    service: Annotated[CandidatePoolService, Depends(get_candidate_pool_service)],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> dict[str, Any]:
    rule = await _service_call(
        service.create_buyer_prospect_rule(
            context,
            payload,
            department_id=scope.department_id,
            idempotency_key=_single_idempotency_key(request, idempotency_key),
            ip=get_client_ip(request),
            user_agent=get_user_agent(request),
        )
    )
    return envelope(request, data=rule)


@router.get(
    "/{pool_id}",
    response_model=SuccessEnvelope[BuyerProspectRulePublic],
    responses=_error_responses(401, 403, 404, 422),
)
async def get_buyer_prospect_rule(
    pool_id: UUID,
    request: Request,
    scope: Annotated[DepartmentScope, Depends(resolve_phase3a_department_scope)],
    context: BuyerProspectsReadContext,
    service: Annotated[CandidatePoolService, Depends(get_candidate_pool_service)],
) -> dict[str, Any]:
    rule = await _service_call(
        service.get_buyer_prospect_rule(
            context,
            pool_id=pool_id,
            department_id=scope.department_id,
        )
    )
    return envelope(request, data=rule)


@router.put(
    "/{pool_id}",
    response_model=SuccessEnvelope[BuyerProspectRulePublic],
    responses=_error_responses(401, 403, 404, 409, 422),
)
async def update_buyer_prospect_rule(
    pool_id: UUID,
    payload: BuyerProspectRuleUpdateInput,
    request: Request,
    scope: Annotated[DepartmentScope, Depends(resolve_phase3a_department_scope)],
    context: BuyerProspectsAuthoringWriteContext,
    service: Annotated[CandidatePoolService, Depends(get_candidate_pool_service)],
) -> dict[str, Any]:
    rule = await _service_call(
        service.update_buyer_prospect_rule(
            context,
            pool_id=pool_id,
            payload=payload,
            department_id=scope.department_id,
            ip=get_client_ip(request),
            user_agent=get_user_agent(request),
        )
    )
    return envelope(request, data=rule)


@router.post(
    "/{pool_id}/lifecycle",
    response_model=SuccessEnvelope[BuyerProspectRulePublic],
    responses=_error_responses(401, 403, 404, 409, 422),
)
async def set_buyer_prospect_rule_lifecycle(
    pool_id: UUID,
    payload: BuyerProspectRuleLifecycleInput,
    request: Request,
    scope: Annotated[DepartmentScope, Depends(resolve_phase3a_department_scope)],
    context: BuyerProspectsWriteContext,
    service: Annotated[CandidatePoolService, Depends(get_candidate_pool_service)],
) -> dict[str, Any]:
    rule = await _service_call(
        service.set_buyer_prospect_rule_lifecycle(
            context,
            pool_id=pool_id,
            payload=payload,
            department_id=scope.department_id,
            ip=get_client_ip(request),
            user_agent=get_user_agent(request),
        )
    )
    return envelope(request, data=rule)


@router.post(
    "/{pool_id}/runs",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=SuccessEnvelope[CandidatePoolRunPublic],
    responses={
        status.HTTP_200_OK: {
            "model": SuccessEnvelope[CandidatePoolRunPublic],
            "description": "Idempotent Market Prospect Rule run replay",
        },
        **_error_responses(401, 403, 404, 409, 422),
    },
)
async def run_buyer_prospect_rule(
    pool_id: UUID,
    payload: BuyerProspectRuleRunRequest,
    request: Request,
    scope: Annotated[DepartmentScope, Depends(resolve_phase3a_department_scope)],
    context: BuyerProspectsWriteContext,
    service: Annotated[CandidatePoolService, Depends(get_candidate_pool_service)],
    dispatcher: Annotated[TargetingTaskDispatcher, Depends(get_targeting_task_dispatcher)],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> JSONResponse:
    # Keep the parsed empty body visible at the boundary; no historical or
    # client-authored policy shape may enter this employee route.
    del payload
    run = await _service_call(
        service.reserve_run(
            context,
            pool_id=pool_id,
            payload=CandidatePoolRunRequest(),
            department_id=scope.department_id,
            idempotency_key=_single_idempotency_key(request, idempotency_key),
            ip=get_client_ip(request),
            user_agent=get_user_agent(request),
        )
    )
    if run.status is CandidatePoolRunStatus.PENDING:
        try:
            await dispatcher.materialize(run.id)
        except Exception:
            # The durable PENDING Run can be safely reconciled by the worker.
            _log_dispatch_failure(run_id=run.id)
    response_status = status.HTTP_200_OK if run.idempotent_replay else status.HTTP_202_ACCEPTED
    return JSONResponse(
        status_code=response_status,
        content=jsonable_encoder(envelope(request, data=run)),
    )
