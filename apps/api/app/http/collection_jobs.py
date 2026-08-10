"""Thin HTTP routes for Phase 1B collection jobs."""

from typing import Annotated, Any
from uuid import UUID

from backend_core.auth.service import AuthContext
from backend_core.imports.schemas import CollectionJobCreate, CollectionJobPublic
from backend_core.imports.service import ImportService
from fastapi import APIRouter, Depends, Request, status

from app.http.dependencies import get_import_service, require_auth, require_import_mutation
from app.http.responses import envelope

router = APIRouter(prefix="/api/v1/collection-jobs", tags=["collection-jobs"])


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_collection_job(
    payload: CollectionJobCreate,
    request: Request,
    context: Annotated[AuthContext, Depends(require_import_mutation)],
    service: Annotated[ImportService, Depends(get_import_service)],
) -> dict[str, Any]:
    job = await service.create_collection_job(context, payload)
    return envelope(request, data=CollectionJobPublic.model_validate(job))


@router.get("")
async def list_collection_jobs(
    request: Request,
    context: Annotated[AuthContext, Depends(require_auth)],
    service: Annotated[ImportService, Depends(get_import_service)],
) -> dict[str, Any]:
    jobs = await service.list_collection_jobs(context)
    return envelope(
        request,
        data=[CollectionJobPublic.model_validate(job) for job in jobs],
    )


@router.get("/{collection_job_id}")
async def get_collection_job(
    collection_job_id: UUID,
    request: Request,
    context: Annotated[AuthContext, Depends(require_auth)],
    service: Annotated[ImportService, Depends(get_import_service)],
) -> dict[str, Any]:
    job = await service.get_collection_job(context, collection_job_id)
    return envelope(request, data=CollectionJobPublic.model_validate(job))
