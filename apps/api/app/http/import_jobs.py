"""Thin HTTP routes for persisted two-phase import jobs."""

import logging
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Annotated, Any, overload
from uuid import UUID, uuid4

from backend_core.auth.service import AuthContext
from backend_core.imports.enums import ImportRowAction
from backend_core.imports.repository import ImportJobFileRecord
from backend_core.imports.schemas import (
    BulkImportJobCreate,
    ImportConfirmInput,
    ImportDispatchResult,
    ImportJobFilePublic,
    ImportJobFileUploadResult,
    ImportJobPublic,
    ImportMappingUpdate,
    ImportPreviewInput,
    ImportRowCategory,
    ImportRowPublic,
    ImportRowsPage,
    SourceAcquiredAtUpdate,
)
from backend_core.imports.service import FileUploadDecision, ImportService, QueueDecision
from fastapi import APIRouter, Depends, File, Form, Query, Request, UploadFile, status
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
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
from app.http.responses import ErrorEnvelope, SuccessEnvelope, envelope

router = APIRouter(prefix="/api/v1/import-jobs", tags=["import-jobs"])
logger = logging.getLogger(__name__)
IMPORT_ROWS_QUERY_PARAMETERS = frozenset({"offset", "limit", "action", "category"})


def _error_responses(*status_codes: int) -> dict[int | str, dict[str, Any]]:
    """Describe the existing error envelope without changing runtime handling."""

    return {
        status_code: {
            "model": ErrorEnvelope,
            "description": "Error response",
        }
        for status_code in status_codes
    }


@overload
def _utc(value: datetime) -> datetime: ...


@overload
def _utc(value: None) -> None: ...


def _utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _file_public(record: ImportJobFileRecord) -> ImportJobFilePublic:
    occurrence = record.occurrence
    stored_file = record.stored_file
    return ImportJobFilePublic(
        id=occurrence.id,
        import_job_id=occurrence.import_job_id,
        stored_file_id=stored_file.id,
        position=occurrence.position,
        original_filename=occurrence.original_filename,
        declared_mime=occurrence.declared_mime,
        status=occurrence.status,
        source_acquired_at=_utc(occurrence.source_acquired_at),
        source_acquired_at_origin=occurrence.source_acquired_at_origin,
        source_acquired_at_confirmation_required=(
            occurrence.source_acquired_at_confirmation_required
        ),
        detected_type=stored_file.detected_type,
        detected_mime=stored_file.detected_mime,
        file_size=stored_file.size,
        sha256=stored_file.sha256,
        detected_fields=occurrence.detected_fields,
        field_mapping=occurrence.field_mapping,
        mapping_hash=occurrence.mapping_hash,
        raw_rows=occurrence.raw_rows,
        warning_rows=occurrence.warning_rows,
        error_rows=occurrence.error_rows,
        error_code=occurrence.error_code,
        error_message=occurrence.error_message,
        parse_task_id=occurrence.parse_task_id,
        parse_attempts=occurrence.parse_attempts,
        parse_started_at=_utc(occurrence.parse_started_at),
        parse_completed_at=_utc(occurrence.parse_completed_at),
        excluded_at=_utc(occurrence.excluded_at),
        created_at=_utc(occurrence.created_at),
        updated_at=_utc(occurrence.updated_at),
    )


def _upload_public(decision: FileUploadDecision) -> ImportJobFileUploadResult:
    return ImportJobFileUploadResult(
        file=_file_public(decision.record),
        idempotent=decision.idempotent,
    )


def _dispatch_public(decision: QueueDecision) -> ImportDispatchResult:
    return ImportDispatchResult(
        import_job_id=decision.job.id,
        status=decision.job.status,
        preview_revision=decision.job.preview_revision,
        task_id=decision.task_id,
        idempotent=decision.idempotent,
    )


