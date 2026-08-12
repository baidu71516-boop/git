"""Phase 2 Task 2 service contracts for Bulk Draft file management."""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from uuid import UUID, uuid4

import pytest
from backend_core.audit.enums import AuditAction
from backend_core.audit.models import AuditLog
from backend_core.auth.enums import DepartmentStatus, OperatorStatus, Role
from backend_core.auth.models import AuthSession, Department, DepartmentPermission, Operator
from backend_core.auth.service import AuthContext
from backend_core.db import models as database_models  # noqa: F401
from backend_core.db.base import Base
from backend_core.imports.enums import (
    CollectionJobStatus,
    ImportJobFileStatus,
    ImportJobStatus,
    ImportSourceType,
    SourceAcquiredAtOrigin,
)
from backend_core.imports.errors import ImportDomainError
from backend_core.imports.models import (
    CollectionJob,
    ImportJob,
    ImportJobFile,
    ImportJobFileClientId,
    StoredImportFile,
)
from backend_core.imports.parsers import ParserLimits
from backend_core.imports.service import FileUploadDecision, ImportService
from backend_core.imports.storage import LocalStorageAdapter
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine


class MutableClock:
    def __init__(self) -> None:
        self.current = datetime(2026, 8, 12, 4, 0, tzinfo=UTC)

    def now(self) -> datetime:
        return self.current


@dataclass
class BulkServiceHarness:
    session: AsyncSession
    service: ImportService
    context: AuthContext
    operator_id: UUID
    collection: CollectionJob
    storage: LocalStorageAdapter
    clock: MutableClock


def valid_csv(label: str) -> bytes:
    return (
        "达人名称,达人官方地址\n" f"{label},https://www.xiaohongshu.com/user/profile/{label}\n"
    ).encode("utf-8-sig")


async def chunks(content: bytes) -> AsyncIterator[bytes]:
    midpoint = len(content) // 2
    yield content[:midpoint]
    yield content[midpoint:]


@asynccontextmanager
async def bulk_service_harness(
    *,
    max_batch_files: int = 20,
    max_batch_bytes: int = 100 * 1024 * 1024,
) -> AsyncIterator[BulkServiceHarness]:
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    with TemporaryDirectory(prefix="phase2-bulk-service-") as directory:
        async with factory() as session:
            department = Department(
                name=f"Bulk Service {uuid4().hex}",
                password_hash="not-used-by-service-test",
                status=DepartmentStatus.ACTIVE,
                session_days=30,
            )
            session.add(department)
            await session.flush()
            operator = Operator(
                department_id=department.id,
                name="Bulk Service Operator",
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
                user_agent="phase2-bulk-service-test",
                expires_at=datetime.now(UTC) + timedelta(hours=1),
            )
            collection = CollectionJob(
                name="Bulk Service Collection",
                industry="测试",
                purpose="验证多文件 Service 契约",
                target_action="Preview",
                target_count=20,
                department_id=department.id,
                owner_operator_id=operator.id,
                source_type=ImportSourceType.MANUAL_HUITUN_EXPORT,
                status=CollectionJobStatus.ACTIVE,
            )
            session.add_all(
                [
                    DepartmentPermission(department_id=department.id, role=Role.OPERATOR),
                    auth_session,
                    collection,
                ]
            )
            await session.commit()
            context = AuthContext(
                department=department,
                operator=operator,
                role=Role.OPERATOR,
                auth_session=auth_session,
            )
            clock = MutableClock()
            storage = LocalStorageAdapter(Path(directory))
            service = ImportService(
                session,
                storage,
                parser_limits=ParserLimits(max_rows=100, max_columns=100, max_cells=10_000),
                max_file_bytes=25 * 1024 * 1024,
                retention_days=30,
                max_batch_files=max_batch_files,
                max_batch_bytes=max_batch_bytes,
                source_acquired_clock_skew_seconds=300,
                clock=clock,
            )
            yield BulkServiceHarness(
                session=session,
                service=service,
                context=context,
                operator_id=operator.id,
                collection=collection,
                storage=storage,
                clock=clock,
            )
    await engine.dispose()


async def create_bulk(harness: BulkServiceHarness) -> ImportJob:
    return await harness.service.create_bulk_import_job(
        harness.context,
        collection_job_id=harness.collection.id,
        ip="127.0.0.1",
        user_agent="phase2-bulk-service-test",
    )


