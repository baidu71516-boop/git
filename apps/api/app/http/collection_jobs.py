"""Thin HTTP routes for Phase 1B collection jobs."""

import logging
from collections.abc import Awaitable
from typing import Annotated, Any
from uuid import UUID

from backend_core.auth import ALL_OF, EXACT, EffectiveAuthorizationContext, ModuleKey
from backend_core.growth.enums import CandidatePoolRunStatus
from backend_core.growth.schemas import BuyerScreeningBootstrapPublic
from backend_core.growth.service import CandidatePoolService, TargetingError
from backend_core.imports.schemas import (
    CollectionJobCreate,
    CollectionJobPublic,
    CollectionJobScreeningRulesUpdate,
)
from backend_core.imports.service import ImportService
from fastapi import APIRouter, Depends, Header, Request, status
from pydantic import ConfigDict

from app.http.candidate_pools import get_candidate_pool_service
from app.http.dependencies import (
    get_client_ip,
    get_import_service,
    get_targeting_task_dispatcher,
    get_user_agent,
    require_module,
    require_module_write,
)
from app.http.errors import ApiError
from app.http.phase3a_http import Phase3AHttpWrite
from app.http.responses import ErrorEnvelope, SuccessEnvelope, envelope
from app.http.targeting_tasks import TargetingTaskDispatcher

router = APIRouter(prefix="/api/v1/collection-jobs", tags=["collection-jobs"])
logger = logging.getLogger(__name__)
DataCollectionReadContext = Annotated[
    EffectiveAuthorizationContext,
    Depends(require_module(EXACT(ModuleKey.DATA_COLLECTION))),
]
DataCollectionWriteContext = Annotated[
    EffectiveAuthorizationContext,
    Depends(require_module_write(EXACT(ModuleKey.DATA_COLLECTION))),
]
BuyerScreeningWriteContext = Annotated[
    EffectiveAuthorizationContext,
    Depends(require_module_write(ALL_OF(ModuleKey.DATA_COLLECTION, ModuleKey.CANDIDATE_POOLS))),
]


class BuyerScreeningBootstrapRequest(Phase3AHttpWrite):
    """Deliberately empty: Buyer policy and taxonomy are server-owned."""

    model_config = ConfigDict(extra="forbid")


def _error_responses(*status_codes: int) -> dict[int | str, dict[str, Any]]:
    return {
        status_code: {"model": ErrorEnvelope, "description": "Error response"}
        for status_code in status_codes
    }


async def _buyer_screening_service_call[ResultT](awaitable: Awaitable[ResultT]) -> ResultT:
    try:
        return await awaitable
    except TargetingError as exc:
        raise ApiError(exc.status_code, exc.code, exc.message) from exc


def _single_idempotency_key(request: Request, idempotency_key: str | None) -> str:
    if len(request.headers.getlist("idempotency-key")) != 1 or not idempotency_key:
        raise ApiError(422, "IDEMPOTENCY_KEY_INVALID", "Idempotency-Key is invalid")
    return idempotency_key


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    response_model=SuccessEnvelope[CollectionJobPublic],
    responses=_error_responses(401, 403, 409, 422),
)
async def create_collection_job(
    payload: CollectionJobCreate,
    request: Request,
    context: DataCollectionWriteContext,
    service: Annotated[ImportService, Depends(get_import_service)],
) -> dict[str, Any]:
    job = await service.create_collection_job(context, payload)
    return envelope(request, data=CollectionJobPublic.model_validate(job))


@router.get(
    "",
    response_model=SuccessEnvelope[list[CollectionJobPublic]],
    responses=_error_responses(401),
)
async def list_collection_jobs(
    request: Request,
    context: DataCollectionReadContext,
    service: Annotated[ImportService, Depends(get_import_service)],
) -> dict[str, Any]:
    jobs = await service.list_collection_jobs(context)
    return envelope(
        request,
        data=[CollectionJobPublic.model_validate(job) for job in jobs],
    )


@router.put(
    "/{collection_job_id}/screening-rules",
    response_model=SuccessEnvelope[CollectionJobPublic],
    responses=_error_responses(401, 403, 404, 409, 422),
)
async def update_collection_job_screening_rules(
    collection_job_id: UUID,
    payload: CollectionJobScreeningRulesUpdate,
    request: Request,
    context: DataCollectionWriteContext,
    service: Annotated[ImportService, Depends(get_import_service)],
) -> dict[str, Any]:
    job = await service.update_collection_job_screening_rules(
        context,
        collection_job_id,
        payload,
        ip=get_client_ip(request),
        user_agent=get_user_agent(request),
    )
    return envelope(request, data=CollectionJobPublic.model_validate(job))


@router.post(
    "/{collection_job_id}/buyer-screening",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=SuccessEnvelope[BuyerScreeningBootstrapPublic],
    responses=_error_responses(401, 403, 404, 409, 422),
)
async def bootstrap_buyer_screening(
    collection_job_id: UUID,
    payload: BuyerScreeningBootstrapRequest,
    request: Request,
    context: BuyerScreeningWriteContext,
    service: Annotated[CandidatePoolService, Depends(get_candidate_pool_service)],
    dispatcher: Annotated[TargetingTaskDispatcher, Depends(get_targeting_task_dispatcher)],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> dict[str, Any]:
    # Keep an explicit empty contract at the HTTP boundary. Referencing the
    # parsed value makes it impossible to silently accept client taxonomy JSON.
    del payload
    result = await _buyer_screening_service_call(
        service.bootstrap_buyer_screening(
            context,
            collection_job_id=collection_job_id,
            idempotency_key=_single_idempotency_key(request, idempotency_key),
            ip=get_client_ip(request),
            user_agent=get_user_agent(request),
        )
    )
    if result.run.status is CandidatePoolRunStatus.PENDING:
        try:
            await dispatcher.materialize(result.run.id)
        except Exception:
            # The run is durable before this point. A reconciler can safely
            # republish it without making the user create another Pool.
            logger.warning(
                "buyer_screening_bootstrap_dispatch_failed",
                extra={"candidate_pool_run_id": str(result.run.id)},
            )
    return envelope(request, data=result)


@router.get(
    "/{collection_job_id}",
    response_model=SuccessEnvelope[CollectionJobPublic],
    responses=_error_responses(401, 404, 422),
)
async def get_collection_job(
    collection_job_id: UUID,
    request: Request,
    context: DataCollectionReadContext,
    service: Annotated[ImportService, Depends(get_import_service)],
) -> dict[str, Any]:
    job = await service.get_collection_job(context, collection_job_id)
    return envelope(request, data=CollectionJobPublic.model_validate(job))
