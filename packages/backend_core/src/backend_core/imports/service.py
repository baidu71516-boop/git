"""Authenticated collection/import orchestration used by the HTTP layer."""

import re
from collections.abc import AsyncIterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import PurePosixPath
from typing import Protocol
from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from backend_core.audit.enums import AuditAction, AuditResult
from backend_core.audit.repository import AuditRepository
from backend_core.auth.enums import Role
from backend_core.auth.service import AuthContext
from backend_core.imports.enums import (
    CollectionJobStatus,
    ImportJobFailedStage,
    ImportJobFileStatus,
    ImportJobStatus,
    ImportRowAction,
    SourceAcquiredAtOrigin,
    StoredFileType,
)
from backend_core.imports.errors import ImportDomainError
from backend_core.imports.hashing import hash_document
from backend_core.imports.mappings import validate_mapping
from backend_core.imports.models import (
    CollectionJob,
    ImportJob,
    ImportJobFile,
    ImportRow,
    StoredImportFile,
)
from backend_core.imports.parsers import ParserLimits, validate_upload_type
from backend_core.imports.repository import ImportRepository
from backend_core.imports.schemas import CollectionJobCreate
from backend_core.imports.state_machine import transition_import_job
from backend_core.imports.storage import StorageAdapter

CONTROL_CHARACTERS = re.compile(r"[\x00-\x1f\x7f]")


class Clock(Protocol):
    def now(self) -> datetime: ...


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(UTC)


@dataclass(frozen=True)
class QueueDecision:
    job: ImportJob
    should_dispatch: bool
    idempotent: bool = False


def sanitize_original_filename(filename: str, suffix: str) -> str:
    normalized = filename.replace("\\", "/")
    basename = PurePosixPath(normalized).name
    sanitized = CONTROL_CHARACTERS.sub("", basename).strip()
    if not sanitized:
        sanitized = f"upload{suffix}"
    return sanitized[:255]


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