async def upload(
    harness: BulkServiceHarness,
    import_job_id: UUID,
    *,
    client_file_id: str,
    label: str,
    source_acquired_at: datetime | None = None,
) -> FileUploadDecision:
    return await harness.service.upload_import_job_file(
        harness.context,
        import_job_id=import_job_id,
        client_file_id=client_file_id,
        filename=f"../unsafe/{label}.csv",
        declared_mime="text/csv",
        chunks=chunks(valid_csv(label)),
        source_acquired_at=source_acquired_at,
        ip="127.0.0.1",
        user_agent="phase2-bulk-service-test",
    )


async def upload_content(
    harness: BulkServiceHarness,
    import_job_id: UUID,
    *,
    client_file_id: str,
    filename: str,
    declared_mime: str,
    content: bytes,
) -> FileUploadDecision:
    return await harness.service.upload_import_job_file(
        harness.context,
        import_job_id=import_job_id,
        client_file_id=client_file_id,
        filename=filename,
        declared_mime=declared_mime,
        chunks=chunks(content),
        source_acquired_at=None,
        ip="127.0.0.1",
        user_agent="phase2-bulk-service-test",
    )


async def count_rows(session: AsyncSession, model: type[object]) -> int:
    return int(await session.scalar(select(func.count()).select_from(model)) or 0)


async def refresh_context(harness: BulkServiceHarness) -> None:
    await harness.session.refresh(harness.context.department)
    if harness.context.operator is not None:
        await harness.session.refresh(harness.context.operator)
    await harness.session.refresh(harness.context.auth_session)


def stored_blob_paths(storage: LocalStorageAdapter) -> list[Path]:
    return sorted(path for path in storage.root.iterdir() if path.is_file())


def as_utc(value: datetime) -> datetime:
    """Normalize SQLite's timezone-naive DateTime round trip as UTC."""
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def assert_domain_error(error: ImportDomainError, *, status_code: int) -> None:
    assert error.status_code == status_code


def test_create_bulk_import_job_is_draft_without_legacy_file_facts_and_is_audited() -> None:
    async def scenario() -> None:
        async with bulk_service_harness() as harness:
            job = await create_bulk(harness)

            assert job.collection_job_id == harness.collection.id
            assert job.department_id == harness.context.department.id
            assert job.operator_id == harness.operator_id
            assert job.status is ImportJobStatus.DRAFT
            assert job.preview_revision == 0
            assert job.stored_file_id is None
            assert job.original_filename is None
            assert job.mime_type is None
            assert job.file_size is None
            assert job.sha256 is None
            assert harness.service.max_batch_files == 20
            assert harness.service.max_batch_bytes == 100 * 1024 * 1024
            assert await count_rows(harness.session, ImportJobFile) == 0

            audits = list(
                await harness.session.scalars(
                    select(AuditLog).where(AuditLog.action == AuditAction.IMPORT_BATCH_CREATED)
                )
            )
            assert len(audits) == 1
            assert audits[0].department_id == harness.context.department.id
            assert audits[0].operator_id == harness.operator_id
            assert audits[0].entity_id == job.id

    asyncio.run(scenario())


