"""Processor-level gates for Phase 2 file parse and revision-zero batch staging."""

import asyncio
import csv
import hashlib
import io
from collections.abc import AsyncIterator, Iterable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import UUID, uuid4

import pytest
from backend_core.auth.enums import DepartmentStatus, OperatorStatus, Role
from backend_core.auth.models import Department, Operator
from backend_core.db import models as database_models  # noqa: F401
from backend_core.db.base import Base
from backend_core.imports.batch_processor import BatchImportProcessor
from backend_core.imports.enums import (
    CollectionJobStatus,
    ImportJobFailedStage,
    ImportJobFileStatus,
    ImportJobStatus,
    ImportRowAction,
    ImportSourceType,
    SourceAcquiredAtOrigin,
    StoredFileType,
)
from backend_core.imports.errors import ImportDomainError
from backend_core.imports.mappings import HUITUN_FIELD_MAPPING
from backend_core.imports.models import (
    CollectionJob,
    ImportJob,
    ImportJobFile,
    ImportRow,
    StoredImportFile,
)
from backend_core.imports.parsers import ParserLimits
from backend_core.imports.preview_processor import UnifiedPreviewProcessor
from backend_core.imports.repository import ImportRepository
from backend_core.imports.storage import LocalStorageAdapter
from backend_core.influencers.enums import DataSource, Platform
from backend_core.influencers.models import (
    Influencer,
    InfluencerContact,
    InfluencerCurrentMetrics,
    InfluencerMetricSnapshot,
    InfluencerPlatformAccount,
    InfluencerSourceState,
    PlatformAccountSourceIdentity,
)
from openpyxl import Workbook
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

MAPPING = {
    "name": "nickname",
    "account_id": "platform_account_id",
    "external_id": "external_source_id",
    "profile_url": "profile_url",
    "updated_at": "source_updated_at",
    "followers": "followers_count",
    "email": "email",
}
HEADERS = (*MAPPING, "fixture_nonce")


@dataclass(frozen=True, slots=True)
class BatchFixture:
    job_id: UUID
    files: tuple[ImportJobFile, ...]


@asynccontextmanager
async def processor_harness() -> (
    AsyncIterator[tuple[async_sessionmaker[AsyncSession], LocalStorageAdapter]]
):
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    with TemporaryDirectory(prefix="phase2-batch-processor-") as directory:
        yield factory, LocalStorageAdapter(Path(directory))
    await engine.dispose()


async def _one_chunk(content: bytes) -> AsyncIterator[bytes]:
    yield content


def _row(
    account_id: str,
    *,
    external_id: str = "",
    profile_url: str = "",
    name: str | None = None,
    updated_at: str = "2026-08-10 12:00:00",
    followers: str = "100",
    email: str = "",
    nonce: str = "fixture",
) -> dict[str, str]:
    return {
        "name": name or f"Fixture {account_id}",
        "account_id": account_id,
        "external_id": external_id,
        "profile_url": profile_url,
        "updated_at": updated_at,
        "followers": followers,
        "email": email,
        "fixture_nonce": nonce,
    }


def _csv(rows: Iterable[dict[str, str]], *, headers: Iterable[str] = HEADERS) -> bytes:
    ordered_headers = tuple(headers)
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=ordered_headers)
    writer.writeheader()
    for row in rows:
        writer.writerow({header: row.get(header, "") for header in ordered_headers})
    return stream.getvalue().encode()


def _xlsx(rows: Iterable[dict[str, str]], *, headers: Iterable[str]) -> bytes:
    ordered_headers = tuple(headers)
    stream = io.BytesIO()
    workbook = Workbook()
    try:
        worksheet = workbook.active
        assert worksheet is not None
        worksheet.append(list(ordered_headers))
        for row in rows:
            worksheet.append([row.get(header, "") for header in ordered_headers])
        workbook.save(stream)
    finally:
        workbook.close()
    return stream.getvalue()


def _huitun_rows(prefix: str, count: int = 500) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for index in range(count):
        values = {header: "--" for header in HUITUN_FIELD_MAPPING}
        values.update(
            {
                "达人名称": f"Synthetic {prefix} {index}",
                "达人官方地址": (f"https://www.xiaohongshu.com/user/profile/{prefix}-{index}"),
                "更新时间": "2026-08-10 12:00:00",
                "粉丝数": str(10_000 + index),
            }
        )
        rows.append(values)
    return rows


