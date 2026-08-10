"""Thin HTTP routes for persisted two-phase import jobs."""

from collections.abc import AsyncIterator
from typing import Annotated, Any
from uuid import UUID, uuid4

from backend_core.auth.service import AuthContext
from backend_core.imports.enums import ImportRowAction
from backend_core.imports.errors import ImportDomainError
from backend_core.imports.schemas import (
    ImportConfirmInput,
    ImportDispatchResult,
    ImportJobPublic,
    ImportMappingUpdate,
    ImportRowPublic,
    ImportRowsPage,
)
from backend_core.imports.service import ImportService, QueueDecision
from fastapi import APIRouter, Depends, File, Form, Query, Request, UploadFile, status
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse

from app.http.dependencies import (
    get_client_ip,
    get_import_service,
    get_import_task_dispatcher,
    get_user_agent,
    require_auth,
    require_import_mutation,
)
from app.http.import_tasks import ImportTaskDispatcher
from app.http.responses import envelope

router = APIRouter(prefix="/api/v1/import-jobs", tags=["import-jobs"])


def _dispatch_public(decision: QueueDecision) -> ImportDispatchResult:
    return ImportDispatchResult(
        import_job_id=decision.job.id,
        status=decision.job.status,
        preview_revision=decision.job.preview_revision,
        task_id=decision.job.confirm_task_id or decision.job.parse_task_id,
        idempotent=decision.idempotent,
    )


async def _compensate_dispatch_failure(
    *,
    service: ImportService,
    context: AuthContext,
    import_job_id: UUID,
    task_id: str,
    request: Request,
) -> None:
    await service.mark_dispatch_failed(
        context,
        import_job_id,
        task_id,
        ip=get_client_ip(request),
        user_agent=get_user_agent(request),
    )
    raise ImportDomainError(
        "TASK_DISPATCH_FAILED",
        "Background task could not be queued; the import was marked failed",
        status_code=503,
    )


@router.post("", status_code=status.HTTP_202_ACCEPTED)
async def upload_import_file(
    request: Request,
    collection_job_id: Annotated[UUID, Form()],
    file: Annotated[UploadFile, File()],
    context: Annotated[AuthContext, Depends(require_import_mutation)],
    service: Annotated[ImportService, Depends(get_import_service)],
    dispatcher: Annotated[ImportTaskDispatcher, Depends(get_import_task_dispatcher)],
) -> dict[str, Any]:
    task_id = uuid4().hex

    async def chunks() -> AsyncIterator[bytes]:
        while content := await file.read(1024 * 1024):
            yield content

    try:
        job = await service.create_import_job(
            context,
            collection_job_id=collection_job_id,
            filename=file.filename or "upload",
            declared_mime=file.content_type or "application/octet-stream",
            chunks=chunks(),
            parse_task_id=task_id,
            ip=get_client_ip(request),
            user_agent=get_user_agent(request),
        )
    finally:
        await file.close()
    try:
        await dispatcher.parse(job.id, task_id)
    except Exception:
        await _compensate_dispatch_failure(
            service=service,
            context=context,
            import_job_id=job.id,
            task_id=task_id,
            request=request,
        )
    return envelope(request, data=ImportJobPublic.model_validate(job))


@router.get("/{import_job_id}")
async def get_import_job(
    import_job_id: UUID,
    request: Request,
    context: Annotated[AuthContext, Depends(require_auth)],
    service: Annotated[ImportService, Depends(get_import_service)],
) -> dict[str, Any]:
    job = await service.get_import_job(context, import_job_id)
    return envelope(request, data=ImportJobPublic.model_validate(job))