def test_upload_preflight_rejects_unknown_cross_scope_and_frozen_jobs_before_storage() -> None:
    async def scenario() -> None:
        async with bulk_service_harness() as harness:
            other_department = Department(
                name=f"Bulk Service Other {uuid4().hex}",
                password_hash="not-used-by-service-test",
                status=DepartmentStatus.ACTIVE,
                session_days=30,
            )
            harness.session.add(other_department)
            await harness.session.flush()
            other_operator = Operator(
                department_id=other_department.id,
                name="Bulk Service Other Operator",
                role=Role.OPERATOR,
                status=OperatorStatus.ACTIVE,
            )
            harness.session.add(other_operator)
            await harness.session.flush()
            other_auth_session = AuthSession(
                department_id=other_department.id,
                operator_id=other_operator.id,
                token_hash=uuid4().hex + uuid4().hex,
                csrf_token_hash=uuid4().hex + uuid4().hex,
                ip="127.0.0.1",
                user_agent="phase2-bulk-service-cross-scope-test",
                expires_at=datetime.now(UTC) + timedelta(hours=1),
            )
            other_collection = CollectionJob(
                name="Bulk Service Other Collection",
                industry="测试",
                purpose="验证上传前 scope 门禁",
                target_action="Preview",
                target_count=20,
                department_id=other_department.id,
                owner_operator_id=other_operator.id,
                source_type=ImportSourceType.MANUAL_HUITUN_EXPORT,
                status=CollectionJobStatus.ACTIVE,
            )
            harness.session.add_all(
                [
                    DepartmentPermission(
                        department_id=other_department.id,
                        role=Role.OPERATOR,
                    ),
                    other_auth_session,
                    other_collection,
                ]
            )
            await harness.session.commit()
            other_context = AuthContext(
                department=other_department,
                operator=other_operator,
                role=Role.OPERATOR,
                auth_session=other_auth_session,
            )
            cross_scope_job = await harness.service.create_bulk_import_job(
                other_context,
                collection_job_id=other_collection.id,
                ip="127.0.0.1",
                user_agent="phase2-bulk-service-cross-scope-test",
            )
            frozen_job = await create_bulk(harness)
            frozen_job.status = ImportJobStatus.PREVIEW_READY
            await harness.session.commit()

            async def assert_rejected_before_storage(
                import_job_id: UUID,
                *,
                expected_code: str,
                expected_status: int,
            ) -> None:
                consumed_chunks = 0

                async def monitored_chunks() -> AsyncIterator[bytes]:
                    nonlocal consumed_chunks
                    consumed_chunks += 1
                    yield valid_csv("must-not-be-read")

                with patch.object(
                    harness.storage,
                    "store",
                    wraps=harness.storage.store,
                ) as store_spy:
                    with pytest.raises(ImportDomainError) as exc_info:
                        await harness.service.upload_import_job_file(
                            harness.context,
                            import_job_id=import_job_id,
                            client_file_id=f"preflight-{uuid4().hex}",
                            filename="preflight.csv",
                            declared_mime="text/csv",
                            chunks=monitored_chunks(),
                            source_acquired_at=None,
                            ip="127.0.0.1",
                            user_agent="phase2-bulk-service-test",
                        )

                assert store_spy.await_count == 0
                assert consumed_chunks == 0
                assert exc_info.value.code == expected_code
                assert_domain_error(exc_info.value, status_code=expected_status)

            await assert_rejected_before_storage(
                uuid4(),
                expected_code="IMPORT_JOB_NOT_FOUND",
                expected_status=404,
            )
            await assert_rejected_before_storage(
                cross_scope_job.id,
                expected_code="IMPORT_JOB_NOT_FOUND",
                expected_status=404,
            )
            await assert_rejected_before_storage(
                frozen_job.id,
                expected_code="IMPORT_BATCH_FROZEN",
                expected_status=409,
            )
            assert stored_blob_paths(harness.storage) == []

    asyncio.run(scenario())


def test_invalid_supported_file_content_is_422_while_mime_and_extension_remain_415() -> None:
    async def scenario() -> None:
        invalid_supported_files = (
            ("invalid.csv", "text/csv", b"header\x00,value\n", "INVALID_CSV"),
            (
                "invalid.xlsx",
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                b"PK\x03\x04not-a-valid-zip",
                "INVALID_XLSX",
            ),
        )
        for filename, declared_mime, content, expected_code in invalid_supported_files:
            async with bulk_service_harness() as harness:
                job = await create_bulk(harness)
                with pytest.raises(ImportDomainError) as exc_info:
                    await upload_content(
                        harness,
                        job.id,
                        client_file_id=f"invalid-{uuid4().hex}",
                        filename=filename,
                        declared_mime=declared_mime,
                        content=content,
                    )
                assert exc_info.value.code == expected_code
                assert_domain_error(exc_info.value, status_code=422)
                assert stored_blob_paths(harness.storage) == []

        async with bulk_service_harness() as harness:
            job = await create_bulk(harness)
            job_id = job.id
            with pytest.raises(ImportDomainError) as empty_error:
                await upload_content(
                    harness,
                    job_id,
                    client_file_id="empty-file",
                    filename="empty.csv",
                    declared_mime="text/csv",
                    content=b"",
                )
            assert empty_error.value.code == "EMPTY_FILE"
            assert_domain_error(empty_error.value, status_code=422)
            await refresh_context(harness)

            with pytest.raises(ImportDomainError) as mime_error:
                await upload_content(
                    harness,
                    job_id,
                    client_file_id="mime-mismatch",
                    filename="mime-mismatch.csv",
                    declared_mime="image/png",
                    content=valid_csv("mime-mismatch"),
                )
            assert mime_error.value.code == "MIME_MISMATCH"
            assert_domain_error(mime_error.value, status_code=415)
            await refresh_context(harness)

            with pytest.raises(ImportDomainError) as extension_error:
                await upload_content(
                    harness,
                    job_id,
                    client_file_id="invalid-extension",
                    filename="invalid-extension.txt",
                    declared_mime="text/plain",
                    content=valid_csv("invalid-extension"),
                )
            assert extension_error.value.code == "INVALID_FILE_EXTENSION"
            assert_domain_error(extension_error.value, status_code=415)
            assert stored_blob_paths(harness.storage) == []

    asyncio.run(scenario())


