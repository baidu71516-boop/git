"""Service contracts for Phase 2 per-file parse queueing and mutation."""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import UUID, uuid4

import pytest
from backend_core.audit.enums import AuditAction
from backend_core.audit.models import AuditLog
from backend_core.auth.enums import DepartmentStatus, OperatorStatus, Role
from backend_core.auth.models import AuthSession, Department, Operator
from backend_core.auth.service import AuthContext
from backend_core.db import models as database_models  # noqa: F401
from backend_core.db.base import Base
from backend_core.imports.contracts import CanonicalInfluencerRecord, PlatformIdentity
from backend_core.imports.enums import (
    CollectionJobStatus,
    ImportJobFileStatus,
    ImportJobStatus,
    ImportRowAction,
    ImportSourceType,
    ImportTaskKind,
    ImportTaskState,
    SourceAcquiredAtOrigin,
    StoredFileType,
)
from backend_core.imports.errors import ImportDomainError
from backend_core.imports.hashing import hash_document
from backend_core.imports.models import (
    CollectionJob,
    ImportJob,
    ImportJobFile,
    ImportRow,
    ImportTaskRequest,
    StoredImportFile,
)
from backend_core.imports.parsers import ParserLimits
from backend_core.imports.schemas import ImportMappingUpdate, ImportRowPublic
from backend_core.imports.service import ImportService
from backend_core.imports.storage import LocalStorageAdapter
from backend_core.influencers.enums import DataSource, Platform
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

VALID_MAPPING = {"name": "nickname", "url": "profile_url"}


@dataclass(frozen=True, slots=True)
class Harness:
    session: AsyncSession
    service: ImportService
    context: AuthContext
    job: ImportJob
    occurrence: ImportJobFile


@asynccontextmanager
async def service_harness() -> AsyncIterator[Harness]:
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    with TemporaryDirectory(prefix="phase2-file-service-") as directory:
        async with factory() as session:
            department = Department(
                name=f"File parse service {uuid4().hex}",
                password_hash="not-used-by-test",
                status=DepartmentStatus.ACTIVE,
                session_days=30,
            )
            session.add(department)
            await session.flush()
            operator = Operator(
                department_id=department.id,
                name="File parse operator",
                role=Role.OPERATOR,
                status=OperatorStatus.ACTIVE,
            )
            session.add(operator)
            await session.flush()
            auth_session = AuthSession(
                department_id=department.id,
                operator_id=operator.id,
                token_hash=uuid4().hex + uuid4().hex,
                csrf_token_hash=uuid4().hex + uuid4().hex,
                ip="127.0.0.1",
                user_agent="batch-parse-service-test",
                expires_at=datetime.now(UTC) + timedelta(hours=1),
            )
            collection = CollectionJob(
                name="File parse collection",
                industry="test",
                purpose="verify per-file parse service",
                target_action="parse",
                target_count=100,
                department_id=department.id,
                owner_operator_id=operator.id,
                source_type=ImportSourceType.GENERIC_CSV,
                status=CollectionJobStatus.ACTIVE,
            )
            stored = StoredImportFile(
                sha256="1" * 64,
                storage_key="opaque/test.csv",
                size=42,
                detected_type=StoredFileType.CSV,
                detected_mime="text/csv",
                expires_at=datetime.now(UTC) + timedelta(days=30),
            )
            session.add_all([auth_session, collection, stored])
            await session.flush()
            job = ImportJob(
                collection_job_id=collection.id,
                department_id=department.id,
                operator_id=operator.id,
                source_type=ImportSourceType.GENERIC_CSV,
                status=ImportJobStatus.DRAFT,
                preview_revision=0,
            )
            session.add(job)
            await session.flush()
            occurrence = ImportJobFile(
                import_job_id=job.id,
                stored_file_id=stored.id,
                position=1,
                original_filename="safe.csv",
                declared_mime="text/csv",
                status=ImportJobFileStatus.UPLOADED,
                source_acquired_at=datetime.now(UTC),
                source_acquired_at_origin=SourceAcquiredAtOrigin.SERVER_DEFAULT,
            )
            session.add(occurrence)
            await session.commit()
            context = AuthContext(
                department=department,
                operator=operator,
                role=Role.OPERATOR,
                auth_session=auth_session,
            )
            service = ImportService(
                session,
                LocalStorageAdapter(Path(directory)),
                parser_limits=ParserLimits(),
                max_file_bytes=25 * 1024 * 1024,
                retention_days=30,
            )
            yield Harness(session, service, context, job, occurrence)
    await engine.dispose()


