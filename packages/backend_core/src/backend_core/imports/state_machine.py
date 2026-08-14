"""Single import-job state machine shared by HTTP and workers."""

from backend_core.imports.enums import ImportJobFailedStage, ImportJobStatus
from backend_core.imports.errors import ImportDomainError
from backend_core.imports.models import ImportJob

ALLOWED_TRANSITIONS: dict[ImportJobStatus, frozenset[ImportJobStatus]] = {
    ImportJobStatus.DRAFT: frozenset({ImportJobStatus.PREVIEWING, ImportJobStatus.CANCELLED}),
    ImportJobStatus.UPLOADED: frozenset(
        {ImportJobStatus.PARSING, ImportJobStatus.FAILED, ImportJobStatus.CANCELLED}
    ),
    ImportJobStatus.PARSING: frozenset(
        {
            ImportJobStatus.MAPPING_REQUIRED,
            ImportJobStatus.PREVIEWING,
            ImportJobStatus.FAILED,
            ImportJobStatus.CANCELLED,
        }
    ),
    ImportJobStatus.MAPPING_REQUIRED: frozenset(
        {ImportJobStatus.PREVIEWING, ImportJobStatus.FAILED, ImportJobStatus.CANCELLED}
    ),
    ImportJobStatus.PREVIEWING: frozenset(
        {ImportJobStatus.PREVIEW_READY, ImportJobStatus.FAILED, ImportJobStatus.CANCELLED}
    ),
    ImportJobStatus.PREVIEW_READY: frozenset(
        {
            ImportJobStatus.PREVIEWING,
            ImportJobStatus.PREVIEW_STALE,
            ImportJobStatus.CONFIRM_QUEUED,
            ImportJobStatus.FAILED,
            ImportJobStatus.CANCELLED,
        }
    ),
    ImportJobStatus.PREVIEW_STALE: frozenset(
        {ImportJobStatus.PREVIEWING, ImportJobStatus.FAILED, ImportJobStatus.CANCELLED}
    ),
    ImportJobStatus.CONFIRM_QUEUED: frozenset(
        {
            ImportJobStatus.IMPORTING,
            ImportJobStatus.PREVIEW_STALE,
            ImportJobStatus.FAILED,
            ImportJobStatus.CANCELLED,
        }
    ),
    ImportJobStatus.IMPORTING: frozenset(
        {
            ImportJobStatus.COMPLETED,
            ImportJobStatus.PREVIEW_STALE,
            ImportJobStatus.FAILED,
        }
    ),
    ImportJobStatus.COMPLETED: frozenset(),
    ImportJobStatus.FAILED: frozenset({ImportJobStatus.PREVIEWING, ImportJobStatus.CONFIRM_QUEUED}),
    ImportJobStatus.CANCELLED: frozenset(),
}


def transition_import_job(job: ImportJob, target: ImportJobStatus) -> None:
    if (
        job.status is ImportJobStatus.FAILED
        and target is ImportJobStatus.PREVIEWING
        and job.failed_stage is not ImportJobFailedStage.PREVIEW
    ):
        raise ImportDomainError(
            "INVALID_STATE_TRANSITION",
            "Only a failed Preview stage can be retried as Preview",
            status_code=409,
        )
    if (
        job.status is ImportJobStatus.FAILED
        and target is ImportJobStatus.CONFIRM_QUEUED
        and job.failed_stage is not ImportJobFailedStage.CONFIRM
    ):
        raise ImportDomainError(
            "INVALID_STATE_TRANSITION",
            "Only a failed Confirm stage can be retried as Confirm",
            status_code=409,
        )
    if target not in ALLOWED_TRANSITIONS[job.status]:
        raise ImportDomainError(
            "INVALID_STATE_TRANSITION",
            f"Import job cannot transition from {job.status.value} to {target.value}",
            status_code=409,
        )
    job.status = target