def test_bulk_file_count_limit_is_twenty_and_excluded_occurrences_still_count() -> None:
    async def scenario() -> None:
        async with bulk_service_harness(max_batch_files=20) as harness:
            job = await create_bulk(harness)
            job_id = job.id
            first = await upload(
                harness,
                job_id,
                client_file_id="client-00",
                label="row-00",
            )
            first_file_id = first.record.occurrence.id
            await harness.service.exclude_import_job_file(
                harness.context,
                job_id,
                first_file_id,
                ip="127.0.0.1",
                user_agent="phase2-bulk-service-test",
            )
            for index in range(1, 20):
                await upload(
                    harness,
                    job_id,
                    client_file_id=f"client-{index:02d}",
                    label=f"row-{index:02d}",
                )

            files = await harness.service.list_import_job_files(harness.context, job_id)
            assert [record.occurrence.position for record in files] == list(range(1, 21))
            assert files[0].occurrence.status is ImportJobFileStatus.EXCLUDED

            with pytest.raises(ImportDomainError) as exc_info:
                await upload(
                    harness,
                    job_id,
                    client_file_id="client-20",
                    label="row-20",
                )
            assert_domain_error(exc_info.value, status_code=413)
            assert await count_rows(harness.session, ImportJobFile) == 20
            assert await count_rows(harness.session, StoredImportFile) == 20
            assert len(stored_blob_paths(harness.storage)) == 20

    asyncio.run(scenario())


def test_bulk_byte_limit_counts_excluded_occurrences_and_cleans_rejected_candidate() -> None:
    async def scenario() -> None:
        first_content = valid_csv("byte-a")
        second_content = valid_csv("byte-b")
        configured_limit = len(first_content) + len(second_content)
        async with bulk_service_harness(max_batch_bytes=configured_limit) as harness:
            job = await create_bulk(harness)
            job_id = job.id
            first = await upload(
                harness,
                job_id,
                client_file_id="byte-a",
                label="byte-a",
            )
            await harness.service.exclude_import_job_file(
                harness.context,
                job_id,
                first.record.occurrence.id,
                ip="127.0.0.1",
                user_agent="phase2-bulk-service-test",
            )
            await upload(
                harness,
                job_id,
                client_file_id="byte-b",
                label="byte-b",
            )
            assert sum(path.stat().st_size for path in stored_blob_paths(harness.storage)) == (
                configured_limit
            )

            with pytest.raises(ImportDomainError) as exc_info:
                await upload(
                    harness,
                    job_id,
                    client_file_id="byte-c",
                    label="byte-c",
                )
            assert_domain_error(exc_info.value, status_code=413)
            assert len(stored_blob_paths(harness.storage)) == 2
            assert await count_rows(harness.session, ImportJobFile) == 2
            assert await count_rows(harness.session, StoredImportFile) == 2

    asyncio.run(scenario())