def assert_error(error: ImportDomainError, code: str, status_code: int) -> None:
    assert error.code == code
    assert error.status_code == status_code


def test_initial_queue_is_persisted_once_and_reuses_the_existing_task_token() -> None:
    async def scenario() -> None:
        async with service_harness() as harness:
            task_id = str(uuid4())
            first = await harness.service.queue_import_job_file(
                harness.context,
                harness.job.id,
                harness.occurrence.id,
                task_id,
                ip="127.0.0.1",
                user_agent="test",
            )
            assert first.should_dispatch is True
            assert first.idempotent is False
            assert first.task_id == task_id
            assert first.record.occurrence.status is ImportJobFileStatus.PARSING
            assert first.record.occurrence.parse_attempts == 1
            assert first.record.occurrence.parse_started_at is None
            task = await harness.session.scalar(
                select(ImportTaskRequest).where(ImportTaskRequest.task_token == UUID(first.task_id))
            )
            assert task is not None
            assert task.task_kind is ImportTaskKind.FILE_PARSE
            assert task.state is ImportTaskState.REQUESTED
            assert task.dispatch_attempts == 1
            assert task.last_dispatch_attempt_at is not None
            assert task.next_retry_at is not None

            replay = await harness.service.queue_import_job_file(
                harness.context,
                harness.job.id,
                harness.occurrence.id,
                str(uuid4()),
                ip="127.0.0.1",
                user_agent="test",
            )
            assert replay.should_dispatch is False
            assert replay.idempotent is True
            assert replay.task_id == task_id
            assert replay.record.occurrence.parse_attempts == 1

    asyncio.run(scenario())


def test_mapping_is_validated_file_scoped_and_busy_requests_are_idempotent_or_rejected() -> None:
    async def scenario() -> None:
        async with service_harness() as harness:
            harness.occurrence.status = ImportJobFileStatus.MAPPING_REQUIRED
            harness.occurrence.detected_fields = ["name", "url"]
            await harness.session.commit()

            with pytest.raises(ImportDomainError) as invalid:
                await harness.service.update_import_job_file_mapping(
                    harness.context,
                    harness.job.id,
                    harness.occurrence.id,
                    {"missing": "nickname", "url": "profile_url"},
                    "invalid-token",
                    ip="127.0.0.1",
                    user_agent="test",
                )
            assert_error(invalid.value, "MAPPING_INVALID", 422)

            mapping_task_id = str(uuid4())
            mapped = await harness.service.update_import_job_file_mapping(
                harness.context,
                harness.job.id,
                harness.occurrence.id,
                VALID_MAPPING,
                mapping_task_id,
                ip="127.0.0.1",
                user_agent="test",
            )
            assert mapped.should_dispatch is True
            assert mapped.record.occurrence.field_mapping == VALID_MAPPING
            assert mapped.record.occurrence.mapping_hash is not None
            assert mapped.record.occurrence.parse_attempts == 1

            same = await harness.service.update_import_job_file_mapping(
                harness.context,
                harness.job.id,
                harness.occurrence.id,
                dict(reversed(tuple(VALID_MAPPING.items()))),
                str(uuid4()),
                ip="127.0.0.1",
                user_agent="test",
            )
            assert same.idempotent is True
            assert same.should_dispatch is False
            assert same.task_id == mapping_task_id

            with pytest.raises(ImportDomainError) as busy:
                await harness.service.update_import_job_file_mapping(
                    harness.context,
                    harness.job.id,
                    harness.occurrence.id,
                    {"name": "nickname", "url": "external_source_id"},
                    "busy-token",
                    ip="127.0.0.1",
                    user_agent="test",
                )
            assert_error(busy.value, "IMPORT_FILE_BUSY", 409)

            mapping_audits = list(
                await harness.session.scalars(
                    select(AuditLog).where(
                        AuditLog.action == AuditAction.IMPORT_FILE_MAPPING_UPDATED
                    )
                )
            )
            assert len(mapping_audits) == 1
            assert mapping_audits[0].after is not None
            assert "mapping" not in mapping_audits[0].after

    asyncio.run(scenario())


