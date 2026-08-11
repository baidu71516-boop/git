import asyncio
import csv
import io
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import UUID, uuid4

import pytest
from backend_core.auth.enums import DepartmentStatus, OperatorStatus, Role
from backend_core.auth.models import Department, Operator
from backend_core.db import models as database_models  # noqa: F401
from backend_core.db.base import Base
from backend_core.imports.enums import (
    CollectionJobStatus,
    ImportJobFileStatus,
    ImportJobStatus,
    ImportSourceType,
    SourceAcquiredAtOrigin,
    StoredFileType,
)
from backend_core.imports.mappings import HUITUN_FIELD_MAPPING
from backend_core.imports.models import CollectionJob, ImportJob, ImportJobFile, StoredImportFile
from backend_core.imports.parsers import ParserLimits
from backend_core.imports.processor import ImportProcessor
from backend_core.imports.storage import LocalStorageAdapter
from backend_core.influencers.models import (
    Influencer,
    InfluencerContact,
    InfluencerCurrentMetrics,
    InfluencerMetricSnapshot,
    InfluencerPlatformAccount,
    InfluencerSourceState,
)
from sqlalchemy import func, select
from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import ArgumentError
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.schema import CreateSchema, DropSchema


def _gated_test_database_url() -> URL:
    raw_url = os.environ.get("TEST_DATABASE_URL")
    if not raw_url:
        pytest.skip("TEST_DATABASE_URL is not set", allow_module_level=True)
    try:
        url = make_url(raw_url)
    except ArgumentError:
        pytest.skip("TEST_DATABASE_URL is invalid", allow_module_level=True)
    database_name = (url.database or "").lower()
    if url.get_backend_name() != "postgresql" or "phase1b_test" not in database_name:
        pytest.skip(
            "TEST_DATABASE_URL must be PostgreSQL and its database name must contain "
            "'phase1b_test'",
            allow_module_level=True,
        )
    return url.set(drivername="postgresql+psycopg")


TEST_DATABASE_URL = _gated_test_database_url()


def _limits() -> ParserLimits:
    return ParserLimits(max_rows=100, max_columns=100, max_cells=10_000)


async def _one_chunk(content: bytes) -> AsyncIterator[bytes]:
    yield content


def _sanitized_huitun_csv(profile_id: str) -> bytes:
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=list(HUITUN_FIELD_MAPPING))
    writer.writeheader()
    row = {header: "--" for header in HUITUN_FIELD_MAPPING}
    row.update(
        {
            "达人名称": f"脱敏并发达人-{profile_id}",
            "达人官方地址": f"https://www.xiaohongshu.com/user/profile/{profile_id}",
            "小红书号": f"sanitized-{profile_id}",
            "更新时间": "2026-08-10 12:00:00",
            "联系邮箱": f"{profile_id}@example.invalid",
            "粉丝数": "1234",
            "灰豚指数": "88.5",
        }
    )
    writer.writerow(row)
    return stream.getvalue().encode("utf-8-sig")


@asynccontextmanager
async def _isolated_postgres() -> (
    AsyncIterator[tuple[async_sessionmaker[AsyncSession], LocalStorageAdapter]]
):
    schema_name = f"phase1b_concurrency_{uuid4().hex}"
    admin_engine = create_async_engine(TEST_DATABASE_URL, pool_pre_ping=True)
    test_engine: AsyncEngine | None = None
    schema_created = False
    try:
        async with admin_engine.begin() as connection:
            await connection.execute(CreateSchema(schema_name))
        schema_created = True
        test_engine = create_async_engine(
            TEST_DATABASE_URL,
            connect_args={"options": f"-csearch_path={schema_name}"},
            pool_pre_ping=True,
        )
        async with test_engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        factory = async_sessionmaker(test_engine, expire_on_commit=False)
        with TemporaryDirectory(prefix="phase1b-import-postgres-") as storage_directory:
            yield factory, LocalStorageAdapter(Path(storage_directory))
    finally:
        if test_engine is not None:
            await test_engine.dispose()
        try:
            if schema_created:
                async with admin_engine.begin() as connection:
                    await connection.execute(DropSchema(schema_name, cascade=True, if_exists=True))
        finally:
            await admin_engine.dispose()