def test_bulk_file_mutations_are_frozen_after_first_preview_revision() -> None:
    async def scenario() -> None:
        # Non-Draft status alone freezes uploads, even before a revision exists.
        async with bulk_service_harness() as harness:
            job = await create_bulk(harness)
            job_id = job.id
            uploaded = await upload(
                harness,
                job_id,
                client_file_id="frozen-a",
                label="frozen-a",
            )
            occurrence = uploaded.record.occurrence
            occurrence_id = occurrence.id
            original_acquisition = occurrence.source_acquired_at
            job.status = ImportJobStatus.PREVIEW_READY
            await harness.session.commit()

            with pytest.raises(ImportDomainError) as upload_error:
                await upload(
                    harness,
                    job_id,
                    client_file_id="frozen-b",
                    label="frozen-b",
                )
            assert_domain_error(upload_error.value, status_code=409)

            persisted = await harness.session.get(ImportJobFile, occurrence_id)
            assert persisted is not None
            assert persisted.status is ImportJobFileStatus.UPLOADED
            assert persisted.source_acquired_at == original_acquisition
            assert len(stored_blob_paths(harness.storage)) == 1
            assert await count_rows(harness.session, ImportJobFile) == 1

        # A non-zero revision independently freezes acquisition updates.
        async with bulk_service_harness() as harness:
            job = await create_bulk(harness)
            uploaded = await upload(
                harness,
                job.id,
                client_file_id="revision-a",
                label="revision-a",
            )
            occurrence_id = uploaded.record.occurrence.id
            job.preview_revision = 1
            assert job.status is ImportJobStatus.DRAFT
            await harness.session.commit()
            with pytest.raises(ImportDomainError) as update_error:
                await harness.service.update_import_job_file_source_acquired_at(
                    harness.context,
                    job.id,
                    occurrence_id,
                    source_acquired_at=harness.clock.now() - timedelta(days=1),
                    ip="127.0.0.1",
                    user_agent="phase2-bulk-service-test",
                )
            assert_domain_error(update_error.value, status_code=409)

        # Exclude has the same Draft/revision guard rather than a looser path.
        async with bulk_service_harness() as harness:
            job = await create_bulk(harness)
            uploaded = await upload(
                harness,
                job.id,
                client_file_id="exclude-frozen",
                label="exclude-frozen",
            )
            occurrence_id = uploaded.record.occurrence.id
            job.status = ImportJobStatus.PREVIEW_READY
            await harness.session.commit()
            with pytest.raises(ImportDomainError) as exclude_error:
                await harness.service.exclude_import_job_file(
                    harness.context,
                    job.id,
                    occurrence_id,
                    ip="127.0.0.1",
                    user_agent="phase2-bulk-service-test",
                )
            assert_domain_error(exclude_error.value, status_code=409)

    asyncio.run(scenario())


def test_source_acquired_at_requires_timezone_obeys_clock_skew_and_patch_confirms() -> None:
    async def scenario() -> None:
        async with bulk_service_harness() as harness:
            job = await create_bulk(harness)
            job_id = job.id
            with pytest.raises(ImportDomainError) as naive_error:
                await upload(
                    harness,
                    job_id,
                    client_file_id="naive",
                    label="naive",
                    source_acquired_at=datetime(2026, 8, 12, 4, 0),
                )
            assert_domain_error(naive_error.value, status_code=422)
            await refresh_context(harness)
            with pytest.raises(ImportDomainError) as future_error:
                await upload(
                    harness,
                    job_id,
                    client_file_id="future",
                    label="future",
                    source_acquired_at=harness.clock.now() + timedelta(minutes=5, seconds=1),
                )
            assert_domain_error(future_error.value, status_code=422)
            await refresh_context(harness)
            assert stored_blob_paths(harness.storage) == []

            boundary = harness.clock.now() + timedelta(minutes=5)
            uploaded = await upload(
                harness,
                job_id,
                client_file_id="boundary",
                label="boundary",
                source_acquired_at=boundary,
            )
            occurrence = uploaded.record.occurrence
            assert occurrence.source_acquired_at_origin is SourceAcquiredAtOrigin.USER_CONFIRMED
            assert occurrence.source_acquired_at == boundary

            patched_time = harness.clock.now() - timedelta(days=2)
            updated = await harness.service.update_import_job_file_source_acquired_at(
                harness.context,
                job_id,
                occurrence.id,
                source_acquired_at=patched_time,
                ip="127.0.0.1",
                user_agent="phase2-bulk-service-test",
            )
            assert updated.occurrence.source_acquired_at is not None
            assert as_utc(updated.occurrence.source_acquired_at) == patched_time
            assert (
                updated.occurrence.source_acquired_at_origin
                is SourceAcquiredAtOrigin.USER_CONFIRMED
            )

            with pytest.raises(ImportDomainError) as patch_naive_error:
                await harness.service.update_import_job_file_source_acquired_at(
                    harness.context,
                    job.id,
                    occurrence.id,
                    source_acquired_at=datetime(2026, 8, 10, 4, 0),
                    ip="127.0.0.1",
                    user_agent="phase2-bulk-service-test",
                )
            assert_domain_error(patch_naive_error.value, status_code=422)
            with pytest.raises(ImportDomainError) as patch_future_error:
                await harness.service.update_import_job_file_source_acquired_at(
                    harness.context,
                    job.id,
                    occurrence.id,
                    source_acquired_at=harness.clock.now() + timedelta(minutes=5, seconds=1),
                    ip="127.0.0.1",
                    user_agent="phase2-bulk-service-test",
                )
            assert_domain_error(patch_future_error.value, status_code=422)

            # A delayed PATCH remains bounded by the original server acceptance
            # time, not by the day on which the user submits the correction.
            harness.clock.current += timedelta(days=10)
            received_at = as_utc(occurrence.created_at)
            with pytest.raises(ImportDomainError) as delayed_patch_error:
                await harness.service.update_import_job_file_source_acquired_at(
                    harness.context,
                    job.id,
                    occurrence.id,
                    source_acquired_at=received_at + timedelta(minutes=5, seconds=1),
                    ip="127.0.0.1",
                    user_agent="phase2-bulk-service-test",
                )
            assert_domain_error(delayed_patch_error.value, status_code=422)

            source_audits = list(
                await harness.session.scalars(
                    select(AuditLog).where(
                        AuditLog.action == AuditAction.IMPORT_FILE_SOURCE_ACQUIRED_AT_UPDATED
                    )
                )
            )
            assert len(source_audits) == 1

    asyncio.run(scenario())