class ImportService:
    """Owns API-facing state transitions; parser/matcher logic lives elsewhere."""

    def __init__(
        self,
        session: AsyncSession,
        storage: StorageAdapter,
        *,
        parser_limits: ParserLimits,
        max_file_bytes: int,
        retention_days: int,
        clock: Clock | None = None,
    ) -> None:
        self.session = session
        self.storage = storage
        self.repository = ImportRepository(session)
        self.audit = AuditRepository(session)
        self.parser_limits = parser_limits
        self.max_file_bytes = max_file_bytes
        self.retention_days = retention_days
        self.clock = clock or SystemClock()

    @staticmethod
    def _require_mutation(context: AuthContext) -> None:
        if context.operator is None:
            raise ImportDomainError(
                "OPERATOR_REQUIRED", "Select an operator first", status_code=409
            )
        if context.role == Role.VIEWER:
            raise ImportDomainError(
                "PERMISSION_DENIED", "Viewer role is read-only", status_code=403
            )

    @staticmethod
    def _require_scope(context: AuthContext, department_id: UUID) -> None:
        if context.role != Role.SUPER_ADMIN and context.department.id != department_id:
            raise ImportDomainError(
                "PERMISSION_DENIED", "Department data scope denied", status_code=403
            )

    @staticmethod
    def _operator_id(context: AuthContext) -> UUID:
        if context.operator is None:
            raise ImportDomainError(
                "OPERATOR_REQUIRED", "Select an operator first", status_code=409
            )
        return context.operator.id

    async def create_collection_job(
        self,
        context: AuthContext,
        payload: CollectionJobCreate,
    ) -> CollectionJob:
        self._require_mutation(context)
        job = CollectionJob(
            name=payload.name.strip(),
            industry=payload.industry.strip(),
            subdirection=payload.subdirection.strip() if payload.subdirection else None,
            purpose=payload.purpose.strip(),
            target_action=payload.target_action.strip(),
            follower_min=payload.follower_min,
            follower_max=payload.follower_max,
            target_count=payload.target_count,
            department_id=context.department.id,
            owner_operator_id=self._operator_id(context),
            source_type=payload.source_type,
            status=CollectionJobStatus.ACTIVE,
            notes=payload.notes.strip() if payload.notes else None,
        )
        self.session.add(job)
        await self.session.commit()
        return job

    async def list_collection_jobs(self, context: AuthContext) -> list[CollectionJob]:
        department_id = None if context.role == Role.SUPER_ADMIN else context.department.id
        return await self.repository.list_collection_jobs(department_id)

    async def get_collection_job(
        self, context: AuthContext, collection_job_id: UUID
    ) -> CollectionJob:
        job = await self.repository.get_collection_job(collection_job_id)
        if job is None:
            raise ImportDomainError(
                "COLLECTION_JOB_NOT_FOUND", "Collection job not found", status_code=404
            )
        self._require_scope(context, job.department_id)
        return job

    async def create_import_job(
        self,
        context: AuthContext,
        *,
        collection_job_id: UUID,
        filename: str,
        declared_mime: str,
        chunks: AsyncIterable[bytes],
        parse_task_id: str,
        ip: str,
        user_agent: str,
    ) -> ImportJob:
        self._require_mutation(context)
        collection = await self.get_collection_job(context, collection_job_id)
        suffix = PurePosixPath(filename.replace("\\", "/")).suffix.lower()
        stored_object = await self.storage.store(
            chunks, suffix=suffix, max_bytes=self.max_file_bytes
        )
        owns_storage_object = True
        try:
            content = await self.storage.read(
                stored_object.storage_key,
                expected_size=stored_object.size,
                expected_sha256=stored_object.sha256,
            )
            detected_type = validate_upload_type(
                content,
                suffix=suffix,
                declared_mime=declared_mime,
                limits=self.parser_limits,
            )
            detected_mime = (
                "text/csv"
                if detected_type == StoredFileType.CSV
                else "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            )
            now = self.clock.now()
            expires_at = now + timedelta(days=self.retention_days)
            stored_file = await self.repository.get_stored_file_by_sha256(stored_object.sha256)
            if stored_file is None:
                candidate = StoredImportFile(
                    sha256=stored_object.sha256,
                    storage_key=stored_object.storage_key,
                    size=stored_object.size,
                    detected_type=detected_type,
                    detected_mime=detected_mime,
                    encoding=None,
                    parse_metadata=None,
                    expires_at=expires_at,
                )
                try:
                    async with self.session.begin_nested():
                        self.session.add(candidate)
                        await self.session.flush()
                    stored_file = candidate
                except IntegrityError:
                    stored_file = await self.repository.get_stored_file_by_sha256(
                        stored_object.sha256
                    )
                    if stored_file is None:
                        raise
            if stored_file.storage_key != stored_object.storage_key:
                await self.storage.delete(stored_object.storage_key)
                owns_storage_object = False
            if _as_utc(stored_file.expires_at) < _as_utc(expires_at):
                stored_file.expires_at = expires_at

            import_job = ImportJob(
                collection_job_id=collection.id,
                department_id=collection.department_id,
                operator_id=self._operator_id(context),
                stored_file_id=stored_file.id,
                original_filename=sanitize_original_filename(filename, suffix),
                mime_type=declared_mime[:160] or "application/octet-stream",
                file_size=stored_object.size,
                sha256=stored_object.sha256,
                source_type=collection.source_type,
                status=ImportJobStatus.UPLOADED,
                preview_revision=0,
                parse_task_id=parse_task_id,
            )
            self.session.add(import_job)
            await self.session.flush()
            self.session.add(
                ImportJobFile(
                    import_job_id=import_job.id,
                    stored_file_id=stored_file.id,
                    position=1,
                    original_filename=import_job.original_filename or "upload",
                    declared_mime=import_job.mime_type,
                    status=ImportJobFileStatus.UPLOADED,
                    source_acquired_at=None,
                    source_acquired_at_origin=SourceAcquiredAtOrigin.LEGACY_UNKNOWN,
                    parse_task_id=parse_task_id,
                )
            )
            await self.session.flush()
            self.audit.add(
                action=AuditAction.IMPORT_FILE_UPLOADED,
                result=AuditResult.SUCCESS,
                department_id=collection.department_id,
                operator_id=self._operator_id(context),
                ip=ip,
                user_agent=user_agent,
                entity_type="import_job",
                entity_id=import_job.id,
                after={
                    "collection_job_id": str(collection.id),
                    "sha256": stored_object.sha256,
                    "size": stored_object.size,
                    "detected_type": detected_type.value,
                    "storage_reused": stored_file.storage_key != stored_object.storage_key,
                },
            )
            await self.session.commit()
            owns_storage_object = False
            return import_job
        except BaseException:
            await self.session.rollback()
            if owns_storage_object:
                await self.storage.delete(stored_object.storage_key)
            raise

    async def list_import_jobs(self, context: AuthContext) -> list[ImportJob]:
        department_id = None if context.role == Role.SUPER_ADMIN else context.department.id
        return await self.repository.list_import_jobs(department_id)

    async def get_import_job(self, context: AuthContext, import_job_id: UUID) -> ImportJob:
        job = await self.repository.get_import_job(import_job_id)
        if job is None:
            raise ImportDomainError("IMPORT_JOB_NOT_FOUND", "Import job not found", status_code=404)
        self._require_scope(context, job.department_id)
        return job

    async def list_import_rows(
        self,
        context: AuthContext,
        import_job_id: UUID,
        *,
        offset: int,
        limit: int,
        action: ImportRowAction | None,
    ) -> tuple[list[ImportRow], int]:
        await self.get_import_job(context, import_job_id)
        rows, total = await self.repository.list_import_rows(
            import_job_id, offset=offset, limit=limit, action=action
        )
        return list(rows), total

    async def request_mapping_preview(
        self,
        context: AuthContext,
        import_job_id: UUID,
        mapping: dict[str, str],
        task_id: str,
        *,
        ip: str,
        user_agent: str,
    ) -> QueueDecision:
        self._require_mutation(context)
        job = await self.repository.get_import_job(import_job_id, for_update=True)
        if job is None:
            raise ImportDomainError("IMPORT_JOB_NOT_FOUND", "Import job not found", status_code=404)
        self._require_scope(context, job.department_id)
        occurrence = await self.repository.get_legacy_import_job_file(job, for_update=True)
        if job.detected_fields is None:
            raise ImportDomainError(
                "MAPPING_UNAVAILABLE", "File fields have not been detected", status_code=409
            )
        validated = validate_mapping(job.detected_fields, mapping)
        if job.status not in {
            ImportJobStatus.MAPPING_REQUIRED,
            ImportJobStatus.PREVIEW_READY,
            ImportJobStatus.PREVIEW_STALE,
        }:
            raise ImportDomainError(
                "INVALID_STATE_TRANSITION",
                "Mapping cannot be changed in this state",
                status_code=409,
            )
        transition_import_job(job, ImportJobStatus.PREVIEWING)
        job.field_mapping = validated
        job.mapping_hash = hash_document(validated)
        job.parse_task_id = task_id
        job.preview_summary = None
        job.error_code = None
        job.error_message = None
        occurrence.detected_fields = list(job.detected_fields)
        occurrence.field_mapping = validated
        occurrence.mapping_hash = job.mapping_hash
        occurrence.status = ImportJobFileStatus.PARSING
        occurrence.parse_task_id = task_id
        occurrence.error_code = None
        occurrence.error_message = None
        self.audit.add(
            action=AuditAction.IMPORT_MAPPING_UPDATED,
            result=AuditResult.SUCCESS,
            department_id=job.department_id,
            operator_id=self._operator_id(context),
            ip=ip,
            user_agent=user_agent,
            entity_type="import_job",
            entity_id=job.id,
            after={"next_preview_revision": job.preview_revision + 1},
        )
        await self.session.commit()
        return QueueDecision(job=job, should_dispatch=True)

    async def request_preview(
        self,
        context: AuthContext,
        import_job_id: UUID,
        task_id: str,
    ) -> QueueDecision:
        self._require_mutation(context)
        job = await self.repository.get_import_job(import_job_id, for_update=True)
        if job is None:
            raise ImportDomainError("IMPORT_JOB_NOT_FOUND", "Import job not found", status_code=404)
        self._require_scope(context, job.department_id)
        occurrence = await self.repository.get_legacy_import_job_file(job, for_update=True)
        if not job.field_mapping or not job.mapping_hash:
            raise ImportDomainError(
                "MAPPING_REQUIRED", "A valid mapping is required", status_code=409
            )
        if job.status not in {
            ImportJobStatus.MAPPING_REQUIRED,
            ImportJobStatus.PREVIEW_READY,
            ImportJobStatus.PREVIEW_STALE,
        }:
            raise ImportDomainError(
                "INVALID_STATE_TRANSITION",
                "Preview cannot be generated in this state",
                status_code=409,
            )
        transition_import_job(job, ImportJobStatus.PREVIEWING)
        job.parse_task_id = task_id
        job.preview_summary = None
        job.error_code = None
        job.error_message = None
        occurrence.field_mapping = dict(job.field_mapping)
        occurrence.mapping_hash = job.mapping_hash
        occurrence.status = ImportJobFileStatus.PARSING
        occurrence.parse_task_id = task_id
        occurrence.error_code = None
        occurrence.error_message = None
        await self.session.commit()
        return QueueDecision(job=job, should_dispatch=True)

    async def request_confirm(
        self,
        context: AuthContext,
        import_job_id: UUID,
        preview_revision: int,
        task_id: str,
        *,
        ip: str,
        user_agent: str,
    ) -> QueueDecision:
        self._require_mutation(context)
        job = await self.repository.get_import_job(import_job_id, for_update=True)
        if job is None:
            raise ImportDomainError("IMPORT_JOB_NOT_FOUND", "Import job not found", status_code=404)
        self._require_scope(context, job.department_id)
        if job.status == ImportJobStatus.COMPLETED:
            if job.confirmed_revision == preview_revision:
                await self.session.commit()
                return QueueDecision(job=job, should_dispatch=False, idempotent=True)
            raise ImportDomainError(
                "PREVIEW_STALE",
                "Confirmed revision does not match the completed import",
                status_code=409,
            )
        if job.status in {ImportJobStatus.CONFIRM_QUEUED, ImportJobStatus.IMPORTING}:
            if job.confirmed_revision == preview_revision:
                await self.session.commit()
                return QueueDecision(job=job, should_dispatch=False, idempotent=True)
            raise ImportDomainError(
                "PREVIEW_STALE",
                "Another preview revision is already being confirmed",
                status_code=409,
            )
        if job.status != ImportJobStatus.PREVIEW_READY:
            raise ImportDomainError(
                "INVALID_STATE_TRANSITION", "Only a ready preview can be confirmed", status_code=409
            )
        if job.preview_revision != preview_revision:
            self.audit.add(
                action=AuditAction.IMPORT_PREVIEW_STALE,
                result=AuditResult.DENIED,
                department_id=job.department_id,
                operator_id=self._operator_id(context),
                ip=ip,
                user_agent=user_agent,
                entity_type="import_job",
                entity_id=job.id,
                after={
                    "reason": "revision_mismatch",
                    "requested_revision": preview_revision,
                    "current_revision": job.preview_revision,
                },
            )
            await self.session.commit()
            raise ImportDomainError(
                "PREVIEW_STALE",
                "Preview revision is stale; regenerate or confirm the current preview",
                status_code=409,
            )
        transition_import_job(job, ImportJobStatus.CONFIRM_QUEUED)
        job.confirmed_revision = preview_revision
        job.confirmed_at = self.clock.now()
        job.confirm_task_id = task_id
        self.audit.add(
            action=AuditAction.IMPORT_CONFIRM_REQUESTED,
            result=AuditResult.SUCCESS,
            department_id=job.department_id,
            operator_id=self._operator_id(context),
            ip=ip,
            user_agent=user_agent,
            entity_type="import_job",
            entity_id=job.id,
            after={"preview_revision": preview_revision},
        )
        await self.session.commit()
        return QueueDecision(job=job, should_dispatch=True)

    async def cancel(
        self,
        context: AuthContext,
        import_job_id: UUID,
        *,
        ip: str,
        user_agent: str,
    ) -> ImportJob:
        self._require_mutation(context)
        job = await self.repository.get_import_job(import_job_id, for_update=True)
        if job is None:
            raise ImportDomainError("IMPORT_JOB_NOT_FOUND", "Import job not found", status_code=404)
        self._require_scope(context, job.department_id)
        occurrence = await self.repository.get_legacy_import_job_file(job, for_update=True)
        transition_import_job(job, ImportJobStatus.CANCELLED)
        occurrence.status = ImportJobFileStatus.EXCLUDED
        self.audit.add(
            action=AuditAction.IMPORT_CANCELLED,
            result=AuditResult.SUCCESS,
            department_id=job.department_id,
            operator_id=self._operator_id(context),
            ip=ip,
            user_agent=user_agent,
            entity_type="import_job",
            entity_id=job.id,
            after={"preview_revision": job.preview_revision},
        )
        await self.session.commit()
        return job

    async def mark_dispatch_failed(
        self,
        context: AuthContext,
        import_job_id: UUID,
        task_id: str,
        *,
        ip: str,
        user_agent: str,
    ) -> None:
        job = await self.repository.get_import_job(import_job_id, for_update=True)
        if job is None:
            await self.session.rollback()
            return
        self._require_scope(context, job.department_id)
        expected = task_id in {job.parse_task_id, job.confirm_task_id}
        if not expected or job.status not in {
            ImportJobStatus.UPLOADED,
            ImportJobStatus.PREVIEWING,
            ImportJobStatus.CONFIRM_QUEUED,
        }:
            await self.session.rollback()
            return
        if task_id == job.parse_task_id:
            occurrence = await self.repository.get_legacy_import_job_file(job, for_update=True)
            occurrence.status = ImportJobFileStatus.FAILED
            occurrence.error_code = "TASK_DISPATCH_FAILED"
            occurrence.error_message = "Background task could not be queued"
            job.failed_stage = ImportJobFailedStage.PREVIEW
        else:
            job.failed_stage = ImportJobFailedStage.CONFIRM
        transition_import_job(job, ImportJobStatus.FAILED)
        job.error_code = "TASK_DISPATCH_FAILED"
        job.error_message = "Background task could not be queued"
        self.audit.add(
            action=AuditAction.IMPORT_FAILED,
            result=AuditResult.FAILED,
            department_id=job.department_id,
            operator_id=context.operator.id if context.operator else None,
            ip=ip,
            user_agent=user_agent,
            entity_type="import_job",
            entity_id=job.id,
            after={"reason": "task_dispatch_failed"},
        )
        await self.session.commit()