@router.get("/{import_job_id}/rows")
async def list_import_rows(
    import_job_id: UUID,
    request: Request,
    context: Annotated[AuthContext, Depends(require_auth)],
    service: Annotated[ImportService, Depends(get_import_service)],
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    action: Annotated[ImportRowAction | None, Query()] = None,
) -> dict[str, Any]:
    rows, total = await service.list_import_rows(
        context,
        import_job_id,
        offset=offset,
        limit=limit,
        action=action,
    )
    page = ImportRowsPage(
        items=[ImportRowPublic.model_validate(row) for row in rows],
        total=total,
        offset=offset,
        limit=limit,
    )
    return envelope(request, data=page)


@router.put("/{import_job_id}/mapping", status_code=status.HTTP_202_ACCEPTED)
async def update_mapping(
    import_job_id: UUID,
    payload: ImportMappingUpdate,
    request: Request,
    context: Annotated[AuthContext, Depends(require_import_mutation)],
    service: Annotated[ImportService, Depends(get_import_service)],
    dispatcher: Annotated[ImportTaskDispatcher, Depends(get_import_task_dispatcher)],
) -> dict[str, Any]:
    task_id = uuid4().hex
    decision = await service.request_mapping_preview(
        context,
        import_job_id,
        payload.mapping,
        task_id,
        ip=get_client_ip(request),
        user_agent=get_user_agent(request),
    )
    try:
        await dispatcher.parse(import_job_id, task_id)
    except Exception:
        await _compensate_dispatch_failure(
            service=service,
            context=context,
            import_job_id=import_job_id,
            task_id=task_id,
            request=request,
        )
    return envelope(request, data=_dispatch_public(decision))


@router.post("/{import_job_id}/preview", status_code=status.HTTP_202_ACCEPTED)
async def regenerate_preview(
    import_job_id: UUID,
    request: Request,
    context: Annotated[AuthContext, Depends(require_import_mutation)],
    service: Annotated[ImportService, Depends(get_import_service)],
    dispatcher: Annotated[ImportTaskDispatcher, Depends(get_import_task_dispatcher)],
) -> dict[str, Any]:
    task_id = uuid4().hex
    decision = await service.request_preview(context, import_job_id, task_id)
    try:
        await dispatcher.parse(import_job_id, task_id)
    except Exception:
        await _compensate_dispatch_failure(
            service=service,
            context=context,
            import_job_id=import_job_id,
            task_id=task_id,
            request=request,
        )
    return envelope(request, data=_dispatch_public(decision))


@router.post("/{import_job_id}/confirm", status_code=status.HTTP_202_ACCEPTED)
async def confirm_import(
    import_job_id: UUID,
    payload: ImportConfirmInput,
    request: Request,
    context: Annotated[AuthContext, Depends(require_import_mutation)],
    service: Annotated[ImportService, Depends(get_import_service)],
    dispatcher: Annotated[ImportTaskDispatcher, Depends(get_import_task_dispatcher)],
) -> JSONResponse:
    task_id = uuid4().hex
    decision = await service.request_confirm(
        context,
        import_job_id,
        payload.preview_revision,
        task_id,
        ip=get_client_ip(request),
        user_agent=get_user_agent(request),
    )
    if decision.should_dispatch:
        try:
            await dispatcher.confirm(import_job_id, payload.preview_revision, task_id)
        except Exception:
            await _compensate_dispatch_failure(
                service=service,
                context=context,
                import_job_id=import_job_id,
                task_id=task_id,
                request=request,
            )
    response_status = status.HTTP_200_OK if decision.idempotent else status.HTTP_202_ACCEPTED
    return JSONResponse(
        status_code=response_status,
        content=jsonable_encoder(envelope(request, data=_dispatch_public(decision))),
    )


@router.post("/{import_job_id}/cancel")
async def cancel_import(
    import_job_id: UUID,
    request: Request,
    context: Annotated[AuthContext, Depends(require_import_mutation)],
    service: Annotated[ImportService, Depends(get_import_service)],
) -> dict[str, Any]:
    job = await service.cancel(
        context,
        import_job_id,
        ip=get_client_ip(request),
        user_agent=get_user_agent(request),
    )
    return envelope(request, data=ImportJobPublic.model_validate(job))