def test_historical_sha_reuse_requires_explicit_acquisition_confirmation() -> None:
    async def scenario() -> None:
        async with bulk_service_harness() as harness:
            first_job = await create_bulk(harness)
            first = await upload(
                harness,
                first_job.id,
                client_file_id="history-a",
                label="historical",
            )
            second_job = await create_bulk(harness)
            historical = await upload(
                harness,
                second_job.id,
                client_file_id="history-b",
                label="historical",
            )

            assert historical.idempotent is False
            assert historical.record.occurrence.id != first.record.occurrence.id
            assert historical.source_acquired_at_confirmation_required is True
            assert (
                historical.record.occurrence.source_acquired_at_origin
                is SourceAcquiredAtOrigin.SERVER_DEFAULT
            )
            assert await count_rows(harness.session, StoredImportFile) == 1
            assert await count_rows(harness.session, ImportJobFile) == 2
            assert len(stored_blob_paths(harness.storage)) == 1

            original_records = await harness.service.list_import_job_files(
                harness.context, first_job.id
            )
            assert len(original_records) == 1
            assert original_records[0].occurrence.source_acquired_at_confirmation_required is False

            historical_acquisition = historical.record.occurrence.source_acquired_at
            assert historical_acquisition is not None
            confirmed = await harness.service.update_import_job_file_source_acquired_at(
                harness.context,
                second_job.id,
                historical.record.occurrence.id,
                source_acquired_at=historical_acquisition,
                ip="127.0.0.1",
                user_agent="phase2-bulk-service-test",
            )
            assert confirmed.occurrence.source_acquired_at_confirmation_required is False
            assert (
                confirmed.occurrence.source_acquired_at_origin
                is SourceAcquiredAtOrigin.USER_CONFIRMED
            )

            third_job = await create_bulk(harness)
            explicit = await upload(
                harness,
                third_job.id,
                client_file_id="history-c",
                label="historical",
                source_acquired_at=harness.clock.now() - timedelta(days=1),
            )
            assert explicit.source_acquired_at_confirmation_required is False
            assert (
                explicit.record.occurrence.source_acquired_at_origin
                is SourceAcquiredAtOrigin.USER_CONFIRMED
            )

    asyncio.run(scenario())


def test_new_occurrence_audit_is_not_duplicated_by_idempotent_or_sha_alias_retries() -> None:
    async def scenario() -> None:
        async with bulk_service_harness() as harness:
            job = await create_bulk(harness)
            first = await upload(
                harness,
                job.id,
                client_file_id="audit-a",
                label="audit-content",
            )
            original_acquisition = first.record.occurrence.source_acquired_at
            replay = await upload(
                harness,
                job.id,
                client_file_id="audit-a",
                label="audit-content",
                source_acquired_at=harness.clock.now() - timedelta(days=10),
            )
            alias = await upload(
                harness,
                job.id,
                client_file_id="audit-b",
                label="audit-content",
            )

            assert replay.idempotent is True
            assert alias.idempotent is True
            assert replay.record.occurrence.id == first.record.occurrence.id
            assert alias.record.occurrence.id == first.record.occurrence.id
            assert replay.record.occurrence.source_acquired_at == original_acquisition
            assert await count_rows(harness.session, ImportJobFile) == 1
            assert await count_rows(harness.session, ImportJobFileClientId) == 2
            upload_audits = list(
                await harness.session.scalars(
                    select(AuditLog).where(AuditLog.action == AuditAction.IMPORT_FILE_UPLOADED)
                )
            )
            assert len(upload_audits) == 1

    asyncio.run(scenario())