def test_retry_resets_file_error_and_reserves_durable_dispatch() -> None:
    async def scenario() -> None:
        async with service_harness() as harness:
            job_id = harness.job.id
            occurrence_id = harness.occurrence.id
            harness.occurrence.status = ImportJobFileStatus.FAILED
            harness.occurrence.error_code = "INVALID_CSV"
            harness.occurrence.error_message = "deterministic failure"
            harness.occurrence.parse_attempts = 1
            await harness.session.commit()

            task_id = str(uuid4())
            retried = await harness.service.retry_import_job_file(
                harness.context,
                job_id,
                occurrence_id,
                task_id,
                ip="127.0.0.1",
                user_agent="test",
            )
            occurrence = retried.record.occurrence
            assert occurrence.status is ImportJobFileStatus.PARSING
            assert occurrence.parse_attempts == 2
            assert occurrence.error_code is None
            assert occurrence.error_message is None
            assert occurrence.parse_completed_at is None
            task = await harness.session.scalar(
                select(ImportTaskRequest).where(ImportTaskRequest.task_token == UUID(task_id))
            )
            assert task is not None
            assert task.state is ImportTaskState.REQUESTED
            assert task.dispatch_attempts == 1
            assert task.last_dispatch_attempt_at is not None
            assert task.next_retry_at is not None
            persisted_job = await harness.session.get(ImportJob, job_id)
            assert persisted_job is not None
            assert persisted_job.status is ImportJobStatus.DRAFT

            retry_audits = list(
                await harness.session.scalars(
                    select(AuditLog).where(AuditLog.action == AuditAction.IMPORT_FILE_RETRIED)
                )
            )
            assert len(retry_audits) == 1

    asyncio.run(scenario())


def test_retry_mapping_guard_rbac_nested_404_and_exclude_removes_staging_rows() -> None:
    async def scenario() -> None:
        async with service_harness() as harness:
            harness.occurrence.status = ImportJobFileStatus.MAPPING_REQUIRED
            harness.occurrence.detected_fields = ["name", "url"]
            await harness.session.commit()
            with pytest.raises(ImportDomainError) as mapping_required:
                await harness.service.retry_import_job_file(
                    harness.context,
                    harness.job.id,
                    harness.occurrence.id,
                    "retry-without-mapping",
                    ip="127.0.0.1",
                    user_agent="test",
                )
            assert_error(mapping_required.value, "MAPPING_REQUIRED", 409)

            assert harness.context.operator is not None
            viewer = AuthContext(
                department=harness.context.department,
                operator=Operator(
                    id=harness.context.operator.id,
                    department_id=harness.context.department.id,
                    name=harness.context.operator.name,
                    role=Role.VIEWER,
                    status=OperatorStatus.ACTIVE,
                ),
                role=Role.OPERATOR,
                auth_session=harness.context.auth_session,
            )
            with pytest.raises(ImportDomainError) as viewer_error:
                await harness.service.retry_import_job_file(
                    viewer,
                    harness.job.id,
                    harness.occurrence.id,
                    "viewer-token",
                    ip="127.0.0.1",
                    user_agent="test",
                )
            assert_error(viewer_error.value, "PERMISSION_DENIED", 403)

            with pytest.raises(ImportDomainError) as wrong_parent:
                await harness.service.retry_import_job_file(
                    harness.context,
                    uuid4(),
                    harness.occurrence.id,
                    "wrong-parent",
                    ip="127.0.0.1",
                    user_agent="test",
                )
            assert_error(wrong_parent.value, "IMPORT_JOB_NOT_FOUND", 404)

            harness.occurrence.status = ImportJobFileStatus.READY
            row = ImportRow(
                import_job_id=harness.job.id,
                import_job_file_id=harness.occurrence.id,
                row_number=2,
                raw_data={"name": "lineage-only"},
                normalized_data=None,
                action=ImportRowAction.ERROR,
                warnings=[],
                errors=[{"code": "TEST"}],
                preview_revision=0,
                plan_hash="2" * 64,
            )
            harness.session.add(row)
            await harness.session.commit()
            await harness.service.exclude_import_job_file(
                harness.context,
                harness.job.id,
                harness.occurrence.id,
                ip="127.0.0.1",
                user_agent="test",
            )
            assert harness.occurrence.status is ImportJobFileStatus.EXCLUDED
            row_count = await harness.session.scalar(
                select(func.count())
                .select_from(ImportRow)
                .where(ImportRow.import_job_file_id == harness.occurrence.id)
            )
            assert row_count == 0

    asyncio.run(scenario())