async def _seed_preview_ready_jobs(
    session: AsyncSession,
    storage: LocalStorageAdapter,
    *,
    profile_id: str,
    job_count: int,
) -> list[UUID]:
    content = _sanitized_huitun_csv(profile_id)
    stored = await storage.store(_one_chunk(content), suffix=".csv", max_bytes=25 * 1024 * 1024)
    department = Department(
        name=f"PostgreSQL concurrency fixture {uuid4().hex}",
        password_hash="not-used-by-worker",
        status=DepartmentStatus.ACTIVE,
        session_days=30,
    )
    session.add(department)
    await session.flush()
    operator = Operator(
        department_id=department.id,
        name="脱敏并发测试操作人",
        role=Role.OPERATOR,
        status=OperatorStatus.ACTIVE,
    )
    session.add(operator)
    await session.flush()
    collection = CollectionJob(
        name="脱敏 PostgreSQL 并发任务",
        industry="测试行业",
        purpose="验证并发确认",
        target_action="确认导入",
        target_count=job_count,
        department_id=department.id,
        owner_operator_id=operator.id,
        source_type=ImportSourceType.MANUAL_HUITUN_EXPORT,
        status=CollectionJobStatus.ACTIVE,
    )
    session.add(collection)
    stored_file = StoredImportFile(
        sha256=stored.sha256,
        storage_key=stored.storage_key,
        size=stored.size,
        detected_type=StoredFileType.CSV,
        detected_mime="text/csv",
        expires_at=datetime.now(UTC) + timedelta(days=1),
    )
    session.add(stored_file)
    await session.flush()

    jobs = [
        ImportJob(
            collection_job_id=collection.id,
            department_id=department.id,
            operator_id=operator.id,
            stored_file_id=stored_file.id,
            original_filename=f"sanitized-concurrency-{index}.csv",
            mime_type="text/csv",
            file_size=stored.size,
            sha256=stored.sha256,
            source_type=ImportSourceType.MANUAL_HUITUN_EXPORT,
            status=ImportJobStatus.UPLOADED,
            preview_revision=0,
            parse_task_id=f"parse-concurrency-{index}",
        )
        for index in range(job_count)
    ]
    session.add_all(jobs)
    await session.flush()
    session.add_all(
        [
            ImportJobFile(
                import_job_id=job.id,
                stored_file_id=stored_file.id,
                position=1,
                client_file_id=f"legacy:{job.id}",
                original_filename=job.original_filename or "sanitized-concurrency.csv",
                declared_mime=job.mime_type,
                status=ImportJobFileStatus.UPLOADED,
                source_acquired_at=None,
                source_acquired_at_origin=SourceAcquiredAtOrigin.LEGACY_UNKNOWN,
                parse_task_id=job.parse_task_id,
            )
            for job in jobs
        ]
    )
    await session.commit()

    for job in jobs:
        processor = ImportProcessor(session, storage, parser_limits=_limits())
        preview = await processor.parse_and_preview(job.id)
        assert preview["status"] == ImportJobStatus.PREVIEW_READY.value
        assert preview["preview_revision"] == 1
    for job in jobs:
        queued_job = await session.get(ImportJob, job.id)
        assert queued_job is not None
        queued_job.status = ImportJobStatus.CONFIRM_QUEUED
        queued_job.confirmed_revision = 1
        queued_job.confirm_task_id = f"confirm-concurrency-{job.id}"
    await session.commit()
    return [job.id for job in jobs]


async def _confirm_concurrently(
    factory: async_sessionmaker[AsyncSession],
    storage: LocalStorageAdapter,
    job_ids: list[UUID],
) -> list[dict[str, object]]:
    barrier = asyncio.Barrier(len(job_ids) + 1)

    async def confirm(job_id: UUID) -> dict[str, object]:
        async with factory() as session:
            processor = ImportProcessor(session, storage, parser_limits=_limits())
            await barrier.wait()
            return await processor.confirm(job_id, 1)

    tasks = [asyncio.create_task(confirm(job_id)) for job_id in job_ids]
    await barrier.wait()
    return list(await asyncio.gather(*tasks))


async def _count(session: AsyncSession, model: type[object]) -> int:
    return int(await session.scalar(select(func.count()).select_from(model)) or 0)


def test_duplicate_concurrent_confirm_is_atomic_and_idempotent() -> None:
    async def scenario() -> None:
        async with _isolated_postgres() as (factory, storage):
            async with factory() as setup_session:
                job_id = (
                    await _seed_preview_ready_jobs(
                        setup_session,
                        storage,
                        profile_id="same-job",
                        job_count=1,
                    )
                )[0]

            results = await _confirm_concurrently(factory, storage, [job_id, job_id])

            assert results[0] == results[1]
            assert results[0]["created_rows"] == 1
            async with factory() as inspection_session:
                job = await inspection_session.get(ImportJob, job_id)
                assert job is not None
                assert job.status == ImportJobStatus.COMPLETED
                assert job.result == results[0]
                assert await _count(inspection_session, Influencer) == 1
                assert await _count(inspection_session, InfluencerPlatformAccount) == 1
                assert await _count(inspection_session, InfluencerSourceState) == 1
                assert await _count(inspection_session, InfluencerContact) == 1
                assert await _count(inspection_session, InfluencerCurrentMetrics) == 1
                assert await _count(inspection_session, InfluencerMetricSnapshot) == 1

    asyncio.run(scenario())


def test_concurrent_jobs_with_same_identity_complete_once_and_stale_once() -> None:
    async def scenario() -> None:
        async with _isolated_postgres() as (factory, storage):
            async with factory() as setup_session:
                job_ids = await _seed_preview_ready_jobs(
                    setup_session,
                    storage,
                    profile_id="cross-job",
                    job_count=2,
                )

            results = await _confirm_concurrently(factory, storage, job_ids)

            assert (
                sum(
                    result.get("status") == ImportJobStatus.PREVIEW_STALE.value
                    for result in results
                )
                == 1
            )
            async with factory() as inspection_session:
                jobs = list(
                    await inspection_session.scalars(
                        select(ImportJob).where(ImportJob.id.in_(job_ids))
                    )
                )
                statuses = [job.status for job in jobs]
                assert statuses.count(ImportJobStatus.COMPLETED) == 1
                assert statuses.count(ImportJobStatus.PREVIEW_STALE) == 1
                assert await _count(inspection_session, Influencer) == 1
                assert await _count(inspection_session, InfluencerPlatformAccount) == 1
                assert await _count(inspection_session, InfluencerSourceState) == 1
                assert await _count(inspection_session, InfluencerContact) == 1
                assert await _count(inspection_session, InfluencerCurrentMetrics) == 1
                assert await _count(inspection_session, InfluencerMetricSnapshot) == 1

    asyncio.run(scenario())