def test_excluding_file_keeps_blob_lineage_and_records_audit() -> None:
    async def scenario() -> None:
        async with bulk_service_harness() as harness:
            job = await create_bulk(harness)
            uploaded = await upload(
                harness,
                job.id,
                client_file_id="exclude",
                label="exclude",
            )
            blob = stored_blob_paths(harness.storage)
            assert len(blob) == 1

            excluded = await harness.service.exclude_import_job_file(
                harness.context,
                job.id,
                uploaded.record.occurrence.id,
                ip="127.0.0.1",
                user_agent="phase2-bulk-service-test",
            )

            assert excluded.occurrence.status is ImportJobFileStatus.EXCLUDED
            assert excluded.occurrence.excluded_at is not None
            assert as_utc(excluded.occurrence.excluded_at) == harness.clock.now()
            assert stored_blob_paths(harness.storage) == blob
            assert await count_rows(harness.session, StoredImportFile) == 1
            assert await count_rows(harness.session, ImportJobFile) == 1
            audit_actions = list(await harness.session.scalars(select(AuditLog.action)))
            assert audit_actions.count(AuditAction.IMPORT_FILE_EXCLUDED) == 1

    asyncio.run(scenario())


def test_upload_database_failure_rolls_back_rows_and_compensates_storage() -> None:
    async def scenario() -> None:
        async with bulk_service_harness() as harness:
            job = await create_bulk(harness)

            async def fail_commit() -> None:
                raise RuntimeError("simulated commit failure")

            with patch.object(harness.session, "commit", new=fail_commit):
                with pytest.raises(RuntimeError, match="simulated commit failure"):
                    await upload(
                        harness,
                        job.id,
                        client_file_id="commit-failure",
                        label="commit-failure",
                    )

            assert stored_blob_paths(harness.storage) == []
            assert await count_rows(harness.session, StoredImportFile) == 0
            assert await count_rows(harness.session, ImportJobFile) == 0
            assert await count_rows(harness.session, ImportJobFileClientId) == 0

    asyncio.run(scenario())


def test_upload_exception_path_rolls_back_and_never_commits() -> None:
    async def scenario() -> None:
        async with bulk_service_harness() as harness:
            job = await create_bulk(harness)

            with (
                patch.object(
                    harness.session,
                    "commit",
                    wraps=harness.session.commit,
                ) as commit_spy,
                patch.object(
                    harness.session,
                    "rollback",
                    wraps=harness.session.rollback,
                ) as rollback_spy,
            ):
                with pytest.raises(ImportDomainError) as exc_info:
                    await upload_content(
                        harness,
                        job.id,
                        client_file_id="invalid-transaction",
                        filename="invalid-transaction.csv",
                        declared_mime="text/csv",
                        content=b"header\x00,value\n",
                    )

            assert exc_info.value.code == "INVALID_CSV"
            assert_domain_error(exc_info.value, status_code=422)
            assert rollback_spy.await_count == 1
            assert commit_spy.await_count == 0
            assert stored_blob_paths(harness.storage) == []
            assert await count_rows(harness.session, StoredImportFile) == 0
            assert await count_rows(harness.session, ImportJobFile) == 0
            assert await count_rows(harness.session, ImportJobFileClientId) == 0

    asyncio.run(scenario())


def test_candidate_cleanup_retries_a_transient_delete_failure() -> None:
    async def scenario() -> None:
        async with bulk_service_harness() as harness:
            job = await create_bulk(harness)
            real_delete = harness.storage.delete
            attempts = 0

            async def transient_delete(storage_key: str) -> None:
                nonlocal attempts
                attempts += 1
                if attempts == 1:
                    raise ImportDomainError(
                        "STORAGE_DELETE_FAILED",
                        "simulated transient cleanup failure",
                    )
                await real_delete(storage_key)

            with patch.object(harness.storage, "delete", new=transient_delete):
                with pytest.raises(ImportDomainError) as exc_info:
                    await upload_content(
                        harness,
                        job.id,
                        client_file_id="cleanup-retry",
                        filename="cleanup-retry.csv",
                        declared_mime="text/csv",
                        content=b"header\x00,value\n",
                    )

            assert exc_info.value.code == "INVALID_CSV"
            assert attempts == 2
            assert stored_blob_paths(harness.storage) == []
            assert await count_rows(harness.session, StoredImportFile) == 0
            assert await count_rows(harness.session, ImportJobFile) == 0
            assert await count_rows(harness.session, ImportJobFileClientId) == 0

    asyncio.run(scenario())


