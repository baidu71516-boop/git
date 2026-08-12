"""Authenticated collection/import orchestration used by the HTTP layer."""

import asyncio
import hashlib
import logging
import re
from collections.abc import AsyncIterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import PurePosixPath
from typing import Protocol, cast
from uuid import UUID

from sqlalchemy import inspect as sa_inspect
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

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
    ImportJobFileClientId,
    ImportRow,
    StoredImportFile,
)
from backend_core.imports.parsers import ParserLimits, validate_upload_type
from backend_core.imports.repository import ImportJobFileRecord, ImportRepository
from backend_core.imports.schemas import CollectionJobCreate
from backend_core.imports.state_machine import transition_import_job
from backend_core.imports.storage import StorageAdapter

CONTROL_CHARACTERS = re.compile(r"[\x00-\x1f\x7f]")
logger = logging.getLogger(__name__)


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


@dataclass(frozen=True)
class FileUploadDecision:
    record: ImportJobFileRecord
    idempotent: bool

    @property
    def source_acquired_at_confirmation_required(self) -> bool:
        return self.record.occurrence.source_acquired_at_confirmation_required


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
        max_batch_files: int = 20,
        max_batch_bytes: int = 100 * 1024 * 1024,
        source_acquired_clock_skew_seconds: int = 300,
        clock: Clock | None = None,
    ) -> None:
        self.session = session
        self.storage = storage
        self.repository = ImportRepository(session)
        self.audit = AuditRepository(session)
        self.parser_limits = parser_limits
        self.max_file_bytes = max_file_bytes
        self.retention_days = retention_days
        self.max_batch_files = max_batch_files
        self.max_batch_bytes = max_batch_bytes
        self.source_acquired_clock_skew_seconds = source_acquired_clock_skew_seconds
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
        identity = sa_inspect(context.department).identity
        if context.role is not Role.SUPER_ADMIN and (
            identity is None or identity[0] != department_id
        ):
            raise ImportDomainError(
                "PERMISSION_DENIED", "Department data scope denied", status_code=403
            )

    @staticmethod
    def _file_scope_visible(context: AuthContext, department_id: UUID) -> bool:
        identity = sa_inspect(context.department).identity
        return context.role is Role.SUPER_ADMIN or (
            identity is not None and identity[0] == department_id
        )

    async def _get_file_scoped_import_job(
        self,
        context: AuthContext,
        import_job_id: UUID,
        *,
        for_update: bool = False,
    ) -> ImportJob:
        job = await self.repository.get_import_job(import_job_id, for_update=for_update)
        if job is None or not self._file_scope_visible(context, job.department_id):
            raise ImportDomainError("IMPORT_JOB_NOT_FOUND", "Import job not found", status_code=404)
        return job

    @staticmethod
    def _operator_id(context: AuthContext) -> UUID:
        if context.operator is None:
            raise ImportDomainError(
                "OPERATOR_REQUIRED", "Select an operator first", status_code=409
            )
        identity = sa_inspect(context.operator).identity
        if identity is None:
            raise ImportDomainError(
                "OPERATOR_REQUIRED", "Select an operator first", status_code=409
            )
        return cast(UUID, identity[0])

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
        commit_attempted = False
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
                    if (
                        self.session.bind is not None
                        and self.session.bind.dialect.name == "postgresql"
                    ):
                        async with self.session.begin_nested():
                            self.session.add(candidate)
                            await self.session.flush()
                    else:
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
                await self._delete_candidate_safely(stored_object.storage_key)
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
            commit_attempted = True
            await self._commit_upload_transaction()
            owns_storage_object = False
            return import_job
        except BaseException:
            try:
                await self.session.rollback()
            except BaseException:
                logger.warning("legacy_import_upload_transaction_rollback_failed")
            if owns_storage_object:
                should_delete = True
                if commit_attempted:
                    committed = await self._candidate_is_committed(
                        sha256=stored_object.sha256,
                        storage_key=stored_object.storage_key,
                    )
                    should_delete = committed is False
                if should_delete:
                    await self._delete_candidate_safely(stored_object.storage_key)
            raise

    async def create_bulk_import_job(
        self,
        context: AuthContext,
        *,
        collection_job_id: UUID,
        ip: str,
        user_agent: str,
    ) -> ImportJob:
        self._require_mutation(context)
        collection = await self.get_collection_job(context, collection_job_id)
        if collection.status is not CollectionJobStatus.ACTIVE:
            raise ImportDomainError(
                "COLLECTION_JOB_NOT_ACTIVE",
                "Collection job is not active",
                status_code=409,
            )
        job = ImportJob(
            collection_job_id=collection.id,
            department_id=collection.department_id,
            operator_id=self._operator_id(context),
            stored_file_id=None,
            original_filename=None,
            mime_type=None,
            file_size=None,
            sha256=None,
            source_type=collection.source_type,
            status=ImportJobStatus.DRAFT,
            detected_fields=None,
            field_mapping=None,
            mapping_hash=None,
            preview_revision=0,
        )
        self.session.add(job)
        await self.session.flush()
        self.audit.add(
            action=AuditAction.IMPORT_BATCH_CREATED,
            result=AuditResult.SUCCESS,
            department_id=collection.department_id,
            operator_id=self._operator_id(context),
            ip=ip,
            user_agent=user_agent,
            entity_type="import_job",
            entity_id=job.id,
            after={"collection_job_id": str(collection.id), "status": job.status.value},
        )
        await self.session.commit()
        return job

    @staticmethod
    def _require_bulk_draft(job: ImportJob) -> None:
        if job.status is not ImportJobStatus.DRAFT or job.preview_revision != 0:
            raise ImportDomainError(
                "IMPORT_BATCH_FROZEN",
                "Bulk import files can only be changed before the first preview",
                status_code=409,
            )

    def _validate_source_acquired_at(
        self,
        value: datetime | None,
        *,
        accepted_at: datetime,
    ) -> tuple[datetime, SourceAcquiredAtOrigin]:
        accepted_utc = _as_utc(accepted_at)
        if value is None:
            return accepted_utc, SourceAcquiredAtOrigin.SERVER_DEFAULT
        if value.tzinfo is None or value.utcoffset() is None:
            raise ImportDomainError(
                "INVALID_SOURCE_ACQUIRED_AT",
                "source_acquired_at must include a timezone",
                status_code=422,
            )
        value_utc = value.astimezone(UTC)
        latest = accepted_utc + timedelta(seconds=self.source_acquired_clock_skew_seconds)
        if value_utc > latest:
            raise ImportDomainError(
                "INVALID_SOURCE_ACQUIRED_AT",
                "source_acquired_at is later than the accepted clock-skew window",
                status_code=422,
            )
        return value_utc, SourceAcquiredAtOrigin.USER_CONFIRMED

    async def _commit_upload_transaction(self) -> None:
        """Finish a file transaction even when the request task is cancelled."""

        commit_task = asyncio.create_task(self.session.commit())
        try:
            await asyncio.shield(commit_task)
        except asyncio.CancelledError:
            while not commit_task.done():
                try:
                    await asyncio.shield(commit_task)
                except asyncio.CancelledError:
                    continue
            if not commit_task.cancelled():
                try:
                    commit_task.result()
                except BaseException:
                    pass
            raise

    async def _candidate_is_committed(self, *, sha256: str, storage_key: str) -> bool | None:
        """Check commit outcome using a new connection; None means fail-safe unknown."""

        bind = self.session.bind
        if not isinstance(bind, AsyncEngine):
            return None
        try:
            factory = async_sessionmaker(bind, expire_on_commit=False)
            async with factory() as verification_session:
                committed_key = await verification_session.scalar(
                    select(StoredImportFile.storage_key).where(StoredImportFile.sha256 == sha256)
                )
            return committed_key == storage_key
        except BaseException:
            logger.warning(
                "import_upload_commit_outcome_verification_failed",
                extra={"sha256": sha256},
            )
            return None

    async def _delete_candidate_safely(self, storage_key: str) -> None:
        """Best-effort cleanup without hiding the caller's primary failure.

        Cancellation is deliberately not swallowed. Other cleanup failures are
        retried a bounded number of times, then logged with an opaque fingerprint
        rather than a storage key or filesystem path.
        """

        fingerprint = hashlib.sha256(storage_key.encode("utf-8")).hexdigest()[:12]
        max_attempts = 3
        for attempt in range(1, max_attempts + 1):
            try:
                await self.storage.delete(storage_key)
                return
            except asyncio.CancelledError:
                raise
            except Exception as error:
                if attempt == max_attempts:
                    logger.warning(
                        "import_upload_candidate_cleanup_failed "
                        "key_fingerprint=%s attempts=%d error_type=%s",
                        fingerprint,
                        max_attempts,
                        type(error).__name__,
                    )

    async def upload_import_job_file(
        self,
        context: AuthContext,
        *,
        import_job_id: UUID,
        client_file_id: str,
        filename: str,
        declared_mime: str,
        chunks: AsyncIterable[bytes],
        source_acquired_at: datetime | None,
        ip: str,
        user_agent: str,
    ) -> FileUploadDecision:
        self._require_mutation(context)
        normalized_client_id = client_file_id.strip()
        if not normalized_client_id or len(normalized_client_id) > 160:
            raise ImportDomainError(
                "INVALID_CLIENT_FILE_ID",
                "client_file_id must contain between 1 and 160 characters",
                status_code=422,
            )
        suffix = PurePosixPath(filename.replace("\\", "/")).suffix.lower()
        if suffix not in {".csv", ".xlsx"}:
            raise ImportDomainError(
                "INVALID_FILE_EXTENSION",
                "Only CSV and XLSX are allowed",
                status_code=415,
            )

        preflight_job = await self._get_file_scoped_import_job(context, import_job_id)
        self._require_bulk_draft(preflight_job)

        stored_object = None
        owns_storage_object = False
        commit_attempted = False
        try:
            try:
                stored_object = await self.storage.store(
                    chunks, suffix=suffix, max_bytes=self.max_file_bytes
                )
            except ImportDomainError as error:
                if error.code == "EMPTY_FILE":
                    error.status_code = 422
                raise
            owns_storage_object = True
            content = await self.storage.read(
                stored_object.storage_key,
                expected_size=stored_object.size,
                expected_sha256=stored_object.sha256,
            )
            try:
                detected_type = validate_upload_type(
                    content,
                    suffix=suffix,
                    declared_mime=declared_mime,
                    limits=self.parser_limits,
                )
            except ImportDomainError as error:
                if error.code in {"INVALID_FILE_EXTENSION", "MIME_MISMATCH"}:
                    error.status_code = 415
                elif error.status_code == 400:
                    error.status_code = 422
                raise
            detected_mime = (
                "text/csv"
                if detected_type is StoredFileType.CSV
                else "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            )
            accepted_at = _as_utc(self.clock.now())
            acquisition, acquisition_origin = self._validate_source_acquired_at(
                source_acquired_at,
                accepted_at=accepted_at,
            )

            job = await self._get_file_scoped_import_job(context, import_job_id, for_update=True)
            self._require_bulk_draft(job)

            alias_record = await self.repository.get_file_client_id_alias(
                job.id, normalized_client_id
            )
            if alias_record is not None:
                if alias_record.stored_file.sha256 != stored_object.sha256:
                    await self._delete_candidate_safely(stored_object.storage_key)
                    owns_storage_object = False
                    await self.session.rollback()
                    raise ImportDomainError(
                        "IDEMPOTENCY_CONFLICT",
                        "client_file_id is already bound to different content",
                        status_code=409,
                    )
                await self._delete_candidate_safely(stored_object.storage_key)
                owns_storage_object = False
                await self._commit_upload_transaction()
                return FileUploadDecision(alias_record, idempotent=True)

            sha_record = await self.repository.get_import_job_file_by_sha256(
                job.id, stored_object.sha256
            )
            if sha_record is not None:
                self.session.add(
                    ImportJobFileClientId(
                        import_job_id=job.id,
                        import_job_file_id=sha_record.occurrence.id,
                        client_file_id=normalized_client_id,
                    )
                )
                await self._delete_candidate_safely(stored_object.storage_key)
                owns_storage_object = False
                await self._commit_upload_transaction()
                return FileUploadDecision(sha_record, idempotent=True)

            expires_at = accepted_at + timedelta(days=self.retention_days)
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
                    if (
                        self.session.bind is not None
                        and self.session.bind.dialect.name == "postgresql"
                    ):
                        async with self.session.begin_nested():
                            self.session.add(candidate)
                            await self.session.flush()
                    else:
                        self.session.add(candidate)
                        await self.session.flush()
                    stored_file = candidate
                except IntegrityError:
                    stored_file = await self.repository.get_stored_file_by_sha256(
                        stored_object.sha256
                    )
                    if stored_file is None:
                        raise
            storage_reused = stored_file.storage_key != stored_object.storage_key
            if storage_reused:
                await self._delete_candidate_safely(stored_object.storage_key)
                owns_storage_object = False
            if _as_utc(stored_file.expires_at) < _as_utc(expires_at):
                stored_file.expires_at = expires_at

            count, total_bytes, max_position = await self.repository.import_job_file_usage(job.id)
            if count >= self.max_batch_files:
                raise ImportDomainError(
                    "IMPORT_BATCH_FILE_LIMIT",
                    "Bulk import file count limit exceeded",
                    status_code=413,
                )
            if total_bytes + stored_file.size > self.max_batch_bytes:
                raise ImportDomainError(
                    "IMPORT_BATCH_SIZE_LIMIT",
                    "Bulk import cumulative file size limit exceeded",
                    status_code=413,
                )

            historical_reuse = await self.repository.has_other_job_file_reference(
                stored_file.id, job.id
            )
            source_acquired_at_confirmation_required = (
                historical_reuse and acquisition_origin is SourceAcquiredAtOrigin.SERVER_DEFAULT
            )
            occurrence = ImportJobFile(
                import_job_id=job.id,
                stored_file_id=stored_file.id,
                position=max_position + 1,
                original_filename=sanitize_original_filename(filename, suffix),
                declared_mime=(declared_mime[:160] or "application/octet-stream"),
                status=ImportJobFileStatus.UPLOADED,
                source_acquired_at=acquisition,
                source_acquired_at_origin=acquisition_origin,
                source_acquired_at_confirmation_required=(source_acquired_at_confirmation_required),
                created_at=accepted_at,
                updated_at=accepted_at,
            )
            self.session.add(occurrence)
            await self.session.flush()
            self.session.add(
                ImportJobFileClientId(
                    import_job_id=job.id,
                    import_job_file_id=occurrence.id,
                    client_file_id=normalized_client_id,
                )
            )
            self.audit.add(
                action=AuditAction.IMPORT_FILE_UPLOADED,
                result=AuditResult.SUCCESS,
                department_id=job.department_id,
                operator_id=self._operator_id(context),
                ip=ip,
                user_agent=user_agent,
                entity_type="import_job_file",
                entity_id=occurrence.id,
                after={
                    "import_job_id": str(job.id),
                    "position": occurrence.position,
                    "sha256": stored_file.sha256,
                    "size": stored_file.size,
                    "detected_type": detected_type.value,
                    "storage_reused": storage_reused,
                    "source_acquired_at_origin": acquisition_origin.value,
                    "source_acquired_at_confirmation_required": (
                        source_acquired_at_confirmation_required
                    ),
                },
            )
            commit_attempted = True
            await self._commit_upload_transaction()
            owns_storage_object = False
            return FileUploadDecision(
                ImportJobFileRecord(occurrence, stored_file),
                idempotent=False,
            )
        except BaseException:
            try:
                await self.session.rollback()
            except BaseException:
                logger.warning("import_upload_transaction_rollback_failed")
            if owns_storage_object and stored_object is not None:
                should_delete = True
                if commit_attempted:
                    committed = await self._candidate_is_committed(
                        sha256=stored_object.sha256,
                        storage_key=stored_object.storage_key,
                    )
                    should_delete = committed is False
                if should_delete:
                    await self._delete_candidate_safely(stored_object.storage_key)
            raise

    async def list_import_job_files(
        self,
        context: AuthContext,
        import_job_id: UUID,
    ) -> list[ImportJobFileRecord]:
        job = await self._get_file_scoped_import_job(context, import_job_id)
        return await self.repository.list_import_job_file_records(job.id)

    async def update_import_job_file_source_acquired_at(
        self,
        context: AuthContext,
        import_job_id: UUID,
        import_job_file_id: UUID,
        source_acquired_at: datetime,
        *,
        ip: str,
        user_agent: str,
    ) -> ImportJobFileRecord:
        self._require_mutation(context)
        job = await self._get_file_scoped_import_job(context, import_job_id, for_update=True)
        self._require_bulk_draft(job)
        record = await self.repository.get_import_job_file_record(job.id, import_job_file_id)
        if record is None:
            raise ImportDomainError(
                "IMPORT_JOB_FILE_NOT_FOUND", "Import job file not found", status_code=404
            )
        now = _as_utc(self.clock.now())
        accepted_at = _as_utc(record.occurrence.created_at)
        acquisition, _ = self._validate_source_acquired_at(
            source_acquired_at,
            accepted_at=accepted_at,
        )
        before_time = record.occurrence.source_acquired_at
        before_origin = record.occurrence.source_acquired_at_origin
        before_confirmation_required = record.occurrence.source_acquired_at_confirmation_required
        record.occurrence.source_acquired_at = acquisition
        record.occurrence.source_acquired_at_origin = SourceAcquiredAtOrigin.USER_CONFIRMED
        record.occurrence.source_acquired_at_confirmation_required = False
        record.occurrence.updated_at = now
        self.audit.add(
            action=AuditAction.IMPORT_FILE_SOURCE_ACQUIRED_AT_UPDATED,
            result=AuditResult.SUCCESS,
            department_id=job.department_id,
            operator_id=self._operator_id(context),
            ip=ip,
            user_agent=user_agent,
            entity_type="import_job_file",
            entity_id=record.occurrence.id,
            before={
                "import_job_id": str(job.id),
                "source_acquired_at": (
                    _as_utc(before_time).isoformat() if before_time is not None else None
                ),
                "source_acquired_at_origin": before_origin.value,
                "source_acquired_at_confirmation_required": (before_confirmation_required),
            },
            after={
                "import_job_id": str(job.id),
                "source_acquired_at": acquisition.isoformat(),
                "source_acquired_at_origin": SourceAcquiredAtOrigin.USER_CONFIRMED.value,
                "source_acquired_at_confirmation_required": False,
            },
        )
        await self.session.commit()
        return record

    async def exclude_import_job_file(
        self,
        context: AuthContext,
        import_job_id: UUID,
        import_job_file_id: UUID,
        *,
        ip: str,
        user_agent: str,
    ) -> ImportJobFileRecord:
        self._require_mutation(context)
        job = await self._get_file_scoped_import_job(context, import_job_id, for_update=True)
        self._require_bulk_draft(job)
        record = await self.repository.get_import_job_file_record(job.id, import_job_file_id)
        if record is None:
            raise ImportDomainError(
                "IMPORT_JOB_FILE_NOT_FOUND", "Import job file not found", status_code=404
            )
        occurrence = record.occurrence
        if occurrence.status is ImportJobFileStatus.EXCLUDED:
            await self.session.commit()
            return record
        if occurrence.status is ImportJobFileStatus.PARSING:
            raise ImportDomainError(
                "IMPORT_FILE_BUSY",
                "A file cannot be excluded while parsing",
                status_code=409,
            )
        occurrence.status = ImportJobFileStatus.EXCLUDED
        occurrence.excluded_at = _as_utc(self.clock.now())
        occurrence.updated_at = occurrence.excluded_at
        self.audit.add(
            action=AuditAction.IMPORT_FILE_EXCLUDED,
            result=AuditResult.SUCCESS,
            department_id=job.department_id,
            operator_id=self._operator_id(context),
            ip=ip,
            user_agent=user_agent,
            entity_type="import_job_file",
            entity_id=occurrence.id,
            after={"import_job_id": str(job.id), "status": occurrence.status.value},
        )
        await self.session.commit()
        return record

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