@pytest.mark.parametrize("task_state", [ImportTaskState.REQUESTED, ImportTaskState.RETRY_WAIT])
def test_cancel_atomically_cancels_non_running_durable_tasks(
    task_state: ImportTaskState,
) -> None:
    async def scenario() -> None:
        async with service_harness() as harness:
            decision = await harness.service.queue_import_job_file(
                harness.context,
                harness.job.id,
                harness.occurrence.id,
                str(uuid4()),
                ip="127.0.0.1",
                user_agent="test",
            )
            task = await harness.session.scalar(
                select(ImportTaskRequest).where(
                    ImportTaskRequest.task_token == UUID(decision.task_id)
                )
            )
            assert task is not None
            if task_state is ImportTaskState.RETRY_WAIT:
                task.state = ImportTaskState.RETRY_WAIT
                task.next_retry_at = datetime.now(UTC) + timedelta(minutes=1)
                await harness.session.commit()

            cancelled = await harness.service.cancel(
                harness.context,
                harness.job.id,
                ip="127.0.0.1",
                user_agent="test",
            )
            assert cancelled.status is ImportJobStatus.CANCELLED
            await harness.session.refresh(task)
            assert task.state is ImportTaskState.CANCELLED
            assert task.completed_at is not None
            assert task.next_retry_at is None
            assert task.lease_expires_at is None

    asyncio.run(scenario())


def test_cancel_rejects_a_running_durable_task_without_changing_the_job() -> None:
    async def scenario() -> None:
        async with service_harness() as harness:
            decision = await harness.service.queue_import_job_file(
                harness.context,
                harness.job.id,
                harness.occurrence.id,
                str(uuid4()),
                ip="127.0.0.1",
                user_agent="test",
            )
            task = await harness.session.scalar(
                select(ImportTaskRequest).where(
                    ImportTaskRequest.task_token == UUID(decision.task_id)
                )
            )
            assert task is not None
            task.state = ImportTaskState.RUNNING
            task.run_attempts = 1
            task.started_at = datetime.now(UTC)
            task.lease_expires_at = datetime.now(UTC) + timedelta(minutes=1)
            task.next_retry_at = None
            await harness.session.commit()

            with pytest.raises(ImportDomainError) as running:
                await harness.service.cancel(
                    harness.context,
                    harness.job.id,
                    ip="127.0.0.1",
                    user_agent="test",
                )
            assert_error(running.value, "IMPORT_TASK_RUNNING", 409)
            await harness.session.refresh(harness.job)
            await harness.session.refresh(task)
            assert harness.job.status is ImportJobStatus.DRAFT
            assert task.state is ImportTaskState.RUNNING

    asyncio.run(scenario())