def test_commit_result_ambiguity_keeps_canonical_blob_when_commit_actually_succeeded() -> None:
    async def scenario() -> None:
        async with bulk_service_harness() as harness:
            job = await create_bulk(harness)
            real_commit = harness.session.commit
            successful_commits = 0

            async def commit_then_raise() -> None:
                nonlocal successful_commits
                await real_commit()
                successful_commits += 1
                raise RuntimeError("simulated unknown commit outcome")

            with patch.object(harness.session, "commit", new=commit_then_raise):
                with pytest.raises(RuntimeError, match="simulated unknown commit outcome"):
                    await upload(
                        harness,
                        job.id,
                        client_file_id="ambiguous-commit",
                        label="ambiguous-commit",
                    )

            assert successful_commits == 1
            assert await count_rows(harness.session, StoredImportFile) == 1
            assert await count_rows(harness.session, ImportJobFile) == 1
            assert await count_rows(harness.session, ImportJobFileClientId) == 1
            blobs = stored_blob_paths(harness.storage)
            assert len(blobs) == 1

            occurrence = await harness.session.scalar(select(ImportJobFile))
            stored_file = await harness.session.scalar(select(StoredImportFile))
            assert occurrence is not None
            assert stored_file is not None
            assert occurrence.stored_file_id == stored_file.id
            assert blobs[0].name == stored_file.storage_key
            assert await harness.storage.read(
                stored_file.storage_key,
                expected_size=stored_file.size,
                expected_sha256=stored_file.sha256,
            ) == valid_csv("ambiguous-commit")

    asyncio.run(scenario())


def test_legacy_commit_result_ambiguity_also_keeps_canonical_blob() -> None:
    async def scenario() -> None:
        async with bulk_service_harness() as harness:
            real_commit = harness.session.commit
            successful_commits = 0

            async def commit_then_raise() -> None:
                nonlocal successful_commits
                await real_commit()
                successful_commits += 1
                raise RuntimeError("simulated unknown legacy commit outcome")

            with patch.object(harness.session, "commit", new=commit_then_raise):
                with pytest.raises(
                    RuntimeError,
                    match="simulated unknown legacy commit outcome",
                ):
                    await harness.service.create_import_job(
                        harness.context,
                        collection_job_id=harness.collection.id,
                        filename="legacy-ambiguous.csv",
                        declared_mime="text/csv",
                        chunks=chunks(valid_csv("legacy-ambiguous")),
                        parse_task_id="legacy-ambiguous-task",
                        ip="127.0.0.1",
                        user_agent="phase2-bulk-service-test",
                    )

            assert successful_commits == 1
            assert await count_rows(harness.session, StoredImportFile) == 1
            assert await count_rows(harness.session, ImportJob) == 1
            assert await count_rows(harness.session, ImportJobFile) == 1
            assert await count_rows(harness.session, ImportJobFileClientId) == 0
            blobs = stored_blob_paths(harness.storage)
            assert len(blobs) == 1
            stored_file = await harness.session.scalar(select(StoredImportFile))
            assert stored_file is not None
            assert blobs[0].name == stored_file.storage_key
            assert await harness.storage.read(
                stored_file.storage_key,
                expected_size=stored_file.size,
                expected_sha256=stored_file.sha256,
            ) == valid_csv("legacy-ambiguous")

    asyncio.run(scenario())


def test_cancelled_upload_waits_for_commit_and_preserves_committed_lineage() -> None:
    async def scenario() -> None:
        async with bulk_service_harness() as harness:
            job = await create_bulk(harness)
            real_commit = harness.session.commit
            commit_started = asyncio.Event()
            allow_commit = asyncio.Event()

            async def delayed_commit() -> None:
                commit_started.set()
                await allow_commit.wait()
                await real_commit()

            with patch.object(harness.session, "commit", new=delayed_commit):
                task = asyncio.create_task(
                    upload(
                        harness,
                        job.id,
                        client_file_id="cancelled-request",
                        label="cancelled-request",
                    )
                )
                await commit_started.wait()
                task.cancel()
                allow_commit.set()
                with pytest.raises(asyncio.CancelledError):
                    await task

            assert await count_rows(harness.session, StoredImportFile) == 1
            assert await count_rows(harness.session, ImportJobFile) == 1
            assert await count_rows(harness.session, ImportJobFileClientId) == 1
            blobs = stored_blob_paths(harness.storage)
            assert len(blobs) == 1
            stored_file = await harness.session.scalar(select(StoredImportFile))
            assert stored_file is not None
            assert blobs[0].name == stored_file.storage_key

    asyncio.run(scenario())