async def _stored_file(
    session: AsyncSession,
    storage: LocalStorageAdapter,
    content: bytes,
    *,
    file_type: StoredFileType = StoredFileType.CSV,
) -> StoredImportFile:
    suffix = ".csv" if file_type is StoredFileType.CSV else ".xlsx"
    detected_mime = (
        "text/csv"
        if file_type is StoredFileType.CSV
        else "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    stored = await storage.store(_one_chunk(content), suffix=suffix, max_bytes=25 * 1024 * 1024)
    model = StoredImportFile(
        sha256=stored.sha256,
        storage_key=stored.storage_key,
        size=stored.size,
        detected_type=file_type,
        detected_mime=detected_mime,
        expires_at=datetime.now(UTC) + timedelta(days=30),
    )
    session.add(model)
    await session.flush()
    return model


async def _seed(
    factory: async_sessionmaker[AsyncSession],
    storage: LocalStorageAdapter,
    contents: Iterable[bytes],
    *,
    mappings: Iterable[dict[str, str] | None] | None = None,
    statuses: Iterable[ImportJobFileStatus] | None = None,
    confirmation_required: Iterable[bool] | None = None,
    file_types: Iterable[StoredFileType] | None = None,
    source_type: ImportSourceType = ImportSourceType.GENERIC_CSV,
) -> BatchFixture:
    file_contents = list(contents)
    mapping_list = list(mappings or [dict(MAPPING) for _ in file_contents])
    status_list = list(statuses or [ImportJobFileStatus.PARSING for _ in file_contents])
    confirmation_list = list(confirmation_required or [False for _ in file_contents])
    file_type_list = list(file_types or [StoredFileType.CSV for _ in file_contents])
    assert (
        len(file_contents)
        == len(mapping_list)
        == len(status_list)
        == len(confirmation_list)
        == len(file_type_list)
    )
    async with factory() as session:
        department = Department(
            name=f"Batch processor fixture {uuid4().hex}",
            password_hash="not-used-by-test",
            status=DepartmentStatus.ACTIVE,
            session_days=30,
        )
        session.add(department)
        await session.flush()
        operator = Operator(
            department_id=department.id,
            name="Processor fixture operator",
            role=Role.OPERATOR,
            status=OperatorStatus.ACTIVE,
        )
        session.add(operator)
        await session.flush()
        collection = CollectionJob(
            name="Batch processor collection",
            industry="test",
            purpose="verify Task 3 staging",
            target_action="import",
            target_count=10_000,
            department_id=department.id,
            owner_operator_id=operator.id,
            source_type=source_type,
            status=CollectionJobStatus.ACTIVE,
        )
        session.add(collection)
        await session.flush()
        job = ImportJob(
            collection_job_id=collection.id,
            department_id=department.id,
            operator_id=operator.id,
            source_type=source_type,
            status=ImportJobStatus.DRAFT,
            preview_revision=0,
        )
        session.add(job)
        await session.flush()
        occurrences: list[ImportJobFile] = []
        for position, (content, mapping, status, requires_confirmation, file_type) in enumerate(
            zip(
                file_contents,
                mapping_list,
                status_list,
                confirmation_list,
                file_type_list,
                strict=True,
            ),
            start=1,
        ):
            stored = await _stored_file(session, storage, content, file_type=file_type)
            task_id = f"parse-{job.id}-{position}"
            suffix = ".csv" if file_type is StoredFileType.CSV else ".xlsx"
            declared_mime = (
                "text/csv"
                if file_type is StoredFileType.CSV
                else "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            )
            occurrence = ImportJobFile(
                import_job_id=job.id,
                stored_file_id=stored.id,
                position=position,
                original_filename=f"sanitized-{position}{suffix}",
                declared_mime=declared_mime,
                status=status,
                source_acquired_at=datetime.now(UTC),
                source_acquired_at_origin=SourceAcquiredAtOrigin.SERVER_DEFAULT,
                source_acquired_at_confirmation_required=requires_confirmation,
                field_mapping=mapping,
                parse_task_id=None if status is ImportJobFileStatus.EXCLUDED else task_id,
                parse_attempts=0 if status is ImportJobFileStatus.EXCLUDED else 1,
                excluded_at=datetime.now(UTC) if status is ImportJobFileStatus.EXCLUDED else None,
            )
            session.add(occurrence)
            occurrences.append(occurrence)
        await session.commit()
        return BatchFixture(job_id=job.id, files=tuple(occurrences))


def _processor(
    session: AsyncSession,
    storage: LocalStorageAdapter,
    *,
    parser_max_rows: int = 20_000,
    parser_max_warnings: int = 1_000,
    max_batch_rows: int = 10_000,
) -> BatchImportProcessor:
    return BatchImportProcessor(
        session,
        storage,
        parser_limits=ParserLimits(
            max_rows=parser_max_rows,
            max_warnings=parser_max_warnings,
            max_columns=100,
            max_cells=max(parser_max_rows, 1) * len(HEADERS) + len(HEADERS),
        ),
        max_batch_rows=max_batch_rows,
    )


async def _parse(
    factory: async_sessionmaker[AsyncSession],
    storage: LocalStorageAdapter,
    job_id: UUID,
    occurrence: ImportJobFile,
    *,
    task_id: str | None = None,
    parser_max_rows: int = 20_000,
    parser_max_warnings: int = 1_000,
    max_batch_rows: int = 10_000,
) -> dict[str, object]:
    async with factory() as session:
        return await _processor(
            session,
            storage,
            parser_max_rows=parser_max_rows,
            parser_max_warnings=parser_max_warnings,
            max_batch_rows=max_batch_rows,
        ).parse_file(
            job_id,
            occurrence.id,
            task_id or str(occurrence.parse_task_id),
        )


async def _rows(session: AsyncSession, job_id: UUID) -> list[ImportRow]:
    return list(
        await session.scalars(
            select(ImportRow)
            .join(ImportJobFile, ImportJobFile.id == ImportRow.import_job_file_id)
            .where(ImportRow.import_job_id == job_id)
            .order_by(ImportJobFile.position, ImportRow.row_number, ImportRow.id)
        )
    )


async def _assert_job_is_draft(session: AsyncSession, job_id: UUID) -> ImportJob:
    job = await session.get(ImportJob, job_id)
    assert job is not None
    assert job.status is ImportJobStatus.DRAFT
    assert job.preview_revision == 0
    assert job.preview_summary is None
    return job


async def _business_count(session: AsyncSession) -> int:
    models = (
        Influencer,
        InfluencerPlatformAccount,
        PlatformAccountSourceIdentity,
        InfluencerSourceState,
        InfluencerContact,
        InfluencerCurrentMetrics,
        InfluencerMetricSnapshot,
    )
    total = 0
    for model in models:
        total += int(await session.scalar(select(func.count()).select_from(model)) or 0)
    return total


async def _queue_preview(
    factory: async_sessionmaker[AsyncSession],
    job_id: UUID,
    task_id: str,
) -> None:
    async with factory() as session:
        job = await session.get(ImportJob, job_id)
        assert job is not None
        job.status = ImportJobStatus.PREVIEWING
        job.parse_task_id = task_id
        await session.commit()


def test_unified_preview_promotes_complete_staging_once_without_business_writes() -> None:
    async def scenario() -> None:
        async with processor_harness() as (factory, storage):
            fixture = await _seed(
                factory,
                storage,
                [
                    _csv([_row("owner"), _row("owner", nonce="duplicate")]),
                    _csv([_row("second")]),
                ],
            )
            for occurrence in fixture.files:
                await _parse(factory, storage, fixture.job_id, occurrence)

            task_id = uuid4().hex
            await _queue_preview(factory, fixture.job_id, task_id)

            async with factory() as session:
                processor = UnifiedPreviewProcessor(
                    session,
                    storage,
                    parser_limits=ParserLimits(max_rows=10_000),
                )
                result = await processor.build(fixture.job_id, task_id)
                assert result == {
                    "import_job_id": str(fixture.job_id),
                    "status": "preview_ready",
                    "preview_revision": 1,
                    "idempotent": False,
                }

            async with factory() as session:
                job = await session.get(ImportJob, fixture.job_id)
                assert job is not None
                assert job.preview_revision == 1
                assert job.preview_summary is not None
                assert job.preview_summary["raw_rows"] == 3
                assert job.preview_summary["internal_duplicate_rows"] == 1
                assert job.preview_summary["unique_rows"] == 2
                assert job.preview_summary["screening_unknown_rows"] == 2
                rows = await _rows(session, fixture.job_id)
                assert {row.preview_revision for row in rows} == {1}
                assert len({row.plan_hash for row in rows}) == 3
                assert all(len(row.plan_hash) == 64 for row in rows)
                assert await _business_count(session) == 0

            async with factory() as session:
                replay = await UnifiedPreviewProcessor(
                    session,
                    storage,
                    parser_limits=ParserLimits(max_rows=10_000),
                ).build(fixture.job_id, task_id)
                assert replay["idempotent"] is True
                assert replay["preview_revision"] == 1

    asyncio.run(scenario())


def test_unified_preview_reparses_blob_instead_of_trusting_staging() -> None:
    async def scenario() -> None:
        async with processor_harness() as (factory, storage):
            fixture = await _seed(factory, storage, [_csv([_row("blob-authority")])])
            occurrence = fixture.files[0]
            await _parse(factory, storage, fixture.job_id, occurrence)

            async with factory() as session:
                staged = (await _rows(session, fixture.job_id))[0]
                poisoned_normalized = dict(staged.normalized_data or {})
                poisoned_normalized["display_name"] = "POISONED STAGING VALUE"
                staged.normalized_data = poisoned_normalized
                staged.raw_data = {**staged.raw_data, "name": "POISONED RAW VALUE"}
                await session.commit()

            task_id = uuid4().hex
            await _queue_preview(factory, fixture.job_id, task_id)
            async with factory() as session:
                await UnifiedPreviewProcessor(
                    session,
                    storage,
                    parser_limits=ParserLimits(max_rows=10_000),
                ).build(fixture.job_id, task_id)

            async with factory() as session:
                rebuilt = (await _rows(session, fixture.job_id))[0]
                assert rebuilt.preview_revision == 1
                assert rebuilt.raw_data["name"] == "Fixture blob-authority"
                assert rebuilt.normalized_data is not None
                assert rebuilt.normalized_data["display_name"] == "Fixture blob-authority"
                assert await _business_count(session) == 0

    asyncio.run(scenario())


def test_unified_preview_blob_integrity_failure_preserves_last_complete_revision() -> None:
    async def scenario() -> None:
        async with processor_harness() as (factory, storage):
            fixture = await _seed(factory, storage, [_csv([_row("immutable-blob")])])
            occurrence = fixture.files[0]
            await _parse(factory, storage, fixture.job_id, occurrence)

            first_task = uuid4().hex
            await _queue_preview(factory, fixture.job_id, first_task)
            async with factory() as session:
                await UnifiedPreviewProcessor(
                    session,
                    storage,
                    parser_limits=ParserLimits(max_rows=10_000),
                ).build(fixture.job_id, first_task)

            async with factory() as session:
                complete_job = await session.get(ImportJob, fixture.job_id)
                complete_rows = await _rows(session, fixture.job_id)
                stored = await session.get(StoredImportFile, occurrence.stored_file_id)
                assert complete_job is not None and complete_job.preview_summary is not None
                assert stored is not None
                complete_summary = dict(complete_job.preview_summary)
                complete_hash = complete_rows[0].plan_hash
                target = storage.root / stored.storage_key
                original = target.read_bytes()
                target.write_bytes(bytes([original[0] ^ 1]) + original[1:])
                assert target.stat().st_size == stored.size

            second_task = uuid4().hex
            await _queue_preview(factory, fixture.job_id, second_task)
            async with factory() as session:
                with pytest.raises(ImportDomainError) as error:
                    await UnifiedPreviewProcessor(
                        session,
                        storage,
                        parser_limits=ParserLimits(max_rows=10_000),
                    ).build(fixture.job_id, second_task)
                assert error.value.code == "FILE_INTEGRITY_FAILED"

            async with factory() as session:
                failed = await session.get(ImportJob, fixture.job_id)
                rows = await _rows(session, fixture.job_id)
                file_model = await session.get(ImportJobFile, occurrence.id)
                assert failed is not None
                assert failed.status is ImportJobStatus.FAILED
                assert failed.failed_stage is ImportJobFailedStage.PREVIEW
                assert failed.preview_revision == 1
                assert failed.preview_summary == complete_summary
                assert len(rows) == 1
                assert rows[0].preview_revision == 1
                assert rows[0].plan_hash == complete_hash
                assert file_model is not None
                assert file_model.status is ImportJobFileStatus.READY
                assert await _business_count(session) == 0

    asyncio.run(scenario())


def test_unified_preview_safe_parser_rejects_corrupt_blob_after_integrity_check() -> None:
    async def scenario() -> None:
        async with processor_harness() as (factory, storage):
            fixture = await _seed(factory, storage, [_csv([_row("parser-safety")])])
            occurrence = fixture.files[0]
            await _parse(factory, storage, fixture.job_id, occurrence)

            async with factory() as session:
                stored = await session.get(StoredImportFile, occurrence.stored_file_id)
                assert stored is not None
                corrupt = b"name,account_id\nunsafe,\x00value\n"
                target = storage.root / stored.storage_key
                target.write_bytes(corrupt)
                stored.size = len(corrupt)
                stored.sha256 = hashlib.sha256(corrupt).hexdigest()
                await session.commit()

            task_id = uuid4().hex
            await _queue_preview(factory, fixture.job_id, task_id)
            async with factory() as session:
                with pytest.raises(ImportDomainError) as error:
                    await UnifiedPreviewProcessor(
                        session,
                        storage,
                        parser_limits=ParserLimits(max_rows=10_000),
                    ).build(fixture.job_id, task_id)
                assert error.value.code == "INVALID_CSV"

            async with factory() as session:
                failed = await session.get(ImportJob, fixture.job_id)
                rows = await _rows(session, fixture.job_id)
                assert failed is not None
                assert failed.status is ImportJobStatus.FAILED
                assert failed.failed_stage is ImportJobFailedStage.PREVIEW
                assert failed.preview_revision == 0
                assert {row.preview_revision for row in rows} == {0}
                assert await _business_count(session) == 0

    asyncio.run(scenario())


def test_unified_preview_mapping_fingerprint_failure_is_atomic() -> None:
    async def scenario() -> None:
        async with processor_harness() as (factory, storage):
            fixture = await _seed(factory, storage, [_csv([_row("mapping-freeze")])])
            occurrence = fixture.files[0]
            await _parse(factory, storage, fixture.job_id, occurrence)
            async with factory() as session:
                file_model = await session.get(ImportJobFile, occurrence.id)
                assert file_model is not None and file_model.mapping_hash is not None
                file_model.field_mapping = {"name": "nickname"}
                await session.commit()

            task_id = uuid4().hex
            await _queue_preview(factory, fixture.job_id, task_id)
            async with factory() as session:
                with pytest.raises(ImportDomainError) as error:
                    await UnifiedPreviewProcessor(
                        session,
                        storage,
                        parser_limits=ParserLimits(max_rows=10_000),
                    ).build(fixture.job_id, task_id)
                assert error.value.code == "IMPORT_PREVIEW_REVALIDATION_FAILED"

            async with factory() as session:
                failed = await session.get(ImportJob, fixture.job_id)
                rows = await _rows(session, fixture.job_id)
                assert failed is not None
                assert failed.status is ImportJobStatus.FAILED
                assert failed.failed_stage is ImportJobFailedStage.PREVIEW
                assert failed.preview_revision == 0
                assert {row.preview_revision for row in rows} == {0}
                assert await _business_count(session) == 0

    asyncio.run(scenario())


def test_unified_preview_stale_token_is_noop_without_storage_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        async with processor_harness() as (factory, storage):
            fixture = await _seed(factory, storage, [_csv([_row("stale-token")])])
            await _parse(factory, storage, fixture.job_id, fixture.files[0])
            current_task = uuid4().hex
            await _queue_preview(factory, fixture.job_id, current_task)

            read_count = 0
            original_read = storage.read

            async def tracked_read(*args: object, **kwargs: object) -> bytes:
                nonlocal read_count
                read_count += 1
                return await original_read(*args, **kwargs)  # type: ignore[arg-type]

            monkeypatch.setattr(storage, "read", tracked_read)
            async with factory() as session:
                result = await UnifiedPreviewProcessor(
                    session,
                    storage,
                    parser_limits=ParserLimits(max_rows=10_000),
                ).build(fixture.job_id, "stale-delivery")
                assert result["idempotent"] is True
                assert result["status"] == ImportJobStatus.PREVIEWING.value
            assert read_count == 0

            async with factory() as session:
                job = await session.get(ImportJob, fixture.job_id)
                assert job is not None
                assert job.status is ImportJobStatus.PREVIEWING
                assert job.parse_task_id == current_task
                assert job.failed_stage is None

    asyncio.run(scenario())


def test_unified_preview_cancellation_rolls_back_without_marking_failed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        async with processor_harness() as (factory, storage):
            fixture = await _seed(factory, storage, [_csv([_row("cancelled")])])
            await _parse(factory, storage, fixture.job_id, fixture.files[0])
            task_id = uuid4().hex
            await _queue_preview(factory, fixture.job_id, task_id)

            async def cancel_read(*_args: object, **_kwargs: object) -> bytes:
                raise asyncio.CancelledError

            monkeypatch.setattr(storage, "read", cancel_read)
            async with factory() as session:
                with pytest.raises(asyncio.CancelledError):
                    await UnifiedPreviewProcessor(
                        session,
                        storage,
                        parser_limits=ParserLimits(max_rows=10_000),
                    ).build(fixture.job_id, task_id)

            async with factory() as session:
                job = await session.get(ImportJob, fixture.job_id)
                assert job is not None
                assert job.status is ImportJobStatus.PREVIEWING
                assert job.failed_stage is None
                assert job.preview_revision == 0

    asyncio.run(scenario())


def test_preview_retry_exhaustion_is_current_token_guarded() -> None:
    async def scenario() -> None:
        async with processor_harness() as (factory, storage):
            fixture = await _seed(factory, storage, [_csv([_row("retry-exhausted")])])
            await _parse(factory, storage, fixture.job_id, fixture.files[0])
            task_id = uuid4().hex
            await _queue_preview(factory, fixture.job_id, task_id)

            async with factory() as session:
                processor = UnifiedPreviewProcessor(
                    session,
                    storage,
                    parser_limits=ParserLimits(max_rows=10_000),
                )
                await processor.mark_failed_after_retry_exhausted(fixture.job_id, "stale-delivery")
            async with factory() as session:
                current = await session.get(ImportJob, fixture.job_id)
                assert current is not None
                assert current.status is ImportJobStatus.PREVIEWING

            async with factory() as session:
                await UnifiedPreviewProcessor(
                    session,
                    storage,
                    parser_limits=ParserLimits(max_rows=10_000),
                ).mark_failed_after_retry_exhausted(fixture.job_id, task_id)
            async with factory() as session:
                failed = await session.get(ImportJob, fixture.job_id)
                assert failed is not None
                assert failed.status is ImportJobStatus.FAILED
                assert failed.failed_stage is ImportJobFailedStage.PREVIEW
                assert failed.error_code == "HEAVY_IMPORT_RETRY_EXHAUSTED"
                assert failed.error_message == (
                    "Unified Preview could not acquire the heavy-import slot"
                )

    asyncio.run(scenario())


def test_unified_preview_lock_order_precedes_prefetch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        async with processor_harness() as (factory, storage):
            fixture = await _seed(factory, storage, [_csv([_row("lock-order")])])
            await _parse(factory, storage, fixture.job_id, fixture.files[0])
            task_id = uuid4().hex
            await _queue_preview(factory, fixture.job_id, task_id)

            async with factory() as session:
                processor = UnifiedPreviewProcessor(
                    session,
                    storage,
                    parser_limits=ParserLimits(max_rows=10_000),
                )
                events: list[str] = []
                get_job = processor.repository.get_import_job
                get_collection = processor.repository.get_collection_job
                acquire_locks = processor.repository.acquire_identity_locks
                plan = processor.batch.plan_preview_staging

                async def traced_get_job(
                    job_id: UUID, *, for_update: bool = False
                ) -> ImportJob | None:
                    events.append("job_lock" if for_update else "job_discovery")
                    return await get_job(job_id, for_update=for_update)

                async def traced_get_collection(
                    collection_id: UUID, *, for_update: bool = False
                ) -> CollectionJob | None:
                    events.append("collection_lock" if for_update else "collection_read")
                    return await get_collection(collection_id, for_update=for_update)

                async def traced_locks(identities: Iterable[str]) -> None:
                    frozen = tuple(identities)
                    events.append(
                        "job_advisory"
                        if any(value.startswith("phase2:unified-preview-job:") for value in frozen)
                        else "identity_locks"
                    )
                    await acquire_locks(frozen)

                async def traced_plan(*args: object, **kwargs: object) -> object:
                    events.append("prefetch_plan")
                    return await plan(*args, **kwargs)  # type: ignore[arg-type]

                monkeypatch.setattr(processor.repository, "get_import_job", traced_get_job)
                monkeypatch.setattr(
                    processor.repository, "get_collection_job", traced_get_collection
                )
                monkeypatch.setattr(processor.repository, "acquire_identity_locks", traced_locks)
                monkeypatch.setattr(processor.batch, "plan_preview_staging", traced_plan)
                await processor.build(fixture.job_id, task_id)

                assert events.index("collection_lock") < events.index("job_lock")
                assert events.index("identity_locks") < events.index("prefetch_plan")

    asyncio.run(scenario())


def test_missing_mapping_is_file_scoped_and_keeps_job_draft() -> None:
    async def scenario() -> None:
        async with processor_harness() as (factory, storage):
            fixture = await _seed(
                factory,
                storage,
                [_csv([_row("mapping-required")])],
                mappings=[None],
            )
            occurrence = fixture.files[0]

            result = await _parse(factory, storage, fixture.job_id, occurrence)

            assert result["status"] == ImportJobFileStatus.MAPPING_REQUIRED.value
            async with factory() as session:
                await _assert_job_is_draft(session, fixture.job_id)
                file_model = await session.get(ImportJobFile, occurrence.id)
                assert file_model is not None
                assert file_model.status is ImportJobFileStatus.MAPPING_REQUIRED
                assert file_model.detected_fields == list(HEADERS)
                assert await _rows(session, fixture.job_id) == []
                assert await _business_count(session) == 0

    asyncio.run(scenario())


def test_parse_failure_is_file_scoped_and_does_not_erase_other_ready_rows() -> None:
    async def scenario() -> None:
        async with processor_harness() as (factory, storage):
            fixture = await _seed(
                factory,
                storage,
                [_csv([_row("ready-row")]), b"name,account_id\n\x00corrupt,row\n"],
            )
            ready_file, bad_file = fixture.files
            await _parse(factory, storage, fixture.job_id, ready_file)

            with pytest.raises(ImportDomainError):
                await _parse(factory, storage, fixture.job_id, bad_file)

            async with factory() as session:
                await _assert_job_is_draft(session, fixture.job_id)
                rows = await _rows(session, fixture.job_id)
                assert [(row.import_job_file_id, row.row_number) for row in rows] == [
                    (ready_file.id, 2)
                ]
                failed = await session.get(ImportJobFile, bad_file.id)
                assert failed is not None
                assert failed.status is ImportJobFileStatus.FAILED
                assert failed.error_code is not None
                assert await _business_count(session) == 0

    asyncio.run(scenario())


def test_reparse_shrink_reuses_locator_and_removes_stale_rows() -> None:
    async def scenario() -> None:
        async with processor_harness() as (factory, storage):
            fixture = await _seed(
                factory,
                storage,
                [_csv([_row("keep"), _row("remove-a"), _row("remove-b")])],
            )
            occurrence = fixture.files[0]
            await _parse(factory, storage, fixture.job_id, occurrence)

            async with factory() as session:
                before = await _rows(session, fixture.job_id)
                assert [row.row_number for row in before] == [2, 3, 4]
                kept_id = before[0].id
                replacement = await _stored_file(session, storage, _csv([_row("keep")]))
                file_model = await session.get(ImportJobFile, occurrence.id)
                assert file_model is not None
                file_model.stored_file_id = replacement.id
                file_model.status = ImportJobFileStatus.PARSING
                file_model.parse_task_id = "reparse-shrink"
                file_model.parse_attempts += 1
                file_model.parse_completed_at = None
                await session.commit()

            await _parse(
                factory,
                storage,
                fixture.job_id,
                occurrence,
                task_id="reparse-shrink",
            )

            async with factory() as session:
                await _assert_job_is_draft(session, fixture.job_id)
                after = await _rows(session, fixture.job_id)
                assert [(row.row_number, row.id) for row in after] == [(2, kept_id)]
                assert await _business_count(session) == 0

    asyncio.run(scenario())


def test_excluded_is_a_safe_noop_and_confirmation_required_does_not_block_parse() -> None:
    async def scenario() -> None:
        async with processor_harness() as (factory, storage):
            fixture = await _seed(
                factory,
                storage,
                [_csv([_row("excluded")]), _csv([_row("requires-confirmation")])],
                statuses=(ImportJobFileStatus.EXCLUDED, ImportJobFileStatus.PARSING),
                confirmation_required=(False, True),
            )
            excluded, awaiting_confirmation = fixture.files

            excluded_result = await _parse(
                factory,
                storage,
                fixture.job_id,
                excluded,
                task_id="stale-excluded-delivery",
            )
            parsed_result = await _parse(
                factory,
                storage,
                fixture.job_id,
                awaiting_confirmation,
            )

            assert excluded_result["status"] == ImportJobFileStatus.EXCLUDED.value
            assert parsed_result["status"] == ImportJobFileStatus.READY.value
            async with factory() as session:
                await _assert_job_is_draft(session, fixture.job_id)
                rows = await _rows(session, fixture.job_id)
                assert [(row.import_job_file_id, row.row_number) for row in rows] == [
                    (awaiting_confirmation.id, 2)
                ]
                file_model = await session.get(ImportJobFile, awaiting_confirmation.id)
                assert file_model is not None
                assert file_model.status is ImportJobFileStatus.READY
                assert file_model.source_acquired_at_confirmation_required is True
                assert await _business_count(session) == 0

    asyncio.run(scenario())


def test_parser_row_boundary_accepts_10000_and_rejects_10001() -> None:
    async def scenario() -> None:
        async with processor_harness() as (factory, storage):
            # Keep the limit gate focused on row-count correctness instead of
            # Task 4 database-prefetch performance: these rows form one hard-
            # identity component, while distinct unmapped nonces keep the raw
            # file physically representative.
            ten_thousand = (_row("limit-same", nonce=f"limit-{index}") for index in range(10_000))
            over_limit = (_row("over-same", nonce=f"over-{index}") for index in range(10_001))
            accepted_fixture = await _seed(
                factory,
                storage,
                [_csv(ten_thousand)],
            )
            rejected_fixture = await _seed(
                factory,
                storage,
                [_csv(over_limit)],
            )
            accepted = accepted_fixture.files[0]
            rejected = rejected_fixture.files[0]

            result = await _parse(factory, storage, accepted_fixture.job_id, accepted)
            assert result["status"] == ImportJobFileStatus.READY.value
            with pytest.raises(ImportDomainError) as error:
                await _parse(factory, storage, rejected_fixture.job_id, rejected)
            assert error.value.code == "IMPORT_BATCH_ROW_LIMIT"

            async with factory() as session:
                await _assert_job_is_draft(session, accepted_fixture.job_id)
                await _assert_job_is_draft(session, rejected_fixture.job_id)
                assert (
                    await session.scalar(
                        select(func.count())
                        .select_from(ImportRow)
                        .where(ImportRow.import_job_file_id == accepted.id)
                    )
                    == 10_000
                )
                rejected_model = await session.get(ImportJobFile, rejected.id)
                assert rejected_model is not None
                assert rejected_model.status is ImportJobFileStatus.FAILED
                assert rejected_model.error_code == "IMPORT_BATCH_ROW_LIMIT"
                assert await _business_count(session) == 0

    asyncio.run(scenario())


def _synthetic_file(position: int, *, row_count: int = 500) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for offset in range(row_count):
        global_index = (position - 1) * row_count + offset
        account_id = f"unique-{global_index}"
        if offset == 1:
            account_id = f"inside-{position}"
        elif offset == 2:
            account_id = f"inside-{position}"
        elif offset == 3:
            account_id = "cross-file"
        overrides: dict[str, str] = {
            "email": "shared-contact@example.invalid" if offset in {4, 5} else "",
            "nonce": f"file-{position}-row-{offset}",
        }
        if offset == 6 and position <= 3:
            account_id = "transitive-account" if position <= 2 else ""
            overrides["external_id"] = "transitive-external" if position >= 2 else ""
            overrides["updated_at"] = f"2026-08-1{position} 12:00:00"
        elif offset == 7:
            overrides["name"] = "Same nickname is not identity"
        elif offset == 8 and position <= 2:
            account_id = ""
            overrides["profile_url"] = "https://www.xiaohongshu.com/user/profile/profile-duplicate"
            overrides["updated_at"] = f"2026-08-1{position} 12:00:00"
        elif offset == 9 and position <= 2:
            overrides["external_id"] = "external-duplicate"
            overrides["updated_at"] = f"2026-08-1{position} 12:00:00"
        elif offset == 10 and position <= 2:
            account_id = "same-time-conflict"
            overrides["name"] = f"Conflicting name {position}"
        elif offset == 11 and position <= 2:
            account_id = "newer-owner"
            overrides["name"] = f"Observation {position}"
            overrides["updated_at"] = f"2026-08-1{position} 12:00:00"
        elif offset == 12 and position == 1:
            overrides["followers"] = "not-an-integer"
        elif offset == 13 and position == 1:
            account_id = ""
        rows.append(_row(account_id, **overrides))
    return rows


def test_generated_four_by_500_batch_has_stable_summary_and_provenance() -> None:
    async def scenario() -> None:
        async with processor_harness() as (factory, storage):
            fixture = await _seed(
                factory,
                storage,
                [_csv(_synthetic_file(position)) for position in range(1, 5)],
            )
            for occurrence in reversed(fixture.files):
                result = await _parse(factory, storage, fixture.job_id, occurrence)
                assert result["status"] == ImportJobFileStatus.READY.value

            async with factory() as session:
                await _assert_job_is_draft(session, fixture.job_id)
                rows = await _rows(session, fixture.job_id)
                duplicate_rows = [row for row in rows if row.action is ImportRowAction.SKIP]
                owner_rows = [row for row in rows if row.action is not ImportRowAction.SKIP]
                assert len(rows) == 2_000
                manual_rows = [row for row in rows if row.action is ImportRowAction.MANUAL_REVIEW]
                error_rows = [row for row in rows if row.action is ImportRowAction.ERROR]
                assert len(duplicate_rows) == 12
                assert len(manual_rows) == 2
                assert len(error_rows) == 1, [
                    (
                        row.import_job_file_id,
                        row.row_number,
                        [error.get("code") for error in row.errors],
                    )
                    for row in error_rows
                ]
                assert len(owner_rows) == 1_988
                assert {row.import_job_file_id for row in rows} == {
                    occurrence.id for occurrence in fixture.files
                }
                assert all(
                    any(warning.get("code") == "BATCH_DUPLICATE" for warning in row.warnings)
                    for row in duplicate_rows
                )
                assert all(row.preview_revision == 0 for row in rows)
                invalid_metric = next(
                    row for row in rows if row.raw_data.get("fixture_nonce") == "file-1-row-12"
                )
                assert any(
                    warning.get("code") == "INVALID_INTEGER" for warning in invalid_metric.warnings
                )
                same_nickname = [
                    row for row in rows if row.raw_data.get("fixture_nonce", "").endswith("row-7")
                ]
                assert len(same_nickname) == 4
                assert all(row.action is ImportRowAction.CREATE for row in same_nickname)
                persisted_files = list(
                    await session.scalars(
                        select(ImportJobFile)
                        .where(ImportJobFile.import_job_id == fixture.job_id)
                        .order_by(ImportJobFile.position)
                    )
                )
                assert persisted_files[0].warning_rows >= 1
                assert persisted_files[0].error_rows == 1
                assert all(item.raw_rows == 500 for item in persisted_files)
                assert await _business_count(session) == 0

    asyncio.run(scenario())


def test_batch_planning_never_falls_back_to_per_row_repository_reads(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def reject_legacy_read(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("batch planning must use the prefetched repository facade")

    for method_name in (
        "get_platform_account",
        "accounts_by_platform_id",
        "accounts_by_external_id",
        "accounts_by_profile_url",
        "accounts_by_handle",
        "get_source_identity",
        "get_source_state",
        "get_current_metrics",
        "snapshot_exists",
        "source_contacts",
        "contacts_with_normalized_value",
    ):
        monkeypatch.setattr(ImportRepository, method_name, reject_legacy_read)

    async def scenario() -> None:
        async with processor_harness() as (factory, storage):
            fixture = await _seed(
                factory,
                storage,
                [
                    _csv(
                        [
                            _row(
                                "prefetched-only",
                                external_id="prefetched-external",
                                profile_url=(
                                    "https://www.xiaohongshu.com/user/profile/prefetched-only"
                                ),
                                email="prefetched@example.invalid",
                            )
                        ]
                    )
                ],
            )

            result = await _parse(factory, storage, fixture.job_id, fixture.files[0])
            assert result["status"] == ImportJobFileStatus.READY.value
            async with factory() as session:
                rows = await _rows(session, fixture.job_id)
                assert len(rows) == 1
                assert rows[0].action is ImportRowAction.CREATE

    asyncio.run(scenario())


def test_four_file_mapping_variants_normalize_two_thousand_huitun_rows() -> None:
    """A batch may mix order, optional columns, file type, and explicit mapping."""

    async def scenario() -> None:
        full_headers = list(HUITUN_FIELD_MAPPING)
        reordered_headers = list(reversed(full_headers))
        subset_headers = ["达人名称", "达人官方地址", "更新时间", "粉丝数"]
        custom_mapping = {
            "custom_name": "nickname",
            "custom_profile": "profile_url",
            "custom_account": "platform_account_id",
            "custom_external": "external_source_id",
            "custom_updated": "source_updated_at",
            "custom_followers": "followers_count",
            "custom_email": "email",
        }
        custom_headers = list(custom_mapping)

        rows_a = _huitun_rows("standard")
        rows_b = _huitun_rows("reordered-xlsx")
        rows_c = _huitun_rows("optional-missing")
        rows_d = [
            {
                "custom_name": row["达人名称"],
                "custom_profile": row["达人官方地址"],
                "custom_account": "",
                "custom_external": "",
                "custom_updated": row["更新时间"],
                "custom_followers": row["粉丝数"],
                "custom_email": row["联系邮箱"],
            }
            for row in _huitun_rows("explicit-mapping")
        ]

        # One formal 4×500 matrix covers both per-file shape variance and every
        # Task 3 identity boundary.  All values are synthetic.
        rows_a[2] = dict(rows_a[1])  # same-file exact duplicate
        rows_b[3] = dict(rows_a[3])  # cross-file exact duplicate

        rows_a[4].update(
            {
                "达人名称": "Transitive component",
                "达人官方地址": "https://www.xiaohongshu.com/user/profile/transitive-profile",
                "更新时间": "2026-08-10 12:00:00",
                "粉丝数": "12345",
            }
        )
        rows_d[4].update(
            {
                "custom_name": "Transitive component",
                "custom_profile": ("https://www.xiaohongshu.com/user/profile/transitive-profile"),
                "custom_external": "transitive-external",
                "custom_updated": "2026-08-11 12:00:00",
                "custom_followers": "12345",
            }
        )
        rows_d[5].update(
            {
                "custom_name": "Transitive component",
                "custom_external": "transitive-external",
                "custom_updated": "2026-08-12 12:00:00",
                "custom_followers": "12345",
            }
        )

        rows_a[6]["联系邮箱"] = "same-contact@example.invalid"
        rows_b[6]["联系邮箱"] = "same-contact@example.invalid"
        rows_a[7]["达人名称"] = "Same nickname is not identity"
        rows_b[7]["达人名称"] = "Same nickname is not identity"

        for row, source_time, display_name in (
            (rows_a[8], "2026-08-10 12:00:00", "Older profile duplicate"),
            (rows_b[8], "2026-08-11 12:00:00", "Newer profile duplicate"),
        ):
            row.update(
                {
                    "达人名称": display_name,
                    "达人官方地址": ("https://www.xiaohongshu.com/user/profile/profile-duplicate"),
                    "更新时间": source_time,
                }
            )

        for index, source_time in ((9, "2026-08-10 12:00:00"), (10, "2026-08-11 12:00:00")):
            rows_d[index].update(
                {
                    "custom_external": "external-duplicate",
                    "custom_updated": source_time,
                }
            )
        for index, source_time in ((11, "2026-08-10 12:00:00"), (12, "2026-08-11 12:00:00")):
            rows_d[index].update(
                {
                    "custom_account": "platform-account-duplicate",
                    "custom_profile": "",
                    "custom_updated": source_time,
                }
            )

        for left, right, profile, source_time in (
            (rows_a[13], rows_b[13], "same-time-conflict", "2026-08-10 12:00:00"),
            (rows_a[14], rows_b[14], "unknown-time-conflict", "--"),
        ):
            for position, row in enumerate((left, right), start=1):
                row.update(
                    {
                        "达人名称": f"Conflicting payload {profile} {position}",
                        "达人官方地址": (f"https://www.xiaohongshu.com/user/profile/{profile}"),
                        "更新时间": source_time,
                    }
                )
        for row, source_time, display_name in (
            (rows_a[15], "2026-08-10 12:00:00", "Older observation"),
            (rows_b[15], "2026-08-12 12:00:00", "Newer observation"),
        ):
            row.update(
                {
                    "达人名称": display_name,
                    "达人官方地址": "https://www.xiaohongshu.com/user/profile/newer-owner",
                    "更新时间": source_time,
                }
            )
        rows_a[16]["粉丝数"] = "not-an-integer"
        rows_a[17]["达人官方地址"] = "--"  # deterministic row error
        rows_d[18].update(
            {
                "custom_account": "database-account-a",
                "custom_profile": "",
                "custom_external": "database-component-bridge",
            }
        )
        rows_d[19].update(
            {
                "custom_account": "database-account-b",
                "custom_profile": "",
                "custom_external": "database-component-bridge",
            }
        )

        async with processor_harness() as (factory, storage):
            fixture = await _seed(
                factory,
                storage,
                [
                    _csv(rows_a, headers=full_headers),
                    _xlsx(rows_b, headers=reordered_headers),
                    _csv(rows_c, headers=subset_headers),
                    _csv(rows_d, headers=custom_headers),
                ],
                mappings=[None, None, None, custom_mapping],
                file_types=[
                    StoredFileType.CSV,
                    StoredFileType.XLSX,
                    StoredFileType.CSV,
                    StoredFileType.CSV,
                ],
                source_type=ImportSourceType.MANUAL_HUITUN_EXPORT,
            )
            async with factory() as session:
                first_influencer = Influencer(display_name="Existing synthetic A")
                second_influencer = Influencer(display_name="Existing synthetic B")
                session.add_all((first_influencer, second_influencer))
                await session.flush()
                session.add_all(
                    (
                        InfluencerPlatformAccount(
                            influencer_id=first_influencer.id,
                            platform=Platform.XIAOHONGSHU,
                            platform_account_id="database-account-a",
                            account_name="Existing synthetic A",
                            source=DataSource.HUITUN,
                            is_active=True,
                        ),
                        InfluencerPlatformAccount(
                            influencer_id=second_influencer.id,
                            platform=Platform.XIAOHONGSHU,
                            platform_account_id="database-account-b",
                            account_name="Existing synthetic B",
                            source=DataSource.HUITUN,
                            is_active=True,
                        ),
                    )
                )
                await session.commit()
                business_baseline = await _business_count(session)
                assert business_baseline == 4
            for occurrence in fixture.files:
                result = await _parse(factory, storage, fixture.job_id, occurrence)
                assert result["status"] == ImportJobFileStatus.READY.value

            async with factory() as session:
                await _assert_job_is_draft(session, fixture.job_id)
                rows = await _rows(session, fixture.job_id)
                assert len(rows) == 2_000
                assert {row.import_job_file_id for row in rows} == {
                    occurrence.id for occurrence in fixture.files
                }
                duplicate_rows = [row for row in rows if row.action is ImportRowAction.SKIP]
                manual_rows = [row for row in rows if row.action is ImportRowAction.MANUAL_REVIEW]
                error_rows = [row for row in rows if row.action is ImportRowAction.ERROR]
                assert len(duplicate_rows) == 8
                assert len(manual_rows) == 6
                assert len(error_rows) == 1
                assert any(
                    (row.merge_plan or {}).get("batch_manual_review", {}).get("reason")
                    == "BATCH_DATABASE_IDENTITY_CONFLICT"
                    for row in manual_rows
                )
                assert all(
                    any(warning.get("code") == "BATCH_DUPLICATE" for warning in row.warnings)
                    for row in duplicate_rows
                )
                assert all(row.normalized_data is not None for row in rows)
                assert all(
                    isinstance(row.normalized_data["metrics"].get("followers_count"), int)
                    for row in rows
                    if row.normalized_data is not None
                    and row.raw_data.get("达人官方地址") != "--"
                    and row.raw_data.get("粉丝数") != "not-an-integer"
                )
                invalid_metric = next(
                    row for row in rows if row.raw_data.get("粉丝数") == "not-an-integer"
                )
                assert any(
                    warning.get("code") == "INVALID_INTEGER" for warning in invalid_metric.warnings
                )
                persisted_files = list(
                    await session.scalars(
                        select(ImportJobFile)
                        .where(ImportJobFile.import_job_id == fixture.job_id)
                        .order_by(ImportJobFile.position)
                    )
                )
                assert [item.raw_rows for item in persisted_files] == [500, 500, 500, 500]
                assert persisted_files[0].detected_fields == full_headers
                assert persisted_files[1].detected_fields == reordered_headers
                assert persisted_files[2].detected_fields == subset_headers
                assert persisted_files[3].field_mapping == custom_mapping
                assert await _business_count(session) == business_baseline

    asyncio.run(scenario())


def test_global_replan_retains_file_level_parser_warning_provenance() -> None:
    async def scenario() -> None:
        warning_rows = _huitun_rows("warning-file", count=2)
        for row in warning_rows:
            row["灰豚指数"] = "=1+1"
        second_rows = _huitun_rows("later-file", count=1)
        async with processor_harness() as (factory, storage):
            fixture = await _seed(
                factory,
                storage,
                [
                    _xlsx(warning_rows, headers=HUITUN_FIELD_MAPPING),
                    _csv(second_rows, headers=HUITUN_FIELD_MAPPING),
                ],
                mappings=[None, None],
                file_types=[StoredFileType.XLSX, StoredFileType.CSV],
                source_type=ImportSourceType.MANUAL_HUITUN_EXPORT,
            )
            await _parse(
                factory,
                storage,
                fixture.job_id,
                fixture.files[0],
                parser_max_warnings=1,
            )
            await _parse(
                factory,
                storage,
                fixture.job_id,
                fixture.files[1],
                parser_max_warnings=1,
            )

            async with factory() as session:
                rows = await _rows(session, fixture.job_id)
                first_file_rows = [
                    row for row in rows if row.import_job_file_id == fixture.files[0].id
                ]
                assert any(
                    warning.get("code") == "WARNINGS_TRUNCATED"
                    for warning in first_file_rows[0].warnings
                )

    asyncio.run(scenario())