def test_task3_schemas_forbid_extra_mapping_input_and_expose_file_locator() -> None:
    with pytest.raises(ValidationError):
        ImportMappingUpdate.model_validate({"mapping": VALID_MAPPING, "unfrozen_extra": True})
    assert "import_job_file_id" in ImportRowPublic.model_fields


def test_excluding_a_duplicate_owner_replans_the_remaining_ready_row() -> None:
    async def scenario() -> None:
        async with service_harness() as harness:
            first = harness.occurrence
            first.status = ImportJobFileStatus.READY
            first.parse_task_id = "owner-task"
            first.field_mapping = VALID_MAPPING
            first.mapping_hash = hash_document(VALID_MAPPING)
            second_stored = StoredImportFile(
                sha256="3" * 64,
                storage_key="opaque/second.csv",
                size=43,
                detected_type=StoredFileType.CSV,
                detected_mime="text/csv",
                expires_at=datetime.now(UTC) + timedelta(days=30),
            )
            harness.session.add(second_stored)
            await harness.session.flush()
            second = ImportJobFile(
                import_job_id=harness.job.id,
                stored_file_id=second_stored.id,
                position=2,
                original_filename="second.csv",
                declared_mime="text/csv",
                status=ImportJobFileStatus.READY,
                source_acquired_at=datetime.now(UTC),
                source_acquired_at_origin=SourceAcquiredAtOrigin.SERVER_DEFAULT,
                field_mapping=VALID_MAPPING,
                mapping_hash=hash_document(VALID_MAPPING),
            )
            harness.session.add(second)
            await harness.session.flush()
            record = CanonicalInfluencerRecord(
                display_name="Stable duplicate",
                platform_identity=PlatformIdentity(
                    platform=Platform.XIAOHONGSHU,
                    platform_account_id="stable-duplicate",
                    account_handle=None,
                    profile_url=None,
                    normalized_profile_url=None,
                    external_source_id=None,
                ),
                source=DataSource.GENERIC,
                source_updated_at=datetime(2026, 8, 10, tzinfo=UTC),
            )
            normalized = record.as_dict()
            first_row = ImportRow(
                import_job_id=harness.job.id,
                import_job_file_id=first.id,
                row_number=2,
                raw_data={"name": "Stable duplicate", "url": "stable-duplicate"},
                normalized_data=normalized,
                action=ImportRowAction.CREATE,
                warnings=[],
                errors=[],
                preview_revision=0,
                plan_hash="4" * 64,
            )
            harness.session.add(first_row)
            await harness.session.flush()
            second_row = ImportRow(
                import_job_id=harness.job.id,
                import_job_file_id=second.id,
                row_number=2,
                raw_data={"name": "Stable duplicate", "url": "stable-duplicate"},
                normalized_data=normalized,
                action=ImportRowAction.SKIP,
                merge_plan={
                    "batch_duplicate": {
                        "owner_import_job_file_id": str(first.id),
                        "owner_row_number": 2,
                    }
                },
                warnings=[{"code": "BATCH_DUPLICATE"}],
                errors=[],
                preview_revision=0,
                plan_hash="5" * 64,
            )
            harness.session.add(second_row)
            await harness.session.commit()

            await harness.service.exclude_import_job_file(
                harness.context,
                harness.job.id,
                first.id,
                ip="127.0.0.1",
                user_agent="test",
            )

            rows = list(
                await harness.session.scalars(
                    select(ImportRow).where(ImportRow.import_job_id == harness.job.id)
                )
            )
            assert len(rows) == 1
            assert rows[0].import_job_file_id == second.id
            assert rows[0].action is ImportRowAction.CREATE
            assert "batch_duplicate" not in rows[0].merge_plan
            assert not any(warning.get("code") == "BATCH_DUPLICATE" for warning in rows[0].warnings)

    asyncio.run(scenario())
