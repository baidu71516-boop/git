"""Thin HTTP routes for Phase 1B collection jobs."""

from typing import Annotated, Any
from uuid import UUID

from backend_core.auth import EXACT, EffectiveAuthorizationContext, ModuleKey
from backend_core.imports.schemas import (
    CollectionJobCreate,
    CollectionJobPublic,
    CollectionJobScreeningRulesUpdate,
)
from backend_core.imports.service import ImportService
from fastapi import APIRouter, Depends, Request, status

from app.http.dependencies import (
    get_client_ip,
    get_import_service,
    get_user_agent,
    require_module,
    require_module_write,
)
from app.http.responses import ErrorEnvelope, SuccessEnvelope, envelope

router = APIRouter(prefix="/api/v1/collection-jobs", tags=["collection-jobs"])
DataCollectionReadContext = Annotated[
    EffectiveAuthorizationContext,
    Depends(require_module(EXACT(ModuleKey.DATA_COLLECTION))),
]
DataCollectionWriteContext = Annotated[
    EffectiveAuthorizationContext,
    Depends(require_module_write(EXACT(ModuleKey.DATA_COLLECTION))),
]


def _error_responses(*status_codes: int) -> dict[int | str, dict[str, Any]]:
    return {
        status_code: {"model": ErrorEnvelope, "description": "Error response"}
        for status_code in status_codes
    }


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