def validate_import_rows_query(request: Request) -> None:
    """Reject ambiguous row filters before they reach the read service."""

    for name in IMPORT_ROWS_QUERY_PARAMETERS:
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
    if "action" in request.query_params and "category" in request.query_params:
        raise RequestValidationError(
            [
                {
                    "type": "value_error",
                    "loc": ("query", "category"),
                    "msg": "Value error, category and action are mutually exclusive",
                    "input": request.query_params.get("category"),
                    "ctx": {"error": ValueError("category and action are mutually exclusive")},
                }
            ]
        )


def _log_dispatch_failure(
    *,
    import_job_id: UUID,
    task_id: str,
) -> None:
    logger.warning(
        "import_task_immediate_dispatch_failed",
        extra={"import_job_id": str(import_job_id), "task_token": task_id},
    )


def _log_file_dispatch_failure(
    *,
    import_job_id: UUID,
    import_job_file_id: UUID,
    task_id: str,
) -> None:
    logger.warning(
        "import_file_task_immediate_dispatch_failed",
        extra={
            "import_job_id": str(import_job_id),
            "import_job_file_id": str(import_job_file_id),
            "task_token": task_id,
        },
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
    task_id = str(uuid4())

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
        _log_dispatch_failure(
            import_job_id=job.id,
            task_id=task_id,
        )
    return envelope(request, data=ImportJobPublic.model_validate(job))


@router.post(
    "/bulk",
    status_code=status.HTTP_201_CREATED,
    response_model=SuccessEnvelope[ImportJobPublic],
    responses=_error_responses(401, 403, 404, 409, 422),
)
async def create_bulk_import_job(
    payload: BulkImportJobCreate,
    request: Request,
    context: Annotated[AuthContext, Depends(require_import_mutation)],
    service: Annotated[ImportService, Depends(get_import_service)],
) -> dict[str, Any]:
    job = await service.create_bulk_import_job(
        context,
        collection_job_id=payload.collection_job_id,
        ip=get_client_ip(request),
        user_agent=get_user_agent(request),
        refresh_queue_id=payload.refresh_queue_id,
    )
    return envelope(request, data=ImportJobPublic.model_validate(job))


@router.post(
    "/{import_job_id}/files",
    status_code=status.HTTP_201_CREATED,
    response_model=SuccessEnvelope[ImportJobFileUploadResult],
    responses={
        status.HTTP_200_OK: {
            "model": SuccessEnvelope[ImportJobFileUploadResult],
            "description": "Idempotent upload replay",
        },
        **_error_responses(401, 403, 404, 409, 413, 415, 422),
    },
)
async def upload_bulk_import_file(
    import_job_id: UUID,
    request: Request,
    client_file_id: Annotated[str, Form(min_length=1, max_length=160)],
    file: Annotated[UploadFile, File()],
    context: Annotated[AuthContext, Depends(require_import_mutation)],
    service: Annotated[ImportService, Depends(get_import_service)],
    dispatcher: Annotated[ImportTaskDispatcher, Depends(get_import_task_dispatcher)],
    source_acquired_at: Annotated[datetime | None, Form()] = None,
) -> JSONResponse:
    task_id = str(uuid4())

    async def chunks() -> AsyncIterator[bytes]:
        while content := await file.read(1024 * 1024):
            yield content

    try:
        decision = await service.upload_import_job_file(
            context,
            import_job_id=import_job_id,
            client_file_id=client_file_id,
            filename=file.filename or "upload",
            declared_mime=file.content_type or "application/octet-stream",
            chunks=chunks(),
            source_acquired_at=source_acquired_at,
            parse_task_id=task_id,
            ip=get_client_ip(request),
            user_agent=get_user_agent(request),
        )
    finally:
        await file.close()
    if decision.should_dispatch and decision.task_id is not None:
        try:
            await dispatcher.parse_file(
                import_job_id,
                decision.record.occurrence.id,
                decision.task_id,
            )
        except Exception:
            _log_file_dispatch_failure(
                import_job_id=import_job_id,
                import_job_file_id=decision.record.occurrence.id,
                task_id=decision.task_id,
            )
    response_status = status.HTTP_200_OK if decision.idempotent else status.HTTP_201_CREATED
    return JSONResponse(
        status_code=response_status,
        content=jsonable_encoder(envelope(request, data=_upload_public(decision))),
    )


@router.get(
    "/{import_job_id}/files",
    response_model=SuccessEnvelope[list[ImportJobFilePublic]],
    responses=_error_responses(401, 404, 422),
)
async def list_bulk_import_files(
    import_job_id: UUID,
    request: Request,
    context: Annotated[AuthContext, Depends(require_auth)],
    service: Annotated[ImportService, Depends(get_import_service)],
) -> dict[str, Any]:
    records = await service.list_import_job_files(context, import_job_id)
    return envelope(request, data=[_file_public(record) for record in records])


@router.patch(
    "/{import_job_id}/files/{import_job_file_id}",
    response_model=SuccessEnvelope[ImportJobFilePublic],
    responses=_error_responses(401, 403, 404, 409, 422),
)
async def update_bulk_import_file(
    import_job_id: UUID,
    import_job_file_id: UUID,
    payload: SourceAcquiredAtUpdate,
    request: Request,
    context: Annotated[AuthContext, Depends(require_import_mutation)],
    service: Annotated[ImportService, Depends(get_import_service)],
) -> dict[str, Any]:
    record = await service.update_import_job_file_source_acquired_at(
        context,
        import_job_id,
        import_job_file_id,
        payload.source_acquired_at,
        ip=get_client_ip(request),
        user_agent=get_user_agent(request),
    )
    return envelope(request, data=_file_public(record))


@router.put(
    "/{import_job_id}/files/{import_job_file_id}/mapping",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=SuccessEnvelope[ImportJobFilePublic],
    responses={
        status.HTTP_200_OK: {
            "model": SuccessEnvelope[ImportJobFilePublic],
            "description": "Idempotent mapping request",
        },
        **_error_responses(401, 403, 404, 409, 422),
    },
)
async def update_bulk_import_file_mapping(
    import_job_id: UUID,
    import_job_file_id: UUID,
    payload: ImportMappingUpdate,
    request: Request,
    context: Annotated[AuthContext, Depends(require_import_mutation)],
    service: Annotated[ImportService, Depends(get_import_service)],
    dispatcher: Annotated[ImportTaskDispatcher, Depends(get_import_task_dispatcher)],
) -> JSONResponse:
    task_id = str(uuid4())
    decision = await service.update_import_job_file_mapping(
        context,
        import_job_id,
        import_job_file_id,
        payload.mapping,
        task_id,
        ip=get_client_ip(request),
        user_agent=get_user_agent(request),
    )
    if decision.should_dispatch:
        try:
            await dispatcher.parse_file(import_job_id, import_job_file_id, decision.task_id)
        except Exception:
            _log_file_dispatch_failure(
                import_job_id=import_job_id,
                import_job_file_id=import_job_file_id,
                task_id=decision.task_id,
            )
    response_status = status.HTTP_200_OK if decision.idempotent else status.HTTP_202_ACCEPTED
    return JSONResponse(
        status_code=response_status,
        content=jsonable_encoder(envelope(request, data=_file_public(decision.record))),
    )


@router.post(
    "/{import_job_id}/files/{import_job_file_id}/retry",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=SuccessEnvelope[ImportJobFilePublic],
    responses={
        status.HTTP_200_OK: {
            "model": SuccessEnvelope[ImportJobFilePublic],
            "description": "Idempotent retry request",
        },
        **_error_responses(401, 403, 404, 409, 422),
    },
)
async def retry_bulk_import_file(
    import_job_id: UUID,
    import_job_file_id: UUID,
    request: Request,
    context: Annotated[AuthContext, Depends(require_import_mutation)],
    service: Annotated[ImportService, Depends(get_import_service)],
    dispatcher: Annotated[ImportTaskDispatcher, Depends(get_import_task_dispatcher)],
) -> JSONResponse:
    task_id = str(uuid4())
    decision = await service.retry_import_job_file(
        context,
        import_job_id,
        import_job_file_id,
        task_id,
        ip=get_client_ip(request),
        user_agent=get_user_agent(request),
    )
    if decision.should_dispatch:
        try:
            await dispatcher.parse_file(import_job_id, import_job_file_id, decision.task_id)
        except Exception:
            _log_file_dispatch_failure(
                import_job_id=import_job_id,
                import_job_file_id=import_job_file_id,
                task_id=decision.task_id,
            )
    response_status = status.HTTP_200_OK if decision.idempotent else status.HTTP_202_ACCEPTED
    return JSONResponse(
        status_code=response_status,
        content=jsonable_encoder(envelope(request, data=_file_public(decision.record))),
    )


@router.post(
    "/{import_job_id}/files/{import_job_file_id}/exclude",
    response_model=SuccessEnvelope[ImportJobFilePublic],
    responses=_error_responses(401, 403, 404, 409, 422),
)
async def exclude_bulk_import_file(
    import_job_id: UUID,
    import_job_file_id: UUID,
    request: Request,
    context: Annotated[AuthContext, Depends(require_import_mutation)],
    service: Annotated[ImportService, Depends(get_import_service)],
) -> dict[str, Any]:
    record = await service.exclude_import_job_file(
        context,
        import_job_id,
        import_job_file_id,
        ip=get_client_ip(request),
        user_agent=get_user_agent(request),
    )
    return envelope(request, data=_file_public(record))


@router.get(
    "/{import_job_id}",
    response_model=SuccessEnvelope[ImportJobPublic],
    responses=_error_responses(401, 403, 404, 422),
)
async def get_import_job(
    import_job_id: UUID,
    request: Request,
    context: Annotated[AuthContext, Depends(require_auth)],
    service: Annotated[ImportService, Depends(get_import_service)],
) -> dict[str, Any]:
    job = await service.get_import_job(context, import_job_id)
    return envelope(request, data=ImportJobPublic.model_validate(job))


@router.get(
    "/{import_job_id}/rows",
    response_model=SuccessEnvelope[ImportRowsPage],
    responses=_error_responses(401, 403, 404, 409, 422),
    dependencies=[Depends(validate_import_rows_query)],
)
async def list_import_rows(
    import_job_id: UUID,
    request: Request,
    context: Annotated[AuthContext, Depends(require_auth)],
    service: Annotated[ImportService, Depends(get_import_service)],
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    action: Annotated[ImportRowAction | None, Query()] = None,
    category: Annotated[ImportRowCategory | None, Query()] = None,
) -> dict[str, Any]:
    rows, total = await service.list_import_rows(
        context,
        import_job_id,
        offset=offset,
        limit=limit,
        action=action,
        category=category,
    )
    page = ImportRowsPage(
        items=[
            row if isinstance(row, ImportRowPublic) else ImportRowPublic.model_validate(row)
            for row in rows
        ],
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
    task_id = str(uuid4())
    decision = await service.request_mapping_preview(
        context,
        import_job_id,
        payload.mapping,
        task_id,
        ip=get_client_ip(request),
        user_agent=get_user_agent(request),
    )
    try:
        await dispatcher.parse(import_job_id, decision.task_id)
    except Exception:
        _log_dispatch_failure(
            import_job_id=import_job_id,
            task_id=decision.task_id,
        )
    return envelope(request, data=_dispatch_public(decision))


@router.post(
    "/{import_job_id}/preview",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=SuccessEnvelope[ImportDispatchResult],
    responses={
        status.HTTP_200_OK: {
            "model": SuccessEnvelope[ImportDispatchResult],
            "description": "Idempotent Preview request replay",
        },
        **_error_responses(401, 403, 404, 409, 422),
    },
)
async def regenerate_preview(
    import_job_id: UUID,
    request: Request,
    context: Annotated[AuthContext, Depends(require_import_mutation)],
    service: Annotated[ImportService, Depends(get_import_service)],
    dispatcher: Annotated[ImportTaskDispatcher, Depends(get_import_task_dispatcher)],
    payload: ImportPreviewInput | None = None,
) -> JSONResponse:
    task_id = str(uuid4())
    decision = await service.request_preview(
        context,
        import_job_id,
        task_id,
        ip=get_client_ip(request),
        user_agent=get_user_agent(request),
        rebuild=payload.rebuild if payload is not None else False,
    )
    if decision.should_dispatch:
        try:
            if decision.task_kind == "bulk_preview":
                await dispatcher.preview(import_job_id, decision.task_id)
            else:
                await dispatcher.parse(import_job_id, decision.task_id)
        except Exception:
            _log_dispatch_failure(
                import_job_id=import_job_id,
                task_id=decision.task_id,
            )
    response_status = status.HTTP_200_OK if decision.idempotent else status.HTTP_202_ACCEPTED
    return JSONResponse(
        status_code=response_status,
        content=jsonable_encoder(envelope(request, data=_dispatch_public(decision))),
    )


@router.post(
    "/{import_job_id}/retry",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=SuccessEnvelope[ImportDispatchResult],
    responses={
        status.HTTP_200_OK: {
            "model": SuccessEnvelope[ImportDispatchResult],
            "description": "Idempotent failed Preview or Confirm retry replay",
        },
        **_error_responses(401, 403, 404, 409, 422),
    },
)
async def retry_import_stage(
    import_job_id: UUID,
    request: Request,
    context: Annotated[AuthContext, Depends(require_import_mutation)],
    service: Annotated[ImportService, Depends(get_import_service)],
    dispatcher: Annotated[ImportTaskDispatcher, Depends(get_import_task_dispatcher)],
) -> JSONResponse:
    """Retry exactly the PostgreSQL-persisted failed Preview or Confirm stage."""

    task_id = str(uuid4())
    decision = await service.retry_import_job(
        context,
        import_job_id,
        task_id,
        ip=get_client_ip(request),
        user_agent=get_user_agent(request),
    )
    if decision.should_dispatch:
        try:
            if decision.task_kind == "confirm":
                if decision.job.confirmed_revision is None:
                    raise RuntimeError("Confirm retry lost its persisted revision")
                await dispatcher.confirm(
                    import_job_id,
                    decision.job.confirmed_revision,
                    decision.task_id,
                )
            else:
                await dispatcher.preview(import_job_id, decision.task_id)
        except Exception:
            _log_dispatch_failure(
                import_job_id=import_job_id,
                task_id=decision.task_id,
            )
    response_status = status.HTTP_200_OK if decision.idempotent else status.HTTP_202_ACCEPTED
    return JSONResponse(
        status_code=response_status,
        content=jsonable_encoder(envelope(request, data=_dispatch_public(decision))),
    )


@router.post(
    "/{import_job_id}/confirm",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=SuccessEnvelope[ImportDispatchResult],
    responses={
        status.HTTP_200_OK: {
            "model": SuccessEnvelope[ImportDispatchResult],
            "description": "Idempotent Confirm request replay",
        },
        **_error_responses(401, 403, 404, 409, 422),
    },
)
async def confirm_import(
    import_job_id: UUID,
    payload: ImportConfirmInput,
    request: Request,
    context: Annotated[AuthContext, Depends(require_import_mutation)],
    service: Annotated[ImportService, Depends(get_import_service)],
    dispatcher: Annotated[ImportTaskDispatcher, Depends(get_import_task_dispatcher)],
) -> JSONResponse:
    task_id = str(uuid4())
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
            await dispatcher.confirm(
                import_job_id,
                payload.preview_revision,
                decision.task_id,
            )
        except Exception:
            _log_dispatch_failure(
                import_job_id=import_job_id,
                task_id=decision.task_id,
            )
    response_status = status.HTTP_200_OK if decision.idempotent else status.HTTP_202_ACCEPTED
    return JSONResponse(
        status_code=response_status,
        content=jsonable_encoder(envelope(request, data=_dispatch_public(decision))),
    )


@router.post(
    "/{import_job_id}/cancel",
    response_model=SuccessEnvelope[ImportJobPublic],
    responses=_error_responses(401, 403, 404, 409, 422),
)
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
